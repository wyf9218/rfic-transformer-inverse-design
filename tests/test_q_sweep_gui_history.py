from __future__ import annotations

import hashlib
import http.client
import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.synthesis import model_adapter
from rfic_transformer_inverse_design.synthesis import q_sweep_gui as gui
from rfic_transformer_inverse_design.synthesis.q_sweep import (
    LAYOUT_EVIDENCE_CLASS, PhysicalTarget3,
)


def model(name: str = "original") -> SimpleNamespace:
    return SimpleNamespace(model_id=name, model_seed=1, target_frequency_ghz=15.0,
                           contract={}, summary={})


def store(root: Path, *, name: str = "original", mode: str = "proxy") -> gui.JobStore:
    return gui.JobStore(model=model(name), output_root=root, mode=mode,
                        physical_backend_command=None)


def candidate(q: int) -> dict:
    target = {"Lp_nH": 1.0, "Ls_nH": 1.2, "Q_scalar": float(q), "K_abs": 0.7}
    return {
        "candidate_id": f"demo_q{q:02d}", "q_target": q,
        "target_features": target, "observed_features": dict(target),
        "geometry_um": {"line_width_um": 5.0}, "geometry_sha256": "a" * 64,
        "per_feature_abs_error": {name: 0.0 for name in target},
        "per_feature_percent_error": {name: 0.0 for name in target},
        "declared_range_normalized_rmse": 0.0, "relative_percent_rmse": 0.0,
        "evidence_source": "FROZEN_FORWARD_PROXY_DIAGNOSTIC", "artifacts": {},
    }


def result() -> dict:
    return {"mode": "proxy", "design_id": "demo", "selected_q": 10,
            "selection_score": 0.0,
            "target_three_features": {"Lp_nH": 1.0, "Ls_nH": 1.2, "K_abs": 0.7},
            "evidence_source": "FROZEN_FORWARD_PROXY_DIAGNOSTIC",
            "candidates": [candidate(q) for q in range(10, 21)],
            "selected_artifacts": {}}


def disable_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gui.threading, "Thread", lambda **_: SimpleNamespace(start=lambda: None))


def test_task_creation_preserves_no_clobber_and_freezes_model_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_workers(monkeypatch)
    jobs = store(tmp_path / "runs")
    original = jobs.model
    created = jobs.create({"design_id": "demo", "lp_nh": 1, "ls_nh": 1.2, "k_abs": 0.7})
    job = jobs._jobs[created["job_id"]]
    assert not job.output_dir.exists()
    assert (jobs._metadata_root / f"{job.job_id}.json").is_file()
    jobs.model = model("replacement")
    jobs.model_provenance["model_id"] = "replacement"

    def fake_execute(**kwargs):
        assert kwargs["model"] is original
        assert kwargs["mode"] == "proxy"
        kwargs["output_dir"].mkdir(exist_ok=False)
        (kwargs["output_dir"] / "proxy_candidates.json").write_text(json.dumps(result()))
        return SimpleNamespace(record=result)

    monkeypatch.setattr(gui, "execute_q_sweep", fake_execute)
    jobs._run(job.job_id)
    restored = store(jobs.output_root, name="new-on-restart", mode="physical")
    record = restored.public_record(job.job_id)
    assert record["status"] == "SUCCEEDED"
    assert record["mode"] == "proxy"
    assert record["model_provenance"]["model_id"] == "original"
    assert record["completed_candidate_count"] == 11
    assert record["progress"]["proxy_candidates_completed"] == 11
    assert record["history_restored"] is True
    assert record["elapsed_seconds"] >= 0
    assert record["candidates"][0]["geometry_um"] == {"line_width_um": 5.0}
    assert record["candidates"][0]["proxy_features_from_same_run"] == candidate(10)["observed_features"]
    assert record["candidates"][0]["proxy_features_source_sha256"] == hashlib.sha256(
        (job.output_dir / "proxy_candidates.json").read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("previous_status", ["QUEUED", "RUNNING"])
def test_restart_never_relaunches_unfinished_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_status: str,
) -> None:
    disable_workers(monkeypatch)
    jobs = store(tmp_path / "runs")
    created = jobs.create({"design_id": "demo", "lp_nh": 1, "ls_nh": 1.2, "k_abs": 0.7})
    job = jobs._jobs[created["job_id"]]
    job.status = previous_status
    job.started_utc = "2026-09-06T01:00:00+00:00"
    jobs._persist(job)
    original_record = (jobs._metadata_root / f"{job.job_id}.json").read_bytes()
    monkeypatch.setattr(gui, "execute_q_sweep", lambda **_: pytest.fail("history launched computation"))
    restored = store(jobs.output_root, name="new-model")
    record = restored.public_record(job.job_id)
    assert record["status"] == "INTERRUPTED"
    assert record["completed_utc"] is None
    assert record["elapsed_seconds"] is None
    assert record["model_provenance"]["model_id"] == "original"
    assert not job.output_dir.exists()
    assert (jobs._metadata_root / f"{job.job_id}.json").read_bytes() == original_record


