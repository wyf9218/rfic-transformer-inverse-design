#!/usr/bin/env python3
"""Audit one 100k checkpoint of the MARS56 grounded S4P million campaign.

The current grounded-S4P production checkpoint is:

stable Touchstone index -> response features -> geometry enrichment ->
Lp/Ls/Q/|K| uniformity -> inverse-training table -> model checkpoint.

Older S8P-era flows also wrote a ``scalar_q_feature_dataset`` directory.  This
audit intentionally follows the current S4P checkpoint pipeline instead, so a
valid S4P chunk is not rejected for missing obsolete artifacts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANONICAL_GEOMETRY_FIELDS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)
GEOMETRY_FINGERPRINT_SCHEMA = "mars56_grounded_s4p_geometry_v1"
GEOMETRY_FINGERPRINT_QUANTIZATION_UM = 1.0e-6


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
    paths = _paths(args)
    artifacts = {name: _read_json(path) for name, path in paths.items()}
    checks = _checks(paths, artifacts, args)
    overall_status = "PASS" if all(check.pass_bool for check in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "ACCEPT_100K_S4P_CHUNK_AND_CONTINUE" if overall_status == "PASS" else "STOP_OR_REPAIR_THIS_100K_S4P_CHUNK",
        "chunk_index": int(args.chunk_index),
        "expected_sample_count": int(args.expected_sample_count),
        "out_dir": str(out_dir),
        "artifact_paths": {name: str(path) for name, path in paths.items()},
        "artifact_statuses": _artifact_statuses(artifacts),
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This checkpoint verifies local EMX S4P generation, response extraction, inverse-training table, and scalable model test evidence.",
            "It does not replace periodic HFSS correlation validation of sampled designs.",
            "Model quality warnings are preserved in the model-test summary even when this checkpoint accepts the chunk.",
        ],
    }
    summary_path = out_dir / "mars56_s4p_million_chunk_checkpoint_summary.json"
    report_path = out_dir / "MARS56_S4P_MILLION_CHUNK_CHECKPOINT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-index", type=int, required=True)
    parser.add_argument("--expected-sample-count", type=int, default=100_000)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--quality-dir", help="Legacy quality directory; use --checkpoint-dir for the current S4P flow")
    parser.add_argument("--model-test-dir", help="Legacy model-test directory; use --checkpoint-dir for the current S4P flow")
    parser.add_argument("--checkpoint-dir", help="Directory produced by run_mars56_s4p_physical_checkpoint_pipeline.sh")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-training-rows", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    candidate_dir = Path(args.candidate_dir).expanduser().resolve()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve() if args.checkpoint_dir else None
    quality_dir = (
        checkpoint_dir
        if checkpoint_dir is not None
        else Path(args.quality_dir or "").expanduser().resolve()
    )
    model_test_dir = (
        checkpoint_dir / "physical_feature_inverse_checkpoint_test"
        if checkpoint_dir is not None
        else Path(args.model_test_dir or "").expanduser().resolve()
    )
    return {
        "candidate_queue": candidate_dir / "mars56_grounded_s4p_candidate_queue_summary.json",
        "parallel_dataset": dataset_dir / "parallel_candidate_queue_dataset_summary.json",
        "checkpoint_pipeline": quality_dir / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
        "response_features": quality_dir / "response_features" / "response_feature_extraction_summary.json",
        "geometry_enrichment": quality_dir / "enriched_geometry" / "geometry_enrichment_manifest.json",
        "uniformity": quality_dir / "physical_feature_uniformity" / "physical_feature_uniformity_summary.json",
        "uniformity_manifest": quality_dir / "physical_feature_uniformity" / "physical_feature_uniformity_manifest.json",
        "inverse_training": quality_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
        "model_checkpoint_test": model_test_dir / "physical_feature_inverse_checkpoint_test_summary.json",
    }


def _checks(paths: dict[str, Path], artifacts: dict[str, dict[str, Any]], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    expected = int(args.expected_sample_count)
    min_training = int(args.min_training_rows or expected)
    for name, path in paths.items():
        checks.append(_check(f"{name} summary exists", path.is_file(), str(path)))
        if path.is_file():
            checks.append(_check(f"{name} summary parses", "_parse_error" not in artifacts[name], artifacts[name].get("_parse_error", "JSON object")))

    candidate = artifacts.get("candidate_queue") or {}
    identity = candidate.get("identity_audit") if isinstance(candidate.get("identity_audit"), dict) else {}
    checks.extend(
        [
            _status_check("candidate queue PASS", candidate),
            _int_equals("candidate sample count", candidate.get("sample_count"), expected),
            _check(
                "candidate requires unique canonical geometry",
                candidate.get("require_unique_geometry") is True,
                candidate.get("require_unique_geometry"),
            ),
            _check(
                "candidate requires unique source ID",
                candidate.get("require_unique_source_id") is True,
                candidate.get("require_unique_source_id"),
            ),
            _check(
                "candidate geometry fingerprint schema",
                candidate.get("geometry_fingerprint_schema") == GEOMETRY_FINGERPRINT_SCHEMA,
                candidate.get("geometry_fingerprint_schema"),
            ),
            _float_equals(
                "candidate geometry fingerprint quantization",
                candidate.get("geometry_fingerprint_quantization_um"),
                GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
            ),
            _check(
                "candidate canonical geometry field order",
                candidate.get("canonical_geometry_fields") == list(CANONICAL_GEOMETRY_FIELDS),
                candidate.get("canonical_geometry_fields"),
            ),
            _int_equals("candidate identity row count", identity.get("row_count"), expected),
            _int_equals(
                "candidate unique canonical geometry count",
                identity.get("unique_geometry_fingerprint_count"),
                expected,
            ),
            _int_equals(
                "candidate duplicate canonical geometry extra rows",
                identity.get("duplicate_geometry_extra_row_count"),
                0,
            ),
            _int_equals(
                "candidate duplicate canonical geometry groups",
                identity.get("duplicate_geometry_group_count"),
                0,
            ),
            _int_equals(
                "candidate missing canonical geometry fingerprints",
                identity.get("missing_geometry_fingerprint_count"),
                0,
            ),
            _int_equals(
                "candidate unique source ID count",
                identity.get("unique_source_candidate_id_count"),
                expected,
            ),
            _int_equals(
                "candidate missing source IDs",
                identity.get("missing_source_candidate_id_count"),
                0,
            ),
            _int_equals(
                "candidate duplicate source ID extra rows",
                identity.get("duplicate_source_candidate_id_extra_row_count"),
                0,
            ),
            _int_equals(
                "candidate duplicate source ID groups",
                identity.get("duplicate_source_candidate_id_group_count"),
                0,
            ),
        ]
    )

    parallel = artifacts.get("parallel_dataset") or {}
    touchstone = parallel.get("touchstone_output_contract") if isinstance(parallel.get("touchstone_output_contract"), dict) else {}
    checks.extend(
        [
            _status_check("parallel dataset PASS", parallel),
            _int_equals("merged row count", parallel.get("merged_row_count"), expected),
            _int_equals("pass shard count equals shard count", parallel.get("pass_shard_count"), parallel.get("shard_count")),
            _int_equals("touchstone ok row count", touchstone.get("ok_row_count"), expected),
            _int_equals("touchstone nonzero file count", touchstone.get("nonzero_file_count"), expected),
            _int_equals("touchstone extension match count", touchstone.get("extension_match_count"), expected),
            _int_equals("touchstone port error count", touchstone.get("port_error_count"), 0),
            _int_equals("touchstone frequency error count", touchstone.get("frequency_error_count"), 0),
        ]
    )

    checkpoint = artifacts.get("checkpoint_pipeline") or {}
    checks.append(_status_check("checkpoint pipeline PASS", checkpoint))

    response = artifacts.get("response_features") or {}
    response_counts = response.get("counts") if isinstance(response.get("counts"), dict) else {}
    checks.extend(
        [
            _status_check("response extraction PASS", response),
            _int_equals("response candidate count", response_counts.get("touchstone_candidates"), expected),
            _int_equals("response ok count", response_counts.get("ok_rows"), expected),
        ]
    )

    enrichment = artifacts.get("geometry_enrichment") or {}
    checks.extend(
        [
            _status_check("geometry enrichment PASS", enrichment),
            _int_equals("enrichment input row count", enrichment.get("input_row_count"), expected),
            _int_equals("enrichment output row count", enrichment.get("enriched_row_count"), expected),
        ]
    )

    uniformity = artifacts.get("uniformity") or {}
    checks.extend(
        [
            _status_check("physical feature uniformity PASS", uniformity),
            _int_equals("uniformity row count", uniformity.get("row_count"), expected),
            _int_at_least("uniformity valid physical rows", uniformity.get("valid_feature_count"), min_training),
        ]
    )
    uniformity_manifest = artifacts.get("uniformity_manifest") or {}
    checks.extend(
        [
            _status_check("physical feature uniformity artifact manifest PASS", uniformity_manifest),
            _int_at_least("uniformity visual artifact count", uniformity_manifest.get("visual_artifact_count"), 3),
        ]
    )

    inverse = artifacts.get("inverse_training") or {}
    checks.extend(
        [
            _status_check("inverse training table PASS", inverse),
            _int_at_least("inverse training row count", inverse.get("training_count"), min_training),
        ]
    )

    model = artifacts.get("model_checkpoint_test") or {}
    checks.extend(
        [
            _status_check("model checkpoint test executed", model),
            _int_at_least("model usable row count", model.get("usable_row_count"), min_training),
            _int_at_least("model holdout test row count", model.get("test_row_count"), 1),
        ]
    )
    return checks


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _artifact_statuses(artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {name: str(data.get("overall_status") or data.get("_parse_error") or "") for name, data in artifacts.items()}


def _status_check(name: str, data: dict[str, Any] | None, expected: str = "PASS") -> Check:
    actual = "" if not isinstance(data, dict) else str(data.get("overall_status") or "")
    return _check(name, actual == expected, f"actual={actual!r}, expected={expected!r}")


def _int_equals(name: str, actual: Any, expected: Any) -> Check:
    try:
        actual_int = int(actual)
        expected_int = int(expected)
    except (TypeError, ValueError):
        return _check(name, False, f"actual={actual!r}, expected={expected!r}")
    return _check(name, actual_int == expected_int, f"actual={actual_int}, expected={expected_int}")


def _int_at_least(name: str, actual: Any, minimum: int) -> Check:
    try:
        value = int(actual)
    except (TypeError, ValueError):
        return _check(name, False, f"actual={actual!r}, minimum={minimum}")
    return _check(name, value >= int(minimum), f"actual={value}, minimum={minimum}")


def _float_equals(name: str, actual: Any, expected: float) -> Check:
    try:
        actual_float = float(actual)
    except (TypeError, ValueError):
        return _check(name, False, f"actual={actual!r}, expected={expected!r}")
    return _check(
        name,
        actual_float == float(expected),
        f"actual={actual_float!r}, expected={float(expected)!r}",
    )


def _check(name: str, passed: bool, detail: Any) -> Check:
    return Check("PASS" if passed else "FAIL", name, str(detail))


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 S4P Million Chunk Checkpoint",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Chunk index: `{summary['chunk_index']}`",
        f"- Expected samples: `{summary['expected_sample_count']}`",
        "",
        "## Artifact Statuses",
        "",
    ]
    for name, status in summary["artifact_statuses"].items():
        lines.append(f"- `{name}`: `{status}`")
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {check['status']}: {check['name']} - {check['detail']}" for check in summary["checks"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
