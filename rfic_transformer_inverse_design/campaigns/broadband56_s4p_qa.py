"""Build hash-bound exact-grid broadband56 S4P QA products.

The builder is intentionally post-simulation.  It accepts only fresh-EMX
receipts produced by :mod:`broadband56_exact_gds_emx`, revalidates their exact
four-port 56-point Touchstone artifacts, converts S to Z without interpolation,
and writes the complete per-frequency matrix and physical-feature table.  It
never invokes Cadence, Calibre, EMX, or a surrogate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..analysis.extraction import single_ended_to_differential_z
from ..network_analysis import z_to_s
from ..sim.touchstone import load_touchstone
from .broadband56_balanced200k import (
    ACQUISITION_SOURCES_BY_PHASE,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    matrix_columns,
)
from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT
from .broadband56_exact_gds_emx import (
    EXACT_GDS_EMX_PASS_DECISION,
    EXACT_GDS_EMX_RECEIPT_SCHEMA,
)
from .broadband56_full_campaign_authorization import PORT_AND_GROUNDING_CONTRACT


QA_RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_exact56_s4p_qa_receipt.v1"
QA_PASS_DECISION = "ACCEPT_EXACT56_FRESH_EMX_S4P_FEATURE_PRODUCTS"
QA_FAILURE_NAME = "S4P_QA_FAILURE.json"
QA_RECEIPT_NAME = "S4P_QA_RECEIPT.json"
QA_INDEX_NAME = "S4P_QA_INDEX.csv"
LONG_FEATURES_NAME = "broadband_features_long.csv"
FEATURE_MANIFEST_NAME = "broadband_features_manifest.json"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

PASSIVITY_SINGULAR_VALUE_TOLERANCE = 1.0e-6
RECIPROCITY_ABSOLUTE_TOLERANCE = 1.0e-6
S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE = 1.0e-8

INPUT_FIELDS = (
    "accepted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "campaign_phase",
    "acquisition_source",
    "campaign_contract_fingerprint",
    "exact_gds_emx_receipt_path",
    "exact_gds_emx_receipt_sha256",
)

IDENTITY_FIELDS = (
    "accepted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "campaign_phase",
    "acquisition_source",
    "campaign_contract_fingerprint",
    "exact_gds_emx_receipt_sha256",
    "s4p_sha256",
    "frequency_hz",
)

FEATURE_COLUMNS = (
    "lp_h",
    "ls_h",
    "lp_nh",
    "ls_nh",
    "qp",
    "qs",
    "qmin",
    "mutual_inductance_h",
    "signed_k",
    "k_abs",
    "ls_over_lp",
    "xp_ohm",
    "xs_ohm",
)

VALIDITY_COLUMNS = (
    "finite_values",
    "positive_primary_resistance",
    "positive_secondary_resistance",
    "positive_primary_inductive_reactance",
    "positive_secondary_inductive_reactance",
    "extraction_continuity_status",
    "below_half_srf",
    "broadband_descriptor_valid",
    "strict_lumped_valid",
    "srf_status",
    "passivity_status",
    "reciprocity_status",
    "inside_broad_response_envelope",
    "inside_literature_practical_panel",
    "outside_envelope_reason",
)

LONG_FEATURE_FIELDS = IDENTITY_FIELDS + FEATURE_COLUMNS + VALIDITY_COLUMNS + matrix_columns()

QA_INDEX_FIELDS = (
    "accepted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "campaign_phase",
    "acquisition_source",
    "campaign_contract_fingerprint",
    "exact_gds_emx_receipt_path",
    "exact_gds_emx_receipt_size_bytes",
    "exact_gds_emx_receipt_sha256",
    "source_exact_gds_sha256",
    "source_calibre_receipt_sha256",
    "s4p_path",
    "s4p_size_bytes",
    "s4p_sha256",
    "port_count",
    "frequency_points",
    "frequency_start_hz",
    "frequency_stop_hz",
    "frequency_step_hz",
    "s_to_z_roundtrip_max_abs_error",
    "passivity_fail_frequency_count",
    "reciprocity_fail_frequency_count",
    "broadband_descriptor_valid_rows",
    "strict_lumped_valid_rows",
    "primary_srf_status",
    "secondary_srf_status",
    "qa_status",
)


class Broadband56S4pQaError(RuntimeError):
    """An exact-grid fresh-EMX S4P failed the production QA contract."""


@dataclass(frozen=True)
class FilePin:
    path: str
    size_bytes: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int

    def public_record(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SrfEstimate:
    status: str
    estimate_hz: float | None
    lower_bound_hz: float | None


@dataclass(frozen=True)
class ArtifactQaResult:
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def build_exact56_s4p_qa_products(
    *,
    input_index_path: Path,
    out_dir: Path,
    expected_geometry_count: int | None = None,
) -> dict[str, Any]:
    """Build no-clobber exact56 QA products from a fresh-EMX receipt index."""

    source_pin, _ = _pin_regular_file(
        input_index_path,
        expected_sha256=None,
        expected_size=None,
        label="fresh-EMX receipt index",
        capture_bytes=False,
    )
    output = _absolute_lexical(out_dir)
    if output.exists():
        raise Broadband56S4pQaError(f"refusing existing output directory: {output}")
    if _path_has_symlink_component(output.parent):
        raise Broadband56S4pQaError(
            f"output parent contains a symlink component: {output.parent}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700, exist_ok=False)

    feature_path = output / LONG_FEATURES_NAME
    qa_index_path = output / QA_INDEX_NAME
    manifest_path = output / FEATURE_MANIFEST_NAME
    receipt_path = output / QA_RECEIPT_NAME
    sums_path = output / SHA256SUMS_NAME
    geometry_count = 0
    feature_row_count = 0
    descriptor_valid_rows = 0
    strict_valid_rows = 0
    seen_geometry_sha256: set[str] = set()
    seen_candidate_sha256: set[str] = set()

    try:
        with Path(source_pin.path).open(newline="", encoding="utf-8-sig") as source, feature_path.open(
            "x", newline="", encoding="utf-8"
        ) as feature_handle, qa_index_path.open("x", newline="", encoding="utf-8") as index_handle:
            reader = csv.DictReader(source)
            source_fields = tuple(reader.fieldnames or ())
            missing = sorted(set(INPUT_FIELDS) - set(source_fields))
            if missing:
                raise Broadband56S4pQaError(
                    f"fresh-EMX receipt index lacks columns: {missing}"
                )
            feature_writer = csv.DictWriter(feature_handle, fieldnames=list(LONG_FEATURE_FIELDS))
            index_writer = csv.DictWriter(index_handle, fieldnames=list(QA_INDEX_FIELDS))
            feature_writer.writeheader()
            index_writer.writeheader()

            for line_number, raw_row in enumerate(reader, start=2):
                row = {key: str(value or "").strip() for key, value in raw_row.items()}
                sequence = _canonical_positive_int(
                    row["accepted_sequence"], f"line {line_number} accepted_sequence"
                )
                expected_sequence = geometry_count + 1
                if sequence != expected_sequence:
                    raise Broadband56S4pQaError(
                        "accepted_sequence must be contiguous from one: "
                        f"line={line_number}, actual={sequence}, expected={expected_sequence}"
                    )
                geometry_id = row["geometry_id"]
                if not geometry_id:
                    raise Broadband56S4pQaError(
                        f"line {line_number} geometry_id is empty"
                    )
                geometry_sha256 = _require_sha256(
                    row["geometry_sha256"], f"line {line_number} geometry_sha256"
                )
                candidate_sha256 = _require_sha256(
                    row["candidate_id_sha256"],
                    f"line {line_number} candidate_id_sha256",
                )
                if geometry_sha256 in seen_geometry_sha256:
                    raise Broadband56S4pQaError(
                        f"duplicate geometry_sha256 at line {line_number}"
                    )
                if candidate_sha256 in seen_candidate_sha256:
                    raise Broadband56S4pQaError(
                        f"duplicate candidate_id_sha256 at line {line_number}"
                    )
                phase = row["campaign_phase"]
                source_name = row["acquisition_source"]
                if phase not in ACQUISITION_SOURCES_BY_PHASE:
                    raise Broadband56S4pQaError(
                        f"line {line_number} has unsupported campaign_phase {phase!r}"
                    )
                if source_name not in ACQUISITION_SOURCES_BY_PHASE[phase]:
                    raise Broadband56S4pQaError(
                        f"line {line_number} acquisition_source is invalid for {phase}"
                    )
                if (
                    row["campaign_contract_fingerprint"]
                    != SCIENTIFIC_CONTRACT_FINGERPRINT
                ):
                    raise Broadband56S4pQaError(
                        f"line {line_number} campaign fingerprint mismatch"
                    )

                receipt_path_input = _resolve_artifact_path(
                    Path(source_pin.path),
                    row["exact_gds_emx_receipt_path"],
                    f"line {line_number} exact_gds_emx_receipt_path",
                )
                expected_receipt_sha256 = _require_sha256(
                    row["exact_gds_emx_receipt_sha256"],
                    f"line {line_number} exact_gds_emx_receipt_sha256",
                )
                receipt_pin, receipt_bytes = _pin_regular_file(
                    receipt_path_input,
                    expected_sha256=expected_receipt_sha256,
                    expected_size=None,
                    label=f"line {line_number} exact-GDS fresh-EMX receipt",
                    capture_bytes=True,
                )
                emx_receipt = _decode_json_object(
                    receipt_bytes, f"line {line_number} exact-GDS fresh-EMX receipt"
                )
                emx_contract = _validate_exact_gds_emx_receipt(
                    emx_receipt,
                    candidate_sha256=candidate_sha256,
                    geometry_sha256=geometry_sha256,
                    line_number=line_number,
                )
                s4p_path = _resolve_artifact_path(
                    Path(receipt_pin.path),
                    emx_contract["touchstone_path"],
                    f"line {line_number} fresh-EMX Touchstone path",
                )
                s4p_pin, _ = _pin_regular_file(
                    s4p_path,
                    expected_sha256=emx_contract["touchstone_sha256"],
                    expected_size=emx_contract["touchstone_size_bytes"],
                    label=f"line {line_number} fresh-EMX Touchstone",
                    capture_bytes=False,
                )
                if Path(s4p_pin.path).suffix.lower() != ".s4p":
                    raise Broadband56S4pQaError(
                        f"line {line_number} fresh-EMX artifact is not .s4p"
                    )

                artifact = audit_exact56_s4p(Path(s4p_pin.path))
                _reverify_pin(receipt_pin, "exact-GDS fresh-EMX receipt")
                _reverify_pin(s4p_pin, "fresh-EMX Touchstone")

                identity = {
                    "accepted_sequence": sequence,
                    "geometry_id": geometry_id,
                    "geometry_sha256": geometry_sha256,
                    "candidate_id_sha256": candidate_sha256,
                    "campaign_phase": phase,
                    "acquisition_source": source_name,
                    "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                    "exact_gds_emx_receipt_sha256": receipt_pin.sha256,
                    "s4p_sha256": s4p_pin.sha256,
                }
                for feature_row in artifact.rows:
                    feature_writer.writerow({**identity, **feature_row})

                summary = artifact.summary
                index_writer.writerow(
                    {
                        **identity,
                        "exact_gds_emx_receipt_path": receipt_pin.path,
                        "exact_gds_emx_receipt_size_bytes": receipt_pin.size_bytes,
                        "source_exact_gds_sha256": emx_contract[
                            "source_exact_gds_sha256"
                        ],
                        "source_calibre_receipt_sha256": emx_contract[
                            "source_calibre_receipt_sha256"
                        ],
                        "s4p_path": s4p_pin.path,
                        "s4p_size_bytes": s4p_pin.size_bytes,
                        "port_count": summary["port_count"],
                        "frequency_points": summary["frequency_points"],
                        "frequency_start_hz": summary["frequency_start_hz"],
                        "frequency_stop_hz": summary["frequency_stop_hz"],
                        "frequency_step_hz": summary["frequency_step_hz"],
                        "s_to_z_roundtrip_max_abs_error": summary[
                            "s_to_z_roundtrip_max_abs_error"
                        ],
                        "passivity_fail_frequency_count": summary[
                            "passivity_fail_frequency_count"
                        ],
                        "reciprocity_fail_frequency_count": summary[
                            "reciprocity_fail_frequency_count"
                        ],
                        "broadband_descriptor_valid_rows": summary[
                            "broadband_descriptor_valid_rows"
                        ],
                        "strict_lumped_valid_rows": summary[
                            "strict_lumped_valid_rows"
                        ],
                        "primary_srf_status": summary["primary_srf"]["status"],
                        "secondary_srf_status": summary["secondary_srf"]["status"],
                        "qa_status": "PASS",
                    }
                )
                seen_geometry_sha256.add(geometry_sha256)
                seen_candidate_sha256.add(candidate_sha256)
                geometry_count += 1
                feature_row_count += len(artifact.rows)
                descriptor_valid_rows += int(
                    summary["broadband_descriptor_valid_rows"]
                )
                strict_valid_rows += int(summary["strict_lumped_valid_rows"])

        if geometry_count <= 0:
            raise Broadband56S4pQaError("fresh-EMX receipt index has no data rows")
        if expected_geometry_count is not None and geometry_count != int(
            expected_geometry_count
        ):
            raise Broadband56S4pQaError(
                "geometry count mismatch: "
                f"actual={geometry_count}, expected={expected_geometry_count}"
            )
        expected_rows = geometry_count * len(FREQUENCY_GRID_HZ)
        if feature_row_count != expected_rows:
            raise Broadband56S4pQaError(
                f"feature row count mismatch: actual={feature_row_count}, expected={expected_rows}"
            )
        _reverify_pin(source_pin, "fresh-EMX receipt index")

        feature_pin, _ = _pin_regular_file(
            feature_path,
            expected_sha256=None,
            expected_size=None,
            label="broadband long features",
            capture_bytes=False,
        )
        index_pin, _ = _pin_regular_file(
            qa_index_path,
            expected_sha256=None,
            expected_size=None,
            label="S4P QA index",
            capture_bytes=False,
        )
        manifest = {
            "schema": "rfic_transformer.broadband56_v2_exact56_feature_manifest.v1",
            "generated_utc": _utc_now(),
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "format": "csv",
            "geometry_count": geometry_count,
            "frequency_points_per_geometry": len(FREQUENCY_GRID_HZ),
            "total_row_count": feature_row_count,
            "frequency_vector_hz": list(FREQUENCY_GRID_HZ),
            "interpolation_or_resampling_used": False,
            "columns": list(LONG_FEATURE_FIELDS),
            "partition_count": 1,
            "partitions": [
                {
                    **feature_pin.public_record(),
                    "row_count": feature_row_count,
                }
            ],
        }
        _write_json_new(manifest_path, manifest)
        manifest_pin, _ = _pin_regular_file(
            manifest_path,
            expected_sha256=None,
            expected_size=None,
            label="broadband feature manifest",
            capture_bytes=False,
        )
        receipt = {
            "schema": QA_RECEIPT_SCHEMA,
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": QA_PASS_DECISION,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "source_fresh_emx_receipt_index": source_pin.public_record(),
            "qa_index": index_pin.public_record(),
            "broadband_features_long": feature_pin.public_record(),
            "broadband_features_manifest": manifest_pin.public_record(),
            "geometry_count": geometry_count,
            "geometry_unique_count": len(seen_geometry_sha256),
            "candidate_unique_count": len(seen_candidate_sha256),
            "frequency_points_per_geometry": len(FREQUENCY_GRID_HZ),
            "geometry_frequency_rows": feature_row_count,
            "broadband_descriptor_valid_rows": descriptor_valid_rows,
            "strict_lumped_valid_rows": strict_valid_rows,
            "frequency_contract": {
                "start_hz": FREQUENCY_GRID_HZ[0],
                "stop_hz": FREQUENCY_GRID_HZ[-1],
                "step_hz": 1_000_000_000,
                "points": len(FREQUENCY_GRID_HZ),
                "exact_hz": list(FREQUENCY_GRID_HZ),
                "interpolation_allowed": False,
                "resampling_used": False,
            },
            "port_contract": {
                "ports": 4,
                "port_order": list(PORT_AND_GROUNDING_CONTRACT["port_order"]),
                "port_mode": PORT_AND_GROUNDING_CONTRACT["port_mode"],
                "touchstone_extension": ".s4p",
            },
            "extraction_contract": {
                "s_to_z": "repository_network_analysis_s_to_z",
                "differential_projection": "single_ended_to_differential_z_external_P001_P002_P003_P004",
                "s_to_z_roundtrip_absolute_tolerance": S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE,
                "passivity_singular_value_tolerance": PASSIVITY_SINGULAR_VALUE_TOLERANCE,
                "reciprocity_absolute_tolerance": RECIPROCITY_ABSOLUTE_TOLERANCE,
                "passivity_and_reciprocity_are_diagnostic_not_acceptance_filters": True,
                "near_resonance_invalid_lumped_rows_do_not_reject_valid_s4p_geometry": True,
                "below_half_srf_method": (
                    "first_positive_to_nonpositive_reactance_crossing_"
                    "linear_bracket_conservative_when_censored"
                ),
            },
            "fresh_real_emx_receipts_required": True,
            "proxy_or_historical_labels_used": False,
            "simulator_action_taken": False,
        }
        _write_json_new(receipt_path, receipt)
        _write_sums_new(
            sums_path,
            [receipt_path, manifest_path, qa_index_path, feature_path],
            root=output,
        )
        return {
            "overall_status": "PASS",
            "receipt_path": str(receipt_path),
            "receipt_sha256": _sha256(receipt_path),
            "qa_index_path": str(qa_index_path),
            "long_features_path": str(feature_path),
            "feature_manifest_path": str(manifest_path),
            "sha256s_path": str(sums_path),
            "geometry_count": geometry_count,
            "geometry_frequency_rows": feature_row_count,
        }
    except Exception as exc:
        failure_path = output / QA_FAILURE_NAME
        if not failure_path.exists():
            _write_json_new(
                failure_path,
                {
                    "schema": QA_RECEIPT_SCHEMA,
                    "generated_utc": _utc_now(),
                    "overall_status": "FAIL",
                    "decision": "REJECT_EXACT56_S4P_QA_PRODUCTS",
                    "campaign_id": CAMPAIGN_ID,
                    "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                    "source_fresh_emx_receipt_index": source_pin.public_record(),
                    "geometry_rows_completed_before_failure": geometry_count,
                    "feature_rows_completed_before_failure": feature_row_count,
                    "error": f"{type(exc).__name__}: {exc}",
                    "simulator_action_taken": False,
                },
            )
        if isinstance(exc, Broadband56S4pQaError):
            raise
        raise Broadband56S4pQaError(str(exc)) from exc


def audit_exact56_s4p(path: Path) -> ArtifactQaResult:
    """Audit one exact 56-point four-port S4P and derive all feature rows."""

    try:
        touchstone = load_touchstone(path)
    except Exception as exc:  # noqa: BLE001 - preserve parser error.
        raise Broadband56S4pQaError(f"Touchstone parse failed: {exc}") from exc
    frequencies = np.asarray(touchstone.freqs_hz, dtype=np.float64)
    expected_frequencies = np.asarray(FREQUENCY_GRID_HZ, dtype=np.float64)
    s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
    checks = {
        "port_count_exact_four": int(touchstone.num_ports) == 4,
        "frequency_count_exact_56": int(touchstone.num_freqs) == len(FREQUENCY_GRID_HZ),
        "frequency_vector_exact": np.array_equal(frequencies, expected_frequencies),
        "frequency_strictly_increasing": bool(
            frequencies.size > 1 and np.all(np.diff(frequencies) > 0.0)
        ),
        "s_matrix_shape_exact": tuple(s_matrix.shape) == (56, 4, 4),
        "s_matrix_finite": bool(
            np.isfinite(s_matrix.real).all() and np.isfinite(s_matrix.imag).all()
        ),
        "reference_impedance_valid": _reference_impedance_valid(
            touchstone.reference_impedance_ohm, ports=4
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise Broadband56S4pQaError(
            "fresh-EMX S4P exact contract failed: " + ", ".join(failed)
        )

    try:
        z_matrix = np.asarray(touchstone.to_z_parameters(), dtype=np.complex128)
    except Exception as exc:  # noqa: BLE001 - preserve conversion failure.
        raise Broadband56S4pQaError(f"S-to-Z conversion failed: {exc}") from exc
    if tuple(z_matrix.shape) != (56, 4, 4) or not (
        np.isfinite(z_matrix.real).all() and np.isfinite(z_matrix.imag).all()
    ):
        raise Broadband56S4pQaError("S-to-Z conversion produced incomplete or non-finite Z")
    try:
        reconstructed_s = z_to_s(
            z_matrix, z0=touchstone.reference_impedance_ohm
        )
    except Exception as exc:  # noqa: BLE001
        raise Broadband56S4pQaError(f"Z-to-S roundtrip failed: {exc}") from exc
    roundtrip_error = float(np.max(np.abs(reconstructed_s - s_matrix)))
    if not math.isfinite(roundtrip_error) or roundtrip_error > S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE:
        raise Broadband56S4pQaError(
            "S-to-Z roundtrip exceeds tolerance: "
            f"actual={roundtrip_error:.17g}, allowed={S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE:.17g}"
        )
    z_diff = single_ended_to_differential_z(z_matrix)
    if tuple(z_diff.shape) != (56, 2, 2) or not (
        np.isfinite(z_diff.real).all() and np.isfinite(z_diff.imag).all()
    ):
        raise Broadband56S4pQaError(
            "differential Z projection produced incomplete or non-finite data"
        )

    primary_srf = _estimate_first_srf(frequencies, z_diff[:, 0, 0].imag)
    secondary_srf = _estimate_first_srf(frequencies, z_diff[:, 1, 1].imag)
    rows: list[dict[str, Any]] = []
    passivity_failures = 0
    reciprocity_failures = 0
    descriptor_valid_rows = 0
    strict_valid_rows = 0

    for frequency_index, frequency_hz in enumerate(FREQUENCY_GRID_HZ):
        s_at_frequency = s_matrix[frequency_index]
        z_at_frequency = z_matrix[frequency_index]
        z_diff_at_frequency = z_diff[frequency_index]
        omega = 2.0 * math.pi * float(frequency_hz)
        z11 = complex(z_diff_at_frequency[0, 0])
        z22 = complex(z_diff_at_frequency[1, 1])
        z21 = complex(z_diff_at_frequency[1, 0])
        lp_h = float(z11.imag / omega)
        ls_h = float(z22.imag / omega)
        mutual_h = float(z21.imag / omega)
        product = abs(lp_h * ls_h)
        signed_k = mutual_h / math.sqrt(product) if product > 1.0e-30 else math.nan
        qp = _derived_ratio(z11.imag, z11.real)
        qs = _derived_ratio(z22.imag, z22.real)
        qmin = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
        ls_over_lp = _derived_ratio(ls_h, lp_h)
        features = {
            "lp_h": lp_h,
            "ls_h": ls_h,
            "lp_nh": lp_h * 1.0e9,
            "ls_nh": ls_h * 1.0e9,
            "qp": qp,
            "qs": qs,
            "qmin": qmin,
            "mutual_inductance_h": mutual_h,
            "signed_k": signed_k,
            "k_abs": abs(signed_k),
            "ls_over_lp": ls_over_lp,
            "xp_ohm": omega * lp_h,
            "xs_ohm": omega * ls_h,
        }
        finite_values = bool(
            np.isfinite(s_at_frequency.real).all()
            and np.isfinite(s_at_frequency.imag).all()
            and np.isfinite(z_at_frequency.real).all()
            and np.isfinite(z_at_frequency.imag).all()
            and all(math.isfinite(value) for value in features.values())
        )
        positive_primary_resistance = z11.real > 0.0
        positive_secondary_resistance = z22.real > 0.0
        positive_primary_reactance = z11.imag > 0.0
        positive_secondary_reactance = z22.imag > 0.0
        broadband_valid = bool(
            finite_values
            and positive_primary_resistance
            and positive_secondary_resistance
            and positive_primary_reactance
            and positive_secondary_reactance
        )
        below_half_srf = _below_half_srf(
            float(frequency_hz), primary_srf
        ) and _below_half_srf(float(frequency_hz), secondary_srf)
        strict_valid = bool(broadband_valid and below_half_srf)
        descriptor_valid_rows += int(broadband_valid)
        strict_valid_rows += int(strict_valid)

        max_singular_value = float(np.linalg.svd(s_at_frequency, compute_uv=False)[0])
        passivity_status = (
            "PASS"
            if max_singular_value <= 1.0 + PASSIVITY_SINGULAR_VALUE_TOLERANCE
            else "FAIL"
        )
        reciprocity_error = float(np.max(np.abs(s_at_frequency - s_at_frequency.T)))
        reciprocity_status = (
            "PASS"
            if reciprocity_error <= RECIPROCITY_ABSOLUTE_TOLERANCE
            else "FAIL"
        )
        passivity_failures += int(passivity_status == "FAIL")
        reciprocity_failures += int(reciprocity_status == "FAIL")

        broad_inside, broad_reasons = _broad_envelope(features)
        practical_inside = bool(
            0.10 <= features["k_abs"] <= 0.85
            and 0.50 <= features["ls_over_lp"] <= 2.0
        )
        matrix_values: dict[str, float] = {}
        for matrix_name, matrix in (
            ("s", s_at_frequency),
            ("z", z_at_frequency),
        ):
            for row_index in range(4):
                for col_index in range(4):
                    value = complex(matrix[row_index, col_index])
                    matrix_values[f"{matrix_name}{row_index + 1}{col_index + 1}_re"] = float(
                        value.real
                    )
                    matrix_values[f"{matrix_name}{row_index + 1}{col_index + 1}_im"] = float(
                        value.imag
                    )
        rows.append(
            {
                "frequency_hz": int(frequency_hz),
                **features,
                "finite_values": _bool_text(finite_values),
                "positive_primary_resistance": _bool_text(
                    positive_primary_resistance
                ),
                "positive_secondary_resistance": _bool_text(
                    positive_secondary_resistance
                ),
                "positive_primary_inductive_reactance": _bool_text(
                    positive_primary_reactance
                ),
                "positive_secondary_inductive_reactance": _bool_text(
                    positive_secondary_reactance
                ),
                "extraction_continuity_status": "PASS",
                "below_half_srf": _bool_text(below_half_srf),
                "broadband_descriptor_valid": _bool_text(broadband_valid),
                "strict_lumped_valid": _bool_text(strict_valid),
                "srf_status": (
                    f"PRIMARY={primary_srf.status};SECONDARY={secondary_srf.status}"
                ),
                "passivity_status": passivity_status,
                "reciprocity_status": reciprocity_status,
                "inside_broad_response_envelope": _bool_text(broad_inside),
                "inside_literature_practical_panel": _bool_text(
                    practical_inside
                ),
                "outside_envelope_reason": "" if broad_inside else ";".join(broad_reasons),
                **matrix_values,
            }
        )

    return ArtifactQaResult(
        rows=tuple(rows),
        summary={
            "port_count": 4,
            "frequency_points": 56,
            "frequency_start_hz": FREQUENCY_GRID_HZ[0],
            "frequency_stop_hz": FREQUENCY_GRID_HZ[-1],
            "frequency_step_hz": 1_000_000_000,
            "s_to_z_roundtrip_max_abs_error": roundtrip_error,
            "passivity_fail_frequency_count": passivity_failures,
            "reciprocity_fail_frequency_count": reciprocity_failures,
            "broadband_descriptor_valid_rows": descriptor_valid_rows,
            "strict_lumped_valid_rows": strict_valid_rows,
            "primary_srf": _srf_record(primary_srf),
            "secondary_srf": _srf_record(secondary_srf),
            "checks": checks,
        },
    )


def _validate_exact_gds_emx_receipt(
    receipt: Mapping[str, Any],
    *,
    candidate_sha256: str,
    geometry_sha256: str,
    line_number: int,
) -> dict[str, Any]:
    output = receipt.get("emx_output")
    manifest_contract = receipt.get("manifest_contract")
    frequency_contract = receipt.get("frequency_contract")
    if not isinstance(output, Mapping):
        raise Broadband56S4pQaError(
            f"line {line_number} exact-GDS fresh-EMX receipt lacks emx_output"
        )
    if not isinstance(manifest_contract, Mapping):
        raise Broadband56S4pQaError(
            f"line {line_number} exact-GDS fresh-EMX receipt lacks manifest_contract"
        )
    if not isinstance(frequency_contract, Mapping):
        raise Broadband56S4pQaError(
            f"line {line_number} exact-GDS fresh-EMX receipt lacks frequency_contract"
        )
    checks = {
        "schema": receipt.get("schema") == EXACT_GDS_EMX_RECEIPT_SCHEMA,
        "overall_status": receipt.get("overall_status") == "PASS",
        "decision": receipt.get("decision") == EXACT_GDS_EMX_PASS_DECISION,
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract_fingerprint": receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate_id": str(receipt.get("candidate_id_sha256") or "").lower()
        == candidate_sha256,
        "geometry_identity": str(
            receipt.get("geometry_identity_sha256") or ""
        ).lower()
        == geometry_sha256,
        "fresh_real_emx": receipt.get("fresh_real_emx_executed") is True,
        "proxy_or_historical_label_excluded": receipt.get(
            "proxy_or_historical_label_used"
        )
        is False,
        "source_pins_unchanged": receipt.get("source_pins_unchanged_after_emx")
        is True,
        "no_cadence_in_emx_runner": receipt.get("cadence_executed_by_this_runner")
        is False,
        "no_calibre_in_emx_runner": receipt.get("calibre_executed_by_this_runner")
        is False,
        "no_gds_regeneration": receipt.get("gds_generated_or_copied_by_this_runner")
        is False,
        "simulator_action_recorded": receipt.get("simulator_action_taken") is True,
        "frequency_vector_exact": tuple(
            int(value) for value in frequency_contract.get("exact_hz", [])
        )
        == FREQUENCY_GRID_HZ,
        "frequency_points_exact": frequency_contract.get("points") == 56,
        "port_order_exact": tuple(manifest_contract.get("port_order") or ())
        == tuple(PORT_AND_GROUNDING_CONTRACT["port_order"]),
        "signal_labels_exact": tuple(manifest_contract.get("signal_labels") or ())
        == tuple(PORT_AND_GROUNDING_CONTRACT["port_order"]),
        "pin_purpose_exact": manifest_contract.get("cadence_pin_purpose")
        == PORT_AND_GROUNDING_CONTRACT["cadence_pin_purpose"],
        "output_ports_exact": output.get("num_ports") == 4,
        "output_frequency_points_exact": output.get("num_frequency_points") == 56,
        "output_frequency_start_exact": output.get("frequency_start_hz")
        == FREQUENCY_GRID_HZ[0],
        "output_frequency_stop_exact": output.get("frequency_stop_hz")
        == FREQUENCY_GRID_HZ[-1],
        "output_frequency_step_exact": output.get("frequency_step_hz")
        == 1_000_000_000,
        "output_checks_exact": _all_true_exact_output_checks(output.get("checks")),
        "forbidden_output_empty": _forbidden_output_empty(
            receipt.get("forbidden_output_scan")
        ),
    }
    for name in (
        "full_campaign_authorization_receipt",
        "private_configuration",
        "source_calibre_zero_blocking_receipt",
        "source_calibre_report",
        "source_exact_gds",
        "source_layout_manifest",
    ):
        checks[f"{name}_evidence"] = _evidence_record_valid(receipt.get(name))
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise Broadband56S4pQaError(
            f"line {line_number} exact-GDS fresh-EMX receipt failed: "
            + ", ".join(failed)
        )
    touchstone_path = str(output.get("touchstone_path") or "").strip()
    if not touchstone_path:
        raise Broadband56S4pQaError(
            f"line {line_number} exact-GDS fresh-EMX receipt lacks Touchstone path"
        )
    return {
        "touchstone_path": touchstone_path,
        "touchstone_size_bytes": _positive_int(
            output.get("touchstone_size_bytes"),
            f"line {line_number} touchstone_size_bytes",
        ),
        "touchstone_sha256": _require_sha256(
            output.get("touchstone_sha256"),
            f"line {line_number} touchstone_sha256",
        ),
        "source_exact_gds_sha256": str(
            receipt["source_exact_gds"]["sha256"]
        ).lower(),
        "source_calibre_receipt_sha256": str(
            receipt["source_calibre_zero_blocking_receipt"]["sha256"]
        ).lower(),
    }


def _all_true_exact_output_checks(value: Any) -> bool:
    expected = {
        "port_count_exact_four",
        "frequency_count_exact_56",
        "frequency_vector_exact",
        "s_matrix_shape_exact",
        "s_matrix_finite",
    }
    return isinstance(value, Mapping) and all(value.get(name) is True for name in expected)


def _forbidden_output_empty(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return all(value.get(name) == [] for name in ("gds_files", "symlinks", "forbidden_directories"))


def _evidence_record_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    path = str(value.get("path") or "").strip()
    try:
        size = int(value.get("size_bytes"))
    except (TypeError, ValueError):
        return False
    digest = str(value.get("sha256") or "").strip().lower()
    return bool(path and size > 0 and SHA256_PATTERN.fullmatch(digest))


def _estimate_first_srf(frequencies_hz: np.ndarray, reactance: np.ndarray) -> SrfEstimate:
    values = np.asarray(reactance, dtype=np.float64)
    if values.shape != frequencies_hz.shape or not np.isfinite(values).all():
        raise Broadband56S4pQaError("SRF reactance series is incomplete or non-finite")
    if values[0] <= 0.0:
        return SrfEstimate(
            status="AT_OR_BELOW_FIRST_5_GHZ_POINT",
            estimate_hz=None,
            lower_bound_hz=None,
        )
    for index in range(1, len(values)):
        if values[index] <= 0.0 and values[index - 1] > 0.0:
            f0 = float(frequencies_hz[index - 1])
            f1 = float(frequencies_hz[index])
            x0 = float(values[index - 1])
            x1 = float(values[index])
            if x1 == x0:
                estimate = f1
            else:
                estimate = f0 + (0.0 - x0) * (f1 - f0) / (x1 - x0)
            estimate = min(max(estimate, f0), f1)
            return SrfEstimate(
                status=f"BRACKETED_{int(f0)}_{int(f1)}_HZ",
                estimate_hz=estimate,
                lower_bound_hz=None,
            )
    return SrfEstimate(
        status="CENSORED_ABOVE_60_GHZ",
        estimate_hz=None,
        lower_bound_hz=float(FREQUENCY_GRID_HZ[-1]),
    )


def _below_half_srf(frequency_hz: float, srf: SrfEstimate) -> bool:
    if srf.estimate_hz is not None:
        return 2.0 * frequency_hz < srf.estimate_hz
    if srf.lower_bound_hz is not None:
        return 2.0 * frequency_hz <= srf.lower_bound_hz
    return False


def _srf_record(value: SrfEstimate) -> dict[str, Any]:
    return {
        "status": value.status,
        "estimate_hz": value.estimate_hz,
        "lower_bound_hz": value.lower_bound_hz,
    }


def _broad_envelope(features: Mapping[str, float]) -> tuple[bool, list[str]]:
    checks = (
        ("lp_nh", 0.03 <= features["lp_nh"] <= 8.0),
        ("ls_nh", 0.03 <= features["ls_nh"] <= 8.0),
        ("xp_ohm", 10.0 <= features["xp_ohm"] <= 250.0),
        ("xs_ohm", 10.0 <= features["xs_ohm"] <= 250.0),
        ("qp", 2.0 <= features["qp"] <= 35.0),
        ("qs", 2.0 <= features["qs"] <= 35.0),
        ("k_abs", 0.05 <= features["k_abs"] <= 0.85),
        ("ls_over_lp", 0.25 <= features["ls_over_lp"] <= 4.0),
    )
    reasons = [f"{name}_outside_broad_envelope" for name, passed in checks if not passed]
    return not reasons, reasons


def _reference_impedance_valid(value: Any, *, ports: int) -> bool:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 0:
        return bool(math.isfinite(float(array)) and float(array) > 0.0)
    return bool(
        array.ndim == 1
        and array.shape == (ports,)
        and np.isfinite(array).all()
        and np.all(array > 0.0)
    )


def _derived_ratio(numerator: float, denominator: float) -> float:
    if abs(float(denominator)) <= 1.0e-18:
        return math.nan
    value = float(numerator) / float(denominator)
    return value if math.isfinite(value) else math.nan


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _canonical_positive_int(value: Any, label: str) -> int:
    text = str(value or "").strip()
    try:
        parsed = int(text)
    except (TypeError, ValueError) as exc:
        raise Broadband56S4pQaError(f"{label} is not an integer") from exc
    if str(parsed) != text or parsed <= 0:
        raise Broadband56S4pQaError(f"{label} is not a canonical positive integer")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise Broadband56S4pQaError(f"{label} is not an integer") from exc
    if parsed <= 0:
        raise Broadband56S4pQaError(f"{label} must be positive")
    return parsed


def _require_sha256(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise Broadband56S4pQaError(f"{label} is not SHA-256")
    return digest


def _resolve_artifact_path(source_path: Path, value: Any, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise Broadband56S4pQaError(f"{label} is empty")
    path = Path(text)
    if not path.is_absolute():
        path = source_path.parent / path
    return _absolute_lexical(path)


def _pin_regular_file(
    path: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
    label: str,
    capture_bytes: bool,
) -> tuple[FilePin, bytes]:
    absolute = _absolute_lexical(path)
    if _path_has_symlink_component(absolute):
        raise Broadband56S4pQaError(f"{label} path contains a symlink component")
    try:
        before = os.lstat(absolute)
    except OSError as exc:
        raise Broadband56S4pQaError(f"{label} is missing: {absolute}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise Broadband56S4pQaError(f"{label} is not a nonempty regular file")
    descriptor = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    captured: list[bytes] = []
    try:
        opened = os.fstat(descriptor)
        identity = _stat_identity(opened)
        if identity != _stat_identity(before):
            raise Broadband56S4pQaError(f"{label} identity changed before read")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if capture_bytes:
                captured.append(chunk)
        after_fd = os.fstat(descriptor)
        after_path = os.lstat(absolute)
        if identity != _stat_identity(after_fd) or identity != _stat_identity(after_path):
            raise Broadband56S4pQaError(f"{label} identity changed during read")
    finally:
        os.close(descriptor)
    actual_sha256 = digest.hexdigest()
    if expected_sha256 is not None and actual_sha256 != _require_sha256(
        expected_sha256, f"expected {label} SHA-256"
    ):
        raise Broadband56S4pQaError(f"{label} SHA-256 mismatch")
    if expected_size is not None and int(expected_size) != int(opened.st_size):
        raise Broadband56S4pQaError(f"{label} size mismatch")
    return (
        FilePin(
            path=str(absolute),
            size_bytes=int(opened.st_size),
            sha256=actual_sha256,
            device=int(opened.st_dev),
            inode=int(opened.st_ino),
            mtime_ns=int(opened.st_mtime_ns),
            ctime_ns=int(opened.st_ctime_ns),
        ),
        b"".join(captured),
    )


def _reverify_pin(pin: FilePin, label: str) -> None:
    current, _ = _pin_regular_file(
        Path(pin.path),
        expected_sha256=pin.sha256,
        expected_size=pin.size_bytes,
        label=label,
        capture_bytes=False,
    )
    if current != pin:
        raise Broadband56S4pQaError(f"{label} inode identity changed")


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _path_has_symlink_component(path: Path) -> bool:
    absolute = _absolute_lexical(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
        except FileNotFoundError:
            continue
    return False


def _decode_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise Broadband56S4pQaError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise Broadband56S4pQaError(f"{label} must be a JSON object")
    return value


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_sums_new(path: Path, files: Sequence[Path], *, root: Path) -> None:
    lines = [f"{_sha256(item)}  {item.relative_to(root)}" for item in files]
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "Broadband56S4pQaError",
    "FEATURE_MANIFEST_NAME",
    "LONG_FEATURES_NAME",
    "QA_FAILURE_NAME",
    "QA_INDEX_NAME",
    "QA_PASS_DECISION",
    "QA_RECEIPT_NAME",
    "QA_RECEIPT_SCHEMA",
    "SHA256SUMS_NAME",
    "audit_exact56_s4p",
    "build_exact56_s4p_qa_products",
]
