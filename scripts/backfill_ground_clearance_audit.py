#!/usr/bin/env python3
"""Backfill dataset-level signal-to-ground shield clearance evidence.

This script is intended for older MARS runs that already have `dataset_rows.csv`
or per-evaluation `summary.json` files but were generated before
`sample-dataset` wrote `final500_ground_clearance_audit.json` automatically. It
does not run EMX. It re-exports layouts with the current geometry checker and
aggregates the per-layout `signal_shield_clearance_audit.json` sidecars.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from rfic_transformer_inverse_design.api import TransformerEmxEvaluator, load_run_config
from rfic_transformer_inverse_design.core.adapter import TransformerOptimizationAdapter
from rfic_transformer_inverse_design.core.topology import TransformerSpec
from rfic_transformer_inverse_design.dataset import GROUND_CLEARANCE_AUDIT_FILENAME, write_ground_clearance_audit
from rfic_transformer_inverse_design.execution.serialization import _json_default


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    run_config = load_run_config(path=args.config)
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else dataset_dir / "ground_clearance_reexport"
    out_path = Path(args.out).expanduser().resolve() if args.out else dataset_dir / GROUND_CLEARANCE_AUDIT_FILENAME
    records = _load_geometry_records(dataset_dir, run_config=run_config, limit=args.limit)
    if args.expected_count is not None and len(records) != int(args.expected_count):
        raise SystemExit(f"Expected {args.expected_count} geometry records, found {len(records)}")

    evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=work_dir)
    results = [evaluator.export_only(geometry) for _source_key, geometry in records]
    audit = write_ground_clearance_audit(results, out_path)
    summary = {
        "dataset_dir": str(dataset_dir),
        "work_dir": str(work_dir),
        "out": str(out_path),
        "source_record_count": len(records),
        "candidate_count": audit.get("candidate_count"),
        "pass_count": audit.get("pass_count"),
        "reject_count": audit.get("reject_count"),
        "missing_or_other_count": audit.get("missing_or_other_count"),
        "selected_cache_key": (audit.get("selected") or {}).get("cache_key") if isinstance(audit.get("selected"), dict) else None,
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")

    print(f"clearance_audit={out_path}")
    print(f"summary={summary_path}")
    print(f"candidate_count={summary['candidate_count']}")
    print(f"pass_count={summary['pass_count']}")
    print(f"reject_count={summary['reject_count']}")
    print(f"missing_or_other_count={summary['missing_or_other_count']}")
    if int(audit.get("missing_or_other_count") or 0) > 0 and not args.no_fail_exit:
        return 2
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Run directory containing dataset_rows.csv or evaluations/*/summary.json")
    parser.add_argument("--config", help="Run config used for the original dataset")
    parser.add_argument("--out", help=f"Output audit path; defaults to <dataset_dir>/{GROUND_CLEARANCE_AUDIT_FILENAME}")
    parser.add_argument("--work-dir", help="Re-export work directory; defaults to <dataset_dir>/ground_clearance_reexport")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--limit", type=int, help="Debug limit for quick smoke tests")
    parser.add_argument("--no-fail-exit", action="store_true", help="Return 0 even if some records lack clearance evidence")
    return parser.parse_args(argv)


def _load_geometry_records(
    dataset_dir: Path,
    *,
    run_config,
    limit: int | None,
) -> list[tuple[str, TransformerSpec]]:
    summary_records = _load_geometry_records_from_summaries(dataset_dir, run_config=run_config)
    records = summary_records if summary_records else _load_geometry_records_from_rows(dataset_dir, run_config=run_config)
    if limit is not None:
        return records[: int(limit)]
    return records


def _load_geometry_records_from_summaries(dataset_dir: Path, *, run_config) -> list[tuple[str, TransformerSpec]]:
    adapter = TransformerOptimizationAdapter(run_config.bounds)
    records: list[tuple[str, TransformerSpec]] = []
    for path in sorted((dataset_dir / "evaluations").glob("*/summary.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        flat = payload.get("geometry")
        if not isinstance(flat, dict):
            continue
        records.append((str(payload.get("cache_key") or path.parent.name), _geometry_from_flat(flat, adapter=adapter)))
    return records


def _load_geometry_records_from_rows(dataset_dir: Path, *, run_config) -> list[tuple[str, TransformerSpec]]:
    rows_path = dataset_dir / "dataset_rows.csv"
    if not rows_path.exists():
        raise SystemExit(f"No evaluations/*/summary.json or dataset_rows.csv found under {dataset_dir}")
    adapter = TransformerOptimizationAdapter(run_config.bounds)
    records: list[tuple[str, TransformerSpec]] = []
    with rows_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            flat = {key.removeprefix("geom__"): _parse_csv_scalar(value) for key, value in row.items() if key.startswith("geom__")}
            if not flat:
                continue
            source_key = row.get("evaluation") or row.get("cache_key") or row.get("sample_id") or f"row_{index:06d}"
            records.append((str(source_key), _geometry_from_flat(flat, adapter=adapter)))
    return records


def _geometry_from_flat(flat: dict[str, object], *, adapter: TransformerOptimizationAdapter) -> TransformerSpec:
    vector = []
    missing: list[str] = []
    for name in adapter.field_order():
        value = flat.get(name)
        if value is None:
            missing.append(name)
            continue
        vector.append(float(value))
    if missing:
        raise SystemExit(f"Geometry record is missing active fields: {missing}")
    return adapter.from_vector(vector)


def _parse_csv_scalar(value: object) -> object:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return float(text)
    except ValueError:
        return text


if __name__ == "__main__":
    raise SystemExit(main())
