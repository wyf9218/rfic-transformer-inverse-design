from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "record_broadband56_corrected_foundry_layout_authorization.py"
SPEC = importlib.util.spec_from_file_location("corrected_authorization_recorder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

BUILDER_SCRIPT = ROOT / "scripts" / "build_broadband56_corrected_foundry_layout_candidate.py"
BUILDER_SPEC = importlib.util.spec_from_file_location("corrected_candidate_builder", BUILDER_SCRIPT)
assert BUILDER_SPEC and BUILDER_SPEC.loader
BUILDER = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(BUILDER)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(path: Path, content: str) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": _sha(path)}


def _candidate(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    previous = _record(tmp_path / "previous.yaml", "previous\n")
    corrected = _record(tmp_path / "corrected.yaml", "corrected\n")
    diff = _record(tmp_path / "diff.json", "{}\n")
    deck = _record(tmp_path / "deck.rule", "deck\n")
    full = _record(tmp_path / "full.json", "{}\n")
    private = _record(tmp_path / "private.json", "{}\n")
    runtime = {
        "path": str((tmp_path / "runtime").resolve()),
        "head_commit": BUILDER.EXACT_PUBLIC_CODE_COMMIT,
        "tree_sha1": "1" * 40,
        "working_tree_clean": True,
    }
    candidate = {
        "schema": BUILDER.CANDIDATE_SCHEMA,
        "campaign_id": BUILDER.CAMPAIGN_ID,
        "scientific_contract_fingerprint_sha256": BUILDER.SCIENTIFIC_CONTRACT_FINGERPRINT,
        "approval_status": "PENDING_EXPLICIT_PROJECT_OWNER_SHA256_APPROVAL",
        "requested_authorization_scope": BUILDER.REQUESTED_AUTHORIZATION_SCOPE,
        "candidate_file_authorizes_execution": False,
        "simulator_action_taken": False,
        "exact_required_public_code_commit": BUILDER.EXACT_PUBLIC_CODE_COMMIT,
        "corrected_public_runtime": runtime,
        "previous_private_configuration": previous,
        "corrected_private_configuration": corrected,
        "configuration_diff": diff,
        "corrected_foundry_layout_contract": BUILDER.EXPECTED_FOUNDRY_LAYOUT,
        "frequency_contract": BUILDER.expected_frequency_contract(),
        "geometry_contract": BUILDER.expected_geometry_contract(),
        "port_and_grounding_contract": BUILDER.PORT_AND_GROUNDING_CONTRACT,
        "calibre_rule_deck_identity": {
            **deck,
            "sha256": BUILDER.EXPECTED_CALIBRE_RULE_DECK_SHA256,
        },
        "unchanged_contract": {
            "overall_status": "PASS",
            "drc_rule_change": False,
            "geometry_bounds_change": False,
            "frequency_contract_change": False,
        },
        "generated_layout_audit_contract": {
            "requirements": [
                {"name": name, "required": True, "current_result": "NOT_RUN"}
                for name in BUILDER.GENERATED_LAYOUT_AUDIT_REQUIREMENTS
            ]
        },
        "existing_full_campaign_authorization": full,
        "private_evidence": {"fixture": private},
        "controller_invariant": {
            "controller_count": 1,
            "authoritative_controller_pid": BUILDER.EXPECTED_CONTROLLER_PID,
            "project_active_simulator_count": 0,
        },
    }
    # The production deck digest is immutable; make the fixture bytes match it
    # by replacing the record verification with the real expected identity.
    candidate["calibre_rule_deck_identity"] = {
        "path": str((tmp_path / "deck.rule").resolve()),
        "size_bytes": (tmp_path / "deck.rule").stat().st_size,
        "sha256": BUILDER.EXPECTED_CALIBRE_RULE_DECK_SHA256,
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate, sort_keys=True) + "\n", encoding="utf-8")
    return path, runtime


def _args(candidate: Path, out_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        candidate=str(candidate),
        candidate_sha256=_sha(candidate),
        approved_by=MODULE.EXPECTED_APPROVED_BY,
        approved_utc="2026-09-02T15:00:00+00:00",
        approval_reference="current Codex-thread exact-SHA approval",
        out_dir=str(out_dir),
    )


def _patch_live(
    monkeypatch: pytest.MonkeyPatch,
    runtime: dict[str, object],
) -> None:
    monkeypatch.setattr(MODULE, "_git_snapshot", lambda _path: runtime)
    monkeypatch.setattr(
        MODULE,
        "_live_controller_snapshot",
        lambda _pid: {
            "authoritative_controller_pid": BUILDER.EXPECTED_CONTROLLER_PID,
            "authoritative_controller_alive": True,
            "authoritative_controller_state": "T",
            "controller_count": 1,
            "project_active_simulator_count": 0,
        },
    )
    original = MODULE._verify_file_record

    def verify(value):
        if value.get("sha256") == BUILDER.EXPECTED_CALIBRE_RULE_DECK_SHA256:
            path = Path(value["path"])
            return (
                {"path": str(path), "size_bytes": path.stat().st_size, "sha256": value["sha256"]},
                True,
                "fixture immutable deck identity",
            )
        return original(value)

    monkeypatch.setattr(MODULE, "_verify_file_record", verify)


def test_records_exact_candidate_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, runtime = _candidate(tmp_path)
    _patch_live(monkeypatch, runtime)
    receipt = MODULE.record_authorization(_args(candidate, tmp_path / "out"), candidate_path=candidate)
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == MODULE.APPROVAL_DECISION
    assert receipt["one_corrected_rescue_golden_authorized"] is True
    assert receipt["automatic_post_golden_full_campaign_continuation_authorized"] is True
    assert receipt["nn_training_authorized"] is False
    assert receipt["simulator_action_taken_by_recorder"] is False


def test_wrong_sha_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, runtime = _candidate(tmp_path)
    _patch_live(monkeypatch, runtime)
    args = _args(candidate, tmp_path / "out")
    args.candidate_sha256 = "0" * 64
    receipt = MODULE.record_authorization(args, candidate_path=candidate)
    assert receipt["overall_status"] == "FAIL"
    assert receipt["one_corrected_rescue_golden_authorized"] is False


def test_no_clobber_rejected(tmp_path: Path) -> None:
    candidate, _runtime = _candidate(tmp_path)
    out_dir = tmp_path / "existing"
    out_dir.mkdir()
    assert MODULE.main([
        "--candidate", str(candidate),
        "--candidate-sha256", _sha(candidate),
        "--approved-by", MODULE.EXPECTED_APPROVED_BY,
        "--approved-utc", "2026-09-02T15:00:00+00:00",
        "--approval-reference", "current Codex-thread exact-SHA approval",
        "--out-dir", str(out_dir),
    ]) == 2
