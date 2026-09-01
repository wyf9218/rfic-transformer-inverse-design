from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    canonical_geometry_sha256,
    contract_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _load_module():
    path = ROOT / "scripts" / "finalize_broadband56_campaign_histories.py"
    spec = importlib.util.spec_from_file_location(
        "finalize_broadband56_campaign_histories", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cell_id(conditioned: int) -> str:
    anchor_index, local = divmod(conditioned, PRIMARY_CELLS_PER_ANCHOR)
    k_bin = local % 6
    local //= 6
    qmin_bin = local % 6
    local //= 6
    xs_bin = local % 6
    xp_bin = local // 6
    return (
        f"f{ANCHOR_FREQUENCIES_GHZ[anchor_index]:02d}_xp{xp_bin}_"
        f"xs{xs_bin}_q{qmin_bin}_k{k_bin}"
    )


def _accepted_rows(fingerprint: str, *, mixed_last_round: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(4):
        geometry = {
            "primary_outer_width_um": 220.0 + index,
            "primary_outer_height_um": 221.0,
            "secondary_outer_width_um": 210.0,
            "secondary_outer_height_um": 211.0,
            "line_width_um": 8.0,
            "primary_terminal_y_span_um": 50.0,
            "secondary_terminal_y_span_um": 51.0,
            "offset_um": float(index),
            "primary_feed_extension_um": 150.0,
            "secondary_feed_extension_um": 151.0,
        }
        assert tuple(geometry) == GEOMETRY_FIELDS
        sequence = index + 1
        source = "base_space_filling" if sequence <= 2 else "underfilled_response_repair"
        if mixed_last_round and sequence == 4:
            source = "maximin_geometry_exploration"
        rows.append(
            {
                "geometry_id": f"g{index:04d}",
                "geometry_sha256": canonical_geometry_sha256(geometry),
                "campaign_contract_fingerprint": fingerprint,
                "accepted_sequence": sequence,
                "campaign_phase": "PHASE_A" if sequence <= 2 else "PHASE_B",
                "acquisition_source": source,
                **{f"geom__{name}": value for name, value in geometry.items()},
            }
        )
    return rows


def _write_audit(
    root: Path,
    *,
    count: int,
    target_count: int,
    fingerprint: str,
    contract_path: Path,
    accepted_snapshot: Path,
) -> Path:
    directory = root / f"audit_{count}"
    directory.mkdir()
    cells_path = directory / "physical_coverage_cells_by_anchor.csv"
    target = count / float(PRIMARY_CELLS_PER_ANCHOR)
    cell_rows: list[dict[str, object]] = []
    for conditioned in range(PRIMARY_FREQUENCY_CONDITIONED_CELLS):
        anchor_index, local = divmod(conditioned, PRIMARY_CELLS_PER_ANCHOR)
        actual = count if local == 0 else 0
        status = (
            "unobserved_under_current_geometry_contract"
            if actual == 0
            else ("underfilled" if actual < target else "observed")
        )
        cell_rows.append(
            {
                "anchor_ghz": ANCHOR_FREQUENCIES_GHZ[anchor_index],
                "cell_id": _cell_id(conditioned),
                "local_cell_index": local,
                "conditioned_cell_index": conditioned,
                "actual_count": actual,
                "target_count": target,
                "deficit": max(target - actual, 0.0),
                "cell_status": status,
            }
        )
    _write_csv(cells_path, cell_rows)

    is_terminal = count == target_count
    status_path = directory / "CHECKPOINT_STATUS.json"
    status_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "checkpoint_status": "COMPLETE_200K" if is_terminal else "CHECKPOINT_COMPLETE",
                "audit_mode": "checkpoint",
                "coverage_status": "COVERAGE_PARTIAL",
                "accepted_geometries": count,
                "s4p_artifacts": count,
                "geometry_frequency_rows": count * 56,
            }
        ),
        encoding="utf-8",
    )
    coverage_path = directory / "COVERAGE_SUMMARY.json"
    coverage_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "expected_accepted_geometries": count,
                "feature_row_count": count * 56,
                "coverage_status": "COVERAGE_PARTIAL",
                "geometry_unique_anchor_coverage": {
                    "observed_cells": 8,
                    "observed_cell_fraction": 8 / PRIMARY_FREQUENCY_CONDITIONED_CELLS,
                    "normalized_entropy": 0.1 + count / 100.0,
                    "coefficient_of_variation": 2.0,
                    "gini_coefficient": 0.8,
                    "underfilled_cells": PRIMARY_FREQUENCY_CONDITIONED_CELLS - 8,
                    "top_1pct_cell_concentration": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    receipt_path = directory / "CHECKPOINT_RECEIPT.json"
    receipt_path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "USE_CHECKPOINT",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "expected_accepted": count,
                "audit_mode": "checkpoint",
                "checks": [{"name": "synthetic_audit", "pass": True}],
                "inputs": {
                    "contract": _evidence(contract_path),
                    "accepted_geometries": _evidence(accepted_snapshot),
                },
                "outputs": {
                    "coverage_cells": _evidence(cells_path),
                    "checkpoint_status": _evidence(status_path),
                    "coverage_summary": _evidence(coverage_path),
                },
            }
        ),
        encoding="utf-8",
    )
    _write_sha256s(directory)
    return directory


