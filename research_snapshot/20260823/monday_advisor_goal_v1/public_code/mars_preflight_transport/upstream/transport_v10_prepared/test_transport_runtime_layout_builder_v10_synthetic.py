#!/usr/bin/env python3
"""Result-blind synthetic regression for the v10 transport/runtime core builder."""

from __future__ import annotations

import contextlib
import copy
import ctypes
import hashlib
import importlib.util
import inspect
import json
import os
import stat
import sys
import tempfile
import threading
import types
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
PHYSICAL = HERE.parent
V8 = PHYSICAL / "post_stage06_release_chain_v8_prepared_20260822T142204Z"
V9_NEGATIVE_QA = (
    PHYSICAL
    / "independent_transport_runtime_layout_builder_v9_qa_20260822T203210Z"
)
V8_NEGATIVE_QA = (
    PHYSICAL
    / "independent_transport_runtime_layout_builder_v8_qa_20260822T200141Z"
)
V7_NEGATIVE_QA = (
    PHYSICAL
    / "independent_transport_runtime_layout_builder_v7_qa_20260822T185912Z"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load_module(
    "result_blind_transport_builder_v10",
    HERE / "build_result_free_transport_runtime_v10.py",
)

DIST_MODULES = {
    "numpy": "numpy/__init__.py",
    "matplotlib": "matplotlib/__init__.py",
    "contourpy": "contourpy/__init__.py",
    "cycler": "cycler/__init__.py",
    "fonttools": "fontTools/__init__.py",
    "kiwisolver": "kiwisolver/__init__.py",
    "packaging": "packaging/__init__.py",
    "pillow": "PIL/__init__.py",
    "pyparsing": "pyparsing/__init__.py",
    "python-dateutil": "dateutil/__init__.py",
    "six": "six.py",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_distribution(root: Path, name: str) -> None:
    version = "1.0"
    module = DIST_MODULES[name]
    write(root / module, f'__version__ = "{version}"\n')
    dist_dir = root / f"{name.replace('-', '_')}-{version}.dist-info"
    metadata = dist_dir / "METADATA"
    record = dist_dir / "RECORD"
    write(metadata, f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    rows = [
        module,
        metadata.relative_to(root).as_posix(),
        record.relative_to(root).as_posix(),
        *builder.EXTERNAL_RECORD_EXCLUSIONS[name],
    ]
    write(record, "".join(f"{row},,\n" for row in rows))


def make_site(root: Path) -> None:
    root.mkdir()
    for name in builder.COPY_DISTRIBUTIONS:
        make_distribution(root, name)


def thaw(root: Path) -> None:
    if not root.exists():
        return
    paths = sorted(root.rglob("*"), key=lambda item: len(item.parts))
    for path in paths:
        with contextlib.suppress(OSError):
            if path.is_dir() and not path.is_symlink():
                os.chmod(path, 0o755)
            elif path.is_file() and not path.is_symlink():
                os.chmod(path, 0o644)
    with contextlib.suppress(OSError):
        os.chmod(root, 0o755)


def rejected(call: Callable[[], Any]) -> bool:
    try:
        call()
    except builder.BuildError:
        return True
    return False


def synthetic_rename_noreplace(old_fd: int, old_name: str,
                               new_fd: int, new_name: str) -> None:
    try:
        os.stat(new_name, dir_fd=new_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise builder.BuildError("synthetic no-replace collision")
    # Darwin rejects renaming a mode-0555 directory even when both parent
    # directories are writable.  This is only the explicitly non-Linux
    # synthetic injection: retain the inode and restore the frozen mode via a
    # held nofollow FD.  Production still requires Linux renameat2.
    source_fd = -1
    try:
        if sys.platform == "darwin":
            source_fd = os.open(
                old_name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=old_fd,
            )
            os.fchmod(source_fd, 0o755)
        os.rename(old_name, new_name, src_dir_fd=old_fd, dst_dir_fd=new_fd)
    finally:
        if source_fd >= 0:
            os.fchmod(source_fd, 0o555)
            os.close(source_fd)


def synthetic_terminal_publish(
    journal_fd: int,
    name: str,
    data: bytes,
    *,
    mid_write_hook: Callable[[], None] | None = None,
    after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    return builder.publish_terminal_via_injected_complete_rename(
        journal_fd,
        name,
        data,
        rename_impl=synthetic_regular_rename_noreplace,
        mid_write_hook=mid_write_hook,
        after_link_before_dir_fsync_hook=after_link_before_dir_fsync_hook,
    )


def synthetic_regular_rename_noreplace(
    old_fd: int, old_name: str, new_fd: int, new_name: str
) -> None:
    """Atomic no-clobber rename for the Darwin synthetic terminal fixture."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        if renameatx_np(
            old_fd,
            ctypes.c_char_p(os.fsencode(old_name)),
            new_fd,
            ctypes.c_char_p(os.fsencode(new_name)),
            0x00000004,  # RENAME_EXCL from Darwin sys/stdio.h
        ) != 0:
            err = ctypes.get_errno()
            raise builder.BuildError(
                f"synthetic renameatx_np(RENAME_EXCL) failed: "
                f"errno={err} {os.strerror(err)}"
            )
        return
    if sys.platform.startswith("linux"):
        builder.renameat2_noreplace(old_fd, old_name, new_fd, new_name)
        return
    raise builder.BuildError(
        "no atomic no-clobber regular rename implementation on this platform"
    )


_execute_authorized = builder.execute_synthetic_author_test


def execute_synthetic(*args: Any, **kwargs: Any) -> dict[str, Any]:
    enforce_fixed = kwargs.pop("enforce_fixed", False)
    if enforce_fixed is not False:
        raise AssertionError("synthetic helper cannot enable production mode")
    kwargs["terminal_publish_impl"] = synthetic_terminal_publish
    kwargs["terminal_publication_method"] = (
        builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
    )
    return _execute_authorized(*args, **kwargs)


def dummy_package_bindings(temp: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    temp.mkdir(parents=True)
    package = temp / "v10-package"
    package.mkdir()
    package_names = {
        "builder": "build_result_free_transport_runtime_v10.py",
        "test": "test_transport_runtime_layout_builder_v10_synthetic.py",
        "smoke": "result_free_runtime_smoke_v10.py",
        "smoke_test": "test_result_free_runtime_smoke_v10_synthetic.py",
        "bundle_manifest": "BUNDLE_MANIFEST.json",
        "sha256_index": "SHA256SUMS",
        "prepared_receipt": "PREPARED_RESULT_FREE_RECEIPT.json",
    }
    package_binding: dict[str, Any] = {"directory": os.fspath(package)}
    for stem, filename in package_names.items():
        path = package / filename
        write(path, f"synthetic {stem}\n")
        os.chmod(path, 0o444)
        package_binding[f"{stem}_path"] = os.fspath(path)
        package_binding[f"{stem}_sha256"] = sha(path)

    audit = temp / "v10-audit"
    audit.mkdir()
    audit_names = {
        "report": "REPORT.md", "output": "OUTPUT.json", "log": "LOG.txt",
        "harness": "HARNESS.py", "sha256_index": "SHA256SUMS",
    }
    audit_binding: dict[str, Any] = {
        "directory": os.fspath(audit),
        "action_scoped_verdict": (
            "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
        ),
    }
    for stem, filename in audit_names.items():
        path = audit / filename
        write(path, f"synthetic audit {stem}\n")
        os.chmod(path, 0o444)
        audit_binding[f"{stem}_path"] = os.fspath(path)
        audit_binding[f"{stem}_sha256"] = sha(path)
    receipt_path = audit / "RECEIPT.json"
    audit_receipt = {
        "schema": builder.V10_QA_RECEIPT_SCHEMA,
        "status": builder.V10_QA_RECEIPT_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "action_scoped_verdict": builder.V10_QA_ACTION_VERDICT,
        "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        "package": {
            "directory": package_binding["directory"],
            "builder_sha256": package_binding["builder_sha256"],
            "test_sha256": package_binding["test_sha256"],
            "smoke_sha256": package_binding["smoke_sha256"],
            "smoke_test_sha256": package_binding["smoke_test_sha256"],
            "bundle_manifest_sha256": package_binding[
                "bundle_manifest_sha256"
            ],
            "sha256_index_sha256": package_binding["sha256_index_sha256"],
            "prepared_receipt_sha256": package_binding[
                "prepared_receipt_sha256"
            ],
        },
        "audit_evidence": {
            key: audit_binding[f"{key}_sha256"]
            for key in ("report", "output", "log", "harness", "sha256_index")
        },
        "authority": {
            "transport_runtime_layout_authorized": False,
            "mars_preflight_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "controller_or_outer_main_authorized": False,
            "deployment_or_resume_authorized": False,
        },
    }
    receipt_path.write_bytes(builder.canonical_json_bytes(audit_receipt))
    os.chmod(receipt_path, 0o444)
    audit_binding["receipt_path"] = os.fspath(receipt_path)
    audit_binding["receipt_sha256"] = sha(receipt_path)
    return package_binding, audit_binding


def frozen_v7_negative_qa_binding() -> dict[str, Any]:
    filenames = {
        "bundle_manifest": "BUNDLE_MANIFEST.json",
        "log": "COMMAND_LOG.txt",
        "output": "INDEPENDENT_QA_OUTPUT.json",
        "receipt": "INDEPENDENT_QA_RECEIPT.json",
        "report": "INDEPENDENT_QA_REPORT_CN.md",
        "closure": "PACKAGE_CLOSURE_QA.json",
        "harness": "QA_HARNESS_OR_METHOD.md",
        "sha256_index": "SHA256SUMS",
    }
    result: dict[str, Any] = {
        "directory": os.fspath(V7_NEGATIVE_QA),
        "action_scoped_verdict": builder.V7_NEGATIVE_QA_ACTION_VERDICT,
        "finding_counts": copy.deepcopy(
            builder.V7_NEGATIVE_QA_BINDING["finding_counts"]
        ),
    }
    for stem, filename in filenames.items():
        result[f"{stem}_path"] = os.fspath(V7_NEGATIVE_QA / filename)
        result[f"{stem}_sha256"] = builder.V7_NEGATIVE_QA_BINDING[
            f"{stem}_sha256"
        ]
    return result


def frozen_v8_negative_qa_binding() -> dict[str, Any]:
    result: dict[str, Any] = {
        "directory": os.fspath(V8_NEGATIVE_QA),
        "action_scoped_verdict": builder.V8_NEGATIVE_QA_ACTION_VERDICT,
        "finding_counts": copy.deepcopy(
            builder.V8_NEGATIVE_QA_BINDING["finding_counts"]
        ),
    }
    for stem, item in builder.V8_NEGATIVE_QA_FILE_BINDINGS.items():
        result[f"{stem}_path"] = os.fspath(
            V8_NEGATIVE_QA / item["filename"]
        )
        result[f"{stem}_sha256"] = item["sha256"]
    return result


def frozen_v9_negative_qa_binding() -> dict[str, Any]:
    result: dict[str, Any] = {
        "directory": os.fspath(V9_NEGATIVE_QA),
        "action_scoped_verdict": builder.V9_NEGATIVE_QA_ACTION_VERDICT,
        "finding_counts": copy.deepcopy(
            builder.V9_NEGATIVE_QA_BINDING["finding_counts"]
        ),
    }
    for stem, item in builder.V9_NEGATIVE_QA_FILE_BINDINGS.items():
        result[f"{stem}_path"] = os.fspath(
            V9_NEGATIVE_QA / item["filename"]
        )
        result[f"{stem}_sha256"] = item["sha256"]
    return result


def write_frozen_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    os.chmod(path, 0o444)


def manifest_records(directory: Path, names: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": name,
            "role": f"synthetic_result_blind_fixture_{name.lower()}",
            "sha256": sha(directory / name),
            "size_bytes": (directory / name).stat().st_size,
        }
        for name in sorted(names)
    ]


def create_dynamic_preflight_v2_closures(
    temp: Path, decision_id: str
) -> tuple[Path, Path, Path, Path]:
    authority = copy.deepcopy(builder.PREFLIGHT_V2_ALL_FALSE_AUTHORITY)
    package_dir = temp / f"preflight-v2-prepared-{decision_id}"
    package_dir.mkdir()
    package_payload = {
        "AUTHOR_COMPILE_V2_OUTPUT.json": builder.canonical_json_bytes({
            "schema": "synthetic_preflight_v2_compile_output_v1",
            "status": "PASS_2_OF_2",
        }),
        "AUTHOR_PREFLIGHT_V2_SYNTHETIC_OUTPUT.json": builder.canonical_json_bytes({
            "schema": "synthetic_preflight_v2_author_output_v1",
            "status": "PASS_SYNTHETIC",
        }),
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V2.json": (
            builder.canonical_json_bytes({
                "schema": "synthetic_preflight_v2_contract_v1",
                "status": "RESULT_BLIND_FIXTURE_ONLY",
            })
        ),
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V2_CN.md": (
            "# synthetic result-blind preflight-v2 fixture\n"
        ).encode("utf-8"),
        "UPSTREAM_EVIDENCE_BINDINGS_V2.json": builder.canonical_json_bytes({
            "schema": "synthetic_preflight_v2_upstream_bindings_v1",
            "status": "RESULT_BLIND_FIXTURE_ONLY",
        }),
        "run_result_free_mars_native_preflight_v2.py": (
            b"# synthetic result-blind preflight-v2 source fixture\n"
        ),
        "test_result_free_mars_native_preflight_v2_synthetic.py": (
            b"# synthetic result-blind preflight-v2 test fixture\n"
        ),
    }
    for name, data in package_payload.items():
        write_frozen_bytes(package_dir / name, data)
    package_manifest = {
        "schema": builder.PREFLIGHT_V2_PREPARED_MANIFEST_SCHEMA,
        "status": builder.PREFLIGHT_V2_PREPARED_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "payload_file_count": 7,
        "files": manifest_records(package_dir, set(package_payload)),
        "closure_files_not_in_payload_manifest": sorted(
            builder.PREFLIGHT_V2_PREPARED_CLOSURE_NAMES
        ),
        "authority": authority,
    }
    package_manifest_path = package_dir / "BUNDLE_MANIFEST.json"
    write_frozen_bytes(
        package_manifest_path, builder.canonical_json_bytes(package_manifest)
    )
    package_receipt = {
        "schema": builder.PREFLIGHT_V2_PREPARED_RECEIPT_SCHEMA,
        "status": builder.PREFLIGHT_V2_PREPARED_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "package_directory": package_dir.name,
        "package_closure": {
            "bundle_manifest_sha256": sha(package_manifest_path),
            "payload_file_count": 7,
            "sha_index_listed_count_expected": 9,
            "top_level_file_count_expected": 10,
        },
        "locked_tools": {
            "preflight": {
                "path": "run_result_free_mars_native_preflight_v2.py",
                "sha256": sha(
                    package_dir / "run_result_free_mars_native_preflight_v2.py"
                ),
                "line_count": 1,
            },
            "synthetic_test": {
                "path": "test_result_free_mars_native_preflight_v2_synthetic.py",
                "sha256": sha(
                    package_dir
                    / "test_result_free_mars_native_preflight_v2_synthetic.py"
                ),
                "line_count": 1,
            },
        },
        "author_validation": {
            "darwin_actual": "NOT_RUN_SYNTHETIC_FIXTURE",
            "linux_xfs_actual": "NOT_RUN_NON_LINUX",
            "manifest_payload_hash_and_size_pass": True,
            "source_compile": {
                "checked": 2,
                "passed": 2,
                "failed": 0,
                "output_sha256": sha(package_dir / "AUTHOR_COMPILE_V2_OUTPUT.json"),
            },
            "synthetic_test": {
                "checked": 1,
                "passed": 1,
                "failed": 0,
                "raw_output_sha256": sha(
                    package_dir / "AUTHOR_PREFLIGHT_V2_SYNTHETIC_OUTPUT.json"
                ),
            },
            "strict_json_parse_pass": True,
        },
        "scope": {
            "mars_accessed": False,
            "mars_written": False,
            "results_accessed": False,
            "external_processes_inspected_or_controlled": False,
            "real_preflight_or_smoke_subprocess_started": False,
            "signals_sent": False,
            "controller_or_outer_main_executed": False,
            "deployment_or_resume_executed": False,
            "production_root_or_journal_created_or_modified": False,
        },
        "authority": authority,
        "next_legal_action": "FRESH_INDEPENDENT_QA_ONLY",
    }
    write_frozen_bytes(
        package_dir / "PREPARED_RESULT_FREE_RECEIPT.json",
        builder.canonical_json_bytes(package_receipt),
    )
    package_index_path = package_dir / "SHA256SUMS"
    package_index_bytes = "".join(
        f"{sha(package_dir / name)}  {name}\n"
        for name in sorted(builder.PREFLIGHT_V2_PREPARED_INDEX_NAMES)
    ).encode("utf-8")
    write_frozen_bytes(package_index_path, package_index_bytes)
    os.chmod(package_dir, 0o555)

    audit_dir = temp / f"preflight-v2-independent-qa-{decision_id}"
    audit_dir.mkdir()
    audit_payload = {
        "COMMAND_LOG.txt": b"synthetic result-blind QA command log\n",
        "INDEPENDENT_QA_OUTPUT.json": builder.canonical_json_bytes({
            "schema": "synthetic_preflight_v2_qa_output_v1",
            "status": builder.PREFLIGHT_V2_QA_STATUS,
        }),
        "INDEPENDENT_QA_REPORT_CN.md": b"# synthetic result-blind QA report\n",
        "PACKAGE_CLOSURE_QA.json": builder.canonical_json_bytes({
            "schema": "synthetic_preflight_v2_qa_closure_v1",
            "status": "PASS_EXACT_CLOSURE",
        }),
        "QA_HARNESS_OR_METHOD.md": b"# synthetic result-blind QA method\n",
    }
    for name, data in audit_payload.items():
        write_frozen_bytes(audit_dir / name, data)
    audit_manifest = {
        "schema": builder.PREFLIGHT_V2_QA_MANIFEST_SCHEMA,
        "status": builder.PREFLIGHT_V2_QA_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "payload_file_count": 5,
        "files": manifest_records(audit_dir, set(audit_payload)),
        "closure_files_not_in_payload_manifest": sorted(
            builder.PREFLIGHT_V2_QA_CLOSURE_NAMES
        ),
        "action_scoped_verdict": builder.PREFLIGHT_V2_QA_ACTION_VERDICT,
        "finding_counts": copy.deepcopy(builder.PREFLIGHT_V2_ZERO_FINDINGS),
        "authority": authority,
    }
    audit_manifest_path = audit_dir / "BUNDLE_MANIFEST.json"
    write_frozen_bytes(
        audit_manifest_path, builder.canonical_json_bytes(audit_manifest)
    )
    audit_receipt = {
        "schema": builder.PREFLIGHT_V2_QA_RECEIPT_SCHEMA,
        "status": builder.PREFLIGHT_V2_QA_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "qa_directory": audit_dir.name,
        "action_scoped_verdict": builder.PREFLIGHT_V2_QA_ACTION_VERDICT,
        "audited_package": {
            "directory": package_dir.name,
            "script_sha256": sha(
                package_dir / "run_result_free_mars_native_preflight_v2.py"
            ),
            "test_sha256": sha(
                package_dir / "test_result_free_mars_native_preflight_v2_synthetic.py"
            ),
            "contract_sha256": sha(
                package_dir / "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V2.json"
            ),
            "evidence_bindings_sha256": sha(
                package_dir / "UPSTREAM_EVIDENCE_BINDINGS_V2.json"
            ),
            "prepared_receipt_sha256": sha(
                package_dir / "PREPARED_RESULT_FREE_RECEIPT.json"
            ),
            "bundle_manifest_sha256": sha(package_manifest_path),
            "sha256_index_sha256": sha(package_index_path),
        },
        "qa_artifacts": {
            key: {"path": name, "sha256": sha(audit_dir / name)}
            for key, name in {
                "report": "INDEPENDENT_QA_REPORT_CN.md",
                "output": "INDEPENDENT_QA_OUTPUT.json",
                "log": "COMMAND_LOG.txt",
                "harness": "QA_HARNESS_OR_METHOD.md",
                "closure": "PACKAGE_CLOSURE_QA.json",
                "manifest": "BUNDLE_MANIFEST.json",
            }.items()
        },
        "independent_validation": {
            "package_closure": "PASS_10_OF_10",
            "source_compile": "PASS_2_OF_2",
            "synthetic": "PASS_RESULT_BLIND_FIXTURE",
            "linux_xfs_actual": "NOT_RUN_NON_LINUX",
        },
        "finding_counts": copy.deepcopy(builder.PREFLIGHT_V2_ZERO_FINDINGS),
        "authority": authority,
        "scope": {
            "mars_accessed": False,
            "results_accessed": False,
            "real_preflight_executed": False,
            "production_executed": False,
            "external_processes_inspected_or_controlled": False,
            "signals_sent": False,
            "candidate_modified": False,
            "memory_modified": False,
        },
        "next_legal_action": "SEPARATE_EXACT_AUTHORIZATION_ONLY",
    }
    audit_receipt_path = audit_dir / "INDEPENDENT_QA_RECEIPT.json"
    write_frozen_bytes(
        audit_receipt_path, builder.canonical_json_bytes(audit_receipt)
    )
    audit_index_path = audit_dir / "SHA256SUMS"
    audit_index_bytes = "".join(
        f"{sha(audit_dir / name)}  {name}\n"
        for name in sorted(builder.PREFLIGHT_V2_QA_INDEX_NAMES)
    ).encode("utf-8")
    write_frozen_bytes(audit_index_path, audit_index_bytes)
    os.chmod(audit_dir, 0o555)
    return package_manifest_path, package_index_path, audit_receipt_path, audit_index_path


def make_authorization(temp: Path, site: Path, final_root: Path,
                       decision_id: str) -> tuple[dict[str, Any], Path, str, list[str]]:
    snapshot = builder.discover_source(site)
    try:
        inventory = snapshot.inventory()
    finally:
        snapshot.close()
    package, audit = dummy_package_bindings(temp / f"bindings-{decision_id}")
    parent = final_root.parent
    parent_info = parent.stat()
    journal_dir = parent / f".result-free-transport-v10.{decision_id}"
    auth_path = temp / f"authorization-{decision_id}.json"
    source_python = os.path.abspath(sys.executable)
    (
        preflight_manifest_path,
        preflight_package_index_path,
        preflight_audit_receipt_path,
        preflight_audit_index_path,
    ) = create_dynamic_preflight_v2_closures(temp, decision_id)
    root_anchor_dir = temp / f"root-preflight-launch-{decision_id}"
    root_anchor_dir.mkdir()
    root_launch_auth_path = root_anchor_dir / "ROOT_LAUNCH_AUTHORIZATION.json"
    root_launch_auth_path.write_bytes(builder.canonical_json_bytes({
        "schema": builder.ROOT_LAUNCH_AUTHORIZATION_SCHEMA,
        "status": builder.ROOT_LAUNCH_AUTHORIZATION_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "decision_id": decision_id,
        "preflight_package_manifest_path": os.fspath(preflight_manifest_path),
        "preflight_package_manifest_sha256": sha(preflight_manifest_path),
        "preflight_package_index_path": os.fspath(preflight_package_index_path),
        "preflight_package_index_sha256": sha(preflight_package_index_path),
        "preflight_independent_audit_receipt_path": os.fspath(
            preflight_audit_receipt_path
        ),
        "preflight_independent_audit_receipt_sha256": sha(
            preflight_audit_receipt_path
        ),
        "preflight_independent_audit_index_path": os.fspath(
            preflight_audit_index_path
        ),
        "preflight_independent_audit_index_sha256": sha(
            preflight_audit_index_path
        ),
        "authority": {
            "preflight_launch_authorized": True,
            "transport_runtime_layout_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "deployment_or_resume_authorized": False,
        },
    }))
    os.chmod(root_launch_auth_path, 0o444)
    os.chmod(root_anchor_dir, 0o555)
    launch_receipt_path = temp / f"outer-launch-receipt-{decision_id}.json"
    launch_receipt_path.write_bytes(builder.canonical_json_bytes({
        "schema": "synthetic_outer_launch_receipt_v1",
        "decision_id": decision_id,
    }))
    os.chmod(launch_receipt_path, 0o444)
    outer_process_argv = [source_python, "-I", "-B", "-S", "synthetic-preflight-v2"]
    package_builder_path = Path(package["builder_path"])
    auth: dict[str, Any] = {
        "schema": builder.AUTH_SCHEMA,
        "status": builder.AUTH_STATUS,
        "decision_id": decision_id,
        "final_root": os.fspath(final_root),
        "source_python": source_python,
        "source_python_sha256": sha(Path(source_python)),
        "source_bundle": {"path": os.fspath(V8), **builder.V8_BINDING},
        "source_site_packages": os.fspath(site),
        "source_inventory": inventory,
        "bindings": {
            "v8_scientific_independent_audit": dict(builder.V8_AUDIT_BINDING),
            "v1_builder": dict(builder.V1_BINDING),
            "v1_builder_independent_audit": copy.deepcopy(builder.V1_AUDIT_BINDING),
            "v10_package": package,
            "v10_builder_independent_audit": audit,
            "v9_builder_negative_independent_audit": (
                frozen_v9_negative_qa_binding()
            ),
            "v8_builder_negative_independent_audit": (
                frozen_v8_negative_qa_binding()
            ),
            "v7_builder_negative_independent_audit": (
                frozen_v7_negative_qa_binding()
            ),
        },
        "logical_builder_argv": [],
        "trusted_launch": {
            "schema": builder.HELD_BUILDER_LAUNCH_SCHEMA,
            "status": builder.HELD_BUILDER_LAUNCH_STATUS,
            "method": builder.HELD_BUILDER_LAUNCH_METHOD,
            "interpreter_fd": builder.HELD_INTERPRETER_FD,
            "builder_source_fd": builder.HELD_BUILDER_SOURCE_FD,
            "interpreter_proc_path": f"/proc/self/fd/{builder.HELD_INTERPRETER_FD}",
            "builder_source_proc_path": f"/proc/self/fd/{builder.HELD_BUILDER_SOURCE_FD}",
            "interpreter_fd_inheritable": True,
            "builder_source_fd_inheritable": False,
            "interpreter_identity": builder.Identity.from_stat(
                Path(source_python).stat()
            ).json(),
            "builder_source_identity": builder.Identity.from_stat(
                package_builder_path.stat()
            ).json(),
            "interpreter_sha256": sha(Path(source_python)),
            "builder_source_sha256": package["builder_sha256"],
            "builder_original_evidence_path": package["builder_path"],
            "outer_launch_receipt_path": os.fspath(launch_receipt_path),
            "outer_launch_receipt_sha256": sha(launch_receipt_path),
            "outer_process_argv": outer_process_argv,
            "outer_process_argv_sha256": builder.sha256_bytes(
                builder.canonical_json_bytes(outer_process_argv)
            ),
            "root_launch_authorization_path": os.fspath(root_launch_auth_path),
            "root_launch_authorization_sha256": sha(root_launch_auth_path),
            "preflight_package_manifest_path": os.fspath(
                preflight_manifest_path
            ),
            "preflight_package_manifest_sha256": sha(preflight_manifest_path),
            "preflight_package_index_path": os.fspath(
                preflight_package_index_path
            ),
            "preflight_package_index_sha256": sha(
                preflight_package_index_path
            ),
            "preflight_independent_audit_receipt_path": os.fspath(
                preflight_audit_receipt_path
            ),
            "preflight_independent_audit_receipt_sha256": sha(
                preflight_audit_receipt_path
            ),
            "preflight_independent_audit_index_path": os.fspath(
                preflight_audit_index_path
            ),
            "preflight_independent_audit_index_sha256": sha(
                preflight_audit_index_path
            ),
        },
        "journal": {
            "directory": os.fspath(journal_dir),
            "begin": os.fspath(journal_dir / builder.JOURNAL_NAMES["begin"]),
            "intent": os.fspath(journal_dir / builder.JOURNAL_NAMES["intent"]),
            "terminal": os.fspath(journal_dir / builder.JOURNAL_NAMES["terminal"]),
            "lock": os.fspath(journal_dir / builder.JOURNAL_NAMES["lock"]),
            "parent_path": os.fspath(parent),
            "parent_device": parent_info.st_dev,
            "parent_inode": parent_info.st_ino,
        },
        "scope": "RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY",
        "authority": {
            "transport_runtime_layout_authorized": True,
            "result_access_authorized": False,
            "signals_authorized": False,
            "controller_or_outer_main_authorized": False,
            "deployment_or_resume_authorized": False,
        },
    }
    launch = auth["trusted_launch"]
    outer_receipt = {
        "schema": builder.OUTER_LAUNCH_RECEIPT_SCHEMA,
        "status": builder.OUTER_LAUNCH_RECEIPT_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "decision_id": decision_id,
        "root_launch_authorization_path": launch[
            "root_launch_authorization_path"
        ],
        "root_launch_authorization_sha256": launch[
            "root_launch_authorization_sha256"
        ],
        "preflight_package_manifest_path": launch[
            "preflight_package_manifest_path"
        ],
        "preflight_package_manifest_sha256": launch[
            "preflight_package_manifest_sha256"
        ],
        "preflight_package_index_path": launch["preflight_package_index_path"],
        "preflight_package_index_sha256": launch[
            "preflight_package_index_sha256"
        ],
        "preflight_independent_audit_receipt_path": launch[
            "preflight_independent_audit_receipt_path"
        ],
        "preflight_independent_audit_receipt_sha256": launch[
            "preflight_independent_audit_receipt_sha256"
        ],
        "preflight_independent_audit_index_path": launch[
            "preflight_independent_audit_index_path"
        ],
        "preflight_independent_audit_index_sha256": launch[
            "preflight_independent_audit_index_sha256"
        ],
        "interpreter_fd": launch["interpreter_fd"],
        "builder_source_fd": launch["builder_source_fd"],
        "interpreter_identity": launch["interpreter_identity"],
        "builder_source_identity": launch["builder_source_identity"],
        "interpreter_sha256": launch["interpreter_sha256"],
        "builder_source_sha256": launch["builder_source_sha256"],
        "builder_original_evidence_path": launch[
            "builder_original_evidence_path"
        ],
        "outer_process_argv": launch["outer_process_argv"],
        "outer_process_argv_sha256": launch["outer_process_argv_sha256"],
        "scope": "TRUSTED_HELD_PREFLIGHT_BUILDER_LAUNCH_ONLY",
        "authority": {
            "builder_launch_authorized": True,
            "transport_runtime_layout_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "controller_or_outer_main_authorized": False,
            "deployment_or_resume_authorized": False,
        },
    }
    os.chmod(launch_receipt_path, 0o644)
    launch_receipt_path.write_bytes(builder.canonical_json_bytes(outer_receipt))
    os.chmod(launch_receipt_path, 0o444)
    launch["outer_launch_receipt_sha256"] = sha(launch_receipt_path)
    auth["logical_builder_argv"] = [
        source_python, "-I", "-B", "-S", package["builder_path"],
        "--source-bundle", os.fspath(V8),
        "--authorization", os.fspath(auth_path),
        "--trusted-authorization-sha256", builder.AUTH_SHA_ARGV_MARKER,
        "--execute", builder.EXECUTE_TEXT,
    ]
    auth_bytes = builder.canonical_json_bytes(auth)
    auth_sha = builder.sha256_bytes(auth_bytes)
    auth_path.write_bytes(auth_bytes)
    os.chmod(auth_path, 0o444)
    observed = [auth_sha if item == builder.AUTH_SHA_ARGV_MARKER else item
                for item in auth["logical_builder_argv"]]
    return auth, auth_path, auth_sha, observed


def create_interrupted_staging(
    temp: Path, site: Path, decision_id: str
) -> tuple[dict[str, Any], Path, str, list[str], Path, Path]:
    final = temp / f"final-{decision_id}"
    auth, auth_path, auth_sha, observed = make_authorization(
        temp, site, final, decision_id
    )
    journal = Path(auth["journal"]["directory"])

    def deny_fail_terminal() -> None:
        raise OSError("synthetic crash prevents FAIL terminal publication")

    def interrupt_before_publish() -> None:
        raise OSError("synthetic crash after durable intent before publish")

    try:
        execute_synthetic(
            auth,
            auth_path,
            auth_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=observed,
            before_rename_hook=interrupt_before_publish,
            fail_terminal_mid_write_hook=deny_fail_terminal,
            linux_integration="NOT_RUN_NON_LINUX",
        )
    except OSError:
        pass
    if (
        final.exists()
        or not (journal / builder.JOURNAL_NAMES["intent"]).is_file()
        or not (journal / builder.JOURNAL_NAMES["staging"]).is_dir()
        or (journal / builder.JOURNAL_NAMES["terminal"]).exists()
    ):
        raise AssertionError("failed to create exact interrupted staging fixture")
    return auth, auth_path, auth_sha, observed, journal, final


def rewrite_intent_core(journal: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    path = journal / builder.JOURNAL_NAMES["intent"]
    intent = builder.strict_json_loads(path.read_bytes())
    mutate(intent["core"])
    intent["core_digest"] = builder.sha256_bytes(
        builder.canonical_json_bytes(intent["core"])
    )
    os.chmod(path, 0o644)
    path.write_bytes(builder.canonical_json_bytes(intent))
    os.chmod(path, 0o444)


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="transport-runtime-v10-synthetic-") as raw:
        temp = Path(raw).resolve()
        site = temp / "site-packages"
        make_site(site)

        snapshot = builder.discover_source(site)
        try:
            inventory = snapshot.inventory()
            excluded = [
                {"distribution": name, "relative_path": path}
                for name in builder.COPY_DISTRIBUTIONS
                for path in snapshot.distributions[name].excluded_members
            ]
            checks["exact7_external_record_allowlist_accepted_and_evidenced"] = (
                len(excluded) == 7
                and inventory["external_record_exclusion_evidence"]
                == builder.external_record_exclusion_evidence()
                and all(
                    key in inventory["distributions"][name]
                    for name in builder.COPY_DISTRIBUTIONS
                    for key in (
                        "record_sha256", "metadata_sha256",
                        "safe_closure_digest", "member_snapshot_digest",
                    )
                )
            )
        finally:
            snapshot.close()

        api_production_parent = temp / "native-api-production-parent"
        api_production_parent.mkdir()
        api_work_root = temp / "native-api-work-root"
        api_work_root.mkdir()
        api_production_fd = os.open(
            api_production_parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        api_work_fd = os.open(
            api_work_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        api_original_expected_root = builder.EXPECTED_FINAL_ROOT
        api_request = {
            "schema": builder.NATIVE_COMPATIBILITY_API_SCHEMA,
            "scope": builder.NATIVE_COMPATIBILITY_API_SCOPE,
            "decision_id": builder.NATIVE_COMPATIBILITY_DECISION_ID,
            "authorization_sha256": "a" * 64,
            "compatibility_root": os.fspath(api_work_root / "compat_runtime_root"),
            "compatibility_journal": os.fspath(
                api_work_root / ".result-free-transport-v10.synthetic-native"
            ),
            "canonical_production_final_root_forbidden": os.fspath(
                api_production_parent / "ROOT"
            ),
            "canonical_production_journal_forbidden": os.fspath(
                api_production_parent
                / (
                    ".result-free-transport-v10."
                    + builder.NATIVE_COMPATIBILITY_DECISION_ID
                )
            ),
            "canonical_production_parent_forbidden": os.fspath(
                api_production_parent
            ),
            "publication_requirements": {
                "real_linux_renameat2_noreplace": True,
                "real_linux_xfs_otmpfile_procfd_linkat": True,
                "pathname_fallback_allowed": False,
            },
            "authority": {
                "not_production_build": True,
                "production_root_write_authorized": False,
                "result_access_authorized": False,
                "signals_authorized": False,
                "controller_or_resume_authorized": False,
            },
        }
        api_production_identity = builder.identity_fd(api_production_fd)
        api_work_identity = builder.identity_fd(api_work_fd)
        try:
            builder.EXPECTED_FINAL_ROOT = api_production_parent / "ROOT"
            api_internal_evidence = builder._execute_native_compatibility_probe_core(
                request=api_request,
                production_parent_fd=api_production_fd,
                compatibility_work_root_fd=api_work_fd,
                rename_impl=synthetic_rename_noreplace,
                terminal_publish_impl=synthetic_terminal_publish,
            )
            checks["scoped_native_api_private_core_noncanonical_fixture_passes"] = (
                set(api_internal_evidence) == {
                    "root_identity", "journal_identity", "terminal_identity",
                    "request_sha256",
                }
                and (api_work_root / "compat_runtime_root").is_dir()
                and (
                    api_work_root / ".result-free-transport-v10.synthetic-native"
                    / builder.NATIVE_COMPATIBILITY_TERMINAL_NAME
                ).is_file()
                and not (api_production_parent / "ROOT").exists()
                and not (
                    api_production_parent
                    / (
                        ".result-free-transport-v10."
                        + builder.NATIVE_COMPATIBILITY_DECISION_ID
                    )
                ).exists()
            )
            checks["scoped_native_api_borrowed_fds_remain_open_and_identical"] = (
                builder._same_directory_object(
                    builder.identity_fd(api_production_fd), api_production_identity
                )
                and builder._same_directory_object(
                    builder.identity_fd(api_work_fd), api_work_identity
                )
            )
            wrong_primitives_work = temp / "native-api-wrong-primitives-work"
            wrong_primitives_work.mkdir()
            wrong_primitives_fd = os.open(
                wrong_primitives_work,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            wrong_primitives_request = copy.deepcopy(api_request)
            wrong_primitives_request["compatibility_root"] = os.fspath(
                wrong_primitives_work / "compat_runtime_root"
            )
            wrong_primitives_request["compatibility_journal"] = os.fspath(
                wrong_primitives_work / ".compat-journal"
            )
            try:
                checks["scoped_native_api_rejects_nonexact_primitive_objects"] = (
                    rejected(lambda: (
                        builder.execute_scoped_noncanonical_native_compatibility_preflight_v1(
                            request=wrong_primitives_request,
                            production_parent_fd=api_production_fd,
                            compatibility_work_root_fd=wrong_primitives_fd,
                            rename_impl=synthetic_rename_noreplace,
                            terminal_publish_impl=synthetic_terminal_publish,
                        )
                    ))
                    and list(wrong_primitives_work.iterdir()) == []
                )
            finally:
                os.close(wrong_primitives_fd)
            alias_work = api_production_parent / "nested-work-root"
            alias_work.mkdir()
            alias_work_fd = os.open(
                alias_work, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            alias_request = copy.deepcopy(api_request)
            alias_request["compatibility_root"] = os.fspath(alias_work / "compat")
            alias_request["compatibility_journal"] = os.fspath(
                alias_work / ".compat-journal"
            )
            try:
                checks["scoped_native_api_rejects_work_root_under_production_parent"] = (
                    rejected(lambda: builder._validate_native_compatibility_request(
                        alias_request,
                        production_parent_fd=api_production_fd,
                        compatibility_work_root_fd=alias_work_fd,
                    ))
                    and list(alias_work.iterdir()) == []
                )
            finally:
                os.close(alias_work_fd)
            canonical_target_request = copy.deepcopy(api_request)
            canonical_target_request["compatibility_root"] = os.fspath(
                api_production_parent / "ROOT"
            )
            canonical_target_request["compatibility_journal"] = os.fspath(
                api_production_parent / ".other-journal"
            )
            checks["scoped_native_api_rejects_canonical_production_target"] = rejected(
                lambda: builder._validate_native_compatibility_request(
                    canonical_target_request,
                    production_parent_fd=api_production_fd,
                    compatibility_work_root_fd=api_production_fd,
                )
            )
            signature = inspect.signature(
                builder.execute_scoped_noncanonical_native_compatibility_preflight_v1
            )
            checks["scoped_native_api_exact_keyword_only_signature_and_schema"] = (
                list(signature.parameters) == [
                    "request", "production_parent_fd", "compatibility_work_root_fd",
                    "rename_impl", "terminal_publish_impl",
                ]
                and all(
                    item.kind is inspect.Parameter.KEYWORD_ONLY
                    for item in signature.parameters.values()
                )
                and builder.NATIVE_COMPATIBILITY_API_SCHEMA
                == "historical_200k_fixed10k_v10_scoped_native_compatibility_api_v1"
            )
        finally:
            builder.EXPECTED_FINAL_ROOT = api_original_expected_root
            os.close(api_work_fd)
            os.close(api_production_fd)

        terminal_race_dir = temp / "synthetic-terminal-atomic-race"
        terminal_race_dir.mkdir()
        terminal_race_fd = os.open(
            terminal_race_dir,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        terminal_payload = builder.canonical_json_bytes({
            "schema": "synthetic-terminal-v10",
            "status": "OWN_PAYLOAD_MUST_NOT_OVERWRITE",
        })
        foreign_payload = builder.canonical_json_bytes({
            "schema": "synthetic-terminal-v10",
            "status": "RACING_CANONICAL_MUST_SURVIVE",
        })

        def create_racer_then_atomic_noreplace(
            old_fd: int, old_name: str, new_fd: int, new_name: str
        ) -> None:
            racer_fd = os.open(
                new_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=new_fd,
            )
            try:
                offset = 0
                while offset < len(foreign_payload):
                    offset += os.write(racer_fd, foreign_payload[offset:])
                os.fchmod(racer_fd, 0o444)
                os.fsync(racer_fd)
            finally:
                os.close(racer_fd)
            synthetic_regular_rename_noreplace(
                old_fd, old_name, new_fd, new_name
            )

        try:
            atomic_race_rejected = rejected(lambda: (
                builder.publish_terminal_via_injected_complete_rename(
                    terminal_race_fd,
                    builder.JOURNAL_NAMES["terminal"],
                    terminal_payload,
                    rename_impl=create_racer_then_atomic_noreplace,
                )
            ))
            canonical_race_path = (
                terminal_race_dir / builder.JOURNAL_NAMES["terminal"]
            )
            checks["synthetic_terminal_atomic_noreplace_race_never_overwrites"] = (
                atomic_race_rejected
                and canonical_race_path.read_bytes() == foreign_payload
                and stat.S_IMODE(canonical_race_path.stat().st_mode) == 0o444
                and canonical_race_path.stat().st_nlink == 1
                and not any(
                    path.name.startswith(".TERMINAL.v10.complete.")
                    for path in terminal_race_dir.iterdir()
                )
            )
        finally:
            os.close(terminal_race_fd)

        unexpected = temp / "unexpected-external"
        make_site(unexpected)
        numpy_record = next(unexpected.glob("numpy-*.dist-info/RECORD"))
        numpy_record.write_text(
            numpy_record.read_text(encoding="utf-8") + "../../../bin/not-allowed,,\n",
            encoding="utf-8",
        )
        checks["eighth_external_record_member_rejected"] = rejected(
            lambda: builder.discover_source(unexpected)
        )

        missing = temp / "missing-external"
        make_site(missing)
        font_record = next(missing.glob("fonttools-*.dist-info/RECORD"))
        font_record.write_text(
            font_record.read_text(encoding="utf-8").replace("../../../bin/ttx,,\n", ""),
            encoding="utf-8",
        )
        checks["missing_one_of_exact7_rejected"] = rejected(
            lambda: builder.discover_source(missing)
        )

        mutation_site = temp / "member-mutation"
        make_site(mutation_site)
        member_snapshot = builder.discover_source(mutation_site)
        try:
            (mutation_site / DIST_MODULES["numpy"]).write_text("mutated=True\n", encoding="utf-8")
            checks["member_mutation_after_discovery_rejected"] = rejected(
                lambda: builder.revalidate_source(member_snapshot)
            )
        finally:
            member_snapshot.close()

        record_site = temp / "record-mutation"
        make_site(record_site)
        record_snapshot = builder.discover_source(record_site)
        try:
            record = next(record_site.glob("numpy-*.dist-info/RECORD"))
            record.write_text(record.read_text(encoding="utf-8") + "numpy/new.py,,\n", encoding="utf-8")
            write(record_site / "numpy/new.py", "new=True\n")
            checks["record_file_set_mutation_after_discovery_rejected"] = rejected(
                lambda: builder.revalidate_source(record_snapshot)
            )
        finally:
            record_snapshot.close()

        metadata_site = temp / "metadata-mutation"
        make_site(metadata_site)
        metadata_snapshot = builder.discover_source(metadata_site)
        try:
            metadata = next(metadata_site.glob("numpy-*.dist-info/METADATA"))
            metadata.write_text(metadata.read_text(encoding="utf-8") + "Summary: changed\n", encoding="utf-8")
            checks["metadata_mutation_after_discovery_rejected"] = rejected(
                lambda: builder.revalidate_source(metadata_snapshot)
            )
        finally:
            metadata_snapshot.close()

        checks["strict_json_duplicate_key_rejected"] = rejected(
            lambda: builder.strict_json_loads(b'{"a":1,"a":2}')
        )
        checks["strict_json_nan_rejected"] = rejected(
            lambda: builder.strict_json_loads(b'{"a":NaN}')
        )
        checks["direct_pathname_builder_main_fails_before_argument_or_path_use"] = rejected(
            builder.main
        )
        checks["builder_held_launch_contract_fixes_fd197_fd198_and_proc_paths"] = (
            builder.HELD_BUILDER_LAUNCH_CONTRACT
            == {
                "schema": builder.HELD_BUILDER_LAUNCH_SCHEMA,
                "status": builder.HELD_BUILDER_LAUNCH_STATUS,
                "method": builder.HELD_BUILDER_LAUNCH_METHOD,
                "interpreter_fd": 197,
                "builder_source_fd": 198,
                "interpreter_proc_path": "/proc/self/fd/197",
                "builder_source_proc_path": "/proc/self/fd/198",
                "interpreter_fd_inheritable": True,
                "builder_source_fd_inheritable": False,
            }
        )

        read_auth, read_path, read_sha, read_argv = make_authorization(
            temp, site, temp / "read-final", "read-auth-single-fd"
        )
        parsed, _ = builder.read_authorization_single_open(read_path, read_sha)
        checks["authorization_single_nofollow_fd_hash_and_parse_positive"] = parsed == read_auth
        production_parameters = set(
            inspect.signature(builder.execute_authorized).parameters
        )
        checks["production_entry_exposes_no_mode_token_or_injection_hooks"] = (
            production_parameters
            == {
                "auth", "authorization_path", "authorization_sha256",
                "held_context", "logical_process_argv", "authorization_lease",
            }
        )
        production_entry_calls: list[str] = []
        original_live_validator = builder.validate_live_held_builder_launch
        original_transaction_core = builder._execute_transaction_core

        def stop_after_live(*_args: Any, **_kwargs: Any) -> None:
            production_entry_calls.append("live")
            raise builder.BuildError("synthetic stop after mandatory live gate")

        def forbidden_core(*_args: Any, **_kwargs: Any) -> None:
            production_entry_calls.append("core")
            raise AssertionError("core reached before live gate")

        try:
            builder.validate_live_held_builder_launch = stop_after_live
            builder._execute_transaction_core = forbidden_core
            production_auth_lease = builder.FrozenFileLease.open(
                read_path, read_sha
            )
            entry_rejected = rejected(lambda: builder.execute_authorized(
                read_auth, read_path, read_sha,
                held_context=read_auth["trusted_launch"],
                logical_process_argv=read_argv,
                authorization_lease=production_auth_lease,
            ))
        finally:
            builder._execute_transaction_core = original_transaction_core
            builder.validate_live_held_builder_launch = original_live_validator
        checks["production_entry_runs_live_gate_before_transaction_core"] = (
            entry_rejected and production_entry_calls == ["live"]
        )
        fixed_target_auth = copy.deepcopy(read_auth)
        fixed_target_auth["final_root"] = os.fspath(builder.EXPECTED_FINAL_ROOT)
        fixed_target_auth["journal"]["parent_path"] = os.fspath(
            builder.EXPECTED_FINAL_ROOT.parent
        )
        fixed_target_auth["journal"]["directory"] = os.fspath(
            builder.EXPECTED_FINAL_ROOT.parent / ".result-free-transport-v10.synthetic"
        )
        checks["synthetic_entry_hard_rejects_fixed_production_root_tree"] = rejected(
            lambda: execute_synthetic(
                fixed_target_auth, read_path, read_sha,
                rename_impl=synthetic_rename_noreplace,
                observed_argv=read_argv,
            )
        )
        canonical_probe_parent = temp / "canonical-production-parent-probe"
        canonical_probe_parent.mkdir()
        alias_probe_parent = temp / "ancestor-symlink-alias-probe"
        alias_probe_parent.symlink_to(canonical_probe_parent, target_is_directory=True)
        alias_auth = copy.deepcopy(read_auth)
        alias_auth["final_root"] = os.fspath(alias_probe_parent / "ROOT")
        alias_auth["journal"]["parent_path"] = os.fspath(alias_probe_parent)
        alias_auth["journal"]["directory"] = os.fspath(
            alias_probe_parent / ".result-free-transport-v10.alias-probe"
        )
        original_expected_final_root = builder.EXPECTED_FINAL_ROOT
        try:
            builder.EXPECTED_FINAL_ROOT = canonical_probe_parent / "ROOT"
            checks[
                "synthetic_entry_rejects_ancestor_symlink_alias_to_canonical_parent"
            ] = rejected(lambda: builder._reject_synthetic_production_paths(
                alias_auth
            ))
        finally:
            builder.EXPECTED_FINAL_ROOT = original_expected_final_root

        def retarget_synthetic_parent(
            source: dict[str, Any], parent: Path, decision: str
        ) -> tuple[dict[str, Any], Path, Path]:
            value = copy.deepcopy(source)
            final = parent / f"synthetic-final-{decision}"
            journal_dir = parent / f".result-free-transport-v10.{decision}"
            value["final_root"] = os.fspath(final)
            value["journal"]["parent_path"] = os.fspath(parent)
            value["journal"]["directory"] = os.fspath(journal_dir)
            for key in ("begin", "intent", "terminal", "lock"):
                value["journal"][key] = os.fspath(
                    journal_dir / builder.JOURNAL_NAMES[key]
                )
            parent_info = parent.stat()
            value["journal"]["parent_device"] = parent_info.st_dev
            value["journal"]["parent_inode"] = parent_info.st_ino
            return value, final, journal_dir

        reverse_container = temp / "reverse-containment-broad-parent"
        reverse_container.mkdir()
        reverse_canonical_parent = (
            reverse_container / "canonical_parent" / "runtime"
        )
        reverse_canonical_parent.mkdir(parents=True)
        reverse_auth, reverse_final, reverse_journal = (
            retarget_synthetic_parent(
                read_auth, reverse_container, "reverse-containment"
            )
        )
        reverse_synthetic_fd = builder.open_directory_path(reverse_container)
        reverse_canonical_fd = builder.open_directory_path(
            reverse_canonical_parent
        )
        try:
            reverse_direction_reproduced = (
                not builder.directory_fd_is_at_or_below(
                    reverse_synthetic_fd,
                    builder.identity_fd(reverse_canonical_fd),
                )
                and builder.directory_fd_is_at_or_below(
                    reverse_canonical_fd,
                    builder.identity_fd(reverse_synthetic_fd),
                )
            )
        finally:
            os.close(reverse_canonical_fd)
            os.close(reverse_synthetic_fd)
        original_expected_final_root = builder.EXPECTED_FINAL_ROOT
        try:
            builder.EXPECTED_FINAL_ROOT = reverse_canonical_parent / "ROOT"
            reverse_rejected = rejected(lambda: execute_synthetic(
                reverse_auth, read_path, read_sha,
                rename_impl=synthetic_rename_noreplace,
                observed_argv=read_argv,
            ))
        finally:
            builder.EXPECTED_FINAL_ROOT = original_expected_final_root
        checks[
            "synthetic_entry_rejects_broad_parent_containing_canonical_before_write_or_lock"
        ] = (
            reverse_direction_reproduced
            and reverse_rejected
            and not reverse_final.exists()
            and not reverse_journal.exists()
        )

        equal_parent = temp / "equal-inode-parent"
        equal_parent.mkdir()
        equal_left_fd = builder.open_directory_path(equal_parent)
        equal_right_fd = builder.open_directory_path(equal_parent)
        try:
            checks[
                "bidirectional_inode_guard_rejects_equal_held_directory_inode"
            ] = rejected(lambda: builder.reject_bidirectional_directory_overlap(
                equal_left_fd,
                equal_right_fd,
                label="equal-inode-hostile",
            ))
        finally:
            os.close(equal_right_fd)
            os.close(equal_left_fd)

        disjoint_canonical_parent = temp / "legal-disjoint-canonical"
        disjoint_canonical_parent.mkdir()
        disjoint_synthetic_parent = temp / "legal-disjoint-synthetic"
        disjoint_synthetic_parent.mkdir()
        disjoint_auth, disjoint_final, disjoint_journal = (
            retarget_synthetic_parent(
                read_auth, disjoint_synthetic_parent, "legal-disjoint"
            )
        )
        disjoint_lease: builder.SyntheticProductionPathLease | None = None
        original_expected_final_root = builder.EXPECTED_FINAL_ROOT
        try:
            builder.EXPECTED_FINAL_ROOT = disjoint_canonical_parent / "ROOT"
            disjoint_lease = builder._reject_synthetic_production_paths(
                disjoint_auth
            )
            disjoint_lease.revalidate()
            checks[
                "synthetic_entry_accepts_held_componentwise_nofollow_disjoint_temp"
            ] = (
                builder._same_directory_object(
                    builder.identity_fd(disjoint_lease.synthetic_parent_fd),
                    disjoint_lease.synthetic_parent_identity,
                )
                and disjoint_lease.canonical_parent_fd >= 0
                and not disjoint_final.exists()
                and not disjoint_journal.exists()
            )
        finally:
            if disjoint_lease is not None:
                disjoint_lease.close()
            builder.EXPECTED_FINAL_ROOT = original_expected_final_root

        canonical_real_parent = temp / "canonical-real-parent"
        canonical_real_parent.mkdir()
        canonical_symlink_parent = temp / "canonical-ancestor-symlink"
        canonical_symlink_parent.symlink_to(
            canonical_real_parent, target_is_directory=True
        )
        symlink_synthetic_parent = temp / "canonical-symlink-disjoint"
        symlink_synthetic_parent.mkdir()
        canonical_symlink_auth, _, canonical_symlink_journal = (
            retarget_synthetic_parent(
                read_auth, symlink_synthetic_parent, "canonical-symlink"
            )
        )
        original_expected_final_root = builder.EXPECTED_FINAL_ROOT
        try:
            builder.EXPECTED_FINAL_ROOT = canonical_symlink_parent / "ROOT"
            canonical_symlink_rejected = rejected(
                lambda: builder._reject_synthetic_production_paths(
                    canonical_symlink_auth
                )
            )
        finally:
            builder.EXPECTED_FINAL_ROOT = original_expected_final_root
        checks[
            "synthetic_entry_rejects_canonical_parent_ancestor_symlink_nofollow"
        ] = canonical_symlink_rejected and not canonical_symlink_journal.exists()

        rename_canonical_parent = temp / "rename-canonical-parent"
        rename_canonical_parent.mkdir()
        rename_canonical_moved = temp / "rename-canonical-parent-moved"
        rename_synthetic_parent = temp / "rename-canonical-synthetic"
        rename_synthetic_parent.mkdir()
        rename_canonical_auth, rename_canonical_final, rename_canonical_journal = (
            retarget_synthetic_parent(
                read_auth, rename_synthetic_parent, "rename-canonical"
            )
        )

        def rename_canonical_to_alias_after_lease() -> None:
            rename_canonical_parent.rename(rename_canonical_moved)
            rename_canonical_parent.symlink_to(
                rename_canonical_moved, target_is_directory=True
            )

        original_expected_final_root = builder.EXPECTED_FINAL_ROOT
        try:
            builder.EXPECTED_FINAL_ROOT = rename_canonical_parent / "ROOT"
            rename_canonical_rejected = rejected(lambda: execute_synthetic(
                rename_canonical_auth, read_path, read_sha,
                rename_impl=synthetic_rename_noreplace,
                observed_argv=read_argv,
                after_path_separation_hook=(
                    rename_canonical_to_alias_after_lease
                ),
            ))
        finally:
            builder.EXPECTED_FINAL_ROOT = original_expected_final_root
        checks[
            "synthetic_entry_rejects_canonical_rename_to_alias_after_held_admission_before_write"
        ] = (
            rename_canonical_rejected
            and not rename_canonical_final.exists()
            and not rename_canonical_journal.exists()
        )

        rename_synthetic_canonical = temp / "rename-synthetic-canonical"
        rename_synthetic_canonical.mkdir()
        rename_synthetic_parent = temp / "rename-synthetic-parent"
        rename_synthetic_parent.mkdir()
        rename_synthetic_moved = temp / "rename-synthetic-parent-moved"
        rename_synthetic_auth, rename_synthetic_final, rename_synthetic_journal = (
            retarget_synthetic_parent(
                read_auth, rename_synthetic_parent, "rename-synthetic"
            )
        )

        def rename_synthetic_to_alias_after_lease() -> None:
            rename_synthetic_parent.rename(rename_synthetic_moved)
            rename_synthetic_parent.symlink_to(
                rename_synthetic_moved, target_is_directory=True
            )

        original_expected_final_root = builder.EXPECTED_FINAL_ROOT
        try:
            builder.EXPECTED_FINAL_ROOT = rename_synthetic_canonical / "ROOT"
            rename_synthetic_rejected = rejected(lambda: execute_synthetic(
                rename_synthetic_auth, read_path, read_sha,
                rename_impl=synthetic_rename_noreplace,
                observed_argv=read_argv,
                after_path_separation_hook=(
                    rename_synthetic_to_alias_after_lease
                ),
            ))
        finally:
            builder.EXPECTED_FINAL_ROOT = original_expected_final_root
        checks[
            "synthetic_entry_rejects_synthetic_rename_to_alias_after_held_admission_before_write"
        ] = (
            rename_synthetic_rejected
            and not rename_synthetic_final.exists()
            and not rename_synthetic_journal.exists()
            and not (rename_synthetic_moved / rename_synthetic_final.name).exists()
            and not (rename_synthetic_moved / rename_synthetic_journal.name).exists()
        )

        root_binding_mismatch = copy.deepcopy(read_auth)
        original_root_auth_path = Path(
            root_binding_mismatch["trusted_launch"][
                "root_launch_authorization_path"
            ]
        )
        mismatched_root_auth_path = temp / "mismatched-root-launch.json"
        mismatched_root_value = builder.strict_json_loads(
            original_root_auth_path.read_bytes()
        )
        mismatched_root_value["preflight_package_manifest_sha256"] = sha(
            Path(root_binding_mismatch["trusted_launch"][
                "preflight_package_index_path"
            ])
        )
        mismatched_root_auth_path.write_bytes(
            builder.canonical_json_bytes(mismatched_root_value)
        )
        os.chmod(mismatched_root_auth_path, 0o444)
        root_binding_mismatch["trusted_launch"][
            "root_launch_authorization_path"
        ] = os.fspath(mismatched_root_auth_path)
        root_binding_mismatch["trusted_launch"][
            "root_launch_authorization_sha256"
        ] = sha(mismatched_root_auth_path)
        checks[
            "self_hashed_root_launch_anchor_mismatch_is_cross_binding_rejected"
        ] = rejected(lambda: builder.validate_dynamic_preflight_anchor_files(
            root_binding_mismatch
        ))

        dynamic_launch = read_auth["trusted_launch"]
        dynamic_package_dir = Path(
            dynamic_launch["preflight_package_manifest_path"]
        ).parent
        dynamic_audit_dir = Path(
            dynamic_launch["preflight_independent_audit_receipt_path"]
        ).parent
        dynamic_package_index, dynamic_package_members = (
            builder.read_exact_frozen_index_closure(
                dynamic_package_dir,
                index_path=Path(dynamic_launch["preflight_package_index_path"]),
                index_sha256=dynamic_launch["preflight_package_index_sha256"],
                expected_top_names=builder.PREFLIGHT_V2_PREPARED_TOP_NAMES,
                expected_index_names=builder.PREFLIGHT_V2_PREPARED_INDEX_NAMES,
                label="synthetic dynamic preflight v2 prepared package",
            )
        )
        dynamic_audit_index, dynamic_audit_members = (
            builder.read_exact_frozen_index_closure(
                dynamic_audit_dir,
                index_path=Path(
                    dynamic_launch["preflight_independent_audit_index_path"]
                ),
                index_sha256=dynamic_launch[
                    "preflight_independent_audit_index_sha256"
                ],
                expected_top_names=builder.PREFLIGHT_V2_QA_TOP_NAMES,
                expected_index_names=builder.PREFLIGHT_V2_QA_INDEX_NAMES,
                label="synthetic dynamic preflight v2 independent QA package",
            )
        )

        def validate_dynamic_semantics(
            package_index: dict[str, str],
            package_members: dict[str, bytes],
            audit_index: dict[str, str],
            audit_members: dict[str, bytes],
        ) -> None:
            builder.validate_preflight_v2_dynamic_closure_semantics(
                package_directory=dynamic_package_dir,
                package_index_sha256=dynamic_launch[
                    "preflight_package_index_sha256"
                ],
                package_index=package_index,
                package_members=package_members,
                audit_directory=dynamic_audit_dir,
                audit_index=audit_index,
                audit_members=audit_members,
            )

        checks["dynamic_preflight_v2_exact16_member_deep_semantics_positive"] = (
            not rejected(lambda: validate_dynamic_semantics(
                dynamic_package_index,
                dynamic_package_members,
                dynamic_audit_index,
                dynamic_audit_members,
            ))
            and len(dynamic_package_index) == 9
            and len(dynamic_audit_index) == 7
        )
        package_status_members = dict(dynamic_package_members)
        package_status_index = dict(dynamic_package_index)
        package_status_manifest = builder.strict_json_loads(
            package_status_members["BUNDLE_MANIFEST.json"]
        )
        package_status_manifest["status"] = "SELF_HASHED_BUT_NOT_PREPARED_STATUS"
        package_status_members["BUNDLE_MANIFEST.json"] = builder.canonical_json_bytes(
            package_status_manifest
        )
        package_status_index["BUNDLE_MANIFEST.json"] = builder.sha256_bytes(
            package_status_members["BUNDLE_MANIFEST.json"]
        )
        checks["dynamic_preflight_v2_self_hashed_wrong_prepared_status_rejected"] = (
            rejected(lambda: validate_dynamic_semantics(
                package_status_index,
                package_status_members,
                dynamic_audit_index,
                dynamic_audit_members,
            ))
        )
        qa_authority_members = dict(dynamic_audit_members)
        qa_authority_index = dict(dynamic_audit_index)
        qa_authority_receipt = builder.strict_json_loads(
            qa_authority_members["INDEPENDENT_QA_RECEIPT.json"]
        )
        qa_authority_receipt["authority"]["mars_access_authorized"] = True
        qa_authority_members["INDEPENDENT_QA_RECEIPT.json"] = (
            builder.canonical_json_bytes(qa_authority_receipt)
        )
        qa_authority_index["INDEPENDENT_QA_RECEIPT.json"] = builder.sha256_bytes(
            qa_authority_members["INDEPENDENT_QA_RECEIPT.json"]
        )
        checks["dynamic_preflight_v2_self_hashed_true_authority_rejected"] = rejected(
            lambda: validate_dynamic_semantics(
                dynamic_package_index,
                dynamic_package_members,
                qa_authority_index,
                qa_authority_members,
            )
        )
        qa_finding_members = dict(dynamic_audit_members)
        qa_finding_index = dict(dynamic_audit_index)
        qa_finding_receipt = builder.strict_json_loads(
            qa_finding_members["INDEPENDENT_QA_RECEIPT.json"]
        )
        qa_finding_receipt["finding_counts"]["P1"] = 1
        qa_finding_members["INDEPENDENT_QA_RECEIPT.json"] = (
            builder.canonical_json_bytes(qa_finding_receipt)
        )
        qa_finding_index["INDEPENDENT_QA_RECEIPT.json"] = builder.sha256_bytes(
            qa_finding_members["INDEPENDENT_QA_RECEIPT.json"]
        )
        checks["dynamic_preflight_v2_self_hashed_nonzero_finding_rejected"] = rejected(
            lambda: validate_dynamic_semantics(
                dynamic_package_index,
                dynamic_package_members,
                qa_finding_index,
                qa_finding_members,
            )
        )
        extra_package_index = dict(dynamic_package_index)
        extra_package_members = dict(dynamic_package_members)
        extra_package_members["UNBOUND_EXTRA.txt"] = b"unbound\n"
        extra_package_index["UNBOUND_EXTRA.txt"] = builder.sha256_bytes(b"unbound\n")
        checks["dynamic_preflight_v2_extra_self_hashed_member_rejected"] = rejected(
            lambda: validate_dynamic_semantics(
                extra_package_index,
                extra_package_members,
                dynamic_audit_index,
                dynamic_audit_members,
            )
        )

        v9_negative_binding = frozen_v9_negative_qa_binding()
        checks[
            "formal_v9_negative_qa_exact10_index8_receipt_report_manifest_and_p1_bound"
        ] = (
            not rejected(lambda: builder.validate_v9_negative_audit_binding(
                v9_negative_binding, verify_bytes=True
            ))
            and len([
                key for key in v9_negative_binding if key.endswith("_path")
            ]) == 10
            and all(
                v9_negative_binding[f"{stem}_sha256"] == item["sha256"]
                for stem, item in builder.V9_NEGATIVE_QA_FILE_BINDINGS.items()
            )
            and v9_negative_binding["finding_counts"]
            == {"P0": 0, "P1": 1, "P2": 0, "P3": 0}
        )
        v9_binding_tampers = []
        for stem in ("receipt", "report", "sha256_index"):
            tampered = copy.deepcopy(v9_negative_binding)
            tampered[f"{stem}_sha256"] = builder.sha256_bytes(
                f"wrong-v9-{stem}".encode("utf-8")
            )
            v9_binding_tampers.append(rejected(
                lambda value=tampered: builder.validate_v9_negative_audit_binding(
                    value, verify_bytes=True
                )
            ))
        v9_receipt_value = builder.strict_json_loads(
            Path(v9_negative_binding["receipt_path"]).read_bytes()
        )
        v9_finding_tamper = copy.deepcopy(v9_receipt_value)
        v9_finding_tamper["findings"][0]["affected_paths"].append(
            "unfrozen_extra_path"
        )
        v9_binding_tampers.append(rejected(
            lambda: builder.validate_v9_negative_audit_receipt_semantics(
                v9_finding_tamper, audit_binding=v9_negative_binding
            )
        ))
        checks[
            "formal_v9_negative_qa_receipt_report_index_and_finding_tamper_rejected"
        ] = v9_binding_tampers == [True, True, True, True]

        v8_negative_binding = frozen_v8_negative_qa_binding()
        checks[
            "formal_v8_negative_qa_all14_exact_bytes_index_receipt_manifest_and_p0_bound"
        ] = (
            not rejected(lambda: builder.validate_v8_negative_audit_binding(
                v8_negative_binding, verify_bytes=True
            ))
            and len([
                key for key in v8_negative_binding if key.endswith("_path")
            ]) == 14
            and all(
                v8_negative_binding[f"{stem}_sha256"] == item["sha256"]
                for stem, item in builder.V8_NEGATIVE_QA_FILE_BINDINGS.items()
            )
            and v8_negative_binding["finding_counts"]
            == {"P0": 1, "P1": 0, "P2": 0, "P3": 0}
        )
        v8_negative_tamper = copy.deepcopy(v8_negative_binding)
        v8_negative_tamper["output_sha256"] = v8_negative_binding[
            "command_log_sha256"
        ]
        checks["formal_v8_negative_qa_one_of14_tamper_rejected"] = rejected(
            lambda: builder.validate_v8_negative_audit_binding(
                v8_negative_tamper, verify_bytes=True
            )
        )

        v7_negative_binding = frozen_v7_negative_qa_binding()
        checks["formal_v7_negative_qa_all8_exact_bytes_and_index_bound"] = (
            not rejected(lambda: builder.validate_v7_negative_audit_binding(
                v7_negative_binding, verify_bytes=True
            ))
            and len([
                key for key in v7_negative_binding if key.endswith("_path")
            ]) == 8
            and all(
                v7_negative_binding[f"{stem}_sha256"]
                == builder.V7_NEGATIVE_QA_BINDING[f"{stem}_sha256"]
                for stem in (
                    "bundle_manifest", "log", "output", "receipt", "report",
                    "closure", "harness", "sha256_index",
                )
            )
        )
        v7_negative_tamper = copy.deepcopy(v7_negative_binding)
        v7_negative_tamper["output_sha256"] = sha(Path(
            v7_negative_tamper["log_path"]
        ))
        checks["formal_v7_negative_qa_one_of8_tamper_rejected"] = rejected(
            lambda: builder.validate_v7_negative_audit_binding(
                v7_negative_tamper, verify_bytes=True
            )
        )

        swap_rejections: list[bool] = []
        swap_targets = [
            read_path,
            Path(read_auth["trusted_launch"]["outer_launch_receipt_path"]),
            Path(read_auth["bindings"]["v10_package"]["builder_path"]),
            Path(read_auth["bindings"]["v10_builder_independent_audit"][
                "receipt_path"
            ]),
        ]
        for index, target in enumerate(swap_targets):
            trust_lease = builder.ProductionTrustLease.open(
                read_auth, read_path, read_sha
            )
            try:
                original_bytes = target.read_bytes()
                backup = target.with_name(f".{target.name}.held-backup-{index}")
                os.rename(target, backup)
                try:
                    target.write_bytes(original_bytes)
                    os.chmod(target, 0o444)
                    swap_rejections.append(rejected(
                        lambda: trust_lease.revalidate(read_auth)
                    ))
                finally:
                    target.unlink(missing_ok=True)
                    os.rename(backup, target)
            finally:
                trust_lease.close()
        checks[
            "trust_lease_rejects_identical_byte_inode_swap_for_auth_outer_package_audit"
        ] = swap_rejections == [True, True, True, True]
        replacement = temp / "replacement-auth.json"

        def replace_auth_path() -> None:
            read_path.rename(temp / "original-auth-held-open.json")
            replacement.write_bytes(builder.canonical_json_bytes(read_auth))
            replacement.rename(read_path)

        checks["authorization_path_replacement_after_single_fd_read_rejected"] = rejected(
            lambda: builder.read_authorization_single_open(
                read_path, read_sha, after_read_hook=replace_auth_path
            )
        )

        wrong_type = copy.deepcopy(read_auth)
        wrong_type["journal"]["parent_inode"] = True
        checks["authorization_bool_for_integer_type_rejected"] = rejected(
            lambda: builder.validate_authorization_payload(
                wrong_type, read_path, read_sha, read_argv, enforce_fixed=False
            )
        )
        wrong_argv = list(read_argv)
        wrong_argv[-1] += "x"
        checks[
            "authorization_exact_logical_builder_argv_rejected_on_one_byte_change"
        ] = rejected(
            lambda: builder.validate_authorization_payload(
                read_auth, read_path, read_sha, wrong_argv, enforce_fixed=False
            )
        )
        wrong_launch_fd = copy.deepcopy(read_auth)
        wrong_launch_fd["trusted_launch"]["builder_source_fd"] = 199
        checks["trusted_launch_wrong_fixed_builder_fd_rejected"] = rejected(
            lambda: builder.validate_authorization_payload(
                wrong_launch_fd, read_path, read_sha, read_argv,
                enforce_fixed=False,
            )
        )
        wrong_launch_inheritance = copy.deepcopy(read_auth)
        wrong_launch_inheritance["trusted_launch"][
            "builder_source_fd_inheritable"
        ] = True
        checks["trusted_launch_builder_fd_leak_policy_rejected"] = rejected(
            lambda: builder.validate_authorization_payload(
                wrong_launch_inheritance, read_path, read_sha, read_argv,
                enforce_fixed=False,
            )
        )
        wrong_launch_argv = copy.deepcopy(read_auth)
        wrong_launch_argv["trusted_launch"]["outer_process_argv"].append("tamper")
        checks["trusted_launch_outer_argv_digest_tamper_rejected"] = rejected(
            lambda: builder.validate_authorization_payload(
                wrong_launch_argv, read_path, read_sha, read_argv,
                enforce_fixed=False,
            )
        )
        wrong_launch_mode = copy.deepcopy(read_auth)
        wrong_launch_mode["trusted_launch"]["builder_source_identity"]["mode"] = "0644"
        checks["trusted_launch_unfrozen_builder_source_mode_rejected"] = rejected(
            lambda: builder.validate_authorization_payload(
                wrong_launch_mode, read_path, read_sha, read_argv,
                enforce_fixed=False,
            )
        )
        missing_smoke_test = copy.deepcopy(read_auth)
        del missing_smoke_test["bindings"]["v10_package"]["smoke_test_path"]
        del missing_smoke_test["bindings"]["v10_package"]["smoke_test_sha256"]
        checks["v10_package_binding_requires_smoke_test_bytes"] = rejected(
            lambda: builder.validate_authorization_payload(
                missing_smoke_test, read_path, read_sha, read_argv,
                enforce_fixed=False,
            )
        )
        outer_receipt_value = builder.strict_json_loads(Path(
            read_auth["trusted_launch"]["outer_launch_receipt_path"]
        ).read_bytes())
        forged_outer_semantics = copy.deepcopy(outer_receipt_value)
        forged_outer_semantics["authority"][
            "transport_runtime_layout_authorized"
        ] = True
        checks[
            "self_hashed_outer_launch_receipt_cannot_expand_transport_authority"
        ] = rejected(lambda: builder.validate_outer_launch_receipt_semantics(
            forged_outer_semantics, auth=read_auth
        ))
        audit_receipt_value = builder.strict_json_loads(Path(
            read_auth["bindings"]["v10_builder_independent_audit"]["receipt_path"]
        ).read_bytes())
        forged_audit_semantics = copy.deepcopy(audit_receipt_value)
        forged_audit_semantics["finding_counts"]["P1"] = 1
        checks[
            "self_hashed_v10_qa_receipt_with_finding_cannot_authorize"
        ] = rejected(lambda: builder.validate_v10_audit_receipt_semantics(
            forged_audit_semantics,
            audit_binding=read_auth["bindings"]["v10_builder_independent_audit"],
            package_binding=read_auth["bindings"]["v10_package"],
        ))
        audit_binding = read_auth["bindings"]["v10_builder_independent_audit"]
        builder.validate_bound_files(
            audit_binding, builder.V10_AUDIT_BINDING_KEYS,
            "synthetic_v10_audit", verify_bytes=True,
        )
        audit_receipt = Path(audit_binding["receipt_path"])
        os.chmod(audit_receipt, 0o644)
        audit_receipt.write_text("tampered audit receipt\n", encoding="utf-8")
        os.chmod(audit_receipt, 0o444)
        checks["v10_independent_audit_receipt_byte_mutation_rejected"] = rejected(
            lambda: builder.validate_bound_files(
                audit_binding, builder.V10_AUDIT_BINDING_KEYS,
                "synthetic_v10_audit", verify_bytes=True,
            )
        )
        outer_launch_receipt = Path(
            read_auth["trusted_launch"]["outer_launch_receipt_path"]
        )
        os.chmod(outer_launch_receipt, 0o644)
        outer_launch_receipt.write_text(
            '{"schema":"tampered_outer_launch_receipt"}\n', encoding="utf-8"
        )
        os.chmod(outer_launch_receipt, 0o444)
        checks["outer_launch_receipt_byte_mutation_rejected"] = rejected(
            lambda: builder.read_authorization_single_open(
                outer_launch_receipt,
                read_auth["trusted_launch"]["outer_launch_receipt_sha256"],
            )
        )

        live_source = inspect.getsource(builder.validate_live_held_builder_launch)
        checks["held_builder_fds_are_readonly_regular_singlelink_and_bounded"] = (
            "fcntl.F_GETFL" in live_source
            and "os.O_ACCMODE != os.O_RDONLY" in live_source
            and "require_regular_fd(fd, label)" in live_source
            and "HELD_SOURCE_READ_LIMIT_BYTES" in live_source
        )
        checks["live_gate_reopens_interpreter_package_audit_and_launch_paths"] = (
            "os.path.realpath(auth[\"source_python\"])" in live_source
            and "validate_bound_files(" in live_source
            and "validate_v10_audit_receipt_semantics(" in live_source
            and "validate_v9_negative_audit_binding(" in live_source
            and "validate_outer_launch_receipt_semantics(" in live_source
        )
        checks["builder_receipt_binds_exact_held_smoke_bootstrap"] = (
            builder.V10_SMOKE_BOOTSTRAP_SHA256
            == "a38e950b705e12cb07c30148a7e2fedf5b60e6c17c8d49a21984973cda1a34b4"
            and builder.V10_SMOKE_BOOTSTRAP_SIZE_BYTES == 12667
        )

        final = temp / "fixed-root"
        auth, auth_path, auth_sha, observed_argv = make_authorization(
            temp, site, final, "recovery-transaction"
        )
        journal = Path(auth["journal"]["directory"])
        intent_seen_before_rename = False

        def before_rename() -> None:
            nonlocal intent_seen_before_rename
            intent = journal / builder.JOURNAL_NAMES["intent"]
            intent_seen_before_rename = intent.is_file()
            if intent_seen_before_rename:
                json.loads(intent.read_text(encoding="utf-8"))

        terminal_failure_seen = False
        partial_noncanonical_seen = False

        def fail_terminal_once() -> None:
            nonlocal terminal_failure_seen, partial_noncanonical_seen
            terminal_failure_seen = True
            temporary = [
                path for path in journal.iterdir()
                if path.name.startswith(".TERMINAL.v10.complete.")
            ]
            partial_noncanonical_seen = (
                not (journal / builder.JOURNAL_NAMES["terminal"]).exists()
                and len(temporary) == 1
                and temporary[0].stat().st_size > 0
                and stat.S_IMODE(temporary[0].stat().st_mode) == 0o600
            )
            raise OSError("synthetic terminal interruption after publish")

        first_failed = False
        try:
            execute_synthetic(
                auth, auth_path, auth_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=observed_argv,
                before_rename_hook=before_rename,
                terminal_mid_write_hook=fail_terminal_once,
                linux_integration="NOT_RUN_NON_LINUX",
            )
        except OSError:
            first_failed = True
        checks["mid_terminal_write_has_no_canonical_path_and_intent_is_durable"] = (
            first_failed and intent_seen_before_rename and final.is_dir()
            and partial_noncanonical_seen
            and not (journal / builder.JOURNAL_NAMES["terminal"]).exists()
            and not any(
                path.name.startswith(".TERMINAL.v10.complete.")
                for path in journal.iterdir()
            )
        )
        recovered = execute_synthetic(
            auth, auth_path, auth_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=observed_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        terminal_path = journal / builder.JOURNAL_NAMES["terminal"]
        receipt_bytes = terminal_path.read_bytes()
        receipt = builder.strict_json_loads(receipt_bytes)
        builder.validate_build_pass_receipt_schema(receipt)
        details["positive_receipt"] = receipt
        checks["published_without_terminal_recovers_exact_pass_receipt"] = (
            recovered["status"] == "RECOVERED_PASS_FROM_DURABLE_COMMIT_INTENT"
            and terminal_failure_seen
            and receipt == recovered["receipt"]
            and stat.S_IMODE(terminal_path.stat().st_mode) == 0o444
            and terminal_path.stat().st_nlink == 1
            and receipt["journal"]["terminal_publication_method"]
            == builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
            and receipt["journal"]["terminal_canonical_visibility_rule"]
            == builder.TERMINAL_CANONICAL_VISIBILITY_RULE
        )
        checks["pass_receipt_exact_schema_no_self_reference"] = (
            set(receipt) == {
                "schema", "status", "created_utc", "decision_id", "authorization",
                "journal", "publication", "runtime", "bound_v8", "source_runtime",
                "support_files", "external_record_exclusions", "package_binding",
                "trusted_launch", "scope",
            }
            and "terminal_sha256" not in receipt["journal"]
            and receipt["external_record_exclusions"]["count"] == 7
            and receipt["authorization"]["logical_builder_argv"] == observed_argv
            and receipt["trusted_launch"] == auth["trusted_launch"]
            and receipt["package_binding"]["v10_smoke_test_sha256"]
            == auth["bindings"]["v10_package"]["smoke_test_sha256"]
        )
        checks["final_inode_tree_manifest_private_and_v8_bound_in_receipt"] = (
            receipt["publication"]["final_inode_equals_staging"] is True
            and receipt["publication"]["final_root_inode"]
            == receipt["publication"]["staging_inode"]
            and receipt["runtime"]["files_only_private_root_digest"]
            == receipt["runtime"]["files_only_runtime_root_digest"]
            and receipt["publication"]["files_only_full_root_digest"]
            != receipt["publication"]["structural_full_root_digest"]
            and receipt["runtime"]["bundle_root_path"]
            == os.fspath(final / "bundle")
            and receipt["runtime"]["bundle_root_inode"]
            == (final / "bundle").stat().st_ino
            and set(receipt["support_files"]) == set(builder.SUPPORT_FILES)
            and all(
                receipt["support_files"][name]["inode"]
                == (final / name).stat().st_ino
                for name in builder.SUPPORT_FILES
            )
            and receipt["bound_v8"]["prepared_receipt_sha256"]
            == builder.V8_BINDING["receipt_sha256"]
            and receipt["bound_v8"]["bundle_manifest_sha256"]
            == builder.V8_BINDING["bundle_manifest_sha256"]
            and receipt["bound_v8"]["sha256_index_sha256"]
            == builder.V8_BINDING["sha256_index_sha256"]
        )
        checks["darwin_linux_integration_explicitly_not_run"] = (
            sys.platform.startswith("linux")
            or receipt["scope"]["linux_integration"] == "NOT_RUN_NON_LINUX"
        )
        checks["final_exact_six_children_and_frozen_modes"] = (
            {path.name for path in final.iterdir()} == builder.ROOT_CHILDREN
            and all(
                (stat.S_IMODE(path.lstat().st_mode) == 0o555 if path.is_dir()
                 else stat.S_IMODE(path.lstat().st_mode) == 0o444
                 and path.lstat().st_nlink == 1)
                for path in (final, *final.rglob("*"))
            )
        )

        after_link_present_final = temp / "after-link-present-final"
        (
            after_link_present_auth,
            after_link_present_auth_path,
            after_link_present_sha,
            after_link_present_argv,
        ) = make_authorization(
            temp,
            site,
            after_link_present_final,
            "after-link-before-dir-fsync-present",
        )
        after_link_present_journal = Path(
            after_link_present_auth["journal"]["directory"]
        )
        linked_complete_seen = False

        # The callback needs a held directory FD without leaking it.
        def after_complete_link_present_hook() -> None:
            nonlocal linked_complete_seen
            journal_fd = os.open(
                after_link_present_journal,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                value, _, identity, raw = builder.read_canonical_terminal_at(
                    journal_fd, builder.JOURNAL_NAMES["terminal"]
                )
                linked_complete_seen = (
                    value["status"] == builder.BUILD_PASS_RECEIPT_STATUS
                    and identity.mode == 0o444
                    and identity.nlink == 1
                    and raw == builder.canonical_json_bytes(value)
                )
            finally:
                os.close(journal_fd)
            raise OSError(
                "synthetic crash after complete link before directory fsync"
            )

        present_interrupted = False
        try:
            execute_synthetic(
                after_link_present_auth,
                after_link_present_auth_path,
                after_link_present_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=after_link_present_argv,
                terminal_after_link_before_dir_fsync_hook=(
                    after_complete_link_present_hook
                ),
                linux_integration="NOT_RUN_NON_LINUX",
            )
        except OSError:
            present_interrupted = True
        present_terminal_path = (
            after_link_present_journal / builder.JOURNAL_NAMES["terminal"]
        )
        present_bytes_before_recovery = present_terminal_path.read_bytes()
        present_recovered = execute_synthetic(
            after_link_present_auth,
            after_link_present_auth_path,
            after_link_present_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=after_link_present_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        checks["after_link_before_dir_fsync_present_state_recovers_exact_pass"] = (
            present_interrupted
            and linked_complete_seen
            and present_recovered["status"] == "ALREADY_TERMINAL_PASS"
            and present_terminal_path.read_bytes() == present_bytes_before_recovery
            and stat.S_IMODE(present_terminal_path.stat().st_mode) == 0o444
            and present_terminal_path.stat().st_nlink == 1
        )

        after_link_absent_final = temp / "after-link-absent-final"
        (
            after_link_absent_auth,
            after_link_absent_auth_path,
            after_link_absent_sha,
            after_link_absent_argv,
        ) = make_authorization(
            temp,
            site,
            after_link_absent_final,
            "after-link-before-dir-fsync-absent",
        )
        after_link_absent_journal = Path(
            after_link_absent_auth["journal"]["directory"]
        )
        unlinked_complete_terminal = False

        def simulate_unpersisted_link() -> None:
            nonlocal unlinked_complete_terminal
            terminal = (
                after_link_absent_journal / builder.JOURNAL_NAMES["terminal"]
            )
            unlinked_complete_terminal = (
                terminal.is_file()
                and stat.S_IMODE(terminal.stat().st_mode) == 0o444
            )
            terminal.unlink()
            raise OSError("synthetic crash state where unfsynced link is absent")

        absent_interrupted = False
        try:
            execute_synthetic(
                after_link_absent_auth,
                after_link_absent_auth_path,
                after_link_absent_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=after_link_absent_argv,
                terminal_after_link_before_dir_fsync_hook=simulate_unpersisted_link,
                linux_integration="NOT_RUN_NON_LINUX",
            )
        except OSError:
            absent_interrupted = True
        absent_before_recovery = not (
            after_link_absent_journal / builder.JOURNAL_NAMES["terminal"]
        ).exists()
        absent_recovered = execute_synthetic(
            after_link_absent_auth,
            after_link_absent_auth_path,
            after_link_absent_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=after_link_absent_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        absent_terminal_path = (
            after_link_absent_journal / builder.JOURNAL_NAMES["terminal"]
        )
        checks["after_link_before_dir_fsync_absent_state_republishes_exact_pass"] = (
            absent_interrupted
            and unlinked_complete_terminal
            and absent_before_recovery
            and absent_recovered["status"]
            == "RECOVERED_PASS_FROM_DURABLE_COMMIT_INTENT"
            and stat.S_IMODE(absent_terminal_path.stat().st_mode) == 0o444
            and absent_terminal_path.stat().st_nlink == 1
            and absent_terminal_path.read_bytes()
            == builder.canonical_json_bytes(absent_recovered["receipt"])
        )

        def terminal_parent_replacement_case(
            side: str, phase: str, terminal_kind: str
        ) -> dict[str, Any]:
            """Exercise both held parents through first/recovery PASS/FAIL."""

            case = temp / f"terminal-continuity-{phase}-{terminal_kind}-{side}"
            case.mkdir()
            evidence = case / "evidence"
            evidence.mkdir()
            synthetic_parent = case / "synthetic-parent"
            canonical_parent = case / "canonical-parent"
            synthetic_parent.mkdir()
            canonical_parent.mkdir()
            moved_parent = case / f"{side}-held-moved"
            final_root = synthetic_parent / "final"
            if terminal_kind == "fail" and phase == "first":
                final_root.mkdir()
                write(final_root / "marker.txt", "no-clobber collision\n")
            decision = f"terminal-continuity-{phase}-{terminal_kind}-{side}"
            case_auth, case_auth_path, case_sha, case_argv = make_authorization(
                evidence, site, final_root, decision
            )
            journal_name = Path(case_auth["journal"]["directory"]).name
            old_expected = builder.EXPECTED_FINAL_ROOT
            builder.EXPECTED_FINAL_ROOT = canonical_parent / "ROOT"
            fixture_ready = phase == "first"
            replacement_called = False

            def deny_fixture_fail_terminal() -> None:
                raise OSError("preserve interrupted recovery fixture without terminal")

            def interrupt_before_publish() -> None:
                raise OSError("durable intent fixture before staging publication")

            def replace_named_parent() -> None:
                nonlocal replacement_called
                replacement_called = True
                target = (
                    synthetic_parent if side == "synthetic"
                    else canonical_parent
                )
                target.rename(moved_parent)
                target.mkdir()

            error_type = None
            returned_status = None
            strict_terminal = False
            no_clobber = False
            origin_state = False
            try:
                if phase == "recovery":
                    try:
                        execute_synthetic(
                            case_auth,
                            case_auth_path,
                            case_sha,
                            rename_impl=synthetic_rename_noreplace,
                            enforce_fixed=False,
                            observed_argv=case_argv,
                            before_rename_hook=interrupt_before_publish,
                            fail_terminal_mid_write_hook=(
                                deny_fixture_fail_terminal
                            ),
                            linux_integration="NOT_RUN_NON_LINUX",
                        )
                    except OSError:
                        pass
                    journal_before = (
                        synthetic_parent / journal_name
                    )
                    fixture_ready = (
                        not final_root.exists()
                        and (
                            journal_before / builder.JOURNAL_NAMES["intent"]
                        ).is_file()
                        and (
                            journal_before / builder.JOURNAL_NAMES["staging"]
                        ).is_dir()
                        and not (
                            journal_before / builder.JOURNAL_NAMES["terminal"]
                        ).exists()
                    )
                    if terminal_kind == "fail" and fixture_ready:
                        staging = (
                            journal_before / builder.JOURNAL_NAMES["staging"]
                        )
                        os.chmod(staging, 0o755)
                        write(staging / "hostile-extra-member.txt", "reject\n")
                        os.chmod(staging / "hostile-extra-member.txt", 0o444)
                        os.chmod(staging, 0o555)

                kwargs: dict[str, Any] = {}
                if terminal_kind == "pass":
                    kwargs["terminal_after_link_before_dir_fsync_hook"] = (
                        replace_named_parent
                    )
                else:
                    kwargs[
                        "fail_terminal_after_link_before_dir_fsync_hook"
                    ] = replace_named_parent
                try:
                    value = execute_synthetic(
                        case_auth,
                        case_auth_path,
                        case_sha,
                        rename_impl=synthetic_rename_noreplace,
                        enforce_fixed=False,
                        observed_argv=case_argv,
                        linux_integration="NOT_RUN_NON_LINUX",
                        **kwargs,
                    )
                    returned_status = value.get("status")
                except BaseException as exc:
                    error_type = type(exc).__name__

                origin_parent = (
                    moved_parent if side == "synthetic" else synthetic_parent
                )
                terminal_path = (
                    origin_parent / journal_name
                    / builder.JOURNAL_NAMES["terminal"]
                )
                if terminal_path.is_file():
                    journal_fd = os.open(
                        terminal_path.parent,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    )
                    try:
                        terminal, _, identity, raw_bytes = (
                            builder.read_canonical_terminal_at(
                                journal_fd, builder.JOURNAL_NAMES["terminal"]
                            )
                        )
                        if terminal_kind == "pass":
                            builder.validate_build_pass_receipt_schema(terminal)
                            status_exact = (
                                terminal["status"]
                                == builder.BUILD_PASS_RECEIPT_STATUS
                            )
                        else:
                            builder.validate_fail_terminal_schema(
                                terminal,
                                authorization_sha256=case_sha,
                                decision_id=decision,
                                terminal_publication_method=(
                                    builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
                                ),
                            )
                            status_exact = (
                                terminal["status"]
                                == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
                            )
                        strict_terminal = (
                            status_exact
                            and identity.mode == 0o444
                            and identity.nlink == 1
                            and raw_bytes == builder.canonical_json_bytes(terminal)
                        )
                        before_bytes = raw_bytes
                        second_rejected = rejected(
                            lambda: synthetic_terminal_publish(
                                journal_fd,
                                builder.JOURNAL_NAMES["terminal"],
                                builder.canonical_json_bytes({
                                    "schema": "must-not-clobber-v10"
                                }),
                            )
                        )
                        no_clobber = (
                            second_rejected
                            and terminal_path.read_bytes() == before_bytes
                        )
                    finally:
                        os.close(journal_fd)
                if side == "synthetic":
                    origin_state = (
                        moved_parent.is_dir()
                        and not (synthetic_parent / "final").exists()
                        and not (synthetic_parent / journal_name).exists()
                    )
                else:
                    origin_state = (
                        synthetic_parent.is_dir()
                        and canonical_parent.is_dir()
                        and moved_parent.is_dir()
                    )
            finally:
                builder.EXPECTED_FINAL_ROOT = old_expected
                for path in (synthetic_parent, canonical_parent, moved_parent):
                    thaw(path)
            return {
                "fixture_ready": fixture_ready,
                "replacement_called": replacement_called,
                "rejected": returned_status is None and error_type == "BuildError",
                "returned_status": returned_status,
                "error_type": error_type,
                "strict_terminal": strict_terminal,
                "terminal_no_clobber": no_clobber,
                "origin_state_preserved": origin_state,
            }

        continuity_matrix = {
            f"{phase}_{terminal_kind}_{side}": terminal_parent_replacement_case(
                side, phase, terminal_kind
            )
            for phase in ("first", "recovery")
            for terminal_kind in ("pass", "fail")
            for side in ("synthetic", "canonical")
        }
        details["terminal_named_parent_continuity_matrix"] = continuity_matrix
        checks[
            "terminal_after_link_synthetic_and_canonical_replacement_matrix_rejected"
        ] = all(
            item["fixture_ready"]
            and item["replacement_called"]
            and item["rejected"]
            and item["strict_terminal"]
            and item["terminal_no_clobber"]
            and item["origin_state_preserved"]
            for item in continuity_matrix.values()
        )
        checks[
            "terminal_continuity_matrix_covers_first_recovery_pass_fail_both_sides"
        ] = set(continuity_matrix) == {
            f"{phase}_{terminal_kind}_{side}"
            for phase in ("first", "recovery")
            for terminal_kind in ("pass", "fail")
            for side in ("synthetic", "canonical")
        }

        def existing_terminal_durability_replacement_case(
            side: str, terminal_kind: str
        ) -> dict[str, Any]:
            case = temp / f"existing-terminal-{terminal_kind}-{side}"
            case.mkdir()
            evidence = case / "evidence"
            evidence.mkdir()
            synthetic_parent = case / "synthetic-parent"
            canonical_parent = case / "canonical-parent"
            synthetic_parent.mkdir()
            canonical_parent.mkdir()
            moved_parent = case / f"{side}-held-moved"
            final_root = synthetic_parent / "final"
            if terminal_kind == "fail":
                final_root.mkdir()
                write(final_root / "marker.txt", "existing collision\n")
            decision = f"existing-terminal-{terminal_kind}-{side}"
            case_auth, case_auth_path, case_sha, case_argv = make_authorization(
                evidence, site, final_root, decision
            )
            journal_name = Path(case_auth["journal"]["directory"]).name
            terminal_before_path = (
                synthetic_parent / journal_name
                / builder.JOURNAL_NAMES["terminal"]
            )
            old_expected = builder.EXPECTED_FINAL_ROOT
            builder.EXPECTED_FINAL_ROOT = canonical_parent / "ROOT"
            first_exact = False
            replacement_called = False
            second_error = None
            bytes_unchanged = False
            strict_terminal = False
            original_fsync = builder.os.fsync
            try:
                if terminal_kind == "pass":
                    first_value = execute_synthetic(
                        case_auth,
                        case_auth_path,
                        case_sha,
                        rename_impl=synthetic_rename_noreplace,
                        enforce_fixed=False,
                        observed_argv=case_argv,
                        linux_integration="NOT_RUN_NON_LINUX",
                    )
                    first_exact = (
                        first_value["status"]
                        == "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
                    )
                else:
                    first_exact = rejected(lambda: execute_synthetic(
                        case_auth,
                        case_auth_path,
                        case_sha,
                        rename_impl=synthetic_rename_noreplace,
                        enforce_fixed=False,
                        observed_argv=case_argv,
                        linux_integration="NOT_RUN_NON_LINUX",
                    ))
                before_bytes = terminal_before_path.read_bytes()
                parent_identity = (
                    synthetic_parent.stat().st_dev,
                    synthetic_parent.stat().st_ino,
                )

                def replace_after_parent_durability(fd: int) -> None:
                    nonlocal replacement_called
                    info = os.fstat(fd)
                    original_fsync(fd)
                    if (
                        not replacement_called
                        and (info.st_dev, info.st_ino) == parent_identity
                    ):
                        target = (
                            synthetic_parent if side == "synthetic"
                            else canonical_parent
                        )
                        target.rename(moved_parent)
                        target.mkdir()
                        replacement_called = True

                builder.os.fsync = replace_after_parent_durability
                try:
                    execute_synthetic(
                        case_auth,
                        case_auth_path,
                        case_sha,
                        rename_impl=synthetic_rename_noreplace,
                        enforce_fixed=False,
                        observed_argv=case_argv,
                        linux_integration="NOT_RUN_NON_LINUX",
                    )
                except BaseException as exc:
                    second_error = type(exc).__name__
                finally:
                    builder.os.fsync = original_fsync
                origin_parent = (
                    moved_parent if side == "synthetic" else synthetic_parent
                )
                terminal_after_path = (
                    origin_parent / journal_name
                    / builder.JOURNAL_NAMES["terminal"]
                )
                bytes_unchanged = (
                    terminal_after_path.is_file()
                    and terminal_after_path.read_bytes() == before_bytes
                )
                if terminal_after_path.is_file():
                    terminal_fd = os.open(
                        terminal_after_path.parent,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    )
                    try:
                        value, _, identity, raw_bytes = (
                            builder.read_canonical_terminal_at(
                                terminal_fd, builder.JOURNAL_NAMES["terminal"]
                            )
                        )
                    finally:
                        os.close(terminal_fd)
                    strict_terminal = (
                        identity.mode == 0o444
                        and identity.nlink == 1
                        and raw_bytes == builder.canonical_json_bytes(value)
                        and (
                            value["status"] == builder.BUILD_PASS_RECEIPT_STATUS
                            if terminal_kind == "pass"
                            else value["status"]
                            == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
                        )
                    )
            finally:
                builder.os.fsync = original_fsync
                builder.EXPECTED_FINAL_ROOT = old_expected
                for path in (synthetic_parent, canonical_parent, moved_parent):
                    thaw(path)
            return {
                "first_exact": first_exact,
                "replacement_called_after_parent_fsync": replacement_called,
                "second_rejected": second_error == "BuildError",
                "terminal_bytes_unchanged": bytes_unchanged,
                "strict_terminal": strict_terminal,
            }

        existing_terminal_matrix = {
            f"{terminal_kind}_{side}": (
                existing_terminal_durability_replacement_case(
                    side, terminal_kind
                )
            )
            for terminal_kind in ("pass", "fail")
            for side in ("synthetic", "canonical")
        }
        details["existing_terminal_durability_matrix"] = (
            existing_terminal_matrix
        )
        checks[
            "existing_pass_fail_terminal_durability_rejects_both_parent_replacements"
        ] = all(all(item.values()) for item in existing_terminal_matrix.values())

        swap_final = temp / "swap-final"
        swap_auth, swap_auth_path, swap_sha, swap_argv = make_authorization(
            temp, site, swap_final, "staging-inode-swap"
        )
        swap_journal = Path(swap_auth["journal"]["directory"])

        def swap_staging_entry() -> None:
            staging = swap_journal / builder.JOURNAL_NAMES["staging"]
            staging.rename(swap_journal / "ORIGINAL_STAGING_PRESERVED")
            staging.mkdir()

        swap_rejected = rejected(lambda: execute_synthetic(
            swap_auth, swap_auth_path, swap_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=swap_argv,
            before_rename_hook=swap_staging_entry,
            linux_integration="NOT_RUN_NON_LINUX",
        ))
        swap_terminal = builder.strict_json_loads(
            (swap_journal / builder.JOURNAL_NAMES["terminal"]).read_bytes()
        )
        checks["staging_entry_swap_rejected_and_inode_safe_fail_preserved"] = (
            swap_rejected and not swap_final.exists()
            and swap_terminal["status"] == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
            and swap_terminal["staging_preservation"]["entry_matches_expected"] is False
            and (swap_journal / "ORIGINAL_STAGING_PRESERVED").is_dir()
        )

        collision_final = temp / "collision-final"
        collision_final.mkdir()
        marker = collision_final / "marker.txt"
        write(marker, "do not overwrite\n")
        collision_auth, collision_path, collision_sha, collision_argv = make_authorization(
            temp, site, collision_final, "existing-final-collision"
        )
        collision_rejected = rejected(lambda: execute_synthetic(
            collision_auth, collision_path, collision_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=collision_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        ))
        collision_terminal = builder.strict_json_loads(
            (Path(collision_auth["journal"]["terminal"])).read_bytes()
        )
        checks["existing_final_no_clobber_fail_terminal_and_parent_fsync_path"] = (
            collision_rejected and marker.read_text(encoding="utf-8") == "do not overwrite\n"
            and collision_terminal["status"] == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
            and collision_terminal["fixed_root_published"] is False
            and collision_terminal["fixed_root_path_present"] is True
            and collision_terminal["fixed_root_exactly_validated"] is False
        )

        fail_mid_final = temp / "fail-terminal-mid-write-existing-root"
        fail_mid_final.mkdir()
        write(fail_mid_final / "marker.txt", "collision remains\n")
        (
            fail_mid_auth,
            fail_mid_auth_path,
            fail_mid_sha,
            fail_mid_argv,
        ) = make_authorization(
            temp,
            site,
            fail_mid_final,
            "fail-terminal-mid-write-atomicity",
        )
        fail_mid_journal = Path(fail_mid_auth["journal"]["directory"])
        fail_partial_noncanonical_seen = False

        def interrupt_fail_terminal_mid_write() -> None:
            nonlocal fail_partial_noncanonical_seen
            temporary = [
                path for path in fail_mid_journal.iterdir()
                if path.name.startswith(".TERMINAL.v10.complete.")
            ]
            fail_partial_noncanonical_seen = (
                not (
                    fail_mid_journal / builder.JOURNAL_NAMES["terminal"]
                ).exists()
                and len(temporary) == 1
                and temporary[0].stat().st_size > 0
                and stat.S_IMODE(temporary[0].stat().st_mode) == 0o600
            )
            raise OSError("synthetic FAIL terminal mid-write interruption")

        first_fail_mid_rejected = rejected(lambda: execute_synthetic(
            fail_mid_auth,
            fail_mid_auth_path,
            fail_mid_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=fail_mid_argv,
            fail_terminal_mid_write_hook=interrupt_fail_terminal_mid_write,
            linux_integration="NOT_RUN_NON_LINUX",
        ))
        fail_mid_canonical_absent = not (
            fail_mid_journal / builder.JOURNAL_NAMES["terminal"]
        ).exists()
        fail_mid_after_link_seen = False

        def interrupt_fail_mid_recovery_after_link() -> None:
            nonlocal fail_mid_after_link_seen
            terminal = fail_mid_journal / builder.JOURNAL_NAMES["terminal"]
            fail_mid_after_link_seen = (
                terminal.is_file()
                and stat.S_IMODE(terminal.stat().st_mode) == 0o444
                and terminal.stat().st_nlink == 1
            )
            raise OSError(
                "second synthetic interruption after complete FAIL link"
            )

        second_fail_mid_rejected = False
        try:
            execute_synthetic(
                fail_mid_auth,
                fail_mid_auth_path,
                fail_mid_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=fail_mid_argv,
                fail_terminal_after_link_before_dir_fsync_hook=(
                    interrupt_fail_mid_recovery_after_link
                ),
                linux_integration="NOT_RUN_NON_LINUX",
            )
        except OSError:
            second_fail_mid_rejected = True
        fail_mid_terminal_path = (
            fail_mid_journal / builder.JOURNAL_NAMES["terminal"]
        )
        fail_mid_bytes_after_second_interrupt = fail_mid_terminal_path.read_bytes()
        original_fsync = builder.os.fsync
        dual_recovery_fsync_identities: list[tuple[int, int]] = []

        def record_dual_recovery_fsync(fd: int) -> None:
            info = os.fstat(fd)
            dual_recovery_fsync_identities.append((info.st_dev, info.st_ino))
            original_fsync(fd)

        builder.os.fsync = record_dual_recovery_fsync
        try:
            third_fail_mid_rejected = rejected(lambda: execute_synthetic(
                fail_mid_auth,
                fail_mid_auth_path,
                fail_mid_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=fail_mid_argv,
                linux_integration="NOT_RUN_NON_LINUX",
            ))
        finally:
            builder.os.fsync = original_fsync
        fail_mid_journal_fd = os.open(
            fail_mid_journal,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            fail_mid_terminal, _, fail_mid_identity, fail_mid_raw = (
                builder.read_canonical_terminal_at(
                    fail_mid_journal_fd, builder.JOURNAL_NAMES["terminal"]
                )
            )
        finally:
            os.close(fail_mid_journal_fd)
        checks["fail_terminal_mid_write_leaves_no_canonical_then_recovers_full_fail"] = (
            first_fail_mid_rejected
            and fail_partial_noncanonical_seen
            and fail_mid_canonical_absent
            and second_fail_mid_rejected
            and third_fail_mid_rejected
            and fail_mid_terminal["status"]
            == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
            and fail_mid_terminal["terminal_publication_method"]
            == builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
            and fail_mid_identity.mode == 0o444
            and fail_mid_identity.nlink == 1
            and fail_mid_raw == builder.canonical_json_bytes(fail_mid_terminal)
            and fail_mid_terminal_path.is_file()
        )
        dual_expected_fsync = {
            (fail_mid_journal.stat().st_dev, fail_mid_journal.stat().st_ino),
            (
                fail_mid_journal.parent.stat().st_dev,
                fail_mid_journal.parent.stat().st_ino,
            ),
        }
        checks[
            "fail_terminal_two_interruptions_midwrite_then_afterlink_recovers_durably"
        ] = (
            first_fail_mid_rejected
            and fail_mid_canonical_absent
            and second_fail_mid_rejected
            and fail_mid_after_link_seen
            and third_fail_mid_rejected
            and fail_mid_terminal_path.read_bytes()
            == fail_mid_bytes_after_second_interrupt
            and dual_expected_fsync.issubset(
                set(dual_recovery_fsync_identities)
            )
        )

        fail_after_link_final = temp / "fail-terminal-after-link-existing-root"
        fail_after_link_final.mkdir()
        write(fail_after_link_final / "marker.txt", "collision remains\n")
        (
            fail_after_auth,
            fail_after_auth_path,
            fail_after_sha,
            fail_after_argv,
        ) = make_authorization(
            temp,
            site,
            fail_after_link_final,
            "fail-terminal-after-link-atomicity",
        )
        fail_after_journal = Path(fail_after_auth["journal"]["directory"])
        full_fail_seen_before_dir_fsync = False

        def interrupt_full_fail_after_link() -> None:
            nonlocal full_fail_seen_before_dir_fsync
            terminal = fail_after_journal / builder.JOURNAL_NAMES["terminal"]
            full_fail_seen_before_dir_fsync = (
                terminal.is_file()
                and stat.S_IMODE(terminal.stat().st_mode) == 0o444
                and terminal.stat().st_nlink == 1
            )
            raise OSError("synthetic FAIL terminal interruption after link")

        fail_after_first_rejected = rejected(lambda: execute_synthetic(
            fail_after_auth,
            fail_after_auth_path,
            fail_after_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=fail_after_argv,
            fail_terminal_after_link_before_dir_fsync_hook=(
                interrupt_full_fail_after_link
            ),
            linux_integration="NOT_RUN_NON_LINUX",
        ))
        fail_after_terminal_path = (
            fail_after_journal / builder.JOURNAL_NAMES["terminal"]
        )
        fail_after_bytes = fail_after_terminal_path.read_bytes()
        original_fsync = builder.os.fsync
        recovery_fsync_identities: list[tuple[int, int]] = []

        def record_recovery_fsync(fd: int) -> None:
            info = os.fstat(fd)
            recovery_fsync_identities.append((info.st_dev, info.st_ino))
            original_fsync(fd)

        builder.os.fsync = record_recovery_fsync
        try:
            fail_after_second_rejected = rejected(lambda: execute_synthetic(
                fail_after_auth,
                fail_after_auth_path,
                fail_after_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=fail_after_argv,
                linux_integration="NOT_RUN_NON_LINUX",
            ))
        finally:
            builder.os.fsync = original_fsync
        expected_fail_recovery_fsync = {
            (fail_after_journal.stat().st_dev, fail_after_journal.stat().st_ino),
            (
                fail_after_journal.parent.stat().st_dev,
                fail_after_journal.parent.stat().st_ino,
            ),
        }
        checks["fail_terminal_after_link_is_full_strict_and_not_rewritten"] = (
            fail_after_first_rejected
            and full_fail_seen_before_dir_fsync
            and fail_after_second_rejected
            and fail_after_terminal_path.read_bytes() == fail_after_bytes
            and stat.S_IMODE(fail_after_terminal_path.stat().st_mode) == 0o444
            and fail_after_terminal_path.stat().st_nlink == 1
            and expected_fail_recovery_fsync.issubset(
                set(recovery_fsync_identities)
            )
        )
        checks[
            "existing_strict_fail_recovery_fsyncs_journal_and_parent"
        ] = expected_fail_recovery_fsync.issubset(
            set(recovery_fsync_identities)
        )

        invalid_after_publish_final = temp / "invalid-after-publish-final"
        invalid_auth, invalid_path, invalid_sha, invalid_argv = make_authorization(
            temp, site, invalid_after_publish_final,
            "post-publish-verification-failure",
        )

        def invalidate_published_root_before_terminal() -> None:
            os.chmod(invalid_after_publish_final, 0o755)
            raise OSError("synthetic post-publish structural invalidation")

        invalid_rejected = False
        try:
            execute_synthetic(
                invalid_auth,
                invalid_path,
                invalid_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=invalid_argv,
                terminal_mid_write_hook=invalidate_published_root_before_terminal,
                linux_integration="NOT_RUN_NON_LINUX",
            )
        except OSError:
            invalid_rejected = True
        invalid_terminal = builder.strict_json_loads(
            Path(invalid_auth["journal"]["terminal"]).read_bytes()
        )
        checks["fail_receipt_truthfully_reports_published_inode_but_not_exact_validity"] = (
            invalid_rejected
            and invalid_terminal["fixed_root_path_present"] is True
            and invalid_terminal["fixed_root_published"] is True
            and invalid_terminal["fixed_root_exactly_validated"] is False
        )

        digest_tree = temp / "digest-tree"
        digest_tree.mkdir()
        write(digest_tree / "one.bin", "one\n")
        (digest_tree / "nested").mkdir()
        write(digest_tree / "nested" / "two.bin", "two\n")
        digest_fd = os.open(
            digest_tree, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        fresh_calls = 0
        original_fresh_cursor = builder.fresh_directory_cursor

        def counted_fresh_cursor(held_fd: int) -> int:
            nonlocal fresh_calls
            fresh_calls += 1
            return original_fresh_cursor(held_fd)

        builder.fresh_directory_cursor = counted_fresh_cursor
        try:
            structural_before = builder.inventory_structural(
                digest_fd, include_root=True
            )
            files_before = builder.files_only_records(structural_before)
            files_digest_before = builder.files_only_digest(files_before)
            structural_digest_before = builder.structural_digest(
                structural_before
            )
            os.chmod(digest_tree / "nested", 0o700)
            structural_after_mode = builder.inventory_structural(
                digest_fd, include_root=True
            )
            files_after_mode = builder.files_only_records(
                structural_after_mode
            )
        finally:
            builder.fresh_directory_cursor = original_fresh_cursor
            os.close(digest_fd)
        checks["explicit_files_only_and_structural_algorithms_diverge_and_mode_is_structural"] = (
            builder.FILES_ONLY_DIGEST_ALGORITHM
            != builder.STRUCTURAL_DIGEST_ALGORITHM
            and builder.files_only_digest(files_after_mode)
            == files_digest_before
            and builder.structural_digest(structural_after_mode)
            != structural_digest_before
            and structural_before[0]["relative_path"] == "."
        )
        lock_final = temp / "lock-final"
        lock_auth, lock_auth_path, lock_sha, lock_argv = make_authorization(
            temp, site, lock_final, "single-writer-lock-transaction"
        )
        lock_journal = Path(lock_auth["journal"]["directory"])
        contender: dict[str, Any] = {}

        def run_contender_while_owner_holds_lock(_: Any) -> None:
            names_before = {path.name for path in lock_journal.iterdir()}
            contender["result"] = execute_synthetic(
                lock_auth,
                lock_auth_path,
                lock_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=lock_argv,
                linux_integration="NOT_RUN_NON_LINUX",
            )
            contender["no_mutation"] = names_before == {
                path.name for path in lock_journal.iterdir()
            }

        owner_result = execute_synthetic(
            lock_auth,
            lock_auth_path,
            lock_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=lock_argv,
            after_discovery_hook=run_contender_while_owner_holds_lock,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        later_result = execute_synthetic(
            lock_auth,
            lock_auth_path,
            lock_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=lock_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        checks["flock_single_writer_contender_no_mutation_owner_pass_later_already_pass"] = (
            contender["result"]["status"]
            == "IN_PROGRESS_NO_TRANSACTION_PAYLOAD_MUTATION"
            and contender["result"][
                "journal_directory_created_by_this_attempt"
            ] is False
            and contender["result"]["lock_created_by_this_attempt"] is False
            and contender["result"]["journal_mutated_by_this_attempt"] is False
            and contender["result"][
                "transaction_payload_written_by_this_attempt"
            ] is False
            and contender["no_mutation"] is True
            and owner_result["status"]
            == "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
            and later_result["status"] == "ALREADY_TERMINAL_PASS"
            and owner_result["receipt"]["journal"]["lock_method"]
            == builder.LOCK_METHOD
            and owner_result["receipt"]["journal"]["lock_inode"]
            == (lock_journal / builder.JOURNAL_NAMES["lock"]).stat().st_ino
        )

        empty_journal_final = temp / "empty-journal-final"
        empty_auth, empty_path, empty_sha, empty_argv = make_authorization(
            temp,
            site,
            empty_journal_final,
            "crash-after-mkdir-before-lock",
        )
        empty_journal = Path(empty_auth["journal"]["directory"])
        empty_journal.mkdir(mode=0o700)
        empty_result = execute_synthetic(
            empty_auth,
            empty_path,
            empty_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=empty_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        empty_later = execute_synthetic(
            empty_auth,
            empty_path,
            empty_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=empty_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        checks["empty_preexisting_journal_after_mkdir_crash_initializes_and_passes"] = (
            empty_result["status"]
            == "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
            and empty_later["status"] == "ALREADY_TERMINAL_PASS"
            and (empty_journal / builder.JOURNAL_NAMES["lock"]).is_file()
            and (empty_journal / builder.JOURNAL_NAMES["begin"]).is_file()
            and (empty_journal / builder.JOURNAL_NAMES["terminal"]).is_file()
        )

        race_final = temp / "lock-open-before-flock-final"
        race_auth, race_path, race_sha, race_argv = make_authorization(
            temp,
            site,
            race_final,
            "shared-ocreat-open-before-flock-race",
        )
        race_journal = Path(race_auth["journal"]["directory"])
        a_opened_lock = threading.Event()
        allow_a_flock = threading.Event()
        b_holds_lock = threading.Event()
        allow_b_finish = threading.Event()
        race_state: dict[str, Any] = {}

        def pause_a_after_shared_lock_open(
            _lock_fd: int, lock_identity: builder.Identity
        ) -> None:
            race_state["a_lock_identity"] = lock_identity.json()
            a_opened_lock.set()
            if not allow_a_flock.wait(timeout=10):
                raise RuntimeError("timed out waiting to release A before flock")

        def pause_b_while_holding_lock(_: Any) -> None:
            b_holds_lock.set()
            if not allow_b_finish.wait(timeout=10):
                raise RuntimeError("timed out waiting to release lock owner B")

        def run_a_open_before_flock() -> None:
            try:
                race_state["a_result"] = execute_synthetic(
                    race_auth,
                    race_path,
                    race_sha,
                    rename_impl=synthetic_rename_noreplace,
                    enforce_fixed=False,
                    observed_argv=race_argv,
                    lock_opened_before_flock_hook=pause_a_after_shared_lock_open,
                    linux_integration="NOT_RUN_NON_LINUX",
                )
            except BaseException as exc:
                race_state["a_error"] = repr(exc)

        def run_b_lock_winner() -> None:
            try:
                race_state["b_result"] = execute_synthetic(
                    race_auth,
                    race_path,
                    race_sha,
                    rename_impl=synthetic_rename_noreplace,
                    enforce_fixed=False,
                    observed_argv=race_argv,
                    after_discovery_hook=pause_b_while_holding_lock,
                    linux_integration="NOT_RUN_NON_LINUX",
                )
            except BaseException as exc:
                race_state["b_error"] = repr(exc)

        thread_a = threading.Thread(target=run_a_open_before_flock, daemon=True)
        thread_b = threading.Thread(target=run_b_lock_winner, daemon=True)
        thread_a.start()
        a_reached_window = a_opened_lock.wait(timeout=10)
        if a_reached_window:
            thread_b.start()
        b_became_owner = a_reached_window and b_holds_lock.wait(timeout=10)
        names_before_a_contends = (
            {path.name for path in race_journal.iterdir()}
            if b_became_owner else set()
        )
        allow_a_flock.set()
        thread_a.join(timeout=10)
        names_after_a_contends = (
            {path.name for path in race_journal.iterdir()}
            if race_journal.is_dir() else set()
        )
        allow_b_finish.set()
        if thread_b.ident is not None:
            thread_b.join(timeout=10)
        race_later: dict[str, Any] = {}
        if (
            not thread_a.is_alive()
            and not thread_b.is_alive()
            and race_state.get("b_result", {}).get("status")
            == "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
        ):
            race_later = execute_synthetic(
                race_auth,
                race_path,
                race_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=race_argv,
                linux_integration="NOT_RUN_NON_LINUX",
            )
        race_lock_path = race_journal / builder.JOURNAL_NAMES["lock"]
        race_begin_path = race_journal / builder.JOURNAL_NAMES["begin"]
        race_terminal_path = race_journal / builder.JOURNAL_NAMES["terminal"]
        race_begin = (
            builder.strict_json_loads(race_begin_path.read_bytes())
            if race_begin_path.is_file() else {}
        )
        race_terminal = (
            builder.strict_json_loads(race_terminal_path.read_bytes())
            if race_terminal_path.is_file() else {}
        )
        race_lock_inode = (
            race_lock_path.stat().st_ino if race_lock_path.is_file() else -1
        )
        checks["shared_lock_creator_loser_truthfully_reports_lock_only_mutation"] = (
            a_reached_window
            and b_became_owner
            and not thread_a.is_alive()
            and not thread_b.is_alive()
            and "a_error" not in race_state
            and "b_error" not in race_state
            and race_state.get("a_result", {}).get("status")
            == "IN_PROGRESS_NO_TRANSACTION_PAYLOAD_MUTATION"
            and race_state.get("a_result", {}).get(
                "journal_mutated_by_this_attempt"
            ) is True
            and race_state.get("a_result", {}).get(
                "lock_created_by_this_attempt"
            ) is True
            and race_state.get("a_result", {}).get(
                "journal_directory_created_by_this_attempt"
            ) is True
            and race_state.get("a_result", {}).get(
                "transaction_payload_written_by_this_attempt"
            ) is False
            and race_state.get("b_result", {}).get("status")
            == "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
            and race_later.get("status") == "ALREADY_TERMINAL_PASS"
            and names_before_a_contends == names_after_a_contends
            == {builder.JOURNAL_NAMES["lock"], builder.JOURNAL_NAMES["begin"]}
            and race_terminal.get("status") == builder.BUILD_PASS_RECEIPT_STATUS
            and race_begin.get("lock_identity", {}).get("inode")
            == race_state.get("a_lock_identity", {}).get("inode")
            == race_lock_inode
            == race_state.get("b_result", {}).get("receipt", {}).get(
                "journal", {}
            ).get("lock_inode")
        )

        (
            recovery_mid_auth,
            recovery_mid_auth_path,
            recovery_mid_sha,
            recovery_mid_argv,
            recovery_mid_journal,
            recovery_mid_final,
        ) = create_interrupted_staging(
            temp, site, "recovery-terminal-republish-mid-write"
        )
        recovery_mid_partial_seen = False

        def interrupt_recovery_terminal_mid_write() -> None:
            nonlocal recovery_mid_partial_seen
            temporary = [
                path for path in recovery_mid_journal.iterdir()
                if path.name.startswith(".TERMINAL.v10.complete.")
            ]
            recovery_mid_partial_seen = (
                recovery_mid_final.is_dir()
                and not (
                    recovery_mid_journal / builder.JOURNAL_NAMES["terminal"]
                ).exists()
                and len(temporary) == 1
                and temporary[0].stat().st_size > 0
            )
            raise OSError("second interruption during recovery terminal publish")

        recovery_mid_interrupted = False
        try:
            execute_synthetic(
                recovery_mid_auth,
                recovery_mid_auth_path,
                recovery_mid_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=recovery_mid_argv,
                terminal_mid_write_hook=interrupt_recovery_terminal_mid_write,
                linux_integration="NOT_RUN_NON_LINUX",
            )
        except OSError:
            recovery_mid_interrupted = True
        recovery_mid_canonical_absent = not (
            recovery_mid_journal / builder.JOURNAL_NAMES["terminal"]
        ).exists()
        recovery_mid_final_result = execute_synthetic(
            recovery_mid_auth,
            recovery_mid_auth_path,
            recovery_mid_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=recovery_mid_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        )
        checks["recovery_republish_mid_write_cannot_be_converted_to_outer_fail"] = (
            recovery_mid_interrupted
            and recovery_mid_partial_seen
            and recovery_mid_canonical_absent
            and recovery_mid_final_result["status"]
            == "RECOVERED_PASS_FROM_DURABLE_COMMIT_INTENT"
            and (
                recovery_mid_journal / builder.JOURNAL_NAMES["terminal"]
            ).is_file()
            and builder.strict_json_loads((
                recovery_mid_journal / builder.JOURNAL_NAMES["terminal"]
            ).read_bytes())["status"] == builder.BUILD_PASS_RECEIPT_STATUS
        )

        def run_recovery_attack(
            decision: str,
            filesystem_mutator: Callable[[Path, Path], None] | None = None,
            core_mutator: Callable[[dict[str, Any]], None] | None = None,
        ) -> bool:
            attack_auth, attack_path, attack_sha, attack_argv, attack_journal, attack_final = (
                create_interrupted_staging(temp, site, decision)
            )
            staging = attack_journal / builder.JOURNAL_NAMES["staging"]
            if filesystem_mutator is not None:
                filesystem_mutator(attack_journal, staging)

            def update_core(core: dict[str, Any]) -> None:
                core["staging_identity"] = builder.Identity.from_stat(
                    staging.stat()
                ).json()
                bundle_path = staging / "bundle"
                private_path = staging / "private_runtime_site_packages"
                if bundle_path.is_dir():
                    core["bundle_identity"] = builder.Identity.from_stat(
                        bundle_path.stat()
                    ).json()
                if private_path.is_dir():
                    core["private_identity"] = builder.Identity.from_stat(
                        private_path.stat()
                    ).json()
                if core_mutator is not None:
                    core_mutator(core)

            rewrite_intent_core(attack_journal, update_core)
            refused = rejected(lambda: execute_synthetic(
                attack_auth,
                attack_path,
                attack_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=attack_argv,
                linux_integration="NOT_RUN_NON_LINUX",
            ))
            terminal_file = attack_journal / builder.JOURNAL_NAMES["terminal"]
            terminal = (
                builder.strict_json_loads(terminal_file.read_bytes())
                if terminal_file.is_file() else {}
            )
            outcome = (
                refused
                and not attack_final.exists()
                and terminal.get("status")
                == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
            )
            thaw(attack_journal)
            return outcome

        def remove_required_child(journal_path: Path, staging: Path) -> None:
            os.chmod(staging, 0o755)
            (staging / builder.SUPPORT_FILES[0]).rename(
                journal_path / "REMOVED_REQUIRED_CHILD"
            )
            os.chmod(staging, 0o555)

        def add_unauthorized_file(_: Path, staging: Path) -> None:
            os.chmod(staging, 0o755)
            write(staging / "UNAUTHORIZED_EXTRA_FILE", "not authorized\n")
            os.chmod(staging / "UNAUTHORIZED_EXTRA_FILE", 0o444)
            os.chmod(staging, 0o555)

        def replace_with_fake_bundle(journal_path: Path, staging: Path) -> None:
            os.chmod(staging, 0o755)
            original = staging / "bundle"
            os.chmod(original, 0o755)
            original.rename(journal_path / "ORIGINAL_BUNDLE_PRESERVED")
            os.chmod(journal_path / "ORIGINAL_BUNDLE_PRESERVED", 0o555)
            fake = staging / "bundle"
            fake.mkdir()
            write(fake / "SHA256SUMS", "0" * 64 + "  rogue.txt\n")
            write(fake / "rogue.txt", "rogue\n")
            fake_fd = os.open(fake, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                builder.freeze_tree(fake_fd)
            finally:
                os.close(fake_fd)
            os.chmod(staging, 0o555)

        def journal_record_attack(
            decision: str,
            record_name: str,
            mutate: Callable[[dict[str, Any]], None] | None = None,
            *,
            mode: int = 0o444,
            canonical: bool = True,
        ) -> bool:
            (
                attack_auth, attack_path, attack_sha, attack_argv,
                attack_journal, attack_final,
            ) = create_interrupted_staging(temp, site, decision)
            record_path = attack_journal / record_name
            value = builder.strict_json_loads(record_path.read_bytes())
            if mutate is not None:
                mutate(value)
            data = (
                builder.canonical_json_bytes(value)
                if canonical
                else json.dumps(value, indent=1, sort_keys=False).encode("utf-8")
            )
            os.chmod(record_path, 0o644)
            record_path.write_bytes(data)
            os.chmod(record_path, mode)
            refused = rejected(lambda: execute_synthetic(
                attack_auth,
                attack_path,
                attack_sha,
                rename_impl=synthetic_rename_noreplace,
                enforce_fixed=False,
                observed_argv=attack_argv,
                linux_integration="NOT_RUN_NON_LINUX",
            ))
            outcome = refused and not attack_final.exists()
            thaw(attack_journal)
            return outcome

        checks["begin_extra_ignored_field_is_rejected"] = journal_record_attack(
            "begin-extra-field",
            builder.JOURNAL_NAMES["begin"],
            lambda value: value.update({"ignored": False}),
        )
        checks["begin_mode0644_is_rejected"] = journal_record_attack(
            "journal-begin-mode-0644", builder.JOURNAL_NAMES["begin"],
            mode=0o644,
        )
        checks["commit_intent_noncanonical_json_is_rejected"] = (
            journal_record_attack(
                "journal-intent-noncanonical",
                builder.JOURNAL_NAMES["intent"],
                canonical=False,
            )
        )
        checks["commit_intent_recovery_rule_tamper_is_rejected"] = (
            journal_record_attack(
                "journal-intent-recovery-rule-tamper",
                builder.JOURNAL_NAMES["intent"],
                lambda value: value.update({"recovery_rule": "TRUST_INTENT"}),
            )
        )

        checks["recovery_rejects_self_consistent_intent_with_missing_root_child"] = run_recovery_attack(
            "recovery-missing-child", remove_required_child
        )
        checks["recovery_rejects_self_consistent_intent_with_extra_root_file"] = run_recovery_attack(
            "recovery-extra-root-file", add_unauthorized_file
        )
        checks["recovery_rejects_self_consistent_intent_with_fake_bundle"] = run_recovery_attack(
            "recovery-fake-v10-bundle", replace_with_fake_bundle
        )
        checks["recovery_rejects_self_hashed_fake_source_inventory_evidence"] = run_recovery_attack(
            "recovery-fake-source-inventory",
            core_mutator=lambda core: core["build"]["source_inventory"].update(
                {
                    "inventory_digest": builder.sha256_bytes(
                        b"forged-source-inventory"
                    )
                }
            ),
        )
        checks[
            "recovery_rejects_self_hashed_forged_outer_launch_receipt_binding"
        ] = run_recovery_attack(
            "recovery-forged-outer-launch-receipt",
            core_mutator=lambda core: core["trusted_launch"].update(
                {"outer_launch_receipt_sha256": "f" * 64}
            ),
        )

        (
            partial_auth,
            partial_auth_path,
            partial_sha,
            partial_argv,
            partial_journal,
            partial_final,
        ) = create_interrupted_staging(
            temp, site, "externally-injected-partial-canonical-terminal"
        )
        partial_terminal_path = (
            partial_journal / builder.JOURNAL_NAMES["terminal"]
        )
        partial_bytes = b'{"schema": "truncated-before-complete-json"'
        partial_terminal_path.write_bytes(partial_bytes)
        os.chmod(partial_terminal_path, 0o600)
        partial_rejected = rejected(lambda: execute_synthetic(
            partial_auth,
            partial_auth_path,
            partial_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=partial_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        ))
        checks["external_partial_canonical_terminal_is_rejected_never_overwritten"] = (
            partial_rejected
            and not partial_final.exists()
            and partial_terminal_path.read_bytes() == partial_bytes
            and stat.S_IMODE(partial_terminal_path.stat().st_mode) == 0o600
            and partial_terminal_path.stat().st_nlink == 1
        )

        (
            forged_fail_auth,
            forged_fail_auth_path,
            forged_fail_sha,
            forged_fail_argv,
            forged_fail_journal,
            forged_fail_final,
        ) = create_interrupted_staging(
            temp, site, "externally-injected-complete-fail-terminal"
        )
        forged_fail_staging = (
            forged_fail_journal / builder.JOURNAL_NAMES["staging"]
        )
        forged_fail_identity = builder.Identity.from_stat(
            forged_fail_staging.stat()
        )
        forged_fail_value = builder.fail_terminal(
            forged_fail_sha,
            forged_fail_auth["decision_id"],
            "FORGED_EXTERNAL_FAIL_MUST_NOT_OVERRIDE_VALID_INTENT",
            builder.BuildError("synthetic forged complete FAIL"),
            forged_fail_identity,
            forged_fail_identity,
            terminal_publication_method=(
                builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
            ),
        )
        forged_fail_bytes = builder.canonical_json_bytes(forged_fail_value)
        forged_fail_terminal_path = (
            forged_fail_journal / builder.JOURNAL_NAMES["terminal"]
        )
        forged_fail_terminal_path.write_bytes(forged_fail_bytes)
        os.chmod(forged_fail_terminal_path, 0o444)
        forged_fail_rejected = rejected(lambda: execute_synthetic(
            forged_fail_auth,
            forged_fail_auth_path,
            forged_fail_sha,
            rename_impl=synthetic_rename_noreplace,
            enforce_fixed=False,
            observed_argv=forged_fail_argv,
            linux_integration="NOT_RUN_NON_LINUX",
        ))
        checks["complete_forged_fail_cannot_override_independently_valid_pass_intent"] = (
            forged_fail_rejected
            and not forged_fail_final.exists()
            and forged_fail_staging.is_dir()
            and forged_fail_terminal_path.read_bytes() == forged_fail_bytes
            and stat.S_IMODE(forged_fail_terminal_path.stat().st_mode) == 0o444
            and forged_fail_terminal_path.stat().st_nlink == 1
        )
        checks[
            "conflicting_valid_pass_evidence_never_accepts_or_rewrites_existing_fail"
        ] = checks[
            "complete_forged_fail_cannot_override_independently_valid_pass_intent"
        ]

        source = (HERE / "build_result_free_transport_runtime_v10.py").read_text(encoding="utf-8")
        checks["every_builder_inventory_uses_fresh_reopened_directory_cursor"] = (
            fresh_calls >= 4 and source.count("os.listdir(") == 1
            and "fresh_directory_cursor" in source
        )
        checks["production_has_only_dirfd_renameat2_noreplace_no_overwrite_fallback"] = (
            "renameat2" in source and "RENAME_NOREPLACE" in source
            and "os.replace(" not in source and "shutil.rmtree" not in source
            and "os.kill" not in source and "SIGCONT" not in source
        )
        core_source = inspect.getsource(builder._execute_transaction_core)
        recovery_source = inspect.getsource(builder.recover_existing)
        production_entry_source = inspect.getsource(builder.execute_authorized)
        terminal_call_lines = [
            index
            for index, line in enumerate(source.splitlines())
            if "terminal_evidence = terminal_publish_impl(" in line
        ]
        terminal_post_publish_tails = [
            source.splitlines()[index + 1:index + 24]
            for index in terminal_call_lines
        ]
        recovery_lines = recovery_source.splitlines()
        recovery_durability_then_continuity = sum(
            recovery_lines[index].strip() == "os.fsync(parent_fd)"
            and recovery_lines[index + 1].strip()
            == "revalidate_terminal_continuity()"
            for index in range(len(recovery_lines) - 1)
        )
        checks[
            "all_four_terminal_publishers_revalidate_after_durability_before_exit"
        ] = (
            len(terminal_call_lines) == 4
            and all(
                any("os.fsync(parent_fd)" in line for line in tail)
                and any(
                    "revalidate_terminal_continuity()" in line
                    for line in tail
                )
                and next(
                    index for index, line in enumerate(tail)
                    if "revalidate_terminal_continuity()" in line
                ) > next(
                    index for index, line in enumerate(tail)
                    if "os.fsync(parent_fd)" in line
                )
                for tail in terminal_post_publish_tails
            )
        )
        checks[
            "existing_terminal_durability_paths_revalidate_before_return_or_raise"
        ] = (
            recovery_durability_then_continuity >= 5
            and "existing transaction is terminal FAIL before intent"
            in recovery_source
            and '"status": "ALREADY_TERMINAL_PASS"' in recovery_source
            and "existing_terminal_is_fail" in recovery_source
        )
        checks[
            "production_trust_lease_is_held_and_revalidated_through_pass_fail_terminal"
        ] = (
            core_source.count(
                "revalidate_production_trust(auth, trust_lease)"
            ) >= 5
            and recovery_source.count(
                "revalidate_production_trust(auth, trust_lease)"
            ) >= 3
            and "finally:" in production_entry_source
            and "trust_lease.close()" in production_entry_source
            and "authorization_lease.close()" in production_entry_source
            and production_entry_source.rfind(
                "revalidate_production_trust(auth, trust_lease)"
            ) > production_entry_source.find("result = _execute_transaction_core(")
            and production_entry_source.rfind(
                "revalidate_production_trust(auth, trust_lease)"
            ) < production_entry_source.find("return result")
            and "FrozenFileLease" in source
            and "FrozenDirectoryLease" in source
        )
        production_terminal_source = inspect.getsource(
            builder.publish_terminal_linux_otmpfile_noreplace
        )
        before_link_source = production_terminal_source.split(
            "_linux_linkat_proc_self_fd_follow", 1
        )[0]
        checks["production_terminal_uses_anonymous_complete_inode_no_pathname_fallback"] = (
            "O_TMPFILE" in production_terminal_source
            and "_linux_linkat_proc_self_fd_follow" in production_terminal_source
            and "O_CREAT" not in production_terminal_source
            and "write_bytes_at_exclusive" not in production_terminal_source
            and before_link_source.find("os.fchmod(tmp_fd, 0o444)")
            < before_link_source.find("os.fsync(tmp_fd)")
            and before_link_source.find("os.fsync(tmp_fd)") >= 0
            and "no pathname fallback" in production_terminal_source
            and "probe_terminal_publication_linux_xfs" in source
        )
        proc_link_source = inspect.getsource(
            builder._linux_linkat_proc_self_fd_follow
        )
        proc_verify_source = inspect.getsource(builder._verified_proc_self_fd_path)
        checks[
            "production_terminal_uses_unprivileged_proc_self_fd_linkat_follow_only"
        ] = (
            "AT_FDCWD" in proc_link_source
            and "PROC_SELF_FD_PATH" in proc_verify_source
            and "AT_SYMLINK_FOLLOW" in proc_link_source
            and "AT_EMPTY_PATH" not in source
            and 'ctypes.c_char_p(b"")' not in proc_link_source
            and "_linux_linkat_empty_path" not in source
        )
        checks["proc_self_fd_reference_is_held_inode_and_procfs_verified"] = (
            "PROC_SUPER_MAGIC" in proc_verify_source
            and "follow_symlinks=False" in proc_verify_source
            and "follow_symlinks=True" in proc_verify_source
            and "os.fstat(source_fd)" in proc_verify_source
            and "held_before == held_after == referenced == reopened == absolute"
            in proc_verify_source
        )
        checks["at_empty_path_capability_path_removed"] = (
            "AT_EMPTY_PATH" not in source
            and "CAP_DAC_READ_SEARCH" not in source
            and builder.PRODUCTION_TERMINAL_PUBLICATION_METHOD
            == (
                "LINUX_XFS_O_TMPFILE_COMPLETE_FCHMOD0444_FSYNC_"
                "LINKAT_PROC_SELF_FD_AT_SYMLINK_FOLLOW_NOREPLACE_DIRFSYNC_V1"
            )
        )

        linkat_arguments: list[Any] = []
        proc_expectations: list[int] = []
        before_identity = builder.Identity(71, 72, 73, 74, 0o444, 0)
        after_identity = builder.Identity(71, 72, 73, 74, 0o444, 1)

        class FakeLinkat:
            argtypes: Any = None
            restype: Any = None

            def __call__(self, *args: Any) -> int:
                linkat_arguments.extend(args)
                return 0

        class FakeLibc:
            linkat = FakeLinkat()

        original_platform = builder.sys.platform
        original_verified_proc = builder._verified_proc_self_fd_path
        original_cdll = builder.ctypes.CDLL
        original_fstat = builder.os.fstat

        def fake_verified_proc(
            source_fd: int, *, expected_nlink: int
        ) -> tuple[str, Any]:
            proc_expectations.append(expected_nlink)
            return (
                f"/proc/self/fd/{source_fd}",
                before_identity if expected_nlink == 0 else after_identity,
            )

        def fake_fstat(fd: int) -> Any:
            return types.SimpleNamespace(
                st_dev=71, st_ino=72, st_size=73, st_mtime_ns=74,
                st_mode=stat.S_IFREG | 0o444, st_nlink=1,
            )

        try:
            builder.sys.platform = "linux"
            builder._verified_proc_self_fd_path = fake_verified_proc
            builder.ctypes.CDLL = lambda *args, **kwargs: FakeLibc()
            builder.os.fstat = fake_fstat
            builder._linux_linkat_proc_self_fd_follow(19, 23, "TERMINAL.json")
        finally:
            builder.os.fstat = original_fstat
            builder.ctypes.CDLL = original_cdll
            builder._verified_proc_self_fd_path = original_verified_proc
            builder.sys.platform = original_platform
        checks[
            "proc_self_fd_linkat_syscall_arguments_are_exact_and_no_clobber"
        ] = (
            proc_expectations == [0, 1]
            and len(linkat_arguments) == 5
            and linkat_arguments[0] == builder.AT_FDCWD
            and linkat_arguments[1].value == b"/proc/self/fd/19"
            and linkat_arguments[2] == 23
            and linkat_arguments[3].value == b"TERMINAL.json"
            and linkat_arguments[4] == builder.AT_SYMLINK_FOLLOW
        )

        for path in (
            final, lock_final, invalid_after_publish_final,
            swap_journal / "ORIGINAL_STAGING_PRESERVED",
            api_work_root / "compat_runtime_root",
            api_work_root / ".result-free-transport-v10.synthetic-native",
        ):
            thaw(path)

    pass_count = sum(value is True for value in checks.values())
    payload = {
        "schema": "historical_200k_fixed10k_transport_runtime_builder_v10_synthetic_test_v1",
        "status": "PASS" if pass_count == len(checks) else "FAIL",
        "check_count": len(checks),
        "pass_count": pass_count,
        "fail_count": len(checks) - pass_count,
        "checks": checks,
        "receipt_schema": {
            "schema": builder.BUILD_PASS_RECEIPT_SCHEMA,
            "status": builder.BUILD_PASS_RECEIPT_STATUS,
            "top_level_keys": sorted(details.get("positive_receipt", {})),
        },
        "terminal_continuity_regression": {
            "publisher_paths": details.get(
                "terminal_named_parent_continuity_matrix", {}
            ),
            "existing_terminal_durability": details.get(
                "existing_terminal_durability_matrix", {}
            ),
        },
        "linux_integration": (
            "NOT_RUN_NON_LINUX" if not sys.platform.startswith("linux")
            else "SYNTHETIC_RENAME_INJECTED_NOT_PRODUCTION"
        ),
        "scope": {
            "mars_accessed": False,
            "network_accessed": False,
            "results_accessed": False,
            "processes_inspected": False,
            "signals_sent": False,
            "controller_or_outer_main_executed": False,
            "production_root_created": False,
            "synthetic_temp_only": True,
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
