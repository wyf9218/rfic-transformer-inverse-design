from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.analysis.extraction import (
    differential_2port_to_4port_s,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    FREQUENCY_GRID_HZ,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (
    EXACT_GDS_EMX_PASS_DECISION,
    EXACT_GDS_EMX_RECEIPT_SCHEMA,
)
from rfic_transformer_inverse_design.campaigns.broadband56_s4p_qa import (
    Broadband56S4pQaError,
    FEATURE_MANIFEST_NAME,
    LONG_FEATURES_NAME,
    QA_FAILURE_NAME,
    QA_INDEX_NAME,
    QA_RECEIPT_NAME,
    audit_exact56_s4p,
    build_exact56_s4p_qa_products,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_raw_finalizer():
    path = ROOT / "scripts" / "finalize_broadband56_balanced200k_raw_products.py"
    spec = importlib.util.spec_from_file_location("broadband56_raw_finalizer_for_qa", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _evidence_record(name: str, token: str) -> dict[str, object]:
    return {"path": f"/private/{name}", "size_bytes": 1, "sha256": token * 64}


def _write_emx_receipt(
    root: Path,
    *,
    s4p_path: Path,
    candidate_sha256: str,
    geometry_sha256: str,
    proxy_used: bool = False,
) -> Path:
    receipt = {
        "schema": EXACT_GDS_EMX_RECEIPT_SCHEMA,
        "overall_status": "PASS",
        "decision": EXACT_GDS_EMX_PASS_DECISION,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate_id_sha256": candidate_sha256,
        "geometry_identity_sha256": geometry_sha256,
        "full_campaign_authorization_receipt": _evidence_record("full.json", "1"),
        "private_configuration": _evidence_record("config.yaml", "2"),
        "source_calibre_zero_blocking_receipt": _evidence_record(
            "calibre_receipt.json", "3"
        ),
        "source_calibre_report": _evidence_record("calibre.report", "4"),
        "source_exact_gds": _evidence_record("design.gds", "5"),
        "source_layout_manifest": _evidence_record("manifest.json", "6"),
        "top_cell": "transformer",
        "manifest_contract": {
            "port_count": 4,
            "port_order": ["P001", "P002", "P003", "P004"],
            "signal_labels": ["P001", "P002", "P003", "P004"],
            "cadence_pin_purpose": 51,
            "top_cell": "transformer",
            "checks": {},
        },
        "frequency_contract": {
            "start_hz": FREQUENCY_GRID_HZ[0],
            "stop_hz": FREQUENCY_GRID_HZ[-1],
            "step_hz": 1_000_000_000,
            "points": 56,
            "exact_hz": list(FREQUENCY_GRID_HZ),
        },
        "emx_output": {
            "touchstone_path": str(s4p_path),
            "touchstone_size_bytes": s4p_path.stat().st_size,
            "touchstone_sha256": _sha256(s4p_path),
            "emx_command_path": str(root / "emx_command.json"),
            "emx_command_size_bytes": 1,
            "emx_command_sha256": "7" * 64,
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
        "proxy_or_historical_label_used": proxy_used,
        "simulator_action_taken": True,
        "forbidden_output_scan": {
            "gds_files": [],
            "symlinks": [],
            "forbidden_directories": [],
        },
    }
    path = root / "EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return path


def _write_input_index(
    root: Path,
    *,
    receipt_path: Path,
    geometry_sha256: str = "a" * 64,
    candidate_sha256: str = "b" * 64,
    receipt_sha256: str | None = None,
) -> Path:
    path = root / "fresh_emx_receipt_index.csv"
    row = {
        "accepted_sequence": 1,
        "geometry_id": "geometry_000001",
        "geometry_sha256": geometry_sha256,
        "candidate_id_sha256": candidate_sha256,
        "campaign_phase": "PHASE_A",
        "acquisition_source": "base_space_filling",
        "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "exact_gds_emx_receipt_path": str(receipt_path),
        "exact_gds_emx_receipt_sha256": receipt_sha256 or _sha256(receipt_path),
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def _fixture(tmp_path: Path, frequencies: np.ndarray | None = None) -> tuple[Path, Path]:
    s4p_path = tmp_path / "fresh.s4p"
    _write_physical_s4p(
        s4p_path,
        np.asarray(
            frequencies if frequencies is not None else FREQUENCY_GRID_HZ,
            dtype=float,
        ),
    )
    receipt_path = _write_emx_receipt(
        tmp_path,
        s4p_path=s4p_path,
        candidate_sha256="b" * 64,
        geometry_sha256="a" * 64,
    )
    return _write_input_index(tmp_path, receipt_path=receipt_path), s4p_path


def test_exact56_qa_builds_hash_bound_long_features(tmp_path: Path) -> None:
    input_index, s4p_path = _fixture(tmp_path)
    out_dir = tmp_path / "qa"

    result = build_exact56_s4p_qa_products(
        input_index_path=input_index,
        out_dir=out_dir,
        expected_geometry_count=1,
    )

    assert result["overall_status"] == "PASS"
    assert result["geometry_count"] == 1
    assert result["geometry_frequency_rows"] == 56
    with (out_dir / LONG_FEATURES_NAME).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 56
    assert [int(row["frequency_hz"]) for row in rows] == list(FREQUENCY_GRID_HZ)
    assert float(rows[10]["lp_nh"]) == pytest.approx(0.8, rel=1e-8)
    assert float(rows[10]["ls_nh"]) == pytest.approx(1.0, rel=1e-8)
    assert float(rows[10]["k_abs"]) == pytest.approx(0.35, rel=1e-8)
    assert rows[0]["exact_gds_emx_receipt_sha256"] == _sha256(
        tmp_path / "EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json"
    )
    receipt = json.loads((out_dir / QA_RECEIPT_NAME).read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["geometry_frequency_rows"] == 56
    assert receipt["frequency_contract"]["resampling_used"] is False
    assert receipt["proxy_or_historical_labels_used"] is False
    manifest = json.loads(
        (out_dir / FEATURE_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert manifest["frequency_vector_hz"] == list(FREQUENCY_GRID_HZ)
    assert manifest["interpolation_or_resampling_used"] is False
    assert (out_dir / QA_INDEX_NAME).is_file()

    finalizer = _load_raw_finalizer()
    accepted = finalizer.AcceptedAttempt(
        row={
            "campaign_phase": "PHASE_A",
            "acquisition_source": "base_space_filling",
        },
        geometry_id="geometry_000001",
        geometry_sha256="a" * 64,
        accepted_sequence=1,
        s4p_path=s4p_path,
        s4p_sha256=_sha256(s4p_path),
    )
    rebound_path = tmp_path / "rebound_features.csv"
    row_count, _ = finalizer._validate_and_write_long_features(
        source_path=out_dir / LONG_FEATURES_NAME,
        destination_path=rebound_path,
        accepted=(accepted,),
        fingerprint=SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    assert row_count == 56
    assert _sha256(rebound_path) == _sha256(out_dir / LONG_FEATURES_NAME)


def test_exact56_qa_rejects_old_111_point_grid(tmp_path: Path) -> None:
    frequencies = np.arange(5.0e9, 60.0e9 + 0.5e9, 0.5e9)
    input_index, _ = _fixture(tmp_path, frequencies)
    out_dir = tmp_path / "qa"
    with pytest.raises(Broadband56S4pQaError, match="exact contract failed"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=out_dir,
        )
    assert (out_dir / QA_FAILURE_NAME).is_file()
    assert not (out_dir / QA_RECEIPT_NAME).exists()


def test_exact56_qa_rejects_missing_frequency(tmp_path: Path) -> None:
    frequencies = np.asarray(FREQUENCY_GRID_HZ[:-1], dtype=float)
    input_index, _ = _fixture(tmp_path, frequencies)
    with pytest.raises(Broadband56S4pQaError, match="exact contract failed"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=tmp_path / "qa",
        )


def test_exact56_qa_rejects_duplicate_frequency(tmp_path: Path) -> None:
    frequencies = np.asarray(FREQUENCY_GRID_HZ, dtype=float)
    frequencies[20] = frequencies[19]
    input_index, _ = _fixture(tmp_path, frequencies)
    with pytest.raises(Broadband56S4pQaError, match="exact contract failed"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=tmp_path / "qa",
        )


def test_exact56_qa_rejects_nonfinite_s_matrix(tmp_path: Path) -> None:
    input_index, s4p_path = _fixture(tmp_path)
    lines = s4p_path.read_text(encoding="utf-8").splitlines()
    data_index = next(
        index for index, line in enumerate(lines) if not line.startswith(("!", "#"))
    )
    tokens = lines[data_index].split()
    tokens[1] = "nan"
    lines[data_index] = " ".join(tokens)
    s4p_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    receipt_path = tmp_path / "EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["emx_output"]["touchstone_size_bytes"] = s4p_path.stat().st_size
    receipt["emx_output"]["touchstone_sha256"] = _sha256(s4p_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    input_index = _write_input_index(tmp_path, receipt_path=receipt_path)
    with pytest.raises(Broadband56S4pQaError, match="s_matrix_finite"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=tmp_path / "qa",
        )


def test_exact56_qa_rejects_s4p_hash_drift(tmp_path: Path) -> None:
    input_index, s4p_path = _fixture(tmp_path)
    s4p_path.write_text(s4p_path.read_text(encoding="utf-8") + "! mutation\n", encoding="utf-8")
    with pytest.raises(Broadband56S4pQaError, match="SHA-256 mismatch"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=tmp_path / "qa",
        )


def test_exact56_qa_rejects_proxy_label_receipt(tmp_path: Path) -> None:
    s4p_path = tmp_path / "fresh.s4p"
    _write_physical_s4p(s4p_path, np.asarray(FREQUENCY_GRID_HZ, dtype=float))
    receipt_path = _write_emx_receipt(
        tmp_path,
        s4p_path=s4p_path,
        candidate_sha256="b" * 64,
        geometry_sha256="a" * 64,
        proxy_used=True,
    )
    input_index = _write_input_index(tmp_path, receipt_path=receipt_path)
    with pytest.raises(Broadband56S4pQaError, match="proxy_or_historical_label_excluded"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=tmp_path / "qa",
        )


def test_exact56_qa_rejects_receipt_hash_drift(tmp_path: Path) -> None:
    input_index, _ = _fixture(tmp_path)
    receipt_path = tmp_path / "EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json"
    receipt_path.write_text(receipt_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Broadband56S4pQaError, match="SHA-256 mismatch"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=tmp_path / "qa",
        )


def test_exact56_qa_is_no_clobber(tmp_path: Path) -> None:
    input_index, _ = _fixture(tmp_path)
    out_dir = tmp_path / "qa"
    out_dir.mkdir()
    with pytest.raises(Broadband56S4pQaError, match="refusing existing"):
        build_exact56_s4p_qa_products(
            input_index_path=input_index,
            out_dir=out_dir,
        )


def test_exact56_qa_cli_builds_products(tmp_path: Path) -> None:
    input_index, _ = _fixture(tmp_path)
    out_dir = tmp_path / "qa"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_broadband56_exact56_s4p_qa.py"),
            "--input-index",
            str(input_index),
            "--out-dir",
            str(out_dir),
            "--expected-geometry-count",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "geometry_frequency_rows=56" in completed.stdout
    assert (out_dir / QA_RECEIPT_NAME).is_file()


def test_audit_exact56_rejects_singular_s_to_z(tmp_path: Path) -> None:
    path = tmp_path / "singular.s4p"
    lines = ["# GHz S RI R 50"]
    for frequency_hz in FREQUENCY_GRID_HZ:
        values = [f"{frequency_hz / 1e9:g}"]
        for row in range(4):
            for column in range(4):
                value = 1.0 if row == column else 0.0
                values.extend([f"{value:g}", "0"])
        lines.append(" ".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(Broadband56S4pQaError, match="S-to-Z conversion failed"):
        audit_exact56_s4p(path)
