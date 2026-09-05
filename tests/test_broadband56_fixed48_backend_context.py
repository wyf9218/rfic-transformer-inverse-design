"""Real backend/context/admission integration with no simulator delegates."""
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_dispatch as dispatch
from rfic_transformer_inverse_design.campaigns import broadband56_scheduling as scheduling
from rfic_transformer_inverse_design.campaigns import broadband56_capacity_snapshot_adapter as adapter
from tests.test_broadband56_fixed48_scheduling import fixed_samples
from tests.test_broadband56_production_backend import _stage_receipt
from tests.test_broadband56_scheduling import write
from tests.test_run_broadband56_v2_production_stage_backend import MODULE, _fixture, _identity, _sha


@pytest.mark.parametrize("seats", [1, 12, 48])
def test_real_backend_context_consumes_partial_fixed48_history(tmp_path, monkeypatch, seats):
    class ReachedDelegate(BaseException):
        pass

    args = _fixture(tmp_path)
    root = Path(args.campaign_root)
    backend_pin = _identity(Path(args.backend_identity_manifest))
    auth_sha = _sha(Path(args.full_campaign_receipt))
    prior = None
    for index, (stage, accepted) in enumerate((("GOLDEN", 1), ("PILOT_32", 32)), 1):
        dest = root / "stages" / f"{index:06d}_{stage.lower()}"
        value = _stage_receipt(dest / "backend", stage=stage, target=accepted)
        value.update(backend_identity_manifest_sha256=backend_pin["sha256"],
                     full_campaign_authorization_receipt_sha256=auth_sha,
                     prior_stage_receipt_sha256=prior)
        prior = _sha(write(dest / "STAGE_RECEIPT.json", value))

    paths, _ = fixed_samples(root, count=5, seats=seats)
    initial = json.loads(paths[-1].read_text())
    old_lease_path = Path(initial["supervisor_lease"]["path"])
    lease = json.loads(old_lease_path.read_text())
    lease["backend_identity_manifest"] = backend_pin
    lease_pin = scheduling.file_record(write(old_lease_path, lease))
    overlay_path = Path(initial["operational_overlay_manifest"]["path"])
    overlay = json.loads(overlay_path.read_text())
    overlay["corrected_backend_manifest"] = backend_pin
    overlay_pin = scheduling.file_record(write(overlay_path, overlay))
    for path in paths:
        item = json.loads(path.read_text())
        item.update(supervisor_lease=lease_pin, operational_overlay_manifest=overlay_pin)
        capacity_path = Path(item["per_tool_capacity_evidence"]["path"])
        capacity = json.loads(capacity_path.read_text())
        capacity["supervisor_lease"] = lease_pin
        item["per_tool_capacity_evidence"] = scheduling.file_record(write(capacity_path, capacity))
        raw = dict(item)
        raw.pop("source_snapshot")
        raw["schema"] = scheduling.RAW_SNAPSHOT_SCHEMA
        item["source_snapshot"] = scheduling.file_record(write(Path(item["source_snapshot"]["path"]), raw))
        write(path, item)
    initial = json.loads(paths[-1].read_text())
    initial_pin = scheduling.file_record(paths[-1])
    history = root / "scheduling_history/stage_000003"
    state = {"schema": "rfic_transformer.broadband56_stage_resource_history.v1",
             "bindings": {key: initial[key] for key in (
                 "campaign_id", "contract_fingerprint_sha256", "supervisor_lease",
                 "operational_overlay_manifest", "owner_swap_override_receipt")},
             "stage": "PILOT_1000", "initial_snapshot": initial_pin,
             "latest_snapshot": initial_pin, "overall_status": "OBSERVED", "error": None}
    pointer = write(history / "LATEST.json",
                    scheduling.file_record(write(history / "STATE_000001.json", state)))
    policy = scheduling.swap.evaluate_capacity_snapshot(initial, stage="PILOT_1000", current_accepted=32)
    gate = {"schema": scheduling.swap.GATE_SCHEMA, "overall_status": "PASS",
            "decision": "READY_FOR_CURRENT_STAGE", "current_stage": "PILOT_1000",
            "current_accepted": 32, "resource_policy": initial["resource_policy"],
            "swap_policy": scheduling.swap.SWAP_POLICY,
            "campaign_id": initial["campaign_id"],
            "contract_fingerprint_sha256": initial["contract_fingerprint_sha256"],
            "snapshot_captured_utc": scheduling._utc(initial["captured_utc"]).isoformat(timespec="seconds"),
            "checks": [{"name": key, "pass": value, "detail": str(value)}
                       for key, value in policy["checks"].items()],
            "evidence": {"resource_snapshot": initial_pin}}
    adapted = adapter.normalize_capacity_snapshot_for_stage_launcher(
        paths[-1], write(root / "resource_gates/current.json", gate), root / "adapter")
    monkeypatch.setenv("BROADBAND56_STAGE_RESOURCE_HISTORY", str(pointer))
    args.stage, args.cumulative_target = "PILOT_1000", 1000
    args.resource_snapshot, args.max_concurrency = str(adapted["adapted_snapshot_path"]), 48
    delegate_calls = []

    def first_delegate(command, **kwargs):
        delegate_calls.append(command)
        context_path = Path(kwargs["env"]["BROADBAND56_STAGE_CONTEXT"])
        context = json.loads(context_path.read_text())
        assert context["current_accepted"] == 32
        assert context["max_concurrency"] == 48
        assert context["admitted_concurrency"] == seats
        assert context["scheduling_decision"]["healthy_check_streak"] == 5
        assert context["backend_identity_manifest"] == backend_pin
        assert context["initial_resource_snapshot"] == initial_pin
        assert kwargs["env"]["BROADBAND56_STAGE_RESOURCE_HISTORY"] == str(pointer)
        with monkeypatch.context() as environment:
            environment.setenv("BROADBAND56_STAGE_CONTEXT", str(context_path))
            gate = dispatch.stage_admission(48)
            assert gate() == seats
            assert gate.last_decision["requested_concurrency"] == 48
            assert gate.last_decision["target_executor_capacity"] == 48
            rows = list(dispatch.bounded_completed(53, lambda index: index, max_workers=48,
                        admission=gate, receipt_dir=tmp_path / "delegate_dispatch",
                        poll_seconds=.001))
            assert sorted(future.result() for _, future in rows) == list(range(53))
        receipt = json.loads((tmp_path / "delegate_dispatch/DISPATCH_RECEIPT.json").read_text())
        assert receipt["submitted_count"] == 53
        assert receipt["peak_inflight_delegates"] == seats
        assert receipt["executor_capacity"] == 48
        assert not receipt["native_concurrency_proven"]
        raise ReachedDelegate()

    monkeypatch.setattr(MODULE.subprocess, "run", first_delegate)
    with pytest.raises(ReachedDelegate):
        MODULE.run_stage_backend(args, out_dir=Path(args.backend_out_dir), completed_roles=[])
    assert len(delegate_calls) == 1
