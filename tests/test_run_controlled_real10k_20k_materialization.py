from __future__ import annotations

import ast
import csv
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_controlled_real10k_20k_materialization.py"
SPEC = importlib.util.spec_from_file_location("controlled_materialization_gate", SCRIPT_PATH)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_static_boundary_has_no_subprocess_exec_or_signal_primitive() -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "subprocess" not in imported
    assert "signal" not in imported
    forbidden_calls = {
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.kill",
        "os.killpg",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.system",
    }
    observed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            observed.add(f"{node.func.value.id}.{node.func.attr}")
        elif isinstance(node.func, ast.Name):
            observed.add(node.func.id)
    assert not (observed & forbidden_calls)
    assert not ({"Popen", "spawn", "kill", "killpg", "system"} & observed)


def _write(path: Path, data: str) -> Path:
    path.write_text(data, encoding="utf-8")
    return path.resolve()


def _identity(path: Path) -> dict[str, object]:
    metadata = path.stat()
    return {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "st_uid": metadata.st_uid,
        "st_gid": metadata.st_gid,
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def _record(path: Path, path_value: str) -> dict[str, object]:
    return {
        "path": path_value,
        "sha256": _sha(path),
        "identity": {
            **_identity(path),
            "nlink": path.stat().st_nlink,
            "size_bytes": path.stat().st_size,
        },
    }


def _frozen_json(path: Path, payload: object) -> dict[str, object]:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o444)
    return _record(path, path.name)


def _index_bytes(records: list[dict[str, object]]) -> bytes:
    return "".join(
        f"{record['sha256']}  {record['path']}\n" for record in records
    ).encode("ascii")


