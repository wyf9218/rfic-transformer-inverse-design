from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

from rfic_transformer_inverse_design.analysis.extraction import (
    differential_2port_to_4port_s,
)
from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (
    EXACT_GDS_EMX_PASS_DECISION,
    EXACT_GDS_EMX_RECEIPT_SCHEMA,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_exact56_s4p_qa_batch.py"


def test_batch_partitions_valid_and_invalid_exact56_s4p(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "EXACT56_S4P_QA_BATCH_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["qa_pass_count"] == 1
    assert receipt["qa_fail_count"] == 1
    assert receipt["geometry_frequency_rows"] == 56
    assert receipt["failed_candidates_counted_as_accepted"] is False
    assert receipt["proxy_or_historical_labels_used"] is False
    assert receipt["simulator_action_taken"] is False

    passed = _read_csv(out / "EXACT_GDS_EMX_QA_PASS_INDEX.csv")
    failures = _read_csv(out / "EXACT56_S4P_QA_FAILURE_INDEX.csv")
    artifacts = _read_csv(out / "S4P_ARTIFACT_INDEX.csv")
    features = _read_csv(out / "BROADBAND_FEATURES_LONG.csv")
    assert [row["candidate_id_sha256"] for row in passed] == ["a" * 64]
    assert [row["candidate_id_sha256"] for row in failures] == ["b" * 64]
    assert failures[0]["terminal_stage"] == "exact56_s4p_qa"
    assert len(artifacts) == 1
    assert len(features) == 56
    assert [int(row["frequency_hz"]) for row in features] == list(
        FREQUENCY_GRID_HZ
    )


def test_batch_rejects_duplicate_geometry_before_qa(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    rows = _read_csv(fixture["input_index"])
    rows[1]["geometry_sha256"] = rows[0]["geometry_sha256"]
    _write_csv(fixture["input_index"], rows)
    input_receipt = json.loads(
        fixture["input_receipt"].read_text(encoding="utf-8")
    )
    input_receipt["pass_index"] = _record(fixture["input_index"])
    fixture["input_receipt"].write_text(
        json.dumps(input_receipt), encoding="utf-8"
    )

    result = _run(fixture)
    assert result.returncode == 2
    assert "duplicated" in result.stderr
    assert not fixture["out_dir"].exists()


def test_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt_path = fixture["out_dir"] / "EXACT56_S4P_QA_BATCH_ROLE_RECEIPT.json"
    before = receipt_path.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt_path.read_bytes() == before


def test_batch_propagates_an_empty_fresh_emx_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _empty_csv_keep_header(fixture["input_index"])
    input_receipt = json.loads(
        fixture["input_receipt"].read_text(encoding="utf-8")
    )
    input_receipt["pass_index"] = _record(fixture["input_index"])
    fixture["input_receipt"].write_text(
        json.dumps(input_receipt), encoding="utf-8"
    )

    result = _run(fixture)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (fixture["out_dir"] / "EXACT56_S4P_QA_BATCH_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["submitted_count"] == 0
    assert receipt["qa_pass_count"] == 0
    assert receipt["qa_fail_count"] == 0
    assert receipt["geometry_frequency_rows"] == 0
    assert receipt["simulator_action_taken"] is False


def _fixture(root: Path) -> dict:
    rows = []
    for index, candidate_id in enumerate(("a" * 64, "b" * 64), start=1):
        geometry_sha = str(index) * 64
        frequencies = np.asarray(
            FREQUENCY_GRID_HZ if index == 1 else FREQUENCY_GRID_HZ[:-1],
            dtype=float,
        )
        s4p_path = root / f"candidate_{index}.s4p"
        _write_physical_s4p(s4p_path, frequencies)
        emx_receipt = root / f"candidate_{index}_emx_receipt.json"
        _write_emx_receipt(
            emx_receipt,
            s4p_path=s4p_path,
            candidate_sha=candidate_id,
            geometry_sha=geometry_sha,
        )
        rows.append(
            {
                "accepted_sequence": index,
                "geometry_id": f"geometry_{index}",
                "geometry_sha256": geometry_sha,
                "candidate_id_sha256": candidate_id,
                "campaign_phase": "PHASE_A",
                "acquisition_source": "base_space_filling",
                "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "exact_gds_emx_receipt_path": str(emx_receipt),
                "exact_gds_emx_receipt_sha256": _sha(emx_receipt),
            }
        )
    input_index = root / "fresh_emx_pass_index.csv"
    _write_csv(input_index, rows)
    input_receipt = root / "EXACT_GDS_EMX_BATCH_ROLE_RECEIPT.json"
    input_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "stage": "GOLDEN",
                "pass_index": _record(input_index),
            }
        ),
        encoding="utf-8",
    )
    manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "full_band_s4p_qa_builder": _record(SCRIPT),
                    "full_band_s4p_qa_module": _record(
                        Path(broadband56_s4p_qa.__file__).resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
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
                "backend_identity_manifest": _record(manifest),
            }
        ),
        encoding="utf-8",
    )
    return {
        "input_index": input_index,
        "input_receipt": input_receipt,
        "manifest": manifest,
        "authorization": authorization,
        "out_dir": root / "qa_batch",
    }


