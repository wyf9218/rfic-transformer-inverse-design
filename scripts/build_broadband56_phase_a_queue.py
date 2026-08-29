#!/usr/bin/env python3
"""Build an exact 10-D, label-free Phase-A queue for broadband56 V2."""

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
from scipy.stats import qmc

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import TransformerOptimizationAdapter, load_run_config  # noqa: E402
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    canonical_geometry_bounds,
    canonical_geometry_sha256,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.layout.drc_rules import audit_tsmc65_top_metal_geometry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    _require_new_output_directory(out_dir)
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "contract")
    for error in validate_contract(contract):
        checks.append(_check(f"contract::{error}", False, error))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract))
    config = _load_config(config_path, checks)
    if config is not None:
        _validate_config(config, checks)

    excluded_hashes = _read_excluded_hashes([Path(value).expanduser().resolve() for value in args.exclude_geometry_csv], checks)
    rows: list[dict[str, Any]] = []
    attempts = 0
    rejected = 0
    duplicates = 0
    rejection_examples: list[str] = []
    bounds: dict[str, tuple[float, float]] = {}
    if config is not None and int(args.count) > 0:
        adapter = TransformerOptimizationAdapter(config.bounds)
        bounds = canonical_geometry_bounds(adapter)
        seen = set(excluded_hashes)
        for round_index in range(max(1, int(args.max_sampling_rounds))):
            remaining = int(args.count) - len(rows)
            if remaining <= 0:
                break
            sample_count = max(remaining, int(math.ceil(remaining * float(args.oversample_factor))))
            unit = _sample_unit(sample_count, len(GEOMETRY_FIELDS), str(args.sampler), int(args.seed) + round_index)
            lower = np.asarray([bounds[name][0] for name in GEOMETRY_FIELDS], dtype=float)
            upper = np.asarray([bounds[name][1] for name in GEOMETRY_FIELDS], dtype=float)
            values = qmc.scale(unit, lower, upper)
            for vector, unit_vector in zip(values, unit):
                attempts += 1
                geometry_values = {name: float(value) for name, value in zip(GEOMETRY_FIELDS, vector)}
                try:
                    geometry = _geometry_from_campaign_values(adapter, geometry_values)
                except Exception as exc:  # noqa: BLE001
                    rejected += 1
                    if len(rejection_examples) < 20:
                        rejection_examples.append(f"attempt {attempts}: geometry build: {type(exc).__name__}: {exc}")
                    continue
                errors = [*config.bounds.validate(geometry), *geometry.validate()]
                drc = audit_tsmc65_top_metal_geometry(geometry, config)
                if not bool(drc.get("ok")):
                    errors.extend(f"DRC: {value}" for value in drc.get("errors") or [])
                if errors:
                    rejected += 1
                    if len(rejection_examples) < 20:
                        rejection_examples.append(f"attempt {attempts}: {'; '.join(errors)}")
                    continue
                flat = geometry.flat_dict()
                geometry_hash = canonical_geometry_sha256(flat)
                if geometry_hash in seen:
                    duplicates += 1
                    continue
                seen.add(geometry_hash)
                rows.append(
                    _candidate_row(
                        len(rows),
                        flat,
                        unit_vector,
                        geometry_hash,
                        fingerprint,
                        str(args.phase),
                        str(args.acquisition_source),
                        str(args.sampler),
                        int(args.seed) + round_index,
                        drc,
                    )
                )
                if len(rows) >= int(args.count):
                    break

    checks.extend(
        [
            _check("requested_count_positive", int(args.count) > 0, args.count),
            _check("requested_count_within_phase_a", int(args.count) <= 50_000, args.count),
            _check("phase_is_frozen_phase_a", str(args.phase) == "PHASE_A", args.phase),
            _check(
                "acquisition_source_is_base_space_filling",
                str(args.acquisition_source) == "base_space_filling",
                args.acquisition_source,
            ),
            _check("queue_count_exact", len(rows) == int(args.count), f"actual={len(rows)}, expected={args.count}"),
            _check("canonical_geometry_unique", len({row["geometry_sha256"] for row in rows}) == len(rows), len(rows)),
            _check("queue_contains_no_response_labels", all(not _looks_like_label(key) for row in rows for key in row), "geometry and provenance only"),
        ]
    )
    status = "PASS" if checks and all(item["pass"] for item in checks) else "FAIL"
    queue_path = out_dir / "broadband56_candidate_queue.csv"
    _write_csv(queue_path, rows)
    summary_path = out_dir / "broadband56_candidate_queue_summary.json"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_QUEUE_FOR_PRIVATE_PRODUCTION_BACKEND" if status == "PASS" else "DO_NOT_RUN_CADENCE_CALIBRE_OR_EMX",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "campaign_phase": str(args.phase),
        "acquisition_source": str(args.acquisition_source),
        "sampler": str(args.sampler),
        "seed": int(args.seed),
        "requested_count": int(args.count),
        "queue_count": len(rows),
        "sampling_attempts": attempts,
        "analytical_or_drc_rejections": rejected,
        "duplicate_rejections": duplicates,
        "excluded_prior_geometry_count": len(excluded_hashes),
        "canonical_geometry_fields": list(GEOMETRY_FIELDS),
        "canonical_geometry_bounds": {name: list(bounds.get(name, ())) for name in GEOMETRY_FIELDS},
        "candidate_queue": _file_evidence(queue_path),
        "checks": checks,
        "rejection_examples": rejection_examples,
        "scientific_boundary": "This queue is geometry-only. It contains no proxy or physical labels and is not accepted data.",
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)
    print(f"overall_status={status}")
    print(f"queue_count={len(rows)}")
    print(f"candidate_queue={queue_path}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--sampler", choices=("lhs_optimized", "sobol"), default="sobol")
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--phase", default="PHASE_A")
    parser.add_argument("--acquisition-source", default="base_space_filling")
    parser.add_argument("--exclude-geometry-csv", action="append", default=[])
    parser.add_argument("--oversample-factor", type=float, default=1.25)
    parser.add_argument("--max-sampling-rounds", type=int, default=20)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _sample_unit(count: int, dimensions: int, sampler: str, seed: int) -> np.ndarray:
    if sampler == "lhs_optimized":
        return qmc.LatinHypercube(d=dimensions, seed=seed, optimization="random-cd").random(n=count)
    engine = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
    exponent = int(math.ceil(math.log2(max(1, count))))
    return engine.random_base2(m=exponent)[:count]


