from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_tool_capacity as capacity
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import CapacityPolicyError
from rfic_transformer_inverse_design.campaigns.broadband56_scheduling import file_record, measured_worker_cap


@pytest.mark.parametrize("total,used,issued_word,used_word", [
    (300, 0, "licenses", "licenses"), (300, 1, "licenses", "license"),
    (300, 2, "licenses", "licenses"), (1, 1, "license", "license")])
def test_license_parser_preserves_singular_seat_usage(total, used, issued_word, used_word):
    raw = f"Users of TOOL_Solver:  (Total of {total} {issued_word} issued;  Total of {used} {used_word} in use)"
    assert capacity.parse_license_counts(raw) == {"tool_solver": {"total": total, "used": used, "free": total-used}}


def test_duplicate_license_features_are_not_summed():
    raw = "Users of X:  (Total of 3 licenses issued;  Total of 0 licenses in use)\n"
    with pytest.raises(CapacityPolicyError):
        capacity.parse_license_counts(raw + raw)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return file_record(path)


def fixture(root):
    now = datetime.now(timezone.utc)
    backend = write(root / "backend.json", {"campaign_id": "campaign", "contract_fingerprint_sha256": "a" * 64})
    binary = {}
    for tool, name in (("cadence", "strmout"), ("calibre", "calibre"), ("emx", "emx")):
        p = root / name
        p.write_bytes(b"\x7fELFfixture-native-not-executed")
        binary[tool] = file_record(p)
    observations = []
    for index, tool in enumerate(capacity.TOOL_NAMES):
        native = tool == "emx"
        record = {"tool": tool, "pid": index + 10, "start_ticks": 100,
                  "threads": 9, "VmHWM_bytes": 1_000_000,
                  "executable_path": binary[tool]["path"], "executable_sha256": binary[tool]["sha256"]} if native else {
                      "tool": tool, "pid": index + 10, "start_ticks": 100,
                      "observed_threads": 1, "high_water_bytes": 100_000,
                      "executable": binary[tool]}
        sample = {"captured_utc": (now - timedelta(seconds=60)).isoformat(),
                  "native_processes" if native else "processes": [record]}
        sample_pin = write(root / f"samples_{tool}.jsonl", sample)
        evidence = {"schema": "rfic_transformer.broadband56_native_role_observation.v1",
                    "observation_status": "RECORDED", "errors": [], "bindings": {"backend": backend},
                    "samples": sample_pin, "sample_count": 1} if native else {
                        "schema": "rfic_transformer.broadband56_readonly_tool_observation.v1",
                        "overall_status": "PASS_OBSERVATION_ONLY", "error": None, "backend": backend,
                        "sample_source": sample_pin, "sample_count": 1}
        observations.append(write(root / f"observation_{tool}.json", evidence))
    log = root / "strmout.log"
    log.write_text("XStream translation in multi-threaded mode with 4 threads.\n")
    args = dict(baseline_backend=backend, observations=observations, thread_logs={"cadence": [file_record(log)]})
    footprints = capacity.derive_footprints(**args)
    footprint = write(root / "footprints.json", footprints)
    lease = write(root / "lease.json", {"fixture": True})
    query = {"mode": "READ_ONLY_LMSTAT_NO_CHECKOUT", "returncode": 0, "server_up": True,
             "captured_utc": now.isoformat(), "response_sha256": "b" * 64,
             "features": {"fixture_feature": {"total": 48, "used": 0, "free": 48}},
             "query_tool": binary["cadence"], "loader": binary["cadence"], "environment_source": backend}
    query_pin = write(root / "license.json", query)
    snapshot = {"campaign_id": "campaign", "contract_fingerprint_sha256": "a" * 64,
                "captured_utc": now.isoformat(), "supervisor_lease": lease,
                "resources": {"logical_cpu_count": 192, "memory_total_bytes": 1_000_000_000,
                              "memory_available_bytes": 800_000_000},
                "licenses": {"simulator_license_capacity": 48}, "isolation": {"fixture_unchanged": True}}
    kwargs = dict(snapshot=snapshot, footprint_record=footprint, license_queries={"query": query_pin},
                  license_features={tool: [("query", "fixture_feature")] for tool in capacity.TOOL_NAMES},
                  out_dir=root / "capacity", now=now)
    return args, footprints, kwargs


