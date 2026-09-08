"""One local, read-only reporting successor for an exact resource-wait recovery.

Not installed by importing or preflighting this module. The original consumer,
reader, scientific configuration, statistics and figures are reused unchanged.
No physics commands, signals, baseline recaptures or baseline rendering occur.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import time

from . import frequency_physical_reporting_resume as prior

SCHEMA = "frequency_physical_reporting_recovery_once.v1"
PRIOR_SHA = "caf500fd07592caf33142e1cf3683af1fd5c455ac79a917ab36ecea83b56f0c1"
KEYS = {"schema", "implementation", "original_configuration", "predecessor_final",
        "predecessor_launch", "native_launch", "recovery_manifest", "report_deadline_utc", "output"}
require, pin, checked, read = prior.require, prior.pin, prior.checked, prior.read


def mirrored(item):
    require(set(item) == {"local", "remote"}, "Exact local/remote mirror pins required")
    prior.remote_pin(item["remote"])
    value = read(item["local"])
    require(all(item["local"][k] == item["remote"][k] for k in ("sha256", "bytes")),
            "Recovery manifest mirror differs")
    return value


def native_identity(request, original, previous):
    """Validate observed launch metadata; this is not a live remote observation."""
    native = read(request["native_launch"])
    require(native.get("schema") == "frequency_physical_resource_wait_recovery_launch.v1",
            "Not an observed recovery launch receipt")
    require(all(type(native.get(k)) is int and native[k] > 0 for k in ("pid", "uid", "start_ticks"))
            and native["pid"] > 1 and native["uid"] == previous["uid"]
            and (native["pid"], native["start_ticks"]) != (previous["pid"], previous["start_ticks"]),
            "Recovery native process identity differs")
    manifest = mirrored(request["recovery_manifest"])
    require(set(manifest) == {"schema", "base_config", "previous_operational_budget", "release",
                             "acknowledged_failures", "stages"}
            and manifest["schema"] == "frequency_physical_resource_wait_recovery.v1",
            "Wrong recovery manifest fields")
    require(native["base_config"] == manifest["base_config"] == original["remote"]["remote_config"]
            and native["operational_budget"] == manifest["previous_operational_budget"] == previous["operational_budget"]
            and native["resource_wait_recovery"] == request["recovery_manifest"]["remote"]
            and native["release"] == manifest["release"], "Recovery launch/manifest/scientific identity differs")
    for item in (native["base_config"], native["operational_budget"], native["resource_wait_recovery"]):
        prior.remote_pin(item)
    command = native["command"]
    require(isinstance(command, list) and len(command) == 10 and Path(command[0]).is_absolute()
            and command[0] == previous["command"][0] and command[1:] == ["-B", "-m",
                "research.broadband56_nn.frequency_physical_dispatch", "--config", native["base_config"]["path"],
                "--operational-budget", native["operational_budget"]["path"],
                "--resource-wait-recovery", native["resource_wait_recovery"]["path"]],
            "Only the exact ten-argument recovery dispatcher is supported")
    release, old = native["release"], previous["release"]
    require(set(release) == {"code_root", "source_pins"} and Path(release["code_root"]).is_absolute()
            and release["code_root"] != old["code_root"], "Recovery needs a distinct pinned release")
    def sources(value):
        result = {}
        for item in value["source_pins"]:
            prior.remote_pin(item)
            name = str(Path(item["path"]).relative_to(value["code_root"]))
            require(name not in result, "Duplicate release source")
            result[name] = item
        return result
    current, previous_sources = sources(release), sources(old)
    require(len(current) == len(previous_sources) == 7 and current.keys() == previous_sources.keys(),
            "Original seven-source closure differs")
    for name in current:
        if name != "research/broadband56_nn/frequency_physical_dispatch.py":
            require(all(current[name][k] == previous_sources[name][k] for k in ("sha256", "bytes")),
                    "Recovery may not replace the original scientific adapters")
    require(manifest["stages"] and manifest["acknowledged_failures"], "Missing preserved failure provenance")
    for item in manifest["acknowledged_failures"]:
        prior.remote_pin(item)
    for stage in manifest["stages"]:
        require(set(stage) == {"request", "intent", "process", "log", "preflight", "old_wrapper"},
                "Recovery stage provenance differs")
        for item in stage.values():
            prior.remote_pin(item)
    return native


def lineage(final_pin, original, consumer, inputs, launches, seen=None):
    """Follow actual FINAL -> CONFIG -> predecessor links, never synthetic finals."""
    seen = set() if seen is None else seen
    require(final_pin["path"] not in seen, "Cyclic predecessor lineage")
    seen.add(final_pin["path"])
    final = read(final_pin); config_pin = final["configuration"]; config = read(config_pin)
    inputs.extend((final_pin, config_pin))
    require(Path(final_pin["path"]) == Path(config["output"]) / "FINAL_RECEIPT.json",
            "Final is not owned by its actual reporting configuration")
    launch_pin = pin(Path(config["output"]) / "LAUNCH.json"); launch = read(launch_pin)
    require(launch["configuration"] == config_pin and type(launch["pid"]) is int and launch["pid"] > 1,
            "Predecessor launch/config binding differs")
    require(consumer.current_process(launch["pid"]) is None, "Predecessor reporting PID has not exited")
    launches.append(launch_pin); inputs.append(launch_pin)
    ancestor, native = None, None
    if config_pin == original["config_pin"]:
        require(config["schema"] == "frequency_incremental_statistics_consumer.v1", "Wrong original consumer")
    else:
        require(config.get("schema") in (prior.SCHEMA, SCHEMA)
                and config["original_configuration"] == original["config_pin"]
                and final["original_configuration"] == original["config_pin"], "Scientific configuration drift")
        checked(config["implementation"]); inputs.append(config["implementation"])
        expected_impl = prior.pin(prior.__file__) if config["schema"] == prior.SCHEMA else pin(__file__)
        require(config["implementation"] == expected_impl, "Unknown predecessor reporting implementation")
        predecessor_key = "original_final" if config["schema"] == prior.SCHEMA else "predecessor_final"
        require(final[predecessor_key] == config[predecessor_key]
                and final["native_launch"] == config["native_launch"]
                and final["report_deadline_utc"] == config["report_deadline_utc"], "Predecessor terminal binding differs")
        ancestor = lineage(config[predecessor_key], original, consumer, inputs, launches, seen)
        if config["schema"] == prior.SCHEMA:
            prior.check_local_exit(dict(request=config, original=original, consumer=consumer))
            inputs.extend(config[k] for k in ("original_launch", "reportonce_terminal", "reportonce_launch"))
            native = prior.native_identity(config["native_launch"], original)
        else:
            require(config["predecessor_launch"] == ancestor["launch"], "Recovery predecessor launch differs")
            require(config["report_deadline_utc"] == ancestor["config"]["report_deadline_utc"], "Report budget changed")
            native = native_identity(config, original, ancestor["native"])
            inputs.append(config["recovery_manifest"]["local"])
        inputs.append(config["native_launch"])
    # The reporting configuration is the genuine owner of this publication.
    # This read-only validation context is never passed to scientific produce().
    context = dict(original, config_pin=config_pin)
    accepted, latest, previous = prior.seeded_publication(final, dict(original=context, consumer=consumer))
    snapshot = read(latest); publication = read(snapshot["publication"])
    inputs.extend((latest, snapshot["publication"], snapshot["statistics"], snapshot["figures"]))
    if ancestor:
        require(all(accepted.get(k) == v for k, v in ancestor["accepted"].items()), "Published predecessor capture changed")
        require(final.get("N_seeded_requests") == len(ancestor["accepted"]), "Predecessor seed count differs")
        if latest != ancestor["latest"]:
            require(Path(latest["path"]).is_relative_to(Path(config["output"]) / "snapshots")
                    and config_pin in publication["source_receipts"]
                    and config[predecessor_key] in publication["source_receipts"], "Snapshot publication provenance differs")
    return dict(config=config, accepted=accepted, latest=latest, previous=previous, native=native, launch=launch_pin)


def load_config(path):
    identity = pin(path); request = read(identity)
    require(set(request) == KEYS and request["schema"] == SCHEMA, "Wrong recovery reporting request fields")
    require(request["implementation"] == pin(__file__), "Reporting recovery implementation changed")
    require(pin(prior.__file__)["sha256"] == PRIOR_SHA, "Original reporting validator changed")
    require(request["original_configuration"]["sha256"] == prior.BASE_CONFIG_SHA, "Wrong original science configuration")
    raw = read(request["original_configuration"]); consumer = prior.load_consumer(raw["consumer_source"])
    original = consumer.load_config(request["original_configuration"]["path"])
    require(original["reporting_remote_reader"]["sha256"] == prior.READER_SHA
            and original["remote"]["remote_reader_source"] == checked(original["reporting_remote_reader"]).read_text(),
            "Original read-only remote reader changed")
    inputs = [identity, request["implementation"], pin(prior.__file__), request["original_configuration"]]
    launches = []
    seed = lineage(request["predecessor_final"], original, consumer, inputs, launches)
    require(seed["native"] is not None and request["predecessor_launch"] == seed["launch"],
            "Exact successor predecessor launch required")
    require(request["report_deadline_utc"] == seed["config"]["report_deadline_utc"], "Existing report deadline must be retained")
    native = native_identity(request, original, seed["native"])
    inputs.extend((request["native_launch"], request["recovery_manifest"]["local"]))
    out = Path(request["output"])
    require(out.is_absolute() and out.parent.is_dir() and not out.exists(), "Fresh no-clobber output required")
    require(not any(Path(p["path"]).is_relative_to(out) for p in inputs), "Output would contain an input")
    return dict(request=request, config_pin=identity, original=original, consumer=consumer, seed=seed,
                launches=launches, inputs=inputs, native=native, output=out,
                deadline=prior.utc(request["report_deadline_utc"]),
                remote=dict(original["remote"], queue_pid=native["pid"], queue_start_ticks=native["start_ticks"]))


def preflight(path, clock=prior.now):
    config = load_config(path)
    require(clock() < config["deadline"], "Existing reporting budget expired")
    return dict(status="CANDIDATE_PREFLIGHT_PASS_NOT_INSTALLED", N_seeded_requests=len(config["seed"]["accepted"]),
                configuration=config["config_pin"], latest_snapshot=config["seed"]["latest"],
                previous_figures=config["seed"]["previous"], same_reporting_lock=config["original"]["lock_path"],
                remote_calls=0, output_created=False, baseline_rerender=False)


def run(path, sleep=time.sleep, clock=prior.now):
    config = load_config(path)
    require(clock() < config["deadline"], "Existing reporting budget expired")
    original, consumer, out = config["original"], config["consumer"], config["output"]
    lock = Path(original["lock_path"])
    require(lock.is_file() and not lock.is_symlink(), "Original reporting lease must exist")
    fd = os.open(lock, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status="ALREADY_RUNNING_NO_DUPLICATE", output_created=False)
        for p in config["launches"]:
            require(consumer.current_process(read(p)["pid"]) is None, "Predecessor reporting PID has not exited")
        for p in config["inputs"] + original["source_pins"]:
            checked(p)
        out.mkdir()
        for name in ("publications", "snapshots", "remote_captures"):
            (out / name).mkdir()
        accepted = dict(config["seed"]["accepted"])
        latest, previous = config["seed"]["latest"], config["seed"]["previous"]
        binding = dict(configuration=config["config_pin"], original_configuration=original["config_pin"],
                       predecessor_final=config["request"]["predecessor_final"], native_launch=config["request"]["native_launch"])
        consumer.save(out / "LAUNCH.json", dict(binding, pid=os.getpid(), N_seeded_requests=len(accepted),
            latest_snapshot=latest, previous_figures=previous, created_utc=clock().isoformat(),
            baseline_rerender=False, remote_modified=False, role="READ_ONLY_RECOVERY_REPORTER_NOT_PHYSICS_OWNER"))
        checks = index = 0
        reason, error = "DEADLINE_PARTIAL", None
        try:
            while clock() < config["deadline"]:
                for p in config["inputs"] + original["source_pins"]:
                    checked(p)
                observer = original["observer"]
                state = observer.remote_call(config["remote"], "snapshot", fd=fd)
                require(type(state["queue_alive"]) is bool and [x["request_id"] for x in state["requests"]] ==
                        [x["request_id"] for x in original["jobs"]], "Original320 metadata order differs")
                checks += 1; consumer.save(out / f"REMOTE_CHECK_{checks:06d}.json", state)
                for item in state["requests"]:
                    rid = item["request_id"]
                    if rid in accepted:
                        require(read(accepted[rid])["summary"]["receipt"] == item["receipt"], "Published remote receipt changed")
                        continue
                    if item["receipt"] is None:
                        continue
                    observer.collect(config["remote"], item, out / "remote_captures", fd, caller=observer.remote_call)
                    capture = pin(out / "remote_captures" / "completed" / rid / "CAPTURE.json")
                    require(read(capture)["summary"]["request_id"] == rid, "New capture identity differs")
                    for p in config["inputs"] + original["source_pins"]:
                        checked(p)
                    candidate = dict(accepted, **{rid: capture}); index += 1
                    entries = [candidate[j["request_id"]] for j in original["jobs"] if j["request_id"] in candidate]
                    publication = consumer.publish_capture_set(out, entries,
                        [config["request"]["predecessor_final"], config["config_pin"], config["request"]["native_launch"]], index)
                    # Original scientific config, statistics and figure functions only.
                    next_latest, next_previous = consumer.produce(original, publication, out, index, previous)
                    for p in config["inputs"] + original["source_pins"]:
                        checked(p)
                    accepted, latest, previous = candidate, next_latest, next_previous
                if len(accepted) == 320:
                    reason = "ALL320_ACCOUNTED"; break
                if not state["queue_alive"]:
                    reason = "PHYSICAL_QUEUE_ENDED_PARTIAL"; break
                sleep(min(120, max(0, (config["deadline"] - clock()).total_seconds())))
        except Exception as exc:
            reason, error = "STOPPED_WITH_EVIDENCE_ERROR", type(exc).__name__ + ": " + str(exc)
        result = dict(binding, status="COMPLETE" if len(accepted) == 320 and error is None else "PARTIAL",
            end_reason=reason, error=error, N_original_requests=320, N_original_candidates=3520,
            N_accounted_requests=len(accepted), N_pending_requests=320-len(accepted), latest_snapshot=latest,
            N_seeded_requests=len(config["seed"]["accepted"]), N_new_requests=len(accepted)-len(config["seed"]["accepted"]),
            N_new_snapshots=len(accepted)-len(config["seed"]["accepted"]),
            report_deadline_utc=config["request"]["report_deadline_utc"], created_utc=clock().isoformat(),
            remote_modified=False, simulation_or_training_started=False, baseline_rerender=False,
            automatic_retry=False, visual_acceptance="NOT_IMPLIED")
        consumer.save(out / "FINAL_RECEIPT.json", result)
        return result
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True); parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    print(json.dumps(preflight(args.request) if args.preflight else run(args.request), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
