from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_deployed100k_exact_contract_on_200k.py"
)
EVALUATOR_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "evaluate_architecture_matched_fixed8k.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_deployed100k_exact_contract_on_200k_script", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_fake_observer(
    path: Path,
    *,
    blas_path: Path,
    returncode: int = 0,
    observed_steps: int = 5,
    finite_checks_pass: bool = True,
    delay_seconds: float = 0.0,
) -> None:
    path.write_text(
        f"""
import argparse, json, os, pathlib, sys, time
separator = sys.argv.index('--')
observer_argv = sys.argv[1:separator]
trainer_argv = sys.argv[separator + 1:]
p = argparse.ArgumentParser()
p.add_argument('--trainer-source', required=True)
p.add_argument('--expected-trainer-sha256', required=True)
p.add_argument('--expected-python-sha256', required=True)
p.add_argument('--expected-numpy-version', required=True)
p.add_argument('--expected-numpy-core-sha256', required=True)
p.add_argument('--expected-blas-sha256', required=True)
p.add_argument('--expected-thread-limit', required=True)
p.add_argument('--receipt', required=True)
p.add_argument('--observe-steps', required=True)
observer = p.parse_args(observer_argv)
t = argparse.ArgumentParser()
t.add_argument('--training-csv', required=True)
t.add_argument('--out-dir', required=True)
trainer = t.parse_args(trainer_argv)
time.sleep({delay_seconds!r})
out = pathlib.Path(trainer.out_dir)
out.mkdir(parents=True, exist_ok=True)
json.dump({{'OMP_NUM_THREADS': os.environ.get('OMP_NUM_THREADS')}}, open(out/'thread_env.json', 'w'))
print('trainer stdout', flush=True)
print('trainer stderr', file=sys.stderr, flush=True)
if {returncode} == 0:
    (out/'physical_feature_tandem_inverse_summary.json').write_text('{{"execution_status":"PASS"}}\\n')
    (out/'physical_feature_tandem_inverse_weights.npz').write_bytes(b'checkpoint')
    json.dump({{
        'status': 'PASS',
        'stages': {{
            'forward_proxy': {{'observed_steps': {observed_steps}}},
            'tandem_inverse': {{'observed_steps': {observed_steps}}},
        }},
        'runtime_identity': {{
            'loaded_blas_libraries': [{{
                'path': {str(blas_path)!r},
                'sha256': observer.expected_blas_sha256,
            }}],
        }},
        'runtime_checks': {{
            'parameters_finite': {finite_checks_pass!r},
            'gradients_finite': {finite_checks_pass!r},
            'adam_state_finite': {finite_checks_pass!r},
            'blas_library_sha256_exact_set': {finite_checks_pass!r},
        }},
    }}, open(observer.receipt, 'w'))
(out.parent/'trainer_finished.marker').write_text('finished\\n')
sys.exit({returncode})
""",
        encoding="utf-8",
    )