def test_raw_samples_and_thread_log_produce_empirical_capacity_without_changing_snapshot(tmp_path):
    _, footprint, kwargs = fixture(tmp_path)
    before = deepcopy(kwargs["snapshot"])
    assert footprint["tools"]["cadence"]["threads_per_job"] == 4
    assert footprint["tools"]["calibre"]["peak_rss_bytes_per_job"] == 100_000
    assert footprint["tools"]["emx"]["threads_per_job"] == 9
    record = capacity.materialize_capacity(**kwargs)
    assert kwargs["snapshot"] == before
    snapshot = {**before, "per_tool_capacity_evidence": record}
    assert measured_worker_cap(snapshot) == (2, "MEASURED_PER_TOOL_CAPACITY")
    assert json.loads(Path(record["path"]).read_text())["admission_authorized"] is False
    assert footprint["absolute_job_bounds_proven"] is False


def test_two_simultaneous_children_reserve_their_sum(tmp_path):
    args, _, _ = fixture(tmp_path)
    pin = args["observations"][1]
    evidence = json.loads(Path(pin["path"]).read_text())
    sample_path = Path(evidence["sample_source"]["path"])
    sample = json.loads(sample_path.read_text())
    sample["processes"].append({**sample["processes"][0], "pid": 50})
    evidence["sample_source"] = write(sample_path, sample)
    args["observations"][1] = write(Path(pin["path"]), evidence)
    tool = capacity.derive_footprints(**args)["tools"]["calibre"]
    assert tool["threads_per_job"] == 2
    assert tool["peak_rss_bytes_per_job"] == 200_000


@pytest.mark.parametrize("mutation", ["status", "backend", "sample_count", "duplicate", "wrapper", "missing_tool", "bad_thread_log"])
def test_incomplete_corrupt_or_misclassified_observations_fail_closed(tmp_path, mutation):
    args, _, _ = fixture(tmp_path)
    pin = args["observations"][0]
    evidence = json.loads(Path(pin["path"]).read_text())
    if mutation == "status":
        evidence["overall_status"] = "FAIL_OBSERVATION"
    elif mutation == "backend":
        evidence["backend"] = {}
    elif mutation == "sample_count":
        evidence["sample_count"] = 2
    elif mutation == "duplicate":
        p = Path(evidence["sample_source"]["path"])
        sample = json.loads(p.read_text())
        sample["processes"] *= 2
        evidence["sample_source"] = write(p, sample)
    elif mutation == "wrapper":
        Path(args["thread_logs"]["cadence"][0]["path"]).parent.joinpath("strmout").write_text("#!/bin/sh\n")
    elif mutation == "missing_tool":
        args["observations"].pop()
    elif mutation == "bad_thread_log":
        p = Path(args["thread_logs"]["cadence"][0]["path"])
        p.write_text("no explicit count")
        args["thread_logs"]["cadence"] = [file_record(p)]
    args["observations"][0] = write(Path(pin["path"]), evidence)
    with pytest.raises(CapacityPolicyError):
        capacity.derive_footprints(**args)


@pytest.mark.parametrize("mutation", ["stale_query", "future_query", "query_failure", "seats", "feature_missing",
                                     "source_changed", "fabricated_peak", "wrong_contract", "stale_snapshot"])
def test_capacity_revalidates_measurements_and_fresh_seat_evidence(tmp_path, mutation):
    _, _, kwargs = fixture(tmp_path)
    if mutation in {"stale_query", "future_query", "query_failure", "seats", "feature_missing"}:
        p = Path(kwargs["license_queries"]["query"]["path"])
        query = json.loads(p.read_text())
        if mutation in {"stale_query", "future_query"}:
            delta = -151 if mutation == "stale_query" else 1
            query["captured_utc"] = (kwargs["now"] + timedelta(seconds=delta)).isoformat()
        elif mutation == "query_failure":
            query["returncode"] = 1
        elif mutation == "seats":
            query["features"]["fixture_feature"]["free"] = 49
        else:
            query["features"] = {}
        kwargs["license_queries"]["query"] = write(p, query)
    elif mutation == "source_changed":
        (tmp_path / "samples_calibre.jsonl").write_text("{}")
    elif mutation == "fabricated_peak":
        p = Path(kwargs["footprint_record"]["path"])
        value = json.loads(p.read_text())
        value["tools"]["emx"]["peak_rss_bytes_per_job"] = 1
        kwargs["footprint_record"] = write(p, value)
    elif mutation == "wrong_contract":
        kwargs["snapshot"]["contract_fingerprint_sha256"] = "c" * 64
    else:
        kwargs["now"] += timedelta(seconds=301)
    with pytest.raises((CapacityPolicyError, KeyError)):
        capacity.materialize_capacity(**kwargs)
    assert not kwargs["out_dir"].exists()


