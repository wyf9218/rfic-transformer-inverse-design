from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_exact_audited_gds_emx.py"
SPEC = importlib.util.spec_from_file_location("exact_audited_gds_emx_cli", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_cli_binds_runner_and_module_sha_before_dispatch(monkeypatch, tmp_path) -> None:
    calls = []

    def _fake_run(**kwargs):
        calls.append(kwargs)
        return {
            "receipt_path": str(tmp_path / "receipt.json"),
            "touchstone_path": str(tmp_path / "emx.s4p"),
        }

    monkeypatch.setattr(
        MODULE.exact_gds_emx, "run_exact_audited_gds_fresh_emx", _fake_run
    )
    argv = _argv(tmp_path)

    assert MODULE.main(argv) == 0
    assert len(calls) == 1
    assert calls[0]["candidate_id_sha256"] == "1" * 64
    assert calls[0]["geometry_identity_sha256"] == "2" * 64


def test_cli_rejects_module_sha_drift_before_dispatch(monkeypatch, tmp_path) -> None:
    calls = []

    def _must_not_run(**kwargs):
        calls.append(kwargs)
        raise AssertionError("runner must not be dispatched")

    monkeypatch.setattr(
        MODULE.exact_gds_emx, "run_exact_audited_gds_fresh_emx", _must_not_run
    )
    argv = _argv(tmp_path)
    marker = argv.index("--expected-module-sha256")
    argv[marker + 1] = "f" * 64

    assert MODULE.main(argv) == 2
    assert calls == []


def _argv(tmp_path: Path) -> list[str]:
    return [
        "--config",
        str(tmp_path / "config.yaml"),
        "--expected-config-sha256",
        "3" * 64,
        "--gds",
        str(tmp_path / "candidate.gds"),
        "--expected-gds-sha256",
        "4" * 64,
        "--manifest",
        str(tmp_path / "manifest.json"),
        "--expected-manifest-sha256",
        "5" * 64,
        "--calibre-receipt",
        str(tmp_path / "calibre.json"),
        "--expected-calibre-receipt-sha256",
        "6" * 64,
        "--full-campaign-receipt",
        str(tmp_path / "authorization.json"),
        "--expected-full-campaign-receipt-sha256",
        "7" * 64,
        "--candidate-id-sha256",
        "1" * 64,
        "--geometry-identity-sha256",
        "2" * 64,
        "--out-dir",
        str(tmp_path / "out"),
        "--expected-runner-sha256",
        _sha256(SCRIPT),
        "--expected-module-sha256",
        _sha256(Path(MODULE.exact_gds_emx.__file__)),
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
