from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
)
from rfic_transformer_inverse_design import layout as layout_package


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_calibre_batch.py"


def test_calibre_batch_preserves_pass_and_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source_evidence_before = {
        path: path.read_bytes()
        for path in [
            *fixture["source_audits"],
            *fixture["physical_audits"],
        ]
    }
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads((out / "CALIBRE_BATCH_ROLE_RECEIPT.json").read_text())
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["calibre_pass_count"] == 1
    assert receipt["calibre_fail_count"] == 1
    assert receipt["failed_candidates_counted_as_accepted"] is False
    assert receipt["simulator_action_taken"] is True
    assert receipt["delegate_execution_mode"] == (
        "importlib-main-with-current-contract-required-checks-v1"
    )
    assert len(receipt["delegate_execution_contract_sha256"]) == 64
    delegate_input = Path(receipt["delegate_input_index"]["path"])
    assert delegate_input.name == "CALIBRE_DELEGATE_INPUT_INDEX.csv"
    delegate_rows = _read_csv(delegate_input)
    assert all(
        row["gds_timestamp_normalization_algorithm"]
        == GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        for row in delegate_rows
    )
    delegate_audit_index = _read_csv(
        Path(receipt["delegate_geometry_audit_index"]["path"])
    )
    assert len(delegate_audit_index) == 2
    for row in delegate_audit_index:
        audit = json.loads(
            Path(row["delegate_geometry_audit_path"]).read_text()
        )
        assert audit["overall_status"] == "PASS"
        assert audit["gds_timestamp_normalization_algorithm"] == (
            GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        )
        assert audit["teacher_only_foundry_slotting_prechecks_applied"] is False
        assert set(audit["checks"]) == {
            "geometry_range_pass",
            "topology_pass",
            "line_width_sync_pass",
            "angle_45_135_pass",
            "ground_clearance_pass",
        }
        assert all(audit["checks"].values())
    assert "gds_timestamp_normalization_algorithm" not in _csv_fields(
        fixture["input_index"]
    )
    for path in fixture["source_audits"]:
        assert "gds_timestamp_normalization_algorithm" not in json.loads(
            path.read_text()
        )
    assert all(
        path.read_bytes() == before
        for path, before in source_evidence_before.items()
    )

    passed = _read_csv(out / "CALIBRE_PASS_INDEX.csv")
    failed = _read_csv(out / "CALIBRE_FAILURE_INDEX.csv")
    evidence = _read_csv(out / "CALIBRE_EVIDENCE_INDEX.csv")
    assert [row["candidate_id_sha256"] for row in passed] == ["a" * 64]
    assert [row["candidate_id_sha256"] for row in failed] == ["f" * 64]
    assert {row["overall_status"] for row in evidence} == {"PASS", "FAIL"}
    assert passed[0]["gds_physical_identity_status"] == "PASS"
    assert passed[0]["blocking_drc_violation_count"] == "0"


def test_calibre_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt = fixture["out_dir"] / "CALIBRE_BATCH_ROLE_RECEIPT.json"
    before = receipt.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt.read_bytes() == before


def test_calibre_batch_propagates_an_empty_gds_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _empty_csv_keep_header(fixture["input_index"])
    input_receipt = json.loads(fixture["input_receipt"].read_text())
    input_receipt["pass_index"] = _record(fixture["input_index"])
    fixture["input_receipt"].write_text(json.dumps(input_receipt))

    result = _run(fixture)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (fixture["out_dir"] / "CALIBRE_BATCH_ROLE_RECEIPT.json").read_text()
    )
    assert receipt["submitted_count"] == 0
    assert receipt["calibre_pass_count"] == 0
    assert receipt["calibre_fail_count"] == 0
    assert receipt["simulator_action_taken"] is False


