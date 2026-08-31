from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (
    CALIBRE_ZERO_BLOCKING_PASS_DECISION,
    CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_calibre_zero_blocking_receipt.py"
CANDIDATE_ID = "1" * 64
GEOMETRY_ID = "2" * 64


def test_builds_exact_zero_blocking_receipt_without_simulator(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    receipt_path = fixture["out_dir"] / "CALIBRE_ZERO_BLOCKING_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA
    assert receipt["decision"] == CALIBRE_ZERO_BLOCKING_PASS_DECISION
    assert receipt["campaign_id"] == CAMPAIGN_ID
    assert receipt["contract_fingerprint_sha256"] == SCIENTIFIC_CONTRACT_FINGERPRINT
    assert receipt["candidate_id_sha256"] == CANDIDATE_ID
    assert receipt["geometry_identity_sha256"] == GEOMETRY_ID
    assert receipt["gds_sha256"] == _sha256(fixture["gds"])
    assert receipt["calibre_blocking_violations"] == 0
    assert receipt["calibre_total_violations"] == 1
    assert receipt["calibre_documented_warnings"] == 1
    assert receipt["simulator_action_taken"] is False
    assert receipt["gds_generated_or_modified_by_this_builder"] is False
    sums = (fixture["out_dir"] / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert _sha256(receipt_path) in sums


def test_rejects_blocking_violation_without_official_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    summary = json.loads(fixture["summary"].read_text(encoding="utf-8"))
    summary["blocking_drc_violation_count"] = 1
    fixture["summary"].write_text(json.dumps(summary), encoding="utf-8")
    fixture["summary_sha256"] = _sha256(fixture["summary"])

    result = _run(fixture)
    assert result.returncode == 2
    assert "blocking_zero" in result.stderr
    assert not fixture["out_dir"].exists()


def test_rejects_summary_bound_to_other_gds(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    summary = json.loads(fixture["summary"].read_text(encoding="utf-8"))
    summary["gds_sha256"] = "3" * 64
    fixture["summary"].write_text(json.dumps(summary), encoding="utf-8")
    fixture["summary_sha256"] = _sha256(fixture["summary"])

    result = _run(fixture)
    assert result.returncode == 2
    assert "gds_sha256" in result.stderr
    assert not fixture["out_dir"].exists()


def test_rejects_manifest_top_cell_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
    manifest["top_cell"] = "OTHER"
    fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    fixture["manifest_sha256"] = _sha256(fixture["manifest"])

    result = _run(fixture)
    assert result.returncode == 2
    assert "gds_top_cell" in result.stderr
    assert not fixture["out_dir"].exists()


def test_rejects_geometry_audit_that_is_not_pass(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    audit = {"overall_status": "FAIL"}
    fixture["geometry_audit"].write_text(json.dumps(audit), encoding="utf-8")
    summary = json.loads(fixture["summary"].read_text(encoding="utf-8"))
    summary["geometry_audit_sha256"] = _sha256(fixture["geometry_audit"])
    fixture["summary"].write_text(json.dumps(summary), encoding="utf-8")
    fixture["summary_sha256"] = _sha256(fixture["summary"])

    result = _run(fixture)
    assert result.returncode == 2
    assert "geometry audit is not PASS" in result.stderr
    assert not fixture["out_dir"].exists()


def test_rejects_existing_output_before_reading_as_success(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["out_dir"].mkdir()
    result = _run(fixture)
    assert result.returncode == 2
    assert "refusing existing output directory" in result.stderr


def _fixture(root: Path) -> dict[str, Path | str]:
    config = root / "config.yaml"
    config.write_text("target:\n  start_hz: 5000000000\n", encoding="utf-8")
    gds = root / "candidate.gds"
    gds.write_bytes(b"exact candidate GDS bytes")
    manifest = root / "candidate.layout.json"
    manifest.write_text(json.dumps({"top_cell": "TRANSFORMER"}), encoding="utf-8")
    report = root / "DRC.rep"
    report.write_text("TOTAL RESULTS GENERATED = 1\n", encoding="utf-8")
    geometry_audit = root / "geometry_audit.json"
    geometry_audit.write_text(json.dumps({"overall_status": "PASS"}), encoding="utf-8")
    summary = root / "drc_summary.json"
    summary.write_text(
        json.dumps(
            {
                "schema": "candidate_bound_tsmc65_calibre_macro_ip_back_end_drc_v1",
                "overall_status": "PASS",
                "candidate_id_sha256": CANDIDATE_ID,
                "candidate_geometry_identity_sha256": GEOMETRY_ID,
                "gds_path": str(gds),
                "gds_sha256": _sha256(gds),
                "gds_top_cell": "TRANSFORMER",
                "drc_violation_count": 1,
                "blocking_drc_violation_count": 0,
                "documented_warning_count": 1,
                "drc_report_path": str(report),
                "drc_report_sha256": _sha256(report),
                "geometry_audit_path": str(geometry_audit),
                "geometry_audit_sha256": _sha256(geometry_audit),
                "production_campaign_modification_authorized": False,
            }
        ),
        encoding="utf-8",
    )
    return {
        "config": config,
        "config_sha256": _sha256(config),
        "gds": gds,
        "gds_sha256": _sha256(gds),
        "manifest": manifest,
        "manifest_sha256": _sha256(manifest),
        "report": report,
        "geometry_audit": geometry_audit,
        "summary": summary,
        "summary_sha256": _sha256(summary),
        "out_dir": root / "receipt_out",
    }


def _run(fixture: dict[str, Path | str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(fixture["config"]),
            "--expected-config-sha256",
            str(fixture["config_sha256"]),
            "--gds",
            str(fixture["gds"]),
            "--expected-gds-sha256",
            str(fixture["gds_sha256"]),
            "--manifest",
            str(fixture["manifest"]),
            "--expected-manifest-sha256",
            str(fixture["manifest_sha256"]),
            "--calibre-summary",
            str(fixture["summary"]),
            "--expected-calibre-summary-sha256",
            str(fixture["summary_sha256"]),
            "--candidate-id-sha256",
            CANDIDATE_ID,
            "--geometry-identity-sha256",
            GEOMETRY_ID,
            "--out-dir",
            str(fixture["out_dir"]),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
