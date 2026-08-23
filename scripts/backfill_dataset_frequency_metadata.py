#!/usr/bin/env python3
"""Backfill per-row Touchstone frequency metadata into an existing dataset CSV.

This helper is intended for older MARS dataset runs generated before
`sparam_freq_*` columns were written. It does not create EM data; it only parses
already-existing Touchstone files and writes frequency coverage metadata so the
strict validator can prove whether a run is narrowband or wideband.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone


FREQUENCY_COLUMNS = (
    "sparam_freq_start_hz",
    "sparam_freq_stop_hz",
    "sparam_freq_points",
    "sparam_freq_step_hz",
    "sparam_freq_step_min_hz",
    "sparam_freq_step_max_hz",
    "sparam_freq_step_span_hz",
)


@dataclass(frozen=True)
class FrequencySpec:
    start_hz: float | None = None
    stop_hz: float | None = None
    points: int | None = None
    step_hz: float | None = None
    step_min_hz: float | None = None
    step_max_hz: float | None = None
    step_span_hz: float | None = None

    def available(self) -> bool:
        return (
            self.start_hz is not None
            and self.stop_hz is not None
            and self.points is not None
            and self.step_hz is not None
        )

    def as_row_values(self) -> dict[str, str]:
        return {
            "sparam_freq_start_hz": _format_number(self.start_hz),
            "sparam_freq_stop_hz": _format_number(self.stop_hz),
            "sparam_freq_points": "" if self.points is None else str(int(self.points)),
            "sparam_freq_step_hz": _format_number(self.step_hz),
            "sparam_freq_step_min_hz": _format_number(self.step_min_hz),
            "sparam_freq_step_max_hz": _format_number(self.step_max_hz),
            "sparam_freq_step_span_hz": _format_number(self.step_span_hz),
        }

    def as_manifest(self) -> dict[str, float | int | None]:
        return {
            "start_hz": self.start_hz,
            "stop_hz": self.stop_hz,
            "points": self.points,
            "step_hz": self.step_hz,
            "step_min_hz": self.step_min_hz,
            "step_max_hz": self.step_max_hz,
            "step_span_hz": self.step_span_hz,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Directory containing dataset_manifest.json and dataset_rows.csv")
    parser.add_argument("--csv", default=None, help="Override input CSV path")
    parser.add_argument("--manifest", default=None, help="Override input manifest path")
    parser.add_argument("--output-csv", default=None, help="Output CSV path; defaults to dataset_rows_frequency_backfilled.csv")
    parser.add_argument("--output-manifest", default=None, help="Output manifest path; defaults to dataset_manifest_frequency_backfilled.json")
    parser.add_argument("--summary", default=None, help="Output JSON summary path")
    parser.add_argument("--in-place", action="store_true", help="Overwrite dataset_rows.csv and dataset_manifest.json after writing .bak files")
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-points", type=int, default=None)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    csv_path = Path(args.csv).expanduser().resolve() if args.csv else dataset_dir / "dataset_rows.csv"
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else dataset_dir / "dataset_manifest.json"
    output_csv = (
        Path(args.output_csv).expanduser().resolve()
        if args.output_csv
        else (csv_path if args.in_place else dataset_dir / "dataset_rows_frequency_backfilled.csv")
    )
    output_manifest = (
        Path(args.output_manifest).expanduser().resolve()
        if args.output_manifest
        else (manifest_path if args.in_place else dataset_dir / "dataset_manifest_frequency_backfilled.json")
    )
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else dataset_dir / "frequency_backfill_summary.json"

    manifest = _read_json(manifest_path)
    rows = _read_rows(csv_path)
    expected = _expected_frequency(args)

    updated_rows: list[dict[str, str]] = []
    specs: list[FrequencySpec] = []
    errors: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    ok_row_count = 0
    parsed_count = 0
    for index, row in enumerate(rows):
        updated = dict(row)
        if _as_bool(row.get("ok")):
            ok_row_count += 1
            path = _resolve_touchstone_path(dataset_dir, row.get("touchstone_path") or "")
            if path is None:
                errors.append({"row": index, "error": "missing touchstone_path"})
            elif not path.exists():
                errors.append({"row": index, "path": str(path), "error": "touchstone_path does not exist"})
            else:
                try:
                    spec = _frequency_spec_from_touchstone(path)
                except Exception as exc:  # noqa: BLE001 - keep exact parser failure in summary.
                    errors.append({"row": index, "path": str(path), "error": f"{type(exc).__name__}: {exc}"})
                else:
                    parsed_count += 1
                    specs.append(spec)
                    updated.update(spec.as_row_values())
                    mismatch = _frequency_mismatch(spec, expected, float(args.frequency_tolerance_hz))
                    if mismatch:
                        mismatches.append({"row": index, "path": str(path), "mismatch": mismatch})
        updated_rows.append(updated)

    common_spec = _common_spec(specs, tolerance_hz=float(args.frequency_tolerance_hz))
    updated_manifest = dict(manifest)
    if common_spec is not None:
        updated_manifest["target_frequency"] = common_spec.as_manifest()
        spq = dict(updated_manifest.get("sparameter_quality") or {})
        spq["frequency_point_count"] = _summary_numbers([float(spec.points) for spec in specs if spec.points is not None])
        spq["frequency_start_hz"] = _summary_numbers([spec.start_hz for spec in specs if spec.start_hz is not None])
        spq["frequency_stop_hz"] = _summary_numbers([spec.stop_hz for spec in specs if spec.stop_hz is not None])
        spq["frequency_step_hz"] = _summary_numbers([spec.step_hz for spec in specs if spec.step_hz is not None])
        spq["frequency_step_span_hz"] = _summary_numbers([spec.step_span_hz for spec in specs if spec.step_span_hz is not None])
        updated_manifest["sparameter_quality"] = spq

    status = "PASS"
    reasons: list[str] = []
    if errors:
        status = "FAIL"
        reasons.append(f"{len(errors)} ok rows could not be parsed")
    if mismatches:
        status = "FAIL"
        reasons.append(f"{len(mismatches)} ok rows do not match expected frequency")
    if ok_row_count and parsed_count != ok_row_count:
        status = "FAIL"
        reasons.append(f"parsed_count {parsed_count} != ok_row_count {ok_row_count}")
    if specs and common_spec is None:
        status = "FAIL"
        reasons.append("parsed Touchstone files do not share one common frequency grid")

    if args.in_place:
        _backup_once(csv_path)
        _backup_once(manifest_path)
    _write_rows(updated_rows, output_csv)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(updated_manifest, indent=2), encoding="utf-8")
    summary = {
        "overall_status": status,
        "reasons": reasons,
        "dataset_dir": str(dataset_dir),
        "input_csv": str(csv_path),
        "output_csv": str(output_csv),
        "input_manifest": str(manifest_path),
        "output_manifest": str(output_manifest),
        "row_count": len(rows),
        "ok_row_count": ok_row_count,
        "parsed_count": parsed_count,
        "error_count": len(errors),
        "mismatch_count": len(mismatches),
        "common_frequency": None if common_spec is None else common_spec.as_manifest(),
        "expected_frequency": None if expected is None else expected.as_manifest(),
        "errors": errors[:20],
        "mismatches": mismatches[:20],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"output_csv={output_csv}")
    print(f"output_manifest={output_manifest}")
    print(f"summary={summary_path}")
    print(f"ok_row_count={ok_row_count}")
    print(f"parsed_count={parsed_count}")
    if reasons:
        print("reasons=" + "; ".join(reasons))
    if status == "FAIL" and not args.no_fail_exit:
        return 2
    return 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing CSV: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_rows(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    for key in FREQUENCY_COLUMNS:
        if key not in seen:
            fieldnames.append(key)
            seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _frequency_spec_from_touchstone(path: Path) -> FrequencySpec:
    result = load_touchstone(path)
    freqs = result.freqs_hz.astype(float)
    if freqs.size == 0:
        raise ValueError(f"No frequencies in {path}")
    if freqs.size == 1:
        return FrequencySpec(start_hz=float(freqs[0]), stop_hz=float(freqs[0]), points=1)
    diffs = np_diff(freqs)
    step_min = float(min(diffs))
    step_max = float(max(diffs))
    return FrequencySpec(
        start_hz=float(freqs[0]),
        stop_hz=float(freqs[-1]),
        points=int(freqs.size),
        step_hz=float(diffs[0]),
        step_min_hz=step_min,
        step_max_hz=step_max,
        step_span_hz=float(step_max - step_min),
    )


def np_diff(values: Any) -> list[float]:
    return [float(values[index + 1] - values[index]) for index in range(len(values) - 1)]


def _expected_frequency(args: argparse.Namespace) -> FrequencySpec | None:
    values = (
        args.expected_frequency_start_ghz,
        args.expected_frequency_stop_ghz,
        args.expected_frequency_step_ghz,
        args.expected_frequency_points,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SystemExit("Expected frequency requires start, stop, step, and points")
    return FrequencySpec(
        start_hz=float(args.expected_frequency_start_ghz) * 1.0e9,
        stop_hz=float(args.expected_frequency_stop_ghz) * 1.0e9,
        step_hz=float(args.expected_frequency_step_ghz) * 1.0e9,
        points=int(args.expected_frequency_points),
    )


def _frequency_mismatch(spec: FrequencySpec, expected: FrequencySpec | None, tolerance_hz: float) -> str | None:
    if expected is None:
        return None
    mismatches = []
    for field in ("start_hz", "stop_hz", "step_hz"):
        actual = getattr(spec, field)
        want = getattr(expected, field)
        if want is not None and (actual is None or abs(float(actual) - float(want)) > tolerance_hz):
            mismatches.append(f"{field}: got={actual}, expected={want}")
    if expected.points is not None and spec.points != expected.points:
        mismatches.append(f"points: got={spec.points}, expected={expected.points}")
    return "; ".join(mismatches) if mismatches else None


def _common_spec(specs: list[FrequencySpec], *, tolerance_hz: float) -> FrequencySpec | None:
    if not specs:
        return None
    first = specs[0]
    for spec in specs[1:]:
        if _frequency_mismatch(spec, first, tolerance_hz):
            return None
    return first


def _summary_numbers(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "std": None}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "std": math.sqrt(variance),
    }


def _resolve_touchstone_path(dataset_dir: Path, path_text: str) -> Path | None:
    value = path_text.strip()
    if not value or value == "None":
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return dataset_dir / path


def _backup_once(path: Path) -> None:
    if not path.exists():
        return
    backup = path.with_suffix(path.suffix + ".bak_before_frequency_backfill")
    if not backup.exists():
        shutil.copy2(path, backup)


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.17g}"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


if __name__ == "__main__":
    raise SystemExit(main())
