#!/usr/bin/env python3
"""Audit the current MARS S8P objective: 5-60 GHz, 1 GHz, 56 points, grounded unused ports."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    quality_dir = Path(args.quality_dir).expanduser().resolve() if args.quality_dir else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(dataset_dir / "dataset_rows.csv")
    manifest = _read_json(dataset_dir / "dataset_manifest.json")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    touchstone_records = _audit_touchstones(dataset_dir, ok_rows, args)
    quality_records = _audit_quality_dir(quality_dir, args) if quality_dir else []
    checks = _build_checks(rows, ok_rows, manifest, touchstone_records, quality_records, args)
    status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "MARS56_GROUNDED_S8P_OBJECTIVE_VERIFIED" if status == "PASS" else "DO_NOT_CLAIM_MARS56_GROUNDED_S8P_OBJECTIVE",
        "dataset_dir": str(dataset_dir),
        "quality_dir": "" if quality_dir is None else str(quality_dir),
        "expected_contract": {
            "touchstone_extension": ".s8p",
            "ports": int(args.expected_ports),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
            "differential_port_pairs": str(args.expected_differential_port_pairs),
            "unused_ports_are_grounded": bool(args.require_ground_unused_ports),
        },
        "row_count": len(rows),
        "ok_count": len(ok_rows),
        "touchstone_summary": _touchstone_summary(touchstone_records),
        "quality_summary": _quality_summary(quality_records),
        "checks": checks,
        "limitations": [
            "This audit proves the local MARS dataset/quality artifacts match the configured 56-point S8P grounded-port contract.",
            "It does not prove HFSS agreement or physics quality; those remain separate validation gates.",
            "It does not run EMX; it only checks existing Touchstone and post-processing artifacts.",
        ],
    }

    summary_path = out_dir / "mars56_grounded_s8p_objective_audit_summary.json"
    report_path = out_dir / "MARS56_GROUNDED_S8P_OBJECTIVE_AUDIT.md"
    touchstone_csv = out_dir / "mars56_grounded_s8p_touchstone_records.csv"
    checks_csv = out_dir / "mars56_grounded_s8p_objective_checks.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_csv(touchstone_csv, touchstone_records)
    _write_csv(checks_csv, checks)

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"touchstone_records={touchstone_csv}")
    print(f"checks_csv={checks_csv}")
    for check in checks:
        print(f"{check['status']:4s} {check['name']}: {check['detail']}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--quality-dir", help="Optional dataset_quality_gates directory to cross-check")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--expected-ok-count", type=int)
    parser.add_argument("--require-all-ok", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-differential-port-pairs", default="1,4:5,6")
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-checks", type=int, default=500)
    parser.add_argument("--require-ground-unused-ports", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}


def _audit_touchstones(dataset_dir: Path, rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for idx, row in enumerate(rows[: max(0, int(args.max_touchstone_checks))]):
        raw_path = _row_touchstone_path(row)
        path = _resolve(dataset_dir, raw_path) if raw_path else None
        record: dict[str, Any] = {
            "row_index": idx,
            "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{idx}",
            "path": "" if path is None else str(path),
            "exists": False,
            "suffix": "",
            "num_ports": "",
            "freq_start_ghz": "",
            "freq_stop_ghz": "",
            "freq_points": "",
            "freq_step_ghz": "",
            "status": "FAIL",
            "reason": "",
        }
        if path is None:
            record["reason"] = "missing touchstone path"
            records.append(record)
            continue
        record["suffix"] = path.suffix.lower()
        if not path.is_file():
            record["reason"] = "touchstone file not found"
            records.append(record)
            continue
        record["exists"] = True
        try:
            result = load_touchstone(path)
        except Exception as exc:  # noqa: BLE001
            record["reason"] = f"load failed: {type(exc).__name__}: {exc}"
            records.append(record)
            continue
        freqs = np.asarray(result.freqs_hz, dtype=float)
        record["num_ports"] = int(result.num_ports)
        record["freq_points"] = int(freqs.size)
        if freqs.size:
            record["freq_start_ghz"] = float(freqs[0]) / 1.0e9
            record["freq_stop_ghz"] = float(freqs[-1]) / 1.0e9
        if freqs.size > 1:
            diffs = np.diff(freqs)
            record["freq_step_ghz"] = float(diffs[0]) / 1.0e9
            record["freq_step_span_hz"] = float(np.max(diffs) - np.min(diffs))
        reasons = _touchstone_reasons(path, int(result.num_ports), freqs, args)
        record["status"] = "PASS" if not reasons else "FAIL"
        record["reason"] = "; ".join(reasons)
        records.append(record)
    return records


def _audit_quality_dir(quality_dir: Path | None, args: argparse.Namespace) -> list[dict[str, Any]]:
    if quality_dir is None:
        return []
    summary = _read_json(quality_dir / "dataset_quality_gates_summary.json")
    response_manifest = _read_json(quality_dir / "response_features" / "dataset_manifest.json")
    s8p_summary = _read_json(
        quality_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_dataset_audit_summary.json"
    )
    return [
        {
            "name": "dataset quality gates summary",
            "status": "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            "detail": f"overall_status={summary.get('overall_status')}",
        },
        {
            "name": "quality gate used grounded unused ports",
            "status": "PASS" if bool((summary.get("arguments") or {}).get("touchstone_ground_unused_ports")) else "FAIL",
            "detail": f"touchstone_ground_unused_ports={(summary.get('arguments') or {}).get('touchstone_ground_unused_ports')}",
        },
        {
            "name": "quality gate used 8-port Touchstone extraction",
            "status": "PASS" if int((summary.get("arguments") or {}).get("touchstone_expected_ports") or -1) == int(args.expected_ports) else "FAIL",
            "detail": f"touchstone_expected_ports={(summary.get('arguments') or {}).get('touchstone_expected_ports')}",
        },
        {
            "name": "response feature manifest grounded unused ports",
            "status": "PASS" if bool((response_manifest.get("response_feature_extraction") or {}).get("ground_unused_ports")) else "FAIL",
            "detail": f"response_feature_extraction={response_manifest.get('response_feature_extraction')}",
        },
        {
            "name": "S8P physical-feature audit summary",
            "status": "PASS" if s8p_summary.get("overall_status") == "PASS" else "FAIL",
            "detail": f"overall_status={s8p_summary.get('overall_status')}",
        },
    ]


def _build_checks(
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    manifest: dict[str, Any],
    touchstone_records: list[dict[str, Any]],
    quality_records: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    expected_ok = args.expected_ok_count if args.expected_ok_count is not None else args.expected_count
    expected_pairs, expected_pairs_reason = _parse_pairs(str(args.expected_differential_port_pairs))
    actual_pairs, actual_pairs_reason = _manifest_pairs(manifest)
    checks = [
        _check(bool(rows), "dataset_rows.csv present", f"rows={len(rows)}"),
        _check(args.expected_count is None or len(rows) == int(args.expected_count), "dataset row count", f"expected={args.expected_count} actual={len(rows)}"),
        _check(expected_ok is None or len(ok_rows) >= int(expected_ok), "ok row count", f"expected>={expected_ok} actual={len(ok_rows)}"),
        _check((not args.require_all_ok) or len(ok_rows) == len(rows), "all rows ok", f"rows={len(rows)} ok={len(ok_rows)}"),
        _check(str(manifest.get("port_mode")) == str(args.expected_port_mode), "manifest port mode", f"expected={args.expected_port_mode} actual={manifest.get('port_mode')}"),
        _check(actual_pairs is not None, "manifest differential port pairs parse", actual_pairs_reason or str(manifest.get("differential_port_pairs"))),
        _check(actual_pairs is not None and actual_pairs == expected_pairs, "manifest differential port pairs", f"expected={expected_pairs} actual={actual_pairs}; {expected_pairs_reason or actual_pairs_reason}"),
        _check((not args.require_ground_unused_ports) or _manifest_ground_unused(manifest), "manifest grounded unused ports", f"ground_unused={_manifest_ground_unused(manifest)}"),
        _check(bool(touchstone_records), "sampled touchstones checked", f"checked={len(touchstone_records)}"),
        _check(all(item["status"] == "PASS" for item in touchstone_records), "sampled touchstones match 56-point S8P contract", f"pass={sum(item['status'] == 'PASS' for item in touchstone_records)} checked={len(touchstone_records)}"),
    ]
    checks.extend(
        _check(item["status"] == "PASS", item["name"], str(item["detail"]))
        for item in quality_records
    )
    return checks


def _touchstone_reasons(path: Path, num_ports: int, freqs: np.ndarray, args: argparse.Namespace) -> list[str]:
    reasons: list[str] = []
    tol = float(args.frequency_tolerance_hz)
    if path.suffix.lower() != ".s8p":
        reasons.append(f"expected .s8p, got {path.suffix}")
    if int(num_ports) != int(args.expected_ports):
        reasons.append(f"expected {args.expected_ports} ports, got {num_ports}")
    if int(freqs.size) != int(args.expected_frequency_points):
        reasons.append(f"expected {args.expected_frequency_points} frequency points, got {freqs.size}")
    if freqs.size:
        expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
        expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
        if abs(float(freqs[0]) - expected_start) > tol:
            reasons.append(f"expected start {args.expected_frequency_start_ghz} GHz, got {float(freqs[0]) / 1e9:.12g} GHz")
        if abs(float(freqs[-1]) - expected_stop) > tol:
            reasons.append(f"expected stop {args.expected_frequency_stop_ghz} GHz, got {float(freqs[-1]) / 1e9:.12g} GHz")
    if freqs.size > 1:
        diffs = np.diff(freqs)
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        if abs(float(diffs[0]) - expected_step) > tol or float(np.max(diffs) - np.min(diffs)) > tol:
            reasons.append(
                f"expected step {args.expected_frequency_step_ghz} GHz, "
                f"got first={float(diffs[0]) / 1e9:.12g} GHz span={float(np.max(diffs) - np.min(diffs)):.6g} Hz"
            )
    return reasons


def _manifest_pairs(manifest: dict[str, Any]) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    raw = manifest.get("differential_port_pairs")
    if isinstance(raw, str):
        return _parse_pairs(raw)
    if not isinstance(raw, list) or len(raw) != 2:
        return None, f"missing or invalid differential_port_pairs={raw!r}"
    pairs: list[tuple[int, int]] = []
    for pair in raw:
        if isinstance(pair, dict):
            values = [pair.get("positive", pair.get("pos", pair.get("p"))), pair.get("negative", pair.get("neg", pair.get("n")))]
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            values = list(pair)
        else:
            return None, f"invalid pair={pair!r}"
        try:
            pairs.append((int(values[0]), int(values[1])))
        except (TypeError, ValueError):
            return None, f"non-integer pair={pair!r}"
    flat = [port for pair in pairs for port in pair]
    if min(flat) >= 1:
        pairs = [(a - 1, b - 1) for a, b in pairs]
    return (pairs[0], pairs[1]), ""


def _parse_pairs(text: str) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    try:
        groups = [item.strip() for item in str(text).split(":") if item.strip()]
        if len(groups) != 2:
            return None, f"expected two pairs in {text!r}"
        pairs = []
        for group in groups:
            parts = [item.strip() for item in group.split(",") if item.strip()]
            if len(parts) != 2:
                return None, f"expected two ports in {group!r}"
            pairs.append((int(parts[0]), int(parts[1])))
    except ValueError as exc:
        return None, f"invalid pair text={text!r}: {exc}"
    flat = [port for pair in pairs for port in pair]
    if len(set(flat)) != 4:
        return None, f"pairs must use four distinct ports, got {text!r}"
    if min(flat) >= 1:
        pairs = [(a - 1, b - 1) for a, b in pairs]
    return (pairs[0], pairs[1]), ""


def _manifest_ground_unused(manifest: dict[str, Any]) -> bool:
    if bool(manifest.get("ground_unused_s8p_ports")):
        return True
    emx = manifest.get("emx") if isinstance(manifest.get("emx"), dict) else {}
    if bool(emx.get("ground_unused_s8p_ports")):
        return True
    response = manifest.get("response_feature_extraction") if isinstance(manifest.get("response_feature_extraction"), dict) else {}
    return bool(response.get("ground_unused_ports"))


def _row_touchstone_path(row: dict[str, str]) -> str:
    for key in ("touchstone_path", "raw_touchstone_path", "sparam_path", "emx_s8p_path", "emx_touchstone_path"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _resolve(root: Path, path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "none", "nan"}


def _check(condition: bool, name: str, detail: str) -> dict[str, str]:
    return {"status": "PASS" if condition else "FAIL", "name": name, "detail": detail}


def _touchstone_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked": len(records),
        "pass_count": sum(item.get("status") == "PASS" for item in records),
        "fail_count": sum(item.get("status") == "FAIL" for item in records),
    }


def _quality_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked": len(records),
        "pass_count": sum(item.get("status") == "PASS" for item in records),
        "fail_count": sum(item.get("status") == "FAIL" for item in records),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 Grounded S8P Objective Audit",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Quality dir: `{summary['quality_dir'] or 'not provided'}`",
        "",
        "## Contract",
        "",
    ]
    contract = summary["expected_contract"]
    for key, value in contract.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Checks", ""])
    for check in summary["checks"]:
        lines.append(f"- `{check['status']}` {check['name']}: {check['detail']}")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
