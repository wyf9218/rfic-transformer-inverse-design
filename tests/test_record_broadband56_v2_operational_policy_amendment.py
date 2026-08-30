from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = (
    ROOT
    / "docs"
    / "research"
    / "BROADBAND56_V2_CAPACITY_POLICY_AMENDMENT_CANDIDATE_20260830.json"
)
EXPECTED_SHA256 = "aca4883a08abca1d8309b6e1af4f6fd4916c03e7240056a434413a7b50d63a8d"


def _load_module():
    path = ROOT / "scripts" / "record_broadband56_v2_operational_policy_amendment.py"
    spec = importlib.util.spec_from_file_location(
        "record_broadband56_v2_operational_policy_amendment", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments(out_dir: Path, *, candidate: Path = CANDIDATE, sha: str | None = None):
    return [
        "--candidate",
        str(candidate),
        "--candidate-sha256",
        sha or _sha256(candidate),
        "--approved-by",
        "Yufeng Wang, project owner and project leader",
        "--approved-utc",
        "2026-08-30T18:30:00Z",
        "--approval-reference",
        "explicit unit-test capacity policy approval",
        "--out-dir",
        str(out_dir),
    ]


def test_candidate_has_frozen_exact_sha() -> None:
    assert _sha256(CANDIDATE) == EXPECTED_SHA256


def test_records_full_staged_policy_approval_without_execution(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "approval"

    assert module.main(_arguments(out_dir)) == 0

    receipt = json.loads(
        (out_dir / "OPERATIONAL_POLICY_APPROVAL_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["resource_policy"] == "CAPACITY_NORMALIZED_HIGH_LOAD_V1"
    assert receipt["approved_candidate"]["sha256"] == EXPECTED_SHA256
    assert receipt["authorization_scope"] == "FULL_CAMPAIGN"
    assert receipt["one_golden_authorized"] is True
    assert receipt["pilot_32_authorized"] is True
    assert receipt["pilot_1000_authorized"] is True
    assert receipt["phase_a_authorized"] is True
    assert receipt["phase_b_authorized"] is True
    assert receipt["phase_c_authorized"] is True
    assert receipt["campaign_200k_authorized"] is True
    assert receipt["execution_effect"] == "NONE_RECORD_ONLY"
    assert all(item["pass"] for item in receipt["checks"])


def test_wrong_sha_fails_closed(tmp_path: Path) -> None:
    module = _load_module()

    assert module.main(_arguments(tmp_path / "bad", sha="0" * 64)) == 2
    receipt = json.loads(
        (tmp_path / "bad" / "OPERATIONAL_POLICY_APPROVAL_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "FAIL"
    assert receipt["one_golden_authorized"] is False
    assert receipt["campaign_200k_authorized"] is False


def test_tampered_scientific_contract_fails_closed(tmp_path: Path) -> None:
    module = _load_module()
    payload = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    payload["frequency_contract"]["step_ghz"] = 0.5
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    assert module.main(_arguments(tmp_path / "tampered_out", candidate=tampered)) == 2


def test_no_clobber_refuses_existing_output(tmp_path: Path) -> None:
    module = _load_module()
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    assert module.main(_arguments(out_dir)) == 2
