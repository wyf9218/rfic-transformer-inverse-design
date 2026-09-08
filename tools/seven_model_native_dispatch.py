#!/usr/bin/env python3
"""One ordinary scheduler tick around a separately approved foreground entry.

This stdlib adapter does not train, evaluate, inspect numerical results, install
jobs, signal processes, or change the frozen study. Only explicit pre-training
waits may be revisited. An interrupted or partial dispatch requires external
reconciliation; deleting its state is not a supported recovery procedure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys


WAIT = {"WAITING_FOR_10K", "WAITING_RESOURCE"}
COMPLETE = "TRAINED_AND_EVALUATED"
STARTED_PATHS = ("TRAINING_BUDGET.json", "stages", "PHYSICAL_PARITY.json",
                 "STUDY_MODELS.json", "evaluation", "packages")


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(value, *, exists=True):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=exists):
        raise ValueError("absolute, canonical, non-symlink paths required")
    return path


def _raw(path):
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("regular file required")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read()
        after, live = os.fstat(fd), path.stat(follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if identity(before) != identity(after) or identity(after) != identity(live):
            raise ValueError("file changed during metadata read")
        return raw
    finally:
        os.close(fd)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _pinned(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("exact lowercase SHA-256 required")
    raw = _raw(path)
    if _sha(raw) != expected:
        raise ValueError("pinned artifact changed: " + str(path))
    return raw


def _pin(path):
    raw = _raw(path)
    return {"path": str(path), "sha256": _sha(raw), "bytes": len(raw)}


def _sync(path):
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write(path, value):
    """No-clobber evidence; an interrupted partial record is deliberately fatal."""
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(Path(path).parent)
    return _sha(raw)


def load_config(config_path, expected_sha, qa_path, qa_sha):
    """Read-only exact gate. The independent receipt binds code and config."""
    config = _json(_pinned(config_path, expected_sha))
    qa = _json(_pinned(qa_path, qa_sha))
    source_sha = _sha(_raw(Path(__file__).resolve()))
    if config.get("schema") != "bb_native_dispatch.v1":
        raise ValueError("unsupported dispatch configuration")
    if (qa.get("status") != "GO_FOR_NATIVE_DISPATCH" or
            qa.get("config_sha256") != expected_sha or
            qa.get("dispatcher_sha256") != source_sha):
        raise ValueError("exact independent native dispatch GO required")
    if config.get("dispatcher_sha256", source_sha) != source_sha:
        raise ValueError("configuration pins a different dispatcher")
    study = _path(config["study_root"])
    state = _path(config["state_root"], exists=False)
    if not study.is_dir() or study == state or study in state.parents or state in study.parents:
        raise ValueError("state must be outside and not contain the study")
    if not state.parent.is_dir() or (state.exists() and not state.is_dir()):
        raise ValueError("state parent must exist; state must be a directory")
    for name in ("wrapper", "request", "access", "active_marker"):
        pin = config[name]
        raw = _pinned(pin["path"], pin["sha256"])
        if "bytes" in pin and pin["bytes"] != len(raw):
            raise ValueError("pinned size mismatch")
    request = _json(_pinned(config["request"]["path"], config["request"]["sha256"]))
    identity = {"campaign_id": request["campaign_id"], "milestone": 10000,
                "suite_version": request["suite_version"]}
    key = "10k-" + _sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())[:24]
    if (key != config["study_key"] or key != study.name or key != request.get("study_key") or
            request.get("control_root") != str(study.parent)):
        raise ValueError("study identity differs from the frozen request")
    marker = _json(_pinned(config["active_marker"]["path"], config["active_marker"]["sha256"]))
    candidate = marker.get("candidate_request", {})
    if (config["active_marker"]["path"] != str(study / "ACTIVE_RUNTIME_REVISION.json") or
            marker.get("status") != "ACTIVE" or marker.get("study_root") != str(study) or
            any(candidate.get(k) != config["request"][k] for k in ("path", "sha256"))):
        raise ValueError("active marker does not bind the exact request")
    return config


def read_study_state(root):
    """Only journal/control metadata; never predictions or numerical metrics."""
    root = _path(root)
    events_dir = _path(root / "events")
    paths = sorted(events_dir.iterdir())
    if not paths:
        raise ValueError("existing study journal is required")
    prior, latest, raw, stops = None, None, None, []
    for number, path in enumerate(paths, 1):
        raw = _raw(path)
        latest = _json(raw)
        if (path.name != f"event_{number:06d}.json" or latest.get("sequence") != number or
                latest.get("prior_event_sha256") != prior or latest.get("schema") != "bb_study_event.v1"):
            raise ValueError("journal chain identity mismatch")
        prior = _sha(raw)
        if latest.get("status") in ("FAILED", "PARTIAL"):
            stops.append(latest["status"])
    if _raw(root / "run_status.json") != raw or sorted(events_dir.iterdir()) != paths:
        raise ValueError("journal/cache mismatch or concurrent journal update")
    started = [name for name in STARTED_PATHS if os.path.lexists(root / name)]
    return {"status": latest["status"], "event": latest,
            "event_sha256": prior, "event_path": str(paths[-1]),
            "training_evidence": started, "stop_statuses": stops}


def classify_result(returncode, stdout, before, after):
    """A zero exit code alone is neither completion nor a retriable wait."""
    try:
        lines = stdout.splitlines()
        result = _json(lines[-1]) if lines else {}
    except (ValueError, UnicodeError):
        return "NEEDS_RECONCILIATION"
    if after["status"] == COMPLETE and returncode == 0:
        if result == after["event"]:
            return COMPLETE
        # The suite already verifies the entire package before this response.
        package = result.get("package", {})
        expected = Path(after["event_path"]).parent.parent / "packages" / "SEVEN_PACKAGE.json"
        if result.get("status") == "ALREADY_COMPLETE" and package.get("path") == str(expected):
            try:
                _pinned(expected, package["sha256"])
                return COMPLETE
            except (ValueError, OSError, KeyError):
                pass
        return "NEEDS_RECONCILIATION"
    if after["stop_statuses"]:
        return "STOPPED_" + after["stop_statuses"][-1]
    if before["training_evidence"] or after["training_evidence"]:
        return "NEEDS_RECONCILIATION"
    if (returncode == 75 and result.get("entry_preflight", {}).get("status") == "WAITING_RESOURCE"
            and before == after and after["status"] in WAIT):
        return "WAITING_RESOURCE"
    if (returncode == 0 and after["status"] in WAIT and result == after["event"] and
            after["event"]["sequence"] > before["event"]["sequence"]):
        return after["status"]
    return "NEEDS_RECONCILIATION"


def _binding(config_path, expected_sha, qa_path, qa_sha):
    return {"schema": "bb_native_dispatch_identity.v1", "config": str(_path(config_path)),
            "config_sha256": expected_sha, "qa_receipt": str(_path(qa_path)),
            "qa_sha256": qa_sha, "dispatcher_sha256": _sha(_raw(Path(__file__).resolve()))}


def _snapshot_record(value):
    event = _json(_pinned(value["event_path"], value["event_sha256"]))
    if (event != value["event"] or value["status"] != event["status"] or
            not isinstance(value["training_evidence"], list) or not isinstance(value["stop_statuses"], list)):
        raise ValueError("saved dispatch snapshot does not bind its event")


def _history(state, binding, config):
    identity = state / "CONFIG_IDENTITY.json"
    if not identity.exists() or _json(_raw(identity)) != binding:
        raise ValueError("permanent dispatcher state identity mismatch")
    entries = sorted(state.iterdir())
    attempts = [path for path in entries if re.fullmatch(r"attempt_\d{6}", path.name)]
    if set(entries) != set(attempts + [identity, state / "dispatch.lock"]):
        raise ValueError("unexpected dispatcher state entry; reconciliation required")
    previous, last = None, None
    for number, attempt in enumerate(attempts, 1):
        if attempt.name != f"attempt_{number:06d}" or _path(attempt) != attempt:
            raise ValueError("attempt gap or substituted directory")
        intent_path = attempt / "INTENT.json"
        if not intent_path.exists():
            return attempts, "NEEDS_RECONCILIATION"
        intent = _json(_raw(intent_path))
        if (intent.get("schema") != "bb_native_dispatch_intent.v1" or intent.get("identity") != binding or
                intent.get("prior_completion_sha256") != previous or
                intent.get("argv") != ["/bin/bash", config["wrapper"]["path"]]):
            raise ValueError("attempt identity or chain changed")
        _snapshot_record(intent["before"])
        completion = attempt / "COMPLETION.json"
        if not completion.exists():
            return attempts, "NEEDS_RECONCILIATION"
        value = _json(_raw(completion))
        if (value.get("schema") != "bb_native_dispatch_completion.v1" or
                value.get("intent_sha256") != _sha(_raw(intent_path))):
            raise ValueError("completion does not bind attempt intent")
        for name in ("stdout", "stderr"):
            record = value[name]
            if record["path"] != str(attempt / (name + ".log")):
                raise ValueError("completion log path mismatch")
            _pinned(record["path"], record["sha256"])
        if value["after"] is not None:
            _snapshot_record(value["after"])
        replay = (classify_result(value["returncode"], _raw(attempt / "stdout.log"),
                                  intent["before"], value["after"])
                  if value["reason"] is None and value["after"] is not None else "NEEDS_RECONCILIATION")
        if replay != value["status"] or value.get("automatic_retry_allowed") != (replay in WAIT):
            raise ValueError("completion classification does not match saved evidence")
        previous, last = _sha(_raw(completion)), value["status"]
        if last not in WAIT and number != len(attempts):
            raise ValueError("automatic retry after stopped attempt")
    return attempts, last


def _admission(snapshot, last):
    if last is not None and last not in WAIT:
        return last
    if snapshot["status"] == COMPLETE:
        return "EXISTING_COMPLETION_REQUIRES_VERIFICATION"
    if snapshot["stop_statuses"]:
        return "STOPPED_" + snapshot["stop_statuses"][-1]
    if snapshot["training_evidence"] or snapshot["status"] not in WAIT:
        return "NEEDS_RECONCILIATION"
    return "READY_FOR_TICK"


def status(config_path, expected_sha, qa_path, qa_sha):
    config = load_config(config_path, expected_sha, qa_path, qa_sha)
    state = Path(config["state_root"])
    snapshot = read_study_state(config["study_root"])
    last = None
    if state.exists():
        _, last = _history(state, _binding(config_path, expected_sha, qa_path, qa_sha), config)
    return {"status": _admission(snapshot, last), "study_status": snapshot["status"],
            "state_root": str(state), "read_only": True}


def tick(config_path, expected_sha, qa_path, qa_sha):
    config = load_config(config_path, expected_sha, qa_path, qa_sha)
    binding = _binding(config_path, expected_sha, qa_path, qa_sha)
    state = Path(config["state_root"])
    state.mkdir(mode=0o700, exist_ok=True)
    _sync(state.parent)
    fd = os.open(state / "dispatch.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "DISPATCH_LOCK_BUSY", "child_started": False}
        os.set_inheritable(fd, True)
        identity = state / "CONFIG_IDENTITY.json"
        if not identity.exists():
            if set(p.name for p in state.iterdir()) != {"dispatch.lock"}:
                raise ValueError("unbound nonempty dispatcher state")
            _write(identity, binding)
        attempts, last = _history(state, binding, config)
        before = read_study_state(config["study_root"])
        decision = _admission(before, last)
        if decision != "READY_FOR_TICK":
            return {"status": decision, "child_started": False}
        attempt = state / f"attempt_{len(attempts) + 1:06d}"
        attempt.mkdir(mode=0o700)
        _sync(state)
        prior = _sha(_raw(attempts[-1] / "COMPLETION.json")) if attempts else None
        intent = {"schema": "bb_native_dispatch_intent.v1", "identity": binding,
                  "created_utc": _utc(), "pid": os.getpid(), "uid": os.getuid(),
                  "prior_completion_sha256": prior, "before": before,
                  "argv": ["/bin/bash", config["wrapper"]["path"]]}
        intent_sha = _write(attempt / "INTENT.json", intent)
        result_code, outcome, reason, after = None, "NEEDS_RECONCILIATION", None, None
        with (attempt / "stdout.log").open("xb") as stdout, (attempt / "stderr.log").open("xb") as stderr:
            try:
                if load_config(config_path, expected_sha, qa_path, qa_sha) != config:
                    raise ValueError("configuration changed before spawn")
                if read_study_state(config["study_root"]) != before:
                    raise ValueError("study changed before spawn")
                child = subprocess.run(intent["argv"], cwd=str(Path(config["wrapper"]["path"]).parent),
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       pass_fds=(fd,), check=False,
                                       env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"})
                result_code = child.returncode
                stdout.flush()
                after = read_study_state(config["study_root"])
                outcome = classify_result(result_code, _raw(attempt / "stdout.log"), before, after)
            except Exception as error:
                reason = type(error).__name__ + ": " + str(error)
            for log in (stdout, stderr):
                log.flush()
                os.fsync(log.fileno())
        receipt = {"schema": "bb_native_dispatch_completion.v1", "status": outcome,
                   "intent_sha256": intent_sha, "completed_utc": _utc(),
                   "returncode": result_code, "reason": reason,
                   "after": after,
                   "stdout": _pin(attempt / "stdout.log"), "stderr": _pin(attempt / "stderr.log"),
                   "automatic_retry_allowed": outcome in WAIT}
        _write(attempt / "COMPLETION.json", receipt)
        return {**receipt, "attempt": str(attempt)}
    finally:
        # Never LOCK_UN: a surviving child shares this open-file-description.
        os.close(fd)


def render_launchd(config_path, expected_sha, qa_path, qa_sha, *, python, label, interval_seconds=300):
    config = load_config(config_path, expected_sha, qa_path, qa_sha)
    executable = Path(python)
    if (not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+", label) or
            type(interval_seconds) is not int or interval_seconds < 60):
        raise ValueError("absolute executable, ordinary label, interval >=60 seconds required")
    if str(executable) != _json(_raw(config["request"]["path"]))["environment"]["python"]:
        raise ValueError("scheduler must use the independent frozen research environment")
    return plistlib.dumps({"Label": label, "ProgramArguments": [str(executable), "-I", "-B", "-S",
        str(Path(__file__).resolve()), "tick", "--config", str(_path(config_path)),
        "--config-sha256", expected_sha, "--qa-receipt", str(_path(qa_path)), "--qa-sha256", qa_sha],
        "StartInterval": interval_seconds, "RunAtLoad": False, "KeepAlive": False,
        "AbandonProcessGroup": True, "ProcessType": "Background"}, sort_keys=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("tick", "status", "check-config", "render-launchd"))
    for name in ("config", "config-sha256", "qa-receipt", "qa-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--python")
    parser.add_argument("--label")
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    positional = (args.config, args.config_sha256, args.qa_receipt, args.qa_sha256)
    try:
        if args.action == "render-launchd":
            sys.stdout.buffer.write(render_launchd(*positional, python=args.python,
                label=args.label, interval_seconds=args.interval_seconds))
            return 0
        if args.action == "check-config":
            load_config(*positional)
            result = {"status": "CONFIG_VALID", "automatic_trigger": "NOT_INSTALLED"}
        else:
            result = (tick if args.action == "tick" else status)(*positional)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result["status"] in WAIT | {COMPLETE, "CONFIG_VALID", "READY_FOR_TICK", "DISPATCH_LOCK_BUSY"} else 2
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "NO_GO", "reason": type(error).__name__ + ": " + str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
