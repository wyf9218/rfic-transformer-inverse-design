#!/usr/bin/env python3
"""Prepare a MARS wideband sample-dataset config and command file.

This helper materializes a run-specific config from the 248k template so the
500-sample pilot and later production runs use the same wideband frequency grid,
grounded-shield port mode, and Cadence pin purpose.
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    template_path = Path(args.template).expanduser().resolve()
    out_config = Path(args.out_config).expanduser().resolve()
    commands_path = (
        Path(args.commands_out).expanduser().resolve()
        if args.commands_out
        else out_config.with_suffix(out_config.suffix + ".commands.sh")
    )
    summary_path = (
        Path(args.summary).expanduser().resolve()
        if args.summary
        else out_config.with_suffix(out_config.suffix + ".summary.json")
    )

    raw = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    points = _frequency_points(args.frequency_start_ghz, args.frequency_stop_ghz, args.frequency_step_ghz)
    _apply_overrides(raw, args, points)

    out_config.parent.mkdir(parents=True, exist_ok=True)
    out_config.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=False), encoding="utf-8")
    commands_text = _commands_text(out_config, args)
    commands_path.write_text(commands_text, encoding="utf-8")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "template": str(template_path),
        "out_config": str(out_config),
        "commands": str(commands_path),
        "run_dir": args.run_dir,
        "count": int(args.count),
        "batch_size": int(args.batch_size),
        "sampler": args.sampler,
        "seed": int(args.seed),
        "frequency": {
            "start_hz": float(args.frequency_start_ghz) * 1.0e9,
            "stop_hz": float(args.frequency_stop_ghz) * 1.0e9,
            "step_hz": float(args.frequency_step_ghz) * 1.0e9,
            "points": points,
            "loader_points": points,
        },
        "port_mode": args.port_mode,
        "cadence_pin_purpose": int(args.pin_purpose),
        "shield": {
            "enabled": bool(args.shield_enabled),
            "kind": args.shield_kind,
            "margin_um": float(args.shield_margin_um),
            "width_um": float(args.shield_width_um),
        },
        "preflight_command": _preflight_command(out_config, args),
        "sample_dataset_command": _sample_command(out_config, args),
        "post_run_progress_audit_command": _progress_audit_command(args),
        "post_run_quality_gate_command": _quality_gate_command(args),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"config={out_config}")
    print(f"commands={commands_path}")
    print(f"summary={summary_path}")
    print(f"frequency_points={points}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        default=str(Path(__file__).resolve().parents[1] / "configs" / "mars_dataset_248k_template.yaml"),
    )
    parser.add_argument("--out-config", required=True)
    parser.add_argument("--commands-out")
    parser.add_argument("--summary")
    parser.add_argument("--run-dir", default="runs/dataset500_wideband_grounded_20260613")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--sampler", choices=("lhs", "lhs_optimized", "sobol"), default="lhs_optimized")
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--pin-purpose", type=int, default=51)
    parser.add_argument("--shield-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--shield-kind", default="ring")
    parser.add_argument("--shield-margin-um", type=float, default=100.0)
    parser.add_argument("--shield-width-um", type=float, default=10.0)
    parser.add_argument("--z-load-ohm", type=float, default=50.0)
    return parser.parse_args(argv)


def _frequency_points(start_ghz: float, stop_ghz: float, step_ghz: float) -> int:
    if step_ghz <= 0.0:
        raise SystemExit("frequency step must be positive")
    if stop_ghz <= start_ghz:
        raise SystemExit("frequency stop must be greater than start")
    intervals = (float(stop_ghz) - float(start_ghz)) / float(step_ghz)
    rounded = round(intervals)
    if not math.isclose(intervals, rounded, rel_tol=0.0, abs_tol=1.0e-9):
        raise SystemExit(
            f"frequency_step_ghz must divide stop-start exactly enough for an inclusive grid: intervals={intervals}"
        )
    return int(rounded) + 1


def _apply_overrides(raw: dict[str, Any], args: argparse.Namespace, points: int) -> None:
    target = dict(raw.get("target") or {})
    target["frequency_start_hz"] = float(args.frequency_start_ghz) * 1.0e9
    target["frequency_stop_hz"] = float(args.frequency_stop_ghz) * 1.0e9
    target["frequency_step_hz"] = float(args.frequency_step_ghz) * 1.0e9
    target["band_points"] = int(points)
    raw["target"] = target

    emx = dict(raw.get("emx") or {})
    emx["port_mode"] = args.port_mode
    emx["cadence_pin_purpose"] = int(args.pin_purpose)
    raw["emx"] = emx

    transformer = dict(raw.get("transformer") or {})
    shield = dict(transformer.get("shield") or {})
    shield["enabled"] = bool(args.shield_enabled)
    shield["kind"] = args.shield_kind
    shield["margin_um"] = float(args.shield_margin_um)
    shield["width_um"] = float(args.shield_width_um)
    transformer["shield"] = shield
    raw["transformer"] = transformer


def _commands_text(config_path: Path, args: argparse.Namespace) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# 1) Fill real MARS EMX/Cadence paths in the config before running with --check-emx-paths.",
        _preflight_command(config_path, args),
        "",
        "# 2) Launch the wideband sample-dataset pilot only after preflight passes.",
        "MPLCONFIGDIR=$PWD/.mplconfig \\",
        _sample_command(config_path, args),
        "",
        "# 3) After the pilot finishes, prove file completeness and EMX command semantics.",
        _progress_audit_command(args),
        "",
        "# 4) Run all local acceptance gates before using the data.",
        _quality_gate_command(args),
        "",
    ]
    return "\n".join(lines)


def _preflight_command(config_path: Path, args: argparse.Namespace) -> str:
    return " ".join(
        [
            ".venv/bin/python",
            "scripts/preflight_dataset_config.py",
            _quote(str(config_path)),
            "--check-emx-paths",
            "--report",
            _quote(str(Path(args.run_dir).with_name(Path(args.run_dir).name + "_config_preflight.md"))),
            "--summary",
            _quote(str(Path(args.run_dir).with_name(Path(args.run_dir).name + "_config_preflight.json"))),
        ]
    )


def _sample_command(config_path: Path, args: argparse.Namespace) -> str:
    return " ".join(
        [
            ".venv/bin/python",
            "-m",
            "rfic_transformer_inverse_design.interfaces.cli",
            "sample-dataset",
            "--config",
            _quote(str(config_path)),
            "--count",
            str(int(args.count)),
            "--batch-size",
            str(int(args.batch_size)),
            "--sampler",
            args.sampler,
            "--seed",
            str(int(args.seed)),
            "--z-load-ohm",
            str(float(args.z_load_ohm)),
            "--out-dir",
            _quote(args.run_dir),
            "--fail-on-error",
        ]
    )


def _progress_audit_command(args: argparse.Namespace) -> str:
    points = _frequency_points(args.frequency_start_ghz, args.frequency_stop_ghz, args.frequency_step_ghz)
    return " ".join(
        [
            ".venv/bin/python",
            "scripts/audit_mars_run_progress.py",
            _quote(args.run_dir),
            "--out-dir",
            _quote(str(Path(args.run_dir) / "mars_run_progress_audit_20260613")),
            "--expected-count",
            str(int(args.count)),
            "--expected-frequency-start-ghz",
            str(float(args.frequency_start_ghz)),
            "--expected-frequency-stop-ghz",
            str(float(args.frequency_stop_ghz)),
            "--expected-frequency-step-ghz",
            str(float(args.frequency_step_ghz)),
            "--expected-frequency-points",
            str(points),
            "--max-touchstone-frequency-checks",
            str(int(args.count)),
            "--require-clearance-audit",
            "--require-geometry-quality",
            "--internal-angle-deg",
            "135",
            "--terminal-angle-deg",
            "90",
            "--require-emx-command",
            "--expected-port-mode",
            _quote(args.port_mode),
            "--expected-pin-purpose",
            str(int(args.pin_purpose)),
        ]
    )


def _quality_gate_command(args: argparse.Namespace) -> str:
    points = _frequency_points(args.frequency_start_ghz, args.frequency_stop_ghz, args.frequency_step_ghz)
    return " ".join(
        [
            ".venv/bin/python",
            "scripts/run_dataset_quality_gates.py",
            _quote(args.run_dir),
            "--out-dir",
            _quote(str(Path(args.run_dir) / "dataset_quality_gates_20260613")),
            "--require-emx",
            "--expected-port-mode",
            _quote(args.port_mode),
            "--expected-pin-purpose",
            str(int(args.pin_purpose)),
            "--require-clearance-audit",
            "--expected-frequency-start-ghz",
            str(float(args.frequency_start_ghz)),
            "--expected-frequency-stop-ghz",
            str(float(args.frequency_stop_ghz)),
            "--expected-frequency-step-ghz",
            str(float(args.frequency_step_ghz)),
            "--expected-frequency-points",
            str(points),
            "--max-touchstone-frequency-checks",
            str(int(args.count)),
            "--audit-sampling-distribution",
            "--sampling-require-uniform-closer-than-normal",
            "--sampling-min-uniform-vs-normal-fields-fraction",
            "0.8",
            "--sampling-min-histogram-entropy-frac",
            "0.85",
            "--sampling-max-min-norm",
            "0.05",
            "--sampling-min-max-norm",
            "0.95",
            "--sampling-space-filling-strata",
            "20",
            "--sampling-max-space-filling-empty-strata-frac",
            "0",
            "--sampling-max-space-filling-duplicate-frac",
            "0",
            "--touchstone-all",
            "--touchstone-target-frequency-ghz",
            "15",
            "--touchstone-positive-window-start-ghz",
            str(float(args.frequency_start_ghz)),
            "--touchstone-positive-window-stop-ghz",
            "30",
            "--touchstone-shape-window-start-ghz",
            str(float(args.frequency_start_ghz)),
            "--touchstone-shape-window-stop-ghz",
            "30",
            "--touchstone-max-shape-spike-ratio",
            "4",
            "--touchstone-max-shape-relative-step",
            "0.25",
            "--extract-response-features",
            "--audit-response-feature-coverage",
            "--response-require-cm",
            "--response-min-valid-count",
            str(int(args.count)),
            "--audit-zin-coverage",
            "--zin-min-valid-count",
            str(int(args.count)),
            "--select-hfss-samples",
            "--hfss-sample-count",
            "8",
        ]
    )


def _quote(value: str) -> str:
    return shlex.quote(value)


if __name__ == "__main__":
    raise SystemExit(main())
