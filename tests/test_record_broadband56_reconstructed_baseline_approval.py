from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "docs" / "research" / "BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_20260829.json"


def _load_module():
    path = ROOT / "scripts" / "record_broadband56_reconstructed_baseline_approval.py"
    spec = importlib.util.spec_from_file_location("record_broadband56_reconstructed_baseline_approval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments(out_dir: Path, *, candidate_sha256: str | None = None) -> list[str]:
    return [
        "--candidate-contract",
        str(CANDIDATE),
        "--candidate-sha256",
        candidate_sha256 or _sha256(CANDIDATE),
        "--approved-by",
        "unit-test-project-owner",
        "--approved-utc",
        "2026-08-29T14:00:00Z",
        "--approval-reference",
        "explicit unit-test approval reference",
        "--out-dir",
        str(out_dir),
    ]


def test_records_exact_preparation_only_approval(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "approved"

    assert module.main(_arguments(out_dir)) == 0

    receipt_path = out_dir / "RECONSTRUCTED_BASELINE_APPROVAL_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "APPROVE_V2_PREPARATION_PREFLIGHT_ONLY"
    assert receipt["approved_contract"]["sha256"] == _sha256(CANDIDATE)
    assert receipt["preparation_preflight_authorized"] is True
    assert receipt["automatic_command_authorized"] is False
    assert receipt["golden_authorized"] is False
    assert receipt["simulator_authorized"] is False
    assert receipt["execution_effect"] == "NONE_RECORD_ONLY"
    assert (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8") == (
        f"{_sha256(receipt_path)}  {receipt_path.name}\n"
    )

    prepare = importlib.util.module_from_spec(
        spec := importlib.util.spec_from_file_location(
            "prepare_broadband56_for_approval_test",
            ROOT / "scripts" / "prepare_broadband56_balanced200k_campaign.py",
        )
    )
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = prepare
    spec.loader.exec_module(prepare)
    checks: list[dict[str, object]] = []
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    prepare._validate_reconstructed_baseline_approval(checks, receipt, candidate, CANDIDATE)
    assert checks and all(item["pass"] for item in checks)


def test_wrong_candidate_sha_writes_fail_receipt(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "wrong_sha"

    assert module.main(_arguments(out_dir, candidate_sha256="0" * 64)) == 2

    receipt = json.loads((out_dir / "RECONSTRUCTED_BASELINE_APPROVAL_RECEIPT.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert receipt["overall_status"] == "FAIL"
    assert receipt["decision"] == "DO_NOT_AUTHORIZE_RECONSTRUCTED_BASELINE"
    assert receipt["preparation_preflight_authorized"] is False
    assert "candidate_sha256_matches_exact_bytes" in failed


def test_invalid_human_metadata_fails_and_output_is_no_clobber(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "invalid_metadata"
    args = _arguments(out_dir)
    args[args.index("--approved-by") + 1] = "TBD"
    args[args.index("--approved-utc") + 1] = "2026-08-29T14:00:00"
    args[args.index("--approval-reference") + 1] = "UNKNOWN"

    assert module.main(args) == 2
    receipt = json.loads((out_dir / "RECONSTRUCTED_BASELINE_APPROVAL_RECEIPT.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert {
        "approved_by_is_explicit",
        "approved_utc_is_timezone_aware",
        "approval_reference_is_explicit",
    } <= failed

    with pytest.raises(FileExistsError, match="no-clobber"):
        module.main(args)
