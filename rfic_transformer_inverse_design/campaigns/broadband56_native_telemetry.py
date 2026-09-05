"""Observation-only native process evidence; never a launch or capacity gate."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .broadband56_isolation_identity import read_process_identity

ROLE_TO_TOOL = {
    "cadence_streamout_runner": "cadence",
    "calibre_runner": "calibre",
    "exact_audited_gds_emx_runner": "emx",
}
NATIVE_NAMES = {
    "virtuoso": "cadence", "dbAccess": "cadence", "strmin": "cadence",
    "strmout": "cadence", "calibre": "calibre", "emx": "emx",
}
IDENTITY_FIELDS = (
    "pid", "uid", "start_ticks", "boot_id", "command_line_sha256",
    "executable_path", "executable_sha256",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pin(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path.resolve()), "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def _stat(path: Path) -> dict[str, Any]:
    fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": int(path.name), "uid": path.stat().st_uid,
            "parent_pid": int(fields[1]), "start_ticks": int(fields[19]),
            "state": fields[0]}


def _exited(path: Path) -> bool:
    try:
        return _stat(path)["state"] in {"Z", "X"}
    except (FileNotFoundError, ProcessLookupError):
        return True


def descendant_pids(records: dict[int, dict], root: dict) -> set[int]:
    """Reject a stale parent edge instead of attaching a child to a reused PID."""
    seen = {root["pid"]}
    while True:
        added = set()
        for pid, record in records.items():
            parent = record["parent_pid"]
            if pid in seen or parent not in seen:
                continue
            if record["start_ticks"] < records[parent]["start_ticks"]:
                raise ValueError("native telemetry encountered a reused parent PID")
            added.add(pid)
        if not added:
            return {pid for pid in seen - {root["pid"]} if records[pid]["uid"] == root["uid"]}
        seen.update(added)


class NativeProcessSampler:
    def __init__(self, root_pid: int, *, proc_root: Path = Path("/proc")) -> None:
        self.proc_root = proc_root
        self.cache: dict[Path, str] = {}
        self.root = read_process_identity(
            root_pid, proc_root=proc_root, executable_hash_cache=self.cache,
        )
        if self.root is None:
            raise ValueError("native telemetry root identity is unavailable")

    def _check_root(self) -> None:
        current = read_process_identity(
            self.root["pid"], proc_root=self.proc_root, executable_hash_cache=self.cache,
        )
        if current is None or any(current[k] != self.root[k] for k in IDENTITY_FIELDS):
            raise ValueError("native telemetry root identity changed or exited")

    def sample(self) -> dict[str, Any]:
        self._check_root()
        records = {}
        for path in self.proc_root.iterdir():
            if not path.name.isdecimal():
                continue
            try:
                # A container launcher can be nondumpable/root-owned in /proc
                # while its solver returns to the project UID. Keep transit
                # ancestry metadata, but measure only project-owned endpoints.
                records[int(path.name)] = _stat(path)
            except (FileNotFoundError, ProcessLookupError):
                continue
        if records.get(self.root["pid"], {}).get("start_ticks") != self.root["start_ticks"]:
            raise ValueError("native telemetry root disappeared during scan")
        native = []
        protected_transit = []
        for pid in sorted(descendant_pids(records, self.root)):
            path = self.proc_root / str(pid)
            if records[pid]["state"] in {"Z", "X"}:
                continue
            try:
                try:
                    exe = (path / "exe").resolve(strict=True)
                except PermissionError:
                    comm = (path / "comm").read_text().strip()
                    command = (path / "cmdline").read_bytes()
                    after = _stat(path)
                    if (comm != "starter-suid" or command.replace(b"\0", b" ").strip() != b"Singularity runtime parent"
                            or any(after[k] != records[pid][k] for k in ("uid", "parent_pid", "start_ticks"))):
                        raise
                    protected_transit.append({**after, "comm": comm,
                        "command_line_sha256": hashlib.sha256(command).hexdigest(),
                        "executable_identity": "UNREADABLE_NOT_AN_AUTHORITY_CHECK",
                        "counted_as_native_solver": False})
                    continue
                tool = NATIVE_NAMES.get(exe.name)
                if tool is None:
                    continue
                with exe.open("rb") as handle:
                    if handle.read(4) != b"\x7fELF":
                        continue
                identity = read_process_identity(
                    pid, proc_root=self.proc_root, executable_hash_cache=self.cache,
                )
                if identity is None:
                    if _exited(path):
                        continue
                    raise ValueError("live native process identity is unreadable")
                status = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines()
                              if ":" in line)
                metrics = {}
                for field in ("VmRSS", "VmHWM"):
                    amount, unit = status[field].split()
                    if unit != "kB" or int(amount) < 0:
                        raise ValueError("invalid native memory observation")
                    metrics[field + "_bytes"] = int(amount) * 1024
                metrics["threads"] = int(status["Threads"])
                if metrics["threads"] < 1:
                    raise ValueError("invalid native thread observation")
                after = _stat(path)
                if after["state"] in {"Z", "X"}:
                    continue
                if any(after[k] != records[pid][k] or identity[k] != records[pid][k]
                       for k in ("pid", "uid", "parent_pid", "start_ticks")):
                    raise ValueError("native process identity changed during observation")
                native.append({**{k: v for k, v in identity.items() if k != "command_text"},
                               "tool": tool, **metrics})
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (KeyError, ValueError):
                if _exited(path):
                    continue
                raise
        # Every retained process spans this common instant. A sequential scan
        # alone could falsely count two short-lived, non-overlapping solvers.
        captured = _utc()
        survivors = []
        for record in native:
            current = read_process_identity(
                record["pid"], proc_root=self.proc_root, executable_hash_cache=self.cache,
            )
            if current is None:
                path = self.proc_root / str(record["pid"])
                if not _exited(path):
                    raise ValueError("live native verification identity is unreadable")
                continue
            if all(current[k] == record[k] for k in IDENTITY_FIELDS):
                survivors.append(record)
        self._check_root()
        return {"captured_utc": captured, "verified_utc": _utc(), "native_processes": survivors,
                "protected_transit_processes": protected_transit,
                "counts": {tool: sum(p["tool"] == tool for p in survivors)
                           for tool in ("cadence", "calibre", "emx")}}


class NativeRoleObserver:
    """One thread in the existing backend; errors invalidate telemetry, not children."""

    def __init__(self, out_dir: Path, *, role: str, admitted_limit: int,
                 command: list[str], bindings: dict, root_pid: int | None = None,
                 interval_seconds: float = 1.0, proc_root: Path = Path("/proc")) -> None:
        if role not in ROLE_TO_TOOL or interval_seconds <= 0:
            raise ValueError("invalid native observation role or interval")
        self.out_dir, self.proc_root = out_dir, proc_root
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.interval = interval_seconds
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.result: dict[str, Any] = {}
        self.samples = 0
        self.peaks = dict.fromkeys(("cadence", "calibre", "emx"), 0)
        self.errors: list[str] = []
        argument_limit = None
        if "--max-concurrency" in command:
            argument_limit = int(command[command.index("--max-concurrency") + 1])
        self.header = {
            "schema": "rfic_transformer.broadband56_native_role_observation.v1",
            "scope": "SAMPLED_NATIVE_PROCESSES_NOT_CAPACITY_OR_PHYSICAL_ACCEPTANCE",
            "role": role, "started_utc": _utc(), "bindings": bindings,
            "backend_admitted_max_concurrency": admitted_limit,
            "executor_argv_max_concurrency": argument_limit,
            "command_argv_sha256": hashlib.sha256(json.dumps(command, separators=(",", ":")).encode()).hexdigest(),
            "sampling_interval_seconds": interval_seconds,
            "complete_job_peak_memory_proven": False,
            "complete_job_thread_budget_proven": False,
            "license_capacity_proven": False, "accepted_increment": 0,
            "benchmark_level_completed": False,
        }

    def _sample(self) -> None:
        sample = self.sampler.sample()
        self.handle.write(json.dumps(sample, sort_keys=True) + "\n")
        self.handle.flush()
        self.samples += 1
        for tool, count in sample["counts"].items():
            self.peaks[tool] = max(self.peaks[tool], count)

    def _loop(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self._sample()
            except Exception as exc:
                self.errors.append(f"{type(exc).__name__}: {exc}")
                return

    def __enter__(self) -> dict:
        self.out_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.handle = (self.out_dir / "PROCESS_SAMPLES.jsonl").open("x", encoding="utf-8")
        try:
            self.sampler = NativeProcessSampler(self.root_pid, proc_root=self.proc_root)
            self.header["root_process"] = {k: v for k, v in self.sampler.root.items() if k != "command_text"}
            self._sample()
            self.thread = threading.Thread(target=self._loop, name="native-role-observation", daemon=True)
            self.thread.start()
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        return self.result

    def __exit__(self, *exc_info: Any) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        try:
            self.handle.close()
            summary = {**self.header, "finished_utc": _utc(), "sample_count": self.samples,
                       "observation_status": "PARTIAL" if self.errors else "RECORDED",
                       "sampled_peak_native_concurrency": self.peaks if self.samples else None,
                       "errors": self.errors, "samples": _pin(self.out_dir / "PROCESS_SAMPLES.jsonl")}
            path = self.out_dir / "NATIVE_ROLE_OBSERVATION.json"
            with path.open("x", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, sort_keys=True)
                handle.write("\n")
            self.result.update({"receipt": _pin(path), "observation_status": summary["observation_status"],
                                "sampled_peak_native_concurrency": summary["sampled_peak_native_concurrency"]})
        except Exception as exc:
            self.result.update({"observation_status": "NOT_MEASURED", "receipt": None,
                                "error": f"{type(exc).__name__}: {exc}"})


@contextmanager
def observe_native_role(role: str, out_dir: Path, **kwargs: Any):
    if role not in ROLE_TO_TOOL:
        yield {}
    else:
        with NativeRoleObserver(out_dir, role=role, **kwargs) as result:
            yield result
