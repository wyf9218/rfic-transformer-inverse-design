from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.campaigns import (
    broadband56_capacity_policy as capacity_policy,
)
from rfic_transformer_inverse_design.campaigns import (
    broadband56_capacity_snapshot_adapter as adapter,
)
from rfic_transformer_inverse_design.campaigns import (
    broadband56_swap_override_policy as swap_policy,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGE_LAUNCHER = _load(
    ROOT / "scripts" / "run_broadband56_v2_stage_launcher.py",
    "capacity_adapter_stage_launcher",
)
SWAP_CONTROLLER = _load(
    ROOT / "scripts" / "run_broadband56_v2_swap_override_queue_controller.py",
    "capacity_adapter_swap_controller",
)


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _record(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }


def _legacy_snapshot(captured: datetime) -> dict:
    return {
        "schema": capacity_policy.SNAPSHOT_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": (
            capacity_policy.SCIENTIFIC_CONTRACT_FINGERPRINT
        ),
        "resource_policy": capacity_policy.RESOURCE_POLICY,
        "captured_utc": captured.isoformat(timespec="seconds"),
        "resources": {
            "logical_cpu_count": 192,
            "physical_cpu_count": 96,
            "load_1m": 9.0,
            "load_5m": 12.0,
            "load_15m": 20.0,
            "cpu_total_utilization_percent": 5.0,
            "cpu_user_utilization_percent": 3.0,
            "cpu_system_utilization_percent": 2.0,
            "iowait_percent": 0.01,
            "runnable_process_count": 3,
            "blocked_process_count": 0,
            "memory_total_bytes": 1_000_000,
            "memory_available_bytes": 700_000,
            "swap_total_bytes": 100_000,
            "swap_used_bytes": 70_000,
            "swap_sample_interval_seconds": 60.0,
            "swap_in_pages_delta": 731,
            "swap_out_pages_delta": 0,
            "active_swap_thrashing": True,
            "filesystem_free_bytes": 20 * 1024**3,
        },
        "licenses": {
            "cadence_available": True,
            "calibre_available": True,
            "emx_available": True,
            "simulator_license_capacity": 4,
        },
        "isolation": {
            "authoritative_supervisor_count": 1,
            "duplicate_supervisor_count": 0,
            "duplicate_runner_count": 0,
            "unexpected_project_child_count": 0,
            "project_owned_cadence_children": 0,
            "project_owned_calibre_children": 0,
            "project_owned_emx_children": 0,
            "output_path_collision": False,
        },
    }


def _materialize(
    tmp_path: Path,
    *,
    captured: datetime | None = None,
    mutate=None,
) -> tuple[Path, Path, dict, dict, datetime]:
    captured = captured or datetime.now(timezone.utc).replace(microsecond=0)
    legacy_path = _write_json(tmp_path / "legacy.json", _legacy_snapshot(captured))
    source = copy.deepcopy(_legacy_snapshot(captured))
    source.update(
        schema=swap_policy.SNAPSHOT_SCHEMA,
        swap_policy=swap_policy.SWAP_POLICY,
        source_snapshot=_record(legacy_path),
        preserved_unknown_field={"must_remain": [1, 2, 3]},
    )
    source["resources"].update(
        active_swap_thrashing=False,
        blocked_process_count_delta=0,
        legacy_reported_active_swap_thrashing=True,
        oom_kill_delta=0,
    )
    if mutate is not None:
        mutate(source)
    source_path = _write_json(tmp_path / "source.json", source)
    try:
        decision = swap_policy.evaluate_capacity_snapshot(
            source,
            stage="GOLDEN",
            current_accepted=0,
        )
    except capacity_policy.CapacityPolicyError:
        decision = {"pass": True, "checks": {}}
    gate_status = "PASS" if decision["pass"] else "WAIT"
    gate = {
        "schema": swap_policy.GATE_SCHEMA,
        "overall_status": gate_status,
        "decision": (
            "READY_FOR_CURRENT_STAGE"
            if gate_status == "PASS"
            else "WAITING_FOR_CAPACITY"
        ),
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": (
            capacity_policy.SCIENTIFIC_CONTRACT_FINGERPRINT
        ),
        "resource_policy": capacity_policy.RESOURCE_POLICY,
        "swap_policy": swap_policy.SWAP_POLICY,
        "current_stage": "GOLDEN",
        "current_accepted": 0,
        "snapshot_captured_utc": captured.isoformat(timespec="seconds"),
        "checks": [
            {"name": name, "pass": bool(passed), "detail": str(passed)}
            for name, passed in decision["checks"].items()
        ],
        "evidence": {"resource_snapshot": _record(source_path)},
    }
    gate_path = _write_json(tmp_path / "gate.json", gate)
    return source_path, gate_path, source, gate, captured