def test_legacy_terminal_is_read_only_and_never_uses_current_model(tmp_path: Path) -> None:
    root = tmp_path / "runs" / "legacy_1"
    root.mkdir(parents=True)
    (root / "selection.json").write_text(json.dumps(result()))
    (root / "run_manifest.json").write_text(json.dumps({
        "schema": "rfic_q_sweep_run_manifest.v1", "overall_status": "PASS", "mode": "proxy",
        "started_utc": "2026-08-24T01:00:00+00:00", "completed_utc": "2026-08-24T01:00:02+00:00",
        "model_id": "historical-model", "model_seed": 12,
    }))
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    jobs = store(root.parent, name="current-model")
    record = jobs.public_record("legacy_1")
    assert record["model_provenance"]["model_id"] == "historical-model"
    assert record["model_provenance"]["runtime_checkpoint_status"] == "HISTORICAL_MANIFEST_ONLY_NOT_RELOADED"
    assert record["elapsed_seconds"] == 2
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_corrupt_history_is_visible_and_does_not_hide_valid_jobs(tmp_path: Path) -> None:
    jobs = store(tmp_path / "runs")
    (jobs._metadata_root / "broken.json").write_text("not JSON")
    restored = store(jobs.output_root)
    listing = restored.list_jobs()
    assert listing["jobs"] == []
    assert listing["history_warnings"] == ["broken.json: JSONDecodeError"]


def test_failed_model_import_keeps_current_model_and_active_job_blocks_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = store(tmp_path / "runs")
    original = jobs.model
    monkeypatch.setattr(model_adapter, "load_model", lambda *_, **__: (_ for _ in ()).throw(ValueError("bad hash")))
    with pytest.raises(ValueError, match="bad hash"):
        jobs.switch_model({"model_dir": str(tmp_path / "model"), "profile": "custom"})
    assert jobs.model is original
    disable_workers(monkeypatch)
    created = jobs.create({"design_id": "demo", "lp_nh": 1, "ls_nh": 1.2, "k_abs": 0.7})
    with pytest.raises(RuntimeError, match="正在运行"):
        jobs.switch_model({"loaded_model_key": jobs._active_model_key})
    assert jobs._jobs[created["job_id"]].model is original


def test_verified_model_can_switch_and_roll_back_without_losing_old_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = store(tmp_path / "runs")
    original = jobs.model
    original_key = jobs._active_model_key
    replacement = model("replacement")
    monkeypatch.setattr(model_adapter, "load_model", lambda *_, **__: replacement)
    monkeypatch.setattr(gui, "_model_provenance", lambda *_: {
        "model_id": "replacement", "runtime_checkpoint_status": "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED",
    })
    imported = jobs.switch_model({"model_dir": "/private/full/model", "profile": "custom"})
    assert jobs.model is replacement
    assert len(imported["loaded_models"]) == 2
    assert "/private/" not in json.dumps(imported)
    rolled_back = jobs.switch_model({"loaded_model_key": original_key})
    assert jobs.model is original
    assert rolled_back["model_provenance"]["model_id"] == "original"


