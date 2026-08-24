from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_exact_trainer_with_finite_observer.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("finite_observer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observer_records_finite_first_updates(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_module()
    for key in module.THREAD_ENV_KEYS:
        monkeypatch.setenv(key, "1")
    fake_blas_sha256 = "a" * 64
    monkeypatch.setattr(
        module,
        "_loaded_blas_libraries",
        lambda: [
            {
                "file_name": "libopenblas-test.so",
                "path": "/test/libopenblas-test.so",
                "sha256": fake_blas_sha256,
                "size_bytes": 1,
            }
        ],
    )
    trainer = tmp_path / "trainer.py"
    trainer.write_text(
        """
import json
from pathlib import Path

import numpy as np

def _adam_step(weights, biases, grad_weights, grad_biases, state, learning_rate):
    state['step'] += 1
    for index in range(len(weights)):
        state['mw'][index] += grad_weights[index]
        state['vw'][index] += grad_weights[index] ** 2
        state['mb'][index] += grad_biases[index]
        state['vb'][index] += grad_biases[index] ** 2
        weights[index] -= learning_rate * grad_weights[index]
        biases[index] -= learning_rate * grad_biases[index]

def main(argv=None):
    widths = (3,) if argv and argv[0] == 'one-stage' else (3, 4)
    results = []
    for width in widths:
        weights = [np.ones((2, width))]
        biases = [np.ones(width)]
        state = {'step': 0, 'mw': [np.zeros((2, width))],
                 'vw': [np.zeros((2, width))], 'mb': [np.zeros(width)],
                 'vb': [np.zeros(width)]}
        for _ in range(3):
            _adam_step(weights, biases, [np.ones((2, width))],
                       [np.ones(width)], state, 0.01)
        results.append({
            'weights': [value.tolist() for value in weights],
            'biases': [value.tolist() for value in biases],
            'state': {
                key: value if key == 'step' else [item.tolist() for item in value]
                for key, value in state.items()
            },
        })
    Path(argv[-1]).write_text(json.dumps(results, sort_keys=True), encoding='utf-8')
    return 0
""".lstrip(),
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    sha = module._sha256(trainer)
    runtime = module._runtime_identity()
    baseline_result = tmp_path / "baseline.json"
    observed_result = tmp_path / "observed.json"
    baseline_spec = importlib.util.spec_from_file_location("baseline_trainer", trainer)
    assert baseline_spec and baseline_spec.loader
    baseline_module = importlib.util.module_from_spec(baseline_spec)
    baseline_spec.loader.exec_module(baseline_module)
    assert baseline_module.main(["normal", str(baseline_result)]) == 0
    arguments = [
        "--trainer-source",
        str(trainer),
        "--expected-trainer-sha256",
        sha,
        "--expected-python-sha256",
        runtime["python_executable_sha256"],
        "--expected-numpy-version",
        runtime["numpy_version"],
        "--expected-numpy-core-sha256",
        runtime["numpy_core_sha256"],
        "--expected-blas-sha256",
        fake_blas_sha256,
        "--expected-thread-limit",
        "1",
        "--receipt",
        str(receipt),
        "--observe-steps",
        "3",
        "--",
        "normal",
        str(observed_result),
    ]
    assert module.main(arguments) == 0
    result = json.loads(receipt.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert all(result["runtime_checks"].values())
    assert result["stages"]["forward_proxy"]["parameter_count"] == 9
    assert result["stages"]["tandem_inverse"]["parameter_count"] == 12
    assert result["stages"]["forward_proxy"]["observed_steps"] == 3
    assert result["stages"]["tandem_inverse"]["observed_steps"] == 3
    assert observed_result.read_bytes() == baseline_result.read_bytes()

    incomplete_receipt = tmp_path / "incomplete_receipt.json"
    incomplete_arguments = list(arguments)
    incomplete_arguments[incomplete_arguments.index("--receipt") + 1] = str(
        incomplete_receipt
    )
    incomplete_result = tmp_path / "incomplete.json"
    separator = incomplete_arguments.index("--")
    incomplete_arguments[separator + 1] = "one-stage"
    incomplete_arguments[-1] = str(incomplete_result)
    assert module.main(incomplete_arguments) == 3
    incomplete = json.loads(incomplete_receipt.read_text(encoding="utf-8"))
    assert incomplete["status"] == "FAIL_INCOMPLETE_FINITE_UPDATE_OBSERVATION"
    assert incomplete["observer_exit_code"] == 3