def _build_committed_preflight_fixture(
    base: Path,
    *,
    candidate: Path,
    output: Path,
    receipt: Path,
    python: Path,
    host: dict[str, object],
    files: dict[str, Path],
    package_role_sha: dict[str, str],
    python_version: str,
    numpy_version: str,
) -> dict[str, Path]:
    runtime_closure_sha = "f" * 64
    root = base / "preflight_receipt"
    root.mkdir(mode=0o700)
    root_identity = _identity(root)
    parent_identity = _identity(root.parent)
    nonce = "0123456789abcdef0123456789abcdef"
    authorities = GATE._mars_preflight_authorities()
    lease = root.parent / f".{root.name}.controlled_real10k_20k_preflight_once_lease.json"
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lease_prepared = {
        "schema": GATE.MARS_PREFLIGHT_LEASE_SCHEMA,
        "state": "PREPARED",
        "challenge_nonce": nonce,
        "receipt_root": {"path": str(root), "identity": root_identity},
        "created_utc": created,
        "consumed_utc": "0000-00-00T00:00:00Z",
        "single_use": True,
        "retry_authorized": False,
        "authorities": authorities,
    }
    lease.write_text(json.dumps(lease_prepared, indent=2, sort_keys=True) + "\n")
    lease.chmod(0o600)
    lease_initial = {
        **_record(lease, str(lease)),
        "schema": GATE.MARS_PREFLIGHT_LEASE_SCHEMA,
        "state": "PREPARED",
    }
    prepared_payload = {
        "schema": GATE.MARS_PREFLIGHT_PREPARED_SCHEMA,
        "status": "PREPARED_AWAITING_INDEPENDENT_EXACT_CODE_GO",
        "phase": "PREPARE",
        "generated_utc": created,
        "challenge_nonce": nonce,
        "receipt_root": {"path": str(root), "identity": root_identity},
        "receipt_parent": {"path": str(root.parent), "identity": parent_identity},
        "external_one_use_lease": lease_initial,
        "execution_contract": {},
        "required_execute_schema": "controlled_real10k_20k_mars_code_go_v3",
        "required_execute_scope": "MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY",
        "authorities": authorities,
        "next_legal_action": "INDEPENDENT_RESULT_BLIND_REVIEW_AND_EXACT_CODE_GO_ONLY",
    }
    prepared_path = root / GATE.MARS_PREFLIGHT_PREPARED_NAME
    prepared_record = _frozen_json(prepared_path, prepared_payload)
    qa_payload = {
        "schema": GATE.MARS_PREFLIGHT_QA_SCHEMA,
        "status": "INDEPENDENT_QA_REQUIRED",
        "verdict": "NO_GO_PENDING_EXACT_CODE_GO_V3",
        "challenge_nonce": nonce,
        "prepared_receipt": prepared_record,
        "external_one_use_lease": lease_initial,
        "required_go": {},
        "authorities": authorities,
        "next_legal_action": "EXTERNAL_INDEPENDENT_QA_ONLY",
    }
    qa_path = root / GATE.MARS_PREFLIGHT_QA_NAME
    qa_record = _frozen_json(qa_path, qa_payload)
    prepare_index = root / GATE.MARS_PREFLIGHT_PREPARE_INDEX_NAME
    prepare_index.write_bytes(_index_bytes([prepared_record, qa_record]))
    prepare_index.chmod(0o444)
    prepare_index_record = _record(prepare_index, prepare_index.name)
    lease_consumed = dict(lease_prepared)
    lease_consumed["state"] = "CONSUMED"
    lease_consumed["consumed_utc"] = created
    lease.chmod(0o600)
    lease.write_text(json.dumps(lease_consumed, indent=2, sort_keys=True) + "\n")
    lease.chmod(0o444)
    lease_consumed_record = {
        **_record(lease, str(lease)),
        "schema": GATE.MARS_PREFLIGHT_LEASE_SCHEMA,
        "state": "CONSUMED",
    }
    runtime_closure = {
        "python": {
            "implementation": "CPython",
            "version": python_version,
            "abi_tag": "fixture-abi",
            "platform": "linux-x86_64",
            "executable_sha256": _sha(python),
        },
        "numpy": {"version": numpy_version},
        "pure_archive": {"sha256": "d" * 64},
        "native_libraries": [],
        "native_extensions": [],
        "system_library_allowlist": ["libc.so.6"],
    }
    active_runtime = {
        "schema": "controlled_real10k_20k_runtime_attestation_v1",
        "entrypoint": "native_smoke",
        "manifest_sha256": runtime_closure_sha,
        "pure_archive_sha256": "d" * 64,
        "bootstrap_sha256": package_role_sha["runtime_bootstrap_code"],
    }
    module_origins = {
        "rfic_transformer_inverse_design": {
            "kind": "sealed_pure_zip",
            "origin": (
                "descriptor-zip:/proc/self/fd/203!/"
                "rfic_transformer_inverse_design/__init__.py"
            ),
            "sha256": package_role_sha["runtime_package_init_code"],
        }
    }
    startup_attestation = {
        **active_runtime,
        "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
        "entrypoint_sha256": package_role_sha["native_smoke_test"],
        "python": {
            key: runtime_closure["python"][key]
            for key in ("implementation", "version", "abi_tag", "platform")
        },
        "python_flags": {
            "isolated": 1,
            "no_site": 1,
            "dont_write_bytecode": True,
        },
        "numpy_version": numpy_version,
        "module_origins": module_origins,
        "native_library_sha256": {},
        "native_extension_sha256": {},
        "system_library_allowlist": ["libc.so.6"],
        "site_initialization_disabled": True,
        "external_package_fallback_allowed": False,
    }
    terminal_attestation = {
        **active_runtime,
        "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
        "exit_code": 0,
        "module_origins": module_origins,
        "system_library_allowlist": ["libc.so.6"],
        "external_package_fallback_allowed": False,
    }
    code_roles = (
        "package_builder_code",
        "runtime_bootstrap_code",
        "preflight_code",
        "materialization_builder_code",
        "materialization_gate_code",
        "splitter_code",
        "runner_code",
        "trainer_code",
        "evaluator_code",
        "runtime_package_init_code",
        "shared_contract_code",
    )
    runtime_identity = {
        "schema": "controlled_real10k_20k_preflight_runtime_identity_v2",
        "python_version": python_version,
        "numpy_version": numpy_version,
        "python_executable_path": str(python),
        "python_executable_sha256": _sha(python),
        "active_runtime": active_runtime,
        "startup_attestation": startup_attestation,
        "terminal_attestation": terminal_attestation,
        "compiled_role_count": len(code_roles) + 1,
        "consumed_code_role_sha256": {
            role: package_role_sha[role] for role in code_roles
        },
        "native_smoke_result_sha256": "1" * 64,
        "native_smoke_attestation_sha256": "2" * 64,
        "descriptor_closed": True,
        "raw_runtime_fallback_authorized": False,
    }
    code_go_path = base / "PREFLIGHT_CODE_GO.json"
    code_go_path.write_text("{}\n", encoding="utf-8")
    singleton_contract_path = base / "PROCESS_SINGLETON_CONTRACT.json"
    singleton_contract_payload = {
        "schema": "controlled_real10k_20k_process_singleton_contract_v1",
        "lock": {
            "relative_path": "CONTROLLED_SINGLETON.lock",
            "operation": "LOCK_EX|LOCK_NB",
        },
        "protected_entrypoints": [],
        "proc_audit": {"substring_matching_allowed": False},
        "lifetime": {"full_lifetime_required": True},
        "conflict_policy": {"verdict": "NO_GO_DUPLICATE_CONTROLLED_PROCESS"},
    }
    singleton_contract_path.write_text(
        json.dumps(singleton_contract_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    singleton_contract_path.chmod(0o444)
    singleton_lock_path = base / "CONTROLLED_SINGLETON.lock"
    singleton_lock_path.write_bytes(b"")
    singleton_lock_path.chmod(0o444)
    process_singleton = {
        "contract": _record(singleton_contract_path, str(singleton_contract_path)),
        "contract_payload": singleton_contract_payload,
        "lock": _record(singleton_lock_path, str(singleton_lock_path)),
        "lock_operation": "LOCK_EX|LOCK_NB",
        "lock_held_for_full_execute_lifetime": True,
        "protected_entrypoints": [],
        "proc_audit_contract": {"substring_matching_allowed": False},
        "before": {},
        "after": {},
        "all_counts_zero": True,
        "current_uid_only": True,
    }
    package_root = base / "package_v5"
    package_root.mkdir()
    package_root.chmod(0o555)
    package_root_identity = _identity(package_root)
    package_commit_sha = "7" * 64
    package_manifest_sha = "8" * 64
    package_receipt_sha = "9" * 64
    package_qa_sha = "a" * 64
    package_index_sha = "b" * 64
    attempt_root = base / "package_build_attempt"
    attempt_root.mkdir()
    attempt_body_path = attempt_root / GATE.PACKAGE_BUILD_ATTEMPT_BODY_NAME
    attempt_body_payload = {
        "schema": GATE.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
        "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "invocation": {
            "argv": ["build_controlled_real10k_20k_mars_package.py", "--fixture"],
            "cwd": {
                "lexical": str(base),
                "resolved": str(base),
                "device": base.stat().st_dev,
                "inode": base.stat().st_ino,
            },
            "output_dir": str(package_root),
            "failure_receipt_dir": str(attempt_root),
            "package_spec": {
                "path": str(base / "PACKAGE_BUILD_SPEC.json"),
                "expected_sha256": "c" * 64,
            },
            "builder": {
                "path": str(SCRIPT_PATH),
                "expected_sha256": package_role_sha["package_builder_code"],
            },
            "python": {
                "implementation": "CPython",
                "version": python_version,
                "version_info": [
                    sys.version_info.major,
                    sys.version_info.minor,
                    sys.version_info.micro,
                    sys.version_info.releaselevel,
                    sys.version_info.serial,
                ],
                "executable_lexical": str(python),
                "executable_resolved": str(python),
                "executable_sha256": _sha(python),
                "flags": {"isolated": 0},
            },
            "runtime": {
                "platform": "fixture-linux",
                "machine": "x86_64",
                "system": "Linux",
                "release": "fixture",
                "byteorder": sys.byteorder,
                "filesystem_encoding": sys.getfilesystemencoding(),
            },
            "environment": {
                "raw_values_recorded": False,
                "key_count": 0,
                "keys": [],
                "keyset_sha256": GATE._canonical_json_sha([]),
                "key_value_map_sha256": "d" * 64,
            },
        },
        "observed_identity": {
            "package_spec_sha256": "c" * 64,
            "builder_sha256": package_role_sha["package_builder_code"],
            "package_output_device": package_root_identity["st_dev"],
            "package_output_inode": package_root_identity["st_ino"],
        },
        "package": {
            "path": str(package_root),
            "manifest_sha256": package_manifest_sha,
            "receipt_sha256": package_receipt_sha,
            "independent_qa_required_sha256": package_qa_sha,
            "sha256sums_sha256": package_index_sha,
            "package_commit_sha256": package_commit_sha,
            "file_count": 42,
        },
        "partial_output_preserved": False,
        "authorities": GATE._package_authorities(),
        "execution_authorized": False,
    }
    _frozen_json(attempt_body_path, attempt_body_payload)
    attempt_root_identity = _identity(attempt_root)
    attempt_root_identity["mode_octal"] = "0555"
    attempt_parent_identity = _identity(attempt_root.parent)
    attempt_committed_path = attempt_root / GATE.PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
    attempt_committed_payload = {
        "schema": GATE.PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
        "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
        "committed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "body": {
            "path": str(attempt_body_path),
            "sha256": _sha(attempt_body_path),
            "schema": GATE.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        },
        "package_commit": {
            "path": str(package_root / "PACKAGE_COMMIT.json"),
            "sha256": package_commit_sha,
            "schema": GATE.PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        },
        "package_root": {
            "path": str(package_root),
            "st_dev": package_root_identity["st_dev"],
            "st_ino": package_root_identity["st_ino"],
            "mode_octal": "0555",
        },
        "attempt_root": {
            "path": str(attempt_root),
            "st_dev": attempt_root_identity["st_dev"],
            "st_ino": attempt_root_identity["st_ino"],
            "mode_octal": "0555",
        },
        "attempt_parent": {
            "path": str(attempt_root.parent),
            "st_dev": attempt_parent_identity["st_dev"],
            "st_ino": attempt_parent_identity["st_ino"],
            "mode_octal": attempt_parent_identity["mode_octal"],
        },
        "publication": {
            "body_file_fsync": True,
            "attempt_root_fsync": True,
            "attempt_parent_fsync": True,
            "attempt_root_frozen": True,
            "continuity_verified": True,
            "terminal_inode_reserved_create_once_before_freeze": True,
            "terminal_bytes_published_after_durability": True,
            "post_commit_attempt_file_creation_permitted": False,
        },
        "authorities": GATE._package_authorities(),
        "execution_authorized": False,
    }
    _frozen_json(attempt_committed_path, attempt_committed_payload)
    attempt_root.chmod(0o555)
    body_payload = {
        "schema": GATE.MARS_PREFLIGHT_BODY_SCHEMA,
        "status": "PASS_BODY_AWAITING_DURABLE_COMMIT",
        "preflight_pass": False,
        "started_utc": created,
        "body_generated_utc": created,
        "package": {
            "root": str(package_root),
            "manifest_sha256": package_manifest_sha,
            "sha_index_sha256": package_index_sha,
            "receipt_sha256": package_receipt_sha,
            "independent_qa_required_sha256": package_qa_sha,
            "commit_sha256": package_commit_sha,
            "build_attempt_body_path": str(attempt_body_path),
            "build_attempt_body_sha256": _sha(attempt_body_path),
            "build_attempt_committed_path": str(attempt_committed_path),
            "build_attempt_committed_sha256": _sha(attempt_committed_path),
            "role_sha256": package_role_sha,
            "role_identity": {
                "runtime_dependency_closure_json": {
                    "kind": "file",
                    "path": "runtime/contracts/RUNTIME_CLOSURE.json",
                    "sha256": runtime_closure_sha,
                },
                "runtime_dependency_closure_tree": {
                    "kind": "tree",
                    "path": "runtime/dependencies",
                    "sha256": "c" * 64,
                },
                "runtime_bootstrap_code": {
                    "kind": "file",
                    "path": (
                        "runtime/bootstrap/"
                        "controlled_real10k_20k_runtime_bootstrap.py"
                    ),
                    "sha256": package_role_sha["runtime_bootstrap_code"],
                },
            },
            "runtime_dependency_closure": runtime_closure,
            "runtime_entrypoints": {
                "preflight": "runtime/scripts/preflight_controlled_real10k_20k_mars.py",
                "materialization": "runtime/scripts/run_controlled_real10k_20k_materialization.py",
                "runner": "runtime/scripts/run_controlled_real10k_20k_paired.py",
                "trainer": "runtime/scripts/train_physical_feature_tandem_inverse.py",
                "evaluator": "runtime/scripts/evaluate_controlled_real10k_20k_common.py",
                "native_smoke": "runtime/tests/controlled_real10k_20k_mars_native_smoke.py",
            },
        },
        "host_identity": {
            "hostname": host["hostname"],
            "uid": host["uid"],
            "boot_id": host["boot_id"],
        },
        "runtime_identity": runtime_identity,
        "process_singleton": process_singleton,
        "candidate_output_dirs": [str(candidate), str(output), str(receipt)],
        "candidate_output_dirs_absent_before_and_after": True,
        "native_tests": {
            "requested": True,
            "roles": ["native_smoke_test"],
            "returncode": 0,
            "stdout_sha256": "1" * 64,
            "stdout_size_bytes": 1,
            "stderr_sha256": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
            "stderr_size_bytes": 0,
            "elapsed_seconds": 0.1,
            "attestation_sha256": "2" * 64,
            "attestation_size_bytes": 1,
            "attestation_ephemeral_no_clobber_file": True,
            "protocol_schema": "controlled_real10k_20k_native_smoke_result_v3",
            "test_id": "descriptor_closed_package_consumer_graph_v5",
            "executed_test_count": 1,
            "exact_structured_pass": True,
            "isolated_python_flags": ["-I", "-B", "-S"],
            "environment_keyset": [
                "CONTROLLED_REAL10K_20K_PREFLIGHT_ONLY",
                "LANG",
                "LC_ALL",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
            ],
            "runtime_identity": runtime_identity,
        },
        "host_load_snapshot": {
            "load1": 0.0,
            "load5": 0.0,
            "load15": 0.0,
            "cpu_count": 1,
            "gate_applied": False,
            "record_only": True,
        },
        "checks": {"package_exact": True, "native_tests_pass": True},
        "external_code_go": {
            "path": str(code_go_path),
            "sha256": _sha(code_go_path),
        },
        "receipt_transaction": {
            "prepared_binding": {},
            "consumed_external_one_use_lease": lease_consumed_record,
        },
        "committed_terminal_marker_required": GATE.MARS_PREFLIGHT_COMMITTED_NAME,
        "authorities": authorities,
        "next_legal_action": "NO_ACTION_UNTIL_DURABLE_COMMITTED_MARKER_IS_VERIFIED",
    }
    body_path = root / GATE.MARS_PREFLIGHT_BODY_NAME
    body_record = _frozen_json(body_path, body_payload)
    success_index = root / GATE.MARS_PREFLIGHT_INDEX_NAME
    success_index.write_bytes(
        _index_bytes([prepared_record, qa_record, prepare_index_record, body_record])
    )
    success_index.chmod(0o444)
    success_index_record = _record(success_index, success_index.name)
    committed_payload = {
        "schema": GATE.MARS_PREFLIGHT_COMMITTED_SCHEMA,
        "status": "COMMITTED_PASS_PREFLIGHT_ONLY",
        "committed_utc": created,
        "preflight_pass": True,
        "receipt_root": {
            "path": str(root),
            "prepared_identity": root_identity,
            "committed_identity": {**root_identity, "mode_octal": "0555"},
        },
        "receipt_parent": {"path": str(root.parent), "identity": parent_identity},
        "prepared_artifacts": {
            "prepared_receipt": prepared_record,
            "execution_qa_required": qa_record,
            "prepare_sha256sums": prepare_index_record,
        },
        "receipt_body": body_record,
        "sha256_index": success_index_record,
        "external_code_go": {
            "path": str(code_go_path),
            "sha256": _sha(code_go_path),
            "schema": "controlled_real10k_20k_mars_code_go_v3",
            "scope": "MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY",
        },
        "consumed_external_one_use_lease": lease_consumed_record,
        "process_singleton": process_singleton,
        "exact_root_filenames": list(GATE.MARS_PREFLIGHT_SUCCESS_FILES),
        "failure_marker_absent_at_commit": True,
        "failure_marker_has_absolute_precedence": True,
        "body_is_not_authority": True,
        "authorities": authorities,
        "next_legal_action": "SEPARATE_RESULT_BLIND_MATERIALIZATION_RECEIPT_AND_EXACT_AUTHORIZATION_REQUIRED",
    }
    committed_path = root / GATE.MARS_PREFLIGHT_COMMITTED_NAME
    _frozen_json(committed_path, committed_payload)
    root.chmod(0o555)
    return {
        "root": root.resolve(),
        "committed": committed_path.resolve(),
        "body": body_path.resolve(),
        "lease": lease.resolve(),
        "singleton_contract": singleton_contract_path.resolve(),
        "singleton_lock": singleton_lock_path.resolve(),
        "package_attempt_root": attempt_root.resolve(),
        "package_attempt_body": attempt_body_path.resolve(),
        "package_attempt_committed": attempt_committed_path.resolve(),
    }