def _adapt(
    tmp_path: Path,
    *,
    captured: datetime | None = None,
    mutate=None,
) -> tuple[dict, dict, datetime, Path]:
    source_path, gate_path, source, _gate, captured = _materialize(
        tmp_path,
        captured=captured,
        mutate=mutate,
    )
    result = adapter.normalize_capacity_snapshot_for_stage_launcher(
        source_path,
        gate_path,
        tmp_path / "adapted",
        converted_utc=captured + timedelta(seconds=5),
    )
    adapted_path = result["adapted_snapshot_path"]
    return json.loads(adapted_path.read_text()), source, captured, adapted_path


def _evaluate(adapted: dict, captured: datetime) -> dict:
    return adapter.evaluate_adapted_capacity_snapshot(
        adapted,
        stage="GOLDEN",
        current_accepted=0,
        evaluated_utc=captured + timedelta(seconds=10),
    )


def test_generation6_schema_adapts_and_preserves_complete_payload(
    tmp_path: Path,
) -> None:
    adapted, source, captured, adapted_path = _adapt(tmp_path)

    assert adapted["schema"] == capacity_policy.SNAPSHOT_SCHEMA
    assert adapted["operational_resource_policy"] == swap_policy.SWAP_POLICY
    assert adapted["capacity_schema_adapter"]["profile"] == adapter.ADAPTER_PROFILE
    restored = copy.deepcopy(adapted)
    restored.pop("capacity_schema_adapter")
    restored.pop("operational_resource_policy")
    restored["schema"] = swap_policy.SNAPSHOT_SCHEMA
    assert restored == source
    assert _evaluate(adapted, captured)["pass"] is True
    receipt = json.loads(
        (adapted_path.parent / adapter.ADAPTER_RECEIPT_NAME).read_text()
    )
    assert receipt["adapted_snapshot"] == _record(adapted_path)
    assert receipt["resource_values_preserved"] is True


def test_legacy_capacity_schema_keeps_legacy_semantics() -> None:
    captured = datetime.now(timezone.utc).replace(microsecond=0)
    legacy = _legacy_snapshot(captured)
    legacy["resources"].update(
        swap_in_pages_delta=0,
        active_swap_thrashing=False,
    )

    result = capacity_policy.evaluate_capacity_snapshot(
        legacy,
        stage="GOLDEN",
        current_accepted=0,
    )

    assert result["pass"] is True
    assert "adapter_profile" not in result


def test_unknown_source_schema_fails_closed(tmp_path: Path) -> None:
    source_path, gate_path, *_ = _materialize(
        tmp_path,
        mutate=lambda value: value.update(schema="unknown.schema.v1"),
    )

    with pytest.raises(adapter.CapacitySnapshotAdapterError, match="identity"):
        adapter.normalize_capacity_snapshot_for_stage_launcher(
            source_path,
            gate_path,
            tmp_path / "adapted",
        )


def test_missing_required_source_field_fails_closed(tmp_path: Path) -> None:
    source_path, gate_path, *_ = _materialize(
        tmp_path,
        mutate=lambda value: value["resources"].pop("memory_available_bytes"),
    )

    with pytest.raises(adapter.CapacitySnapshotAdapterError, match="missing"):
        adapter.normalize_capacity_snapshot_for_stage_launcher(
            source_path,
            gate_path,
            tmp_path / "adapted",
        )


def test_source_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    source_path, gate_path, *_ = _materialize(tmp_path)
    source_path.write_text(source_path.read_text() + "\n", encoding="utf-8")

    with pytest.raises(adapter.CapacitySnapshotAdapterError, match="identity"):
        adapter.normalize_capacity_snapshot_for_stage_launcher(
            source_path,
            gate_path,
            tmp_path / "adapted",
        )


def test_resource_pass_remains_pass_without_threshold_change(tmp_path: Path) -> None:
    adapted, _source, captured, _path = _adapt(tmp_path)

    result = _evaluate(adapted, captured)

    assert result["pass"] is True
    assert result["failed_checks"] == []
    assert result["metrics"]["advisory_nonzero_swap_in"] is True


