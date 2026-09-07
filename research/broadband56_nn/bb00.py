"""v3 BB00: new-snapshot training of the historical 256x3 tandem structure.

Uses shared data/checkpoint/device/RNG primitives. Historical weights are NEVER
used by train_bb00. Replacement optimization is explicit, not legacy resume.
Public inference units: forward(geometry_um)->[Lp_nH,Ls_nH,Qmin,K_abs];
inverse(physical_15ghz)->geometry_um. No frequency input, S outputs or BB tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn

from .io import read_json, save_json, sha256, canonical_sha, save_checkpoint, load_checkpoint, utc_now
from .training import Bundle, configure_device, model_digest, _rng_state, _restore_rng
from rfic_transformer_inverse_design.synthesis.frozen_mlp import GEOMETRY_COLUMNS


@dataclass
class BB00Config:
    role: str
    steps: int = 128
    schedule_total_steps: int = 128
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
    forward_checkpoint: str | None = None
    deadline_utc: str | None = None
    response_spans: tuple = (2.5, 2.5, 20.0, 0.8)
    allow_io_adaptation: bool = False


class BB00MLP(nn.Module):
    def __init__(self, role, normalizer):
        super().__init__()
        if role not in ("forward", "inverse"):
            raise ValueError("BB00 role must be forward or inverse")
        self.role = role
        self.geometry_dim = len(normalizer["g_mean"])
        dimensions = [self.geometry_dim, 256, 256, 256, 4] if role == "forward" else [4, 256, 256, 256, self.geometry_dim]
        self.layers = nn.ModuleList(nn.Linear(a, b) for a, b in zip(dimensions[:-1], dimensions[1:]))
        for key in ("g_mean", "g_scale", "y_mean", "y_scale", "g_lower", "g_upper"):
            self.register_buffer(key, torch.as_tensor(normalizer[key], dtype=torch.float32))
        self.architecture = {"role": role, "widths": dimensions, "hidden_activation": "tanh_approximation_GELU",
             "geometry_projection": "independent_sigmoid_to_observed_valid_train_envelope", "frequency_ghz": 15.0,
             "input_units": "um" if role == "forward" else ["nH", "nH", "dimensionless_Qmin", "dimensionless_absK"],
             "output_units": ["nH", "nH", "dimensionless_Qmin", "dimensionless_absK"] if role == "forward" else "um"}

    def forward(self, values):
        dimension = self.geometry_dim if self.role == "forward" else 4
        if values.ndim != 2 or values.shape[-1] != dimension:
            raise ValueError("BB00 accepts native two-dimensional physical inputs only")
        x = (values-self.g_mean)/self.g_scale if self.role == "forward" else (values-self.y_mean)/self.y_scale
        for i, layer in enumerate(self.layers):
            x = layer(x)
            if i != len(self.layers)-1:
                # Exact expression used by frozen NumPy implementation.
                x = .5*x*(1+torch.tanh(math.sqrt(2/math.pi)*(x+.044715*x**3)))
        if self.role == "forward":
            return x*self.y_scale+self.y_mean
        return (self.g_lower+(self.g_upper-self.g_lower)*torch.sigmoid(x.clamp(-40, 40)))*self.g_scale+self.g_mean


def legacy_recipe(receipt_path, expected_sha):
    if sha256(receipt_path) != expected_sha:
        raise ValueError("legacy recipe receipt SHA mismatch")
    receipt = read_json(receipt_path)
    if receipt.get("status") != "PASS" or receipt.get("schema") != "bb_r0_replay.v1":
        raise ValueError("existing qualified historical replay required")
    architecture = receipt["architecture"]
    if (architecture.get("forward_surrogate") != [10, 256, 256, 256, 4] or
            architecture.get("inverse_mlp") != [4, 256, 256, 256, 10] or
            architecture.get("hidden_activation") != "gelu" or
            architecture.get("geometry_projection") != "sigmoid_to_training_envelope" or
            receipt["target_frequency_ghz"] != 15):
        raise ValueError("historical 256x3 native15GHz architecture not proven")
    loss = receipt["historical_loss_contract"]
    required = {"response_weight": 1.0, "geometry_anchor_weight": .01, "topology_feasibility_weight": 0.0,
                "response_loss_scaling": "declared_range", "response_loss_family": "mse",
                "response_weight_schedule": "warmup_ramp_adaptive_ema", "response_schedule_domain": "optimizer_update",
                "response_warmup_fraction": .05, "response_ramp_fraction": .25,
                "response_adaptive_ema_decay": .95, "response_adaptive_min_multiplier": .25, "response_adaptive_max_multiplier": 4.0}
    if any(loss.get(key) != value for key, value in required.items()):
        raise ValueError("historical loss recipe differs; do not silently approximate")
    summary_pin = receipt["sources"]["summary"]
    if sha256(summary_pin["path"]) != summary_pin["sha256"]:
        raise ValueError("historical summary source changed")
    summary = read_json(summary_pin["path"])
    if summary["model_comparison_contract"]["loss"] != loss:
        raise ValueError("recipe and summary disagree")
    trainer = Path(__file__).resolve().parents[2]/"scripts/train_physical_feature_tandem_inverse.py"
    return {"receipt_sha256": expected_sha, "model_id": receipt["model_id"], "source_summary_sha256": summary_pin["sha256"],
            "loss": required, "historical_optimizer": summary["model_comparison_contract"]["optimization"],
            "historical_trainer_identity": receipt["trainer_identity"],
            "schedule_formula_source": {"path": str(trainer), "sha256": sha256(trainer)},
            "reconstruction": "new PyTorch adapter of recorded architecture and located current trainer formulas; original trainer byte identity not assumed",
            "historical_optimizer_state": "NOT_LOADED; never legacy exact resume"}


def prepare_bb00(bundle, contract, spans, allow_io_adaptation=False):
    if contract["field_names"] != bundle.norm["field_names"]:
        raise ValueError("geometry field order differs")
    fields = [x.removeprefix("geom__") for x in contract["field_names"]]
    historical = [x.removeprefix("geom__") for x in GEOMETRY_COLUMNS]
    io_status = "MATCHED_10D_FIELD_ORDER" if fields == historical else "IO_ADAPTED"
    if io_status == "IO_ADAPTED" and not allow_io_adaptation:
        raise ValueError("geometry IO adaptation requires explicit configuration")
    frequencies = np.flatnonzero(bundle.arrays["frequency_hz"] == 15e9)
    if len(frequencies) != 1:
        raise ValueError("exact single15GHz source frequency required")
    frequency = int(frequencies[0])
    valid = bundle.arrays["y_valid"][:, frequency].all(-1) & np.isfinite(bundle.arrays["y"][:, frequency]).all(-1)
    train = bundle.train[valid[bundle.train]]
    val = bundle.val[valid[bundle.val]]
    if len(train) < 2 or not len(val):
        raise ValueError("insufficient train/validation strict-valid15GHz labels")
    g, y = bundle.arrays["geometry"][train], bundle.arrays["y"][train, frequency]
    if not np.isfinite(g).all():
        raise ValueError("nonfinite train geometry")
    gs, ys = np.maximum(np.std(g, axis=0), 1e-6), np.maximum(np.std(y, axis=0), 1e-6)
    gm = np.mean(g, axis=0)
    normalized_g = (g-gm)/gs
    spans = np.asarray(spans, float)
    if spans.shape != (4,) or not np.isfinite(spans).all() or np.any(spans <= 0):
        raise ValueError("four positive predeclared response spans required")
    weights = (ys/spans)**2
    weights /= weights.mean()
    norm = {"g_mean": gm.tolist(), "g_scale": gs.tolist(), "y_mean": y.mean(0).tolist(), "y_scale": ys.tolist(),
            "g_lower": normalized_g.min(0).tolist(), "g_upper": normalized_g.max(0).tolist(),
            "response_spans": spans.tolist(), "response_dimension_weights": weights.tolist(),
            "fit_scope": "new snapshot strict-valid15GHz train only; old support NEVER filters rows",
            "field_names": contract["field_names"], "physical_features": ["Lp_nH", "Ls_nH", "Qmin", "K_abs"],
            "io_status": io_status}
    exposure = {part: {"split_geometries": int((bundle.arrays["split"] == code).sum()),
                      "eligible15ghz_geometries": int(((bundle.arrays["split"] == code) & valid).sum())}
                for part, code in (("train", 0), ("validation", 1), ("test", 2))}
    return norm, train, val, frequency, exposure


def load_bb00(checkpoint, device="cpu", expected_sha256=None):
    if expected_sha256 and sha256(checkpoint) != expected_sha256:
        raise ValueError("BB00 checkpoint SHA mismatch")
    state = load_checkpoint(checkpoint)
    if state.get("schema") != "bb00_training_state.v1" or state.get("kind") != "BB00":
        raise ValueError("not a new-data BB00 checkpoint; historical reference is separate")
    if canonical_sha(state["normalizer"]) != state["normalizer_sha"]:
        raise ValueError("BB00 normalizer identity mismatch")
    model = BB00MLP(state["role"], state["normalizer"]).to(device)
    model.load_state_dict(state["model_state"])
    if model.architecture != state["architecture"] or model_digest(model) != state["model_sha"]:
        raise ValueError("BB00 model identity mismatch")
    model.eval()
    return model, state


def load_historical_torch(package, expected_manifest_sha256, device="cpu"):
    """Port exact legacy bytes only for explicitly separate adapter acceptance.

    Not an initializer accepted by train_bb00; no optimizer state is inferred.
    Float64 is retained for comparing against the original NumPy runtime.
    """
    from .baseline_package import load_package
    historical = load_package(package, expected_manifest_sha256=expected_manifest_sha256)
    arrays = historical.model.arrays
    norm = {"g_mean": arrays["y_mean"], "g_scale": arrays["y_scale"],
            "y_mean": arrays["x_mean"], "y_scale": arrays["x_scale"],
            "g_lower": arrays["geometry_lower"], "g_upper": arrays["geometry_upper"]}
    forward, inverse = BB00MLP("forward", norm).double(), BB00MLP("inverse", norm).double()
    with torch.no_grad():
        for model in (forward, inverse):
            for key, values in norm.items():
                getattr(model, key).copy_(torch.as_tensor(values, dtype=torch.float64))
            for layer, weight, bias in zip(model.layers, arrays[model.role+"_weights"], arrays[model.role+"_biases"]):
                if layer.weight.shape != weight.T.shape or layer.bias.shape != bias.shape:
                    raise ValueError("legacy actual arrays do not match256x3 structure")
                layer.weight.copy_(torch.as_tensor(weight.T, dtype=torch.float64))
                layer.bias.copy_(torch.as_tensor(bias, dtype=torch.float64))
            model.eval().requires_grad_(False).to(device)
    return forward, inverse, historical


def historical_adapter_parity(package, expected_manifest_sha256, receipt_path):
    """Eight synthetic in-support probes; not a historical evaluation rerun.

    Tests new Torch port against original NumPy output and NumPy central input
    derivatives. No original train/validation/test targets are evaluated.
    """
    import sys
    import warnings
    torch.set_num_threads(2)
    forward, inverse, historical = load_historical_torch(package, expected_manifest_sha256)
    model = historical.model
    runtime = sys.modules[type(model).__module__]
    rng = np.random.default_rng(170031)
    targets = model.arrays["x_mean"] + rng.uniform(-.05,.05,(8,4))*model.arrays["x_scale"]
    targets = np.clip(targets, model.support_lower+.01*(model.support_upper-model.support_lower),
                      model.support_upper-.01*(model.support_upper-model.support_lower))
    geometry = model.arrays["y_mean"]+rng.uniform(-.05,.05,(8,10))*model.arrays["y_scale"]

    def numpy_forward(g):
        g=(g-model.arrays["y_mean"])/model.arrays["y_scale"]
        return runtime._predict(g,model.arrays["forward_weights"],model.arrays["forward_biases"])*model.arrays["x_scale"]+model.arrays["x_mean"]

    checks = {}
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        for name, module, values, numpy_call in (("forward",forward,geometry,numpy_forward),
                   ("inverse",inverse,targets,lambda y:model.predict(y).geometry)):
            x=torch.tensor(values,dtype=torch.float64,requires_grad=True)
            output=module(x)
            reference=numpy_call(values)
            gradient=torch.autograd.grad(output.sum(),x)[0].numpy()
            numeric=np.zeros_like(values)
            for column in range(values.shape[1]):
                step=1e-5*max(1.,float(np.max(np.abs(values[:,column]))))
                upper,lower=values.copy(),values.copy()
                upper[:,column]+=step
                lower[:,column]-=step
                numeric[:,column]=(numpy_call(upper).sum(1)-numpy_call(lower).sum(1))/(2*step)
            finite=np.isfinite(reference).all() and np.isfinite(output.detach().numpy()).all() and np.isfinite(gradient).all() and np.isfinite(numeric).all()
            checks[name]={"finite":bool(finite),"output_max_abs_difference":float(np.max(np.abs(output.detach().numpy()-reference))),
                "output_pass":bool(np.allclose(output.detach().numpy(),reference,atol=2e-10,rtol=2e-12)),
                "input_gradient_max_abs_difference":float(np.max(np.abs(gradient-numeric))),
                "input_gradient_pass":bool(np.allclose(gradient,numeric,atol=2e-5,rtol=2e-5))}
    result={"schema":"bb00_historical_torch_adapter_parity.v1", "status":"PASS" if all(v["finite"] and v["output_pass"] and v["input_gradient_pass"] for v in checks.values()) else "FAIL",
       "package_manifest_sha256":expected_manifest_sha256,"bb00_adapter_sha256":sha256(__file__),
       "probes":8,"probe_source":"deterministic synthetic perturbations about historical train normalizer means; no dataset targets loaded",
       "dtype":"NumPy float64 / Torch CPU float64","seed":170031,"checks":checks,
       "output_tolerance":{"atol":2e-10,"rtol":2e-12},"input_gradient_tolerance":{"atol":2e-5,"rtol":2e-5},
       "finite_difference":"central derivative of sum of native output coordinates, epsilon=1e-5*max(1,max_abs_input_column)",
       "runtime_warnings":[{"category":x.category.__name__,"message":str(x.message)} for x in observed],
       "historical_evaluation_repeated":False,"legacy_optimizer_resume":"NOT_PROVEN; optimizer state not loaded",
       "new10k_training_performed":False,"real_emx_validation":"NOT_RUN","created_utc":utc_now()}
    save_json(receipt_path,result)
    return result


def _schedule(total):
    warmup, ramp = max(1, round(total*.05)), max(1, round(total*.25))
    if warmup+ramp >= total:
        ramp = max(0, total-warmup-1)
    return {"total_units": total, "warmup_units": warmup, "ramp_units": ramp,
            "ema_response_mse": None, "ema_geometry_mse": None, "reference_loss_ratio": None}


def _response_weight(step, state):
    warm, ramp = state["warmup_units"], state["ramp_units"]
    if step <= warm:
        return 0.0
    if ramp and step <= warm+ramp:
        return (step-warm)/ramp
    if state["reference_loss_ratio"] is None or state["reference_loss_ratio"] <= 0:
        return 1.0
    ratio = state["ema_geometry_mse"]/max(state["ema_response_mse"], 1e-18)
    return float(np.clip(ratio/state["reference_loss_ratio"], .25, 4))


def _update_ema(state, response, geometry, step):
    for key, value in (("ema_response_mse", response), ("ema_geometry_mse", geometry)):
        state[key] = value if state[key] is None else .95*state[key]+.05*value
    if step >= state["warmup_units"]+state["ramp_units"] and state["reference_loss_ratio"] is None:
        state["reference_loss_ratio"] = state["ema_geometry_mse"]/max(state["ema_response_mse"], 1e-18)


def _components(model, forward, g, y, norm):
    scale = torch.as_tensor(norm["y_scale"], dtype=y.dtype, device=y.device)
    weight = torch.as_tensor(norm["response_dimension_weights"], dtype=y.dtype, device=y.device)
    if model.role == "forward":
        return (((model(g)-y)/scale).square()*weight).mean(), torch.zeros((), device=y.device)
    geometry = model(y)
    response = (((forward(geometry)-y)/scale).square()*weight).mean()
    gs = torch.as_tensor(norm["g_scale"], dtype=g.dtype, device=g.device)
    return response, ((geometry-g)/gs).square().mean()


def train_bb00(data_root, out, config, contract_path, legacy_replay_receipt,
               expected_legacy_replay_sha256, *, resume_checkpoint=None,
               resume_best_checkpoint=None, resume_best_checkpoint_sha256=None,
               resume_probe=False):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        return _train(data_root, out, config, contract_path, legacy_replay_receipt,
                      expected_legacy_replay_sha256, resume_checkpoint,
                      resume_best_checkpoint, resume_best_checkpoint_sha256, resume_probe)
    except Exception as exc:
        save_json(out/"TRAINING_FAILED.json", {"status": "FAILED", "error": str(exc), "created_utc": utc_now(),
                   "historical_weights_loaded": False, "production_modified": False})
        raise


def _train(data_root, out, config, contract_path, legacy_path, legacy_sha, resume, resume_best, resume_best_sha, resume_probe):
    start = time.monotonic()
    if config.role not in ("forward", "inverse") or config.steps < 1 or config.schedule_total_steps < 1:
        raise ValueError("positive bounded BB00 training configuration required")
    if config.effective_batch != 32 or config.micro_batch < 1 or 32 % config.micro_batch:
        raise ValueError("effective batch32 with divisor microbatch required")
    if config.validation_interval < 1 or config.patience < 1 or config.max_epochs != 200:
        raise ValueError("invalid common validation/epoch budget")
    initial = load_checkpoint(resume) if resume else None
    if initial:
        if initial.get("resume_probe") or initial.get("research_comparison_eligible") is False:
            raise ValueError("diagnostic resume descendants cannot seed the research comparison")
        # Persisted recipe is sufficient after relocation: never follow original
        # summary/trainer paths embedded in a copied historical receipt.
        if sha256(legacy_path) != legacy_sha or initial["recipe"]["receipt_sha256"] != legacy_sha:
            raise ValueError("resume legacy recipe identity mismatch")
        recipe = initial["recipe"]
    else:
        recipe = legacy_recipe(legacy_path, legacy_sha)
    bundle = Bundle(data_root)
    contract = read_json(contract_path)
    if "contract_bounds_um" in bundle.norm:
        if any(not np.array_equal(contract[key], bundle.norm["contract_bounds_um"][key]) for key in ("lower", "upper")):
            raise ValueError("new-data contract geometry bounds differ from accepted snapshot")
    if "port_contract" in bundle.manifest:
        if any(contract.get("port_contract", {}).get(key) != bundle.manifest["port_contract"].get(key)
               for key in ("port_order", "reference_impedance_ohm", "mode", "internal_permutation")):
            raise ValueError("new-data port contract differs from accepted snapshot")
    norm, train, val, frequency, exposure = prepare_bb00(bundle, contract, config.response_spans, config.allow_io_adaptation)
    norm_sha, contract_sha = canonical_sha(norm), canonical_sha(contract)
    sources = {name: sha256(Path(__file__).parent/name) for name in ("bb00.py", "training.py", "io.py")}
    fixed_config = {k: v for k, v in asdict(config).items() if k not in ("steps", "deadline_utc", "forward_checkpoint")}
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    device = configure_device(config)
    rng = np.random.default_rng(config.seed)
    model = BB00MLP(config.role, norm).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1.0)
    forward, fsha = None, None
    if config.role == "inverse":
        if not config.forward_checkpoint:
            raise ValueError("new-data trained forward checkpoint required")
        forward, fs = load_bb00(config.forward_checkpoint, device=device)
        fsha = sha256(config.forward_checkpoint)
        if (fs["role"] != "forward" or fs["data_sha"] != bundle.data_sha or fs["normalizer_sha"] != norm_sha or
                fs["contract_sha"] != contract_sha or fs["step"] < 1):
            raise ValueError("forward snapshot/normalizer/contract qualification mismatch")
        forward.eval().requires_grad_(False)
    initial_digest, forward_digest = model_digest(model), model_digest(forward) if forward is not None else None
    begin, best, stale, best_path, history, seen = 0, math.inf, 0, None, [], set()
    schedule = _schedule(config.schedule_total_steps)
    parent = None
    if resume:
        for key, expected in {"schema": "bb00_training_state.v1", "role": config.role, "kind": "BB00", "data_sha": bundle.data_sha,
             "normalizer_sha": norm_sha, "contract_sha": contract_sha, "forward_checkpoint_sha256": fsha,
             "fixed_train_config_sha": canonical_sha(fixed_config), "recipe_sha256": canonical_sha(recipe), "runtime_source_sha256": sources}.items():
            if initial.get(key) != expected:
                raise ValueError("BB00 exact resume mismatch: "+key)
        model.load_state_dict(initial["model_state"])
        if model_digest(model) != initial["model_sha"]:
            raise ValueError("resume model digest differs")
        optimizer.load_state_dict(initial["optimizer_state"])
        scheduler.load_state_dict(initial["scheduler_state"])
        begin, best, stale = initial["step"], initial["best_validation"], initial["stale_validations"]
        best_path = Path(resume_best or initial["best_checkpoint"])
        if bool(resume_best) != bool(resume_best_sha):
            raise ValueError("relocated best requires exact original SHA")
        if resume_best_sha and sha256(best_path) != resume_best_sha:
            raise ValueError("relocated BB00 best SHA mismatch")
        bs = load_checkpoint(best_path)
        if (bs["model_sha"] != initial["best_model_sha"] or bs["step"] > begin or bs["best_validation"] != best or
                any(bs.get(key) != initial.get(key) for key in ("role", "data_sha", "normalizer_sha", "contract_sha", "forward_checkpoint_sha256"))):
            raise ValueError("resume best identity mismatch")
        schedule, history, seen = initial["response_schedule_state"], initial["history"], set(initial["gradient_source_indices_seen"])
        _restore_rng(initial["rng_state"], rng)
        parent = {"path": str(Path(resume).resolve()), "sha256": sha256(resume)}
        initial_digest = model_digest(model)
    elif resume_best or resume_best_sha:
        raise ValueError("best relocation only applies to resume")
    if resume_probe and (not resume or config.steps != 1):
        raise ValueError("diagnostic resume probe requires exactly one update from existing state")
    end = begin+1 if resume_probe else min(begin+config.steps, config.schedule_total_steps, math.ceil(200*len(train)/32))
    if end <= begin:
        raise ValueError("frozen training horizon already exhausted")
    deadline = datetime.fromisoformat(config.deadline_utc.replace("Z", "+00:00")) if config.deadline_utc else None
    save_json(out/"config.json", {**asdict(config), "architecture": model.architecture, "normalizer_sha": norm_sha,
        "data_sha": bundle.data_sha, "contract_sha": contract_sha, "recipe": recipe, "eligible_rows": exposure,
        "initialization": "EXACT_NEW_ADAPTER_RESUME" if resume else "RANDOM_FROM_SCRATCH_NO_LEGACY_WEIGHTS",
        "resume_probe": resume_probe, "research_comparison_eligible": not resume_probe,
        "replacement_optimization": "AdamW, constant scheduler, uniform replacement batch32, gradient clipping; NOT historical optimizer replication",
        "loss_forward": "mean(standardized_response_error^2 * mean_normalized((train_y_scale/declared_span)^2))",
        "loss_inverse": "scheduled_weight * forward_response_MSE + 0.01 * standardized_geometry_anchor_MSE; topology=0",
        "validation_selection": "sqrt(global_weighted_response_MSE) + 0.01 * sqrt(global_geometry_MSE); no scheduled multiplier",
        "schedule_update": "validation-event EMA (located current trainer); frozen total update horizon; no validation gradient",
        "normalizer_policy": norm["fit_scope"], "runtime_source_sha256": sources, "parent_checkpoint": parent,
        "geometry_output": "independent observed valid-train envelope; coupled feasibility NOT guaranteed; evaluator must count failures",
        "test_labels_optimized": False, "test_access_scope": "validity count only, no test prediction/loss/model selection",
        "REAL_EMX_VALIDATION": "NOT_RUN"})
    save_json(out/"normalizer.json", norm)
    save_json(out/"contract.json", contract)

    def batch(indices):
        return (torch.as_tensor(bundle.arrays["geometry"][indices], dtype=torch.float32, device=device),
                torch.as_tensor(bundle.arrays["y"][indices, frequency], dtype=torch.float32, device=device))

    def validate():
        model.eval()
        rmse, gmse = 0.0, 0.0
        with torch.no_grad():
            for offset in range(0, len(val), config.micro_batch):
                indices = val[offset:offset+config.micro_batch]
                r, g = _components(model, forward, *batch(indices), norm)
                rmse += float(r.cpu())*len(indices)
                gmse += float(g.cpu())*len(indices)
        rmse, gmse = rmse/len(val), gmse/len(val)
        if not math.isfinite(rmse+gmse):
            raise FloatingPointError("nonfinite BB00 validation")
        return math.sqrt(rmse)+(.01*math.sqrt(gmse) if config.role == "inverse" else 0), rmse, gmse

    best_model_sha = initial["best_model_sha"] if resume else None
    last_path, last_step, first_gradient = None, begin, None
    stop = "UPDATE_BUDGET_COMPLETE"
    for step in range(begin+1, end+1):
        if deadline and datetime.now(timezone.utc) >= deadline:
            stop = "TIME_BUDGET_PARTIAL"
            break
        model.train()
        optimizer.zero_grad(set_to_none=True)
        indices = rng.choice(train, 32, replace=True)
        seen.update(map(int, indices))
        weight = _response_weight(step, schedule) if config.role == "inverse" else 1.0
        train_loss = 0.0
        for offset in range(0, 32, config.micro_batch):
            ix = indices[offset:offset+config.micro_batch]
            response, geometry = _components(model, forward, *batch(ix), norm)
            loss = weight*response + (.01*geometry if config.role == "inverse" else 0)
            if not torch.isfinite(loss):
                raise FloatingPointError("nonfinite BB00 training loss")
            (loss*len(ix)/32).backward()
            train_loss += float(loss.detach().cpu())*len(ix)/32
        gradient = float(torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip, error_if_nonfinite=True).cpu())
        if gradient <= 0:
            raise FloatingPointError("zero BB00 trainable gradient")
        first_gradient = gradient if first_gradient is None else first_gradient
        optimizer.step()
        scheduler.step()
        last_step = step
        record = {"step": step, "train_loss": train_loss, "response_weight": weight, "validation_loss": None,
                  "elapsed_seconds": time.monotonic()-start}
        history.append(record)
        # Every step is resumable; validation/selection only common frozen intervals.
        if step % config.validation_interval == 0 or step == config.schedule_total_steps or best_path is None:
            value, response, geometry = validate()
            record["validation_loss"] = value
            if config.role == "inverse":
                _update_ema(schedule, response, geometry, step)
            if value < best:
                best, stale = value, 0
                best_path = out/f"checkpoint_step_{step:06d}.pt"
                best_model_sha = model_digest(model)
            else:
                stale += 1
        if best_path is not None:
            last_path = out/f"checkpoint_step_{step:06d}.pt"
            state = {"schema": "bb00_training_state.v1", "role": config.role, "kind": "BB00", "step": step,
                "model_state": {k: v.detach().cpu() for k,v in model.state_dict().items()}, "model_sha": model_digest(model),
                "optimizer_state": optimizer.state_dict(), "scheduler_state": scheduler.state_dict(), "rng_state": _rng_state(rng),
                "response_schedule_state": schedule, "normalizer": norm, "normalizer_sha": norm_sha, "architecture": model.architecture,
                "data_sha": bundle.data_sha, "data_manifest_sha": bundle.manifest_sha, "data_root": str(bundle.root),
                "contract": contract, "contract_sha": contract_sha, "geometry_dim": bundle.dim,
                "split_by_geometry_sha256": dict(zip(bundle.arrays["geometry_sha256"].tolist(), bundle.arrays["split"].tolist())),
                "eligible_rows": exposure, "eligible_train_source_indices": train.tolist(), "gradient_source_indices_seen": sorted(seen),
                "gradient_draws": step*32, "epoch_equivalent": step*32/len(train), "history": history,
                "best_validation": best, "stale_validations": stale, "best_checkpoint": str(best_path), "best_model_sha": best_model_sha,
                "forward_checkpoint": config.forward_checkpoint, "forward_checkpoint_sha256": fsha, "forward_model_sha": forward_digest,
                "train_config": asdict(config), "fixed_train_config_sha": canonical_sha(fixed_config), "recipe": recipe,
                "recipe_sha256": canonical_sha(recipe), "runtime_source_sha256": sources, "parent_checkpoint": parent, "created_utc": utc_now(),
                "initialization": "EXACT_NEW_ADAPTER_RESUME" if resume else "RANDOM_FROM_SCRATCH_NO_LEGACY_WEIGHTS",
                "resume_probe": resume_probe, "research_comparison_eligible": not resume_probe,
                "historical_weights_loaded": False, "real_emx_validation": "NOT_RUN"}
            save_checkpoint(last_path, state)
        if stale >= config.patience:
            stop = "VALIDATION_EARLY_STOP"
            break
    if last_path is None or last_step == begin:
        raise RuntimeError("no resumable validated update completed; preserve partial files")
    if forward is not None and model_digest(forward) != forward_digest:
        raise RuntimeError("BB00 frozen forward changed")
    if model_digest(model) == initial_digest:
        raise RuntimeError("BB00 weights did not update")
    receipt = {"schema": "bb00_training_receipt.v1", "status": "SMOKE_TRAINED" if bundle.manifest.get("evidence") == "SYNTHETIC_TEST_ONLY" else "PARTIAL",
        "role": config.role, "kind": "BB00", "started_step": begin, "completed_step": last_step, "updates_this_run": last_step-begin,
        "best_checkpoint": str(best_path), "best_sha256": sha256(best_path), "last_checkpoint": str(last_path), "last_sha256": sha256(last_path),
        "best_validation": best, "trainable_weights_changed": True, "frozen_forward_unchanged": True,
        "model_sha": model_digest(model), "parameter_counts": {"total": sum(p.numel() for p in model.parameters())},
        "data_sha": bundle.data_sha, "normalizer_sha": norm_sha, "first_gradient_norm": first_gradient,
        "eligible_rows": exposure, "gradient_draws": last_step*32, "unique_gradient_geometries": len(seen),
        "test_access": False, "test_access_scope": "validity count only, no test prediction/loss/model selection",
        "elapsed_seconds": time.monotonic()-start, "stop_reason": stop, "load_resume_check": "PENDING_SEPARATE_PROCESS",
        "historical_weights_loaded": False, "real_emx_validation": "NOT_RUN", "created_utc": utc_now()}
    receipt.update(resume_probe=resume_probe, research_comparison_eligible=not resume_probe)
    save_json(out/"history.json", history)
    save_json(out/"TRAINING_RECEIPT.json", receipt)
    return receipt