@pytest.fixture()
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    base = tmp_path.resolve()
    inputs = base / "inputs"
    inputs.mkdir()
    files = {
        "builder": _write(inputs / "builder.py", "# frozen builder\n"),
        "shared": _write(inputs / "shared.py", "# frozen shared\n"),
        "splitter": _write(inputs / "splitter.py", "# frozen splitter\n"),
        "prereg": _write(inputs / "prereg.json", "{}\n"),
        "add11": _write(inputs / "add11.json", "{}\n"),
        "add12": _write(inputs / "add12.json", "{}\n"),
        "historical": _write(inputs / "historical.csv", "opaque historical bytes\n"),
        "authoritative": _write(inputs / "authoritative.csv", "opaque authoritative bytes\n"),
        "summary": _write(inputs / "summary.json", "opaque model-summary bytes\n"),
        "numpy_core": _write(inputs / "numpy_core.bin", "opaque NumPy core bytes\n"),
        "numpy_config": _write(inputs / "numpy_config.py", "# opaque NumPy config bytes\n"),
    }
    monkeypatch.setattr(GATE, "FROZEN_HISTORICAL_10K_SHA256", _sha(files["historical"]))
    monkeypatch.setattr(GATE, "FROZEN_AUTHORITATIVE_100K_SHA256", _sha(files["authoritative"]))
    monkeypatch.setattr(GATE, "FROZEN_HISTORICAL_SUMMARY_SHA256", _sha(files["summary"]))
    monkeypatch.setattr(GATE, "FROZEN_PREREG_V1_SHA256", _sha(files["prereg"]))
    monkeypatch.setattr(GATE, "FROZEN_PREREG_ADDENDUM_V1_1_SHA256", _sha(files["add11"]))
    monkeypatch.setattr(GATE, "FROZEN_PREREG_ADDENDUM_V1_2_SHA256", _sha(files["add12"]))
    monkeypatch.setattr(
        GATE,
        "COUNTS",
        {
            "historical_source": 6,
            "authoritative_source": 12,
            "small_gradient_train": 3,
            "large_gradient_train": 5,
            "validation": 2,
            "test": 1,
            "extra": 2,
            "large_source": 8,
        },
    )
    local_python_version = ".".join(str(value) for value in sys.version_info[:3])
    local_numpy_version = GATE.importlib.metadata.version("numpy")
    monkeypatch.setattr(
        GATE,
        "_active_numpy_identity",
        lambda: (
            local_numpy_version,
            "descriptor-zip:/proc/self/fd/203!/numpy/__init__.py",
        ),
    )
    monkeypatch.setattr(GATE, "EXPECTED_PRODUCTION_PYTHON_VERSION", local_python_version)
    monkeypatch.setattr(GATE, "EXPECTED_PRODUCTION_NUMPY_VERSION", local_numpy_version)
    runtime_closure_sha = "f" * 64
    runtime_attestation = {
        "schema": "controlled_real10k_20k_runtime_attestation_v1",
        "entrypoint": "materialization",
        "manifest_sha256": runtime_closure_sha,
        "pure_archive_sha256": "d" * 64,
        "bootstrap_sha256": "2" * 64,
    }
    monkeypatch.setattr(
        GATE,
        "_require_sealed_runtime",
        lambda expected_sha: (
            dict(runtime_attestation)
            if expected_sha == runtime_closure_sha
            else (_ for _ in ()).throw(AssertionError("wrong runtime closure SHA"))
        ),
    )
    candidate = base / "candidate"
    output = base / "material_output"
    receipt = base / "execution_receipt"
    python = Path(sys.executable).resolve(strict=True)
    host = GATE._host_identity()
    package_role_sha = {
        "package_builder_code": "1" * 64,
        "runtime_bootstrap_code": "2" * 64,
        "preflight_code": "3" * 64,
        "materialization_gate_code": _sha(SCRIPT_PATH),
        "materialization_builder_code": _sha(files["builder"]),
        "shared_contract_code": _sha(files["shared"]),
        "splitter_code": _sha(files["splitter"]),
        "preregistration_v1_json": _sha(files["prereg"]),
        "preregistration_addendum_v1_1_json": _sha(files["add11"]),
        "preregistration_addendum_v1_2_json": _sha(files["add12"]),
        "historical_10k_csv": _sha(files["historical"]),
        "authoritative_100k_csv": _sha(files["authoritative"]),
        "historical_model_summary_json": _sha(files["summary"]),
        "runner_code": "a" * 64,
        "trainer_code": "b" * 64,
        "evaluator_code": "4" * 64,
        "runtime_package_init_code": "5" * 64,
        "native_smoke_test": "6" * 64,
    }
    preflight_fixture = _build_committed_preflight_fixture(
        base,
        candidate=candidate,
        output=output,
        receipt=receipt,
        python=python,
        host=host,
        files=files,
        package_role_sha=package_role_sha,
        python_version=local_python_version,
        numpy_version=local_numpy_version,
    )
    files["preflight"] = preflight_fixture["body"]
    files["preflight_root"] = preflight_fixture["root"]
    files["preflight_committed"] = preflight_fixture["committed"]
    files["preflight_lease"] = preflight_fixture["lease"]
    files["package_attempt_root"] = preflight_fixture["package_attempt_root"]
    files["package_attempt_body"] = preflight_fixture["package_attempt_body"]
    files["package_attempt_committed"] = preflight_fixture[
        "package_attempt_committed"
    ]
    argv = [
        "--phase",
        "PREPARE",
        "--candidate-dir",
        str(candidate),
        "--materialization-out-dir",
        str(output),
        "--execution-receipt-dir",
        str(receipt),
        "--historical-10k-csv",
        str(files["historical"]),
        "--historical-10k-sha256",
        _sha(files["historical"]),
        "--authoritative-100k-csv",
        str(files["authoritative"]),
        "--authoritative-100k-sha256",
        _sha(files["authoritative"]),
        "--historical-model-summary-json",
        str(files["summary"]),
        "--historical-model-summary-sha256",
        _sha(files["summary"]),
        "--builder-script",
        str(files["builder"]),
        "--builder-sha256",
        _sha(files["builder"]),
        "--shared-contract",
        str(files["shared"]),
        "--shared-contract-sha256",
        _sha(files["shared"]),
        "--splitter-source",
        str(files["splitter"]),
        "--splitter-sha256",
        _sha(files["splitter"]),
        "--prereg-v1",
        str(files["prereg"]),
        "--prereg-v1-sha256",
        _sha(files["prereg"]),
        "--prereg-addendum-v1-1",
        str(files["add11"]),
        "--prereg-addendum-v1-1-sha256",
        _sha(files["add11"]),
        "--prereg-addendum-v1-2",
        str(files["add12"]),
        "--prereg-addendum-v1-2-sha256",
        _sha(files["add12"]),
        "--mars-preflight-root",
        str(files["preflight_root"]),
        "--mars-preflight-committed-sha256",
        _sha(files["preflight_committed"]),
        "--python-executable",
        str(python),
        "--python-executable-sha256",
        _sha(python),
        "--expected-runtime-closure-json-sha256",
        runtime_closure_sha,
        "--expected-hostname",
        GATE.socket.gethostname(),
        "--expected-uid",
        str(os.getuid()),
        "--expected-python-version",
        local_python_version,
    ]
    assert GATE.main(argv) == 0
    manifest_path = candidate / GATE.MANIFEST_NAME
    index_path = candidate / GATE.SHA_INDEX_NAME
    manifest = json.loads(manifest_path.read_text())
    audit = GATE._audit_candidate_closure(
        candidate,
        expected_manifest_sha=_sha(manifest_path),
        expected_index_sha=_sha(index_path),
    )
    return {
        "base": base,
        "candidate": candidate,
        "output": output,
        "receipt": receipt,
        "manifest": manifest,
        "manifest_sha": _sha(manifest_path),
        "index_sha": _sha(index_path),
        "audit": audit,
        "files": files,
    }


