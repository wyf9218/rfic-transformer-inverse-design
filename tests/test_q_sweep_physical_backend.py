from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from rfic_transformer_inverse_design.api import (
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.paths import default_proc_path
from rfic_transformer_inverse_design.synthesis import q_sweep_physical_backend as backend
from rfic_transformer_inverse_design.synthesis.q_sweep import _geometry_sha256


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
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


def _geometry_from_config(config: Path) -> dict[str, float]:
    run_config = load_run_config(config)
    midpoint = run_config.bounds.midpoint().with_shared_line_width(7.5)
    flat = midpoint.flat_dict()
    return {
        "primary_outer_width_um": float(flat["primary_outer_width_um"]),
        "primary_outer_height_um": float(flat["primary_outer_height_um"]),
        "secondary_outer_width_um": float(flat["secondary_outer_width_um"]),
        "secondary_outer_height_um": float(flat["secondary_outer_height_um"]),
        "line_width_um": float(flat["line_width_um"]),
        "primary_terminal_y_span_um": float(
            flat["primary_terminal_y_span_um"]
        ),
        "secondary_terminal_y_span_um": float(
            flat["secondary_terminal_y_span_um"]
        ),
        "offset_um": float(flat["offset_um"]),
        "primary_feed_extension_um": float(flat["primary_feed_extension_um"]),
        "secondary_feed_extension_um": float(
            flat["secondary_feed_extension_um"]
        ),
    }


def _write_request(tmp_path: Path, config: Path) -> tuple[Path, dict[str, Any]]:
    geometry = _geometry_from_config(config)
    rows = []
    for q_target in range(10, 21):
        row_geometry = dict(geometry)
        row_geometry["offset_um"] = float(q_target - 15)
        rows.append(
            {
                "candidate_id": f"demo_q{q_target:02d}",
                "q_target": q_target,
                "target_features": {
                    "Lp_nH": 1.15,
                    "Ls_nH": 1.40,
                    "Q_scalar": float(q_target),
                    "K_abs": 0.76,
                },
                "geometry_um": row_geometry,
                "geometry_sha256": _geometry_sha256(row_geometry),
            }
        )
    payload: dict[str, Any] = {
        "schema": "rfic_q_sweep_physical_request.v1",
        "model_id": "dummy",
        "model_seed": 0,
        "model_identity": {
            "model_id": "dummy",
            "model_seed": 0,
            "contract_payload_sha256": "",
            "architecture": {},
        },
        "target_frequency_ghz": 15.0,
        "q_values": list(range(10, 21)),
        "required_label_source": "FRESH_REAL_EMX",
        "required_outputs": ["GDS", "S4P", "Lp", "Ls", "Qp", "Qs", "K_abs"],
        "candidates": rows,
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path, payload


def _preflight(tmp_path: Path) -> backend.PhysicalBackendPreflight:
    config, process = _write_config(tmp_path)
    request, _ = _write_request(tmp_path, config)
    return backend.preflight_physical_backend(
        request_json=request,
        config=config,
        config_sha256=_sha256(config),
        process_sha256=_sha256(process),
    )


def test_gui_operating_point_request_fails_before_native_preparation(tmp_path, monkeypatch):
    config, process = _write_config(tmp_path)
    request, payload = _write_request(tmp_path, config)
    payload["label_policy"] = "operating_point_15ghz_v1"
    payload["srf_required"] = False
    payload["required_evidence"] = ["operating_point_evidence", "physical_gate_evidence"]
    request.write_text(json.dumps(payload), encoding="utf-8")

    def must_not_prepare(*args, **kwargs):
        raise AssertionError("unsupported producer must fail before candidate preparation")

    monkeypatch.setattr(backend, "_prepare_request_candidates", must_not_prepare)
    with pytest.raises(backend.PhysicalBackendContractError, match="NOT_NATIVE_ACCEPTED"):
        backend.preflight_physical_backend(
            request_json=request,
            config=config,
            config_sha256=_sha256(config),
            process_sha256=_sha256(process),
        )


def test_preflight_maps_shared_10d_geometry_to_current_11_field_adapter(
    tmp_path: Path,
) -> None:
    prepared = _preflight(tmp_path)

    assert len(prepared.candidates) == 11
    assert prepared.adapter.field_order() == backend.EXPECTED_ADAPTER_FIELDS
    candidate = prepared.candidates[0]
    assert len(candidate.geometry_um) == 10
    assert len(candidate.adapter_vector) == 11
    assert candidate.geometry.primary.trace_width_um == pytest.approx(7.5)
    assert candidate.geometry.secondary.trace_width_um == pytest.approx(7.5)
    assert candidate.geometry.primary.vdd_bar is not None
    assert candidate.geometry.secondary.vdd_bar is not None
    assert candidate.geometry.primary.vdd_bar.width_um == pytest.approx(7.5)
    assert candidate.geometry.secondary.vdd_bar.width_um == pytest.approx(7.5)
    assert prepared.run_config.emx.differential_port_pairs == ((0, 1), (2, 3))
    assert prepared.run_config.target.frequency_points_hz().shape == (111,)


def test_preflight_rejects_geometry_sha_mismatch(tmp_path: Path) -> None:
    config, process = _write_config(tmp_path)
    request, payload = _write_request(tmp_path, config)
    payload["candidates"][0]["geometry_sha256"] = "0" * 64
    request.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        backend.PhysicalBackendContractError,
        match="geometry SHA-256 mismatch",
    ):
        backend.preflight_physical_backend(
            request_json=request,
            config=config,
            config_sha256=_sha256(config),
            process_sha256=_sha256(process),
        )


def test_preflight_rejects_mapped_geometry_outside_configured_bounds(
    tmp_path: Path,
) -> None:
    config, process = _write_config(tmp_path)
    request, payload = _write_request(tmp_path, config)
    geometry = payload["candidates"][0]["geometry_um"]
    geometry["line_width_um"] = 13.0
    payload["candidates"][0]["geometry_sha256"] = _geometry_sha256(geometry)
    request.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        backend.PhysicalBackendContractError,
        match="outside configured bounds",
    ):
        backend.preflight_physical_backend(
            request_json=request,
            config=config,
            config_sha256=_sha256(config),
            process_sha256=_sha256(process),
        )


