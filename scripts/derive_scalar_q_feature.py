#!/usr/bin/env python3
"""Derive a single scalar Q feature from Qp/Qs dataset columns.

The project goal now says the inverse-design input should be Lp/Ls/Q/K. The
existing extraction keeps Qp and Qs separate to avoid guessing the meaning of
"Q". This script creates an explicitly defined scalar Q column, such as
``q_center = min(qp_center, qs_center)`` or a mean, without modifying the
original dataset.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


Q_DEFINITIONS = ("min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = Path(args.csv).expanduser().resolve() if args.csv else dataset_dir / "dataset_rows.csv"
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else dataset_dir / "dataset_manifest.json"
    output_rows_path = out_dir / "dataset_rows.csv"
    output_manifest_path = out_dir / "dataset_manifest.json"
    summary_path = out_dir / "scalar_q_feature_summary.json"
    report_path = out_dir / "scalar_q_feature_report.md"

    rows = _read_rows(rows_path)
    manifest = _read_json(manifest_path)
    updated_rows, row_records = _derive_rows(rows, dataset_dir, args)
    fail_records = [record for record in row_records if record["status"] == "FAIL"]
    valid_values = [float(record["q_value"]) for record in row_records if record["status"] == "PASS"]
    status = "PASS" if rows and valid_values and not fail_records else "FAIL"

    _write_rows(updated_rows, output_rows_path)
    updated_manifest = dict(manifest)
    updated_manifest["scalar_q_feature"] = {
        "output_column": str(args.output_column),
        "definition": str(args.q_definition),
        "primary_column": str(args.primary_column),
        "secondary_column": str(args.secondary_column),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "valid_count": len(valid_values),
        "fail_count": len(fail_records),
    }
    output_manifest_path.write_text(json.dumps(updated_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.copy_touchstones:
        _copy_lightweight_artifacts(dataset_dir, out_dir)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_SCALAR_Q_DERIVED_DATASET" if status == "PASS" else "DO_NOT_USE_SCALAR_Q_DERIVED_DATASET",
        "dataset_dir": str(dataset_dir),
        "input_rows_csv": str(rows_path),
        "input_manifest": str(manifest_path),
        "out_dir": str(out_dir),
        "output_rows_csv": str(output_rows_path),
        "output_manifest": str(output_manifest_path),
        "row_count": len(rows),
        "valid_q_count": len(valid_values),
        "fail_count": len(fail_records),
        "q_summary": _value_summary(valid_values),
        "row_records_preview": row_records[:20],
        "input_source": _file_source(rows_path),
        "arguments": vars(args),
        "limitations": [
            "This script does not choose the project Q definition; it applies the explicit --q-definition requested by the user.",
            "The derived q_center column is a deterministic post-processing feature from simulator-derived Qp/Qs labels.",
            "Original Qp/Qs columns are preserved in the output CSV.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"output_rows_csv={output_rows_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--csv", help="Override input dataset_rows.csv")
    parser.add_argument("--manifest", help="Override input dataset_manifest.json")
    parser.add_argument("--q-definition", required=True, choices=Q_DEFINITIONS)
    parser.add_argument("--output-column", default="q_center")
    parser.add_argument("--primary-column", default="qp_center")
    parser.add_argument("--secondary-column", default="qs_center")
    parser.add_argument("--require-positive-input-q", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--copy-touchstones",
        action="store_true",
        help="Copy evaluations/ into out-dir so tools that check Touchstone existence can run on the derived dataset.",
    )
    parser.add_argument(
        "--absolute-touchstone-paths",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When not copying evaluations/, rewrite relative touchstone_path values to absolute paths pointing at the source dataset.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}


def _derive_rows(
    rows: list[dict[str, str]],
    dataset_dir: Path,
    args: argparse.Namespace,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    updated_rows: list[dict[str, str]] = []
    records = []
    for idx, row in enumerate(rows):
        updated = dict(row)
        if bool(args.absolute_touchstone_paths) and not bool(args.copy_touchstones):
            for key in ("touchstone_path", "raw_touchstone_path"):
                raw_path = str(updated.get(key) or "").strip()
                if raw_path and not Path(raw_path).expanduser().is_absolute():
                    updated[key] = str((dataset_dir / raw_path).resolve())
        qp = _row_float(row, _q_column_aliases(str(args.primary_column), "primary"))
        qs = _row_float(row, _q_column_aliases(str(args.secondary_column), "secondary"))
        record = {
            "row_index": idx,
            "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{idx}",
            "status": "PASS",
            "reason": "",
            "qp": qp,
            "qs": qs,
            "q_value": None,
        }
        if qp is None or qs is None:
            record.update({"status": "FAIL", "reason": "missing or non-finite Qp/Qs"})
        elif bool(args.require_positive_input_q) and (qp <= 0.0 or qs <= 0.0):
            record.update({"status": "FAIL", "reason": "Qp/Qs must be positive"})
        else:
            try:
                q_value = _derive_q(float(qp), float(qs), str(args.q_definition))
            except ValueError as exc:
                record.update({"status": "FAIL", "reason": str(exc)})
            else:
                updated[str(args.output_column)] = _format_float(q_value)
                record["q_value"] = float(q_value)
        updated_rows.append(updated)
        records.append(record)
    return updated_rows, records


def _derive_q(qp: float, qs: float, definition: str) -> float:
    if definition == "min":
        return min(qp, qs)
    if definition == "mean":
        return 0.5 * (qp + qs)
    if definition == "geometric_mean":
        if qp <= 0.0 or qs <= 0.0:
            raise ValueError("geometric_mean requires positive Qp/Qs")
        return math.sqrt(qp * qs)
    if definition == "harmonic_mean":
        if qp <= 0.0 or qs <= 0.0:
            raise ValueError("harmonic_mean requires positive Qp/Qs")
        return 2.0 / (1.0 / qp + 1.0 / qs)
    if definition == "primary":
        return qp
    if definition == "secondary":
        return qs
    raise ValueError(f"unsupported q definition: {definition}")


def _write_rows(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _copy_lightweight_artifacts(dataset_dir: Path, out_dir: Path) -> None:
    evaluations = dataset_dir / "evaluations"
    if evaluations.is_dir():
        target = out_dir / "evaluations"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(evaluations, target)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _row_float(row: dict[str, str], candidates: list[str]) -> float | None:
    normalized = {_normalize_column_name(key): key for key in row}
    for candidate in candidates:
        if candidate in row:
            value = _as_float(row.get(candidate))
            if value is not None:
                return value
        actual = normalized.get(_normalize_column_name(candidate))
        if actual is not None:
            value = _as_float(row.get(actual))
            if value is not None:
                return value
    return None


def _q_column_aliases(preferred: str, side: str) -> list[str]:
    if side == "primary":
        aliases = [
            preferred,
            "qp_center",
            "q_p_center",
            "Qp_center",
            "Qp",
            "qp",
            "q_primary_center",
            "primary_q_center",
        ]
    else:
        aliases = [
            preferred,
            "qs_center",
            "q_s_center",
            "Qs_center",
            "Qs",
            "qs",
            "q_secondary_center",
            "secondary_q_center",
        ]
    out: list[str] = []
    for item in aliases:
        if item and item not in out:
            out.append(item)
    return out


def _normalize_column_name(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _format_float(value: float) -> str:
    return f"{float(value):.12g}"


def _value_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "std": None}
    arr = np.asarray(values, dtype=float)
    return {"min": float(np.min(arr)), "max": float(np.max(arr)), "mean": float(np.mean(arr)), "std": float(np.std(arr))}


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    return out


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Scalar Q Feature Derivation",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Definition: `{summary['arguments']['q_definition']}`",
        f"Output column: `{summary['arguments']['output_column']}`",
        f"Valid rows: `{summary['valid_q_count']}`",
        f"Failed rows: `{summary['fail_count']}`",
        f"Output CSV: `{summary['output_rows_csv']}`",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