def _go_payload(prepared: dict[str, object], *, now: datetime | None = None) -> dict[str, object]:
    observed = now or datetime.now(timezone.utc).replace(microsecond=0)
    audit = prepared["audit"]
    assert isinstance(audit, dict)
    return {
        "schema": GATE.GO_SCHEMA,
        "status": "GO",
        "scope": GATE.GO_SCOPE,
        "issued_utc": (observed - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_utc": (observed + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "challenge_nonce": audit["manifest"]["challenge_nonce"],
        "reviewer": {
            "reviewer_id": "independent-result-blind-reviewer",
            "independent": True,
            "result_blind": True,
            "reviewed_without_numerical_results": True,
        },
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "bindings": GATE._expected_go_bindings(
            audit["manifest"], audit["manifest_sha256"], audit["sha_index_sha256"]
        ),
        "authorities": dict(GATE.GO_AUTHORITIES),
    }


def _write_go(prepared: dict[str, object], payload: dict[str, object], name: str = "GO.json") -> Path:
    path = Path(prepared["base"]) / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path.resolve()


def _execute_argv(prepared: dict[str, object], go_path: Path) -> list[str]:
    return [
        "--phase",
        "EXECUTE",
        "--candidate-dir",
        str(prepared["candidate"]),
        "--candidate-manifest-sha256",
        str(prepared["manifest_sha"]),
        "--candidate-sha256sums-sha256",
        str(prepared["index_sha"]),
        "--go-json",
        str(go_path),
        "--go-sha256",
        _sha(go_path),
    ]


def _process_audit(*, duplicate: bool = False) -> dict[str, object]:
    matches = [
        {
            "pid": os.getpid(),
            "roles": ["materialization_controller"],
            "argv": [sys.executable, str(SCRIPT_PATH)],
            "argv_bytes_sha256": "a" * 64,
            "identity_valid": True,
            "sealed_request": {"entrypoint": "materialization"},
            "observed_descriptor_identity": {"fixture": True},
        }
    ]
    if duplicate:
        matches.append(
            {
                "pid": os.getpid() + 1,
                "roles": ["paired_runner"],
                "argv": ["python", "run_controlled_real10k_20k_paired.py"],
                "argv_bytes_sha256": "b" * 64,
                "identity_valid": True,
                "sealed_request": {"entrypoint": "runner"},
                "observed_descriptor_identity": {"fixture": True},
            }
        )
    return {
        "schema": "controlled_real10k_20k_materialization_process_audit_v1",
        "uid": os.getuid(),
        "current_pid": os.getpid(),
        "substring_matching_used": False,
        "exact_descriptor_runtime_identity_required": True,
        "matches": matches,
        "match_count": len(matches),
    }


@pytest.mark.parametrize("value", [123, True, "A" * 64, "a" * 64 + " "])
def test_sha256_parser_rejects_type_case_and_whitespace_aliases(value: object) -> None:
    with pytest.raises(GATE.MaterializationGateError, match="lowercase SHA-256"):
        GATE._normalized_sha(value, "hostile SHA")


def _row(index: int, split: str, cell_group: int) -> dict[str, str]:
    inputs = (
        (0.6, 0.6, 6.0, 0.05)
        if cell_group == 0
        else (1.4, 1.4, 12.0, 0.3)
        if cell_group == 1
        else (2.4, 2.4, 22.0, 0.65)
    )
    geometry = (200.0 + index * 0.01, 205.0, 210.0, 215.0, 4.0, 30.0, 31.0, 0.0, 120.0, 121.0)
    result = {
        "controlled_source_row_number": str(index + 1),
        "controlled_origin": "historical10k_exact_authoritative_match" if index < GATE.COUNTS["historical_source"] else "authoritative100k_train_cell_extra",
        "controlled_physical_cell_4d": GATE._physical_cell(inputs),
        "controlled_split_assignment": split,
        "canonical_geometry_identity_sha256": GATE._geometry_identity(geometry, portable=False),
        "portable_geometry_decimal12_sha256": GATE._geometry_identity(geometry, portable=True),
        "evaluation": f"eval-{index:04d}",
        "touchstone_path": f"/opaque/existing/{index:04d}.s4p",
        "touchstone_sha256": hashlib.sha256(f"touchstone-{index}".encode()).hexdigest(),
    }
    result.update({column: format(value, ".17g") for column, value in zip(GATE.INPUT_COLUMNS, inputs)})
    result.update({column: format(value, ".17g") for column, value in zip(GATE.GEOMETRY_COLUMNS, geometry)})
    return result


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fake_material_output(prepared: dict[str, object], *, tamper: bool = False) -> None:
    root = Path(prepared["output"])
    root.mkdir()
    audit = prepared["audit"]
    assert isinstance(audit, dict)
    bindings = audit["bindings"]
    small = [
        *[_row(index, "train", 0) for index in range(3)],
        *[_row(index, "validation", 1) for index in range(3, 5)],
        _row(5, "test", 2),
    ]
    large = small + [_row(index, "train", 0) for index in range(6, 8)]
    arm10 = root / GATE.MATERIAL_OUTPUT_ORDER[0]
    arm20 = root / GATE.MATERIAL_OUTPUT_ORDER[1]
    for path, rows in ((arm10, small), (arm20, large)):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(GATE.OUTPUT_COLUMNS), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    cells = {
        split: sorted({row["controlled_physical_cell_4d"] for row in small if row["controlled_split_assignment"] == split})
        for split in ("train", "validation", "test")
    }
    validation = [row for row in small if row["controlled_split_assignment"] == "validation"]
    test = [row for row in small if row["controlled_split_assignment"] == "test"]
    holdout = {
        "schema": "fixed_common_holdout_geometry_identity_v1",
        "identity_kind": "canonical_geometry_sha256",
        "historical_model_summary_sha256": GATE.FROZEN_HISTORICAL_SUMMARY_SHA256,
        "shared_contract_sha256": bindings["shared_contract_code"]["sha256"],
        "selection_method": "exact_historical_physical_cell_grouped_split_reconstruction",
        "selection_uses_model_results": False,
        "stratification": ["physical_cell_4d"],
        "physical_cell_encoding": GATE.PHYSICAL_CELL_ENCODING,
        "physical_cell_bins": GATE.PHYSICAL_CELL_BINS,
        "physical_lower": list(GATE.INPUT_LOWER),
        "physical_upper": list(GATE.INPUT_UPPER),
        "validation_count": GATE.COUNTS["validation"],
        "test_count": GATE.COUNTS["test"],
        "validation_geometry_identities": sorted(row["canonical_geometry_identity_sha256"] for row in validation),
        "test_geometry_identities": sorted(row["canonical_geometry_identity_sha256"] for row in test),
        "validation_portable_decimal12_geometry_identities": sorted(row["portable_geometry_decimal12_sha256"] for row in validation),
        "test_portable_decimal12_geometry_identities": sorted(row["portable_geometry_decimal12_sha256"] for row in test),
        "train_cell_ids": cells["train"],
        "validation_cell_ids": cells["validation"],
        "test_cell_ids": cells["test"],
        "physical_cell_partition_fingerprint_sha256": "1" * 64,
        "complete_cell_isolation": {
            "all_historical_rows_assigned_once": True,
            "every_cell_assigned_to_exactly_one_split": True,
            "train_validation_test_cell_overlap_count": 0,
            "appended_rows_restricted_to_train_cells": True,
            "appended_train_row_count": GATE.COUNTS["extra"],
            "appended_occupied_train_cell_count": 1,
            "train_cell_count": 1,
            "validation_cell_count": 1,
            "test_cell_count": 1,
        },
        "common_holdout_fingerprint_sha256": "2" * 64,
        "boundary": "frozen synthetic contract fixture",
    }
    holdout_path = root / GATE.MATERIAL_OUTPUT_ORDER[2]
    _write_json(holdout_path, holdout)
    normalization = {
        "schema": "declared_midpoint_half_range_normalization_v1",
        "input_columns": list(GATE.INPUT_COLUMNS),
        "geometry_columns": list(GATE.GEOMETRY_COLUMNS),
        "input_lower": list(GATE.INPUT_LOWER),
        "input_upper": list(GATE.INPUT_UPPER),
        "geometry_lower": list(GATE.GEOMETRY_LOWER),
        "geometry_upper": list(GATE.GEOMETRY_UPPER),
        "input_midpoint": [(a + b) * 0.5 for a, b in zip(GATE.INPUT_LOWER, GATE.INPUT_UPPER)],
        "input_half_range": [(b - a) * 0.5 for a, b in zip(GATE.INPUT_LOWER, GATE.INPUT_UPPER)],
        "geometry_midpoint": [(a + b) * 0.5 for a, b in zip(GATE.GEOMETRY_LOWER, GATE.GEOMETRY_UPPER)],
        "geometry_half_range": [(b - a) * 0.5 for a, b in zip(GATE.GEOMETRY_LOWER, GATE.GEOMETRY_UPPER)],
        "train_arm_specific_statistics_used": False,
        "large_arm_empirical_statistics_used": False,
        "all_loaded_rows_required_inside_declared_bounds": True,
        "boundary": "Both arms use identical declared midpoint/half-range arrays and the identical sigmoid decoder envelope. No arm-specific empirical mean, variance, minimum, or maximum is used.",
    }
    norm_path = root / GATE.MATERIAL_OUTPUT_ORDER[3]
    _write_json(norm_path, normalization)
    impl = {
        role: {
            "path": bindings[bound]["path"],
            "sha256": bindings[bound]["sha256"],
            "size_bytes": bindings[bound]["size_bytes"],
        }
        for role, bound in {
            "builder": "materialization_builder_code",
            "shared_contract": "shared_contract_code",
            "splitter_source": "splitter_code",
        }.items()
    }
    artifacts4 = GATE._artifact_map_exact(root, GATE.MATERIAL_OUTPUT_ORDER[:4])
    summary = {
        "schema": "controlled_real10k_20k_nested_materialization_v2",
        "generated_utc": GATE._utc_now(),
        "status": "PASS",
        "decision": "PREPARED_FOR_INDEPENDENT_QA",
        "result_accessed": False,
        "model_training_performed": False,
        "emx_performed": False,
        "implementation_identities": impl,
        "verified_input_consumption": {
            "mode": "GATE_VERIFIED_HELD_BYTES_ONLY",
            "verified_context_schema": GATE.VERIFIED_CONTEXT_SCHEMA,
            "exact_role_order": list(GATE.VERIFIED_CONTEXT_ROLES),
            "role_sha256": {
                role: bindings[role]["sha256"]
                for role in GATE.VERIFIED_CONTEXT_ROLES
            },
            "path_reopen_for_consumed_inputs": False,
        },
        "shared_contract": {"physical_cell_encoding": GATE.PHYSICAL_CELL_ENCODING, "physical_cell_bins": 4, "extra_selection_seed": GATE.SELECTION_SEED, "paired_seeds": list(GATE.PAIRED_SEEDS)},
        "source_identities": {
            "historical_10k_csv": {"path": bindings["historical_10k_csv"]["path"], "sha256": bindings["historical_10k_csv"]["sha256"], "rows": GATE.COUNTS["historical_source"]},
            "authoritative_100k_csv": {"path": bindings["authoritative_100k_csv"]["path"], "sha256": bindings["authoritative_100k_csv"]["sha256"], "rows": GATE.COUNTS["authoritative_source"]},
            "historical_model_summary_json": {"path": bindings["historical_model_summary_json"]["path"], "sha256": bindings["historical_model_summary_json"]["sha256"]},
        },
        "historical_model_contract": {},
        "historical_to_authoritative_match": {},
        "split_reconstruction": {},
        "selection_contract": {},
        "arm_counts": {
            "n10000": {"source_table_rows": GATE.COUNTS["historical_source"], "gradient_train_rows": GATE.COUNTS["small_gradient_train"], "validation_rows": GATE.COUNTS["validation"], "test_rows": GATE.COUNTS["test"]},
            "n20000": {"source_table_rows": GATE.COUNTS["large_source"], "gradient_train_rows": GATE.COUNTS["large_gradient_train"], "validation_rows": GATE.COUNTS["validation"], "test_rows": GATE.COUNTS["test"]},
        },
        "nested_identity_contract": {
            "arm_n10000_is_exact_ordered_prefix_and_row_subset_of_arm_n20000": True,
            "common_output_schema": list(GATE.OUTPUT_COLUMNS),
            "historical_row_record_set_sha256": GATE._row_record_set_sha(small),
            "extra_row_record_set_sha256": GATE._row_record_set_sha(large[len(small):]),
            "common_validation_and_test_unchanged": True,
            "geometry_identity_overlap_historical_vs_extra": 0,
            "touchstone_identity_overlap_historical_vs_extra": 0,
        },
        "fixed_contracts": {
            "common_holdout": {"path": str(holdout_path), "sha256": _sha(holdout_path)},
            "declared_midpoint_half_range_normalization": {"path": str(norm_path), "sha256": _sha(norm_path), "train_arm_specific_statistics_used": False, "large_arm_empirical_statistics_used": False},
        },
        "production_exact_checks": {key: True for key in GATE.PRODUCTION_EXACT_CHECKS},
        "artifacts": artifacts4,
        "release_boundary": "training remains unauthorized",
        "training_launch_authorized": False,
        "independent_qa_required": True,
    }
    summary_path = root / GATE.MATERIAL_OUTPUT_ORDER[4]
    _write_json(summary_path, summary)
    qa = {
        "schema": "controlled_real10k_20k_independent_qa_required_v2",
        "generated_utc": GATE._utc_now(),
        "status": "INDEPENDENT_QA_REQUIRED",
        "verdict": "NO_GO_PENDING_FRESH_INDEPENDENT_QA",
        "materialization_summary": {"path": str(summary_path), "sha256": _sha(summary_path)},
        "implementation_identities": impl,
        "frozen_artifacts": artifacts4,
        "frozen_scientific_contract": {
            "physical_cell_encoding": GATE.PHYSICAL_CELL_ENCODING,
            "physical_cell_bins": 4,
            "extra_selection_seed": GATE.SELECTION_SEED,
            "paired_seeds": list(GATE.PAIRED_SEEDS),
            "source_table_rows": {"n10000": GATE.COUNTS["historical_source"], "n20000": GATE.COUNTS["large_source"]},
            "gradient_train_rows": {"n10000": GATE.COUNTS["small_gradient_train"], "n20000": GATE.COUNTS["large_gradient_train"]},
            "validation_rows_common": GATE.COUNTS["validation"],
            "test_rows_common": GATE.COUNTS["test"],
        },
        "training_authorized": False,
        "result_access_authorized": False,
        "fresh_emx_authorized": False,
        "next_legal_gate": {},
    }
    qa_path = root / GATE.MATERIAL_OUTPUT_ORDER[5]
    _write_json(qa_path, qa)
    artifacts6 = GATE._artifact_map_exact(root, GATE.MATERIAL_OUTPUT_ORDER[:6])
    receipt = {
        "schema": "controlled_real10k_20k_nested_materialization_receipt_v2",
        "generated_utc": GATE._utc_now(),
        "status": "PASS",
        "verdict": "PREPARED_FOR_INDEPENDENT_QA",
        "checks": {},
        "source_sha256": {"historical_10k_csv": GATE.FROZEN_HISTORICAL_10K_SHA256, "authoritative_100k_csv": GATE.FROZEN_AUTHORITATIVE_100K_SHA256, "historical_model_summary_json": GATE.FROZEN_HISTORICAL_SUMMARY_SHA256},
        "implementation_identities": impl,
        "artifact_identities": artifacts6,
        "arm_source_rows": {"n10000": GATE.COUNTS["historical_source"], "n20000": GATE.COUNTS["large_source"]},
        "gradient_train_rows": {"n10000": GATE.COUNTS["small_gradient_train"], "n20000": GATE.COUNTS["large_gradient_train"]},
        "validation_rows_common": GATE.COUNTS["validation"],
        "test_rows_common": GATE.COUNTS["test"],
        "production_exact_checks": {key: True for key in GATE.PRODUCTION_EXACT_CHECKS},
        "training_launch_authorized": False,
        "independent_qa_required": True,
        "independent_qa_required_record": {"path": str(qa_path), "sha256": _sha(qa_path)},
        "next_legal_gate": "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO",
        "sha256_closure_contract": {},
    }
    receipt_path = root / GATE.MATERIAL_OUTPUT_ORDER[6]
    _write_json(receipt_path, receipt)
    index = root / GATE.MATERIAL_SHA_INDEX_NAME
    index.write_text("".join(f"{_sha(root / name)}  {name}\n" for name in GATE.MATERIAL_OUTPUT_ORDER), encoding="ascii")
    if tamper:
        with arm20.open("a", encoding="utf-8") as handle:
            handle.write("tampered\n")


def _install_fake_execute(monkeypatch: pytest.MonkeyPatch, prepared: dict[str, object], *, mode: str) -> None:
    monkeypatch.setattr(
        GATE, "_scan_linux_current_uid_processes", lambda *_args: _process_audit()
    )
    if mode == "success":
        monkeypatch.setattr(GATE, "_load_builder_main", lambda *_: (lambda _argv, *, verified_context: (_fake_material_output(prepared), 0)[1]))
    elif mode == "tamper":
        monkeypatch.setattr(GATE, "_load_builder_main", lambda *_: (lambda _argv, *, verified_context: (_fake_material_output(prepared, tamper=True), 0)[1]))
    elif mode == "fail":
        def fail_builder(
            _argv: list[str], *, verified_context: dict[str, object]
        ) -> int:
            assert set(verified_context) == {"schema", "entries"}
            output = Path(prepared["output"])
            output.mkdir()
            (output / "partial.txt").write_text("preserve me\n", encoding="utf-8")
            raise RuntimeError("synthetic builder failure")
        monkeypatch.setattr(GATE, "_load_builder_main", lambda *_: fail_builder)


def test_prepare_is_immutable_result_blind_exact_closure(prepared: dict[str, object]) -> None:
    candidate = Path(prepared["candidate"])
    assert stat.S_IMODE(candidate.stat().st_mode) == 0o555
    assert {path.name for path in candidate.iterdir()} == {GATE.MANIFEST_NAME, GATE.QA_REQUIRED_NAME, GATE.PREPARED_RECEIPT_NAME, GATE.SHA_INDEX_NAME}
    manifest = prepared["manifest"]
    assert manifest["result_or_row_access"]["csv_rows_read"] is False
    assert manifest["authorities"] == GATE.CANDIDATE_AUTHORITIES
    assert manifest["materialization_contract"]["counts"] == GATE.COUNTS


def test_candidate_rejects_superseded_v1_manifest_schema(
    prepared: dict[str, object]
) -> None:
    candidate = Path(prepared["candidate"])
    manifest_path = candidate / GATE.MANIFEST_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema"] = "controlled_real10k_20k_materialization_gate_manifest_v1"
    candidate.chmod(0o755)
    manifest_path.chmod(0o644)
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o444)
    candidate.chmod(0o555)
    with pytest.raises(GATE.MaterializationGateError):
        GATE._audit_candidate_closure(
            candidate,
            expected_manifest_sha=_sha(manifest_path),
            expected_index_sha=str(prepared["index_sha"]),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "runtime_path",
        "native_roles",
        "prereg_role_sha",
        "runtime_manifest_sha",
        "system_allowlist",
        "old_body_schema_v2",
    ],
)
def test_mars_preflight_exact_binding_tamper_rejected(
    prepared: dict[str, object], mutation: str
) -> None:
    audit = prepared["audit"]
    files = prepared["files"]
    assert isinstance(audit, dict) and isinstance(files, dict)
    payload = json.loads(Path(files["preflight"]).read_text(encoding="utf-8"))
    payload = copy.deepcopy(payload)
    if mutation == "runtime_path":
        payload["runtime_identity"]["python_executable_path"] += ".wrong"
    elif mutation == "native_roles":
        payload["native_tests"]["roles"] = ["native_smoke_test", "unbound_test"]
    elif mutation == "prereg_role_sha":
        payload["package"]["role_sha256"]["preregistration_v1_json"] = "0" * 64
    elif mutation == "runtime_manifest_sha":
        payload["runtime_identity"]["active_runtime"]["manifest_sha256"] = "0" * 64
    elif mutation == "system_allowlist":
        payload["runtime_identity"]["startup_attestation"][
            "system_library_allowlist"
        ] = ["libattacker.so"]
    else:
        payload["schema"] = "controlled_real10k_20k_mars_preflight_receipt_body_v2"
    held = GATE._HeldClosure()
    try:
        with pytest.raises(GATE.MaterializationGateError):
            GATE._audit_mars_preflight_body(
                payload,
                held=held,
                bindings=audit["bindings"],
                runtime=audit["manifest"]["runtime_identity"],
                host=audit["manifest"]["host_identity"],
                candidate_dir=audit["root"],
                materialization_out_dir=audit["materialization_out_dir"],
                execution_receipt_dir=audit["execution_receipt_dir"],
            )
    finally:
        held.close()


