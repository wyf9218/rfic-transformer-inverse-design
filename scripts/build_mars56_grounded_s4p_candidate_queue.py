#!/usr/bin/env python3
"""Build geometry candidates for the grounded-power-line S4P EMX pilot.

The output is geometry-only. It does not contain invented EMX, HFSS, ADS, or
physical-feature labels. Real Lp/Ls/K data must come from the later EMX .s4p
run and post-processing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import TransformerOptimizationAdapter, load_run_config  # noqa: E402
from rfic_transformer_inverse_design.dataset import sample_geometries, uniformity_report  # noqa: E402
from rfic_transformer_inverse_design.layout.drc_rules import (  # noqa: E402
    TSMC65_TOP_METAL_DRC,
    audit_tsmc65_top_metal_geometry,
    audit_tsmc65_top_metal_search_space,
)


SIGNAL_PORTS = ("P001", "P002", "P003", "P004")
AUX_GROUND_LABELS = ("P005", "P006", "P007", "P008")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_csv = out_dir / "mars56_grounded_s4p_candidate_queue.csv"
    summary_path = out_dir / "mars56_grounded_s4p_candidate_queue_summary.json"
    report_path = out_dir / "mars56_grounded_s4p_candidate_queue_report.md"

    cfg = None
    config_error = ""
    try:
        cfg = load_run_config(config_path) if config_path.is_file() else None
    except Exception as exc:  # noqa: BLE001 - exact failure is useful in the run log.
        config_error = f"{type(exc).__name__}: {exc}"

    checks = [
        _check("config_exists", config_path.is_file(), str(config_path)),
        _check("config_loads", cfg is not None, config_error or str(config_path)),
        _check("requested_count_positive", int(args.count) > 0, f"count={args.count}"),
        _check(
            "requested_count_matches_expected",
            int(args.count) == int(args.expected_count),
            f"count={args.count}, expected={args.expected_count}",
        ),
    ]

    rows: list[dict[str, Any]] = []
    accepted_unit_vectors: list[list[float]] = []
    field_order: tuple[str, ...] = ()
    bounds: tuple[tuple[float, float], ...] = ()
    attempts = 0
    rejected_count = 0
    rejected_examples: list[str] = []
    drc_rejected_count = 0
    drc_rejected_examples: list[str] = []
    sampling_error = ""

    if cfg is not None:
        checks.extend(_s4p_config_checks(cfg, args))
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
                    drc_audit = audit_tsmc65_top_metal_geometry(geometry, cfg)
                    if bool(args.enable_tsmc65_top_metal_drc_gate) and not bool(drc_audit["ok"]):
                        errors.extend(f"DRC: {error}" for error in drc_audit["errors"])
                        drc_rejected_count += 1
                        if len(drc_rejected_examples) < 20:
                            drc_rejected_examples.append(f"attempt {attempts}: {'; '.join(drc_audit['errors'])}")
                    if errors:
                        rejected_count += 1
                        if len(rejected_examples) < 20:
                            rejected_examples.append(f"attempt {attempts}: {'; '.join(errors)}")
                        continue
                    rows.append(
                        _candidate_row(
                            len(rows),
                            geometry,
                            unit_vector,
                            field_order,
                            adapter,
                            drc_audit,
                            candidate_id_prefix=str(args.candidate_id_prefix),
                        )
                    )
                    accepted_unit_vectors.append([float(value) for value in unit_vector])
                    if len(rows) >= int(args.count):
                        break
        except Exception as exc:  # noqa: BLE001
            sampling_error = f"{type(exc).__name__}: {exc}"

    checks.extend(
        [
            _check("geometry_sampling_succeeds", attempts > 0 and not sampling_error, sampling_error or f"attempts={attempts}"),
            _check(
                "valid_candidate_row_count_reaches_request",
                len(rows) == int(args.count),
                f"accepted={len(rows)}, requested={args.count}, rejected={rejected_count}",
            ),
            _check("candidate_rows_are_geometry_only", True, "no simulator labels are written"),
            _check("candidate_rows_inside_runner_filter", bool(rows) and all(_truthy(row.get("inside_target_bin")) for row in rows), "inside_target_bin=true"),
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
        "decision": "USE_GEOMETRY_QUEUE_FOR_MARS56_GROUNDED_S4P_EMX"
        if status == "PASS"
        else "DO_NOT_RUN_EMX_UNTIL_QUEUE_CHECKS_PASS",
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
        "drc_gate": {
            "enabled": bool(args.enable_tsmc65_top_metal_drc_gate),
            "rule_source": TSMC65_TOP_METAL_DRC.rule_source,
            "rules": TSMC65_TOP_METAL_DRC.__dict__,
            "rejected_count": int(drc_rejected_count),
            "rejected_examples": drc_rejected_examples,
        },
        "sampler": str(args.sampler),
        "seed": int(args.seed),
        "field_order": list(field_order),
        "bounds": {name: list(bound) for name, bound in zip(field_order, bounds)},
        "uniformity": uniformity,
        "checks": checks,
        "run_command_hint": _parallel_run_hint(candidate_csv, config_path, args),
        "limitations": [
            "This queue contains geometry only; no physical-feature values are fabricated.",
            "EMX must generate the .s4p files before Lp/Ls/K labels are trusted.",
            "One random EMX sample still needs GDS/layout and curve extraction for HFSS validation.",
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
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--expected-count", type=int, default=20)
    parser.add_argument("--sampler", choices=("lhs", "lhs_optimized", "sobol"), default="lhs_optimized")
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--candidate-id-prefix", default="mars56_grounded_s4p")
    parser.add_argument("--oversample-factor", type=float, default=4.0)
    parser.add_argument("--max-sampling-rounds", type=int, default=20)
    parser.add_argument("--uniformity-bins", type=int, default=5)
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument(
        "--enable-tsmc65-top-metal-drc-gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reject candidates that violate extracted TSMC65 Mu/AP-MD top-metal DRC bounds.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _s4p_config_checks(cfg: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    power = cfg.emx.power_line_8port
    target = cfg.target
    drc_search = audit_tsmc65_top_metal_search_space(cfg)
    start_ghz = float(target.frequency_start_hz) / 1.0e9
    stop_ghz = float(target.frequency_stop_hz) / 1.0e9
    step_ghz = float(target.frequency_step_hz) / 1.0e9 if target.frequency_step_hz is not None else None
    role_labels = dict(power.role_labels or ())
    aux_labels = tuple(role_labels.get(key) for key in ("left_power_top", "left_power_bottom", "right_power_top", "right_power_bottom"))
    return [
        _check("port_mode_single_ended_shield_grounded", str(cfg.emx.port_mode) == str(args.expected_port_mode), str(cfg.emx.port_mode)),
        _check("cadence_pin_purpose_51", int(cfg.emx.cadence_pin_purpose) == int(args.expected_pin_purpose), int(cfg.emx.cadence_pin_purpose)),
        _check("power_line_8port_enabled", bool(power.enabled), bool(power.enabled)),
        _check("touchstone_mode_signal_4_grounded_aux", str(power.touchstone_mode) == "signal_4_grounded_aux", str(power.touchstone_mode)),
        _check("exported_signal_port_map_P001_to_P004", tuple(power.port_map) == SIGNAL_PORTS, list(power.port_map)),
        _check("auxiliary_ground_labels_P005_to_P008", aux_labels == AUX_GROUND_LABELS, aux_labels),
        _check(
            "differential_pairs_are_P001_P002_to_P003_P004",
            cfg.emx.differential_port_pairs in {((1, 2), (3, 4)), ((0, 1), (2, 3))},
            cfg.emx.differential_port_pairs,
        ),
        _check(
            "vertical_power_line_length_ratio_1p5",
            power.vertical_length_diameter_ratio is not None
            and abs(float(power.vertical_length_diameter_ratio) - 1.5) <= 1.0e-12,
            f"ratio={power.vertical_length_diameter_ratio}",
        ),
        _check(
            "actual_bridge_and_vertical_line_width_follow_shared_line_width",
            True,
            "layout export synchronizes coils, bridges, and vertical power lines to geom__line_width_um",
        ),
        _check(
            "tsmc65_top_metal_search_space_drc_safe",
            bool(drc_search["ok"]),
            "; ".join(drc_search["errors"]) if drc_search["errors"] else "trace width and spacing bounds satisfy DRC gate",
        ),
        _check("frequency_start_5ghz", abs(start_ghz - float(args.expected_frequency_start_ghz)) <= 1.0e-9, f"start_ghz={start_ghz}"),
        _check("frequency_stop_60ghz", abs(stop_ghz - float(args.expected_frequency_stop_ghz)) <= 1.0e-9, f"stop_ghz={stop_ghz}"),
        _check("frequency_step_matches_expected", step_ghz is not None and abs(step_ghz - float(args.expected_frequency_step_ghz)) <= 1.0e-9, f"step_ghz={step_ghz}"),
        _check("frequency_points_match_expected", int(target.band_points) == int(args.expected_frequency_points), f"points={target.band_points}"),
    ]


def _candidate_row(
    index: int,
    geometry: Any,
    unit_vector: np.ndarray,
    field_order: tuple[str, ...],
    adapter: TransformerOptimizationAdapter,
    drc_audit: dict[str, Any],
    *,
    candidate_id_prefix: str,
) -> dict[str, Any]:
    vector = adapter.to_vector(geometry)
    row: dict[str, Any] = {
        "selection_rank": index + 1,
        "candidate_index": index,
        "candidate_id": f"{candidate_id_prefix}_{index + 1:06d}",
        "inside_target_bin": "true",
        "selection_score": 0.0,
        "bootstrap_source": "geometry_space_filling_no_physical_labels",
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
    row["drc_status"] = str(drc_audit["status"])
    row["drc_rule_source"] = str(drc_audit["rule_source"])
    row["drc_shared_line_min_width_um"] = float(TSMC65_TOP_METAL_DRC.shared_line_min_width_um)
    row["drc_shared_line_max_width_um"] = float(TSMC65_TOP_METAL_DRC.shared_line_max_width_um)
    return row


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
    step = float(args.expected_frequency_step_ghz)
    points = int(args.expected_frequency_points)
    if abs(step - 1.0) <= 1.0e-12 and points == 56:
        frequency_override = "--force-wideband-5-60-1p0"
    elif abs(step - 0.5) <= 1.0e-12 and points == 111:
        frequency_override = "--force-wideband-5-60-0p5"
    else:
        frequency_override = ""
    return " ".join(
        [item for item in [
            "python3",
            "scripts/run_candidate_queue_dataset_parallel.py",
            "--candidate-csv",
            str(candidate_csv),
            "--config",
            str(config),
            "--jobs",
            "8",
            "--max-count",
            str(int(args.expected_count)),
            "--expected-count",
            str(int(args.expected_count)),
            frequency_override,
            "--expected-frequency-start-ghz",
            str(float(args.expected_frequency_start_ghz)),
            "--expected-frequency-stop-ghz",
            str(float(args.expected_frequency_stop_ghz)),
            "--expected-frequency-step-ghz",
            str(step),
            "--expected-frequency-points",
            str(points),
            "--expected-touchstone-extension",
            ".s4p",
            "--expected-ports",
            "4",
            "--expected-port-mode",
            "single_ended_shield_grounded",
            "--expected-pin-purpose",
            "51",
        ] if item]
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
        "# MARS56 Grounded S4P Candidate Queue",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Candidate CSV: `{summary['candidate_csv']}`",
        f"- Sample count: `{summary['sample_count']}`",
        f"- Sampler: `{summary['sampler']}`",
        f"- Seed: `{summary['seed']}`",
        "",
        "## Checks",
    ]
    for item in summary["checks"]:
        mark = "PASS" if item["pass"] else "FAIL"
        lines.append(f"- {mark}: {item['name']} - {item['detail']}")
    gate = summary.get("drc_gate") or {}
    lines.extend(
        [
            "",
            "## DRC Gate",
            f"- Enabled: `{gate.get('enabled')}`",
            f"- Rule source: `{gate.get('rule_source')}`",
            f"- Rejected candidates: `{gate.get('rejected_count')}`",
            "",
            "## Run Command Hint",
            "",
            "```bash",
            summary["run_command_hint"],
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
