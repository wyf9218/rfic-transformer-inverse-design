from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_v2_stage_attempt_products.py"


def test_attempt_products_close_mixed_terminal_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    receipt = json.loads(
        (fixture["out_dir"] / "STAGE_ATTEMPT_PRODUCTS_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["raw_candidate_count"] == 4
    assert receipt["accepted_count"] == 1
    assert receipt["rejected_count"] == 3
    assert receipt["geometry_frequency_rows"] == 56
    assert receipt["simulator_action_taken"] is False

    ledger = _read_csv(fixture["out_dir"] / "ATTEMPT_LEDGER.csv")
    assert [row["terminal_stage"] for row in ledger] == [
        "CADENCE_FAILURE",
        "CALIBRE_FAILURE",
        "EMX_FAILURE",
        "ACCEPTED",
    ]
    assert ledger[-1]["accepted_sequence"] == "1"
    assert ledger[-1]["fresh_real_emx"] == "true"
    assert ledger[1]["calibre_blocking_violations"] == "1"
    funnel = {
        row["stage"]: int(row["count"])
        for row in _read_csv(fixture["out_dir"] / "FAILURE_FUNNEL.csv")
    }
    assert funnel["raw_geometry_candidates"] == 4
    assert funnel["cadence_failures"] == 1
    assert funnel["calibre_blocking_failures"] == 1
    assert funnel["emx_failures"] == 1
    assert funnel["accepted_geometries"] == 1


def test_attempt_products_reject_gds_identity_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    gds_receipt = json.loads(fixture["gds_receipt"].read_text(encoding="utf-8"))
    gds_pass_path = Path(gds_receipt["pass_index"]["path"])
    rows = _read_csv(gds_pass_path)
    failed = rows.pop()
    failure_path = tmp_path / "gds_fail.csv"
    _write_csv(
        failure_path,
        list(failed),
        [{**failed, "terminal_stage": "gds_identity", "error": "hash mismatch"}],
    )
    _write_csv(gds_pass_path, list(rows[0]), rows)
    gds_receipt["pass_index"] = _record(gds_pass_path)
    gds_receipt["failure_index"] = _record(failure_path)
    gds_receipt["identity_pass_count"] = 2
    gds_receipt["identity_fail_count"] = 1
    fixture["gds_receipt"].write_text(json.dumps(gds_receipt), encoding="utf-8")
    _rebind_downstream(fixture)

    result = _run(fixture)
    assert result.returncode == 2
    assert "provenance failures" in result.stderr
    assert not fixture["out_dir"].exists()


def test_attempt_products_are_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt_path = fixture["out_dir"] / "STAGE_ATTEMPT_PRODUCTS_ROLE_RECEIPT.json"
    before = receipt_path.read_bytes()
    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt_path.read_bytes() == before


def _fixture(root: Path) -> dict[str, Path]:
    candidates = [_candidate(index) for index in range(4)]
    raw_path = root / "raw_candidates.csv"
    _write_csv(raw_path, list(candidates[0]), candidates)

    evidence = root / "evidence"
    evidence.mkdir()
    gds_paths = []
    for index in range(1, 4):
        path = evidence / f"candidate_{index}.gds"
        path.write_bytes(f"GDS-{index}".encode())
        gds_paths.append(path)

    calibre_report = evidence / "calibre_blocking.rep"
    calibre_report.write_text("BLOCKING=1\n", encoding="utf-8")
    calibre_summary = evidence / "calibre_blocking_summary.json"
    calibre_summary.write_text(
        json.dumps(
            {
                "drc_report_path": str(calibre_report),
                "drc_report_sha256": _sha(calibre_report),
            }
        ),
        encoding="utf-8",
    )
    zero_reports = []
    zero_receipts = []
    for index in (2, 3):
        report = evidence / f"calibre_zero_{index}.rep"
        report.write_text("BLOCKING=0\n", encoding="utf-8")
        receipt = evidence / f"calibre_zero_{index}.json"
        receipt.write_text(
            json.dumps(
                {
                    "gds_path": str(gds_paths[index - 1]),
                    "gds_sha256": _sha(gds_paths[index - 1]),
                    "calibre_report_path": str(report),
                    "calibre_report_sha256": _sha(report),
                }
            ),
            encoding="utf-8",
        )
        zero_reports.append(report)
        zero_receipts.append(receipt)

    emx_failure_evidence = evidence / "emx_failure.json"
    emx_failure_evidence.write_text("{\"return_code\": 2}\n", encoding="utf-8")
    emx_command = evidence / "emx_command.json"
    emx_command.write_text("[\"emx\", \"candidate_3.gds\"]\n", encoding="utf-8")
    s4p = evidence / "candidate_3.s4p"
    s4p.write_text("fresh exact56 fixture\n", encoding="utf-8")
    exact_receipt = evidence / "candidate_3_exact_emx.json"
    exact_receipt.write_text(
        json.dumps(
            {
                "emx_output": {
                    "emx_command_path": str(emx_command),
                    "emx_command_sha256": _sha(emx_command),
                    "touchstone_path": str(s4p),
                    "touchstone_sha256": _sha(s4p),
                }
            }
        ),
        encoding="utf-8",
    )

    cadence_pass = candidates[1:]
    cadence_fail = [{**candidates[0], "terminal_stage": "cadence", "error": "streamout failed"}]
    cadence_pass_path = root / "cadence_pass.csv"
    cadence_fail_path = root / "cadence_fail.csv"
    _write_csv(cadence_pass_path, list(cadence_pass[0]), cadence_pass)
    _write_csv(cadence_fail_path, list(cadence_fail[0]), cadence_fail)

    gds_rows = [
        {
            **candidate,
            "gds_path": str(gds),
            "gds_sha256": _sha(gds),
        }
        for candidate, gds in zip(candidates[1:], gds_paths)
    ]
    gds_pass_path = root / "gds_pass.csv"
    gds_fail_path = root / "gds_fail.csv"
    _write_csv(gds_pass_path, list(gds_rows[0]), gds_rows)
    _write_csv(gds_fail_path, ["candidate_id_sha256", "geometry_sha256"], [])

    calibre_pass = gds_rows[1:]
    calibre_fail = [
        {
            **gds_rows[0],
            "terminal_stage": "calibre",
            "drc_summary_path": str(calibre_summary),
            "drc_summary_sha256": _sha(calibre_summary),
            "blocking_drc_violation_count": 1,
            "error": "blocking DRC",
        }
    ]
    calibre_pass_path = root / "calibre_pass.csv"
    calibre_fail_path = root / "calibre_fail.csv"
    _write_csv(calibre_pass_path, list(calibre_pass[0]), calibre_pass)
    _write_csv(calibre_fail_path, list(calibre_fail[0]), calibre_fail)

    zero_rows = []
    for candidate, gds_row, receipt in zip(candidates[2:], gds_rows[1:], zero_receipts):
        zero_rows.append(
            {
                **gds_row,
                "calibre_receipt_path": str(receipt),
                "calibre_receipt_sha256": _sha(receipt),
            }
        )
    zero_pass_path = root / "zero_pass.csv"
    zero_fail_path = root / "zero_fail.csv"
    _write_csv(zero_pass_path, list(zero_rows[0]), zero_rows)
    _write_csv(zero_fail_path, ["candidate_id_sha256", "geometry_sha256"], [])

    exact_pass = [
        {
            **candidates[3],
            "accepted_sequence": 1,
            "exact_gds_emx_receipt_path": str(exact_receipt),
            "exact_gds_emx_receipt_sha256": _sha(exact_receipt),
        }
    ]
    exact_fail = [
        {
            **candidates[2],
            "terminal_stage": "EMX_FAILURE",
            "return_code": 2,
            "failure_path": "",
            "failure_sha256": "",
            "s4p_path": "",
            "s4p_sha256": "",
            "frequency_points": 0,
            "fresh_real_emx": "false",
            "error": "EMX exited 2",
        }
    ]
    exact_evidence = [
        {
            **candidates[2],
            "delegate_result_path": str(emx_failure_evidence),
            "delegate_result_sha256": _sha(emx_failure_evidence),
        },
        {
            **candidates[3],
            "delegate_result_path": str(exact_receipt),
            "delegate_result_sha256": _sha(exact_receipt),
        },
    ]
    exact_pass_path = root / "exact_pass.csv"
    exact_fail_path = root / "exact_fail.csv"
    exact_evidence_path = root / "exact_evidence.csv"
    _write_csv(exact_pass_path, list(exact_pass[0]), exact_pass)
    _write_csv(exact_fail_path, list(exact_fail[0]), exact_fail)
    _write_csv(exact_evidence_path, list(exact_evidence[0]), exact_evidence)

    qa_pass_path = root / "qa_pass.csv"
    qa_fail_path = root / "qa_fail.csv"
    s4p_index_path = root / "s4p_index.csv"
    feature_path = root / "features.csv"
    _write_csv(qa_pass_path, list(exact_pass[0]), exact_pass)
    _write_csv(qa_fail_path, ["candidate_id_sha256", "geometry_sha256", "terminal_stage", "error"], [])
    _write_csv(
        s4p_index_path,
        ["accepted_sequence", "candidate_id_sha256", "geometry_sha256", "s4p_path", "s4p_sha256"],
        [
            {
                "accepted_sequence": 1,
                "candidate_id_sha256": candidates[3]["candidate_id_sha256"],
                "geometry_sha256": candidates[3]["geometry_sha256"],
                "s4p_path": str(s4p),
                "s4p_sha256": _sha(s4p),
            }
        ],
    )
    _write_csv(
        feature_path,
        ["accepted_sequence", "candidate_id_sha256", "geometry_sha256", "frequency_hz"],
        [
            {
                "accepted_sequence": 1,
                "candidate_id_sha256": candidates[3]["candidate_id_sha256"],
                "geometry_sha256": candidates[3]["geometry_sha256"],
                "frequency_hz": frequency,
            }
            for frequency in FREQUENCY_GRID_HZ
        ],
    )

    manifest = root / "backend_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "stage_attempt_product_builder": _record(SCRIPT)
                },
            }
        ),
        encoding="utf-8",
    )
    authorization = root / "authorization.json"
    authorization.write_text(
        json.dumps(
            {
                "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
                "overall_status": "PASS",
                "decision": FULL_CAMPAIGN_PASS_DECISION,
                "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "backend_identity_manifest": _record(manifest),
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "backend_identity_manifest": _record(manifest),
        "full_campaign_authorization_receipt": _record(authorization),
    }

    cadence_receipt = root / "cadence_receipt.json"
    _write_role_receipt(
        cadence_receipt,
        "TERMINAL_PARTITION_CANDIDATE_BOUND_CADENCE_STREAMOUT",
        binding,
        submitted_count=4,
        cadence_pass_count=3,
        cadence_fail_count=1,
        input_candidate_queue=_record(raw_path),
        pass_candidate_queue=_record(cadence_pass_path),
        failure_index=_record(cadence_fail_path),
    )
    gds_receipt = root / "gds_receipt.json"
    _write_role_receipt(
        gds_receipt,
        "TERMINAL_PARTITION_GDS_PHYSICAL_IDENTITY_AUDIT",
        binding,
        submitted_count=3,
        identity_pass_count=3,
        identity_fail_count=0,
        pass_index=_record(gds_pass_path),
        failure_index=_record(gds_fail_path),
    )
    calibre_receipt = root / "calibre_receipt.json"
    _write_role_receipt(
        calibre_receipt,
        "TERMINAL_PARTITION_FOUNDRY_CALIBRE_DRC",
        binding,
        submitted_count=3,
        calibre_pass_count=2,
        calibre_fail_count=1,
        input_role_receipt=_record(gds_receipt),
        pass_index=_record(calibre_pass_path),
        failure_index=_record(calibre_fail_path),
    )
    zero_receipt = root / "zero_receipt.json"
    _write_role_receipt(
        zero_receipt,
        "TERMINAL_PARTITION_ZERO_BLOCKING_CALIBRE_RECEIPTS",
        binding,
        submitted_count=2,
        receipt_pass_count=2,
        receipt_fail_count=0,
        input_role_receipt=_record(calibre_receipt),
        pass_index=_record(zero_pass_path),
        failure_index=_record(zero_fail_path),
    )
    exact_role_receipt = root / "exact_role_receipt.json"
    _write_role_receipt(
        exact_role_receipt,
        "TERMINAL_PARTITION_EXACT_GDS_FRESH_EMX_ATTEMPT",
        binding,
        submitted_count=2,
        emx_pass_count=1,
        emx_fail_count=1,
        input_role_receipt=_record(zero_receipt),
        pass_index=_record(exact_pass_path),
        failure_index=_record(exact_fail_path),
        delegate_evidence_index=_record(exact_evidence_path),
    )
    qa_receipt = root / "qa_receipt.json"
    _write_role_receipt(
        qa_receipt,
        "TERMINAL_PARTITION_EXACT56_S4P_QA",
        binding,
        submitted_count=1,
        qa_pass_count=1,
        qa_fail_count=0,
        input_role_receipt=_record(exact_role_receipt),
        qa_pass_index=_record(qa_pass_path),
        failure_index=_record(qa_fail_path),
        s4p_artifact_index=_record(s4p_index_path),
        long_features=_record(feature_path),
    )
    return {
        "manifest": manifest,
        "authorization": authorization,
        "cadence_receipt": cadence_receipt,
        "gds_receipt": gds_receipt,
        "calibre_receipt": calibre_receipt,
        "zero_receipt": zero_receipt,
        "exact_receipt": exact_role_receipt,
        "qa_receipt": qa_receipt,
        "out_dir": root / "attempt_products",
    }


def _candidate(index: int) -> dict[str, str]:
    values = {
        "primary_outer_width_um": 250.0 + index,
        "primary_outer_height_um": 252.0 + index,
        "secondary_outer_width_um": 240.0 + index,
        "secondary_outer_height_um": 242.0 + index,
        "line_width_um": 6.0,
        "primary_terminal_y_span_um": 95.0 + index,
        "secondary_terminal_y_span_um": 96.0 + index,
        "offset_um": float(index),
        "primary_feed_extension_um": 145.0 + index,
        "secondary_feed_extension_um": 146.0 + index,
    }
    geometry_sha = canonical_geometry_sha256(values)
    return {
        "candidate_id_sha256": geometry_sha,
        "candidate_geometry_identity_sha256": geometry_sha,
        "geometry_id": geometry_sha,
        "geometry_sha256": geometry_sha,
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "campaign_phase": "PHASE_A",
        "acquisition_source": "base_space_filling",
        "analytical_status": "PASS",
        "topology_status": "PASS",
        **{f"geom__{name}": str(values[name]) for name in GEOMETRY_FIELDS},
    }


def _write_role_receipt(
    path: Path,
    decision: str,
    binding: dict,
    **values: object,
) -> None:
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": decision,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "stage": "GOLDEN",
                **binding,
                **values,
            }
        ),
        encoding="utf-8",
    )


