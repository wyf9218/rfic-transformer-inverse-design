"""Packaged, fail-closed structured-GDS backend for the exact Q sweep.

The runner exports all eleven request-bound model geometries without invoking
EMX.  Its PASS receipt is layout evidence only: no S4P, physical response,
Calibre DRC, HFSS correlation, or measurement is created or implied.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..execution.evaluator import TransformerEmxEvaluator
from ..layout.export import export_transformer_layout
from .layout_evidence import (
    LAYOUT_EVIDENCE_CLASS,
    LAYOUT_EVIDENCE_SCHEMA,
    STRUCTURED_EXPORTER_ID,
    validate_layout_evidence_manifest,
)
from .q_sweep import Q_SWEEP_VALUES
from .q_sweep_physical_backend import (
    PhysicalBackendContractError,
    PhysicalBackendExecutionError,
    PhysicalBackendPreflight,
    PreparedCandidate,
    preflight_physical_backend,
)


LAYOUT_REQUEST_SCHEMA = "rfic_q_sweep_layout_request.v1"
LAYOUT_RESULTS_SCHEMA = "rfic_q_sweep_layout_results.v1"
LAYOUT_RECEIPT_SCHEMA = "rfic_q_sweep_layout_backend_receipt.v1"
LAYOUT_RECEIPT_FILENAME = "layout_backend_receipt.json"
LAYOUT_CLAIM_FILENAME = "layout_backend_claim.json"
LAYOUT_RESULTS_FILENAME = "layout_results.json"
REQUIRED_LAYOUT_OUTPUTS = frozenset(
    {"GDS", "geometry_json", "layout_manifest", "evaluation_summary"}
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def preflight_layout_backend(
    *,
    request_json: str | Path,
    config: str | Path,
    config_sha256: str,
    process_sha256: str,
) -> PhysicalBackendPreflight:
    """Reuse the physical runner's exact hash/config/port/geometry preflight."""

    preflight = preflight_physical_backend(
        request_json=request_json,
        config=config,
        config_sha256=config_sha256,
        process_sha256=process_sha256,
        expected_request_schema=LAYOUT_REQUEST_SCHEMA,
        expected_label_source=LAYOUT_EVIDENCE_CLASS,
        required_outputs=REQUIRED_LAYOUT_OUTPUTS,
    )
    checks = list(preflight.checks)
    if preflight.request.get("emx_requested") is not False:
        checks.append(
            {
                "name": "layout_request_disables_emx",
                "status": "FAIL",
                "detail": preflight.request.get("emx_requested"),
            }
        )
        raise PhysicalBackendContractError(
            "layout request must explicitly set emx_requested=false",
            checks=checks,
        )
    checks.append(
        {
            "name": "layout_request_disables_emx",
            "status": "PASS",
            "detail": False,
        }
    )
    outputs = preflight.request.get("required_outputs")
    exact_outputs = (
        isinstance(outputs, list)
        and len(outputs) == len(REQUIRED_LAYOUT_OUTPUTS)
        and set(outputs) == set(REQUIRED_LAYOUT_OUTPUTS)
    )
    if not exact_outputs:
        checks.append(
            {
                "name": "layout_request_outputs_are_gds_only",
                "status": "FAIL",
                "detail": outputs,
            }
        )
        raise PhysicalBackendContractError(
            "layout request must contain only the structured-GDS output contract",
            checks=checks,
        )
    checks.append(
        {
            "name": "layout_request_outputs_are_gds_only",
            "status": "PASS",
            "detail": sorted(REQUIRED_LAYOUT_OUTPUTS),
        }
    )
    return replace(preflight, checks=tuple(checks))


