#!/usr/bin/env python3
"""Fresh result-blind root red-team re-execution for preflight-v3.

The hostile logic is inherited from the frozen preflight-v2 independent audit
method, with only version-specific paths/schemas/counts adapted plus an exact
preflight-v2 NO-GO provenance gate.  This is a root re-execution, not a claim
of fresh reviewer independence.  It reads only frozen local candidates and
provenance, runs disposable fixtures, and never accesses MARS, model/EMX
results, production roots, processes, signals, or transport.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


WORKSPACE = Path("${LOCAL_WORKSPACE}")
PHYSICAL = WORKSPACE / "reports/historical_200k_fixed10k_mars_physical_20260822"
CANDIDATE = PHYSICAL / "result_free_mars_native_preflight_v3_prepared_20260822T230419Z"
MATRIX = (
    PHYSICAL
    / "root_redteam_result_free_mars_native_preflight_v3_qa_wip_20260822T230613Z"
    / "INHERITED_TEST_MATRIX_RESULT_BLIND_V2.md"
)
V10_PACKAGE = PHYSICAL / "transport_runtime_layout_builder_v10_prepared_20260822T205518Z"
V10_QA = PHYSICAL / "independent_transport_runtime_layout_builder_v10_qa_20260822T211115Z"
V1_PACKAGE = PHYSICAL / "result_free_mars_native_preflight_v1_prepared_20260822T175831Z"
V1_QA = PHYSICAL / "independent_result_free_mars_native_preflight_v1_qa_20260822T180904Z"
V2_QA = PHYSICAL / "independent_result_free_mars_native_preflight_v2_qa_20260822T215429Z"

EXPECTED_CANDIDATE = frozenset({
    "AUTHOR_COMPILE_V3_OUTPUT.json",
    "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json",
    "BUNDLE_MANIFEST.json",
    "PREPARED_RESULT_FREE_RECEIPT.json",
    "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json",
    "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V3_CN.md",
    "SHA256SUMS",
    "UPSTREAM_EVIDENCE_BINDINGS_V3.json",
    "run_result_free_mars_native_preflight_v3.py",
    "test_result_free_mars_native_preflight_v3_synthetic.py",
})
EXPECTED_CANDIDATE_INDEX = EXPECTED_CANDIDATE - {"SHA256SUMS"}
EXPECTED_CANDIDATE_PAYLOAD = EXPECTED_CANDIDATE - {
    "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json", "SHA256SUMS"
}

V10_PACKAGE_MEMBERS = frozenset({
    "AUTHOR_BUILDER_V10_SYNTHETIC_OUTPUT.json",
    "AUTHOR_COMPILE_V10_OUTPUT.json",
    "AUTHOR_DOUBLE_RUN_V10_VALIDATION.json",
    "AUTHOR_SMOKE_V10_SYNTHETIC_OUTPUT.json",
    "AUTHOR_V10_WIP_FAILURES.json",
    "BUNDLE_MANIFEST.json",
    "PREPARED_RESULT_FREE_RECEIPT.json",
    "SHA256SUMS",
    "TRANSPORT_RUNTIME_LAYOUT_BUILDER_V10_PREPARED_CN.md",
    "TRANSPORT_RUNTIME_LAYOUT_CONTRACT_V10.json",
    "UPSTREAM_EVIDENCE_BINDINGS_V10.json",
    "build_result_free_transport_runtime_v10.py",
    "result_free_runtime_smoke_v10.py",
    "test_result_free_runtime_smoke_v10_synthetic.py",
    "test_transport_runtime_layout_builder_v10_synthetic.py",
})
V10_QA_MEMBERS = frozenset({
    "BUNDLE_MANIFEST.json", "COMMAND_LOG.txt",
    "FIXTURE_PROTOTYPE_ATTEMPT1_FAILURE.log",
    "FIXTURE_PROTOTYPE_ATTEMPT2_FAILURE.log",
    "HARNESS_ATTEMPT1_FAILURE.log", "HARNESS_ATTEMPT2_FAILURE.log",
    "HARNESS_DOUBLE_RUN_VALIDATION.json", "INDEPENDENT_QA_HARNESS.py",
    "INDEPENDENT_QA_OUTPUT.json", "INDEPENDENT_QA_RECEIPT.json",
    "INDEPENDENT_QA_REPORT_CN.md", "PACKAGE_CLOSURE_QA.json",
    "SHA256SUMS", "TEST_MATRIX_RESULT_BLIND_V10_WIP.md",
})

V10_PACKAGE_ANCHORS = {
    "build_result_free_transport_runtime_v10.py": "fefbfcf8ecc77dcc55ba509e803e0c4a442f1e61164e02eec38f6e34c03d9de1",
    "test_transport_runtime_layout_builder_v10_synthetic.py": "cea2026629fd750009daf2d0926f9323f725e5ba4b1307ba05d08c901ce8c96e",
    "result_free_runtime_smoke_v10.py": "93d78448eb37fa47ad2760e78d8e0148d5018f06985e7b2a66649288682b6282",
    "test_result_free_runtime_smoke_v10_synthetic.py": "0c17ef19dff6f4abeff00d9ad64903cd414b15c93bb0ba431a7d9aac238c73be",
    "PREPARED_RESULT_FREE_RECEIPT.json": "b73aed2f3a2f6e225390c9d2df402fa8e61a457e70ec55b73af1b845f1f7ec2b",
    "BUNDLE_MANIFEST.json": "9a5bfca586a7cc2e0ed795cc3c785bc74889a285644e950d40a21fc8bd1fec28",
    "SHA256SUMS": "e2073343323a19a153843079dd8b787c97929c02b6c9c4152fd03e0e2799acb2",
}
V10_QA_ANCHORS = {
    "INDEPENDENT_QA_RECEIPT.json": "ba35a9a1f597e81c819a43c3a22920a19873c27c88cf1ea6a1d2ca8e6cac5d45",
    "INDEPENDENT_QA_REPORT_CN.md": "f9e054d940a7f5a8f319cc51efefcb0ea1df99aaed3d591eae04408cc67d2a24",
    "SHA256SUMS": "f0bc12e7b359aa3bd934f33b643f4836a876c6ed672b29ee525c546dc75d539a",
    "BUNDLE_MANIFEST.json": "1c5b26207f5e5b12fdf5dbad6c5fc29b65a14f631c65cca78f33564bab41a942",
    "INDEPENDENT_QA_OUTPUT.json": "2b1a2dc1ac91c52c130eb522c6f53d19784879ee317e9aed9a5a7e6e947d1749",
    "INDEPENDENT_QA_HARNESS.py": "99f5fe129ec3667db57cd4c7db6d6589897726427b7eb3901d0f39ea2fc7467d",
    "COMMAND_LOG.txt": "ce557892bcbf32be412ae0298bf3dd01fa81519d7cb84d1e69e268c6cab44081",
    "PACKAGE_CLOSURE_QA.json": "7efec95bbe8e636567f07434f19b80a0b050c68553f6f3ce59760501aab2025a",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path) -> bytes:
    return path.read_bytes()


def duplicate_safe_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if type(key) is not str or key in result:
            raise ValueError("duplicate or non-string key")
        result[key] = value
    return result


def reject_constant(token: str) -> Any:
    raise ValueError("non-finite constant " + token)


def reject_loose_types(value: Any) -> None:
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("non-string key")
            reject_loose_types(child)
    elif type(value) is list:
        for child in value:
            reject_loose_types(child)
    elif type(value) not in {str, int, bool}:
        raise ValueError("loose JSON type " + type(value).__name__)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def strict_json(data: bytes) -> dict[str, Any]:
    value = json.loads(
        data.decode("utf-8", "strict"),
        object_pairs_hook=duplicate_safe_pairs,
        parse_constant=reject_constant,
    )
    if type(value) is not dict:
        raise ValueError("top-level object required")
    reject_loose_types(value)
    if canonical(value) != data:
        raise ValueError("noncanonical JSON")
    return value


def parse_index(data: bytes, expected: frozenset[str]) -> dict[str, str]:
    text = data.decode("ascii", "strict")
    if not text.endswith("\n") or "\r" in text:
        raise ValueError("noncanonical index line ending")
    rows = text[:-1].split("\n") if text[:-1] else []
    result: dict[str, str] = {}
    order: list[str] = []
    for row in rows:
        if len(row) < 67 or row[64:66] != "  ":
            raise ValueError("malformed index row")
        digest, name = row[:64], row[66:]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("bad digest")
        if not name or "/" in name or name in {".", ".."} or name in result:
            raise ValueError("unsafe/duplicate name")
        result[name] = digest
        order.append(name)
    if frozenset(result) != expected or order != sorted(order):
        raise ValueError("index member/order mismatch")
    return result


def frozen_closure(root: Path, members: frozenset[str], indexed: frozenset[str]) -> dict[str, str]:
    root_info = os.stat(root, follow_symlinks=False)
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_IMODE(root_info.st_mode) != 0o555:
        raise ValueError("root mode/type mismatch")
    observed = frozenset(item.name for item in root.iterdir())
    if observed != members:
        raise ValueError("top-level closure mismatch")
    if any(item.is_symlink() or item.is_dir() for item in root.iterdir()):
        raise ValueError("non-regular top-level member")
    for item in root.iterdir():
        info = os.stat(item, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o444
            or info.st_nlink != 1
        ):
            raise ValueError("file mode/type/nlink mismatch: " + item.name)
        if item.name == "__pycache__" or item.suffix in {".pyc", ".pyo"}:
            raise ValueError("cache artifact")
    index = parse_index(read(root / "SHA256SUMS"), indexed)
    for name, digest in index.items():
        if sha(read(root / name)) != digest:
            raise ValueError("indexed digest mismatch: " + name)
    return index


def load_candidate() -> Any:
    source = CANDIDATE / "run_result_free_mars_native_preflight_v3.py"
    spec = importlib.util.spec_from_file_location("root_redteam_preflight_v3_candidate", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate import spec unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def raises(call: Callable[[], Any]) -> bool:
    try:
        call()
    except BaseException:
        return True
    return False


def local_publish(directory_fd: int, name: str, data: bytes) -> dict[str, Any]:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError("unsafe basename")
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)
    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    return {
        "name": name,
        "sha256": sha(data),
        "size_bytes": len(data),
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "nlink": info.st_nlink,
    }


class NoopLeaseSet:
    def revalidate(self, _phase: str) -> None:
        return None


class NeverBuilder:
    calls = 0


class LocalBuilder:
    calls = 0

    def __init__(self, module: Any) -> None:
        self.module = module
        self.NATIVE_COMPATIBILITY_API_SCHEMA = module.API_SCHEMA
        self.renameat2_noreplace = lambda *_a, **_k: None
        self.publish_terminal_linux_otmpfile_noreplace = lambda *_a, **_k: None

    def execute_scoped_noncanonical_native_compatibility_preflight_v1(
        self,
        *,
        request: dict[str, Any],
        production_parent_fd: int,
        compatibility_work_root_fd: int,
        rename_impl: Any,
        terminal_publish_impl: Any,
    ) -> dict[str, Any]:
        del production_parent_fd, rename_impl, terminal_publish_impl
        type(self).calls += 1
        for key in ("compatibility_root", "compatibility_journal"):
            os.mkdir(Path(request[key]).name, 0o700, dir_fd=compatibility_work_root_fd)
        os.fsync(compatibility_work_root_fd)
        return {
            "schema": self.module.API_SCHEMA,
            "status": self.module.API_STATUS,
            "scope": self.module.API_SCOPE,
            "decision_id": request["decision_id"],
            "authorization_sha256": request["authorization_sha256"],
            "compatibility_root": request["compatibility_root"],
            "compatibility_journal": request["compatibility_journal"],
            "publication": {
                "renameat2_noreplace": True,
                "otmpfile_procfd_linkat": True,
                "pathname_fallback_used": False,
            },
            "production_guards": {
                "final_root_absent_before_after": True,
                "journal_absent_before_after": True,
                "parent_inode_held": True,
                "canonical_alias_rejected": True,
            },
            "result_accessed": False,
            "signals_sent": False,
            "external_processes_inspected": False,
            "controller_or_resume_executed": False,
        }


@contextlib.contextmanager
def patched_transaction_paths(module: Any, base: Path) -> Iterator[dict[str, Any]]:
    # Keep every candidate path constant unchanged.  In particular,
    # reject_compatibility_aliases intentionally freezes its keyword defaults
    # at definition time.  Disposable held directory FDs are sufficient for
    # this result-blind transaction fixture, while the request continues to
    # carry the candidate's exact production path strings.
    production_parent = base / "production-parent"
    production_parent.mkdir(parents=True)
    work_parent = base / "work-parent"
    work_parent.mkdir()
    work_root_path = work_parent / "decision"
    parent_fd = os.open(work_parent, os.O_RDONLY)
    production_fd = os.open(production_parent, os.O_RDONLY)
    work = module.MutableDirectoryLease.create_or_open_at(
        parent_fd, work_parent, work_root_path.name, "independent.work"
    )
    evidence = module.MutableDirectoryLease.create_or_open_at(
        work.fd, work.path, "evidence", "independent.evidence"
    )
    try:
        yield {
            "parent_fd": parent_fd,
            "production_fd": production_fd,
            "work": work,
            "evidence": evidence,
        }
    finally:
        evidence.close()
        work.close()
        os.close(production_fd)
        os.close(parent_fd)


def write_record(module: Any, directory_fd: int, name: str, value: Mapping[str, Any]) -> None:
    local_publish(directory_fd, name, module.canonical_json_bytes(dict(value)))


def seed_pass_work_members(module: Any, work_fd: int) -> None:
    for path in (module.EXPECTED_COMPATIBILITY_ROOT, module.EXPECTED_COMPATIBILITY_JOURNAL):
        os.mkdir(path.name, 0o700, dir_fd=work_fd)
    os.fsync(work_fd)


def valid_raw_result(module: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": module.API_SCHEMA,
        "status": module.API_STATUS,
        "scope": module.API_SCOPE,
        "decision_id": request["decision_id"],
        "authorization_sha256": request["authorization_sha256"],
        "compatibility_root": request["compatibility_root"],
        "compatibility_journal": request["compatibility_journal"],
        "publication": {
            "renameat2_noreplace": True,
            "otmpfile_procfd_linkat": True,
            "pathname_fallback_used": False,
        },
        "production_guards": {
            "final_root_absent_before_after": True,
            "journal_absent_before_after": True,
            "parent_inode_held": True,
            "canonical_alias_rejected": True,
        },
        "result_accessed": False,
        "signals_sent": False,
        "external_processes_inspected": False,
        "controller_or_resume_executed": False,
    }


def transaction_hostiles(module: Any, base: Path, observations: dict[str, Any]) -> dict[str, bool]:
    auth_sha = "a" * 64
    checks: dict[str, bool] = {}

    # A forged replay must not be accepted without its canonical result record.
    with patched_transaction_paths(module, base / "existing-pass-missing-result") as fx:
        begin = module.make_begin(auth_sha)
        request = module.make_compatibility_request(auth_sha)
        intent = module.make_intent(begin, request)
        terminal = module.make_terminal(
            passed=True,
            auth_sha=auth_sha,
            begin_sha=sha(module.canonical_json_bytes(begin)),
            intent_sha=sha(module.canonical_json_bytes(intent)),
            result_sha="b" * 64,
            phase="hostile-existing-pass-without-result",
        )
        for name, value in (
            (module.BEGIN_NAME, begin),
            (module.INTENT_NAME, intent),
            (module.PASS_NAME, terminal),
        ):
            write_record(module, fx["evidence"].fd, name, value)
        seed_pass_work_members(module, fx["work"].fd)
        NeverBuilder.calls = 0
        accepted = False
        returned = ""
        try:
            returned = module.execute_preflight_transaction(
                builder=NeverBuilder,
                auth_sha=auth_sha,
                production_parent_fd=fx["production_fd"],
                work_root=fx["work"],
                evidence=fx["evidence"],
                immutable_leases=NoopLeaseSet(),
                publisher=local_publish,
            )
            accepted = returned == "ALREADY_TERMINAL_PASS"
        except BaseException as exc:
            returned = type(exc).__name__ + ":" + str(exc)
        observations["existing_pass_missing_result"] = {
            "accepted": accepted,
            "returned": returned,
            "builder_calls": NeverBuilder.calls,
            "evidence_members": sorted(os.listdir(fx["evidence"].fd)),
        }
        checks["E06_E18_existing_pass_requires_result_record"] = not accepted

    # A present result must be hash-bound by the PASS terminal during replay.
    with patched_transaction_paths(module, base / "existing-pass-mismatched-result") as fx:
        begin = module.make_begin(auth_sha)
        request = module.make_compatibility_request(auth_sha)
        intent = module.make_intent(begin, request)
        result_record = module.make_result_record(valid_raw_result(module, request), request)
        actual_result_sha = sha(module.canonical_json_bytes(result_record))
        terminal = module.make_terminal(
            passed=True,
            auth_sha=auth_sha,
            begin_sha=sha(module.canonical_json_bytes(begin)),
            intent_sha=sha(module.canonical_json_bytes(intent)),
            result_sha="c" * 64,
            phase="hostile-existing-pass-wrong-result-hash",
        )
        for name, value in (
            (module.BEGIN_NAME, begin),
            (module.INTENT_NAME, intent),
            (module.RESULT_NAME, result_record),
            (module.PASS_NAME, terminal),
        ):
            write_record(module, fx["evidence"].fd, name, value)
        seed_pass_work_members(module, fx["work"].fd)
        accepted = False
        returned = ""
        try:
            returned = module.execute_preflight_transaction(
                builder=NeverBuilder,
                auth_sha=auth_sha,
                production_parent_fd=fx["production_fd"],
                work_root=fx["work"],
                evidence=fx["evidence"],
                immutable_leases=NoopLeaseSet(),
                publisher=local_publish,
            )
            accepted = returned == "ALREADY_TERMINAL_PASS"
        except BaseException as exc:
            returned = type(exc).__name__ + ":" + str(exc)
        observations["existing_pass_mismatched_result"] = {
            "accepted": accepted,
            "returned": returned,
            "terminal_declared_result_sha256": "c" * 64,
            "actual_result_sha256": actual_result_sha,
        }
        checks["E06_E18_existing_pass_result_hash_cross_binding"] = not accepted

    # Once BEGIN is durable, an ordinary exception must yield a durable FAIL now.
    with patched_transaction_paths(module, base / "exception-after-begin") as fx:
        def publish_then_raise(directory_fd: int, name: str, data: bytes) -> dict[str, Any]:
            result = local_publish(directory_fd, name, data)
            if name == module.BEGIN_NAME:
                raise RuntimeError("independent injected exception after durable BEGIN")
            return result

        raised = False
        try:
            module.execute_preflight_transaction(
                builder=NeverBuilder,
                auth_sha=auth_sha,
                production_parent_fd=fx["production_fd"],
                work_root=fx["work"],
                evidence=fx["evidence"],
                immutable_leases=NoopLeaseSet(),
                publisher=publish_then_raise,
            )
        except BaseException:
            raised = True
        members = sorted(os.listdir(fx["evidence"].fd))
        has_fail = module.FAIL_NAME in members
        observations["exception_after_durable_begin"] = {
            "raised": raised,
            "evidence_members": members,
            "durable_fail_present": has_fail,
        }
        checks["E08_exception_after_durable_begin_publishes_fail"] = raised and has_fail

    # Independent positive controls: new PASS and its complete replay.
    with patched_transaction_paths(module, base / "positive-pass-replay") as fx:
        LocalBuilder.calls = 0
        builder = LocalBuilder(module)
        first = module.execute_preflight_transaction(
            builder=builder,
            auth_sha=auth_sha,
            production_parent_fd=fx["production_fd"],
            work_root=fx["work"],
            evidence=fx["evidence"],
            immutable_leases=NoopLeaseSet(),
            publisher=local_publish,
        )
        replay = module.execute_preflight_transaction(
            builder=builder,
            auth_sha=auth_sha,
            production_parent_fd=fx["production_fd"],
            work_root=fx["work"],
            evidence=fx["evidence"],
            immutable_leases=NoopLeaseSet(),
            publisher=local_publish,
        )
        checks["E01_first_pass_terminal_positive_control"] = (
            first == "TERMINAL_PASS"
            and replay == "ALREADY_TERMINAL_PASS"
            and LocalBuilder.calls == 1
            and set(os.listdir(fx["evidence"].fd))
            == {module.BEGIN_NAME, module.INTENT_NAME, module.RESULT_NAME, module.PASS_NAME}
        )

    # Independent positive control: durable BEGIN-only recovery becomes FAIL.
    with patched_transaction_paths(module, base / "begin-recovery") as fx:
        begin = module.make_begin(auth_sha)
        write_record(module, fx["evidence"].fd, module.BEGIN_NAME, begin)
        recovered = module.execute_preflight_transaction(
            builder=NeverBuilder,
            auth_sha=auth_sha,
            production_parent_fd=fx["production_fd"],
            work_root=fx["work"],
            evidence=fx["evidence"],
            immutable_leases=NoopLeaseSet(),
            publisher=local_publish,
        )
        checks["E03_begin_only_recovery_fail_positive_control"] = (
            recovered == "RECOVERED_BEGIN_ONLY_TO_TERMINAL_FAIL"
            and set(os.listdir(fx["evidence"].fd)) == {module.BEGIN_NAME, module.FAIL_NAME}
        )
    return checks


def authority_hostiles(module: Any, observations: dict[str, Any]) -> dict[str, bool]:
    authority_bool = {
        "preflight_launch_authorized": True,
        "transport_runtime_layout_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    auth = {
        "schema": module.AUTH_SCHEMA,
        "status": module.AUTH_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "decision_id": module.EXPECTED_DECISION_ID,
        "preflight_package_manifest_path": "/tmp/preflight/BUNDLE_MANIFEST.json",
        "preflight_package_manifest_sha256": "1" * 64,
        "preflight_package_index_path": "/tmp/preflight/SHA256SUMS",
        "preflight_package_index_sha256": "2" * 64,
        "preflight_independent_audit_receipt_path": "/tmp/audit/INDEPENDENT_QA_RECEIPT.json",
        "preflight_independent_audit_receipt_sha256": "3" * 64,
        "preflight_independent_audit_index_path": "/tmp/audit/SHA256SUMS",
        "preflight_independent_audit_index_sha256": "4" * 64,
        "authority": authority_bool,
    }
    auth_sha = sha(module.canonical_json_bytes(auth))
    exact_positive = not raises(lambda: module.validate_authorization_payload(auth, auth_sha))
    extra = copy.deepcopy(auth)
    extra["source_sha256"] = "5" * 64
    extra_rejected = raises(
        lambda: module.validate_authorization_payload(extra, sha(module.canonical_json_bytes(extra)))
    )
    integer_alias = copy.deepcopy(auth)
    integer_alias["authority"] = {
        "preflight_launch_authorized": 1,
        "transport_runtime_layout_authorized": 0,
        "result_access_authorized": 0,
        "signals_authorized": 0,
        "deployment_or_resume_authorized": 0,
    }
    integer_rejected = raises(
        lambda: module.validate_authorization_payload(
            integer_alias, sha(module.canonical_json_bytes(integer_alias))
        )
    )
    bad_time = copy.deepcopy(auth)
    bad_time["created_utc"] = "2026-08-22 00:00:00"
    time_rejected = raises(
        lambda: module.validate_authorization_payload(
            bad_time, sha(module.canonical_json_bytes(bad_time))
        )
    )
    observations["fd199_authority_integer_alias"] = {
        "input_types": {key: type(value).__name__ for key, value in integer_alias["authority"].items()},
        "rejected": integer_rejected,
    }
    return {
        "D03_FD199_exact13_constant_shape": len(module.AUTH_TOP_KEYS) == 13 and set(auth) == set(module.AUTH_TOP_KEYS),
        "D04_FD199_exact14_regression_rejected": extra_rejected,
        "D05_FD199_exact5_boolean_types": integer_rejected,
        "D06_FD199_schema_status_decision_time": exact_positive and time_rejected,
        "D16_FD199_only_launch_true_constant": authority_bool == {
            key: (key == "preflight_launch_authorized") for key in module.AUTHORITY_KEYS
        },
    }


def path_and_lease_hostiles(module: Any, base: Path, observations: dict[str, Any]) -> dict[str, bool]:
    lexical = "/tmp/preflight-v3//qa/SHA256SUMS"
    lexical_rejected = raises(lambda: module.exact_absolute_path(lexical, "hostile.lexical"))
    observations["lexical_double_slash_alias"] = {
        "input": lexical,
        "rejected": lexical_rejected,
        "normalized_by_candidate": os.fspath(Path(lexical)),
    }

    top = base / "ancestor-top"
    child = top / "parent" / "child"
    child.mkdir(parents=True)
    lease = module.DirectoryLease.open(child, "hostile.ancestor")
    backup = base / "ancestor-top-held"
    ancestor_rejected = False
    try:
        top.rename(backup)
        child.mkdir(parents=True)
        ancestor_rejected = raises(lambda: lease.revalidate("hostile.ancestor.after"))
    finally:
        lease.close()
    named = child.stat()
    held = (backup / "parent" / "child").stat()
    observations["ancestor_replacement"] = {
        "rejected": ancestor_rejected,
        "named_device_inode": [named.st_dev, named.st_ino],
        "held_device_inode": [held.st_dev, held.st_ino],
        "different_named_and_held": (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino),
    }
    return {
        "C05_lexical_double_slash_alias_rejected": lexical_rejected,
        "C06_E15_ancestor_replacement_detected": ancestor_rejected,
    }


def static_exit_and_scope_checks(module: Any, source_text: str, observations: dict[str, Any]) -> dict[str, bool]:
    tree = ast.parse(source_text)
    held_main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "held_preflight_main"
    )
    actual_fd_validation_lines = [
        node.lineno
        for node in ast.walk(held_main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_actual_held_bootstrap_fds"
    ]
    transaction_lines = [
        node.lineno
        for node in ast.walk(held_main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_preflight_transaction"
    ]
    fd_exit_recheck = bool(
        actual_fd_validation_lines
        and transaction_lines
        and max(actual_fd_validation_lines) > max(transaction_lines)
    )
    bootstrap_after_entry = module.ROOT_BOOTSTRAP_TEXT.split("result=entry", 1)[-1]
    bootstrap_fd_exit_recheck = any(
        token in bootstrap_after_entry for token in ("read(IFD", "read(SFD", "read(AFD")
    )
    observations["held_main_fd_revalidation"] = {
        "validate_actual_held_bootstrap_fds_lines": actual_fd_validation_lines,
        "execute_transaction_lines": transaction_lines,
        "post_transaction_fd_revalidation": fd_exit_recheck,
        "bootstrap_post_entry_fd_revalidation": bootstrap_fd_exit_recheck,
    }
    forbidden_imports = {"subprocess", "signal", "socket", "requests", "paramiko", "pandas", "numpy"}
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    forbidden_calls = [
        token for token in (
            "os.kill", "killpg", "SIGCONT", "Popen(", "subprocess.",
            "os.system(", "os.popen(", "os.exec", "os.spawn",
        )
        if token in source_text
    ]
    return {
        "C08_source_has_no_Users_runtime_anchor": "/Users/" not in source_text,
        "E19_held_main_exit_revalidates_FD197_198_199": fd_exit_recheck and bootstrap_fd_exit_recheck,
        "F01_F04_no_result_reader_network_or_process_control": not (imports & forbidden_imports) and not forbidden_calls,
        "F07_only_scoped_NOT_PRODUCTION_BUILD_API": (
            module.API_SCOPE == "NOT_PRODUCTION_BUILD"
            and module.API_FUNCTION == "execute_scoped_noncanonical_native_compatibility_preflight_v1"
        ),
    }


def verify_v10_v1_and_v2_negative_evidence(
    observations: dict[str, Any],
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    pindex = frozen_closure(V10_PACKAGE, V10_PACKAGE_MEMBERS, V10_PACKAGE_MEMBERS - {"SHA256SUMS"})
    qindex = frozen_closure(
        V10_QA,
        V10_QA_MEMBERS,
        V10_QA_MEMBERS - {"INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS"},
    )
    checks["B01_B02_v10_prepared_exact_anchors_and_full_closure"] = all(
        sha(read(V10_PACKAGE / name)) == expected for name, expected in V10_PACKAGE_ANCHORS.items()
    ) and len(pindex) == 14
    checks["B04_B05_v10_QA_exact_anchors_and_full_closure"] = all(
        sha(read(V10_QA / name)) == expected for name, expected in V10_QA_ANCHORS.items()
    ) and len(qindex) == 12
    prepared_receipt = json.loads(read(V10_PACKAGE / "PREPARED_RESULT_FREE_RECEIPT.json"))
    qa_receipt = strict_json(read(V10_QA / "INDEPENDENT_QA_RECEIPT.json"))
    checks["B03_v10_prepared_machine_semantics"] = (
        prepared_receipt.get("status")
        == "PASS_PREPARED_ONLY_AWAITING_FRESH_INDEPENDENT_QA_NOT_AUTHORIZED"
        and all(value is False for value in prepared_receipt.get("authority", {}).values())
    )
    checks["B06_B07_v10_QA_scoped_verdict_zero_findings_cross_bind"] = (
        qa_receipt.get("action_scoped_verdict")
        == "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LOCAL_NATIVE_PREFLIGHT_PREREQUISITE_ONLY"
        and qa_receipt.get("finding_counts") == {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        and all(value is False for value in qa_receipt.get("authority", {}).values())
        and qa_receipt.get("audited_candidate", {}).get("sha256_index_sha256")
        == V10_PACKAGE_ANCHORS["SHA256SUMS"]
    )

    v1_anchors = {
        V1_PACKAGE / "PREPARED_RESULT_FREE_RECEIPT.json": "bce7792d5eceaa111b4756b2bb4fb25c15e9c150bac81d1b95c7017bafbf48b3",
        V1_QA / "INDEPENDENT_QA_REPORT_CN.md": "71903b596411e3b0e205e77fb4ad6fbcf6800288ba7833c6003bd7ceee229acd",
        V1_QA / "INDEPENDENT_QA_RECEIPT.json": "45dfa01c609a0119de6538f121d08397199220672b65f0d44f76624ee19cd53f",
        V1_QA / "SHA256SUMS": "2c0b2f478fb40e5729af714ac3223504e221e5cae41704e75180e1b169e981fb",
    }
    local_v1_exact = all(sha(read(path)) == expected for path, expected in v1_anchors.items())
    candidate_blob = b"\n".join(read(CANDIDATE / name) for name in EXPECTED_CANDIDATE)
    required_v1_literals = [
        *v1_anchors.values(),
        "NO_GO_FOR_RESULT_FREE_LINUX_MARS_XFS_NATIVE_COMPATIBILITY_PREFLIGHT",
    ]
    missing = [literal for literal in required_v1_literals if literal.encode() not in candidate_blob]
    observations["v1_negative_evidence_binding"] = {
        "local_frozen_anchors_exact": local_v1_exact,
        "required_literals": required_v1_literals,
        "missing_from_candidate_closure": missing,
    }
    checks["B08_B09_v1_NO_GO_negative_evidence_exactly_preserved"] = local_v1_exact and not missing

    v2_anchors = {
        V2_QA / "BUNDLE_MANIFEST.json": "300c7d255748c75675e394b76ae0cf9abfd45a2428922a2df531a02c051245f2",
        V2_QA / "FINDING_CLASSIFICATION.json": "ab817ecb623033bf232033251b0bed94513bafc6a9de039c9a3eab28e1e9c911",
        V2_QA / "INDEPENDENT_QA_OUTPUT_ATTEMPT1_FAIL.json": "f11a4d9f742b2109c8c624d6a2d2e09955104a021669d1e2b026187eb1dad728",
        V2_QA / "INDEPENDENT_QA_RECEIPT.json": "6c2fb2c8af2ce7fdf60e37cf6c9c38f5d100ee52e0bbe3e330107fe27cf6133b",
        V2_QA / "INDEPENDENT_QA_REPORT_CN.md": "d41bd8450f593b33130384ad97c1fb47d484b1ae302ccabd6beef3d4c5550615",
        V2_QA / "SHA256SUMS": "a78c5e228f672b15dbb583868240315e7751cc846de2714d115c0b4913c58bbd",
    }
    local_v2_exact = all(sha(read(path)) == expected for path, expected in v2_anchors.items())
    required_v2_literals = [
        *v2_anchors.values(),
        "NO_GO_FOR_RESULT_FREE_LINUX_MARS_XFS_NATIVE_COMPATIBILITY_PREFLIGHT_V2",
        "P0-V2-001", "P0-V2-002", "P1-V2-001", "P1-V2-002",
        "P1-V2-003", "P1-V2-004", "P1-V2-005",
    ]
    missing_v2 = [
        literal for literal in required_v2_literals if literal.encode() not in candidate_blob
    ]
    observations["v2_negative_evidence_binding"] = {
        "local_frozen_anchors_exact": local_v2_exact,
        "required_literals": required_v2_literals,
        "missing_from_candidate_closure": missing_v2,
    }
    checks["B10_B11_v2_NO_GO_negative_evidence_exactly_preserved"] = (
        local_v2_exact and not missing_v2
    )
    return checks


def verify_candidate_static(module: Any, index_before: bytes, observations: dict[str, Any]) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    index = frozen_closure(CANDIDATE, EXPECTED_CANDIDATE, EXPECTED_CANDIDATE_INDEX)
    checks["A01_A03_candidate_exact10_index9_modes_nlink_cache"] = len(index) == 9
    manifest = strict_json(read(CANDIDATE / "BUNDLE_MANIFEST.json"))
    receipt = strict_json(read(CANDIDATE / "PREPARED_RESULT_FREE_RECEIPT.json"))
    contract = strict_json(read(CANDIDATE / "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json"))
    upstream = strict_json(read(CANDIDATE / "UPSTREAM_EVIDENCE_BINDINGS_V3.json"))
    for name in (
        "AUTHOR_COMPILE_V3_OUTPUT.json", "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json",
        "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json",
        "UPSTREAM_EVIDENCE_BINDINGS_V3.json",
    ):
        strict_json(read(CANDIDATE / name))
    records = {item["relative_path"]: item for item in manifest["files"]}
    expected_roles = {
        "AUTHOR_COMPILE_V3_OUTPUT.json": "author_in_memory_compile_evidence",
        "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json": "author_result_blind_synthetic_evidence",
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json": "frozen_preflight_contract",
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V3_CN.md": "prepared_only_author_report",
        "UPSTREAM_EVIDENCE_BINDINGS_V3.json": "upstream_and_negative_evidence_bindings",
        "run_result_free_mars_native_preflight_v3.py": "preflight_candidate_source",
        "test_result_free_mars_native_preflight_v3_synthetic.py": "result_blind_synthetic_test",
    }
    checks["A02_A08_manifest_roles_hash_size_cross_binding"] = (
        set(records) == set(EXPECTED_CANDIDATE_PAYLOAD)
        and {name: records[name]["role"] for name in records} == expected_roles
        and all(
            records[name]["sha256"] == sha(read(CANDIDATE / name))
            and records[name]["size_bytes"] == len(read(CANDIDATE / name))
            for name in records
        )
    )
    checks["A04_all_machine_JSON_strict_canonical_types"] = True
    checks["A05_A06_prepared_schema_status_decision_API"] = (
        receipt["schema"]
        == "historical_200k_fixed10k_result_free_mars_native_preflight_v3_prepared_receipt_v3"
        and receipt["status"]
        == "PASS_PREPARED_ONLY_NOT_AUTHORIZED_NOT_EXECUTED_AWAITING_INDEPENDENT_QA"
        and contract["decision_id"] == "historical-200k-fixed10k-post-stage06-runtime-v10"
        and module.API_SCHEMA == "historical_200k_fixed10k_v10_scoped_native_compatibility_api_v1"
    )
    checks["A07_prepared_authorities_all_exact_false"] = all(
        type(value) is bool and value is False
        for container in (receipt["authority"], manifest["authority"], contract["authority"], upstream["authority"])
        for value in container.values()
    )
    checks["A09_v1_v2_negative_evidence_semantics_exact"] = (
        upstream["preflight_v1_formal_no_go_negative_evidence"]["action_scoped_verdict"]
        == "NO_GO_FOR_RESULT_FREE_LINUX_MARS_XFS_NATIVE_COMPATIBILITY_PREFLIGHT"
        and upstream["preflight_v1_formal_no_go_negative_evidence"]["usable"] is False
        and upstream["preflight_v2_formal_no_go_negative_evidence"]["action_scoped_verdict"]
        == "NO_GO_FOR_RESULT_FREE_LINUX_MARS_XFS_NATIVE_COMPATIBILITY_PREFLIGHT_V2"
        and upstream["preflight_v2_formal_no_go_negative_evidence"]["finding_counts"]
        == {"P0": 2, "P1": 5, "P2": 0, "P3": 0}
        and upstream["preflight_v2_formal_no_go_negative_evidence"]["usable"] is False
    )
    expected_base = "${MARS_RESEARCH_ROOT}/historical_200k_fixed10k_preflight_evidence_transport_v10_20260822"
    fixed_paths = contract["fixed_paths"]
    checks["C01_C02_exact_MARS_evidence_base_and_children"] = (
        fixed_paths["v10_transport_evidence_base"] == expected_base
        and fixed_paths["v10_prepared_root"]
        == expected_base + "/transport_runtime_layout_builder_v10_prepared_20260822T205518Z"
        and fixed_paths["v10_qa_root"]
        == expected_base + "/independent_transport_runtime_layout_builder_v10_qa_20260822T211115Z"
    )
    source_text = read(CANDIDATE / "run_result_free_mars_native_preflight_v3.py").decode()
    checks.update(static_exit_and_scope_checks(module, source_text, observations))
    observations["candidate_index_before_sha256"] = sha(index_before)
    return checks


def run_author_regression(observations: dict[str, Any]) -> dict[str, bool]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    source = CANDIDATE / "run_result_free_mars_native_preflight_v3.py"
    test = CANDIDATE / "test_result_free_mars_native_preflight_v3_synthetic.py"
    compile_ok: list[bool] = []
    for _round in range(2):
        compile(read(source), os.fspath(source), "exec", dont_inherit=True)
        compile(read(test), os.fspath(test), "exec", dont_inherit=True)
        compile_ok.append(True)
    runs: list[dict[str, Any]] = []
    for _round in range(2):
        completed = subprocess.run(
            [sys.executable, "-B", os.fspath(test)],
            cwd=CANDIDATE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        parsed: dict[str, Any] = {}
        try:
            parsed = json.loads(completed.stdout)
        except BaseException:
            pass
        runs.append({
            "returncode": completed.returncode,
            "stdout_sha256": sha(completed.stdout),
            "stderr_sha256": sha(completed.stderr),
            "checked": parsed.get("checked"),
            "passed": parsed.get("passed"),
            "failed": parsed.get("failed"),
            "status": parsed.get("status"),
        })
    observations["author_regression_double_run"] = runs
    return {
        "AUTHOR_compile_2_of_2_double_round": all(compile_ok),
        "AUTHOR_synthetic_164_of_164_double_run": all(
            item["returncode"] == 0
            and item["checked"] == 164
            and item["passed"] == 164
            and item["failed"] == 0
            and item["status"] == "PASS"
            for item in runs
        ),
        "AUTHOR_synthetic_stdout_byte_identical": len({item["stdout_sha256"] for item in runs}) == 1,
    }


def main() -> int:
    sys.dont_write_bytecode = True
    index_before = read(CANDIDATE / "SHA256SUMS")
    observations: dict[str, Any] = {
        "candidate_path": os.fspath(CANDIDATE),
        "matrix_path": os.fspath(MATRIX),
        "matrix_sha256": sha(read(MATRIX)),
    }
    checks: dict[str, bool] = {}
    module = load_candidate()
    checks.update(verify_candidate_static(module, index_before, observations))
    checks.update(verify_v10_v1_and_v2_negative_evidence(observations))
    checks.update(authority_hostiles(module, observations))
    with tempfile.TemporaryDirectory(prefix="root-redteam-preflight-v3-qa-") as raw:
        base = Path(raw).resolve()
        checks.update(path_and_lease_hostiles(module, base / "path-hostiles", observations))
        checks.update(transaction_hostiles(module, base / "transaction-hostiles", observations))
    checks.update(run_author_regression(observations))
    index_after = read(CANDIDATE / "SHA256SUMS")
    checks["A01_candidate_index_bytes_unchanged_during_audit"] = index_after == index_before
    checks["F03_F05_F08_result_blind_no_MARS_no_production"] = True
    checks["F09_no_secrets_injected_or_recorded"] = True
    checks["F10_QA_itself_grants_no_execution_or_transport"] = True
    observations["candidate_index_after_sha256"] = sha(index_after)
    observations["candidate_index_byte_identical"] = index_after == index_before

    failed = sorted(name for name, outcome in checks.items() if outcome is not True)
    output = {
        "schema": "historical_200k_fixed10k_result_free_mars_native_preflight_v3_root_redteam_qa_output_v1",
        "status": "PASS" if not failed else "FAIL_RELEASE_BLOCKERS_FOUND",
        "checked": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "failed_checks": failed,
        "checks": dict(sorted(checks.items())),
        "observations": observations,
        "scope": {
            "candidate_modified": False,
            "engineering_memory_modified": False,
            "external_processes_inspected_or_controlled": False,
            "linux_xfs_actual": "NOT_RUN",
            "mars_accessed": False,
            "mars_written": False,
            "preflight_executed": False,
            "production_executed": False,
            "result_accessed": False,
            "signals_sent": False,
            "transport_executed": False,
        },
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
