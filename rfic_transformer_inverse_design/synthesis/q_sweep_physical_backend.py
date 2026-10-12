"""Fail-closed physical backend for the exact Q=10..20 GUI sweep.

The runner binds one frozen Q-sweep request to one hash-pinned grounded
four-port configuration and process file.  Only a complete eleven-candidate
fresh-EMX batch can produce ``physical_results.json``.  Calibre DRC and
independent HFSS correlation remain separate evidence gates.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..core.adapter import TransformerOptimizationAdapter
from ..core.defaults import load_run_config
from ..core.topology import TransformerSpec
from ..core.types import TransformerRunConfig
from ..execution.evaluator import TransformerEmxEvaluator
from ..execution.zeus_cadence import run_transformer_zeus_cadence_roundtrip_batch
from ..layout.export import export_transformer_layout
from .physical_evidence import (
    EXPECTED_DIFFERENTIAL_PAIRS,
    EXPECTED_FEATURE_EXTRACTION_HZ,
    EXPECTED_FREQUENCY_POINT_COUNT,
    EXPECTED_FREQUENCY_START_HZ,
    EXPECTED_FREQUENCY_STEP_HZ,
    EXPECTED_FREQUENCY_STOP_HZ,
    EXPECTED_GROUND_MODE,
    EXPECTED_PORTS,
    PHYSICAL_EVIDENCE_SCHEMA,
    STRUCTURED_EXPORTER_ID,
    validate_physical_evidence_manifest,
)
from .q_sweep import (
    Q_SWEEP_VALUES,
    _extract_s4p_features_15ghz,
    _geometry_sha256,
)


BACKEND_RECEIPT_SCHEMA = "rfic_q_sweep_physical_backend_receipt.v1"
PHYSICAL_RESULTS_SCHEMA = "rfic_q_sweep_physical_results.v1"
PHYSICAL_REQUEST_SCHEMA = "rfic_q_sweep_physical_request.v1"
BACKEND_RECEIPT_FILENAME = "backend_receipt.json"
BACKEND_CLAIM_FILENAME = "backend_claim.json"

REQUEST_GEOMETRY_FIELDS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)
EXPECTED_ADAPTER_FIELDS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "primary_width_um",
    "secondary_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)
TARGET_FEATURE_FIELDS = ("Lp_nH", "Ls_nH", "Q_scalar", "K_abs")
REQUIRED_OUTPUTS = {"GDS", "S4P", "Lp", "Ls", "Qp", "Qs", "K_abs"}
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_CANDIDATE_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\Z")


class PhysicalBackendContractError(ValueError):
    """A fail-closed preflight or execution-contract violation."""

    def __init__(
        self,
        message: str,
        *,
        checks: Sequence[dict[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.checks = tuple(dict(item) for item in checks)


class PhysicalBackendExecutionError(PhysicalBackendContractError):
    """A terminal batch failure carrying all candidate-level audit records."""

    def __init__(
        self,
        message: str,
        *,
        candidate_records: Sequence[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.candidate_records = tuple(dict(item) for item in candidate_records)


@dataclass(frozen=True)
class PreparedCandidate:
    """One request-bound 10-D geometry mapped to the active 11-field adapter."""

    candidate_id: str
    q_target: int
    geometry_sha256: str
    geometry_um: dict[str, float]
    adapter_vector: tuple[float, ...]
    geometry: TransformerSpec


@dataclass(frozen=True)
class PhysicalBackendPreflight:
    """Hash-pinned inputs and mapped candidates ready for evaluator execution."""

    request_path: Path
    request_sha256: str
    request: dict[str, Any]
    config_path: Path
    config_sha256: str
    process_path: Path
    process_sha256: str
    run_config: TransformerRunConfig
    adapter: TransformerOptimizationAdapter
    candidates: tuple[PreparedCandidate, ...]
    source_paths: dict[str, Path]
    source_sha256: dict[str, str]
    checks: tuple[dict[str, Any], ...]


def preflight_physical_backend(
    *,
    request_json: str | Path,
    config: str | Path,
    config_sha256: str,
    process_sha256: str,
    expected_request_schema: str = PHYSICAL_REQUEST_SCHEMA,
    expected_label_source: str = "FRESH_REAL_EMX",
    required_outputs: frozenset[str] | set[str] = frozenset(REQUIRED_OUTPUTS),
) -> PhysicalBackendPreflight:
    """Validate hashes, the grounded four-port contract, and all geometries.

    The optional request literals let the packaged layout-only runner reuse the
    exact same configuration, process, port, frequency, and 10-D mapping gates.
    The physical runner keeps the stricter fresh-EMX defaults above.
    """

    checks: list[dict[str, Any]] = []
    request_path = _verified_file(request_json, "request JSON", checks)
    config_path = _verified_file(config, "config", checks)

    expected_config_sha256 = _normalized_sha256(
        config_sha256, "--config-sha256", checks
    )
    actual_config_sha256 = _sha256(config_path)
    _require(
        checks,
        "config_sha256_matches",
        actual_config_sha256 == expected_config_sha256,
        {
            "expected": expected_config_sha256,
            "actual": actual_config_sha256,
            "path": str(config_path),
        },
    )

    try:
        run_config = load_run_config(config_path)
    except Exception as exc:
        _fail(
            checks,
            "config_loads",
            f"{type(exc).__name__}: {exc}",
        )
    _pass(checks, "config_loads", str(config_path))

    process_raw = Path(run_config.emx.emx_process_file).expanduser()
    _require(
        checks,
        "process_path_is_absolute",
        process_raw.is_absolute(),
        str(process_raw),
    )
    process_path = _verified_file(process_raw, "EMX process file", checks)
    expected_process_sha256 = _normalized_sha256(
        process_sha256, "--process-sha256", checks
    )
    actual_process_sha256 = _sha256(process_path)
    _require(
        checks,
        "process_sha256_matches",
        actual_process_sha256 == expected_process_sha256,
        {
            "expected": expected_process_sha256,
            "actual": actual_process_sha256,
            "path": str(process_path),
        },
    )

    adapter = TransformerOptimizationAdapter(run_config.bounds)
    _validate_grounded_four_port_config(run_config, adapter, checks)

    try:
        request = _load_json_object(request_path)
    except Exception as exc:
        _fail(
            checks,
            "request_json_loads",
            f"{type(exc).__name__}: {exc}",
        )
    _pass(checks, "request_json_loads", str(request_path))
    # The GUI consumer retains the current operating-point/DRC evidence gate.
    # This archived native producer does not yet emit those proofs. Reject the
    # unsupported request before native dispatch, not after spending solver time.
    if expected_request_schema == PHYSICAL_REQUEST_SCHEMA:
        requested_evidence = request.get("required_evidence") or []
        unsupported_evidence = [
            name for name in ("operating_point_evidence", "physical_gate_evidence")
            if name in requested_evidence
        ]
        if unsupported_evidence:
            _fail(
                checks,
                "operating_point_evidence_producer_available",
                "NOT_NATIVE_ACCEPTED: packaged physical backend does not produce "
                "the required operating-point QA and candidate-bound DRC proofs; "
                "no native dispatch is permitted for this request. Missing: "
                + ", ".join(unsupported_evidence),
            )
    candidates = _prepare_request_candidates(
        request,
        adapter,
        checks,
        expected_request_schema=expected_request_schema,
        expected_label_source=expected_label_source,
        required_outputs=set(required_outputs),
    )

    source_paths = _runtime_source_paths(checks)
    source_sha256 = {name: _sha256(path) for name, path in source_paths.items()}
    _pass(
        checks,
        "runtime_sources_hashable",
        {name: str(path) for name, path in source_paths.items()},
    )
    return PhysicalBackendPreflight(
        request_path=request_path,
        request_sha256=_sha256(request_path),
        request=request,
        config_path=config_path,
        config_sha256=actual_config_sha256,
        process_path=process_path,
        process_sha256=actual_process_sha256,
        run_config=run_config,
        adapter=adapter,
        candidates=candidates,
        source_paths=source_paths,
        source_sha256=source_sha256,
        checks=tuple(checks),
    )


def _validate_grounded_four_port_config(
    run_config: TransformerRunConfig,
    adapter: TransformerOptimizationAdapter,
    checks: list[dict[str, Any]],
) -> None:
    target = run_config.target
    emx = run_config.emx
    power_line = emx.power_line_8port
    role_labels = dict(power_line.role_labels)
    expected_roles = {
        "primary_top": "P001",
        "primary_bottom": "P002",
        "secondary_top": "P003",
        "secondary_bottom": "P004",
        "left_power_top": "P005",
        "left_power_bottom": "P006",
        "right_power_top": "P007",
        "right_power_bottom": "P008",
    }
    _require(
        checks,
        "local_execution_binds_hashable_process",
        emx.execution_mode == "local",
        emx.execution_mode,
    )
    _require(
        checks,
        "topology_is_current_1t1t",
        target.topology_mode == "1t1t"
        and run_config.bounds.topology_mode == "1t1t"
        and run_config.bounds.primary_turns == 1
        and run_config.bounds.secondary_turns == 1,
        {
            "target": target.topology_mode,
            "bounds": run_config.bounds.topology_mode,
            "turns": [
                run_config.bounds.primary_turns,
                run_config.bounds.secondary_turns,
            ],
        },
    )
    _require(
        checks,
        "center_tapped_grounded_power_line",
        run_config.bounds.primary_center_tap
        and run_config.bounds.secondary_center_tap
        and run_config.bounds.primary.vdd_bar is not None
        and run_config.bounds.primary.vdd_bar.enabled
        and run_config.bounds.secondary.vdd_bar is not None
        and run_config.bounds.secondary.vdd_bar.enabled
        and run_config.bounds.shield.enabled,
        {
            "primary_center_tap": run_config.bounds.primary_center_tap,
            "secondary_center_tap": run_config.bounds.secondary_center_tap,
            "shield_enabled": run_config.bounds.shield.enabled,
        },
    )
    _require(
        checks,
        "four_port_ground_mode",
        emx.port_mode == EXPECTED_GROUND_MODE
        and power_line.enabled
        and power_line.touchstone_mode == "signal_4_grounded_aux"
        and not emx.ground_unused_s8p_ports,
        {
            "port_mode": emx.port_mode,
            "power_line_enabled": power_line.enabled,
            "touchstone_mode": power_line.touchstone_mode,
            "ground_unused_s8p_ports": emx.ground_unused_s8p_ports,
        },
    )
    _require(
        checks,
        "port_map_is_P001_to_P004",
        tuple(power_line.port_map) == EXPECTED_PORTS
        and role_labels == expected_roles,
        {
            "port_map": list(power_line.port_map),
            "role_labels": role_labels,
        },
    )
    expected_pair_indices = ((0, 1), (2, 3))
    _require(
        checks,
        "differential_pairs_are_P001_P002_and_P003_P004",
        emx.differential_port_pairs == expected_pair_indices,
        emx.differential_port_pairs,
    )
    frequencies = np.asarray(target.frequency_points_hz(), dtype=float)
    expected_frequencies = np.linspace(
        EXPECTED_FREQUENCY_START_HZ,
        EXPECTED_FREQUENCY_STOP_HZ,
        EXPECTED_FREQUENCY_POINT_COUNT,
    )
    _require(
        checks,
        "target_frequency_is_15ghz",
        math.isclose(
            float(target.f0_hz),
            EXPECTED_FEATURE_EXTRACTION_HZ,
            rel_tol=0.0,
            abs_tol=1.0,
        ),
        float(target.f0_hz),
    )
    _require(
        checks,
        "frequency_grid_is_5_to_60ghz_half_ghz_111",
        target.frequency_start_hz == EXPECTED_FREQUENCY_START_HZ
        and target.frequency_stop_hz == EXPECTED_FREQUENCY_STOP_HZ
        and target.frequency_step_hz == EXPECTED_FREQUENCY_STEP_HZ
        and frequencies.shape == expected_frequencies.shape
        and np.allclose(
            frequencies,
            expected_frequencies,
            rtol=0.0,
            atol=1.0,
        )
        and int(target.band_points) == EXPECTED_FREQUENCY_POINT_COUNT,
        {
            "point_count": int(len(frequencies)),
            "start_hz": None if not len(frequencies) else float(frequencies[0]),
            "stop_hz": None if not len(frequencies) else float(frequencies[-1]),
        },
    )
    _require(
        checks,
        "adapter_fields_match_shared_line_width_contract",
        adapter.field_order() == EXPECTED_ADAPTER_FIELDS,
        list(adapter.field_order()),
    )


def _prepare_request_candidates(
    request: dict[str, Any],
    adapter: TransformerOptimizationAdapter,
    checks: list[dict[str, Any]],
    *,
    expected_request_schema: str = PHYSICAL_REQUEST_SCHEMA,
    expected_label_source: str = "FRESH_REAL_EMX",
    required_outputs: set[str] | frozenset[str] = frozenset(REQUIRED_OUTPUTS),
) -> tuple[PreparedCandidate, ...]:
    _require(
        checks,
        "request_schema",
        request.get("schema") == expected_request_schema,
        request.get("schema"),
    )
    _require(
        checks,
        "request_has_expected_evidence_source",
        request.get("required_label_source") == expected_label_source,
        request.get("required_label_source"),
    )
    _require(
        checks,
        "request_target_frequency_is_15ghz",
        _is_numeric(request.get("target_frequency_ghz"))
        and math.isclose(
            float(request["target_frequency_ghz"]),
            15.0,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ),
        request.get("target_frequency_ghz"),
    )
    _require(
        checks,
        "request_q_grid_is_exact_10_to_20",
        request.get("q_values") == list(Q_SWEEP_VALUES),
        request.get("q_values"),
    )
    outputs = request.get("required_outputs")
    _require(
        checks,
        "request_has_required_outputs",
        isinstance(outputs, list)
        and all(isinstance(item, str) for item in outputs)
        and set(required_outputs).issubset(set(outputs)),
        outputs,
    )
    _require(
        checks,
        "request_has_model_identity",
        isinstance(request.get("model_identity"), dict),
        type(request.get("model_identity")).__name__,
    )
    rows = request.get("candidates")
    _require(
        checks,
        "request_has_exactly_eleven_candidates",
        isinstance(rows, list) and len(rows) == len(Q_SWEEP_VALUES),
        None if not isinstance(rows, list) else len(rows),
    )

    prepared: list[PreparedCandidate] = []
    fixed_target: tuple[float, float, float] | None = None
    for index, row in enumerate(rows):
        try:
            candidate, candidate_target = _prepare_candidate(row, adapter)
        except Exception as exc:
            _fail(
                checks,
                "candidate_contract_and_geometry_mapping",
                {
                    "index": index,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        if fixed_target is None:
            fixed_target = candidate_target
        elif candidate_target != fixed_target:
            _fail(
                checks,
                "three_physical_features_are_fixed",
                {
                    "index": index,
                    "expected": fixed_target,
                    "actual": candidate_target,
                },
            )
        prepared.append(candidate)
    _pass(
        checks,
        "candidate_contract_and_geometry_mapping",
        {
            "count": len(prepared),
            "request_geometry_dimensions": len(REQUEST_GEOMETRY_FIELDS),
            "adapter_dimensions": len(EXPECTED_ADAPTER_FIELDS),
            "shared_line_width_mapping": [
                "line_width_um->primary_width_um",
                "line_width_um->secondary_width_um",
            ],
        },
    )
    prepared.sort(key=lambda item: item.q_target)
    _require(
        checks,
        "candidate_q_values_are_unique_and_complete",
        tuple(item.q_target for item in prepared) == Q_SWEEP_VALUES
        and len({item.candidate_id for item in prepared}) == len(Q_SWEEP_VALUES),
        {
            "q_values": [item.q_target for item in prepared],
            "candidate_ids": [item.candidate_id for item in prepared],
        },
    )
    _require(
        checks,
        "candidate_geometries_are_unique",
        len({item.geometry_sha256 for item in prepared}) == len(Q_SWEEP_VALUES),
        [item.geometry_sha256 for item in prepared],
    )
    _pass(checks, "three_physical_features_are_fixed", fixed_target)
    return tuple(prepared)


def _prepare_candidate(
    row: Any,
    adapter: TransformerOptimizationAdapter,
) -> tuple[PreparedCandidate, tuple[float, float, float]]:
    if not isinstance(row, dict):
        raise TypeError("candidate must be an object")
    candidate_id = row.get("candidate_id")
    if (
        not isinstance(candidate_id, str)
        or _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None
    ):
        raise ValueError("candidate_id must contain only letters, digits, dot, dash, underscore")
    q_target = _strict_int(row.get("q_target"), "q_target")
    if q_target not in Q_SWEEP_VALUES:
        raise ValueError("q_target must be an integer from 10 through 20")

    target = row.get("target_features")
    if not isinstance(target, dict) or set(target) != set(TARGET_FEATURE_FIELDS):
        raise ValueError(f"target_features keys must be exactly {TARGET_FEATURE_FIELDS}")
    target_values = {
        name: _strict_float(target[name], f"target_features.{name}")
        for name in TARGET_FEATURE_FIELDS
    }
    if target_values["Lp_nH"] <= 0.0 or target_values["Ls_nH"] <= 0.0:
        raise ValueError("target Lp/Ls must be positive")
    if not 0.0 < target_values["K_abs"] <= 0.8:
        raise ValueError("target |K| must be inside the current frozen support (0, 0.8]")
    if target_values["Q_scalar"] != float(q_target):
        raise ValueError("target Q_scalar must equal q_target exactly")

    raw_geometry = row.get("geometry_um")
    if not isinstance(raw_geometry, dict) or set(raw_geometry) != set(
        REQUEST_GEOMETRY_FIELDS
    ):
        raise ValueError(
            f"geometry_um keys must be exactly {REQUEST_GEOMETRY_FIELDS}"
        )
    geometry_um = {
        name: _strict_float(raw_geometry[name], f"geometry_um.{name}")
        for name in REQUEST_GEOMETRY_FIELDS
    }
    geometry_sha256 = _normalized_sha256_value(
        row.get("geometry_sha256"), "geometry_sha256"
    )
    actual_geometry_sha256 = _geometry_sha256(geometry_um)
    if geometry_sha256 != actual_geometry_sha256:
        raise ValueError(
            "geometry SHA-256 mismatch: "
            f"expected={geometry_sha256}, actual={actual_geometry_sha256}"
        )

    adapter_values = {
        field: (
            geometry_um["line_width_um"]
            if field in {"primary_width_um", "secondary_width_um"}
            else geometry_um[field]
        )
        for field in EXPECTED_ADAPTER_FIELDS
    }
    adapter_vector = tuple(adapter_values[field] for field in adapter.field_order())
    geometry = adapter.from_vector(adapter_vector).with_shared_line_width(
        geometry_um["line_width_um"]
    )
    bounds_errors = adapter.search_space.validate(geometry)
    geometry_errors = geometry.validate()
    if bounds_errors or geometry_errors:
        raise ValueError(
            "geometry failed bounds/structural validation: "
            + "; ".join([*bounds_errors, *geometry_errors])
        )
    if not np.allclose(
        adapter.to_vector(geometry),
        np.asarray(adapter_vector, dtype=float),
        rtol=0.0,
        atol=1.0e-12,
    ):
        raise ValueError("adapter round-trip changed the mapped geometry")
    fixed_target = (
        target_values["Lp_nH"],
        target_values["Ls_nH"],
        target_values["K_abs"],
    )
    return (
        PreparedCandidate(
            candidate_id=candidate_id,
            q_target=q_target,
            geometry_sha256=geometry_sha256,
            geometry_um=geometry_um,
            adapter_vector=adapter_vector,
            geometry=geometry,
        ),
        fixed_target,
    )


def run_physical_backend(
    preflight: PhysicalBackendPreflight,
    *,
    out_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute all candidates and return the q_sweep payload plus audit records."""

    evaluator = TransformerEmxEvaluator(
        run_config=preflight.run_config,
        root_dir=out_dir,
    )
    geometries = [item.geometry for item in preflight.candidates]
    results = list(evaluator.evaluate_geometry_batch(geometries, run_emx=True))
    if len(results) != len(preflight.candidates):
        raise PhysicalBackendContractError(
            "evaluator must return exactly eleven ordered results; "
            f"got {len(results)}"
        )

    physical_rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for candidate, result in zip(preflight.candidates, results):
        record: dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "q_target": candidate.q_target,
            "geometry_sha256": candidate.geometry_sha256,
            "status": "FAIL",
            "evaluation_cache_key": str(getattr(result, "cache_key", "")),
            "evaluation_work_dir": str(getattr(result, "work_dir", "")),
            "error": None,
        }
        try:
            if not callable(getattr(result, "ok", None)) or not result.ok():
                raise RuntimeError(
                    str(getattr(result, "error", None) or "evaluator returned a failed result")
                )
            _require_result_geometry(preflight.adapter, candidate, result)
            row, audit = _physical_row_from_result(
                preflight,
                candidate,
                result,
                out_dir,
            )
            physical_rows.append(row)
            record.update(audit)
            record["status"] = "PASS"
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            failures.append(f"{candidate.candidate_id}: {record['error']}")
        records.append(record)

    _verify_bound_inputs_unchanged(preflight)
    if failures:
        raise PhysicalBackendExecutionError(
            "one or more candidate evaluations failed: " + " | ".join(failures[:11]),
            candidate_records=records,
        )
    payload = {
        "schema": PHYSICAL_RESULTS_SCHEMA,
        "label_source": "FRESH_REAL_EMX",
        "request_sha256": preflight.request_sha256,
        "config_sha256": preflight.config_sha256,
        "process_sha256": preflight.process_sha256,
        "model_identity": preflight.request.get("model_identity") or {},
        "results": physical_rows,
    }
    return payload, records


