"""Operational path-only compatibility, never synthetic physical evidence."""
import copy
import hashlib
import json

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
