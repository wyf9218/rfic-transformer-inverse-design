from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_golden_source import (
    GoldenSourceError, SOURCE_CONTRACT, validate_safe_anchor_source,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bound_source_fixture", ROOT / "tests/test_run_broadband56_v2_bound_queue_builder.py"
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
BUILDER = FIXTURE.MODULE


@pytest.fixture(autouse=True)
def no_child_process(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("source validation must not launch any child")
    monkeypatch.setattr(subprocess, "Popen", denied)


def _bound(tmp_path):
    argv = FIXTURE._fixture(tmp_path)
    assert BUILDER.main(argv) == 0
    path = tmp_path / "out" / BUILDER.OUTPUT_NAME
    summary = json.loads(path.read_text())
    kwargs = {
        "stage": "GOLDEN",
        "geometry_sha256": summary["safe_anchor_geometry_sha256"],
        "config_sha256": summary["corrected_private_configuration"]["sha256"],
        "contract_fingerprint": BUILDER.CONTRACT_FINGERPRINT,
    }
    return path, summary, kwargs


def test_source_is_valid_for_validation_but_not_production_acceptance(tmp_path):
    path, summary, kwargs = _bound(tmp_path)
    result = validate_safe_anchor_source(BUILDER._file_record(path), **kwargs)
    assert result["source_contract"] == SOURCE_CONTRACT
    assert result["source_contract"]["stage_gate_validation_authorized"] is True
    assert result["golden_stage_gate_eligible"] is False
    assert result["eligibility_status"] == "PENDING_FRESH_EMX_AND_EXACT56_QA"
    assert result["production_dataset_accepted_eligible"] is False
    assert result["production_accepted_count_delta"] == 0


@pytest.mark.parametrize("change", ["stage", "geometry_sha256", "config_sha256", "contract_fingerprint"])
def test_wrong_stage_or_bound_identity_rejected(tmp_path, change):
    path, summary, kwargs = _bound(tmp_path)
    kwargs[change] = "PILOT_32" if change == "stage" else "f" * 64
    with pytest.raises(GoldenSourceError):
        validate_safe_anchor_source(BUILDER._file_record(path), **kwargs)


@pytest.mark.parametrize("record_key", [
    "safe_anchor_source_receipt", "corrected_private_configuration",
    "corrected_foundry_layout_approval_receipt", "candidate_queue",
])
def test_bound_source_artifact_drift_rejected(tmp_path, record_key):
    path, summary, kwargs = _bound(tmp_path)
    artifact = Path(summary[record_key]["path"])
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    with pytest.raises(GoldenSourceError):
        validate_safe_anchor_source(BUILDER._file_record(path), **kwargs)


@pytest.mark.parametrize("mutation", ["unknown_source", "count_duplicate", "drop_contract"])
def test_unknown_source_and_forged_acceptance_never_become_pass(tmp_path, mutation):
    path, summary, kwargs = _bound(tmp_path)
    if mutation == "count_duplicate":
        summary["source_contract"]["production_accepted_count_delta"] = 1
        summary["source_contract"]["production_dataset_accepted_eligible"] = True
    elif mutation == "drop_contract":
        del summary["source_contract"]
    else:
        queue = Path(summary["candidate_queue"]["path"])
        with queue.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["acquisition_source"] = "base_space_filling"
        with queue.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        summary["candidate_queue"] = BUILDER._file_record(queue)
    path.write_text(json.dumps(summary))
    with pytest.raises(GoldenSourceError):
        validate_safe_anchor_source(BUILDER._file_record(path), **kwargs)


def test_producer_rejects_unknown_source_before_materialization(tmp_path):
    argv = FIXTURE._fixture(tmp_path)
    queue = Path(argv[argv.index("--safe-anchor-queue") + 1])
    queue.write_text(queue.read_text().replace(BUILDER.SAFE_ANCHOR_SOURCE, "unknown_source"))
    argv[argv.index("--safe-anchor-queue-sha256") + 1] = BUILDER._sha256(queue)
    assert BUILDER.main(argv) == 2
    assert not (tmp_path / "out").exists()
