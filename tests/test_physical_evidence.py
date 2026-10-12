from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import gdstk
import numpy as np
import pytest

from rfic_transformer_inverse_design.sim.base import SParameterResult
from rfic_transformer_inverse_design.synthesis.physical_evidence import (
    PHYSICAL_EVIDENCE_SCHEMA,
    PHYSICAL_EVIDENCE_VALIDATION_SCHEMA,
    STRUCTURED_EXPORTER_ID,
    PhysicalEvidenceValidationError,
    validate_physical_evidence_manifest,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_artifacts(root: Path) -> tuple[Path, Path]:
    candidate = root / "candidate_q10"
    candidate.mkdir(parents=True)
    gds = candidate / "layout.gds"
    library = gdstk.Library()
    top = library.new_cell("TRANSFORMER")
    top.add(gdstk.rectangle((0.0, 0.0), (10.0, 10.0), layer=74))
    library.write_gds(gds)

    s4p = candidate / "emx.s4p"
    SParameterResult(
        freqs_hz=np.linspace(5.0e9, 60.0e9, 111),
        s_matrix=np.zeros((111, 4, 4), dtype=np.complex128),
    ).to_touchstone(s4p)
    return gds, s4p


def _manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    gds, s4p = _write_artifacts(root)
    payload: dict[str, Any] = {
        "schema": PHYSICAL_EVIDENCE_SCHEMA,
        "candidate_id": "demo_q10",
        "q_target": 10,
        "geometry_sha256": "a" * 64,
        "artifacts": {
            "gds": {
                "path": str(gds.relative_to(root)),
                "sha256": _sha256(gds),
            },
            "s4p": {
                "path": str(s4p.relative_to(root)),
                "sha256": _sha256(s4p),
            },
        },
        "top_cell": "TRANSFORMER",
        "structured_exporter": {
            "id": STRUCTURED_EXPORTER_ID,
            "source_sha256": "b" * 64,
        },
        "process_sha256": "c" * 64,
        "config_sha256": "d" * 64,
        "backend_sha256": "e" * 64,
        "emx_wrapper_sha256": "f" * 64,
        "ports": ["P001", "P002", "P003", "P004"],
        "differential_pairs": [["P001", "P002"], ["P003", "P004"]],
        "ground_mode": "single_ended_shield_grounded",
        "frequency_grid": {
            "start_hz": 5.0e9,
            "stop_hz": 60.0e9,
            "step_hz": 0.5e9,
            "point_count": 111,
        },
        "feature_extraction_hz": 15.0e9,
    }
    path = root / "candidate_q10" / "evidence_manifest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path, payload


def _validate(path: Path, root: Path) -> dict[str, Any]:
    return validate_physical_evidence_manifest(
        path,
        root=root,
        expected_candidate_id="demo_q10",
        expected_q_target=10,
        expected_geometry_sha256="a" * 64,
    )


def test_physical_evidence_manifest_passes_and_returns_artifact_metadata(
    tmp_path: Path,
) -> None:
    manifest, _ = _manifest(tmp_path)

    record = _validate(manifest, tmp_path)

    assert record["schema"] == PHYSICAL_EVIDENCE_VALIDATION_SCHEMA
    assert record["status"] == "PASS"
    assert record["candidate_id"] == "demo_q10"
    assert record["ports"] == ["P001", "P002", "P003", "P004"]
    assert record["artifacts"]["gds"]["sha256"] == _sha256(
        tmp_path / "candidate_q10/layout.gds"
    )
    assert record["artifacts"]["s4p"]["frequency_point_count"] == 111
    assert "not Calibre DRC or HFSS evidence" in record["scientific_boundary"]


def test_physical_evidence_rejects_actual_artifact_hash_mismatch(
    tmp_path: Path,
) -> None:
    manifest, payload = _manifest(tmp_path)
    changed = deepcopy(payload)
    changed["artifacts"]["gds"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(
        PhysicalEvidenceValidationError, match="GDS SHA-256 mismatch"
    ):
        _validate(manifest, tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "ports",
            ["P002", "P001", "P003", "P004"],
            "port contract mismatch",
        ),
        (
            "frequency_grid.start_hz",
            6.0e9,
            "frequency_grid.start_hz mismatch",
        ),
    ],
)
def test_physical_evidence_rejects_port_or_grid_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    manifest, payload = _manifest(tmp_path)
    changed = deepcopy(payload)
    if field == "ports":
        changed["ports"] = value
    else:
        changed["frequency_grid"]["start_hz"] = value
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(PhysicalEvidenceValidationError, match=message):
        _validate(manifest, tmp_path)


def test_physical_evidence_rejects_artifact_path_outside_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    manifest, payload = _manifest(root)
    outside = tmp_path / "outside.gds"
    outside.write_bytes((root / "candidate_q10/layout.gds").read_bytes())
    changed = deepcopy(payload)
    changed["artifacts"]["gds"] = {
        "path": str(outside),
        "sha256": _sha256(outside),
    }
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(
        PhysicalEvidenceValidationError, match="unsafe or missing GDS"
    ):
        _validate(manifest, root)
