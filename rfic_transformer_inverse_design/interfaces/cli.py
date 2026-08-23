"""CLI for the RFIC Transformer Toolkit."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from rfic_transformer_inverse_design.api import TransformerEmxEvaluator, TransformerOptimizer, load_run_config
from rfic_transformer_inverse_design.core import (
    TransformerGeometrySpec,
    TransformerOptimizationAdapter,
    topology_mode_from_turns,
)
from rfic_transformer_inverse_design.core.defaults import load_run_config_from_raw
from rfic_transformer_inverse_design.dataset import run_sample_dataset
from rfic_transformer_inverse_design.execution.serialization import _json_default
from rfic_transformer_inverse_design.paths import runtime_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RFIC Transformer Toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-only", help="Generate one layout and preview without EMX")
    _add_common_run_args(create_parser)
    _add_geometry_args(create_parser)

    eval_parser = subparsers.add_parser("eval", help="Evaluate one transformer geometry through EMX")
    _add_common_run_args(eval_parser)
    _add_geometry_args(eval_parser)

    optimize_parser = subparsers.add_parser("optimize", help="Run the transformer optimization search")
    _add_common_run_args(optimize_parser)

    sample_parser = subparsers.add_parser(
        "sample-dataset",
        help="Generate a uniformly sampled transformer dataset through layout/Cadence/EMX evaluation",
    )
    _add_common_run_args(sample_parser)
    sample_parser.add_argument("--count", type=int, required=True, help="Number of geometry samples to generate")
    sample_parser.add_argument("--batch-size", type=int, default=10, help="Number of layouts per Cadence round-trip batch")
    sample_parser.add_argument(
        "--sampler",
        choices=("lhs", "lhs_optimized", "sobol"),
        default="lhs",
        help="Uniform DOE sampler",
    )
    sample_parser.add_argument("--seed", type=int, default=1234, help="Sampler random seed")
    sample_parser.add_argument("--z-load-ohm", type=float, default=50.0, help="Load impedance used for loaded Zin columns")
    sample_parser.add_argument("--uniformity-bins", type=int, default=10, help="Histogram bins for marginal uniformity checks")
    sample_parser.add_argument(
        "--create-only",
        action="store_true",
        help="Only export/check layouts without running Cadence/EMX",
    )
    sample_parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit nonzero if any sampled evaluation fails",
    )

    compare_parser = subparsers.add_parser(
        "compare-lumped",
        help="Overlay differential EMX and lumped-model S-parameters for one Touchstone file",
    )
    compare_parser.add_argument("--touchstone", required=True, help="Path to the EMX .s4p file")
    compare_parser.add_argument("--config", help="Optional transformer YAML config override")
    compare_parser.add_argument(
        "--out-dir",
        help="Optional output directory for the comparison plot. Defaults to the Touchstone folder.",
    )
    return parser.parse_args()


def _add_common_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Optional transformer YAML config override")
    parser.add_argument(
        "--out-dir",
        help=(
            "Root directory for generated artifacts. Defaults to "
            "tmp/rfic_transformer_inverse_design/runs/<command> under the current working directory."
        ),
    )
    parser.add_argument("--primary-turns", type=int, choices=(1, 2))
    parser.add_argument("--secondary-turns", type=int, choices=(1, 2))
    parser.add_argument("--primary-center-tap", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--secondary-center-tap", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--optimizer-name",
        choices=("cma_es", "turbo"),
        help="Optional optimizer backend override.",
    )


def _add_geometry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--primary-outer-width-um", type=float)
    parser.add_argument("--primary-outer-height-um", type=float)
    parser.add_argument("--secondary-outer-width-um", type=float)
    parser.add_argument("--secondary-outer-height-um", type=float)
    parser.add_argument("--outer-width-um", type=float, help="Legacy shorthand that sets both primary and secondary outer width.")
    parser.add_argument("--outer-height-um", type=float, help="Legacy shorthand that sets both primary and secondary outer height.")
    parser.add_argument("--primary-width-um", type=float)
    parser.add_argument("--secondary-width-um", type=float)
    parser.add_argument("--primary-spacing-um", type=float)
    parser.add_argument("--secondary-spacing-um", type=float)
    parser.add_argument("--primary-terminal-y-span-um", type=float)
    parser.add_argument("--secondary-terminal-y-span-um", type=float)
    parser.add_argument("--offset-um", type=float)
    parser.add_argument("--primary-feed-extension-um", type=float)
    parser.add_argument("--secondary-feed-extension-um", type=float)
    parser.add_argument(
        "--feed-extension-um",
        type=float,
        help="Legacy shorthand that sets both primary and secondary feed extensions.",
    )


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "out_dir", None):
        return Path(args.out_dir).resolve()
    return (runtime_root() / "runs" / args.command).resolve()


def _load_cfg(config_path: str | None):
    return load_run_config(path=config_path)


def _apply_topology_overrides(args: argparse.Namespace, cfg):
    raw = asdict(cfg)
    emx = dict(raw.get("emx", {}) or {})
    pairs = emx.get("differential_port_pairs")
    if pairs is not None:
        emx["differential_port_pairs"] = [
            [int(first) + 1, int(second) + 1]
            for first, second in pairs
        ]
    raw["emx"] = emx
    target = dict(raw.get("target", {}) or {})
    bounds = dict(raw.get("bounds", {}) or {})
    primary_bounds = dict(bounds.get("primary", {}) or {})
    secondary_bounds = dict(bounds.get("secondary", {}) or {})
    primary_turns = getattr(args, "primary_turns", None)
    secondary_turns = getattr(args, "secondary_turns", None)
    primary_center_tap = getattr(args, "primary_center_tap", None)
    secondary_center_tap = getattr(args, "secondary_center_tap", None)
    if primary_turns is not None:
        primary_bounds["turns"] = int(primary_turns)
    if secondary_turns is not None:
        secondary_bounds["turns"] = int(secondary_turns)
    if primary_center_tap is not None:
        primary_bounds["center_tap"] = bool(primary_center_tap)
    if secondary_center_tap is not None:
        secondary_bounds["center_tap"] = bool(secondary_center_tap)
    effective_topology_mode = topology_mode_from_turns(
        primary_turns=int(primary_bounds.get("turns", cfg.bounds.primary.turns)),
        secondary_turns=int(secondary_bounds.get("turns", cfg.bounds.secondary.turns)),
    )
    bounds["primary"] = primary_bounds
    bounds["secondary"] = secondary_bounds
    bounds["topology_mode"] = effective_topology_mode
    target["topology_mode"] = effective_topology_mode
    raw["bounds"] = bounds
    raw["target"] = target
    cfg = load_run_config_from_raw(raw)
    if getattr(args, "optimizer_name", None):
        cfg = replace(cfg, optimizer=replace(cfg.optimizer, name=str(args.optimizer_name)))
    return cfg


def _build_geometry(args: argparse.Namespace, cfg) -> TransformerGeometrySpec:
    adapter = TransformerOptimizationAdapter(cfg.bounds)
    values = cfg.bounds.midpoint().flat_dict()
    overrides = {
        "primary_outer_width_um": args.primary_outer_width_um,
        "primary_outer_height_um": args.primary_outer_height_um,
        "secondary_outer_width_um": args.secondary_outer_width_um,
        "secondary_outer_height_um": args.secondary_outer_height_um,
        "primary_width_um": args.primary_width_um,
        "secondary_width_um": args.secondary_width_um,
        "primary_spacing_um": args.primary_spacing_um,
        "secondary_spacing_um": args.secondary_spacing_um,
        "primary_terminal_y_span_um": args.primary_terminal_y_span_um,
        "secondary_terminal_y_span_um": args.secondary_terminal_y_span_um,
        "offset_um": args.offset_um,
        "primary_feed_extension_um": args.primary_feed_extension_um,
        "secondary_feed_extension_um": args.secondary_feed_extension_um,
    }
    if args.outer_width_um is not None:
        overrides["primary_outer_width_um"] = args.outer_width_um
        overrides["secondary_outer_width_um"] = args.outer_width_um
    if args.outer_height_um is not None:
        overrides["primary_outer_height_um"] = args.outer_height_um
        overrides["secondary_outer_height_um"] = args.outer_height_um
    if args.feed_extension_um is not None:
        overrides["primary_feed_extension_um"] = args.feed_extension_um
        overrides["secondary_feed_extension_um"] = args.feed_extension_um
    for key, value in overrides.items():
        if value is not None:
            values[key] = float(value)
    return adapter.from_vector([values[name] for name in adapter.field_order()])


def _print_payload(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, default=_json_default))


def main() -> None:
    args = parse_args()

    if args.command == "compare-lumped":
        cfg = _apply_topology_overrides(args, _load_cfg(args.config))
        evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=_resolve_compare_root(args))
        compare_path = evaluator.compare_touchstone(
            touchstone_path=Path(args.touchstone).resolve(),
            out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        )
        _print_payload(
            {
                "touchstone_path": str(Path(args.touchstone).resolve()),
                "compare_path": str(compare_path),
                "topology_mode": cfg.target.topology_mode,
                "target": {
                    "f0_hz": cfg.target.f0_hz,
                    "lp_h": cfg.target.lp_h,
                    "ls_h": cfg.target.ls_h,
                    "k_target": cfg.target.k_target,
                },
            }
        )
        return

    cfg = _apply_topology_overrides(args, _load_cfg(args.config))
    out_dir = _resolve_out_dir(args)
    evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=out_dir)

    if args.command == "sample-dataset":
        manifest = run_sample_dataset(
            run_config=cfg,
            out_dir=out_dir,
            count=int(args.count),
            batch_size=int(args.batch_size),
            sampler=args.sampler,
            seed=int(args.seed),
            run_emx=not bool(args.create_only),
            z_load_ohm=float(args.z_load_ohm),
            uniformity_bins=int(args.uniformity_bins),
        )
        _print_payload(manifest)
        if bool(args.fail_on_error) and int(manifest.get("fail_count", 0)) > 0:
            raise SystemExit(1)
        return

    if args.command == "optimize":
        optimizer = TransformerOptimizer(evaluator=evaluator)
        result = optimizer.optimize()
    else:
        geometry = _build_geometry(args, cfg)
        result = evaluator.export_only(geometry) if args.command == "create-only" else evaluator.evaluate_geometry(geometry)

    _print_payload(result.summary_dict())
    if result.error is not None and args.command != "create-only":
        raise SystemExit(1)


def _resolve_compare_root(args: argparse.Namespace) -> Path:
    if args.out_dir:
        return Path(args.out_dir).resolve()
    return Path(args.touchstone).resolve().parent


if __name__ == "__main__":
    main()
