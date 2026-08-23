#!/usr/bin/env python3
"""Run the dataset quality gates used before RFIC transformer training.

This orchestrator keeps the individual evidence scripts separate, but provides a
single reproducible command for MARS final-500 and wideband pilot acceptance.
It is conservative: it does not modify the dataset unless both
--backfill-frequency-metadata and --backfill-in-place are supplied.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    expected_outputs: dict[str, Path]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "dataset_quality_gates"
    out_dir.mkdir(parents=True, exist_ok=True)

    steps = _build_steps(dataset_dir, out_dir, args)
    results = [_run_step(step) for step in steps]
    overall_status = "FAIL" if any(result["status"] == "FAIL" for result in results) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "arguments": _argument_summary(args),
        "steps": results,
        "limitations": [
            "This orchestrator collects local dataset gates; it does not run EMX, HFSS, or ADS.",
            "A PASS means the available local artifacts passed the configured gates.",
            "Final production acceptance still requires simulator correlation for sampled designs.",
        ],
    }
    summary_path = out_dir / "dataset_quality_gates_summary.json"
    report_path = out_dir / "dataset_quality_gates_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for result in results:
        print(f"{result['status']:4s} {result['name']}: returncode={result['returncode']}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--require-emx", action="store_true", help="Require EM/S-parameter labels in validate_dataset.py")
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--max-correlation", type=float, default=0.35)
    parser.add_argument("--max-histogram-imbalance-frac", type=float, default=0.25)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument("--max-touchstone-frequency-checks", type=int, default=500)
    parser.add_argument("--backfill-frequency-metadata", action="store_true")
    parser.add_argument("--backfill-in-place", action="store_true")
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--skip-visualization", action="store_true")
    parser.add_argument("--skip-geometry-audit", action="store_true")
    parser.add_argument("--require-clearance-audit", action="store_true")
    parser.add_argument("--min-clearance-pass-fraction", type=float)
    parser.add_argument("--max-clearance-overlap-area-um2", type=float)
    parser.add_argument("--max-clearance-violation-area-um2", type=float)
    parser.add_argument("--allow-clearance-missing", action="store_true")
    parser.add_argument("--skip-touchstone-audit", action="store_true")
    parser.add_argument("--audit-sampling-distribution", action="store_true", help="Recompute input sampling uniformity from rows and bounds")
    parser.add_argument("--sampling-require-uniform-closer-than-normal", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sampling-min-uniform-vs-normal-fields-fraction", type=float, default=1.0)
    parser.add_argument("--sampling-min-histogram-entropy-frac", type=float)
    parser.add_argument("--sampling-max-min-norm", type=float, default=0.05)
    parser.add_argument("--sampling-min-max-norm", type=float, default=0.95)
    parser.add_argument("--sampling-space-filling-strata", type=int, default=20)
    parser.add_argument("--sampling-max-space-filling-empty-strata-frac", type=float, default=0.0)
    parser.add_argument("--sampling-max-space-filling-duplicate-frac", type=float, default=0.0)
    parser.add_argument("--sampling-space-filling-duplicate-round-decimals", type=int, default=12)
    parser.add_argument("--sampling-space-filling-max-nn-samples", type=int, default=2000)
    parser.add_argument("--sampling-min-space-filling-median-nn-distance", type=float)
    parser.add_argument("--extract-response-features", action="store_true", help="Extract Zin/K/Q/L labels from Touchstone files")
    parser.add_argument("--audit-response-feature-coverage", action="store_true", help="Audit K/Q/L/Cm response label coverage after extraction")
    parser.add_argument("--audit-s8p-physical-feature-dataset", action="store_true", help="Audit new .s8p physical-feature dataset readiness")
    parser.add_argument("--s8p-expected-count", type=int, default=500)
    parser.add_argument("--s8p-expected-ok-count", type=int)
    parser.add_argument("--s8p-max-touchstone-checks", type=int, default=500)
    parser.add_argument("--s8p-require-power-line-8port", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--audit-zin-coverage", action="store_true", help="Run Zin coverage audit after response extraction or on dataset rows")
    parser.add_argument("--audit-zin-sweep-coverage", action="store_true", help="Run wideband loaded-Zin coverage audit directly from Touchstone files")
    parser.add_argument("--plan-zin-balanced-acquisition", action="store_true", help="Plan next EMX acquisition targets from under-filled Zin bins")
    parser.add_argument("--build-zin-surrogate-candidates", action="store_true", help="Build predicted candidate Zin CSV from existing real labels for target-bin candidate selection")
    parser.add_argument("--zin-surrogate-candidate-count", type=int, default=5000)
    parser.add_argument("--zin-surrogate-prediction-batch-size", type=int, default=2048)
    parser.add_argument("--zin-surrogate-seed", type=int, default=20260614)
    parser.add_argument("--zin-surrogate-k-neighbors", type=int, default=8)
    parser.add_argument("--zin-surrogate-max-validation-rows", type=int, default=1000)
    parser.add_argument("--zin-surrogate-no-plots", action="store_true")
    parser.add_argument("--select-zin-targeted-candidates", action="store_true", help="Select candidate geometries from predicted Zin against the sparse-bin target plan")
    parser.add_argument("--zin-candidate-predictions-csv", help="CSV containing proposed geometries and predicted Zin for targeted candidate selection")
    parser.add_argument("--zin-candidate-pred-real-column")
    parser.add_argument("--zin-candidate-pred-imag-column")
    parser.add_argument("--zin-candidate-id-column", default="candidate_id")
    parser.add_argument("--zin-candidate-max-total", type=int)
    parser.add_argument("--zin-candidate-max-per-target", type=int)
    parser.add_argument("--zin-candidate-allow-outside-bin", action="store_true")
    parser.add_argument("--zin-candidate-reachable-targets-only", action="store_true")
    parser.add_argument("--zin-candidate-min-candidates-per-reachable-target", type=int, default=1)
    parser.add_argument("--zin-candidate-redistribute-reachable-quota", action="store_true")
    parser.add_argument("--plan-physical-feature-balanced-acquisition", action="store_true", help="Plan next acquisition targets from sparse Lp/Ls/Q/K bins")
    parser.add_argument("--physical-feature-columns", default="lp_nh_center,ls_nh_center,q_center,k_center")
    parser.add_argument("--derive-scalar-q-feature", action="store_true", help="Create an explicit scalar Q column such as q_center before physical-feature steps")
    parser.add_argument("--scalar-q-definition", choices=["min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary"])
    parser.add_argument("--scalar-q-output-column", default="q_center")
    parser.add_argument("--scalar-q-copy-touchstones", action="store_true")
    parser.add_argument("--scalar-q-absolute-touchstone-paths", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--physical-feature-bins", type=int, default=4)
    parser.add_argument("--physical-feature-target-envelope-config")
    parser.add_argument("--physical-feature-target-count-per-bin", type=int)
    parser.add_argument("--physical-feature-plan-desired-total-count", type=int)
    parser.add_argument("--physical-feature-plan-next-count", type=int, default=100)
    parser.add_argument("--physical-feature-plan-max-target-bins", type=int)
    parser.add_argument("--build-physical-feature-surrogate-candidates", action="store_true", help="Predict candidate Lp/Ls/Q/K values for acquisition prioritization")
    parser.add_argument("--physical-feature-surrogate-candidate-count", type=int, default=5000)
    parser.add_argument("--physical-feature-surrogate-prediction-batch-size", type=int, default=2048)
    parser.add_argument("--physical-feature-surrogate-seed", type=int, default=20260615)
    parser.add_argument("--physical-feature-surrogate-k-neighbors", type=int, default=8)
    parser.add_argument("--physical-feature-surrogate-max-validation-rows", type=int, default=1000)
    parser.add_argument("--physical-feature-surrogate-no-plots", action="store_true")
    parser.add_argument("--select-physical-feature-targeted-candidates", action="store_true", help="Select candidate geometries from predicted Lp/Ls/Q/K against sparse feature bins")
    parser.add_argument("--physical-feature-candidate-predictions-csv")
    parser.add_argument("--physical-feature-candidate-id-column", default="candidate_id")
    parser.add_argument("--physical-feature-candidate-max-total", type=int)
    parser.add_argument("--physical-feature-candidate-max-per-target", type=int)
    parser.add_argument("--physical-feature-candidate-allow-outside-bin", action="store_true")
    parser.add_argument("--physical-feature-candidate-reachable-targets-only", action="store_true")
    parser.add_argument("--physical-feature-candidate-min-candidates-per-reachable-target", type=int, default=1)
    parser.add_argument("--physical-feature-candidate-redistribute-reachable-quota", action="store_true")
    parser.add_argument("--select-physical-feature-validation-samples", action="store_true", help="Select EMX rows for HFSS/ADS validation from Lp/Ls/Q/K space")
    parser.add_argument("--physical-feature-validation-sample-count", type=int, default=1)
    parser.add_argument("--physical-feature-validation-seed", type=int, default=20260615)
    parser.add_argument("--physical-feature-validation-mode", choices=["random", "coverage_then_random"], default="random")
    parser.add_argument("--physical-feature-validation-require-touchstone-path", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--physical-feature-validation-check-touchstone-exists", action="store_true")
    parser.add_argument("--build-physical-feature-inverse-training-table", action="store_true", help="Build ML-ready Lp/Ls/Q/K -> geometry training table")
    parser.add_argument("--inverse-training-require-touchstone-path", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--inverse-training-check-touchstone-exists", action="store_true")
    parser.add_argument("--inverse-geometry-config", help="Config YAML used to force inverse geometry columns and validate predicted candidates")
    parser.add_argument("--predict-geometry-from-physical-features", action="store_true", help="Run baseline KNN inverse model for requested physical features")
    parser.add_argument("--inverse-target", action="append", default=[], help="Target physical feature as name=value; repeat for every feature")
    parser.add_argument("--inverse-target-json", help="JSON dict/list of target physical features")
    parser.add_argument("--inverse-candidate-count", type=int, default=1)
    parser.add_argument("--inverse-k-neighbors", type=int, default=8)
    parser.add_argument("--inverse-include-nearest-neighbor-candidates", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--select-hfss-samples", action="store_true", help="Select representative response-labeled samples for HFSS/ADS validation")
    parser.add_argument("--hfss-sample-count", type=int, default=8)
    parser.add_argument("--response-load-ohm", type=float, default=50.0)
    parser.add_argument("--response-min-valid-count", type=int)
    parser.add_argument("--response-require-cm", action="store_true")
    parser.add_argument("--response-min-lp-span-nh", type=float)
    parser.add_argument("--response-min-ls-span-nh", type=float)
    parser.add_argument("--response-min-k-span", type=float)
    parser.add_argument("--response-min-qp-span", type=float)
    parser.add_argument("--response-min-qs-span", type=float)
    parser.add_argument("--response-min-cm-single-primary-span-ff", type=float)
    parser.add_argument("--response-min-occupied-k-q-bins", type=int)
    parser.add_argument("--response-target-envelope-config", help="JSON file with reusable response K/Qp and Lp/Ls target-envelope bounds and thresholds")
    parser.add_argument("--response-target-k-min", type=float)
    parser.add_argument("--response-target-k-max", type=float)
    parser.add_argument("--response-target-qp-min", type=float)
    parser.add_argument("--response-target-qp-max", type=float)
    parser.add_argument("--response-min-target-k-qp-area-frac", type=float)
    parser.add_argument("--response-min-target-k-qp-occupied-2d-bins", type=int)
    parser.add_argument("--response-max-target-k-qp-outside-frac", type=float)
    parser.add_argument("--response-target-lp-min-nh", type=float)
    parser.add_argument("--response-target-lp-max-nh", type=float)
    parser.add_argument("--response-target-ls-min-nh", type=float)
    parser.add_argument("--response-target-ls-max-nh", type=float)
    parser.add_argument("--response-min-target-lp-ls-area-frac", type=float)
    parser.add_argument("--response-min-target-lp-ls-occupied-2d-bins", type=int)
    parser.add_argument("--response-max-target-lp-ls-outside-frac", type=float)
    parser.add_argument("--zin-min-valid-count", type=int)
    parser.add_argument("--zin-min-real-span-ohm", type=float)
    parser.add_argument("--zin-min-imag-span-ohm", type=float)
    parser.add_argument("--zin-min-abs-span-ohm", type=float)
    parser.add_argument("--zin-min-real-bins", type=int)
    parser.add_argument("--zin-min-imag-bins", type=int)
    parser.add_argument("--zin-min-occupied-2d-bins", type=int)
    parser.add_argument("--zin-target-count-per-bin", type=int, default=1)
    parser.add_argument("--zin-bins", type=int, default=10)
    parser.add_argument("--zin-target-envelope-config", help="JSON file with reusable Zin target-envelope bounds and thresholds")
    parser.add_argument("--zin-target-real-min-ohm", type=float)
    parser.add_argument("--zin-target-real-max-ohm", type=float)
    parser.add_argument("--zin-target-imag-min-ohm", type=float)
    parser.add_argument("--zin-target-imag-max-ohm", type=float)
    parser.add_argument("--zin-min-target-envelope-area-frac", type=float)
    parser.add_argument("--zin-min-target-envelope-occupied-2d-bins", type=int)
    parser.add_argument("--zin-max-target-envelope-outside-frac", type=float)
    parser.add_argument("--zin-sweep-frequency-slices-ghz", default="5,10,15,20,25,30,35,40,45,50")
    parser.add_argument("--zin-sweep-min-valid-count", type=int)
    parser.add_argument("--zin-sweep-min-real-span-ohm", type=float)
    parser.add_argument("--zin-sweep-min-imag-span-ohm", type=float)
    parser.add_argument("--zin-sweep-min-occupied-2d-bins", type=int)
    parser.add_argument("--zin-sweep-min-occupied-2d-frac", type=float)
    parser.add_argument("--zin-sweep-min-entropy-frac", type=float, default=0.70)
    parser.add_argument("--zin-plan-target-count-per-bin", type=int)
    parser.add_argument("--zin-plan-desired-total-count", type=int)
    parser.add_argument("--zin-plan-next-count", type=int, default=100)
    parser.add_argument("--zin-plan-max-target-bins", type=int)
    parser.add_argument("--zin-plan-no-plots", action="store_true")
    parser.add_argument("--touchstone-sample-size", type=int, default=20)
    parser.add_argument("--touchstone-seed", type=int, default=20260613)
    parser.add_argument("--touchstone-all", action="store_true")
    parser.add_argument("--touchstone-expected-ports", type=int, default=4)
    parser.add_argument("--touchstone-port-pairs", default="1,2:3,4")
    parser.add_argument(
        "--touchstone-ground-unused-ports",
        action="store_true",
        help="Short ports outside the selected differential pairs to ground for Touchstone audits and response-feature extraction.",
    )
    parser.add_argument("--touchstone-positive-window-start-ghz", type=float)
    parser.add_argument("--touchstone-positive-window-stop-ghz", type=float)
    parser.add_argument("--touchstone-shape-window-start-ghz", type=float)
    parser.add_argument("--touchstone-shape-window-stop-ghz", type=float)
    parser.add_argument("--touchstone-max-shape-spike-ratio", type=float, default=8.0)
    parser.add_argument("--touchstone-max-shape-relative-step", type=float, default=0.5)
    parser.add_argument("--touchstone-target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--max-scatter-dims", type=int, default=6)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _build_steps(dataset_dir: Path, out_dir: Path, args: argparse.Namespace) -> list[Step]:
    steps: list[Step] = []
    freq_args = _frequency_args(args)
    if args.backfill_frequency_metadata:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "backfill_dataset_frequency_metadata.py"),
            str(dataset_dir),
            *freq_args,
            "--frequency-tolerance-hz",
            str(args.frequency_tolerance_hz),
            "--summary",
            str(out_dir / "frequency_backfill_summary.json"),
            "--no-fail-exit",
        ]
        if args.backfill_in_place:
            cmd.append("--in-place")
        else:
            cmd.extend(
                [
                    "--output-csv",
                    str(out_dir / "dataset_rows_frequency_backfilled.csv"),
                    "--output-manifest",
                    str(out_dir / "dataset_manifest_frequency_backfilled.json"),
                ]
            )
        steps.append(
            Step(
                "frequency metadata backfill",
                cmd,
                {"summary": out_dir / "frequency_backfill_summary.json"},
            )
        )

    if not args.skip_validation:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "validate_dataset.py"),
            str(dataset_dir),
            "--expected-port-mode",
            args.expected_port_mode,
            "--expected-pin-purpose",
            str(args.expected_pin_purpose),
            "--max-correlation",
            str(args.max_correlation),
            "--max-histogram-imbalance-frac",
            str(args.max_histogram_imbalance_frac),
            *freq_args,
            "--frequency-tolerance-hz",
            str(args.frequency_tolerance_hz),
            "--max-touchstone-frequency-checks",
            str(args.max_touchstone_frequency_checks),
            "--report",
            str(out_dir / "validation_report.md"),
            "--summary",
            str(out_dir / "validation_summary.json"),
            "--no-fail-exit",
        ]
        if args.require_emx:
            cmd.append("--require-emx")
        steps.append(Step("dataset validation", cmd, {"summary": out_dir / "validation_summary.json", "report": out_dir / "validation_report.md"}))

    if not args.skip_visualization:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "visualize_dataset_quality.py"),
            str(dataset_dir),
            "--out-dir",
            str(out_dir / "dataset_visualizations"),
            "--bins",
            str(args.bins),
            "--max-scatter-dims",
            str(args.max_scatter_dims),
            *freq_args,
            "--frequency-tolerance-hz",
            str(args.frequency_tolerance_hz),
            "--require-report-ready",
        ]
        steps.append(
            Step(
                "dataset visualization",
                cmd,
                {
                    "summary": out_dir / "dataset_visualizations" / "visualization_summary.json",
                    "index": out_dir / "dataset_visualizations" / "visual_report_index.md",
                },
            )
        )

    if args.audit_sampling_distribution:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_sampling_distribution.py"),
            str(dataset_dir),
            "--out-dir",
            str(out_dir / "sampling_distribution_audit"),
            "--bins",
            str(args.bins),
            "--max-histogram-imbalance-frac",
            str(args.max_histogram_imbalance_frac),
            "--max-min-norm",
            str(args.sampling_max_min_norm),
            "--min-max-norm",
            str(args.sampling_min_max_norm),
            "--max-correlation",
            str(args.max_correlation),
            "--min-uniform-vs-normal-fields-fraction",
            str(args.sampling_min_uniform_vs_normal_fields_fraction),
            "--space-filling-strata",
            str(args.sampling_space_filling_strata),
            "--max-space-filling-empty-strata-frac",
            str(args.sampling_max_space_filling_empty_strata_frac),
            "--max-space-filling-duplicate-frac",
            str(args.sampling_max_space_filling_duplicate_frac),
            "--space-filling-duplicate-round-decimals",
            str(args.sampling_space_filling_duplicate_round_decimals),
            "--space-filling-max-nn-samples",
            str(args.sampling_space_filling_max_nn_samples),
            "--no-fail-exit",
        ]
        cmd.append(
            "--require-uniform-closer-than-normal"
            if args.sampling_require_uniform_closer_than_normal
            else "--no-require-uniform-closer-than-normal"
        )
        _append_optional(cmd, "--min-histogram-entropy-frac", args.sampling_min_histogram_entropy_frac)
        _append_optional(cmd, "--min-space-filling-median-nn-distance", args.sampling_min_space_filling_median_nn_distance)
        steps.append(
            Step(
                "sampling distribution audit",
                cmd,
                {
                    "summary": out_dir / "sampling_distribution_audit" / "sampling_distribution_audit_summary.json",
                    "report": out_dir / "sampling_distribution_audit" / "sampling_distribution_audit_report.md",
                    "fields": out_dir / "sampling_distribution_audit" / "sampling_distribution_fields.csv",
                },
            )
        )

    if not args.skip_geometry_audit:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_geometry_quality.py"),
            str(dataset_dir),
            "--out-dir",
            str(out_dir / "geometry_quality_audit"),
            "--expected-port-mode",
            args.expected_port_mode,
            "--expected-pin-purpose",
            str(args.expected_pin_purpose),
            "--no-fail-exit",
        ]
        if args.require_clearance_audit:
            cmd.append("--require-clearance-audit")
        if args.allow_clearance_missing:
            cmd.append("--allow-clearance-missing")
        _append_optional(cmd, "--min-clearance-pass-fraction", args.min_clearance_pass_fraction)
        _append_optional(cmd, "--max-clearance-overlap-area-um2", args.max_clearance_overlap_area_um2)
        _append_optional(cmd, "--max-clearance-violation-area-um2", args.max_clearance_violation_area_um2)
        steps.append(
            Step(
                "geometry quality audit",
                cmd,
                {
                    "summary": out_dir / "geometry_quality_audit" / "geometry_quality_audit_summary.json",
                    "report": out_dir / "geometry_quality_audit" / "geometry_quality_audit_report.md",
                },
            )
        )

    if not args.skip_touchstone_audit:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_dataset_touchstones.py"),
            str(dataset_dir),
            "--out-dir",
            str(out_dir / "dataset_touchstone_preflight"),
            "--sample-size",
            str(args.touchstone_sample_size),
            "--seed",
            str(args.touchstone_seed),
            "--expected-ports",
            str(args.touchstone_expected_ports),
            "--port-pairs",
            str(args.touchstone_port_pairs),
            *freq_args,
            "--frequency-tolerance-hz",
            str(args.frequency_tolerance_hz),
            "--required-sweep-start-ghz",
            str(args.expected_frequency_start_ghz if args.expected_frequency_start_ghz is not None else args.touchstone_target_frequency_ghz),
            "--required-sweep-stop-ghz",
            str(args.expected_frequency_stop_ghz if args.expected_frequency_stop_ghz is not None else args.touchstone_target_frequency_ghz),
            "--target-frequency-ghz",
            str(args.touchstone_target_frequency_ghz),
            "--plot-first",
            "--no-fail-exit",
        ]
        if args.touchstone_all:
            cmd.append("--all")
        if args.touchstone_ground_unused_ports:
            cmd.append("--ground-unused-ports")
        if args.touchstone_positive_window_start_ghz is not None:
            cmd.extend(["--positive-window-start-ghz", str(args.touchstone_positive_window_start_ghz)])
        if args.touchstone_positive_window_stop_ghz is not None:
            cmd.extend(["--positive-window-stop-ghz", str(args.touchstone_positive_window_stop_ghz)])
        if args.touchstone_shape_window_start_ghz is not None:
            cmd.extend(["--shape-window-start-ghz", str(args.touchstone_shape_window_start_ghz)])
        if args.touchstone_shape_window_stop_ghz is not None:
            cmd.extend(["--shape-window-stop-ghz", str(args.touchstone_shape_window_stop_ghz)])
        if args.touchstone_shape_window_start_ghz is not None or args.touchstone_shape_window_stop_ghz is not None:
            cmd.extend(["--max-shape-spike-ratio", str(args.touchstone_max_shape_spike_ratio)])
            cmd.extend(["--max-shape-relative-step", str(args.touchstone_max_shape_relative_step)])
        steps.append(
            Step(
                "dataset Touchstone preflight",
                cmd,
                {
                    "summary": out_dir / "dataset_touchstone_preflight" / "dataset_touchstone_audit_summary.json",
                    "report": out_dir / "dataset_touchstone_preflight" / "dataset_touchstone_audit_report.md",
                },
            )
        )

    response_features_dir = out_dir / "response_features"
    if args.extract_response_features:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "extract_touchstone_response_features.py"),
            str(dataset_dir),
            "--out-dir",
            str(response_features_dir),
            "--target-frequency-ghz",
            str(args.touchstone_target_frequency_ghz),
            "--expected-ports",
            str(args.touchstone_expected_ports),
            "--port-pairs",
            str(args.touchstone_port_pairs),
            "--load-ohm",
            str(args.response_load_ohm),
            *freq_args,
            "--frequency-tolerance-hz",
            str(args.frequency_tolerance_hz),
            "--no-fail-exit",
        ]
        if args.touchstone_ground_unused_ports:
            cmd.append("--ground-unused-ports")
        steps.append(
            Step(
                "response feature extraction",
                cmd,
                {
                    "summary": response_features_dir / "response_feature_extraction_summary.json",
                    "report": response_features_dir / "response_feature_extraction_report.md",
                    "features": response_features_dir / "response_features.csv",
                    "dataset_rows": response_features_dir / "dataset_rows.csv",
                },
            )
        )

    response_or_dataset_source = response_features_dir if args.extract_response_features else dataset_dir
    scalar_q_source = out_dir / "scalar_q_feature_dataset"
    physical_feature_source = scalar_q_source if args.derive_scalar_q_feature else response_or_dataset_source

    if args.derive_scalar_q_feature:
        if not args.scalar_q_definition:
            raise ValueError("--derive-scalar-q-feature requires --scalar-q-definition")
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "derive_scalar_q_feature.py"),
            str(response_or_dataset_source),
            "--out-dir",
            str(scalar_q_source),
            "--q-definition",
            str(args.scalar_q_definition),
            "--output-column",
            str(args.scalar_q_output_column),
            "--no-fail-exit",
        ]
        if args.scalar_q_copy_touchstones:
            cmd.append("--copy-touchstones")
        cmd.append(
            "--absolute-touchstone-paths"
            if args.scalar_q_absolute_touchstone_paths
            else "--no-absolute-touchstone-paths"
        )
        steps.append(
            Step(
                "scalar Q feature derivation",
                cmd,
                {
                    "summary": scalar_q_source / "scalar_q_feature_summary.json",
                    "report": scalar_q_source / "scalar_q_feature_report.md",
                    "dataset_rows": scalar_q_source / "dataset_rows.csv",
                    "manifest": scalar_q_source / "dataset_manifest.json",
                },
            )
        )

    if args.audit_response_feature_coverage:
        response_source = response_or_dataset_source
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_response_feature_coverage.py"),
            str(response_source),
            "--out-dir",
            str(out_dir / "response_feature_coverage_audit"),
            "--bins",
            str(args.zin_bins),
            "--target-count-per-bin",
            str(args.zin_target_count_per_bin),
            "--no-fail-exit",
        ]
        if args.response_require_cm:
            cmd.append("--require-cm")
        _append_optional(cmd, "--min-valid-count", args.response_min_valid_count)
        _append_optional(cmd, "--min-lp-span-nh", args.response_min_lp_span_nh)
        _append_optional(cmd, "--min-ls-span-nh", args.response_min_ls_span_nh)
        _append_optional(cmd, "--min-k-span", args.response_min_k_span)
        _append_optional(cmd, "--min-qp-span", args.response_min_qp_span)
        _append_optional(cmd, "--min-qs-span", args.response_min_qs_span)
        _append_optional(cmd, "--min-cm-single-primary-span-ff", args.response_min_cm_single_primary_span_ff)
        _append_optional(cmd, "--min-occupied-k-q-bins", args.response_min_occupied_k_q_bins)
        _append_optional(cmd, "--target-envelope-config", args.response_target_envelope_config)
        _append_optional(cmd, "--target-k-min", args.response_target_k_min)
        _append_optional(cmd, "--target-k-max", args.response_target_k_max)
        _append_optional(cmd, "--target-qp-min", args.response_target_qp_min)
        _append_optional(cmd, "--target-qp-max", args.response_target_qp_max)
        _append_optional(cmd, "--min-target-k-qp-area-frac", args.response_min_target_k_qp_area_frac)
        _append_optional(cmd, "--min-target-k-qp-occupied-2d-bins", args.response_min_target_k_qp_occupied_2d_bins)
        _append_optional(cmd, "--max-target-k-qp-outside-frac", args.response_max_target_k_qp_outside_frac)
        _append_optional(cmd, "--target-lp-min-nh", args.response_target_lp_min_nh)
        _append_optional(cmd, "--target-lp-max-nh", args.response_target_lp_max_nh)
        _append_optional(cmd, "--target-ls-min-nh", args.response_target_ls_min_nh)
        _append_optional(cmd, "--target-ls-max-nh", args.response_target_ls_max_nh)
        _append_optional(cmd, "--min-target-lp-ls-area-frac", args.response_min_target_lp_ls_area_frac)
        _append_optional(cmd, "--min-target-lp-ls-occupied-2d-bins", args.response_min_target_lp_ls_occupied_2d_bins)
        _append_optional(cmd, "--max-target-lp-ls-outside-frac", args.response_max_target_lp_ls_outside_frac)
        steps.append(
            Step(
                "response feature coverage audit",
                cmd,
                {
                    "summary": out_dir / "response_feature_coverage_audit" / "response_feature_coverage_summary.json",
                    "report": out_dir / "response_feature_coverage_audit" / "response_feature_coverage_report.md",
                    "points": out_dir / "response_feature_coverage_audit" / "response_feature_points.csv",
                    "metrics": out_dir / "response_feature_coverage_audit" / "response_feature_metric_summary.csv",
                },
            )
        )

    if args.audit_s8p_physical_feature_dataset:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_s8p_physical_feature_dataset.py"),
            str(physical_feature_source),
            "--out-dir",
            str(out_dir / "s8p_physical_feature_dataset_audit"),
            "--expected-count",
            str(args.s8p_expected_count),
            "--expected-port-mode",
            str(args.expected_port_mode),
            "--max-touchstone-checks",
            str(args.s8p_max_touchstone_checks),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--expected-ok-count", args.s8p_expected_ok_count)
        _append_optional(cmd, "--expected-frequency-start-ghz", args.expected_frequency_start_ghz)
        _append_optional(cmd, "--expected-frequency-stop-ghz", args.expected_frequency_stop_ghz)
        _append_optional(cmd, "--expected-frequency-step-ghz", args.expected_frequency_step_ghz)
        _append_optional(cmd, "--expected-frequency-points", args.expected_frequency_points)
        _append_optional(cmd, "--scalar-q-definition", args.scalar_q_definition)
        _append_optional(cmd, "--coverage-feature-columns", args.physical_feature_columns)
        cmd.append(
            "--require-power-line-8port"
            if args.s8p_require_power_line_8port
            else "--no-require-power-line-8port"
        )
        if args.touchstone_ground_unused_ports:
            cmd.append("--ground-unused-ports")
        steps.append(
            Step(
                "S8P physical-feature dataset audit",
                cmd,
                {
                    "summary": out_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_dataset_audit_summary.json",
                    "report": out_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_dataset_audit_report.md",
                    "touchstone_csv": out_dir / "s8p_physical_feature_dataset_audit" / "s8p_touchstone_checks.csv",
                    "coverage_marginals": out_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_marginal_histograms.png",
                    "coverage_pairwise": out_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_pairwise_scatter.png",
                    "coverage_heatmaps": out_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_pair_heatmaps.png",
                },
            )
        )

    if args.plan_physical_feature_balanced_acquisition:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "plan_physical_feature_balanced_acquisition.py"),
            str(physical_feature_source),
            "--out-dir",
            str(out_dir / "physical_feature_balanced_acquisition_plan"),
            "--feature-columns",
            str(args.physical_feature_columns),
            "--bins",
            str(args.physical_feature_bins),
            "--next-count",
            str(args.physical_feature_plan_next_count),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--target-envelope-config", args.physical_feature_target_envelope_config)
        _append_optional(cmd, "--target-count-per-bin", args.physical_feature_target_count_per_bin)
        _append_optional(cmd, "--desired-total-count", args.physical_feature_plan_desired_total_count)
        _append_optional(cmd, "--max-target-bins", args.physical_feature_plan_max_target_bins)
        steps.append(
            Step(
                "physical-feature balanced acquisition plan",
                cmd,
                {
                    "summary": out_dir / "physical_feature_balanced_acquisition_plan" / "physical_feature_acquisition_plan_summary.json",
                    "report": out_dir / "physical_feature_balanced_acquisition_plan" / "physical_feature_acquisition_plan_report.md",
                    "bins": out_dir / "physical_feature_balanced_acquisition_plan" / "physical_feature_acquisition_bins.csv",
                    "targets": out_dir / "physical_feature_balanced_acquisition_plan" / "physical_feature_acquisition_targets.csv",
                },
            )
        )

    if args.build_physical_feature_surrogate_candidates:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "build_physical_feature_surrogate_candidate_predictions.py"),
            str(physical_feature_source),
            "--out-dir",
            str(out_dir / "physical_feature_surrogate_candidate_predictions"),
            "--candidate-count",
            str(args.physical_feature_surrogate_candidate_count),
            "--prediction-batch-size",
            str(args.physical_feature_surrogate_prediction_batch_size),
            "--seed",
            str(args.physical_feature_surrogate_seed),
            "--k-neighbors",
            str(args.physical_feature_surrogate_k_neighbors),
            "--max-validation-rows",
            str(args.physical_feature_surrogate_max_validation_rows),
            "--feature-columns",
            str(args.physical_feature_columns),
            "--no-fail-exit",
        ]
        if args.physical_feature_surrogate_no_plots:
            cmd.append("--no-plots")
        steps.append(
            Step(
                "physical-feature surrogate candidate prediction",
                cmd,
                {
                    "summary": out_dir / "physical_feature_surrogate_candidate_predictions" / "candidate_physical_feature_prediction_summary.json",
                    "report": out_dir / "physical_feature_surrogate_candidate_predictions" / "candidate_physical_feature_prediction_report.md",
                    "candidates": out_dir / "physical_feature_surrogate_candidate_predictions" / "candidate_physical_feature_predictions.csv",
                },
            )
        )

    if args.select_physical_feature_targeted_candidates:
        candidate_csv = args.physical_feature_candidate_predictions_csv or str(
            out_dir / "physical_feature_surrogate_candidate_predictions" / "candidate_physical_feature_predictions.csv"
        )
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "select_physical_feature_targeted_candidate_geometries.py"),
            "--plan-dir",
            str(out_dir / "physical_feature_balanced_acquisition_plan"),
            "--candidate-csv",
            str(candidate_csv or ""),
            "--out-dir",
            str(out_dir / "physical_feature_targeted_candidate_selection"),
            "--feature-columns",
            str(args.physical_feature_columns),
            "--candidate-id-column",
            str(args.physical_feature_candidate_id_column),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--max-total", args.physical_feature_candidate_max_total)
        _append_optional(cmd, "--max-per-target", args.physical_feature_candidate_max_per_target)
        if args.physical_feature_candidate_allow_outside_bin:
            cmd.append("--allow-outside-bin")
        if args.physical_feature_candidate_reachable_targets_only:
            cmd.append("--reachable-targets-only")
        if args.physical_feature_candidate_redistribute_reachable_quota:
            cmd.append("--redistribute-reachable-quota")
        _append_optional(
            cmd,
            "--min-candidates-per-reachable-target",
            args.physical_feature_candidate_min_candidates_per_reachable_target,
        )
        steps.append(
            Step(
                "physical-feature targeted candidate geometry selection",
                cmd,
                {
                    "summary": out_dir / "physical_feature_targeted_candidate_selection" / "physical_feature_targeted_candidate_selection_summary.json",
                    "report": out_dir / "physical_feature_targeted_candidate_selection" / "physical_feature_targeted_candidate_selection_report.md",
                    "selected": out_dir / "physical_feature_targeted_candidate_selection" / "physical_feature_targeted_candidate_selection.csv",
                },
            )
        )

    if args.select_physical_feature_validation_samples:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "select_physical_feature_validation_samples.py"),
            str(physical_feature_source),
            "--out-dir",
            str(out_dir / "physical_feature_validation_sample_selection"),
            "--feature-columns",
            str(args.physical_feature_columns),
            "--sample-count",
            str(args.physical_feature_validation_sample_count),
            "--seed",
            str(args.physical_feature_validation_seed),
            "--mode",
            str(args.physical_feature_validation_mode),
            "--no-fail-exit",
        ]
        cmd.append(
            "--require-touchstone-path"
            if args.physical_feature_validation_require_touchstone_path
            else "--no-require-touchstone-path"
        )
        if args.physical_feature_validation_check_touchstone_exists:
            cmd.append("--check-touchstone-exists")
        steps.append(
            Step(
                "physical-feature validation sample selection",
                cmd,
                {
                    "summary": out_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_sample_summary.json",
                    "report": out_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_sample_report.md",
                    "selected": out_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_samples.csv",
                },
            )
        )

    if args.build_physical_feature_inverse_training_table:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "build_physical_feature_inverse_training_table.py"),
            str(physical_feature_source),
            "--out-dir",
            str(out_dir / "physical_feature_inverse_training_table"),
            "--feature-columns",
            str(args.physical_feature_columns),
            "--no-fail-exit",
        ]
        cmd.append(
            "--require-touchstone-path"
            if args.inverse_training_require_touchstone_path
            else "--no-require-touchstone-path"
        )
        if args.inverse_training_check_touchstone_exists:
            cmd.append("--check-touchstone-exists")
        _append_optional(cmd, "--config", args.inverse_geometry_config)
        steps.append(
            Step(
                "physical-feature inverse training table",
                cmd,
                {
                    "summary": out_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
                    "report": out_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_report.md",
                    "table": out_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_table.csv",
                },
            )
        )

    if args.predict_geometry_from_physical_features:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "predict_geometry_from_physical_features.py"),
            "--training-csv",
            str(out_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_table.csv"),
            "--out-dir",
            str(out_dir / "physical_feature_inverse_geometry_prediction"),
            "--candidate-count",
            str(args.inverse_candidate_count),
            "--k-neighbors",
            str(args.inverse_k_neighbors),
            "--no-fail-exit",
        ]
        for target in args.inverse_target:
            cmd.extend(["--target", str(target)])
        _append_optional(cmd, "--target-json", args.inverse_target_json)
        _append_optional(cmd, "--config", args.inverse_geometry_config)
        cmd.append(
            "--include-nearest-neighbor-candidates"
            if args.inverse_include_nearest_neighbor_candidates
            else "--no-include-nearest-neighbor-candidates"
        )
        steps.append(
            Step(
                "physical-feature inverse geometry prediction",
                cmd,
                {
                    "summary": out_dir / "physical_feature_inverse_geometry_prediction" / "physical_feature_inverse_prediction_summary.json",
                    "report": out_dir / "physical_feature_inverse_geometry_prediction" / "physical_feature_inverse_prediction_report.md",
                    "candidates": out_dir / "physical_feature_inverse_geometry_prediction" / "physical_feature_inverse_geometry_candidates.csv",
                },
            )
        )

    if args.audit_zin_coverage:
        zin_source = response_features_dir if args.extract_response_features else dataset_dir
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_zin_coverage.py"),
            str(zin_source),
            "--out-dir",
            str(out_dir / "zin_coverage_audit"),
            "--bins",
            str(args.zin_bins),
            "--target-count-per-bin",
            str(args.zin_target_count_per_bin),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--min-valid-count", args.zin_min_valid_count)
        _append_optional(cmd, "--min-real-span-ohm", args.zin_min_real_span_ohm)
        _append_optional(cmd, "--min-imag-span-ohm", args.zin_min_imag_span_ohm)
        _append_optional(cmd, "--min-abs-span-ohm", args.zin_min_abs_span_ohm)
        _append_optional(cmd, "--min-real-bins", args.zin_min_real_bins)
        _append_optional(cmd, "--min-imag-bins", args.zin_min_imag_bins)
        _append_optional(cmd, "--min-occupied-2d-bins", args.zin_min_occupied_2d_bins)
        _append_optional(cmd, "--target-envelope-config", args.zin_target_envelope_config)
        _append_optional(cmd, "--target-real-min-ohm", args.zin_target_real_min_ohm)
        _append_optional(cmd, "--target-real-max-ohm", args.zin_target_real_max_ohm)
        _append_optional(cmd, "--target-imag-min-ohm", args.zin_target_imag_min_ohm)
        _append_optional(cmd, "--target-imag-max-ohm", args.zin_target_imag_max_ohm)
        _append_optional(cmd, "--min-target-envelope-area-frac", args.zin_min_target_envelope_area_frac)
        _append_optional(cmd, "--min-target-envelope-occupied-2d-bins", args.zin_min_target_envelope_occupied_2d_bins)
        _append_optional(cmd, "--max-target-envelope-outside-frac", args.zin_max_target_envelope_outside_frac)
        steps.append(
            Step(
                "Zin coverage audit",
                cmd,
                {
                    "summary": out_dir / "zin_coverage_audit" / "zin_coverage_audit_summary.json",
                    "report": out_dir / "zin_coverage_audit" / "zin_coverage_audit_report.md",
                    "points": out_dir / "zin_coverage_audit" / "zin_coverage_points.csv",
                },
            )
        )

    if args.plan_zin_balanced_acquisition:
        zin_plan_source = response_features_dir if args.extract_response_features else dataset_dir
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "plan_zin_balanced_acquisition.py"),
            str(zin_plan_source),
            "--out-dir",
            str(out_dir / "zin_balanced_acquisition_plan"),
            "--bins",
            str(args.zin_bins),
            "--next-count",
            str(args.zin_plan_next_count),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--target-envelope-config", args.zin_target_envelope_config)
        _append_optional(cmd, "--target-real-min-ohm", args.zin_target_real_min_ohm)
        _append_optional(cmd, "--target-real-max-ohm", args.zin_target_real_max_ohm)
        _append_optional(cmd, "--target-imag-min-ohm", args.zin_target_imag_min_ohm)
        _append_optional(cmd, "--target-imag-max-ohm", args.zin_target_imag_max_ohm)
        _append_optional(cmd, "--target-count-per-bin", args.zin_plan_target_count_per_bin)
        _append_optional(cmd, "--desired-total-count", args.zin_plan_desired_total_count)
        _append_optional(cmd, "--max-target-bins", args.zin_plan_max_target_bins)
        if args.zin_plan_no_plots:
            cmd.append("--no-plots")
        steps.append(
            Step(
                "Zin balanced acquisition plan",
                cmd,
                {
                    "summary": out_dir / "zin_balanced_acquisition_plan" / "zin_balanced_acquisition_plan_summary.json",
                    "report": out_dir / "zin_balanced_acquisition_plan" / "zin_balanced_acquisition_plan_report.md",
                    "bins": out_dir / "zin_balanced_acquisition_plan" / "zin_balanced_acquisition_bins.csv",
                    "targets": out_dir / "zin_balanced_acquisition_plan" / "zin_balanced_acquisition_targets.csv",
                },
            )
        )

    if args.build_zin_surrogate_candidates:
        surrogate_source = response_features_dir if args.extract_response_features else dataset_dir
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "build_zin_surrogate_candidate_predictions.py"),
            str(surrogate_source),
            "--out-dir",
            str(out_dir / "zin_surrogate_candidate_predictions"),
            "--candidate-count",
            str(args.zin_surrogate_candidate_count),
            "--prediction-batch-size",
            str(args.zin_surrogate_prediction_batch_size),
            "--seed",
            str(args.zin_surrogate_seed),
            "--k-neighbors",
            str(args.zin_surrogate_k_neighbors),
            "--max-validation-rows",
            str(args.zin_surrogate_max_validation_rows),
            "--real-column",
            "zin_center_real_ohm",
            "--imag-column",
            "zin_center_imag_ohm",
            "--no-fail-exit",
        ]
        if args.zin_surrogate_no_plots:
            cmd.append("--no-plots")
        steps.append(
            Step(
                "Zin surrogate candidate prediction",
                cmd,
                {
                    "summary": out_dir / "zin_surrogate_candidate_predictions" / "candidate_zin_prediction_summary.json",
                    "report": out_dir / "zin_surrogate_candidate_predictions" / "candidate_zin_prediction_report.md",
                    "candidates": out_dir / "zin_surrogate_candidate_predictions" / "candidate_zin_predictions.csv",
                },
            )
        )

    if args.select_zin_targeted_candidates:
        candidate_csv = args.zin_candidate_predictions_csv or str(out_dir / "zin_surrogate_candidate_predictions" / "candidate_zin_predictions.csv")
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "select_zin_targeted_candidate_geometries.py"),
            "--plan-dir",
            str(out_dir / "zin_balanced_acquisition_plan"),
            "--candidate-csv",
            str(candidate_csv or ""),
            "--out-dir",
            str(out_dir / "zin_targeted_candidate_selection"),
            "--candidate-id-column",
            str(args.zin_candidate_id_column),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--pred-real-column", args.zin_candidate_pred_real_column)
        _append_optional(cmd, "--pred-imag-column", args.zin_candidate_pred_imag_column)
        _append_optional(cmd, "--max-total", args.zin_candidate_max_total)
        _append_optional(cmd, "--max-per-target", args.zin_candidate_max_per_target)
        if args.zin_candidate_allow_outside_bin:
            cmd.append("--allow-outside-bin")
        if args.zin_candidate_reachable_targets_only:
            cmd.append("--reachable-targets-only")
        if args.zin_candidate_redistribute_reachable_quota:
            cmd.append("--redistribute-reachable-quota")
        _append_optional(
            cmd,
            "--min-candidates-per-reachable-target",
            args.zin_candidate_min_candidates_per_reachable_target,
        )
        steps.append(
            Step(
                "Zin-targeted candidate geometry selection",
                cmd,
                {
                    "summary": out_dir / "zin_targeted_candidate_selection" / "zin_targeted_candidate_selection_summary.json",
                    "report": out_dir / "zin_targeted_candidate_selection" / "zin_targeted_candidate_selection_report.md",
                    "selected": out_dir / "zin_targeted_candidate_selection" / "zin_targeted_candidate_selection.csv",
                },
            )
        )

    if args.audit_zin_sweep_coverage:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "audit_zin_sweep_coverage.py"),
            str(dataset_dir),
            "--out-dir",
            str(out_dir / "zin_sweep_coverage_audit"),
            "--port-pairs",
            "1,2:3,4",
            "--load-ohm",
            str(args.response_load_ohm),
            "--frequency-slices-ghz",
            str(args.zin_sweep_frequency_slices_ghz),
            "--bins",
            str(args.zin_bins),
            *freq_args,
            "--frequency-tolerance-hz",
            str(args.frequency_tolerance_hz),
            "--no-fail-exit",
        ]
        _append_optional(cmd, "--min-valid-count", args.zin_sweep_min_valid_count)
        _append_optional(cmd, "--min-real-span-ohm", args.zin_sweep_min_real_span_ohm)
        _append_optional(cmd, "--min-imag-span-ohm", args.zin_sweep_min_imag_span_ohm)
        _append_optional(cmd, "--min-occupied-2d-bins", args.zin_sweep_min_occupied_2d_bins)
        _append_optional(cmd, "--min-occupied-2d-frac", args.zin_sweep_min_occupied_2d_frac)
        _append_optional(cmd, "--min-entropy-frac", args.zin_sweep_min_entropy_frac)
        steps.append(
            Step(
                "wideband Zin sweep coverage audit",
                cmd,
                {
                    "summary": out_dir / "zin_sweep_coverage_audit" / "zin_sweep_coverage_summary.json",
                    "report": out_dir / "zin_sweep_coverage_audit" / "zin_sweep_coverage_report.md",
                    "points": out_dir / "zin_sweep_coverage_audit" / "zin_sweep_points.csv",
                    "frequency_summary": out_dir / "zin_sweep_coverage_audit" / "zin_sweep_frequency_summary.csv",
                },
            )
        )

    if args.select_hfss_samples:
        selector_source = response_features_dir if args.extract_response_features else dataset_dir
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "select_hfss_validation_samples.py"),
            str(selector_source),
            "--out-dir",
            str(out_dir / "hfss_validation_sample_selection"),
            "--sample-count",
            str(args.hfss_sample_count),
            "--seed",
            str(args.touchstone_seed),
            "--bins",
            str(args.zin_bins),
            "--target-frequency-ghz",
            str(args.touchstone_target_frequency_ghz),
            "--no-fail-exit",
        ]
        steps.append(
            Step(
                "HFSS validation sample selection",
                cmd,
                {
                    "summary": out_dir / "hfss_validation_sample_selection" / "hfss_validation_sample_selection_summary.json",
                    "report": out_dir / "hfss_validation_sample_selection" / "hfss_validation_sample_selection_report.md",
                    "samples": out_dir / "hfss_validation_sample_selection" / "hfss_validation_samples.csv",
                },
            )
        )
    return steps


def _run_step(step: Step) -> dict[str, Any]:
    completed = subprocess.run(step.command, check=False, text=True, capture_output=True)
    outputs = {
        name: {"path": str(path), "exists": path.exists(), "bytes": path.stat().st_size if path.exists() else None}
        for name, path in step.expected_outputs.items()
    }
    output_status = _output_status(step.expected_outputs)
    status = "PASS" if completed.returncode == 0 and output_status["status"] == "PASS" else "FAIL"
    return {
        "name": step.name,
        "status": status,
        "returncode": int(completed.returncode),
        "output_status": output_status,
        "command": step.command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "outputs": outputs,
    }


def _output_status(outputs: dict[str, Path]) -> dict[str, Any]:
    reasons: list[str] = []
    for name, path in outputs.items():
        if name != "summary" or not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - keep exact parser issue.
            reasons.append(f"{path}: cannot parse JSON summary: {exc}")
            continue
        overall_status = str(data.get("overall_status", "")).upper()
        if overall_status and overall_status != "PASS":
            reasons.append(f"{path}: overall_status={overall_status}")
        status = str(data.get("status", "")).upper()
        if status and status != "PASS":
            reasons.append(f"{path}: status={status}")
        data_status = data.get("data_status")
        if isinstance(data_status, dict) and data_status.get("report_ready") is False:
            reasons.append(f"{path}: report_ready=false")
    return {"status": "FAIL" if reasons else "PASS", "reasons": reasons}


def _frequency_args(args: argparse.Namespace) -> list[str]:
    result: list[str] = []
    _append_optional(result, "--expected-frequency-start-ghz", args.expected_frequency_start_ghz)
    _append_optional(result, "--expected-frequency-stop-ghz", args.expected_frequency_stop_ghz)
    _append_optional(result, "--expected-frequency-step-ghz", args.expected_frequency_step_ghz)
    _append_optional(result, "--expected-frequency-points", args.expected_frequency_points)
    return result


def _append_optional(result: list[str], flag: str, value: float | int | None) -> None:
    if value is not None:
        result.extend([flag, str(value)])


def _argument_summary(args: argparse.Namespace) -> dict[str, Any]:
    keys = [
        "require_emx",
        "expected_port_mode",
        "expected_pin_purpose",
        "expected_frequency_start_ghz",
        "expected_frequency_stop_ghz",
        "expected_frequency_step_ghz",
        "expected_frequency_points",
        "max_histogram_imbalance_frac",
        "sampling_require_uniform_closer_than_normal",
        "sampling_min_uniform_vs_normal_fields_fraction",
        "sampling_min_histogram_entropy_frac",
        "sampling_max_min_norm",
        "sampling_min_max_norm",
        "sampling_space_filling_strata",
        "sampling_max_space_filling_empty_strata_frac",
        "sampling_max_space_filling_duplicate_frac",
        "sampling_space_filling_duplicate_round_decimals",
        "sampling_space_filling_max_nn_samples",
        "sampling_min_space_filling_median_nn_distance",
        "require_clearance_audit",
        "min_clearance_pass_fraction",
        "max_clearance_overlap_area_um2",
        "max_clearance_violation_area_um2",
        "allow_clearance_missing",
        "backfill_frequency_metadata",
        "backfill_in_place",
        "touchstone_sample_size",
        "touchstone_seed",
        "touchstone_all",
        "touchstone_expected_ports",
        "touchstone_port_pairs",
        "touchstone_ground_unused_ports",
        "touchstone_positive_window_start_ghz",
        "touchstone_positive_window_stop_ghz",
        "touchstone_shape_window_start_ghz",
        "touchstone_shape_window_stop_ghz",
        "touchstone_max_shape_spike_ratio",
        "touchstone_max_shape_relative_step",
        "audit_sampling_distribution",
        "extract_response_features",
        "audit_response_feature_coverage",
        "audit_s8p_physical_feature_dataset",
        "s8p_expected_count",
        "s8p_expected_ok_count",
        "s8p_max_touchstone_checks",
        "s8p_require_power_line_8port",
        "response_min_valid_count",
        "response_require_cm",
        "response_target_k_min",
        "response_target_envelope_config",
        "response_target_k_max",
        "response_target_qp_min",
        "response_target_qp_max",
        "response_min_target_k_qp_area_frac",
        "response_min_target_k_qp_occupied_2d_bins",
        "response_max_target_k_qp_outside_frac",
        "response_target_lp_min_nh",
        "response_target_lp_max_nh",
        "response_target_ls_min_nh",
        "response_target_ls_max_nh",
        "response_min_target_lp_ls_area_frac",
        "response_min_target_lp_ls_occupied_2d_bins",
        "response_max_target_lp_ls_outside_frac",
        "plan_physical_feature_balanced_acquisition",
        "physical_feature_columns",
        "derive_scalar_q_feature",
        "scalar_q_definition",
        "scalar_q_output_column",
        "scalar_q_copy_touchstones",
        "scalar_q_absolute_touchstone_paths",
        "physical_feature_bins",
        "physical_feature_target_envelope_config",
        "physical_feature_target_count_per_bin",
        "physical_feature_plan_desired_total_count",
        "physical_feature_plan_next_count",
        "physical_feature_plan_max_target_bins",
        "build_physical_feature_surrogate_candidates",
        "physical_feature_surrogate_candidate_count",
        "physical_feature_surrogate_prediction_batch_size",
        "physical_feature_surrogate_seed",
        "physical_feature_surrogate_k_neighbors",
        "physical_feature_surrogate_max_validation_rows",
        "physical_feature_surrogate_no_plots",
        "select_physical_feature_targeted_candidates",
        "physical_feature_candidate_predictions_csv",
        "physical_feature_candidate_id_column",
        "physical_feature_candidate_max_total",
        "physical_feature_candidate_max_per_target",
        "physical_feature_candidate_allow_outside_bin",
        "physical_feature_candidate_reachable_targets_only",
        "physical_feature_candidate_min_candidates_per_reachable_target",
        "physical_feature_candidate_redistribute_reachable_quota",
        "select_physical_feature_validation_samples",
        "physical_feature_validation_sample_count",
        "physical_feature_validation_seed",
        "physical_feature_validation_mode",
        "physical_feature_validation_require_touchstone_path",
        "physical_feature_validation_check_touchstone_exists",
        "build_physical_feature_inverse_training_table",
        "inverse_training_require_touchstone_path",
        "inverse_training_check_touchstone_exists",
        "predict_geometry_from_physical_features",
        "inverse_target",
        "inverse_target_json",
        "inverse_candidate_count",
        "inverse_k_neighbors",
        "inverse_include_nearest_neighbor_candidates",
        "audit_zin_coverage",
        "audit_zin_sweep_coverage",
        "response_load_ohm",
        "zin_min_valid_count",
        "zin_min_occupied_2d_bins",
        "zin_target_count_per_bin",
        "zin_target_envelope_config",
        "zin_target_real_min_ohm",
        "zin_target_real_max_ohm",
        "zin_target_imag_min_ohm",
        "zin_target_imag_max_ohm",
        "zin_min_target_envelope_area_frac",
        "zin_min_target_envelope_occupied_2d_bins",
        "zin_max_target_envelope_outside_frac",
        "zin_sweep_frequency_slices_ghz",
        "zin_sweep_min_valid_count",
        "zin_sweep_min_real_span_ohm",
        "zin_sweep_min_imag_span_ohm",
        "zin_sweep_min_occupied_2d_bins",
        "zin_sweep_min_occupied_2d_frac",
        "zin_sweep_min_entropy_frac",
        "select_hfss_samples",
        "hfss_sample_count",
    ]
    return {key: getattr(args, key) for key in keys}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Dataset Quality Gates Report",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Output directory: `{summary['out_dir']}`",
        "",
        "| Status | Step | Return code | Outputs |",
        "| --- | --- | ---: | --- |",
    ]
    for step in summary["steps"]:
        outputs = "<br>".join(
            f"{name}: `{item['path']}` ({'exists' if item['exists'] else 'missing'})"
            for name, item in step["outputs"].items()
        )
        lines.append(f"| {step['status']} | {step['name']} | {step['returncode']} | {outputs} |")
    lines.extend(
        [
            "",
            "This report is an orchestrated local evidence summary. Inspect each sub-report before claiming production readiness.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
