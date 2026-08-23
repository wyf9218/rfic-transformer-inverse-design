#!/usr/bin/env python3
"""Result-blind verifier for the frozen Stage07/08 preflight/transport meta-candidate.

This verifier only reads a local candidate directory.  It contains no network,
process-discovery, signal, controller, Stage07/08, or EMX-result code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any


MANIFEST_NAME = "PACKAGE_MANIFEST.json"
RECEIPT_NAME = "PREPARED_RECEIPT.json"
INDEX_NAME = "SHA256SUMS.txt"
CLOSURE_NAMES = frozenset({MANIFEST_NAME, RECEIPT_NAME, INDEX_NAME})
STATUS = "AWAITING_FRESH_INDEPENDENT_QA_NOT_AUTHORIZED"
CONTRACT_SCHEMA = (
    "historical_200k_fixed10k_stage07_08_result_free_preflight_transport_meta_contract_v1"
)
MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_stage07_08_result_free_preflight_transport_manifest_v1"
)
RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_stage07_08_result_free_preflight_transport_prepared_receipt_v1"
)
AUTHORITY_KEYS = frozenset({
    "controller_or_resume_authorized",
    "deployment_authorized",
    "external_process_inspection_or_control_authorized",
    "mars_access_authorized",
    "mars_write_authorized",
    "native_preflight_execution_authorized",
    "production_root_or_journal_write_authorized",
    "result_access_authorized",
    "signals_authorized",
    "transport_build_or_smoke_authorized",
})
PAYLOAD_RECORD_KEYS = frozenset({
    "relative_path",
    "role",
    "source_group",
    "sha256",
    "size_bytes",
    "mode_octal",
    "nlink",
    "freeze_st_dev",
    "freeze_st_ino",
})
SNAPSHOT_KEYS = frozenset({
    "host",
    "watcher_present",
    "watcher_pid",
    "watcher_ppid",
    "watcher_state",
    "full_cmdline_sha256",
    "boot_id",
    "proc_start_ticks",
    "uid",
    "exe_path",
    "exe_sha256",
    "script_path",
    "script_sha256",
    "launch_receipt_path",
    "launch_receipt_sha256",
    "direct_children",
    "matching_watcher_process_count",
    "active_post_stage06_chain_processes",
    "stage07_output_exists",
    "stage08_output_exists",
    "production_runtime_root_identity_changed",
    "transport_receipt_bound",
    "native_preflight_terminal_pass_bound",
    "fresh_independent_candidate_go_bound",
    "separate_sigcont_authorization_bound",
    "pidfd_or_equivalent_identity_bound",
})


class VerificationError(RuntimeError):
    pass


def _no_duplicate_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if type(key) is not str or key in result:
            raise VerificationError(f"duplicate/non-string JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(text: str) -> None:
    raise VerificationError(f"non-finite JSON constant rejected: {text}")


def strict_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label}: invalid strict JSON") from exc
    if type(value) is not dict:
        raise VerificationError(f"{label}: exact JSON object required")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise VerificationError(f"{label}: exact boolean {expected} required")


def exact_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise VerificationError(f"{label}: exact nonnegative integer required")
    return value


def exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise VerificationError(f"{label}: exact nonempty string required")
    return value


def exact_sha(value: Any, label: str) -> str:
    text = exact_string(value, label)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise VerificationError(f"{label}: lowercase SHA-256 required")
    return text


def safe_relative(value: Any, label: str) -> str:
    raw = exact_string(value, label)
    path = PurePosixPath(raw)
    if path.is_absolute() or raw != path.as_posix() or not path.parts:
        raise VerificationError(f"{label}: canonical relative path required")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationError(f"{label}: unsafe path component")
    return raw


def all_false_authority(value: Any, label: str) -> None:
    if type(value) is not dict or set(value) != AUTHORITY_KEYS:
        raise VerificationError(f"{label}: exact authority keyset required")
    for key in AUTHORITY_KEYS:
        exact_bool(value[key], False, f"{label}.{key}")


def _pread_all(fd: int, size: int, label: str) -> bytes:
    if size > 64 * 1024 * 1024:
        raise VerificationError(f"{label}: file exceeds result-free verifier limit")
    parts: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not chunk:
            raise VerificationError(f"{label}: unexpected EOF")
        parts.append(chunk)
        offset += len(chunk)
    return b"".join(parts)


def _open_root(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.fspath(path), flags)
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise VerificationError("candidate root is not a directory")
    return fd


def _open_held_file(root_fd: int, relative_path: str) -> tuple[int, bytes, os.stat_result]:
    parts = PurePosixPath(relative_path).parts
    parent_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
        leaf = parts[-1]
        fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(fd)
        named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(named.st_mode):
            os.close(fd)
            raise VerificationError(f"{relative_path}: regular file required")
        if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
            os.close(fd)
            raise VerificationError(f"{relative_path}: named/held identity mismatch")
        if before.st_nlink != 1:
            os.close(fd)
            raise VerificationError(f"{relative_path}: nlink must equal one")
        raw = _pread_all(fd, before.st_size, relative_path)
        after = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_nlink,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_nlink,
        ):
            os.close(fd)
            raise VerificationError(f"{relative_path}: held identity changed while read")
        return fd, raw, before
    finally:
        os.close(parent_fd)


def inventory_tree(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        if relative_dir.parts:
            directories.add(relative_dir.as_posix())
        for name in list(dirnames):
            path = current_path / name
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise VerificationError(f"non-directory/symlink enrolled as directory: {path}")
        for name in filenames:
            path = current_path / name
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise VerificationError(f"non-regular/symlink enrolled as file: {path}")
            files.add(path.relative_to(root).as_posix())
    return files, directories


def parse_index(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("index is not UTF-8") from exc
    result: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise VerificationError("index line is not canonical")
        digest = exact_sha(line[:64], "index.sha256")
        name = safe_relative(line[66:], "index.path")
        if name in result:
            raise VerificationError("index contains duplicate path")
        result[name] = digest
        order.append(name)
    if order != sorted(order):
        raise VerificationError("index paths are not sorted")
    return result


def load_contract(path: Path) -> dict[str, Any]:
    contract = strict_json_bytes(path.read_bytes(), "contract")
    if contract.get("schema") != CONTRACT_SCHEMA or contract.get("status") != STATUS:
        raise VerificationError("contract schema/status mismatch")
    all_false_authority(contract.get("authority"), "contract.authority")
    behavior = contract.get("candidate_behavior")
    if type(behavior) is not dict or not behavior:
        raise VerificationError("candidate_behavior exact object required")
    for key, value in behavior.items():
        if type(key) is not str:
            raise VerificationError("candidate_behavior key must be string")
        exact_bool(value, False, f"candidate_behavior.{key}")
    boundary = contract.get("fresh_qa_boundary")
    if type(boundary) is not dict:
        raise VerificationError("fresh_qa_boundary object required")
    for key in (
        "fresh_auditor_go_authorizes_sigcont",
        "fresh_auditor_go_authorizes_stage07_or_stage08",
        "fresh_auditor_go_authorizes_transport_or_native_preflight_automatically",
    ):
        exact_bool(boundary.get(key), False, f"fresh_qa_boundary.{key}")
    exact_bool(
        boundary.get("requires_separate_post_preflight_resume_authorization"),
        True,
        "fresh_qa_boundary.requires_separate_post_preflight_resume_authorization",
    )
    upstream = contract.get("upstream_exact_bindings")
    if type(upstream) is not dict or set(upstream) != {
        "preflight_v3_prepared",
        "preflight_v3_root_redteam_supporting",
        "transport_v10_independent_qa",
        "transport_v10_prepared",
    }:
        raise VerificationError("contract upstream group closure mismatch")
    qa = upstream["transport_v10_independent_qa"]
    if qa.get("action_scoped_verdict") != (
        "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LOCAL_NATIVE_PREFLIGHT_PREREQUISITE_ONLY"
    ):
        raise VerificationError("v10 independent QA scoped verdict mismatch")
    counts = qa.get("finding_counts")
    if type(counts) is not dict or set(counts) != {"P0", "P1", "P2", "P3"}:
        raise VerificationError("v10 independent QA finding keyset mismatch")
    for key in counts:
        if exact_int(counts[key], f"v10_qa.finding_counts.{key}") != 0:
            raise VerificationError("v10 independent QA must have zero findings")
    redteam = upstream["preflight_v3_root_redteam_supporting"]
    exact_bool(redteam.get("independent"), False, "root_redteam.independent")
    exact_bool(redteam.get("supporting_only"), True, "root_redteam.supporting_only")
    return contract


def evaluate_future_watcher_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Evaluate synthetic/future evidence only; this function never discovers processes."""
    if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_KEYS:
        raise VerificationError("future watcher snapshot exact keyset mismatch")
    if snapshot["host"] != "${MARS_HOST}":
        raise VerificationError("future watcher host mismatch")
    exact_bool(snapshot["watcher_present"], True, "watcher_present")
    if exact_int(snapshot["watcher_pid"], "watcher_pid") != 2901805:
        raise VerificationError("watcher PID identity drift")
    if exact_int(snapshot["watcher_ppid"], "watcher_ppid") != 1:
        raise VerificationError("watcher PPID identity drift")
    if snapshot["watcher_state"] != "T":
        raise VerificationError("watcher is not stopped")
    if exact_sha(snapshot["full_cmdline_sha256"], "full_cmdline_sha256") != (
        "1b042949118aae7d3bc66e56a36a09a9f50cc14fedf66ad3ebdc6c4e4a53f83d"
    ):
        raise VerificationError("watcher cmdline identity drift")
    for key in (
        "boot_id",
        "exe_path",
        "script_path",
        "launch_receipt_path",
    ):
        exact_string(snapshot[key], key)
    for key in ("exe_sha256", "script_sha256", "launch_receipt_sha256"):
        exact_sha(snapshot[key], key)
    for key in ("proc_start_ticks", "uid", "direct_children"):
        exact_int(snapshot[key], key)
    if snapshot["direct_children"] != 0:
        raise VerificationError("stopped watcher has children")
    if exact_int(snapshot["matching_watcher_process_count"], "matching_watcher_process_count") != 1:
        raise VerificationError("watcher absent or duplicate watcher signature")
    active = snapshot["active_post_stage06_chain_processes"]
    if type(active) is not list or active:
        raise VerificationError("duplicate/active post-Stage06 process detected")
    for key in (
        "stage07_output_exists",
        "stage08_output_exists",
        "production_runtime_root_identity_changed",
    ):
        exact_bool(snapshot[key], False, key)
    for key in (
        "transport_receipt_bound",
        "native_preflight_terminal_pass_bound",
        "fresh_independent_candidate_go_bound",
        "separate_sigcont_authorization_bound",
        "pidfd_or_equivalent_identity_bound",
    ):
        exact_bool(snapshot[key], True, key)
    return {
        "decision": "ELIGIBLE_FOR_EXACTLY_ONE_SEPARATELY_AUTHORIZED_SIGCONT_ONLY",
        "launch_replacement_allowed": False,
        "target_pid": 2901805,
    }