def test_calibre_batch_rejects_failed_physical_identity_check(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    physical_audit = fixture["physical_audits"][0]
    payload = json.loads(physical_audit.read_text())
    payload["checks"]["candidate_bound_gds_identity_pass"] = False
    physical_audit.write_text(json.dumps(payload))
    _refresh_input_evidence(fixture)

    result = _run(fixture)
    assert result.returncode == 2
    assert "physical-identity checks are not all PASS" in result.stderr


def test_calibre_batch_rejects_line_width_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    rows = _read_csv(fixture["input_index"])
    rows[0]["geom__line_width_um"] = "5.25"
    _write_csv(fixture["input_index"], rows)
    _refresh_input_receipt(fixture)

    result = _run(fixture)
    assert result.returncode == 2
    assert "line_width_sync_pass" in result.stderr


def test_calibre_batch_rejects_legacy_delegate_contract_drift(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    delegate = fixture["delegate"]
    delegate.write_text(
        delegate.read_text().replace(
            '    "foundry_bridge_connection_pass",\n', "", 1
        )
    )
    _refresh_manifest_authorization(fixture)

    result = _run(fixture)
    assert result.returncode == 2
    assert "pinned Calibre delegate check contract drifted" in result.stderr


def test_legacy_gds_hash_compatibility_module_is_scoped(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runner = _load_runner_module()
    module_name = runner.LEGACY_GDS_HASH_MODULE_NAME
    delegate = tmp_path / "import_only_delegate.py"
    delegate.write_text(_fake_import_only_delegate_source())

    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delattr(layout_package, "gds_hash", raising=False)
    result = runner._run_delegate_with_current_contract(delegate, [])
    assert result.returncode == 0
    assert module_name not in sys.modules
    assert not hasattr(layout_package, "gds_hash")

    previous = types.ModuleType(module_name)
    monkeypatch.setitem(sys.modules, module_name, previous)
    monkeypatch.setattr(layout_package, "gds_hash", previous, raising=False)
    result = runner._run_delegate_with_current_contract(delegate, [])
    assert result.returncode == 0
    assert sys.modules[module_name] is previous
    assert layout_package.gds_hash is previous


def _fixture(root: Path) -> dict[str, Any]:
    process = root / "TSMC65_05_12_26" / "process.proc"
    process.parent.mkdir()
    process.write_text("process")
    rows = []
    source_audits = []
    physical_audits = []
    for candidate, geometry, normalized in (
        ("a" * 64, "1" * 64, "b" * 64),
        ("f" * 64, "2" * 64, "c" * 64),
    ):
        gds = root / f"{candidate[:1]}.gds"
        gds.write_bytes(f"gds-{candidate[:1]}".encode())
        evaluation = root / f"{candidate[:1]}_evaluation.json"
        evaluation.write_text(
            json.dumps(
                {
                    "ok": True,
                    "geometry_check": {
                        "ok": True,
                        "errors": [],
                        "metrics": {
                            "power_line_8port_bridge_width_um": 5.0,
                            "power_line_8port_primary_bridge_width_um": 5.0,
                            "power_line_8port_secondary_bridge_width_um": 5.0,
                            "primary_winding_centerline_min_internal_angle_deg": 135.0,
                            "primary_winding_centerline_max_internal_angle_deg": 135.0,
                            "primary_winding_centerline_min_terminal_angle_deg": 90.0,
                            "primary_winding_centerline_max_terminal_angle_deg": 90.0,
                            "secondary_winding_centerline_min_internal_angle_deg": 135.0,
                            "secondary_winding_centerline_max_internal_angle_deg": 135.0,
                            "secondary_winding_centerline_min_terminal_angle_deg": 90.0,
                            "secondary_winding_centerline_max_terminal_angle_deg": 90.0,
                        },
                    },
                }
            )
        )
        source_audit = root / f"{candidate[:1]}_geometry_audit.json"
        source_audit.write_text(
            json.dumps(
                {
                    "overall_status": "PASS",
                    "candidate_id_sha256": candidate,
                    "candidate_geometry_identity_sha256": geometry,
                    "gds_sha256": _sha(gds),
                    "gds_timestamp_normalized_sha256": normalized,
                    "evaluation_summary_path": str(evaluation),
                    "evaluation_summary_sha256": _sha(evaluation),
                    "checks": {
                        "candidate_geometry_recomputed": True,
                        "dataset_geometry_recomputed": True,
                        "evaluation_geometry_recomputed": True,
                        "cadence_gds_present_and_nonempty": True,
                        "direct_gds_present_and_nonempty": True,
                        "layout_manifest_present_and_nonempty": True,
                    },
                }
            )
        )
        physical_audit = root / f"{candidate[:1]}_physical_audit.json"
        physical_audit.write_text(
            json.dumps(
                {
                    "overall_status": "PASS",
                    "candidate_id_sha256": candidate,
                    "candidate_geometry_identity_sha256": geometry,
                    "cadence_gds_sha256": _sha(gds),
                    "cadence_gds_timestamp_normalized_sha256": normalized,
                    "checks": {"candidate_bound_gds_identity_pass": True},
                }
            )
        )
        source_audits.append(source_audit)
        physical_audits.append(physical_audit)
        rows.append(
            {
                "candidate_id_sha256": candidate,
                "candidate_geometry_identity_sha256": geometry,
                "analytical_status": "PASS",
                "topology_status": "PASS",
                "top_metal_drc_status": "PASS",
                "drc_status": "PASS",
                "gds_physical_identity_status": "PASS",
                "geom__line_width_um": "5.0",
                "gds_path": str(gds),
                "gds_sha256": _sha(gds),
                "gds_timestamp_normalized_sha256": normalized,
                "gds_timestamp_normalized_sha256_algorithm": (
                    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
                ),
                "geometry_audit_path": str(source_audit),
                "geometry_audit_sha256": _sha(source_audit),
                "gds_physical_identity_audit_path": str(physical_audit),
                "gds_physical_identity_audit_sha256": _sha(physical_audit),
            }
        )
    input_index = root / "gds_pass_index.csv"
    _write_csv(input_index, rows)
    input_receipt = root / "GDS_PHYSICAL_IDENTITY_BATCH_ROLE_RECEIPT.json"
    input_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "stage": "GOLDEN",
                "pass_index": _record(input_index),
            }
        )
    )
    delegate = root / "fake_calibre_delegate.py"
    delegate.write_text(_fake_delegate_source())
    archive = root / "foundry.tar.gz"
    archive.write_bytes(b"foundry-archive")
    deck = root / "deck.svrf"
    deck.write_text("foundry-deck")
    guide = root / "guide.txt"
    guide.write_text("foundry-guide")
    manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "calibre_runner": _record(SCRIPT),
                    "calibre_batch_delegate": _record(delegate),
                },
                "runtime_identities": {
                    "python_executable": _record(Path(sys.executable).resolve()),
                    "calibre_foundry_archive": _record(archive),
                    "calibre_rule_deck": _record(deck),
                    "calibre_user_guide": _record(guide),
                    "emx_process_file": _record(process),
                },
            }
        )
    )
    authorization = root / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    authorization.write_text(
        json.dumps(
            {
                "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
                "overall_status": "PASS",
                "decision": FULL_CAMPAIGN_PASS_DECISION,
                "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "backend_identity_manifest": {"sha256": _sha(manifest)},
                "calibre_authorized_within_current_stage": True,
            }
        )
    )
    return {
        "input_index": input_index,
        "input_receipt": input_receipt,
        "delegate": delegate,
        "manifest": manifest,
        "authorization": authorization,
        "out_dir": root / "out",
        "source_audits": source_audits,
        "physical_audits": physical_audits,
    }


