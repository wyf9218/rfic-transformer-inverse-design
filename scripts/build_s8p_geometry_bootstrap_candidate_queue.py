#!/usr/bin/env python3
"""Build a bootstrap geometry candidate queue for the first S8P EMX run.

This script is for the cold-start case where no real `.s8p` physical-feature
dataset exists yet. It samples geometry uniformly from the configured search
space and writes a candidate CSV that can be fed directly to
`run_candidate_queue_dataset_parallel.py`.

It does not run EMX, HFSS, ADS, or Cadence, and it does not invent physical
feature labels. The generated rows are geometry-only candidates; Lp/Ls/Q/K
labels must come from the later EMX `.s8p` run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import TransformerOptimizationAdapter, load_run_config  # noqa: E402
from rfic_transformer_inverse_design.dataset import sample_geometries, uniformity_report  # noqa: E402


PORT_NAMES = tuple(f"P{idx:03d}" for idx in range(1, 9))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_csv = out_dir / "s8p_geometry_bootstrap_candidate_queue.csv"
    summary_path = out_dir / "s8p_geometry_bootstrap_candidate_queue_summary.json"
    report_path = out_dir / "s8p_geometry_bootstrap_candidate_queue_report.md"

    raw_text = _read_text(config_path)
    cfg = None
    config_error = ""
    try:
        cfg = load_run_config(config_path) if config_path.is_file() else None
    except Exception as exc:  # noqa: BLE001 - exact config failure is recorded for readiness.
        config_error = f"{type(exc).__name__}: {exc}"

    checks = [
        _check("config exists", config_path.is_file(), str(config_path)),
        _check("config has no unresolved placeholders", not _has_placeholder(raw_text), "TODO/REPLACE/TBD/PLACEHOLDER/CONFIRM scan"),
        _check("config loads", cfg is not None, config_error or str(config_path)),
        _check("requested sample count is positive", int(args.count) > 0, f"count={args.count}"),
        _check(
            "requested count matches expected EMX bootstrap count",
            int(args.count) == int(args.expected_count),
            f"count={args.count}, expected_count={args.expected_count}",
        ),
    ]

    rows: list[dict[str, Any]] = []
    accepted_unit_vectors: list[list[float]] = []
    attempts = 0
    rejected_count = 0
    rejected_examples: list[str] = []
    field_order: tuple[str, ...] = ()
    bounds: tuple[tuple[float, float], ...] = ()
    sampling_error = ""
    if cfg is not None:
        checks.extend(_s8p_config_checks(cfg, args))
        try:
            adapter = TransformerOptimizationAdapter(cfg.bounds)
            rounds = max(1, int(args.max_sampling_rounds))
            oversample = max(1.0, float(args.oversample_factor))
            for round_index in range(rounds):
                if len(rows) >= int(args.count):
                    break
                remaining = int(args.count) - len(rows)
                batch_count = max(remaining, int(math.ceil(remaining * oversample)))
                samples = sample_geometries(
                    cfg,
                    count=batch_count,
                    sampler=str(args.sampler),
                    seed=int(args.seed) + round_index,
                )
                if not field_order:
                    field_order = tuple(samples.field_order)
                    bounds = tuple(tuple(map(float, item)) for item in samples.bounds)
                for geometry, unit_vector in zip(samples.geometries, samples.unit_vectors):
                    attempts += 1
                    errors = [*cfg.bounds.validate(geometry), *geometry.validate()]
                    if errors:
                        rejected_count += 1
                        if len(rejected_examples) < 20:
                            rejected_examples.append(f"attempt {attempts}: {'; '.join(errors)}")
                        continue
                    rows.append(_candidate_row(len(rows), geometry, unit_vector, field_order, adapter))
                    accepted_unit_vectors.append([float(value) for value in unit_vector])
                    if len(rows) >= int(args.count):
                        break
        except Exception as exc:  # noqa: BLE001
            sampling_error = f"{type(exc).__name__}: {exc}"
    checks.extend(
        [
            _check("geometry sampling succeeds", attempts > 0 and not sampling_error, sampling_error or f"attempts={attempts}, accepted={len(rows)}"),
            _check("valid candidate row count reaches request", len(rows) == int(args.count), f"accepted={len(rows)}, requested={args.count}, attempts={attempts}, rejected={rejected_count}"),
            _check("invalid sampled geometries are rejected before EMX", True, f"rejected={rejected_count}, examples={rejected_examples[:3]}"),
            _check("candidate row count matches request", len(rows) == int(args.count), f"rows={len(rows)}, count={args.count}"),
            _check("candidate rows include geometry columns", bool(rows) and all(f"geom__{name}" in rows[0] for name in field_order), f"fields={list(field_order)}"),
            _check("candidate rows are accepted by candidate-queue runner filter", bool(rows) and all(_truthy(row.get("inside_target_bin")) for row in rows), "inside_target_bin=true for bootstrap rows"),
        ]
    )

    if rows:
        _write_csv(candidate_csv, rows)
    else:
        candidate_csv.write_text("", encoding="utf-8")

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    unit_matrix = np.asarray(accepted_unit_vectors, dtype=float) if accepted_unit_vectors else np.empty((0, 0), dtype=float)
    uniformity = uniformity_report(unit_matrix, field_order, bins=int(args.uniformity_bins)) if accepted_unit_vectors else {}
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_BOOTSTRAP_CANDIDATE_QUEUE_FOR_FIRST_S8P_EMX_RUN"
        if status == "PASS"
        else "DO_NOT_USE_BOOTSTRAP_QUEUE_UNTIL_CHECKS_PASS",
        "config": str(config_path),
        "out_dir": str(out_dir),
        "candidate_csv": str(candidate_csv),
        "candidate_csv_source": _file_source(candidate_csv),
        "sample_count": len(rows),
        "requested_count": int(args.count),
        "expected_count": int(args.expected_count),
        "sampling_attempt_count": int(attempts),
        "sampling_rejected_count": int(rejected_count),
        "sampling_rejected_examples": rejected_examples,
        "sampler": str(args.sampler),
        "seed": int(args.seed),
        "field_order": list(field_order),
        "bounds": {name: list(bound) for name, bound in zip(field_order, bounds)},
        "uniformity": uniformity,
        "checks": checks,
        "run_command_hint": _parallel_run_hint(candidate_csv, config_path, args),
        "arguments": vars(args),
        "limitations": [
            "This is a geometry bootstrap queue only; it does not contain EMX/HFSS/ADS physical-feature labels.",
            "Use it for the first `.s8p` EMX batch when no real physical-feature training dataset exists yet.",
            "After EMX finishes, run the S8P physical-feature dataset audit, select a random validation sample, and compare EMX/HFSS Lp/Ls/Q/K curves.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"candidate_csv={candidate_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Final S8P run config")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--expected-count", type=int, default=500)
    parser.add_argument("--sampler", choices=("lhs", "lhs_optimized", "sobol"), default="lhs_optimized")
    parser.add_argument("--seed", type=int, default=20260616)
    parser.add_argument("--oversample-factor", type=float, default=4.0)
    parser.add_argument("--max-sampling-rounds", type=int, default=20)
    parser.add_argument("--uniformity-bins", type=int, default=10)
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--expected-bridge-width-um", type=float, default=10.0)
    parser.add_argument("--bridge-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _s8p_config_checks(cfg: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    pairs = cfg.emx.differential_port_pairs
    power = cfg.emx.power_line_8port
    target = cfg.target
    start_ghz = float(target.frequency_start_hz) / 1.0e9
    stop_ghz = float(target.frequency_stop_hz) / 1.0e9
    step_ghz = float(target.frequency_step_hz) / 1.0e9 if target.frequency_step_hz is not None else None
    checks = [
        _check("port mode is single_ended_shield_grounded", str(cfg.emx.port_mode) == str(args.expected_port_mode), str(cfg.emx.port_mode)),
        _check("cadence pin purpose is 51", int(cfg.emx.cadence_pin_purpose) == int(args.expected_pin_purpose), int(cfg.emx.cadence_pin_purpose)),
        _check("power_line_8port is enabled", bool(power.enabled), bool(power.enabled)),
        _check("power_line_8port port map is P001-P008", tuple(power.port_map) == PORT_NAMES, list(power.port_map)),
        _check("differential S8P port pairs are present", pairs is not None and len(pairs) >= 2, pairs),
        _check(
            "bridge width matches vertical power-line width",
            power.bridge_width_um is not None
            and abs(float(power.bridge_width_um) - float(args.expected_bridge_width_um)) <= float(args.bridge_width_tolerance_um),
            f"bridge_width_um={power.bridge_width_um}",
        ),
        _check(
            "vertical power-line length ratio is 1.5",
            power.vertical_length_diameter_ratio is not None
            and abs(float(power.vertical_length_diameter_ratio) - 1.5) <= 1.0e-12,
            f"ratio={power.vertical_length_diameter_ratio}",
        ),
        _check("primary center tap is enabled", bool(cfg.bounds.primary.center_tap), bool(cfg.bounds.primary.center_tap)),
        _check("secondary center tap is enabled", bool(cfg.bounds.secondary.center_tap), bool(cfg.bounds.secondary.center_tap)),
        _check("primary VDD/power-line bar is configured", _vdd_bar_ready(cfg.bounds.primary.vdd_bar), cfg.bounds.primary.vdd_bar),
        _check("secondary VDD/power-line bar is configured", _vdd_bar_ready(cfg.bounds.secondary.vdd_bar), cfg.bounds.secondary.vdd_bar),
        _check(
            "primary VDD/power-line bar layer matches primary coil layer",
            _vdd_bar_ready(cfg.bounds.primary.vdd_bar)
            and int(cfg.bounds.primary.vdd_bar.bar_layer) == int(cfg.emx.ap_layer),
            f"bar_layer={None if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.bar_layer}, coil_layer={cfg.emx.ap_layer}",
        ),
        _check(
            "secondary VDD/power-line bar layer matches secondary coil layer",
            _vdd_bar_ready(cfg.bounds.secondary.vdd_bar)
            and int(cfg.bounds.secondary.vdd_bar.bar_layer) == int(cfg.emx.m9_layer),
            f"bar_layer={None if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.bar_layer}, coil_layer={cfg.emx.m9_layer}",
        ),
        _check(
            "frequency grid starts at 5 GHz",
            abs(start_ghz - float(args.expected_frequency_start_ghz)) <= 1.0e-9,
            f"start_ghz={start_ghz}",
        ),
        _check(
            f"frequency grid stops at {float(args.expected_frequency_stop_ghz):g} GHz",
            abs(stop_ghz - float(args.expected_frequency_stop_ghz)) <= 1.0e-9,
            f"stop_ghz={stop_ghz}",
        ),
        _check(
            f"frequency grid step is {float(args.expected_frequency_step_ghz):g} GHz",
            step_ghz is not None and abs(step_ghz - float(args.expected_frequency_step_ghz)) <= 1.0e-9,
            f"step_ghz={step_ghz}",
        ),
        _check(
            f"frequency grid has {int(args.expected_frequency_points)} points",
            int(target.band_points) == int(args.expected_frequency_points),
            f"points={target.band_points}",
        ),
    ]
    return checks


def _candidate_row(
    index: int,
    geometry: Any,
    unit_vector: np.ndarray,
    field_order: tuple[str, ...],
    adapter: TransformerOptimizationAdapter,
) -> dict[str, Any]:
    vector = adapter.to_vector(geometry)
    row: dict[str, Any] = {
        "selection_rank": index + 1,
        "candidate_index": index,
        "candidate_id": f"s8p_bootstrap_{index + 1:05d}",
        "target_rank": "",
        "target_real_bin": "",
        "target_imag_bin": "",
        "inside_target_bin": "true",
        "selection_score": 0.0,
        "bootstrap_source": "geometry_lhs_space_filling_no_physical_labels",
    }
    for name, value, unit_value in zip(field_order, vector, unit_vector):
        row[f"geom__{name}"] = float(value)
        row[f"unit__{name}"] = float(unit_value)
    flat = geometry.flat_dict()
    line_width = flat.get("line_width_um")
    if line_width is not None:
        row["geom__line_width_um"] = float(line_width)
        if "primary_width_um" in field_order:
            row["unit__line_width_um"] = float(unit_vector[field_order.index("primary_width_um")])
    return row


def _vdd_bar_ready(value: Any) -> bool:
    return value is not None and bool(getattr(value, "enabled", False)) and getattr(value, "bar_layer", None) is not None


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _has_placeholder(text: str) -> bool:
    return bool(re.search(r"\b(TODO|TBD|PLACEHOLDER|REPLACE|CONFIRM)\b|/REPLACE/", text, flags=re.IGNORECASE))


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _parallel_run_hint(candidate_csv: Path, config: Path, args: argparse.Namespace) -> str:
    return " ".join(
        [
            ".venv/bin/python",
            "scripts/run_candidate_queue_dataset_parallel.py",
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            "new_s8p_physical_feature_emx_500",
            "--config",
            str(config),
            "--jobs",
            "8",
            "--expected-jobs",
            "8",
            "--max-count",
            str(int(args.expected_count)),
            "--expected-count",
            str(int(args.expected_count)),
            "--force-wideband-5-60-1p0",
            "--expected-port-mode",
            "single_ended_shield_grounded",
            "--expected-pin-purpose",
            "51",
            "--expected-frequency-start-ghz",
            "5.0",
            "--expected-frequency-stop-ghz",
            "60.0",
            "--expected-frequency-step-ghz",
            "1.0",
            "--expected-frequency-points",
            "56",
            "--fail-on-error",
        ]
    )


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Geometry Bootstrap Candidate Queue",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Candidate CSV: `{summary['candidate_csv']}`",
        f"- Sample count: `{summary['sample_count']}`",
        f"- Sampler: `{summary['sampler']}`",
        f"- Seed: `{summary['seed']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(check['detail'])} |")
    lines.extend(
        [
            "",
            "## Uniformity",
            "",
            f"- Field count: `{len(summary['field_order'])}`",
            f"- Uniformity fields: `{len((summary.get('uniformity') or {}).get('fields', {}))}`",
            "",
            "## Run Hint",
            "",
            "```bash",
            summary["run_command_hint"],
            "```",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
