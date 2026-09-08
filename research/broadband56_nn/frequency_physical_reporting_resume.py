"""Read-only successor of the frozen original320 incremental report consumer.

Dependencies must already be closed and pinned. This module does not install a
waiter, launch physics, alter a scientific budget, or repair an old output. It
seeds the original terminal publication and only reports newly closed requests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

SCHEMA = "frequency_physical_reporting_resume.v1"
BASE_CONFIG_SHA = "e916237fe42d3bde521175f8cc7785e0b4c52c52574ee26fbf407176c2140b18"
CONSUMER_SHA = "b33bbcc974dd6d7e7035313cb40732de005a4c9becf50c922c80d0c9ebbcb14e"
READER_SHA = "2ed0d53754922f8383c83a8d71041aa2e7c5720c1289bb2e463221044d2c7a8c"


def require(test, message):
    if not test:
        raise ValueError(message)


def pin(path):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def checked(item):
    require(pin(item["path"]) == item, "Input changed: " + item["path"])
    return Path(item["path"])


def read(item):
    value = json.loads(checked(item).read_text())
    checked(item)
    return value


def now():
    return datetime.now(timezone.utc)


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Explicit timezone required")
    return result


def load_consumer(item):
    path = checked(item)
    require(item["sha256"] == CONSUMER_SHA, "Not the original frozen consumer")
    spec = importlib.util.spec_from_file_location("frozen_reporting_resume_consumer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checked(item)
    return module


def seeded_publication(final, config):
    """Validate closed local evidence; never follow a remote source path."""
    consumer = config["consumer"]
    base = config["original"]
    n = final.get("N_accounted_requests")
    require(final.get("status") == "PARTIAL" and final.get("error") is None and
            final.get("end_reason") in ("DEADLINE_PARTIAL", "PHYSICAL_QUEUE_ENDED_PARTIAL"),
            "Only a natural original partial terminal can seed a successor")
    require(type(n) is int and 0 < n < 320 and final.get("N_original_requests") == 320 and
            final.get("N_original_candidates") == 3520 and final.get("N_pending_requests") == 320 - n,
            "Original320/3520 terminal denominator differs")
    require(final.get("configuration") == base["config_pin"] and
            final.get("remote_modified") is False and final.get("simulation_or_training_started") is False,
            "Original reporting terminal identity differs")
    require(final.get("latest_snapshot") is not None, "No genuine published baseline")
    snapshot = read(final["latest_snapshot"])
    require(snapshot.get("status") == "PUBLISHED" and snapshot.get("remote_modified") is False,
            "Original snapshot not published")
    publication = read(snapshot["publication"])
    require(publication.get("schema") == "frequency_physical_capture_publication.v1" and
            publication.get("status") == "PUBLISHED", "Original capture publication not closed")
    stats, figures = read(snapshot["statistics"]), read(snapshot["figures"])
    require(stats.get("schema") == "frequency_physical_statistics_receipt.v1" and
            stats.get("status") == "PUBLISHED" and stats.get("N_accounted_requests") == n and
            stats.get("N_original_candidates") == 3520, "Original statistics terminal differs")
    require(figures.get("schema") == "frequency_physical_statistics_figures_receipt.v1" and
            figures.get("status") == "COMPLETE" and figures.get("statistics") == snapshot["statistics"] and
            figures.get("N_accounted_requests") == n and figures.get("N_original_requests") == 320 and
            figures.get("N_original_candidates") == 3520, "Previous figure/statistics identity differs")
    manifest = read(stats["manifest"])
    require(manifest.get("schema") == "frequency_physical_statistics_manifest.v1", "Wrong statistics manifest")
    for item in manifest["artifacts"]:
        checked(item)
    checked(stats["summary"]); checked(stats["sha256sums"])
    jobs = {j["request_id"]: j for j in base["jobs"]}
    accepted = {}
    for item in publication["captures"]:
        capture = read(item)
        summary = capture["summary"]
        rid = summary["request_id"]
        require(rid in jobs and rid not in accepted, "Unknown or duplicate seeded capture")
        job = jobs[rid]
        require(all(summary[k] == job[k] for k in
                    ("request_id", "frequency_ghz", "model_id", "dataset_scope", "q_proxy")) and
                summary["N_original"] == 11 and [r["q"] for r in summary["candidates"]] == list(range(10, 21)),
                "Seeded original eleven/context differs")
        originals = base["remote"]["originals"][rid]
        require([r["candidate_id"] for r in summary["candidates"]] ==
                [r["candidate_id"] for r in originals], "Seeded candidate identities differ")
        for item_file in capture["files"]:
            local, remote = item_file["local"], item_file["remote"]
            checked(local)
            require(all(local[k] == remote[k] for k in ("sha256", "bytes")), "Seeded relocation differs")
        accepted[rid] = item
    expected_ids = [j["request_id"] for j in base["jobs"] if j["request_id"] in accepted]
    require(len(accepted) == n and list(accepted) == expected_ids, "Seeded count/order differs")
    figure_ids = [r["request_id"] for r in figures["request_figures"]]
    require(len(figure_ids) == len(set(figure_ids)) == n and set(figure_ids) == set(accepted),
            "Previous request figure coverage differs")
    for entry in figures["request_figures"]:
        if entry.get("source"):
            checked(entry["source"])
        for export in entry["exports"]:
            checked(export)
    return accepted, final["latest_snapshot"], snapshot["figures"]


def check_local_exit(config):
    """Exact producer-owned receipts plus absence, not a process signal."""
    consumer, original = config["consumer"], config["original"]
    request = config["request"]
    launch = read(request["original_launch"])
    require(launch["configuration"] == original["config_pin"] and
            Path(request["original_launch"]["path"]) == Path(original["output"]) / "LAUNCH.json",
            "Original launch is not producer-owned")
    require(type(launch["pid"]) is int and launch["pid"] > 1 and
            consumer.current_process(launch["pid"]) is None, "Original consumer has not exited")
    once = read(request["reportonce_terminal"])
    once_config = read(once["configuration"])
    once_launch = read(request["reportonce_launch"])
    require(once_config["schema"] == "frequency_physical_export_once.v1" and
            once_config["producer_configuration"] == original["config_pin"] and
            once_config["producer"]["pid"] == launch["pid"] and
            Path(once_config["final_receipt"]) == Path(request["original_final"]["path"]),
            "Report-once belongs to another producer")
    require(once_launch["configuration"] == once["configuration"] and
            Path(request["reportonce_launch"]["path"]) == Path(once_config["wait_output"]) / "WAIT_LAUNCH_RECEIPT.json",
            "Report-once launch differs")
    success = once.get("status") == "FINITE_EXPORT_EXITED_ZERO_REVIEW_REQUIRED"
    failed = once.get("status") == "FAIL_PRESERVED_NO_RETRY"
    name = "ONCE_RECEIPT.json" if success else "FAILURE_RECEIPT.json"
    require((success or failed) and Path(request["reportonce_terminal"]["path"]) ==
            Path(once_config["wait_output"]) / name, "Report-once is not genuinely terminal")
    if success:
        require(once["final_receipt"] == request["original_final"], "Report-once consumed another final")
    require(type(once_launch["pid"]) is int and once_launch["pid"] > 1 and
            consumer.current_process(once_launch["pid"]) is None, "Report-once has not exited")
    return once["status"]


def remote_pin(item):
    """Shape check only: remote paths are verified by the frozen remote reader."""
    require(set(item) == {"path", "sha256", "bytes"} and Path(item["path"]).is_absolute() and
            type(item["bytes"]) is int and item["bytes"] >= 0 and
            isinstance(item["sha256"], str) and len(item["sha256"]) == 64 and
            all(c in "0123456789abcdef" for c in item["sha256"]), "Malformed native source pin")


def native_identity(item, original):
    launch = read(item)
    require(launch.get("schema") == "frequency_physical_resume_child_launch.v1" and
            launch.get("status") == "EXISTING_DISPATCHER_STARTED_NOT_COMPLETION",
            "Not an actual native successor child launch")
    require(all(type(launch.get(k)) is int and launch[k] > 0 for k in ("pid", "uid", "start_ticks"))
            and launch["pid"] > 1, "Native child identity unavailable; do not invent one")
    remote = original["remote"]
    require(launch["base_config"] == remote["remote_config"], "Native successor changed original config")
    remote_pin(launch["base_config"]); remote_pin(launch["operational_budget"])
    remote_pin(launch["waiter_configuration"]); remote_pin(launch["dispatch_intent"])
    require((launch["pid"], launch["start_ticks"]) != (remote["queue_pid"], remote["queue_start_ticks"]),
            "Launch is the old physical process, not its successor")
    command = launch["command"]
    require(isinstance(command, list) and all(isinstance(x, str) for x in command), "Native argv must be explicit")
    require(len(command) == 8 and command[1:4] ==
            ["-B", "-m", "research.broadband56_nn.frequency_physical_dispatch"],
            "Native command is not the existing dispatcher")
    for option, expected in (("--config", launch["base_config"]["path"]),
                             ("--operational-budget", launch["operational_budget"]["path"])):
        require(command.count(option) == 1 and command.index(option) + 1 < len(command) and
                command[command.index(option) + 1] == expected, "Native command identity differs")
    release = launch["release"]
    require(set(release) == {"code_root", "source_pins"} and Path(release["code_root"]).is_absolute(),
            "Malformed native release identity")
    names = {"research/__init__.py", "research/broadband56_nn/__init__.py",
             *{"research/broadband56_nn/" + x for x in ("io.py", "frequency_physical_dispatch.py",
               "frequency_research_emx.py", "frequency_research_gds_audit.py", "frequency_research_calibre.py")}}
    require(len(release["source_pins"]) == len(names) and
            {p["path"] for p in release["source_pins"]} == {str(Path(release["code_root"]) / x) for x in names},
            "Native release seven-file closure differs")
    for source in release["source_pins"]:
        remote_pin(source)
    return launch


def load_config(path):
    config_pin = pin(path)
    request = read(config_pin)
    require(request.get("schema") == SCHEMA, "Wrong successor reporting schema")
    require(checked(request["implementation"]) == Path(__file__).resolve(), "Successor implementation differs")
    require(request["original_configuration"]["sha256"] == BASE_CONFIG_SHA, "Not original frozen base config")
    original_value = read(request["original_configuration"])
    consumer = load_consumer(original_value["consumer_source"])
    # Do not overlay deadline/PID/output into the original scientific validator.
    original = consumer.load_config(checked(request["original_configuration"]))
    require(original["config_pin"] == request["original_configuration"] and
            original["reporting_remote_reader"]["sha256"] == READER_SHA, "Frozen reader/config differs")
    require(Path(request["original_final"]["path"]) == Path(original["output"]) / "FINAL_RECEIPT.json",
            "Final is not original consumer-owned")
    config = dict(request=request, config_pin=config_pin, consumer=consumer, original=original)
    once_status = check_local_exit(config)
    final = read(request["original_final"])
    accepted, latest, previous = seeded_publication(final, config)
    native = native_identity(request["native_launch"], original)
    deadline = utc(request["report_deadline_utc"])
    require(deadline > utc(original["deadline_utc"]), "Successor report deadline must be separately later")
    out = Path(request["output"])
    require(out.is_absolute() and not out.exists() and out.parent.is_dir(), "Fresh output with existing parent required")
    inputs = [config_pin, *[request[k] for k in ("implementation", "original_configuration", "original_final",
              "original_launch", "reportonce_terminal", "reportonce_launch", "native_launch")]]
    require(not any(Path(p["path"]).resolve().is_relative_to(out.resolve()) for p in inputs),
            "Output cannot contain an input")
    remote = dict(original["remote"], queue_pid=native["pid"], queue_start_ticks=native["start_ticks"])
    require(remote["remote_reader_source"] == checked(original["reporting_remote_reader"]).read_text(),
            "Remote reader source was replaced")
    return dict(config, accepted=accepted, latest=latest, previous=previous, native=native, remote=remote,
                deadline=deadline, output=out, input_pins=inputs, reportonce_status=once_status)


def preflight(path, clock=now):
    config = load_config(path)
    require(clock() < config["deadline"], "Successor report budget expired")
    return dict(status="LOCAL_READ_ONLY_SUCCESSOR_PREFLIGHT_PASS", configuration=config["config_pin"],
                N_original_requests=320, N_original_candidates=3520, N_seeded_requests=len(config["accepted"]),
                previous_figures=config["previous"], latest_snapshot=config["latest"],
                reportonce_status=config["reportonce_status"], native_launch=config["request"]["native_launch"],
                same_reporting_lock=config["original"]["lock_path"], remote_calls=0, output_created=False,
                baseline_rerender=False, native_launch_is_not_completion=True)


def run(path, sleep=time.sleep, clock=now):
    config = load_config(path)
    require(clock() < config["deadline"], "Successor report budget expired")
    consumer, original, out = config["consumer"], config["original"], config["output"]
    lock = Path(original["lock_path"])
    require(lock.is_file() and not lock.is_symlink(), "Original reporting lock must already exist")
    fd = os.open(lock, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status="ALREADY_RUNNING_NO_DUPLICATE", output_created=False)
        # Repeat the actual exit check under the original reporting lease.
        check_local_exit(config)
        for item in config["input_pins"]:
            checked(item)
        out.mkdir()
        for name in ("publications", "snapshots", "remote_captures"):
            (out / name).mkdir()
        accepted = dict(config["accepted"])
        latest, previous = config["latest"], config["previous"]
        consumer.save(out / "LAUNCH.json", dict(pid=os.getpid(), configuration=config["config_pin"],
            original_configuration=original["config_pin"], original_final=config["request"]["original_final"],
            native_launch=config["request"]["native_launch"], N_seeded_requests=len(accepted),
            previous_figures=previous, latest_snapshot=latest, created_utc=clock().isoformat(),
            role="READ_ONLY_REPORTING_SUCCESSOR_NOT_PHYSICS_OWNER", baseline_rerender=False, remote_modified=False))
        index = checks = 0
        reason, error = "DEADLINE_PARTIAL", None
        try:
            while clock() < config["deadline"]:
                for item in config["input_pins"] + original["source_pins"]:
                    checked(item)
                observer = original["observer"]
                state = observer.remote_call(config["remote"], "snapshot", fd=fd)
                checks += 1
                require([r["request_id"] for r in state["requests"]] == [j["request_id"] for j in original["jobs"]]
                        and type(state["queue_alive"]) is bool, "Remote original320 metadata order differs")
                consumer.save(out / f"REMOTE_CHECK_{checks:06d}.json", state)
                for item in state["requests"]:
                    rid = item["request_id"]
                    if rid in accepted:
                        require(read(accepted[rid])["summary"]["receipt"] == item["receipt"],
                                "Previously published remote terminal changed")
                        continue
                    if item["receipt"] is None:
                        continue
                    observer.collect(config["remote"], item, out / "remote_captures", fd,
                                     caller=observer.remote_call)
                    capture = pin(out / "remote_captures" / "completed" / rid / "CAPTURE.json")
                    require(read(capture)["summary"]["request_id"] == rid, "New capture request differs")
                    for source in config["input_pins"] + original["source_pins"]:
                        checked(source)
                    candidate = dict(accepted, **{rid: capture})
                    index += 1
                    entries = [candidate[j["request_id"]] for j in original["jobs"] if j["request_id"] in candidate]
                    publication = consumer.publish_capture_set(out, entries,
                        [config["request"]["original_final"], config["config_pin"], config["request"]["native_launch"]], index)
                    # Reuse the unchanged producer and its unchanged scientific config.
                    next_latest, next_previous = consumer.produce(original, publication, out, index, previous)
                    for source in config["input_pins"] + original["source_pins"]:
                        checked(source)
                    accepted, latest, previous = candidate, next_latest, next_previous
                    print(json.dumps(dict(stage="SUCCESSOR_NEW_REQUEST_PUBLISHED", request_id=rid,
                                          N_accounted_requests=len(accepted), snapshot=latest)), flush=True)
                if len(accepted) == 320:
                    reason = "ALL320_ACCOUNTED"
                    break
                if not state["queue_alive"]:
                    reason = "PHYSICAL_QUEUE_ENDED_PARTIAL"
                    break
                sleep(min(120, max(0, (config["deadline"] - clock()).total_seconds())))
        except Exception as exc:
            reason, error = "STOPPED_WITH_EVIDENCE_ERROR", type(exc).__name__ + ": " + str(exc)
        result = dict(status="COMPLETE" if len(accepted) == 320 and error is None else "PARTIAL",
            end_reason=reason, error=error, N_original_requests=320, N_original_candidates=3520,
            N_accounted_requests=len(accepted), N_pending_requests=320 - len(accepted), latest_snapshot=latest,
            configuration=config["config_pin"], original_configuration=original["config_pin"],
            original_final=config["request"]["original_final"], native_launch=config["request"]["native_launch"],
            N_seeded_requests=len(config["accepted"]), N_new_requests=len(accepted) - len(config["accepted"]),
            N_new_snapshots=len(accepted) - len(config["accepted"]), report_deadline_utc=config["request"]["report_deadline_utc"],
            created_utc=clock().isoformat(), remote_modified=False, simulation_or_training_started=False,
            baseline_rerender=False, automatic_retry=False, visual_acceptance="NOT_IMPLIED")
        consumer.save(out / "FINAL_RECEIPT.json", result)
        return result
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    print(json.dumps(preflight(args.request) if args.preflight else run(args.request),
                     sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
