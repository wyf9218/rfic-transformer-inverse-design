#!/usr/bin/env python3
"""Stage or authorize a five-arm production acquisition-mix contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARMS = ("coarse_4d", "rare_marginal", "pairwise_gap", "random_exploration", "geometry_diversity")
REQUIRED_SCRIPTS = (
    "select_physical_feature_acquisition_mix.py",
    "select_physical_feature_targeted_candidate_geometries.py",
    "materialize_physical_feature_targeted_s4p_queue.py",
    "audit_mars56_s4p_candidate_queue_provenance.py",
    "run_mars56_s4p_adaptive_physical_acquisition_round.sh",
    "run_accepted_1m_campaign_controller.sh",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    recommendation_path = Path(args.recommendation_summary).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    scripts_dir = Path(args.scripts_dir).expanduser().resolve()
    recommendation = _read_json(recommendation_path)
    mix = recommendation.get("recommended_mix") or {}
    counts = mix.get("counts") or {}
    base_checks = {
        "recommendation_summary_exists": recommendation_path.is_file(),
        "recommendation_status_pass": recommendation.get("overall_status") == "PASS",
        "recommendation_is_proposal_only": recommendation.get("outcome_status") == "PROPOSAL_ONLY_NOT_DEPLOYED",
        "queue_count_exact": int(recommendation.get("queue_count") or 0) == int(args.expected_queue_count),
        "arm_set_exact": set(counts) == set(ARMS),
        "counts_nonnegative_integers": all(isinstance(counts.get(arm), int) and counts[arm] >= 0 for arm in ARMS),
        "count_sum_exact": sum(int(counts.get(arm) or 0) for arm in ARMS) == int(args.expected_queue_count),
        "proposal_did_not_self_authorize": (recommendation.get("production_mapping") or {}).get("automatic_command_authorized") is False,
        "required_scripts_exist": all((scripts_dir / name).is_file() for name in REQUIRED_SCRIPTS),
    }
    preflight_path = Path(args.controller_preflight_summary).expanduser().resolve() if args.controller_preflight_summary else None
    release_path = Path(args.resource_release_json).expanduser().resolve() if args.resource_release_json else None
    preflight = _read_json(preflight_path) if preflight_path else {}
    release = _read_json(release_path) if release_path else {}
    authorization_checks = {
        "authorization_requested": bool(args.authorize),
        "controller_preflight_exists": bool(preflight_path and preflight_path.is_file()),
        "controller_preflight_pass": preflight.get("overall_status") == "PASS",
        "controller_preflight_queue_count_matches": int(preflight.get("queue_count") or 0) == int(args.expected_queue_count),
        "controller_preflight_jobs_48": int(preflight.get("jobs") or 0) == 48,
        "resource_release_exists": bool(release_path and release_path.is_file()),
        "resource_release_pass": release.get("overall_status") == "PASS",
        "tapeout_window_explicitly_released": release.get("tapeout_resource_window_released") is True,
        "resource_release_has_approver": bool(str(release.get("approved_by") or "").strip()),
        "resource_release_not_before_july_16": _release_not_before(release.get("approved_utc")),
    }
    base_pass = all(base_checks.values())
    authorize_pass = bool(args.authorize) and all(authorization_checks.values())
    automatic_authorized = base_pass and authorize_pass
    overall_status = "PASS" if base_pass and (not args.authorize or authorize_pass) else "FAIL"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script_sources = {
        name: _file_source(scripts_dir / name)
        for name in REQUIRED_SCRIPTS
    }
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "authorization_status": (
            "AUTHORIZED_FOR_CONTROLLER_PREFLIGHT"
            if automatic_authorized
            else ("STAGED_AWAITING_RESOURCE_RELEASE_AND_FINAL_PREFLIGHT" if base_pass else "REJECTED_INVALID_PROPOSAL")
        ),
        "automatic_command_authorized": automatic_authorized,
        "proxy_values_are_acquisition_only": True,
        "production_acquisition_mix": {
            "queue_count": int(args.expected_queue_count),
            "counts": {arm: int(counts.get(arm) or 0) for arm in ARMS},
            "fractions": {arm: float((mix.get("fractions") or {}).get(arm) or 0.0) for arm in ARMS},
        },
        "recommendation_source": _file_source(recommendation_path),
        "strict_uniformity_source": recommendation.get("uniformity_summary") or {},
        "controller_preflight_source": _file_source(preflight_path) if preflight_path else None,
        "resource_release_source": _file_source(release_path) if release_path else None,
        "script_sources": script_sources,
        "base_checks": base_checks,
        "authorization_checks": authorization_checks,
        "activation_boundary": (
            "This file is accepted by the production wrapper only when automatic_command_authorized is true. "
            "Authorization requires a general controller preflight plus an explicit post-tapeout resource release. "
            "The controller must then run --preflight-only again with this exact contract before generation resumes."
        ),
        "scientific_boundary": (
            "The mix allocates candidate simulations only. Proxy values rank candidates; all realized physical features "
            "and training labels must come from new real EMX S4P files."
        ),
        "arguments": vars(args),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"automatic_command_authorized={str(automatic_authorized).lower()}")
    print(f"output={output_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recommendation-summary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scripts-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--expected-queue-count", type=int, default=120000)
    parser.add_argument("--controller-preflight-summary")
    parser.add_argument("--resource-release-json")
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.expected_queue_count < 1:
        parser.error("--expected-queue-count must be positive")
    return args


def _release_not_before(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc) >= datetime(2026, 7, 16, tzinfo=timezone.utc)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _file_source(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        out.update(
            {
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return out


if __name__ == "__main__":
    raise SystemExit(main())
