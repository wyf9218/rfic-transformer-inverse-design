#!/usr/bin/env python3
"""Audit MARS56 1M evidence against the production plan contract."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    args = _parse_args()
    contract_path = Path(args.contract_json).expanduser().resolve()
    evidence_path = Path(args.evidence_index_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    contract = _read_json(contract_path)
    evidence = _read_json(evidence_path)
    checks: list[dict[str, Any]] = []

    checks.append(_check("contract_json_exists", contract_path.is_file(), str(contract_path)))
    checks.append(_check("evidence_index_json_exists", evidence_path.is_file(), str(evidence_path)))
    checks.append(_check("contract_name_valid", contract.get("contract_name") == "mars56_s4p_1m_physical_feature_uniformity_contract", contract.get("contract_name")))

    expected_chunks = _as_int(contract.get("expected_chunks"))
    expected_per_chunk = _as_int(contract.get("expected_per_chunk"))
    expected_total = _as_int(contract.get("expected_total_rows"))
    if expected_chunks is None:
        expected_chunks = 10
    if expected_per_chunk is None:
        expected_per_chunk = 100000
    if expected_total is None:
        expected_total = expected_chunks * expected_per_chunk

    checks.append(_check("expected_chunks_positive", expected_chunks > 0, expected_chunks))
    checks.append(_check("expected_per_chunk_positive", expected_per_chunk > 0, expected_per_chunk))
    checks.append(_check("expected_total_matches_product", expected_total == expected_chunks * expected_per_chunk, expected_total))

    checkpoint_contract = (
        contract.get("physical_feature_checkpoint_contract")
        if isinstance(contract.get("physical_feature_checkpoint_contract"), dict)
        else {}
    )
    strict_uniformity_thresholds = {
        "min_four_d_occupied_fraction": (0.50, "min"),
        "min_four_d_normalized_entropy": (0.80, "min"),
        "max_four_d_nonzero_bin_imbalance": (4.0, "max"),
    }
    for name, (limit, direction) in strict_uniformity_thresholds.items():
        value = _as_float(checkpoint_contract.get(name))
        if direction == "min":
            passed = value is not None and value >= limit
        else:
            passed = value is not None and value <= limit
        checks.append(
            _check(
                f"checkpoint_contract_{name}_not_weakened",
                passed,
                {"observed": value, "required": limit, "direction": direction},
            )
        )
    checks.append(
        _check(
            "checkpoint_contract_requires_plots",
            checkpoint_contract.get("require_plots") is True,
            checkpoint_contract.get("require_plots"),
        )
    )

    formal_evidence = evidence.get("formal_100k") if isinstance(evidence.get("formal_100k"), list) else []
    cumulative_evidence = evidence.get("cumulative") if isinstance(evidence.get("cumulative"), list) else []
    formal_by_tag = {str(item.get("tag")): item for item in formal_evidence if isinstance(item, dict)}
    cumulative_by_expected = {_as_int(item.get("expected_count")): item for item in cumulative_evidence if isinstance(item, dict)}

    formal_results = []
    for chunk in contract.get("formal_100k_chunks", []):
        if not isinstance(chunk, dict):
            continue
        tag = str(chunk.get("tag"))
        required_rows = _as_int(chunk.get("expected_rows")) or expected_per_chunk
        item = formal_by_tag.get(tag)
        result = _audit_formal_chunk(tag, required_rows, item)
        formal_results.append(result)
        checks.append(_check(f"formal_chunk_{tag}", result["status"] == "PASS", result))

    cumulative_results = []
    for checkpoint in contract.get("cumulative_checkpoints", []):
        if not isinstance(checkpoint, dict):
            continue
        tag = str(checkpoint.get("tag"))
        required_rows = _as_int(checkpoint.get("expected_rows"))
        item = cumulative_by_expected.get(required_rows)
        result = _audit_cumulative_checkpoint(tag, required_rows, item)
        cumulative_results.append(result)
        checks.append(_check(f"cumulative_checkpoint_{tag}", result["status"] == "PASS", result))

    formal_pass_count = sum(1 for item in formal_results if item["status"] == "PASS")
    cumulative_pass_count = sum(1 for item in cumulative_results if item["status"] == "PASS")
    total_nonempty = sum(int(item.get("nonempty_s4p_count") or 0) for item in formal_results)

    checks.extend(
        [
            _check("formal_pass_count_meets_contract", formal_pass_count >= expected_chunks, f"{formal_pass_count}/{expected_chunks}"),
            _check("cumulative_pass_count_meets_contract", cumulative_pass_count >= expected_chunks, f"{cumulative_pass_count}/{expected_chunks}"),
            _check("total_nonempty_s4p_meets_contract", total_nonempty >= expected_total, f"{total_nonempty}/{expected_total}"),
        ]
    )

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "ONE_MILLION_PLAN_CONTRACT_EVIDENCE_PASS" if status == "PASS" else "ONE_MILLION_PLAN_CONTRACT_EVIDENCE_INCOMPLETE",
        "contract_json": str(contract_path),
        "evidence_index_json": str(evidence_path),
        "expected_chunks": expected_chunks,
        "expected_per_chunk": expected_per_chunk,
        "expected_total_rows": expected_total,
        "formal_pass_count": formal_pass_count,
        "cumulative_pass_count": cumulative_pass_count,
        "total_nonempty_s4p": total_nonempty,
        "formal_results": formal_results,
        "cumulative_results": cumulative_results,
        "checks": checks,
    }
    summary_path = out_dir / "mars56_1m_production_plan_contract_audit_summary.json"
    report_path = out_dir / "mars56_1m_production_plan_contract_audit_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"summary={summary_path}")
    print(f"overall_status={status}")
    print(f"PRODUCTION_PLAN_CONTRACT_AUDIT_STATUS={status}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-json", required=True)
    parser.add_argument("--evidence-index-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_parse_error": "JSONDecodeError"}


def _audit_formal_chunk(tag: str, required_rows: int, item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {"tag": tag, "status": "FAIL", "reasons": ["missing_formal_chunk_in_evidence"], "nonempty_s4p_count": 0}
    reasons: list[str] = []
    nonempty = _as_int(item.get("nonempty_s4p_count")) or 0
    if nonempty < required_rows:
        reasons.append(f"nonempty_s4p_count={nonempty}")
    if item.get("dataset_summary_status") != "PASS":
        reasons.append(f"dataset_summary_status={item.get('dataset_summary_status')!r}")
    if item.get("checkpoint_proof") != "PASS":
        reasons.append(f"checkpoint_proof={item.get('checkpoint_proof')!r}")
    if item.get("evidence_status") != "PASS":
        reasons.append(f"evidence_status={item.get('evidence_status')!r}")
    missing_artifacts = item.get("missing_required_artifacts")
    if missing_artifacts:
        reasons.append(f"missing_required_artifacts={missing_artifacts}")
    return {
        "tag": tag,
        "status": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
        "nonempty_s4p_count": nonempty,
        "dataset_summary_status": item.get("dataset_summary_status"),
        "checkpoint_proof": item.get("checkpoint_proof"),
        "evidence_status": item.get("evidence_status"),
    }


def _audit_cumulative_checkpoint(tag: str, required_rows: int | None, item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {"tag": tag, "expected_count": required_rows, "status": "FAIL", "reasons": ["missing_cumulative_checkpoint_in_evidence"]}
    reasons: list[str] = []
    if item.get("checkpoint_proof") != "PASS":
        reasons.append(f"checkpoint_proof={item.get('checkpoint_proof')!r}")
    if item.get("evidence_status") != "PASS":
        reasons.append(f"evidence_status={item.get('evidence_status')!r}")
    missing_artifacts = item.get("missing_required_artifacts")
    if missing_artifacts:
        reasons.append(f"missing_required_artifacts={missing_artifacts}")
    actual_expected = _as_int(item.get("expected_count"))
    if required_rows is not None and actual_expected != required_rows:
        reasons.append(f"expected_count={actual_expected!r}")
    return {
        "tag": tag,
        "expected_count": required_rows,
        "status": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
        "checkpoint_proof": item.get("checkpoint_proof"),
        "evidence_status": item.get("evidence_status"),
    }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 1M Production Plan Contract Audit",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Formal chunk PASS: `{summary['formal_pass_count']}` / `{summary['expected_chunks']}`",
        f"- Cumulative checkpoint PASS: `{summary['cumulative_pass_count']}` / `{summary['expected_chunks']}`",
        f"- Total non-empty `.s4p`: `{summary['total_nonempty_s4p']}` / `{summary['expected_total_rows']}`",
        "",
        "## Formal Chunks",
        "",
        "| Tag | Status | Non-empty `.s4p` | Reasons |",
        "| --- | --- | ---: | --- |",
    ]
    for item in summary["formal_results"]:
        reasons = "; ".join(item["reasons"]) if item["reasons"] else "none"
        lines.append(f"| `{item['tag']}` | `{item['status']}` | {item['nonempty_s4p_count']} | {reasons} |")
    lines.extend(["", "## Cumulative Checkpoints", "", "| Tag | Expected rows | Status | Reasons |", "| --- | ---: | --- | --- |"])
    for item in summary["cumulative_results"]:
        reasons = "; ".join(item["reasons"]) if item["reasons"] else "none"
        lines.append(f"| `{item['tag']}` | {item['expected_count']} | `{item['status']}` | {reasons} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
