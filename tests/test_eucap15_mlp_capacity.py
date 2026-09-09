"""SYNTHETIC-only capacity/continuation checks, never paper or EMX evidence.

Eight-row training deliberately uses the existing 2-D IO_ADAPTED fixture;
the independent parameter/gradient checks use ten geometry dimensions.
Only this file is new. Tests run only when explicitly invoked by the parent.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import torch

from research.broadband56_nn import bb00
from research.broadband56_nn.frequency_tandem import training_config
from research.broadband56_nn.io import load_checkpoint, save_checkpoint, save_json, sha256
from research.broadband56_nn.training import model_digest
from tests.test_bb00 import inputs


WIDTHS = ((256, 256), (128, 128, 128), (256, 256, 256),
          (512, 512, 512), (256, 256, 256, 256, 256))
NAMES = ("2x256", "3x128", "3x256", "3x512", "5x256")
V = Path(__file__).resolve().parents[1]
OLD_BB00 = V.parent / "frequency-indexed-mlp-20260908/research/broadband56_nn/bb00.py"


def exact(actual, expected):
    """Recursive bit-exact state assertion, including every optimizer/RNG tensor."""
    if isinstance(expected, torch.Tensor):
        assert isinstance(actual, torch.Tensor)
        assert actual.dtype == expected.dtype and actual.shape == expected.shape
        assert torch.equal(actual, expected)
    elif isinstance(expected, np.ndarray):
        assert isinstance(actual, np.ndarray) and actual.dtype == expected.dtype
        assert np.array_equal(actual, expected)
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys()
        for key in expected:
            exact(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert type(actual) is type(expected) and len(actual) == len(expected)
        for a, b in zip(actual, expected):
            exact(a, b)
    else:
        assert actual == expected


def exact_continuation(resumed, whole):
    for field in ("model_state", "model_sha", "optimizer_state", "scheduler_state", "rng_state",
                  "response_schedule_state", "normalizer", "normalizer_sha", "architecture",
                  "data_sha", "contract_sha", "split_by_geometry_sha256", "eligible_rows",
                  "eligible_train_source_indices", "gradient_source_indices_seen", "gradient_draws",
                  "best_validation", "best_model_sha", "stale_validations", "fixed_train_config_sha",
                  "forward_checkpoint_sha256", "forward_model_sha", "step"):
        exact(resumed[field], whole[field])
    # Wall time, paths, and parent lineage intentionally differ; scientific
    # history (including validation-event EMA inputs) must not differ.
    for a, b in zip(resumed["history"], whole["history"]):
        exact({k: v for k, v in a.items() if k != "elapsed_seconds"},
              {k: v for k, v in b.items() if k != "elapsed_seconds"})
    assert len(resumed["history"]) == len(whole["history"]) == 3


def normalizer10():
    return {"g_mean": [float(i + 1) for i in range(10)], "g_scale": [1.0] * 10,
            "g_lower": [-1.0] * 10, "g_upper": [1.0] * 10,
            "y_mean": [1.0, 2.0, 10.0, 0.4], "y_scale": [0.2, 0.3, 2.0, 0.1],
            "frequency_ghz": 15, "label_mode": "STRICT_LUMPED"}


class SyntheticRuns:
    def __init__(self, root):
        self.root = root
        self.data, self.contract, self.recipe = inputs(root)
        self.recipe_sha = sha256(self.recipe)
        self.base = bb00.BB00Config("forward", steps=3, schedule_total_steps=3,
            device="cpu", threads=2, validation_interval=1, checkpoint_interval=1,
            allow_io_adaptation=True, micro_batch=8, log_progress=False)
        self.cached = {}
        self.common_forward = self.train("common_forward_default", self.base)
        self.common_forward_sha = sha256(self.common_forward["best_checkpoint"])

    def train(self, name, config, *, resume=None):
        return bb00.train_bb00(self.data, self.root / name, config, self.contract,
            self.recipe, self.recipe_sha, resume_checkpoint=resume)

    def run(self, hidden, role):
        key = (tuple(hidden), role)
        if key not in self.cached:
            name = NAMES[WIDTHS.index(tuple(hidden))] + "_" + role
            config = replace(self.base, role=role, hidden_layers=tuple(hidden),
                forward_checkpoint=self.common_forward["best_checkpoint"] if role == "inverse" else None)
            first = self.train(name + "_first1", replace(config, steps=1))
            resumed = self.train(name + "_resume2", replace(config, steps=2), resume=first["last_checkpoint"])
            whole = self.train(name + "_whole3", config)
            self.cached[key] = dict(config=config, first=first, resumed=resumed, whole=whole)
        return self.cached[key]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    torch.set_num_threads(2)
    return SyntheticRuns(tmp_path_factory.mktemp("SYNTHETIC_CAPACITY_NOT_PAPER"))


@pytest.mark.parametrize("hidden", WIDTHS, ids=NAMES)
def test_all_capacities_actual_forward_inverse_updates_load_and_exact_resume(runs, hidden):
    for role in ("forward", "inverse"):
        case = runs.run(hidden, role)
        first, resumed, whole = (case[k] for k in ("first", "resumed", "whole"))
        assert first["completed_step"] == 1 and resumed["started_step"] == 1
        assert resumed["completed_step"] == whole["completed_step"] == 3
        assert resumed["updates_this_run"] == 2 and resumed["trainable_weights_changed"]
        assert first["first_gradient_norm"] > 0 and resumed["first_gradient_norm"] > 0
        state = load_checkpoint(resumed["last_checkpoint"])
        uninterrupted = load_checkpoint(whole["last_checkpoint"])
        exact_continuation(state, uninterrupted)
        assert state["optimizer_state"]["state"]
        assert all(float(v["step"]) == 3 for v in state["optimizer_state"]["state"].values())
        assert state["model_sha"] != load_checkpoint(first["last_checkpoint"])["model_sha"]
        assert state["normalizer"]["io_status"] == "IO_ADAPTED"
        assert state["gradient_draws"] == 96 and state["historical_weights_loaded"] is False
        assert state["real_emx_validation"] == "NOT_RUN"
        assert state["architecture"]["widths"] == ([2, *hidden, 4] if role == "forward" else [4, *hidden, 2])
        assert tuple(state["train_config"]["hidden_layers"]) == hidden
        for path in (resumed["last_checkpoint"], whole["best_checkpoint"]):
            model, loaded = bb00.load_bb00(path, expected_sha256=sha256(path))
            assert model_digest(model) == loaded["model_sha"]
            exact(model.state_dict(), loaded["model_state"])
            values = torch.tensor([loaded["normalizer"]["g_mean" if role == "forward" else "y_mean"]])
            prediction = model(values)
            assert prediction.shape == (1, 4 if role == "forward" else 2)
            assert torch.isfinite(prediction).all()
        if role == "inverse":
            assert resumed["frozen_forward_unchanged"] is True
            assert state["forward_checkpoint_sha256"] == runs.common_forward_sha
    assert sha256(runs.common_forward["best_checkpoint"]) == runs.common_forward_sha


@pytest.mark.parametrize("hidden", WIDTHS, ids=NAMES)
def test_ten_dimensional_counts_and_common_forward_geometry_gradient(hidden):
    torch.set_num_threads(2)
    norm = normalizer10()
    torch.manual_seed(20260909)
    common = bb00.BB00MLP("forward", norm).eval().requires_grad_(False)
    common_sha = model_digest(common)
    for role in ("forward", "inverse"):
        model = bb00.BB00MLP(role, norm, hidden_layers=list(hidden))
        dims = [10, *hidden, 4] if role == "forward" else [4, *hidden, 10]
        expected = sum((a + 1) * b for a, b in zip(dims[:-1], dims[1:]))
        assert sum(p.numel() for p in model.parameters()) == expected
        assert model.architecture["widths"] == dims
        if hidden == (256, 256, 256):
            assert expected == (135428 if role == "forward" else 135434)
        if role == "inverse":
            target = torch.tensor([norm["y_mean"]], dtype=torch.float32, requires_grad=True)
            geometry = model(target)
            geometry.retain_grad()
            common(geometry).square().mean().backward()
            assert geometry.grad is not None and torch.isfinite(geometry.grad).all()
            assert float(geometry.grad.abs().sum()) > 0
            assert target.grad is not None and float(target.grad.abs().sum()) > 0
            assert sum(float(p.grad.abs().sum()) for p in model.parameters() if p.grad is not None) > 0
            assert all(p.grad is None for p in common.parameters())
            assert model_digest(common) == common_sha
            low = model.g_lower * model.g_scale + model.g_mean
            high = model.g_upper * model.g_scale + model.g_mean
            assert torch.all(geometry >= low) and torch.all(geometry <= high)


@pytest.fixture(scope="module")
def legacy_module():
    assert OLD_BB00.is_file(), "explicit original worktree BB00 is required for this parity check"
    name = "research.broadband56_nn._capacity_test_original_bb00"
    spec = importlib.util.spec_from_file_location(name, OLD_BB00)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("role", ["forward", "inverse"])
def test_default_architecture_initial_weights_outputs_gradients_match_original(legacy_module, role):
    before = sha256(OLD_BB00)
    norm = normalizer10()
    torch.manual_seed(2026090901)
    old = legacy_module.BB00MLP(role, norm)
    torch.manual_seed(2026090901)
    new = bb00.BB00MLP(role, norm)
    assert new.architecture == old.architecture
    exact(new.state_dict(), old.state_dict())
    center = norm["g_mean" if role == "forward" else "y_mean"]
    old_input = torch.tensor([center, [v + 0.01 for v in center]], requires_grad=True)
    new_input = old_input.detach().clone().requires_grad_()
    old_output, new_output = old(old_input), new(new_input)
    assert torch.equal(new_output, old_output)
    old_output.square().sum().backward()
    new_output.square().sum().backward()
    exact(new_input.grad, old_input.grad)
    for old_p, new_p in zip(old.parameters(), new.parameters()):
        exact(new_p.grad, old_p.grad)
    assert sha256(OLD_BB00) == before
    assert bb00.BB00Config("forward").hidden_layers == (256, 256, 256)


@pytest.mark.parametrize("hidden", [None, (), (256,), (256, 128, 256), (64, 64, 64),
    [256, 256, 256.0], [True, True, True], "256,256,256"])
def test_unsupported_or_non_integer_hidden_widths_rejected(hidden):
    with pytest.raises((ValueError, TypeError)):
        bb00.BB00MLP("forward", normalizer10(), hidden_layers=hidden)


@pytest.mark.parametrize("role", ["forward", "inverse"])
def test_no_frequency_input_or_wrong_input_rank_is_accepted(role):
    model = bb00.BB00MLP(role, normalizer10(), hidden_layers=(256, 256))
    dim = 10 if role == "forward" else 4
    for values in (torch.zeros(dim), torch.zeros(2, dim + 1), torch.zeros(1, 2, dim)):
        with pytest.raises(ValueError):
            model(values)


@pytest.mark.parametrize("forgery", ["widths", "activation", "train_config", "schema", "tensor"])
def test_loader_rejects_forged_capacity_or_state(runs, tmp_path, forgery):
    state = deepcopy(load_checkpoint(runs.common_forward["best_checkpoint"]))
    if forgery == "widths":
        state["architecture"]["widths"] = [2, 128, 128, 128, 4]
    elif forgery == "activation":
        state["architecture"]["hidden_activation"] = "relu"
    elif forgery == "train_config":
        state["train_config"]["hidden_layers"] = [256, 256]
    elif forgery == "schema":
        state["schema"] = "SYNTHETIC_UNSUPPORTED_SCHEMA"
    else:
        state["model_state"]["layers.0.weight"][0, 0] += 1.0
    target = tmp_path / "SYNTHETIC_FORGED.pt"
    save_checkpoint(target, state)
    with pytest.raises((ValueError, RuntimeError)):
        bb00.load_bb00(target)


@pytest.mark.parametrize("change", [{"hidden_layers": (128, 128, 128)}, {"micro_batch": 4},
                                  {"seed": 18}, {"lr": 1e-3}])
def test_resume_rejects_training_contract_drift(runs, tmp_path, change):
    source = runs.run((256, 256), "forward")
    config = replace(source["config"], steps=1, **change)
    with pytest.raises(ValueError, match="resume mismatch"):
        bb00.train_bb00(runs.data, tmp_path / "SYNTHETIC_REJECTED", config,
            runs.contract, runs.recipe, runs.recipe_sha, resume_checkpoint=source["first"]["last_checkpoint"])


def test_resume_rejects_forged_architecture_even_when_weights_and_config_unchanged(runs, tmp_path):
    source = runs.run((256, 256), "forward")
    state = deepcopy(load_checkpoint(source["first"]["last_checkpoint"]))
    state["architecture"]["hidden_activation"] = "relu"
    forged = tmp_path / "SYNTHETIC_FORGED_RESUME.pt"
    save_checkpoint(forged, state)
    with pytest.raises(ValueError, match="resume mismatch"):
        bb00.train_bb00(runs.data, tmp_path / "SYNTHETIC_REJECTED", replace(source["config"], steps=1),
            runs.contract, runs.recipe, runs.recipe_sha, resume_checkpoint=forged)


@pytest.mark.parametrize("forgery", ["actual_tensor", "fixed_train_config_sha", "resume_probe"])
def test_relocated_resume_best_requires_real_digest_and_original_metadata(runs, tmp_path, forgery):
    source = runs.run((256, 256), "forward")
    state = deepcopy(load_checkpoint(source["first"]["best_checkpoint"]))
    assert state["step"] == 1
    if forgery == "actual_tensor":
        # The checkpoint file SHA is genuine, but its claimed model digest is
        # deliberately unchanged after corrupting the actual saved tensor.
        state["model_state"]["layers.0.weight"][0, 0] += 1.0
    elif forgery == "fixed_train_config_sha":
        state["fixed_train_config_sha"] = "0" * 64
    else:
        state["resume_probe"] = True
    forged = tmp_path / "SYNTHETIC_FORGED_RELOCATED_BEST.pt"
    save_checkpoint(forged, state)
    with pytest.raises(ValueError, match="identity mismatch"):
        bb00.train_bb00(runs.data, tmp_path / "SYNTHETIC_REJECTED_RELOCATED_BEST",
            replace(source["config"], steps=1), runs.contract, runs.recipe, runs.recipe_sha,
            resume_checkpoint=source["first"]["last_checkpoint"],
            resume_best_checkpoint=str(forged), resume_best_checkpoint_sha256=sha256(forged))


def test_config_json_lists_and_schema_and_microbatch_guard(runs, tmp_path):
    for hidden in WIDTHS:
        config = training_config({"schema": "frequency_tandem_train.v1", "train": {
            "role": "forward", "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
            "hidden_layers": list(hidden)}})
        assert tuple(config.hidden_layers) == hidden
    with pytest.raises(ValueError):
        training_config({"schema": "SYNTHETIC_WRONG_SCHEMA", "train": {}})
    with pytest.raises(ValueError, match="microbatch"):
        bb00.train_bb00(runs.data, tmp_path / "SYNTHETIC_BAD_MICROBATCH",
            replace(runs.base, micro_batch=3, hidden_layers=(128, 128, 128)),
            runs.contract, runs.recipe, runs.recipe_sha)


def test_missing_hidden_config_loads_legacy_default_and_no_clobber(runs, tmp_path):
    state = deepcopy(load_checkpoint(runs.common_forward["best_checkpoint"]))
    state["train_config"].pop("hidden_layers")
    legacy_shaped = tmp_path / "SYNTHETIC_DEFAULT_PRE_CAPACITY.pt"
    save_checkpoint(legacy_shaped, state)
    model, loaded = bb00.load_bb00(legacy_shaped)
    assert model.architecture == state["architecture"]
    assert model_digest(model) == loaded["model_sha"]
    with pytest.raises(FileExistsError):
        bb00.train_bb00(runs.data, runs.root / "common_forward_default", runs.base,
            runs.contract, runs.recipe, runs.recipe_sha)


@pytest.mark.parametrize("field,value", [("resume_probe", True),
    ("research_comparison_eligible", False), ("best_model_sha", "0" * 64)])
def test_inverse_rejects_nonselected_or_diagnostic_common_forward(runs, tmp_path, field, value):
    state = deepcopy(load_checkpoint(runs.common_forward["best_checkpoint"]))
    state[field] = value
    source = tmp_path / "SYNTHETIC_INELIGIBLE_FORWARD.pt"
    save_checkpoint(source, state)
    config = replace(runs.base, role="inverse", steps=1, hidden_layers=(128, 128, 128),
        forward_checkpoint=str(source))
    with pytest.raises(ValueError, match="validation-selected, non-probe"):
        bb00.train_bb00(runs.data, tmp_path / "SYNTHETIC_REJECTED_INVERSE", config,
            runs.contract, runs.recipe, runs.recipe_sha)


@pytest.mark.parametrize("role,hidden", [("forward", (256, 256)), ("inverse", (128, 128, 128))])
def test_nondefault_real_frequency_cli_resume_in_fresh_process(runs, tmp_path, role, hidden):
    source = runs.run(hidden, role)
    document = {"schema": "frequency_tandem_train.v1", "data_root": str(runs.data),
        "contract_path": str(runs.contract), "legacy_replay_receipt": str(runs.recipe),
        "legacy_replay_sha256": runs.recipe_sha,
        "train": asdict(replace(source["config"], steps=2))}
    configuration = tmp_path / "SYNTHETIC_CLI_CONFIG.json"
    save_json(configuration, document)
    command = [sys.executable, "-B", "-m", "research.broadband56_nn.frequency_tandem", "train",
        "--config", str(configuration), "--out", str(tmp_path / "fresh_cli_resume"),
        "--resume", source["first"]["last_checkpoint"]]
    env = dict(os.environ, PYTHONPATH=str(V), PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="0",
        OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", NUMEXPR_NUM_THREADS="2")
    process = subprocess.Popen(command, cwd=V, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate(timeout=120)
    (tmp_path / "stdout.txt").write_text(stdout)
    (tmp_path / "stderr.txt").write_text(stderr)
    save_json(tmp_path / "SYNTHETIC_EXECUTION.json", {"command": command, "pid": process.pid,
        "parent_pid": os.getpid(), "returncode": process.returncode, "synthetic_only": True,
        "source_data": "8 synthetic rows; IO_ADAPTED; no paper results", "real_emx": "NOT_RUN"})
    assert process.pid != os.getpid() and process.returncode == 0, stdout + stderr
    receipt = json.loads(stdout.strip().splitlines()[-1])
    assert receipt["started_step"] == 1 and receipt["completed_step"] == 3
    resumed = load_checkpoint(receipt["last_checkpoint"])
    exact_continuation(resumed, load_checkpoint(source["whole"]["last_checkpoint"]))
    assert tuple(resumed["train_config"]["hidden_layers"]) == hidden
    model, _ = bb00.load_bb00(receipt["last_checkpoint"])
    assert model.architecture["widths"][1:-1] == list(hidden)
