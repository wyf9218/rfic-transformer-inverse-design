"""Synthetic preflight tests only: no GDS, Cadence, Calibre or EMX execution."""
import ast
import csv
import json
from pathlib import Path

import pytest

from research.broadband56_nn import frequency_physical_lane as lane
from research.broadband56_nn.io import canonical_sha, save_json, sha256


def pin(path):
    return {"path": str(path), "sha256": sha256(path)}


def write_json(path, value):
    path.write_text(json.dumps(value))
    return pin(path)


def csv_file(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return pin(path)


@pytest.fixture
def lane_request(tmp_path):
    lower = [160, 160, 160, 160, 3, 20, 20, -90, 100, 100]
    upper = [520, 520, 520, 520, 12, 90, 90, 90, 320, 320]
    geometry = dict(zip(lane.GEOMETRY_FIELDS, [300, 300, 300, 300, 6, 50, 50, 0, 200, 200]))
    geometry_sha = lane.canonical_geometry_sha256(geometry)
    queue = [{"candidate_id": "research-geometry-1", "candidate_id_sha256": geometry_sha,
        "candidate_geometry_identity_sha256": geometry_sha, "geometry_sha256": geometry_sha,
        **{"geom__"+k: v for k, v in geometry.items()}}]
    targets = [{"target_id": f"t{i}", "frequency_ghz": 15} for i in range(3)]
    # Group names are opaque: a future Qscan group is not hardcoded to B/C.
    selected = [{"target_id": f"t{i}", "selection_group": "Qscan_q17"} for i in range(3)]
    mapping = [{"target_id": f"t{i}", "selection_group": "Qscan_q17",
        "status": "PENDING", "candidate_id_sha256": geometry_sha} for i in range(2)]
    mapping.append({"target_id": "t2", "selection_group": "Qscan_q17",
        "status": "FAIL_ANALYTIC_PRECHECK", "candidate_id_sha256": None})
    norm = {"frequency_ghz": 15, "synthetic": True}
    contract = {"field_names": list(lane.GEOMETRY_FIELDS), "lower": lower, "upper": upper,
                "units": "um", "grid_um": .005}
    artifacts = {
        "target_manifest.csv": csv_file(tmp_path / "targets.csv", targets),
        "emx_preselected.json": write_json(tmp_path / "preselection.json", {"selected": selected}),
        "normalizer.json": write_json(tmp_path / "normalizer.json", norm),
        "geometry_contract.json": write_json(tmp_path / "contract.json", contract),
    }
    identity = {"frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
                "normalizer_sha256": canonical_sha(norm), "contract_sha256": canonical_sha(contract)}
    for key in ("dataset", "data_manifest", "splits", "forward_checkpoint", "inverse_checkpoint"):
        path = tmp_path / key
        path.write_bytes((key + " SYNTHETIC_NOT_A_MODEL_OR_DATASET").encode())
        identity[key] = pin(path)
    freeze = {"model_id": "f15-synthetic", "config": {"study_id": "research-only"},
              "identity": identity, "artifacts": artifacts}
    bindings = {"evaluation_freeze": write_json(tmp_path / "freeze.json", freeze),
        "target_manifest": artifacts["target_manifest.csv"],
        "preselection": artifacts["emx_preselected.json"],
        "candidate_csv": csv_file(tmp_path / "queue.csv", queue),
        "target_candidate_map": write_json(tmp_path / "mapping.json", {"rows": mapping})}
    value = {"schema": lane.REQUEST_SCHEMA, "study_id": "research-only", "model_id": "f15-synthetic",
        "frequency_ghz": 15, "label_mode": "STRICT_LUMPED", "bindings": bindings,
        "candidate_count": 1, "runtime_pins": {}}
    path = tmp_path / "request.json"
    write_json(path, value)
    return path, value, tmp_path


def save_request(request):
    path, value, _ = request
    write_json(path, value)
    return path


def test_handoff_preserves_failed_targets_and_duplicate_candidate_mapping(lane_request):
    path, value, root = lane_request
    result = lane.prepare(path, root / "preflight")
    assert (result["N_selected"], result["N_unique_candidates"], result["N_analytic_precheck_failed"]) == (3, 1, 1)
    assert result["REAL_EMX_VALIDATION"] == "NOT_RUN"
    assert result["status"] == "PREPARED_RUNTIME_NOT_VERIFIED"
    assert result["physical_chain_runtime"] == "NOT_VERIFIED"
    assert result["simulator_launch_allowed_by_this_receipt"] is False
    assert result["resource_admission"] == result["license_availability"] == "NOT_CHECKED"
    assert all(x["status"] == "NOT_VERIFIED" for x in result["runtime_checks"].values())
    assert result["cadence_only_command_template"] is None
    with pytest.raises(FileExistsError):
        lane.prepare(path, root / "preflight")


def test_all_runtime_hashes_still_do_not_establish_launch_or_actual_chain(lane_request):
    path, value, root = lane_request
    for role in lane.RUNTIME_ROLES:
        file = root / ("runtime_" + role)
        file.write_bytes(b"SYNTHETIC NONEXECUTABLE")
        value["runtime_pins"][role] = pin(file)
    result = lane.prepare(save_request(lane_request), root / "out")
    assert all(x["status"] == "HASH_VERIFIED_ONLY" for x in result["runtime_checks"].values())
    command = result["cadence_only_command_template"]
    assert "--cadence-streamout-only" in command
    assert "--force-wideband-5-60-1p0" in command
    assert "--full-campaign-receipt" not in command
    assert command[command.index("--batch-size")+1] == "1"
    assert command[command.index("--jobs")+1] == "1"
    assert result["command_executed"] is False and result["physical_chain_runtime"] == "NOT_VERIFIED"


@pytest.mark.parametrize("key,value", [("model_id", "wrong-model"), ("study_id", "another-study"),
    ("frequency_ghz", 16), ("frequency_ghz", 15.0), ("frequency_ghz", True),
    ("label_mode", "POINTWISE_DESCRIPTOR_EXPERIMENTAL"), ("candidate_count", 2)])
def test_wrong_identity_or_count_fails_before_output(lane_request, key, value):
    path, raw, root = lane_request
    raw[key] = value
    with pytest.raises(ValueError):
        lane.prepare(save_request(lane_request), root / "out")
    assert not (root / "out").exists()


def test_changed_model_bytes_fail_before_output(lane_request):
    path, value, root = lane_request
    (root / "forward_checkpoint").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA mismatch"):
        lane.prepare(path, root / "out")
    assert not (root / "out").exists()


def test_missing_selected_target_cannot_be_replaced(lane_request):
    path, value, root = lane_request
    mapping = json.loads((root / "mapping.json").read_text())
    mapping["rows"].pop()
    value["bindings"]["target_candidate_map"] = write_json(root / "mapping.json", mapping)
    with pytest.raises(ValueError, match="exact full selected set"):
        lane.prepare(save_request(lane_request), root / "out")


def test_success_based_extra_candidate_rejected(lane_request):
    path, value, root = lane_request
    with (root / "queue.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    extra = dict(rows[0]); extra["candidate_id_sha256"] = "a"*64
    value["bindings"]["candidate_csv"] = csv_file(root / "queue.csv", rows+[extra])
    with pytest.raises(ValueError, match="unselected or replaced"):
        lane.prepare(save_request(lane_request), root / "out")


@pytest.mark.parametrize("bad", ["nan", "10000", "300.001"])
def test_invalid_geometry_never_enters_cadence_command(lane_request, bad):
    path, value, root = lane_request
    with (root / "queue.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["geom__primary_outer_width_um"] = bad
    value["bindings"]["candidate_csv"] = csv_file(root / "queue.csv", rows)
    with pytest.raises(ValueError):
        lane.prepare(save_request(lane_request), root / "out")


def test_symlinked_input_rejected(lane_request):
    path, value, root = lane_request
    link = root / "link.csv"; link.symlink_to(root / "queue.csv")
    value["bindings"]["candidate_csv"]["path"] = str(link)
    with pytest.raises(ValueError, match="symlink"):
        lane.prepare(save_request(lane_request), root / "out")


@pytest.fixture
def calibre_fixture(tmp_path):
    gds = tmp_path / "SYNTHETIC_NOT_GDS.bin"; gds.write_bytes(b"pretend bytes, not parsed")
    digest = "b"*64
    checks = {name: True for name in lane.GEOMETRY_CHECKS}
    audit = {"overall_status": "PASS", "candidate_id_sha256": digest,
        "candidate_geometry_identity_sha256": digest, "gds_sha256": sha256(gds), "checks": checks}
    audit_pin = write_json(tmp_path / "audit.json", audit)
    receipt = {**audit, "schema": "candidate_bound_tsmc65_calibre_macro_ip_back_end_drc_v1",
        "gds_path": str(gds), "geometry_audit_path": audit_pin["path"],
        "geometry_audit_sha256": audit_pin["sha256"], "blocking_drc_violation_count": 0,
        "drc_engine": "Calibre", "drc_scope": "foundry_macro_ip_back_end", "gds_top_cell": "TRANSFORMER",
        "checks": {name: True for name in lane.CALIBRE_CHECKS}}
    for name in ("drc_report", "drc_rule_deck", "drc_source_rule_deck"):
        file = tmp_path / name; file.write_text("synthetic")
        receipt[name+"_path"] = str(file); receipt[name+"_sha256"] = sha256(file)
    receipt_path = tmp_path / "calibre.json"
    args = {"candidate_id_sha256": digest, "geometry_identity_sha256": digest, "gds_pin": pin(gds),
        "geometry_audit_pin": audit_pin, "calibre_receipt_pin": write_json(receipt_path, receipt)}
    return args, receipt, receipt_path


def test_same_raw_gds_bound_zero_blocking_is_narrow_not_runtime_verified(calibre_fixture):
    args, _, _ = calibre_fixture
    result = lane.verify_same_gds_zero_blocking(**args)
    assert result["status"] == "SAME_RAW_GDS_AND_ZERO_BLOCKING_BINDING_VERIFIED_ONLY"
    assert result["ready_for_emx"] is False and result["emx_invoked"] is False


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(blocking_drc_violation_count=False),
    lambda r: r.update(blocking_drc_violation_count=1),
    lambda r: r.update(gds_sha256="f"*64),
    lambda r: r.update(candidate_id_sha256="a"*64),
    lambda r: r["checks"].update(foundry_drc_pass="True"),
    lambda r: r["checks"].pop("foundry_via_stack_and_landing_pad_pass"),
    lambda r: r.update(drc_engine="Mock"),
    lambda r: r.update(geometry_audit_sha256="c"*64),
])
def test_incomplete_or_wrong_calibre_evidence_rejected(calibre_fixture, mutate):
    args, receipt, path = calibre_fixture
    mutate(receipt); args["calibre_receipt_pin"] = write_json(path, receipt)
    with pytest.raises(ValueError):
        lane.verify_same_gds_zero_blocking(**args)


def test_post_calibre_gds_change_is_not_allowed(calibre_fixture):
    args, _, _ = calibre_fixture
    Path(args["gds_pin"]["path"]).write_bytes(b"changed after Calibre")
    with pytest.raises(ValueError, match="SHA mismatch"):
        lane.verify_same_gds_zero_blocking(**args)


def test_no_simulator_process_or_controller_calls_exist_in_preflight_module():
    source = Path(lane.__file__).read_text()
    tree = ast.parse(source)
    imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
    assert not imports & {"subprocess", "multiprocessing", "signal", "socket"}
    forbidden = {"Popen", "system", "fork", "kill", "run_solver", "_run_emx", "execv", "spawn"}
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & forbidden
