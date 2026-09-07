"""Command line entry for isolated research; never controls production."""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
import traceback

from .io import read_json, save_json, load_checkpoint, utc_now


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-data")
    prepare.add_argument("--source-manifest", required=True)
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--previous-splits")
    prepare.add_argument("--seed", type=int, default=17)
    for name in ("train-forward", "train-inverse", "resume", "finetune"):
        train = sub.add_parser(name)
        train.add_argument("--data", required=True)
        train.add_argument("--out", required=True)
        train.add_argument("--contract", required=True)
        train.add_argument("--kind", choices=("F1", "F2", "F3", "I1", "I2", "I3", "I4"))
        train.add_argument("--checkpoint")
        train.add_argument("--forward-checkpoint")
        train.add_argument("--package-id")
        train.add_argument("--steps", type=int, default=256)
        train.add_argument("--seed", type=int, default=17)
        train.add_argument("--device", choices=("mps", "cpu"), default="mps")
        train.add_argument("--micro-batch", type=int, default=8)
        train.add_argument("--validation-interval", type=int, default=32)
        train.add_argument("--physical-parity-receipt")
        train.add_argument("--deadline-utc")
        train.add_argument("--preserve-normalizer", action="store_true")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare-data":
            from .data import prepare_data
            return prepare_data(args.source_manifest, args.out, args.previous_splits, args.seed)
        from .training import TrainConfig, train
        if args.command in ("resume", "finetune"):
            if not args.checkpoint:
                raise ValueError("resume/finetune requires checkpoint")
            prior = load_checkpoint(args.checkpoint)
            allowed = {field.name for field in fields(TrainConfig)}
            cfg = {key: value for key, value in prior["train_config"].items() if key in allowed}
            cfg.update(steps=args.steps, device=args.device, micro_batch=args.micro_batch,
                       validation_interval=args.validation_interval, deadline_utc=args.deadline_utc)
            if args.forward_checkpoint:
                cfg["forward_checkpoint"] = args.forward_checkpoint
            if args.physical_parity_receipt:
                cfg.update(physical_ready=True, physical_parity_receipt=args.physical_parity_receipt)
        else:
            if not args.kind:
                raise ValueError("training requires kind")
            role = "forward" if args.command == "train-forward" else "inverse"
            if (role == "forward") != args.kind.startswith("F"):
                raise ValueError("model kind does not match training role")
            cfg = dict(role=role, kind=args.kind, steps=args.steps, seed=args.seed,
                       device=args.device, micro_batch=args.micro_batch,
                       validation_interval=args.validation_interval, deadline_utc=args.deadline_utc,
                       forward_checkpoint=args.forward_checkpoint, package_id=args.package_id,
                       physical_ready=bool(args.physical_parity_receipt),
                       physical_parity_receipt=args.physical_parity_receipt)
        return train(args.data, args.out, TrainConfig(**cfg), args.contract,
                     resume_checkpoint=args.checkpoint if args.command == "resume" else None,
                     finetune_checkpoint=args.checkpoint if args.command == "finetune" else None,
                     preserve_normalizer=args.preserve_normalizer)
    except Exception as exc:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        # A failed attempt is permanent evidence; never reuse its output path.
        failure = out / "FAILED.json"
        if not failure.exists():
            save_json(failure, {"status": "FAILED", "created_utc": utc_now(),
                               "error_type": type(exc).__name__, "error": str(exc),
                               "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
