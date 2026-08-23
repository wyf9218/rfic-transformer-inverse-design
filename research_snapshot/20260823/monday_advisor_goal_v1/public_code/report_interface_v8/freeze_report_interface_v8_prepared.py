#!/usr/bin/env python3
"""Freeze this result-blind v8 prepared candidate without path/FD drift."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[2]
SOURCE_WIP = (
    WORKSPACE
    / "reports/historical_200k_fixed10k_mars_physical_20260822"
    / "report_interface_compatibility_v8_wip_20260823T042401Z"
)
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
INDEX_NAME = "SHA256SUMS.txt"
RECEIPT_NAME = "PREPARED_RECEIPT.json"

PAYLOAD_ROLES = {
    "AUTHOR_COMPILE_OUTPUT.json": "author_compile_output",
    "AUTHOR_HOSTILE_MATRIX_RUN1.json": "author_hostile_matrix_run_1",
    "AUTHOR_HOSTILE_MATRIX_RUN2.json": "author_hostile_matrix_run_2",
    "AUTHOR_STATIC_COMPATIBILITY_OUTPUT.json": "author_static_compatibility_output",
    "AUTHOR_UNITTEST_OUTPUT.txt": "author_unittest_output",
    "AUTHOR_VALIDATION_SUMMARY.json": "author_validation_summary",
    "FORMAL_V5_NO_GO_BINDING.json": "formal_v5_no_go_binding",
    "FORMAL_V6_NO_GO_BINDING.json": "formal_v6_no_go_binding",
    "FORMAL_V7_NO_GO_BINDING.json": "formal_v7_no_go_binding",
    "FROZEN_V8_RELEASE_CONTRACT_REFERENCE.json": "frozen_v8_release_contract_reference",
    "PREPARED_REPORT_CN.md": "prepared_report",
    "PREPARED_RECEIPT.json": "prepared_receipt",
    "README_CN.md": "prepared_readme",
    "REPORT_INTERFACE_COMPATIBILITY_CONTRACT_V8.json": "report_interface_contract_v8",
    "SOURCE_WIP_BINDING.json": "source_wip_binding",
    "adapt_complete_emx_interface_v8.py": "adapter_implementation",
    "check_v8_against_frozen_v8_producer.py": "frozen_producer_static_checker",
    "consume_portable_emx_interface_v8.py": "portable_consumer_implementation",
    "freeze_report_interface_v8_prepared.py": "candidate_freeze_builder",
    "run_report_interface_v8_hostile_matrix.py": "hostile_matrix_harness",
    "test_report_interface_compatibility_v8.py": "compatibility_unittest_suite"
}

EXACT_WIP_BYTES = {
    "FORMAL_V5_NO_GO_BINDING.json",
    "FORMAL_V6_NO_GO_BINDING.json",
    "FORMAL_V7_NO_GO_BINDING.json",
    "FROZEN_V8_RELEASE_CONTRACT_REFERENCE.json",
    "adapt_complete_emx_interface_v8.py",
    "check_v8_against_frozen_v8_producer.py",
    "consume_portable_emx_interface_v8.py",
}


class FreezeError(RuntimeError):
    pass


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def strict_json(raw: bytes, label: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in values:
            if key in out:
                raise FreezeError(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out

    def reject_constant(token: str) -> None:
        raise FreezeError(f"{label}: non-finite JSON constant {token}")

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=reject_constant,
    )


def read_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        chunks.append(block)
    return b"".join(chunks)


def mode_bits(value: os.stat_result) -> int:
    return stat.S_IMODE(value.st_mode)


def open_absolute_dir_chain(path: Path) -> list[tuple[str, int, os.stat_result]]:
    resolved = path.absolute()
    if not resolved.is_absolute():
        raise FreezeError("candidate root must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    chain: list[tuple[str, int, os.stat_result]] = []
    fd = os.open("/", flags)
    chain.append(("/", fd, os.fstat(fd)))
    try:
        for component in resolved.parts[1:]:
            child = os.open(component, flags, dir_fd=fd)
            child_stat = os.fstat(child)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child)
                raise FreezeError(f"non-directory path component: {component}")
            chain.append((component, child, child_stat))
            fd = child
    except Exception:
        for _, held, _ in reversed(chain):
            os.close(held)
        raise
    return chain


def verify_fresh_chain(expected: list[tuple[str, int, os.stat_result]]) -> None:
    fresh = open_absolute_dir_chain(ROOT)
    try:
        if len(fresh) != len(expected):
            raise FreezeError("absolute candidate path component count changed")
        for (left_name, _, left), (right_name, _, right) in zip(expected, fresh):
            if left_name != right_name or (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino):
                raise FreezeError(f"candidate ancestor identity changed at {left_name!r}")
    finally:
        for _, fd, _ in reversed(fresh):
            os.close(fd)


def open_snapshot(root_fd: int, name: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, dir_fd=root_fd)
    observed = os.fstat(fd)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        os.close(fd)
        raise FreezeError(f"{name}: expected single-link regular file")
    raw = read_fd(fd)
    if len(raw) != observed.st_size:
        os.close(fd)
        raise FreezeError(f"{name}: held size differs from held bytes")
    return {
        "fd": fd,
        "raw": raw,
        "sha256": sha(raw),
        "st_dev": observed.st_dev,
        "st_ino": observed.st_ino,
        "size": observed.st_size,
    }


def write_exclusive_snapshot(root_fd: int, name: str, raw: bytes) -> dict[str, Any]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, 0o600, dir_fd=root_fd)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise FreezeError(f"{name}: short write")
            view = view[count:]
        os.fsync(fd)
        observed = os.fstat(fd)
        held_raw = read_fd(fd)
        if held_raw != raw or observed.st_size != len(raw):
            raise FreezeError(f"{name}: exclusive write/held bytes mismatch")
        return {
            "fd": fd,
            "raw": held_raw,
            "sha256": sha(held_raw),
            "st_dev": observed.st_dev,
            "st_ino": observed.st_ino,
            "size": observed.st_size,
        }
    except Exception:
        os.close(fd)
        raise


def revalidate_named(root_fd: int, name: str, snapshot: dict[str, Any]) -> None:
    named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
        raise FreezeError(f"{name}: named object is not single-link regular file")
    identity = (named.st_dev, named.st_ino, named.st_size)
    expected = (snapshot["st_dev"], snapshot["st_ino"], snapshot["size"])
    if identity != expected:
        raise FreezeError(f"{name}: named identity/size differs from held snapshot")
    fresh = open_snapshot(root_fd, name)
    try:
        if (
            fresh["st_dev"], fresh["st_ino"], fresh["size"], fresh["sha256"], fresh["raw"]
        ) != (
            snapshot["st_dev"], snapshot["st_ino"], snapshot["size"],
            snapshot["sha256"], snapshot["raw"]
        ):
            raise FreezeError(f"{name}: fresh named bytes differ from held snapshot")
    finally:
        os.close(fresh["fd"])


def validate_author_outputs(snapshots: dict[str, dict[str, Any]]) -> None:
    compile_output = strict_json(snapshots["AUTHOR_COMPILE_OUTPUT.json"]["raw"], "compile")
    if (compile_output["compile_pass_count"], compile_output["compile_fail_count"]) != (5, 0):
        raise FreezeError("compile gate is not 5/5 PASS")
    unittest_text = snapshots["AUTHOR_UNITTEST_OUTPUT.txt"]["raw"].decode("utf-8")
    if "Ran 39 tests" not in unittest_text or "\nOK" not in unittest_text or "FAILED" in unittest_text:
        raise FreezeError("direct unittest output is not 39/39 PASS")
    hostile_payloads = [
        strict_json(snapshots[name]["raw"], name)
        for name in ("AUTHOR_HOSTILE_MATRIX_RUN1.json", "AUTHOR_HOSTILE_MATRIX_RUN2.json")
    ]
    if snapshots["AUTHOR_HOSTILE_MATRIX_RUN1.json"]["raw"] != snapshots["AUTHOR_HOSTILE_MATRIX_RUN2.json"]["raw"]:
        raise FreezeError("hostile matrix replay bytes differ")
    for payload in hostile_payloads:
        expected = (
            payload["unittest_method_count"], payload["hostile_gate_pass_count"],
            payload["hostile_gate_fail_count"], payload["failures"]
        )
        if expected != (39, 151, 0, []):
            raise FreezeError("hostile matrix is not 39 methods / 151 gates PASS")
    static_output = strict_json(
        snapshots["AUTHOR_STATIC_COMPATIBILITY_OUTPUT.json"]["raw"], "static compatibility"
    )
    if static_output["status"] != "PASS_RESULT_BLIND_STATIC_SOURCE_CONTRACT":
        raise FreezeError("static compatibility gate is not PASS")
    if len(static_output["gates"]) != 22 or any(not str(value).startswith("PASS") for value in static_output["gates"].values()):
        raise FreezeError("static compatibility is not 22/22 PASS")
    validation = strict_json(
        snapshots["AUTHOR_VALIDATION_SUMMARY.json"]["raw"], "author validation summary"
    )
    if validation["status"] != "AWAITING_FRESH_INDEPENDENT_QA":
        raise FreezeError("author validation summary status drift")


def main() -> int:
    if set(PAYLOAD_ROLES.values()) != set(PAYLOAD_ROLES.values()) or len(set(PAYLOAD_ROLES.values())) != len(PAYLOAD_ROLES):
        raise FreezeError("package roles must be unique")
    if ROOT.name != "report_interface_compatibility_v8_prepared_20260823T045542Z":
        raise FreezeError("freeze builder may run only in the exact prepared root")
    if (ROOT / MANIFEST_NAME).exists() or (ROOT / INDEX_NAME).exists() or (ROOT / RECEIPT_NAME).exists():
        raise FreezeError("no-clobber terminal package outputs already exist")

    chain = open_absolute_dir_chain(ROOT)
    root_fd = chain[-1][1]
    snapshots: dict[str, dict[str, Any]] = {}
    try:
        expected_before = set(PAYLOAD_ROLES) - {RECEIPT_NAME}
        observed_before = set(os.listdir(root_fd))
        if observed_before != expected_before:
            raise FreezeError(
                f"unregistered or missing pre-freeze artifact: expected={sorted(expected_before)} observed={sorted(observed_before)}"
            )
        for name in sorted(expected_before):
            snapshots[name] = open_snapshot(root_fd, name)

        binding = strict_json(snapshots["SOURCE_WIP_BINDING.json"]["raw"], "source WIP binding")
        source_hashes = binding["source_files_sha256"]
        for name, expected_sha in source_hashes.items():
            raw = (SOURCE_WIP / name).read_bytes()
            if sha(raw) != expected_sha:
                raise FreezeError(f"source WIP hash drift: {name}")
        for name in EXACT_WIP_BYTES:
            if snapshots[name]["raw"] != (SOURCE_WIP / name).read_bytes():
                raise FreezeError(f"prepared exact-byte dependency differs from source WIP: {name}")

        contract = strict_json(
            snapshots["REPORT_INTERFACE_COMPATIBILITY_CONTRACT_V8.json"]["raw"], "contract"
        )
        if contract["status"] != "AWAITING_FRESH_INDEPENDENT_QA":
            raise FreezeError("contract status is not AWAITING_FRESH_INDEPENDENT_QA")
        readme_title = snapshots["README_CN.md"]["raw"].decode("utf-8").splitlines()[0]
        if "AWAITING_FRESH_INDEPENDENT_QA" not in readme_title or "WIP" in readme_title:
            raise FreezeError("README title is not prepared/awaiting")
        validate_author_outputs(snapshots)

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload_bindings = {
            PAYLOAD_ROLES[name]: {
                "path": name,
                "sha256": snapshots[name]["sha256"],
                "size_bytes": snapshots[name]["size"],
                "device": snapshots[name]["st_dev"],
                "inode": snapshots[name]["st_ino"],
            }
            for name in sorted(snapshots)
        }
        receipt = {
            "schema": "report_interface_compatibility_v8_prepared_receipt_v1",
            "status": "AWAITING_FRESH_INDEPENDENT_QA",
            "prepared_utc": now,
            "candidate_root": str(ROOT.relative_to(WORKSPACE)),
            "source_wip_root": str(SOURCE_WIP.relative_to(WORKSPACE)),
            "author_gates": {
                "compile": "5/5 PASS",
                "direct_unittest": "39/39 PASS",
                "hostile_matrix_run_1": "151/151 PASS",
                "hostile_matrix_run_2": "151/151 PASS",
                "frozen_v8_static_compatibility": "22/22 PASS",
                "formal_v7_failed_attacks_replayed": "3/3 REJECTED",
            },
            "payload_bindings_before_receipt": payload_bindings,
            "expected_final_counts": {
                "manifest_record_count": len(PAYLOAD_ROLES),
                "sha256_index_entry_count": len(PAYLOAD_ROLES) + 1,
                "top_level_regular_file_count": len(PAYLOAD_ROLES) + 2,
                "unique_package_roles": len(PAYLOAD_ROLES),
                "nested_interface_roles": 41,
                "unique_nested_path_sha_identities_required": 41,
            },
            "freeze_contract": {
                "absolute_path_components_held_nofollow": True,
                "every_payload_held_by_fd_through_freeze": True,
                "device_inode_size_bytes_sha_revalidated": True,
                "all_files_mode": "0444",
                "all_files_nlink": 1,
                "root_mode": "0555",
                "no_extras_allowed": True,
                "no_clobber": True,
            },
            "authority": {
                "independent_go_issued": False,
                "execution_authorized": False,
                "result_access_authorized": False,
                "mars_access_authorized": False,
                "watcher_signal_authorized": False,
                "stage07_or_stage08_authorized": False,
                "report_publication_authorized": False,
            },
            "boundaries": {
                "actual_complete_interface_or_results_read": False,
                "actual_emx_metrics_or_results_read": False,
                "mars_login_performed": False,
                "production_chain_executed": False,
                "watcher_resume_or_signal_performed": False,
            },
            "next_legal_action": "FRESH_RESULT_BLIND_INDEPENDENT_QA_OF_THIS_EXACT_FROZEN_V8_PACKAGE",
        }
        snapshots[RECEIPT_NAME] = write_exclusive_snapshot(root_fd, RECEIPT_NAME, canonical(receipt))

        records = []
        for name in sorted(PAYLOAD_ROLES):
            item = snapshots[name]
            records.append({
                "role": PAYLOAD_ROLES[name],
                "path": name,
                "sha256": item["sha256"],
                "size_bytes": item["size"],
                "device": item["st_dev"],
                "inode": item["st_ino"],
                "expected_nlink": 1,
                "frozen_mode": "0444",
            })
        identities = {(row["path"], row["sha256"]) for row in records}
        if len({row["role"] for row in records}) != len(records) or len(identities) != len(records):
            raise FreezeError("package role-to-(path,SHA) identities are not one-to-one")
        aggregate_raw = "".join(
            f"{row['role']}\t{row['sha256']}  {row['path']}\n" for row in records
        ).encode("utf-8")
        manifest = {
            "schema": "report_interface_compatibility_v8_package_manifest_v1",
            "status": "AWAITING_FRESH_INDEPENDENT_QA",
            "candidate_root": str(ROOT.relative_to(WORKSPACE)),
            "record_count": len(records),
            "unique_role_count": len({row["role"] for row in records}),
            "unique_path_sha_identity_count": len(identities),
            "records": records,
            "aggregate_algorithm": "sha256(sorted manifest rows: role TAB sha256 two-spaces path newline)",
            "aggregate_sha256": sha(aggregate_raw),
            "expected_final_top_level_regular_file_count": len(records) + 2,
            "expected_sha256sum_entry_count": len(records) + 1,
            "all_regular_files_expected_nlink": 1,
            "all_files_frozen_mode": "0444",
            "prepared_root_mode": "0555",
            "contains_actual_emx_or_metric_values": False,
            "author_package_is_independent_go": False,
        }
        snapshots[MANIFEST_NAME] = write_exclusive_snapshot(
            root_fd, MANIFEST_NAME, canonical(manifest)
        )
        indexed_names = sorted(set(PAYLOAD_ROLES) | {MANIFEST_NAME})
        index_raw = "".join(
            f"{snapshots[name]['sha256']}  {name}\n" for name in indexed_names
        ).encode("utf-8")
        snapshots[INDEX_NAME] = write_exclusive_snapshot(root_fd, INDEX_NAME, index_raw)

        expected_final = set(PAYLOAD_ROLES) | {MANIFEST_NAME, INDEX_NAME}
        if set(os.listdir(root_fd)) != expected_final:
            raise FreezeError("final package contains an unregistered or missing artifact")

        for name in sorted(snapshots):
            revalidate_named(root_fd, name, snapshots[name])
            os.fchmod(snapshots[name]["fd"], 0o444)
        os.fchmod(root_fd, 0o555)
        os.fsync(root_fd)

        for name in sorted(snapshots):
            revalidate_named(root_fd, name, snapshots[name])
            named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if mode_bits(named) != 0o444:
                raise FreezeError(f"{name}: final mode is not 0444")
        if mode_bits(os.fstat(root_fd)) != 0o555:
            raise FreezeError("candidate root final mode is not 0555")
        verify_fresh_chain(chain)
        if set(os.listdir(root_fd)) != expected_final:
            raise FreezeError("post-freeze package artifact set changed")

        print(json.dumps({
            "status": "AWAITING_FRESH_INDEPENDENT_QA",
            "candidate_root": str(ROOT),
            "payload_record_count": len(records),
            "final_top_level_file_count": len(expected_final),
            "package_manifest_sha256": snapshots[MANIFEST_NAME]["sha256"],
            "prepared_receipt_sha256": snapshots[RECEIPT_NAME]["sha256"],
            "sha256sums_sha256": snapshots[INDEX_NAME]["sha256"],
            "root_mode": "0555",
            "file_mode": "0444",
            "independent_go_issued": False,
        }, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    finally:
        for item in snapshots.values():
            try:
                os.close(item["fd"])
            except OSError:
                pass
        for _, fd, _ in reversed(chain):
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
