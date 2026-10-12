from __future__ import annotations

import hashlib
import json
import http.client
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import rfic_transformer_inverse_design.synthesis.q_sweep_gui as gui_module
from rfic_transformer_inverse_design.synthesis.q_sweep import PhysicalTarget3
from rfic_transformer_inverse_design.synthesis.q_sweep_gui import (
    Job,
    JobStore,
    GuiServer,
    _attach_physical_q_components,
    _collect_artifacts,
    _error_summary,
    _index_html,
    _model_provenance,
    _public_candidates,
)


def _candidate(q_value: int, *, artifacts: dict[str, str] | None = None) -> dict:
    return {
        "candidate_id": f"demo_q{q_value:02d}",
        "q_target": q_value,
        "target_features": {
            "Lp_nH": 1.15,
            "Ls_nH": 1.40,
            "Q_scalar": float(q_value),
            "K_abs": 0.76,
        },
        "observed_features": {
            "Lp_nH": 1.16,
            "Ls_nH": 1.38,
            "Q_scalar": float(q_value) + 0.2,
            "K_abs": 0.75,
        },
        "geometry_um": {},
        "geometry_sha256": f"geometry-{q_value}",
        "per_feature_abs_error": {
            "Lp_nH": 0.01,
            "Ls_nH": 0.02,
            "Q_scalar": 0.2,
            "K_abs": 0.01,
        },
        "per_feature_percent_error": {
            "Lp_nH": 1.0,
            "Ls_nH": 2.0,
            "Q_scalar": 3.0,
            "K_abs": 4.0,
        },
        "declared_range_normalized_rmse": q_value / 1000.0,
        "relative_percent_rmse": float(q_value),
        "evidence_source": "FRESH_REAL_EMX",
        "artifacts": artifacts or {},
    }


def _provenance_model(contract: dict, summary: dict) -> SimpleNamespace:
    return SimpleNamespace(
        model_id="actual-model-id",
        model_seed=20260711,
        target_frequency_ghz=15.0,
        contract=contract,
        summary=summary,
    )


def _bound_candidates() -> list[dict]:
    candidates: list[dict] = []
    for q_value in range(10, 21):
        candidate = _candidate(q_value)
        qmin = float(q_value) + 0.2
        candidate["fresh_emx_features_15ghz"] = {
            "Qp": qmin,
            "Qs": qmin + 0.3,
            "Qmin": qmin,
        }
        candidate["artifacts"].update(
            {
                "s4p_sha256": "a" * 64,
                "evidence_manifest_sha256": "b" * 64,
                "backend_receipt_sha256": "c" * 64,
                "physical_evidence_class": "CANDIDATE_BOUND_GDS_S4P_CONTRACT",
            }
        )
        candidates.append(candidate)
    return candidates


def test_html_prioritizes_error_summary_best_layout_and_per_q_gds() -> None:
    html = _index_html("proxy")

    assert "候选误差 CSV" not in html
    assert "运行 JSON" not in html
    assert 'id="gdsQ"' in html
    assert 'id="downloadGds"' in html
    assert "11 个候选综合误差均值" in html
    assert "已标亮最优" in html
    assert "该 Q 的 GDS" in html
    assert "下载 GDS" in html
    assert "待物理生成" in html
    assert "它不是 GDS、DRC、EMX 或 HFSS 证据" in html
    assert "实际运行模型与证据状态" in html
    assert 'id="modelId"' in html
    assert 'id="modelArchitecture"' in html
    assert "完整网络结构" in html
    assert 'id="modelContractSha"' in html
    assert "source rows 不等于 gradient-training rows" in html
    assert 'id="observedQHeader"' in html
    assert "Qp 元数据" in html
    assert "Qs 元数据" in html
    assert "冻结 proxy 重建 Q（非物理）" in html
    assert "Qp/Qs 未运行，因此显示为“—”" in html
    assert "仅诊断，不是物理证据" in html


