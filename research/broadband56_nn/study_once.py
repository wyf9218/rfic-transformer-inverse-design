"""Research-only durable admission/journal primitives. Never controls production.

An advisory lock is held across each foreground worker and inherited by its
training child. A dead parent therefore cannot authorize duplicate GPU work.
No PID timeout, scheduler installation, remote write, or automatic retry occurs.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

from .io import canonical_sha, read_json, sha256, utc_now


class BusyStudy(RuntimeError):
    pass


def study_key(campaign_id: str, suite_version: str = "seven-model-10k-v3") -> str:
    if not campaign_id or not suite_version:
        raise ValueError("campaign and suite identity required")
    identity = {"campaign_id": campaign_id, "milestone": 10000, "suite_version": suite_version}
    return "10k-" + canonical_sha(identity)[:24]


def pin(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def verify_pin(value):
    path = Path(value["path"])
    if not path.is_file() or sha256(path) != value["sha256"]:
        raise ValueError(f"artifact identity changed: {path}")
    if "bytes" in value and path.stat().st_size != value["bytes"]:
        raise ValueError(f"artifact size changed: {path}")
    return path


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value, *, immutable=False):
    """Durable complete JSON, exclusive publication for immutable records."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False) + "\n").encode()
    fd, temp = tempfile.mkstemp(prefix=".unpublished-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(temp, path)
        else:
            os.replace(temp, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def lease(path):
    """Lock inode is permanent: never unlink a possibly held lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyStudy(str(path)) from exc
        os.set_inheritable(fd, True)
        yield fd
    finally:
        # close, not LOCK_UN: inherited children must retain the shared lease.
        os.close(fd)


class Journal:
    def __init__(self, root):
        self.root = Path(root)
        self.events = self.root / "events"
        self.events.mkdir(parents=True, exist_ok=True)

    def load(self):
        prior, value = None, None
        records = sorted(self.events.glob("event_*.json"))
        for index, path in enumerate(records, 1):
            value = read_json(path)
            if path.name != f"event_{index:06d}.json" or value["sequence"] != index:
                raise ValueError("journal gap or reordered records")
            if value["prior_event_sha256"] != prior:
                raise ValueError("journal hash chain changed")
            prior = sha256(path)
        return value

    def append(self, status, detail):
        previous = self.load()
        sequence = 1 if previous is None else previous["sequence"] + 1
        prior_path = self.events / f"event_{sequence - 1:06d}.json"
        value = {"schema": "bb_study_event.v1", "sequence": sequence,
                 "prior_event_sha256": sha256(prior_path) if previous else None,
                 "created_utc": utc_now(), "pid": os.getpid(), "uid": os.getuid(),
                 "status": status, "detail": detail}
        atomic_json(self.events / f"event_{sequence:06d}.json", value, immutable=True)
        atomic_json(self.root / "run_status.json", value)
        return value


def resource_snapshot(root, *, device="mps", min_available_bytes=8 * 1024**3,
                      min_disk_bytes=10 * 1024**3):
    """Conservative local admission; no SSH and no production-host probes."""
    free = shutil.disk_usage(Path(root)).free
    available = total = None
    if platform.system() == "Darwin":
        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
        vm = subprocess.check_output(["vm_stat"], text=True)
        page = int(re.search(r"page size of (\d+) bytes", vm).group(1))
        rows = {m.group(1): int(m.group(2)) for m in
                re.finditer(r"^(Pages [^:]+):\s+(\d+)\.", vm, re.MULTILINE)}
        available = page * sum(rows.get(key, 0) for key in
                               ("Pages free", "Pages inactive", "Pages speculative"))
    elif Path("/proc/meminfo").is_file():
        values = dict(re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.MULTILINE))
        total = int(values["MemTotal"]) * 1024
        available = int(values["MemAvailable"]) * 1024
    import torch
    hardware_ok = device == "cpu" or (device == "mps" and torch.backends.mps.is_available())
    passed = available is not None and available >= min_available_bytes and free >= min_disk_bytes and hardware_ok
    return {"status": "PASS" if passed else "WAITING_RESOURCE", "host": platform.node(),
            "architecture": platform.machine(), "system": platform.system(),
            "cpu_logical": os.cpu_count(), "cpu_threads_authorized": 2,
            "memory_total_bytes": total, "memory_available_estimate_bytes": available,
            "memory_measurement": "free+inactive+speculative on Darwin; MemAvailable on Linux",
            "min_available_bytes": min_available_bytes, "disk_free_bytes": free,
            "min_disk_bytes": min_disk_bytes, "device": device, "hardware_available": hardware_ok,
            "max_concurrent_training": 1, "mps_process_memory_fraction": 0.25,
            "production_host_used_for_training": False, "observed_utc": utc_now()}


def run_child(argv, cwd, log_path, lock_fds):
    """A child keeps all research locks even if this parent exits abruptly."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("xb") as log:
        result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT,
                                pass_fds=tuple(lock_fds), check=False)
    return {"argv": list(argv), "returncode": result.returncode, "log": pin(log_path)}