def _run(fixture: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--stage",
            "GOLDEN",
            "--input-role-receipt",
            str(fixture["input_receipt"]),
            "--backend-identity-manifest",
            str(fixture["manifest"]),
            "--full-campaign-receipt",
            str(fixture["authorization"]),
            "--out-dir",
            str(fixture["out_dir"]),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _fake_delegate_source() -> str:
    return r'''import argparse
import csv
import hashlib
import json
from pathlib import Path

from rfic_transformer_inverse_design.layout.gds_hash import (
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
    gds_timestamp_normalized_sha256,
)

REQUIRED_GEOMETRY_CHECKS = (
    "geometry_range_pass",
    "topology_pass",
    "line_width_sync_pass",
    "angle_45_135_pass",
    "ground_clearance_pass",
    "foundry_layout_audit_pass",
    "manufacturing_grid_canonicalization_pass",
    "foundry_slotted_ground_frame_pass",
    "foundry_power_line_contract_pass",
    "foundry_via_stack_and_landing_pad_pass",
    "foundry_bridge_connection_pass",
)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--input-index-csv", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--foundry-archive", required=True)
    p.add_argument("--expected-archive-sha256", required=True)
    p.add_argument("--expected-deck-sha256", required=True)
    p.add_argument("--expected-user-guide-sha256", required=True)
    args, _ = p.parse_known_args(argv)
    if GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM != (
        "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
    ):
        return 13
    if gds_timestamp_normalized_sha256.__module__ != (
        "rfic_transformer_inverse_design.campaigns.broadband56_gds_identity"
    ):
        return 14
    out = Path(args.out_dir)
    out.mkdir(parents=True)
    with Path(args.input_index_csv).open(newline="", encoding="utf-8") as h:
        reader = csv.DictReader(h)
        source_fields = set(reader.fieldnames or [])
        source = list(reader)
    required_fields = {
        "candidate_id_sha256",
        "candidate_geometry_identity_sha256",
        "gds_path",
        "gds_timestamp_normalized_sha256",
        "gds_timestamp_normalization_algorithm",
        "geometry_audit_path",
    }
    if not required_fields.issubset(source_fields):
        return 9
    if any(
        row["gds_timestamp_normalization_algorithm"]
        != "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
        for row in source
    ):
        return 10
    expected_current_checks = (
        "geometry_range_pass",
        "topology_pass",
        "line_width_sync_pass",
        "angle_45_135_pass",
        "ground_clearance_pass",
    )
    if REQUIRED_GEOMETRY_CHECKS != expected_current_checks:
        return 11
    rows = []
    for row in source:
        audit = json.loads(Path(row["geometry_audit_path"]).read_text())
        if not (
            audit["overall_status"] == "PASS"
            and tuple(audit["effective_required_geometry_checks"])
            == expected_current_checks
            and audit["teacher_only_foundry_slotting_prechecks_applied"] is False
            and all(audit["checks"].get(name) is True for name in expected_current_checks)
        ):
            return 12
        success = not row["candidate_id_sha256"].startswith("f")
        candidate_dir = out / "candidates" / row["candidate_id_sha256"][:16]
        candidate_dir.mkdir(parents=True)
        summary_path = candidate_dir / "drc_summary.json"
        if success:
            summary = {
                "overall_status": "PASS",
                "candidate_id_sha256": row["candidate_id_sha256"],
                "candidate_geometry_identity_sha256": row["candidate_geometry_identity_sha256"],
            }
            summary_path.write_text(json.dumps(summary))
            summary_sha = hashlib.sha256(summary_path.read_bytes()).hexdigest()
        else:
            summary_sha = ""
        rows.append({
            "candidate_id_sha256": row["candidate_id_sha256"],
            "candidate_geometry_identity_sha256": row["candidate_geometry_identity_sha256"],
            "drc_summary_path": str(summary_path) if success else "",
            "drc_summary_sha256": summary_sha,
            "drc_violation_count": "0" if success else "4",
            "blocking_drc_violation_count": "0" if success else "4",
            "documented_warning_count": "0",
            "overall_status": "PASS" if success else "FAIL",
            "error": "" if success else "synthetic blocking violations",
        })
    index = out / "drc_index.csv"
    with index.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    pass_count = sum(row["overall_status"] == "PASS" for row in rows)
    archive = Path(args.foundry_archive)
    summary = {
        "schema": "tsmc65_calibre_macro_ip_back_end_drc_batch_v1",
        "overall_status": "PASS" if pass_count == len(rows) else "FAIL",
        "candidate_count": len(rows),
        "pass_count": pass_count,
        "fail_count": len(rows) - pass_count,
        "foundry_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "foundry_source_deck_sha256": args.expected_deck_sha256,
        "foundry_user_guide_sha256": args.expected_user_guide_sha256,
        "drc_index_csv": str(index),
        "drc_index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
    }
    (out / "tsmc65_calibre_macro_drc_batch_summary.json").write_text(
        json.dumps(summary)
    )
    return 0
'''


def _fake_import_only_delegate_source() -> str:
    return r'''from rfic_transformer_inverse_design.layout.gds_hash import (
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
    gds_timestamp_normalized_sha256,
)

REQUIRED_GEOMETRY_CHECKS = (
    "geometry_range_pass",
    "topology_pass",
    "line_width_sync_pass",
    "angle_45_135_pass",
    "ground_clearance_pass",
    "foundry_layout_audit_pass",
    "manufacturing_grid_canonicalization_pass",
    "foundry_slotted_ground_frame_pass",
    "foundry_power_line_contract_pass",
    "foundry_via_stack_and_landing_pad_pass",
    "foundry_bridge_connection_pass",
)


def main(argv=None):
    if GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM != (
        "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
    ):
        return 21
    if gds_timestamp_normalized_sha256.__module__ != (
        "rfic_transformer_inverse_design.campaigns.broadband56_gds_identity"
    ):
        return 22
    return 0
'''


def _load_runner_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_test_run_broadband56_v2_calibre_batch", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refresh_input_evidence(fixture: dict[str, Any]) -> None:
    rows = _read_csv(fixture["input_index"])
    physical_by_candidate = {
        json.loads(path.read_text())["candidate_id_sha256"]: path
        for path in fixture["physical_audits"]
    }
    for row in rows:
        audit = physical_by_candidate[row["candidate_id_sha256"]]
        row["gds_physical_identity_audit_sha256"] = _sha(audit)
    _write_csv(fixture["input_index"], rows)
    _refresh_input_receipt(fixture)


def _refresh_input_receipt(fixture: dict[str, Any]) -> None:
    receipt = json.loads(fixture["input_receipt"].read_text())
    receipt["pass_index"] = _record(fixture["input_index"])
    fixture["input_receipt"].write_text(json.dumps(receipt))


def _refresh_manifest_authorization(fixture: dict[str, Any]) -> None:
    manifest = json.loads(fixture["manifest"].read_text())
    manifest["script_identities"]["calibre_batch_delegate"] = _record(
        fixture["delegate"]
    )
    fixture["manifest"].write_text(json.dumps(manifest))
    authorization = json.loads(fixture["authorization"].read_text())
    authorization["backend_identity_manifest"]["sha256"] = _sha(
        fixture["manifest"]
    )
    fixture["authorization"].write_text(json.dumps(authorization))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _csv_fields(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _empty_csv_keep_header(path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        fields = list(csv.DictReader(handle).fieldnames or [])
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writeheader()


def _record(path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