def test_resource_fail_remains_fail_after_adaptation(tmp_path: Path) -> None:
    adapted, _source, captured, _path = _adapt(
        tmp_path,
        mutate=lambda value: value["licenses"].update(emx_available=False),
    )

    result = _evaluate(adapted, captured)

    assert result["pass"] is False
    assert result["failed_checks"] == ["license_gate"]


def test_snapshot_over_300_seconds_is_stale(tmp_path: Path) -> None:
    adapted, _source, captured, _path = _adapt(tmp_path)

    with pytest.raises(adapter.CapacitySnapshotAdapterError, match="stale"):
        adapter.evaluate_adapted_capacity_snapshot(
            adapted,
            stage="GOLDEN",
            current_accepted=0,
            evaluated_utc=captured + timedelta(seconds=300, microseconds=1),
        )


def test_adapter_creation_does_not_reset_snapshot_age(tmp_path: Path) -> None:
    captured = datetime.now(timezone.utc).replace(microsecond=0)
    source_path, gate_path, *_ = _materialize(tmp_path, captured=captured)
    result = adapter.normalize_capacity_snapshot_for_stage_launcher(
        source_path,
        gate_path,
        tmp_path / "adapted",
        converted_utc=captured + timedelta(seconds=299),
    )

    assert adapter.adapted_snapshot_is_fresh(
        result["adapted_snapshot_path"],
        evaluated_utc=captured + timedelta(seconds=300),
    )
    assert not adapter.adapted_snapshot_is_fresh(
        result["adapted_snapshot_path"],
        evaluated_utc=captured + timedelta(seconds=301),
    )


def test_active_swap_degradation_still_blocks(tmp_path: Path) -> None:
    def mutate(value: dict) -> None:
        value["resources"].update(
            swap_in_pages_delta=0,
            swap_out_pages_delta=60,
            active_swap_thrashing=True,
        )

    adapted, _source, captured, _path = _adapt(tmp_path, mutate=mutate)

    result = _evaluate(adapted, captured)

    assert result["pass"] is False
    assert result["checks"]["swap_thrash_gate"] is False


@pytest.mark.parametrize(
    ("mutation", "failed_gate"),
    [
        (lambda value: value["licenses"].update(calibre_available=False), "license_gate"),
        (
            lambda value: value["isolation"].update(duplicate_runner_count=1),
            "isolation_gate",
        ),
        (
            lambda value: value["resources"].update(filesystem_free_bytes=1),
            "storage_gate",
        ),
    ],
)
def test_hard_gates_still_block(tmp_path: Path, mutation, failed_gate: str) -> None:
    adapted, _source, captured, _path = _adapt(tmp_path, mutate=mutation)

    result = _evaluate(adapted, captured)

    assert result["pass"] is False
    assert result["checks"][failed_gate] is False


def test_real_stage_launcher_parser_accepts_adapted_snapshot_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapted, _source, _captured, _path = _adapt(tmp_path)
    simulator_called = False

    def forbidden_subprocess(*_args, **_kwargs):
        nonlocal simulator_called
        simulator_called = True
        raise AssertionError("no subprocess is permitted in parser preflight")

    monkeypatch.setattr(STAGE_LAUNCHER.subprocess, "run", forbidden_subprocess)

    result = STAGE_LAUNCHER.evaluate_capacity_snapshot(
        adapted,
        stage="GOLDEN",
        current_accepted=0,
    )

    assert result["pass"] is True
    assert result["adapter_profile"] == adapter.ADAPTER_PROFILE
    assert simulator_called is False


def test_adapter_rejects_nonfinite_resource_value(tmp_path: Path) -> None:
    source_path, gate_path, *_ = _materialize(
        tmp_path,
        mutate=lambda value: value["resources"].update(load_1m=float("inf")),
    )

    with pytest.raises(adapter.CapacitySnapshotAdapterError, match="nonfinite"):
        adapter.normalize_capacity_snapshot_for_stage_launcher(
            source_path,
            gate_path,
            tmp_path / "adapted",
        )


def test_adapter_output_is_no_clobber(tmp_path: Path) -> None:
    source_path, gate_path, *_ = _materialize(tmp_path)
    out_dir = tmp_path / "adapted"
    adapter.normalize_capacity_snapshot_for_stage_launcher(
        source_path,
        gate_path,
        out_dir,
    )

    with pytest.raises(adapter.CapacitySnapshotAdapterError, match="no-clobber"):
        adapter.normalize_capacity_snapshot_for_stage_launcher(
            source_path,
            gate_path,
            out_dir,
        )