def test_candidate_reaudit_has_no_external_numpy_path_dependency(
    prepared: dict[str, object]
) -> None:
    files = prepared["files"]
    assert isinstance(files, dict)
    Path(files["numpy_core"]).write_text("drifted NumPy core bytes\n", encoding="utf-8")
    audit = GATE._audit_candidate_closure(
        Path(prepared["candidate"]),
        expected_manifest_sha=str(prepared["manifest_sha"]),
        expected_index_sha=str(prepared["index_sha"]),
    )
    assert audit["manifest"]["runtime_identity"]["descriptor_sealed_runtime"] is True


@pytest.mark.parametrize(
    "attack",
    ["reserved_empty", "missing", "corrupt", "swap", "extra", "root_replacement"],
)
def test_candidate_reaudit_rejects_nonterminal_package_attempt_closure(
    prepared: dict[str, object], attack: str
) -> None:
    files = prepared["files"]
    assert isinstance(files, dict)
    root = Path(files["package_attempt_root"])
    body = Path(files["package_attempt_body"])
    committed = Path(files["package_attempt_committed"])
    body_bytes = body.read_bytes()
    committed_bytes = committed.read_bytes()
    root.chmod(0o755)
    if attack == "reserved_empty":
        committed.chmod(0o644)
        committed.write_bytes(b"")
        committed.chmod(0o444)
    elif attack == "missing":
        committed.unlink()
    elif attack == "corrupt":
        committed.chmod(0o644)
        committed.write_bytes(b'{"schema":')
        committed.chmod(0o444)
    elif attack == "swap":
        body.chmod(0o644)
        committed.chmod(0o644)
        body.write_bytes(committed_bytes)
        committed.write_bytes(body_bytes)
        body.chmod(0o444)
        committed.chmod(0o444)
    elif attack == "extra":
        extra = root / "UNREVIEWED.json"
        extra.write_text("{}\n", encoding="utf-8")
        extra.chmod(0o444)
    else:
        displaced = root.with_name(root.name + "_displaced")
        root.rename(displaced)
        root.mkdir()
        body = root / GATE.PACKAGE_BUILD_ATTEMPT_BODY_NAME
        committed = root / GATE.PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
        body.write_bytes(body_bytes)
        committed.write_bytes(committed_bytes)
        body.chmod(0o444)
        committed.chmod(0o444)
    root.chmod(0o555)
    with pytest.raises(GATE.MaterializationGateError):
        GATE._audit_candidate_closure(
            Path(prepared["candidate"]),
            expected_manifest_sha=str(prepared["manifest_sha"]),
            expected_index_sha=str(prepared["index_sha"]),
        )


