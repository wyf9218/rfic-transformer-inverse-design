"""Synthetic /proc fixtures only; no solver launch or capacity claim."""

import hashlib
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_native_telemetry as telemetry


@pytest.fixture
def proc(tmp_path):
    root = tmp_path / "proc"
    boot = root / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fixture-boot\n")
    _process(root, 100, 1, 1000, "python3")
    return root


def _process(proc, pid, parent, ticks, name, *, elf=True):
    directory = proc / str(pid)
    directory.mkdir(exist_ok=True)
    executable = proc.parent / "bin" / name
    executable.parent.mkdir(exist_ok=True)
    if not executable.exists():
        executable.write_bytes((b"\x7fELF" if elf else b"#!/bin/sh\n") + b"fixture-not-executable")
    link = directory / "exe"
    if not link.exists():
        link.symlink_to(executable)
    fields = ["S", str(parent), *(["0"] * 17), str(ticks)]
    (directory / "stat").write_text(f"{pid} (fixture process) " + " ".join(fields))
    (directory / "cmdline").write_bytes(name.encode() + b"\0fixture\0")
    (directory / "status").write_text("VmRSS:\t40 kB\nVmHWM:\t80 kB\nThreads:\t2\n")
    return directory


def test_counts_only_same_root_native_elf_and_binds_identity(proc):
    _process(proc, 110, 100, 1010, "python3")
    _process(proc, 120, 110, 1020, "emx")
    _process(proc, 121, 110, 1021, "emx")
    _process(proc, 130, 1, 1030, "emx")  # Same user, unrelated job.
    _process(proc, 140, 100, 1040, "calibre", elf=False)  # Wrapper, not solver.
    sample = telemetry.NativeProcessSampler(100, proc_root=proc).sample()
    assert sample["counts"] == {"cadence": 0, "calibre": 0, "emx": 2}
    assert [p["pid"] for p in sample["native_processes"]] == [120, 121]
    item = sample["native_processes"][0]
    assert item["VmHWM_bytes"] == 80 * 1024
    assert item["threads"] == 2
    assert item["start_ticks"] == 1020
    assert "command_text" not in item
    assert item["executable_sha256"] == hashlib.sha256((proc.parent / "bin/emx").read_bytes()).hexdigest()
    assert sample["captured_utc"] <= sample["verified_utc"]


def test_parent_pid_reuse_is_not_accepted_as_ancestry(proc):
    _process(proc, 110, 100, 900, "emx")
    with pytest.raises(ValueError, match="reused parent PID"):
        telemetry.NativeProcessSampler(100, proc_root=proc).sample()


@pytest.mark.parametrize("drift", ["start_ticks", "command", "boot"])
def test_root_change_invalidates_observation(proc, drift):
    sampler = telemetry.NativeProcessSampler(100, proc_root=proc)
    if drift == "start_ticks":
        _process(proc, 100, 1, 1001, "python3")
    elif drift == "command":
        (proc / "100/cmdline").write_bytes(b"different\0")
    else:
        (proc / "sys/kernel/random/boot_id").write_text("different-boot")
    with pytest.raises(ValueError, match="root identity changed"):
        sampler.sample()


@pytest.mark.parametrize("status", ["Threads: 1\n", "VmRSS: 40 MB\nVmHWM: 80 kB\nThreads: 2\n",
                                    "VmRSS: 40 kB\nVmHWM: 80 kB\nThreads: 0\n"])
def test_live_native_missing_metrics_are_not_zero(proc, status):
    path = _process(proc, 110, 100, 1010, "emx")
    (path / "status").write_text(status)
    with pytest.raises((KeyError, ValueError)):
        telemetry.NativeProcessSampler(100, proc_root=proc).sample()


def test_exited_and_zombie_children_do_not_inflate_concurrency(proc, monkeypatch):
    _process(proc, 110, 100, 1010, "emx")
    zombie = _process(proc, 120, 100, 1020, "calibre")
    (zombie / "stat").write_text((zombie / "stat").read_text().replace(") S ", ") Z "))
    original = telemetry.read_process_identity
    reads = 0

    def identity(pid, **kwargs):
        nonlocal reads
        if pid == 110:
            reads += 1
            if reads == 2:  # Gone before the common verification instant.
                stat = proc / "110/stat"
                stat.write_text(stat.read_text().replace(") S ", ") Z "))
                return None
        return original(pid, **kwargs)

    monkeypatch.setattr(telemetry, "read_process_identity", identity)
    sample = telemetry.NativeProcessSampler(100, proc_root=proc).sample()
    assert sample["counts"] == {"cadence": 0, "calibre": 0, "emx": 0}


