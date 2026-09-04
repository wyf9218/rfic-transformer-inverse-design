from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.analysis.extraction import (
    differential_2port_to_4port_s,
    single_ended_to_differential_z,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
    contract_fingerprint,
    matrix_columns,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _load_module():
    path = ROOT / "scripts" / "finalize_broadband56_balanced200k_raw_products.py"
    spec = importlib.util.spec_from_file_location(
        "finalize_broadband56_balanced200k_raw_products", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_contract(tmp_path: Path) -> tuple[Path, Path, str]:
    production_config = tmp_path / "production.yaml"
    production_config.write_text("private_contract_fixture: true\n", encoding="utf-8")
    contract = json.loads(PUBLIC_CONTRACT.read_text(encoding="utf-8"))
    contract["inherited_contract_evidence"] = {
        "previous_campaign_id": "synthetic_v1",
        "previous_contract_sha256": "b" * 64,
        "previous_config_sha256": "c" * 64,
        "production_config_sha256": _sha256(production_config),
        "private_runtime_paths_not_for_publication": True,
    }
    contract["preparation_status"] = "PASS"
    contract["contract_fingerprint_sha256"] = contract_fingerprint(contract)
    path = tmp_path / "campaign_contract_frozen.json"
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return path, production_config, str(contract["contract_fingerprint_sha256"])


def _geometry(index: int) -> dict[str, float]:
    values = {
        "primary_outer_width_um": 220.0 + index,
        "primary_outer_height_um": 221.0 + index,
        "secondary_outer_width_um": 210.0 + index,
        "secondary_outer_height_um": 211.0 + index,
        "line_width_um": 8.0,
        "primary_terminal_y_span_um": 50.0,
        "secondary_terminal_y_span_um": 51.0,
        "offset_um": float(index),
        "primary_feed_extension_um": 150.0,
        "secondary_feed_extension_um": 151.0,
    }
    assert tuple(values) == GEOMETRY_FIELDS
    return values


def _write_s4p(path: Path, *, lp_nh: float, ls_nh: float, q: float, k: float) -> None:
    frequencies = np.asarray(FREQUENCY_GRID_HZ, dtype=float)
    omega = 2.0 * np.pi * frequencies
    lp_h = lp_nh * 1.0e-9
    ls_h = ls_nh * 1.0e-9
    mutual_h = k * math.sqrt(lp_h * ls_h)
    z_diff = np.zeros((len(frequencies), 2, 2), dtype=np.complex128)
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
    differential_2port_to_4port_s(frequencies, s_diff).to_touchstone(path)


def _evidence_file(path: Path, content: str) -> tuple[str, str]:
    path.write_text(content, encoding="utf-8")
    return str(path), _sha256(path)


def _base_attempt(
    tmp_path: Path,
    *,
    fingerprint: str,
    index: int,
    terminal: str,
    statuses: tuple[str, ...],
) -> dict[str, object]:
    geometry = _geometry(index)
    candidate_path, candidate_sha = _evidence_file(
        tmp_path / f"candidate_{index}.csv", f"candidate,{index}\n"
    )
    row: dict[str, object] = {
        "attempt_id": f"attempt_{index:04d}",
        "retry_of_attempt_id": "",
        "geometry_id": f"geometry_{index:04d}",
        "geometry_sha256": canonical_geometry_sha256(geometry),
        "campaign_contract_fingerprint": fingerprint,
        "accepted_sequence": "",
        "campaign_phase": "PHASE_A",
        "acquisition_source": "base_space_filling",
        "terminal_stage": terminal,
        "calibre_blocking_violations": 0,
        "frequency_points": "",
        "fresh_real_emx": "false",
        "candidate_source_path": candidate_path,
        "candidate_source_sha256": candidate_sha,
        "gds_path": "",
        "gds_sha256": "",
        "calibre_report_path": "",
        "calibre_report_sha256": "",
        "emx_log_path": "",
        "emx_log_sha256": "",
        "s4p_path": "",
        "s4p_sha256": "",
        **{f"geom__{name}": value for name, value in geometry.items()},
    }
    for name, status in zip(
        (
            "duplicate_status",
            "geometry_bounds_status",
            "analytical_status",
            "topology_status",
            "cadence_gds_status",
            "calibre_status",
            "emx_status",
            "s4p_status",
            "s_to_z_status",
            "feature_extraction_status",
        ),
        statuses,
    ):
        row[name] = status
    return row


def _accepted_attempt(
    tmp_path: Path,
    *,
    fingerprint: str,
    index: int,
    sequence: int,
) -> dict[str, object]:
    row = _base_attempt(
        tmp_path,
        fingerprint=fingerprint,
        index=index,
        terminal="ACCEPTED",
        statuses=("PASS",) * 10,
    )
    gds_path = tmp_path / f"design_{index}.gds"
    gds_path.write_bytes(f"gds-{index}".encode())
    calibre_path, calibre_sha = _evidence_file(
        tmp_path / f"calibre_{index}.txt", "TOTAL BLOCKING VIOLATIONS: 0\n"
    )
    emx_path, emx_sha = _evidence_file(
        tmp_path / f"emx_{index}.log", "fresh EMX complete\n"
    )
    s4p_path = tmp_path / f"design_{index}.s4p"
    _write_s4p(s4p_path, lp_nh=0.5 + 0.01 * index, ls_nh=0.6 + 0.01 * index, q=10.0, k=0.2)
    row.update(
        {
            "accepted_sequence": sequence,
            "campaign_phase": "PHASE_A",
            "acquisition_source": "base_space_filling",
            "frequency_points": 56,
            "fresh_real_emx": "true",
            "gds_path": str(gds_path),
            "gds_sha256": _sha256(gds_path),
            "calibre_report_path": calibre_path,
            "calibre_report_sha256": calibre_sha,
            "emx_log_path": emx_path,
            "emx_log_sha256": emx_sha,
            "s4p_path": str(s4p_path),
            "s4p_sha256": _sha256(s4p_path),
        }
    )
    return row


def _feature_rows(attempts: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    accepted = sorted(
        (row for row in attempts if row["terminal_stage"] == "ACCEPTED"),
        key=lambda row: int(row["accepted_sequence"]),
    )
    for attempt in accepted:
        touchstone = load_touchstone(Path(str(attempt["s4p_path"])))
        z_single = touchstone.to_z_parameters()
        z_diff = single_ended_to_differential_z(z_single)
        for frequency_index, frequency_hz in enumerate(FREQUENCY_GRID_HZ):
            omega = 2.0 * math.pi * frequency_hz
            z11 = complex(z_diff[frequency_index, 0, 0])
            z22 = complex(z_diff[frequency_index, 1, 1])
            z21 = complex(z_diff[frequency_index, 1, 0])
            lp_h = z11.imag / omega
            ls_h = z22.imag / omega
            mutual_h = z21.imag / omega
            signed_k = mutual_h / math.sqrt(max(abs(lp_h * ls_h), 1.0e-30))
            qp = z11.imag / z11.real
            qs = z22.imag / z22.real
            row: dict[str, object] = {
                "accepted_sequence": attempt["accepted_sequence"],
                "geometry_id": attempt["geometry_id"],
                "geometry_sha256": attempt["geometry_sha256"],
                "campaign_phase": attempt["campaign_phase"],
                "acquisition_source": attempt["acquisition_source"],
                "campaign_contract_fingerprint": attempt["campaign_contract_fingerprint"],
                "s4p_sha256": attempt["s4p_sha256"],
                "frequency_hz": frequency_hz,
                "lp_h": lp_h,
                "ls_h": ls_h,
                "lp_nh": lp_h * 1.0e9,
                "ls_nh": ls_h * 1.0e9,
                "qp": qp,
                "qs": qs,
                "qmin": min(qp, qs),
                "mutual_inductance_h": mutual_h,
                "signed_k": signed_k,
                "k_abs": abs(signed_k),
                "ls_over_lp": ls_h / lp_h,
                "xp_ohm": omega * lp_h,
                "xs_ohm": omega * ls_h,
                "finite_values": "true",
                "positive_primary_resistance": "true",
                "positive_secondary_resistance": "true",
                "positive_primary_inductive_reactance": "true",
                "positive_secondary_inductive_reactance": "true",
                "extraction_continuity_status": "PASS",
                "below_half_srf": "true",
                "broadband_descriptor_valid": "true",
                "strict_lumped_valid": "true",
                "srf_status": "CENSORED_ABOVE_60_GHZ",
                "passivity_status": "PASS",
                "reciprocity_status": "PASS",
                "inside_broad_response_envelope": "true",
                "inside_literature_practical_panel": "true",
                "outside_envelope_reason": "",
            }
            for matrix_name, matrix in (
                ("s", touchstone.s_matrix[frequency_index]),
                ("z", z_single[frequency_index]),
            ):
                for matrix_row in range(4):
                    for matrix_col in range(4):
                        value = complex(matrix[matrix_row, matrix_col])
                        row[f"{matrix_name}{matrix_row + 1}{matrix_col + 1}_re"] = value.real
                        row[f"{matrix_name}{matrix_row + 1}{matrix_col + 1}_im"] = value.imag
            assert set(matrix_columns()).issubset(row)
            rows.append(row)
    return rows


def _fixture(tmp_path: Path) -> dict[str, object]:
    contract, production_config, fingerprint = _write_contract(tmp_path)
    attempts = [
        _accepted_attempt(tmp_path, fingerprint=fingerprint, index=0, sequence=1),
        _accepted_attempt(tmp_path, fingerprint=fingerprint, index=1, sequence=2),
        _base_attempt(
            tmp_path,
            fingerprint=fingerprint,
            index=2,
            terminal="ANALYTICAL_FAILURE",
            statuses=("PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
        ),
    ]
    ledger = tmp_path / "attempt_ledger.csv"
    features = tmp_path / "features.csv"
    _write_csv(ledger, attempts)
    _write_csv(features, _feature_rows(attempts))
    return {
        "contract": contract,
        "production_config": production_config,
        "fingerprint": fingerprint,
        "attempts": attempts,
        "ledger": ledger,
        "features": features,
    }


def _run(module, fixture: dict[str, object], out_dir: Path) -> dict[str, int]:
    return module.finalize_raw_products(
        contract_path=Path(fixture["contract"]),
        production_config_path=Path(fixture["production_config"]),
        full_campaign_receipt_path=(
            Path(fixture["full_campaign_receipt"])
            if fixture.get("full_campaign_receipt")
            else None
        ),
        attempt_ledger_path=Path(fixture["ledger"]),
        long_features_path=Path(fixture["features"]),
        out_dir=out_dir,
        expected_accepted=2,
    )


def test_materializes_complete_fresh_emx_products_and_receipt(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    out_dir = tmp_path / "raw_products"

    result = _run(module, fixture, out_dir)

    assert result == {"accepted_geometries": 2, "geometry_frequency_rows": 112}
    assert {path.name for path in out_dir.iterdir()} == {
        "accepted_geometry_200k.csv",
        "rejected_geometry_index.csv",
        "broadband_features_11p2m_long.csv",
        "broadband_features_manifest.json",
        "sparameter_artifact_index_200k.csv",
        "geometry_provenance_200k.csv",
        "failure_funnel.csv",
        "RAW_PRODUCTS_RECEIPT.json",
        "SHA256SUMS.txt",
    }
    receipt = json.loads((out_dir / "RAW_PRODUCTS_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["counts"]["accepted_geometries"] == 2
    assert receipt["counts"]["accepted_s4p_geometries"] == 2
    assert receipt["counts"]["accepted_feature_complete_geometries"] == 2
    assert receipt["counts"]["geometry_frequency_rows"] == 112
    assert receipt["checks"]["proxy_values_excluded_from_labels"] is True
    with (out_dir / "failure_funnel.csv").open(newline="", encoding="utf-8") as handle:
        funnel = {row["stage"]: int(row["count"]) for row in csv.DictReader(handle)}
    assert funnel["raw_geometry_candidates"] == 3
    assert funnel["analytical_failures"] == 1
    assert funnel["accepted_geometries"] == 2
    assert set(funnel) == set(module.FAILURE_FUNNEL_ORDER)
    manifest = json.loads(
        (out_dir / "broadband_features_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["total_row_count"] == 112
    assert manifest["geometry_count"] == 2
    assert manifest["partitions"][0]["sha256"] == _sha256(
        out_dir / "broadband_features_11p2m_long.csv"
    )
    column_types = {row["name"]: row["logical_type"] for row in manifest["columns"]}
    assert column_types["lp_h"] == "float64"
    assert column_types["below_half_srf"] == "boolean"
    with (out_dir / "sparameter_artifact_index_200k.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        artifact = next(csv.DictReader(handle))
    assert int(artifact["s4p_size_bytes"]) > 0
    assert int(artifact["port_count"]) == 4
    assert int(artifact["first_frequency_hz"]) == FREQUENCY_GRID_HZ[0]
    assert int(artifact["last_frequency_hz"]) == FREQUENCY_GRID_HZ[-1]


@pytest.mark.parametrize("column", ["s11_re", "z43_im", "lp_nh", "signed_k", "qmin"])
def test_tampered_s4p_bound_value_fails_without_official_output(
    tmp_path: Path, column: str
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    with Path(fixture["features"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0][column] = str(float(rows[0][column]) + 0.25)
    _write_csv(Path(fixture["features"]), rows)
    out_dir = tmp_path / "raw_products"

    with pytest.raises(module.RawProductFinalizationError, match="S4P-bound value mismatch"):
        _run(module, fixture, out_dir)

    assert not out_dir.exists()
    assert not list(tmp_path.glob(".raw_products.staging.*"))


def test_accepted_proxy_label_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    attempts = list(fixture["attempts"])
    attempts[0]["fresh_real_emx"] = "false"
    _write_csv(Path(fixture["ledger"]), attempts)

    with pytest.raises(module.RawProductFinalizationError, match="fresh real EMX"):
        _run(module, fixture, tmp_path / "raw_products")


def test_production_config_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    Path(fixture["production_config"]).write_text("changed: true\n", encoding="utf-8")

    with pytest.raises(module.RawProductFinalizationError, match="production config SHA-256"):
        _run(module, fixture, tmp_path / "raw_products")


def test_approved_corrected_foundry_layout_config_is_bound_to_receipt_chain(
    tmp_path: Path,
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    previous_config = Path(fixture["production_config"])
    corrected_config = tmp_path / "production_corrected.yaml"
    corrected_config.write_text(
        previous_config.read_text(encoding="utf-8")
        + "foundry_layout:\n  enabled: true\n  manufacturing_grid_um: 0.005\n",
        encoding="utf-8",
    )
    contract_path = Path(fixture["contract"])
    corrected_receipt = tmp_path / "CORRECTED_FOUNDRY_LAYOUT_AUTHORIZATION_RECEIPT.json"
    corrected_payload = {
        "schema": "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1",
        "overall_status": "PASS",
        "authorization_scope": (
            "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
        ),
        "restore_corrected_foundry_layout_contract_authorized": True,
        "verified_bound_files": {
            "previous_private_configuration": _file_record(previous_config),
            "corrected_private_configuration": _file_record(corrected_config),
            "private_evidence.campaign_contract_frozen": _file_record(contract_path),
        },
    }
    corrected_receipt.write_text(
        json.dumps(corrected_payload, indent=2) + "\n", encoding="utf-8"
    )
    full_receipt = tmp_path / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    full_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": module.CAMPAIGN_ID,
                "contract_fingerprint_sha256": fixture["fingerprint"],
                "campaign_200k_authorized": True,
                "authorization_composition": {
                    "corrected_foundry_layout_approval_receipt": _file_record(
                        corrected_receipt
                    )
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    fixture["production_config"] = corrected_config
    fixture["full_campaign_receipt"] = full_receipt

    out_dir = tmp_path / "raw_products_corrected"
    result = _run(module, fixture, out_dir)

    assert result == {"accepted_geometries": 2, "geometry_frequency_rows": 112}
    receipt = json.loads(
        (out_dir / "RAW_PRODUCTS_RECEIPT.json").read_text(encoding="utf-8")
    )
    authorization = receipt["inputs"]["production_config_authorization"]
    assert authorization["mode"] == "APPROVED_CORRECTED_FOUNDRY_LAYOUT_REPLACEMENT"
    assert authorization["frozen_config_sha256"] == _sha256(previous_config)
    assert authorization["effective_config_sha256"] == _sha256(corrected_config)
    assert receipt["checks"]["production_config_authorization_chain_verified"] is True


def test_corrected_config_without_approval_chain_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    Path(fixture["production_config"]).write_text("changed: true\n", encoding="utf-8")

    with pytest.raises(module.RawProductFinalizationError, match="FULL_CAMPAIGN receipt"):
        _run(module, fixture, tmp_path / "raw_products")


def test_duplicate_accepted_geometry_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    attempts = list(fixture["attempts"])
    for field in ("geometry_id", "geometry_sha256", *(f"geom__{name}" for name in GEOMETRY_FIELDS)):
        attempts[1][field] = attempts[0][field]
    attempts[1]["retry_of_attempt_id"] = attempts[0]["attempt_id"]
    _write_csv(Path(fixture["ledger"]), attempts)

    with pytest.raises(module.RawProductFinalizationError, match="duplicate accepted canonical geometry"):
        _run(module, fixture, tmp_path / "raw_products")


def test_duplicate_candidate_is_accounted_separately_from_retry(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    attempts = list(fixture["attempts"])
    duplicate = _base_attempt(
        tmp_path,
        fingerprint=str(fixture["fingerprint"]),
        index=3,
        terminal="DUPLICATE_CANDIDATE",
        statuses=("FAIL",) + ("NOT_RUN",) * 9,
    )
    for field in (
        "geometry_id",
        "geometry_sha256",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    ):
        duplicate[field] = attempts[0][field]
    attempts.append(duplicate)
    _write_csv(Path(fixture["ledger"]), attempts)

    out_dir = tmp_path / "raw_products"
    _run(module, fixture, out_dir)

    receipt = json.loads((out_dir / "RAW_PRODUCTS_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["failure_funnel"]["duplicate_candidates"] == 1
    assert receipt["counts"]["retry_attempts"] == 0
    with (out_dir / "rejected_geometry_index.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rejected = list(csv.DictReader(handle))
    assert {row["terminal_stage"] for row in rejected} == {
        "ANALYTICAL_FAILURE",
        "DUPLICATE_CANDIDATE",
    }


def test_invalid_lumped_descriptor_retains_finite_s_and_z_row() -> None:
    module = _load_module()
    frequency_hz = FREQUENCY_GRID_HZ[0]
    omega = 2.0 * math.pi * frequency_hz
    s_matrix = np.zeros((4, 4), dtype=np.complex128)
    z_matrix = np.eye(4, dtype=np.complex128)
    z_diff = np.asarray(
        [[1.0j, 0.2j], [0.2j, 1.0 + 2.0j]], dtype=np.complex128
    )
    lp_h = 1.0 / omega
    ls_h = 2.0 / omega
    signed_k = 0.2 / math.sqrt(2.0)
    row: dict[str, object] = {
        "lp_h": lp_h,
        "ls_h": ls_h,
        "lp_nh": lp_h * 1.0e9,
        "ls_nh": ls_h * 1.0e9,
        "qp": "nan",
        "qs": 2.0,
        "qmin": "nan",
        "mutual_inductance_h": 0.2 / omega,
        "signed_k": signed_k,
        "k_abs": abs(signed_k),
        "ls_over_lp": 2.0,
        "xp_ohm": 1.0,
        "xs_ohm": 2.0,
        "finite_values": "false",
        "positive_primary_resistance": "false",
        "positive_secondary_resistance": "true",
        "positive_primary_inductive_reactance": "true",
        "positive_secondary_inductive_reactance": "true",
        "extraction_continuity_status": "PASS",
        "below_half_srf": "true",
        "broadband_descriptor_valid": "false",
        "strict_lumped_valid": "false",
        "srf_status": "CENSORED_ABOVE_60_GHZ",
        "passivity_status": "PASS",
        "reciprocity_status": "PASS",
        "inside_broad_response_envelope": "false",
        "inside_literature_practical_panel": "true",
        "outside_envelope_reason": "invalid_qp_and_reactance_below_envelope",
    }
    for matrix_name, matrix in (("s", s_matrix), ("z", z_matrix)):
        for matrix_row in range(4):
            for matrix_col in range(4):
                value = complex(matrix[matrix_row, matrix_col])
                row[f"{matrix_name}{matrix_row + 1}{matrix_col + 1}_re"] = value.real
                row[f"{matrix_name}{matrix_row + 1}{matrix_col + 1}_im"] = value.imag

    module._audit_bound_feature_row(
        row,
        s_matrix=s_matrix,
        z_matrix=z_matrix,
        z_diff=z_diff,
        frequency_hz=frequency_hz,
        line=2,
    )

    row["finite_values"] = "true"
    with pytest.raises(module.RawProductFinalizationError, match="finite_values contradicts"):
        module._audit_bound_feature_row(
            row,
            s_matrix=s_matrix,
            z_matrix=z_matrix,
            z_diff=z_diff,
            frequency_hz=frequency_hz,
            line=2,
        )


def test_invalid_status_chain_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    attempts = list(fixture["attempts"])
    attempts[2]["topology_status"] = "PASS"
    _write_csv(Path(fixture["ledger"]), attempts)

    with pytest.raises(module.RawProductFinalizationError, match="status-chain/terminal-stage"):
        _run(module, fixture, tmp_path / "raw_products")


def test_nonterminal_feature_audit_status_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    with Path(fixture["features"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["passivity_status"] = "PENDING"
    _write_csv(Path(fixture["features"]), rows)

    with pytest.raises(module.RawProductFinalizationError, match="passivity audit status"):
        _run(module, fixture, tmp_path / "raw_products")


def test_physical_evidence_byte_tamper_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    accepted = next(
        row for row in fixture["attempts"] if row["terminal_stage"] == "ACCEPTED"
    )
    Path(str(accepted["gds_path"])).write_bytes(b"tampered-gds")

    with pytest.raises(module.RawProductFinalizationError, match="gds_path evidence"):
        _run(module, fixture, tmp_path / "raw_products")


def test_no_clobber_rejects_existing_output(tmp_path: Path) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    out_dir = tmp_path / "raw_products"
    out_dir.mkdir()

    with pytest.raises(module.RawProductFinalizationError, match="output path already exists"):
        _run(module, fixture, out_dir)
