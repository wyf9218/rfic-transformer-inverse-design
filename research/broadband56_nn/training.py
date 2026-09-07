"""Bounded single-process research training; never invokes a simulator/controller."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import random
import time
import math
import os
import subprocess
import numpy as np
import torch

from .io import read_json, save_json, sha256, canonical_sha, save_checkpoint, load_checkpoint, utc_now
from .models import build_forward, build_inverse, architecture_config, parameter_counts
from .physics import GeometryDecoder, extract_physical, requested_physical_loss
from .specs import make_spec, tokenize, spectrum_loss, forward_loss


@dataclass
class TrainConfig:
    role: str
    kind: str
    steps: int = 256
    effective_batch: int = 32
    micro_batch: int = 8
    seed: int = 17
    device: str = "mps"
    threads: int = 2
    lr: float = 3e-4
    weight_decay: float = 1e-4
    gradient_clip: float = 1.0
    validation_interval: int = 32
    patience: int = 20
    max_epochs: int = 200
    physical_ready: bool = False
    physical_parity_receipt: str | None = None
    deadline_utc: str | None = None
    forward_checkpoint: str | None = None
    package_id: str | None = None


class Bundle:
    def __init__(self, root, normalizer=None):
        self.root = Path(root).resolve()
        self.manifest = read_json(self.root / "data_manifest.json")
        self.manifest_sha = sha256(self.root / "data_manifest.json")
        self.data_sha = sha256(self.root / "dataset.npz")
        for name, artifact in self.manifest.get("artifacts", {}).items():
            if sha256(self.root / artifact["path"]) != artifact["sha256"]:
                raise ValueError(f"prepared data artifact SHA mismatch: {name}")
        with np.load(self.root / "dataset.npz", allow_pickle=False) as source:
            self.arrays = {key: source[key] for key in source.files}
        self.norm = normalizer or read_json(self.root / "normalizer.json")
        self.norm_sha = canonical_sha(self.norm)
        self.train = np.flatnonzero(self.arrays["split"] == 0)
        self.val = np.flatnonzero(self.arrays["split"] == 1)
        if not len(self.train) or not len(self.val):
            raise ValueError("empty train/validation split")
        self.dim = self.arrays["geometry"].shape[1]
        self.frequency = torch.as_tensor(self.arrays["frequency_hz"], dtype=torch.float32)

    def batch(self, indices, device):
        keys = ("geometry", "s", "y", "y_valid")
        return {key: torch.as_tensor(self.arrays[key][indices],
                dtype=torch.bool if key == "y_valid" else torch.float32, device=device)
                for key in keys}

    def g_normalize(self, geometry):
        low = torch.as_tensor(self.norm["g_min"], dtype=geometry.dtype, device=geometry.device)
        high = torch.as_tensor(self.norm["g_max"], dtype=geometry.dtype, device=geometry.device)
        return 2 * (geometry - low) / (high - low).clamp_min(1e-12) - 1

    def denormalize_s(self, prediction):
        scale = torch.as_tensor(self.norm["s_scale"], dtype=prediction.dtype, device=prediction.device)
        mean = torch.as_tensor(self.norm["s_mean"], dtype=prediction.dtype, device=prediction.device)
        return prediction * scale + mean


def configure_device(config):
    torch.set_num_threads(config.threads)
    if config.device == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("requested MPS unavailable; no silent hardware change")
        torch.mps.set_per_process_memory_fraction(0.25)
    elif config.device != "cpu":
        raise ValueError("this local profile authorizes only CPU or MPS")
    return torch.device(config.device)


def model_digest(model):
    import hashlib
    digest = hashlib.sha256()
    for key, value in sorted(model.state_dict().items()):
        digest.update(key.encode())
        digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def decoder_from_contract(contract, device):
    names = contract["field_names"]
    return GeometryDecoder(names, contract["lower"], contract["upper"],
                           contract.get("topology_contract")).float().to(device)


def create_model(config, dim, device):
    factory = build_forward if config.role == "forward" else build_inverse
    return factory(config.kind, dim).to(device)


def load_forward(path, bundle, device):
    state = load_checkpoint(path)
    if state["role"] != "forward" or state["data_sha"] != bundle.data_sha:
        raise ValueError("forward role/snapshot mismatch")
    if state["normalizer_sha"] != bundle.norm_sha:
        raise ValueError("forward normalizer differs")
    model = build_forward(state["kind"], bundle.dim).to(device)
    model.load_state_dict(state["model_state"])
    model.eval().requires_grad_(False)
    return model, state


def requested_loss(raw_s, geometry, spec, bundle, decoder, contract):
    sm, ym = spec["s_mask"], spec["y_mask"]
    s_rows, y_rows = sm.any((1, 2)), ym.any((1, 2))
    loss = spectrum_loss(raw_s, spec, bundle.norm["s_scale"]).sum()
    if bool(y_rows.any()):
        prediction = extract_physical(raw_s[y_rows], bundle.frequency, contract["port_contract"])
        # Main PHYSICAL tasks use the strict-lumped source validity domain.
        prediction["valid"] = prediction["valid"] & prediction["strict_lumped_valid"][..., None]
        physical = requested_physical_loss(prediction, spec["y_target"][y_rows],
                    ym[y_rows], torch.as_tensor(bundle.norm["y_scale"], device=raw_s.device),
                    relation=spec["y_relation"][y_rows])
        loss = loss + physical * y_rows.sum()
    loss = loss / len(raw_s)
    feasibility = decoder.feasibility(geometry)["penalty"].mean()
    return loss + 0.1 * feasibility


def step_loss(model, batch, bundle, config, rng, decoder, contract, forward=None):
    if config.role == "forward":
        prediction = bundle.denormalize_s(model(bundle.g_normalize(batch["geometry"]), bundle.frequency))
        return forward_loss(prediction, batch["s"], bundle.norm["s_scale"])[0]
    spec = make_spec(batch["s"], batch["y"], batch["y_valid"], bundle.frequency, rng,
                     task=None if config.physical_ready else "SPECTRUM")
    tokens, mask = tokenize(spec, bundle.norm)
    geometry = decoder(model(tokens, mask))
    raw_s = bundle.denormalize_s(forward(bundle.g_normalize(geometry), bundle.frequency))
    return requested_loss(raw_s, geometry, spec, bundle, decoder, contract)


def validation_loss(model, bundle, config, decoder, contract, forward, device):
    # Identical fixed RNG/sorted geometry order on every validation call.
    model.eval()
    rng = np.random.default_rng(170029)
    total, count = 0.0, 0
    with torch.no_grad():
        for start in range(0, len(bundle.val), config.micro_batch):
            ix = bundle.val[start:start + config.micro_batch]
            loss = step_loss(model, bundle.batch(ix, device), bundle, config, rng,
                             decoder, contract, forward)
            total += float(loss.cpu()) * len(ix)
            count += len(ix)
    model.train()
    return total / count


def _rng_state(generator):
    state = {"python": random.getstate(), "numpy": np.random.get_state(),
             "sampler": generator.bit_generator.state, "torch": torch.get_rng_state()}
    if torch.backends.mps.is_available():
        state["mps"] = torch.mps.get_rng_state()
    return state


def _restore_rng(state, generator):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    generator.bit_generator.state = state["sampler"]
    torch.set_rng_state(state["torch"])
    if "mps" in state:
        torch.mps.set_rng_state(state["mps"])


def train(data_root, out_dir, config, contract_path, *, resume_checkpoint=None,
          finetune_checkpoint=None, preserve_normalizer=False):
    """Create one new run. Resume never appends into a historical run directory."""
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    config_dict = asdict(config)
    save_json(out / "TRAIN_REQUEST.json", {**config_dict, "created_utc": utc_now()})
    initial = load_checkpoint(resume_checkpoint or finetune_checkpoint) if (resume_checkpoint or finetune_checkpoint) else None
    if finetune_checkpoint and not preserve_normalizer:
        raise ValueError("finetune requires explicit preserve_normalizer; migration is not implicit")
    bundle = Bundle(data_root, initial["normalizer"] if finetune_checkpoint else None)
    contract = read_json(contract_path)
    source_pins = {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")}
    if contract["field_names"] != bundle.norm["field_names"]:
        raise ValueError("geometry field order mismatch")
    if config.effective_batch != 32 or 32 % config.micro_batch:
        raise ValueError("v2 first-run effective batch is exactly 32")
    if config.physical_ready:
        if not config.physical_parity_receipt:
            raise ValueError("physical training requires actual parity receipt")
        parity = read_json(config.physical_parity_receipt)
        if parity.get("status") != "PASS" or parity.get("data_sha") != bundle.data_sha:
            raise ValueError("physical parity receipt does not qualify this snapshot")
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    device = configure_device(config)
    rng = np.random.default_rng(config.seed)
    model = create_model(config, bundle.dim, device)
    decoder = decoder_from_contract(contract, device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    # Explicit constant scheduler, persisted and restored; no hidden LR restarts.
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1.0)
    begin, best, stale = 0, math.inf, 0
    if initial:
        if (initial["role"], initial["kind"]) != (config.role, config.kind):
            raise ValueError("resume/finetune architecture differs")
        model.load_state_dict(initial["model_state"])
        if resume_checkpoint:
            if initial["data_sha"] != bundle.data_sha or initial["normalizer_sha"] != bundle.norm_sha:
                raise ValueError("resume requires same snapshot and normalizer")
            if initial["contract_sha"] != canonical_sha(contract):
                raise ValueError("resume geometry/port contract differs")
            optimizer.load_state_dict(initial["optimizer_state"])
            scheduler.load_state_dict(initial["scheduler_state"])
            begin, best, stale = initial["step"], initial["best_validation"], initial["stale_validations"]
        elif initial["data_sha"] == bundle.data_sha:
            raise ValueError("same snapshot requires resume, not new-data finetune")
    forward = None
    forward_sha = None
    if config.role == "inverse":
        forward, _ = load_forward(config.forward_checkpoint, bundle, device)
        forward_sha = model_digest(forward)
        if resume_checkpoint and initial["forward_model_sha"] != forward_sha:
            raise ValueError("resume requires identical frozen forward")
    if resume_checkpoint:
        # Restore only AFTER all modules (including the frozen forward) exist.
        _restore_rng(initial["rng_state"], rng)
    save_json(out / "normalizer.json", bundle.norm)
    save_json(out / "contract.json", contract)
    save_json(out / "config.json", {**config_dict, "architecture": architecture_config(model),
              "data_sha": bundle.data_sha, "data_manifest_sha": bundle.manifest_sha,
              "normalizer_sha": bundle.norm_sha, "contract_sha": canonical_sha(contract),
              "forward_model_sha": forward_sha, "normalizer_policy": "preserved_prior" if finetune_checkpoint else "train_only",
              "optimizer_policy": "restored" if resume_checkpoint else "fresh",
              "max_epochs": 200, "training_budget_unit": "optimizer_updates",
              "sample_policy": "uniform train geometry with replacement; 32 examples/update",
              "loss_forward": "shared-scale S MSE + 0.1 adjacent-difference MSE; aux=0",
              "loss_inverse": "requested EQ normalized MSE + 0.1 analytical feasibility; anchor=0",
              "surrogate_qualification": "SURROGATE_NOT_QUALIFIED_FOR_PHYSICAL_CLAIMS",
              "runtime_source_sha256": source_pins,
              "real_emx_validation": "NOT_RUN"})
    history = []
    initial_digest = model_digest(model)
    total_steps = min(begin + config.steps, math.ceil(200 * len(bundle.train) / 32))
    deadline = datetime.fromisoformat(config.deadline_utc.replace("Z", "+00:00")) if config.deadline_utc else None
    last_step = begin
    last_checkpoint = best_checkpoint = None
    reason = "UPDATE_BUDGET_COMPLETE"
    first_grad_norm = None
    for step in range(begin + 1, total_steps + 1):
        if deadline and datetime.now(timezone.utc) >= deadline:
            reason = "TIME_BUDGET_PARTIAL"
            break
        model.train()
        optimizer.zero_grad(set_to_none=True)
        indices = rng.choice(bundle.train, size=32, replace=True)
        loss_total = 0.0
        for offset in range(0, 32, config.micro_batch):
            ix = indices[offset:offset + config.micro_batch]
            loss = step_loss(model, bundle.batch(ix, device), bundle, config, rng,
                             decoder, contract, forward)
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError(f"nonfinite loss at update {step}")
            (loss * len(ix) / 32).backward()
            loss_total += float(loss.detach().cpu()) * len(ix) / 32
        norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip, error_if_nonfinite=True)
        if float(norm.cpu()) == 0:
            raise FloatingPointError(f"zero trainable gradient at update {step}")
        if first_grad_norm is None:
            first_grad_norm = float(norm.cpu())
        optimizer.step()
        scheduler.step()
        last_step = step
        record = {"step": step, "train_loss": loss_total, "elapsed_seconds": time.monotonic() - start,
                  "validation_loss": None}
        # Validation at recorded common update intervals and final step only.
        if step % config.validation_interval == 0 or step == total_steps:
            val = validation_loss(model, bundle, config, decoder, contract, forward, device)
            if not math.isfinite(val):
                raise FloatingPointError("nonfinite validation objective")
            record["validation_loss"] = val
            improved = val < best
            if improved:
                best, stale = val, 0
            else:
                stale += 1
            history.append(record)
            state = {"schema": "bb_training_state.v1", "role": config.role, "kind": config.kind,
                     "step": step, "model_state": {k: v.detach().cpu() for k, v in model.state_dict().items()},
                     "optimizer_state": optimizer.state_dict(), "scheduler_state": scheduler.state_dict(),
                     "rng_state": _rng_state(rng), "amp_scaler": None,
                     "best_validation": best, "stale_validations": stale, "history": history,
                     "normalizer": bundle.norm, "normalizer_sha": bundle.norm_sha,
                     "data_root": str(bundle.root), "data_sha": bundle.data_sha,
                     "data_manifest_sha": bundle.manifest_sha, "contract": contract,
                     "contract_sha": canonical_sha(contract), "forward_checkpoint": config.forward_checkpoint,
                     "forward_model_sha": forward_sha, "train_config": config_dict,
                     "geometry_dim": bundle.dim, "architecture": architecture_config(model),
                     "runtime_source_sha256": source_pins,
                     "model_sha": model_digest(model), "created_utc": utc_now(),
                     "sampler_state": "included in rng_state.sampler"}
            last_checkpoint = out / f"checkpoint_step_{step:06d}.pt"
            save_checkpoint(last_checkpoint, state)
            if improved:
                best_checkpoint = last_checkpoint
            print(json.dumps({"event": "validation_checkpoint", "kind": config.kind,
                              "package": config.package_id, **record}), flush=True)
            if stale >= config.patience:
                reason = "VALIDATION_EARLY_STOP"
                break
        else:
            history.append(record)
        if step % 8 == 0:
            print(json.dumps({"event": "progress", "kind": config.kind,
                              "package": config.package_id, **record}), flush=True)
    if last_step == begin:
        raise RuntimeError("no update completed before deadline; not a trained package")
    # A deadline may fall between validation checkpoints: save the real final state.
    if last_checkpoint is None or not str(last_checkpoint).endswith(f"{last_step:06d}.pt"):
        val = validation_loss(model, bundle, config, decoder, contract, forward, device)
        state = {"schema": "bb_training_state.v1", "role": config.role, "kind": config.kind,
                 "step": last_step, "model_state": {k: v.detach().cpu() for k, v in model.state_dict().items()},
                 "optimizer_state": optimizer.state_dict(), "scheduler_state": scheduler.state_dict(),
                 "rng_state": _rng_state(rng), "amp_scaler": None, "best_validation": min(best, val),
                 "stale_validations": stale, "history": history, "normalizer": bundle.norm,
                 "normalizer_sha": bundle.norm_sha, "data_root": str(bundle.root),
                 "data_sha": bundle.data_sha, "data_manifest_sha": bundle.manifest_sha,
                 "contract": contract, "contract_sha": canonical_sha(contract),
                 "forward_checkpoint": config.forward_checkpoint, "forward_model_sha": forward_sha,
                 "train_config": config_dict, "geometry_dim": bundle.dim,
                 "architecture": architecture_config(model), "model_sha": model_digest(model),
                 "runtime_source_sha256": source_pins,
                 "created_utc": utc_now()}
        last_checkpoint = out / f"checkpoint_step_{last_step:06d}.pt"
        save_checkpoint(last_checkpoint, state)
        if val < best or best_checkpoint is None:
            best, best_checkpoint = val, last_checkpoint
    if forward is not None and model_digest(forward) != forward_sha:
        raise RuntimeError("frozen forward changed")
    final_digest = model_digest(model)
    if final_digest == initial_digest:
        raise RuntimeError("model weights did not change")
    if best_checkpoint is None:
        best_checkpoint = Path(resume_checkpoint) if resume_checkpoint else last_checkpoint
    status = "PRETRAINED_PARTIAL" if reason == "TIME_BUDGET_PARTIAL" else "PRETRAINED"
    receipt = {"status": status, "stop_reason": reason, "started_step": begin,
               "completed_step": last_step, "updates_this_run": last_step - begin,
               "gradient_geometry_exposures": (last_step - begin) * 32,
               "training_geometries": len(bundle.train), "validation_geometries": len(bundle.val),
               "test_access": False, "best_validation": best,
               "last_checkpoint": str(last_checkpoint), "last_sha256": sha256(last_checkpoint),
               "best_checkpoint": str(best_checkpoint), "best_sha256": sha256(best_checkpoint),
               "first_gradient_norm": first_grad_norm, "trainable_weights_changed": True,
               "frozen_forward_unchanged": forward is None or model_digest(forward) == forward_sha,
               "model_sha": final_digest, "forward_model_sha": forward_sha,
               "parameter_counts": parameter_counts(model), "elapsed_seconds": time.monotonic() - start,
               "data_sha": bundle.data_sha, "created_utc": utc_now(),
               "physical_accuracy": "NOT_ESTABLISHED", "real_emx_validation": "NOT_RUN",
               "load_resume_check": "PENDING_SEPARATE_PROCESS"}
    save_json(out / "history.json", history)
    save_json(out / "TRAINING_RECEIPT.json", receipt)
    print(json.dumps({"event": "training_terminal", **receipt}), flush=True)
    return receipt
