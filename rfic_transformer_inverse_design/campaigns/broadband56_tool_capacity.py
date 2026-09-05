"""Derive measured trial budgets without launching a tool or changing a gate.

Sampled RSS/thread envelopes are observations, not absolute bounds for future
geometries. They complement live resource gates and do not authorize a launch.
Private startup binding supplies the exact sources and license-feature mapping.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .broadband56_capacity_policy import CapacityPolicyError
from .broadband56_native_telemetry import NATIVE_NAMES
from .broadband56_scheduling import TOOL_NAMES, _bound, _positive_int, _utc, file_record

FOOTPRINT_SCHEMA = "rfic_transformer.broadband56_measured_tool_footprints.v1"
SCOPE = "SAMPLED_NATIVE_RSS_AND_REPORTED_THREADS_NOT_ABSOLUTE_JOB_BOUNDS"
LICENSE_FEATURE_RE = re.compile(
    r"Users of ([^:]+):\s+\(Total of (\d+) licenses? issued;\s+Total of (\d+) licenses? in use\)", re.I)


def parse_license_counts(response: str) -> dict[str, dict[str, int]]:
    """FlexNet uses singular 'license' when exactly one seat is in use."""
    result = {}
    for feature, total, used in LICENSE_FEATURE_RE.findall(response):
        name, total, used = feature.strip().lower(), int(total), int(used)
        if name in result or used > total:
            raise CapacityPolicyError("duplicate or inconsistent license feature counts")
        result[name] = {"total": total, "used": used, "free": total - used}
    return result


def _verified(record: Mapping[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.is_absolute() or file_record(path) != dict(record):
        raise CapacityPolicyError("tool measurement source identity mismatch")
    return path


def derive_footprints(*, baseline_backend: Mapping[str, Any],
                      observations: list[Mapping[str, Any]],
                      thread_logs: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, Any]:
    """Reduce pinned raw samples; never trust a caller's peak-memory numbers."""
    _, backend = _bound(baseline_backend)
    tools = {tool: {"threads_per_job": 0, "peak_rss_bytes_per_job": 0,
                    "sampled_process_identities": set()} for tool in TOOL_NAMES}
    sources, times = {}, []

    def retain(record: Mapping[str, Any]) -> Path:
        if sources.get(record["path"]) == dict(record):
            return Path(record["path"])
        path = _verified(record)
        sources[str(path)] = dict(record)
        return path

    retain(baseline_backend)
    for record in observations:
        evidence = json.loads(retain(record).read_text())
        legacy = evidence.get("schema") == "rfic_transformer.broadband56_readonly_tool_observation.v1"
        native = evidence.get("schema") == "rfic_transformer.broadband56_native_role_observation.v1"
        if legacy:
            passed = evidence.get("overall_status") == "PASS_OBSERVATION_ONLY" and evidence.get("error") is None
            parent, sample_pin = evidence.get("backend"), evidence.get("sample_source")
        elif native:
            passed = evidence.get("observation_status") == "RECORDED" and not evidence.get("errors")
            parent, sample_pin = evidence.get("bindings", {}).get("backend"), evidence.get("samples")
        else:
            raise CapacityPolicyError("unsupported native measurement schema")
        if not passed or parent != baseline_backend:
            raise CapacityPolicyError("failed observation or mismatched measurement backend")
        samples = [json.loads(line) for line in retain(sample_pin).read_text().splitlines()]
        if not samples or len(samples) != evidence.get("sample_count"):
            raise CapacityPolicyError("missing or incomplete tool sample source")
        previous = None
        for sample in samples:
            stamp = _utc(sample["captured_utc"])
            if previous is not None and stamp <= previous:
                raise CapacityPolicyError("duplicate or unordered tool observation timestamps")
            previous = stamp
            times.append(stamp)
            sample_tools = {tool: [0, 0] for tool in TOOL_NAMES}
            seen = set()
            for process in sample["processes" if legacy else "native_processes"]:
                tool = process["tool"]
                if tool not in tools:
                    raise CapacityPolicyError("unexpected native tool measurement")
                if legacy:
                    binary = retain(process["executable"])
                    threads, rss = process["observed_threads"], process["high_water_bytes"]
                else:
                    binary = Path(process["executable_path"])
                    pin = sources.get(str(binary)) or file_record(binary)
                    if pin["sha256"] != process["executable_sha256"]:
                        raise CapacityPolicyError("observed native executable changed")
                    retain(pin)
                    threads, rss = process["threads"], process["VmHWM_bytes"]
                if NATIVE_NAMES.get(binary.name) != tool:
                    raise CapacityPolicyError("native executable does not match its tool")
                with binary.open("rb") as stream:
                    if stream.read(4) != b"\x7fELF":
                        raise CapacityPolicyError("measurement must identify a native ELF, not a wrapper")
                target = tools[tool]
                sample_tools[tool][0] += _positive_int(threads, "observed threads")
                sample_tools[tool][1] += _positive_int(rss, "observed RSS")
                identity = (
                    _positive_int(process["pid"], "observed PID"),
                    _positive_int(process["start_ticks"], "observed start ticks"))
                if identity in seen:
                    raise CapacityPolicyError("duplicate native identity in one sample")
                seen.add(identity)
                target["sampled_process_identities"].add(identity)
            # Reserve the whole observed tool tree, not just its largest child.
            for tool, (threads, rss) in sample_tools.items():
                tools[tool]["threads_per_job"] = max(tools[tool]["threads_per_job"], threads)
                tools[tool]["peak_rss_bytes_per_job"] = max(tools[tool]["peak_rss_bytes_per_job"], rss)
    for tool, records in thread_logs.items():
        if tool not in tools:
            raise CapacityPolicyError("unexpected thread-log tool")
        for record in records:
            values = re.findall(r"\bwith ([1-9][0-9]*) threads\b", retain(record).read_text())
            if not values:
                raise CapacityPolicyError("thread log contains no explicit thread count")
            tools[tool]["threads_per_job"] = max(tools[tool]["threads_per_job"], *map(int, values))
    for tool, values in tools.items():
        if not values["sampled_process_identities"]:
            raise CapacityPolicyError(f"native footprint not measured: {tool}")
        values["sampled_process_identities"] = [list(v) for v in sorted(values["sampled_process_identities"])]
    for record in sources.values():
        _verified(record)
    return {"schema": FOOTPRINT_SCHEMA, "campaign_id": backend["campaign_id"],
            "contract_fingerprint_sha256": backend["contract_fingerprint_sha256"],
            "baseline_backend": dict(baseline_backend), "tools": tools,
            "observed_from_utc": min(times).isoformat(), "observed_to_utc": max(times).isoformat(),
            "source_files": list(sources.values()), "measurement_scope": SCOPE,
            "derivation": {"baseline_backend": dict(baseline_backend),
                           "observations": observations, "thread_logs": thread_logs},
            "absolute_job_bounds_proven": False, "simulator_action_taken": False,
            "admission_authorized": False}