def _run(fixture: dict) -> subprocess.CompletedProcess[str]:
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
            "--max-concurrency",
            "2",
            "--out-dir",
            str(fixture["out_dir"]),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_physical_s4p(path: Path, frequencies_hz: np.ndarray) -> None:
    omega = 2.0 * np.pi * frequencies_hz
    lp_h = 0.8e-9
    ls_h = 1.0e-9
    q = 12.0
    mutual_h = 0.35 * math.sqrt(lp_h * ls_h)
    z_diff = np.zeros((len(frequencies_hz), 2, 2), dtype=np.complex128)
    z_diff[:, 0, 0] = omega * lp_h / q + 1j * omega * lp_h
    z_diff[:, 1, 1] = omega * ls_h / q + 1j * omega * ls_h
    z_diff[:, 0, 1] = 1j * omega * mutual_h
    z_diff[:, 1, 0] = 1j * omega * mutual_h
    identity = np.eye(2, dtype=np.complex128)
    s_diff = np.empty_like(z_diff)
    for index, matrix in enumerate(z_diff):
        s_diff[index] = (matrix - 100.0 * identity) @ np.linalg.inv(
            matrix + 100.0 * identity
        )
    differential_2port_to_4port_s(frequencies_hz, s_diff).to_touchstone(path)


def _write_emx_receipt(
    path: Path,
    *,
    s4p_path: Path,
    candidate_sha: str,
    geometry_sha: str,
) -> None:
    evidence = lambda token: {
        "path": f"/private/{token}",
        "size_bytes": 1,
        "sha256": token[0] * 64,
    }
    payload = {
        "schema": EXACT_GDS_EMX_RECEIPT_SCHEMA,
        "overall_status": "PASS",
        "decision": EXACT_GDS_EMX_PASS_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate_id_sha256": candidate_sha,
        "geometry_identity_sha256": geometry_sha,
        "full_campaign_authorization_receipt": evidence("1.json"),
        "private_configuration": evidence("2.yaml"),
        "source_calibre_zero_blocking_receipt": evidence("3.json"),
        "source_calibre_report": evidence("4.rep"),
        "source_exact_gds": evidence("5.gds"),
        "source_layout_manifest": evidence("6.json"),
        "manifest_contract": {
            "port_order": ["P001", "P002", "P003", "P004"],
            "signal_labels": ["P001", "P002", "P003", "P004"],
            "cadence_pin_purpose": 51,
        },
        "frequency_contract": {
            "points": 56,
            "exact_hz": list(FREQUENCY_GRID_HZ),
        },
        "emx_output": {
            "touchstone_path": str(s4p_path),
            "touchstone_size_bytes": s4p_path.stat().st_size,
            "touchstone_sha256": _sha(s4p_path),
            "num_ports": 4,
            "num_frequency_points": 56,
            "frequency_start_hz": FREQUENCY_GRID_HZ[0],
            "frequency_stop_hz": FREQUENCY_GRID_HZ[-1],
            "frequency_step_hz": 1_000_000_000,
            "checks": {
                "port_count_exact_four": True,
                "frequency_count_exact_56": True,
                "frequency_vector_exact": True,
                "s_matrix_shape_exact": True,
                "s_matrix_finite": True,
            },
        },
        "source_pins_unchanged_after_emx": True,
        "cadence_executed_by_this_runner": False,
        "calibre_executed_by_this_runner": False,
        "gds_generated_or_copied_by_this_runner": False,
        "fresh_real_emx_executed": True,
        "proxy_or_historical_label_used": False,
        "simulator_action_taken": True,
        "forbidden_output_scan": {
            "gds_files": [],
            "symlinks": [],
            "forbidden_directories": [],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _empty_csv_keep_header(path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        fields = list(csv.DictReader(handle).fieldnames or [])
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writeheader()


def _record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