def _rebind_downstream(fixture: dict[str, Path]) -> None:
    chain = [
        (fixture["calibre_receipt"], fixture["gds_receipt"]),
        (fixture["zero_receipt"], fixture["calibre_receipt"]),
        (fixture["exact_receipt"], fixture["zero_receipt"]),
        (fixture["qa_receipt"], fixture["exact_receipt"]),
    ]
    for receipt_path, input_path in chain:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload["input_role_receipt"] = _record(input_path)
        receipt_path.write_text(json.dumps(payload), encoding="utf-8")


def _run(fixture: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--stage",
            "GOLDEN",
            "--current-accepted",
            "0",
            "--backend-identity-manifest",
            str(fixture["manifest"]),
            "--full-campaign-receipt",
            str(fixture["authorization"]),
            "--cadence-role-receipt",
            str(fixture["cadence_receipt"]),
            "--gds-identity-role-receipt",
            str(fixture["gds_receipt"]),
            "--calibre-role-receipt",
            str(fixture["calibre_receipt"]),
            "--calibre-zero-role-receipt",
            str(fixture["zero_receipt"]),
            "--exact-gds-emx-role-receipt",
            str(fixture["exact_receipt"]),
            "--exact56-role-receipt",
            str(fixture["qa_receipt"]),
            "--out-dir",
            str(fixture["out_dir"]),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