def materialize_capacity(*, snapshot: Mapping[str, Any], footprint_record: Mapping[str, Any],
                         license_queries: Mapping[str, Mapping[str, Any]],
                         license_features: Mapping[str, list[tuple[str, str]]],
                         out_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """Join immutable native observations to fresh, pinned read-only seat counts.

    The returned record can be attached by a bound probe before publication.
    Original snapshot resources, licenses, isolation and policy remain intact.
    """
    now = _utc(now or datetime.now(timezone.utc))
    captured = _utc(snapshot["captured_utc"])
    if not 0 <= (now - captured).total_seconds() <= 300:
        raise CapacityPolicyError("capacity source snapshot is stale or future dated")
    _, footprints = _bound(footprint_record)
    _bound(snapshot["supervisor_lease"])
    if (footprints.get("schema") != FOOTPRINT_SCHEMA or footprints.get("measurement_scope") != SCOPE
            or footprints.get("absolute_job_bounds_proven") is not False
            or any(footprints.get(k) != snapshot.get(k) for k in ("campaign_id", "contract_fingerprint_sha256"))
            or _utc(footprints["observed_to_utc"]) > captured):
        raise CapacityPolicyError("tool footprint scope, time or contract mismatch")
    for record in footprints["source_files"]:
        _verified(record)
    if derive_footprints(**footprints["derivation"]) != footprints:
        raise CapacityPolicyError("stored footprint differs from its raw measurements")
    queries = {}
    for name, record in license_queries.items():
        _, value = _bound(record)
        stamp = _utc(value["captured_utc"])
        if (value.get("mode") != "READ_ONLY_LMSTAT_NO_CHECKOUT" or value.get("returncode") != 0
                or value.get("server_up") is not True
                or not 0 <= (now - stamp).total_seconds() <= 150
                or abs((stamp - captured).total_seconds()) > 150):
            raise CapacityPolicyError("license query failed, stale or not read-only")
        for key in ("query_tool", "loader", "environment_source"):
            _verified(value[key])
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("response_sha256", ""))):
            raise CapacityPolicyError("missing raw license-response hash")
        queries[name] = value
    if set(license_features) != set(TOOL_NAMES):
        raise CapacityPolicyError("missing per-tool checkout feature mapping")
    observed = {}
    for tool in TOOL_NAMES:
        seats = []
        if not license_features[tool]:
            raise CapacityPolicyError("empty license-feature mapping")
        for query, feature in license_features[tool]:
            value = queries[query]["features"][feature]
            total, used, free = (value[k] for k in ("total", "used", "free"))
            if any(type(v) is not int or v < 0 for v in (total, used, free)) or total - used != free:
                raise CapacityPolicyError("inconsistent license seat counts")
            seats.append(free)
        values = footprints["tools"][tool]
        observed[tool] = {"free_seats": min(seats),
                          "threads_per_job": _positive_int(values["threads_per_job"], "native threads"),
                          "peak_rss_bytes_per_job": _positive_int(values["peak_rss_bytes_per_job"], "sampled RSS")}
    out_dir.mkdir(mode=0o700, exist_ok=False)
    tools = {}
    for tool, values in observed.items():
        path = out_dir / f"{tool}_measurement.json"
        with path.open("x") as stream:
            json.dump({"tool": tool, "observed": values, "footprints": dict(footprint_record),
                       "license_queries": dict(license_queries), "checkout_features": license_features[tool],
                       "measurement_scope": SCOPE}, stream, indent=2, sort_keys=True)
        tools[tool] = {"observed": values, "measurement_source": file_record(path)}
    path = out_dir / "TOOL_CAPACITY.json"
    with path.open("x") as stream:
        json.dump({"schema": "rfic_transformer.broadband56_tool_capacity.v1",
                   "campaign_id": snapshot["campaign_id"],
                   "contract_fingerprint_sha256": snapshot["contract_fingerprint_sha256"],
                   "supervisor_lease": snapshot["supervisor_lease"],
                   "captured_utc": snapshot["captured_utc"], "tools": tools,
                   "measurement_scope": SCOPE, "absolute_job_bounds_proven": False,
                   "admission_authorized": False, "simulator_action_taken": False},
                  stream, indent=2, sort_keys=True)
    return file_record(path)
