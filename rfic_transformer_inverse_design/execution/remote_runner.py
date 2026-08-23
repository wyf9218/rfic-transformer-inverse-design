"""Remote-side runner for SSH-dispatched EMX evaluations."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from ..core.defaults import load_run_config
from ..core.topology import TransformerSpec
from .evaluator import TransformerEmxEvaluator


def _geometry_from_flat_payload(*, run_config, payload: dict[str, object]) -> TransformerSpec:
    bounds = run_config.bounds
    return TransformerSpec.from_flat_dict(
        dict(payload or {}),
        topology_mode=bounds.topology_mode,
        primary_turns=bounds.primary.turns,
        secondary_turns=bounds.secondary.turns,
        primary_center_tap=bounds.primary.center_tap,
        secondary_center_tap=bounds.secondary.center_tap,
        primary_bridge_layer=bounds.primary.bridge_layer,
        secondary_bridge_layer=bounds.secondary.bridge_layer,
        primary_bridge_via_layer=bounds.primary.bridge_via_layer,
        secondary_bridge_via_layer=bounds.secondary.bridge_via_layer,
        primary_bridge_lower_layer=bounds.primary.bridge_lower_layer,
        secondary_bridge_lower_layer=bounds.secondary.bridge_lower_layer,
        primary_bridge_lower_via_layer=bounds.primary.bridge_lower_via_layer,
        secondary_bridge_lower_via_layer=bounds.secondary.bridge_lower_via_layer,
        primary_bridge_section=bounds.primary.bridge_section_spec(),
        secondary_bridge_section=bounds.secondary.bridge_section_spec(),
        primary_vdd_bar=bounds.primary.vdd_bar,
        secondary_vdd_bar=bounds.secondary.vdd_bar,
        shield=bounds.shield,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remote EMX evaluation runner")
    parser.add_argument("--config", required=True, help="Path to the run_config YAML file")
    parser.add_argument("--geometry", required=True, help="Path to the geometry payload JSON file")
    parser.add_argument("--root-dir", required=True, help="Root directory for evaluator work products")
    parser.add_argument("--out-json", help="Optional path for the runner result JSON")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_config = load_run_config(Path(args.config))
    run_config = replace(
        run_config,
        emx=replace(
            run_config.emx,
            execution_mode="local",
        ),
    )
    geometry_payload = json.loads(Path(args.geometry).read_text(encoding="utf-8"))
    geometry = _geometry_from_flat_payload(run_config=run_config, payload=geometry_payload)
    evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=Path(args.root_dir))
    result = evaluator.evaluate_geometry(geometry, run_emx=True)
    summary_candidates = (
        result.work_dir / "summary_cadence_roundtrip.json",
        result.work_dir / "summary.json",
    )
    summary_path = next((path for path in summary_candidates if path.exists()), summary_candidates[-1])
    payload = {
        "cache_key": result.cache_key,
        "work_dir": str(result.work_dir),
        "summary_path": str(summary_path),
        "ok": result.ok(),
        "error": result.error,
    }
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
