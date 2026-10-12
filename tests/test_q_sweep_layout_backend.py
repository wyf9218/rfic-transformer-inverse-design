from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import gdstk
import pytest
import yaml

from rfic_transformer_inverse_design.api import load_run_config
from rfic_transformer_inverse_design.paths import default_proc_path
from rfic_transformer_inverse_design.synthesis import q_sweep_layout_backend as backend
from rfic_transformer_inverse_design.synthesis.layout_evidence import (
    LAYOUT_EVIDENCE_CLASS,
    LAYOUT_EVIDENCE_SCHEMA,
    LayoutEvidenceValidationError,
    STRUCTURED_EXPORTER_ID,
    validate_layout_evidence_manifest,
)
from rfic_transformer_inverse_design.synthesis.q_sweep import _geometry_sha256


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_evidence_fixture(
    root: Path,
    *,
    top_cell: str = "EXPECTED_TOP",
    gds_mode: str = "valid",
) -> Path:
    root.mkdir()
    gds = root / "candidate.gds"
    library = gdstk.Library()
    if gds_mode == "valid":
        cell = library.new_cell(top_cell)
        cell.add(gdstk.rectangle((0.0, 0.0), (10.0, 10.0), layer=1))
    elif gds_mode == "empty_top":
        library.new_cell(top_cell)
        unrelated = library.new_cell("UNRELATED_DRAWABLE")
        unrelated.add(gdstk.rectangle((0.0, 0.0), (10.0, 10.0), layer=1))
    elif gds_mode == "non_root_top":
        child = library.new_cell(top_cell)
        child.add(gdstk.rectangle((0.0, 0.0), (10.0, 10.0), layer=1))
        root_cell = library.new_cell("ACTUAL_ROOT")
        root_cell.add(gdstk.Reference(child))
    else:  # pragma: no cover - fixture misuse
        raise AssertionError(gds_mode)
    library.write_gds(str(gds))

    process = root / "fixture.proc"
    process.write_text("synthetic unit-test process\n", encoding="utf-8")
    process_sha256 = _sha256(process)
    artifacts: dict[str, dict[str, str]] = {
        "gds": {"path": gds.name, "sha256": _sha256(gds)}
    }
    for name in ("geometry_json", "evaluation_summary"):
        path = root / f"{name}.json"
        path.write_text(json.dumps({"artifact": name}) + "\n", encoding="utf-8")
        artifacts[name] = {"path": path.name, "sha256": _sha256(path)}
    layout_manifest = root / "layout_manifest.json"
    layout_manifest.write_text(
        json.dumps(
            {
                "top_cell": top_cell,
                "layout_path": gds.name,
                "ports": [{"name": f"P{index:03d}"} for index in range(1, 5)],
                "layer_draw_order": [1],
                "process_layer_summary": {
                    "schema": "rfic_transformer_process_layer_summary.v1",
                    "process_file": str(process),
                    "records": {"M1": {"layer": 1}},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts["layout_manifest"] = {
        "path": layout_manifest.name,
        "sha256": _sha256(layout_manifest),
    }

    manifest = root / "evidence_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": LAYOUT_EVIDENCE_SCHEMA,
                "candidate_id": "candidate_q11",
                "q_target": 11,
                "geometry_sha256": "1" * 64,
                "artifacts": artifacts,
                "top_cell": top_cell,
                "structured_exporter": {
                    "id": STRUCTURED_EXPORTER_ID,
                    "source_sha256": "2" * 64,
                },
                "config_sha256": "3" * 64,
                "process_sha256": process_sha256,
                "layout_backend_sha256": "5" * 64,
                "evaluator_sha256": "6" * 64,
                "emx_status": "NOT_RUN",
                "calibre_drc": "NOT_RUN",
                "hfss_correlation": "NOT_RUN",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _validate_fixture(manifest: str | Path, root: Path) -> dict[str, Any]:
    return validate_layout_evidence_manifest(
        manifest,
        root=root,
        expected_candidate_id="candidate_q11",
        expected_q_target=11,
        expected_geometry_sha256="1" * 64,
    )


def test_layout_evidence_accepts_path_manifest(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    manifest = _write_evidence_fixture(root)

    validated = _validate_fixture(manifest, root)

    assert validated["evidence_class"] == LAYOUT_EVIDENCE_CLASS
    assert validated["top_cell"] == "EXPECTED_TOP"
    assert validated["emx_status"] == "NOT_RUN"


@pytest.mark.parametrize("gds_mode", ["empty_top", "non_root_top"])
def test_layout_evidence_rejects_empty_or_non_root_declared_top_cell(
    tmp_path: Path,
    gds_mode: str,
) -> None:
    root = tmp_path / gds_mode
    manifest = _write_evidence_fixture(root, gds_mode=gds_mode)

    with pytest.raises(LayoutEvidenceValidationError, match="top cell|empty GDS"):
        _validate_fixture(manifest, root)


def test_layout_evidence_rejects_gds_tampering(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    manifest = _write_evidence_fixture(root)
    gds = root / "candidate.gds"
    gds.write_bytes(gds.read_bytes() + b"tampered")

    with pytest.raises(LayoutEvidenceValidationError, match="gds SHA-256 mismatch"):
        _validate_fixture(manifest, root)


def _write_layout_config(tmp_path: Path) -> tuple[Path, Path]:
    template = Path(
        "configs/mars_s4p_grounded_powerline_physical_feature_500_template.yaml"
    )
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    process = Path(default_proc_path()).resolve()
    raw["emx"]["emx_process_file"] = str(process)
    raw["emx"]["execution_mode"] = "local"
    config = tmp_path / "grounded_s4p.yaml"
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config, process


def _write_layout_request(tmp_path: Path, config: Path) -> Path:
    run_config = load_run_config(config)
    flat = run_config.bounds.midpoint().with_shared_line_width(7.5).flat_dict()
    base_geometry = {
        "primary_outer_width_um": float(flat["primary_outer_width_um"]),
        "primary_outer_height_um": float(flat["primary_outer_height_um"]),
        "secondary_outer_width_um": float(flat["secondary_outer_width_um"]),
        "secondary_outer_height_um": float(flat["secondary_outer_height_um"]),
        "line_width_um": float(flat["line_width_um"]),
        "primary_terminal_y_span_um": float(flat["primary_terminal_y_span_um"]),
        "secondary_terminal_y_span_um": float(flat["secondary_terminal_y_span_um"]),
        "offset_um": float(flat["offset_um"]),
        "primary_feed_extension_um": float(flat["primary_feed_extension_um"]),
        "secondary_feed_extension_um": float(flat["secondary_feed_extension_um"]),
    }
    candidates = []
    for q_target in range(10, 21):
        geometry = dict(base_geometry)
        geometry["offset_um"] = float(q_target - 15)
        candidates.append(
            {
                "candidate_id": f"layout_q{q_target:02d}",
                "q_target": q_target,
                "target_features": {
                    "Lp_nH": 1.15,
                    "Ls_nH": 1.40,
                    "Q_scalar": float(q_target),
                    "K_abs": 0.76,
                },
                "geometry_um": geometry,
                "geometry_sha256": _geometry_sha256(geometry),
            }
        )
    request = tmp_path / "layout_request.json"
    request.write_text(
        json.dumps(
            {
                "schema": backend.LAYOUT_REQUEST_SCHEMA,
                "model_id": "layout-test-model",
                "model_seed": 0,
                "model_identity": {
                    "model_id": "layout-test-model",
                    "model_seed": 0,
                    "contract_payload_sha256": "7" * 64,
                    "architecture": {},
                },
                "target_frequency_ghz": 15.0,
                "q_values": list(range(10, 21)),
                "required_label_source": LAYOUT_EVIDENCE_CLASS,
                "required_outputs": sorted(backend.REQUIRED_LAYOUT_OUTPUTS),
                "emx_requested": False,
                "candidates": candidates,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return request


def test_layout_backend_exports_exact_batch_with_emx_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, process = _write_layout_config(tmp_path)
    request = _write_layout_request(tmp_path, config)
    output = tmp_path / "layout_output"
    actual_evaluator = backend.TransformerEmxEvaluator

    class TrackingEvaluator(actual_evaluator):
        run_emx_values: list[bool] = []

        def evaluate_geometry_batch(
            self,
            geometries: list[Any] | tuple[Any, ...],
            run_emx: bool = True,
        ) -> list[Any]:
            self.run_emx_values.append(run_emx)
            return super().evaluate_geometry_batch(geometries, run_emx=run_emx)

    monkeypatch.setattr(backend, "TransformerEmxEvaluator", TrackingEvaluator)
    monkeypatch.setenv("RFIC_SKIP_LAYOUT_PREVIEWS", "1")
    monkeypatch.setenv("RFIC_SKIP_LUMPED_COMPARE", "1")

    return_code = backend.main(
        [
            "--request-json",
            str(request),
            "--out-dir",
            str(output),
            "--config",
            str(config),
            "--config-sha256",
            _sha256(config),
            "--process-sha256",
            _sha256(process),
        ]
    )

    assert return_code == 0
    assert TrackingEvaluator.run_emx_values == [False]
    receipt = json.loads((output / backend.LAYOUT_RECEIPT_FILENAME).read_text())
    results = json.loads((output / backend.LAYOUT_RESULTS_FILENAME).read_text())
    assert receipt["overall_status"] == "PASS"
    assert receipt["evidence_source"] == LAYOUT_EVIDENCE_CLASS
    assert receipt["candidate_count"] == receipt["pass_count"] == 11
    assert receipt["emx_status"] == "NOT_RUN"
    assert receipt["s4p_generated_count"] == 0
    assert results["emx_status"] == "NOT_RUN"
    assert len(results["results"]) == 11
    assert not any(
        path.is_file() and re.fullmatch(r"\.s\d+p", path.suffix.lower())
        for path in output.rglob("*")
    )
    for row in results["results"]:
        gds = output / row["artifacts"]["gds"]
        evidence = output / row["artifacts"]["evidence_manifest"]
        assert gds.is_file() and gds.stat().st_size > 0
        assert gdstk.read_gds(str(gds)).cells
        validated = validate_layout_evidence_manifest(
            evidence,
            root=output,
            expected_candidate_id=row["candidate_id"],
            expected_q_target=row["q_target"],
            expected_geometry_sha256=row["geometry_sha256"],
        )
        assert validated["candidate_id"] == row["candidate_id"]


@pytest.mark.parametrize(
    ("emx_requested", "extra_output", "message"),
    [
        (True, None, "emx_requested=false"),
        (False, "S4P", "structured-GDS output contract"),
    ],
)
def test_layout_preflight_rejects_emx_or_extra_outputs(
    tmp_path: Path,
    emx_requested: bool,
    extra_output: str | None,
    message: str,
) -> None:
    config, process = _write_layout_config(tmp_path)
    request = _write_layout_request(tmp_path, config)
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["emx_requested"] = emx_requested
    if extra_output is not None:
        payload["required_outputs"].append(extra_output)
    request.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(backend.PhysicalBackendContractError, match=message):
        backend.preflight_layout_backend(
            request_json=request,
            config=config,
            config_sha256=_sha256(config),
            process_sha256=_sha256(process),
        )
