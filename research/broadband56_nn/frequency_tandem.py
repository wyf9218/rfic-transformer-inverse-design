"""Frequency-routed Tandem MLPs using BB00 (default 256x3; explicit capacity option).

Frequency is a route, never a network input. This entry never invokes EMX,
changes production, fills absent frequencies, or promotes SELF_PROXY to truth.
All output directories are new; continuation explicitly names a saved state.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import torch

from .bb00 import BB00Config, load_bb00, prepare_bb00, train_bb00
from .io import read_json, sha256
from .training import Bundle


LABEL_MODES = ("STRICT_LUMPED", "POINTWISE_DESCRIPTOR_EXPERIMENTAL")


def training_config(document):
    if document.get("schema") != "frequency_tandem_train.v1":
        raise ValueError("frequency_tandem_train.v1 configuration required")
    raw = document["train"]
    for key in ("role", "frequency_ghz", "label_mode"):
        if key not in raw:
            raise ValueError("new frequency task requires explicit " + key)
    config = BB00Config(**{"steps": 12000, "schedule_total_steps": 12000,
                          "validation_interval": 100, "checkpoint_interval": 100,
                          "log_progress": True, **raw})
    if type(config.frequency_ghz) is not int or not 5 <= config.frequency_ghz <= 60:
        raise ValueError("integer GHz 5..60 required, no interpolation or nearest route")
    if config.label_mode not in LABEL_MODES:
        raise ValueError("explicit strict or descriptor mode required")
    return config


def train_from_config(config_path, out, *, resume_checkpoint=None, resume_probe=False):
    document = read_json(config_path)
    config = training_config(document)
    # BB00 retains optimizer/scheduler/RNG, response warmup/ramp/EMA, sampler,
    # valid-train normalizer, best/last and exact continuation source identity.
    return train_bb00(document["data_root"], out, config, document["contract_path"],
        document["legacy_replay_receipt"], document["legacy_replay_sha256"],
        resume_checkpoint=resume_checkpoint, resume_probe=resume_probe)


def inspect_training_data(config_path):
    document = read_json(config_path)
    config = training_config(document)
    bundle = Bundle(document["data_root"])
    norm, train, val, frequency, exposure = prepare_bb00(bundle, read_json(document["contract_path"]),
        config.response_spans, config.allow_io_adaptation,
        frequency_ghz=config.frequency_ghz, label_mode=config.label_mode)
    return {"schema": "frequency_training_data.v1", "frequency_ghz": config.frequency_ghz,
        "label_mode": config.label_mode, "source_unique_geometries": len(bundle.arrays["geometry"]),
        "frequency_index": frequency, "eligible_rows": exposure,
        "gradient_training_geometries": len(train), "validation_geometries": len(val),
        "geometry_fields": norm["field_names"], "normalizer": norm,
        "data_sha256": bundle.data_sha, "data_manifest_sha256": bundle.manifest_sha,
        "configuration": asdict(config), "trained": False, "REAL_EMX_VALIDATION": "NOT_RUN"}


def load_frequency_pair(forward_checkpoint, inverse_checkpoint, *, frequency_ghz,
                        label_mode, device="cpu"):
    if type(frequency_ghz) is not int or not 5 <= frequency_ghz <= 60 or label_mode not in LABEL_MODES:
        raise ValueError("unsupported exact integer-frequency/label-mode route")
    forward, fs = load_bb00(forward_checkpoint, device=device)
    inverse, ins = load_bb00(inverse_checkpoint, device=device)
    if fs["role"] != "forward" or ins["role"] != "inverse":
        raise ValueError("forward/inverse roles do not match")
    for state in (fs, ins):
        if (state["normalizer"].get("frequency_ghz", 15) != frequency_ghz or
                state["normalizer"].get("label_mode", "STRICT_LUMPED") != label_mode):
            raise ValueError("checkpoint route mismatch; no neighboring-frequency fallback")
    if (ins["forward_checkpoint_sha256"] != sha256(forward_checkpoint) or
            any(fs[key] != ins[key] for key in ("data_sha", "normalizer_sha", "contract_sha"))):
        raise ValueError("Tandem forward/inverse data or frozen-forward identity mismatch")
    forward.requires_grad_(False)
    inverse.requires_grad_(False)
    return forward, inverse, fs, ins


def infer(forward_checkpoint, inverse_checkpoint, targets, *, frequency_ghz, label_mode,
          allow_extrapolation=False, device="cpu"):
    torch.set_num_threads(2)
    forward, inverse, fs, ins = load_frequency_pair(forward_checkpoint, inverse_checkpoint,
        frequency_ghz=frequency_ghz, label_mode=label_mode, device=device)
    target = np.asarray(targets, dtype=np.float64)
    if target.ndim == 1:
        target = target[None, :]
    if target.ndim != 2 or target.shape[1] != 4 or not np.isfinite(target).all():
        raise ValueError("finite targets in [Lp_nH,Ls_nH,Q_scalar,K_abs] order required")
    norm = ins["normalizer"]
    if "train_support_min" not in norm or "train_support_max" not in norm:
        raise ValueError("checkpoint lacks explicit train support; do not invent a support domain")
    outside = ((target < np.asarray(norm["train_support_min"])) |
               (target > np.asarray(norm["train_support_max"]))).any(1)
    if outside.any() and not allow_extrapolation:
        raise ValueError("OUTSIDE_TRAIN_SUPPORT: explicit research extrapolation is required")
    with torch.no_grad():
        geometry = inverse(torch.as_tensor(target, dtype=torch.float32, device=device))
        predicted = forward(geometry)
    g, y = geometry.cpu().numpy(), predicted.cpu().numpy()
    if not np.isfinite(g).all() or not np.isfinite(y).all():
        raise FloatingPointError("nonfinite model output; no successful geometry reported")
    return {"schema": "frequency_tandem_prediction.v1", "frequency_ghz": frequency_ghz,
        "label_mode": label_mode, "feature_order": ["Lp_nH", "Ls_nH", "Q_scalar", "K_abs"],
        "feature_units": ["nH", "nH", "dimensionless", "dimensionless"],
        "geometry_fields": norm["field_names"], "geometry_units": "um",
        "targets": target.tolist(), "geometry": g.tolist(), "predicted_response": y.tolist(),
        "outside_train_marginal_support": outside.tolist(), "target_clipping": False,
        "support_caveat": "train marginal bounds do not establish joint feasibility",
        "forward_sha256": sha256(forward_checkpoint), "inverse_sha256": sha256(inverse_checkpoint),
        "forward_step": fs["step"], "inverse_step": ins["step"],
        "validation_source": "SELF_PROXY", "convergence_claim": "NOT_ESTABLISHED",
        "analytic_geometry_validation": "NOT_RUN", "REAL_EMX_VALIDATION": "NOT_RUN"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train")
    train.add_argument("--config", required=True)
    train.add_argument("--out", required=True)
    train.add_argument("--resume")
    train.add_argument("--resume-probe", action="store_true")
    inspect = commands.add_parser("inspect-data")
    inspect.add_argument("--config", required=True)
    predict = commands.add_parser("infer")
    predict.add_argument("--forward", required=True)
    predict.add_argument("--inverse", required=True)
    predict.add_argument("--frequency-ghz", type=int, required=True)
    predict.add_argument("--label-mode", choices=LABEL_MODES, required=True)
    predict.add_argument("--targets", required=True, help="JSON four-vector or array of four-vectors")
    predict.add_argument("--allow-extrapolation", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "train":
        result = train_from_config(args.config, args.out, resume_checkpoint=args.resume, resume_probe=args.resume_probe)
    elif args.command == "inspect-data":
        result = inspect_training_data(args.config)
    else:
        result = infer(args.forward, args.inverse, json.loads(args.targets), frequency_ghz=args.frequency_ghz,
            label_mode=args.label_mode, allow_extrapolation=args.allow_extrapolation)
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