def test_zero_free_seats_are_zero_capacity_not_an_observation_failure(tmp_path):
    _, _, kwargs = fixture(tmp_path)
    p = Path(kwargs["license_queries"]["query"]["path"])
    query = json.loads(p.read_text())
    query["features"]["fixture_feature"] = {"total": 48, "used": 48, "free": 0}
    kwargs["license_queries"]["query"] = write(p, query)
    record = capacity.materialize_capacity(**kwargs)
    assert measured_worker_cap({**kwargs["snapshot"], "per_tool_capacity_evidence": record})[0] == 0


def test_capacity_output_is_no_clobber(tmp_path):
    _, _, kwargs = fixture(tmp_path)
    result = capacity.materialize_capacity(**kwargs)
    before = Path(result["path"]).read_bytes()
    with pytest.raises(FileExistsError):
        capacity.materialize_capacity(**kwargs)
    assert Path(result["path"]).read_bytes() == before


def test_bound_probe_attaches_capacity_before_publishing_unchanged_source(tmp_path, monkeypatch):
    from tests.test_broadband56_capacity_snapshot_adapter import SWAP_CONTROLLER as controller, _legacy_snapshot

    _, _, kwargs = fixture(tmp_path)
    raw = _legacy_snapshot(kwargs["now"])
    raw["resources"].update(kwargs["snapshot"]["resources"])
    source = write(tmp_path / "raw.json", raw)
    # The producer fixture is separate from the real campaign's science fields.
    fp = json.loads(Path(kwargs["footprint_record"]["path"]).read_text())
    backend = fp["derivation"]["baseline_backend"]
    backend_payload = json.loads(Path(backend["path"]).read_text())
    backend_payload.update(campaign_id=raw["campaign_id"], contract_fingerprint_sha256=raw["contract_fingerprint_sha256"])
    new_backend = write(Path(backend["path"]), backend_payload)
    fp["derivation"]["baseline_backend"] = new_backend
    for index, record in enumerate(fp["derivation"]["observations"]):
        value = json.loads(Path(record["path"]).read_text())
        if "backend" in value:
            value["backend"] = new_backend
        else:
            value["bindings"]["backend"] = new_backend
        fp["derivation"]["observations"][index] = write(Path(record["path"]), value)
    kwargs["footprint_record"] = write(Path(kwargs["footprint_record"]["path"]), capacity.derive_footprints(**fp["derivation"]))
    query_path = Path(kwargs["license_queries"]["query"]["path"])
    query = json.loads(query_path.read_text())
    query["environment_source"] = new_backend
    kwargs["license_queries"]["query"] = write(query_path, query)
    audit = {"schema": controller.isolation_identity.AUDIT_SCHEMA, "campaign_id": controller.CAMPAIGN_ID,
             "queue_id": controller.QUEUE_ID, "logical_supervisor_id": controller.SUPERVISOR_ID,
             "isolation": raw["isolation"], "simulator_action_taken": False, "campaign_data_modified": False}
    audit_path = Path(write(tmp_path / "audit.json", audit)["path"])
    common = Path(write(tmp_path / "bound.json", {})["path"])
    monkeypatch.setattr(controller, "_proc_counter", lambda *_: 0)
    monkeypatch.setattr(controller, "_run_isolation_identity_audit", lambda **_: audit_path)
    calls = []

    def builder(*, snapshot, out_dir, check_index):
        calls.append(check_index)
        result = capacity.materialize_capacity(**{**kwargs, "snapshot": snapshot})
        snapshot["resources"]["logical_cpu_count"] = 1  # Cannot mutate the publisher's copy.
        return result

    shim = SimpleNamespace(_read_json=lambda p, _: json.loads(p.read_text()), ControllerError=ValueError)
    factory = controller._swap_override_probe_factory(shim, original_run_probe=lambda *_: Path(source["path"]),
        override_receipt_path=common, overlay_manifest_path=common, operational_handoff_path=common,
        isolation_hotfix_handoff_path=common, supervisor_recovery_handoff_paths=[],
        isolation_auditor_path=common, isolation_module_path=common,
        isolation_lease_path=Path(kwargs["snapshot"]["supervisor_lease"]["path"]), isolation_lease_generation=1,
        backend_manifest_path=common, campaign_root=tmp_path, campaign_lock=common,
        python_bin=common, tool_capacity_builder=builder)
    published = json.loads(factory(common, tmp_path, 7).read_text())
    assert calls == [7]
    assert published["licenses"] == raw["licenses"]
    assert published["resources"]["logical_cpu_count"] == 192
    assert published["source_snapshot"] == source
    assert file_record(Path(source["path"])) == source
    assert measured_worker_cap(published)[0] == 2