def _fixture(tmp_path: Path, *, mixed_last_round: bool = False) -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    rows = _accepted_rows(fingerprint, mixed_last_round=mixed_last_round)
    accepted_2 = tmp_path / "accepted_2.csv"
    accepted_4 = tmp_path / "accepted_4.csv"
    _write_csv(accepted_2, rows[:2])
    _write_csv(accepted_4, rows)
    audit_2 = _write_audit(
        tmp_path,
        count=2,
        target_count=4,
        fingerprint=fingerprint,
        contract_path=contract_path,
        accepted_snapshot=accepted_2,
    )
    audit_4 = _write_audit(
        tmp_path,
        count=4,
        target_count=4,
        fingerprint=fingerprint,
        contract_path=contract_path,
        accepted_snapshot=accepted_4,
    )
    return {
        "contract": contract_path,
        "accepted": accepted_4,
        "audits": [audit_2, audit_4],
    }


def _patch_small_contract(module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "TARGET_ACCEPTED_GEOMETRIES", 4)
    monkeypatch.setattr(module, "REQUIRED_HISTORY_AUDIT_COUNTS", (2, 4))
    monkeypatch.setattr(module, "REQUIRED_CHECKPOINT_COUNTS", (2, 4))
    rounds = (
        module.RoundExpectation(
            round_id="phase_a_small",
            phase="PHASE_A",
            accepted_start=0,
            accepted_end=2,
            active_source_quotas=(("base_space_filling", 2),),
            fallback_source_quotas=(("base_space_filling", 2),),
        ),
        module.RoundExpectation(
            round_id="phase_b_small",
            phase="PHASE_B",
            accepted_start=2,
            accepted_end=4,
            active_source_quotas=(("underfilled_response_repair", 2),),
            fallback_source_quotas=(("maximin_geometry_exploration", 2),),
        ),
    )
    monkeypatch.setattr(module, "_round_expectations", lambda: rounds)


def test_campaign_root_discovers_unique_formal_checkpoint_directories(
    tmp_path: Path,
) -> None:
    module = _load_module()
    campaign = tmp_path / "campaign"
    expected = (100, 1_000)
    for index, count in enumerate(expected, start=1):
        directory = (
            campaign
            / "stages"
            / f"{index:06d}_stage"
            / "backend"
            / "roles"
            / "checkpoint"
        )
        directory.mkdir(parents=True)
        (directory / "CHECKPOINT_STATUS.json").write_text(
            json.dumps({"accepted_geometries": count, "audit_mode": "checkpoint"}),
            encoding="utf-8",
        )
    discovered = module._discover_audit_dirs(
        campaign,
        required_counts=expected,
    )
    assert [path.name for path in discovered] == ["checkpoint", "checkpoint"]
    assert [
        json.loads((path / "CHECKPOINT_STATUS.json").read_text())["accepted_geometries"]
        for path in discovered
    ] == list(expected)


def test_campaign_root_discovery_rejects_distinct_duplicate_count(
    tmp_path: Path,
) -> None:
    module = _load_module()
    campaign = tmp_path / "campaign"
    for index in (1, 2):
        directory = campaign / "stages" / f"{index:06d}_stage" / "checkpoint"
        directory.mkdir(parents=True)
        (directory / "CHECKPOINT_STATUS.json").write_text(
            json.dumps({"accepted_geometries": 100, "audit_mode": "checkpoint"}),
            encoding="utf-8",
        )
    with pytest.raises(module.HistoryFinalizationError, match="multiple distinct"):
        module._discover_audit_dirs(campaign, required_counts=(100,))