def layout_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[gui.JobStore, gui.Job]:
    jobs = store(tmp_path / "runs", mode="physical")
    root = jobs.output_root / "layout_job"
    root.mkdir()
    payload = result()
    payload.update(mode="layout", evidence_source=LAYOUT_EVIDENCE_CLASS)
    artifacts = {}
    for item in payload["candidates"]:
        q = item["q_target"]
        path = root / f"q{q}.gds"
        path.write_bytes(f"synthetic-bound-gds-{q}".encode())
        item["evidence_source"] = LAYOUT_EVIDENCE_CLASS
        item["artifacts"] = {"gds": str(path), "gds_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "layout_evidence_class": LAYOUT_EVIDENCE_CLASS}
        artifacts[f"gds_q{q:02d}"] = path
    job = gui.Job(job_id="layout_job", target=PhysicalTarget3("demo", 1, 1.2, 0.7),
                  output_dir=root, mode="layout", status="SUCCEEDED", result=payload,
                  artifacts=artifacts, model_provenance={"model_id": "saved-layout-model"})
    jobs._jobs[job.job_id] = job
    # The unit under test is aggregation after the unchanged physical/layout gate.
    # Upstream receipt and GDS-parser tests independently cover those validators.
    monkeypatch.setattr(gui, "_layout_batch_download_ready", lambda *_: True)
    monkeypatch.setattr(gui, "_layout_candidate_artifacts_intact", lambda *_: True)
    monkeypatch.setattr(gui, "_physical_batch_download_ready", lambda *_: pytest.fail("wrong job mode"))
    return jobs, job


def test_bundle_rechecks_every_file_and_does_not_write_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs, job = layout_job(tmp_path, monkeypatch)
    before = {path.name: path.read_bytes() for path in job.output_dir.iterdir()}
    payload, name = jobs.gds_bundle(job.job_id)
    assert name == "demo_q10-20_gds.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {f"demo_q{q:02d}.gds" for q in range(10, 21)}
        assert archive.read("demo_q17.gds") == b"synthetic-bound-gds-17"
    assert {path.name: path.read_bytes() for path in job.output_dir.iterdir()} == before
    job.artifacts["gds_q17"].write_bytes(b"changed-after-display")
    with pytest.raises(PermissionError, match="证据门"):
        jobs.gds_bundle(job.job_id)


def test_zip_and_history_http_routes_use_real_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs, job = layout_job(tmp_path, monkeypatch)
    server = gui.GuiServer(("127.0.0.1", 0), jobs)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/api/jobs")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["jobs"][0]["job_id"] == job.job_id
        connection.request("GET", f"/api/jobs/{job.job_id}/artifacts/gds_zip")
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "application/zip"
        assert "demo_q10-20_gds.zip" in response.getheader("Content-Disposition")
        with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
            assert len(archive.namelist()) == 11
        # Stored selected aliases cannot bypass the candidate-specific gate.
        job.artifacts["gds"] = job.artifacts["gds_q10"]
        job.artifacts["s4p"] = job.artifacts["gds_q10"]
        for alias in ("gds", "s4p"):
            connection.request("GET", f"/api/jobs/{job.job_id}/artifacts/{alias}")
            response = connection.getresponse()
            assert response.status == 404
            response.read()
        job.status = "FAILED"
        connection.request("GET", f"/api/jobs/{job.job_id}/artifacts/gds_q10")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_progress_counts_observed_files_without_claiming_candidate_validation(tmp_path: Path) -> None:
    root = tmp_path / "running"
    backend = root / "physical_backend"
    backend.mkdir(parents=True)
    (root / "proxy_candidates.json").write_text(json.dumps(result()))
    (backend / "first.gds").write_bytes(b"incomplete")
    (backend / "first.s4p").write_text("partial output")
    job = gui.Job("running", PhysicalTarget3("demo", 1, 1.2, 0.7), root,
                  status="RUNNING", mode="physical")
    progress = gui._job_progress(job, "physical")
    assert progress["stage"] == "physical_backend"
    assert progress["proxy_candidates_completed"] == 11
    assert progress["gds_files_observed"] == 1
    assert progress["s4p_files_observed"] == 1
    assert "not validated candidate counts" in progress["evidence_basis"]


def test_proxy_comparison_requires_saved_hash_and_exact_candidate_binding(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    path = root / "proxy_candidates.json"
    path.write_text(json.dumps(result()))
    job = gui.Job("run", PhysicalTarget3("demo", 1, 1.2, 0.7), root,
                  status="SUCCEEDED", mode="physical")
    rows = result()["candidates"]
    assert gui._same_run_proxy_features(job, rows) == {}
    job.proxy_candidates_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(gui._same_run_proxy_features(job, rows)) == 11
    rows[0]["target_features"]["Q_scalar"] = 99
    assert gui._same_run_proxy_features(job, rows) == {}
    path.write_text(json.dumps(result()) + "\n")
    assert gui._same_run_proxy_features(job, result()["candidates"]) == {}


@pytest.mark.parametrize("failure", [KeyError("missing model_id"), TypeError("bad contract")])
def test_invalid_model_http_import_returns_400_and_keeps_previous_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    jobs = store(tmp_path / "runs")
    previous = jobs.model

    def invalid_load(*_, **__):
        raise failure

    monkeypatch.setattr(model_adapter, "load_model", invalid_load)
    server = gui.GuiServer(("127.0.0.1", 0), jobs)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("POST", "/api/model", json.dumps({"model_dir": "invalid", "profile": "custom"}),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["error"]
        assert jobs.model is previous
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
