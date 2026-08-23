#!/usr/bin/env python3
"""Build a deterministic, feasibility-gated 10-D current-foundry pilot queue.

The queue is response-blind.  Latin-hypercube points cover the configured
geometry domain, while physical labels remain absent until the exact geometry
is streamed through Cadence and solved by real EMX.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import qmc

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import (  # noqa: E402
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.layout.feasibility import (  # noqa: E402
    PARAMETERIZED_GEOMETRY_NAMES,
    audit_parameterized_transformer_geometry,
    project_power_line_8port_port_ground_overlap,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    REPO_ROOT / "configs/mars_current_foundry_s4p_5_60_0p5_drc_margin_v2.yaml"
)
QUEUE_SCHEMA = "current_foundry_response_blind_10d_queue_v1"
IDENTITY_SCHEMA = "current_foundry_10d_geometry_identity_v1"
CANDIDATE_IDENTITY_SCHEMA = "current_foundry_10d_candidate_identity_v1"
EXPECTED_PORT_MAP = ("P001", "P002", "P003", "P004")
EXPECTED_FOUNDARY_LAYOUT = {
    "enabled": True,
    "manufacturing_grid_um": 0.005,
    "power_line_stitch_pad_depth_um": 6.0,
    "shield_strap_width_um": 10.0,
    "shield_strap_pitch_um": 20.0,
}
SOURCE_FILES = (
    "rfic_transformer_inverse_design/layout/export.py",
    "rfic_transformer_inverse_design/layout/feasibility.py",
    "rfic_transformer_inverse_design/layout/drc_rules.py",
    "rfic_transformer_inverse_design/sim/emx/layout_export.py",
    "rfic_transformer_inverse_design/sim/emx/simulation.py",
    "scripts/run_candidate_queue_dataset.py",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_run_config(config_path)
    adapter = TransformerOptimizationAdapter(config.bounds)
    checks = _contract_checks(config, config_path)
    grid_um = float(config.emx.foundry_layout.manufacturing_grid_um)
    bounds = _parameter_bounds(config)
    rows, generation = _generate_rows(
        config=config,
        adapter=adapter,
        bounds=bounds,
        count=int(args.count),
        seed=int(args.seed),
        candidate_multiplier=int(args.candidate_multiplier),
        grid_um=grid_um,
    )
    queue_path = out_dir / "current_foundry_pilot_queue.csv"
    _write_csv(queue_path, rows)

    checks.update(
        {
            "requested_count_positive": int(args.count) > 0,
            "candidate_multiplier_positive": int(args.candidate_multiplier) > 0,
            "accepted_count_matches_request": len(rows) == int(args.count),
            "accepted_geometry_identities_unique": len(
                {row["candidate_geometry_identity_sha256"] for row in rows}
            )
            == len(rows),
            "all_rows_response_blind": all(
                row["label_status"] == "UNLABELED_AWAITING_FRESH_REAL_EMX"
                for row in rows
            ),
            "queue_csv_exists_nonzero": queue_path.is_file()
            and queue_path.stat().st_size > 0,
        }
    )
    status = "PASS" if checks and all(checks.values()) else "FAIL"
    source_files = {
        relative: _file_record(REPO_ROOT / relative) for relative in SOURCE_FILES
    }
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema": QUEUE_SCHEMA,
        "overall_status": status,
        "decision": (
            "READY_FOR_CADENCE_CALIBRE_REAL_EMX_PILOT"
            if status == "PASS"
            else "DO_NOT_RUN_CURRENT_FOUNDRY_PILOT"
        ),
        "config": _file_record(config_path),
        "queue_csv": _file_record(queue_path),
        "requested_count": int(args.count),
        "accepted_count": len(rows),
        "seed": int(args.seed),
        "candidate_multiplier": int(args.candidate_multiplier),
        "sampler": "latin_hypercube_random_cd",
        "parameter_order": list(PARAMETERIZED_GEOMETRY_NAMES),
        "parameter_bounds_um": {
            name: [float(value[0]), float(value[1])]
            for name, value in bounds.items()
        },
        "manufacturing_grid_um": grid_um,
        "generation": generation,
        "source_files": source_files,
        "checks": checks,
        "limitations": [
            "This queue contains geometry proposals only; it contains no EMX labels.",
            "Analytical geometry checks do not replace candidate-bound Cadence stream-out and Calibre DRC.",
            "Physical-feature range and uniformity can be evaluated only after fresh real EMX S4P files exist.",
            "Proxy predictions, if added downstream, may rank candidates but never become training labels.",
        ],
    }
    summary_path = out_dir / "current_foundry_pilot_queue_summary.json"
    report_path = out_dir / "current_foundry_pilot_queue_report.md"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"queue_csv={queue_path}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, default=32)
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--candidate-multiplier", type=int, default=20)
    return parser.parse_args(argv)


def _contract_checks(config: Any, config_path: Path) -> dict[str, bool]:
    power = config.emx.power_line_8port
    foundry = config.emx.foundry_layout
    return {
        "config_exists": config_path.is_file(),
        "topology_is_1t1t": str(config.target.topology_mode) == "1t1t",
        "both_windings_center_tapped": bool(
            config.bounds.primary.center_tap
            and config.bounds.secondary.center_tap
        ),
        "single_ended_shield_grounded": (
            config.emx.port_mode == "single_ended_shield_grounded"
        ),
        "touchstone_contract_is_four_signal_ports": bool(
            power.enabled
            and power.touchstone_mode == "signal_4_grounded_aux"
            and tuple(power.port_map) == EXPECTED_PORT_MAP
        ),
        "pin_purpose_is_51": int(config.emx.cadence_pin_purpose) == 51,
        "frequency_is_5_to_60_ghz_at_0p5_ghz": bool(
            math.isclose(config.target.frequency_start_hz, 5.0e9)
            and math.isclose(config.target.frequency_stop_hz, 60.0e9)
            and math.isclose(config.target.frequency_step_hz, 0.5e9)
            and int(config.target.band_points) == 111
        ),
        "foundry_layout_enabled": foundry.enabled is True,
        "manufacturing_grid_is_0p005_um": math.isclose(
            foundry.manufacturing_grid_um, 0.005
        ),
        "power_line_stitch_depth_is_6_um": math.isclose(
            foundry.power_line_stitch_pad_depth_um, 6.0
        ),
        "shield_strap_contract_matches": bool(
            math.isclose(foundry.shield_strap_width_um, 10.0)
            and math.isclose(foundry.shield_strap_pitch_um, 20.0)
        ),
    }


def _parameter_bounds(config: Any) -> dict[str, tuple[float, float]]:
    by_name = config.bounds.bounds_by_name()
    primary_width = by_name["primary_width_um"]
    secondary_width = by_name["secondary_width_um"]
    shared_width = (
        max(float(primary_width[0]), float(secondary_width[0])),
        min(float(primary_width[1]), float(secondary_width[1])),
    )
    if shared_width[1] <= shared_width[0]:
        raise ValueError("primary and secondary trace-width bounds do not overlap")
    return {
        "primary_outer_width_um": tuple(by_name["primary_outer_width_um"]),
        "primary_outer_height_um": tuple(by_name["primary_outer_height_um"]),
        "secondary_outer_width_um": tuple(by_name["secondary_outer_width_um"]),
        "secondary_outer_height_um": tuple(by_name["secondary_outer_height_um"]),
        "line_width_um": shared_width,
        "primary_terminal_y_span_um": tuple(
            by_name["primary_terminal_y_span_um"]
        ),
        "secondary_terminal_y_span_um": tuple(
            by_name["secondary_terminal_y_span_um"]
        ),
        "offset_um": tuple(by_name["offset_um"]),
        "primary_feed_extension_um": tuple(
            by_name["primary_feed_extension_um"]
        ),
        "secondary_feed_extension_um": tuple(
            by_name["secondary_feed_extension_um"]
        ),
    }


def _generate_rows(
    *,
    config: Any,
    adapter: TransformerOptimizationAdapter,
    bounds: dict[str, tuple[float, float]],
    count: int,
    seed: int,
    candidate_multiplier: int,
    grid_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if count < 1 or candidate_multiplier < 1:
        return [], {
            "attempted_count": 0,
            "rejected_count": 0,
            "rejection_categories": {},
            "projection_count": 0,
        }
    lower = np.asarray([bounds[name][0] for name in PARAMETERIZED_GEOMETRY_NAMES])
    upper = np.asarray([bounds[name][1] for name in PARAMETERIZED_GEOMETRY_NAMES])
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    rejection_categories: Counter[str] = Counter()
    projection_count = 0
    max_attempts = max(count, count * candidate_multiplier)
    candidate_index = 0
    round_index = 0
    round_sizes: list[int] = []
    while len(rows) < count and candidate_index < max_attempts:
        remaining = count - len(rows)
        round_size = min(remaining, max_attempts - candidate_index)
        round_sizes.append(int(round_size))
        sampler = qmc.LatinHypercube(
            d=len(PARAMETERIZED_GEOMETRY_NAMES),
            seed=seed + round_index * 1009,
            optimization="random-cd",
        )
        candidates = qmc.scale(sampler.random(round_size), lower, upper)
        for vector in candidates:
            values = {
                name: _snap_and_clip(float(value), grid_um, bounds[name])
                for name, value in zip(PARAMETERIZED_GEOMETRY_NAMES, vector)
            }
            audit = audit_parameterized_transformer_geometry(
                values,
                config,
                adapter=adapter,
            )
            projection_applied = False
            if audit["failure_categories"] == [
                "power_line_8port_port_ground_overlap"
            ]:
                projection = project_power_line_8port_port_ground_overlap(
                    values,
                    config,
                    adapter=adapter,
                )
                projected = projection.get("projected_values") or {}
                if projected:
                    values = {
                        name: _snap_and_clip(
                            float(projected.get(name, values[name])),
                            grid_um,
                            bounds[name],
                        )
                        for name in PARAMETERIZED_GEOMETRY_NAMES
                    }
                    audit = audit_parameterized_transformer_geometry(
                        values,
                        config,
                        adapter=adapter,
                    )
                    projection_applied = bool(projection.get("repair_applied"))
            if audit["status"] != "PASS":
                for category in audit["failure_categories"] or ["unspecified"]:
                    rejection_categories[str(category)] += 1
                candidate_index += 1
                continue
            identity = _geometry_identity(values)
            if identity in identities:
                rejection_categories["duplicate_after_grid_snap"] += 1
                candidate_index += 1
                continue
            identities.add(identity)
            if projection_applied:
                projection_count += 1
            rank = len(rows) + 1
            candidate_id = f"current_foundry_pilot_{seed}_{rank:05d}"
            candidate_id_sha256 = _candidate_identity(
                candidate_id=candidate_id,
                geometry_identity=identity,
                seed=seed,
                rank=rank,
            )
            row: dict[str, Any] = {
                "selection_rank": rank,
                "candidate_index": candidate_index,
                "candidate_id": candidate_id,
                "candidate_id_sha256": candidate_id_sha256,
                "candidate_geometry_identity_sha256": identity,
                "candidate_identity_schema": CANDIDATE_IDENTITY_SCHEMA,
                "geometry_identity_schema": IDENTITY_SCHEMA,
                "queue_seed": int(seed),
                "selection_source": "response_blind_geometry_lhs",
                "candidate_generation_mode": "current_foundry_lhs_10d",
                "label_status": "UNLABELED_AWAITING_FRESH_REAL_EMX",
                "local_preflight_status": "PASS",
                "drc_status": "ANALYTICAL_PASS_CALIBRE_REQUIRED",
                "projection_applied": str(projection_applied).lower(),
            }
            row.update(
                {
                    f"geom__{name}": values[name]
                    for name in PARAMETERIZED_GEOMETRY_NAMES
                }
            )
            rows.append(row)
            candidate_index += 1
            if len(rows) == count:
                break
        round_index += 1
    return rows, {
        "maximum_attempt_count": int(max_attempts),
        "actual_evaluated_count": int(candidate_index),
        "lhs_round_count": int(round_index),
        "lhs_round_sizes": round_sizes,
        "evaluated_until_candidate_index": (
            int(rows[-1]["candidate_index"]) if rows else None
        ),
        "rejected_count_before_completion": int(
            candidate_index - len(rows)
        ),
        "rejection_categories": dict(sorted(rejection_categories.items())),
        "projection_count": int(projection_count),
        "acceptance_fraction_over_evaluated": (
            float(len(rows)) / float(int(rows[-1]["candidate_index"]) + 1)
            if rows
            else 0.0
        ),
    }


def _snap_and_clip(
    value: float,
    grid_um: float,
    bounds: tuple[float, float],
) -> float:
    snapped = round(float(value) / float(grid_um)) * float(grid_um)
    return float(min(max(snapped, float(bounds[0])), float(bounds[1])))


def _geometry_identity(values: dict[str, float]) -> str:
    canonical = {
        "schema": IDENTITY_SCHEMA,
        "geometry_um": {
            name: format(float(values[name]), ".12g")
            for name in PARAMETERIZED_GEOMETRY_NAMES
        },
    }
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_identity(
    *,
    candidate_id: str,
    geometry_identity: str,
    seed: int,
    rank: int,
) -> str:
    canonical = {
        "schema": CANDIDATE_IDENTITY_SCHEMA,
        "queue_schema": QUEUE_SCHEMA,
        "candidate_id": str(candidate_id),
        "candidate_geometry_identity_sha256": str(geometry_identity).lower(),
        "seed": int(seed),
        "selection_rank": int(rank),
    }
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "exists": resolved.is_file(),
        "size_bytes": resolved.stat().st_size if resolved.is_file() else None,
        "sha256": _sha256(resolved),
    }


def _render_report(summary: dict[str, Any]) -> str:
    generation = summary["generation"]
    checks = summary["checks"]
    lines = [
        "# Current-foundry 10-D pilot queue",
        "",
        f"- Status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Requested / accepted: `{summary['requested_count']}` / `{summary['accepted_count']}`",
        f"- Seed: `{summary['seed']}`",
        f"- Manufacturing grid: `{summary['manufacturing_grid_um']} um`",
        f"- Projection count: `{generation['projection_count']}`",
        f"- Rejections before completion: `{generation['rejected_count_before_completion']}`",
        "",
        "## Contract checks",
        "",
    ]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        for name, passed in checks.items()
    )
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- This artifact is a response-blind geometry queue.",
            "- Cadence identity, Calibre DRC, real EMX S4P, and physical-feature distribution remain downstream gates.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
