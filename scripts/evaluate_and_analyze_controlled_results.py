#!/usr/bin/env python3
"""Run the preregistered controlled evaluators and paired analysis once."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHARED_FREF_EVALUATOR_SHA = "49f4137519d8891b7bbea0db39a7feff901836a20cfb8e2f20b60eb198fc27eb"
COMMON_FORWARD_EVALUATOR_SHA = "cd0e7fb2d219744a9080d5dfc44f91dc0c609b050b608c595509cb0b3f547360"
PAIRED_STATISTICS_SHA = "d513290c0bd1c5353bde911e15b5697f6a0e9d7408b88fb479e3ad3db3bd6b5a"
LEGACY_EVALUATOR_SHA = "ffa428e2fc9dc1598bd85979fcae637dd5479d332e81dc773cc926b4b2254a36"
TRAINER_SHA = "92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be"
TARGET_SHA = "c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407"
HOLDOUT_SHA = "4cd2e1f584c2cf7c14ef64a89508bd30d92aec4eb4b1c377effe1d561b9b8ebe"
NORMALIZATION_SHA = "9b29ac93f3eb0735964492497ec2032157c5ae290ce3ad2b97216a4bc4b34d47"
LARGE_DATA_SHA = "61d93c5489081f41bb8878c1ef847c61972408c4135aca06828ce8d09bf5d61c"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq-i-phase-summary", required=True)
    parser.add_argument("--expected-rq-i-phase-summary-sha256", required=True)
    parser.add_argument("--rq-f-phase-summary", required=True)
    parser.add_argument("--expected-rq-f-phase-summary-sha256", required=True)
    parser.add_argument("--fref-binding-json", required=True)
    parser.add_argument("--expected-fref-binding-sha256", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--experiment-root", required=True)
    parser.add_argument("--runtime-addendum", required=True)
    parser.add_argument("--targets-json", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha(path: Path, expected: str, label: str) -> str:
    actual = _sha256(path)
    if actual != expected.strip().lower():
        raise ValueError(f"{label} SHA mismatch: {actual} != {expected}")
    return actual


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _run(command: list[str], log_path: Path, environment: dict[str, str]) -> None:
    with log_path.open("x", encoding="utf-8") as log:
        completed = subprocess.run(
            ["nice", "-n", "19", *command],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed rc={completed.returncode}; log={log_path}")


def _metric_row(
    *,
    estimand: str,
    metric_id: str,
    label: str,
    panel: str,
    role: str,
    unit: str,
    direction: str,
    replicate: int,
    small: float,
    large: float,
) -> dict[str, Any]:
    return {
        "estimand_id": estimand,
        "metric_id": metric_id,
        "metric_label": label,
        "panel": panel,
        "role": role,
        "unit": unit,
        "better_direction": direction,
        "replicate": replicate,
        "small_value": small,
        "large_value": large,
    }


def main() -> int:
    args = _parse_args()
    root = Path(args.experiment_root).resolve()
    runtime_addendum = Path(args.runtime_addendum).resolve()
    scripts = runtime_addendum / "scripts"
    shared_evaluator = scripts / "evaluate_controlled_tandem_shared_fref_fixed_targets.py"
    common_evaluator = scripts / "evaluate_controlled_forward_common_holdout.py"
    statistics_script = scripts / "analyze_controlled_paired_replicates.py"
    legacy_evaluator = scripts / "evaluate_historical_tandem_fixed_targets.py"
    trainer = root / "runtime_snapshot_v2/scripts/train_physical_feature_tandem_inverse.py"
    normalization = root / "runtime_snapshot_v2/fixed_declared_normalization_contract_v1.json"
    holdout = root / "data_materialization_v1/fixed_common_holdout_manifest.json"
    large_data = root / "data_materialization_v1/arm_large_n200000_with_common_holdout.csv"
    python = Path(
        "/volumes/research-localdata/ywang3652/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python"
    )
    fixed_paths = {
        "shared_fref_evaluator": (shared_evaluator, SHARED_FREF_EVALUATOR_SHA),
        "common_forward_evaluator": (common_evaluator, COMMON_FORWARD_EVALUATOR_SHA),
        "paired_statistics": (statistics_script, PAIRED_STATISTICS_SHA),
        "legacy_evaluator": (legacy_evaluator, LEGACY_EVALUATOR_SHA),
        "trainer": (trainer, TRAINER_SHA),
        "normalization": (normalization, NORMALIZATION_SHA),
        "holdout": (holdout, HOLDOUT_SHA),
        "large_data": (large_data, LARGE_DATA_SHA),
        "targets": (Path(args.targets_json).resolve(), TARGET_SHA),
        "rq_i_phase": (
            Path(args.rq_i_phase_summary).resolve(),
            args.expected_rq_i_phase_summary_sha256,
        ),
        "rq_f_phase": (
            Path(args.rq_f_phase_summary).resolve(),
            args.expected_rq_f_phase_summary_sha256,
        ),
        "fref_binding": (
            Path(args.fref_binding_json).resolve(),
            args.expected_fref_binding_sha256,
        ),
        "preregistration": (
            Path(args.preregistration_json).resolve(),
            args.expected_preregistration_sha256,
        ),
        "clarification": (
            Path(args.clarification_addendum_json).resolve(),
            args.expected_clarification_addendum_sha256,
        ),
    }
    sources = {
        label: {"path": str(path), "sha256": _require_sha(path, expected, label)}
        for label, (path, expected) in fixed_paths.items()
    }
    rq_i_phase = _read_json(fixed_paths["rq_i_phase"][0])
    rq_f_phase = _read_json(fixed_paths["rq_f_phase"][0])
    fref_binding = _read_json(fixed_paths["fref_binding"][0])
    for expected_phase, payload in (
        ("rq_i_shared_fref", rq_i_phase),
        ("rq_f_own_forward", rq_f_phase),
    ):
        if payload.get("phase") != expected_phase or payload.get("overall_status") != "PASS_ALL_10_RUNS_VALIDATION_ONLY_TEST_SEALED":
            raise ValueError(f"phase is not frozen PASS: {expected_phase}")
    if fref_binding.get("overall_status") != "PASS_FREF_FROZEN_RQ_I_ARMS_MAY_USE_THIS_FORWARD_ONLY":
        raise ValueError("F_ref binding is not PASS")

    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber evaluation output exists: {out_dir}")
    out_dir.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update(
        {
            "OMP_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
            "NUMEXPR_NUM_THREADS": "4",
            "PYTHONPATH": f"{scripts}:{root / 'runtime_snapshot_v2'}",
        }
    )
    try:
        rq_i_outputs = out_dir / "rq_i_fixed10k_shared_fref"
        rq_i_outputs.mkdir()
        rq_i_summaries: dict[tuple[int, str], dict[str, Any]] = {}
        for run in sorted(
            rq_i_phase["runs"], key=lambda value: (int(value["replicate"]), str(value["arm"]))
        ):
            replicate = int(run["replicate"])
            arm = str(run["arm"])
            seed = int(run["seed"])
            arm_out = rq_i_outputs / f"rep{replicate}_{arm}"
            command = [
                str(python),
                str(shared_evaluator),
                "--arm-weights",
                run["weights"]["path"],
                "--arm-summary",
                run["summary"]["path"],
                "--fref-weights",
                fref_binding["f_ref_weights"]["path"],
                "--fref-summary",
                fref_binding["f_ref_summary"]["path"],
                "--targets-json",
                sources["targets"]["path"],
                "--trainer-source",
                str(trainer),
                "--preregistration-json",
                sources["preregistration"]["path"],
                "--clarification-addendum-json",
                sources["clarification"]["path"],
                "--out-dir",
                str(arm_out),
                "--arm",
                arm,
                "--replicate",
                str(replicate),
                "--model-id",
                f"controlled_rq_i_rep{replicate}_{arm}_seed{seed}",
                "--expected-arm-training-rows",
                "100000" if arm == "small" else "200000",
                "--expected-arm-seed",
                str(seed),
                "--expected-fref-seed",
                "2026082201",
                "--expected-arm-weights-sha256",
                run["weights"]["sha256"],
                "--expected-arm-summary-sha256",
                run["summary"]["sha256"],
                "--expected-fref-weights-sha256",
                fref_binding["f_ref_weights"]["sha256"],
                "--expected-fref-summary-sha256",
                fref_binding["f_ref_summary"]["sha256"],
                "--expected-fref-forward-component-sha256",
                fref_binding["canonical_forward_component_sha256"],
                "--expected-targets-sha256",
                TARGET_SHA,
                "--expected-trainer-sha256",
                TRAINER_SHA,
                "--expected-preregistration-sha256",
                sources["preregistration"]["sha256"],
                "--expected-clarification-addendum-sha256",
                sources["clarification"]["sha256"],
                "--expected-legacy-evaluator-sha256",
                LEGACY_EVALUATOR_SHA,
            ]
            _run(command, rq_i_outputs / f"rep{replicate}_{arm}.console.log", environment)
            summary_path = arm_out / f"controlled_{arm}_rep{replicate}_shared_fref_fixed10k_summary.json"
            rq_i_summaries[(replicate, arm)] = _read_json(summary_path)

        rq_f_out = out_dir / "rq_f_common_real_emx_test"
        common_command = [
            str(python),
            str(common_evaluator),
            "--rq-f-phase-summary",
            sources["rq_f_phase"]["path"],
            "--expected-rq-f-phase-summary-sha256",
            sources["rq_f_phase"]["sha256"],
            "--large-data-csv",
            str(large_data),
            "--expected-large-data-csv-sha256",
            LARGE_DATA_SHA,
            "--common-holdout-manifest",
            str(holdout),
            "--expected-common-holdout-manifest-sha256",
            HOLDOUT_SHA,
            "--fixed-normalization-json",
            str(normalization),
            "--expected-fixed-normalization-sha256",
            NORMALIZATION_SHA,
            "--trainer-source",
            str(trainer),
            "--expected-trainer-source-sha256",
            TRAINER_SHA,
            "--preregistration-json",
            sources["preregistration"]["path"],
            "--expected-preregistration-sha256",
            sources["preregistration"]["sha256"],
            "--clarification-addendum-json",
            sources["clarification"]["path"],
            "--expected-clarification-addendum-sha256",
            sources["clarification"]["sha256"],
            "--out-dir",
            str(rq_f_out),
        ]
        _run(common_command, out_dir / "rq_f_common_real_emx_test.console.log", environment)
        rq_f_summary_path = rq_f_out / "controlled_forward_common_real_emx_test_summary.json"
        rq_f_summary = _read_json(rq_f_summary_path)
        rq_f_results = {
            (int(value["replicate"]), str(value["arm"])): value
            for value in rq_f_summary["model_results"]
        }

        metric_rows: list[dict[str, Any]] = []
        for replicate in range(1, 6):
            small_i = rq_i_summaries[(replicate, "small")]["metrics"]
            large_i = rq_i_summaries[(replicate, "large")]["metrics"]
            for metric_id, label, panel_key, role in (
                (
                    "legacy_joint_fixed_range_rmse",
                    "Shared-F_ref joint range RMSE",
                    "legacy_k_le_0p8_8000_primary",
                    "primary",
                ),
                (
                    "all10k_joint_fixed_range_rmse",
                    "Shared-F_ref joint range RMSE",
                    "all_10000_coverage_stress",
                    "secondary_coverage_stress",
                ),
                (
                    "highk_joint_fixed_range_rmse",
                    "Shared-F_ref joint range RMSE",
                    "high_k_gt_0p8_2000_extrapolation",
                    "secondary_extrapolation",
                ),
            ):
                metric_rows.append(
                    _metric_row(
                        estimand="RQ_I_shared_F_ref",
                        metric_id=metric_id,
                        label=label,
                        panel=panel_key,
                        role=role,
                        unit="fraction",
                        direction="lower",
                        replicate=replicate,
                        small=float(small_i[panel_key]["joint_fixed_frame_range_rmse"]),
                        large=float(large_i[panel_key]["joint_fixed_frame_range_rmse"]),
                    )
                )
            for metric_id, label, field, direction in (
                ("legacy_q_target_met_rate", "Q lower-bound target-met rate", "target_met_rate", "higher"),
                ("legacy_q_shortfall_mae", "Q shortfall MAE", "shortfall_mae", "lower"),
            ):
                panel_key = "legacy_k_le_0p8_8000_primary"
                metric_rows.append(
                    _metric_row(
                        estimand="RQ_I_shared_F_ref",
                        metric_id=metric_id,
                        label=label,
                        panel=panel_key,
                        role="secondary_engineering",
                        unit="fraction" if field == "target_met_rate" else "Q",
                        direction=direction,
                        replicate=replicate,
                        small=float(small_i[panel_key]["q_minimum_semantics"][field]),
                        large=float(large_i[panel_key]["q_minimum_semantics"][field]),
                    )
                )

            small_f = rq_f_results[(replicate, "small")]["metrics"]
            large_f = rq_f_results[(replicate, "large")]["metrics"]
            metric_rows.append(
                _metric_row(
                    estimand="RQ_F_common_real_EMX",
                    metric_id="joint_declared_range_rmse",
                    label="Forward joint range RMSE",
                    panel="common_real_EMX_test_9096",
                    role="primary",
                    unit="fraction",
                    direction="lower",
                    replicate=replicate,
                    small=float(small_f["joint_declared_range_normalized_rmse"]),
                    large=float(large_f["joint_declared_range_normalized_rmse"]),
                )
            )
            for feature, unit in (
                ("lp_nh", "nH"),
                ("ls_nh", "nH"),
                ("q", "Q"),
                ("k_abs", "absolute K"),
            ):
                metric_rows.append(
                    _metric_row(
                        estimand="RQ_F_common_real_EMX",
                        metric_id=f"{feature}_mae",
                        label=f"Forward {feature} MAE",
                        panel="common_real_EMX_test_9096",
                        role="secondary_per_feature",
                        unit=unit,
                        direction="lower",
                        replicate=replicate,
                        small=float(small_f["per_feature"][feature]["mae_physical"]),
                        large=float(large_f["per_feature"][feature]["mae_physical"]),
                    )
                )

        paired_csv = out_dir / "controlled_paired_metrics_input.csv"
        with paired_csv.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(metric_rows[0]))
            writer.writeheader()
            writer.writerows(metric_rows)
        paired_csv_sha = _sha256(paired_csv)
        statistics_out = out_dir / "paired_statistics_and_figures"
        statistics_command = [
            str(python),
            str(statistics_script),
            "--paired-metrics-csv",
            str(paired_csv),
            "--expected-paired-metrics-sha256",
            paired_csv_sha,
            "--preregistration-json",
            sources["preregistration"]["path"],
            "--expected-preregistration-sha256",
            sources["preregistration"]["sha256"],
            "--clarification-addendum-json",
            sources["clarification"]["path"],
            "--expected-clarification-addendum-sha256",
            sources["clarification"]["sha256"],
            "--out-dir",
            str(statistics_out),
        ]
        _run(statistics_command, out_dir / "paired_statistics.console.log", environment)
        statistics_summary = statistics_out / "controlled_paired_statistics_summary.json"
        result = {
            "schema": "controlled_evaluation_and_analysis_terminal_v1",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall_status": "PASS_CONTROLLED_RESULTS_READY_FOR_REPORT_REVIEW",
            "sources": sources,
            "rq_i_evaluation_count": len(rq_i_summaries),
            "rq_f_common_test_model_count": len(rq_f_results),
            "paired_metric_row_count": len(metric_rows),
            "paired_metrics_csv": {"path": str(paired_csv), "sha256": paired_csv_sha},
            "rq_f_common_test_summary": {
                "path": str(rq_f_summary_path),
                "sha256": _sha256(rq_f_summary_path),
            },
            "paired_statistics_summary": {
                "path": str(statistics_summary),
                "sha256": _sha256(statistics_summary),
            },
            "claim_boundary": (
                "RQ-F is common real-label forward accuracy. RQ-I remains fixed-frame shared-F_ref proxy "
                "evidence conditional on one F_ref. Neither is a complete end-to-end fresh-EMX inverse claim."
            ),
        }
        terminal = out_dir / "terminal_summary.json"
        terminal.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(f"overall_status={result['overall_status']}")
        print(f"terminal_summary={terminal}")
        print(f"terminal_summary_sha256={_sha256(terminal)}")
        return 0
    except Exception as exc:
        fail_path = out_dir / "evaluation_FAIL.json"
        if not fail_path.exists():
            fail_path.write_text(
                json.dumps(
                    {
                        "schema": "controlled_evaluation_and_analysis_fail_v1",
                        "overall_status": "FAIL_NO_SILENT_RETRY",
                        "error": f"{type(exc).__name__}: {exc}",
                        "failed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
