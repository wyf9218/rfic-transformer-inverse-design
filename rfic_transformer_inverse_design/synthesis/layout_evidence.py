"""Fail-closed evidence validation for structured GDS-only Q-sweep exports.

This evidence class proves that a request-bound model geometry was exported to
a parseable non-empty binary GDS through the tracked structured exporter.  It
explicitly does not claim S-parameters, EMX, Calibre DRC, HFSS, or measurement.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


LAYOUT_EVIDENCE_SCHEMA = "rfic_q_sweep_candidate_layout_evidence.v1"
LAYOUT_EVIDENCE_VALIDATION_SCHEMA = (
    "rfic_q_sweep_candidate_layout_evidence_validation.v1"
)
LAYOUT_EVIDENCE_CLASS = "STRUCTURED_GDS_EXPORT_ONLY"
STRUCTURED_EXPORTER_ID = (
    "rfic_transformer_inverse_design.layout.export.export_transformer_layout"
)

_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_TOP_LEVEL_KEYS = {
    "schema",
    "candidate_id",
    "q_target",
    "geometry_sha256",
    "artifacts",
    "top_cell",
    "structured_exporter",
    "config_sha256",
    "process_sha256",
    "layout_backend_sha256",
    "evaluator_sha256",
    "emx_status",
    "calibre_drc",
    "hfss_correlation",
}
_ARTIFACT_KEYS = {
    "gds",
    "geometry_json",
    "layout_manifest",
    "evaluation_summary",
}
_ARTIFACT_RECORD_KEYS = {"path", "sha256"}
_EXPORTER_KEYS = {"id", "source_sha256"}


class LayoutEvidenceValidationError(ValueError):
    """A structured-layout evidence record is incomplete or inconsistent."""


def validate_layout_evidence_manifest(
    manifest_path: str | Path,
    *,
    root: str | Path,
    expected_candidate_id: str,
    expected_q_target: int,
    expected_geometry_sha256: str,
) -> dict[str, Any]:
    """Validate one candidate's GDS-only evidence and return bound metadata."""

    root_path = Path(root).expanduser().resolve(strict=True)
    if not root_path.is_dir():
        raise LayoutEvidenceValidationError("layout evidence root is not a directory")
    manifest = _contained_file(root_path, manifest_path, "layout evidence manifest")
    payload = _load_json_object(manifest)
    _require_exact_keys(payload, _TOP_LEVEL_KEYS, "layout evidence manifest")

    if payload["schema"] != LAYOUT_EVIDENCE_SCHEMA:
        raise LayoutEvidenceValidationError("unsupported layout evidence schema")
    candidate_id = _required_string(payload["candidate_id"], "candidate_id")
    if candidate_id != expected_candidate_id:
        raise LayoutEvidenceValidationError("candidate_id mismatch")
    q_target = _required_int(payload["q_target"], "q_target")
    if q_target != expected_q_target or q_target not in range(10, 21):
        raise LayoutEvidenceValidationError("exact Q target mismatch")
    geometry_sha256 = _normalized_sha256(
        payload["geometry_sha256"], "geometry_sha256"
    )
    if geometry_sha256 != _normalized_sha256(
        expected_geometry_sha256, "expected_geometry_sha256"
    ):
        raise LayoutEvidenceValidationError("geometry SHA-256 mismatch")

    artifacts = _required_object(payload["artifacts"], "artifacts")
    _require_exact_keys(artifacts, _ARTIFACT_KEYS, "artifacts")
    artifact_records: dict[str, dict[str, str]] = {}
    suffixes = {
        "gds": ".gds",
        "geometry_json": ".json",
        "layout_manifest": ".json",
        "evaluation_summary": ".json",
    }
    for name, suffix in suffixes.items():
        record = _required_object(artifacts[name], f"artifacts.{name}")
        _require_exact_keys(record, _ARTIFACT_RECORD_KEYS, f"artifacts.{name}")
        path = _contained_file(root_path, record["path"], f"artifacts.{name}")
        if path.suffix.lower() != suffix:
            raise LayoutEvidenceValidationError(
                f"artifacts.{name} must use the {suffix} suffix"
            )
        digest = _normalized_sha256(record["sha256"], f"artifacts.{name}.sha256")
        if _sha256(path) != digest:
            raise LayoutEvidenceValidationError(f"artifacts.{name} SHA-256 mismatch")
        artifact_records[name] = {"path": str(path), "sha256": digest}
    if len({record["path"] for record in artifact_records.values()}) != len(
        artifact_records
    ):
        raise LayoutEvidenceValidationError("layout evidence artifact paths overlap")

    provenance = {
        name: _normalized_sha256(payload[name], name)
        for name in (
            "config_sha256",
            "process_sha256",
            "layout_backend_sha256",
            "evaluator_sha256",
        )
    }
    top_cell = _required_string(payload["top_cell"], "top_cell")
    gds_path = Path(artifact_records["gds"]["path"])
    layout_manifest_path = Path(artifact_records["layout_manifest"]["path"])
    _validate_layout_manifest(
        layout_manifest_path,
        gds_path=gds_path,
        top_cell=top_cell,
        process_sha256=provenance["process_sha256"],
    )
    _validate_gds(gds_path, top_cell, candidate_id)
    exporter = _required_object(payload["structured_exporter"], "structured_exporter")
    _require_exact_keys(exporter, _EXPORTER_KEYS, "structured_exporter")
    if exporter["id"] != STRUCTURED_EXPORTER_ID:
        raise LayoutEvidenceValidationError("unexpected structured exporter identity")
    exporter_sha256 = _normalized_sha256(
        exporter["source_sha256"], "structured_exporter.source_sha256"
    )

    if payload["emx_status"] != "NOT_RUN":
        raise LayoutEvidenceValidationError("GDS-only evidence must declare EMX NOT_RUN")
    if payload["calibre_drc"] != "NOT_RUN":
        raise LayoutEvidenceValidationError("GDS-only evidence must declare DRC NOT_RUN")
    if payload["hfss_correlation"] != "NOT_RUN":
        raise LayoutEvidenceValidationError("GDS-only evidence must declare HFSS NOT_RUN")

    return {
        "schema": LAYOUT_EVIDENCE_VALIDATION_SCHEMA,
        "evidence_class": LAYOUT_EVIDENCE_CLASS,
        "candidate_id": candidate_id,
        "q_target": q_target,
        "geometry_sha256": geometry_sha256,
        "top_cell": top_cell,
        "artifacts": artifact_records,
        "structured_exporter": {
            "id": STRUCTURED_EXPORTER_ID,
            "source_sha256": exporter_sha256,
        },
        **provenance,
        "emx_status": "NOT_RUN",
        "calibre_drc": "NOT_RUN",
        "hfss_correlation": "NOT_RUN",
        "evidence_manifest": str(manifest),
        "evidence_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }


def _validate_gds(path: Path, top_cell: str, candidate_id: str) -> None:
    import gdstk

    try:
        library = gdstk.read_gds(str(path))
    except Exception as exc:
        raise LayoutEvidenceValidationError(
            f"invalid GDS for {candidate_id}: {exc}"
        ) from exc
    cells = {cell.name: cell for cell in library.cells}
    if top_cell not in cells:
        raise LayoutEvidenceValidationError(f"GDS top cell mismatch for {candidate_id}")
    root_cells = {cell.name for cell in library.top_level()}
    if root_cells != {top_cell}:
        raise LayoutEvidenceValidationError(
            f"GDS declared top cell is not the unique root for {candidate_id}"
        )
    declared_top = cells[top_cell]
    drawable_count = (
        len(declared_top.polygons)
        + len(declared_top.paths)
        + len(declared_top.references)
    )
    if drawable_count <= 0:
        raise LayoutEvidenceValidationError(
            f"GDS declared top cell is empty for {candidate_id}"
        )


def _validate_layout_manifest(
    path: Path,
    *,
    gds_path: Path,
    top_cell: str,
    process_sha256: str,
) -> None:
    manifest = _load_json_object(path)
    if manifest.get("top_cell") != top_cell:
        raise LayoutEvidenceValidationError("layout manifest top_cell mismatch")
    raw_layout_path = manifest.get("layout_path")
    if not isinstance(raw_layout_path, str) or not raw_layout_path:
        raise LayoutEvidenceValidationError("layout manifest layout_path is missing")
    try:
        manifest_gds = (
            Path(raw_layout_path)
            if Path(raw_layout_path).is_absolute()
            else path.parent / raw_layout_path
        ).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LayoutEvidenceValidationError(
            f"layout manifest GDS is missing: {exc}"
        ) from exc
    if manifest_gds != gds_path.resolve(strict=True):
        raise LayoutEvidenceValidationError("layout manifest points to a different GDS")

    ports = manifest.get("ports")
    if not isinstance(ports, list):
        raise LayoutEvidenceValidationError("layout manifest ports are missing")
    port_names = [
        item.get("name") if isinstance(item, dict) else None for item in ports
    ]
    if port_names != ["P001", "P002", "P003", "P004"]:
        raise LayoutEvidenceValidationError(
            "layout manifest ports must be ordered P001 through P004"
        )

    layer_order = manifest.get("layer_draw_order")
    if (
        not isinstance(layer_order, list)
        or not layer_order
        or any(isinstance(value, bool) or not isinstance(value, int) for value in layer_order)
    ):
        raise LayoutEvidenceValidationError(
            "layout manifest layer_draw_order is missing or invalid"
        )
    process_summary = manifest.get("process_layer_summary")
    if (
        not isinstance(process_summary, dict)
        or process_summary.get("schema")
        != "rfic_transformer_process_layer_summary.v1"
        or not isinstance(process_summary.get("records"), dict)
        or not process_summary["records"]
    ):
        raise LayoutEvidenceValidationError(
            "layout manifest process layer summary is incomplete"
        )
    process_value = process_summary.get("process_file")
    if not isinstance(process_value, str) or not process_value:
        raise LayoutEvidenceValidationError("layout manifest process file is missing")
    try:
        process_path = Path(process_value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LayoutEvidenceValidationError(
            f"layout manifest process file is missing: {exc}"
        ) from exc
    if not process_path.is_file() or _sha256(process_path) != process_sha256:
        raise LayoutEvidenceValidationError(
            "layout manifest process file SHA-256 mismatch"
        )


def _contained_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise LayoutEvidenceValidationError(f"{label} path is missing")
    raw = Path(value)
    try:
        path = (raw if raw.is_absolute() else root / raw).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LayoutEvidenceValidationError(f"{label} is missing: {exc}") from exc
    if not path.is_relative_to(root) or not path.is_file():
        raise LayoutEvidenceValidationError(f"{label} escaped the output root")
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutEvidenceValidationError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LayoutEvidenceValidationError(f"JSON object required: {path}")
    return value


def _required_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LayoutEvidenceValidationError(f"{label} must be an object")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise LayoutEvidenceValidationError(f"{label} must be a non-empty string")
    return value


def _required_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LayoutEvidenceValidationError(f"{label} must be an integer")
    return value


def _normalized_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise LayoutEvidenceValidationError(f"{label} must be a complete SHA-256")
    return value.lower()


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LayoutEvidenceValidationError(
            f"{label} keys mismatch: expected={sorted(expected)}, actual={sorted(value)}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "LAYOUT_EVIDENCE_CLASS",
    "LAYOUT_EVIDENCE_SCHEMA",
    "LAYOUT_EVIDENCE_VALIDATION_SCHEMA",
    "LayoutEvidenceValidationError",
    "STRUCTURED_EXPORTER_ID",
    "validate_layout_evidence_manifest",
]
