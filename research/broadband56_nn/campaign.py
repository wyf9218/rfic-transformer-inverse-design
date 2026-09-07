"""One bounded BB research sequence; no production or scheduler side effects."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import traceback

from .io import save_json, utc_now, sha256, read_json
from .models import PACKAGE_MAPPING
from .training import TrainConfig, train


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--physical-parity-receipt", required=True)
    parser.add_argument("--forward-steps", type=int, default=256)
    parser.add_argument("--inverse-steps", type=int, default=128)
    parser.add_argument("--deadline-utc", required=True)
    parser.add_argument("--device", default="mps", choices=("mps", "cpu"))
    parser.add_argument("--micro-batch", type=int, default=8)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=False)
    save_json(root / "CAMPAIGN_PLAN.json", {
        "created_utc": utc_now(), "pid": os.getpid(), "status": "STARTING",
        "data": str(Path(args.data).resolve()), "contract_sha256": sha256(args.contract),
        "physics_parity_sha256": sha256(args.physical_parity_receipt),
        "forward_updates_each": args.forward_steps, "inverse_updates_each": args.inverse_steps,
        "effective_batch": 32, "micro_batch": args.micro_batch, "seed": 17,
        "reference_seed": 29, "independent_reference": "F1 architecture, separate seed, never inverse gradient",
        "mapping": PACKAGE_MAPPING, "deadline_utc": args.deadline_utc,
        "production_modified": False, "real_emx_validation": "NOT_RUN",
        "comparison": "common data, split, target generator, loss and update budget; not equal parameter/compute",
        "selection": "validation only; test sealed until explicit configuration freeze",
        "prior_P01_P06": "not overwritten or renamed", "training_process_count": 1,
    })
    forwards, results = {}, {}
    for label, kind, seed in (("F1", "F1", 17), ("F2", "F2", 17),
                               ("F3", "F3", 17), ("FREF", "F1", 29)):
        destination = root / "shared_forward" / label
        config = TrainConfig("forward", kind, steps=args.forward_steps, seed=seed,
                             device=args.device, micro_batch=args.micro_batch,
                             deadline_utc=args.deadline_utc, package_id=label)
        try:
            result = train(args.data, destination, config, args.contract)
            forwards[label] = result["best_checkpoint"]
            results[label] = result
        except Exception as error:
            destination.mkdir(parents=True, exist_ok=True)
            failure = {"status": "FAILED", "error": str(error), "traceback": traceback.format_exc(), "created_utc": utc_now()}
            save_json(destination / "FAILED.json", failure)
            results[label] = failure
            print(json.dumps({"event": "stage_failed", "stage": label, **failure}), flush=True)
    for label, (forward_kind, inverse_kind) in PACKAGE_MAPPING.items():
        destination = root / label
        if forward_kind not in forwards:
            results[label] = {"status": "BLOCKED", "reason": "required forward did not complete"}
            save_json(destination / "BLOCKED.json", results[label])
            continue
        config = TrainConfig("inverse", inverse_kind, steps=args.inverse_steps, seed=17,
                             device=args.device, micro_batch=args.micro_batch,
                             physical_ready=True, physical_parity_receipt=args.physical_parity_receipt,
                             deadline_utc=args.deadline_utc, forward_checkpoint=forwards[forward_kind],
                             package_id=label)
        try:
            results[label] = train(args.data, destination, config, args.contract)
        except Exception as error:
            destination.mkdir(parents=True, exist_ok=True)
            failure = {"status": "FAILED", "error": str(error), "traceback": traceback.format_exc(), "created_utc": utc_now()}
            save_json(destination / "FAILED.json", failure)
            results[label] = failure
            print(json.dumps({"event": "stage_failed", "stage": label, **failure}), flush=True)
    save_json(root / "CAMPAIGN_RECEIPT.json", {"created_utc": utc_now(), "results": results,
               "all_six_trained": all(results[label]["status"] in ("PRETRAINED", "PRETRAINED_PARTIAL") for label in PACKAGE_MAPPING),
               "load_resume_check": "PENDING", "evaluation": "PENDING", "real_emx_validation": "NOT_RUN"})


if __name__ == "__main__":
    main()