@pytest.mark.parametrize("bad_hash_argument", ["config", "process"])
def test_preflight_rejects_config_or_process_sha_mismatch(
    tmp_path: Path,
    bad_hash_argument: str,
) -> None:
    config, process = _write_config(tmp_path)
    request, _ = _write_request(tmp_path, config)
    config_sha = "0" * 64 if bad_hash_argument == "config" else _sha256(config)
    process_sha = "0" * 64 if bad_hash_argument == "process" else _sha256(process)

    with pytest.raises(
        backend.PhysicalBackendContractError,
        match=f"{bad_hash_argument}_sha256_matches",
    ):
        backend.preflight_physical_backend(
            request_json=request,
            config=config,
            config_sha256=config_sha,
            process_sha256=process_sha,
        )


def test_mock_evaluator_failure_writes_terminal_receipt_without_physical_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, process = _write_config(tmp_path)
    request, _ = _write_request(tmp_path, config)
    output = tmp_path / "backend_output"
    output.mkdir()  # q_sweep creates the fresh empty physical_backend directory.

    class FailedResult:
        def __init__(self, index: int) -> None:
            self.cache_key = f"mock-{index}"
            self.work_dir = output / "evaluations" / self.cache_key
            self.error = "synthetic evaluator failure; EMX was not run"

        def ok(self) -> bool:
            return False

    class MockEvaluator:
        call_count = 0
        run_emx = None

        def __init__(self, *, run_config: Any, root_dir: Path) -> None:
            assert run_config.emx.port_mode == "single_ended_shield_grounded"
            assert root_dir == output.resolve()

        def evaluate_geometry_batch(
            self, geometries: list[Any], *, run_emx: bool
        ) -> list[FailedResult]:
            MockEvaluator.call_count = len(geometries)
            MockEvaluator.run_emx = run_emx
            return [FailedResult(index) for index in range(len(geometries))]

    monkeypatch.setattr(backend, "TransformerEmxEvaluator", MockEvaluator)
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

    assert return_code == 1
    assert MockEvaluator.call_count == 11
    assert MockEvaluator.run_emx is True
    assert not (output / "physical_results.json").exists()
    receipt = json.loads((output / backend.BACKEND_RECEIPT_FILENAME).read_text())
    assert receipt["overall_status"] == "FAIL"
    assert receipt["decision"] == "DO_NOT_USE_Q_SWEEP_PHYSICAL_BACKEND_OUTPUT"
    assert receipt["candidate_count"] == 11
    assert receipt["pass_count"] == 0
    assert receipt["fail_count"] == 11
    assert len(receipt["candidates"]) == 11
    assert {item["status"] for item in receipt["candidates"]} == {"FAIL"}
    assert "synthetic evaluator failure" in receipt["failure_message"]
    assert (output / backend.BACKEND_CLAIM_FILENAME).is_file()


def test_preflight_failure_receipt_is_written_before_evaluator_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, process = _write_config(tmp_path)
    request, _ = _write_request(tmp_path, config)
    output = tmp_path / "preflight_failure"

    class ForbiddenEvaluator:
        def __init__(self, **_: Any) -> None:
            raise AssertionError("evaluator must not be constructed")

    monkeypatch.setattr(backend, "TransformerEmxEvaluator", ForbiddenEvaluator)
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
            "0" * 64,
        ]
    )

    assert return_code == 1
    receipt = json.loads((output / backend.BACKEND_RECEIPT_FILENAME).read_text())
    assert receipt["overall_status"] == "FAIL"
    assert receipt["candidate_count"] == 0
    assert receipt["preflight_checks"][-1]["name"] == "process_sha256_matches"
    assert receipt["preflight_checks"][-1]["status"] == "FAIL"
    assert not (output / "physical_results.json").exists()


