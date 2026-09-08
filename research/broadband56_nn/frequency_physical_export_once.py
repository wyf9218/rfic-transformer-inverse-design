"""One local, read-only terminal wait followed by one finite figure-export call.

This is not a training/solver supervisor. It never signals its observed producer,
changes deadlines, submits physics jobs, or retries a failed export. The existing
producer must exit before its published FINAL_RECEIPT is consumed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .io import read_json, save_json, sha256, utc_now

SCHEMA = "frequency_physical_export_once.v1"
BATCH_MODULE = "research.broadband56_nn.frequency_physical_export_batch"
AGGREGATES = {"formal-selected", "formal-all", "development-selected",
              "development-all", "selection"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def checked(record):
    actual = pin(record["path"])
    require(actual == record, "file identity changed: " + str(record["path"]))
    return Path(actual["path"])


def process_identity(pid):
    """Read only the specified PID; no kill(0), signals, process scans or SSH."""
    require(isinstance(pid, int) and pid > 1, "invalid producer PID")
    observed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "pid=,uid=,lstart=,args="],
        capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"},
        check=False,
    )
    if observed.returncode == 1 and not observed.stdout.strip() and not observed.stderr.strip():
        return None
    require(observed.returncode == 0, "process observation failed; not evidence of exit")
    fields = observed.stdout.strip().split(None, 7)
    require(len(fields) == 8 and int(fields[0]) == pid, "invalid process observation")
    return {"pid": pid, "uid": int(fields[1]), "lstart": " ".join(fields[2:7]),
            "args_sha256": hashlib.sha256(fields[7].encode()).hexdigest()}


def same_producer(expected):
    actual = process_identity(expected["pid"])
    if actual is None:
        return False
    require(actual["uid"] == os.getuid(), "refuse another user's process")
    if actual["lstart"] != expected["lstart"]:
        return False  # PID reused: original producer exited, never signal new owner.
    require(actual == expected, "producer identity changed; refuse to assume exit")
    return True


def load_config(path):
    record = pin(path)
    config = read_json(checked(record))
    require(config["schema"] == SCHEMA, "unsupported once-export schema")
    require(config["producer"]["uid"] == os.getuid(), "producer is not current user")
    require(10 <= config["poll_seconds"] <= 60, "bounded native polling required")
    require(set(config["aggregates"]) <= AGGREGATES, "unknown aggregate")
    require(len(config["aggregates"]) == len(set(config["aggregates"])), "duplicate aggregate")
    producer = read_json(checked(config["producer_configuration"]))
    require(producer["schema"] == "frequency_incremental_statistics_consumer.v1",
            "not the existing producer contract")
    require(Path(config["final_receipt"]).resolve() ==
            Path(producer["output"]).resolve() / "FINAL_RECEIPT.json",
            "final path is not producer-owned")
    require(Path(config["python"]).is_file() and Path(config["cwd"]).is_dir(),
            "Python/worktree unavailable")
    wait_out, batch_out = Path(config["wait_output"]).resolve(), Path(config["batch_output"]).resolve()
    require(wait_out != batch_out and wait_out.parent == batch_out.parent,
            "isolated sibling output directories required")
    require(not batch_out.exists(), "batch output already exists; no duplicate dispatch")
    require(not wait_out.exists(), "once waiter already installed; no automatic retry")
    require(config["source_pins"], "source pins required")
    for source in config["source_pins"]:
        checked(source)
    deadline = datetime.fromisoformat(config["wait_deadline_utc"].replace("Z", "+00:00"))
    require(deadline.tzinfo is not None, "timezone required")
    return config, record, deadline


def command(config):
    args = [config["python"], "-B", "-m", BATCH_MODULE, "--final", config["final_receipt"],
            "--out", config["batch_output"], "--mode", "run"]
    for aggregate in config["aggregates"]:
        args += ["--aggregate", aggregate]
    return args


def preflight(path):
    config, record, deadline = load_config(path)
    require(datetime.now(timezone.utc) < deadline, "wait budget already expired")
    alive = same_producer(config["producer"])
    require(alive or Path(config["final_receipt"]).is_file(),
            "producer exited without FINAL_RECEIPT; do not invent a terminal")
    return {"status": "READY_TO_WAIT_READONLY" if alive else "READY_FOR_TERMINAL_CHECK",
            "configuration": record, "producer_alive": alive, "command": command(config),
            "output_created": False, "training_or_simulation_started": False}


def run(path, *, sleeper=time.sleep, now=lambda: datetime.now(timezone.utc), runner=subprocess.run):
    config, record, deadline = load_config(path)
    out = Path(config["wait_output"]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    save_json(out / "WAIT_LAUNCH_RECEIPT.json",
              {"status": "WAITING_FOR_ORIGINAL_PRODUCER_EXIT", "pid": os.getpid(),
               "configuration": record, "producer": config["producer"], "created_utc": utc_now(),
               "producer_signals": 0, "AI_polling": False})
    try:
        while same_producer(config["producer"]):
            require(now() < deadline, "wait budget ended; no export dispatched")
            sleeper(min(config["poll_seconds"], max(0, (deadline - now()).total_seconds())))
        require(now() < deadline, "wait budget ended; no export dispatched")
        # Parent exit is required because its existing save_json publishes by name
        # before close/fsync. Do not read a potentially still-being-written final.
        final_pin = pin(config["final_receipt"])
        final = read_json(checked(final_pin))
        require(final["configuration"] == config["producer_configuration"],
                "terminal belongs to another producer contract")
        require(final["status"] in {"COMPLETE", "PARTIAL"}, "not a supported terminal")
        for source in config["source_pins"]:
            checked(source)
        checked(record)
        args = command(config)
        save_json(out / "DISPATCH_INTENT.json",
                  {"configuration": record, "final_receipt": final_pin, "command": args,
                   "created_utc": utc_now(), "no_retry": True})
        env = {**os.environ, "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
               "OPENBLAS_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg"}
        with (out / "export.stdout.log").open("x") as stdout, (out / "export.stderr.log").open("x") as stderr:
            result = runner(args, cwd=config["cwd"], env=env, stdout=stdout, stderr=stderr, check=False)
        require(result.returncode == 0, "export failed; output preserved; no automatic retry")
        receipt = {"status": "FINITE_EXPORT_EXITED_ZERO_REVIEW_REQUIRED", "created_utc": utc_now(),
                   "configuration": record, "final_receipt": final_pin,
                   "producer_terminal_status": final["status"],
                   "batch_output": config["batch_output"], "visual_acceptance": "NOT_IMPLIED",
                   "producer_signals": 0, "solver_or_model_calls": 0,
                   "source_final_unchanged": checked(final_pin) is not None}
        save_json(out / "ONCE_RECEIPT.json", receipt)
        return receipt
    except Exception as exc:
        save_json(out / "FAILURE_RECEIPT.json",
                  {"status": "FAIL_PRESERVED_NO_RETRY", "created_utc": utc_now(),
                   "error_type": type(exc).__name__, "error": str(exc), "configuration": record,
                   "producer_signals": 0, "solver_or_model_calls": 0})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Read-only; no output or process start")
    args = parser.parse_args()
    result = preflight(args.request) if args.check else run(args.request)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

