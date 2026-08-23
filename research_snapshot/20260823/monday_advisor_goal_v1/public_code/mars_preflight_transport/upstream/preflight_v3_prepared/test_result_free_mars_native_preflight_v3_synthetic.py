#!/usr/bin/env python3
"""Local result-blind hostile fixtures for prepared-only preflight v3.

No MARS path is opened.  All mutable fixtures live in a disposable local
temporary directory.  The real v10 API and Linux O_TMPFILE publisher are not
executed here.
"""

from __future__ import annotations

import copy
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "run_result_free_mars_native_preflight_v3.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("preflight_v3_prepared_only", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load preflight module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = load_module()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rejected(call: Callable[[], Any]) -> bool:
    try:
        call()
    except (preflight.PreflightError, OSError, FileExistsError):
        return True
    return False


def frozen_file(path: Path, label: str) -> tuple[str, str]:
    data = (label + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o444)
    return os.fspath(path.resolve()), digest(data)


def frozen_bytes(path: Path, data: bytes, *, mode: int = 0o444) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return os.fspath(path.resolve()), digest(data)


def all_false_authority() -> dict[str, bool]:
    return {key: False for key in preflight.QA_AUTHORITY_KEYS}


def write_exact_manifest_package(
    root: Path,
    *,
    payload: dict[str, bytes],
    manifest_schema: str,
    receipt_name: str,
    receipt: dict[str, Any] | Callable[[str], dict[str, Any]],
    status: str,
    qa: bool,
) -> None:
    """Create a disposable exact manifest/receipt/index closure."""

    root.mkdir(parents=True)
    for name, data in payload.items():
        frozen_bytes(root / name, data)
    payload_records = [
        {
            "relative_path": name,
            "role": "synthetic_exact_closure_fixture",
            "sha256": digest(payload[name]),
            "size_bytes": len(payload[name]),
        }
        for name in sorted(payload)
    ]
    closure_names = sorted({"BUNDLE_MANIFEST.json", receipt_name, "SHA256SUMS"})
    manifest: dict[str, Any] = {
        "schema": manifest_schema,
        "status": status,
        "created_utc": "2026-08-22T00:00:00Z",
        "payload_file_count": len(payload),
        "files": payload_records,
        "closure_files_not_in_payload_manifest": closure_names,
        "authority": all_false_authority(),
    }
    if qa:
        manifest["action_scoped_verdict"] = preflight.QA_GO_VERDICT
        manifest["finding_counts"] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    manifest_bytes = preflight.canonical_json_bytes(manifest)
    frozen_bytes(root / "BUNDLE_MANIFEST.json", manifest_bytes)
    resolved_receipt = receipt(digest(manifest_bytes)) if callable(receipt) else receipt
    frozen_bytes(root / receipt_name, preflight.canonical_json_bytes(resolved_receipt))
    index_names = sorted(set(payload) | {"BUNDLE_MANIFEST.json", receipt_name})
    index_data = "".join(
        f"{digest((root / name).read_bytes())}  {name}\n" for name in index_names
    ).encode("utf-8")
    frozen_bytes(root / "SHA256SUMS", index_data)
    root.chmod(0o555)


