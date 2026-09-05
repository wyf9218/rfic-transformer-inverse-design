import csv
import json

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_frozen_queue_batches as frozen


def fixture(tmp_path, count=900):
    rows = [{"candidate_id_sha256": f"{i:064x}", "geometry_sha256": f"{i:064x}",
             "candidate_index": str(i), "seed": "20260828", "primary_outer_width_um": "100.000000000001"}
            for i in range(count)]
    path = tmp_path / "queue.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    payload = {"overall_status": "PASS", "campaign_id": frozen.CAMPAIGN_ID,
               "contract_fingerprint_sha256": "f" * 64, "seed": 20260828,
               "sampler": "lhs_optimized", "acquisition_source": "base_space_filling",
               "campaign_phase": "PHASE_A", "canonical_geometry_fields": list(frozen.GEOMETRY_FIELDS),
               "checks": [{"name": "fixture_only_not_real_geometry", "pass": True}],
               "candidate_queue": frozen.file_identity(path), "queue_count": count}
    receipt = tmp_path / "queue_summary.json"
    receipt.write_text(json.dumps(payload))
    return rows, receipt


def select(path, **kwargs):
    return frozen.select_frozen_queue(path, frozen.file_identity(path)["sha256"],
        fingerprint="f" * 64, seed=20260828, sampler="lhs_optimized",
        acquisition_source="base_space_filling", phase="PHASE_A", **kwargs)


def test_900_rows_partition_exactly_into_28_full_batches_and_one_remainder(tmp_path):
    rows, receipt = fixture(tmp_path)
    excluded, restored, indexes = set(), [], []
    before = frozen.file_identity(receipt)
    for offset in range(0, 900, 32):
        batch, proof = select(receipt, count=32, excluded_hashes=excluded)
        restored.extend(batch)
        excluded.update(r["geometry_sha256"] for r in batch)
        indexes.extend(proof["source_row_indexes"])
        assert proof["sampler_executed"] is False
        assert proof["dispatch_claim_created"] is False
    assert restored == rows
    assert indexes == list(range(900))
    assert frozen.file_identity(receipt) == before


def test_noncontiguous_terminal_exclusions_do_not_renumber_remaining_rows(tmp_path):
    rows, receipt = fixture(tmp_path, 40)
    excluded = {rows[i]["geometry_sha256"] for i in (0, 3, 5)}
    batch, proof = select(receipt, count=32, excluded_hashes=excluded)
    assert batch == [r for r in rows if r["geometry_sha256"] not in excluded][:32]
    assert proof["source_row_indexes"][:5] == [1, 2, 4, 6, 7]


def test_exhaustion_fails_instead_of_sampling_replacements(tmp_path):
    rows, receipt = fixture(tmp_path, 31)
    batch, proof = select(receipt, count=32, excluded_hashes=set())
    assert batch == rows
    assert proof["selected_count"] == 31
    assert proof["requested_candidate_ceiling"] == 32
    with pytest.raises(ValueError, match="exhausted"):
        select(receipt, count=32, excluded_hashes={r["geometry_sha256"] for r in rows})


@pytest.mark.parametrize("mutation", ["csv", "receipt_hash", "seed", "duplicate", "checks"])
def test_source_identity_and_provenance_fail_closed(tmp_path, mutation):
    _, receipt = fixture(tmp_path, 40)
    payload = json.loads(receipt.read_text())
    path = tmp_path / "queue.csv"
    if mutation == "csv":
        path.write_text(path.read_text()+"\n")
    elif mutation == "receipt_hash":
        with pytest.raises(ValueError, match="SHA mismatch"):
            frozen.select_frozen_queue(receipt, "0"*64, count=32, excluded_hashes=set(),
                fingerprint="f"*64, seed=20260828, sampler="lhs_optimized",
                acquisition_source="base_space_filling", phase="PHASE_A")
        return
    elif mutation == "duplicate":
        lines = path.read_text().splitlines()
        lines[-1] = lines[-2]
        path.write_text("\n".join(lines)+"\n")
        payload["candidate_queue"] = frozen.file_identity(path)
    elif mutation == "seed":
        payload["seed"] += 1
    else:
        payload["checks"][0]["pass"] = False
    receipt.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        select(receipt, count=32, excluded_hashes=set())