def verify_candidate(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    root_fd = _open_root(root)
    held: list[int] = []
    try:
        root_info = os.fstat(root_fd)
        if stat.S_IMODE(root_info.st_mode) != 0o555:
            raise VerificationError("candidate root mode must be 0555")
        files, directories = inventory_tree(root)
        manifest_fd, manifest_raw, manifest_info = _open_held_file(root_fd, MANIFEST_NAME)
        held.append(manifest_fd)
        receipt_fd, receipt_raw, _ = _open_held_file(root_fd, RECEIPT_NAME)
        held.append(receipt_fd)
        index_fd, index_raw, _ = _open_held_file(root_fd, INDEX_NAME)
        held.append(index_fd)
        manifest = strict_json_bytes(manifest_raw, "manifest")
        receipt = strict_json_bytes(receipt_raw, "receipt")
        if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("status") != STATUS:
            raise VerificationError("manifest schema/status mismatch")
        all_false_authority(manifest.get("authority"), "manifest.authority")
        if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != STATUS:
            raise VerificationError("receipt schema/status mismatch")
        all_false_authority(receipt.get("authority"), "receipt.authority")
        if receipt.get("package_directory") != root.name:
            raise VerificationError("receipt package directory mismatch")
        if receipt.get("manifest_sha256") != sha256_bytes(manifest_raw):
            raise VerificationError("receipt does not bind manifest bytes")
        if receipt.get("freeze_root_st_dev") != root_info.st_dev:
            raise VerificationError("receipt root device mismatch")
        if receipt.get("freeze_root_st_ino") != root_info.st_ino:
            raise VerificationError("receipt root inode mismatch")
        records = manifest.get("files")
        if type(records) is not list:
            raise VerificationError("manifest files list required")
        expected_paths: set[str] = set()
        roles: set[str] = set()
        identities: set[tuple[str, str]] = set()
        for index, raw_record in enumerate(records):
            if type(raw_record) is not dict or set(raw_record) != PAYLOAD_RECORD_KEYS:
                raise VerificationError(f"manifest.files[{index}] exact record mismatch")
            path = safe_relative(raw_record["relative_path"], f"manifest.files[{index}].path")
            role = exact_string(raw_record["role"], f"manifest.files[{index}].role")
            source_group = exact_string(
                raw_record["source_group"], f"manifest.files[{index}].source_group"
            )
            del source_group
            digest = exact_sha(raw_record["sha256"], f"manifest.files[{index}].sha256")
            if path in expected_paths or role in roles or (path, digest) in identities:
                raise VerificationError("duplicate path, role, or artifact identity")
            expected_paths.add(path)
            roles.add(role)
            identities.add((path, digest))
            fd, raw, info = _open_held_file(root_fd, path)
            held.append(fd)
            if stat.S_IMODE(info.st_mode) != 0o444:
                raise VerificationError(f"{path}: mode must be 0444")
            if raw_record["mode_octal"] != "0444":
                raise VerificationError(f"{path}: manifest mode mismatch")
            if exact_int(raw_record["nlink"], f"{path}.nlink") != 1:
                raise VerificationError(f"{path}: manifest nlink mismatch")
            if exact_int(raw_record["size_bytes"], f"{path}.size") != len(raw):
                raise VerificationError(f"{path}: size mismatch")
            if exact_int(raw_record["freeze_st_dev"], f"{path}.st_dev") != info.st_dev:
                raise VerificationError(f"{path}: freeze device mismatch")
            if exact_int(raw_record["freeze_st_ino"], f"{path}.st_ino") != info.st_ino:
                raise VerificationError(f"{path}: freeze inode mismatch")
            if digest != sha256_bytes(raw):
                raise VerificationError(f"{path}: SHA mismatch")
        expected_dirs = manifest.get("directories")
        if type(expected_dirs) is not list or any(type(item) is not str for item in expected_dirs):
            raise VerificationError("manifest directories list required")
        if expected_dirs != sorted(set(expected_dirs)) or set(expected_dirs) != directories:
            raise VerificationError("unregistered or missing directory")
        for relative_dir in directories:
            info = os.lstat(root / relative_dir)
            if stat.S_IMODE(info.st_mode) != 0o555:
                raise VerificationError(f"{relative_dir}: directory mode must be 0555")
        if files != expected_paths | CLOSURE_NAMES:
            raise VerificationError("unregistered or missing regular file")
        if manifest.get("payload_file_count") != len(records):
            raise VerificationError("manifest payload count mismatch")
        index = parse_index(index_raw)
        expected_index_paths = files - {INDEX_NAME}
        if set(index) != expected_index_paths:
            raise VerificationError("index exact closure mismatch")
        for path, digest in index.items():
            fd, raw, _ = _open_held_file(root_fd, path)
            held.append(fd)
            if sha256_bytes(raw) != digest:
                raise VerificationError(f"index SHA mismatch: {path}")
        if receipt.get("payload_file_count") != len(records):
            raise VerificationError("receipt payload count mismatch")
        if receipt.get("total_regular_file_count") != len(files):
            raise VerificationError("receipt total file count mismatch")
        if receipt.get("sha256_index_listed_count") != len(index):
            raise VerificationError("receipt index count mismatch")
        contract = load_contract(root / "MARS_STAGE07_08_RESUME_ONLY_CONTRACT.json")
        del contract
        # Final same-FD and named-path continuity check after every parse/hash gate.
        for fd in held:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise VerificationError("held artifact identity changed before final return")
        return {
            "status": "PASS_LOCAL_RESULT_BLIND_FROZEN_CANDIDATE_VERIFICATION",
            "payload_file_count": len(records),
            "total_regular_file_count": len(files),
            "directory_count": len(directories),
            "unique_role_count": len(roles),
            "unique_artifact_identity_count": len(identities),
            "sha256_index_listed_count": len(index),
            "manifest_sha256": sha256_bytes(manifest_raw),
            "sha256sums_sha256": sha256_bytes(index_raw),
            "authority_all_false": True,
            "mars_accessed": False,
            "results_accessed": False,
            "signals_sent": False,
        }
    finally:
        for fd in held:
            try:
                os.close(fd)
            except OSError:
                pass
        os.close(root_fd)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--snapshot-json", type=Path)
    args = parser.parse_args()
    if args.snapshot_json is not None:
        snapshot = strict_json_bytes(args.snapshot_json.read_bytes(), "snapshot")
        result = evaluate_future_watcher_snapshot(snapshot)
    else:
        result = verify_candidate(args.candidate)
    print(canonical_json_bytes(result).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
