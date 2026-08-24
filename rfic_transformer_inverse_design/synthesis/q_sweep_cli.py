"""Command-line entry point for three-input exact-Q MLP synthesis."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .frozen_mlp import FrozenTandemMLP
from .q_sweep import PhysicalTarget3, execute_q_sweep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Hold Lp/Ls/|K| fixed, scan Q=10..20 through one frozen MLP, "
            "and select by proxy or fresh real-EMX error."
        )
    )
    parser.add_argument(
        "--model-dir",
        default=os.environ.get("RFIC_Q_SWEEP_MODEL_DIR"),
        help="Private directory containing the hash-bound summary and NPZ weights.",
    )
    parser.add_argument("--model-contract", default=None)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--lp-nh", type=float, required=True)
    parser.add_argument("--ls-nh", type=float, required=True)
    parser.add_argument("--k-abs", type=float, required=True)
    parser.add_argument("--mode", choices=("proxy", "physical"), default="proxy")
    parser.add_argument(
        "--physical-backend-command",
        default=os.environ.get("RFIC_Q_SWEEP_PHYSICAL_BACKEND"),
        help=(
            "Private MARS adapter command. It receives --request-json and "
            "--out-dir arguments and must return all eleven fresh-EMX rows."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.model_dir:
        raise SystemExit(
            "--model-dir or RFIC_Q_SWEEP_MODEL_DIR is required; weights are not "
            "distributed in the public repository"
        )
    model = FrozenTandemMLP.load(
        args.model_dir,
        contract_path=args.model_contract,
    )
    target = PhysicalTarget3(
        design_id=args.design_id,
        lp_nh=args.lp_nh,
        ls_nh=args.ls_nh,
        k_abs=args.k_abs,
    )
    result = execute_q_sweep(
        model=model,
        target=target,
        output_dir=args.out_dir,
        mode=args.mode,
        physical_backend_command=args.physical_backend_command,
    )
    print(
        json.dumps(
            {
                "overall_status": "PASS",
                "mode": result.mode,
                "selected_q": result.selected_q,
                "selection_score": result.selection_score,
                "evidence_source": result.evidence_source,
                "output_dir": str(Path(args.out_dir).expanduser().resolve()),
                "selected_artifacts": result.selected_artifacts,
                "scientific_boundary": result.scientific_boundary,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