def _write_fake_evaluator(path: Path, *, returncode: int = 0) -> None:
    path.write_text(
        f"""
import argparse, hashlib, pathlib, sys
p = argparse.ArgumentParser()
p.add_argument('--reference-contract', required=True)
p.add_argument('--expected-reference-contract-sha256', required=True)
p.add_argument('--model-100k-id', required=True)
p.add_argument('--model-100k-summary', required=True)
p.add_argument('--model-100k-weights', required=True)
p.add_argument('--model-100k-trainer-source', required=True)
p.add_argument('--expected-model-100k-summary-sha256', required=True)
p.add_argument('--expected-model-100k-weights-sha256', required=True)
p.add_argument('--expected-model-100k-trainer-sha256', required=True)
p.add_argument('--model-200k-id', required=True)
p.add_argument('--model-200k-summary', required=True)
p.add_argument('--model-200k-weights', required=True)
p.add_argument('--model-200k-trainer-source', required=True)
p.add_argument('--expected-model-200k-summary-sha256', required=True)
p.add_argument('--expected-model-200k-weights-sha256', required=True)
p.add_argument('--expected-model-200k-trainer-sha256', required=True)
p.add_argument('--targets-json', required=True)
p.add_argument('--expected-targets-sha256', required=True)
p.add_argument('--out-dir', required=True)
a = p.parse_args()
def sha(path): return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
if sha(a.model_200k_summary) != a.expected_model_200k_summary_sha256: sys.exit(42)
if sha(a.model_200k_weights) != a.expected_model_200k_weights_sha256: sys.exit(43)
out = pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
names = [
    'per_target_100k_predictions.csv',
    'per_target_200k_predictions.csv',
    'architecture_matched_comparison.csv',
    'evaluation_summary.json',
]
for name in names: (out/name).write_text('verified legacy8k output\\n')
with (out/'SHA256SUMS.txt').open('w') as handle:
    for name in names: handle.write(f'{{sha(out/name)}}  {{name}}\\n')
print('evaluator stdout', flush=True)
print('evaluator stderr', file=sys.stderr, flush=True)
sys.exit({returncode})
""",
        encoding="utf-8",
    )