def test_candidate_manifest_index_qa_prepared_and_preflight_parse_from_held_bytes(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_path_json(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("candidate authority JSON must not be parsed through a path reopen")

    monkeypatch.setattr(GATE, "_read_json", forbidden_path_json)
    audit = GATE._audit_candidate_closure(
        Path(prepared["candidate"]),
        expected_manifest_sha=str(prepared["manifest_sha"]),
        expected_index_sha=str(prepared["index_sha"]),
    )
    expected_held = {
        f"candidate:{name}"
        for name in (
            GATE.MANIFEST_NAME,
            GATE.QA_REQUIRED_NAME,
            GATE.PREPARED_RECEIPT_NAME,
            GATE.SHA_INDEX_NAME,
        )
    } | {f"bound:{role}" for role in GATE.BOUND_ROLE_ORDER}
    assert set(audit["held_snapshot_sha256"]) == expected_held
    assert (
        audit["held_snapshot_sha256"]["bound:mars_preflight_committed"]
        == audit["bindings"]["mars_preflight_committed"]["sha256"]
    )


def _reaudit_committed_preflight(prepared: dict[str, object]) -> None:
    audit = prepared["audit"]
    files = prepared["files"]
    assert isinstance(audit, dict) and isinstance(files, dict)
    held = GATE._HeldClosure()
    try:
        GATE._audit_committed_preflight_closure(
            files["preflight_root"],
            audit["bindings"]["mars_preflight_committed"]["sha256"],
            held=held,
            bindings=audit["bindings"],
            runtime=audit["manifest"]["runtime_identity"],
            host=audit["manifest"]["host_identity"],
            candidate_dir=audit["root"],
            materialization_out_dir=audit["materialization_out_dir"],
            execution_receipt_dir=audit["execution_receipt_dir"],
        )
    finally:
        held.close()


@pytest.mark.parametrize("mutation", ["body_only", "failure_marker", "extra_file"])
def test_committed_preflight_exact_terminal_closure_rejects_hostile_membership(
    prepared: dict[str, object], mutation: str
) -> None:
    files = prepared["files"]
    assert isinstance(files, dict)
    root = Path(files["preflight_root"])
    root.chmod(0o755)
    if mutation == "body_only":
        Path(files["preflight_committed"]).unlink()
    elif mutation == "failure_marker":
        (root / GATE.MARS_PREFLIGHT_FAILURE_NAME).write_text("{}\n", encoding="utf-8")
    else:
        (root / "UNREVIEWED_EXTRA").write_text("hostile\n", encoding="utf-8")
    root.chmod(0o555)
    with pytest.raises(GATE.MaterializationGateError):
        _reaudit_committed_preflight(prepared)


def test_committed_preflight_rejects_same_path_root_inode_replacement(
    prepared: dict[str, object]
) -> None:
    files = prepared["files"]
    assert isinstance(files, dict)
    root = Path(files["preflight_root"])
    backup = root.parent / f".{root.name}.original-inode"
    root.chmod(0o755)
    os.replace(root, backup)
    shutil.copytree(backup, root, copy_function=shutil.copy2)
    root.chmod(0o555)
    try:
        with pytest.raises(GATE.MaterializationGateError, match="receipt-root inode"):
            _reaudit_committed_preflight(prepared)
    finally:
        root.chmod(0o755)
        shutil.rmtree(root)
        os.replace(backup, root)


def test_committed_preflight_rejects_same_bytes_replaced_external_lease_inode(
    prepared: dict[str, object]
) -> None:
    files = prepared["files"]
    assert isinstance(files, dict)
    lease = Path(files["preflight_lease"])
    raw = lease.read_bytes()
    lease.unlink()
    lease.write_bytes(raw)
    lease.chmod(0o444)
    with pytest.raises(GATE.MaterializationGateError, match="lease live binding"):
        _reaudit_committed_preflight(prepared)


def test_go_sha256_is_checked_before_json_parse(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_go(prepared, _go_payload(prepared), "wrong-sha.json")

    def forbidden_parse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("JSON parser must not run before GO SHA validation")

    monkeypatch.setattr(GATE.json, "loads", forbidden_parse)
    with pytest.raises(GATE.MaterializationGateError, match="before parsing"):
        GATE._validate_go(path, "0" * 64, prepared["audit"])


def test_transient_module_path_substitution_cannot_change_loaded_callable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "builder": tmp_path / "build_controlled_real10k_20k_nested.py",
        "shared": tmp_path / "controlled_real10k_20k_contract.py",
        "splitter": tmp_path / "model_splitting.py",
    }
    shutil.copy2(ROOT / "scripts" / paths["builder"].name, paths["builder"])
    shutil.copy2(
        ROOT / "rfic_transformer_inverse_design" / paths["shared"].name,
        paths["shared"],
    )
    shutil.copy2(
        ROOT / "rfic_transformer_inverse_design" / paths["splitter"].name,
        paths["splitter"],
    )
    held = GATE._HeldClosure()
    try:
        snapshots = {
            role: held.open(role, path, role)
            for role, path in paths.items()
        }
        backups: list[tuple[Path, Path]] = []
        for role, path in paths.items():
            backup = path.with_suffix(path.suffix + ".held-original")
            os.replace(path, backup)
            path.write_text(
                "SENTINEL_UNVERIFIED_PATH_MODULE = True\n"
                "def main(*args, **kwargs): return 913\n",
                encoding="utf-8",
            )
            backups.append((path, backup))
            assert _sha(path) != snapshots[role].sha256
        try:
            sealed_members = {
                "materialization_builder_code": snapshots["builder"],
                "shared_contract_code": snapshots["shared"],
                "splitter_code": snapshots["splitter"],
            }
            monkeypatch.setattr(
                GATE,
                "_active_member_source",
                lambda role, expected_sha: (
                    sealed_members[role].raw,
                    str(sealed_members[role].path),
                )
                if sealed_members[role].sha256 == expected_sha
                else (_ for _ in ()).throw(AssertionError("wrong sealed member SHA")),
            )
            main = GATE._load_builder_main(
                snapshots["builder"], snapshots["shared"], snapshots["splitter"]
            )
            assert main.__code__.co_filename == str(paths["builder"])
            assert "SENTINEL_UNVERIFIED_PATH_MODULE" not in main.__globals__
            assert (
                main.__globals__["controlled_contract"].__verified_snapshot_sha256__
                == snapshots["shared"].sha256
            )
            assert (
                main.__globals__["model_splitting"].__verified_snapshot_sha256__
                == snapshots["splitter"].sha256
            )
        finally:
            for path, backup in backups:
                path.unlink()
                os.replace(backup, path)
        with pytest.raises(GATE.MaterializationGateError, match="continuity failed"):
            held.assert_continuity()
    finally:
        held.close()


def test_go_validation_consumes_one_held_snapshot_during_transient_substitution(
    prepared: dict[str, object],
) -> None:
    payload = _go_payload(prepared)
    path = _write_go(prepared, payload, "held-go.json")
    snapshot = GATE._HeldSnapshot(
        path,
        "held GO test",
        expected_sha256=_sha(path),
    )
    backup = path.with_suffix(".held-original")
    try:
        os.replace(path, backup)
        path.write_text('{"schema":"SUBSTITUTED_UNREVIEWED_GO"}\n', encoding="utf-8")
        observed, raw, digest = GATE._validate_go(
            path,
            snapshot.sha256,
            prepared["audit"],
            go_snapshot=snapshot,
        )
        assert observed == payload
        assert raw == snapshot.raw
        assert digest == snapshot.sha256
        path.unlink()
        os.replace(backup, path)
        with pytest.raises(GATE.MaterializationGateError, match="continuity failed"):
            snapshot.assert_continuity()
    finally:
        if backup.exists():
            if path.exists():
                path.unlink()
            os.replace(backup, path)
        snapshot.close()


@pytest.mark.parametrize("mutation", ["missing", "wrong_scope", "wrong_nonce", "wrong_source", "wrong_code", "wrong_prereg", "wrong_authority", "nonindependent"])
def test_hostile_go_contract_rejected(prepared: dict[str, object], mutation: str) -> None:
    payload = _go_payload(prepared)
    if mutation == "missing":
        payload.pop("scope")
    elif mutation == "wrong_scope":
        payload["scope"] = "TRAINING"
    elif mutation == "wrong_nonce":
        payload["challenge_nonce"] = "0" * 32
    elif mutation == "wrong_source":
        payload["bindings"]["artifact_sha256"]["historical_10k_csv"] = "0" * 64
    elif mutation == "wrong_code":
        payload["bindings"]["artifact_sha256"]["materialization_builder_code"] = "0" * 64
    elif mutation == "wrong_prereg":
        payload["bindings"]["artifact_sha256"]["preregistration_v1"] = "0" * 64
    elif mutation == "wrong_authority":
        payload["authorities"]["training"] = True
    elif mutation == "nonindependent":
        payload["reviewer"]["independent"] = False
    path = _write_go(prepared, payload, f"{mutation}.json")
    with pytest.raises(GATE.MaterializationGateError):
        GATE._validate_go(path, _sha(path), prepared["audit"])


@pytest.mark.parametrize("attack", ["bool_int_alias", "duplicate_key", "nonfinite"])
def test_external_go_rejects_json_type_alias_duplicate_and_nonfinite(
    prepared: dict[str, object], attack: str
) -> None:
    payload = _go_payload(prepared)
    path = Path(prepared["base"]) / f"strict-{attack}.json"
    if attack == "bool_int_alias":
        payload["authorities"]["training"] = 0
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True)
        if attack == "duplicate_key":
            raw = raw.replace('"status": "GO"', '"status": "GO", "status": "GO"', 1)
        else:
            raw = raw.replace('"p0": 0', '"p0": NaN', 1)
        path.write_text(raw + "\n", encoding="utf-8")
    with pytest.raises(GATE.MaterializationGateError):
        GATE._validate_go(path, _sha(path), prepared["audit"])


@pytest.mark.parametrize("timing", ["stale", "future", "overlong"])
def test_go_freshness_rejected(prepared: dict[str, object], timing: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = _go_payload(prepared, now=now)
    if timing == "stale":
        payload["issued_utc"] = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload["expires_utc"] = (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif timing == "future":
        payload["issued_utc"] = (now + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        payload["issued_utc"] = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload["expires_utc"] = (now + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _write_go(prepared, payload, f"{timing}.json")
    with pytest.raises(GATE.MaterializationGateError):
        GATE._validate_go(path, _sha(path), prepared["audit"], now=now)


@pytest.mark.parametrize("existing", ["output", "receipt"])
def test_existing_single_use_path_rejected_without_modification(prepared: dict[str, object], existing: str) -> None:
    path = Path(prepared[existing])
    path.mkdir()
    marker = path / "third_party.txt"
    marker.write_text("do not touch\n", encoding="utf-8")
    go = _write_go(prepared, _go_payload(prepared))
    with pytest.raises(FileExistsError):
        GATE.main(_execute_argv(prepared, go))
    assert marker.read_text() == "do not touch\n"


def test_duplicate_controlled_process_rejected_before_intent(prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    go = _write_go(prepared, _go_payload(prepared))
    monkeypatch.setattr(
        GATE,
        "_scan_linux_current_uid_processes",
        lambda *_args: _process_audit(duplicate=True),
    )
    with pytest.raises(GATE.MaterializationGateError):
        GATE.main(_execute_argv(prepared, go))
    assert not Path(prepared["receipt"]).exists()
    assert not Path(prepared["output"]).exists()


def test_execute_rejects_nonsealed_numpy_runtime_before_intent(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    go = _write_go(prepared, _go_payload(prepared))
    monkeypatch.setattr(
        GATE,
        "_active_numpy_identity",
        lambda: (GATE.EXPECTED_PRODUCTION_NUMPY_VERSION, "/host/site-packages/numpy"),
    )
    with pytest.raises(
        GATE.MaterializationGateError,
        match="descriptor-sealed Python/NumPy runtime",
    ):
        GATE.main(_execute_argv(prepared, go))
    assert not Path(prepared["receipt"]).exists()
    assert not Path(prepared["output"]).exists()


def test_singleton_audit_rejects_identity_invalid_current_process() -> None:
    audit = _process_audit()
    audit["matches"][0]["identity_valid"] = False
    with pytest.raises(GATE.MaterializationGateError, match="only the current"):
        GATE._validate_singleton(audit)


def test_second_singleton_scan_blocks_builder_invocation(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    go = _write_go(prepared, _go_payload(prepared))
    audits = iter((_process_audit(), _process_audit(duplicate=True)))
    monkeypatch.setattr(
        GATE, "_scan_linux_current_uid_processes", lambda *_args: next(audits)
    )
    invoked = False

    def fake_builder(
        _argv: list[str], *, verified_context: dict[str, object]
    ) -> int:
        nonlocal invoked
        invoked = True
        return 0

    monkeypatch.setattr(GATE, "_load_builder_main", lambda *_args: fake_builder)
    with pytest.raises(GATE.MaterializationGateError, match="exactly one"):
        GATE.main(_execute_argv(prepared, go))
    receipt = Path(prepared["receipt"])
    assert invoked is False
    assert (receipt / GATE.INTENT_NAME).is_file()
    assert not (receipt / GATE.RUNNING_NAME).exists()
    assert (receipt / GATE.FAIL_NAME).is_file()


def test_builder_failure_preserves_partial_and_consumes_attempt(prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    go = _write_go(prepared, _go_payload(prepared))
    _install_fake_execute(monkeypatch, prepared, mode="fail")
    with pytest.raises(RuntimeError, match="synthetic builder failure"):
        GATE.main(_execute_argv(prepared, go))
    receipt = Path(prepared["receipt"])
    output = Path(prepared["output"])
    assert (receipt / GATE.INTENT_NAME).is_file()
    assert (receipt / GATE.RUNNING_NAME).is_file()
    assert (receipt / GATE.FAIL_NAME).is_file()
    assert (output / "partial.txt").read_text() == "preserve me\n"
    failure = json.loads((receipt / GATE.FAIL_NAME).read_text())
    assert failure["retry_authorized"] is False
    assert failure["partial_material_output_preserved"] is True


def test_tampered_material_output_fails_deep_validation(prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    go = _write_go(prepared, _go_payload(prepared))
    _install_fake_execute(monkeypatch, prepared, mode="tamper")
    with pytest.raises(GATE.MaterializationGateError, match="SHA mismatch"):
        GATE.main(_execute_argv(prepared, go))
    receipt = Path(prepared["receipt"])
    assert (receipt / GATE.FAIL_NAME).is_file()
    assert not (receipt / GATE.COMPLETE_NAME).exists()


def test_exact_success_then_replayed_go_is_rejected(prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    go = _write_go(prepared, _go_payload(prepared))
    _install_fake_execute(monkeypatch, prepared, mode="success")
    assert GATE.main(_execute_argv(prepared, go)) == 0
    receipt = Path(prepared["receipt"])
    complete = json.loads((receipt / GATE.COMPLETE_NAME).read_text())
    assert complete["status"] == "COMPLETE_RESULT_BLIND_MATERIALIZATION_DEEP_VALIDATED"
    assert complete["materialization_validation"]["arm_rows"] == {"n10000": 6, "n20000": 8}
    assert complete["candidate_manifest"] == {
        "path": str(Path(prepared["candidate"]) / GATE.MANIFEST_NAME),
        "sha256": str(prepared["manifest_sha"]),
    }
    assert complete["candidate_sha_index"] == {
        "path": str(Path(prepared["candidate"]) / GATE.SHA_INDEX_NAME),
        "sha256": str(prepared["index_sha"]),
    }
    assert complete["materialization_go_authority"] == {
        "path": str(receipt / GATE.GO_COPY_NAME),
        "sha256": _sha(receipt / GATE.GO_COPY_NAME),
    }
    assert complete["materialization_output"]["sha256sums"] == {
        "path": str(Path(prepared["output"]) / GATE.MATERIAL_SHA_INDEX_NAME),
        "sha256": _sha(Path(prepared["output"]) / GATE.MATERIAL_SHA_INDEX_NAME),
    }
    assert (
        complete["materialization_output"]["artifact_closure"]
        == complete["materialization_validation"]["artifact_closure"]
    )
    assert complete["training_authorized"] is False
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o555
    with pytest.raises(FileExistsError):
        GATE.main(_execute_argv(prepared, go))
