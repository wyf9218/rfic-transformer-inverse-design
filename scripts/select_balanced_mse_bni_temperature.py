#!/usr/bin/env python3
"""Freeze one Balanced-MSE BNI temperature from MSE validation history only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_COMPLETE_STATUSES = {"PASS", "COMPLETE_REVIEW_REQUIRED"}
VALIDATION_FIELD = "validation_feature_balanced_response_normalized_rmse"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    baseline_path = Path(args.mse_summary).expanduser().resolve()
    history_path = Path(args.mse_history).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "balanced_mse_bni_temperature_selection.json"
    report_path = out_dir / "balanced_mse_bni_temperature_selection.md"

    baseline = _read_json(baseline_path)
    history = _read_csv(history_path)
    response_contract = baseline.get("response_loss_contract") or {}
    split_audit = baseline.get("split_audit") or {}
    best_epochs = baseline.get("best_epochs") or {}
    try:
        best_inverse_epoch = int(best_epochs.get("tandem_inverse"))
    except (TypeError, ValueError):
        best_inverse_epoch = 0
    selected_rows = [
        row
        for row in history
        if str(row.get("stage") or "") == "tandem_inverse"
        and _integer(row.get("epoch")) == best_inverse_epoch
    ]
    validation_rmse = (
        _finite(selected_rows[0].get(VALIDATION_FIELD), positive=True)
        if len(selected_rows) == 1
        else None
    )
    temperature = None if validation_rmse is None else 2.0 * validation_rmse**2
    training_sha = str(baseline.get("training_csv_sha256") or "")
    split_sha = str(split_audit.get("split_fingerprint_sha256") or "")
    partition_sha = str(split_audit.get("physical_cell_partition_fingerprint_sha256") or "")
    input_columns = list(baseline.get("input_columns") or [])
    checks = {
        "mse_summary_exists": baseline_path.is_file(),
        "mse_history_exists": history_path.is_file(),
        "mse_model_complete": baseline.get("overall_status") in VALID_COMPLETE_STATUSES,
        "baseline_loss_family_is_mse": response_contract.get("family") == "mse",
        "physical_cell_grouped_split": split_audit.get("split_mode") == "physical_cell_grouped",
        "physical_cell_bounds_explicit": split_audit.get("physical_cell_range_source") == "explicit",
        "four_ordered_physical_inputs": _physical_semantics(input_columns) == ["lp", "ls", "q", "k"],
        "training_csv_sha256_present": _is_sha256(training_sha),
        "split_fingerprint_present": _is_sha256(split_sha),
        "cell_partition_fingerprint_present": _is_sha256(partition_sha),
        "best_inverse_epoch_positive": best_inverse_epoch > 0,
        "one_matching_validation_history_row": len(selected_rows) == 1,
        "validation_rmse_finite_positive": validation_rmse is not None,
        "derived_temperature_finite_positive": temperature is not None
        and math.isfinite(temperature)
        and temperature > 0.0,
        "derived_temperature_within_numerical_guard": temperature is not None
        and float(args.min_temperature) <= temperature <= float(args.max_temperature),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    decision = "RUN_SINGLE_BNI_ARM_WITH_RECORDED_TEMPERATURE" if status == "PASS" else "DO_NOT_RUN_BNI_FIX_SELECTION_CONTRACT"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "checks": checks,
        "method": "single_temperature_from_best_MSE_validation_epoch",
        "formula": "tau = 2 * validation_feature_balanced_response_normalized_rmse^2",
        "paper_basis": "Ren et al., Balanced MSE, CVPR 2022; tau=2*sigma^2",
        "best_inverse_epoch": best_inverse_epoch if best_inverse_epoch > 0 else None,
        "validation_metric_field": VALIDATION_FIELD,
        "validation_rmse": validation_rmse,
        "selected_temperature_tau": temperature,
        "temperature_numerical_guard": {
            "minimum": float(args.min_temperature),
            "maximum": float(args.max_temperature),
            "boundary": "The guard rejects a numerically pathological scale; it does not clip or tune tau.",
        },
        "test_metrics_used": False,
        "test_predictions_used": False,
        "hyperparameter_sweep_performed": False,
        "provenance": {
            "mse_summary": str(baseline_path),
            "mse_summary_sha256": _sha256_file(baseline_path) if baseline_path.is_file() else "",
            "mse_history": str(history_path),
            "mse_history_sha256": _sha256_file(history_path) if history_path.is_file() else "",
            "training_csv_sha256": training_sha,
            "split_fingerprint_sha256": split_sha,
            "physical_cell_partition_fingerprint_sha256": partition_sha,
        },
        "scientific_boundary": (
            "This freezes one BNI temperature without reading test metrics or predictions. It does not prove BNI "
            "is better. The fixed MSE and BNI models may be compared on the complete test set only after training."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"selected_temperature_tau={temperature}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mse-summary", required=True)
    parser.add_argument("--mse-history", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-temperature", type=float, default=1.0e-8)
    parser.add_argument("--max-temperature", type=float, default=100.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(float(args.min_temperature)) or float(args.min_temperature) <= 0.0:
        parser.error("--min-temperature must be finite and positive")
    if not math.isfinite(float(args.max_temperature)) or float(args.max_temperature) <= float(args.min_temperature):
        parser.error("--max-temperature must be finite and greater than --min-temperature")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def _integer(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number


def _finite(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0.0):
        return None
    return number


def _physical_semantics(columns: list[str]) -> list[str]:
    result = []
    for column in columns:
        name = str(column).lower().removeprefix("input__").removeprefix("phys__")
        if name.startswith("lp"):
            result.append("lp")
        elif name.startswith("ls"):
            result.append("ls")
        elif name.startswith(("q", "qp", "qs")):
            result.append("q")
        elif name.startswith("k") or "kw" in name:
            result.append("k")
        else:
            result.append("")
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _render_report(payload: dict[str, Any]) -> str:
    checks = "\n".join(
        f"- {name}: {'PASS' if passed else 'FAIL'}"
        for name, passed in payload["checks"].items()
    )
    return (
        "# Balanced-MSE BNI temperature selection\n\n"
        f"- Overall status: `{payload['overall_status']}`\n"
        f"- Decision: `{payload['decision']}`\n"
        f"- Best inverse epoch: `{payload['best_inverse_epoch']}`\n"
        f"- Validation RMSE: `{payload['validation_rmse']}`\n"
        f"- Selected tau: `{payload['selected_temperature_tau']}`\n"
        "- Test metrics used: `False`\n"
        "- Hyperparameter sweep: `False`\n\n"
        "## Contract checks\n\n"
        f"{checks}\n\n"
        "## Scientific boundary\n\n"
        f"{payload['scientific_boundary']}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
