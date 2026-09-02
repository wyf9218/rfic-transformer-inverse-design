from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_rebound_queue_controller.py"
SPEC = importlib.util.spec_from_file_location("rebound_queue_controller", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_private_rebind_arguments_are_removed_before_delegate_parse() -> None:
    digest = "a" * 64
    private, remaining = MODULE._parse_private_args(
        [
            "--delegate-controller",
            "/tmp/controller.py",
            "--delegate-controller-sha256",
            digest,
            "--old-full-campaign-receipt",
            "/tmp/full.json",
            "--old-full-campaign-receipt-sha256",
            digest,
            "--old-backend-manifest",
            "/tmp/old.json",
            "--old-backend-manifest-sha256",
            digest,
            "--corrected-approval-receipt",
            "/tmp/corrected.json",
            "--corrected-approval-receipt-sha256",
            digest,
            "--queue-rebind-receipt",
            "/tmp/rebind.json",
            "--queue-rebind-receipt-sha256",
            digest,
            "--supervisor-handoff-receipt",
            "/tmp/handoff.json",
            "--supervisor-handoff-receipt-sha256",
            digest,
            "--post-rebind-execution-gate",
            "/tmp/gate.json",
            "--post-rebind-execution-gate-sha256",
            digest,
            "--campaign-root",
            "/tmp/campaign",
        ]
    )
    assert private.queue_rebind_receipt == "/tmp/rebind.json"
    assert remaining == ["--campaign-root", "/tmp/campaign"]


def test_safe_interruptible_sleep_handles_elapsed_deadline(monkeypatch) -> None:
    class Controller:
        STOP_REQUESTED = False

    readings = iter((10.0, 11.0))
    sleeps: list[float] = []
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: next(readings))
    monkeypatch.setattr(MODULE.time, "sleep", sleeps.append)
    MODULE._safe_interruptible_sleep_factory(Controller)(1)
    assert sleeps == []
