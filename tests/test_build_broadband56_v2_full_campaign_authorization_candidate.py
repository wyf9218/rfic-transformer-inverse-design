from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    PUBLIC_EVIDENCE_FIELDS,
    validate_full_campaign_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_v2_full_campaign_authorization_candidate.py"
SPEC = importlib.util.spec_from_file_location("full_campaign_candidate_builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(MODULE, "ROOT", repo)
    for index, relative in enumerate(PUBLIC_EVIDENCE_FIELDS.values()):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"evidence-{index}\n", encoding="utf-8")
    hash_args = [
        "preparation-receipt",
        "private-configuration",
        "historical-configuration",
        "campaign-contract-frozen",
        "primary-bins-frozen",
        "secondary-coverage-frozen",
        "geometry-bounds-frozen",
        "phase-plan-frozen",
        "operational-policy-approval-receipt",
        "backend-identity-manifest",
        "backend-identity-verification-receipt",
        "queue-controller",
        "stage-launcher",
        "production-stage-backend",
        "phase-a-queue-builder",
        "adaptive-candidate-pool-builder",
        "acquisition-ensemble-trainer",
        "acquisition-predictor",
        "adaptive-candidate-selector",
        "adaptive-round-stager",
        "cadence-streamout-runner",
        "candidate-gds-index-builder",
        "gds-physical-identity-auditor",
        "gds-physical-identity-module",
        "calibre-runner",
        "calibre-zero-blocking-receipt-builder",
        "exact-audited-gds-emx-runner",
        "exact-audited-gds-emx-module",
        "full-band-s4p-qa-builder",
        "full-band-s4p-qa-module",
        "raw-products-finalizer",
        "checkpoint-auditor",
        "campaign-histories-finalizer",
        "training-readiness-finalizer",
        "final-delivery-auditor",
        "historical-gds-identity-pass-receipt",
    ]
    argv = [
        "--out-dir",
        str(tmp_path / "candidate"),
        "--generated-utc",
        "2026-08-30T20:30:00Z",
    ]
    for index, name in enumerate(hash_args, start=1):
        argv.extend([f"--{name}-sha256", f"{index:064x}"])
    argv.extend(
        [
            "--historical-backend-pass-receipt",
            f"{'a' * 64}:2589",
            "--historical-backend-pass-receipt",
            f"{'b' * 64}:3272",
        ]
    )
    return argv


def test_builds_public_safe_exact_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)

    assert MODULE.main(argv) == 0

    out_dir = tmp_path / "candidate"
    paths = list(out_dir.glob("BROADBAND56_V2_FULL_CAMPAIGN_AUTHORIZATION_CANDIDATE_*.json"))
    assert len(paths) == 1
    candidate = json.loads(paths[0].read_text())
    assert not validate_full_campaign_candidate(candidate, repository_root=MODULE.ROOT)
    assert candidate["execution_effect_of_candidate_file"] == "NONE_REQUEST_ONLY"
    assert candidate["automatic_campaign_execution_authorized"] is False
    runtime = candidate["runtime_and_backend_identity"]
    assert runtime["candidate_gds_index_builder_sha256"]
    assert runtime["gds_physical_identity_auditor_sha256"]
    assert runtime["gds_physical_identity_module_sha256"]
    serialized = json.dumps(candidate)
    assert "/volumes/" not in serialized
    assert _sha(paths[0]) in (out_dir / "SHA256SUMS.txt").read_text()


def test_rejects_existing_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    (tmp_path / "candidate").mkdir()

    assert MODULE.main(argv) == 2


def test_rejects_too_few_historical_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    marker = argv.index("--historical-backend-pass-receipt")
    del argv[marker : marker + 2]

    assert MODULE.main(argv) == 2


def test_rejects_non_timezone_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    argv[argv.index("--generated-utc") + 1] = "2026-08-30T20:30:00"

    assert MODULE.main(argv) == 2