def _case(
    tmp_path: Path,
    *,
    trainer_rc: int = 0,
    evaluator_rc: int = 0,
    observed_steps: int = 5,
    finite_checks_pass: bool = True,
    trainer_delay: float = 0.0,
):
    module = _load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trainer = tmp_path / "scripts" / "trainer.py"
    trainer.parent.mkdir()
    trainer.write_text("# exact original trainer\n", encoding="utf-8")
    blas_library = tmp_path / "libblas_exact.dylib"
    blas_library.write_text("fake exact blas\n", encoding="utf-8")
    expected_blas_sha256 = _sha256(blas_library)
    observer = tmp_path / "finite_observer.py"
    _write_fake_observer(
        observer,
        blas_path=blas_library,
        returncode=trainer_rc,
        observed_steps=observed_steps,
        finite_checks_pass=finite_checks_pass,
        delay_seconds=trainer_delay,
    )
    trainer_helper = tmp_path / "rfic_transformer_inverse_design" / "model_splitting.py"
    trainer_helper.parent.mkdir()
    trainer_helper.write_text("# exact split helper\n", encoding="utf-8")
    evaluator = tmp_path / "evaluator.py"
    _write_fake_evaluator(evaluator, returncode=evaluator_rc)
    dataset = tmp_path / "source_200k.csv"
    targets = tmp_path / "fixed_legacy8k.json"
    reference_summary = tmp_path / "reference_100k_summary.json"
    reference_weights = tmp_path / "reference_100k_weights.npz"
    dataset.write_text("row_id,value\n1,2\n", encoding="utf-8")
    targets.write_text('{"target_count":8000}\n', encoding="utf-8")
    reference_summary.write_text('{"model_id":"reference"}\n', encoding="utf-8")
    reference_weights.write_bytes(b"reference-weights")
    python_executable = Path(sys.executable).resolve()
    numpy_core = Path(np._core._multiarray_umath.__file__).resolve()

    checkpoint_dir = run_dir / "checkpoints"
    evaluation_dir = run_dir / "evaluation"
    candidate_summary = checkpoint_dir / "physical_feature_tandem_inverse_summary.json"
    candidate_weights = checkpoint_dir / "physical_feature_tandem_inverse_weights.npz"
    finite_receipt = run_dir / "FINITE_OBSERVER_RECEIPT.json"
    trainer_argv_file = tmp_path / "trainer_argv.json"
    evaluator_argv_file = tmp_path / "evaluator_argv.json"
    trainer_argv = [
        str(python_executable),
        str(observer),
        "--trainer-source", str(trainer),
        "--expected-trainer-sha256", _sha256(trainer),
        "--expected-python-sha256", _sha256(python_executable),
        "--expected-numpy-version", np.__version__,
        "--expected-numpy-core-sha256", _sha256(numpy_core),
        "--expected-blas-sha256", expected_blas_sha256,
        "--expected-thread-limit", "2",
        "--receipt", str(finite_receipt),
        "--observe-steps", "5",
        "--",
        "--training-csv", str(dataset),
        "--out-dir", str(checkpoint_dir),
    ]
    _write_json(trainer_argv_file, {"schema": "exact_argv_v1", "argv": trainer_argv})

    reference = tmp_path / "REFERENCE.source.json"
    dataset_binding = tmp_path / "DATASET.source.json"
    _write_json(reference, {
        "schema": "test_reference_v1",
        "trainer": {"path": str(trainer), "sha256": _sha256(trainer)},
        "observer": {"path": str(observer), "sha256": _sha256(observer)},
        "trainer_helper": {"path": str(trainer_helper), "sha256": _sha256(trainer_helper)},
        "runtime_identity": {
            "numpy_version": np.__version__,
            "python": {"path": str(python_executable), "sha256": _sha256(python_executable)},
            "numpy_core": {"path": str(numpy_core), "sha256": _sha256(numpy_core)},
            "blas": {"path": str(blas_library), "sha256": expected_blas_sha256},
        },
        "summary": {"path": str(reference_summary), "sha256": _sha256(reference_summary)},
        "weights": {"path": str(reference_weights), "sha256": _sha256(reference_weights)},
        "trainer_argv": {"path": str(trainer_argv_file), "sha256": _sha256(trainer_argv_file)},
    })
    evaluator_argv = [
        str(python_executable), str(evaluator),
        "--reference-contract", str(reference),
        "--expected-reference-contract-sha256", _sha256(reference),
        "--model-100k-id", "current_foundry_qmin_response_only_seed20260713",
        "--model-100k-summary", str(reference_summary),
        "--expected-model-100k-summary-sha256", _sha256(reference_summary),
        "--model-100k-weights", str(reference_weights),
        "--expected-model-100k-weights-sha256", _sha256(reference_weights),
        "--model-100k-trainer-source", str(trainer),
        "--expected-model-100k-trainer-sha256", _sha256(trainer),
        "--model-200k-id", "test_architecture_matched_200k",
        "--model-200k-summary", str(candidate_summary),
        "--expected-model-200k-summary-sha256", "__MODEL_200K_SUMMARY_SHA256__",
        "--model-200k-weights", str(candidate_weights),
        "--expected-model-200k-weights-sha256", "__MODEL_200K_WEIGHTS_SHA256__",
        "--model-200k-trainer-source", str(trainer),
        "--expected-model-200k-trainer-sha256", _sha256(trainer),
        "--targets-json", str(targets),
        "--expected-targets-sha256", _sha256(targets),
        "--out-dir", str(evaluation_dir),
    ]
    _write_json(evaluator_argv_file, {"schema": "template_argv_v1", "argv": evaluator_argv})
    _write_json(dataset_binding, {
        "schema": "test_dataset_binding_v1",
        "source_table": {"path": str(dataset), "sha256": _sha256(dataset)},
        "targets": {"path": str(targets), "sha256": _sha256(targets)},
        "evaluator": {"path": str(evaluator), "sha256": _sha256(evaluator)},
        "evaluator_template": {
            "path": str(evaluator_argv_file), "sha256": _sha256(evaluator_argv_file)
        },
    })
    args = [
        "--run-dir", str(run_dir),
        "--reference-contract-json", str(reference),
        "--reference-contract-sha256", _sha256(reference),
        "--dataset-binding-json", str(dataset_binding),
        "--dataset-binding-sha256", _sha256(dataset_binding),
        "--trainer-path", str(trainer),
        "--trainer-sha256", _sha256(trainer),
        "--trainer-helper-path", str(trainer_helper),
        "--trainer-helper-sha256", _sha256(trainer_helper),
        "--python-path", str(python_executable),
        "--python-sha256", _sha256(python_executable),
        "--numpy-version", np.__version__,
        "--numpy-core-path", str(numpy_core),
        "--numpy-core-sha256", _sha256(numpy_core),
        "--blas-path", str(blas_library),
        "--blas-sha256", expected_blas_sha256,
        "--trainer-entrypoint-path", str(observer),
        "--trainer-entrypoint-sha256", _sha256(observer),
        "--dataset-path", str(dataset),
        "--dataset-sha256", _sha256(dataset),
        "--reference-summary-path", str(reference_summary),
        "--reference-summary-sha256", _sha256(reference_summary),
        "--reference-weights-path", str(reference_weights),
        "--reference-weights-sha256", _sha256(reference_weights),
        "--fixed-targets-path", str(targets),
        "--fixed-targets-sha256", _sha256(targets),
        "--trainer-argv-json", str(trainer_argv_file),
        "--trainer-argv-sha256", _sha256(trainer_argv_file),
        "--evaluator-path", str(evaluator),
        "--evaluator-sha256", _sha256(evaluator),
        "--evaluator-argv-json", str(evaluator_argv_file),
        "--evaluator-argv-sha256", _sha256(evaluator_argv_file),
        "--candidate-summary-path", str(candidate_summary),
        "--candidate-weights-path", str(candidate_weights),
        "--finite-observer-receipt", str(finite_receipt),
        "--thread-limit", "2",
    ]
    case = locals()
    return module, case, args