def test_real_queue_entrypoint_preserves_rows_and_never_calls_sampler(tmp_path, monkeypatch):
    from tests import test_broadband56_balanced200k_contract as fixtures

    module = fixtures._load_queue_module()
    source = tmp_path / "source"
    common = ["--contract", str(fixtures.CONTRACT), "--config", str(fixtures.TEMPLATE),
              "--sampler", "sobol", "--seed", "20260828"]
    assert module.main(common + ["--out-dir", str(source), "--count", "40"]) == 0
    source_receipt = source / "broadband56_candidate_queue_summary.json"
    source_pin = frozen.file_identity(source / "broadband56_candidate_queue.csv")
    def no_sampling(*args, **kwargs):
        raise AssertionError("frozen selection must not call the sampler")
    monkeypatch.setattr(module, "_sample_unit", no_sampling)
    # The campaign exclusion-chain validator has separate identity tests. This
    # integration fixture tests real geometry/CSV selection without a campaign.
    monkeypatch.setattr(module, "_campaign_exclusion_paths", lambda *a, **kw: [])
    output = tmp_path / "batch"
    assert module.main(common + ["--out-dir", str(output), "--count", "32",
        "--attempt-candidate-limit", "32", "--campaign-root", str(tmp_path),
        "--stage", "PILOT_1000", "--current-accepted", "100",
        "--frozen-queue-receipt", str(source_receipt),
        "--frozen-queue-receipt-sha256", frozen.file_identity(source_receipt)["sha256"]]) == 0
    with (source / "broadband56_candidate_queue.csv").open(newline="") as stream:
        original = list(csv.DictReader(stream))
    with (output / "broadband56_candidate_queue.csv").open(newline="") as stream:
        selected = list(csv.DictReader(stream))
    assert selected == original[:32]
    assert frozen.file_identity(source / "broadband56_candidate_queue.csv") == source_pin
    summary = json.loads((output / "broadband56_candidate_queue_summary.json").read_text())
    assert summary["sampling_attempts"] == 0
    assert summary["frozen_batch"]["sampler_executed"] is False
    bound = frozen.validate_frozen_selection(output / "broadband56_candidate_queue_summary.json",
        source_receipt_path=source_receipt, source_receipt_sha256=frozen.file_identity(source_receipt)["sha256"],
        candidate_ceiling=32, fingerprint=summary["contract_fingerprint_sha256"])
    assert bound["actual_selected_candidates"] == 32
    assert bound["accepted_increment"] == 0


def test_bounded_cli_rejects_a_smaller_sampler_invocation(tmp_path):
    from tests import test_broadband56_balanced200k_contract as fixtures

    module = fixtures._load_queue_module()
    with pytest.raises(SystemExit):
        module._parse_args(["--contract", str(fixtures.CONTRACT), "--config", str(fixtures.TEMPLATE),
            "--out-dir", str(tmp_path), "--count", "32", "--attempt-candidate-limit", "32",
            "--campaign-root", str(tmp_path)])


def selected_fixture(tmp_path, *, count=4):
    rows, source = fixture(tmp_path, 40)
    batch, proof = select(source, count=32,
        excluded_hashes={r["geometry_sha256"] for r in rows[:-count]})
    output = tmp_path / "selected.csv"
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(batch)
    payload = json.loads(source.read_text())
    payload.update(candidate_queue=frozen.file_identity(output), queue_count=count,
        requested_count=count, requested_candidate_ceiling=32, sampling_attempts=0, frozen_batch=proof)
    receipt = tmp_path / "selected_summary.json"
    receipt.write_text(json.dumps(payload))
    return source, receipt


def validate(source, receipt):
    return frozen.validate_frozen_selection(receipt, source_receipt_path=source,
        source_receipt_sha256=frozen.file_identity(source)["sha256"],
        candidate_ceiling=32, fingerprint="f" * 64)


@pytest.mark.parametrize("count", [1, 4, 32])
def test_dispatch_binds_actual_short_tail_and_never_counts_it_as_accepted(tmp_path, count):
    source, receipt = selected_fixture(tmp_path, count=count)
    result = validate(source, receipt)
    assert result["candidate_ceiling"] == 32
    assert result["actual_selected_candidates"] == count
    assert result["source_row_indexes"] == list(range(40-count, 40))
    assert result["accepted_increment"] == 0


@pytest.mark.parametrize("mutation", ["count", "ceiling", "index", "reordered", "seed",
                                      "source", "sampled", "failed", "csv", "geometry"])
def test_dispatch_rejects_wrong_count_source_or_modified_rows(tmp_path, mutation):
    source, receipt = selected_fixture(tmp_path)
    d = json.loads(receipt.read_text())
    if mutation == "count":
        d["queue_count"] = 32
    elif mutation == "ceiling":
        d["requested_candidate_ceiling"] = 4
    elif mutation == "index":
        d["frozen_batch"]["source_row_indexes"][0] = -1
    elif mutation == "reordered":
        d["frozen_batch"]["source_row_indexes"].reverse()
    elif mutation == "seed":
        d["seed"] += 1
    elif mutation == "source":
        d["frozen_batch"]["source_receipt"]["sha256"] = "0" * 64
    elif mutation == "sampled":
        d["sampling_attempts"] = 4
    elif mutation == "failed":
        d["checks"][0]["pass"] = False
    else:
        path = tmp_path / "selected.csv"
        path.write_text(path.read_text().replace("100.000000000001", "100.000000000002"))
        if mutation == "geometry":
            # Updating the new CSV's hash cannot conceal modified source rows.
            d["candidate_queue"] = frozen.file_identity(path)
    receipt.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate(source, receipt)
