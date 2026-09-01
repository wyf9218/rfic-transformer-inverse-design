from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import CAMPAIGN_ID
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    PRODUCTION_BACKEND_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (
    ATTEMPT_FAILURE_ACCOUNTING_FIELDS,
    STAGE_PROGRESS_ARTIFACT_FIELDS,
    STAGE_PROGRESS_DECISION,
    STAGE_PROGRESS_SCHEMA,
    STAGE_PROGRESS_SAFEGUARDS,
    STAGE_PROGRESS_STATUS,
    accepted_after_progress,
    validate_stage_progress_chain,
    validate_stage_progress_receipt,
)


BACKEND_SHA = "1" * 64
AUTHORIZATION_SHA = "2" * 64


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _receipt(
    root: Path,
    *,
    attempt_index: int,
    before: int,
    accepted: int,
    raw: int,
    prior_sha: str | None,
    stage: str = "PILOT_32",
    cumulative_target: int = 32,
) -> tuple[Path, dict[str, object]]:
    root.mkdir(parents=True)
    artifacts = {}
    for role in STAGE_PROGRESS_ARTIFACT_FIELDS:
        path = root / f"{role}.txt"
        path.write_text(f"{role} attempt {attempt_index}\n", encoding="utf-8")
        artifacts[role] = _identity(path)
    funnel = {field: 0 for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS}
    funnel["raw_geometry_candidates"] = raw
    funnel["accepted_geometries"] = accepted
    funnel["analytical_failures"] = raw - accepted
    after = before + accepted
    receipt = {
        "schema": STAGE_PROGRESS_SCHEMA,
        "overall_status": STAGE_PROGRESS_STATUS,
        "decision": STAGE_PROGRESS_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "attempt_index": attempt_index,
        "cumulative_target": cumulative_target,
        "accepted_before": before,
        "accepted_this_attempt": accepted,
        "accepted_after": after,
        "remaining_after": cumulative_target - after,
        "raw_candidates_this_attempt": raw,
        "terminal_attempts_this_attempt": raw,
        "prior_progress_receipt_sha256": prior_sha,
        "backend_identity_manifest_sha256": BACKEND_SHA,
        "full_campaign_authorization_receipt_sha256": AUTHORIZATION_SHA,
        "safeguards": dict(STAGE_PROGRESS_SAFEGUARDS),
        "failure_accounting": funnel,
        "artifacts": artifacts,
        "round_cumulative_inputs": None,
        "simulator_action_taken": False,
        "stage_pass_receipt_created": False,
        "evidence_preserved": True,
    }
    receipt_path = root / "STAGE_PROGRESS_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return receipt_path, receipt


def test_valid_progress_chain_is_sha_linked_and_advances_only_current_count(
    tmp_path: Path,
) -> None:
    first_path, first = _receipt(
        tmp_path / "attempt_1",
        attempt_index=1,
        before=1,
        accepted=20,
        raw=24,
        prior_sha=None,
    )
    second_path, second = _receipt(
        tmp_path / "attempt_2",
        attempt_index=2,
        before=21,
        accepted=8,
        raw=10,
        prior_sha=_sha(first_path),
    )
    records = [(first_path, first), (second_path, second)]

    assert validate_stage_progress_chain(
        records,
        stage="PILOT_32",
        base_accepted=1,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
    ) == []
    assert accepted_after_progress(records, base_accepted=1) == 29


def test_progress_receipt_rejects_stage_completion_or_overshoot(tmp_path: Path) -> None:
    path, receipt = _receipt(
        tmp_path / "attempt",
        attempt_index=1,
        before=1,
        accepted=31,
        raw=31,
        prior_sha=None,
    )

    errors = validate_stage_progress_receipt(
        receipt,
        stage="PILOT_32",
        attempt_index=1,
        accepted_before=1,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    )

    assert "progress receipt must remain strictly below the stage target" in errors


def test_progress_chain_rejects_changed_prior_receipt_bytes(tmp_path: Path) -> None:
    first_path, first = _receipt(
        tmp_path / "attempt_1",
        attempt_index=1,
        before=1,
        accepted=10,
        raw=12,
        prior_sha=None,
    )
    second_path, second = _receipt(
        tmp_path / "attempt_2",
        attempt_index=2,
        before=11,
        accepted=10,
        raw=12,
        prior_sha=_sha(first_path),
    )
    first_path.write_text(first_path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    errors = validate_stage_progress_chain(
        [(first_path, first), (second_path, second)],
        stage="PILOT_32",
        base_accepted=1,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
    )

    assert any("prior_progress_receipt_sha256 mismatch" in error for error in errors)


def test_progress_receipt_rejects_artifact_escape(tmp_path: Path) -> None:
    path, receipt = _receipt(
        tmp_path / "attempt",
        attempt_index=1,
        before=1,
        accepted=5,
        raw=8,
        prior_sha=None,
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    receipt["artifacts"]["attempt_ledger"] = _identity(outside)

    errors = validate_stage_progress_receipt(
        receipt,
        stage="PILOT_32",
        attempt_index=1,
        accepted_before=1,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    )

    assert "artifacts.attempt_ledger.path escapes the progress output root" in errors


def test_frozen_boundary_requires_exact_cumulative_inputs(tmp_path: Path) -> None:
    path, receipt = _receipt(
        tmp_path / "attempt",
        attempt_index=1,
        before=50_000,
        accepted=5_000,
        raw=5_000,
        prior_sha=None,
        stage="PHASE_B",
        cumulative_target=150_000,
    )

    errors = validate_stage_progress_receipt(
        receipt,
        stage="PHASE_B",
        attempt_index=1,
        accepted_before=50_000,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    )
    assert "round_cumulative_inputs must be an object" in errors

    receipt["round_cumulative_inputs"] = receipt["artifacts"]
    assert validate_stage_progress_receipt(
        receipt,
        stage="PHASE_B",
        attempt_index=1,
        accepted_before=50_000,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    ) == []


def test_mid_shard_rejects_cumulative_inputs(tmp_path: Path) -> None:
    path, receipt = _receipt(
        tmp_path / "attempt",
        attempt_index=1,
        before=50_000,
        accepted=4_900,
        raw=5_000,
        prior_sha=None,
        stage="PHASE_B",
        cumulative_target=150_000,
    )
    receipt["round_cumulative_inputs"] = receipt["artifacts"]

    errors = validate_stage_progress_receipt(
        receipt,
        stage="PHASE_B",
        attempt_index=1,
        accepted_before=50_000,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    )

    assert (
        "round_cumulative_inputs is allowed only at a frozen intermediate accepted boundary"
        in errors
    )


def test_nonadaptive_100_boundary_requires_cumulative_inputs(tmp_path: Path) -> None:
    path, receipt = _receipt(
        tmp_path / "attempt",
        attempt_index=1,
        before=32,
        accepted=68,
        raw=70,
        prior_sha=None,
        stage="PILOT_1000",
        cumulative_target=1_000,
    )

    errors = validate_stage_progress_receipt(
        receipt,
        stage="PILOT_1000",
        attempt_index=1,
        accepted_before=32,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    )
    assert "round_cumulative_inputs must be an object" in errors

    receipt["round_cumulative_inputs"] = receipt["artifacts"]
    assert validate_stage_progress_receipt(
        receipt,
        stage="PILOT_1000",
        attempt_index=1,
        accepted_before=32,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=BACKEND_SHA,
        authorization_receipt_sha256=AUTHORIZATION_SHA,
        verify_artifacts=True,
        artifact_root=path.parent,
    ) == []