def _geometry_from_campaign_values(adapter: TransformerOptimizationAdapter, values: dict[str, float]) -> Any:
    vector = []
    for name in adapter.field_order():
        if name in {"primary_width_um", "secondary_width_um"}:
            vector.append(values["line_width_um"])
        else:
            vector.append(values[name])
    return adapter.from_vector(vector).with_shared_line_width(values["line_width_um"])


def _candidate_row(
    index: int,
    flat: dict[str, Any],
    unit_vector: np.ndarray,
    geometry_hash: str,
    fingerprint: str,
    phase: str,
    acquisition_source: str,
    sampler: str,
    sampling_seed: int,
    drc: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "selection_rank": index + 1,
        "candidate_index": index,
        "candidate_id": f"b56v2_{geometry_hash[:16]}",
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "campaign_phase": phase,
        "acquisition_source": acquisition_source,
        "geometry_id": geometry_hash,
        "geometry_sha256": geometry_hash,
        "geometry_fingerprint_sha256": geometry_hash,
        "geometry_fingerprint_schema": "ordered_10d_um_sha256_v2",
        "geometry_fingerprint_quantization_um": "1e-9",
        "inside_target_bin": "true",
        "selection_score": "",
        "analytical_status": "PASS",
        "topology_status": "PASS",
        "top_metal_drc_status": "PASS",
        "drc_status": str(drc.get("status") or "PASS"),
        "drc_rule_source": str(drc.get("rule_source") or ""),
        "candidate_generation_mode": f"{sampler}_normalized_10d",
        "candidate_generation_seed": int(sampling_seed),
    }
    for name, unit_value in zip(GEOMETRY_FIELDS, unit_vector):
        row[f"geom__{name}"] = float(flat[name])
        row[f"unit__{name}"] = float(unit_value)
    return row


def _read_excluded_hashes(paths: list[Path], checks: list[dict[str, Any]]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        if not path.is_file():
            checks.append(_check("excluded_geometry_csv_exists", False, str(path)))
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                value = str(row.get("geometry_sha256") or row.get("geometry_id") or "").strip().lower()
                if value:
                    excluded.add(value)
        checks.append(_check("excluded_geometry_csv_exists", True, str(path)))
    return excluded


def _validate_config(config: Any, checks: list[dict[str, Any]]) -> None:
    grid = tuple(int(round(value)) for value in config.target.frequency_points_hz())
    power = config.emx.power_line_8port
    checks.extend(
        [
            _check("frequency_grid_exact_56", grid == FREQUENCY_GRID_HZ, str(grid)),
            _check("port_mode_grounded", str(config.emx.port_mode) == "single_ended_shield_grounded", config.emx.port_mode),
            _check("touchstone_mode_s4p", str(power.touchstone_mode) == "signal_4_grounded_aux", power.touchstone_mode),
            _check("signal_port_map_exact", tuple(power.port_map) == ("P001", "P002", "P003", "P004"), power.port_map),
            _check("cadence_pin_purpose_51", int(config.emx.cadence_pin_purpose) == 51, config.emx.cadence_pin_purpose),
        ]
    )


def _looks_like_label(name: str) -> bool:
    lowered = name.lower()
    blocked = ("lp_nh", "ls_nh", "qp", "qs", "qmin", "signed_k", "k_abs", "xp_ohm", "xs_ohm", "s11_", "z11_")
    return not lowered.startswith(("geom__", "unit__")) and any(token in lowered for token in blocked)


def _load_config(path: Path, checks: list[dict[str, Any]]) -> Any | None:
    if not path.is_file():
        checks.append(_check("config_exists", False, str(path)))
        return None
    checks.append(_check("config_exists", True, str(path)))
    try:
        config = load_run_config(path)
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("config_loads", False, f"{type(exc).__name__}: {exc}"))
        return None
    checks.append(_check("config_loads", True, str(path)))
    return config


def _read_json(path: Path, checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(f"{name}_exists", False, str(path)))
        return {}
    checks.append(_check(f"{name}_exists", True, str(path)))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(f"{name}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{name}_parses", isinstance(value, dict), type(value).__name__))
    return value if isinstance(value, dict) else {}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"no-clobber output already exists: {path}")


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": str(name), "pass": bool(passed), "detail": str(detail)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _write_sha256s(out_dir: Path) -> None:
    index = out_dir / "SHA256SUMS.txt"
    lines = []
    for path in sorted(item for item in out_dir.iterdir() if item.is_file() and item != index):
        lines.append(f"{_sha256(path)}  {path.name}")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