def test_unreadable_live_verification_is_not_reported_as_zero(proc, monkeypatch):
    _process(proc, 110, 100, 1010, "emx")
    original = telemetry.read_process_identity
    reads = 0

    def identity(pid, **kwargs):
        nonlocal reads
        if pid == 110:
            reads += 1
            if reads == 2:
                return None
        return original(pid, **kwargs)

    monkeypatch.setattr(telemetry, "read_process_identity", identity)
    with pytest.raises(ValueError, match="live native verification identity is unreadable"):
        telemetry.NativeProcessSampler(100, proc_root=proc).sample()


def test_different_uid_is_not_a_root_descendant():
    root = {"pid": 10, "uid": 1, "parent_pid": 1, "start_ticks": 100}
    records = {10: root, 11: {"pid": 11, "uid": 2, "parent_pid": 10, "start_ticks": 110}}
    assert telemetry.descendant_pids(records, root) == set()


def test_context_receipt_separates_admitted_argv_and_native_counts(proc, tmp_path):
    _process(proc, 110, 100, 1010, "calibre")
    observer = telemetry.NativeRoleObserver(
        tmp_path / "observation", role="calibre_runner", admitted_limit=2,
        command=["python", "calibre_wrapper.py"], bindings={"fixture": True},
        proc_root=proc, root_pid=100, interval_seconds=60,
    )
    with observer as result:
        assert observer.thread.is_alive()
    assert not observer.thread.is_alive()
    receipt = json.loads(Path(result["receipt"]["path"]).read_text())
    assert receipt["backend_admitted_max_concurrency"] == 2
    assert receipt["executor_argv_max_concurrency"] is None
    assert receipt["sampled_peak_native_concurrency"]["calibre"] == 1
    assert receipt["benchmark_level_completed"] is False
    assert receipt["accepted_increment"] == 0
    assert receipt["license_capacity_proven"] is False
    assert receipt["complete_job_peak_memory_proven"] is False
    assert receipt["root_process"]["pid"] == 100
    samples = Path(receipt["samples"]["path"])
    assert receipt["samples"]["sha256"] == hashlib.sha256(samples.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        with observer:
            pytest.fail("no-clobber observer allowed reuse")


def test_role_failure_closes_observer_and_does_not_replace_original_error(proc, tmp_path):
    class RoleFailure(BaseException):
        pass

    observer = telemetry.NativeRoleObserver(
        tmp_path / "failure", role="exact_audited_gds_emx_runner", admitted_limit=2,
        command=["python", "emx.py", "--max-concurrency", "2"], bindings={},
        proc_root=proc, root_pid=100, interval_seconds=60,
    )
    with pytest.raises(RoleFailure):
        with observer:
            raise RoleFailure()
    assert not observer.thread.is_alive()
    receipt = json.loads(Path(observer.result["receipt"]["path"]).read_text())
    assert receipt["executor_argv_max_concurrency"] == 2
    assert receipt["sampled_peak_native_concurrency"]["emx"] == 0
    assert receipt["observation_status"] == "RECORDED"  # Not role PASS.


def test_missing_proc_does_not_block_role_or_report_measured_zero(tmp_path):
    observer = telemetry.NativeRoleObserver(
        tmp_path / "unavailable", role="cadence_streamout_runner", admitted_limit=1,
        command=["python", "cadence.py"], bindings={}, proc_root=tmp_path / "no-proc",
    )
    with observer:
        role_executed = True
    assert role_executed
    assert observer.result["observation_status"] == "PARTIAL"
    assert observer.result["sampled_peak_native_concurrency"] is None


def test_non_native_role_does_not_create_observer(tmp_path):
    out = tmp_path / "not-created"
    with telemetry.observe_native_role("phase_a_queue_builder", out) as result:
        assert result == {}
    assert not out.exists()


def test_partial_sampling_does_not_claim_complete_job_capacity(proc, tmp_path, monkeypatch):
    observer = telemetry.NativeRoleObserver(
        tmp_path / "partial", role="calibre_runner", admitted_limit=1,
        command=[], bindings={}, proc_root=proc, root_pid=100, interval_seconds=0.001,
    )
    original = telemetry.NativeProcessSampler.sample
    failed = telemetry.threading.Event()
    calls = 0

    def sample(self):
        nonlocal calls
        calls += 1
        if calls > 1:
            failed.set()
            raise OSError("fixture observation unavailable")
        return original(self)

    monkeypatch.setattr(telemetry.NativeProcessSampler, "sample", sample)
    with observer:
        assert failed.wait(5)
    assert observer.result["observation_status"] == "PARTIAL"
    receipt = json.loads(Path(observer.result["receipt"]["path"]).read_text())
    assert receipt["sample_count"] == 1
    assert len(receipt["errors"]) == 1
