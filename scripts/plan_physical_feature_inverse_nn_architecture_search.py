#!/usr/bin/env python3
"""Plan per-chunk NN architecture search for physical-feature inverse design.

The accepted workflow input is physical features (Lp, Ls, Q, K/Kw), not Zin.
This script is intentionally gate-friendly: it can be run before a 100k chunk
exists, records WAITING evidence, and writes the exact MLP architecture search
contract that should run once the chunk training table is available.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_FEATURE_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center"


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
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(training_csv)
    feature_columns = _resolve_columns(rows, args.input_columns, args.input_prefix, default=DEFAULT_FEATURE_COLUMNS)
    geometry_columns = _resolve_columns(rows, args.geometry_columns, args.geom_prefix)
    candidates = _architecture_candidates(args)
    candidate_csv = out_dir / "physical_feature_inverse_nn_architecture_candidates.csv"
    runbook_path = out_dir / "physical_feature_inverse_nn_architecture_search_runbook.sh"
    summary_path = out_dir / "physical_feature_inverse_nn_architecture_search_summary.json"
    report_path = out_dir / "physical_feature_inverse_nn_architecture_search_report.md"

    checks = [
        _check("training CSV exists", training_csv.is_file(), str(training_csv)),
        _check("training rows present", bool(rows), f"rows={len(rows)}"),
        _check("training rows meet minimum", len(rows) >= int(args.min_training_rows), f"rows={len(rows)}, minimum={args.min_training_rows}"),
        _check("input columns present", bool(feature_columns), ",".join(feature_columns)),
        _check("geometry columns present", bool(geometry_columns), ",".join(geometry_columns[:8])),
        *_physical_feature_checks(feature_columns, args),
        _check("architecture candidates present", bool(candidates), f"candidates={len(candidates)}"),
    ]
    status = "PASS" if all(check.pass_bool for check in checks) else ("WAITING_FOR_TRAINING_CSV" if not training_csv.is_file() else "FAIL")
    decision = {
        "PASS": "READY_TO_RUN_NN_ARCHITECTURE_SEARCH_FOR_THIS_CHUNK",
        "WAITING_FOR_TRAINING_CSV": "WAIT_FOR_100K_CHUNK_PHYSICAL_FEATURE_TRAINING_TABLE",
        "FAIL": "FIX_TRAINING_TABLE_BEFORE_NN_ARCHITECTURE_SEARCH",
    }[status]

    _write_csv(candidate_csv, candidates)
    runbook_path.write_text(_render_runbook(args, training_csv, out_dir, candidates), encoding="utf-8")
    runbook_path.chmod(0o755)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "training_csv": str(training_csv),
        "out_dir": str(out_dir),
        "candidate_csv": str(candidate_csv),
        "runbook": str(runbook_path),
        "training_count": len(rows),
        "min_training_rows": int(args.min_training_rows),
        "input_columns": feature_columns,
        "geometry_columns": geometry_columns,
        "architecture_candidate_count": len(candidates),
        "search_protocol": _search_protocol(args),
        "selection_rule": _selection_rule(args),
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This script plans the NN architecture search; it does not fabricate trained model metrics.",
            "The search must be run only on real EMX-labeled physical-feature training rows after the EMX/HFSS gate has passed.",
            "Selected neural inverse-design candidates still require layout checks, EMX .s8p generation, and EMX/HFSS physical-curve validation.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"candidate_csv={candidate_csv}")
    print(f"runbook={runbook_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--geometry-columns")
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--min-training-rows", type=int, default=100_000)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.10)
    parser.add_argument("--seeds", default="20260626,20260627,20260628")
    parser.add_argument("--hidden-widths", default="128,256,512")
    parser.add_argument("--depths", default="2,3,4")
    parser.add_argument("--dropouts", default="0,0.05,0.10")
    parser.add_argument("--learning-rates", default="0.001,0.0005")
    parser.add_argument("--weight-decays", default="0,0.0001")
    parser.add_argument("--batch-sizes", default="512,1024")
    parser.add_argument("--max-candidates", type=int, default=48)
    parser.add_argument("--primary-metric", default="validation_normalized_rmse")
    parser.add_argument("--secondary-metric", default="geometry_bound_violation_rate")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_columns(rows: list[dict[str, str]], explicit: str | None, prefix: str, *, default: str = "") -> list[str]:
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]
    if not rows:
        return [item.strip() for item in default.split(",") if item.strip()]
    return sorted(column for column in rows[0] if column.startswith(prefix))


def _physical_feature_checks(feature_columns: list[str], args: argparse.Namespace) -> list[Check]:
    normalized = {column.removeprefix(str(args.input_prefix)).lower() for column in feature_columns}
    zin_columns = [column for column in feature_columns if "zin" in column.lower()]
    required_groups = {
        "lp": any("lp" in column for column in normalized),
        "ls": any("ls" in column for column in normalized),
        "q": any(column.startswith("q") or "_q" in column for column in normalized),
        "k_or_kw": any("k" in column for column in normalized),
    }
    return [
        _check("inverse NN inputs do not use Zin", not zin_columns, f"zin_columns={zin_columns}"),
        _check("inverse NN inputs include Lp/Ls/Q/K", all(required_groups.values()), str(required_groups)),
    ]


def _architecture_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    widths = _parse_ints(args.hidden_widths)
    depths = _parse_ints(args.depths)
    dropouts = _parse_floats(args.dropouts)
    learning_rates = _parse_floats(args.learning_rates)
    weight_decays = _parse_floats(args.weight_decays)
    batch_sizes = _parse_ints(args.batch_sizes)
    seeds = _parse_ints(args.seeds)
    candidates: list[dict[str, Any]] = []
    index = 1
    for depth in depths:
        for width in widths:
            for dropout in dropouts:
                for lr in learning_rates:
                    for wd in weight_decays:
                        for batch_size in batch_sizes:
                            for seed in seeds:
                                candidates.append(
                                    {
                                        "candidate_id": f"mlp_{index:03d}",
                                        "model_family": "mlp_residual",
                                        "hidden_depth": depth,
                                        "hidden_width": width,
                                        "activation": "gelu",
                                        "normalization": "standardize_inputs_and_outputs",
                                        "dropout": dropout,
                                        "learning_rate": lr,
                                        "weight_decay": wd,
                                        "batch_size": batch_size,
                                        "seed": seed,
                                        "max_epochs": 300,
                                        "early_stopping_patience": 25,
                                        "loss": "mse_on_standardized_geometry",
                                    }
                                )
                                index += 1
                                if len(candidates) >= int(args.max_candidates):
                                    return candidates
    return candidates


def _search_protocol(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "split": "deterministic_shuffle_train_validation_test",
        "validation_fraction": float(args.validation_fraction),
        "test_fraction": float(args.test_fraction),
        "input_scaling": "standardize Lp/Ls/Q/K features using training split only",
        "output_scaling": "standardize geometry outputs using training split only",
        "early_stopping": "validation loss patience per candidate",
        "repeat_seeds": _parse_ints(args.seeds),
        "checkpoint_frequency": "after every 100k generated EMX samples",
    }


def _selection_rule(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "primary_metric": str(args.primary_metric),
        "secondary_metric": str(args.secondary_metric),
        "tie_breakers": [
            "lower test_normalized_rmse",
            "lower maximum per-geometry normalized error",
            "lower geometry_bound_violation_rate",
            "smaller model when metrics are statistically tied",
        ],
        "acceptance_notes": [
            "A selected NN architecture is not accepted by ML metrics alone.",
            "Top predicted geometries must be checked by layout rules and re-simulated in EMX.",
            "Periodic HFSS correlation samples remain required after the EMX/HFSS gate.",
        ],
    }


def _render_runbook(args: argparse.Namespace, training_csv: Path, out_dir: Path, candidates: list[dict[str, Any]]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Planned NN architecture search commands.",
        "# This runbook intentionally records architecture configs only; add the real NN trainer when available.",
        f"TRAINING_CSV={_shell_quote(str(training_csv))}",
        f"OUT_DIR={_shell_quote(str(out_dir))}",
        "echo \"training_csv=${TRAINING_CSV}\"",
        "echo \"out_dir=${OUT_DIR}\"",
        "",
    ]
    for candidate in candidates:
        lines.append(
            "echo "
            + _shell_quote(
                "candidate={candidate_id} depth={hidden_depth} width={hidden_width} dropout={dropout} "
                "lr={learning_rate} wd={weight_decay} batch={batch_size} seed={seed}".format(**candidate)
            )
        )
    lines.append("")
    return "\n".join(lines)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-Feature Inverse NN Architecture Search Plan",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Training rows: `{summary['training_count']}`",
        f"- Architecture candidates: `{summary['architecture_candidate_count']}`",
        f"- Candidate CSV: `{summary['candidate_csv']}`",
        "",
        "## Contract",
        "",
        f"- Inputs: `{', '.join(summary['input_columns'])}`",
        f"- Outputs: `{', '.join(summary['geometry_columns'])}`",
        f"- Primary metric: `{summary['selection_rule']['primary_metric']}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {check['status']}: {check['name']} - {check['detail']}" for check in summary["checks"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_ints(text: str) -> list[int]:
    return [int(item.strip()) for item in str(text).split(",") if item.strip()]


def _parse_floats(text: str) -> list[float]:
    values = [float(item.strip()) for item in str(text).split(",") if item.strip()]
    return [value for value in values if math.isfinite(value)]


def _check(name: str, passed: bool, detail: Any) -> Check:
    return Check("PASS" if passed else "FAIL", name, str(detail))


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
