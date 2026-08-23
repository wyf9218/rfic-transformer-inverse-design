#!/usr/bin/env python3
"""Freeze the complete local meta-candidate with held-FD byte continuity.

This is a local packaging helper.  It does not access MARS, execute transport or
native preflight, inspect external processes, send signals, or read EMX results.
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
STATUS = "AWAITING_FRESH_INDEPENDENT_QA_NOT_AUTHORIZED"
AUTHORITY = {
    "controller_or_resume_authorized": False,
    "deployment_authorized": False,
    "external_process_inspection_or_control_authorized": False,
    "mars_access_authorized": False,
    "mars_write_authorized": False,
    "native_preflight_execution_authorized": False,
    "production_root_or_journal_write_authorized": False,
    "result_access_authorized": False,
    "signals_authorized": False,
    "transport_build_or_smoke_authorized": False,
}
CORE_ROLES = {
    "AUTHOR_TARGETED_CHECKS.json": "preserved_first_targeted_check_topology_failure_evidence",
    "AUTHOR_TARGETED_CHECKS_REPLAY_V2.json": "author_targeted_result_blind_test_evidence",
    "BUILD_EVENTS.json": "preserved_builder_and_test_events",
    "MARS_STAGE07_08_RESUME_ONLY_CONTRACT.json": "future_resume_only_fail_closed_contract",
    "PREPARED_REPORT_CN.md": "prepared_candidate_scope_and_boundary_report",
    "freeze_prepared_candidate.py": "held_fd_freeze_builder_source",
    "run_author_targeted_checks.py": "targeted_local_check_runner_source",
    "test_result_free_preflight_transport_candidate.py": "meta_candidate_hostile_test_source",
    "verify_result_free_preflight_transport_candidate.py": "frozen_candidate_read_only_verifier_source",
}


class FreezeError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_fd(fd: int, size: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(size - offset, 1024 * 1024), offset)
        if not chunk:
            raise FreezeError("unexpected EOF while holding payload")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def safe_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() != relative:
        raise FreezeError(f"unsafe relative path: {relative}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise FreezeError(f"unsafe relative path component: {relative}")
    return path.parts


def open_held(root_fd: int, relative: str) -> tuple[int, bytes, os.stat_result]:
    parts = safe_parts(relative)
    parent_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            child_fd = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = child_fd
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
            raise FreezeError(f"regular file required: {relative}")
        if before.st_nlink != 1 or named.st_nlink != 1:
            os.close(fd)
            raise FreezeError(f"nlink one required: {relative}")
        if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
            os.close(fd)
            raise FreezeError(f"named/held mismatch: {relative}")
        raw = read_fd(fd, before.st_size)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            os.close(fd)
            raise FreezeError(f"identity changed while held: {relative}")
        return fd, raw, before
    finally:
        os.close(parent_fd)


def write_exclusive(root_fd: int, name: str, raw: bytes) -> int:
    fd = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o444,
        dir_fd=root_fd,
    )
    offset = 0
    try:
        while offset < len(raw):
            offset += os.write(fd, raw[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
        os.lseek(fd, 0, os.SEEK_SET)
        if read_fd(fd, len(raw)) != raw:
            raise FreezeError(f"exclusive publication byte mismatch: {name}")
        return fd
    except Exception:
        os.close(fd)
        raise


def inventory(root: Path) -> tuple[list[str], list[str]]:
    files: list[str] = []
    dirs: list[str] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        if rel_dir.parts:
            dirs.append(rel_dir.as_posix())
        for name in dirnames:
            info = os.lstat(current_path / name)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FreezeError(f"symlink/non-directory rejected: {current_path / name}")
        for name in filenames:
            info = os.lstat(current_path / name)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise FreezeError(f"symlink/non-file rejected: {current_path / name}")
            if info.st_nlink != 1:
                raise FreezeError(f"hardlink rejected: {current_path / name}")
            files.append((current_path / name).relative_to(root).as_posix())
    return sorted(files), sorted(dirs)


def role_for(path: str) -> tuple[str, str]:
    if path in CORE_ROLES:
        return CORE_ROLES[path], "meta_candidate_core"
    parts = PurePosixPath(path).parts
    if len(parts) != 3 or parts[0] != "upstream":
        raise FreezeError(f"unregistered payload enrollment: {path}")
    group, filename = parts[1], parts[2]
    if group not in {
        "preflight_v3_prepared",
        "preflight_v3_root_redteam_supporting",
        "transport_v10_independent_qa",
        "transport_v10_prepared",
    }:
        raise FreezeError(f"unregistered upstream group: {path}")
    normalized = "".join(ch if ch.isalnum() else "_" for ch in filename).strip("_").lower()
    return f"upstream__{group}__{normalized}", group


def chmod_directory(path: Path) -> None:
    fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fchmod(fd, 0o555)
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--final-package-name", required=True)
    parser.add_argument("--created-utc", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    for name in (MANIFEST_NAME, RECEIPT_NAME, INDEX_NAME):
        if (root / name).exists() or (root / name).is_symlink():
            raise FreezeError(f"no-clobber closure path already exists: {name}")
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    held: dict[str, tuple[int, bytes, os.stat_result]] = {}
    closure_fds: list[int] = []
    try:
        root_info = os.fstat(root_fd)
        payload_paths, dirs = inventory(root)
        if set(payload_paths) != set(CORE_ROLES) | {
            path for path in payload_paths if path.startswith("upstream/")
        }:
            raise FreezeError("core or upstream payload closure mismatch")
        records: list[dict[str, Any]] = []
        roles: set[str] = set()
        identities: set[tuple[str, str]] = set()
        for path in payload_paths:
            fd, raw, info = open_held(root_fd, path)
            held[path] = (fd, raw, info)
            role, group = role_for(path)
            identity = (path, sha(raw))
            if role in roles or identity in identities:
                raise FreezeError("duplicate role or artifact identity")
            roles.add(role)
            identities.add(identity)
            records.append(
                {
                    "relative_path": path,
                    "role": role,
                    "source_group": group,
                    "sha256": identity[1],
                    "size_bytes": len(raw),
                    "mode_octal": "0444",
                    "nlink": 1,
                    "freeze_st_dev": info.st_dev,
                    "freeze_st_ino": info.st_ino,
                }
            )
        manifest = {
            "schema": (
                "historical_200k_fixed10k_stage07_08_result_free_preflight_transport_manifest_v1"
            ),
            "status": STATUS,
            "created_utc": args.created_utc,
            "authority": AUTHORITY,
            "files": records,
            "directories": dirs,
            "payload_file_count": len(records),
            "closure_files_not_in_payload_manifest": [MANIFEST_NAME, RECEIPT_NAME, INDEX_NAME],
            "all_roles_unique": len(roles) == len(records),
            "all_path_sha_identities_unique": len(identities) == len(records),
            "unregistered_enrollment_allowed": False,
        }
        manifest_raw = canonical(manifest)
        manifest_fd = write_exclusive(root_fd, MANIFEST_NAME, manifest_raw)
        closure_fds.append(manifest_fd)
        receipt = {
            "schema": (
                "historical_200k_fixed10k_stage07_08_result_free_preflight_transport_prepared_receipt_v1"
            ),
            "status": STATUS,
            "created_utc": args.created_utc,
            "package_directory": args.final_package_name,
            "authority": AUTHORITY,
            "manifest_sha256": sha(manifest_raw),
            "payload_file_count": len(records),
            "total_regular_file_count": len(records) + 3,
            "sha256_index_listed_count": len(records) + 2,
            "freeze_root_st_dev": root_info.st_dev,
            "freeze_root_st_ino": root_info.st_ino,
            "freeze_binding": (
                "HELD_FD_NAMED_PATH_DEV_INO_SIZE_BYTES_SHA256_CONTINUITY_THROUGH_"
                "MANIFEST_RECEIPT_INDEX_FCHMOD_FSYNC_AND_FINAL_REVALIDATION"
            ),
            "fresh_qa_effect": (
                "AT_MOST_ELIGIBLE_FOR_SEPARATE_EXACT_SCOPE_TRANSPORT_AND_NATIVE_"
                "PREFLIGHT_AUTHORIZATION_NOT_SIGCONT"
            ),
            "next_legal_action": "FRESH_RESULT_BLIND_INDEPENDENT_QA_OF_EXACT_FROZEN_BYTES_ONLY",
            "scope": {
                "mars_accessed": False,
                "mars_written": False,
                "native_preflight_executed": False,
                "production_root_or_journal_written": False,
                "results_accessed": False,
                "signals_sent": False,
                "stage07_or_stage08_executed": False,
                "transport_built_or_smoked": False,
                "watcher_inspected_or_resumed": False,
            },
            "supporting_evidence_not_fresh_qa": {
                "preflight_v3_author_164_of_164_double_run": True,
                "preflight_v3_root_redteam_36_of_36_double_run": True,
            },
            "v10_formal_qa": {
                "action_scoped_verdict": (
                    "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LOCAL_NATIVE_PREFLIGHT_"
                    "PREREQUISITE_ONLY"
                ),
                "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
                "receipt_sha256": (
                    "ba35a9a1f597e81c819a43c3a22920a19873c27c88cf1ea6a1d2ca8e6cac5d45"
                ),
                "sha256_index_sha256": (
                    "f0bc12e7b359aa3bd934f33b643f4836a876c6ed672b29ee525c546dc75d539a"
                ),
            },
        }
        receipt_raw = canonical(receipt)
        receipt_fd = write_exclusive(root_fd, RECEIPT_NAME, receipt_raw)
        closure_fds.append(receipt_fd)
        all_for_index: dict[str, bytes] = {
            path: raw for path, (_, raw, _) in held.items()
        }
        all_for_index[MANIFEST_NAME] = manifest_raw
        all_for_index[RECEIPT_NAME] = receipt_raw
        index_raw = "".join(
            f"{sha(raw)}  {path}\n" for path, raw in sorted(all_for_index.items())
        ).encode("utf-8")
        index_fd = write_exclusive(root_fd, INDEX_NAME, index_raw)
        closure_fds.append(index_fd)
        # Freeze every held payload file without reopening it by pathname.
        for fd, _, _ in held.values():
            os.fchmod(fd, 0o444)
            os.fsync(fd)
        for relative_dir in sorted(dirs, key=lambda item: len(PurePosixPath(item).parts), reverse=True):
            chmod_directory(root / relative_dir)
        os.fchmod(root_fd, 0o555)
        os.fsync(root_fd)
        expected_files = set(payload_paths) | {MANIFEST_NAME, RECEIPT_NAME, INDEX_NAME}
        final_files, final_dirs = inventory(root)
        if set(final_files) != expected_files or final_dirs != dirs:
            raise FreezeError("post-freeze unregistered enrollment or missing artifact")
        # Final same-FD identity/size/bytes/SHA and named-path continuity.
        for path, (fd, raw, before) in held.items():
            parts = safe_parts(path)
            parent_fd = os.dup(root_fd)
            try:
                for component in parts[:-1]:
                    child = os.open(
                        component,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                    os.close(parent_fd)
                    parent_fd = child
                named = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            finally:
                os.close(parent_fd)
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino, after.st_size, after.st_nlink) != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                1,
            ):
                raise FreezeError(f"held identity changed after freeze: {path}")
            if (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino):
                raise FreezeError(f"named identity changed after freeze: {path}")
            if read_fd(fd, after.st_size) != raw or sha(raw) != records[payload_paths.index(path)]["sha256"]:
                raise FreezeError(f"held bytes changed after freeze: {path}")
        result = {
            "status": "PASS_FROZEN_PREPARED_ONLY_AWAITING_FRESH_INDEPENDENT_QA_NOT_AUTHORIZED",
            "payload_file_count": len(records),
            "total_regular_file_count": len(expected_files),
            "directory_count": len(dirs),
            "unique_role_count": len(roles),
            "unique_artifact_identity_count": len(identities),
            "manifest_sha256": sha(manifest_raw),
            "receipt_sha256": sha(receipt_raw),
            "sha256sums_sha256": sha(index_raw),
            "root_st_dev": root_info.st_dev,
            "root_st_ino": root_info.st_ino,
            "root_mode": "0555",
            "files_mode": "0444",
            "nlink": 1,
            "mars_accessed": False,
            "results_accessed": False,
            "signals_sent": False,
        }
        print(canonical(result).decode("utf-8"), end="")
        return 0
    finally:
        for fd, _, _ in held.values():
            try:
                os.close(fd)
            except OSError:
                pass
        for fd in closure_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        os.close(root_fd)


if __name__ == "__main__":
    raise SystemExit(main())
