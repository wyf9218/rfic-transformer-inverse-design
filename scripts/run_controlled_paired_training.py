#!/usr/bin/env python3
"""Run hash-gated controlled paired training sequentially on a research host.

The physical fixed-10k chain is operationally prioritized but is not a
scientific dependency of this controlled experiment.  A separately frozen
resource-coordination manifest limits this driver to one process, nice 19,
four BLAS threads, and load-gated launches.  A failed run is preserved and
stops the phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


TRAINER_SHA = "92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be"
# The private production source had SHA-256
# d344f7e099db8f4740fb1d7decab604723e8c99b866932db44fdaf2ab8737414.
# This repository copy only replaces site/user-specific defaults with explicit
# portable CLI arguments; the controlled experiment logic is unchanged.
PRIVATE_PRODUCTION_SOURCE_SHA256 = (
    "d344f7e099db8f4740fb1d7decab604723e8c99b866932db44fdaf2ab8737414"
)
NORMALIZATION_SHA = "9b29ac93f3eb0735964492497ec2032157c5ae290ce3ad2b97216a4bc4b34d47"
HOLDOUT_SHA = "4cd2e1f584c2cf7c14ef64a89508bd30d92aec4eb4b1c377effe1d561b9b8ebe"
GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("rq_i_shared_fref", "rq_f_own_forward"), required=True)
    parser.add_argument("--experiment-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--runtime-binding-json", required=True)
    parser.add_argument("--expected-runtime-binding-sha256", required=True)
    parser.add_argument("--fref-binding-json", required=True)
    parser.add_argument("--expected-fref-binding-sha256", required=True)
    parser.add_argument("--resource-coordination-json", required=True)
    parser.add_argument("--expected-resource-coordination-sha256", required=True)
    parser.add_argument("--max-prelaunch-load", type=float, default=80.0)
    parser.add_argument("--max-load-wait-hours", type=float, default=12.0)
    parser.add_argument(
        "--required-host",
        default="",
        help="Optional exact FQDN gate; empty disables the site-specific host check.",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="Python interpreter used for child training processes.",
    )
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


def _write_json_no_clobber(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _canonical_forward_component_sha256(weights_path: Path) -> str:
    digest = hashlib.sha256(b"controlled_forward_component_v1\0")
    with np.load(weights_path, allow_pickle=False) as archive:
        for prefix in ("forward_weight_", "forward_bias_"):
            keys = sorted(
                (key for key in archive.files if key.startswith(prefix)),
                key=lambda key: int(key.removeprefix(prefix)),
            )
            if not keys:
                raise ValueError(f"weights archive lacks {prefix} arrays")
            family = "forward_weights" if prefix == "forward_weight_" else "forward_biases"
            for index, key in enumerate(keys):
                array = np.asarray(archive[key], dtype="<f8", order="C")
                digest.update(f"{family}_{index}\0{array.shape}\0".encode("ascii"))
                digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _wait_for_low_load(maximum: float, maximum_wait_hours: float) -> float:
    attempts = max(1, int(float(maximum_wait_hours) * 3600.0 / 30.0))
    for _ in range(attempts):
        load = os.getloadavg()[0]
        if load < maximum:
            return float(load)
        time.sleep(30)
    raise RuntimeError(
        f"load did not fall below {maximum} within {maximum_wait_hours} hours"
    )


def _cpu_affinity_record(pid: int) -> dict[str, Any]:
    if not hasattr(os, "sched_getaffinity"):
        return {
            "mode": "not_explicitly_pinned_inherits_launcher",
            "available": False,
        }
    allowed = sorted(int(value) for value in os.sched_getaffinity(pid))
    return {
        "mode": "not_explicitly_pinned_inherits_launcher",
        "available": True,
        "allowed_cpu_count": len(allowed),
        "allowed_cpu_ids": allowed,
    }


def _base_command(
    *,
    python: Path,
    trainer: Path,
    training_csv: Path,
    run_dir: Path,
    train_rows: int,
    seed: int,
    holdout: Path,
    normalization: Path,
) -> list[str]:
    return [
        str(python),
        str(trainer),
        "--training-csv",
        str(training_csv),
        "--out-dir",
        str(run_dir),
        "--geometry-columns",
        ",".join(GEOMETRY_COLUMNS),
        "--min-training-rows",
        str(train_rows),
        "--split-mode",
        "fixed_common_holdout_manifest",
        "--fixed-common-holdout-manifest-json",
        str(holdout),
        "--fixed-common-holdout-manifest-sha256",
        HOLDOUT_SHA,
        "--seed",
        str(seed),
        "--split-seed",
        str(seed),
        "--forward-depth",
        "2",
        "--forward-width",
        "128",
        "--inverse-depth",
        "2",
        "--inverse-width",
        "128",
        "--inverse-geometry-projection",
        "independent_sigmoid",
        "--inverse-checkpoint-selection",
        "training_objective",
        "--batch-size",
        "4096",
        "--training-batch-sampler",
        "row_uniform",
        "--exact-update-batch-mode",
        "continuous_permutation_full_batch",
        "--validation-every-optimizer-updates",
        "40",
        "--learning-rate",
        "0.001",
        "--training-learning-rate-schedule",
        "constant",
        "--weight-decay",
        "0.000001",
        "--response-weight",
        "1.0",
        "--geometry-anchor-weight",
        "0.01",
        "--topology-feasibility-weight",
        "0.02",
        "--response-loss-scaling",
        "declared_range",
        "--response-loss-family",
        "mse",
        "--q-target-semantics",
        "exact",
        "--response-weight-schedule",
        "warmup_ramp_adaptive_ema",
        "--response-schedule-domain",
        "optimizer_update",
        "--response-warmup-optimizer-updates",
        "240",
        "--response-ramp-optimizer-updates",
        "960",
        "--response-adaptive-ema-decay",
        "0.95",
        "--response-adaptive-min-multiplier",
        "0.25",
        "--response-adaptive-max-multiplier",
        "4.0",
        "--fixed-normalization-contract-json",
        str(normalization),
        "--fixed-normalization-contract-sha256",
        NORMALIZATION_SHA,
        "--evaluation-mode",
        "validation_only",
        "--local-refinement-steps",
        "0",
        "--max-prediction-rows",
        "10",
        "--robustness-noise-levels",
        "0.01",
        "--robustness-repeats",
        "1",
        "--robustness-max-rows",
        "64",
        "--stage-checkpoint-mode",
        "resume_exact",
    ]


def _check_run_summary(
    summary: dict[str, Any],
    *,
    phase: str,
    arm: str,
    train_rows: int,
    seed: int,
    fref_forward_sha: str,
    weights_path: Path,
) -> dict[str, bool]:
    arguments = summary.get("arguments") or {}
    split = summary.get("split_audit") or {}
    budget = summary.get("optimizer_budget_contract") or {}
    realized = budget.get("realized") or {}
    architecture = (summary.get("model_comparison_contract") or {}).get("architecture") or {}
    checks = {
        "execution_pass": summary.get("execution_status") == "PASS",
        "validation_only": summary.get("evaluation_mode") == "validation_only",
        "test_access_zero": int(
            (summary.get("test_access_contract") or {}).get("test_access_event_count") or 0
        )
        == 0,
        "seed_exact": int(arguments.get("seed") or -1) == seed,
        "train_rows_exact": int((split.get("row_counts") or {}).get("train") or 0)
        == train_rows,
        "validation_rows_exact": int((split.get("row_counts") or {}).get("validation") or 0)
        == 9096,
        "test_rows_exact": int((split.get("row_counts") or {}).get("test") or 0) == 9096,
        "holdout_exact": (
            (split.get("fixed_common_holdout_manifest") or {}).get("sha256") == HOLDOUT_SHA
        ),
        "normalization_exact": (summary.get("normalization_contract") or {}).get("sha256")
        == NORMALIZATION_SHA,
        "trainer_exact": (summary.get("model_comparison_contract") or {}).get(
            "trainer_implementation_sha256"
        )
        == TRAINER_SHA,
        "forward_architecture_exact": architecture.get("forward_hidden_widths") == [128, 128],
        "inverse_architecture_exact": architecture.get("inverse_hidden_widths") == [128, 128],
        "decoder_exact": architecture.get("inverse_geometry_projection") == "independent_sigmoid",
        "early_stopping_disabled": budget.get("early_stopping_enabled") is False,
        "fixed_batch_mode": budget.get("exact_update_batch_mode")
        == "continuous_permutation_full_batch",
    }
    if phase == "rq_i_shared_fref":
        checks.update(
            {
                "forward_frozen": arguments.get("freeze_transported_forward") is True,
                "forward_updates_zero": int(realized.get("forward_optimizer_updates", -1)) == 0,
                "inverse_updates_4800": int(realized.get("inverse_optimizer_updates") or 0) == 4800,
                "forward_component_is_shared_fref": _canonical_forward_component_sha256(weights_path)
                == fref_forward_sha,
            }
        )
    else:
        checks.update(
            {
                "forward_random_initialization": arguments.get("forward_initialization_mode") == "random",
                "forward_updates_4800": int(realized.get("forward_optimizer_updates") or 0) == 4800,
                "inverse_updates_4800": int(realized.get("inverse_optimizer_updates") or 0) == 4800,
            }
        )
    return checks


def main() -> int:
    args = _parse_args()
    if args.required_host and socket.getfqdn() != args.required_host:
        raise RuntimeError(f"wrong host: {socket.getfqdn()}")
    root = Path(args.experiment_root).resolve()
    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber phase output exists: {out_dir}")
    prereg_path = Path(args.preregistration_json).resolve()
    clarification_path = Path(args.clarification_addendum_json).resolve()
    runtime_binding_path = Path(args.runtime_binding_json).resolve()
    fref_binding_path = Path(args.fref_binding_json).resolve()
    resource_coordination_path = Path(args.resource_coordination_json).resolve()
    prerequisites = {
        "preregistration": _require_sha(
            prereg_path, args.expected_preregistration_sha256, "preregistration"
        ),
        "clarification": _require_sha(
            clarification_path,
            args.expected_clarification_addendum_sha256,
            "clarification addendum",
        ),
        "runtime_binding": _require_sha(
            runtime_binding_path, args.expected_runtime_binding_sha256, "runtime binding"
        ),
        "fref_binding": _require_sha(
            fref_binding_path, args.expected_fref_binding_sha256, "F_ref binding"
        ),
        "resource_coordination": _require_sha(
            resource_coordination_path,
            args.expected_resource_coordination_sha256,
            "resource coordination manifest",
        ),
    }
    prereg = _read_json(prereg_path)
    fref_binding = _read_json(fref_binding_path)
    resource_coordination = _read_json(resource_coordination_path)
    if prereg.get("schema") != "controlled_historical_data_scaling_preregistration_v1":
        raise ValueError("wrong preregistration schema")
    if fref_binding.get("overall_status") != "PASS_FREF_FROZEN_RQ_I_ARMS_MAY_USE_THIS_FORWARD_ONLY":
        raise ValueError("F_ref binding is not PASS")
    resource_checks = {
        "schema_exact": resource_coordination.get("schema")
        == "controlled_training_resource_coordination_v1",
        "status_pass": resource_coordination.get("overall_status")
        == "PASS_OPERATIONAL_SCHEDULING_ONLY",
        "not_scientific_dependency": resource_coordination.get(
            "physical_chain_is_scientific_dependency"
        )
        is False,
        "single_process_exact": int(
            resource_coordination.get("maximum_concurrent_training_processes") or 0
        )
        == 1,
        "nice_exact": int(resource_coordination.get("nice") or -1) == 19,
        "thread_limit_exact": int(resource_coordination.get("blas_threads_per_process") or 0)
        == 4,
        "prelaunch_load_exact": float(
            resource_coordination.get("maximum_prelaunch_load_1m") or -1.0
        )
        == float(args.max_prelaunch_load),
    }
    if not all(resource_checks.values()):
        raise ValueError(
            "resource coordination checks failed: "
            + ", ".join(key for key, value in resource_checks.items() if not value)
        )

    runtime = root / "runtime_snapshot_v2"
    trainer = runtime / "scripts/train_physical_feature_tandem_inverse.py"
    normalization = runtime / "fixed_declared_normalization_contract_v1.json"
    data_dir = root / "data_materialization_v1"
    holdout = data_dir / "fixed_common_holdout_manifest.json"
    python = Path(args.python_executable).expanduser().resolve()
    _require_sha(trainer, TRAINER_SHA, "trainer")
    _require_sha(normalization, NORMALIZATION_SHA, "normalization")
    _require_sha(holdout, HOLDOUT_SHA, "holdout")
    fref_weights = Path(fref_binding["f_ref_weights"]["path"])
    fref_summary = Path(fref_binding["f_ref_summary"]["path"])
    _require_sha(fref_weights, fref_binding["f_ref_weights"]["sha256"], "F_ref weights")
    _require_sha(fref_summary, fref_binding["f_ref_summary"]["sha256"], "F_ref summary")
    fref_forward_sha = str(fref_binding["canonical_forward_component_sha256"])
    pair_contract = prereg["replicate_contract"]["pairs"]
    small_records = {
        int(item["replicate"]): item for item in prereg["data_contract"]["small_csvs"]
    }

    out_dir.mkdir(parents=True)
    phase_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_records: list[dict[str, Any]] = []
    try:
        for pair in pair_contract:
            replicate = int(pair["replicate"])
            seed = int(pair["model_init_seed"])
            for arm in ("small", "large"):
                if arm == "small":
                    subset_seed = int(pair["data_subset_seed"])
                    training_csv = data_dir / (
                        f"arm_small_n100000_rep{replicate}_subsetseed{subset_seed}_with_common_holdout.csv"
                    )
                    expected_data_sha = str(small_records[replicate]["sha256"])
                    train_rows = 100000
                else:
                    training_csv = data_dir / "arm_large_n200000_with_common_holdout.csv"
                    expected_data_sha = str(prereg["data_contract"]["large_csv"]["sha256"])
                    train_rows = 200000
                _require_sha(training_csv, expected_data_sha, f"replicate {replicate} {arm} data")
                run_root = out_dir / f"rep{replicate}_{arm}_seed{seed}"
                run_root.mkdir()
                run_dir = run_root / "run"
                command = _base_command(
                    python=python,
                    trainer=trainer,
                    training_csv=training_csv,
                    run_dir=run_dir,
                    train_rows=train_rows,
                    seed=seed,
                    holdout=holdout,
                    normalization=normalization,
                )
                if args.phase == "rq_i_shared_fref":
                    command.extend(
                        [
                            "--forward-initialization-mode",
                            "transported_source_finetune",
                            "--forward-initial-weights",
                            str(fref_weights),
                            "--forward-initial-weights-sha256",
                            fref_binding["f_ref_weights"]["sha256"],
                            "--forward-initial-summary",
                            str(fref_summary),
                            "--forward-initial-summary-sha256",
                            fref_binding["f_ref_summary"]["sha256"],
                            "--freeze-transported-forward",
                            "--forward-max-optimizer-updates",
                            "0",
                            "--inverse-max-optimizer-updates",
                            "4800",
                        ]
                    )
                else:
                    command.extend(
                        [
                            "--forward-max-optimizer-updates",
                            "4800",
                            "--inverse-max-optimizer-updates",
                            "4800",
                        ]
                    )
                _write_json_no_clobber(run_root / "command.json", {"argv": command})
                prelaunch_load = _wait_for_low_load(
                    float(args.max_prelaunch_load), float(args.max_load_wait_hours)
                )
                environment = os.environ.copy()
                environment.update(
                    {
                        "OMP_NUM_THREADS": "4",
                        "OPENBLAS_NUM_THREADS": "4",
                        "MKL_NUM_THREADS": "4",
                        "NUMEXPR_NUM_THREADS": "4",
                        "PYTHONPATH": str(runtime),
                    }
                )
                console_path = run_root / "console.log"
                started = datetime.now(timezone.utc).isoformat(timespec="seconds")
                with console_path.open("x", encoding="utf-8") as console:
                    process = subprocess.Popen(
                        ["nice", "-n", "19", *command],
                        stdout=console,
                        stderr=subprocess.STDOUT,
                        env=environment,
                    )
                    _write_json_no_clobber(
                        run_root / "launch_receipt.json",
                        {
                            "schema": "controlled_paired_arm_launch_v1",
                            "phase": args.phase,
                            "replicate": replicate,
                            "arm": arm,
                            "seed": seed,
                            "pid": process.pid,
                            "started_utc": started,
                            "prelaunch_load_1m": prelaunch_load,
                            "nice": 19,
                            "thread_limit": 4,
                            "maximum_concurrent_training_processes": 1,
                            "cpu_affinity": _cpu_affinity_record(process.pid),
                            "resource_coordination": {
                                "path": str(resource_coordination_path),
                                "sha256": prerequisites["resource_coordination"],
                                "physical_chain_priority": True,
                                "scientific_dependency": False,
                            },
                            "test_access_allowed": False,
                        },
                    )
                    return_code = process.wait()
                if return_code != 0:
                    raise RuntimeError(
                        f"trainer failed: replicate={replicate} arm={arm} rc={return_code}"
                    )
                summary_path = run_dir / "physical_feature_tandem_inverse_summary.json"
                weights_path = run_dir / "physical_feature_tandem_inverse_weights.npz"
                summary = _read_json(summary_path)
                checks = _check_run_summary(
                    summary,
                    phase=args.phase,
                    arm=arm,
                    train_rows=train_rows,
                    seed=seed,
                    fref_forward_sha=fref_forward_sha,
                    weights_path=weights_path,
                )
                if not all(checks.values()):
                    raise RuntimeError(
                        f"terminal checks failed: replicate={replicate} arm={arm} "
                        f"failed={[key for key,value in checks.items() if not value]}"
                    )
                terminal = {
                    "schema": "controlled_paired_arm_terminal_v1",
                    "overall_status": "PASS_VALIDATION_ONLY_TEST_SEALED",
                    "phase": args.phase,
                    "replicate": replicate,
                    "arm": arm,
                    "seed": seed,
                    "training_rows": train_rows,
                    "summary": {"path": str(summary_path), "sha256": _sha256(summary_path)},
                    "weights": {"path": str(weights_path), "sha256": _sha256(weights_path)},
                    "history_sha256": _sha256(
                        run_dir / "physical_feature_tandem_inverse_history.csv"
                    ),
                    "checks": checks,
                    "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                _write_json_no_clobber(run_root / "terminal_receipt.json", terminal)
                run_records.append(terminal)
        phase_summary = {
            "schema": "controlled_paired_training_phase_summary_v1",
            "overall_status": "PASS_ALL_10_RUNS_VALIDATION_ONLY_TEST_SEALED",
            "phase": args.phase,
            "started_utc": phase_started,
            "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prerequisite_sha256": prerequisites,
            "resource_coordination_checks": resource_checks,
            "maximum_concurrent_training_processes": 1,
            "nice": 19,
            "blas_threads_per_process": 4,
            "physical_chain_is_scientific_dependency": False,
            "run_count": len(run_records),
            "runs": run_records,
            "test_access_event_count": 0,
        }
        _write_json_no_clobber(out_dir / "phase_summary.json", phase_summary)
        print(f"overall_status={phase_summary['overall_status']}")
        print(f"phase_summary={out_dir / 'phase_summary.json'}")
        print(f"phase_summary_sha256={_sha256(out_dir / 'phase_summary.json')}")
        return 0
    except Exception as exc:
        fail_path = out_dir / "phase_FAIL.json"
        if not fail_path.exists():
            _write_json_no_clobber(
                fail_path,
                {
                    "schema": "controlled_paired_training_phase_fail_v1",
                    "overall_status": "FAIL_STOPPED_NO_SILENT_RETRY",
                    "phase": args.phase,
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_run_count": len(run_records),
                    "completed_runs": run_records,
                    "failed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