def test_mock_success_writes_hash_bound_pass_receipt_without_running_emx_or_gds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, process = _write_config(tmp_path)
    request, _ = _write_request(tmp_path, config)
    output = tmp_path / "mock_success"

    def mock_run(
        preflight: backend.PhysicalBackendPreflight,
        *,
        out_dir: Path,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        assert out_dir == output.resolve()
        rows = [
            {
                "candidate_id": item.candidate_id,
                "q_target": item.q_target,
                "geometry_sha256": item.geometry_sha256,
                "features_15ghz": {
                    "Lp_nH": 1.15,
                    "Ls_nH": 1.40,
                    "Qp": float(item.q_target),
                    "Qs": float(item.q_target),
                    "K_abs": 0.76,
                },
                "artifacts": {
                    "gds": f"mock/{item.candidate_id}.gds",
                    "s4p": f"mock/{item.candidate_id}.s4p",
                    "evidence_manifest": (
                        f"candidate_evidence/{item.candidate_id}/evidence_manifest.json"
                    ),
                },
            }
            for item in preflight.candidates
        ]
        records = [
            {
                "candidate_id": row["candidate_id"],
                "q_target": row["q_target"],
                "geometry_sha256": row["geometry_sha256"],
                "status": "PASS",
                "error": None,
                "emx_command_sha256": "0" * 64,
                "emx_command_argument_count": 1,
                "artifacts": {
                    **row["artifacts"],
                    "geometry_json": f"mock/{row['candidate_id']}.geometry.json",
                    "layout_manifest": f"mock/{row['candidate_id']}.layout.json",
                    "evaluation_summary": f"mock/{row['candidate_id']}.summary.json",
                    "gds_sha256": "1" * 64,
                    "s4p_sha256": "2" * 64,
                    "evidence_manifest_sha256": "3" * 64,
                    "geometry_json_sha256": "4" * 64,
                    "layout_manifest_sha256": "5" * 64,
                    "evaluation_summary_sha256": "6" * 64,
                },
            }
            for row in rows
        ]
        return (
            {
                "schema": backend.PHYSICAL_RESULTS_SCHEMA,
                "label_source": "FRESH_REAL_EMX",
                "request_sha256": preflight.request_sha256,
                "config_sha256": preflight.config_sha256,
                "process_sha256": preflight.process_sha256,
                "model_identity": preflight.request["model_identity"],
                "results": rows,
            },
            records,
        )

    monkeypatch.setattr(backend, "run_physical_backend", mock_run)
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
    result_path = output / "physical_results.json"
    receipt_path = output / backend.BACKEND_RECEIPT_FILENAME
    receipt = json.loads(receipt_path.read_text())
    assert receipt["schema"] == backend.BACKEND_RECEIPT_SCHEMA
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "FRESH_REAL_EMX_Q_SWEEP_READY"
    assert receipt["evidence_source"] == "FRESH_REAL_EMX"
    assert receipt["backend_receipt"] == str(receipt_path.resolve())
    assert receipt["request_sha256"] == _sha256(request)
    assert receipt["config_sha256"] == _sha256(config)
    assert receipt["process_sha256"] == _sha256(process)
    assert receipt["physical_results"] == str(result_path.resolve())
    assert receipt["physical_results_sha256"] == _sha256(result_path)
    assert receipt["candidate_count"] == receipt["pass_count"] == 11
    assert receipt["fail_count"] == 0
    assert receipt["candidate_evidence_manifest_count"] == 11
    assert set(receipt["runtime_sources"]) == {
        "backend",
        "structured_exporter",
        "emx_wrapper",
    }
    for record in receipt["runtime_sources"].values():
        assert _sha256(Path(record["path"])) == record["sha256"]


def test_nonempty_output_directory_is_never_reused_or_modified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, process = _write_config(tmp_path)
    request, _ = _write_request(tmp_path, config)
    output = tmp_path / "occupied"
    output.mkdir()
    marker = output / "preserve.txt"
    marker.write_text("user evidence must remain unchanged", encoding="utf-8")

    class ForbiddenEvaluator:
        def __init__(self, **_: Any) -> None:
            raise AssertionError("evaluator must not be constructed")

    monkeypatch.setattr(backend, "TransformerEmxEvaluator", ForbiddenEvaluator)
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

    assert return_code == 1
    assert marker.read_text(encoding="utf-8") == "user evidence must remain unchanged"
    assert sorted(path.name for path in output.iterdir()) == ["preserve.txt"]
