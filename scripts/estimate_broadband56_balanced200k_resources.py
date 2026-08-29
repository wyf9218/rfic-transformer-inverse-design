#!/usr/bin/env python3
"""Estimate balanced-200k resources from contract-bound real-EMX pilots.

The estimator is intentionally fail-closed. It accepts only fresh, complete,
non-resumed 32- and 1,000-geometry parallel-run summaries whose campaign
identity is bound to matching successful pilot checkpoint receipts. Synthetic,
create-only, proxy-only, partial, or reused-shard evidence cannot produce an
ETA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    EXPECTED_FEATURE_ROWS,
    FREQUENCY_POINTS,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    validate_contract,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"refusing to overwrite existing output directory: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "contract_json")
    contract_errors = validate_contract(contract) if contract else ["contract unavailable"]
    checks.append(_check("frozen_contract_is_valid", not contract_errors, contract_errors))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract)) if contract else ""

    pilot_32 = _pilot_evidence(
        expected_count=32,
        expected_state="PILOT_32_COMPLETE",
        run_summary_path=Path(args.pilot_32_run_summary).expanduser().resolve(),
        audit_dir=Path(args.pilot_32_audit_dir).expanduser().resolve(),
        fingerprint=fingerprint,
        checks=checks,
    )
    pilot_1000 = _pilot_evidence(
        expected_count=1_000,
        expected_state="PILOT_1000_COMPLETE",
        run_summary_path=Path(args.pilot_1000_run_summary).expanduser().resolve(),
        audit_dir=Path(args.pilot_1000_audit_dir).expanduser().resolve(),
        fingerprint=fingerprint,
        checks=checks,
    )

    overall_status = "PASS" if all(bool(item["pass"]) for item in checks) else "FAIL"
    estimate = _estimate(pilot_32, pilot_1000) if overall_status == "PASS" else None
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "RESOURCE_ESTIMATE_READY" if overall_status == "PASS" else "DO_NOT_USE_RESOURCE_ESTIMATE",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "target_accepted_geometries": TARGET_ACCEPTED_GEOMETRIES,
        "target_geometry_frequency_rows": EXPECTED_FEATURE_ROWS,
        "pilot_evidence": {"pilot_32": pilot_32, "pilot_1000": pilot_1000},
        "estimate": estimate,
        "checks": checks,
        "scientific_boundary": (
            "ETA is an engineering projection from measured real-EMX pilot wall time; it is not a guarantee. "
            "Phase-B/C acquisition, queueing, license pressure, retries, and changing acceptance yield may increase runtime."
        ),
    }
    json_path = out_dir / "RESOURCE_ESTIMATE.json"
    report_path = out_dir / "RESOURCE_ESTIMATE.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    _write_sha256s(out_dir)

    print(f"overall_status={overall_status}")
    print(f"decision={payload['decision']}")
    print(f"resource_estimate={json_path}")
    return 0 if overall_status == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--pilot-32-run-summary", required=True)
    parser.add_argument("--pilot-32-audit-dir", required=True)
    parser.add_argument("--pilot-1000-run-summary", required=True)
    parser.add_argument("--pilot-1000-audit-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _pilot_evidence(
    *,
    expected_count: int,
    expected_state: str,
    run_summary_path: Path,
    audit_dir: Path,
    fingerprint: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    prefix = f"pilot_{expected_count}"
    status_path = audit_dir / "CHECKPOINT_STATUS.json"
    receipt_path = audit_dir / "CHECKPOINT_RECEIPT.json"
    summary = _read_json(run_summary_path, checks, f"{prefix}_run_summary_json")
    status = _read_json(status_path, checks, f"{prefix}_checkpoint_status_json")
    receipt = _read_json(receipt_path, checks, f"{prefix}_checkpoint_receipt_json")

    identity = summary.get("campaign_identity") if isinstance(summary.get("campaign_identity"), dict) else {}
    touchstone = summary.get("touchstone_output_contract") if isinstance(summary.get("touchstone_output_contract"), dict) else {}
    expected_frequency = touchstone.get("expected_frequency") if isinstance(touchstone.get("expected_frequency"), dict) else {}
    summary_checks = summary.get("checks") if isinstance(summary.get("checks"), list) else []
    receipt_checks = receipt.get("checks") if isinstance(receipt.get("checks"), list) else []
    status_evidence = receipt.get("outputs", {}).get("checkpoint_status", {}) if isinstance(receipt.get("outputs"), dict) else {}

    checks.extend(
        [
            _check(f"{prefix}_run_pass", summary.get("overall_status") == "PASS", summary.get("overall_status")),
            _check(
                f"{prefix}_run_is_fresh_real_emx",
                summary.get("run_emx") is True
                and summary.get("create_only") is False
                and _integer(summary.get("reused_shard_count")) == 0
                and _integer(summary.get("pending_shard_count")) == _integer(summary.get("shard_count"))
                and _integer(summary.get("shard_count")) > 0,
                {
                    "run_emx": summary.get("run_emx"),
                    "create_only": summary.get("create_only"),
                    "reused_shards": summary.get("reused_shard_count"),
                    "pending_shards": summary.get("pending_shard_count"),
                    "shards": summary.get("shard_count"),
                },
            ),
            _check(
                f"{prefix}_run_counts_exact",
                _integer(summary.get("input_row_count")) == expected_count
                and _integer(summary.get("merged_row_count")) == expected_count
                and _integer(summary.get("fail_shard_count")) == 0,
                {
                    "input": summary.get("input_row_count"),
                    "merged": summary.get("merged_row_count"),
                    "fail_shards": summary.get("fail_shard_count"),
                },
            ),
            _check(
                f"{prefix}_run_checks_pass",
                bool(summary_checks) and all(item.get("pass") is True for item in summary_checks),
                f"checks={len(summary_checks)}",
            ),
            _check(
                f"{prefix}_campaign_identity_bound",
                identity.get("input_campaign_contract_fingerprints") == [fingerprint]
                and identity.get("merged_campaign_contract_fingerprints") == [fingerprint]
                and _integer(identity.get("input_geometry_sha256_unique_count")) == expected_count
                and _integer(identity.get("merged_geometry_sha256_unique_count")) == expected_count
                and identity.get("geometry_sha256_sets_match") is True,
                identity,
            ),
            _check(
                f"{prefix}_touchstone_contract_exact",
                touchstone.get("checked") is True
                and touchstone.get("expected_extension") == ".s4p"
                and _integer(touchstone.get("expected_ports")) == 4
                and _integer(touchstone.get("parse_error_count")) == 0
                and _integer(touchstone.get("port_error_count")) == 0
                and _integer(touchstone.get("frequency_error_count")) == 0
                and float(expected_frequency.get("start_ghz") or -1.0) == 5.0
                and float(expected_frequency.get("stop_ghz") or -1.0) == 60.0
                and float(expected_frequency.get("step_ghz") or -1.0) == 1.0
                and _integer(expected_frequency.get("points")) == FREQUENCY_POINTS,
                touchstone,
            ),
            _check(
                f"{prefix}_timing_is_measured",
                _positive_finite(summary.get("elapsed_seconds"))
                and _positive_finite(summary.get("rows_per_second_effective"))
                and _positive_finite(summary.get("active_worker_elapsed_seconds_sum")),
                {
                    "elapsed_seconds": summary.get("elapsed_seconds"),
                    "rows_per_second_effective": summary.get("rows_per_second_effective"),
                    "active_worker_elapsed_seconds_sum": summary.get("active_worker_elapsed_seconds_sum"),
                },
            ),
            _check(
                f"{prefix}_checkpoint_status_exact",
                status.get("campaign_id") == CAMPAIGN_ID
                and status.get("contract_fingerprint_sha256") == fingerprint
                and status.get("audit_mode") == "pilot"
                and status.get("checkpoint_status") == expected_state
                and _integer(status.get("accepted_geometries")) == expected_count
                and _integer(status.get("s4p_artifacts")) == expected_count
                and _integer(status.get("geometry_frequency_rows")) == expected_count * FREQUENCY_POINTS,
                status,
            ),
            _check(
                f"{prefix}_checkpoint_receipt_pass",
                receipt.get("overall_status") == "PASS"
                and receipt.get("decision") == "USE_CHECKPOINT"
                and receipt.get("campaign_id") == CAMPAIGN_ID
                and receipt.get("contract_fingerprint_sha256") == fingerprint
                and receipt.get("audit_mode") == "pilot"
                and _integer(receipt.get("expected_accepted")) == expected_count
                and bool(receipt_checks)
                and all(item.get("pass") is True for item in receipt_checks),
                {
                    "overall_status": receipt.get("overall_status"),
                    "decision": receipt.get("decision"),
                    "expected_accepted": receipt.get("expected_accepted"),
                    "checks": len(receipt_checks),
                },
            ),
            _check(
                f"{prefix}_status_hash_bound_to_receipt",
                status_path.is_file()
                and status_evidence.get("sha256") == _sha256(status_path)
                and Path(str(status_evidence.get("path") or "")).expanduser().resolve() == status_path,
                status_evidence,
            ),
        ]
    )
    return {
        "expected_count": expected_count,
        "run_summary": _file_evidence(run_summary_path),
        "checkpoint_status": _file_evidence(status_path),
        "checkpoint_receipt": _file_evidence(receipt_path),
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "rows_per_second_effective": summary.get("rows_per_second_effective"),
        "active_worker_elapsed_seconds_sum": summary.get("active_worker_elapsed_seconds_sum"),
        "jobs_requested": summary.get("jobs_requested"),
        "parallel_efficiency": summary.get("parallel_efficiency"),
        "accepted_geometries": status.get("accepted_geometries"),
        "geometry_frequency_rows": status.get("geometry_frequency_rows"),
    }


def _estimate(pilot_32: dict[str, Any], pilot_1000: dict[str, Any]) -> dict[str, Any]:
    rate_32 = float(pilot_32["rows_per_second_effective"])
    rate_1000 = float(pilot_1000["rows_per_second_effective"])
    current_accepted = int(pilot_1000["accepted_geometries"])
    remaining = TARGET_ACCEPTED_GEOMETRIES - current_accepted
    worker_seconds_per_geometry = float(pilot_1000["active_worker_elapsed_seconds_sum"]) / 1_000.0
    lower_observed_rate = min(rate_32, rate_1000)
    upper_observed_rate = max(rate_32, rate_1000)

    def wall_hours(rate: float) -> float:
        return float(remaining) / float(rate) / 3600.0

    return {
        "current_audited_accepted_geometries": current_accepted,
        "remaining_accepted_geometries": remaining,
        "remaining_geometry_frequency_rows": remaining * FREQUENCY_POINTS,
        "authoritative_nominal_basis": "fresh contract-bound 1000-geometry pilot effective wall throughput",
        "pilot_32_effective_geometries_per_second": rate_32,
        "pilot_1000_effective_geometries_per_second": rate_1000,
        "pilot_1000_worker_seconds_per_geometry": worker_seconds_per_geometry,
        "nominal_remaining_wall_hours": wall_hours(rate_1000),
        "observed_pilot_rate_wall_hour_range": {
            "fast_bound_hours": wall_hours(upper_observed_rate),
            "slow_bound_hours": wall_hours(lower_observed_rate),
            "basis": "min/max effective wall rates from the complete 32 and 1000 pilots; not a confidence interval",
        },
        "throughput_sensitivity_wall_hours": {
            "at_80_percent_of_1000_pilot_rate": wall_hours(rate_1000 * 0.80),
            "at_65_percent_of_1000_pilot_rate": wall_hours(rate_1000 * 0.65),
            "at_50_percent_of_1000_pilot_rate": wall_hours(rate_1000 * 0.50),
        },
        "nominal_remaining_worker_hours": remaining * worker_seconds_per_geometry / 3600.0,
        "counting_rule": (
            "Only the 1000-pilot audited ledger is counted here. The golden and 32-pilot samples are not added "
            "separately because they may overlap the cumulative 1000-pilot ledger."
        ),
    }


def _read_json(path: Path, checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(name, False, f"missing: {path}"))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check(name, False, str(exc)))
        return {}
    checks.append(_check(name, isinstance(payload, dict), str(path)))
    return payload if isinstance(payload, dict) else {}


def _positive_finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def _integer(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_evidence(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _write_sha256s(out_dir: Path) -> None:
    lines = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{_sha256(path)}  {path.name}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Broadband56 Balanced-200k Resource Estimate",
        "",
        f"- Status: `{payload['overall_status']}`",
        f"- Decision: `{payload['decision']}`",
        f"- Campaign: `{payload['campaign_id']}`",
        f"- Contract fingerprint: `{payload['contract_fingerprint_sha256']}`",
    ]
    estimate = payload.get("estimate")
    if isinstance(estimate, dict):
        lines.extend(
            [
                f"- Current audited accepted: `{estimate['current_audited_accepted_geometries']}`",
                f"- Remaining accepted: `{estimate['remaining_accepted_geometries']}`",
                f"- 1,000-pilot throughput: `{estimate['pilot_1000_effective_geometries_per_second']:.6g}` geometries/s",
                f"- Nominal remaining wall time: `{estimate['nominal_remaining_wall_hours']:.3f}` hours",
                f"- Nominal remaining worker time: `{estimate['nominal_remaining_worker_hours']:.3f}` worker-hours",
                "",
                "The observed-pilot range is not a confidence interval. Queueing, license pressure, retries, "
                "and Phase-B/C acquisition overhead remain outside this estimate.",
            ]
        )
    else:
        lines.extend(["", "No ETA was produced because one or more evidence gates failed."])
    lines.extend(["", "## Checks", ""])
    for item in payload["checks"]:
        lines.append(f"- [{'PASS' if item['pass'] else 'FAIL'}] `{item['name']}`: {item['detail']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