def test_controller_boundary_passes_only_adapted_snapshot_to_launcher(
    tmp_path: Path,
) -> None:
    campaign_root = tmp_path / "campaign"
    source_path, gate_path, *_ = _materialize(
        campaign_root / "fixture",
    )
    bound_gate = (
        campaign_root
        / "resource_gates"
        / "000001_fixture"
        / "CAPACITY_RESOURCE_GATE.json"
    )
    _write_json(bound_gate, json.loads(gate_path.read_text()))
    launches: list[dict] = []

    class ControllerError(RuntimeError):
        pass

    controller = SimpleNamespace(
        ControllerError=ControllerError,
        STOP_REQUESTED=False,
        _read_json=lambda path, _label: json.loads(Path(path).read_text()),
        _pilot_bytes_per_geometry=lambda _root: None,
        _pilot_safe_concurrency=lambda _root: None,
        _interruptible_sleep=lambda _seconds: None,
    )

    def original_launcher(**kwargs):
        launches.append(kwargs)
        return {"decision": "NOOP_PARSE_PASS"}

    boundary = SWAP_CONTROLLER._capacity_adapter_stage_launcher_factory(
        controller,
        original_run_stage_launcher=original_launcher,
        poll_seconds=30,
    )
    result = boundary(
        inputs={},
        campaign_root=campaign_root,
        stage="GOLDEN",
        concurrency=1,
        snapshot_path=source_path,
        check_index=1,
    )

    assert result["decision"] == "NOOP_PARSE_PASS"
    assert len(launches) == 1
    launched_snapshot = json.loads(launches[0]["snapshot_path"].read_text())
    assert launched_snapshot["schema"] == capacity_policy.SNAPSHOT_SCHEMA
    assert launches[0]["concurrency"] == 1


def test_controller_boundary_reprobes_after_stale_then_wait(
    tmp_path: Path,
) -> None:
    campaign_root = tmp_path / "campaign"
    stale_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(
        seconds=301
    )
    stale_source, stale_gate, *_ = _materialize(
        campaign_root / "stale",
        captured=stale_time,
    )
    initial_gate = (
        campaign_root
        / "resource_gates"
        / "000001_stale"
        / "CAPACITY_RESOURCE_GATE.json"
    )
    _write_json(initial_gate, json.loads(stale_gate.read_text()))
    wait_source, wait_gate, *_ = _materialize(
        tmp_path / "wait",
        mutate=lambda value: value["licenses"].update(emx_available=False),
    )
    pass_source, pass_gate, *_ = _materialize(tmp_path / "pass")
    probe_paths = iter((wait_source, pass_source))
    gate_paths = iter((wait_gate, pass_gate))
    probe_count = 0
    sleep_count = 0
    launches: list[dict] = []

    class ControllerError(RuntimeError):
        pass

    def run_probe(*_args):
        nonlocal probe_count
        probe_count += 1
        return next(probe_paths)

    def sleep(_seconds):
        nonlocal sleep_count
        sleep_count += 1

    controller = SimpleNamespace(
        ControllerError=ControllerError,
        STOP_REQUESTED=False,
        _read_json=lambda path, _label: json.loads(Path(path).read_text()),
        _pilot_bytes_per_geometry=lambda _root: None,
        _pilot_safe_concurrency=lambda _root: None,
        _interruptible_sleep=sleep,
        _run_probe=run_probe,
        _write_resource_gate=lambda **_kwargs: next(gate_paths),
    )

    def original_launcher(**kwargs):
        launches.append(kwargs)
        return {"decision": "NOOP_PARSE_PASS"}

    boundary = SWAP_CONTROLLER._capacity_adapter_stage_launcher_factory(
        controller,
        original_run_stage_launcher=original_launcher,
        poll_seconds=30,
    )
    result = boundary(
        inputs={"probe_script": tmp_path / "unused_probe.sh"},
        campaign_root=campaign_root,
        stage="GOLDEN",
        concurrency=1,
        snapshot_path=stale_source,
        check_index=1,
    )

    assert result["decision"] == "NOOP_PARSE_PASS"
    assert probe_count == 2
    assert sleep_count == 1
    assert len(launches) == 1
