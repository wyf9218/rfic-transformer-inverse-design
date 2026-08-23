#!/usr/bin/env python3
"""Recommend a staged acquisition mix from a strict real-label uniformity audit.

The output is a proposal only. It cannot modify a production queue, create
labels, or claim that proxy-ranked candidates will land in the requested bins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FEATURES = ("lp", "ls", "q", "k")
PAIRS = ("lp_ls", "lp_q", "lp_k", "ls_q", "ls_k", "q_k")
TARGETED_ARMS = ("rare_marginal", "pairwise_gap", "coarse_4d")
ALL_ARMS = (*TARGETED_ARMS, "random_exploration", "geometry_diversity")
EXPECTED_RANGES = {
    "lp": (0.5, 3.0),
    "ls": (0.5, 3.0),
    "q": (5.0, 25.0),
    "k": (0.0, 0.8),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.uniformity_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source = _read_json(summary_path)
    checks = _source_checks(source, args)
    valid = all(checks.values())
    severity = _severity(source) if valid else {}
    mix = _allocate_mix(severity, args) if valid else {}
    status = "PASS" if valid and mix else "FAIL"
    active_gap = max(
        (float(severity.get(name, 0.0)) for name in ("marginal", "pairwise", "four_d")),
        default=0.0,
    )
    decision = (
        "STAGE_GAP_DRIVEN_EQUAL_BUDGET_REMEDIATION"
        if status == "PASS" and active_gap > 0.0
        else ("NO_STRICT_GAP_SIGNAL_KEEP_EXPLORATION" if status == "PASS" else "FIX_UNIFORMITY_EVIDENCE")
    )

    plot_path = out_dir / "physical_feature_acquisition_mix_proposal.png"
    plot_status = _write_plot(plot_path, severity, mix)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "outcome_status": "PROPOSAL_ONLY_NOT_DEPLOYED",
        "uniformity_summary": {
            "path": str(summary_path),
            "sha256": _sha256(summary_path),
            "overall_status": source.get("overall_status"),
            "valid_feature_count": source.get("valid_feature_count"),
        },
        "checks": checks,
        "severity": severity,
        "severity_definition": {
            "marginal": "maximum strict-gate shortfall across Lp/Ls/Q/|K|",
            "pairwise": "maximum occupancy/entropy shortfall across all six pairs",
            "four_d": "mean occupancy, entropy, and nonzero-imbalance shortfalls",
            "imbalance_transform": "clipped log excess relative to the preregistered limit",
        },
        "recommended_mix": mix,
        "queue_count": int(args.queue_count),
        "production_mapping": {
            "rare_marginal_fraction": mix.get("fractions", {}).get("rare_marginal"),
            "pairwise_fallback_fraction": mix.get("fractions", {}).get("pairwise_gap"),
            "unmapped_proposal_arms": ["random_exploration", "geometry_diversity"],
            "automatic_command_authorized": False,
            "reason": (
                "The current production selector has no equivalent audited queue-share switches for random exploration "
                "and sequential geometry diversity. Omitting those arms would change the preregistered mix."
            ),
        },
        "artifacts": {
            "summary": str(out_dir / "physical_feature_acquisition_mix_proposal_summary.json"),
            "report": str(out_dir / "physical_feature_acquisition_mix_proposal_report.md"),
            "plot": str(plot_path) if plot_status == "PASS" else "",
            "plot_status": plot_status,
        },
        "scientific_boundary": (
            "This recommendation is computed from an existing strict uniformity summary. It does not prove that the "
            "source CSV contains real EMX unless the upstream accepted-pool provenance is independently verified. "
            "Proxy predictions may rank candidates only; realized bin membership and all training labels must come "
            "from new nonempty EMX S4P returns. The proposal cannot alter the paused production queue automatically."
        ),
        "arguments": vars(args),
    }
    summary_out = Path(payload["artifacts"]["summary"])
    report_out = Path(payload["artifacts"]["report"])
    summary_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_out.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"summary={summary_out}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uniformity-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--queue-count", type=int, default=100_000)
    parser.add_argument("--random-exploration-floor", type=float, default=0.10)
    parser.add_argument("--geometry-diversity-floor", type=float, default=0.10)
    parser.add_argument("--targeted-arm-floor", type=float, default=0.15)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    floors = (
        args.random_exploration_floor,
        args.geometry_diversity_floor,
        args.targeted_arm_floor,
    )
    if args.queue_count < 5 or any(not 0.0 <= value < 1.0 for value in floors):
        parser.error("queue count must be >=5 and all floors must be in [0,1)")
    reserved = args.random_exploration_floor + args.geometry_diversity_floor + 3.0 * args.targeted_arm_floor
    if reserved > 1.0 + 1.0e-12:
        parser.error("configured floors exceed the total acquisition budget")
    return args


def _source_checks(data: dict[str, Any], args: argparse.Namespace) -> dict[str, bool]:
    ranges = data.get("ranges") or {}
    thresholds = data.get("distribution_thresholds") or {}
    one_d = data.get("one_dimensional_uniformity") or {}
    pairwise = data.get("pairwise_uniformity") or {}
    four_d = data.get("four_dimensional_uniformity") or {}
    range_match = all(
        (ranges.get(name) or {}).get("explicit") is True
        and math.isclose(float((ranges.get(name) or {}).get("min", math.nan)), bounds[0])
        and math.isclose(float((ranges.get(name) or {}).get("max", math.nan)), bounds[1])
        for name, bounds in EXPECTED_RANGES.items()
    )
    return {
        "summary_object_present": bool(data),
        "uniformity_status_is_explicit": data.get("overall_status") in {"PASS", "FAIL"},
        "enough_realized_rows_for_proposal": int(data.get("valid_feature_count") or 0) >= 1000,
        "declared_ranges_exact_and_explicit": range_match,
        "all_marginal_metrics_present": all(name in one_d for name in FEATURES),
        "all_pair_metrics_present": all(name in pairwise for name in PAIRS),
        "four_d_metrics_present": bool(four_d),
        "strict_thresholds_not_weakened": bool(
            float(thresholds.get("min_1d_occupied_fraction") or 0.0) >= 0.90
            and float(thresholds.get("min_1d_normalized_entropy") or 0.0) >= 0.90
            and float(thresholds.get("max_1d_nonzero_bin_imbalance") or math.inf) <= 2.50
            and float(thresholds.get("min_pair_occupied_fraction") or 0.0) >= 0.65
            and float(thresholds.get("min_pair_normalized_entropy") or 0.0) >= 0.80
            and thresholds.get("require_four_d_gate") is True
            and float(thresholds.get("min_four_d_occupied_fraction") or 0.0) >= 0.50
            and float(thresholds.get("min_four_d_normalized_entropy") or 0.0) >= 0.80
            and float(thresholds.get("max_four_d_nonzero_bin_imbalance") or math.inf) <= 4.0
        ),
        "queue_count_positive": int(args.queue_count) > 0,
    }


def _severity(data: dict[str, Any]) -> dict[str, Any]:
    thresholds = data["distribution_thresholds"]
    one_d = data["one_dimensional_uniformity"]
    pairs = data["pairwise_uniformity"]
    four = data["four_dimensional_uniformity"]
    marginal_by_feature = {
        name: _mean(
            _shortfall(one_d[name]["occupied_fraction"], thresholds["min_1d_occupied_fraction"]),
            _shortfall(one_d[name]["normalized_entropy"], thresholds["min_1d_normalized_entropy"]),
            _imbalance_excess(
                one_d[name]["max_to_min_nonzero_ratio"], thresholds["max_1d_nonzero_bin_imbalance"]
            ),
        )
        for name in FEATURES
    }
    pair_by_name = {
        name: _mean(
            _shortfall(pairs[name]["occupied_fraction"], thresholds["min_pair_occupied_fraction"]),
            _shortfall(pairs[name]["normalized_entropy"], thresholds["min_pair_normalized_entropy"]),
        )
        for name in PAIRS
    }
    four_components = {
        "occupied_fraction": _shortfall(
            four["occupied_fraction"], thresholds["min_four_d_occupied_fraction"]
        ),
        "normalized_entropy": _shortfall(
            four["normalized_entropy"], thresholds["min_four_d_normalized_entropy"]
        ),
        "nonzero_imbalance": _imbalance_excess(
            four["max_to_min_nonzero_ratio"], thresholds["max_four_d_nonzero_bin_imbalance"]
        ),
    }
    return {
        "marginal": float(max(marginal_by_feature.values())),
        "pairwise": float(max(pair_by_name.values())),
        "four_d": float(_mean(*four_components.values())),
        "marginal_by_feature": marginal_by_feature,
        "pairwise_by_name": pair_by_name,
        "four_d_components": four_components,
        "worst_marginal": max(marginal_by_feature, key=marginal_by_feature.get),
        "worst_pair": max(pair_by_name, key=pair_by_name.get),
    }


def _allocate_mix(severity: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    fractions = {
        "random_exploration": float(args.random_exploration_floor),
        "geometry_diversity": float(args.geometry_diversity_floor),
        **{arm: float(args.targeted_arm_floor) for arm in TARGETED_ARMS},
    }
    remaining = 1.0 - sum(fractions.values())
    scores = {
        "rare_marginal": float(severity["marginal"]),
        "pairwise_gap": float(severity["pairwise"]),
        "coarse_4d": float(severity["four_d"]),
    }
    total_score = sum(scores.values())
    if total_score <= 0.0:
        for arm in TARGETED_ARMS:
            fractions[arm] += remaining / len(TARGETED_ARMS)
    else:
        for arm in TARGETED_ARMS:
            fractions[arm] += remaining * scores[arm] / total_score
    fractions = {arm: float(value) for arm, value in fractions.items()}
    counts = _largest_remainder_counts(fractions, int(args.queue_count))
    return {
        "fractions": fractions,
        "counts": counts,
        "fraction_sum": float(sum(fractions.values())),
        "count_sum": int(sum(counts.values())),
        "allocation_policy": "fixed exploration/diversity/targeted floors plus proportional strict-gap severity",
    }


def _largest_remainder_counts(fractions: dict[str, float], total: int) -> dict[str, int]:
    raw = {arm: fraction * total for arm, fraction in fractions.items()}
    counts = {arm: int(math.floor(value)) for arm, value in raw.items()}
    remaining = total - sum(counts.values())
    order = sorted(raw, key=lambda arm: (-(raw[arm] - counts[arm]), ALL_ARMS.index(arm)))
    for arm in order[:remaining]:
        counts[arm] += 1
    return counts


def _shortfall(value: Any, threshold: Any) -> float:
    value_f, threshold_f = float(value), float(threshold)
    return float(max(0.0, (threshold_f - value_f) / max(threshold_f, 1.0e-12)))


def _imbalance_excess(value: Any, limit: Any) -> float:
    ratio = float(value) / max(float(limit), 1.0e-12)
    if ratio <= 1.0:
        return 0.0
    return float(min(1.0, math.log(ratio) / math.log(1000.0)))


def _mean(*values: float) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _write_plot(path: Path, severity: dict[str, Any], mix: dict[str, Any]) -> str:
    if not severity or not mix:
        return "NOT_WRITTEN_INVALID_INPUT"
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8), constrained_layout=True)
    severity_names = ["marginal", "pairwise", "four_d"]
    axes[0].bar(severity_names, [severity[name] for name in severity_names], color=["#3478b7", "#d66b2c", "#6d55a5"])
    axes[0].set_ylim(0.0, 1.0)
    axes[0].set_ylabel("Normalized strict-gap severity")
    axes[0].set_title(f"Real-label audit gaps\nworst 1D={severity['worst_marginal']}, worst pair={severity['worst_pair']}")
    arm_names = list(ALL_ARMS)
    axes[1].bar(arm_names, [mix["counts"][name] for name in arm_names], color="#2c8c74")
    axes[1].tick_params(axis="x", rotation=35)
    axes[1].set_ylabel("Proposed candidates")
    axes[1].set_title("Proposal only: not simulated, not deployed")
    for axis in axes:
        axis.grid(True, axis="y", alpha=0.25)
    fig.suptitle("Next-checkpoint physical-feature acquisition mix", fontsize=14)
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)
    return "PASS"


def _render_report(data: dict[str, Any]) -> str:
    mix = data.get("recommended_mix") or {}
    lines = [
        "# Physical-feature acquisition mix proposal",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Outcome status: **{data['outcome_status']}**",
        f"- Source uniformity status: `{data['uniformity_summary']['overall_status']}`",
        f"- Source SHA-256: `{data['uniformity_summary']['sha256']}`",
        "",
        "## Proposed counts",
        "",
    ]
    for arm, count in (mix.get("counts") or {}).items():
        lines.append(f"- `{arm}`: {count} ({100.0 * mix['fractions'][arm]:.2f}%)")
    lines.extend(["", data["scientific_boundary"], ""])
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


if __name__ == "__main__":
    raise SystemExit(main())
