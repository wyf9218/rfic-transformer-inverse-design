from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "docs" / "research" / "BROADBAND56_V2_GOLDEN_AUTHORIZATION_CANDIDATE_20260830.json"
REQUEST = ROOT / "docs" / "research" / "BROADBAND56_V2_GOLDEN_AUTHORIZATION_REQUEST_20260830.md"
STATUS = ROOT / "docs" / "research" / "BROADBAND56_BALANCED200K_V2_STATUS_20260828.json"
EXPECTED_CANDIDATE_SHA256 = "655a490c027a5aa96412ac982891123e17250412d90819f52d5cb8e17a082965"


def _load_module():
    path = ROOT / "scripts" / "record_broadband56_v2_golden_authorization.py"
    spec = importlib.util.spec_from_file_location("record_broadband56_v2_golden_authorization", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments(out_dir: Path, candidate: Path = CANDIDATE, candidate_sha256: str | None = None) -> list[str]:
    return [
        "--candidate",
        str(candidate),
        "--candidate-sha256",
        candidate_sha256 or _sha256(candidate),
        "--approved-by",
        "unit-test-project-owner",
        "--approved-utc",
        "2026-08-30T16:00:00Z",
        "--approval-reference",
        "explicit unit-test one-golden approval",
        "--out-dir",
        str(out_dir),
    ]


def test_frozen_candidate_sha_is_bound_by_request_and_status() -> None:
    assert _sha256(CANDIDATE) == EXPECTED_CANDIDATE_SHA256
    assert EXPECTED_CANDIDATE_SHA256 in REQUEST.read_text(encoding="utf-8")
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    assert status["golden_authorization_candidate"]["sha256"] == EXPECTED_CANDIDATE_SHA256
    assert status["golden_authorization_candidate"]["approval_status"] == (
        "PENDING_EXPLICIT_PROJECT_OWNER_SHA256_APPROVAL"
    )
    assert status["golden_authorization_candidate"]["golden_authorized"] is False


def test_records_exact_one_golden_approval_without_execution(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "approved"

    assert module.main(_arguments(out_dir)) == 0

    receipt_path = out_dir / "GOLDEN_AUTHORIZATION_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY"
    assert receipt["approved_candidate"]["sha256"] == _sha256(CANDIDATE)
    assert receipt["resource_and_license_gate_authorized"] is True
    assert receipt["one_golden_authorized"] is True
    assert receipt["cadence_authorized_for_one_golden"] is True
    assert receipt["calibre_authorized_for_one_golden"] is True
    assert receipt["emx_authorized_for_one_golden"] is True
    assert receipt["simulator_geometry_limit"] == 1
    assert receipt["pilot_32_authorized"] is False
    assert receipt["pilot_1000_authorized"] is False
    assert receipt["queue_authorized"] is False
    assert receipt["supervisor_authorized"] is False
    assert receipt["phase_a_authorized"] is False
    assert receipt["phase_b_authorized"] is False
    assert receipt["phase_c_authorized"] is False
    assert receipt["campaign_200k_authorized"] is False
    assert receipt["execution_effect"] == "NONE_RECORD_ONLY"
    assert receipt["checks"] and all(item["pass"] for item in receipt["checks"])
    assert (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8") == (
        f"{_sha256(receipt_path)}  {receipt_path.name}\n"
    )


def test_wrong_candidate_sha_writes_fail_receipt(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "wrong_sha"

    assert module.main(_arguments(out_dir, candidate_sha256="0" * 64)) == 2

    receipt = json.loads((out_dir / "GOLDEN_AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert receipt["overall_status"] == "FAIL"
    assert receipt["one_golden_authorized"] is False
    assert receipt["simulator_geometry_limit"] == 0
    assert "candidate_sha256_matches_exact_bytes" in failed


def test_tampered_scope_and_public_evidence_fail_closed(tmp_path: Path) -> None:
    module = _load_module()
    payload = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    payload["permitted_only_after_exact_sha256_approval"]["run_emx_for_that_geometry"] = False
    payload["preparation_evidence"]["public_preparation_status_sha256"] = "0" * 64
    candidate = tmp_path / "tampered_candidate.json"
    candidate.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_dir = tmp_path / "tampered"

    assert module.main(_arguments(out_dir, candidate)) == 2

    receipt = json.loads((out_dir / "GOLDEN_AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert "candidate_permitted_action_set" in failed
    assert "candidate_public_evidence_hashes" in failed
    assert receipt["one_golden_authorized"] is False


def test_invalid_human_metadata_fails_and_output_is_no_clobber(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "invalid_metadata"
    args = _arguments(out_dir)
    args[args.index("--approved-by") + 1] = "TBD"
    args[args.index("--approved-utc") + 1] = "2026-08-30T16:00:00"
    args[args.index("--approval-reference") + 1] = "UNKNOWN"

    assert module.main(args) == 2
    receipt = json.loads((out_dir / "GOLDEN_AUTHORIZATION_RECEIPT.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert {
        "approved_by_is_explicit",
        "approved_utc_is_timezone_aware",
        "approval_reference_is_explicit",
    } <= failed

    with pytest.raises(FileExistsError, match="no-clobber"):
        module.main(args)
