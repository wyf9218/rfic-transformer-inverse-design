#!/usr/bin/env python3
"""Export final-valid EMX S8P candidates as a HFSS validation samples CSV.

``discover_final_valid_emx_s8p_candidates.py`` proves which real EMX S8P files
meet the current Touchstone and layout evidence contract. Downstream HFSS
handoff scripts consume ``physical_feature_validation_samples.csv``. This
script bridges those two artifacts so stale or legacy sample-selection CSVs do
not accidentally drive the final HFSS comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CORE_FIELDS = (
    "selection_rank",
    "evaluation",
    "touchstone_path",
    "source",
    "layout_json_path",
    "power_line_8port_geometry_json_path",
    "summary_json_path",
    "final_validation_candidate_status",
    "touchstone_contract_status",
    "layout_evidence_status",
    "layout_audit_status",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    discovery_summary = Path(args.discovery_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = _read_json(discovery_summary)
    original_rows = _read_original_rows(args.original_samples_csv)
    pass_candidates = [
        item for item in summary.get("results", []) if item.get("final_validation_candidate_status") == "PASS"
    ]
    if args.max_samples is not None:
        pass_candidates = pass_candidates[: max(0, int(args.max_samples))]

    rows = [_candidate_row(item, index, original_rows) for index, item in enumerate(pass_candidates, start=1)]
    status = "PASS" if rows else "FAIL"
    out_csv = out_dir / "physical_feature_validation_samples.csv"
    report_path = out_dir / "final_valid_emx_s8p_sample_selection_report.md"
    selection_summary_path = out_dir / "final_valid_emx_s8p_sample_selection_summary.json"
    fieldnames = _fieldnames(rows)
    _write_csv(out_csv, rows, fieldnames)

    selection_summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "FINAL_VALID_EMX_SAMPLES_READY_FOR_HFSS_HANDOFF"
        if status == "PASS"
        else "NO_FINAL_VALID_EMX_SAMPLES_TO_EXPORT",
        "discovery_summary": str(discovery_summary),
        "discovery_overall_status": summary.get("overall_status"),
        "discovery_final_valid_count": int(summary.get("final_valid_count") or 0),
        "out_dir": str(out_dir),
        "samples_csv": str(out_csv),
        "selected_count": len(rows),
        "max_samples": args.max_samples,
        "original_samples_csv": "" if not args.original_samples_csv else str(Path(args.original_samples_csv).expanduser()),
        "rows": rows,
        "requirements": {
            "candidate_status": "final_validation_candidate_status == PASS",
            "touchstone_contract": ".s8p, 8 ports, 50 ohm, 5-60 GHz, 1.0 GHz, 56 points",
            "layout_contract": "current 8-port power-line layout audit PASS",
            "downstream_csv": "physical_feature_validation_samples.csv",
        },
        "limitations": [
            "This script selects already-generated real EMX candidates only.",
            "It does not run EMX, HFSS, ADS, or Cadence.",
            "Final acceptance still requires HFSS .s8p export and EMX/HFSS Lp/Ls/Q/K/Kw error comparison.",
        ],
    }
    selection_summary_path.write_text(json.dumps(selection_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(selection_summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={selection_summary['decision']}")
    print(f"selected_count={len(rows)}")
    print(f"samples_csv={out_csv}")
    print(f"summary={selection_summary_path}")
    print(f"report={report_path}")
    return 2 if status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--original-samples-csv")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} top-level JSON is {type(data).__name__}")
    return data


def _read_original_rows(path_text: str | None) -> dict[str, dict[str, str]]:
    text = (path_text or "").strip()
    if not text:
        return {}
    path = Path(text).expanduser()
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            keys = {
                (row.get("evaluation") or "").strip(),
                (row.get("sample_id") or "").strip(),
                (row.get("cache_key") or "").strip(),
                (row.get("touchstone_path") or "").strip(),
                (row.get("raw_touchstone_path") or "").strip(),
            }
            for key in keys:
                if key and key not in rows:
                    rows[key] = dict(row)
    return rows


def _candidate_row(item: dict[str, Any], rank: int, original_rows: dict[str, dict[str, str]]) -> dict[str, str]:
    evaluation = str(item.get("evaluation") or f"final_valid_{rank:03d}")
    touchstone_path = str(item.get("touchstone_path") or "")
    base = _matching_original_row(original_rows, evaluation, touchstone_path)
    row = dict(base)
    row.update(
        {
            "selection_rank": str(rank),
            "evaluation": evaluation,
            "touchstone_path": touchstone_path,
            "source": str(item.get("source") or ""),
            "layout_json_path": str(item.get("layout_json_path") or ""),
            "power_line_8port_geometry_json_path": str(item.get("power_line_8port_geometry_json_path") or ""),
            "summary_json_path": str(item.get("summary_json_path") or ""),
            "final_validation_candidate_status": str(item.get("final_validation_candidate_status") or ""),
            "touchstone_contract_status": str(item.get("touchstone_contract_status") or ""),
            "layout_evidence_status": str(item.get("layout_evidence_status") or ""),
            "layout_audit_status": str(item.get("layout_audit_status") or ""),
        }
    )
    return {key: "" if value is None else str(value) for key, value in row.items()}


def _matching_original_row(
    original_rows: dict[str, dict[str, str]], evaluation: str, touchstone_path: str
) -> dict[str, str]:
    for key in (evaluation, touchstone_path, str(Path(touchstone_path).name) if touchstone_path else ""):
        if key and key in original_rows:
            return original_rows[key]
    return {}


def _fieldnames(rows: list[dict[str, str]]) -> list[str]:
    fields: list[str] = []
    for field in DEFAULT_CORE_FIELDS:
        if field not in fields:
            fields.append(field)
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    return fields


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Final-Valid EMX S8P Sample Selection",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Discovery final-valid count: `{summary['discovery_final_valid_count']}`",
        f"- Exported selected count: `{summary['selected_count']}`",
        f"- Samples CSV: `{summary['samples_csv']}`",
        "",
        "## Selected Samples",
        "",
        "| Rank | Evaluation | Touchstone | Source |",
        "| --- | --- | --- | --- |",
    ]
    for row in summary.get("rows", []):
        lines.append(
            "| {rank} | `{evaluation}` | `{touchstone}` | `{source}` |".format(
                rank=row.get("selection_rank", ""),
                evaluation=row.get("evaluation", ""),
                touchstone=row.get("touchstone_path", ""),
                source=row.get("source", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This CSV is the only selected-sample input that should feed the final HFSS handoff after discovery.",
            "It prevents stale random/legacy samples from bypassing the current final-valid EMX gate.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