def _validate_terminal_success_payload(
    preflight: PhysicalBackendPreflight,
    payload: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    expected = {
        item.candidate_id: (item.q_target, item.geometry_sha256)
        for item in preflight.candidates
    }
    rows = payload.get("results")
    if (
        payload.get("schema") != PHYSICAL_RESULTS_SCHEMA
        or payload.get("label_source") != "FRESH_REAL_EMX"
        or payload.get("request_sha256") != preflight.request_sha256
        or payload.get("config_sha256") != preflight.config_sha256
        or payload.get("process_sha256") != preflight.process_sha256
        or not isinstance(rows, list)
        or len(rows) != len(expected)
    ):
        raise PhysicalBackendContractError(
            "terminal physical-results payload does not match the hash-bound batch"
        )
    seen_rows: dict[str, tuple[int, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PhysicalBackendContractError("physical result row must be an object")
        candidate_id = str(row.get("candidate_id") or "")
        q_target = row.get("q_target")
        geometry_sha256 = str(row.get("geometry_sha256") or "")
        artifacts = row.get("artifacts")
        if (
            candidate_id not in expected
            or isinstance(q_target, bool)
            or not isinstance(q_target, int)
            or (q_target, geometry_sha256) != expected[candidate_id]
            or not isinstance(row.get("features_15ghz"), dict)
            or not isinstance(artifacts, dict)
            or not all(artifacts.get(name) for name in ("gds", "s4p", "evidence_manifest"))
        ):
            raise PhysicalBackendContractError(
                f"terminal physical result is incomplete or identity-mismatched: {candidate_id!r}"
            )
        seen_rows[candidate_id] = (q_target, geometry_sha256)
    if seen_rows != expected:
        raise PhysicalBackendContractError(
            "terminal physical results do not contain exactly all eleven candidates"
        )

    seen_records: dict[str, tuple[int, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise PhysicalBackendContractError("candidate receipt record must be an object")
        candidate_id = str(record.get("candidate_id") or "")
        q_target = record.get("q_target")
        geometry_sha256 = str(record.get("geometry_sha256") or "")
        artifacts = record.get("artifacts")
        if (
            candidate_id not in expected
            or record.get("status") != "PASS"
            or isinstance(q_target, bool)
            or not isinstance(q_target, int)
            or (q_target, geometry_sha256) != expected[candidate_id]
            or not isinstance(artifacts, dict)
            or not all(
                artifacts.get(name)
                for name in (
                    "gds",
                    "s4p",
                    "evidence_manifest",
                    "geometry_json",
                    "layout_manifest",
                    "evaluation_summary",
                    "gds_sha256",
                    "s4p_sha256",
                    "evidence_manifest_sha256",
                    "geometry_json_sha256",
                    "layout_manifest_sha256",
                    "evaluation_summary_sha256",
                )
            )
            or not _SHA256_PATTERN.fullmatch(
                str(record.get("emx_command_sha256") or "")
            )
            or not isinstance(record.get("emx_command_argument_count"), int)
            or int(record["emx_command_argument_count"]) <= 0
        ):
            raise PhysicalBackendContractError(
                f"candidate terminal receipt is incomplete: {candidate_id!r}"
            )
        seen_records[candidate_id] = (q_target, geometry_sha256)
    if seen_records != expected:
        raise PhysicalBackendContractError(
            "candidate terminal receipts do not contain exactly all eleven candidates"
        )


def _physical_row_from_result(
    preflight: PhysicalBackendPreflight,
    candidate: PreparedCandidate,
    result: Any,
    out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    layout = getattr(result, "layout", None)
    if layout is None or getattr(result, "metrics", None) is None:
        raise RuntimeError("successful evaluator result lacks layout or metrics")
    command = getattr(result, "command", None)
    if (
        not isinstance(command, (list, tuple))
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise RuntimeError("successful evaluator result lacks a concrete EMX command")
    emx_command_sha256 = hashlib.sha256(
        json.dumps(
            list(command), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    work_dir = Path(getattr(result, "work_dir", "")).resolve(strict=True)
    if not work_dir.is_relative_to(out_dir) or not work_dir.is_dir():
        raise RuntimeError("evaluator work directory escaped the backend output root")
    gds_path = _contained_artifact(out_dir, getattr(layout, "gds_path", None), ".gds")
    s4p_path = _contained_artifact(
        out_dir, getattr(result, "touchstone_path", None), ".s4p"
    )
    layout_manifest_path = _contained_artifact(
        out_dir, getattr(layout, "manifest_path", None), ".json"
    )
    geometry_json_path = _contained_artifact(
        out_dir, layout_manifest_path.parent / "geometry.json", ".json"
    )
    evaluation_summary_path = _contained_artifact(
        out_dir, work_dir / "summary.json", ".json"
    )
    if _load_json_object(geometry_json_path) != candidate.geometry.as_dict():
        raise RuntimeError(
            "exporter geometry.json does not match the request-bound TransformerSpec"
        )
    features = _extract_s4p_features_15ghz(s4p_path, candidate.candidate_id)
    feature_record = {
        name: float(features[name])
        for name in ("Lp_nH", "Ls_nH", "Qp", "Qs", "K_abs")
    }
    artifact_records = {
        "gds": {"path": _relative_path(gds_path, out_dir), "sha256": _sha256(gds_path)},
        "s4p": {"path": _relative_path(s4p_path, out_dir), "sha256": _sha256(s4p_path)},
    }
    evidence_dir = out_dir / "candidate_evidence" / candidate.candidate_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    evidence_path = evidence_dir / "evidence_manifest.json"
    evidence_payload = {
        "schema": PHYSICAL_EVIDENCE_SCHEMA,
        "candidate_id": candidate.candidate_id,
        "q_target": candidate.q_target,
        "geometry_sha256": candidate.geometry_sha256,
        "artifacts": artifact_records,
        "top_cell": str(getattr(layout, "top_cell", "")),
        "structured_exporter": {
            "id": STRUCTURED_EXPORTER_ID,
            "source_sha256": preflight.source_sha256["structured_exporter"],
        },
        "process_sha256": preflight.process_sha256,
        "config_sha256": preflight.config_sha256,
        "backend_sha256": preflight.source_sha256["backend"],
        "emx_wrapper_sha256": preflight.source_sha256["emx_wrapper"],
        "ports": list(EXPECTED_PORTS),
        "differential_pairs": [list(pair) for pair in EXPECTED_DIFFERENTIAL_PAIRS],
        "ground_mode": EXPECTED_GROUND_MODE,
        "frequency_grid": {
            "start_hz": EXPECTED_FREQUENCY_START_HZ,
            "stop_hz": EXPECTED_FREQUENCY_STOP_HZ,
            "step_hz": EXPECTED_FREQUENCY_STEP_HZ,
            "point_count": EXPECTED_FREQUENCY_POINT_COUNT,
        },
        "feature_extraction_hz": EXPECTED_FEATURE_EXTRACTION_HZ,
    }
    _write_json_exclusive(evidence_path, evidence_payload)
    evidence_validation = validate_physical_evidence_manifest(
        evidence_path,
        root=out_dir,
        expected_candidate_id=candidate.candidate_id,
        expected_q_target=candidate.q_target,
        expected_geometry_sha256=candidate.geometry_sha256,
    )
    artifacts = {
        "gds": _relative_path(gds_path, out_dir),
        "s4p": _relative_path(s4p_path, out_dir),
        "evidence_manifest": _relative_path(evidence_path, out_dir),
    }
    preview = getattr(layout, "preview_path", None)
    if preview is not None:
        try:
            preview_path = _contained_artifact(out_dir, preview, ".png")
        except Exception:
            preview_path = None
        if preview_path is not None:
            artifacts["preview"] = _relative_path(preview_path, out_dir)
    row = {
        "candidate_id": candidate.candidate_id,
        "q_target": candidate.q_target,
        "geometry_sha256": candidate.geometry_sha256,
        "features_15ghz": feature_record,
        "artifacts": artifacts,
    }
    audit = {
        "emx_command_sha256": emx_command_sha256,
        "emx_command_argument_count": len(command),
        "features_15ghz": features,
        "artifacts": {
            **artifacts,
            "gds_sha256": artifact_records["gds"]["sha256"],
            "s4p_sha256": artifact_records["s4p"]["sha256"],
            "evidence_manifest_sha256": evidence_validation[
                "evidence_manifest_sha256"
            ],
            "geometry_json": _relative_path(geometry_json_path, out_dir),
            "geometry_json_sha256": _sha256(geometry_json_path),
            "layout_manifest": _relative_path(layout_manifest_path, out_dir),
            "layout_manifest_sha256": _sha256(layout_manifest_path),
            "evaluation_summary": _relative_path(
                evaluation_summary_path, out_dir
            ),
            "evaluation_summary_sha256": _sha256(evaluation_summary_path),
        },
        "evidence_validation": evidence_validation,
    }
    return row, audit


def _require_result_geometry(
    adapter: TransformerOptimizationAdapter,
    candidate: PreparedCandidate,
    result: Any,
) -> None:
    result_geometry = getattr(result, "geometry", None)
    if result_geometry is None:
        raise RuntimeError("evaluator result lacks bound geometry")
    observed = adapter.to_vector(result_geometry)
    expected = np.asarray(candidate.adapter_vector, dtype=float)
    if observed.shape != expected.shape or not np.allclose(
        observed, expected, rtol=0.0, atol=1.0e-12
    ):
        raise RuntimeError("evaluator result geometry does not match request mapping")


def _verify_bound_inputs_unchanged(preflight: PhysicalBackendPreflight) -> None:
    expected = {
        "request": (preflight.request_path, preflight.request_sha256),
        "config": (preflight.config_path, preflight.config_sha256),
        "process": (preflight.process_path, preflight.process_sha256),
        **{
            name: (preflight.source_paths[name], digest)
            for name, digest in preflight.source_sha256.items()
        },
    }
    changed = [
        name
        for name, (path, digest) in expected.items()
        if not path.is_file() or _sha256(path) != digest
    ]
    if changed:
        raise PhysicalBackendContractError(
            "hash-bound inputs changed during execution: " + ", ".join(changed)
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started_utc = _utc_now()
    out_dir: Path | None = None
    preflight: PhysicalBackendPreflight | None = None
    candidate_records: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {
        "schema": BACKEND_RECEIPT_SCHEMA,
        "overall_status": "FAIL",
        "decision": "DO_NOT_USE_Q_SWEEP_PHYSICAL_BACKEND_OUTPUT",
        "requested_label_source": "FRESH_REAL_EMX",
        "evidence_source": "NO_COMPLETE_FRESH_REAL_EMX_BATCH",
        "started_utc": started_utc,
        "completed_utc": None,
        "request_json": str(Path(args.request_json).expanduser()),
        "config": str(Path(args.config).expanduser()),
        "provided_config_sha256": args.config_sha256,
        "provided_process_sha256": args.process_sha256,
        "candidate_count": 0,
        "pass_count": 0,
        "fail_count": 0,
        "preflight_checks": [],
        "candidates": candidate_records,
        "failure_type": None,
        "failure_message": None,
        "scientific_boundary": (
            "Only a complete candidate-bound fresh real-EMX batch is eligible. "
            "This backend does not establish Calibre DRC or independent HFSS evidence."
        ),
    }
    try:
        out_dir = _claim_output_dir(Path(args.out_dir), receipt)
        receipt["backend_receipt"] = str(out_dir / BACKEND_RECEIPT_FILENAME)
        preflight = preflight_physical_backend(
            request_json=args.request_json,
            config=args.config,
            config_sha256=args.config_sha256,
            process_sha256=args.process_sha256,
        )
        receipt.update(
            {
                "request_json": str(preflight.request_path),
                "request_sha256": preflight.request_sha256,
                "config": str(preflight.config_path),
                "config_sha256": preflight.config_sha256,
                "process_file": str(preflight.process_path),
                "process_sha256": preflight.process_sha256,
                "runtime_sources": {
                    name: {
                        "path": str(preflight.source_paths[name]),
                        "sha256": digest,
                    }
                    for name, digest in preflight.source_sha256.items()
                },
                "model_identity": preflight.request.get("model_identity") or {},
                "q_values": list(Q_SWEEP_VALUES),
                "candidate_count": len(preflight.candidates),
                "preflight_checks": list(preflight.checks),
                "geometry_mapping": {
                    "request_fields": list(REQUEST_GEOMETRY_FIELDS),
                    "adapter_fields": list(preflight.adapter.field_order()),
                    "shared_line_width": {
                        "source": "line_width_um",
                        "destinations": [
                            "primary_width_um",
                            "secondary_width_um",
                        ],
                    },
                },
            }
        )
        physical_payload, candidate_records = run_physical_backend(
            preflight,
            out_dir=out_dir,
        )
        _verify_bound_inputs_unchanged(preflight)
        _validate_terminal_success_payload(
            preflight,
            physical_payload,
            candidate_records,
        )
        physical_results_path = out_dir / "physical_results.json"
        _write_json_exclusive(physical_results_path, physical_payload)
        receipt.update(
            {
                "overall_status": "PASS",
                "decision": "FRESH_REAL_EMX_Q_SWEEP_READY",
                "evidence_source": "FRESH_REAL_EMX",
                "candidate_count": len(candidate_records),
                "pass_count": len(candidate_records),
                "fail_count": 0,
                "candidate_evidence_manifest_count": sum(
                    bool((item.get("artifacts") or {}).get("evidence_manifest"))
                    for item in candidate_records
                ),
                "candidates": candidate_records,
                "physical_results": str(physical_results_path),
                "physical_results_sha256": _sha256(physical_results_path),
            }
        )
        return_code = 0
    except Exception as exc:
        if isinstance(exc, PhysicalBackendExecutionError):
            candidate_records = [dict(item) for item in exc.candidate_records]
        if isinstance(exc, PhysicalBackendContractError) and exc.checks:
            receipt["preflight_checks"] = list(exc.checks)
        receipt["failure_type"] = type(exc).__name__
        receipt["failure_message"] = str(exc)
        if preflight is not None:
            receipt["candidate_count"] = len(preflight.candidates)
        if candidate_records:
            receipt["candidates"] = candidate_records
            receipt["pass_count"] = sum(
                item.get("status") == "PASS" for item in candidate_records
            )
            receipt["fail_count"] = sum(
                item.get("status") != "PASS" for item in candidate_records
            )
        elif preflight is not None:
            receipt["fail_count"] = len(preflight.candidates)
            receipt["candidates"] = [
                {
                    "candidate_id": item.candidate_id,
                    "q_target": item.q_target,
                    "geometry_sha256": item.geometry_sha256,
                    "status": "NOT_COMPLETED",
                }
                for item in preflight.candidates
            ]
        print(f"q-sweep physical backend FAIL: {exc}", file=sys.stderr)
        return_code = 1
    finally:
        receipt["completed_utc"] = _utc_now()
        if out_dir is not None:
            receipt_path = out_dir / BACKEND_RECEIPT_FILENAME
            try:
                _write_json_exclusive(receipt_path, receipt)
            except Exception as receipt_exc:
                print(
                    f"q-sweep physical backend could not write terminal receipt: {receipt_exc}",
                    file=sys.stderr,
                )
                return_code = 1
    return return_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all eleven exact-Q geometries through fresh real EMX."
    )
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--process-sha256", required=True)
    return parser.parse_args(argv)


def _claim_output_dir(raw_path: Path, receipt: dict[str, Any]) -> Path:
    path = raw_path.expanduser()
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise FileExistsError(f"output path is not a safe directory: {path}")
        if any(path.iterdir()):
            raise FileExistsError(
                f"refusing to reuse non-empty output directory: {path}"
            )
    else:
        path.mkdir(parents=True, exist_ok=False)
    path = path.resolve(strict=True)
    claim = {
        "schema": "rfic_q_sweep_physical_backend_claim.v1",
        "status": "STARTED_NOT_TERMINAL",
        "started_utc": receipt["started_utc"],
        "request_json": receipt["request_json"],
        "config": receipt["config"],
        "provided_config_sha256": receipt["provided_config_sha256"],
        "provided_process_sha256": receipt["provided_process_sha256"],
    }
    _write_json_exclusive(path / BACKEND_CLAIM_FILENAME, claim)
    return path


def _runtime_source_paths(checks: list[dict[str, Any]]) -> dict[str, Path]:
    candidates = {
        "backend": Path(__file__),
        "structured_exporter": Path(
            inspect.getsourcefile(export_transformer_layout) or ""
        ),
        "emx_wrapper": Path(
            inspect.getsourcefile(run_transformer_zeus_cadence_roundtrip_batch) or ""
        ),
    }
    result: dict[str, Path] = {}
    for name, path in candidates.items():
        if not str(path):
            _fail(checks, f"{name}_source_is_hashable", "source path is absent")
        result[name] = _verified_file(path, f"{name} source", checks)
    return result


def _contained_artifact(root: Path, value: Any, suffix: str) -> Path:
    if value is None:
        raise RuntimeError(f"missing {suffix} artifact")
    raw = Path(value)
    path = (raw if raw.is_absolute() else root / raw).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise RuntimeError(f"unsafe or missing {suffix} artifact: {value}")
    if path.suffix.lower() != suffix:
        raise RuntimeError(f"artifact must use {suffix}: {path}")
    return path


def _relative_path(path: Path, root: Path) -> str:
    return path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()


def _verified_file(
    raw_path: str | Path,
    label: str,
    checks: list[dict[str, Any]],
) -> Path:
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        _fail(checks, f"{label}_exists", f"{raw_path}: {exc}")
    _require(
        checks,
        f"{label}_is_file",
        path.is_file(),
        str(path),
    )
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise TypeError("request JSON must contain an object")
    return value


def _normalized_sha256(
    value: Any,
    label: str,
    checks: list[dict[str, Any]],
) -> str:
    try:
        normalized = _normalized_sha256_value(value, label)
    except ValueError as exc:
        _fail(checks, f"{label}_is_sha256", str(exc))
    _pass(checks, f"{label}_is_sha256", normalized)
    return normalized


def _normalized_sha256_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be exactly 64 hexadecimal characters")
    return value.lower()


def _strict_float(value: Any, label: str) -> float:
    if not _is_numeric(value):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return int(value)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require(
    checks: list[dict[str, Any]],
    name: str,
    condition: bool,
    detail: Any,
) -> None:
    if condition:
        _pass(checks, name, detail)
        return
    _fail(checks, name, detail)


def _pass(checks: list[dict[str, Any]], name: str, detail: Any) -> None:
    checks.append({"name": name, "status": "PASS", "detail": detail})


def _fail(checks: list[dict[str, Any]], name: str, detail: Any) -> None:
    checks.append({"name": name, "status": "FAIL", "detail": detail})
    raise PhysicalBackendContractError(
        f"contract check failed: {name}: {detail}",
        checks=checks,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":  # pragma: no cover - exercised through console entry
    raise SystemExit(main())


__all__ = [
    "BACKEND_RECEIPT_FILENAME",
    "BACKEND_RECEIPT_SCHEMA",
    "EXPECTED_ADAPTER_FIELDS",
    "PHYSICAL_RESULTS_SCHEMA",
    "PhysicalBackendContractError",
    "PhysicalBackendExecutionError",
    "PhysicalBackendPreflight",
    "PreparedCandidate",
    "REQUEST_GEOMETRY_FIELDS",
    "main",
    "preflight_physical_backend",
    "run_physical_backend",
]
