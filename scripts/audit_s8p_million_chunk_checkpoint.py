#!/usr/bin/env python3
"""Audit one 100k S8P million-campaign checkpoint.

This checkpoint is meant to run after each production chunk.  It verifies that
the chunk produced usable EMX data, physical-feature labels, inverse-training
evidence, and NN architecture-search evidence before the next chunk is trusted.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    paths = _artifact_paths(args)
    artifacts = {name: _read_json(path) for name, path in paths.items()}
    checks = _checks(paths, artifacts, args)
    overall_status = _overall_status(paths, checks)
    decision = _decision(overall_status)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "chunk_index": int(args.chunk_index),
        "expected_sample_count": int(args.expected_sample_count),
        "out_dir": str(out_dir),
        "artifact_paths": {name: str(path) for name, path in paths.items()},
        "artifact_statuses": _artifact_statuses(artifacts),
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "PASS means this 100k chunk has local EMX/dataset/ML checkpoint evidence.",
            "It does not replace periodic HFSS correlation validation of sampled generated designs.",
            "WAITING means one or more expected artifacts have not been produced yet.",
        ],
    }
    summary_path = out_dir / "s8p_million_chunk_checkpoint_summary.json"
    report_path = out_dir / "S8P_MILLION_CHUNK_CHECKPOINT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-index", type=int, required=True)
    parser.add_argument("--expected-sample-count", type=int, default=100_000)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--quality-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--nn-architecture-dir", required=True)
    parser.add_argument("--nn-training-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-training-rows", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _artifact_paths(args: argparse.Namespace) -> dict[str, Path]:
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    quality_dir = Path(args.quality_dir).expanduser().resolve()
    model_dir = Path(args.model_dir).expanduser().resolve()
    audit_dir = Path(args.audit_dir).expanduser().resolve()
    nn_architecture_dir = Path(args.nn_architecture_dir).expanduser().resolve()
    nn_training_dir = Path(args.nn_training_dir).expanduser().resolve()
    return {
        "dataset_manifest": dataset_dir / "dataset_manifest.json",
        "dataset_quality": quality_dir / "dataset_quality_gates_summary.json",
        "inverse_training": quality_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
        "baseline_model": model_dir / "physical_feature_inverse_model_training_summary.json",
        "baseline_model_audit": audit_dir / "physical_feature_inverse_model_quality_summary.json",
        "nn_architecture_plan": nn_architecture_dir / "physical_feature_inverse_nn_architecture_search_summary.json",
        "nn_architecture_train": nn_training_dir / "physical_feature_inverse_nn_architecture_search_training_summary.json",
    }


def _checks(paths: dict[str, Path], artifacts: dict[str, dict[str, Any]], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    expected = int(args.expected_sample_count)
    min_training_rows = int(args.min_training_rows or expected)
    for name, path in paths.items():
        checks.append(_check(f"{name} summary exists", path.is_file(), str(path)))
        if path.is_file():
            checks.append(_check(f"{name} summary parses", "_parse_error" not in artifacts[name], artifacts[name].get("_parse_error", "JSON object")))

    manifest = artifacts.get("dataset_manifest") or {}
    checks.extend(
        [
            _int_at_least("dataset requested count", manifest.get("requested_count"), expected),
            _int_at_least("dataset ok count", manifest.get("ok_count"), expected),
            _int_equals("dataset fail count", manifest.get("fail_count", 0), 0),
        ]
    )
    checks.append(_status_check("dataset quality gates PASS", artifacts.get("dataset_quality"), expected_status="PASS"))
    inverse = artifacts.get("inverse_training") or {}
    checks.extend(
        [
            _status_check("inverse training table PASS", inverse, expected_status="PASS"),
            _int_at_least("inverse training row count", inverse.get("training_count"), min_training_rows),
        ]
    )
    baseline = artifacts.get("baseline_model") or {}
    checks.extend(
        [
            _status_check("baseline inverse model PASS", baseline, expected_status="PASS"),
            _int_at_least("baseline training row count", baseline.get("training_count"), min_training_rows),
        ]
    )
    audit = artifacts.get("baseline_model_audit") or {}
    checks.extend(
        [
            _status_check("baseline inverse model audit PASS", audit, expected_status="PASS"),
            _int_at_least("baseline audit training row count", audit.get("training_count"), min_training_rows),
        ]
    )
    nn_plan = artifacts.get("nn_architecture_plan") or {}
    checks.extend(
        [
            _status_check("NN architecture plan PASS", nn_plan, expected_status="PASS"),
            _int_at_least("NN architecture candidate count", nn_plan.get("architecture_candidate_count"), 1),
        ]
    )
    nn_train = artifacts.get("nn_architecture_train") or {}
    checks.extend(
        [
            _status_check("NN architecture training PASS", nn_train, expected_status="PASS"),
            _int_at_least("NN trained candidate count", nn_train.get("trained_candidate_count"), 1),
            _int_at_least("NN training row count", nn_train.get("training_count"), min_training_rows),
        ]
    )
    selected = nn_train.get("selected_candidate") if isinstance(nn_train.get("selected_candidate"), dict) else {}
    checks.append(_check("NN selected candidate present", bool(selected.get("candidate_id")), str(selected.get("candidate_id", ""))))
    return checks


def _overall_status(paths: dict[str, Path], checks: list[Check]) -> str:
    if any(not path.is_file() for path in paths.values()):
        return "WAITING_FOR_CHUNK_ARTIFACTS"
    return "PASS" if all(check.pass_bool for check in checks) else "FAIL"


def _decision(status: str) -> str:
    return {
        "PASS": "ACCEPT_100K_CHUNK_AND_ALLOW_NEXT_CHUNK",
        "WAITING_FOR_CHUNK_ARTIFACTS": "WAIT_FOR_100K_CHUNK_ARTIFACTS",
        "FAIL": "STOP_MILLION_CAMPAIGN_FIX_THIS_100K_CHUNK",
    }[status]


def _artifact_statuses(artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {name: str(data.get("overall_status") or data.get("_parse_error") or "") for name, data in artifacts.items()}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _status_check(name: str, data: dict[str, Any] | None, *, expected_status: str) -> Check:
    actual = "" if not isinstance(data, dict) else str(data.get("overall_status") or "")
    return _check(name, actual == expected_status, f"actual={actual!r}, expected={expected_status!r}")


def _int_equals(name: str, actual: Any, expected: int) -> Check:
    try:
        value = int(actual)
    except (TypeError, ValueError):
        return _check(name, False, f"actual={actual!r}, expected={expected}")
    return _check(name, value == int(expected), f"actual={value}, expected={expected}")


def _int_at_least(name: str, actual: Any, minimum: int) -> Check:
    try:
        value = int(actual)
    except (TypeError, ValueError):
        return _check(name, False, f"actual={actual!r}, minimum={minimum}")
    return _check(name, value >= int(minimum), f"actual={value}, minimum={minimum}")


def _check(name: str, passed: bool, detail: Any) -> Check:
    return Check("PASS" if passed else "FAIL", name, str(detail))


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Million Chunk Checkpoint",
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
