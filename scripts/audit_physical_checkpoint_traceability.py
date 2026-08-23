#!/usr/bin/env python3
"""Audit source traceability for one physical-feature checkpoint.

The checkpoint pipeline already tests whether Lp/Ls/Q/K distribution and the
inverse-model baseline pass. This script verifies the chain that produced those
artifacts:

source .s4p files -> stable Touchstone index -> response features -> geometry
enrichment -> physical-feature uniformity -> inverse-training table -> model
checkpoint.

It is intentionally read-only and avoids hashing every .s4p file, so it can run
after each 100k checkpoint without dominating EMX runtime.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve() if args.dataset_dir else None
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else checkpoint_dir / "physical_checkpoint_traceability"
    out_dir.mkdir(parents=True, exist_ok=True)

    expected = int(args.expected_count)
    min_valid = int(args.min_valid)

    payload = _build_payload(checkpoint_dir, dataset_dir, expected, min_valid, args)
    summary_path = out_dir / "physical_checkpoint_traceability_summary.json"
    report_path = out_dir / "physical_checkpoint_traceability_report.md"
    payload["summary_json"] = str(summary_path)
    payload["report_md"] = str(report_path)
    payload["overall_status"] = "PASS" if all(item["pass"] for item in payload["checks"]) else "FAIL"
    payload["decision"] = "TRACEABLE_CHECKPOINT_EVIDENCE" if payload["overall_status"] == "PASS" else "REPAIR_CHECKPOINT_TRACEABILITY"

    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")

    print(f"overall_status={payload['overall_status']}")
    print(f"decision={payload['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if payload["overall_status"] == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--dataset-dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--min-valid", type=int, required=True)
    parser.add_argument("--max-path-stat-failures", type=int, default=0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _build_payload(
    checkpoint_dir: Path,
    dataset_dir: Path | None,
    expected: int,
    min_valid: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    paths = {
        "stable_summary": checkpoint_dir / "stable_index" / "stable_touchstone_index_summary.json",
        "stable_manifest_csv": checkpoint_dir / "stable_index" / "stable_touchstone_index_manifest.csv",
        "stable_dataset_manifest": checkpoint_dir / "stable_index" / "dataset_manifest.json",
        "response_summary": checkpoint_dir / "response_features" / "response_feature_extraction_summary.json",
        "response_csv": checkpoint_dir / "response_features" / "response_features.csv",
        "response_rows_csv": checkpoint_dir / "response_features" / "dataset_rows.csv",
        "response_dataset_manifest": checkpoint_dir / "response_features" / "dataset_manifest.json",
        "enrichment_manifest": checkpoint_dir / "enriched_geometry" / "geometry_enrichment_manifest.json",
        "enriched_rows_csv": checkpoint_dir / "enriched_geometry" / "dataset_rows.csv",
        "uniformity_summary": checkpoint_dir / "physical_feature_uniformity" / "physical_feature_uniformity_summary.json",
        "uniformity_manifest": checkpoint_dir / "physical_feature_uniformity" / "physical_feature_uniformity_manifest.json",
        "training_manifest": checkpoint_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
        "training_csv": checkpoint_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_table.csv",
        "model_summary": checkpoint_dir / "physical_feature_inverse_checkpoint_test" / "physical_feature_inverse_checkpoint_test_summary.json",
        "pipeline_summary": checkpoint_dir / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
    }

    stable_summary = _read_json(paths["stable_summary"])
    stable_dataset_manifest = _read_json(paths["stable_dataset_manifest"])
    response_summary = _read_json(paths["response_summary"])
    response_dataset_manifest = _read_json(paths["response_dataset_manifest"])
    enrichment_manifest = _read_json(paths["enrichment_manifest"])
    uniformity_summary = _read_json(paths["uniformity_summary"])
    uniformity_manifest = _read_json(paths["uniformity_manifest"])
    training_manifest = _read_json(paths["training_manifest"])
    model_summary = _read_json(paths["model_summary"])
    pipeline_summary = _read_json(paths["pipeline_summary"])

    stable_rows = _read_csv(paths["stable_manifest_csv"])
    response_rows = _read_csv(paths["response_csv"])
    response_dataset_rows = _read_csv(paths["response_rows_csv"])
    enriched_rows = _read_csv(paths["enriched_rows_csv"])
    training_rows = _read_csv(paths["training_csv"])

    stable_path_stats = _stable_path_stats(stable_rows)
    evaluation_sets = {
        "stable": _evaluation_set(stable_rows),
        "response": _evaluation_set(response_rows),
        "response_rows": _evaluation_set(response_dataset_rows),
        "enriched": _evaluation_set(enriched_rows),
        "training": _evaluation_set(training_rows),
    }
    overlaps = _evaluation_overlaps(evaluation_sets)
    row_counts = {
        "stable_manifest_rows": len(stable_rows),
        "stable_unique_evaluations": len(evaluation_sets["stable"]),
        "response_feature_rows": len(response_rows),
        "response_unique_evaluations": len(evaluation_sets["response"]),
        "response_dataset_rows": len(response_dataset_rows),
        "response_dataset_unique_evaluations": len(evaluation_sets["response_rows"]),
        "enriched_rows": len(enriched_rows),
        "enriched_unique_evaluations": len(evaluation_sets["enriched"]),
        "training_rows": len(training_rows),
        "training_unique_evaluations": len(evaluation_sets["training"]),
        "stable_source_path_exists": stable_path_stats["source_path_exists"],
        "stable_source_path_nonempty": stable_path_stats["source_path_nonempty"],
        "stable_indexed_path_exists": stable_path_stats["indexed_path_exists"],
        "stable_indexed_path_nonempty": stable_path_stats["indexed_path_nonempty"],
        "stable_numeric_rows_positive": stable_path_stats["numeric_rows_positive"],
    }

    source_dataset_dir = stable_summary.get("dataset_dir") or stable_dataset_manifest.get("source_dataset_dir")
    response_source_dataset_dir = response_summary.get("dataset_dir") or response_dataset_manifest.get("source_dataset_dir")

    checks = [
        _check("checkpoint_dir_exists", checkpoint_dir.is_dir(), str(checkpoint_dir)),
        _check("stable_summary_pass", stable_summary.get("status") == "PASS", stable_summary.get("status")),
        _check("stable_indexed_count", _as_int(stable_summary.get("indexed_count")) >= expected, stable_summary.get("indexed_count")),
        _check("stable_manifest_rows", len(stable_rows) >= expected, len(stable_rows)),
        _check("stable_unique_evaluations", len(evaluation_sets["stable"]) >= expected, len(evaluation_sets["stable"])),
        _check("response_unique_evaluations", len(evaluation_sets["response"]) >= expected, len(evaluation_sets["response"])),
        _check("response_dataset_unique_evaluations", len(evaluation_sets["response_rows"]) >= expected, len(evaluation_sets["response_rows"])),
        _check("enriched_unique_evaluations", len(evaluation_sets["enriched"]) >= expected, len(evaluation_sets["enriched"])),
        _check("training_unique_evaluations", len(evaluation_sets["training"]) >= min_valid, len(evaluation_sets["training"])),
        _check("stable_source_paths_exist", stable_path_stats["source_path_missing"] <= int(args.max_path_stat_failures), stable_path_stats),
        _check("stable_indexed_paths_exist", stable_path_stats["indexed_path_missing"] <= int(args.max_path_stat_failures), stable_path_stats),
        _check("stable_numeric_rows_positive", stable_path_stats["numeric_rows_nonpositive"] <= int(args.max_path_stat_failures), stable_path_stats),
        _check("response_summary_pass", response_summary.get("overall_status") == "PASS", response_summary.get("overall_status")),
        _check("response_ok_rows", _dig_int(response_summary, "counts", "ok_rows") >= expected, _dig(response_summary, "counts", "ok_rows")),
        _check("response_feature_rows", len(response_rows) >= expected, len(response_rows)),
        _check("enrichment_manifest_pass", enrichment_manifest.get("overall_status") == "PASS", enrichment_manifest.get("overall_status")),
        _check("enriched_row_count", _as_int(enrichment_manifest.get("enriched_row_count")) >= expected, enrichment_manifest.get("enriched_row_count")),
        _check("uniformity_summary_pass", uniformity_summary.get("overall_status") == "PASS", uniformity_summary.get("overall_status")),
        _check("uniformity_valid_count", _as_int(uniformity_summary.get("valid_feature_count")) >= min_valid, uniformity_summary.get("valid_feature_count")),
        _check("uniformity_manifest_pass", uniformity_manifest.get("overall_status") == "PASS", uniformity_manifest.get("overall_status")),
        _check("uniformity_visual_artifacts", _as_int(uniformity_manifest.get("visual_artifact_count")) >= 3, uniformity_manifest.get("visual_artifact_count")),
        _check("training_manifest_pass", training_manifest.get("overall_status") == "PASS", training_manifest.get("overall_status")),
        _check("training_count", _as_int(training_manifest.get("training_count")) >= min_valid, training_manifest.get("training_count")),
        _check("training_source_sha_present", bool(_dig(training_manifest, "dataset_source", "sha256")), _dig(training_manifest, "dataset_source", "sha256")),
        _check("model_summary_pass", model_summary.get("overall_status") == "PASS", model_summary.get("overall_status")),
        _check("model_usable_rows", _as_int(model_summary.get("usable_row_count")) >= min_valid, model_summary.get("usable_row_count")),
        _check("pipeline_summary_pass", pipeline_summary.get("overall_status") in {None, "PASS"}, pipeline_summary.get("overall_status")),
        _check("stable_to_response_evaluations", overlaps["stable_vs_response_missing"] == 0, overlaps),
        _check("response_to_enriched_evaluations", overlaps["response_vs_enriched_missing"] == 0, overlaps),
        _check("enriched_to_training_evaluations", overlaps["enriched_vs_training_missing"] == 0, overlaps),
    ]
    if dataset_dir is not None:
        checks.append(_check("stable_source_dataset_matches", _same_path(source_dataset_dir, dataset_dir), source_dataset_dir))
        checks.append(_check("response_source_dataset_matches_stable", _same_path(response_source_dataset_dir, paths["stable_summary"].parent), response_source_dataset_dir))

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checkpoint_dir": str(checkpoint_dir),
        "dataset_dir": str(dataset_dir) if dataset_dir else None,
        "expected_count": expected,
        "min_valid": min_valid,
        "paths": {key: str(value) for key, value in paths.items()},
        "row_counts": row_counts,
        "evaluation_overlaps": overlaps,
        "stable_path_stats": stable_path_stats,
        "source_dataset_dir": source_dataset_dir,
        "response_source_dataset_dir": response_source_dataset_dir,
        "training_dataset_source": training_manifest.get("dataset_source"),
        "checks": checks,
        "limitations": [
            "This audit avoids hashing every Touchstone file to keep each 100k checkpoint fast.",
            "It verifies manifest/path/count/evaluation consistency for the checkpoint artifacts.",
            "It does not run EMX, HFSS, ADS, or retrain the final neural model.",
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"_missing": True, "_path": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": type(exc).__name__, "_path": str(path)}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _stable_path_stats(rows: list[dict[str, str]]) -> dict[str, int]:
    stats = {
        "rows": len(rows),
        "source_path_exists": 0,
        "source_path_missing": 0,
        "source_path_nonempty": 0,
        "indexed_path_exists": 0,
        "indexed_path_missing": 0,
        "indexed_path_nonempty": 0,
        "numeric_rows_positive": 0,
        "numeric_rows_nonpositive": 0,
    }
    for row in rows:
        for key, exists_key, missing_key, nonempty_key in (
            ("source_path", "source_path_exists", "source_path_missing", "source_path_nonempty"),
            ("indexed_path", "indexed_path_exists", "indexed_path_missing", "indexed_path_nonempty"),
        ):
            path = Path(str(row.get(key) or "")).expanduser()
            if path.is_file():
                stats[exists_key] += 1
                try:
                    if path.stat().st_size > 0:
                        stats[nonempty_key] += 1
                except OSError:
                    pass
            else:
                stats[missing_key] += 1
        if _as_int(row.get("numeric_rows_detected")) > 0:
            stats["numeric_rows_positive"] += 1
        else:
            stats["numeric_rows_nonpositive"] += 1
    return stats


def _evaluation_set(rows: list[dict[str, str]]) -> set[str]:
    values = set()
    for idx, row in enumerate(rows):
        value = row.get("evaluation") or row.get("sample_id") or row.get("id") or row.get("row_index") or f"row_{idx}"
        if value not in (None, ""):
            values.add(str(value))
    return values


def _evaluation_overlaps(sets: dict[str, set[str]]) -> dict[str, int]:
    def missing(left: str, right: str) -> int:
        if not sets[left] or not sets[right]:
            return 0
        return len(sets[left].difference(sets[right]))

    return {
        "stable_vs_response_missing": missing("stable", "response"),
        "response_vs_stable_missing": missing("response", "stable"),
        "response_vs_enriched_missing": missing("response", "enriched"),
        "enriched_vs_response_missing": missing("enriched", "response"),
        "enriched_vs_training_missing": missing("enriched", "training"),
        "training_vs_enriched_missing": missing("training", "enriched"),
    }


def _same_path(raw: Any, expected: Path) -> bool:
    if not raw:
        return False
    try:
        return Path(str(raw)).expanduser().resolve() == expected.expanduser().resolve()
    except OSError:
        return str(raw) == str(expected)


def _dig(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _dig_int(data: dict[str, Any], *keys: str) -> int:
    return _as_int(_dig(data, *keys))


def _as_int(value: Any) -> int:
    try:
        if value is None or str(value).strip() == "":
            return -1
        number = float(value)
        if not math.isfinite(number):
            return -1
        return int(number)
    except Exception:
        return -1


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Physical Checkpoint Traceability Audit",
        "",
        f"Status: **{payload['overall_status']}**",
        f"Decision: **{payload['decision']}**",
        f"Checkpoint directory: `{payload['checkpoint_dir']}`",
        f"Expected count: `{payload['expected_count']}`",
        f"Minimum valid rows: `{payload['min_valid']}`",
        "",
        "## Row Counts",
        "",
    ]
    for key, value in payload["row_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Checks", "", "| Check | Pass | Detail |", "| --- | --- | --- |"])
    for check in payload["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