def make_exact_self_closures(base: Path, source_bytes: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared_root = base / "preflight-v3-prepared"
    prepared_payload = {
        "AUTHOR_COMPILE_V3_OUTPUT.json": preflight.canonical_json_bytes({"status": "PASS"}),
        "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json": preflight.canonical_json_bytes({"status": "PASS"}),
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json": preflight.canonical_json_bytes({
            "root_bootstrap_sha256": preflight.ROOT_BOOTSTRAP_SHA256,
            "schema": "synthetic_preflight_v3_contract_fixture",
        }),
        "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V3_CN.md": b"synthetic fixture only\n",
        "UPSTREAM_EVIDENCE_BINDINGS_V3.json": preflight.canonical_json_bytes({"schema": "fixture"}),
        "run_result_free_mars_native_preflight_v3.py": source_bytes,
        "test_result_free_mars_native_preflight_v3_synthetic.py": b"# synthetic fixture\n",
    }
    def prepared_receipt(manifest_sha: str) -> dict[str, Any]:
        source = prepared_payload["run_result_free_mars_native_preflight_v3.py"]
        test = prepared_payload["test_result_free_mars_native_preflight_v3_synthetic.py"]
        return {
            "schema": preflight.PREPARED_RECEIPT_SCHEMA,
            "status": preflight.PREPARED_STATUS,
            "created_utc": "2026-08-22T00:00:00Z",
            "package_directory": prepared_root.name,
            "package_closure": {
                "bundle_manifest_sha256": manifest_sha,
                "payload_file_count": 7,
                "sha_index_listed_count_expected": 9,
                "top_level_file_count_expected": 10,
            },
            "locked_tools": {
                "preflight": {
                    "path": "run_result_free_mars_native_preflight_v3.py",
                    "sha256": digest(source),
                    "line_count": len(source.splitlines()),
                },
                "synthetic_test": {
                    "path": "test_result_free_mars_native_preflight_v3_synthetic.py",
                    "sha256": digest(test),
                    "line_count": len(test.splitlines()),
                },
            },
            "author_validation": {
                "darwin_actual": "NOT_RUN_SYNTHETIC_FIXTURE",
                "linux_xfs_actual": "NOT_RUN_SYNTHETIC_FIXTURE",
                "manifest_payload_hash_and_size_pass": True,
                "source_compile": {
                    "checked": 2, "failed": 0, "passed": 2,
                    "output_sha256": digest(
                        prepared_payload["AUTHOR_COMPILE_V3_OUTPUT.json"]
                    ),
                },
                "strict_json_parse_pass": True,
                "synthetic_test": {
                    "checked": 1, "failed": 0, "passed": 1,
                    "raw_output_sha256": digest(
                        prepared_payload[
                            "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json"
                        ]
                    ),
                },
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
            "authority": all_false_authority(),
            "next_legal_action": "INDEPENDENT_QA",
        }
    write_exact_manifest_package(
        prepared_root,
        payload=prepared_payload,
        manifest_schema=preflight.PREPARED_MANIFEST_SCHEMA,
        receipt_name="PREPARED_RESULT_FREE_RECEIPT.json",
        receipt=prepared_receipt,
        status=preflight.PREPARED_STATUS,
        qa=False,
    )
    package = {
        "prepared_receipt_path": os.fspath((prepared_root / "PREPARED_RESULT_FREE_RECEIPT.json").resolve()),
        "prepared_receipt_sha256": digest((prepared_root / "PREPARED_RESULT_FREE_RECEIPT.json").read_bytes()),
        "bundle_manifest_path": os.fspath((prepared_root / "BUNDLE_MANIFEST.json").resolve()),
        "bundle_manifest_sha256": digest((prepared_root / "BUNDLE_MANIFEST.json").read_bytes()),
        "sha256_index_path": os.fspath((prepared_root / "SHA256SUMS").resolve()),
        "sha256_index_sha256": digest((prepared_root / "SHA256SUMS").read_bytes()),
    }

    qa_root = base / "preflight-v3-independent-qa"
    qa_payload = {
        "COMMAND_LOG.txt": b"synthetic fixture only\n",
        "INDEPENDENT_QA_OUTPUT.json": preflight.canonical_json_bytes({"status": "PASS"}),
        "INDEPENDENT_QA_REPORT_CN.md": b"synthetic fixture only\n",
        "PACKAGE_CLOSURE_QA.json": preflight.canonical_json_bytes({"status": "PASS"}),
        "QA_HARNESS_OR_METHOD.md": b"synthetic fixture only\n",
    }
    def qa_receipt(manifest_sha: str) -> dict[str, Any]:
        artifact_names = {
            "closure": "PACKAGE_CLOSURE_QA.json",
            "harness": "QA_HARNESS_OR_METHOD.md",
            "log": "COMMAND_LOG.txt",
            "manifest": "BUNDLE_MANIFEST.json",
            "output": "INDEPENDENT_QA_OUTPUT.json",
            "report": "INDEPENDENT_QA_REPORT_CN.md",
        }
        artifact_hashes = {
            name: manifest_sha if name == "BUNDLE_MANIFEST.json"
            else digest(qa_payload[name])
            for name in artifact_names.values()
        }
        return {
            "schema": preflight.QA_RECEIPT_SCHEMA,
            "status": preflight.QA_GO_STATUS,
            "created_utc": "2026-08-22T00:00:00Z",
            "qa_directory": qa_root.name,
            "action_scoped_verdict": preflight.QA_GO_VERDICT,
            "audited_package": {
                "bundle_manifest_sha256": package["bundle_manifest_sha256"],
                "contract_sha256": digest(prepared_payload["RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json"]),
                "directory": prepared_root.name,
                "evidence_bindings_sha256": digest(prepared_payload["UPSTREAM_EVIDENCE_BINDINGS_V3.json"]),
                "prepared_receipt_sha256": package["prepared_receipt_sha256"],
                "script_sha256": digest(prepared_payload["run_result_free_mars_native_preflight_v3.py"]),
                "sha256_index_sha256": package["sha256_index_sha256"],
                "test_sha256": digest(prepared_payload["test_result_free_mars_native_preflight_v3_synthetic.py"]),
            },
            "qa_artifacts": {
                stem: {"path": name, "sha256": artifact_hashes[name]}
                for stem, name in artifact_names.items()
            },
            "independent_validation": {"synthetic_fixture": "PASS"},
            "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
            "authority": all_false_authority(),
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
            "next_legal_action": "SEPARATE_EXACT_ROOT_AUTHORIZATION",
        }
    write_exact_manifest_package(
        qa_root,
        payload=qa_payload,
        manifest_schema=preflight.QA_MANIFEST_SCHEMA,
        receipt_name="INDEPENDENT_QA_RECEIPT.json",
        receipt=qa_receipt,
        status=preflight.QA_GO_STATUS,
        qa=True,
    )
    audit_names = {
        "report": "INDEPENDENT_QA_REPORT_CN.md",
        "receipt": "INDEPENDENT_QA_RECEIPT.json",
        "output": "INDEPENDENT_QA_OUTPUT.json",
        "log": "COMMAND_LOG.txt",
        "harness": "QA_HARNESS_OR_METHOD.md",
        "closure": "PACKAGE_CLOSURE_QA.json",
        "bundle_manifest": "BUNDLE_MANIFEST.json",
        "sha256_index": "SHA256SUMS",
    }
    audit: dict[str, Any] = {}
    for stem, name in audit_names.items():
        audit[f"{stem}_path"] = os.fspath((qa_root / name).resolve())
        audit[f"{stem}_sha256"] = digest((qa_root / name).read_bytes())
    return package, audit


def make_bound_closure(base: Path) -> Any:
    package, audit = make_exact_self_closures(
        base / "self-closures", SOURCE.read_bytes()
    )

    v10_root = base / "v10-package"
    v10_root.mkdir(parents=True)
    v10_payload_names = preflight.V10_PACKAGE_INDEX_MEMBERS - {
        "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
    }
    for name in sorted(v10_payload_names):
        frozen_bytes(v10_root / name, ("v10-fixture:" + name + "\n").encode())
    v10_manifest = {
        "schema": preflight.V10_PREPARED_MANIFEST_SCHEMA,
        "status": preflight.V10_PREPARED_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "payload_file_count": len(v10_payload_names),
        "files": [
            {
                "relative_path": name,
                "role": "synthetic_v10_upstream_fixture",
                "sha256": digest((v10_root / name).read_bytes()),
                "size_bytes": len((v10_root / name).read_bytes()),
            }
            for name in sorted(v10_payload_names)
        ],
        "closure_files_not_in_payload_manifest": [
            "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
            "SHA256SUMS",
        ],
        "authority": all_false_authority(),
    }
    frozen_bytes(
        v10_root / "BUNDLE_MANIFEST.json",
        preflight.canonical_json_bytes(v10_manifest),
    )
    v10_receipt = {
        "schema": preflight.V10_PREPARED_RECEIPT_SCHEMA,
        "status": preflight.V10_PREPARED_STATUS,
        "package_directory": v10_root.name,
        "package_closure": {
            "bundle_manifest_sha256": digest(
                (v10_root / "BUNDLE_MANIFEST.json").read_bytes()
            ),
            "payload_file_count": 12,
            "sha_index_listed_count_expected": 14,
            "top_level_file_count_expected": 15,
        },
        "authority": all_false_authority(),
    }
    frozen_bytes(
        v10_root / "PREPARED_RESULT_FREE_RECEIPT.json",
        preflight.canonical_json_bytes(v10_receipt),
    )
    v10_index = "".join(
        f"{digest((v10_root / name).read_bytes())}  {name}\n"
        for name in sorted(preflight.V10_PACKAGE_INDEX_MEMBERS)
    ).encode()
    frozen_bytes(v10_root / "SHA256SUMS", v10_index)
    v10_root.chmod(0o555)
    v10_names = {
        "builder": "build_result_free_transport_runtime_v10.py",
        "builder_test": "test_transport_runtime_layout_builder_v10_synthetic.py",
        "smoke": "result_free_runtime_smoke_v10.py",
        "smoke_test": "test_result_free_runtime_smoke_v10_synthetic.py",
        "prepared_receipt": "PREPARED_RESULT_FREE_RECEIPT.json",
        "bundle_manifest": "BUNDLE_MANIFEST.json",
        "sha256_index": "SHA256SUMS",
    }
    v10_package: dict[str, Any] = {}
    for stem, name in v10_names.items():
        v10_package[f"{stem}_path"] = os.fspath((v10_root / name).resolve())
        v10_package[f"{stem}_sha256"] = digest((v10_root / name).read_bytes())

    v10_qa_root = base / "v10-audit"
    v10_qa_root.mkdir(parents=True)
    v10_qa_payload_names = preflight.V10_QA_INDEX_MEMBERS - {
        "BUNDLE_MANIFEST.json"
    }
    for name in sorted(v10_qa_payload_names):
        frozen_bytes(v10_qa_root / name, ("v10-qa-fixture:" + name + "\n").encode())
    v10_qa_manifest = {
        "schema": preflight.V10_QA_MANIFEST_SCHEMA,
        "status": preflight.V10_QA_MANIFEST_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "payload_file_count": len(v10_qa_payload_names),
        "files": [
            {
                "relative_path": name,
                "role": "synthetic_v10_qa_fixture",
                "sha256": digest((v10_qa_root / name).read_bytes()),
                "size_bytes": len((v10_qa_root / name).read_bytes()),
            }
            for name in sorted(v10_qa_payload_names)
        ],
        "closure_files_not_in_payload_manifest": [
            "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
        ],
        "action_scoped_verdict": preflight.V10_QA_ACTION_SCOPED_VERDICT,
        "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        "authority": all_false_authority(),
    }
    frozen_bytes(
        v10_qa_root / "BUNDLE_MANIFEST.json",
        preflight.canonical_json_bytes(v10_qa_manifest),
    )
    v10_qa_receipt = {
        "schema": preflight.V10_QA_RECEIPT_SCHEMA,
        "status": preflight.V10_QA_RECEIPT_STATUS,
        "qa_directory": v10_qa_root.name,
        "action_scoped_verdict": preflight.V10_QA_ACTION_SCOPED_VERDICT,
        "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        "authority": all_false_authority(),
        "audited_candidate": {
            "builder_sha256": v10_package["builder_sha256"],
            "bundle_manifest_sha256": v10_package["bundle_manifest_sha256"],
            "directory": v10_root.name,
            "indexed_count": 14,
            "payload_count": 12,
            "prepared_receipt_sha256": v10_package["prepared_receipt_sha256"],
            "sha256_index_sha256": v10_package["sha256_index_sha256"],
            "smoke_sha256": v10_package["smoke_sha256"],
            "smoke_test_sha256": v10_package["smoke_test_sha256"],
            "test_sha256": v10_package["builder_test_sha256"],
            "top_level_count": 15,
        },
    }
    frozen_bytes(
        v10_qa_root / "INDEPENDENT_QA_RECEIPT.json",
        preflight.canonical_json_bytes(v10_qa_receipt),
    )
    v10_qa_index = "".join(
        f"{digest((v10_qa_root / name).read_bytes())}  {name}\n"
        for name in sorted(preflight.V10_QA_INDEX_MEMBERS)
    ).encode()
    frozen_bytes(v10_qa_root / "SHA256SUMS", v10_qa_index)
    v10_qa_root.chmod(0o555)
    v10_qa_names = {
        "report": "INDEPENDENT_QA_REPORT_CN.md",
        "receipt": "INDEPENDENT_QA_RECEIPT.json",
        "output": "INDEPENDENT_QA_OUTPUT.json",
        "log": "COMMAND_LOG.txt",
        "harness": "INDEPENDENT_QA_HARNESS.py",
        "closure": "PACKAGE_CLOSURE_QA.json",
        "bundle_manifest": "BUNDLE_MANIFEST.json",
        "sha256_index": "SHA256SUMS",
    }
    v10_audit: dict[str, Any] = {}
    for stem, name in v10_qa_names.items():
        v10_audit[f"{stem}_path"] = os.fspath((v10_qa_root / name).resolve())
        v10_audit[f"{stem}_sha256"] = digest((v10_qa_root / name).read_bytes())
    v10_audit["action_scoped_verdict"] = preflight.V10_QA_ACTION_SCOPED_VERDICT
    v10_audit["finding_counts"] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    return preflight.FrozenBindings(package, audit, v10_package, v10_audit)


def dummy_identity(seed: int, mode: str = "0444") -> dict[str, Any]:
    return {
        "device": seed,
        "inode": seed + 1,
        "size_bytes": seed + 2,
        "mtime_ns": seed + 3,
        "ctime_ns": seed + 4,
        "mode": mode,
        "nlink": 1,
    }


def authorization_fixture(
    bindings: Any,
) -> tuple[dict[str, Any], bytes, str]:
    auth = {
        "schema": preflight.AUTH_SCHEMA,
        "status": preflight.AUTH_STATUS,
        "created_utc": "2026-08-22T00:00:00Z",
        "decision_id": preflight.EXPECTED_DECISION_ID,
        "preflight_package_manifest_path": bindings.preflight_package[
            "bundle_manifest_path"
        ],
        "preflight_package_manifest_sha256": bindings.preflight_package[
            "bundle_manifest_sha256"
        ],
        "preflight_package_index_path": bindings.preflight_package[
            "sha256_index_path"
        ],
        "preflight_package_index_sha256": bindings.preflight_package[
            "sha256_index_sha256"
        ],
        "preflight_independent_audit_receipt_path": bindings.preflight_audit[
            "receipt_path"
        ],
        "preflight_independent_audit_receipt_sha256": bindings.preflight_audit[
            "receipt_sha256"
        ],
        "preflight_independent_audit_index_path": bindings.preflight_audit[
            "sha256_index_path"
        ],
        "preflight_independent_audit_index_sha256": bindings.preflight_audit[
            "sha256_index_sha256"
        ],
        "authority": {
            "preflight_launch_authorized": True,
            "transport_runtime_layout_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "deployment_or_resume_authorized": False,
        },
    }
    raw = preflight.canonical_json_bytes(auth)
    return auth, raw, digest(raw)


def bootstrap_context(
    auth_sha: str,
    *,
    held_source: Path | None = None,
    held_interpreter: Path | None = None,
) -> dict[str, Any]:
    source_identity = (
        preflight.FileIdentity.from_stat(held_source.stat()).json()
        if held_source is not None else dummy_identity(20)
    )
    interpreter_identity = (
        preflight.FileIdentity.from_stat(held_interpreter.stat()).json()
        if held_interpreter is not None else dummy_identity(10, "0755")
    )
    source_sha = (
        digest(held_source.read_bytes()) if held_source is not None else digest(b"source")
    )
    interpreter_sha = (
        digest(held_interpreter.read_bytes())
        if held_interpreter is not None else digest(b"interpreter")
    )
    return {
        "protocol": "HELD_FD197_198_199_PRECOMPILE_ROOT_BOOTSTRAP_V3",
        "bootstrap_sha256": preflight.ROOT_BOOTSTRAP_SHA256,
        "proc_argv": [
            "/proc/self/fd/197", "-I", "-B", "-S", "-c",
            preflight.ROOT_BOOTSTRAP_TEXT,
            "--trusted-authorization-sha256", auth_sha,
            "--trusted-preflight-source-sha256", source_sha,
            "--trusted-interpreter-sha256", interpreter_sha,
        ],
        "interpreter_fd": 197,
        "source_fd": 198,
        "authorization_fd": 199,
        "interpreter_identity": interpreter_identity,
        "source_identity": source_identity,
        "authorization_identity": dummy_identity(30),
        "interpreter_sha256": interpreter_sha,
        "source_sha256": source_sha,
        "authorization_sha256": auth_sha,
    }


def marker_source(marker: Path) -> bytes:
    return (
        "from pathlib import Path\n"
        f"Path({os.fspath(marker)!r}).write_text('EXECUTED\\n', encoding='utf-8')\n"
        "def held_preflight_main(_context, _authorization_bytes, _argv):\n"
        "    return 0\n"
    ).encode("utf-8")


def run_root_bootstrap_fixture(
    *,
    source_path: Path,
    authorization_path: Path,
    trusted_authorization_sha256: str,
    declared_source_sha256: str,
    bootstrap_text: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run exact Linux bootstrap text with only /proc reads locally mocked."""

    baseline_fds: set[int] = set()
    for candidate_fd in range(3, 512):
        try:
            fcntl.fcntl(candidate_fd, fcntl.F_GETFD)
            baseline_fds.add(candidate_fd)
        except OSError:
            pass
    interpreter = Path(sys.executable).resolve()
    interpreter_sha = digest(interpreter.read_bytes())
    opened = [
        os.open(interpreter, os.O_RDONLY),
        os.open(source_path, os.O_RDONLY),
        os.open(authorization_path, os.O_RDONLY),
    ]
    logical_argv = [
        "--trusted-authorization-sha256",
        trusted_authorization_sha256,
        "--trusted-preflight-source-sha256",
        declared_source_sha256,
        "--trusted-interpreter-sha256",
        interpreter_sha,
    ]
    selected_bootstrap = (
        preflight.ROOT_BOOTSTRAP_TEXT
        if bootstrap_text is None else bootstrap_text
    )
    proc_argv = [
        "/proc/self/fd/197", "-I", "-B", "-S", "-c",
        selected_bootstrap,
    ] + logical_argv
    proc_bytes = b"\0".join(item.encode("utf-8") for item in proc_argv) + b"\0"
    proc_read, proc_write = os.pipe()
    os.write(proc_write, proc_bytes)
    os.close(proc_write)
    targets = (
        preflight.HELD_INTERPRETER_FD,
        preflight.HELD_PREFLIGHT_SOURCE_FD,
        preflight.HELD_AUTHORIZATION_FD,
    )
    saved: dict[int, tuple[int, int] | None] = {}
    for target in targets:
        try:
            saved[target] = (
                os.dup(target),
                fcntl.fcntl(target, fcntl.F_GETFD),
            )
        except OSError:
            saved[target] = None
    real_open = os.open
    real_argv = sys.argv
    stderr = io.StringIO()

    def fixture_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        lexical = os.fspath(path)
        if lexical == "/proc/self/cmdline":
            return os.dup(proc_read)
        if lexical == "/proc/self/exe":
            return os.dup(preflight.HELD_INTERPRETER_FD)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    returncode = 1
    try:
        for source_fd, target_fd in zip(opened, targets):
            os.dup2(source_fd, target_fd, inheritable=True)
        os.open = fixture_open
        sys.argv = ["-c"] + logical_argv
        with contextlib.redirect_stderr(stderr):
            try:
                exec(
                    compile(
                        selected_bootstrap,
                        "<synthetic-root-bootstrap>",
                        "exec",
                        dont_inherit=True,
                    ),
                    {"__name__": "__main__"},
                )
            except SystemExit as exc:
                returncode = 0 if exc.code is None else int(exc.code)
            except BaseException as exc:
                stderr.write(type(exc).__name__ + ": " + str(exc) + "\n")
                returncode = 1
    finally:
        os.open = real_open
        sys.argv = real_argv
        os.close(proc_read)
        for fd in opened:
            os.close(fd)
        for target, prior in saved.items():
            try:
                os.close(target)
            except OSError:
                pass
            if prior is not None:
                backup, flags = prior
                os.dup2(backup, target, inheritable=True)
                fcntl.fcntl(target, fcntl.F_SETFD, flags)
                os.close(backup)
        for candidate_fd in range(3, 512):
            if candidate_fd in baseline_fds:
                continue
            try:
                os.close(candidate_fd)
            except OSError:
                pass
    return subprocess.CompletedProcess(
        args=proc_argv,
        returncode=returncode,
        stdout=b"",
        stderr=stderr.getvalue().encode("utf-8"),
    )


class FakeBuilder:
    NATIVE_COMPATIBILITY_API_SCHEMA = preflight.API_SCHEMA
    calls = 0

    @staticmethod
    def renameat2_noreplace(*_args: Any, **_kwargs: Any) -> None:
        return None

    @staticmethod
    def publish_terminal_linux_otmpfile_noreplace(*_args: Any, **_kwargs: Any) -> None:
        return None

    @staticmethod
    def execute_scoped_noncanonical_native_compatibility_preflight_v1(
        *, request: dict[str, Any], production_parent_fd: int,
        compatibility_work_root_fd: int, rename_impl: Any,
        terminal_publish_impl: Any,
    ) -> dict[str, Any]:
        FakeBuilder.calls += 1
        if not callable(rename_impl) or not callable(terminal_publish_impl):
            raise RuntimeError("real functions missing")
        if type(production_parent_fd) is not int or type(compatibility_work_root_fd) is not int:
            raise RuntimeError("held dirfds missing")
        try:
            work_info = os.fstat(compatibility_work_root_fd)
        except OSError:
            work_info = None
        if work_info is not None and stat.S_ISDIR(work_info.st_mode):
            for path_key in ("compatibility_root", "compatibility_journal"):
                os.mkdir(
                    Path(request[path_key]).name, 0o700,
                    dir_fd=compatibility_work_root_fd,
                )
            os.fsync(compatibility_work_root_fd)
        return {
            "schema": preflight.API_SCHEMA,
            "status": preflight.API_STATUS,
            "scope": preflight.API_SCOPE,
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


class FailingBuilder(FakeBuilder):
    calls = 0

    @staticmethod
    def execute_scoped_noncanonical_native_compatibility_preflight_v1(
        *, request: dict[str, Any], production_parent_fd: int,
        compatibility_work_root_fd: int, rename_impl: Any,
        terminal_publish_impl: Any,
    ) -> dict[str, Any]:
        del request, production_parent_fd, compatibility_work_root_fd
        del rename_impl, terminal_publish_impl
        FailingBuilder.calls += 1
        raise preflight.PreflightError("synthetic scoped API failure")


def synthetic_atomic_publish(
    directory: Path, name: str, data: bytes,
    *, before_link_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Disposable fixture only; never imported by the production source."""

    temp = directory / (".fixture-" + digest(data + os.urandom(8))[:16])
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)
    if before_link_hook is not None:
        before_link_hook()
    canonical = directory / name
    os.link(temp, canonical)
    os.unlink(temp)
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    info = canonical.stat()
    return {
        "sha256": digest(data), "mode": stat.S_IMODE(info.st_mode),
        "nlink": info.st_nlink, "size": info.st_size,
    }


def synthetic_fd_atomic_publish(
    directory_fd: int,
    name: str,
    data: bytes,
    *,
    after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Local disposable dirfd publisher with production visibility ordering."""

    if not name or "/" in name or name in {".", ".."}:
        raise preflight.PreflightError("fixture canonical basename required")
    temp_name = ".fixture-" + digest(data + os.urandom(16))[:24]
    fd = os.open(
        temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise RuntimeError("fixture short write")
            offset += written
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        os.link(
            temp_name, name,
            src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.unlink(temp_name, dir_fd=directory_fd)
        temp_name = ""
        if after_link_before_dir_fsync_hook is not None:
            after_link_before_dir_fsync_hook()
        os.fsync(directory_fd)
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o444
            or info.st_nlink != 1
            or info.st_size != len(data)
        ):
            raise RuntimeError("fixture publication identity mismatch")
        return {
            "method": preflight.PRODUCTION_EVIDENCE_PUBLICATION_METHOD,
            "canonical_visibility_rule": preflight.EVIDENCE_VISIBILITY_RULE,
            "name": name,
            "sha256": digest(data),
            "size_bytes": len(data),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": "0444",
            "nlink": 1,
        }
    finally:
        os.close(fd)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


class TransactionFixture:
    def __init__(self, base: Path, name: str) -> None:
        self.root = base / name
        self.root.mkdir(mode=0o700)
        self.parent_fd = os.open(self.root, os.O_RDONLY)
        self.production = self.root / "production-parent"
        self.production.mkdir(mode=0o700)
        self.production_fd = os.open(self.production, os.O_RDONLY)
        self.work = preflight.MutableDirectoryLease.create_or_open_at(
            self.parent_fd, self.root, "work", name + ".work"
        )
        self.evidence = preflight.MutableDirectoryLease.create_or_open_at(
            self.work.fd, self.work.path, "evidence", name + ".evidence"
        )
        anchor_path = self.root / "immutable" / "anchor.json"
        anchor_path.parent.mkdir()
        anchor_data = preflight.canonical_json_bytes({"fixture": name})
        frozen_bytes(anchor_path, anchor_data)
        self.immutable = preflight.EvidenceLeaseSet()
        self.immutable.add_file(anchor_path, digest(anchor_data), name + ".anchor")

    def close(self) -> None:
        self.immutable.close()
        self.evidence.close()
        self.work.close()
        os.close(self.production_fd)
        os.close(self.parent_fd)

    def run(
        self,
        *,
        auth_sha: str,
        builder: Any = FakeBuilder,
        publisher: Callable[..., dict[str, Any]] = synthetic_fd_atomic_publish,
    ) -> str:
        return preflight.execute_preflight_transaction(
            builder=builder,
            auth_sha=auth_sha,
            production_parent_fd=self.production_fd,
            work_root=self.work,
            evidence=self.evidence,
            immutable_leases=self.immutable,
            publisher=publisher,
        )

    def members(self) -> set[str]:
        return set(os.listdir(self.evidence.fd))

    def read(self, name: str) -> dict[str, Any] | None:
        return preflight.read_evidence_record(self.evidence.fd, name, name)


class RevalidateHookLeaseSet:
    """Test-only proxy that mutates after one selected lease revalidation."""

    def __init__(
        self,
        delegate: Any,
        trigger_phase: str,
        hook: Callable[[], None],
    ) -> None:
        self.delegate = delegate
        self.trigger_phase = trigger_phase
        self.hook = hook
        self.fired = False

    def revalidate(self, phase: str) -> None:
        self.delegate.revalidate(phase)
        if phase == self.trigger_phase and not self.fired:
            self.fired = True
            self.hook()

    def close(self) -> None:
        self.delegate.close()


def replace_or_alias_named_directory(
    lease: Any,
    *,
    alias: bool,
    suffix: str,
) -> Path:
    """Rename the held directory, then replace its canonical name."""

    backup = lease.path.with_name(lease.path.name + "-held-" + suffix)
    os.rename(lease.path, backup)
    if alias:
        os.symlink(backup.name, lease.path, target_is_directory=True)
    else:
        os.mkdir(lease.path, lease.mode)
    return backup


def terminal_continuity_fixture(
    base: Path,
    *,
    auth_sha: str,
    state: str,
    target: str,
    alias: bool,
) -> tuple[bool, bool]:
    """Exercise one post-terminal named-parent replacement/alias case."""

    case = f"terminal-{state}-{target}-{'alias' if alias else 'replace'}"
    txn = TransactionFixture(base, case)
    terminal_name = (
        preflight.PASS_NAME
        if state in {"first_pass", "intent_recovery_pass", "existing_pass"}
        else preflight.FAIL_NAME
    )
    selected = txn.work if target == "work" else txn.evidence
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        if mutated:
            return
        replace_or_alias_named_directory(
            selected, alias=alias, suffix=case
        )
        mutated = True

    def terminal_publisher(
        directory_fd: int,
        name: str,
        data: bytes,
    ) -> dict[str, Any]:
        return synthetic_fd_atomic_publish(
            directory_fd,
            name,
            data,
            after_link_before_dir_fsync_hook=(
                mutate if name == terminal_name else None
            ),
        )

    try:
        if state in {"begin_only_fail", "intent_recovery_pass"}:
            begin = preflight.make_begin(auth_sha)
            synthetic_fd_atomic_publish(
                txn.evidence.fd,
                preflight.BEGIN_NAME,
                preflight.canonical_json_bytes(begin),
            )
            if state == "intent_recovery_pass":
                intent = preflight.make_intent(
                    begin, preflight.make_compatibility_request(auth_sha)
                )
                synthetic_fd_atomic_publish(
                    txn.evidence.fd,
                    preflight.INTENT_NAME,
                    preflight.canonical_json_bytes(intent),
                )
            continuity_rejected = rejected(
                lambda: txn.run(auth_sha=auth_sha, publisher=terminal_publisher)
            )
        elif state == "exception_fail":
            continuity_rejected = rejected(
                lambda: txn.run(
                    auth_sha=auth_sha,
                    builder=FailingBuilder,
                    publisher=terminal_publisher,
                )
            )
        elif state == "first_pass":
            continuity_rejected = rejected(
                lambda: txn.run(auth_sha=auth_sha, publisher=terminal_publisher)
            )
        elif state == "existing_pass":
            if txn.run(auth_sha=auth_sha) != "TERMINAL_PASS":
                return False, False
            txn.immutable = RevalidateHookLeaseSet(
                txn.immutable, "transaction.existing_terminal", mutate
            )
            continuity_rejected = rejected(lambda: txn.run(auth_sha=auth_sha))
        elif state == "existing_fail":
            if not rejected(
                lambda: txn.run(auth_sha=auth_sha, builder=FailingBuilder)
            ):
                return False, False
            txn.immutable = RevalidateHookLeaseSet(
                txn.immutable, "transaction.existing_terminal", mutate
            )
            continuity_rejected = rejected(lambda: txn.run(auth_sha=auth_sha))
        else:
            raise AssertionError("unknown terminal continuity state")

        terminal = txn.read(terminal_name)
        durable_no_clobber = (
            mutated
            and terminal is not None
            and rejected(
                lambda: synthetic_fd_atomic_publish(
                    txn.evidence.fd,
                    terminal_name,
                    preflight.canonical_json_bytes(terminal),
                )
            )
        )
        return continuity_rejected, durable_no_clobber
    finally:
        txn.close()


def main() -> int:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="preflight-v3-hostile-") as raw_base:
        base = Path(raw_base).resolve()
        bindings = make_bound_closure(base)
        auth, auth_raw, auth_sha = authorization_fixture(bindings)

        checks["default_self_closures_remain_unbound_pending_fresh_qa"] = rejected(
            preflight.require_frozen_bindings
        )
        checks["synthetic_fixture_bindings_are_complete"] = bindings.is_fully_bound()
        checks["direct_pathname_main_is_fail_closed"] = rejected(preflight.main)
        checks["production_source_contains_no_local_users_runtime_anchor"] = (
            "/Users/" not in SOURCE.read_text(encoding="utf-8")
        )
        checks["production_v10_remote_roots_are_exact"] = (
            Path(preflight.FROZEN_BINDINGS.v10_package["builder_path"]).parent
            == preflight.EXPECTED_V10_PACKAGE_ROOT
            and Path(preflight.FROZEN_BINDINGS.v10_audit["receipt_path"]).parent
            == preflight.EXPECTED_V10_AUDIT_ROOT
            and preflight.EXPECTED_V10_PACKAGE_ROOT.parent
            == preflight.EXPECTED_V10_EVIDENCE_BASE
            and preflight.EXPECTED_V10_AUDIT_ROOT.parent
            == preflight.EXPECTED_V10_EVIDENCE_BASE
            and not rejected(
                lambda: preflight.validate_production_v10_binding_roots(
                    preflight.FROZEN_BINDINGS
                )
            )
        )
        alternate_package = dict(preflight.FROZEN_BINDINGS.v10_package)
        alternate_package["builder_path"] = os.fspath(
            preflight.EXPECTED_V10_EVIDENCE_BASE
            / "alternate-v10-package"
            / "build_result_free_transport_runtime_v10.py"
        )
        alternate_bindings = preflight.FrozenBindings(
            preflight.FROZEN_BINDINGS.preflight_package,
            preflight.FROZEN_BINDINGS.preflight_audit,
            alternate_package,
            preflight.FROZEN_BINDINGS.v10_audit,
        )
        checks["production_v10_alternate_base_substitution_rejected"] = rejected(
            lambda: preflight.validate_production_v10_binding_roots(
                alternate_bindings
            )
        )
        alias_package = dict(preflight.FROZEN_BINDINGS.v10_package)
        alias_package["builder_path"] = (
            os.fspath(preflight.EXPECTED_V10_EVIDENCE_BASE)
            + "/alias/../"
            + preflight.EXPECTED_V10_PACKAGE_ROOT.name
            + "/build_result_free_transport_runtime_v10.py"
        )
        alias_bindings = preflight.FrozenBindings(
            preflight.FROZEN_BINDINGS.preflight_package,
            preflight.FROZEN_BINDINGS.preflight_audit,
            alias_package,
            preflight.FROZEN_BINDINGS.v10_audit,
        )
        checks["production_v10_ancestor_alias_spelling_rejected"] = rejected(
            lambda: preflight.validate_production_v10_binding_roots(
                alias_bindings
            )
        )
        checks["authorization_exact_positive"] = not rejected(
            lambda: preflight.validate_authorization_payload(auth, auth_sha)
        )
        checks["authorization_expands_exact_self_closure_positive"] = not rejected(
            lambda: preflight.effective_bindings_from_signed_authorization(
                auth, bindings
            )
        )

        changed = copy.deepcopy(auth)
        changed["production_final_root"] = "/tmp/arbitrary-absent-root"
        checks["old_expanded_authorization_shape_rejected"] = rejected(
            lambda: preflight.validate_authorization_payload(changed, auth_sha)
        )
        changed = copy.deepcopy(auth)
        changed["decision_id"] += "-other"
        checks["decision_id_is_hard_bound"] = rejected(
            lambda: preflight.validate_authorization_payload(changed, auth_sha)
        )
        changed = copy.deepcopy(auth)
        del changed["preflight_package_index_sha256"]
        checks["root_authorization_exact_core_anchor_set_required"] = rejected(
            lambda: preflight.validate_authorization_payload(changed, auth_sha)
        )
        for key in (
            "preflight_package_manifest_sha256",
            "preflight_package_index_sha256",
            "preflight_independent_audit_receipt_sha256",
            "preflight_independent_audit_index_sha256",
        ):
            changed = copy.deepcopy(auth)
            changed[key] = "0" * 64
            checks[f"root_anchor_{key}_actual_bytes_are_bound"] = rejected(
                lambda value=changed: preflight.effective_bindings_from_signed_authorization(
                    value, bindings
                )
            )
        checks["v10_audit_requires_exact_zero_findings"] = not rejected(
            lambda: preflight._validate_zero_findings(
                bindings.v10_audit["finding_counts"], "synthetic.v10.findings"
            )
        )
        checks["v10_audit_requires_exact_scoped_verdict"] = (
            bindings.v10_audit["action_scoped_verdict"]
            == preflight.V10_QA_ACTION_SCOPED_VERDICT
        )
        changed = copy.deepcopy(auth)
        changed["authority"]["transport_runtime_layout_authorized"] = True
        checks["transport_runtime_authority_is_forbidden"] = rejected(
            lambda: preflight.validate_authorization_payload(changed, auth_sha)
        )
        changed = copy.deepcopy(auth)
        changed["held"] = {"authorization_sha256_marker": auth_sha}
        checks["old_held_object_is_forbidden_in_fd199"] = rejected(
            lambda: preflight.validate_authorization_payload(changed, auth_sha)
        )

        context = bootstrap_context(auth_sha)
        checks["bootstrap_context_exact_positive"] = not rejected(
            lambda: preflight.validate_bootstrap_context(context, auth_sha)
        )
        changed_context = copy.deepcopy(context)
        changed_context["proc_argv"][0] = os.fspath(preflight.EXPECTED_SOURCE_PYTHON)
        checks["bootstrap_requires_proc_fd197_argv0"] = rejected(
            lambda: preflight.validate_bootstrap_context(changed_context, auth_sha)
        )
        changed_context = copy.deepcopy(context)
        changed_context["source_fd"] = 200
        checks["bootstrap_requires_fixed_fd198"] = rejected(
            lambda: preflight.validate_bootstrap_context(changed_context, auth_sha)
        )
        changed_context = copy.deepcopy(context)
        changed_context["authorization_sha256"] = "0" * 64
        checks["bootstrap_authorization_hash_tamper_rejected"] = rejected(
            lambda: preflight.validate_bootstrap_context(changed_context, auth_sha)
        )
        changed_context = copy.deepcopy(context)
        changed_context["source_identity"]["mode"] = "0644"
        checks["bootstrap_source_mode_tamper_rejected"] = rejected(
            lambda: preflight.validate_bootstrap_context(changed_context, auth_sha)
        )
        changed_context = copy.deepcopy(context)
        changed_context["proc_argv"][5] += "\n"
        checks["bootstrap_text_hash_tamper_rejected"] = rejected(
            lambda: preflight.validate_bootstrap_context(changed_context, auth_sha)
        )
        logical = context["proc_argv"][6:]
        checks["logical_bootstrap_argv_exact_positive"] = not rejected(
            lambda: preflight.validate_logical_bootstrap_argv(
                logical, context, auth_sha
            )
        )
        changed_logical = list(logical)
        changed_logical[-1] = "0" * 64
        checks["logical_bootstrap_argv_hash_tamper_rejected"] = rejected(
            lambda: preflight.validate_logical_bootstrap_argv(
                changed_logical, context, auth_sha
            )
        )
        checks["bootstrap_source_compiles"] = not rejected(
            lambda: compile(preflight.ROOT_BOOTSTRAP_TEXT, "<bootstrap>", "exec")
        )
        checks["bootstrap_has_no_pathname_source_open"] = (
            "open(SFD" not in preflight.ROOT_BOOTSTRAP_TEXT
            and "compile(sb" in preflight.ROOT_BOOTSTRAP_TEXT
            and "/proc/self/exe" in preflight.ROOT_BOOTSTRAP_TEXT
        )

        def make_bootstrap_case(name: str) -> dict[str, Any]:
            case_root = base / "bootstrap-precompile" / name
            marker = case_root / "EXECUTED.marker"
            package, audit = make_exact_self_closures(
                case_root / "closure", marker_source(marker)
            )
            case_bindings = preflight.FrozenBindings(
                package,
                audit,
                bindings.v10_package,
                bindings.v10_audit,
            )
            case_auth, case_auth_raw, case_auth_sha = authorization_fixture(
                case_bindings
            )
            authorization_path = case_root / "ROOT_AUTHORIZATION.json"
            frozen_bytes(authorization_path, case_auth_raw)
            source_path = (
                Path(package["bundle_manifest_path"]).parent
                / "run_result_free_mars_native_preflight_v3.py"
            )
            return {
                "root": case_root,
                "marker": marker,
                "package": package,
                "audit": audit,
                "bindings": case_bindings,
                "auth": case_auth,
                "auth_raw": case_auth_raw,
                "auth_sha": case_auth_sha,
                "authorization_path": authorization_path,
                "source_path": source_path,
            }

        authorized_case = make_bootstrap_case("authorized")
        authorized_run = run_root_bootstrap_fixture(
            source_path=authorized_case["source_path"],
            authorization_path=authorized_case["authorization_path"],
            trusted_authorization_sha256=authorized_case["auth_sha"],
            declared_source_sha256=digest(
                authorized_case["source_path"].read_bytes()
            ),
        )
        checks["bootstrap_authorized_exact_package_source_executes"] = (
            authorized_run.returncode == 0
            and authorized_case["marker"].read_text(encoding="utf-8")
            == "EXECUTED\n"
        )

        bootstrap_text_case = make_bootstrap_case("bootstrap-text-substitution")
        bootstrap_text_run = run_root_bootstrap_fixture(
            source_path=bootstrap_text_case["source_path"],
            authorization_path=bootstrap_text_case["authorization_path"],
            trusted_authorization_sha256=bootstrap_text_case["auth_sha"],
            declared_source_sha256=digest(
                bootstrap_text_case["source_path"].read_bytes()
            ),
            bootstrap_text=preflight.ROOT_BOOTSTRAP_TEXT + "\n",
        )
        checks["bootstrap_text_substitution_preexec_rejected_by_signed_contract"] = (
            bootstrap_text_run.returncode == 2
            and not bootstrap_text_case["marker"].exists()
        )

        synchronized_case = make_bootstrap_case("synchronized-source-substitution")
        rogue_marker = synchronized_case["root"] / "ROGUE_EXECUTED.marker"
        rogue_source = synchronized_case["root"] / "rogue-source.py"
        frozen_bytes(rogue_source, marker_source(rogue_marker))
        synchronized_run = run_root_bootstrap_fixture(
            source_path=rogue_source,
            authorization_path=synchronized_case["authorization_path"],
            trusted_authorization_sha256=synchronized_case["auth_sha"],
            declared_source_sha256=digest(rogue_source.read_bytes()),
        )
        checks["bootstrap_synchronized_fd198_and_argv_substitution_preexec_rejected"] = (
            synchronized_run.returncode == 2 and not rogue_marker.exists()
        )

        index_case = make_bootstrap_case("index-named-replacement")
        index_path = Path(index_case["package"]["sha256_index_path"])
        index_root = index_path.parent
        index_root.chmod(0o755)
        index_path.rename(index_case["root"] / "ORIGINAL_SHA256SUMS.backup")
        frozen_bytes(index_path, b"0" * 64 + b"  BUNDLE_MANIFEST.json\n")
        index_root.chmod(0o555)
        index_run = run_root_bootstrap_fixture(
            source_path=index_case["source_path"],
            authorization_path=index_case["authorization_path"],
            trusted_authorization_sha256=index_case["auth_sha"],
            declared_source_sha256=digest(index_case["source_path"].read_bytes()),
        )
        checks["bootstrap_index_named_path_replacement_preexec_rejected"] = (
            index_run.returncode == 2 and not index_case["marker"].exists()
        )

        extra_case = make_bootstrap_case("extra-package-member")
        extra_root = Path(extra_case["package"]["bundle_manifest_path"]).parent
        extra_root.chmod(0o755)
        frozen_bytes(extra_root / "UNDECLARED.txt", b"undeclared\n")
        extra_root.chmod(0o555)
        extra_run = run_root_bootstrap_fixture(
            source_path=extra_case["source_path"],
            authorization_path=extra_case["authorization_path"],
            trusted_authorization_sha256=extra_case["auth_sha"],
            declared_source_sha256=digest(extra_case["source_path"].read_bytes()),
        )
        checks["bootstrap_extra_package_member_preexec_rejected"] = (
            extra_run.returncode == 2 and not extra_case["marker"].exists()
        )

        missing_case = make_bootstrap_case("missing-package-member")
        missing_root = Path(missing_case["package"]["bundle_manifest_path"]).parent
        missing_root.chmod(0o755)
        (missing_root / "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V3_CN.md").unlink()
        missing_root.chmod(0o555)
        missing_run = run_root_bootstrap_fixture(
            source_path=missing_case["source_path"],
            authorization_path=missing_case["authorization_path"],
            trusted_authorization_sha256=missing_case["auth_sha"],
            declared_source_sha256=digest(missing_case["source_path"].read_bytes()),
        )
        checks["bootstrap_missing_package_member_preexec_rejected"] = (
            missing_run.returncode == 2 and not missing_case["marker"].exists()
        )

        trusted_case = make_bootstrap_case("trusted-auth-root")
        self_consistent_rogue_case = make_bootstrap_case(
            "self-consistent-unauthorized-package"
        )
        self_consistent_run = run_root_bootstrap_fixture(
            source_path=self_consistent_rogue_case["source_path"],
            authorization_path=self_consistent_rogue_case["authorization_path"],
            trusted_authorization_sha256=trusted_case["auth_sha"],
            declared_source_sha256=digest(
                self_consistent_rogue_case["source_path"].read_bytes()
            ),
        )
        checks["bootstrap_self_consistent_but_unauthorized_package_preexec_rejected"] = (
            self_consistent_run.returncode == 2
            and not self_consistent_rogue_case["marker"].exists()
        )

        held_path = base / "held.py"
        held_path.write_bytes(b"print('held')\n")
        held_path.chmod(0o444)
        fd = os.open(held_path, os.O_RDONLY)
        try:
            os.set_inheritable(fd, True)
            checks["inherited_readonly_fd_positive"] = not rejected(
                lambda: preflight.validate_inherited_held_fd(fd, "held")
            )
            os.set_inheritable(fd, False)
            checks["cloexec_fd_rejected"] = rejected(
                lambda: preflight.validate_inherited_held_fd(fd, "held")
            )
        finally:
            os.close(fd)
        checks["missing_fixed_fd_rejected"] = rejected(
            lambda: preflight.validate_inherited_held_fd(-1, "missing")
        )
        held_path.chmod(0o644)
        fd = os.open(held_path, os.O_RDWR)
        try:
            os.set_inheritable(fd, True)
            checks["ordwr_fd_rejected"] = rejected(
                lambda: preflight.validate_inherited_held_fd(fd, "ordwr")
            )
        finally:
            os.close(fd)
        held_path.chmod(0o444)
        hardlink = base / "held-hardlink.py"
        os.link(held_path, hardlink)
        fd = os.open(held_path, os.O_RDONLY)
        try:
            checks["held_nlink_gt1_rejected"] = rejected(
                lambda: preflight.read_stable_fd(fd, "hardlinked")
            )
        finally:
            os.close(fd)

        anchor_dir = base / "anchor"
        anchor_dir.mkdir()
        anchor_path = anchor_dir / "anchor.json"
        anchor_data = b'{"anchor":true}\n'
        anchor_path.write_bytes(anchor_data)
        anchor_path.chmod(0o444)
        lease = preflight.FileLease.open(anchor_path, digest(anchor_data), "anchor")
        try:
            checks["anchor_held_lease_initial_positive"] = not rejected(
                lambda: lease.revalidate("initial")
            )
            backup = anchor_dir / "anchor.backup"
            anchor_path.rename(backup)
            anchor_path.write_bytes(b'{"anchor":false}\n')
            anchor_path.chmod(0o444)
            checks["anchor_path_replacement_detected"] = rejected(
                lambda: lease.revalidate("replacement")
            )
            anchor_path.unlink()
            backup.rename(anchor_path)
            checks["anchor_replace_restore_epoch_detected"] = rejected(
                lambda: lease.revalidate("replace-restore")
            )
        finally:
            lease.close()

        tamper_dir = base / "tamper"
        tamper_dir.mkdir()
        tamper_path = tamper_dir / "anchor.json"
        tamper_path.write_bytes(anchor_data)
        tamper_path.chmod(0o444)
        lease = preflight.FileLease.open(tamper_path, digest(anchor_data), "tamper")
        try:
            tamper_path.chmod(0o644)
            tamper_path.write_bytes(b'{"anchor":"tampered"}\n')
            tamper_path.chmod(0o444)
            checks["anchor_content_tamper_detected"] = rejected(
                lambda: lease.revalidate("content-tamper")
            )
        finally:
            lease.close()

        canonical_parent = base / "canonical-production-parent"
        canonical_parent.mkdir()
        canonical_root = canonical_parent / "root"
        canonical_journal = canonical_parent / ".journal"
        work_root = base / "work"
        work_root.mkdir()
        safe_root = work_root / "compat"
        safe_journal = work_root / ".compat-journal"
        checks["safe_noncanonical_compatibility_paths_positive"] = not rejected(
            lambda: preflight.reject_compatibility_aliases(
                safe_root, safe_journal,
                allowed_work_root=work_root,
                canonical_root=canonical_root,
                canonical_parent=canonical_parent,
                canonical_journal=canonical_journal,
            )
        )
        checks["canonical_root_as_compatibility_target_rejected"] = rejected(
            lambda: preflight.reject_compatibility_aliases(
                canonical_root, safe_journal,
                allowed_work_root=base,
                canonical_root=canonical_root,
                canonical_parent=canonical_parent,
                canonical_journal=canonical_journal,
            )
        )
        checks["canonical_parent_as_compatibility_target_rejected"] = rejected(
            lambda: preflight.reject_compatibility_aliases(
                canonical_parent, safe_journal,
                allowed_work_root=base,
                canonical_root=canonical_root,
                canonical_parent=canonical_parent,
                canonical_journal=canonical_journal,
            )
        )
        alias = work_root / "alias"
        alias.symlink_to(canonical_parent, target_is_directory=True)
        checks["symlink_alias_into_production_parent_rejected"] = rejected(
            lambda: preflight.reject_compatibility_aliases(
                alias / "nested", safe_journal,
                allowed_work_root=work_root,
                canonical_root=canonical_root,
                canonical_parent=canonical_parent,
                canonical_journal=canonical_journal,
            )
        )

        request = preflight.make_compatibility_request(auth_sha)
        checks["compatibility_request_is_not_production_build"] = (
            request["scope"] == "NOT_PRODUCTION_BUILD"
            and request["authority"]["not_production_build"] is True
            and request["authority"]["production_root_write_authorized"] is False
        )
        checks["compatibility_request_requires_real_native_primitives"] = (
            request["publication_requirements"]["real_linux_renameat2_noreplace"] is True
            and request["publication_requirements"]["real_linux_xfs_otmpfile_procfd_linkat"] is True
            and request["publication_requirements"]["pathname_fallback_allowed"] is False
        )
        result = preflight.invoke_v10_scoped_native_compatibility(
            FakeBuilder, request, parent_fd=41, work_root_fd=42
        )
        checks["future_v10_scoped_api_positive_fixture"] = result["status"] == preflight.API_STATUS
        class MissingApi:
            NATIVE_COMPATIBILITY_API_SCHEMA = preflight.API_SCHEMA
            renameat2_noreplace = staticmethod(lambda: None)
            publish_terminal_linux_otmpfile_noreplace = staticmethod(lambda: None)
        checks["missing_future_v10_api_blocks"] = rejected(
            lambda: preflight.invoke_v10_scoped_native_compatibility(
                MissingApi, request, parent_fd=41, work_root_fd=42
            )
        )
        bad_result = dict(result)
        bad_result["publication"] = dict(result["publication"])
        bad_result["publication"]["pathname_fallback_used"] = True
        checks["v10_pathname_fallback_result_rejected"] = rejected(
            lambda: preflight.validate_compatibility_result(bad_result, request)
        )
        bad_result = dict(result)
        bad_result["production_guards"] = dict(result["production_guards"])
        bad_result["production_guards"]["journal_absent_before_after"] = False
        checks["v10_production_guard_failure_rejected"] = rejected(
            lambda: preflight.validate_compatibility_result(bad_result, request)
        )

        begin = preflight.make_begin(auth_sha)
        preflight.validate_record(begin)
        begin_sha = digest(preflight.canonical_json_bytes(begin))
        intent = preflight.make_intent(begin, request)
        preflight.validate_record(intent)
        intent_sha = digest(preflight.canonical_json_bytes(intent))
        result_sha = digest(preflight.canonical_json_bytes(result))
        passed_terminal = preflight.make_terminal(
            passed=True, auth_sha=auth_sha, begin_sha=begin_sha,
            intent_sha=intent_sha, result_sha=result_sha, phase="native-compatibility-complete",
        )
        failed_terminal = preflight.make_terminal(
            passed=False, auth_sha=auth_sha, begin_sha=begin_sha,
            intent_sha=None, result_sha=None, phase="before-intent",
            error_type="SyntheticFailure", error_message="fixture failure",
        )
        checks["durable_begin_schema_positive"] = not rejected(
            lambda: preflight.validate_record(begin)
        )
        checks["durable_intent_binds_begin"] = intent["begin_sha256"] == begin_sha
        checks["pass_terminal_binds_intent_and_result"] = (
            not rejected(lambda: preflight.validate_record(passed_terminal))
            and passed_terminal["intent_sha256"] == intent_sha
            and passed_terminal["compatibility_result_sha256"] == result_sha
        )
        checks["fail_terminal_is_explicit_and_recoverable"] = (
            not rejected(lambda: preflight.validate_record(failed_terminal))
            and failed_terminal["error_type"] == "SyntheticFailure"
        )
        checks["recovery_new_state"] = preflight.classify_recovery(None, None, None) == "NEW_WRITE_BEGIN"
        checks["recovery_begin_only_state"] = (
            preflight.classify_recovery(begin, None, None)
            == "RECOVER_BEGIN_ONLY_PUBLISH_FAIL_TERMINAL"
        )
        checks["recovery_intent_state"] = (
            preflight.classify_recovery(begin, intent, None)
            == "RECOVER_INTENT_REVALIDATE_COMPATIBILITY_OUTCOME"
        )
        checks["recovery_terminal_pass_state"] = (
            preflight.classify_recovery(begin, intent, passed_terminal)
            == "ALREADY_TERMINAL_PASS"
        )
        tampered_intent = dict(intent)
        tampered_intent["begin_sha256"] = "0" * 64
        checks["recovery_tampered_begin_binding_rejected"] = rejected(
            lambda: preflight.classify_recovery(begin, tampered_intent, None)
        )
        checks["terminal_without_begin_rejected"] = rejected(
            lambda: preflight.classify_recovery(None, None, passed_terminal)
        )

        evidence_dir = base / "evidence"
        evidence_dir.mkdir()
        begin_bytes = preflight.canonical_json_bytes(begin)
        publication = synthetic_atomic_publish(evidence_dir, "BEGIN.json", begin_bytes)
        checks["fixture_atomic_publication_is_0444_nlink1"] = (
            publication["mode"] == 0o444
            and publication["nlink"] == 1
            and publication["sha256"] == begin_sha
        )
        checks["fixture_no_clobber_existing_canonical"] = rejected(
            lambda: synthetic_atomic_publish(evidence_dir, "BEGIN.json", begin_bytes)
        )
        crash_dir = base / "crash-evidence"
        crash_dir.mkdir()
        def crash_before_link() -> None:
            raise RuntimeError("synthetic crash before canonical link")
        try:
            synthetic_atomic_publish(
                crash_dir, "BEGIN.json", begin_bytes,
                before_link_hook=crash_before_link,
            )
        except RuntimeError:
            pass
        checks["partial_crash_leaves_canonical_name_absent"] = not (crash_dir / "BEGIN.json").exists()
        retry = synthetic_atomic_publish(crash_dir, "BEGIN.json", begin_bytes)
        checks["partial_crash_retry_can_publish_exact_record"] = retry["sha256"] == begin_sha
        checks["production_publisher_has_no_nonlinux_fallback"] = (
            preflight.PRODUCTION_EVIDENCE_PUBLICATION_METHOD.startswith("LINUX_XFS_O_TMPFILE")
            and "no pathname fallback" in preflight.publish_otmpfile_noreplace.__doc__.lower()
                if preflight.publish_otmpfile_noreplace.__doc__ else False
        )

        FakeBuilder.calls = 0
        txn = TransactionFixture(base, "transaction-pass")
        try:
            status = txn.run(auth_sha=auth_sha)
            begin_record = txn.read(preflight.BEGIN_NAME)
            intent_record = txn.read(preflight.INTENT_NAME)
            result_record = txn.read(preflight.RESULT_NAME)
            pass_record = txn.read(preflight.PASS_NAME)
            checks["transaction_new_to_terminal_pass"] = status == "TERMINAL_PASS"
            checks["transaction_exact_four_record_pass_closure"] = txn.members() == {
                preflight.BEGIN_NAME, preflight.INTENT_NAME,
                preflight.RESULT_NAME, preflight.PASS_NAME,
            }
            checks["transaction_exact_work_root_pass_closure"] = set(
                os.listdir(txn.work.fd)
            ) == {
                preflight.EXPECTED_EVIDENCE_JOURNAL.name,
                preflight.EXPECTED_COMPATIBILITY_ROOT.name,
                preflight.EXPECTED_COMPATIBILITY_JOURNAL.name,
            }
            checks["transaction_records_are_strict_and_hash_linked"] = (
                begin_record is not None
                and intent_record is not None
                and result_record is not None
                and pass_record is not None
                and intent_record["begin_sha256"] == digest(
                    preflight.canonical_json_bytes(begin_record)
                )
                and pass_record["intent_sha256"] == digest(
                    preflight.canonical_json_bytes(intent_record)
                )
                and pass_record["compatibility_result_sha256"] == digest(
                    preflight.canonical_json_bytes(result_record)
                )
            )
            checks["transaction_scoped_api_called_once"] = FakeBuilder.calls == 1
            fds_before_replay = (
                os.fstat(txn.production_fd).st_ino,
                os.fstat(txn.work.fd).st_ino,
                os.fstat(txn.evidence.fd).st_ino,
            )
            replay_status = txn.run(auth_sha=auth_sha)
            fds_after_replay = (
                os.fstat(txn.production_fd).st_ino,
                os.fstat(txn.work.fd).st_ino,
                os.fstat(txn.evidence.fd).st_ino,
            )
            checks["transaction_terminal_pass_replay_is_idempotent"] = (
                replay_status == "ALREADY_TERMINAL_PASS" and FakeBuilder.calls == 1
            )
            checks["transaction_borrows_and_does_not_close_held_fds"] = (
                fds_before_replay == fds_after_replay
            )
        finally:
            txn.close()

        txn = TransactionFixture(base, "transaction-exclusive-lock")
        competitor_fd = os.open(txn.evidence.path, os.O_RDONLY)
        try:
            preflight.acquire_exclusive_evidence_lock(
                txn.evidence.fd, "synthetic.primary"
            )
            checks["exclusive_evidence_lock_rejects_concurrent_transaction"] = rejected(
                lambda: preflight.acquire_exclusive_evidence_lock(
                    competitor_fd, "synthetic.competitor"
                )
            )
            checks["exclusive_evidence_lock_creates_no_pathname_member"] = (
                txn.members() == set()
            )
            fcntl.flock(txn.evidence.fd, fcntl.LOCK_UN)
            checks["exclusive_evidence_lock_is_recoverable_after_release"] = not rejected(
                lambda: preflight.acquire_exclusive_evidence_lock(
                    competitor_fd, "synthetic.competitor.after_release"
                )
            )
            fcntl.flock(competitor_fd, fcntl.LOCK_UN)
        finally:
            os.close(competitor_fd)
            txn.close()

        FakeBuilder.calls = 0
        txn = TransactionFixture(base, "transaction-partial-v10-staging")
        try:
            seeded_begin = preflight.make_begin(auth_sha)
            seeded_request = preflight.make_compatibility_request(auth_sha)
            seeded_intent = preflight.make_intent(seeded_begin, seeded_request)
            synthetic_fd_atomic_publish(
                txn.evidence.fd, preflight.BEGIN_NAME,
                preflight.canonical_json_bytes(seeded_begin),
            )
            synthetic_fd_atomic_publish(
                txn.evidence.fd, preflight.INTENT_NAME,
                preflight.canonical_json_bytes(seeded_intent),
            )
            os.mkdir(
                preflight.compatibility_staging_name(auth_sha), 0o700,
                dir_fd=txn.work.fd,
            )
            checks["partial_v10_staging_interruption_becomes_durable_fail"] = (
                rejected(lambda: txn.run(auth_sha=auth_sha))
                and preflight.FAIL_NAME in txn.members()
                and preflight.PASS_NAME not in txn.members()
            )
            checks["partial_v10_staging_fail_replay_is_not_wedged"] = (
                txn.run(auth_sha=auth_sha) == "ALREADY_TERMINAL_FAIL"
            )
        finally:
            txn.close()

        FakeBuilder.calls = 0
        txn = TransactionFixture(base, "transaction-begin-link-crash")
        try:
            crash_begin = preflight.make_begin(auth_sha)
            def crash_after_link() -> None:
                raise RuntimeError("synthetic death after canonical link before directory fsync")
            crashed = False
            try:
                synthetic_fd_atomic_publish(
                    txn.evidence.fd, preflight.BEGIN_NAME,
                    preflight.canonical_json_bytes(crash_begin),
                    after_link_before_dir_fsync_hook=crash_after_link,
                )
            except RuntimeError:
                crashed = True
            recovered_visible = txn.read(preflight.BEGIN_NAME)
            checks["after_link_before_dirfsync_crash_keeps_complete_visible_begin"] = (
                crashed and recovered_visible == crash_begin
            )
            recovered_status = txn.run(auth_sha=auth_sha)
            fail_record = txn.read(preflight.FAIL_NAME)
            checks["begin_only_interruption_recovers_to_explicit_fail"] = (
                recovered_status == "RECOVERED_BEGIN_ONLY_TO_TERMINAL_FAIL"
                and txn.members() == {preflight.BEGIN_NAME, preflight.FAIL_NAME}
                and fail_record is not None
                and fail_record["error_type"] == "InterruptedBeforeDurableIntent"
                and FakeBuilder.calls == 0
            )
            checks["begin_only_fail_replay_is_idempotent"] = (
                txn.run(auth_sha=auth_sha) == "ALREADY_TERMINAL_FAIL"
                and FakeBuilder.calls == 0
            )
        finally:
            txn.close()

        FakeBuilder.calls = 0
        txn = TransactionFixture(base, "transaction-intent-recovery")
        try:
            seeded_begin = preflight.make_begin(auth_sha)
            seeded_request = preflight.make_compatibility_request(auth_sha)
            seeded_intent = preflight.make_intent(seeded_begin, seeded_request)
            synthetic_fd_atomic_publish(
                txn.evidence.fd, preflight.BEGIN_NAME,
                preflight.canonical_json_bytes(seeded_begin),
            )
            synthetic_fd_atomic_publish(
                txn.evidence.fd, preflight.INTENT_NAME,
                preflight.canonical_json_bytes(seeded_intent),
            )
            checks["intent_only_interruption_reinvokes_recoverable_api_to_pass"] = (
                txn.run(auth_sha=auth_sha) == "TERMINAL_PASS"
                and FakeBuilder.calls == 1
                and txn.members() == {
                    preflight.BEGIN_NAME, preflight.INTENT_NAME,
                    preflight.RESULT_NAME, preflight.PASS_NAME,
                }
            )
        finally:
            txn.close()

        txn = TransactionFixture(base, "transaction-result-recovery")
        try:
            seeded_begin = preflight.make_begin(auth_sha)
            seeded_request = preflight.make_compatibility_request(auth_sha)
            seeded_intent = preflight.make_intent(seeded_begin, seeded_request)
            raw_result = FakeBuilder.execute_scoped_noncanonical_native_compatibility_preflight_v1(
                request=seeded_request,
                production_parent_fd=txn.production_fd,
                compatibility_work_root_fd=txn.work.fd,
                rename_impl=FakeBuilder.renameat2_noreplace,
                terminal_publish_impl=FakeBuilder.publish_terminal_linux_otmpfile_noreplace,
            )
            seeded_result = preflight.make_result_record(raw_result, seeded_request)
            for name, value in (
                (preflight.BEGIN_NAME, seeded_begin),
                (preflight.INTENT_NAME, seeded_intent),
                (preflight.RESULT_NAME, seeded_result),
            ):
                synthetic_fd_atomic_publish(
                    txn.evidence.fd, name, preflight.canonical_json_bytes(value)
                )
            FakeBuilder.calls = 0
            checks["durable_result_interruption_publishes_pass_without_api_recall"] = (
                txn.run(auth_sha=auth_sha) == "TERMINAL_PASS"
                and FakeBuilder.calls == 0
                and preflight.PASS_NAME in txn.members()
            )
        finally:
            txn.close()

        FailingBuilder.calls = 0
        FakeBuilder.calls = 0
        txn = TransactionFixture(base, "transaction-api-failure")
        try:
            checks["scoped_api_failure_is_raised_after_durable_fail"] = rejected(
                lambda: txn.run(auth_sha=auth_sha, builder=FailingBuilder)
            )
            api_fail = txn.read(preflight.FAIL_NAME)
            checks["scoped_api_failure_preserves_no_clobber_failure_trace"] = (
                FailingBuilder.calls == 1
                and txn.members() == {
                    preflight.BEGIN_NAME, preflight.INTENT_NAME, preflight.FAIL_NAME,
                }
                and api_fail is not None
                and api_fail["error_type"] == "PreflightError"
                and api_fail["error_message"] == "synthetic scoped API failure"
            )
            checks["durable_api_fail_replay_never_calls_api"] = (
                txn.run(auth_sha=auth_sha) == "ALREADY_TERMINAL_FAIL"
                and FakeBuilder.calls == 0
            )
        finally:
            txn.close()

        txn = TransactionFixture(base, "transaction-undeclared-member")
        try:
            synthetic_fd_atomic_publish(
                txn.evidence.fd, "UNDECLARED.json",
                preflight.canonical_json_bytes({"fixture": True}),
            )
            checks["transaction_undeclared_evidence_member_rejected"] = rejected(
                lambda: txn.run(auth_sha=auth_sha)
            )
        finally:
            txn.close()

        txn = TransactionFixture(base, "transaction-dual-terminal")
        try:
            txn.run(auth_sha=auth_sha)
            dual_begin = txn.read(preflight.BEGIN_NAME)
            dual_intent = txn.read(preflight.INTENT_NAME)
            dual_result = txn.read(preflight.RESULT_NAME)
            assert dual_begin is not None and dual_intent is not None and dual_result is not None
            extra_fail = preflight.make_terminal(
                passed=False,
                auth_sha=auth_sha,
                begin_sha=digest(preflight.canonical_json_bytes(dual_begin)),
                intent_sha=digest(preflight.canonical_json_bytes(dual_intent)),
                result_sha=digest(preflight.canonical_json_bytes(dual_result)),
                phase="synthetic-dual-terminal",
                error_type="SyntheticDualTerminal",
                error_message="fixture only",
            )
            synthetic_fd_atomic_publish(
                txn.evidence.fd, preflight.FAIL_NAME,
                preflight.canonical_json_bytes(extra_fail),
            )
            checks["dual_pass_fail_terminals_are_rejected"] = rejected(
                lambda: txn.run(auth_sha=auth_sha)
            )
        finally:
            txn.close()

        txn = TransactionFixture(base, "transaction-result-tamper")
        try:
            seeded_begin = preflight.make_begin(auth_sha)
            seeded_request = preflight.make_compatibility_request(auth_sha)
            seeded_intent = preflight.make_intent(seeded_begin, seeded_request)
            raw_result = FakeBuilder.execute_scoped_noncanonical_native_compatibility_preflight_v1(
                request=seeded_request,
                production_parent_fd=txn.production_fd,
                compatibility_work_root_fd=txn.work.fd,
                rename_impl=FakeBuilder.renameat2_noreplace,
                terminal_publish_impl=FakeBuilder.publish_terminal_linux_otmpfile_noreplace,
            )
            tampered_result = preflight.make_result_record(raw_result, seeded_request)
            tampered_result["compatibility_request_sha256"] = "0" * 64
            for name, value in (
                (preflight.BEGIN_NAME, seeded_begin),
                (preflight.INTENT_NAME, seeded_intent),
                (preflight.RESULT_NAME, tampered_result),
            ):
                synthetic_fd_atomic_publish(
                    txn.evidence.fd, name, preflight.canonical_json_bytes(value)
                )
            checks["tampered_durable_result_rejected_and_fail_recorded"] = (
                rejected(lambda: txn.run(auth_sha=auth_sha))
                and preflight.FAIL_NAME in txn.members()
                and preflight.PASS_NAME not in txn.members()
            )
        finally:
            txn.close()

        for continuity_state in (
            "first_pass",
            "intent_recovery_pass",
            "begin_only_fail",
            "exception_fail",
            "existing_pass",
            "existing_fail",
        ):
            for continuity_target in ("work", "evidence"):
                for continuity_alias in (False, True):
                    continuity_rejected, terminal_no_clobber = (
                        terminal_continuity_fixture(
                            base,
                            auth_sha=auth_sha,
                            state=continuity_state,
                            target=continuity_target,
                            alias=continuity_alias,
                        )
                    )
                    kind = "alias" if continuity_alias else "replacement"
                    prefix = (
                        f"through_terminal_{continuity_state}_"
                        f"{continuity_target}_{kind}"
                    )
                    checks[prefix + "_rejected"] = continuity_rejected
                    checks[prefix + "_durable_terminal_remains_no_clobber"] = (
                        terminal_no_clobber
                    )
        checks["legal_named_parent_separation_path_still_passes"] = checks[
            "transaction_new_to_terminal_pass"
        ]

        path_constant_names = (
            "EXPECTED_SOURCE_PYTHON", "EXPECTED_SOURCE_SITE_PACKAGES",
            "EXPECTED_PRODUCTION_FINAL_ROOT", "EXPECTED_PRODUCTION_PARENT",
            "EXPECTED_PRODUCTION_JOURNAL", "EXPECTED_PREFLIGHT_WORK_PARENT",
            "EXPECTED_PREFLIGHT_WORK_ROOT", "EXPECTED_EVIDENCE_JOURNAL",
            "EXPECTED_COMPATIBILITY_ROOT", "EXPECTED_COMPATIBILITY_JOURNAL",
            "EXPECTED_V10_EVIDENCE_BASE", "EXPECTED_V10_PACKAGE_ROOT",
            "EXPECTED_V10_AUDIT_ROOT",
        )
        original_paths = {
            name: getattr(preflight, name) for name in path_constant_names
        }
        deep_root = base / "exact-deep-closure"
        deep_root.mkdir()
        try:
            interpreter = deep_root / "source-runtime" / "bin" / "python"
            frozen_bytes(interpreter, b"synthetic interpreter fixture\n", mode=0o755)
            site_packages = deep_root / "source-runtime" / "site-packages"
            site_packages.mkdir(parents=True)
            production_parent = deep_root / "production-parent"
            production_parent.mkdir()
            work_parent = deep_root / "preflight-work-parent"
            setattr(preflight, "EXPECTED_SOURCE_PYTHON", interpreter.resolve())
            setattr(preflight, "EXPECTED_SOURCE_SITE_PACKAGES", site_packages.resolve())
            setattr(preflight, "EXPECTED_PRODUCTION_PARENT", production_parent.resolve())
            setattr(
                preflight, "EXPECTED_PRODUCTION_FINAL_ROOT",
                production_parent.resolve() / "canonical-final-root",
            )
            setattr(
                preflight, "EXPECTED_PRODUCTION_JOURNAL",
                production_parent.resolve()
                / f".result-free-transport-v10.{preflight.EXPECTED_DECISION_ID}",
            )
            setattr(preflight, "EXPECTED_PREFLIGHT_WORK_PARENT", work_parent.resolve())
            setattr(
                preflight, "EXPECTED_PREFLIGHT_WORK_ROOT",
                work_parent.resolve() / "decision_001_native_compatibility_only",
            )
            setattr(
                preflight, "EXPECTED_EVIDENCE_JOURNAL",
                preflight.EXPECTED_PREFLIGHT_WORK_ROOT / "evidence",
            )
            setattr(
                preflight, "EXPECTED_COMPATIBILITY_ROOT",
                preflight.EXPECTED_PREFLIGHT_WORK_ROOT / "compat_runtime_root",
            )
            setattr(
                preflight, "EXPECTED_COMPATIBILITY_JOURNAL",
                preflight.EXPECTED_PREFLIGHT_WORK_ROOT
                / f".result-free-transport-v10.{preflight.EXPECTED_DECISION_ID}.native-compatibility",
            )

            upstream = make_bound_closure(deep_root / "upstream")
            setattr(
                preflight,
                "EXPECTED_V10_EVIDENCE_BASE",
                deep_root / "upstream",
            )
            setattr(
                preflight,
                "EXPECTED_V10_PACKAGE_ROOT",
                Path(upstream.v10_package["builder_path"]).parent,
            )
            setattr(
                preflight,
                "EXPECTED_V10_AUDIT_ROOT",
                Path(upstream.v10_audit["receipt_path"]).parent,
            )
            package_binding, audit_binding = make_exact_self_closures(
                deep_root / "self-closures", SOURCE.read_bytes()
            )
            exact_bindings = preflight.FrozenBindings(
                package_binding, audit_binding,
                upstream.v10_package, upstream.v10_audit,
            )
            held_source = Path(package_binding["bundle_manifest_path"]).parent / (
                "run_result_free_mars_native_preflight_v3.py"
            )
            deep_auth, _deep_auth_raw, deep_auth_sha = authorization_fixture(
                exact_bindings
            )
            checks["exact_nested_prepared_and_qa_authorization_positive"] = not rejected(
                lambda: preflight.validate_authorization_payload(
                    deep_auth, deep_auth_sha
                )
            )
            checks["exact_root_authorization_expands_full_self_indexes"] = not rejected(
                lambda: preflight.effective_bindings_from_signed_authorization(
                    deep_auth, exact_bindings
                )
            )
            deep_context = bootstrap_context(
                deep_auth_sha,
                held_source=held_source,
                held_interpreter=interpreter,
            )
            deep_lease = preflight.open_full_evidence_lease(
                exact_bindings,
                preflight_source_sha256=deep_context["source_sha256"],
                interpreter_sha256=deep_context["interpreter_sha256"],
            )
            try:
                checks["exact_10_member_prepared_and_8_member_qa_deep_open"] = (
                    len(deep_lease.directories) == 6
                    and len(deep_lease.files) == 48
                )
                checks["deep_closure_every_anchor_revalidates_while_held"] = not rejected(
                    lambda: deep_lease.revalidate("synthetic.deep_closure")
                )
                prepared_source_leases = [
                    lease for lease in deep_lease.files
                    if lease.path == held_source.resolve()
                ]
                checks["deep_closure_held_fd198_source_cross_binding"] = (
                    len(prepared_source_leases) == 1
                    and prepared_source_leases[0].digest
                    == deep_context["source_sha256"]
                )
            finally:
                deep_lease.close()

            authorization_path = deep_root / "root-authorization.json"
            frozen_bytes(authorization_path, preflight.canonical_json_bytes(deep_auth))
            held_context = bootstrap_context(
                deep_auth_sha,
                held_source=held_source,
                held_interpreter=interpreter,
            )
            held_context["authorization_identity"] = (
                preflight.FileIdentity.from_stat(authorization_path.stat()).json()
            )
            saved_fixed_fds: dict[int, tuple[int, int] | None] = {}
            source_fds: list[int] = []
            old_frozen = preflight.FROZEN_BINDINGS
            old_xfs_gate = preflight.require_linux_xfs_directory_fd
            try:
                for target in (
                    preflight.HELD_INTERPRETER_FD,
                    preflight.HELD_PREFLIGHT_SOURCE_FD,
                    preflight.HELD_AUTHORIZATION_FD,
                ):
                    try:
                        flags = fcntl.fcntl(target, fcntl.F_GETFD)
                        saved_fixed_fds[target] = (os.dup(target), flags)
                    except OSError:
                        saved_fixed_fds[target] = None
                for path, target in (
                    (interpreter, preflight.HELD_INTERPRETER_FD),
                    (held_source, preflight.HELD_PREFLIGHT_SOURCE_FD),
                    (authorization_path, preflight.HELD_AUTHORIZATION_FD),
                ):
                    source_fd = os.open(path, os.O_RDONLY)
                    source_fds.append(source_fd)
                    os.dup2(source_fd, target, inheritable=True)
                preflight.FROZEN_BINDINGS = exact_bindings
                def deliberate_xfs_boundary(_fd: int, _label: str) -> None:
                    raise preflight.PreflightBlocked("synthetic pre-mutation XFS boundary")
                preflight.require_linux_xfs_directory_fd = deliberate_xfs_boundary
                held_blocked_at_xfs = False
                try:
                    preflight.held_preflight_main(
                        held_context,
                        authorization_path.read_bytes(),
                        held_context["proc_argv"][6:],
                    )
                except preflight.PreflightBlocked as exc:
                    held_blocked_at_xfs = str(exc) == "synthetic pre-mutation XFS boundary"
                checks["held_main_validates_fd197_198_199_and_deep_closure_before_xfs_gate"] = (
                    held_blocked_at_xfs
                )
                checks["held_main_xfs_gate_precedes_first_worktree_mutation"] = (
                    not work_parent.exists()
                )
            finally:
                preflight.FROZEN_BINDINGS = old_frozen
                preflight.require_linux_xfs_directory_fd = old_xfs_gate
                for source_fd in source_fds:
                    if source_fd not in saved_fixed_fds:
                        os.close(source_fd)
                for target, saved in saved_fixed_fds.items():
                    try:
                        os.close(target)
                    except OSError:
                        pass
                    if saved is not None:
                        backup, flags = saved
                        os.dup2(backup, target, inheritable=True)
                        fcntl.fcntl(target, fcntl.F_SETFD, flags)
                        os.close(backup)

            extra = Path(package_binding["bundle_manifest_path"]).parent / "UNDECLARED.txt"
            extra.parent.chmod(0o755)
            frozen_bytes(extra, b"must force exact closure rejection\n")
            extra.parent.chmod(0o555)
            checks["prepared_package_undeclared_top_member_rejected"] = rejected(
                lambda: preflight.open_full_evidence_lease(
                    exact_bindings,
                    preflight_source_sha256=deep_context["source_sha256"],
                    interpreter_sha256=deep_context["interpreter_sha256"],
                )
            )
            checks["zero_findings_rejects_boolean_zero_alias"] = rejected(
                lambda: preflight._validate_zero_findings(
                    {"P0": False, "P1": 0, "P2": 0, "P3": 0},
                    "synthetic.bool-zero",
                )
            )
        finally:
            for frozen_fixture_root in (
                deep_root / "self-closures" / "preflight-v3-prepared",
                deep_root / "self-closures" / "preflight-v3-independent-qa",
            ):
                if frozen_fixture_root.exists():
                    frozen_fixture_root.chmod(0o755)
            for name, value in original_paths.items():
                setattr(preflight, name, value)

        checks["v3_lexical_double_slash_path_rejected"] = rejected(
            lambda: preflight.exact_absolute_path(
                "/tmp/preflight-v3//qa/SHA256SUMS", "v3.lexical"
            )
        )

        integer_authority = copy.deepcopy(auth)
        integer_authority["authority"] = {
            "preflight_launch_authorized": 1,
            "transport_runtime_layout_authorized": 0,
            "result_access_authorized": 0,
            "signals_authorized": 0,
            "deployment_or_resume_authorized": 0,
        }
        integer_authority_sha = digest(
            preflight.canonical_json_bytes(integer_authority)
        )
        checks["v3_fd199_integer_boolean_alias_rejected"] = rejected(
            lambda: preflight.validate_authorization_payload(
                integer_authority, integer_authority_sha
            )
        )

        ancestor_top = base / "v3-ancestor-lease"
        ancestor_child = ancestor_top / "parent" / "child"
        ancestor_child.mkdir(parents=True)
        ancestor_lease = preflight.DirectoryLease.open(
            ancestor_child, "v3.ancestor"
        )
        ancestor_held = base / "v3-ancestor-lease-held"
        try:
            ancestor_top.rename(ancestor_held)
            ancestor_child.mkdir(parents=True)
            checks["v3_immutable_ancestor_replacement_rejected"] = rejected(
                lambda: ancestor_lease.revalidate("v3.ancestor.after")
            )
        finally:
            ancestor_lease.close()

        mutable_top = base / "v3-mutable-ancestor"
        mutable_parent = mutable_top / "parent"
        mutable_parent.mkdir(parents=True)
        mutable_parent_fd = os.open(mutable_parent, os.O_RDONLY)
        mutable_lease = preflight.MutableDirectoryLease.create_or_open_at(
            mutable_parent_fd,
            mutable_parent,
            "child",
            "v3.mutable_ancestor",
        )
        mutable_held = base / "v3-mutable-ancestor-held"
        try:
            mutable_top.rename(mutable_held)
            (mutable_parent / "child").mkdir(parents=True)
            checks["v3_mutable_ancestor_replacement_rejected"] = rejected(
                lambda: mutable_lease.revalidate("v3.mutable_ancestor.after")
            )
        finally:
            mutable_lease.close()
            os.close(mutable_parent_fd)

        missing_result_txn = TransactionFixture(base, "v3-existing-pass-missing-result")
        try:
            if missing_result_txn.run(auth_sha=auth_sha) != "TERMINAL_PASS":
                raise AssertionError("v3 missing-result fixture did not reach PASS")
            os.unlink(preflight.RESULT_NAME, dir_fd=missing_result_txn.evidence.fd)
            checks["v3_existing_pass_missing_result_rejected"] = rejected(
                lambda: missing_result_txn.run(auth_sha=auth_sha)
            )
        finally:
            missing_result_txn.close()

        mismatched_result_txn = TransactionFixture(
            base, "v3-existing-pass-mismatched-result"
        )
        try:
            if mismatched_result_txn.run(auth_sha=auth_sha) != "TERMINAL_PASS":
                raise AssertionError("v3 mismatched-result fixture did not reach PASS")
            terminal = mismatched_result_txn.read(preflight.PASS_NAME)
            if terminal is None:
                raise AssertionError("v3 PASS terminal fixture missing")
            os.unlink(preflight.PASS_NAME, dir_fd=mismatched_result_txn.evidence.fd)
            terminal["compatibility_result_sha256"] = "c" * 64
            synthetic_fd_atomic_publish(
                mismatched_result_txn.evidence.fd,
                preflight.PASS_NAME,
                preflight.canonical_json_bytes(terminal),
            )
            checks["v3_existing_pass_result_hash_mismatch_rejected"] = rejected(
                lambda: mismatched_result_txn.run(auth_sha=auth_sha)
            )
        finally:
            mismatched_result_txn.close()

        begin_exception_txn = TransactionFixture(
            base, "v3-exception-after-durable-begin"
        )
        try:
            def publish_begin_then_raise(
                directory_fd: int, name: str, data: bytes, **kwargs: Any,
            ) -> dict[str, Any]:
                published = synthetic_fd_atomic_publish(
                    directory_fd, name, data, **kwargs
                )
                if name == preflight.BEGIN_NAME:
                    raise RuntimeError("v3 injected exception after durable BEGIN")
                return published

            raised_after_begin = False
            try:
                begin_exception_txn.run(
                    auth_sha=auth_sha, publisher=publish_begin_then_raise
                )
            except RuntimeError:
                raised_after_begin = True
            checks["v3_exception_after_durable_begin_publishes_fail"] = (
                raised_after_begin
                and begin_exception_txn.members()
                == {preflight.BEGIN_NAME, preflight.FAIL_NAME}
            )
        finally:
            begin_exception_txn.close()

        source_text = SOURCE.read_text(encoding="utf-8")
        checks["v3_held_fd197_198_199_revalidated_after_transaction"] = (
            source_text.count("validate_actual_held_bootstrap_fds(") >= 3
            and '"interpreter.final"' in preflight.ROOT_BOOTSTRAP_TEXT
            and '"preflight source.final"' in preflight.ROOT_BOOTSTRAP_TEXT
            and '"authorization.final"' in preflight.ROOT_BOOTSTRAP_TEXT
        )

        strict = preflight.canonical_json_bytes({"a": 1})
        checks["strict_canonical_json_positive"] = preflight.strict_json_bytes(strict, "strict") == {"a": 1}
        checks["noncanonical_json_bytes_rejected"] = rejected(
            lambda: preflight.strict_json_bytes(b'{"a": 1}\n', "noncanonical")
        )
        checks["duplicate_json_key_rejected"] = rejected(
            lambda: preflight.strict_json_bytes(b'{"a":1,"a":2}\n', "duplicate")
        )
        checks["blocked_status_all_actual_authorities_false"] = all(
            value is False
            for value in preflight.synthetic_blocked_status()["authority"].values()
        )

    failed = sorted(name for name, passed in checks.items() if not passed)
    output = {
        "schema": "historical_200k_fixed10k_preflight_v3_prepared_only_synthetic_output_v3",
        "status": "PASS" if not failed else "FAIL",
        "checked": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "failed_checks": failed,
        "checks": checks,
        "scope": {
            "mars_accessed": False,
            "results_accessed": False,
            "production_root_or_journal_touched": False,
            "external_processes_inspected": False,
            "signals_sent": False,
            "v10_real_api_executed": False,
            "linux_otmpfile_publisher_executed": False,
        },
    }
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