def run_layout_backend(
    preflight: PhysicalBackendPreflight,
    *,
    out_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Path], dict[str, str]]:
    """Export all eleven GDS files with EMX explicitly disabled."""

    source_paths = _runtime_source_paths()
    source_sha256 = {name: _sha256(path) for name, path in source_paths.items()}
    evaluator = TransformerEmxEvaluator(
        run_config=preflight.run_config,
        root_dir=out_dir,
    )
    geometries = [item.geometry for item in preflight.candidates]
    cache_keys = [evaluator.cache_key(geometry) for geometry in geometries]
    if len(set(cache_keys)) != len(cache_keys):
        raise PhysicalBackendContractError(
            "layout evaluator cache keys collide across distinct request geometries"
        )
    results = list(evaluator.evaluate_geometry_batch(geometries, run_emx=False))
    if len(results) != len(preflight.candidates):
        raise PhysicalBackendContractError(
            "layout evaluator must return exactly eleven ordered results"
        )

    rows: list[dict[str, Any]] = []
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
            "emx_status": "NOT_RUN",
            "error": None,
        }
        try:
            if not callable(getattr(result, "ok", None)) or not result.ok():
                raise RuntimeError(
                    str(getattr(result, "error", None) or "layout export failed")
                )
            row, audit = _layout_row_from_result(
                preflight,
                candidate,
                result,
                out_dir,
                source_sha256,
            )
            rows.append(row)
            record.update(audit)
            record["status"] = "PASS"
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            failures.append(f"{candidate.candidate_id}: {record['error']}")
        records.append(record)

    _verify_inputs_unchanged(preflight, source_paths, source_sha256)
    touchstones = [
        path
        for path in out_dir.rglob("*")
        if path.is_file() and re.fullmatch(r"\.s\d+p", path.suffix.lower())
    ]
    if touchstones:
        failures.append("layout-only run unexpectedly produced Touchstone files")
    if failures:
        raise PhysicalBackendExecutionError(
            "one or more structured layout exports failed: " + " | ".join(failures[:11]),
            candidate_records=records,
        )
    rows.sort(key=lambda item: int(item["q_target"]))
    payload = {
        "schema": LAYOUT_RESULTS_SCHEMA,
        "evidence_source": LAYOUT_EVIDENCE_CLASS,
        "request_sha256": preflight.request_sha256,
        "config_sha256": preflight.config_sha256,
        "process_sha256": preflight.process_sha256,
        "model_identity": preflight.request.get("model_identity") or {},
        "emx_status": "NOT_RUN",
        "calibre_drc": "NOT_RUN",
        "hfss_correlation": "NOT_RUN",
        "results": rows,
    }
    return payload, records, source_paths, source_sha256


