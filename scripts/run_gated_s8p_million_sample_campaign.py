#!/usr/bin/env python3
"""Plan the gated S8P million-sample EMX campaign.

This script is deliberately conservative.  It refuses to plan real production
generation until a current EMX-vs-HFSS `.s8p` validation summary proves the
first acceptance gate:

* both EMX and HFSS are `.s8p` files,
* the grid is 5-60 GHz with 0.5 GHz spacing and 111 points,
* the unused S8P port termination policy matches the accepted validation
  summary and the training-label extraction policy,
* Lp/Ls/Q/K/Kw max errors are within the configured percent gate, with Qp/Qs
  retained as diagnostic channels.

When the gate passes, it writes a ten-chunk plan for 1,000,000 samples, with a
quality/test/model-optimization checkpoint after every 100,000 generated rows.
It does not run EMX unless `--allow-real-emx` is supplied.
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "mars_s8p_physical_feature_500_template.yaml"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_plan_current"
DEFAULT_SEARCH_ROOTS = (
    PROJECT_ROOT / "outputs",
    PROJECT_ROOT / "hfss_validation",
    PROJECT_ROOT,
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    @property
    def pass_bool(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    validation_summary_path = _resolve_validation_summary(args)
    validation_summary = _read_json(validation_summary_path)
    validation_checks, validation_gate = _validation_gate(validation_summary_path, validation_summary, args)
    chunks = _build_chunks(args, out_dir) if validation_gate["status"] == "PASS" else []
    commands_path = out_dir / "s8p_million_sample_campaign.commands.sh"
    _write_command_script(commands_path, validation_gate, chunks, args)

    overall_status = "PASS" if validation_gate["status"] == "PASS" else "FAIL"
    decision = (
        "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN"
        if overall_status == "PASS"
        else "DO_NOT_START_MILLION_SAMPLE_CAMPAIGN_UNTIL_EMX_HFSS_S8P_GATE_PASSES"
    )
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "validation_summary": "" if validation_summary_path is None else str(validation_summary_path),
        "validation_gate": validation_gate,
        "out_dir": str(out_dir),
        "command_script": str(commands_path),
        "total_requested_samples": int(args.total_count),
        "chunk_size": int(args.chunk_size),
        "chunk_count": len(chunks),
        "jobs": int(args.jobs),
        "allow_real_emx": bool(args.allow_real_emx),
        "dry_run": not bool(args.allow_real_emx),
        "config": str(Path(args.config).expanduser().resolve()),
        "frequency_contract": {
            "start_ghz": float(args.expected_frequency_start_ghz),
            "stop_ghz": float(args.expected_frequency_stop_ghz),
            "step_ghz": float(args.expected_frequency_step_ghz),
            "points": int(args.expected_frequency_points),
            "expected_ports": int(args.expected_ports),
            "touchstone_extension": ".s8p",
            "ground_unused_ports": bool(args.ground_unused_ports),
        },
        "chunks": chunks,
        "checks": [check.as_dict() for check in validation_checks],
        "method_notes": [
            "The first EMX-HFSS S8P correlation gate is mandatory before production-scale EMX generation.",
            "Each 100k chunk includes generation, local dataset gates, physical-feature extraction, representative validation-sample selection, inverse-training-table build, baseline model training, inverse-model quality audit, NN architecture-search planning, and executable NumPy MLP architecture search for Lp/Ls/Q/K -> geometry.",
            "The generated command script is dry-run unless --allow-real-emx is supplied.",
            "This planner does not fabricate labels, curves, HFSS exports, or ADS screenshots.",
        ],
    }
    summary_path = out_dir / "s8p_million_sample_campaign_plan_summary.json"
    report_path = out_dir / "S8P_MILLION_SAMPLE_CAMPAIGN_PLAN_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"validation_summary={summary['validation_summary']}")
    print(f"command_script={commands_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-summary", help="Accepted EMX-HFSS S8P validation summary JSON")
    parser.add_argument("--search-root", action="append", help="Root to search for accepted validation summaries when omitted")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--total-count", type=int, default=1_000_000)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--jobs", type=int, default=24)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--sampler", choices=("lhs", "lhs_optimized", "sobol"), default="lhs_optimized")
    parser.add_argument("--seed", type=int, default=20260626)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--emx-port-pairs", default="1,4:5,6")
    parser.add_argument("--hfss-port-pairs", default="1,4:5,6")
    parser.add_argument("--feature-columns", default="lp_nh_center,ls_nh_center,q_center,k_center")
    parser.add_argument("--scalar-q-definition", choices=["min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary"], default="min")
    parser.add_argument(
        "--ground-unused-ports",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Short unused S8P supply/tap ports during response-feature extraction, matching the ADS AC-ground policy. "
            "Use --no-ground-unused-ports only for an explicit open-port diagnostic."
        ),
    )
    parser.add_argument("--physical-feature-bins", type=int, default=6)
    parser.add_argument("--validation-sample-count", type=int, default=8)
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _resolve_validation_summary(args: argparse.Namespace) -> Path | None:
    if args.validation_summary:
        return Path(args.validation_summary).expanduser().resolve()
    roots = [Path(item).expanduser().resolve() for item in (args.search_root or [])] or list(DEFAULT_SEARCH_ROOTS)
    patterns = (
        "accepted_emx_hfss_ads_validation_summary.json",
        "s8p_hfss_postrun_validation_summary.json",
        "latest_s8p_20_pilot_return_import_summary.json",
    )
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            candidates.extend(path for path in root.rglob(pattern) if path.is_file())
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
    return candidates[0].resolve()


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _validation_gate(path: Path | None, summary: dict[str, Any], args: argparse.Namespace) -> tuple[list[Check], dict[str, Any]]:
    checks: list[Check] = []
    checks.append(Check("PASS" if path is not None and path.is_file() else "FAIL", "validation summary exists", "" if path is None else str(path)))
    if summary.get("_parse_error"):
        checks.append(Check("FAIL", "validation summary parses", str(summary.get("_parse_error"))))
    elif summary:
        checks.append(Check("PASS", "validation summary parses", "JSON object"))
    else:
        checks.append(Check("FAIL", "validation summary parses", "missing or empty JSON"))

    if _is_direct_accepted_summary(summary):
        checks.extend(_direct_accepted_checks(summary, args))
        gate_kind = "accepted_emx_hfss_ads_validation"
        worst_error = _worst_direct_error(summary)
    elif _is_postrun_summary(summary):
        checks.extend(_postrun_checks(summary, args))
        gate_kind = "s8p_hfss_postrun_validation"
        worst_error = _worst_postrun_error(summary)
    elif _is_latest_import_summary(summary):
        checks.extend(_latest_import_checks(summary, args))
        gate_kind = "latest_s8p_20_pilot_return_import"
        worst_error = _worst_latest_import_error(summary)
    else:
        checks.append(
            Check(
                "FAIL",
                "recognized validation summary type",
                f"overall_status={summary.get('overall_status')!r}, decision={summary.get('decision')!r}",
            )
        )
        gate_kind = "unknown"
        worst_error = None

    status = "PASS" if checks and all(check.pass_bool for check in checks) else "FAIL"
    return checks, {
        "status": status,
        "kind": gate_kind,
        "max_percent_error_limit": float(args.max_percent_error),
        "worst_percent_error": worst_error,
        "summary_path": "" if path is None else str(path),
        "blocking_checks": [check.as_dict() for check in checks if check.status != "PASS"],
    }


def _is_direct_accepted_summary(summary: dict[str, Any]) -> bool:
    return "compare_summary" in summary and "emx_touchstone" in summary and "hfss_touchstone" in summary


def _is_postrun_summary(summary: dict[str, Any]) -> bool:
    return "records" in summary and str(summary.get("decision", "")).startswith(("ACCEPT_", "DO_NOT_", "WAITING_", "WAIT_FOR_"))


def _is_latest_import_summary(summary: dict[str, Any]) -> bool:
    return "postrun_result" in summary and "return_tarball" in summary


def _direct_accepted_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks = [
        _status_equals("accepted validation status", summary.get("overall_status"), "PASS"),
        _status_equals("accepted validation decision", summary.get("decision"), "ACCEPT_HFSS_VALIDATION_SAMPLE"),
    ]
    checks.extend(_touchstone_record_checks("EMX", summary.get("emx_touchstone") or summary.get("emx_s4p"), args))
    checks.extend(_touchstone_record_checks("HFSS", summary.get("hfss_touchstone") or summary.get("hfss_s4p"), args))
    checks.extend(_argument_contract_checks(summary.get("arguments") or {}, args))
    compare_summary = _read_json(Path(str(summary.get("compare_summary") or "")).expanduser())
    checks.extend(_compare_summary_contract_checks(compare_summary, args))
    checks.extend(_named_check_statuses(summary.get("checks") or [], required_names=("EMX-vs-HFSS compare core metric errors", "EMX-vs-HFSS compare frequency-grid checks")))
    return checks


def _postrun_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks = [
        _status_equals("postrun validation status", summary.get("overall_status"), "PASS"),
        _status_in(
            "postrun validation decision",
            summary.get("decision"),
            {"ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION", "ACCEPT_EMX_HFSS_S8P_VALIDATION"},
        ),
        _status_equals("postrun frequency grid mode", summary.get("frequency_grid_mode"), "final_5_60_0p5_111"),
        Check(
            "PASS" if bool(summary.get("final_acceptance_candidate")) else "FAIL",
            "postrun final acceptance candidate",
            str(summary.get("final_acceptance_candidate")),
        ),
    ]
    checks.extend(_argument_contract_checks(summary.get("arguments") or {}, args))
    records = summary.get("records") or []
    checks.append(Check("PASS" if records else "FAIL", "postrun records present", f"records={len(records)}"))
    for record in records:
        sample = str(record.get("evaluation") or record.get("selection_rank") or "sample")
        checks.append(_status_equals(f"{sample} postrun sample status", record.get("status"), "PASS"))
        emx = str(record.get("emx_s8p") or "")
        hfss = str(record.get("hfss_s8p") or "")
        checks.append(Check("PASS" if emx.lower().endswith(".s8p") else "FAIL", f"{sample} EMX suffix", emx))
        checks.append(Check("PASS" if hfss.lower().endswith(".s8p") else "FAIL", f"{sample} HFSS suffix", hfss))
        worst = _as_float(record.get("worst_percent_error"))
        checks.append(
            Check(
                "PASS" if worst is not None and worst <= float(args.max_percent_error) else "FAIL",
                f"{sample} worst percent error",
                f"{worst} <= {float(args.max_percent_error):g}",
            )
        )
        compare = _read_json(Path(str(record.get("compare_summary") or "")).expanduser())
        checks.extend(_compare_summary_contract_checks(compare, args, prefix=f"{sample} "))
    return checks


def _latest_import_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks = [
        _status_equals("latest import status", summary.get("overall_status"), "PASS"),
        _status_equals("latest import decision", summary.get("decision"), "ACCEPT_EMX_HFSS_S8P_20_PILOT_VALIDATION"),
    ]
    checks.extend(_argument_contract_checks(summary.get("arguments") or {}, args))
    postrun = summary.get("postrun_result") if isinstance(summary.get("postrun_result"), dict) else {}
    checks.append(Check("PASS" if int(postrun.get("returncode", 1)) == 0 else "FAIL", "latest import postrun returncode", str(postrun.get("returncode"))))
    postrun_summary = postrun.get("summary") if isinstance(postrun.get("summary"), dict) else {}
    if postrun_summary:
        checks.extend(_postrun_checks(postrun_summary, args))
    else:
        checks.append(Check("FAIL", "latest import embedded postrun summary", "missing postrun_result.summary"))
    return checks


def _touchstone_record_checks(label: str, record: Any, args: argparse.Namespace) -> list[Check]:
    if not isinstance(record, dict):
        return [Check("FAIL", f"{label} Touchstone record", f"record={record!r}")]
    path = str(record.get("path") or "")
    suffix_ok = path.lower().endswith(".s8p")
    exists = bool(record.get("exists", True))
    return [
        Check("PASS" if suffix_ok else "FAIL", f"{label} Touchstone suffix is .s8p", path),
        Check("PASS" if exists else "FAIL", f"{label} Touchstone file exists in record", f"exists={exists}"),
    ]


def _argument_contract_checks(arguments: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    return [
        _float_equals("compare start GHz", arguments.get("compare_start_ghz") or arguments.get("expected_frequency_start_ghz"), args.expected_frequency_start_ghz, 1e-9),
        _float_equals("compare stop GHz", arguments.get("compare_stop_ghz") or arguments.get("expected_frequency_stop_ghz"), args.expected_frequency_stop_ghz, 1e-9),
        _float_equals("frequency step GHz", arguments.get("expected_frequency_step_ghz"), args.expected_frequency_step_ghz, 1e-9),
        _int_equals("frequency point count", arguments.get("expected_frequency_points"), args.expected_frequency_points),
        _float_le("max percent error gate", arguments.get("max_percent_error"), args.max_percent_error),
        _int_equals("HFSS expected ports", arguments.get("hfss_expected_ports") or arguments.get("expected_ports"), args.expected_ports),
        Check(
            "PASS" if bool(arguments.get("ground_unused_ports", False)) == bool(args.ground_unused_ports) else "FAIL",
            "unused S8P port termination policy matches campaign",
            f"validation={bool(arguments.get('ground_unused_ports', False))}, campaign={bool(args.ground_unused_ports)}",
        ),
    ]


def _compare_summary_contract_checks(summary: dict[str, Any], args: argparse.Namespace, *, prefix: str = "") -> list[Check]:
    checks = [_status_equals(f"{prefix}compare summary status", summary.get("overall_status"), "PASS")]
    criterion = summary.get("criterion") if isinstance(summary.get("criterion"), dict) else {}
    checks.append(_float_le(f"{prefix}compare criterion", criterion.get("max_percent_error"), args.max_percent_error))
    freq = summary.get("frequency_window_hz") if isinstance(summary.get("frequency_window_hz"), dict) else {}
    checks.extend(
        [
            _float_equals(f"{prefix}frequency start Hz", freq.get("min"), float(args.expected_frequency_start_ghz) * 1.0e9, args.frequency_tolerance_hz),
            _float_equals(f"{prefix}frequency stop Hz", freq.get("max"), float(args.expected_frequency_stop_ghz) * 1.0e9, args.frequency_tolerance_hz),
            _int_equals(f"{prefix}frequency window count", freq.get("count"), args.expected_frequency_points),
        ]
    )
    grid = summary.get("frequency_grid_checks") if isinstance(summary.get("frequency_grid_checks"), dict) else {}
    for name in ("ADS no-extrapolation coverage", "expected frequency points", "expected frequency step", "matching HFSS/ADS frequency grid"):
        item = grid.get(name) if isinstance(grid.get(name), dict) else {}
        checks.append(_status_equals(f"{prefix}grid check {name}", item.get("status"), "PASS"))
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    for metric in ("lp_nh", "ls_nh", "q", "k", "kw", "qp", "qs"):
        item = metrics.get(metric) if isinstance(metrics.get(metric), dict) else {}
        checks.append(_status_equals(f"{prefix}metric {metric} status", item.get("status"), "PASS"))
        checks.append(_float_le(f"{prefix}metric {metric} max percent error", item.get("max_percent_error"), args.max_percent_error))
    return checks


def _named_check_statuses(checks_raw: list[Any], *, required_names: tuple[str, ...]) -> list[Check]:
    by_name = {str(item.get("name")): item for item in checks_raw if isinstance(item, dict)}
    checks: list[Check] = []
    for name in required_names:
        item = by_name.get(name, {})
        checks.append(_status_equals(f"runner internal check {name}", item.get("status"), "PASS"))
    return checks


def _build_chunks(args: argparse.Namespace, out_dir: Path) -> list[dict[str, Any]]:
    total = int(args.total_count)
    chunk_size = int(args.chunk_size)
    chunk_count = int(math.ceil(total / chunk_size))
    chunks: list[dict[str, Any]] = []
    for index in range(chunk_count):
        start = index * chunk_size + 1
        stop = min(total, (index + 1) * chunk_size)
        count = stop - start + 1
        chunk_name = f"chunk_{index + 1:02d}_{count}"
        chunk_dir = out_dir / chunk_name
        candidate_dir = chunk_dir / "candidate_queue"
        dataset_dir = chunk_dir / "emx_dataset"
        quality_dir = chunk_dir / "dataset_quality_gates"
        training_dir = quality_dir / "physical_feature_inverse_training_table"
        model_dir = chunk_dir / "inverse_model_training"
        audit_dir = chunk_dir / "inverse_model_quality_audit"
        nn_architecture_dir = chunk_dir / "inverse_nn_architecture_search"
        nn_training_dir = chunk_dir / "inverse_nn_architecture_training"
        checkpoint_dir = chunk_dir / "chunk_checkpoint"
        candidate_csv = candidate_dir / "s8p_geometry_bootstrap_candidate_queue.csv"
        training_csv = training_dir / "physical_feature_inverse_training_table.csv"
        chunks.append(
            {
                "chunk_index": index + 1,
                "sample_start": start,
                "sample_stop": stop,
                "sample_count": count,
                "cumulative_count": stop,
                "chunk_dir": str(chunk_dir),
                "candidate_csv": str(candidate_csv),
                "dataset_dir": str(dataset_dir),
                "quality_dir": str(quality_dir),
                "training_csv": str(training_csv),
                "model_dir": str(model_dir),
                "audit_dir": str(audit_dir),
                "nn_architecture_dir": str(nn_architecture_dir),
                "nn_training_dir": str(nn_training_dir),
                "checkpoint_dir": str(checkpoint_dir),
                "commands": _chunk_commands(
                    args=args,
                    index=index,
                    count=count,
                    candidate_dir=candidate_dir,
                    candidate_csv=candidate_csv,
                    dataset_dir=dataset_dir,
                    quality_dir=quality_dir,
                    training_csv=training_csv,
                    model_dir=model_dir,
                    audit_dir=audit_dir,
                    nn_architecture_dir=nn_architecture_dir,
                    nn_training_dir=nn_training_dir,
                    checkpoint_dir=checkpoint_dir,
                ),
            }
        )
    return chunks


def _chunk_commands(
    *,
    args: argparse.Namespace,
    index: int,
    count: int,
    candidate_dir: Path,
    candidate_csv: Path,
    dataset_dir: Path,
    quality_dir: Path,
    training_csv: Path,
    model_dir: Path,
    audit_dir: Path,
    nn_architecture_dir: Path,
    nn_training_dir: Path,
    checkpoint_dir: Path,
) -> dict[str, list[str]]:
    config = str(Path(args.config).expanduser().resolve())
    python_command = str(args.python_command)
    seed = int(args.seed) + index
    touchstone_ground_unused_args = ["--touchstone-ground-unused-ports"] if bool(args.ground_unused_ports) else []
    return {
        "build_candidate_queue": [
            python_command,
            "scripts/build_s8p_geometry_bootstrap_candidate_queue.py",
            "--config",
            config,
            "--out-dir",
            str(candidate_dir),
            "--count",
            str(count),
            "--expected-count",
            str(count),
            "--sampler",
            str(args.sampler),
            "--seed",
            str(seed),
            "--expected-frequency-start-ghz",
            f"{float(args.expected_frequency_start_ghz):g}",
            "--expected-frequency-stop-ghz",
            f"{float(args.expected_frequency_stop_ghz):g}",
            "--expected-frequency-step-ghz",
            f"{float(args.expected_frequency_step_ghz):g}",
            "--expected-frequency-points",
            str(int(args.expected_frequency_points)),
        ],
        "run_emx_parallel": [
            python_command,
            "scripts/run_candidate_queue_dataset_parallel.py",
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(dataset_dir),
            "--config",
            config,
            "--jobs",
            str(int(args.jobs)),
            "--expected-jobs",
            str(int(args.jobs)),
            "--batch-size",
            str(int(args.batch_size)),
            "--max-count",
            str(count),
            "--expected-count",
            str(count),
            "--force-wideband-5-60-1p0",
            "--expected-port-mode",
            "single_ended_shield_grounded",
            "--expected-pin-purpose",
            "51",
            "--expected-frequency-start-ghz",
            f"{float(args.expected_frequency_start_ghz):g}",
            "--expected-frequency-stop-ghz",
            f"{float(args.expected_frequency_stop_ghz):g}",
            "--expected-frequency-step-ghz",
            f"{float(args.expected_frequency_step_ghz):g}",
            "--expected-frequency-points",
            str(int(args.expected_frequency_points)),
            "--expected-touchstone-extension",
            ".s8p",
            "--expected-ports",
            str(int(args.expected_ports)),
            "--fail-on-error",
            "--resume-completed",
        ],
        "run_quality_gates": [
            python_command,
            "scripts/run_dataset_quality_gates.py",
            str(dataset_dir),
            "--out-dir",
            str(quality_dir),
            "--require-emx",
            "--expected-port-mode",
            "single_ended_shield_grounded",
            "--expected-pin-purpose",
            "51",
            "--expected-frequency-start-ghz",
            f"{float(args.expected_frequency_start_ghz):g}",
            "--expected-frequency-stop-ghz",
            f"{float(args.expected_frequency_stop_ghz):g}",
            "--expected-frequency-step-ghz",
            f"{float(args.expected_frequency_step_ghz):g}",
            "--expected-frequency-points",
            str(int(args.expected_frequency_points)),
            "--touchstone-expected-ports",
            str(int(args.expected_ports)),
            "--touchstone-port-pairs",
            str(args.emx_port_pairs),
            *touchstone_ground_unused_args,
            "--touchstone-all",
            "--extract-response-features",
            "--derive-scalar-q-feature",
            "--scalar-q-definition",
            str(args.scalar_q_definition),
            "--audit-response-feature-coverage",
            "--audit-s8p-physical-feature-dataset",
            "--s8p-expected-count",
            str(count),
            "--s8p-expected-ok-count",
            str(count),
            "--s8p-max-touchstone-checks",
            str(min(count, 1000)),
            "--plan-physical-feature-balanced-acquisition",
            "--physical-feature-columns",
            str(args.feature_columns),
            "--physical-feature-bins",
            str(int(args.physical_feature_bins)),
            "--physical-feature-plan-desired-total-count",
            str(int(args.total_count)),
            "--physical-feature-plan-next-count",
            str(count),
            "--select-physical-feature-validation-samples",
            "--physical-feature-validation-sample-count",
            str(int(args.validation_sample_count)),
            "--physical-feature-validation-mode",
            "coverage_then_random",
            "--build-physical-feature-inverse-training-table",
            "--inverse-geometry-config",
            config,
            "--no-fail-exit",
        ],
        "train_inverse_model": [
            python_command,
            "scripts/train_physical_feature_inverse_model.py",
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(model_dir),
            "--config",
            config,
            "--min-training-rows",
            "8",
            "--no-fail-exit",
        ],
        "audit_inverse_model": [
            python_command,
            "scripts/audit_physical_feature_inverse_model_quality.py",
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(audit_dir),
            "--min-training-rows",
            "8",
            "--no-fail-exit",
        ],
        "plan_nn_architecture_search": [
            python_command,
            "scripts/plan_physical_feature_inverse_nn_architecture_search.py",
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(nn_architecture_dir),
            "--input-columns",
            "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center",
            "--min-training-rows",
            str(count),
            "--no-fail-exit",
        ],
        "train_nn_architecture_search": [
            python_command,
            "scripts/train_physical_feature_inverse_nn_architecture_search.py",
            "--training-csv",
            str(training_csv),
            "--candidate-csv",
            str(nn_architecture_dir / "physical_feature_inverse_nn_architecture_candidates.csv"),
            "--out-dir",
            str(nn_training_dir),
            "--input-columns",
            "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center",
            "--min-training-rows",
            str(count),
            "--max-candidates",
            "48",
            "--no-fail-exit",
        ],
        "audit_chunk_checkpoint": [
            python_command,
            "scripts/audit_s8p_million_chunk_checkpoint.py",
            "--chunk-index",
            str(index + 1),
            "--expected-sample-count",
            str(count),
            "--dataset-dir",
            str(dataset_dir),
            "--quality-dir",
            str(quality_dir),
            "--model-dir",
            str(model_dir),
            "--audit-dir",
            str(audit_dir),
            "--nn-architecture-dir",
            str(nn_architecture_dir),
            "--nn-training-dir",
            str(nn_training_dir),
            "--out-dir",
            str(checkpoint_dir),
            "--min-training-rows",
            str(count),
            "--no-fail-exit",
        ],
    }


def _write_command_script(path: Path, validation_gate: dict[str, Any], chunks: list[dict[str, Any]], args: argparse.Namespace) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated by run_gated_s8p_million_sample_campaign.py",
        f"# Validation gate: {validation_gate.get('status')} ({validation_gate.get('kind')})",
        "",
    ]
    if validation_gate.get("status") != "PASS":
        lines.extend(
            [
                "echo 'STOP: EMX-HFSS S8P validation gate has not passed; million-sample EMX campaign is blocked.' >&2",
                "exit 2",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "echo 'EMX-HFSS S8P validation gate passed; campaign plan is allowed.'",
                "",
            ]
        )
        for chunk in chunks:
            lines.extend(
                [
                    f"echo '== Chunk {chunk['chunk_index']:02d}: samples {chunk['sample_start']}-{chunk['sample_stop']} ==' ",
                    f"mkdir -p {shlex.quote(chunk['chunk_dir'])}",
                ]
            )
            for command_name, command in chunk["commands"].items():
                rendered = _shell_join(command)
                if not args.allow_real_emx and command_name != "build_candidate_queue":
                    lines.append(f"echo '[dry-run] {rendered}'")
                else:
                    lines.append(rendered)
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o755)


def _status_equals(name: str, actual: Any, expected: str) -> Check:
    return Check("PASS" if str(actual) == expected else "FAIL", name, f"actual={actual!r}, expected={expected!r}")


def _status_in(name: str, actual: Any, expected_values: set[str]) -> Check:
    return Check(
        "PASS" if str(actual) in expected_values else "FAIL",
        name,
        f"actual={actual!r}, expected_one_of={sorted(expected_values)!r}",
    )


def _float_equals(name: str, actual: Any, expected: float, tolerance: float) -> Check:
    value = _as_float(actual)
    passed = value is not None and abs(value - float(expected)) <= float(tolerance)
    return Check("PASS" if passed else "FAIL", name, f"actual={actual!r}, expected={expected:g}, tolerance={tolerance:g}")


def _float_le(name: str, actual: Any, expected_max: float) -> Check:
    value = _as_float(actual)
    passed = value is not None and value <= float(expected_max)
    return Check("PASS" if passed else "FAIL", name, f"actual={actual!r}, max={expected_max:g}")


def _int_equals(name: str, actual: Any, expected: int) -> Check:
    try:
        value = int(actual)
    except (TypeError, ValueError):
        return Check("FAIL", name, f"actual={actual!r}, expected={expected}")
    return Check("PASS" if value == int(expected) else "FAIL", name, f"actual={value}, expected={expected}")


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _worst_direct_error(summary: dict[str, Any]) -> float | None:
    compare = _read_json(Path(str(summary.get("compare_summary") or "")).expanduser())
    return _worst_compare_error(compare)


def _worst_postrun_error(summary: dict[str, Any]) -> float | None:
    errors = [_as_float(record.get("worst_percent_error")) for record in (summary.get("records") or []) if isinstance(record, dict)]
    finite = [value for value in errors if value is not None]
    return max(finite) if finite else None


def _worst_latest_import_error(summary: dict[str, Any]) -> float | None:
    postrun = summary.get("postrun_result") if isinstance(summary.get("postrun_result"), dict) else {}
    postrun_summary = postrun.get("summary") if isinstance(postrun.get("summary"), dict) else {}
    return _worst_postrun_error(postrun_summary)


def _worst_compare_error(summary: dict[str, Any]) -> float | None:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    values = []
    for metric in ("k", "qp", "qs", "lp_nh", "ls_nh"):
        item = metrics.get(metric) if isinstance(metrics.get(metric), dict) else {}
        value = _as_float(item.get("max_percent_error"))
        if value is not None:
            values.append(value)
    return max(values) if values else None


def _shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in command)


def _render_report(summary: dict[str, Any]) -> str:
    gate = summary["validation_gate"]
    lines = [
        "# S8P Million-Sample Campaign Plan",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Validation gate: `{gate['status']}` (`{gate['kind']}`)",
        f"- Worst validation error: `{gate.get('worst_percent_error')}` %",
        f"- Total samples: `{summary['total_requested_samples']}`",
        f"- Chunk size: `{summary['chunk_size']}`",
        f"- Chunk count: `{summary['chunk_count']}`",
        f"- Jobs: `{summary['jobs']}`",
        f"- Command script: `{summary['command_script']}`",
        "",
        "## Gate Checks",
        "",
    ]
    for check in summary["checks"]:
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    lines.extend(["", "## Chunk Plan", ""])
    if not summary["chunks"]:
        lines.append("The million-sample campaign is blocked until the EMX-HFSS S8P gate passes.")
    else:
        lines.append("| Chunk | Samples | Cumulative | Required checkpoint |")
        lines.append("| ---: | ---: | ---: | --- |")
        for chunk in summary["chunks"]:
            lines.append(
                f"| {chunk['chunk_index']} | {chunk['sample_count']} | {chunk['cumulative_count']} | "
                "quality gates + physical-feature coverage + inverse model train/audit + NN architecture search/train + chunk checkpoint |"
            )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {item}" for item in summary["method_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
