#!/usr/bin/env python3
"""Aggregate formal single-versus-multihead tandem comparisons across seeds."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    paths = [Path(item).expanduser().resolve() for item in args.comparison_summary]
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "single_multihead_tandem_multiseed_summary.json"
    report_path = out_dir / "single_multihead_tandem_multiseed_report.md"
    records = [_read_json(path) for path in paths]
    contracts = [record.get("comparison_contract") or {} for record in records]
    seeds = [_seed(contract) for contract in contracts]
    base_fingerprints = [_base_contract_fingerprint(contract) for contract in contracts]
    improvements = [_improvement(record) for record in records]
    feature_names = sorted(
        set.intersection(
            *[
                set(((record.get("metrics") or {}).get("per_feature") or {}))
                for record in records
            ]
        )
    ) if records else []
    feature_regressions = {
        feature: [
            _finite(
                ((((record.get("metrics") or {}).get("per_feature") or {}).get(feature) or {}).get(
                    "multihead_relative_regression"
                ))
            )
            for record in records
        ]
        for feature in feature_names
    }
    head_healthy = [
        ((record.get("head_utilization") or {}).get("all_heads_selected_at_least_once") is True)
        and _finite((record.get("head_utilization") or {}).get("entropy")) is not None
        and float((record.get("head_utilization") or {}).get("entropy"))
        >= float(args.minimum_head_utilization_entropy)
        for record in records
    ]
    overheads = [
        _finite((record.get("weight_contract") or {}).get("multihead_parameter_overhead_fraction"))
        for record in records
    ]
    checks = {
        "minimum_summary_count_met": len(records) >= int(args.minimum_seeds),
        "all_summary_paths_unique": len(set(paths)) == len(paths),
        "all_summaries_exist": all(path.is_file() for path in paths),
        "all_comparison_contracts_pass": bool(records)
        and all(record.get("overall_status") == "PASS" and all((record.get("checks") or {}).values()) for record in records),
        "all_evidence_is_formal": bool(records) and all(record.get("formal_evidence") is True for record in records),
        "all_contract_fingerprints_match_except_model_seed": bool(base_fingerprints)
        and None not in base_fingerprints
        and len(set(base_fingerprints)) == 1,
        "model_seeds_valid_and_unique": len(seeds) == len(records)
        and None not in seeds
        and len(set(seeds)) == len(seeds),
        "all_improvements_finite": len(improvements) == len(records) and None not in improvements,
        "all_four_feature_regressions_finite": len(feature_names) == 4
        and all(len(values) == len(records) and None not in values for values in feature_regressions.values()),
        "all_forward_weights_matched_within_seed": bool(records)
        and all((record.get("weight_contract") or {}).get("forward_exact_match") is True for record in records),
        "all_parameter_overheads_finite": len(overheads) == len(records) and None not in overheads,
    }
    contract_pass = all(checks.values())
    statistics = _statistics(
        [float(value) for value in improvements if value is not None],
        {key: [float(value) for value in values if value is not None] for key, values in feature_regressions.items()},
        head_healthy,
        [float(value) for value in overheads if value is not None],
        args,
    )
    gates = _review_gates(statistics, args) if contract_pass else {}
    if not contract_pass:
        overall_status = "FAIL"
        decision = "FIX_MULTI_SEED_ABLATION_CONTRACT"
    elif all(gates.values()):
        overall_status = "PASS"
        decision = "REVIEW_MULTIHEAD_FOR_FIXED_BUDGET_REAL_EMX_CLOSURE"
    else:
        overall_status = "PASS"
        decision = "RETAIN_SINGLEHEAD_BASELINE_MULTI_SEED_GATES_NOT_MET"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "checks": checks,
        "review_gates": gates,
        "seed_count": len(records),
        "model_seeds": seeds,
        "shared_base_contract_fingerprint_sha256": base_fingerprints[0]
        if base_fingerprints and len(set(base_fingerprints)) == 1
        else None,
        "statistics": statistics,
        "comparison_summaries": [
            {"path": str(path), "sha256": _sha256_file(path) if path.is_file() else None}
            for path in paths
        ],
        "scientific_boundary": (
            "This aggregate covers training-seed variability for fixed proxy models and one frozen OOD panel. "
            "It is only a gate to spend a fixed real-EM budget, never an automatic model promotion. Review still "
            "requires production geometry audit, foundry DRC, fresh real EMX S4P closure, and sampled HFSS correlation."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-summary", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-seeds", type=int, default=5)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.05)
    parser.add_argument("--minimum-seed-win-fraction", type=float, default=0.60)
    parser.add_argument("--minimum-bootstrap-median-improvement-lower", type=float, default=0.0)
    parser.add_argument("--maximum-per-feature-median-regression", type=float, default=0.05)
    parser.add_argument("--minimum-head-healthy-seed-fraction", type=float, default=0.80)
    parser.add_argument("--minimum-head-utilization-entropy", type=float, default=0.80)
    parser.add_argument("--maximum-parameter-overhead-fraction", type=float, default=0.10)
    parser.add_argument("--bootstrap-replicates", type=int, default=5_000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.minimum_seeds) < 3:
        parser.error("--minimum-seeds must be at least 3")
    if int(args.bootstrap_replicates) < 100:
        parser.error("--bootstrap-replicates must be at least 100")
    for name in (
        "minimum_material_improvement",
        "minimum_seed_win_fraction",
        "minimum_head_healthy_seed_fraction",
        "minimum_head_utilization_entropy",
        "maximum_parameter_overhead_fraction",
        "maximum_per_feature_median_regression",
    ):
        value = float(getattr(args, name))
        if not 0.0 <= value <= 1.0:
            parser.error(f"--{name.replace('_', '-')} must be in [0, 1]")
    if not 0.0 < float(args.bootstrap_confidence) < 1.0:
        parser.error("--bootstrap-confidence must be in (0, 1)")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _seed(contract: dict[str, Any]) -> int | None:
    value = contract.get("model_seed")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _improvement(record: dict[str, Any]) -> float | None:
    return _finite((record.get("metrics") or {}).get("multihead_relative_improvement"))


def _base_contract_fingerprint(contract: dict[str, Any]) -> str | None:
    if not contract:
        return None
    cleaned = json.loads(json.dumps(contract, sort_keys=True))
    cleaned.pop("model_seed", None)
    shared = cleaned.get("shared_arguments") or {}
    shared.pop("seed", None)
    payload = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _statistics(
    improvements: list[float],
    feature_regressions: dict[str, list[float]],
    head_healthy: list[bool],
    overheads: list[float],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not improvements:
        return {}
    values = np.asarray(improvements, dtype=float)
    rng = np.random.default_rng(int(args.bootstrap_seed))
    medians = np.empty(int(args.bootstrap_replicates), dtype=float)
    for index in range(len(medians)):
        medians[index] = float(np.median(rng.choice(values, size=len(values), replace=True)))
    alpha = (1.0 - float(args.bootstrap_confidence)) / 2.0
    per_feature = {
        feature: {
            "median_relative_regression": float(np.median(regressions)),
            "maximum_seed_relative_regression": float(np.max(regressions)),
        }
        for feature, regressions in feature_regressions.items()
    }
    return {
        "improvement_by_seed": improvements,
        "median_relative_improvement": float(np.median(values)),
        "mean_relative_improvement": float(np.mean(values)),
        "material_improvement_seed_win_fraction": float(
            np.mean(values >= float(args.minimum_material_improvement))
        ),
        "bootstrap_median_relative_improvement_ci": {
            "lower": float(np.quantile(medians, alpha)),
            "median": float(np.quantile(medians, 0.5)),
            "upper": float(np.quantile(medians, 1.0 - alpha)),
        },
        "per_feature": per_feature,
        "head_healthy_seed_fraction": float(np.mean(head_healthy)) if head_healthy else 0.0,
        "maximum_parameter_overhead_fraction": float(np.max(overheads)) if overheads else math.inf,
    }


def _review_gates(statistics: dict[str, Any], args: argparse.Namespace) -> dict[str, bool]:
    feature_medians = [
        float(value.get("median_relative_regression"))
        for value in (statistics.get("per_feature") or {}).values()
    ]
    interval = statistics.get("bootstrap_median_relative_improvement_ci") or {}
    return {
        "median_improvement_meets_material_threshold": float(
            statistics.get("median_relative_improvement", -math.inf)
        )
        >= float(args.minimum_material_improvement),
        "seed_win_fraction_meets_threshold": float(
            statistics.get("material_improvement_seed_win_fraction", 0.0)
        )
        >= float(args.minimum_seed_win_fraction),
        "bootstrap_median_improvement_lower_meets_threshold": float(
            interval.get("lower", -math.inf)
        )
        >= float(args.minimum_bootstrap_median_improvement_lower),
        "no_feature_median_regression_above_limit": len(feature_medians) == 4
        and max(feature_medians) <= float(args.maximum_per_feature_median_regression),
        "head_healthy_seed_fraction_meets_threshold": float(
            statistics.get("head_healthy_seed_fraction", 0.0)
        )
        >= float(args.minimum_head_healthy_seed_fraction),
        "parameter_overhead_within_limit": float(
            statistics.get("maximum_parameter_overhead_fraction", math.inf)
        )
        <= float(args.maximum_parameter_overhead_fraction),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_report(payload: dict[str, Any]) -> str:
    stats = payload.get("statistics") or {}
    lines = [
        "# Multi-seed single-head versus multi-head tandem audit",
        "",
        f"- Overall status: `{payload.get('overall_status')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Seeds: {payload.get('model_seeds')}",
        f"- Median relative improvement: {stats.get('median_relative_improvement')}",
        f"- Material-win seed fraction: {stats.get('material_improvement_seed_win_fraction')}",
        "",
        "## Contract checks",
        "",
    ]
    lines.extend(f"- {name}: {value}" for name, value in (payload.get("checks") or {}).items())
    lines.extend(["", "## Scientific boundary", "", str(payload.get("scientific_boundary") or ""), ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