def test_terminal_audits_write_all_required_history_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    _patch_small_contract(module, monkeypatch)
    out_dir = tmp_path / "history"
    args = [
        "--contract",
        str(fixture["contract"]),
        "--accepted-geometries",
        str(fixture["accepted"]),
    ]
    for audit in fixture["audits"]:
        args.extend(["--audit-dir", str(audit)])
    args.extend(["--out-dir", str(out_dir)])

    status = module.main(args)

    assert status == 0
    assert {
        "coverage_deficit_history.csv",
        "acquisition_round_history.csv",
        "acquisition_source_by_geometry.csv",
        "coverage_summary_200k.json",
        "CAMPAIGN_HISTORY_RECEIPT.json",
        "SHA256SUMS.txt",
    } == {path.name for path in out_dir.iterdir()}
    with (out_dir / "coverage_deficit_history.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        deficits = list(csv.DictReader(handle))
    assert len(deficits) == 2 * PRIMARY_FREQUENCY_CONDITIONED_CELLS
    assert {int(row["accepted_geometries"]) for row in deficits} == {2, 4}

    with (out_dir / "acquisition_round_history.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rounds = list(csv.DictReader(handle))
    assert len(rounds) == 2
    assert [row["execution_mode"] for row in rounds] == [
        "ACTIVE_MIXTURE",
        "ACTIVE_MIXTURE",
    ]
    with (out_dir / "acquisition_source_by_geometry.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        sources = list(csv.DictReader(handle))
    assert len(sources) == 4
    assert {row["evidence_class"] for row in sources} == {
        "accepted_fresh_real_emx_geometry_provenance"
    }

    terminal_coverage = fixture["audits"][-1] / "COVERAGE_SUMMARY.json"
    assert (out_dir / "coverage_summary_200k.json").read_bytes() == terminal_coverage.read_bytes()
    receipt = json.loads((out_dir / "CAMPAIGN_HISTORY_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["checks"]["proxy_predictions_excluded_from_labels"] is True
    assert receipt["checks"]["simulator_and_model_training_not_run"] is True
    for evidence in receipt["outputs"].values():
        path = Path(evidence["path"])
        assert path.is_file()
        assert _sha256(path) == evidence["sha256"]


def test_tampered_audit_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    _patch_small_contract(module, monkeypatch)
    cells = fixture["audits"][0] / "physical_coverage_cells_by_anchor.csv"
    cells.write_text(cells.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    out_dir = tmp_path / "must_not_exist"
    args = [
        "--contract",
        str(fixture["contract"]),
        "--accepted-geometries",
        str(fixture["accepted"]),
    ]
    for audit in fixture["audits"]:
        args.extend(["--audit-dir", str(audit)])
    args.extend(["--out-dir", str(out_dir)])

    assert module.main(args) == 2
    assert not out_dir.exists()


def test_mixed_active_and_fallback_sources_in_one_round_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path, mixed_last_round=True)
    _patch_small_contract(module, monkeypatch)
    out_dir = tmp_path / "must_not_exist"
    args = [
        "--contract",
        str(fixture["contract"]),
        "--accepted-geometries",
        str(fixture["accepted"]),
    ]
    for audit in fixture["audits"]:
        args.extend(["--audit-dir", str(audit)])
    args.extend(["--out-dir", str(out_dir)])

    assert module.main(args) == 2
    assert not out_dir.exists()


def test_self_consistent_but_wrong_cell_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    _patch_small_contract(module, monkeypatch)
    audit = fixture["audits"][0]
    cells_path = audit / "physical_coverage_cells_by_anchor.csv"
    with cells_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["cell_id"] = "wrong_cell_identity"
    _write_csv(cells_path, rows)
    receipt_path = audit / "CHECKPOINT_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["outputs"]["coverage_cells"] = _evidence(cells_path)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    _write_sha256s(audit)
    out_dir = tmp_path / "must_not_exist"
    args = [
        "--contract",
        str(fixture["contract"]),
        "--accepted-geometries",
        str(fixture["accepted"]),
    ]
    for audit_dir in fixture["audits"]:
        args.extend(["--audit-dir", str(audit_dir)])
    args.extend(["--out-dir", str(out_dir)])

    assert module.main(args) == 2
    assert not out_dir.exists()


def test_production_history_contract_has_all_35_audits_and_31_rounds() -> None:
    module = _load_module()
    rounds = module._round_expectations()
    assert len(module.REQUIRED_HISTORY_AUDIT_COUNTS) == 35
    assert len(rounds) == 31
    assert rounds[0].accepted_start == 0
    assert rounds[0].accepted_end == 50_000
    assert rounds[-1].accepted_end == 200_000
    assert sum(item.batch_size for item in rounds) == 200_000
    assert {item.accepted_end for item in rounds}.issubset(
        set(module.REQUIRED_HISTORY_AUDIT_COUNTS)
    )