def test_model_provenance_reports_actual_identity_hashes_and_distinct_row_counts(
    tmp_path: Path,
) -> None:
    summary_sha = "1" * 64
    weights_sha = "2" * 64
    source_sha = "3" * 64
    contract = {
        "model_id": "actual-model-id",
        "model_seed": 20260711,
        "target_frequency_ghz": 15.0,
        "training_label_count": 10000,
        "q_training_definition": "min(Qp, Qs)",
        "architecture": {
            "inverse_mlp": [4, 256, 256, 256, 10],
            "forward_surrogate": [10, 256, 256, 256, 4],
            "hidden_activation": "GELU",
            "geometry_projection": "sigmoid_envelope",
        },
        "artifacts": {
            "summary": {"sha256": summary_sha},
            "weights": {"sha256": weights_sha},
            "training_table": {"sha256": source_sha},
        },
    }
    summary = {
        "training_count": 10000,
        "evaluation_mode": "validation_only",
        "quality_status": "REVIEW_REQUIRED_VALIDATION_ONLY",
        "evaluation_isolation": {
            "row_counts": {"train": 7871, "validation": 1227, "test": 902}
        },
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    metadata = _model_provenance(
        _provenance_model(contract, summary), contract_path=contract_path
    )

    assert metadata["model_id"] == "actual-model-id"
    assert metadata["model_seed"] == 20260711
    assert metadata["runtime_checkpoint_status"] == "RUNTIME_CHECKPOINT_NOT_VERIFIED"
    assert metadata["architecture"] == {
        "inverse_mlp": [4, 256, 256, 256, 10],
        "forward_surrogate": [10, 256, 256, 256, 4],
        "hidden_activation": "GELU",
        "geometry_projection": "sigmoid_envelope",
    }
    assert metadata["rows"]["source_rows"] == {
        "value": 10000,
        "source": "summary.training_count",
    }
    assert metadata["rows"]["gradient_training_rows"]["value"] == 7871
    assert metadata["rows"]["validation_rows"]["value"] == 1227
    assert metadata["rows"]["test_rows"]["value"] == 902
    assert metadata["rows"]["split_sum_matches_source"] is True
    assert metadata["sha256"]["contract"]["value"] == hashlib.sha256(
        contract_path.read_bytes()
    ).hexdigest()
    assert metadata["sha256"]["summary"]["value"] == summary_sha
    assert metadata["sha256"]["weights"]["value"] == weights_sha
    assert str(tmp_path) not in json.dumps(metadata)


def test_model_provenance_marks_unavailable_fields_instead_of_inventing_counts() -> None:
    metadata = _model_provenance(object())

    assert metadata["model_id"] is None
    assert metadata["model_seed"] is None
    assert metadata["rows"]["source_rows"] == {
        "value": None,
        "source": "unavailable",
    }
    assert metadata["rows"]["gradient_training_rows"]["value"] is None
    assert metadata["sha256"]["summary"]["source"] == "unavailable"


def test_error_summary_uses_all_candidates_and_explicit_four_feature_rms() -> None:
    public = _public_candidates([_candidate(10), _candidate(20)])
    summary = _error_summary(public, selected_q=10)

    assert public[0]["mean_absolute_percent_error"] == pytest.approx(2.5)
    assert summary["candidate_count"] == 2
    assert summary["selected_total_error_percent_rmse"] == pytest.approx(10.0)
    assert summary["mean_total_error_percent_rmse"] == pytest.approx(15.0)
    assert "four per-feature percentage errors" in str(summary["definition"])


def test_physical_qp_qs_are_displayable_only_after_exact_candidate_rebinding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "job"
    candidates = _bound_candidates()
    result = {"mode": "physical", "candidates": candidates}

    status = _attach_physical_q_components(root, result)
    public = _public_candidates(result["candidates"])

    assert status == "BOUND_11_OF_11"
    assert public[0]["q_metadata"]["reported_label"] == "Qmin"
    assert public[0]["q_metadata"]["Qp"] == pytest.approx(10.2)
    assert public[0]["q_metadata"]["Qs"] == pytest.approx(10.5)
    assert public[0]["q_metadata"]["components_status"] == "BOUND_FRESH_REAL_EMX"
    assert len(public[0]["q_metadata"]["source_sha256"]) == 64


def test_physical_q_components_fail_closed_on_s4p_qmin_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "job"
    candidates = _bound_candidates()
    candidates[0]["fresh_emx_features_15ghz"]["Qp"] = 10.7
    candidates[0]["fresh_emx_features_15ghz"]["Qmin"] = 10.7
    result = {"mode": "physical", "candidates": candidates}

    status = _attach_physical_q_components(root, result)
    public = _public_candidates(result["candidates"])

    assert status == "UNAVAILABLE_QMIN_MISMATCH"
    assert public[0]["q_metadata"]["Qp"] is None
    assert public[0]["q_metadata"]["components_status"] == "UNAVAILABLE_IN_PUBLIC_RESULT"


def test_collect_artifacts_registers_each_q_gds_inside_job_root(tmp_path: Path) -> None:
    root = tmp_path / "job"
    root.mkdir()
    candidates = []
    for q_value in range(10, 21):
        gds = root / f"demo_q{q_value:02d}.gds"
        gds.write_bytes(f"test-{q_value}".encode())
        candidates.append(_candidate(q_value, artifacts={"gds": str(gds)}))

    collected = _collect_artifacts(root, {"mode": "physical", "candidates": candidates})

    assert {key for key in collected if key.startswith("gds_q")} == {
        f"gds_q{q_value:02d}" for q_value in range(10, 21)
    }
    assert all(path.is_relative_to(root) for path in collected.values())


def test_public_record_refuses_incomplete_physical_batch_and_hides_local_paths(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "runs"
    job_root = output_root / "job"
    job_root.mkdir(parents=True)
    gds = job_root / "demo_q10.gds"
    preview = job_root / "best.png"
    gds.write_bytes(b"test")
    preview.write_bytes(b"png")
    gds_sha = hashlib.sha256(gds.read_bytes()).hexdigest()
    candidate = _candidate(
        10,
        artifacts={
            "gds": str(gds),
            "gds_sha256": gds_sha,
            "preview": str(preview),
        },
    )
    store = JobStore(
        model=object(),  # type: ignore[arg-type]
        output_root=output_root,
        mode="physical",
        physical_backend_command="unused",
    )
    job = Job(
        job_id="job",
        target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
        output_dir=job_root,
        status="SUCCEEDED",
        result={
            "mode": "physical",
            "selected_q": 10,
            "selection_score": 0.01,
            "evidence_source": "FRESH_REAL_EMX",
            "scientific_boundary": "separate evidence gates",
            "candidates": [candidate],
        },
        artifacts={"gds_q10": gds, "preview": preview},
    )
    store._jobs[job.job_id] = job

    record = store.public_record(job.job_id)
    serialized = json.dumps(record)

    assert record["gds_by_q"] == {}
    assert record["evidence_readiness"]["selection_readiness"] == (
        "PHYSICAL_EVIDENCE_NOT_READY"
    )
    assert record["evidence_readiness"]["gds_download_ready_count"] == 0
    assert record["evidence_readiness"]["calibre_drc"] == "NOT_REPORTED_BY_Q_SWEEP"
    assert record["model_provenance"]["rows"]["source_rows"]["value"] is None
    assert str(tmp_path) not in serialized
    assert "artifacts" not in record["candidates"][0]


def test_proxy_public_record_never_reports_physical_readiness(tmp_path: Path) -> None:
    output_root = tmp_path / "runs"
    job_root = output_root / "job"
    job_root.mkdir(parents=True)
    misleading_gds = job_root / "must_not_download.gds"
    misleading_gds.write_bytes(b"registered-but-proxy")
    misleading_sha = hashlib.sha256(misleading_gds.read_bytes()).hexdigest()
    candidates = []
    for q_value in range(10, 21):
        candidate = _candidate(q_value)
        candidate["evidence_source"] = "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
        if q_value == 10:
            candidate["artifacts"] = {
                "gds": str(misleading_gds),
                "gds_sha256": misleading_sha,
            }
        candidates.append(candidate)
    store = JobStore(
        model=object(),  # type: ignore[arg-type]
        output_root=output_root,
        mode="proxy",
        physical_backend_command=None,
    )
    job = Job(
        job_id="job",
        target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
        output_dir=job_root,
        status="SUCCEEDED",
        result={
            "mode": "proxy",
            "selected_q": 10,
            "evidence_source": "FROZEN_FORWARD_PROXY_DIAGNOSTIC",
            "candidates": candidates,
        },
        artifacts={"gds_q10": misleading_gds},
    )
    store._jobs[job.job_id] = job

    record = store.public_record(job.job_id)

    assert record["evidence_readiness"]["selection_readiness"] == (
        "FROZEN_FORWARD_PROXY_DIAGNOSTIC_ONLY"
    )
    assert record["evidence_readiness"]["fresh_real_emx_candidate_count"] == 0
    assert record["evidence_readiness"]["proxy_is_physical_evidence"] is False
    assert record["gds_by_q"] == {}
    assert all(
        candidate["q_metadata"]["components_status"] == "NOT_APPLICABLE_PROXY"
        for candidate in record["candidates"]
    )


def test_artifact_rejects_paths_outside_job_root(tmp_path: Path) -> None:
    output_root = tmp_path / "runs"
    job_root = output_root / "job"
    job_root.mkdir(parents=True)
    outside = tmp_path / "outside.gds"
    outside.write_bytes(b"outside")
    store = JobStore(
        model=object(),  # type: ignore[arg-type]
        output_root=output_root,
        mode="proxy",
        physical_backend_command=None,
    )
    job = Job(
        job_id="job",
        target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
        output_dir=job_root,
        artifacts={"gds_q10": outside},
    )
    store._jobs[job.job_id] = job

    with pytest.raises(PermissionError, match="escaped"):
        store.artifact(job.job_id, "gds_q10")


def test_gds_endpoint_uses_q_specific_attachment_filename_after_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "runs"
    job_root = output_root / "job"
    job_root.mkdir(parents=True)
    gds = job_root / "backend_layout.gds"
    gds.write_bytes(b"verified-upstream-gds")
    store = JobStore(
        model=object(),  # type: ignore[arg-type]
        output_root=output_root,
        mode="physical",
        physical_backend_command="unused",
    )
    job = Job(
        job_id="job",
        target=PhysicalTarget3("professor_demo", 1.15, 1.40, 0.76),
        output_dir=job_root,
        status="SUCCEEDED",
        result={
            "mode": "physical",
            "evidence_source": "FRESH_REAL_EMX",
            "physical_q_metadata_status": "BOUND_11_OF_11",
            "candidates": [
                {
                    "candidate_id": "demo_q17",
                    "q_target": 17,
                    "evidence_source": "FRESH_REAL_EMX",
                    "artifacts": {"gds": str(gds)},
                }
            ],
        },
        artifacts={"gds_q17": gds},
    )
    store._jobs[job.job_id] = job
    server = GuiServer(("127.0.0.1", 0), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        denied_connection = http.client.HTTPConnection(
            *server.server_address, timeout=5
        )
        denied_connection.request("GET", "/api/jobs/job/artifacts/gds_q17")
        denied_response = denied_connection.getresponse()
        denied_response.read()
        denied_connection.close()

        monkeypatch.setattr(
            gui_module, "_physical_batch_download_ready", lambda *_: True
        )
        monkeypatch.setattr(
            gui_module, "_physical_candidate_artifacts_intact", lambda *_: True
        )
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/api/jobs/job/artifacts/gds_q17")
        response = connection.getresponse()
        payload = response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert denied_response.status == 404
    assert response.status == 200
    assert payload == b"verified-upstream-gds"
    assert response.getheader("Content-Type") == "application/octet-stream"
    assert (
        response.getheader("Content-Disposition")
        == 'attachment; filename="professor_demo_q17.gds"'
    )
