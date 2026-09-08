"""Mocked control-flow integration, not model, optimizer or formal study execution.

The separate revision module suite exercises the real resolver and publication.
Here a pinned synthetic resolver isolates parent/worker/package propagation.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import seven_suite as suite
from research.broadband56_nn import runtime_revision as revision
from research.broadband56_nn import study_once as once
from research.broadband56_nn.io import read_json

FUTURE = "2100-01-01T00:30:00+00:00"


def forbidden(*args, **kwargs):
    pytest.fail("real model/training/process/data/evaluation forbidden in metadata integration")


@pytest.fixture(autouse=True)
def no_real_execution(monkeypatch):
    from research.broadband56_nn import training, bb00
    monkeypatch.setattr(suite, "run_child", forbidden)
    monkeypatch.setattr(suite, "load_checkpoint", forbidden)
    monkeypatch.setattr(training, "train", forbidden)
    monkeypatch.setattr(training, "Bundle", forbidden)
    monkeypatch.setattr(bb00, "train_bb00", forbidden)


@pytest.fixture
def bound(tmp_path, monkeypatch):
    control = tmp_path / "control"
    root = control / once.study_key("synthetic")
    root.mkdir(parents=True)
    request = {"schema": "bb_seven_study_request.v1", "suite_version": suite.SUITE,
        "study_key": root.name, "campaign_id": "synthetic", "milestone_geometries": 10000,
        "control_root": str(control), "device": "cpu", "forward_steps": 32,
        "inverse_steps": 32, "runtime_contract": {"path": "SYNTHETIC_NOT_READ"},
        "legacy_replay": {"path": "SYNTHETIC_NOT_READ", "sha256": "synthetic"}}
    base = root / "experiment_plan.json"
    once.atomic_json(base, {**request, "software": "historical"}, immutable=True)
    active = tmp_path / "CANDIDATE.json"
    once.atomic_json(active, {**request, "software": "candidate"}, immutable=True)
    marker = root / revision.MARKER
    once.atomic_json(marker, {"synthetic_marker_only": True}, immutable=True)
    binding = {"base_plan": once.pin(base), "active_request": once.pin(active),
               "revision_marker": once.pin(marker)}
    def resolver(received_root, requested):
        assert Path(received_root).resolve() == root
        if once.pin(requested) != binding["active_request"]:
            raise ValueError("explicit active request required")
        return active, binding
    validations = []
    monkeypatch.setattr(revision, "resolve_active_request", resolver)
    monkeypatch.setattr(suite, "validate_request", lambda value: validations.append(value))
    data = tmp_path / "data"
    data.mkdir()
    once.atomic_json(data / "data_manifest.json", {"artifacts": {"dataset.npz": {"sha256": "synthetic"}}})
    return SimpleNamespace(root=root, control=control, base=base, active=active,
        request=read_json(active), binding=binding, data=data, validations=validations, tmp=tmp_path)


def attempt_request(bound, label="BB00_FORWARD", number=1):
    out = bound.root / "stages" / label / f"attempt_{number:04d}"
    argv = [suite.sys.executable, "-m", "research.broadband56_nn.seven_suite", "_train-worker",
        "--request", str(bound.active), "--request-sha256", bound.binding["active_request"]["sha256"],
        "--study-root", str(bound.root), "--label", label, "--out", str(out),
        "--data", str(bound.data), "--parity", "synthetic", "--deadline", FUTURE, "--steps", "32"]
    value = {"runtime_binding": bound.binding, "argv": argv,
             "spec": suite._stage_spec(label, bound.request)}
    path = out.parent / (out.name + "_REQUEST.json")
    once.atomic_json(path, value, immutable=True)
    return out, path


def test_parent_resolves_before_validation_and_keeps_original_plan(bound):
    original = bound.base.read_bytes()
    result = suite.check_once(bound.active, bound.control)
    assert result["status"] == "WAITING_FOR_10K"
    assert bound.validations == [bound.request]
    assert bound.base.read_bytes() == original
    assert not (bound.root / "TRAINING_BUDGET.json").exists()
    assert not (bound.root / "stages").exists()


def test_parent_cannot_fall_back_to_historical_request(bound):
    with pytest.raises(ValueError, match="explicit active"):
        suite.check_once(bound.base, bound.control)
    assert bound.validations == []
    assert not (bound.root / "events").exists()


@pytest.mark.parametrize("bad_key", ["../escape", "/absolute", "10k-foreign"])
def test_untrusted_key_rejected_before_directory_creation(tmp_path, bad_key):
    control = tmp_path / "control"
    request = tmp_path / "request.json"
    once.atomic_json(request, {"schema": "bb_seven_study_request.v1", "suite_version": suite.SUITE,
        "study_key": bad_key, "campaign_id": "synthetic", "milestone_geometries": 10000,
        "control_root": str(control)})
    with pytest.raises(ValueError, match="stable"):
        suite.check_once(request, control)
    assert not control.exists()


def test_missing_binding_rejects_dangling_marker(tmp_path):
    (tmp_path / revision.MARKER).symlink_to(tmp_path / "absent")
    with pytest.raises(ValueError, match="exact runtime binding"):
        suite._guard_runtime_binding(tmp_path, None)


def test_stage_parent_propagates_active_identity_and_adopts_once(bound, monkeypatch):
    calls = []
    def synthetic_child(argv, *args):
        calls.append(argv)
        out = Path(argv[argv.index("--out") + 1])
        once.atomic_json(out / "TRAINING_RECEIPT.json", {"synthetic": True}, immutable=True)
        once.atomic_json(out / "WORKER_RUNTIME_IDENTITY.json", {
            "runtime_binding": bound.binding, "training_receipt": once.pin(out / "TRAINING_RECEIPT.json")}, immutable=True)
        return {"returncode": 0, "synthetic": True}
    monkeypatch.setattr(suite, "run_child", synthetic_child)
    monkeypatch.setattr(suite, "_qualified_receipt", lambda *a: ({"synthetic": True}, True))
    args = (bound.root, "F1", bound.request, bound.data, bound.tmp / "parity", FUTURE, None, ())
    suite._train_stage(*args, runtime_binding=bound.binding)
    suite._train_stage(*args, runtime_binding=bound.binding)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[argv.index("--request") + 1] == str(bound.active)
    assert argv[argv.index("--request-sha256") + 1] == bound.binding["active_request"]["sha256"]
    saved = read_json(bound.root / "stages/F1/attempt_0001_REQUEST.json")
    done = read_json(bound.root / "stages/F1/STAGE_COMPLETE.json")
    assert saved["runtime_binding"] == done["runtime_binding"] == bound.binding


@pytest.mark.parametrize("fault", ["missing", "foreign_binding", "wrong_argv", "wrong_spec"])
def test_partial_attempt_rejected_before_checkpoint_load_or_child(bound, monkeypatch, fault):
    out, path = attempt_request(bound, "F1")
    out.mkdir()
    if fault == "missing":
        path.unlink()  # Synthetic tmp-path fault injection only.
    else:
        value = read_json(path)
        if fault == "foreign_binding":
            value["runtime_binding"] = {"foreign": True}
        elif fault == "wrong_argv":
            value["argv"][value["argv"].index("--request") + 1] = str(bound.base)
        else:
            value["spec"]["budget"] = 123
        once.atomic_json(path, value)
    monkeypatch.setattr(suite, "_resume_candidate", forbidden)
    with pytest.raises(ValueError, match="attempt"):
        suite._train_stage(bound.root, "F1", bound.request, bound.data, "parity", FUTURE,
                           None, (), runtime_binding=bound.binding)
    assert not (out.parent / "attempt_0002_REQUEST.json").exists()


def test_valid_partial_uses_originating_attempt_and_keeps_runtime(bound, monkeypatch):
    out, _ = attempt_request(bound, "F1")
    out.mkdir()
    checkpoint = out / "checkpoint_step_000016.pt"
    checkpoint.write_bytes(b"synthetic-not-torch")
    Path(str(checkpoint) + ".identity.json").write_text("{}")
    state = {"data_sha": "synthetic", "role": "forward", "kind": "F1", "step": 16,
             "train_config": {"seed": 17, "micro_batch": 8, "effective_batch": 32},
             "best_checkpoint": str(checkpoint)}
    monkeypatch.setattr(suite, "load_checkpoint", lambda p: state)
    step, selected, _ = suite._resume_candidate(out.parent, suite._stage_spec("F1", bound.request),
                                              "synthetic", runtime_binding=bound.binding)
    assert step == 16 and selected == checkpoint


def worker_args(bound):
    out, _ = attempt_request(bound)
    return SimpleNamespace(request=str(bound.active), study_root=str(bound.root),
        request_sha256=bound.binding["active_request"]["sha256"], out=str(out),
        label="BB00_FORWARD", steps=32, data=str(bound.data), forward=None,
        checkpoint=None, parity="synthetic", deadline=FUTURE)


def test_worker_uses_active_request_and_records_receipt_binding(bound, monkeypatch):
    from research.broadband56_nn import bb00
    args = worker_args(bound)
    calls = []
    def synthetic_train(data, out, config, *rest, **kwargs):
        calls.append(config)
        once.atomic_json(Path(out) / "TRAINING_RECEIPT.json", {"synthetic": True}, immutable=True)
        return {"synthetic": True}
    monkeypatch.setattr(bb00, "train_bb00", synthetic_train)
    assert suite.train_worker(args) == {"synthetic": True}
    assert len(calls) == 1 and calls[0].deadline_utc == FUTURE
    proof = read_json(Path(args.out) / "WORKER_RUNTIME_IDENTITY.json")
    assert proof["runtime_binding"] == bound.binding
    assert proof["training_receipt"] == once.pin(Path(args.out) / "TRAINING_RECEIPT.json")


@pytest.mark.parametrize("fault", ["old_request", "wrong_sha", "inferred_missing_sha", "inferred_wrong_out", "wrong_root"])
def test_formal_worker_rejects_identity_before_train(bound, fault):
    args = worker_args(bound)
    if fault == "old_request":
        args.request = str(bound.base)
    elif fault == "wrong_sha":
        args.request_sha256 = "wrong"
    elif fault == "inferred_missing_sha":
        args.study_root = args.request_sha256 = None
    elif fault == "inferred_wrong_out":
        args.study_root = None
        args.out = str(bound.tmp / "foreign")
    else:
        args.study_root = str(bound.tmp / "foreign")
    with pytest.raises(ValueError):
        suite.train_worker(args)


@pytest.mark.parametrize("name,value", [("data", "foreign-data"), ("parity", "foreign-parity"),
    ("deadline", "2101-01-01T00:00:00+00:00"), ("steps", 999),
    ("checkpoint", "foreign-resume.pt"), ("forward", "foreign-forward.pt")])
def test_formal_worker_cannot_change_parent_scientific_or_budget_arguments(bound, name, value):
    args = worker_args(bound)
    setattr(args, name, value)
    with pytest.raises(ValueError, match="arguments differ|forward differs"):
        suite.train_worker(args)
    assert not Path(args.out).exists()


def test_formal_worker_rejects_changed_parent_stage_spec(bound):
    args = worker_args(bound)
    path = Path(args.out).parent / "attempt_0001_REQUEST.json"
    value = read_json(path)
    value["spec"]["budget"] = 999
    once.atomic_json(path, value)
    with pytest.raises(ValueError, match="stage spec differs"):
        suite.train_worker(args)
    assert not Path(args.out).exists()


@pytest.mark.parametrize("entry", ["registry", "evaluate", "package"])
def test_downstream_rejects_foreign_binding_before_work(bound, entry):
    binding = {**bound.binding, "revision_marker": {"foreign": True}}
    with pytest.raises(ValueError, match="identity differs"):
        if entry == "registry":
            suite.build_registries(bound.root, bound.data, {}, runtime_binding=binding)
        elif entry == "evaluate":
            suite.evaluate_completed(bound.root, bound.request, runtime_binding=binding)
        else:
            suite.package_completed(bound.root, bound.request, runtime_binding=binding)


@pytest.mark.parametrize("command", ["prepare-runtime-revision", "activate-runtime-revision"])
def test_cli_revision_routes_without_training(monkeypatch, command):
    calls = []
    if command.startswith("prepare"):
        monkeypatch.setattr(revision, "prepare_revision", lambda *a: calls.append(a) or {"synthetic": True})
        keys = ("base-request", "base-sha256", "predecessor-index", "index-sha256", "out")
    else:
        monkeypatch.setattr(revision, "activate_revision", lambda *a, **kw: calls.append((a, kw)) or {"synthetic": True})
        keys = ("proposal", "proposal-sha256", "qa-receipt", "qa-sha256", "out")
    suite.main([command, *(v for key in keys for v in ("--" + key, "synthetic"))])
    assert len(calls) == 1
