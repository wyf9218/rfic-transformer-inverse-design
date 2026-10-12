"""Fail-closed validation for one exact-Q candidate's physical evidence.

This module validates artifact identity and the frozen four-port/frequency
contract. It does not run an exporter, EMX, Calibre, or HFSS, and a passing
record is not evidence that either DRC or independent HFSS correlation passed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from ..sim.touchstone import load_touchstone


PHYSICAL_EVIDENCE_SCHEMA = "rfic_q_sweep_candidate_physical_evidence.v1"
PHYSICAL_EVIDENCE_VALIDATION_SCHEMA = (
    "rfic_q_sweep_candidate_physical_evidence_validation.v1"
)
STRUCTURED_EXPORTER_ID = (
    "rfic_transformer_inverse_design.layout.export.export_transformer_layout"
)
EXPECTED_PORTS = ("P001", "P002", "P003", "P004")
EXPECTED_DIFFERENTIAL_PAIRS = (("P001", "P002"), ("P003", "P004"))
EXPECTED_GROUND_MODE = "single_ended_shield_grounded"
EXPECTED_FREQUENCY_START_HZ = 5.0e9
EXPECTED_FREQUENCY_STOP_HZ = 60.0e9
EXPECTED_FREQUENCY_STEP_HZ = 0.5e9
EXPECTED_FREQUENCY_POINT_COUNT = 111
EXPECTED_FEATURE_EXTRACTION_HZ = 15.0e9
FREQUENCY_TOLERANCE_HZ = 1.0

_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_TOP_LEVEL_KEYS = {
    "schema",
    "candidate_id",
    "q_target",
    "geometry_sha256",
    "artifacts",
    "top_cell",
    "structured_exporter",
    "process_sha256",
    "config_sha256",
    "backend_sha256",
    "emx_wrapper_sha256",
    "ports",
    "differential_pairs",
    "ground_mode",
    "frequency_grid",
    "feature_extraction_hz",
}
_ARTIFACT_KEYS = {"gds", "s4p"}
_ARTIFACT_RECORD_KEYS = {"path", "sha256"}
_EXPORTER_KEYS = {"id", "source_sha256"}
_FREQUENCY_GRID_KEYS = {"start_hz", "stop_hz", "step_hz", "point_count"}
_PROVENANCE_SHA_FIELDS = (
    "process_sha256",
    "config_sha256",
    "backend_sha256",
    "emx_wrapper_sha256",
)


class PhysicalEvidenceValidationError(ValueError):
    """Raised when candidate physical evidence is incomplete or inconsistent."""


def validate_physical_evidence_manifest(
    manifest_path: str | Path,
    *,
    root: str | Path,
    expected_candidate_id: str,
    expected_q_target: int,
    expected_geometry_sha256: str,
) -> dict[str, Any]:
    """Validate one candidate manifest and return JSON-serializable metadata.

    The v1 manifest binds candidate identity, GDS/S4P paths and hashes,
    structured-exporter identity, physical-stack hashes, ports, grounding,
    the 5-60 GHz grid, and the 15 GHz extraction point. The expected values
    bind the self-described manifest to the Q-sweep request.
    """

    root_path = Path(root).expanduser().resolve(strict=True)
    if not root_path.is_dir():
        raise PhysicalEvidenceValidationError(
            "physical evidence root is not a directory"
        )
    manifest = _contained_file(root_path, manifest_path, "evidence manifest")
    manifest_bytes = manifest.read_bytes()
    payload = _load_json_object(manifest_bytes, manifest)
    _require_exact_keys(payload, _TOP_LEVEL_KEYS, "evidence manifest")

    if payload["schema"] != PHYSICAL_EVIDENCE_SCHEMA:
        raise PhysicalEvidenceValidationError("unsupported physical evidence schema")
    candidate_id = _required_string(payload["candidate_id"], "candidate_id")
    if candidate_id != expected_candidate_id:
        raise PhysicalEvidenceValidationError(
            f"candidate_id mismatch: expected {expected_candidate_id!r}, "
            f"got {candidate_id!r}"
        )
    q_target = _required_int(payload["q_target"], "q_target")
    if q_target not in range(10, 21) or q_target != expected_q_target:
        raise PhysicalEvidenceValidationError(
            f"Q mismatch: expected exact Q={expected_q_target}, got {q_target}"
        )
    expected_geometry = _normalized_sha256(
        expected_geometry_sha256, "expected_geometry_sha256"
    )
    geometry_sha256 = _normalized_sha256(
        payload["geometry_sha256"], "geometry_sha256"
    )
    if geometry_sha256 != expected_geometry:
        raise PhysicalEvidenceValidationError("geometry SHA-256 mismatch")

    artifacts = _required_object(payload["artifacts"], "artifacts")
    _require_exact_keys(artifacts, _ARTIFACT_KEYS, "artifacts")
    artifact_paths: dict[str, Path] = {}
    artifact_hashes: dict[str, str] = {}
    for kind, suffix in (("gds", ".gds"), ("s4p", ".s4p")):
        record = _required_object(artifacts[kind], f"artifacts.{kind}")
        _require_exact_keys(record, _ARTIFACT_RECORD_KEYS, f"artifacts.{kind}")
        path_value = _required_string(
            record["path"], f"artifacts.{kind}.path"
        )
        path = _contained_file(root_path, path_value, f"{kind.upper()} artifact")
        if path.suffix.lower() != suffix:
            raise PhysicalEvidenceValidationError(
                f"{kind.upper()} artifact must use the {suffix} extension"
            )
        declared_hash = _normalized_sha256(
            record["sha256"], f"artifacts.{kind}.sha256"
        )
        actual_hash = _sha256(path)
        if actual_hash != declared_hash:
            raise PhysicalEvidenceValidationError(
                f"{kind.upper()} SHA-256 mismatch"
            )
        artifact_paths[kind] = path
        artifact_hashes[kind] = actual_hash
    if artifact_paths["gds"] == artifact_paths["s4p"]:
        raise PhysicalEvidenceValidationError("GDS and S4P must be distinct files")

    top_cell = _required_string(payload["top_cell"], "top_cell")
    _validate_gds(artifact_paths["gds"], top_cell, candidate_id)
    frequencies_hz = _validate_s4p(artifact_paths["s4p"], candidate_id)
    for kind in ("gds", "s4p"):
        if _sha256(artifact_paths[kind]) != artifact_hashes[kind]:
            raise PhysicalEvidenceValidationError(
                f"{kind.upper()} changed during validation"
            )

    exporter = _required_object(
        payload["structured_exporter"], "structured_exporter"
    )
    _require_exact_keys(exporter, _EXPORTER_KEYS, "structured_exporter")
    exporter_id = _required_string(
        exporter["id"], "structured_exporter.id"
    )
    if exporter_id != STRUCTURED_EXPORTER_ID:
        raise PhysicalEvidenceValidationError("structured exporter id mismatch")
    exporter_source_sha256 = _normalized_sha256(
        exporter["source_sha256"], "structured_exporter.source_sha256"
    )
    provenance_hashes = {
        field: _normalized_sha256(payload[field], field)
        for field in _PROVENANCE_SHA_FIELDS
    }

    ports = _string_tuple(payload["ports"], "ports")
    if ports != EXPECTED_PORTS:
        raise PhysicalEvidenceValidationError(
            "port contract mismatch; expected P001,P002,P003,P004 in order"
        )
    pairs = _pair_tuple(payload["differential_pairs"])
    if pairs != EXPECTED_DIFFERENTIAL_PAIRS:
        raise PhysicalEvidenceValidationError(
            "differential pair mismatch; expected P001-P002 and P003-P004"
        )
    ground_mode = _required_string(payload["ground_mode"], "ground_mode")
    if ground_mode != EXPECTED_GROUND_MODE:
        raise PhysicalEvidenceValidationError(
            f"ground mode mismatch; expected {EXPECTED_GROUND_MODE}"
        )

    frequency_grid = _required_object(
        payload["frequency_grid"], "frequency_grid"
    )
    _require_exact_keys(
        frequency_grid, _FREQUENCY_GRID_KEYS, "frequency_grid"
    )
    _require_frequency_value(
        frequency_grid["start_hz"],
        EXPECTED_FREQUENCY_START_HZ,
        "frequency_grid.start_hz",
    )
    _require_frequency_value(
        frequency_grid["stop_hz"],
        EXPECTED_FREQUENCY_STOP_HZ,
        "frequency_grid.stop_hz",
    )
    _require_frequency_value(
        frequency_grid["step_hz"],
        EXPECTED_FREQUENCY_STEP_HZ,
        "frequency_grid.step_hz",
    )
    point_count = _required_int(
        frequency_grid["point_count"], "frequency_grid.point_count"
    )
    if point_count != EXPECTED_FREQUENCY_POINT_COUNT:
        raise PhysicalEvidenceValidationError(
            "frequency point count mismatch: "
            f"expected {EXPECTED_FREQUENCY_POINT_COUNT}"
        )
    _require_frequency_value(
        payload["feature_extraction_hz"],
        EXPECTED_FEATURE_EXTRACTION_HZ,
        "feature_extraction_hz",
    )

    return {
        "schema": PHYSICAL_EVIDENCE_VALIDATION_SCHEMA,
        "status": "PASS",
        "evidence_class": "CANDIDATE_BOUND_GDS_S4P_CONTRACT",
        "candidate_id": candidate_id,
        "q_target": q_target,
        "geometry_sha256": geometry_sha256,
        "evidence_manifest": str(manifest),
        "evidence_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "artifacts": {
            "gds": {
                "path": str(artifact_paths["gds"]),
                "sha256": artifact_hashes["gds"],
                "top_cell": top_cell,
            },
            "s4p": {
                "path": str(artifact_paths["s4p"]),
                "sha256": artifact_hashes["s4p"],
                "port_count": len(EXPECTED_PORTS),
                "frequency_start_hz": float(frequencies_hz[0]),
                "frequency_stop_hz": float(frequencies_hz[-1]),
                "frequency_step_hz": EXPECTED_FREQUENCY_STEP_HZ,
                "frequency_point_count": int(len(frequencies_hz)),
                "feature_extraction_hz": EXPECTED_FEATURE_EXTRACTION_HZ,
            },
        },
        "structured_exporter": {
            "id": exporter_id,
            "source_sha256": exporter_source_sha256,
        },
        **provenance_hashes,
        "ports": list(ports),
        "differential_pairs": [list(pair) for pair in pairs],
        "ground_mode": ground_mode,
        "scientific_boundary": (
            "This record validates candidate/artifact identity, GDS structure, and "
            "the four-port S4P grid. It is not Calibre DRC or HFSS evidence; fresh "
            "EMX execution still requires its separate terminal backend receipt."
        ),
    }


def _load_json_object(data: bytes, path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PhysicalEvidenceValidationError(
                    f"duplicate JSON key {key!r} in {path}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=reject_duplicates
        )
    except PhysicalEvidenceValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalEvidenceValidationError(
            f"invalid evidence manifest JSON: {exc}"
        ) from exc
    return _required_object(value, "evidence manifest")


def _contained_file(root: Path, value: str | Path, label: str) -> Path:
    raw = Path(value)
    try:
        path = (
            raw if raw.is_absolute() else root / raw
        ).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PhysicalEvidenceValidationError(
            f"missing {label}: {value}"
        ) from exc
    if not path.is_relative_to(root) or not path.is_file():
        raise PhysicalEvidenceValidationError(
            f"unsafe or missing {label}: {value}"
        )
    return path


def _validate_gds(path: Path, top_cell: str, candidate_id: str) -> None:
    import gdstk

    try:
        library = gdstk.read_gds(str(path))
        top_cells = {cell.name: cell for cell in library.top_level()}
    except Exception as exc:
        raise PhysicalEvidenceValidationError(
            f"invalid GDS for {candidate_id}: {exc}"
        ) from exc
    if top_cell not in top_cells:
        raise PhysicalEvidenceValidationError(
            f"GDS top_cell mismatch for {candidate_id}: "
            f"{top_cell!r} is not top-level"
        )
    cell = top_cells[top_cell]
    if not (cell.polygons or cell.paths or cell.references):
        raise PhysicalEvidenceValidationError(
            f"GDS top_cell is empty for {candidate_id}: {top_cell!r}"
        )


def _validate_s4p(path: Path, candidate_id: str) -> np.ndarray:
    try:
        result = load_touchstone(path)
    except Exception as exc:
        raise PhysicalEvidenceValidationError(
            f"invalid S4P for {candidate_id}: {exc}"
        ) from exc
    frequencies = np.asarray(result.freqs_hz, dtype=float)
    expected = np.linspace(
        EXPECTED_FREQUENCY_START_HZ,
        EXPECTED_FREQUENCY_STOP_HZ,
        EXPECTED_FREQUENCY_POINT_COUNT,
    )
    if result.num_ports != len(EXPECTED_PORTS):
        raise PhysicalEvidenceValidationError(
            f"S4P port count mismatch for {candidate_id}: expected 4"
        )
    if result.s_matrix.shape != (EXPECTED_FREQUENCY_POINT_COUNT, 4, 4):
        raise PhysicalEvidenceValidationError(
            f"S4P shape mismatch for {candidate_id}"
        )
    if frequencies.shape != expected.shape or not np.allclose(
        frequencies,
        expected,
        rtol=0.0,
        atol=FREQUENCY_TOLERANCE_HZ,
    ):
        raise PhysicalEvidenceValidationError(
            f"S4P frequency grid mismatch for {candidate_id}"
        )
    if not np.isfinite(frequencies).all() or not np.isfinite(
        result.s_matrix
    ).all():
        raise PhysicalEvidenceValidationError(
            f"non-finite S4P for {candidate_id}"
        )
    if not np.any(
        np.isclose(
            frequencies,
            EXPECTED_FEATURE_EXTRACTION_HZ,
            rtol=0.0,
            atol=FREQUENCY_TOLERANCE_HZ,
        )
    ):
        raise PhysicalEvidenceValidationError(
            f"15 GHz is absent from S4P for {candidate_id}"
        )
    return frequencies


def _require_exact_keys(
    value: dict[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PhysicalEvidenceValidationError(
            f"{label} keys mismatch: missing={missing}, extra={extra}"
        )


def _required_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PhysicalEvidenceValidationError(
            f"{label} must be a JSON object"
        )
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PhysicalEvidenceValidationError(
            f"{label} must be a non-empty string"
        )
    if any(ord(character) < 32 for character in value):
        raise PhysicalEvidenceValidationError(
            f"{label} contains a control character"
        )
    return value


def _required_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhysicalEvidenceValidationError(
            f"{label} must be an integer"
        )
    return value


def _normalized_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise PhysicalEvidenceValidationError(
            f"{label} must be exactly 64 hexadecimal characters"
        )
    return value.lower()


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PhysicalEvidenceValidationError(
            f"{label} must be a JSON array"
        )
    return tuple(
        _required_string(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )


def _pair_tuple(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise PhysicalEvidenceValidationError(
            "differential_pairs must be a JSON array"
        )
    pairs: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 2:
            raise PhysicalEvidenceValidationError(
                f"differential_pairs[{index}] must contain exactly two ports"
            )
        pairs.append(
            (
                _required_string(
                    item[0], f"differential_pairs[{index}][0]"
                ),
                _required_string(
                    item[1], f"differential_pairs[{index}][1]"
                ),
            )
        )
    return tuple(pairs)


def _require_frequency_value(
    value: Any, expected: float, label: str
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PhysicalEvidenceValidationError(
            f"{label} must be numeric"
        )
    observed = float(value)
    if not math.isfinite(observed) or observed != expected:
        raise PhysicalEvidenceValidationError(
            f"{label} mismatch: expected {expected:g}, got {observed!r}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "EXPECTED_DIFFERENTIAL_PAIRS",
    "EXPECTED_FEATURE_EXTRACTION_HZ",
    "EXPECTED_FREQUENCY_POINT_COUNT",
    "EXPECTED_FREQUENCY_START_HZ",
    "EXPECTED_FREQUENCY_STEP_HZ",
    "EXPECTED_FREQUENCY_STOP_HZ",
    "EXPECTED_GROUND_MODE",
    "EXPECTED_PORTS",
    "PHYSICAL_EVIDENCE_SCHEMA",
    "PHYSICAL_EVIDENCE_VALIDATION_SCHEMA",
    "PhysicalEvidenceValidationError",
    "STRUCTURED_EXPORTER_ID",
    "validate_physical_evidence_manifest",
]
