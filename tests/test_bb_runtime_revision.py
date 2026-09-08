"""Synthetic pretraining runtime promotion only; never loads a model or real study.

Only ``runtime_revision._software`` supplies synthetic identities. Race tests wrap
actual lease acquisition or descriptor reads to inject real tmp-path replacements;
they do not stub validation, publication or locks. The 25/26 source files are inert
text, not imported modules. All study state is under pytest tmp_path.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import runtime_revision as revision
from research.broadband56_nn import study_once as once
from research.broadband56_nn.io import canonical_sha, read_json, sha256


def _write(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline_software"
    current = tmp_path / "candidate_software"
    baseline.mkdir()
    current.mkdir()
    names = {"delivery.py", "bb00_delivery.py", "seven_suite.py", "models.py",
             "physics.py", "specs.py", "metrics.py", "evaluation.py"}
    names.update(f"support_{number:02d}.py" for number in range(17))
    assert len(names) == 25
    for name in sorted(names):
        raw = ("# synthetic module " + name + "\n").encode()
        (baseline / name).write_bytes(raw)
        if name in revision.CHANGED_MODULES:
            raw += b"# candidate runtime wiring only\n"
        (current / name).write_bytes(raw)
    (current / "runtime_revision.py").write_bytes(b"# synthetic added adapter\n")

    def software():
        return {p.name: _pin(p) for p in sorted(current.glob("*.py"))}

    monkeypatch.setattr(revision, "_software", software)
    control = tmp_path / "control"
    root = control / once.study_key("synthetic-campaign")
    root.mkdir(parents=True)
    request = {
        "schema": "bb_seven_study_request.v1", "campaign_id": "synthetic-campaign",
        "suite_version": "seven-model-10k-v3", "study_key": root.name,
        "milestone_geometries": 10000, "control_root": str(control),
        "software": {p.name: _pin(p) for p in sorted(baseline.glob("*.py"))},
        "device": "cpu", "forward_steps": 256, "inverse_steps": 128,
        "seed": 17, "fref_seed": 29, "wall_budget_seconds": 1800,
        "micro_batch": 8, "effective_batch": 32, "threads": 2,
        "contract": {"sha256": "a" * 64, "frozen": True},
        "dataset_protocol": {"accepted_sequence": [1, 10000], "frequency_count": 56,
                             "holdout": "geometry_grouped", "seed": 17},
        "target_protocol": {"physical15": ["Lp", "Ls", "Qmin", "K_abs"],
                            "hidden_reference_spectrum": False},
    }
    base_path = _write(tmp_path / "BASE_REQUEST.json", request)
    (root / "experiment_plan.json").write_bytes(base_path.read_bytes())
    (root / "study.lock").touch()
    once.Journal(root).append("WAITING_FOR_10K", {"committed_accepted": 5987})
    index = tmp_path / "ORIGINAL_SHA256SUMS.txt"
    index.write_text("".join(
        f"{sha256(root / name)}  {(root / name).relative_to(tmp_path)}\n"
        for name in sorted(revision.PRETRAINING_FILES)), encoding="utf-8")
    original_files = [base_path, index, *(root / n for n in revision.PRETRAINING_FILES),
                      *sorted(baseline.glob("*.py"))]
    return SimpleNamespace(tmp=tmp_path, baseline=baseline, current=current,
                           control=control, root=root, request=request, base=base_path,
                           index=index, original={p: p.read_bytes() for p in original_files})


def _unchanged(sandbox):
    assert all(path.read_bytes() == raw for path, raw in sandbox.original.items())


def _prepare(sandbox, name="proposal_01"):
    output = sandbox.tmp / name
    result = revision.prepare_revision(sandbox.base, sha256(sandbox.base),
                                       sandbox.index, sha256(sandbox.index), output)
    assert result == read_json(output / "PROPOSAL.json")
    return output / "PROPOSAL.json"


def _go(sandbox, proposal_path, *, patch=None, name="QA.json"):
    proposal = read_json(proposal_path)
    value = {"status": revision.GO_STATUS, "proposal_sha256": sha256(proposal_path),
             "base_request_sha256": proposal["base_request"]["sha256"],
             "candidate_request_sha256": proposal["candidate_request"]["sha256"],
             "candidate_software_sha256": proposal["candidate_software_sha256"],
             "runtime_revision_source_sha256": proposal["runtime_revision_source_sha256"]}
    value.update(patch or {})
    return _write(sandbox.tmp / name, value)


def _activate(sandbox, proposal_path, qa_path, name="activation_01"):
    return revision.activate_revision(proposal_path, sha256(proposal_path),
                                      qa_path, sha256(qa_path), out=sandbox.tmp / name)


def _failed(sandbox, output, filename="ACTIVATION_FAILED.json"):
    record = read_json(sandbox.tmp / output / filename)
    assert record["status"] == "FAIL"
    assert not (sandbox.root / revision.MARKER).exists()
    if filename == "ACTIVATION_FAILED.json":
        assert record["failure_preserved"] is True
        assert record["automatic_retry"] is False


def test_prepare_preserves_original_bytes_and_all_scientific_fields(sandbox):
    path = _prepare(sandbox)
    proposal = read_json(path)
    candidate = read_json(proposal["candidate_request"]["path"])
    _unchanged(sandbox)
    assert set(candidate["software"]) == set(sandbox.request["software"]) | {"runtime_revision.py"}
    assert len(candidate["software"]) == 26
    assert {k: v for k, v in candidate.items() if k != "software"} == {
        k: v for k, v in sandbox.request.items() if k != "software"}
    assert proposal["candidate_software_sha256"] == canonical_sha(candidate["software"])
    assert proposal["scientific_contract_changed"] is False
    assert proposal["training_started"] is proposal["budget_created"] is False
    assert not (sandbox.root / revision.MARKER).exists()
    assert not (sandbox.root / "TRAINING_BUDGET.json").exists()
    for name, pin in proposal["predecessor_copies"].items():
        assert Path(pin["path"]).read_bytes() == (sandbox.root / name).read_bytes()
    assert Path(proposal["base_request_copy"]["path"]).read_bytes() == sandbox.base.read_bytes()
    assert Path(proposal["predecessor_index_copy"]["path"]).read_bytes() == sandbox.index.read_bytes()


@pytest.mark.parametrize("change", ["models.py", "physics.py", "specs.py", "metrics.py", "evaluation.py",
                                   "extra_module", "missing_module", "missing_adapter"])
def test_prepare_rejects_scientific_source_or_module_set_changes(sandbox, change):
    if change == "extra_module":
        (sandbox.current / "unexpected.py").write_text("# extra\n")
    elif change == "missing_module":
        (sandbox.current / "support_00.py").unlink()
    elif change == "missing_adapter":
        (sandbox.current / "runtime_revision.py").unlink()
    else:
        (sandbox.current / change).write_text("# altered scientific implementation\n")
    with pytest.raises(ValueError):
        _prepare(sandbox)
    _failed(sandbox, "proposal_01", "PREPARATION_FAILED.json")
    _unchanged(sandbox)


@pytest.mark.parametrize("kind", ["empty_stages", "empty_selection", "empty_nested_events", "temporary_file", "symlink_file", "symlink_directory"])
def test_prepare_rejects_extra_pretraining_state_including_empty_directories(sandbox, kind):
    if kind.startswith("empty_"):
        target = {"empty_stages": "stages", "empty_selection": "selection", "empty_nested_events": "events/unexpected"}[kind]
        (sandbox.root / target).mkdir()
    elif kind == "temporary_file":
        (sandbox.root / ".unpublished-incomplete").write_text("partial")
    elif kind == "symlink_file":
        (sandbox.root / "unexpected.json").symlink_to(sandbox.base)
    else:
        (sandbox.root / "unexpected_dir").symlink_to(sandbox.baseline, target_is_directory=True)
    with pytest.raises(ValueError):
        _prepare(sandbox)
    _failed(sandbox, "proposal_01", "PREPARATION_FAILED.json")
    _unchanged(sandbox)


@pytest.mark.parametrize("kind", ["external_sha", "wrong_entry", "missing_entry", "duplicate_entry"])
def test_prepare_rejects_wrong_original_index(sandbox, kind):
    index = sandbox.index
    if kind != "external_sha":
        lines = index.read_text().splitlines()
        if kind == "wrong_entry":
            lines[0] = "0" * 64 + lines[0][64:]
        elif kind == "missing_entry":
            lines.pop()
        else:
            lines.append(lines[0])
        index = sandbox.tmp / "FOREIGN_SHA256SUMS.txt"
        index.write_text("\n".join(lines) + "\n")
    expected = "0" * 64 if kind == "external_sha" else sha256(index)
    with pytest.raises(ValueError):
        revision.prepare_revision(sandbox.base, sha256(sandbox.base), index, expected,
                                  sandbox.tmp / "proposal_bad_index")
    _failed(sandbox, "proposal_bad_index", "PREPARATION_FAILED.json")
    _unchanged(sandbox)


@pytest.mark.parametrize("key", ["status", "proposal_sha256", "base_request_sha256", "candidate_request_sha256",
                                "candidate_software_sha256", "runtime_revision_source_sha256"])
def test_activate_requires_every_exact_go_identity(sandbox, key):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal, patch={key: "NOT_THE_REQUIRED_IDENTITY"})
    with pytest.raises(ValueError):
        _activate(sandbox, proposal, qa)
    _failed(sandbox, "activation_01")
    _unchanged(sandbox)


@pytest.mark.parametrize("which", ["proposal", "qa"])
def test_activate_requires_explicit_external_sha(sandbox, which):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    with pytest.raises(ValueError):
        revision.activate_revision(proposal, "0" * 64 if which == "proposal" else sha256(proposal),
                                   qa, "0" * 64 if which == "qa" else sha256(qa),
                                   out=sandbox.tmp / "activation_bad_sha")
    if which == "proposal":
        # The true base-derived study/output exclusion is not trusted until the
        # externally supplied proposal hash passes. Reject before any output.
        assert not (sandbox.tmp / "activation_bad_sha").exists()
        assert not (sandbox.root / revision.MARKER).exists()
        assert not (sandbox.control / "research_device.lock").exists()
    else:
        # Once authentic base admission succeeds, a wrong GO SHA must retain
        # its failure outside the study rather than silently discard evidence.
        _failed(sandbox, "activation_bad_sha")
    _unchanged(sandbox)


@pytest.mark.parametrize("key,value", [
    ("candidate_software_sha256", "0" * 64),
    ("runtime_revision_source_sha256", "0" * 64),
    ("allowed_changed_modules", ["models.py"]),
    ("added_modules", []),
    ("scientific_contract_changed", True),
    ("training_started", True),
    ("budget_created", True),
])
def test_activate_rejects_wrong_proposal_claims_even_with_matching_go(sandbox, key, value):
    proposal_path = _prepare(sandbox)
    proposal = read_json(proposal_path)
    proposal[key] = value
    _write(proposal_path, proposal)
    qa = _go(sandbox, proposal_path)
    with pytest.raises(ValueError):
        _activate(sandbox, proposal_path, qa)
    _failed(sandbox, "activation_01")
    _unchanged(sandbox)


@pytest.mark.parametrize("key,value", [
    ("wall_budget_seconds", 3600), ("seed", 18), ("forward_steps", 512),
    ("device", "mps"), ("milestone_geometries", 20000),
    ("target_protocol", {"hidden_reference_spectrum": True}),
])
def test_activate_cannot_repin_a_changed_scientific_contract(sandbox, key, value):
    proposal_path = _prepare(sandbox)
    proposal = read_json(proposal_path)
    candidate_path = Path(proposal["candidate_request"]["path"])
    candidate = read_json(candidate_path)
    candidate[key] = value
    _write(candidate_path, candidate)
    proposal["candidate_request"] = _pin(candidate_path)
    _write(proposal_path, proposal)
    qa = _go(sandbox, proposal_path)
    with pytest.raises(ValueError):
        _activate(sandbox, proposal_path, qa)
    _failed(sandbox, "activation_01")
    _unchanged(sandbox)


@pytest.mark.parametrize("kind", ["candidate_pin", "candidate_software_pin", "changed_current_source", "changed_base_source", "preserved_copy"])
def test_activate_rejects_candidate_source_and_preserved_copy_drift(sandbox, kind):
    proposal_path = _prepare(sandbox)
    proposal = read_json(proposal_path)
    if kind == "candidate_pin":
        proposal["candidate_request"]["sha256"] = "0" * 64
    elif kind == "candidate_software_pin":
        cp = Path(proposal["candidate_request"]["path"])
        candidate = read_json(cp)
        candidate["software"]["models.py"]["sha256"] = "0" * 64
        _write(cp, candidate)
        proposal["candidate_request"] = _pin(cp)
        proposal["candidate_software_sha256"] = canonical_sha(candidate["software"])
    elif kind == "changed_current_source":
        (sandbox.current / "delivery.py").write_text("# drift after proposal\n")
    elif kind == "changed_base_source":
        (sandbox.baseline / "models.py").write_text("# historical source drift\n")
    else:
        Path(proposal["predecessor_copies"]["run_status.json"]["path"]).write_text("{}\n")
    _write(proposal_path, proposal)
    qa = _go(sandbox, proposal_path)
    with pytest.raises(ValueError):
        _activate(sandbox, proposal_path, qa)
    _failed(sandbox, "activation_01")


@pytest.mark.parametrize("name", ["study.lock", "research_device.lock"])
def test_activation_respects_the_original_study_and_device_locks(sandbox, name):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    lock = sandbox.root / name if name == "study.lock" else sandbox.control / name
    with once.lease(lock):
        with pytest.raises(once.BusyStudy):
            _activate(sandbox, proposal, qa, "activation_busy")
    _failed(sandbox, "activation_busy")
    _unchanged(sandbox)
    preserved = (sandbox.tmp / "activation_busy/ACTIVATION_FAILED.json").read_bytes()
    assert _activate(sandbox, proposal, qa, "activation_after_release")["status"] == "ACTIVATED"
    assert (sandbox.tmp / "activation_busy/ACTIVATION_FAILED.json").read_bytes() == preserved


@pytest.mark.parametrize("advance", ["journal", "budget", "empty_stage"])
def test_state_advance_after_proposal_denies_first_activation(sandbox, advance):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    if advance == "journal":
        once.Journal(sandbox.root).append("WAITING_FOR_10K", {"committed_accepted": 6784})
    elif advance == "budget":
        _write(sandbox.root / "TRAINING_BUDGET.json", {"seconds": 1800})
    else:
        (sandbox.root / "stages").mkdir()
    observed = {p: p.read_bytes() for p in sandbox.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        _activate(sandbox, proposal, qa)
    _failed(sandbox, "activation_01")
    assert all(p.read_bytes() == raw for p, raw in observed.items())


def test_exact_go_activates_once_preserves_plan_and_same_out_is_no_clobber(sandbox):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    result = _activate(sandbox, proposal, qa)
    assert result["status"] == "ACTIVATED"
    assert result["training_started"] is result["budget_created"] is False
    marker = sandbox.root / revision.MARKER
    before = marker.read_bytes()
    inode = marker.stat().st_ino
    assert read_json(marker)["historical_plan_replaced"] is False
    _unchanged(sandbox)
    second = _activate(sandbox, proposal, qa, "activation_idempotent")
    assert second["status"] == "ALREADY_ACTIVE"
    assert marker.read_bytes() == before and marker.stat().st_ino == inode
    assert len(list(sandbox.root.glob(revision.MARKER))) == 1
    receipt = sandbox.tmp / "activation_01/ACTIVATION_RECEIPT.json"
    original_receipt = receipt.read_bytes()
    with pytest.raises(FileExistsError):
        _activate(sandbox, proposal, qa)
    assert receipt.read_bytes() == original_receipt
    assert not (sandbox.tmp / "activation_01/ACTIVATION_FAILED.json").exists()
    assert not (sandbox.root / "TRAINING_BUDGET.json").exists()


def test_prepare_is_no_clobber_and_same_proposal_can_be_prepared_at_another_path(sandbox):
    proposal = _prepare(sandbox)
    original = proposal.read_bytes()
    with pytest.raises(FileExistsError):
        _prepare(sandbox)
    assert proposal.read_bytes() == original
    other = _prepare(sandbox, "proposal_02")
    assert read_json(read_json(proposal)["candidate_request"]["path"]) == read_json(
        read_json(other)["candidate_request"]["path"])
    _unchanged(sandbox)


def test_resolver_before_activation_accepts_only_original_plan_equivalent_bytes(sandbox):
    proposal = _prepare(sandbox)
    active, provenance = revision.resolve_active_request(sandbox.root, sandbox.base)
    assert active == sandbox.root / "experiment_plan.json"
    assert provenance["revision_marker"] is None
    with pytest.raises(ValueError):
        revision.resolve_active_request(sandbox.root, read_json(proposal)["candidate_request"]["path"])
    _unchanged(sandbox)


def test_active_resolver_survives_legitimate_journal_advance_but_rejects_old_request(sandbox):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    _activate(sandbox, proposal, qa)
    candidate = Path(read_json(proposal)["candidate_request"]["path"])
    before = (sandbox.root / revision.MARKER).read_bytes()
    once.Journal(sandbox.root).append("WAITING_RESOURCE", {"device": "cpu"})
    (sandbox.root / "selection").mkdir()
    _write(sandbox.root / "TRAINING_BUDGET.json", {"seconds": 1800})
    selected, provenance = revision.resolve_active_request(sandbox.root, candidate)
    assert selected == candidate
    assert provenance["active_request"] == _pin(candidate)
    assert (sandbox.root / revision.MARKER).read_bytes() == before
    with pytest.raises(ValueError):
        revision.resolve_active_request(sandbox.root, sandbox.base)
    same_bytes_another_path = sandbox.tmp / "CANDIDATE_COPY.json"
    same_bytes_another_path.write_bytes(candidate.read_bytes())
    with pytest.raises(ValueError):
        revision.resolve_active_request(sandbox.root, same_bytes_another_path)
    assert _activate(sandbox, proposal, qa, "activation_after_advance")["status"] == "ALREADY_ACTIVE"


@pytest.mark.parametrize("corruption", ["invalid_json", "schema", "status", "root", "candidate", "base", "proposal_sha", "qa_sha", "symlink"])
def test_active_resolver_rejects_corrupt_or_foreign_marker(sandbox, corruption):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    _activate(sandbox, proposal, qa)
    candidate = Path(read_json(proposal)["candidate_request"]["path"])
    marker = sandbox.root / revision.MARKER
    value = read_json(marker)
    if corruption == "invalid_json":
        marker.write_text("{")
    elif corruption == "symlink":
        other = _write(sandbox.tmp / "foreign_marker.json", value)
        marker.unlink()
        marker.symlink_to(other)
    else:
        if corruption in ("schema", "status"):
            value[corruption] = "FOREIGN"
        elif corruption == "root":
            value["study_root"] = str(sandbox.tmp)
        elif corruption in ("candidate", "base"):
            value[corruption + "_request"]["sha256"] = "0" * 64
        else:
            value["proposal" if corruption == "proposal_sha" else "independent_qa"]["sha256"] = "0" * 64
        _write(marker, value)
    with pytest.raises(ValueError):
        revision.resolve_active_request(sandbox.root, candidate)


def test_active_resolver_rejects_changed_original_plan_or_replaced_lock_inode(sandbox):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    _activate(sandbox, proposal, qa)
    candidate = Path(read_json(proposal)["candidate_request"]["path"])
    plan = sandbox.root / "experiment_plan.json"
    original = plan.read_bytes()
    plan.write_text("{}\n")
    with pytest.raises(ValueError):
        revision.resolve_active_request(sandbox.root, candidate)
    plan.write_bytes(original)
    lock = sandbox.root / "study.lock"
    replacement = sandbox.root / "replacement.lock"
    replacement.write_bytes(lock.read_bytes())
    replacement.replace(lock)
    with pytest.raises(ValueError):
        revision.resolve_active_request(sandbox.root, candidate)


def test_failed_attempt_cannot_be_overwritten_and_does_not_block_new_explicit_attempt(sandbox):
    proposal = _prepare(sandbox)
    bad = _go(sandbox, proposal, patch={"status": "NO_GO"}, name="QA_NO_GO.json")
    with pytest.raises(ValueError):
        _activate(sandbox, proposal, bad, "failed_attempt")
    failure = sandbox.tmp / "failed_attempt/ACTIVATION_FAILED.json"
    failed_bytes = failure.read_bytes()
    good = _go(sandbox, proposal, name="QA_GO.json")
    with pytest.raises(FileExistsError):
        _activate(sandbox, proposal, good, "failed_attempt")
    assert failure.read_bytes() == failed_bytes
    assert _activate(sandbox, proposal, good, "new_explicit_attempt")["status"] == "ACTIVATED"
    assert failure.read_bytes() == failed_bytes
    _unchanged(sandbox)


def test_another_qualified_proposal_cannot_replace_an_active_revision(sandbox):
    first = _prepare(sandbox)
    second = _prepare(sandbox, "proposal_02")
    first_go = _go(sandbox, first, name="QA_FIRST.json")
    second_go = _go(sandbox, second, name="QA_SECOND.json")
    _activate(sandbox, first, first_go)
    marker = sandbox.root / revision.MARKER
    preserved = marker.read_bytes()
    with pytest.raises(ValueError):
        _activate(sandbox, second, second_go, "foreign_activation")
    assert marker.read_bytes() == preserved
    failure = read_json(sandbox.tmp / "foreign_activation/ACTIVATION_FAILED.json")
    assert failure["status"] == "FAIL"
    assert failure["failure_preserved"] is True
    _unchanged(sandbox)


@pytest.mark.parametrize("replaced", ["proposal", "go"])
def test_lease_entry_identity_replacement_fails_before_marker(sandbox, monkeypatch, replaced):
    proposal = _prepare(sandbox)
    qa = _go(sandbox, proposal)
    victim = proposal if replaced == "proposal" else qa
    original_bytes = victim.read_bytes()
    proposal_sha, qa_sha = sha256(proposal), sha256(qa)
    original_lease = revision.lease
    attacked = []

    @contextmanager
    def lease_with_real_path_replacement(path):
        with original_lease(path) as fd:
            if Path(path) == sandbox.control / "research_device.lock" and not attacked:
                replacement = sandbox.tmp / "lease_entry_replacement.json"
                # Identical decoded JSON but different bytes/inode: semantic GO
                # validity cannot substitute for the externally supplied exact SHA.
                replacement.write_bytes(original_bytes + b"\n")
                replacement.replace(victim)
                attacked.append(str(victim))
            yield fd

    monkeypatch.setattr(revision, "lease", lease_with_real_path_replacement)
    with pytest.raises(ValueError):
        revision.activate_revision(proposal, proposal_sha, qa, qa_sha,
                                   out=sandbox.tmp / "activation_lease_race")
    assert attacked == [str(victim)]
    _failed(sandbox, "activation_lease_race")
    _unchanged(sandbox)
    assert victim.read_bytes() == original_bytes + b"\n"
    failure = sandbox.tmp / "activation_lease_race/ACTIVATION_FAILED.json"
    preserved = failure.read_bytes()
    renewed_qa = _go(sandbox, proposal, name="QA_POST_RACE_EXACT.json")
    with pytest.raises(FileExistsError):
        revision.activate_revision(proposal, sha256(proposal), renewed_qa, sha256(renewed_qa),
                                   out=sandbox.tmp / "activation_lease_race")
    assert failure.read_bytes() == preserved


@pytest.mark.parametrize("leaf", ["candidate_evidence", "stages/foreign_attempt"])
def test_forged_proposal_root_cannot_admit_output_inside_true_study(sandbox, leaf):
    proposal_path = _prepare(sandbox)
    proposal = read_json(proposal_path)
    foreign = sandbox.tmp / "foreign_claimed_study"
    foreign.mkdir()
    proposal["pretraining_state"]["root"] = str(foreign)
    _write(proposal_path, proposal)
    qa = _go(sandbox, proposal_path)
    original_entries = sorted(str(p.relative_to(sandbox.root)) for p in sandbox.root.rglob("*"))
    output = sandbox.root / leaf
    with pytest.raises(ValueError):
        revision.activate_revision(proposal_path, sha256(proposal_path), qa, sha256(qa), out=output)
    assert not output.exists()
    assert sorted(str(p.relative_to(sandbox.root)) for p in sandbox.root.rglob("*")) == original_entries
    assert not (sandbox.root / revision.MARKER).exists()
    _unchanged(sandbox)


@pytest.mark.parametrize("same_contents", [False, True])
def test_same_read_attestation_rejects_path_replacement_after_real_descriptor_read(tmp_path, monkeypatch, same_contents):
    target = _write(tmp_path / "ATTESTED.json", {"identity": "checked bytes"})
    original_bytes, expected_sha = target.read_bytes(), sha256(target)
    original_stat = target.stat()
    original_read = os.read
    attacked = []

    def replace_path_after_real_read(fd, length):
        chunk = original_read(fd, length)
        observed = os.fstat(fd)
        if (chunk and not attacked and
                (observed.st_dev, observed.st_ino) == (original_stat.st_dev, original_stat.st_ino)):
            replacement = tmp_path / "REPLACEMENT.json"
            replacement.write_bytes(original_bytes if same_contents else b'{"identity":"foreign bytes"}\n')
            replacement.replace(target)
            attacked.append(True)
        return chunk

    monkeypatch.setattr(revision.os, "read", replace_path_after_real_read)
    with pytest.raises(ValueError):
        revision._read_expected(target, expected_sha)
    assert attacked == [True], "must hit the real descriptor-read window, not pass vacuously"
    assert target.stat().st_ino != original_stat.st_ino
    if same_contents:
        assert target.read_bytes() == original_bytes
