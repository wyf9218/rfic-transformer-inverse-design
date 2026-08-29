#!/usr/bin/env python3
"""Record an explicit reconstructed-baseline approval without running preparation.

This command is an audit recorder, not an authorization source. It writes one
no-clobber approval receipt only when the supplied candidate identity and the
human approval metadata pass all checks. It never invokes MARS, Cadence,
Calibre, EMX, a runner, or the campaign preparation command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANDIDATE_SCHEMA = "rfic_transformer.broadband56_reconstructed_baseline_contract.v1"
CANDIDATE_ORIGIN = "NEW_RECONSTRUCTION_NOT_HISTORICAL_V1"
APPROVAL_SCHEMA = "rfic_transformer.broadband56_reconstructed_baseline_approval.v1"
APPROVAL_DECISION = "APPROVE_V2_PREPARATION_PREFLIGHT_ONLY"
FAIL_DECISION = "DO_NOT_AUTHORIZE_RECONSTRUCTED_BASELINE"
APPROVAL_SOURCE = "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION"
EXPECTED_GRID_HZ = tuple(range(5_000_000_000, 60_000_000_001, 1_000_000_000))
PLACEHOLDERS = {"", "TBD", "UNKNOWN", "PLACEHOLDER"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_path = Path(args.candidate_contract).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    _require_new_output_directory(out_dir)

    checks: list[dict[str, Any]] = []
    candidate = _read_candidate(candidate_path, checks)
    actual_sha256 = _sha256(candidate_path) if candidate_path.is_file() else None
    expected_sha256 = str(args.candidate_sha256).strip().lower()
    approved_by = str(args.approved_by).strip()
    approved_utc = str(args.approved_utc).strip()
    approval_reference = str(args.approval_reference).strip()
    grid = candidate.get("frequency_grid") if isinstance(candidate.get("frequency_grid"), dict) else {}

    checks.extend(
        [
            _check(
                "candidate_sha256_argument_is_valid",
                bool(SHA256_PATTERN.fullmatch(expected_sha256)),
                expected_sha256,
            ),
            _check(
                "candidate_sha256_matches_exact_bytes",
                actual_sha256 is not None and actual_sha256 == expected_sha256,
                f"expected={expected_sha256}, actual={actual_sha256}",
            ),
            _check("candidate_schema", candidate.get("schema") == CANDIDATE_SCHEMA, candidate.get("schema")),
            _check("candidate_origin", candidate.get("contract_origin") == CANDIDATE_ORIGIN, candidate.get("contract_origin")),
            _check(
                "candidate_campaign_id_is_non_v2",
                bool(str(candidate.get("campaign_id") or "").strip())
                and str(candidate.get("campaign_id")) != "broadband56_real_emx_balanced200k_tsmc65_v2",
                candidate.get("campaign_id"),
            ),
            _check(
                "candidate_pending_explicit_approval",
                candidate.get("approval_status") == "PENDING_EXPLICIT_SHA256_APPROVAL",
                candidate.get("approval_status"),
            ),
            _check(
                "candidate_automatic_execution_forbidden",
                candidate.get("automatic_command_authorized") is False,
                candidate.get("automatic_command_authorized"),
            ),
            _check(
                "candidate_production_use_forbidden",
                candidate.get("production_use_authorized") is False,
                candidate.get("production_use_authorized"),
            ),
            _check(
                "candidate_frequency_grid_exact_56",
                _grid_is_exact(grid),
                json.dumps(grid, sort_keys=True),
            ),
            _check(
                "approved_by_is_explicit",
                bool(approved_by) and approved_by.upper() not in PLACEHOLDERS,
                approved_by or "missing",
            ),
            _check(
                "approved_utc_is_timezone_aware",
                _is_timezone_aware_iso8601(approved_utc),
                approved_utc or "missing",
            ),
            _check(
                "approval_reference_is_explicit",
                bool(approval_reference) and approval_reference.upper() not in PLACEHOLDERS,
                approval_reference or "missing",
            ),
        ]
    )

    passed = bool(checks) and all(item["pass"] for item in checks)
    receipt = {
        "schema": APPROVAL_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if passed else "FAIL",
        "decision": APPROVAL_DECISION if passed else FAIL_DECISION,
        "approved_by": approved_by,
        "approved_utc": approved_utc,
        "approval_source": APPROVAL_SOURCE,
        "approval_reference": approval_reference,
        "approved_contract": {
            "campaign_id": candidate.get("campaign_id"),
            "sha256": actual_sha256,
        },
        "candidate_source": {
            "path": str(candidate_path),
            "size_bytes": candidate_path.stat().st_size if candidate_path.is_file() else None,
            "sha256": actual_sha256,
        },
        "preparation_preflight_authorized": passed,
        "automatic_command_authorized": False,
        "golden_authorized": False,
        "simulator_authorized": False,
        "checks": checks,
        "execution_effect": "NONE_RECORD_ONLY",
        "authorization_boundary": (
            "A PASS receipt authorizes only the V2 preparation preflight with this exact candidate SHA. "
            "It does not authorize golden, Cadence, Calibre, EMX, queue execution, or automatic commands."
        ),
    }

    out_dir.mkdir(parents=True)
    receipt_path = out_dir / "RECONSTRUCTED_BASELINE_APPROVAL_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    index_path = out_dir / "SHA256SUMS.txt"
    index_path.write_text(f"{_sha256(receipt_path)}  {receipt_path.name}\n", encoding="utf-8")

    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"receipt={receipt_path}")
    return 0 if passed or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-contract", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approved-utc", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"no-clobber output already exists: {path}")


def _read_candidate(path: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    checks.append(_check("candidate_contract_exists", path.is_file(), str(path)))
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check("candidate_contract_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check("candidate_contract_parses", isinstance(payload, dict), type(payload).__name__))
    return payload if isinstance(payload, dict) else {}


def _grid_is_exact(grid: dict[str, Any]) -> bool:
    try:
        return (
            float(grid.get("start_ghz")) == 5.0
            and float(grid.get("stop_ghz")) == 60.0
            and float(grid.get("step_ghz")) == 1.0
            and int(grid.get("points")) == 56
            and tuple(int(value) for value in grid.get("exact_hz", [])) == EXPECTED_GRID_HZ
        )
    except (TypeError, ValueError):
        return False


def _is_timezone_aware_iso8601(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