def _replace_arg(args: list[str], flag: str, value: str) -> None:
    args[args.index(flag) + 1] = value


def _refresh_manifest_arg(case: dict, args: list[str], manifest_key: str) -> None:
    flag = "--reference-contract-sha256" if manifest_key == "reference" else "--dataset-binding-sha256"
    _replace_arg(args, flag, _sha256(case[manifest_key]))


def _refresh_argv_binding(case: dict, args: list[str], which: str) -> None:
    argv_path = case[f"{which}_argv_file"]
    argv_sha = _sha256(argv_path)
    _replace_arg(args, f"--{which}-argv-sha256", argv_sha)
    if which == "trainer":
        manifest_key, record_key = "reference", "trainer_argv"
    else:
        manifest_key, record_key = "dataset_binding", "evaluator_template"
    payload = json.loads(case[manifest_key].read_text())
    payload[record_key] = {"path": str(argv_path), "sha256": argv_sha}
    _write_json(case[manifest_key], payload)
    _refresh_manifest_arg(case, args, manifest_key)


def _wait_for(path: Path, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists(): return True
        time.sleep(0.02)
    return path.exists()


def test_success_hash_gates_runtime_and_realizes_evaluation_template(tmp_path):
    module, c, args = _case(tmp_path)
    assert module.main(args) == 0
    status = json.loads((c["run_dir"] / "RUN_STATUS.json").read_text())
    launch = json.loads((c["run_dir"] / "LAUNCH_RECEIPT.json").read_text())
    training = json.loads((c["run_dir"] / "TRAINING_RECEIPT.json").read_text())
    evaluation = json.loads((c["run_dir"] / "EVALUATION_RECEIPT.json").read_text())
    complete = json.loads((c["run_dir"] / "COMPLETE_RECEIPT.json").read_text())
    assert status["overall_status"] == "PASS" and status["state"] == "COMPLETE"
    assert launch["trainer_helper_sha256"] == _sha256(c["trainer_helper"])
    assert launch["python_sha256"] == _sha256(c["python_executable"])
    assert launch["shell_used"] is False and launch["trainer_pid"] > 0
    finite = training["finite_observer_receipt"]
    assert finite["observed_steps"] == {"forward_proxy": 5, "tandem_inverse": 5}
    assert finite["runtime_checks_all_true"] is True
    assert finite["loaded_blas_sha256"] == [c["expected_blas_sha256"]]
    assert training["candidate_artifacts"]["summary"]["sha256"] == _sha256(c["candidate_summary"])
    realized_path = c["run_dir"] / "REALIZED_EVALUATION_ARGV.json"
    assert "__MODEL_200K_" not in realized_path.read_text()
    assert evaluation["realized_evaluator_argv_sha256"] == _sha256(realized_path)
    assert complete["finite_observer_receipt_sha256"] == _sha256(c["finite_receipt"])
    assert "[controller-heartbeat] utc=" in (c["run_dir"] / "train_stdout.log").read_text()
    assert status["train_stdout_log"]["size_bytes"] > 0


def test_evaluator_template_is_accepted_by_real_evaluator_parser(tmp_path):
    _, c, _ = _case(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "evaluate_architecture_matched_fixed8k_for_controller_test", EVALUATOR_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    evaluator_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = evaluator_module
    spec.loader.exec_module(evaluator_module)
    parsed = evaluator_module._parse_args(c["evaluator_argv"][2:])
    assert parsed.expected_model_100k_trainer_sha256 == _sha256(c["trainer"])
    assert parsed.expected_model_200k_trainer_sha256 == _sha256(c["trainer"])


def test_nonempty_run_dir_is_rejected_without_modification(tmp_path):
    module, c, args = _case(tmp_path)
    sentinel = c["run_dir"] / "existing.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    assert module.main(args) == 2
    assert list(c["run_dir"].iterdir()) == [sentinel]


def test_symlink_run_dir_is_rejected_before_resolution(tmp_path):
    module, c, args = _case(tmp_path)
    link = tmp_path / "run_link"
    link.symlink_to(c["run_dir"], target_is_directory=True)
    _replace_arg(args, "--run-dir", str(link))
    assert module.main(args) == 2
    assert list(c["run_dir"].iterdir()) == []


def test_dataset_hash_mismatch_fails_before_launch(tmp_path):
    module, c, args = _case(tmp_path)
    _replace_arg(args, "--dataset-sha256", "0" * 64)
    assert module.main(args) == 2
    assert not (c["run_dir"] / "LAUNCH_RECEIPT.json").exists()


def test_reference_weights_are_explicitly_hash_gated(tmp_path):
    module, c, args = _case(tmp_path)
    c["reference_weights"].write_bytes(b"tampered")
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "reference 100k weights SHA-256 mismatch" in failure["error"]


def test_manifest_argv_binding_partition_is_strict(tmp_path):
    module, c, args = _case(tmp_path)
    payload = json.loads(c["dataset_binding"].read_text())
    del payload["evaluator_template"]
    _write_json(c["dataset_binding"], payload)
    _refresh_manifest_arg(c, args, "dataset_binding")
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "dataset binding does not bind exact evaluator argv template" in failure["error"]


def test_strict_trainer_flag_value_rejects_duplicate(tmp_path):
    module, c, args = _case(tmp_path)
    payload = json.loads(c["trainer_argv_file"].read_text())
    payload["argv"].extend(["--training-csv", str(c["dataset"])])
    _write_json(c["trainer_argv_file"], payload)
    _refresh_argv_binding(c, args, "trainer")
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "flag must occur exactly once" in failure["error"]


def test_evaluator_placeholder_duplicate_is_rejected(tmp_path):
    module, c, args = _case(tmp_path)
    payload = json.loads(c["evaluator_argv_file"].read_text())
    payload["argv"].append("__MODEL_200K_SUMMARY_SHA256__")
    _write_json(c["evaluator_argv_file"], payload)
    _refresh_argv_binding(c, args, "evaluator")
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "placeholder must appear once" in failure["error"]


def test_finite_observer_insufficient_steps_blocks_evaluator(tmp_path):
    module, c, args = _case(tmp_path, observed_steps=4)
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "observed_steps is below 5" in failure["error"]
    assert not (c["run_dir"] / "EVALUATION_LAUNCH_RECEIPT.json").exists()


def test_finite_observer_false_runtime_check_blocks_evaluator(tmp_path):
    module, c, args = _case(tmp_path, finite_checks_pass=False)
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "runtime_checks are not all true" in failure["error"]


def test_trainer_failure_is_preserved_without_evaluator(tmp_path):
    module, c, args = _case(tmp_path, trainer_rc=7)
    assert module.main(args) == 2
    status = json.loads((c["run_dir"] / "RUN_STATUS.json").read_text())
    assert status["trainer_returncode"] == 7
    assert not (c["run_dir"] / "EVALUATION_LAUNCH_RECEIPT.json").exists()


def test_evaluator_failure_is_preserved_after_training_pass(tmp_path):
    module, c, args = _case(tmp_path, evaluator_rc=9)
    assert module.main(args) == 2
    status = json.loads((c["run_dir"] / "RUN_STATUS.json").read_text())
    assert status["state"] == "EVALUATION" and status["evaluator_returncode"] == 9


def test_entrypoint_tamper_is_rejected(tmp_path):
    module, c, args = _case(tmp_path)
    c["observer"].write_text("# tampered\n", encoding="utf-8")
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "trainer entrypoint SHA-256 mismatch" in failure["error"]


def test_evaluator_tamper_during_training_is_rejected_before_evaluation_launch(tmp_path):
    module, c, args = _case(tmp_path, trainer_delay=0.25)

    def tamper_after_launch() -> None:
        assert _wait_for(c["run_dir"] / "LAUNCH_RECEIPT.json")
        c["evaluator"].write_text("# tampered during training\n", encoding="utf-8")

    tamper = threading.Thread(target=tamper_after_launch)
    tamper.start()
    assert module.main(args) == 2
    tamper.join(timeout=1.0)
    assert not tamper.is_alive()
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "evaluator immediately before launch SHA-256 mismatch" in failure["error"]
    assert not (c["run_dir"] / "EVALUATION_LAUNCH_RECEIPT.json").exists()


def test_trainer_helper_tamper_is_rejected(tmp_path):
    module, c, args = _case(tmp_path)
    c["trainer_helper"].write_text("# tampered\n", encoding="utf-8")
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "trainer model-splitting helper SHA-256 mismatch" in failure["error"]


def test_trainer_helper_wrong_location_is_rejected(tmp_path):
    module, c, args = _case(tmp_path)
    wrong = tmp_path / "wrong" / "model_splitting.py"
    wrong.parent.mkdir()
    wrong.write_text("# wrong\n", encoding="utf-8")
    _replace_arg(args, "--trainer-helper-path", str(wrong))
    _replace_arg(args, "--trainer-helper-sha256", _sha256(wrong))
    assert module.main(args) == 2
    failure = json.loads((c["run_dir"] / "FAILURE_RECEIPT.json").read_text())
    assert "not the exact import-location" in failure["error"]


def test_launch_receipt_failure_leaves_child_unmanaged_unsignaled(tmp_path, monkeypatch):
    module, c, args = _case(tmp_path, trainer_delay=0.25)
    original = module._write_json_exclusive
    def fail_receipt(path, value):
        if path.name == "LAUNCH_RECEIPT.json": raise OSError("injected")
        return original(path, value)
    monkeypatch.setattr(module, "_write_json_exclusive", fail_receipt)
    assert module.main(args) == 3
    risk = json.loads((c["run_dir"] / "UNMANAGED_CHILD_RISK.json").read_text())
    assert risk["child_was_signaled"] is False and risk["signals_sent"] == []
    assert _wait_for(c["run_dir"] / "trainer_finished.marker")


def test_post_launch_status_failure_leaves_child_unmanaged_unsignaled(tmp_path, monkeypatch):
    module, c, args = _case(tmp_path, trainer_delay=0.25)
    original = module._write_status
    def fail_status(run_dir, payload):
        if payload.get("state") == "TRAINING": raise OSError("injected")
        return original(run_dir, payload)
    monkeypatch.setattr(module, "_write_status", fail_status)
    assert module.main(args) == 3
    risk = json.loads((c["run_dir"] / "UNMANAGED_CHILD_RISK.json").read_text())
    assert risk["child_was_signaled"] is False and risk["signals_sent"] == []
    assert _wait_for(c["run_dir"] / "trainer_finished.marker")