def _layout_row_from_result(
    preflight: PhysicalBackendPreflight,
    candidate: PreparedCandidate,
    result: Any,
    out_dir: Path,
    source_sha256: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    layout = getattr(result, "layout", None)
    if layout is None:
        raise RuntimeError("successful layout result has no structured export")
    if any(
        value is not None
        for value in (
            getattr(result, "command", None),
            getattr(result, "touchstone_path", None),
            getattr(result, "metrics", None),
            getattr(result, "objective", None),
            getattr(result, "single_ended_sparams", None),
            getattr(result, "differential_sparams", None),
            getattr(result, "differential_z", None),
        )
    ):
        raise RuntimeError("layout-only result contains unexpected EMX evidence")
    observed_geometry = preflight.adapter.to_vector(getattr(result, "geometry", None))
    if not np.allclose(
        observed_geometry,
        np.asarray(candidate.adapter_vector, dtype=float),
        rtol=0.0,
        atol=1.0e-12,
    ):
        raise RuntimeError("layout result geometry does not match request mapping")

    work_dir = Path(getattr(result, "work_dir", "")).resolve(strict=True)
    if not work_dir.is_relative_to(out_dir) or not work_dir.is_dir():
        raise RuntimeError("layout work directory escaped the output root")
    gds_path = _contained_artifact(out_dir, getattr(layout, "gds_path", None), ".gds")
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
        raise RuntimeError("geometry.json does not match the request-bound geometry")

    artifact_records = {
        "gds": _artifact_record(gds_path, out_dir),
        "geometry_json": _artifact_record(geometry_json_path, out_dir),
        "layout_manifest": _artifact_record(layout_manifest_path, out_dir),
        "evaluation_summary": _artifact_record(evaluation_summary_path, out_dir),
    }
    evidence_dir = out_dir / "candidate_layout_evidence" / candidate.candidate_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    evidence_path = evidence_dir / "evidence_manifest.json"
    evidence_payload = {
        "schema": LAYOUT_EVIDENCE_SCHEMA,
        "candidate_id": candidate.candidate_id,
        "q_target": candidate.q_target,
        "geometry_sha256": candidate.geometry_sha256,
        "artifacts": artifact_records,
        "top_cell": str(getattr(layout, "top_cell", "")),
        "structured_exporter": {
            "id": STRUCTURED_EXPORTER_ID,
            "source_sha256": source_sha256["structured_exporter"],
        },
        "config_sha256": preflight.config_sha256,
        "process_sha256": preflight.process_sha256,
        "layout_backend_sha256": source_sha256["layout_backend"],
        "evaluator_sha256": source_sha256["evaluator"],
        "emx_status": "NOT_RUN",
        "calibre_drc": "NOT_RUN",
        "hfss_correlation": "NOT_RUN",
    }
    _write_json_exclusive(evidence_path, evidence_payload)
    evidence = validate_layout_evidence_manifest(
        evidence_path,
        root=out_dir,
        expected_candidate_id=candidate.candidate_id,
        expected_q_target=candidate.q_target,
        expected_geometry_sha256=candidate.geometry_sha256,
    )
    artifacts = {
        "gds": artifact_records["gds"]["path"],
        "evidence_manifest": _relative_path(evidence_path, out_dir),
    }
    preview = getattr(layout, "preview_path", None)
    if preview is not None:
        try:
            preview_path = _contained_artifact(out_dir, preview, ".png")
            if not preview_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("preview is not a binary PNG")
        except Exception:
            preview_path = None
        if preview_path is not None:
            artifacts["preview"] = _relative_path(preview_path, out_dir)
    row = {
        "candidate_id": candidate.candidate_id,
        "q_target": candidate.q_target,
        "geometry_sha256": candidate.geometry_sha256,
        "artifacts": artifacts,
    }
    audit_artifacts = {
        **artifacts,
        "gds_sha256": artifact_records["gds"]["sha256"],
        "evidence_manifest_sha256": evidence["evidence_manifest_sha256"],
        "geometry_json": artifact_records["geometry_json"]["path"],
        "geometry_json_sha256": artifact_records["geometry_json"]["sha256"],
        "layout_manifest": artifact_records["layout_manifest"]["path"],
        "layout_manifest_sha256": artifact_records["layout_manifest"]["sha256"],
        "evaluation_summary": artifact_records["evaluation_summary"]["path"],
        "evaluation_summary_sha256": artifact_records["evaluation_summary"]["sha256"],
    }
    return row, {
        "emx_status": "NOT_RUN",
        "artifacts": audit_artifacts,
        "evidence_validation": evidence,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started_utc = _utc_now()
    out_dir: Path | None = None
    preflight: PhysicalBackendPreflight | None = None
    records: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {
        "schema": LAYOUT_RECEIPT_SCHEMA,
        "overall_status": "FAIL",
        "decision": "DO_NOT_USE_INCOMPLETE_LAYOUT_BATCH",
        "requested_label_source": LAYOUT_EVIDENCE_CLASS,
        "evidence_source": "NO_COMPLETE_STRUCTURED_GDS_BATCH",
        "started_utc": started_utc,
        "completed_utc": None,
        "request_json": str(Path(args.request_json).expanduser()),
        "config": str(Path(args.config).expanduser()),
        "provided_config_sha256": args.config_sha256,
        "provided_process_sha256": args.process_sha256,
        "candidate_count": 0,
        "pass_count": 0,
        "fail_count": 0,
        "q_values": list(Q_SWEEP_VALUES),
        "preflight_checks": [],
        "candidates": records,
        "emx_status": "NOT_RUN",
        "s4p_generated_count": 0,
        "calibre_drc": "NOT_RUN",
        "hfss_correlation": "NOT_RUN",
        "failure_type": None,
        "failure_message": None,
        "scientific_boundary": (
            "A PASS proves request-bound structured binary GDS export only. "
            "Proxy errors remain proxy; EMX, S4P, DRC, HFSS, and measurement are NOT_RUN."
        ),
    }
    try:
        out_dir = _claim_output_dir(Path(args.out_dir), receipt)
        receipt["layout_backend_receipt"] = str(out_dir / LAYOUT_RECEIPT_FILENAME)
        preflight = preflight_layout_backend(
            request_json=args.request_json,
            config=args.config,
            config_sha256=args.config_sha256,
            process_sha256=args.process_sha256,
        )
        payload, records, source_paths, source_sha256 = run_layout_backend(
            preflight,
            out_dir=out_dir,
        )
        _validate_terminal_payload(preflight, payload, records)
        results_path = out_dir / LAYOUT_RESULTS_FILENAME
        _write_json_exclusive(results_path, payload)
        receipt.update(
            {
                "overall_status": "PASS",
                "decision": "STRUCTURED_GDS_Q_SWEEP_READY",
                "evidence_source": LAYOUT_EVIDENCE_CLASS,
                "request_json": str(preflight.request_path),
                "request_sha256": preflight.request_sha256,
                "config": str(preflight.config_path),
                "config_sha256": preflight.config_sha256,
                "process_file": str(preflight.process_path),
                "process_sha256": preflight.process_sha256,
                "model_identity": preflight.request.get("model_identity") or {},
                "preflight_checks": list(preflight.checks),
                "runtime_sources": {
                    name: {"path": str(source_paths[name]), "sha256": digest}
                    for name, digest in source_sha256.items()
                },
                "candidate_count": len(records),
                "pass_count": len(records),
                "fail_count": 0,
                "candidate_evidence_manifest_count": len(records),
                "candidates": records,
                "layout_results": str(results_path),
                "layout_results_sha256": _sha256(results_path),
            }
        )
        return_code = 0
    except Exception as exc:
        if isinstance(exc, PhysicalBackendExecutionError):
            records = [dict(item) for item in exc.candidate_records]
        if isinstance(exc, PhysicalBackendContractError) and exc.checks:
            receipt["preflight_checks"] = list(exc.checks)
        receipt["failure_type"] = type(exc).__name__
        receipt["failure_message"] = str(exc)
        if preflight is not None:
            receipt["candidate_count"] = len(preflight.candidates)
        if records:
            receipt["candidates"] = records
            receipt["pass_count"] = sum(item.get("status") == "PASS" for item in records)
            receipt["fail_count"] = sum(item.get("status") != "PASS" for item in records)
        print(f"q-sweep layout backend FAIL: {exc}", file=sys.stderr)
        return_code = 1
    finally:
        receipt["completed_utc"] = _utc_now()
        if out_dir is not None:
            try:
                _write_json_exclusive(out_dir / LAYOUT_RECEIPT_FILENAME, receipt)
            except Exception as receipt_exc:
                print(f"could not write layout receipt: {receipt_exc}", file=sys.stderr)
                return_code = 1
    return return_code


def _validate_terminal_payload(
    preflight: PhysicalBackendPreflight,
    payload: dict[str, Any],
    records: Sequence[dict[str, Any]],
) -> None:
    expected = {
        item.candidate_id: (item.q_target, item.geometry_sha256)
        for item in preflight.candidates
    }
    rows = payload.get("results")
    if (
        payload.get("schema") != LAYOUT_RESULTS_SCHEMA
        or payload.get("evidence_source") != LAYOUT_EVIDENCE_CLASS
        or payload.get("emx_status") != "NOT_RUN"
        or not isinstance(rows, list)
        or len(rows) != len(expected)
        or len(records) != len(expected)
    ):
        raise PhysicalBackendContractError("terminal layout payload is incomplete")
    row_ids = {
        str(row.get("candidate_id")): (
            row.get("q_target"),
            str(row.get("geometry_sha256") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    }
    record_ids = {
        str(record.get("candidate_id")): (
            record.get("q_target"),
            str(record.get("geometry_sha256") or ""),
        )
        for record in records
        if isinstance(record, dict)
        and record.get("status") == "PASS"
        and record.get("emx_status") == "NOT_RUN"
    }
    if row_ids != expected or record_ids != expected:
        raise PhysicalBackendContractError("layout candidates are not exact 11/11")


def _runtime_source_paths() -> dict[str, Path]:
    values = {
        "layout_backend": Path(__file__),
        "structured_exporter": Path(inspect.getsourcefile(export_transformer_layout) or ""),
        "evaluator": Path(inspect.getsourcefile(TransformerEmxEvaluator) or ""),
        "request_mapping_preflight": Path(
            inspect.getsourcefile(preflight_physical_backend) or ""
        ),
        "layout_checks": Path(__file__).resolve().parents[1] / "layout" / "checks.py",
    }
    result: dict[str, Path] = {}
    for name, path in values.items():
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PhysicalBackendContractError(f"runtime source {name} is missing: {exc}") from exc
        if not resolved.is_file():
            raise PhysicalBackendContractError(f"runtime source {name} is not a file")
        result[name] = resolved
    return result


def _verify_inputs_unchanged(
    preflight: PhysicalBackendPreflight,
    source_paths: dict[str, Path],
    source_sha256: dict[str, str],
) -> None:
    expected = {
        "request": (preflight.request_path, preflight.request_sha256),
        "config": (preflight.config_path, preflight.config_sha256),
        "process": (preflight.process_path, preflight.process_sha256),
        **{
            name: (source_paths[name], digest)
            for name, digest in source_sha256.items()
        },
    }
    changed = [
        name
        for name, (path, digest) in expected.items()
        if not path.is_file() or _sha256(path) != digest
    ]
    if changed:
        raise PhysicalBackendContractError(
            "hash-bound layout inputs changed during export: " + ", ".join(changed)
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all eleven exact-Q model geometries to GDS without EMX."
    )
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--process-sha256", required=True)
    return parser.parse_args(argv)


def _claim_output_dir(path: Path, receipt: dict[str, Any]) -> Path:
    path = path.expanduser()
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise FileExistsError(f"refusing unsafe or non-empty output directory: {path}")
    else:
        path.mkdir(parents=True, exist_ok=False)
    path = path.resolve(strict=True)
    _write_json_exclusive(
        path / LAYOUT_CLAIM_FILENAME,
        {
            "schema": "rfic_q_sweep_layout_backend_claim.v1",
            "status": "STARTED_NOT_TERMINAL",
            "started_utc": receipt["started_utc"],
            "request_json": receipt["request_json"],
            "config": receipt["config"],
            "provided_config_sha256": receipt["provided_config_sha256"],
            "provided_process_sha256": receipt["provided_process_sha256"],
            "emx_status": "NOT_RUN",
        },
    )
    return path


def _contained_artifact(root: Path, value: Any, suffix: str) -> Path:
    if value is None:
        raise RuntimeError(f"missing {suffix} artifact")
    raw = Path(value)
    path = (raw if raw.is_absolute() else root / raw).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != suffix:
        raise RuntimeError(f"unsafe or missing {suffix} artifact: {value}")
    return path


def _artifact_record(path: Path, root: Path) -> dict[str, str]:
    return {"path": _relative_path(path, root), "sha256": _sha256(path)}


def _relative_path(path: Path, root: Path) -> str:
    return str(path.resolve(strict=True).relative_to(root.resolve(strict=True)))


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LAYOUT_RECEIPT_SCHEMA",
    "LAYOUT_RESULTS_SCHEMA",
    "LAYOUT_REQUEST_SCHEMA",
    "main",
    "preflight_layout_backend",
    "run_layout_backend",
]
