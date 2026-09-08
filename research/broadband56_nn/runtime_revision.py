"""Explicit pretraining-only runtime promotion; no scheduler or training here.

The historical request/plan, journal and lock are never replaced. An independently
pinned GO may publish one immutable active-request pointer. The old launcher is
not cryptographically disabled; callers must use the resolving suite entrypoint.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .io import canonical_sha, sha256, utc_now
from .study_once import atomic_json, lease, study_key


CHANGED_MODULES = frozenset({"delivery.py", "bb00_delivery.py", "seven_suite.py"})
ADDED_MODULES = frozenset({"runtime_revision.py"})
PRETRAINING_FILES = frozenset({"experiment_plan.json", "run_status.json",
                              "events/event_000001.json", "study.lock"})
MARKER = "ACTIVE_RUNTIME_REVISION.json"
GO_STATUS = "GO_FOR_PRETRAINING_RUNTIME_REVISION"


def _regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"regular non-symlink metadata/source required: {path}")
    return path.resolve(strict=True)


def _attest(path, expected_sha256=None, *, parse_json=False):
    """Hash/parse one descriptor, rejecting replacement while it is read."""
    supplied = Path(path).absolute()
    resolved = _regular(supplied)
    fd = os.open(supplied, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("regular revision input descriptor required")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("explicit runtime revision SHA mismatch")
        payload = json.loads(raw.decode("utf-8")) if parse_json else raw
        after = os.fstat(fd)
        current = os.stat(supplied, follow_symlinks=False)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(current) or
                not stat.S_ISREG(current.st_mode) or supplied.resolve(strict=True) != resolved or
                len(raw) != after.st_size):
            raise ValueError("revision input changed during same-descriptor attestation")
        return payload, {"path": str(resolved), "sha256": digest, "bytes": len(raw)}
    finally:
        os.close(fd)


def _pin(path):
    return _attest(path)[1]


def _checked(pin):
    actual = _pin(pin["path"])
    if actual != {key: pin[key] for key in ("path", "sha256", "bytes")}:
        raise ValueError("immutable runtime revision input changed")
    return Path(actual["path"])


def _expected_sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("explicit SHA-256 identity required")
    return value


def _read_expected(path, expected):
    return _attest(path, _expected_sha(expected), parse_json=True)[0]


def _read_pin(pin):
    value, actual = _attest(pin["path"], _expected_sha(pin["sha256"]), parse_json=True)
    if actual != {key: pin[key] for key in ("path", "sha256", "bytes")}:
        raise ValueError("attested revision metadata pin differs")
    return value


def _software():
    folder = Path(__file__).resolve().parent
    return {path.name: _pin(path) for path in sorted(folder.glob("*.py"))}


def _study_root(request):
    if request.get("schema") != "bb_seven_study_request.v1":
        raise ValueError("seven-model base request required")
    expected = study_key(request["campaign_id"], request["suite_version"])
    if request.get("study_key") != expected or request.get("milestone_geometries") != 10000:
        raise ValueError("stable campaign/10K/suite study identity differs")
    control = Path(request["control_root"])
    if control.is_symlink() or not control.is_dir():
        raise ValueError("original control root must already exist")
    root = control.resolve(strict=True) / expected
    if root.is_symlink() or not root.is_dir():
        raise ValueError("original study root must already exist")
    return root


def _lock_identity(root):
    pin = _pin(root / "study.lock")
    stat = Path(pin["path"]).stat()
    return {**pin, "device": stat.st_dev, "inode": stat.st_ino}


def _pretraining_state(root, base_sha):
    files = {}
    for path in root.rglob("*"):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            raise ValueError("symlink in pretraining study")
        if path.is_dir():
            if relative != "events":
                raise ValueError("study has non-pretraining directories")
        elif path.is_file():
            files[relative] = _pin(path)
        else:
            raise ValueError("unexpected special file in study")
    if set(files) != PRETRAINING_FILES:
        raise ValueError("runtime promotion requires exactly the original four pretraining files")
    if files["experiment_plan.json"]["sha256"] != base_sha:
        raise ValueError("original experiment plan differs from base request")
    event = _read_pin(files["events/event_000001.json"])
    if (files["run_status.json"]["sha256"] != files["events/event_000001.json"]["sha256"] or
            event.get("schema") != "bb_study_event.v1" or event.get("sequence") != 1 or
            event.get("prior_event_sha256") is not None or event.get("status") != "WAITING_FOR_10K"):
        raise ValueError("original single waiting journal/event identity required")
    return {"root": str(root), "files": files, "study_lock": _lock_identity(root)}


def _source_delta(base, candidate):
    old, current = base["software"], candidate["software"]
    if len(old) != 25 or set(current) != set(old) | ADDED_MODULES or set(old) & ADDED_MODULES:
        raise ValueError("only the original 25 modules plus runtime_revision.py are allowed")
    if {key: value for key, value in candidate.items() if key != "software"} != {
            key: value for key, value in base.items() if key != "software"}:
        raise ValueError("runtime revision changed non-software research contract fields")
    if current != _software():
        raise ValueError("candidate source paths/bytes differ from this runtime")
    old_parents = set()
    for name, record in old.items():
        path = _checked(record)
        if path.name != name or not name.endswith(".py"):
            raise ValueError("base source module name/path differs")
        old_parents.add(path.parent)
        if name not in CHANGED_MODULES and record["sha256"] != current[name]["sha256"]:
            raise ValueError(f"non-allowlisted scientific/runtime source changed: {name}")
    if len(old_parents) != 1:
        raise ValueError("original runtime source is not a single frozen module directory")


def _outside_new_out(out, root):
    out = Path(out).absolute()
    resolved = out.resolve()
    if resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved):
        raise ValueError("revision evidence output must be separate from the original study")
    if out.is_symlink():
        raise ValueError("revision evidence output cannot be a symlink")
    out.mkdir(parents=True, exist_ok=False)
    return out.resolve(strict=True)


def _copy_original(source, destination):
    raw, expected = _attest(source)
    with destination.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    result = _pin(destination)
    if result["sha256"] != expected["sha256"] or result["bytes"] != expected["bytes"]:
        raise ValueError("immutable predecessor copy differs")
    return result


def _index_matches(path, state, expected_pin=None):
    raw, actual = _attest(path, expected_pin["sha256"] if expected_pin else None)
    if expected_pin is not None and actual != expected_pin:
        raise ValueError("predecessor index attestation differs")
    index = {}
    for line in raw.decode("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if name in index:
            raise ValueError("duplicate predecessor SHA index entry")
        index[name] = digest
    for record in state["files"].values():
        relative = str(Path(record["path"]).relative_to(Path(path).parent))
        if index.get(relative) != record["sha256"]:
            raise ValueError("predecessor index does not pin the original study state")


def prepare_revision(base_request_path, base_sha256, predecessor_index_path,
                     predecessor_index_sha256, out):
    """Create a no-clobber proposal only; never touch the original study."""
    base, base_pin = _attest(base_request_path, _expected_sha(base_sha256), parse_json=True)
    root = _study_root(base)
    output = _outside_new_out(out, root)
    try:
        _, index_pin = _attest(predecessor_index_path, _expected_sha(predecessor_index_sha256))
        index = Path(index_pin["path"])
        before = _pretraining_state(root, base_sha256)
        _index_matches(index, before, index_pin)
        candidate = {**base, "software": _software()}
        _source_delta(base, candidate)
        copies = {name: _copy_original(record["path"], output / ("PREDECESSOR_" + name.replace("/", "__")))
                  for name, record in before["files"].items()}
        base_copy = _copy_original(base_request_path, output / "BASE_REQUEST.json")
        index_copy = _copy_original(index, output / "PREDECESSOR_SHA256SUMS.txt")
        atomic_json(output / "CANDIDATE_REQUEST.json", candidate, immutable=True)
        if _pretraining_state(root, base_sha256) != before:
            raise ValueError("study changed during proposal preparation")
        _checked(base_pin)
        _checked(index_pin)
        proposal = {"schema": "bb_pretraining_runtime_revision_proposal.v1", "status": "CANDIDATE_ONLY",
            "created_utc": utc_now(), "base_request": base_pin, "base_request_copy": base_copy,
            "predecessor_index": index_pin, "predecessor_index_copy": index_copy,
            "pretraining_state": before, "predecessor_copies": copies,
            "candidate_request": _pin(output / "CANDIDATE_REQUEST.json"),
            "candidate_software_sha256": canonical_sha(candidate["software"]),
            "runtime_revision_source_sha256": sha256(__file__),
            "allowed_changed_modules": sorted(CHANGED_MODULES), "added_modules": sorted(ADDED_MODULES),
            "scientific_contract_changed": False, "training_started": False, "budget_created": False}
        atomic_json(output / "PROPOSAL.json", proposal, immutable=True)
        return proposal
    except Exception as error:
        atomic_json(output / "PREPARATION_FAILED.json", {"status": "FAIL", "created_utc": utc_now(),
            "error": f"{type(error).__name__}: {error}", "original_study_modified": False}, immutable=True)
        raise


def _validate_proposal(proposal):
    if proposal.get("schema") != "bb_pretraining_runtime_revision_proposal.v1" or proposal.get("status") != "CANDIDATE_ONLY":
        raise ValueError("unknown runtime revision proposal")
    if any(proposal.get(key) is not False for key in
           ("scientific_contract_changed", "training_started", "budget_created")):
        raise ValueError("proposal requires explicit unchanged/non-training flags")
    base = _read_pin(proposal["base_request"])
    candidate_path = _checked(proposal["candidate_request"])
    candidate = _read_pin(proposal["candidate_request"])
    _source_delta(base, candidate)
    root = _study_root(base)
    before = proposal["pretraining_state"]
    if before.get("root") != str(root) or set(before.get("files", {})) != PRETRAINING_FILES:
        raise ValueError("proposal points to another pretraining study")
    if (proposal.get("allowed_changed_modules") != sorted(CHANGED_MODULES) or
            proposal.get("added_modules") != sorted(ADDED_MODULES) or
            proposal.get("runtime_revision_source_sha256") != sha256(__file__) or
            proposal.get("candidate_software_sha256") != canonical_sha(candidate["software"])):
        raise ValueError("proposal source allowlist/identity differs")
    for original_name, copy_name in (("base_request", "base_request_copy"),
                                     ("predecessor_index", "predecessor_index_copy")):
        _checked(proposal[original_name])
        _checked(proposal[copy_name])
        if proposal[original_name]["sha256"] != proposal[copy_name]["sha256"]:
            raise ValueError("predecessor metadata copy changed")
    if set(proposal.get("predecessor_copies", {})) != PRETRAINING_FILES:
        raise ValueError("missing preserved original study metadata")
    for name, copy in proposal["predecessor_copies"].items():
        _checked(copy)
        if copy["sha256"] != before["files"][name]["sha256"]:
            raise ValueError("predecessor study copy differs")
    # Compare the historical recorded digests, not the now-mutable live journal.
    _index_matches(proposal["predecessor_index"]["path"], before, proposal["predecessor_index"])
    # Plan and permanent lock stay immutable after activation. Journal/status
    # intentionally are NOT re-required here: normal execution advances them.
    if (_pin(root / "experiment_plan.json") != before["files"]["experiment_plan.json"] or
            _lock_identity(root) != before["study_lock"]):
        raise ValueError("original plan/permanent lock changed")
    return root, candidate_path


def _validate_go(path, expected_sha, proposal_pin, proposal):
    qa, qa_pin = _attest(path, _expected_sha(expected_sha), parse_json=True)
    required = {"status": GO_STATUS, "proposal_sha256": proposal_pin["sha256"],
        "base_request_sha256": proposal["base_request"]["sha256"],
        "candidate_request_sha256": proposal["candidate_request"]["sha256"],
        "candidate_software_sha256": proposal["candidate_software_sha256"],
        "runtime_revision_source_sha256": proposal["runtime_revision_source_sha256"]}
    if any(qa.get(key) != value for key, value in required.items()):
        raise ValueError("missing exact independent pretraining runtime revision GO")
    return qa_pin


def _active(root):
    marker, marker_pin = _attest(root / MARKER, parse_json=True)
    if marker.get("schema") != "bb_active_runtime_revision.v1" or marker.get("status") != "ACTIVE":
        raise ValueError("partial or foreign active runtime marker")
    if any(marker.get(key) is not False for key in
           ("historical_plan_replaced", "training_started", "budget_created")):
        raise ValueError("active marker requires explicit non-training flags")
    proposal = _read_pin(marker["proposal"])
    expected_root, candidate_path = _validate_proposal(proposal)
    qa_pin = _validate_go(_checked(marker["independent_qa"]), marker["independent_qa"]["sha256"],
                          marker["proposal"], proposal)
    if (root != expected_root or marker.get("study_root") != str(root) or
            marker.get("candidate_request") != proposal["candidate_request"] or
            marker.get("base_request") != proposal["base_request"] or qa_pin != marker["independent_qa"]):
        raise ValueError("active runtime marker identity differs")
    return candidate_path, {"base_plan": proposal["pretraining_state"]["files"]["experiment_plan.json"],
        "active_request": proposal["candidate_request"], "revision_marker": marker_pin,
        "proposal": marker["proposal"], "qa": qa_pin}


def activate_revision(proposal_path, expected_proposal_sha256, qa_path, expected_qa_sha256, *, out):
    """Publish only after exact GO under original locks; no automatic retry."""
    proposal, proposal_pin = _attest(proposal_path, _expected_sha(expected_proposal_sha256), parse_json=True)
    # Establish the true, independently hash-bound base study BEFORE admitting
    # any output writes. An untrusted proposal root cannot redirect this guard.
    root = _study_root(_read_pin(proposal["base_request"]))
    if proposal["pretraining_state"].get("root") != str(root):
        raise ValueError("proposal study root differs from hash-verified base")
    output = _outside_new_out(out, root)
    try:
        actual_root, _ = _validate_proposal(proposal)
        if root != actual_root:
            raise ValueError("proposal study path differs")
        qa_pin = _validate_go(qa_path, expected_qa_sha256, proposal_pin, proposal)

        def already_active():
            _, provenance = _active(root)
            if provenance["proposal"] != proposal_pin or provenance["qa"] != qa_pin:
                raise ValueError("another runtime revision is already active")
            return {"status": "ALREADY_ACTIVE", "provenance": provenance}

        if (root / MARKER).exists() or (root / MARKER).is_symlink():
            result = already_active()
        else:
            device_lock = root.parent / "research_device.lock"
            if device_lock.is_symlink() or (device_lock.exists() and not device_lock.is_file()):
                raise ValueError("invalid original research device lock")
            with lease(root / "study.lock"), lease(device_lock):
                if (root / MARKER).exists() or (root / MARKER).is_symlink():
                    result = already_active()
                else:
                    # Reattest disk bytes under BOTH original locks, rather than
                    # trusting a proposal/GO object read before lease acquisition.
                    locked_proposal, locked_pin = _attest(proposal_path, expected_proposal_sha256, parse_json=True)
                    if locked_pin != proposal_pin or locked_proposal != proposal:
                        raise ValueError("proposal changed while acquiring original locks")
                    locked_root, _ = _validate_proposal(locked_proposal)
                    locked_qa = _validate_go(qa_path, expected_qa_sha256, locked_pin, locked_proposal)
                    if locked_root != root or locked_qa != qa_pin:
                        raise ValueError("GO/base binding changed while acquiring original locks")
                    if _pretraining_state(root, locked_proposal["base_request"]["sha256"]) != locked_proposal["pretraining_state"]:
                        raise ValueError("original pretraining state changed before activation")
                    marker = {"schema": "bb_active_runtime_revision.v1", "status": "ACTIVE",
                        "created_utc": utc_now(), "study_root": str(root), "proposal": proposal_pin,
                        "independent_qa": qa_pin, "base_request": proposal["base_request"],
                        "candidate_request": proposal["candidate_request"],
                        "historical_plan_replaced": False, "training_started": False, "budget_created": False}
                    # Last read-only gates immediately before exclusive publish.
                    _checked(proposal_pin)
                    _checked(qa_pin)
                    atomic_json(root / MARKER, marker, immutable=True)
                    _, provenance = _active(root)
                    result = {"status": "ACTIVATED", "provenance": provenance}
        result.update(schema="bb_runtime_revision_activation_receipt.v1", created_utc=utc_now(),
                      training_started=False, budget_created=False)
        atomic_json(output / "ACTIVATION_RECEIPT.json", result, immutable=True)
        return result
    except Exception as error:
        atomic_json(output / "ACTIVATION_FAILED.json", {"schema": "bb_runtime_revision_activation_receipt.v1",
            "status": "FAIL", "created_utc": utc_now(), "error": f"{type(error).__name__}: {error}",
            "failure_preserved": True, "automatic_retry": False}, immutable=True)
        raise


def resolve_active_request(root, requested_path):
    """Select an immutable request without writes; caller validates environment."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("existing original study root required")
    root = root.resolve(strict=True)
    requested = _pin(requested_path)
    if (root / MARKER).exists() or (root / MARKER).is_symlink():
        candidate, provenance = _active(root)
        if requested != provenance["active_request"]:
            raise ValueError("active runtime requires its exact explicitly pinned candidate request path")
        return candidate, provenance
    original = _pin(root / "experiment_plan.json")
    if requested["sha256"] != original["sha256"] or requested["bytes"] != original["bytes"]:
        raise ValueError("no active revision: only original experiment-plan-equivalent bytes are allowed")
    if _study_root(_read_pin(original)) != root:
        raise ValueError("original request control root/study key differs")
    return Path(original["path"]), {"base_plan": original, "active_request": original,
                                    "revision_marker": None}
