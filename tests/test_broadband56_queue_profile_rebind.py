"""Operational path-only compatibility, never synthetic physical evidence."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden


def pin(path):
    raw = path.read_bytes()
    return dict(path=str(path), size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def save(path, value):
    path.write_text(json.dumps(value))
    return pin(path)


@pytest.mark.parametrize("mutation", [None, "seed", "role", "stage", "receipt", "config",
    "sha", "script_bytes", "external_path", "missing_binding", "repeated",
    "duplicate_option", "removed_option", "source_bytes", "other_argument"])
def test_queue_profile_rebind_is_strictly_path_only(tmp_path, mutation):
    old = tmp_path / "old"
    new = tmp_path / "current"
    old.mkdir(); new.mkdir()
    source = old / "build_broadband56_phase_a_queue.py"
    target = new / source.name
    source.write_text("# identical test-only queue delegate\n")
    target.write_bytes(source.read_bytes())
    wrapper = new / "run_broadband56_v2_bound_queue_builder.py"
    wrapper.write_text("# test-only bound wrapper\n")
    command = dict(role="phase_a_queue_builder", receipt="summary.json", shell_used=False,
        argv=["--delegate-script", str(source), "--delegate-sha256", pin(source)["sha256"],
              "--seed", "20260828", "--config", "{private_configuration}"])
    before = dict(stages={s: dict(commands=[copy.deepcopy(command)], result_paths={})
                         for s in ("GOLDEN", "PILOT_32", "PILOT_1000", "PHASE_A")})
    after = copy.deepcopy(before)
    for stage in after["stages"].values():
        stage["commands"][0]["argv"][1] = str(target)
    c = after["stages"]["PILOT_1000"]["commands"][0]
    if mutation == "seed": c["argv"][5] = "7"
    if mutation == "role": c["role"] = "calibre_runner"
    if mutation == "stage": after["stages"].pop("GOLDEN")
    if mutation == "receipt": c["receipt"] = "other.json"
    if mutation == "config": c["argv"][7] = "changed.yaml"
    if mutation == "sha": c["argv"][3] = "0" * 64
    if mutation == "script_bytes": target.write_text("# modified code\n")
    if mutation == "external_path": c["argv"][1] = str(source)
    if mutation == "duplicate_option": before["stages"]["PILOT_1000"]["commands"][0]["argv"] += ["--delegate-script", str(source)]
    if mutation == "removed_option": before["stages"]["PILOT_1000"]["commands"][0]["argv"] = []
    if mutation == "source_bytes": source.write_text("# tampered original\n")
    if mutation == "other_argument": c["argv"] += ["--no-fail-exit"]
    a, b = save(tmp_path / "original.json", before), save(tmp_path / "replacement.json", after)
    binding = dict(original=a, replacement=b,
        kind="IDENTICAL_QUEUE_DELEGATE_CURRENT_RUNTIME_PATH_ONLY", golden_execution_repeated=False)
    if mutation == "missing_binding": binding = {}
    if mutation == "repeated": binding["golden_execution_repeated"] = True
    backend = dict(script_identities=dict(phase_a_queue_builder=pin(wrapper)))
    if mutation is None:
        golden.validate_queue_delegate_profile_rebind(a, b, backend, binding)
    else:
        with pytest.raises(golden.GoldenSourceError):
            golden.validate_queue_delegate_profile_rebind(a, b, backend, binding)


@pytest.mark.parametrize("mutation", [None, "seed", "count", "config", "calibre_args", "role",
    "removed_stage", "extra_stage", "golden_limit", "other_stage_limit", "limit", "missing_cohort",
    "duplicate_option", "source_limit", "source_duplicate", "missing_pilot", "old_kind", "binding_limit",
    "unknown_code", "drift", "source_drift", "external_path", "fake_boolean", "null_binding", "symlink"])
@pytest.mark.parametrize("candidate_limit", [32, 48, 192])
def test_bounded_pilot_profile_only_permits_exact_scheduler_transform(tmp_path, monkeypatch, mutation, candidate_limit):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    source = old / "build_broadband56_phase_a_queue.py"
    target = new / source.name
    source.write_text("# old test-only queue delegate\n")
    target.write_text("# bounded test-only queue delegate\n")
    wrapper = new / "run_broadband56_v2_bound_queue_builder.py"
    wrapper.write_text("# unchanged test-only wrapper\n")
    source_pin, target_pin = pin(source), pin(target)
    monkeypatch.setattr(golden, "GOLDEN_COMPATIBLE_QUEUE_BATCH_REBINDS",
                        frozenset({(source_pin["sha256"], target_pin["sha256"])}))
    command = dict(role="phase_a_queue_builder", receipt="summary.json", shell_used=False,
        argv=["--delegate-script", str(source), "--delegate-sha256", source_pin["sha256"],
              "--seed", "20260828", "--config", "{private_configuration}", "--count", "{remaining_accepted}"])
    before = dict(stages={s: dict(commands=[copy.deepcopy(command), dict(role="calibre_runner", argv=["--strict"])],
                                result_paths={}) for s in ("GOLDEN", "PILOT_32", "PILOT_1000", "PHASE_A")})
    after = copy.deepcopy(before)
    for spec in after["stages"].values():
        spec["commands"][0]["argv"][1] = str(target)
        spec["commands"][0]["argv"][3] = target_pin["sha256"]
    pilot = after["stages"]["PILOT_1000"]
    pilot["max_candidates_per_attempt"] = candidate_limit
    c = pilot["commands"][0]
    c["argv"] += ["--attempt-candidate-limit", str(candidate_limit), "--reuse-campaign-frozen-cohort"]
    if mutation == "seed": c["argv"][5] = "7"
    if mutation == "count": c["argv"][9] = "900"
    if mutation == "config": c["argv"][7] = "other.yaml"
    if mutation == "calibre_args": pilot["commands"][1]["argv"] = ["--ignore-errors"]
    if mutation == "role": c["role"] = "calibre_runner"
    if mutation == "removed_stage": after["stages"].pop("GOLDEN")
    if mutation == "extra_stage": after["stages"]["EXTRA"] = copy.deepcopy(pilot)
    if mutation == "golden_limit": after["stages"]["GOLDEN"]["max_candidates_per_attempt"] = 32
    if mutation == "other_stage_limit": after["stages"]["PHASE_A"]["max_candidates_per_attempt"] = 32
    if mutation == "limit": pilot["max_candidates_per_attempt"] = 31
    if mutation == "missing_cohort": c["argv"].pop()
    if mutation == "duplicate_option": c["argv"] += ["--attempt-candidate-limit", "32"]
    if mutation == "source_limit": before["stages"]["PILOT_1000"]["max_candidates_per_attempt"] = 32
    if mutation == "source_duplicate": before["stages"]["PILOT_1000"]["commands"][0]["argv"] += ["--delegate-script", str(source)]
    if mutation == "missing_pilot":
        before["stages"].pop("PILOT_1000"); after["stages"].pop("PILOT_1000")
    if mutation == "unknown_code": monkeypatch.setattr(golden, "GOLDEN_COMPATIBLE_QUEUE_BATCH_REBINDS", frozenset())
    if mutation == "drift": target.write_text("# other code\n")
    if mutation == "source_drift": source.write_text("# other source\n")
    if mutation == "external_path": c["argv"][1] = str(source)
    if mutation == "symlink":
        alternate = new / "alternate.py"
        target.rename(alternate)
        target.symlink_to(alternate)
    a, b = save(tmp_path / "original.json", before), save(tmp_path / "replacement.json", after)
    binding = dict(original=a, replacement=b, kind=golden.BOUNDED_PILOT_PROFILE_REBIND,
                   golden_execution_repeated=False, max_candidates_per_attempt=candidate_limit)
    if mutation == "old_kind": binding["kind"] = "IDENTICAL_QUEUE_DELEGATE_CURRENT_RUNTIME_PATH_ONLY"
    if mutation == "binding_limit": binding["max_candidates_per_attempt"] = 31
    if mutation == "fake_boolean": binding["golden_execution_repeated"] = 0
    if mutation == "null_binding": binding = None
    backend = dict(script_identities=dict(phase_a_queue_builder=pin(wrapper)))
    if mutation is None:
        golden.validate_queue_delegate_profile_rebind(a, b, backend, binding)
        assert json.loads(Path(a["path"]).read_text()) == before
    else:
        with pytest.raises(golden.GoldenSourceError):
            golden.validate_queue_delegate_profile_rebind(a, b, backend, binding)


def test_pinned_scheduler_pairs_match_the_actual_staged_scripts():
    root = Path(__file__).resolve().parents[1]
    roles = dict(production_stage_backend="run_broadband56_v2_production_stage_backend.py",
                 stage_launcher="run_broadband56_v2_stage_launcher.py",
                 exact_audited_gds_emx_runner="run_broadband56_v2_exact_gds_emx_batch.py",
                 cadence_streamout_delegate="run_candidate_queue_dataset_parallel.py")
    assert len(golden.GOLDEN_COMPATIBLE_SCHEDULER_REBINDS) == 6
    for group, role, before, after in golden.GOLDEN_COMPATIBLE_SCHEDULER_REBINDS:
        assert len(before) == len(after) == 64
        if group == "script_identities" and role in roles:
            assert after == pin(root / "scripts" / roles[role])["sha256"]
        elif role == "calibre_batch_delegate":
            # Private bytes are checked by manifest verification and the exact
            # optional private-delegate test, never embedded in this public repo.
            assert group == "script_identities"
        else:
            assert (group, role) == ("runtime_identities", "resource_probe")
    assert golden.GOLDEN_COMPATIBLE_QUEUE_BATCH_REBINDS == frozenset({(
        "1e1fb5f55fa64a99ffb01f41abcb35a08787fd16cf4d300f91f3b89cf02185ba",
        pin(root / "scripts/build_broadband56_phase_a_queue.py")["sha256"],
    ), (
        "1e1fb5f55fa64a99ffb01f41abcb35a08787fd16cf4d300f91f3b89cf02185ba",
        "d3c53169370ff9695a9b0b7086f8f76e6ee794063b6d39946538dbb947b09349",
    ), (
        "1e1fb5f55fa64a99ffb01f41abcb35a08787fd16cf4d300f91f3b89cf02185ba",
        "55d051edacf5099117c999222c12998c37094cbe808a70f55fa3e3670fc150ea",
    )})
