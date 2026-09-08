"""One bounded, read-only dependency handoff to the frozen reporting successor.

Wait for the two exact local producers to exit, then mirror only the configured
native launch receipt. Never starts/signals physics, retries reporting, changes
science, or creates a same-data baseline. No installation is performed here.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from . import frequency_physical_export_once as once
from . import frequency_physical_reporting_resume as reporter
from .io import read_json, save_json

SCHEMA = "frequency_physical_reporting_handoff_once.v1"
ONCE_SHA = "eb8224a3f6d3d7482a2af285f920e399bf0da7f953206cd0f866ebf62489a823"
REPORTER_SHA = "caf500fd07592caf33142e1cf3683af1fd5c455ac79a917ab36ecea83b56f0c1"
KEYS = {"schema", "once_configuration", "once_process", "nativewait_configuration",
        "remote_child_receipt_path", "ssh_argv", "remote_python", "source_pins", "python", "cwd",
        "wait_output", "reporter_output", "wait_deadline_utc", "report_deadline_utc", "poll_seconds"}

# Literal read-only Python, sent over configured SSH stdin. No imports of native
# dispatch code, subprocesses, writes, signals, globbing, or scientific files.
REMOTE_READ = r'''
import base64, hashlib, json
from pathlib import Path
import sys
a=json.load(sys.stdin)
def item(path, published=False):
    p=Path(path)
    if not p.is_file(): return None
    raw=p.read_bytes()
    if len(raw)>4*1024*1024: raise ValueError("Receipt exceeds bounded size")
    if published and not raw.endswith(b'\n'): return "UNPUBLISHED"
    if p.read_bytes()!=raw: return "UNPUBLISHED"
    try: value=json.loads(raw)
    except json.JSONDecodeError: return "UNPUBLISHED"
    return {"remote":{"path":str(p),"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)},
            "base64":base64.b64encode(raw).decode(),"value":value}
try:
    expected=a["nativewait_configuration"]
    cfg=item(expected["path"])
    if cfg is None or cfg=="UNPUBLISHED":
        result={"state":"PENDING","reason":"NATIVE_WAITER_CONFIGURATION_NOT_CLOSED"}
    else:
        if cfg["remote"]!=expected: raise ValueError("Native waiter config pin changed")
        if cfg["value"].get("schema")!="frequency_physical_resume_once.v1": raise ValueError("Wrong native waiter schema")
        child=Path(a["remote_child_receipt_path"])
        if child.name!="CHILD_LAUNCH_RECEIPT.json" or child.parent!=Path(cfg["value"]["out"]):
            raise ValueError("Child not owned by exact configured native waiter")
        found=item(child, published=True)
        if found is not None and found!="UNPUBLISHED":
            if found["value"].get("waiter_configuration")!=expected: raise ValueError("Child waiter binding changed")
            result={"state":"CHILD","artifacts":[found]}
        else:
            ends=[]
            for name in ("FAILURE.json","TERMINAL.json"):
                terminal=item(child.parent/name, published=True)
                if terminal is not None and terminal!="UNPUBLISHED":
                    if terminal["value"].get("config")!=expected: raise ValueError("Native terminal waiter binding changed")
                    ends.append(terminal)
            result={"state":"NATIVE_TERMINAL_WITHOUT_CHILD","artifacts":ends} if ends else {"state":"PENDING","reason":"CHILD_NOT_CLOSED"}
except Exception as exc:
    result={"state":"EVIDENCE_ERROR","error":type(exc).__name__+": "+str(exc)}
print(json.dumps(result,allow_nan=False))
'''


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc)


def read_pin(item):
    value = read_json(once.checked(item))
    once.checked(item)
    return value


def check_sources(config, identity):
    once.checked(identity)
    for source in config["source_pins"]:
        once.checked(source)


def ssh_shape(argv):
    require(isinstance(argv, list) and len(argv) >= 2 and all(isinstance(x, str) for x in argv)
            and Path(argv[0]).name == "ssh", "Explicit SSH argv required")
    index, settings = 1, []
    while index < len(argv) - 1:
        flag = argv[index]
        require(flag in ("-S", "-o", "-p", "-J") and index + 1 < len(argv) - 1,
                "Only connection options, never SSH commands/forwarding")
        value = argv[index + 1]
        if flag == "-o":
            require(value in ("BatchMode=yes", "StrictHostKeyChecking=yes") or
                    value.startswith("ConnectTimeout=") and value.split("=", 1)[1].isdigit() and
                    1 <= int(value.split("=", 1)[1]) <= 30, "Unsafe or unsupported SSH option")
            settings.append(value)
        index += 2
    require(index == len(argv) - 1 and not argv[-1].startswith("-") and
            not any(c.isspace() for c in argv[-1]) and
            "BatchMode=yes" in settings and "StrictHostKeyChecking=yes" in settings,
            "Noninteractive strict-host SSH required")


def load_config(path):
    identity = once.pin(path)
    config = read_pin(identity)
    require(set(config) == KEYS and config["schema"] == SCHEMA, "Wrong handoff config schema/keys")
    require(type(config["poll_seconds"]) is int and 10 <= config["poll_seconds"] <= 60,
            "Ordinary polling must be bounded at 10..60 seconds")
    once_config = read_pin(config["once_configuration"])
    require(once_config["schema"] == "frequency_physical_export_once.v1", "Not the original export-once config")
    base = read_pin(once_config["producer_configuration"])
    require(base["schema"] == "frequency_incremental_statistics_consumer.v1" and
            once_config["producer_configuration"]["sha256"] == reporter.BASE_CONFIG_SHA,
            "Not the original frozen reporting configuration")
    require(Path(once_config["final_receipt"]).resolve() == Path(base["output"]).resolve() / "FINAL_RECEIPT.json",
            "Original final path differs")
    for process in (once_config["producer"], config["once_process"]):
        require(set(process) == {"pid", "uid", "lstart", "args_sha256"} and
                type(process["pid"]) is int and process["pid"] > 1 and process["uid"] == os.getuid()
                and isinstance(process["lstart"], str) and process["lstart"] and
                len(process["args_sha256"]) == 64, "Exact current-user process identity required")
    require(once_config["producer"]["pid"] != config["once_process"]["pid"], "Different local producers required")
    reporter.remote_pin(config["nativewait_configuration"])
    child = Path(config["remote_child_receipt_path"])
    require(child.is_absolute() and child.name == "CHILD_LAUNCH_RECEIPT.json", "Explicit future native child path required")
    ssh_shape(config["ssh_argv"])
    require(Path(config["remote_python"]).is_absolute(), "Explicit absolute remote Python required")
    require(Path(config["python"]).resolve() == Path(sys.executable).resolve() and
            Path(config["cwd"]).resolve() == Path(__file__).resolve().parents[2], "Use the configured Python and worktree")
    wait_out, report_out = Path(config["wait_output"]), Path(config["reporter_output"])
    require(wait_out.is_absolute() and report_out.is_absolute() and wait_out.parent.is_dir() and report_out.parent.is_dir()
            and not wait_out.exists() and not report_out.exists() and
            not wait_out.is_relative_to(report_out) and not report_out.is_relative_to(wait_out),
            "Fresh separate outputs with existing parents required; no retries")
    pins = {p["path"]: p for p in config["source_pins"]}
    require(len(pins) == len(config["source_pins"]), "Duplicate source pin")
    expected = {str(Path(__file__).resolve()): None, str(Path(once.__file__).resolve()): ONCE_SHA,
                str(Path(reporter.__file__).resolve()): REPORTER_SHA,
                str(Path(__file__).parent / "io.py"): None, str(Path(__file__).parent / "__init__.py"): None}
    require(set(expected) <= set(pins), "Missing actual reused source closure")
    for source, checksum in expected.items():
        require(checksum is None or pins[source]["sha256"] == checksum, "Reused implementation is not frozen")
    check_sources(config, identity)
    wait_deadline, report_deadline = reporter.utc(config["wait_deadline_utc"]), reporter.utc(config["report_deadline_utc"])
    require(report_deadline > wait_deadline > reporter.utc(base["deadline_utc"]), "Separate later wait/report deadlines required")
    return config, identity, once_config, base, wait_deadline


def local_alive(config, once_config):
    return [once.same_producer(p) for p in (once_config["producer"], config["once_process"])]


def preflight(path):
    config, identity, once_config, _, deadline = load_config(path)
    require(now() < deadline, "Handoff wait budget expired")
    return dict(status="READ_ONLY_HANDOFF_READY_TO_WAIT", configuration=identity,
                producer_alive=local_alive(config, once_config), remote_calls=0, output_created=False,
                reporter_calls=0, physics_started=False)


def remote_read(config):
    payload = {key: config[key] for key in ("nativewait_configuration", "remote_child_receipt_path")}
    command = [*config["ssh_argv"], shlex.quote(config["remote_python"]) + " -B -c " + shlex.quote(REMOTE_READ)]
    try:
        result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                timeout=55, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(state="TRANSPORT_UNAVAILABLE", error=type(exc).__name__ + ": " + str(exc))
    if result.returncode:
        return dict(state="TRANSPORT_UNAVAILABLE", returncode=result.returncode, error=result.stderr[-1800:])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return dict(state="TRANSPORT_UNAVAILABLE", error="SSH stdout is not a complete receipt response")


def mirror(out, response, expected_waiter):
    """Preserve source bytes exactly; never fabricate or rewrite native metadata."""
    artifacts = response["artifacts"]
    require(1 <= len(artifacts) <= 2, "Unexpected number of native receipt artifacts")
    pins, names = [], set()
    for item in artifacts:
        remote = item["remote"]; reporter.remote_pin(remote)
        name = Path(remote["path"]).name
        require(name in ("CHILD_LAUNCH_RECEIPT.json", "FAILURE.json", "TERMINAL.json") and name not in names,
                "Unexpected or duplicate native receipt")
        names.add(name)
        raw = base64.b64decode(item["base64"], validate=True)
        require(len(raw) == remote["bytes"] and hashlib.sha256(raw).hexdigest() == remote["sha256"], "Native mirror byte identity differs")
        value = json.loads(raw)
        require(value.get("waiter_configuration" if name == "CHILD_LAUNCH_RECEIPT.json" else "config") == expected_waiter,
                "Native receipt belongs to another waiter")
        dest = out / "native" / name
        with dest.open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        pins.append(dict(remote=remote, local=once.pin(dest)))
    save_json(out / "NATIVE_MIRROR_RECEIPT.json", dict(artifacts=pins, raw_bytes_unchanged=True))
    return pins


def closed_local(config, once_config, base):
    final_pin = once.pin(once_config["final_receipt"])
    final = read_pin(final_pin)
    require(final["configuration"] == once_config["producer_configuration"] and
            final.get("N_original_requests") == 320 and final.get("N_original_candidates") == 3520 and
            final.get("simulation_or_training_started") is False and final.get("remote_modified") is False,
            "Original terminal identity/denominator differs")
    old_launch = once.pin(Path(base["output"]) / "LAUNCH.json")
    launch = read_pin(old_launch)
    require(launch["pid"] == once_config["producer"]["pid"] and
            launch["configuration"] == once_config["producer_configuration"], "Original producer launch identity differs")
    once_launch = once.pin(Path(once_config["wait_output"]) / "WAIT_LAUNCH_RECEIPT.json")
    launched = read_pin(once_launch)
    require(launched["configuration"] == config["once_configuration"] and launched["pid"] == config["once_process"]["pid"],
            "Once process launch differs")
    successes = Path(once_config["wait_output"]) / "ONCE_RECEIPT.json"
    failures = Path(once_config["wait_output"]) / "FAILURE_RECEIPT.json"
    require(successes.is_file() != failures.is_file(), "Once exited without a unique retained terminal")
    terminal_pin = once.pin(successes if successes.is_file() else failures)
    terminal = read_pin(terminal_pin)
    require(terminal["configuration"] == config["once_configuration"] and terminal["status"] ==
            ("FINITE_EXPORT_EXITED_ZERO_REVIEW_REQUIRED" if successes.is_file() else "FAIL_PRESERVED_NO_RETRY"),
            "Wrong report-once terminal")
    if successes.is_file():
        require(terminal["final_receipt"] == final_pin, "Once consumed another original final")
    if final["status"] == "COMPLETE":
        require(final.get("error") is None and final.get("end_reason") == "ALL320_ACCOUNTED" and
                final.get("N_accounted_requests") == 320 and final.get("N_pending_requests") == 0,
                "False original320 completion")
    else:
        require(final["status"] == "PARTIAL" and final.get("error") is None and
                final.get("end_reason") in ("DEADLINE_PARTIAL", "PHYSICAL_QUEUE_ENDED_PARTIAL"),
                "Original reporting error requires explicit review, not automatic bypass")
        n = final.get("N_accounted_requests")
        require(type(n) is int and 0 < n < 320 and final.get("N_pending_requests") == 320 - n and
                final.get("latest_snapshot") is not None, "Missing valid original partial snapshot/count")
        once.checked(final["latest_snapshot"])
    return final, final_pin, old_launch, once_launch, terminal_pin


def run(path, sleep=time.sleep, clock=now, reader=remote_read):
    config, identity, once_config, base, deadline = load_config(path)
    require(clock() < deadline, "Handoff wait budget expired")
    out = Path(config["wait_output"])
    out.mkdir()
    (out / "native").mkdir()
    calls = 0
    save_json(out / "WAIT_LAUNCH_RECEIPT.json", dict(configuration=identity, pid=os.getpid(),
              status="WAITING_READ_ONLY_DEPENDENCIES", created_utc=clock().isoformat(), max_reporter_invocations=1))
    try:
        while True:
            check_sources(config, identity)
            require(clock() < deadline, "Handoff wait deadline ended; no reporter launched")
            if not any(local_alive(config, once_config)):
                break
            sleep(min(config["poll_seconds"], max(0, (deadline - clock()).total_seconds())))
        final, final_pin, old_launch, once_launch, terminal_pin = closed_local(config, once_config, base)
        save_json(out / "LOCAL_EXIT_HANDOFF.json", dict(original_final=final_pin, original_launch=old_launch,
                  once_terminal=terminal_pin, once_launch=once_launch, exact_producers_exited=True))
        if final["status"] == "COMPLETE":
            result = dict(status="NO_WORK_ALREADY_COMPLETE", original_final=final_pin, configuration=identity,
                          reporter_calls=0, remote_calls=0, physics_started=False)
            save_json(out / "HANDOFF_RECEIPT.json", result)
            return result
        while True:
            check_sources(config, identity)
            require(clock() < deadline, "Handoff wait deadline ended; no reporter launched")
            response = reader(config)
            state = response.get("state")
            require(state in ("CHILD", "PENDING", "TRANSPORT_UNAVAILABLE", "EVIDENCE_ERROR", "NATIVE_TERMINAL_WITHOUT_CHILD"),
                    "Unknown read-only response state")
            with (out / "observations.jsonl").open("a") as stream:
                stream.write(json.dumps(dict(utc=clock().isoformat(), **{k: v for k, v in response.items() if k != "artifacts"})) + "\n")
            if state in ("CHILD", "NATIVE_TERMINAL_WITHOUT_CHILD"):
                mirrors = mirror(out, response, config["nativewait_configuration"])
                require(state == "CHILD", "Native waiter ended without an actual child launch; evidence retained")
                require(len(mirrors) == 1 and Path(mirrors[0]["remote"]["path"]) == Path(config["remote_child_receipt_path"]),
                        "Native child mirror path differs")
                child_pin = mirrors[0]["local"]
                break
            require(state != "EVIDENCE_ERROR", "Read-only native evidence error: " + response.get("error", "unknown"))
            sleep(min(config["poll_seconds"], max(0, (deadline - clock()).total_seconds())))
        check_sources(config, identity)
        require(clock() < deadline and not any(local_alive(config, once_config)), "Handoff admission changed")
        resolved = dict(schema=reporter.SCHEMA, implementation=once.pin(reporter.__file__),
            original_configuration=once_config["producer_configuration"], original_final=final_pin,
            original_launch=old_launch, reportonce_terminal=terminal_pin, reportonce_launch=once_launch,
            native_launch=child_pin, report_deadline_utc=config["report_deadline_utc"], output=config["reporter_output"])
        request = out / "RESOLVED_REPORTING_REQUEST.json"
        save_json(request, resolved)
        ready = reporter.preflight(request)
        save_json(out / "REPORTER_PREFLIGHT.json", ready)
        check_sources(config, identity)
        require(clock() < deadline, "Handoff admission ended after preflight; no reporter launched")
        save_json(out / "DISPATCH_INTENT.json", dict(configuration=identity, resolved_request=once.pin(request),
                  created_utc=clock().isoformat(), max_invocations=1, no_retry=True))
        calls = 1
        result = reporter.run(request)  # Ordinary reporter owns its later deadline; never time it out.
        receipt = dict(status="REPORTER_RETURNED_NOT_VISUAL_ACCEPTANCE", configuration=identity,
            resolved_request=once.pin(request), reporter_result=result, reporter_calls=calls,
            created_utc=clock().isoformat(), physics_started=False, remote_modified=False, visual_acceptance="NOT_IMPLIED")
        save_json(out / "HANDOFF_RECEIPT.json", receipt)
        return receipt
    except Exception as exc:
        save_json(out / "FAILURE_RECEIPT.json", dict(status="HANDOFF_FAILED_PRESERVED_NO_RETRY", configuration=identity,
            error=type(exc).__name__ + ": " + str(exc), reporter_calls=calls, created_utc=clock().isoformat(),
            physics_started=False, remote_modified=False))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(preflight(args.request) if args.check else run(args.request), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
