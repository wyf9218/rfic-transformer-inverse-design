"""Synthetic commit-metadata and read-only transport tests, not production QA."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from research.broadband56_nn import research_data_access as access
from research.broadband56_nn.io import sha256


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def _fixture(tmp_path, after=5657):
    root = tmp_path / "producer"
    constants = {"campaign_id": "SYNTHETIC", "contract_fingerprint_sha256": "science",
                 "backend_id": "backend", "backend_identity_manifest_sha256": "backend_sha",
                 "full_campaign_authorization_receipt_sha256": "authorization_sha", "stage": "PHASE_A"}
    boundary = _write(root / "stages/000001/STAGE_PROGRESS_RECEIPT.json",
                      {**constants, "accepted_after": 5000, "attempt_index": 1})
    artifacts = {}
    for name in ("accepted_geometry_increment", "long_features", "s4p_artifact_index", "exact_gds_emx_receipt_index"):
        file = root / "stages/000002" / (name + ".csv")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("SYNTHETIC_METADATA_ONLY_NOT_REAL_ROWS\n")
        artifacts[name] = {"path": str(file), "sha256": sha256(file), "size_bytes": file.stat().st_size}
    increment = {**constants, "accepted_before": 5000, "accepted_after": after,
        "accepted_this_attempt": after - 5000, "attempt_index": 2,
        "prior_progress_receipt_sha256": boundary["sha256"], "overall_status": "INCOMPLETE",
        "decision": "CONTINUE_SAMPLING", "artifacts": artifacts,
        "failure_accounting": {"accepted_geometries": after - 5000},
        "safeguards": dict.fromkeys(("accepted_blocking_calibre_count", "accepted_duplicate_geometry_count",
            "historical_label_count", "interpolated_frequency_record_count", "manual_gds_modification_count",
            "mixed_contract_fingerprint_count", "proxy_label_count"), 0)}
    progress = root / "stages/000002/STAGE_PROGRESS_RECEIPT.json"
    _write(progress, increment)
    handoff = {"schema": "b56_private_read_only_data_handoff.v1", **constants,
        "production_root": str(root), "checkpoint": {"count": 5000, "boundary_progress": boundary}}
    return handoff, progress


def _discover(handoff):
    result = subprocess.run([sys.executable, "-c", access.REMOTE_DISCOVERY],
                            input=json.dumps({"handoff": handoff}), capture_output=True, text=True)
    return result, json.loads(result.stdout) if result.returncode == 0 else None


def test_incomplete_phase_can_contain_a_real_committed_increment(tmp_path):
    handoff, progress = _fixture(tmp_path)
    before = {p: p.read_bytes() for p in Path(handoff["production_root"]).rglob("*") if p.is_file()}
    process, result = _discover(handoff)
    assert process.returncode == 0, process.stderr
    assert result["committed_accepted"] == 5657
    assert result["large_artifacts_hashed"] is False
    assert all(p.read_bytes() == content for p, content in before.items())


def test_crossing_10k_does_not_wait_for_exact_count_or_20k(tmp_path):
    handoff, _ = _fixture(tmp_path, after=10065)
    process, result = _discover(handoff)
    assert process.returncode == 0
    assert result["committed_accepted"] == 10065


@pytest.mark.parametrize("mutation", ["backend", "count", "attempt", "safeguard", "size"])
def test_invalid_commit_cannot_advance_metadata_watermark(tmp_path, mutation):
    handoff, progress = _fixture(tmp_path)
    doc = json.loads(progress.read_text())
    if mutation == "backend":
        doc["backend_id"] = "different"
    elif mutation == "count":
        doc["accepted_before"] = 4999
    elif mutation == "attempt":
        doc["attempt_index"] = 5
    elif mutation == "safeguard":
        doc["safeguards"]["proxy_label_count"] = 1
    else:
        doc["artifacts"]["long_features"]["size_bytes"] += 1
    progress.write_text(json.dumps(doc))
    process, result = _discover(handoff)
    assert process.returncode != 0
    assert result is None


def test_branched_commit_chain_is_not_arbitrated_by_newest_time(tmp_path):
    handoff, progress = _fixture(tmp_path)
    fork = progress.parent.parent / "000003/STAGE_PROGRESS_RECEIPT.json"
    fork.parent.mkdir()
    fork.write_bytes(progress.read_bytes())
    process, _ = _discover(handoff)
    assert process.returncode != 0 and "ambiguous" in process.stderr


def test_live_csv_without_commit_is_never_discovered(tmp_path):
    handoff, _ = _fixture(tmp_path)
    rolling = Path(handoff["production_root"]) / "CAMPAIGN_STATUS.json"
    rolling.write_text(json.dumps({"current_accepted": 200000}))
    process, result = _discover(handoff)
    assert process.returncode == 0
    assert result["committed_accepted"] == 5657


def test_raw_receipt_bytes_not_reserialized_and_bad_transport_rejected(tmp_path):
    raw = b'{"x": 2, "synthetic": true}\n'
    value = {"path": "/private/source.json", "raw_base64": base64.b64encode(raw).decode(),
             "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    saved = access._cache_raw(value, tmp_path)
    assert Path(saved["path"]).read_bytes() == raw
    value["size_bytes"] += 1
    with pytest.raises(ValueError, match="transport identity"):
        access._cache_raw(value, tmp_path)


def test_probe_below_threshold_does_not_download_large_data(tmp_path, monkeypatch):
    handoff, _ = _fixture(tmp_path)
    _, discovered = _discover(handoff)
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps(handoff))
    monkeypatch.setattr(access.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(discovered), ""))
    monkeypatch.setattr(access, "fetch_exact", lambda *a, **kw: pytest.fail("large file copied below10K"))
    result = access.probe_and_localize(handoff_path, sha256(handoff_path), tmp_path / "not_read_base.json",
                                      tmp_path / "cache", host="testhost", control_path="/synthetic/socket")
    assert result["status"] == "WAITING_FOR_10K"
    assert "source_manifest" not in result


def test_eligible_count_waits_before_copy_when_disk_reserve_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    handoff, _ = _fixture(tmp_path, after=10065)
    handoff["checkpoint"]["status"] = _write(tmp_path / "status.json", {"synthetic": True})
    _, discovered = _discover(handoff)
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps(handoff))
    monkeypatch.setattr(access.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(discovered), ""))
    monkeypatch.setattr(access.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024))
    monkeypatch.setattr(access, "fetch_exact", lambda *a, **kw: pytest.fail("transfer ignored resource gate"))
    result = access.probe_and_localize(handoff_path, sha256(handoff_path), tmp_path / "not_read_base.json",
                                      tmp_path / "cache", host="testhost", control_path="/synthetic/socket")
    assert result["status"] == "WAITING_RESOURCE"
    assert result["training_started"] is False and "source_manifest" not in result
