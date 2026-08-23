#!/usr/bin/env python3
"""Audit whether a real EMX pool is ready for the 200k Q-input ablation.

The audit does not train a model and does not alter the accepted pool. It
proves that Qp/Qs, the baseline Q=min(Qp,Qs), frequency metadata, Touchstone
provenance, and independent geometry identity survived the data pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASELINE_FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
SEPARATE_Q_FEATURES = ("lp_nh_center", "ls_nh_center", "qp_center", "qs_center", "k_abs_center")
WIDEBAND_SUMMARY_COLUMNS = (
    "lp_nh_min",
    "lp_nh_max",
    "ls_nh_min",
    "ls_nh_max",
    "qp_min",
    "qp_max",
    "qs_min",
    "qs_max",
    "k_min",
    "k_max",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_csv = _resolve_dataset_csv(args)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_rows(dataset_csv)
    geometry_columns = sorted(column for column in (rows[0] if rows else {}) if column.startswith(args.geometry_prefix))

    baseline_valid = 0
    qp_qs_valid = 0
    q_consistent = 0
    q_mismatches: list[dict[str, Any]] = []
    frequency_valid = 0
    touchstone_valid = 0
    wideband_valid = 0
    accepted_ok = 0
    geometry_keys: set[str] = set()
    geometry_complete = 0
    invalid_reasons: dict[str, int] = {}

    for index, row in enumerate(rows):
        if _truthy(row.get("ok", "true")):
            accepted_ok += 1
        else:
            _increment(invalid_reasons, "not_ok")

        baseline = [_as_float(row.get(column)) for column in BASELINE_FEATURES]
        if all(value is not None for value in baseline):
            baseline_valid += 1
        else:
            _increment(invalid_reasons, "missing_baseline_feature")

        qp = _as_float(row.get("qp_center"))
        qs = _as_float(row.get("qs_center"))
        q = _as_float(row.get("q_center"))
        if qp is not None and qs is not None:
            qp_qs_valid += 1
            expected_q = min(qp, qs)
            if q is not None and math.isclose(q, expected_q, rel_tol=float(args.q_rel_tolerance), abs_tol=float(args.q_abs_tolerance)):
                q_consistent += 1
            else:
                if len(q_mismatches) < int(args.max_examples):
                    q_mismatches.append(
                        {"row_index": index, "q_center": q, "qp_center": qp, "qs_center": qs, "expected_min_q": expected_q}
                    )
                _increment(invalid_reasons, "q_not_min_qp_qs")
        else:
            _increment(invalid_reasons, "missing_qp_or_qs")

        if _frequency_matches(row, args):
            frequency_valid += 1
        else:
            _increment(invalid_reasons, "frequency_contract_mismatch")

        touchstone = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        if touchstone and Path(touchstone).suffix.lower() == str(args.expected_touchstone_extension).lower():
            touchstone_valid += 1
        else:
            _increment(invalid_reasons, "touchstone_provenance_mismatch")

        if all(_as_float(row.get(column)) is not None for column in WIDEBAND_SUMMARY_COLUMNS):
            wideband_valid += 1

        geometry_values = [_as_float(row.get(column)) for column in geometry_columns]
        if geometry_columns and all(value is not None for value in geometry_values):
            geometry_complete += 1
            geometry_keys.add("|".join(f"{float(value):.12g}" for value in geometry_values if value is not None))
        else:
            _increment(invalid_reasons, "missing_geometry")

    row_count = len(rows)
    checks = {
        "dataset_csv_exists": dataset_csv.is_file(),
        "minimum_rows_reached": row_count >= int(args.min_rows),
        "all_rows_marked_ok": row_count > 0 and accepted_ok == row_count,
        "baseline_features_complete": row_count > 0 and baseline_valid == row_count,
        "qp_qs_complete": row_count > 0 and qp_qs_valid == row_count,
        "q_equals_min_qp_qs": row_count > 0 and q_consistent == row_count,
        "frequency_contract_exact": row_count > 0 and frequency_valid == row_count,
        "touchstone_provenance_exact": row_count > 0 and touchstone_valid == row_count,
        "geometry_columns_present": bool(geometry_columns),
        "geometry_complete": row_count > 0 and geometry_complete == row_count,
        "geometry_unique": row_count > 0 and len(geometry_keys) == row_count,
    }
    evidence_checks = {name: value for name, value in checks.items() if name != "minimum_rows_reached"}
    if not dataset_csv.is_file() or row_count == 0:
        status = "WAITING_FOR_DATASET"
        decision = "WAIT_FOR_REAL_EMX_ACCEPTED_POOL"
    elif row_count < int(args.min_rows):
        status = "WAITING_FOR_200K"
        decision = "CONTINUE_REAL_EMX_ACQUISITION_WITHOUT_CHANGING_100K_CONTRACT"
    elif all(evidence_checks.values()):
        status = "PASS"
        decision = "RUN_SHARED_SPLIT_QP_QS_INPUT_ABLATION"
    else:
        status = "FAIL"
        decision = "DO_NOT_RUN_Q_INPUT_ABLATION_FIX_DATA_CONTRACT"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "dataset_csv": str(dataset_csv),
        "row_count": row_count,
        "min_rows": int(args.min_rows),
        "counts": {
            "accepted_ok": accepted_ok,
            "baseline_valid": baseline_valid,
            "qp_qs_valid": qp_qs_valid,
            "q_consistent": q_consistent,
            "frequency_valid": frequency_valid,
            "touchstone_valid": touchstone_valid,
            "wideband_summary_valid": wideband_valid,
            "geometry_complete": geometry_complete,
            "geometry_unique": len(geometry_keys),
        },
        "checks": checks,
        "invalid_reasons": invalid_reasons,
        "q_mismatch_examples": q_mismatches,
        "geometry_columns": geometry_columns,
        "input_ablations": {
            "A_current_contract": list(BASELINE_FEATURES),
            "B_separate_qp_qs": list(SEPARATE_Q_FEATURES),
            "shared_split_required": True,
            "shared_split_basis": list(BASELINE_FEATURES),
            "comparison_boundary": "A and B must use the same row-index fingerprint; model completion alone does not select a winner.",
        },
        "wideband_summary": {
            "columns": list(WIDEBAND_SUMMARY_COLUMNS),
            "complete_row_count": wideband_valid,
            "required_for_q_ablation": False,
            "use": "Forward-proxy auxiliary diagnostics only; these values are not silently added to the inverse target contract.",
        },
        "frequency_contract": {
            "start_hz": float(args.expected_frequency_start_ghz) * 1.0e9,
            "stop_hz": float(args.expected_frequency_stop_ghz) * 1.0e9,
            "step_hz": float(args.expected_frequency_step_ghz) * 1.0e9,
            "points": int(args.expected_frequency_points),
        },
        "scientific_boundary": (
            "PASS proves data readiness for a controlled Q-input ablation. It does not prove that separate Qp/Qs improves inverse design; "
            "that requires shared-split model metrics and real EMX closed-loop validation."
        ),
        "arguments": vars(args),
    }
    summary_path = out_dir / "physical_feature_input_ablation_readiness_summary.json"
    report_path = out_dir / "physical_feature_input_ablation_readiness_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if status in {"PASS", "WAITING_FOR_DATASET", "WAITING_FOR_200K"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir")
    parser.add_argument("--dataset-csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-rows", type=int, default=200_000)
    parser.add_argument("--geometry-prefix", default="geom__")
    parser.add_argument("--expected-touchstone-extension", default=".s4p")
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument("--q-abs-tolerance", type=float, default=1.0e-9)
    parser.add_argument("--q-rel-tolerance", type=float, default=1.0e-9)
    parser.add_argument("--max-examples", type=int, default=20)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.dataset_dir) == bool(args.dataset_csv):
        parser.error("supply exactly one of --dataset-dir or --dataset-csv")
    if int(args.min_rows) < 1:
        parser.error("--min-rows must be positive")
    return args


def _resolve_dataset_csv(args: argparse.Namespace) -> Path:
    if args.dataset_csv:
        return Path(args.dataset_csv).expanduser().resolve()
    return (Path(args.dataset_dir).expanduser().resolve() / "dataset_rows.csv")


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _frequency_matches(row: dict[str, str], args: argparse.Namespace) -> bool:
    expected = (
        float(args.expected_frequency_start_ghz) * 1.0e9,
        float(args.expected_frequency_stop_ghz) * 1.0e9,
        float(args.expected_frequency_step_ghz) * 1.0e9,
    )
    actual = (
        _as_float(row.get("sparam_freq_start_hz")),
        _as_float(row.get("sparam_freq_stop_hz")),
        _as_float(row.get("sparam_freq_step_hz")),
    )
    points = _as_int(row.get("sparam_freq_points"))
    tolerance = float(args.frequency_tolerance_hz)
    return all(value is not None and abs(float(value) - target) <= tolerance for value, target in zip(actual, expected)) and points == int(
        args.expected_frequency_points
    )


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "ok"}


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = int(counts.get(key, 0)) + 1


def _render_report(summary: dict[str, Any]) -> str:
    checks = "\n".join(f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in summary["checks"].items())
    counts = "\n".join(f"- {name}: {value}" for name, value in summary["counts"].items())
    return f"""# Physical-feature input ablation readiness

- Status: `{summary['overall_status']}`
- Decision: `{summary['decision']}`
- Rows: `{summary['row_count']}` / required `{summary['min_rows']}`
- Dataset: `{summary['dataset_csv']}`

## Checks

{checks}

## Counts

{counts}

## Compared inputs

- A: `{summary['input_ablations']['A_current_contract']}`
- B: `{summary['input_ablations']['B_separate_qp_qs']}`
- Shared split basis: `{summary['input_ablations']['shared_split_basis']}`

## Boundary

{summary['scientific_boundary']}
"""


if __name__ == "__main__":
    raise SystemExit(main())
