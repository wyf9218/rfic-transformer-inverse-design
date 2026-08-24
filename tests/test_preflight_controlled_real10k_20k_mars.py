from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_SCRIPT = ROOT / "scripts" / "build_controlled_real10k_20k_mars_package.py"
BUILDER_TEST_SCRIPT = ROOT / "tests" / "test_build_controlled_real10k_20k_mars_package.py"
PREFLIGHT_SCRIPT = ROOT / "scripts" / "preflight_controlled_real10k_20k_mars.py"
NATIVE_SMOKE_SCRIPT = ROOT / "tests" / "controlled_real10k_20k_mars_native_smoke.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("controlled_package_builder_for_preflight_tests", BUILDER_SCRIPT)
builder_fixture = _load(
    "controlled_package_builder_fixture_for_preflight_tests", BUILDER_TEST_SCRIPT
)
preflight = _load("controlled_mars_preflight_test_module", PREFLIGHT_SCRIPT)
REAL_RUNTIME_PROBE = preflight._runtime_probe
REAL_RUN_NATIVE_TESTS = preflight._run_native_tests


BOOT_ID = "11111111-1111-1111-1111-111111111111"
CORE_SHA = "a" * 64
CONFIG_FILE_SHA = "b" * 64
SHOW_CONFIG_SHA = "c" * 64
NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> str:
    path.write_bytes(preflight._json_bytes(value))
    return _sha256(path)


def _set_arg(argv: list[str], option: str, value: str) -> None:
    argv[argv.index(option) + 1] = value


def _thaw_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir() and not path.is_symlink():
            try:
                path.chmod(0o755)
            except FileNotFoundError:
                pass
    root.chmod(0o755)


@pytest.fixture(autouse=True)
def _restore_tmp_permissions(tmp_path: Path):
    yield
    _thaw_tree(tmp_path)


def _prepare_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auto_prepare: bool = True,
) -> dict[str, Any]:
    build_case = builder_fixture._make_case(tmp_path)
    spec_roles = build_case.spec["roles"]
    assert isinstance(spec_roles, dict)
    for role, source in {
        "preflight_code": PREFLIGHT_SCRIPT,
        "native_smoke_test": NATIVE_SMOKE_SCRIPT,
    }.items():
        spec_roles[role] = {
            "kind": "file",
            "source_path": str(source.resolve(strict=True)),
            "sha256": _sha256(source),
        }

    # The sealed runtime archive independently binds native-smoke bytes. Keep
    # that member and its entrypoint identity synchronized with the package role.
    closure = json.loads(build_case.closure_path.read_text(encoding="utf-8"))
    fixture_python = Path(sys.executable).resolve(strict=True)
    closure["python"]["executable_sha256"] = _sha256(fixture_python)
    closure["system_library_allowlist"] = list(
        builder_fixture.builder.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
    )
    native_member = next(
        member for member in closure["members"] if member["role"] == "native_smoke_test"
    )
    native_payload = NATIVE_SMOKE_SCRIPT.read_bytes()
    native_member["sha256"] = hashlib.sha256(native_payload).hexdigest()
    native_member["size_bytes"] = len(native_payload)
    closure["entrypoints"]["native_smoke"]["sha256"] = native_member["sha256"]
    build_case.member_bodies[native_member["path"]] = native_payload
    pure_path = build_case.tree / closure["pure_archive"]["path"]
    pure_path.write_bytes(
        builder_fixture._zip_payload(closure["members"], build_case.member_bodies)
    )
    closure["pure_archive"]["sha256"] = _sha256(pure_path)
    closure["pure_archive"]["size_bytes"] = pure_path.stat().st_size
    build_case.rewrite_closure(closure)

    role_paths = {
        role: Path(record["source_path"])
        for role, record in spec_roles.items()
        if isinstance(record, dict) and record.get("kind") == "file"
    }
    source_identities = {
        role: _sha256(role_paths[role])
        for role in (
            "authoritative_100k_csv",
            "historical_10k_csv",
            "historical_model_summary_json",
        )
    }
    monkeypatch.setattr(preflight, "REQUIRED_SOURCE_ROLES", source_identities)
    preregistration_identities = {
        role: _sha256(role_paths[role])
        for role in (
            "preregistration_v1_json",
            "preregistration_addendum_v1_1_json",
            "preregistration_addendum_v1_2_json",
        )
    }
    monkeypatch.setattr(
        preflight, "REQUIRED_PREREGISTRATION_ROLES", preregistration_identities
    )
    monkeypatch.setattr(
        preflight, "FROZEN_TRAINER_SHA256", _sha256(role_paths["trainer_code"])
    )

    built = builder_fixture._build(build_case)
    package_root = built["package_root"]
    manifest_sha = _sha256(built["manifest"])
    index_sha = _sha256(built["sha_index"])
    commit_sha = _sha256(built["package_commit"])
    build_attempt_body_path = built["build_attempt_receipt"]
    build_attempt_body_sha = _sha256(build_attempt_body_path)
    build_attempt_committed_path = built["build_attempt_committed"]
    build_attempt_committed_sha = _sha256(build_attempt_committed_path)
    package = preflight._audit_package(
        package_root.resolve(),
        expected_manifest_sha256=manifest_sha,
        expected_index_sha256=index_sha,
        expected_commit_sha256=commit_sha,
        build_attempt_body=build_attempt_body_path,
        expected_build_attempt_body_sha256=build_attempt_body_sha,
        build_attempt_committed=build_attempt_committed_path,
        expected_build_attempt_committed_sha256=build_attempt_committed_sha,
    )
    # This long-lived fixture snapshot is used by direct consumer tests. The
    # production PREPARE/EXECUTE audits acquire their own full-lifetime lock.
    preflight.fcntl.flock(
        package["singleton_lock_file"].descriptor, preflight.fcntl.LOCK_UN
    )

    python = Path(sys.executable).resolve(strict=True)
    python_sha = _sha256(python)
    hostname = "frozen-mars-host"
    uid = os.getuid()
    candidates = [
        (tmp_path / name).resolve()
        for name in preflight.REQUIRED_CANDIDATE_OUTPUT_NAMES
    ]
    candidate = candidates[0]
    receipt_dir = (tmp_path / "preflight_receipt").resolve()
    native_roles = list(preflight.REQUIRED_NATIVE_TEST_ROLES)

    monkeypatch.setattr(preflight.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(preflight.os, "getuid", lambda: uid)
    monkeypatch.setattr(preflight, "_boot_id", lambda: BOOT_ID)
    monkeypatch.setattr(preflight, "_now_utc", lambda: NOW)
    monkeypatch.setattr(
        preflight,
        "_scan_current_uid_processes",
        lambda _package, _python_path: {
            "schema": "controlled_real10k_20k_preflight_process_audit_v2",
            "uid": uid,
            "current_pid": os.getpid(),
            "substring_matching_used": False,
            "exact_argv_executable_and_descriptor_identity_required": True,
            "matches": [],
            "match_count": 0,
        },
    )
    active_runtime = {
        "schema": preflight.RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "native_smoke",
        "manifest_sha256": package["role_identity"][
            "runtime_dependency_closure_json"
        ]["sha256"],
        "pure_archive_sha256": package["runtime_dependency_closure"]["pure_archive"][
            "sha256"
        ],
        "bootstrap_sha256": package["role_identity"]["runtime_bootstrap_code"][
            "sha256"
        ],
    }
    startup = {
        **active_runtime,
        "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
    }
    terminal = {
        **active_runtime,
        "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
        "exit_code": 0,
    }
    runtime = {
        "schema": "controlled_real10k_20k_preflight_runtime_identity_v2",
        "python_executable_path": str(python),
        "python_executable_sha256": python_sha,
        "python_version": preflight.EXPECTED_PYTHON_VERSION,
        "numpy_version": preflight.EXPECTED_NUMPY_VERSION,
        "active_runtime": active_runtime,
        "startup_attestation": startup,
        "terminal_attestation": terminal,
        "compiled_role_count": sum(
            role in preflight.REQUIRED_CODE_ROLES
            or role in preflight.REQUIRED_NATIVE_TEST_ROLES
            for role in package["roles"]
        ),
        "consumed_code_role_sha256": dict(package["code_role_sha256"]),
        "native_smoke_result_sha256": "d" * 64,
        "native_smoke_attestation_sha256": "e" * 64,
        "descriptor_closed": True,
        "raw_runtime_fallback_authorized": False,
    }
    fake_native_tests = {
        "requested": True,
        "roles": list(preflight.REQUIRED_NATIVE_TEST_ROLES),
        "returncode": 0,
        "executed_test_count": 1,
        "exact_structured_pass": True,
        "runtime_identity": runtime,
    }
    monkeypatch.setattr(
        preflight,
        "_run_native_tests",
        lambda _python, _package, _roles, _timeout: dict(fake_native_tests),
    )
    monkeypatch.setattr(
        preflight,
        "_load_snapshot",
        lambda: {"load1": 1.0, "load5": 1.25, "load15": 1.5},
    )

    common_argv = [
        "--package-dir",
        str(package_root.resolve()),
        "--expected-manifest-sha256",
        manifest_sha,
        "--expected-sha-index-sha256",
        index_sha,
        "--expected-package-commit-sha256",
        commit_sha,
        "--package-build-attempt-body",
        str(build_attempt_body_path),
        "--expected-package-build-attempt-body-sha256",
        build_attempt_body_sha,
        "--package-build-attempt-committed",
        str(build_attempt_committed_path),
        "--expected-package-build-attempt-committed-sha256",
        build_attempt_committed_sha,
        "--python-executable",
        str(python),
        "--expected-python-executable-sha256",
        python_sha,
        "--expected-hostname",
        hostname,
        "--expected-uid",
        str(uid),
        "--expected-boot-id",
        BOOT_ID,
    ]
    for candidate_path in candidates:
        common_argv.extend(["--candidate-output-dir", str(candidate_path)])
    for role in native_roles:
        common_argv.extend(["--native-test-role", role])
    common_argv.extend(["--receipt-dir", str(receipt_dir)])
    prepare_argv = ["--phase", "PREPARE", *common_argv]
    if auto_prepare:
        assert preflight.main(prepare_argv) == 0
        transaction = preflight._open_execution_transaction(str(receipt_dir))
        try:
            transaction_binding = preflight._receipt_transaction_go_binding(transaction)
        finally:
            transaction.close()
    else:
        transaction_binding = {}
    candidate_paths = preflight._candidate_output_dirs(
        [str(path) for path in candidates], package["root"], receipt_dir
    )
    bindings = preflight._expected_go_bindings(
        package=package,
        python_sha256=python_sha,
        hostname=hostname,
        uid=uid,
        boot_id=BOOT_ID,
        candidate_output_dirs=candidate_paths,
        native_test_roles=native_roles,
        receipt_dir=receipt_dir,
        receipt_transaction=transaction_binding,
    )
    go = {
        "schema": preflight.CODE_GO_SCHEMA,
        "status": "PASS",
        "verdict": "EXACT_CODE_GO",
        "scope": preflight.CODE_GO_SCOPE,
        "issued_utc": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_utc": (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nonce": "0123456789abcdef0123456789abcdef",
        "review": {"independent": True, "result_blind": True},
        "findings": {"p0": 0, "p1": 0},
        "bindings": bindings,
        "authorities": dict(preflight.EXPECTED_AUTHORITIES),
    }
    go_path = (tmp_path / "CODE_GO.json").resolve()
    go_sha = _write_json(go_path, go)
    argv = [
        "--phase",
        "EXECUTE",
        *common_argv,
        "--code-go-receipt",
        str(go_path),
        "--expected-code-go-receipt-sha256",
        go_sha,
    ]
    return {
        "argv": argv,
        "prepare_argv": prepare_argv,
        "package": package,
        "package_root": package_root,
        "manifest_sha": manifest_sha,
        "index_sha": index_sha,
        "commit_sha": commit_sha,
        "build_attempt_body_path": build_attempt_body_path,
        "build_attempt_body_sha": build_attempt_body_sha,
        "build_attempt_committed_path": build_attempt_committed_path,
        "build_attempt_committed_sha": build_attempt_committed_sha,
        "go": go,
        "go_path": go_path,
        "receipt_dir": receipt_dir,
        "candidate": candidate,
        "candidates": candidates,
        "runtime": runtime,
        "python": python,
    }


def _receipt(case: dict[str, Any]) -> dict[str, Any]:
    root = case["receipt_dir"]
    for name in (preflight.PREFLIGHT_FATAL_FAIL_NAME, preflight.PREFLIGHT_BODY_NAME):
        path = root / name
        if path.exists():
            return json.loads(path.read_text())
    raise AssertionError("receipt body or terminal failure is missing")


@pytest.mark.parametrize("value", [123, True, "A" * 64, "a" * 64 + " "])
def test_sha256_parser_rejects_type_case_and_whitespace_aliases(value: Any) -> None:
    with pytest.raises(preflight.PreflightError, match="lowercase SHA-256"):
        preflight._normalized_sha(value, "hostile SHA")


def _install_fake_sealed_native_runtime(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Exercise the sealed protocol on non-Linux CI without faking its evidence."""

    package = case["package"]
    manifest = json.loads(
        Path(
            package["roles"]["runtime_dependency_closure_json"]["absolute_path"]
        ).read_text(encoding="utf-8")
    )
    captured: dict[str, Any] = {}

    class FakeLaunch:
        def __init__(self, attestation_fd: int) -> None:
            self.attestation_fd = attestation_fd
            self.manifest = manifest
            self.process_argv_suffix = [
                "/proc/self/fd/200",
                "--request-fd",
                "201",
                "--entrypoint",
                "native_smoke",
            ]
            self.pass_fds = (200, 201, 202, 203, 204)
            self.request = {"closure_identity": "fixture-descriptor-closure"}

        def __enter__(self) -> "FakeLaunch":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    class FakeBootstrap:
        @staticmethod
        def prepare_sealed_runtime_launch(**kwargs: Any) -> FakeLaunch:
            assert kwargs["expected_manifest_sha256"] == package["role_identity"][
                "runtime_dependency_closure_json"
            ]["sha256"]
            assert kwargs["expected_bootstrap_sha256"] == package["role_identity"][
                "runtime_bootstrap_code"
            ]["sha256"]
            assert kwargs["entrypoint"] == "native_smoke"
            assert kwargs["entrypoint_argv"] == [
                package["roles"]["native_smoke_test"]["absolute_path"]
            ]
            launch = FakeLaunch(kwargs["attestation_output_fd"])
            captured["launch"] = launch
            return launch

    def consume_descriptor(record: dict[str, Any]) -> None:
        assert set(record) == {"descriptor_path", "display_path", "sha256"}
        descriptor = int(record["descriptor_path"].rsplit("/", 1)[1])
        payload = preflight._read_descriptor(descriptor)
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
        assert b"TRANSIENT MALICIOUS" not in payload

    def fake_verified_python(
        _python: Any,
        arguments: list[str],
        *,
        pass_fds: tuple[int, ...],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[bytes]:
        assert arguments == [
            "-I",
            "-B",
            "-S",
            "/proc/self/fd/200",
            "--request-fd",
            "201",
            "--entrypoint",
            "native_smoke",
        ]
        assert {200, 201, 202, 203, 204}.issubset(set(pass_fds))
        assert kwargs["env"] == preflight._minimal_runtime_environment(
            native_smoke=True
        )
        request = json.loads(kwargs["input"])
        assert set(request) == {
            "schema",
            "manifest",
            "receipt",
            "independent_qa_required",
            "sha_index",
            "package_commit",
            "package_build_attempt_body",
            "package_build_attempt_committed",
            "process_singleton_lock",
            "indexed_files",
            "roles",
            "runtime_manifest_sha256",
        }
        assert request["schema"] == preflight.NATIVE_SMOKE_REQUEST_SCHEMA
        for key in (
            "manifest",
            "receipt",
            "independent_qa_required",
            "sha_index",
            "package_commit",
            "package_build_attempt_body",
            "package_build_attempt_committed",
            "process_singleton_lock",
        ):
            consume_descriptor(request[key])
        for record in request["indexed_files"].values():
            consume_descriptor(record)
        assert set(request["roles"]) == preflight.PACKAGE_REQUIRED_ROLES
        for role_record in request["roles"].values():
            if role_record["kind"] == "file":
                consume_descriptor(
                    {key: role_record[key] for key in (
                        "descriptor_path", "display_path", "sha256"
                    )}
                )
            else:
                assert role_record["kind"] == "tree"
                for member in role_record["members"]:
                    consume_descriptor(
                        {key: member[key] for key in (
                            "descriptor_path", "display_path", "sha256"
                        )}
                    )

        module_origins = {
            record["module"]: {
                "kind": "sealed_pure_zip",
                "origin": f"descriptor-zip:/proc/self/fd/203!/{record['path']}",
                "sha256": record["sha256"],
            }
            for record in manifest["members"]
            if record["module"] is not None
        }
        for index, record in enumerate(manifest["native_extensions"], start=205):
            module_origins[record["module"]] = {
                "kind": "sealed_native_extension",
                "origin": f"/proc/self/fd/{index}",
                "sha256": record["sha256"],
            }
        active_runtime = {
            "schema": preflight.RUNTIME_ATTESTATION_SCHEMA,
            "entrypoint": "native_smoke",
            "manifest_sha256": request["runtime_manifest_sha256"],
            "pure_archive_sha256": manifest["pure_archive"]["sha256"],
            "bootstrap_sha256": package["role_identity"]["runtime_bootstrap_code"][
                "sha256"
            ],
        }
        startup = {
            **active_runtime,
            "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
            "entrypoint_sha256": package["role_identity"]["native_smoke_test"][
                "sha256"
            ],
            "python": {
                key: manifest["python"][key]
                for key in ("implementation", "version", "abi_tag", "platform")
            },
            "python_flags": {
                "isolated": 1,
                "no_site": 1,
                "dont_write_bytecode": True,
            },
            "numpy_version": manifest["numpy"]["version"],
            "module_origins": module_origins,
            "native_library_sha256": {
                record["soname"]: record["sha256"]
                for record in manifest["native_libraries"]
            },
            "native_extension_sha256": {
                record["module"]: record["sha256"]
                for record in manifest["native_extensions"]
            },
            "system_library_allowlist": manifest["system_library_allowlist"],
            "site_initialization_disabled": True,
            "external_package_fallback_allowed": False,
        }
        terminal = {
            **active_runtime,
            "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
            "exit_code": 0,
            "module_origins": module_origins,
            "system_library_allowlist": manifest["system_library_allowlist"],
            "external_package_fallback_allowed": False,
        }
        launch = captured["launch"]
        attestation_payload = b"".join(
            (
                json.dumps(
                    record,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            for record in (startup, terminal)
        )
        os.write(
            launch.attestation_fd,
            attestation_payload,
        )
        os.fsync(launch.attestation_fd)
        result = {
            "schema": preflight.NATIVE_SMOKE_RESULT_SCHEMA,
            "status": "PASS",
            "test_id": preflight.NATIVE_SMOKE_TEST_ID,
            "manifest_sha256": package["manifest_sha256"],
            "sha_index_sha256": package["index_sha256"],
            "package_commit_sha256": package["commit_sha256"],
            "package_build_attempt_body_sha256": package[
                "build_attempt_body_sha256"
            ],
            "package_build_attempt_committed_sha256": package[
                "build_attempt_committed_sha256"
            ],
            "runtime_manifest_sha256": request["runtime_manifest_sha256"],
            "role_count": len(preflight.PACKAGE_REQUIRED_ROLES),
            "compiled_python_role_count": sum(
                role in preflight.REQUIRED_CODE_ROLES
                or role in preflight.REQUIRED_NATIVE_TEST_ROLES
                for role in package["roles"]
            ),
            "consumed_role_sha256": dict(package["role_sha256"]),
            "checks": {
                "isolated_python_I_B_S": True,
                "exact_package_v5_role_destinations": True,
                "descriptor_snapshots_only": True,
                "package_commit_and_external_attempt_body_committed_bound": True,
                "package_sha_closure": True,
                "runtime_dependency_closure_bound": True,
                "process_singleton_contract_bound": True,
                "sealed_materialization_runner_trainer_evaluator_imported": True,
                "numpy_shared_splitter_descriptor_bound": True,
                "active_descriptor_runtime_exact": True,
                "result_blind": True,
                "no_training_metrics_emx_or_signal": True,
            },
            "runtime": active_runtime,
        }
        captured["request"] = request
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(
            args=arguments,
            returncode=0,
            stdout=preflight._json_bytes(result),
            stderr=b"",
        )

    monkeypatch.setattr(
        preflight, "_load_verified_runtime_bootstrap", lambda _package: FakeBootstrap
    )
    monkeypatch.setattr(preflight, "_run_verified_python", fake_verified_python)
    return captured


def _replace_then_restore_during(
    target: Path,
    malicious_payload: bytes,
    action: Callable[[], Any],
) -> Any:
    parent = target.parent
    backup = parent / f".{target.name}.verified-backup"
    original_parent_mode = stat.S_IMODE(parent.stat().st_mode)
    parent.chmod(0o755)
    os.replace(target, backup)
    target.write_bytes(malicious_payload)
    target.chmod(0o444)
    try:
        return action()
    finally:
        target.unlink()
        os.replace(backup, target)
        parent.chmod(original_parent_mode)


def test_exact_go_produces_preflight_only_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    assert preflight.main(case["argv"]) == 0
    receipt = _receipt(case)
    assert receipt["schema"] == preflight.PREFLIGHT_SCHEMA
    assert receipt["status"] == "PASS_BODY_AWAITING_DURABLE_COMMIT"
    assert receipt["external_code_go"]["scope"] == preflight.CODE_GO_SCOPE
    assert receipt["external_code_go"]["nonce"] == case["go"]["nonce"]
    assert receipt["external_code_go"]["bound_preflight_receipt_dir"] == str(
        case["receipt_dir"]
    )
    assert receipt["checks"]["external_code_go_fresh"] is True
    assert receipt["checks"]["external_code_go_single_use_receipt_dir_bound"] is True
    assert receipt["host_load_snapshot"]["gate_applied"] is False
    assert all(value is False for value in receipt["authorities"].values())
    assert receipt["preflight_pass"] is False
    assert stat.S_IMODE(case["receipt_dir"].stat().st_mode) == 0o555
    receipt_path = case["receipt_dir"] / preflight.PREFLIGHT_COMMITTED_NAME
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o444
    assert receipt_path.stat().st_nlink == 1
    committed = json.loads(receipt_path.read_text())
    assert committed["status"] == "COMMITTED_PASS_PREFLIGHT_ONLY"
    assert set(path.name for path in case["receipt_dir"].iterdir()) == set(
        preflight.SUCCESS_ROOT_FILES
    )


@pytest.mark.parametrize(
    "attack",
    [
        "reserved_empty",
        "missing",
        "corrupt",
        "swap",
        "extra",
        "root_replacement",
        "old_body_schema_v2",
    ],
)
def test_package_attempt_requires_exact_durable_two_file_terminal_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    package = case["package"]
    resources = [
        *package["held_by_relative"].values(),
        *package["held_directories"],
    ]
    if package["execution_file"] not in package["held_by_relative"].values():
        resources.append(package["execution_file"])
    preflight._close_verified_files(resources)
    root = case["build_attempt_body_path"].parent
    body = case["build_attempt_body_path"]
    committed = case["build_attempt_committed_path"]
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
    elif attack == "root_replacement":
        displaced = root.with_name(root.name + "_displaced")
        root.rename(displaced)
        root.mkdir()
        body = root / preflight.PACKAGE_BUILD_ATTEMPT_BODY_NAME
        committed = root / preflight.PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
        body.write_bytes(body_bytes)
        committed.write_bytes(committed_bytes)
        body.chmod(0o444)
        committed.chmod(0o444)
    else:
        payload = json.loads(body_bytes)
        payload["schema"] = (
            "controlled_real10k_20k_mars_package_build_attempt_receipt_v2"
        )
        body.chmod(0o644)
        body.write_bytes(preflight._json_bytes(payload))
        body.chmod(0o444)
        terminal = json.loads(committed_bytes)
        terminal["body"]["sha256"] = _sha256(body)
        committed.chmod(0o644)
        committed.write_bytes(preflight._json_bytes(terminal))
        committed.chmod(0o444)
    root.chmod(0o555)
    expected_body_sha = _sha256(body) if body.exists() else "0" * 64
    expected_committed_sha = _sha256(committed) if committed.exists() else "0" * 64
    with pytest.raises(preflight.PreflightError):
        preflight._audit_package(
            case["package_root"].resolve(),
            expected_manifest_sha256=case["manifest_sha"],
            expected_index_sha256=case["index_sha"],
            expected_commit_sha256=case["commit_sha"],
            build_attempt_body=body,
            expected_build_attempt_body_sha256=expected_body_sha,
            build_attempt_committed=committed,
            expected_build_attempt_committed_sha256=expected_committed_sha,
        )


def test_preflight_rejects_superseded_package_v4_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    package = case["package"]
    resources = [
        *package["held_by_relative"].values(),
        *package["held_directories"],
    ]
    if package["execution_file"] not in package["held_by_relative"].values():
        resources.append(package["execution_file"])
    preflight._close_verified_files(resources)
    root = case["package_root"]
    manifest_path = root / preflight.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_version"] = "v4"
    root.chmod(0o755)
    manifest_path.chmod(0o644)
    manifest_path.write_bytes(preflight._json_bytes(manifest))
    manifest_path.chmod(0o444)
    root.chmod(0o555)
    with pytest.raises(preflight.PreflightError, match="status or role contract"):
        preflight._audit_package(
            root.resolve(),
            expected_manifest_sha256=_sha256(manifest_path),
            expected_index_sha256=case["index_sha"],
            expected_commit_sha256=case["commit_sha"],
            build_attempt_body=case["build_attempt_body_path"],
            expected_build_attempt_body_sha256=case["build_attempt_body_sha"],
            build_attempt_committed=case["build_attempt_committed_path"],
            expected_build_attempt_committed_sha256=case[
                "build_attempt_committed_sha"
            ],
        )


@pytest.mark.parametrize(
    "attack", ["wrong_scope", "wrong_expected_sha", "superseded_v2_schema"]
)
def test_wrong_external_go_fails_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    runtime_called = False

    def forbidden_runtime(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal runtime_called
        runtime_called = True
        raise AssertionError("runtime must not run before exact CODE_GO")

    monkeypatch.setattr(preflight, "_runtime_probe", forbidden_runtime)
    if attack == "wrong_scope":
        case["go"]["scope"] = "MARS_NATIVE_TEST_AND_DATA_MATERIALIZATION_ONLY"
        go_sha = _write_json(case["go_path"], case["go"])
        _set_arg(case["argv"], "--expected-code-go-receipt-sha256", go_sha)
    elif attack == "wrong_expected_sha":
        _set_arg(case["argv"], "--expected-code-go-receipt-sha256", "f" * 64)
    else:
        case["go"]["schema"] = "controlled_real10k_20k_mars_code_go_v2"
        go_sha = _write_json(case["go_path"], case["go"])
        _set_arg(case["argv"], "--expected-code-go-receipt-sha256", go_sha)
    assert preflight.main(case["argv"]) == 2
    assert _receipt(case)["status"] == "FAIL_NO_GO"
    assert runtime_called is False


@pytest.mark.parametrize(
    "attack",
    ["stale", "future", "overlong", "malformed_timestamp", "malformed_nonce"],
)
def test_code_go_freshness_and_nonce_fail_closed_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    runtime_called = False

    def forbidden_runtime(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal runtime_called
        runtime_called = True
        raise AssertionError("runtime must not run for invalid freshness")

    monkeypatch.setattr(preflight, "_runtime_probe", forbidden_runtime)
    if attack == "stale":
        case["go"]["issued_utc"] = (NOW - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        case["go"]["expires_utc"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif attack == "future":
        case["go"]["issued_utc"] = (NOW + timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        case["go"]["expires_utc"] = (NOW + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    elif attack == "overlong":
        case["go"]["issued_utc"] = (NOW - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        case["go"]["expires_utc"] = (NOW + timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    elif attack == "malformed_timestamp":
        case["go"]["issued_utc"] = "2026-08-24T11:00:00+00:00"
    else:
        case["go"]["nonce"] = "NOT-A-STRICT-NONCE"
    go_sha = _write_json(case["go_path"], case["go"])
    _set_arg(case["argv"], "--expected-code-go-receipt-sha256", go_sha)
    assert preflight.main(case["argv"]) == 2
    assert _receipt(case)["status"] == "FAIL_NO_GO"
    assert runtime_called is False


def test_code_go_cannot_replay_to_alternate_receipt_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    alternate = (tmp_path / "alternate_preflight_receipt").resolve()
    alternate_prepare = list(case["prepare_argv"])
    _set_arg(alternate_prepare, "--receipt-dir", str(alternate))
    assert preflight.main(alternate_prepare) == 0
    alternate_execute = list(case["argv"])
    _set_arg(alternate_execute, "--receipt-dir", str(alternate))
    assert preflight.main(alternate_execute) == 2
    receipt = json.loads((alternate / preflight.PREFLIGHT_FATAL_FAIL_NAME).read_text())
    assert receipt["status"] == "FAIL_NO_GO"
    assert "does not exactly authorize" in receipt["reason"]
    assert (case["receipt_dir"] / preflight.PREPARED_NAME).exists()


def test_code_go_parsing_consumes_verified_snapshot_and_continuity_detects_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    go, verified = preflight._validate_code_go(
        case["go_path"], _sha256(case["go_path"]), case["go"]["bindings"]
    )
    try:
        consumed = _replace_then_restore_during(
            case["go_path"],
            b'{"schema":"MALICIOUS_TRANSIENT_GO"}\n',
            lambda: preflight._read_json_bytes(
                preflight._verified_bytes(verified), "held GO snapshot"
            ),
        )
        assert consumed == go == case["go"]
        try:
            preflight._verify_file_continuity(verified)
        except preflight.PreflightError as exc:
            assert "changed after verification" in str(exc)
    finally:
        verified.close()


@pytest.mark.parametrize("attack", ["empty", "wrong"])
def test_native_test_role_set_must_be_exact_and_nonempty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    position = case["argv"].index("--native-test-role")
    if attack == "empty":
        del case["argv"][position : position + 2]
    else:
        case["argv"][position + 1] = "unreviewed_test"
    assert preflight.main(case["argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert "native test role set/order must be exact and non-empty" in receipt["reason"]


def test_duplicate_process_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    token = "runner"
    monkeypatch.setattr(
        preflight,
        "_scan_current_uid_processes",
        lambda _package, _python_path: {
            "schema": "controlled_real10k_20k_preflight_process_audit_v2",
            "uid": os.getuid(),
            "current_pid": os.getpid(),
            "substring_matching_used": False,
            "exact_argv_executable_and_descriptor_identity_required": True,
            "matches": [{"pid": 4242, "entrypoint": token}],
            "match_count": 1,
        },
    )
    assert preflight.main(case["argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert "duplicate controlled processes" in receipt["reason"]


def test_existing_candidate_output_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    case["candidate"].mkdir()
    assert preflight.main(case["argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert "candidate output directories already exist" in receipt["reason"]


@pytest.mark.parametrize("attack", ["arbitrary", "wrong_order", "missing"])
def test_candidate_output_tuple_is_frozen_not_caller_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    positions = [
        index
        for index, value in enumerate(case["argv"])
        if value == "--candidate-output-dir"
    ]
    assert len(positions) == len(preflight.REQUIRED_CANDIDATE_OUTPUT_NAMES)
    if attack == "arbitrary":
        case["argv"][positions[0] + 1] = str((tmp_path / "caller_selected").resolve())
    elif attack == "wrong_order":
        first = case["argv"][positions[0] + 1]
        second = case["argv"][positions[1] + 1]
        case["argv"][positions[0] + 1] = second
        case["argv"][positions[1] + 1] = first
    else:
        del case["argv"][positions[-1] : positions[-1] + 2]
    assert preflight.main(case["argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert "candidate output directory tuple must be exact and ordered" in receipt["reason"]


def test_wrong_runtime_identity_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    wrong = dict(case["runtime"])
    wrong["python_version"] = "3.12.12"
    monkeypatch.setattr(
        preflight,
        "_runtime_probe",
        lambda _python, _package, _native_tests: wrong,
    )
    assert preflight.main(case["argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert "descriptor-sealed runtime identity python_version" in receipt["reason"]


def test_pass_receipt_binds_exact_python_executable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    assert preflight.main(case["argv"]) == 0
    receipt = _receipt(case)
    assert receipt["runtime_identity"]["python_executable_path"] == str(case["python"])
    assert receipt["runtime_identity"]["python_executable_sha256"] == _sha256(
        case["python"]
    )


def test_package_audit_rejects_wrong_frozen_preregistration_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    wrong = dict(preflight.REQUIRED_PREREGISTRATION_ROLES)
    wrong["preregistration_addendum_v1_2_json"] = "0" * 64
    monkeypatch.setattr(preflight, "REQUIRED_PREREGISTRATION_ROLES", wrong)
    with pytest.raises(preflight.PreflightError, match="frozen preregistration identity mismatch"):
        preflight._audit_package(
            case["package_root"],
            expected_manifest_sha256=case["manifest_sha"],
            expected_index_sha256=case["index_sha"],
            expected_commit_sha256=case["commit_sha"],
            build_attempt_body=case["build_attempt_body_path"],
            expected_build_attempt_body_sha256=case["build_attempt_body_sha"],
            build_attempt_committed=case["build_attempt_committed_path"],
            expected_build_attempt_committed_sha256=case[
                "build_attempt_committed_sha"
            ],
        )


def test_preflight_receipt_is_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    assert preflight.main(case["argv"]) == 0
    before = {
        path.name: _sha256(path) for path in case["receipt_dir"].iterdir() if path.is_file()
    }
    assert preflight.main(case["argv"]) == 2
    after = {
        path.name: _sha256(path) for path in case["receipt_dir"].iterdir() if path.is_file()
    }
    assert after == before


def test_catchable_runtime_exception_is_terminalized_as_durable_fail_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)

    def hostile_runtime(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise subprocess.SubprocessError("hostile catchable runtime failure")

    fsync_kinds: list[str] = []
    original_fsync = preflight.os.fsync

    def observed_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        fsync_kinds.append(
            "directory" if stat.S_ISDIR(metadata.st_mode) else "regular_file"
        )
        original_fsync(descriptor)

    monkeypatch.setattr(preflight, "_runtime_probe", hostile_runtime)
    monkeypatch.setattr(preflight.os, "fsync", observed_fsync)
    assert preflight.main(case["argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert "SubprocessError: hostile catchable runtime failure" in receipt["reason"]
    assert "regular_file" in fsync_kinds
    assert fsync_kinds.count("directory") >= 3


def test_receipt_setup_fsync_failure_after_mkdir_has_durable_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch, auto_prepare=False)
    runtime_called = False
    original_fsync = preflight.os.fsync
    failed_once = False

    def forbidden_runtime(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal runtime_called
        runtime_called = True
        raise AssertionError("runtime must not run after receipt setup failure")

    def fail_first_setup_fsync(descriptor: int) -> None:
        nonlocal failed_once
        if not failed_once and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed_once = True
            raise OSError("synthetic fsync failure after mkdir")
        original_fsync(descriptor)

    monkeypatch.setattr(preflight, "_runtime_probe", forbidden_runtime)
    monkeypatch.setattr(preflight.os, "fsync", fail_first_setup_fsync)
    assert preflight.main(case["prepare_argv"]) == 2
    receipt = _receipt(case)
    assert receipt["status"] == "FAIL_NO_GO"
    assert receipt["phase"] == "PREPARE"
    assert "OSError: synthetic fsync failure after mkdir" in receipt["reason"]
    assert runtime_called is False
    assert failed_once is True
    assert stat.S_IMODE(case["receipt_dir"].stat().st_mode) == 0o555
    receipt_path = case["receipt_dir"] / preflight.PREFLIGHT_FATAL_FAIL_NAME
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o444
    assert receipt_path.stat().st_nlink == 1


@pytest.mark.parametrize("attack", ["traversal", "extra", "symlink"])
def test_hostile_package_closure_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    root = case["package_root"]
    expected_manifest = case["manifest_sha"]
    if attack == "traversal":
        manifest_path = root / preflight.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text())
        manifest["artifacts"][0]["path"] = "../escape"
        manifest_path.chmod(0o644)
        expected_manifest = _write_json(manifest_path, manifest)
        manifest_path.chmod(0o444)
    elif attack == "extra":
        root.chmod(0o755)
        extra = root / "UNREVIEWED_EXTRA"
        extra.write_text("hostile extra file\n")
        extra.chmod(0o444)
        root.chmod(0o555)
    else:
        manifest = json.loads((root / preflight.MANIFEST_NAME).read_text())
        relative = Path(manifest["artifacts"][0]["path"])
        payload = root / relative
        payload.parent.chmod(0o755)
        payload.unlink()
        payload.symlink_to(case["go_path"])
        payload.parent.chmod(0o555)
    with pytest.raises(preflight.PreflightError):
        preflight._audit_package(
            root.resolve(),
            expected_manifest_sha256=expected_manifest,
            expected_index_sha256=case["index_sha"],
            expected_commit_sha256=case["commit_sha"],
            build_attempt_body=case["build_attempt_body_path"],
            expected_build_attempt_body_sha256=case["build_attempt_body_sha"],
            build_attempt_committed=case["build_attempt_committed_path"],
            expected_build_attempt_committed_sha256=case[
                "build_attempt_committed_sha"
            ],
        )


def test_packaged_native_script_executes_one_exact_isolated_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    _install_fake_sealed_native_runtime(case, monkeypatch)
    verified_python = preflight._runtime_executable(
        str(case["python"]), _sha256(case["python"])
    )
    try:
        result = REAL_RUN_NATIVE_TESTS(
            verified_python, case["package"], ["native_smoke_test"], 60
        )
    finally:
        verified_python.close()
    assert result["requested"] is True
    assert result["returncode"] == 0
    assert result["roles"] == ["native_smoke_test"]
    assert result["stdout_size_bytes"] > 0
    assert result["executed_test_count"] == 1
    assert result["exact_structured_pass"] is True
    assert result["isolated_python_flags"] == ["-I", "-B", "-S"]


def test_hostile_pytest_environment_cannot_false_pass_or_change_native_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    _install_fake_sealed_native_runtime(case, monkeypatch)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only -p definitely_unreviewed_plugin")
    monkeypatch.setenv("PYTEST_PLUGINS", "definitely_unreviewed_plugin")
    verified_python = preflight._runtime_executable(
        str(case["python"]), _sha256(case["python"])
    )
    try:
        result = REAL_RUN_NATIVE_TESTS(
            verified_python, case["package"], ["native_smoke_test"], 60
        )
    finally:
        verified_python.close()
    assert result["returncode"] == 0
    assert result["executed_test_count"] == 1
    assert result["exact_structured_pass"] is True
    assert "PYTEST_ADDOPTS" not in result["environment_keyset"]
    assert "PYTEST_PLUGINS" not in result["environment_keyset"]


@pytest.mark.parametrize("target_kind", ["manifest", "shared_contract"])
def test_native_protocol_consumes_verified_snapshots_during_transient_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    _install_fake_sealed_native_runtime(case, monkeypatch)
    target = (
        case["package"]["manifest_path"]
        if target_kind == "manifest"
        else Path(case["package"]["roles"]["shared_contract_code"]["absolute_path"])
    )
    sealed_run = preflight._run_verified_python

    def hostile_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return _replace_then_restore_during(
            target,
            b"MALICIOUS TRANSIENT REPLACEMENT MUST NOT BE CONSUMED\n",
            lambda: sealed_run(*args, **kwargs),
        )

    monkeypatch.setattr(preflight, "_run_verified_python", hostile_run)
    verified_python = preflight._runtime_executable(
        str(case["python"]), _sha256(case["python"])
    )
    try:
        result = REAL_RUN_NATIVE_TESTS(
            verified_python, case["package"], ["native_smoke_test"], 60
        )
    finally:
        verified_python.close()
    assert result["returncode"] == 0
    assert result["executed_test_count"] == 1
    assert result["exact_structured_pass"] is True


def test_runtime_python_linux_invocation_executes_held_verified_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python-copy"
    executable.write_bytes(Path(sys.executable).resolve().read_bytes())
    executable.chmod(0o755)
    verified = preflight._runtime_executable(str(executable.resolve()), _sha256(executable))
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        def consume_held_inode() -> subprocess.CompletedProcess[bytes]:
            captured["args"] = args
            captured.update(kwargs)
            held = preflight._read_descriptor(verified.descriptor)
            assert hashlib.sha256(held).hexdigest() == verified.sha256
            assert held != b"TRANSIENT MALICIOUS PYTHON\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")

        return _replace_then_restore_during(
            executable,
            b"TRANSIENT MALICIOUS PYTHON\n",
            consume_held_inode,
        )

    monkeypatch.setattr(preflight, "_linux_descriptor_execution_available", lambda: True)
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    try:
        preflight._run_verified_python(
            verified,
            ["-I", "-B", "-S", "-c", "pass"],
            pass_fds=(),
            capture_output=True,
        )
    finally:
        verified.close()
    assert captured["args"][0] == str(executable.resolve())
    assert captured["executable"] == f"/proc/self/fd/{verified.descriptor}"
    assert verified.descriptor in captured["pass_fds"]


def test_runtime_probe_reuses_the_one_descriptor_sealed_native_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    native_audit = {
        "runtime_identity": case["runtime"],
        "exact_structured_pass": True,
    }
    monkeypatch.setattr(
        preflight,
        "_run_native_tests",
        lambda *_args, **_kwargs: pytest.fail("runtime probe duplicated native smoke"),
    )
    verified_python = preflight._runtime_executable(
        str(case["python"]), _sha256(case["python"])
    )
    try:
        observed = REAL_RUNTIME_PROBE(
            verified_python, case["package"], native_audit
        )
    finally:
        verified_python.close()
    assert observed == case["runtime"]


def test_final_continuity_gate_rejects_unrestored_package_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _prepare_case(tmp_path, monkeypatch)
    target = Path(case["package"]["roles"]["shared_contract_code"]["absolute_path"])
    backup = tmp_path / "verified-shared-contract-backup"
    original_native = preflight._run_native_tests

    def replace_and_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_native(*args, **kwargs)
        target.parent.chmod(0o755)
        os.replace(target, backup)
        target.write_bytes(b"UNRESTORED MALICIOUS REPLACEMENT\n")
        target.chmod(0o444)
        target.parent.chmod(0o555)
        return result

    monkeypatch.setattr(preflight, "_run_native_tests", replace_and_run)
    try:
        assert preflight.main(case["argv"]) == 2
        receipt = _receipt(case)
        assert receipt["status"] == "FAIL_NO_GO"
        assert (
            "held inode changed after verification" in receipt["reason"]
            or "path no longer names the verified inode" in receipt["reason"]
        )
    finally:
        target.parent.chmod(0o755)
        if target.exists():
            target.unlink()
        if backup.exists():
            os.replace(backup, target)
        target.parent.chmod(0o555)


def _prepare_transaction_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, Any]]:
    receipt_dir = (tmp_path / "transaction_receipt").resolve()
    monkeypatch.setattr(
        preflight,
        "_compute_expected_base_go_bindings",
        lambda _args, _transaction: {"fixture_contract": "RESULT_BLIND"},
    )
    transaction = preflight._create_receipt_transaction(str(receipt_dir))
    try:
        result = preflight._prepare_phase(
            transaction, preflight.argparse.Namespace()
        )
    finally:
        transaction.close()
    return receipt_dir, result


def _fixture_process_singleton_evidence() -> dict[str, Any]:
    empty_audit = {
        "schema": "controlled_real10k_20k_preflight_process_audit_v2",
        "uid": os.geteuid(),
        "current_pid": os.getpid(),
        "substring_matching_used": False,
        "exact_argv_executable_and_descriptor_identity_required": True,
        "matches": [],
        "match_count": 0,
    }
    return {
        "contract": {"path": "/fixture/contract", "sha256": "a" * 64},
        "contract_payload": {
            "schema": "controlled_real10k_20k_process_singleton_contract_v1"
        },
        "lock": {"path": "/fixture/lock", "sha256": "b" * 64},
        "lock_operation": "LOCK_EX|LOCK_NB",
        "lock_held_for_full_execute_lifetime": True,
        "protected_entrypoints": [],
        "proc_audit_contract": {"substring_matching_allowed": False},
        "before": dict(empty_audit),
        "after": dict(empty_audit),
        "all_counts_zero": True,
        "current_uid_only": True,
    }


def test_prepare_qa_freezes_exact_external_go_keysets_and_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_dir, _prepared = _prepare_transaction_only(tmp_path, monkeypatch)
    qa = json.loads(
        (receipt_dir / preflight.EXECUTION_QA_REQUIRED_NAME).read_text(
            encoding="utf-8"
        )
    )
    required = qa["required_go"]
    assert required["binding_keys"] == list(
        preflight.PACKAGE_REQUIRED_GO_BINDING_KEYS
    )
    assert required["authorities"] == preflight.EXPECTED_AUTHORITIES
    assert required["review"] == {"independent": True, "result_blind": True}
    assert required["zero_findings"] == {"p0": 0, "p1": 0}
    assert required["recursive_exact_json_types"] is True
    assert required["bind_receipt_root_and_parent_inode"] is True
    assert required["bind_external_lease_inode_and_sha256"] is True


def test_two_phase_transaction_commits_only_after_consumed_external_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_dir, _prepared = _prepare_transaction_only(tmp_path, monkeypatch)
    transaction = preflight._open_execution_transaction(str(receipt_dir))
    try:
        consumed = preflight._consume_external_lease(transaction)
        go_path = (tmp_path / "fixture-go.json").resolve()
        go_path.write_text("{}\n", encoding="utf-8")
        committed = preflight._publish_committed_success(
            transaction,
            {
                "external_code_go": {
                    "path": str(go_path),
                    "sha256": _sha256(go_path),
                },
                "process_singleton": _fixture_process_singleton_evidence(),
            },
            started_utc="2026-08-24T12:00:00Z",
        )
    finally:
        transaction.close()
    assert committed.name == preflight.PREFLIGHT_COMMITTED_NAME
    assert set(path.name for path in receipt_dir.iterdir()) == set(
        preflight.SUCCESS_ROOT_FILES
    )
    marker = json.loads(committed.read_text())
    assert marker["status"] == "COMMITTED_PASS_PREFLIGHT_ONLY"
    assert marker["consumed_external_one_use_lease"] == consumed
    assert stat.S_IMODE(receipt_dir.stat().st_mode) == 0o555
    lease = json.loads(Path(consumed["path"]).read_text())
    assert lease["state"] == "CONSUMED"


def test_execute_rejects_same_path_replaced_receipt_root_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_dir, _prepared = _prepare_transaction_only(tmp_path, monkeypatch)
    backup = receipt_dir.parent / ".original-receipt-root"
    os.replace(receipt_dir, backup)
    import shutil

    shutil.copytree(backup, receipt_dir, copy_function=shutil.copy2)
    try:
        with pytest.raises(
            preflight._ReceiptTransactionValidationError,
            match="receipt-root binding",
        ):
            preflight._open_execution_transaction(str(receipt_dir))
    finally:
        shutil.rmtree(receipt_dir)
        os.replace(backup, receipt_dir)


def test_execute_rejects_same_bytes_replaced_external_lease_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_dir, prepared = _prepare_transaction_only(tmp_path, monkeypatch)
    lease = Path(prepared["lease"])
    raw = lease.read_bytes()
    lease.unlink()
    lease.write_bytes(raw)
    lease.chmod(0o600)
    with pytest.raises(
        preflight._ReceiptTransactionValidationError,
        match="external lease live binding",
    ):
        preflight._open_execution_transaction(str(receipt_dir))


def test_failure_lease_binding_state_matches_revoked_payload_and_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_dir, _prepared = _prepare_transaction_only(tmp_path, monkeypatch)
    transaction = preflight._open_execution_transaction(str(receipt_dir))
    try:
        binding = preflight._lease_abort_binding(transaction)
        assert binding is not None
        lease_payload = json.loads(transaction.lease_path.read_text(encoding="utf-8"))
        assert lease_payload["state"] == "REVOKED_"
        assert binding["state"] == lease_payload["state"]
        assert binding["sha256"] == _sha256(transaction.lease_path)
    finally:
        transaction.close()


def test_parent_fsync_failure_after_commit_marker_creates_immutable_failure_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_dir, _prepared = _prepare_transaction_only(tmp_path, monkeypatch)
    transaction = preflight._open_execution_transaction(str(receipt_dir))
    preflight._consume_external_lease(transaction)
    go_path = (tmp_path / "fixture-go.json").resolve()
    go_path.write_text("{}\n", encoding="utf-8")
    original_fsync = preflight.os.fsync
    failed = False

    def fail_parent_after_marker(descriptor: int) -> None:
        nonlocal failed
        if (
            not failed
            and descriptor == transaction.parent_fd
            and (receipt_dir / preflight.PREFLIGHT_COMMITTED_NAME).exists()
        ):
            failed = True
            raise OSError("synthetic parent durability failure after marker")
        original_fsync(descriptor)

    monkeypatch.setattr(preflight.os, "fsync", fail_parent_after_marker)
    try:
        with pytest.raises(OSError, match="synthetic parent durability failure") as caught:
            preflight._publish_committed_success(
                transaction,
                {
                    "external_code_go": {
                        "path": str(go_path),
                        "sha256": _sha256(go_path),
                    },
                    "process_singleton": _fixture_process_singleton_evidence(),
                },
                started_utc="2026-08-24T12:00:00Z",
            )
        failure = preflight._terminalize_failure(
            transaction,
            caught.value,
            phase="EXECUTE",
            started_utc="2026-08-24T12:00:00Z",
        )
    finally:
        transaction.close()
    payload = json.loads(failure.read_text())
    assert payload["commit_marker_present"] is True
    assert payload["failure_precedence_absolute"] is True
    assert (receipt_dir / preflight.PREFLIGHT_COMMITTED_NAME).exists()
    assert stat.S_IMODE(receipt_dir.stat().st_mode) == 0o555


@pytest.mark.parametrize("attack", ["bool_int_alias", "duplicate_key", "nonfinite"])
def test_code_go_strict_json_rejects_alias_duplicate_and_nonfinite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    monkeypatch.setattr(preflight, "_now_utc", lambda: NOW)
    go = {
        "schema": preflight.CODE_GO_SCHEMA,
        "status": "PASS",
        "verdict": "EXACT_CODE_GO",
        "scope": preflight.CODE_GO_SCOPE,
        "issued_utc": "2026-08-24T11:00:00Z",
        "expires_utc": "2026-08-24T13:00:00Z",
        "nonce": "0123456789abcdef0123456789abcdef",
        "review": {"independent": True, "result_blind": True},
        "findings": {"p0": 0, "p1": 0},
        "bindings": {"fixture": "exact"},
        "authorities": dict(preflight.EXPECTED_AUTHORITIES),
    }
    path = (tmp_path / f"strict-{attack}.json").resolve()
    if attack == "bool_int_alias":
        go["authorities"]["training_authorized"] = 0
        path.write_text(json.dumps(go, sort_keys=True) + "\n", encoding="utf-8")
    else:
        raw = json.dumps(go, sort_keys=True)
        if attack == "duplicate_key":
            raw = raw.replace('"status": "PASS"', '"status": "PASS", "status": "PASS"', 1)
        else:
            raw = raw.replace('"p0": 0', '"p0": NaN', 1)
        path.write_text(raw + "\n", encoding="utf-8")
    with pytest.raises(preflight.PreflightError):
        preflight._validate_code_go(path, _sha256(path), {"fixture": "exact"})


def test_static_boundary_has_no_spawn_or_signal_primitive_for_controlled_processes() -> None:
    source = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
    native_source = NATIVE_SMOKE_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                calls.append(f"{node.func.value.id}.{node.func.attr}")
            elif isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    assert "subprocess.Popen" not in calls
    assert "os.kill" not in calls
    assert "os.killpg" not in calls
    assert "kill" not in calls
    assert "killpg" not in calls
    assert "signal" not in {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
    assert '"pytest"' not in source
    assert "-m pytest" not in source
    assert "PYTEST_ADDOPTS" not in source
    assert "os.environ.copy" not in source
    assert "import pytest" not in native_source
    assert "RESULT_SCHEMA" in native_source
    assert 'if __name__ == "__main__"' in native_source
    legacy_substring_tokens = (
        "build_controlled_real10k_20k_nested.py",
        "run_controlled_real10k_20k_materialization.py",
        "run_controlled_real10k_20k_paired.py",
        "train_physical_feature_tandem_inverse.py",
    )
    process_source = inspect.getsource(preflight._scan_current_uid_processes)
    assert "argv[0] == str(expected_python_path)" in process_source
    assert '"substring_matching_used": False' in process_source
    assert 'child / "fd" / "200"' in process_source
    assert 'child / "fd" / "201"' in process_source
    assert 'child / "fd" / "202"' in process_source
    assert 'child / "fd" / "203"' in process_source
    for function in (preflight._runtime_probe, preflight._run_native_tests):
        function_source = inspect.getsource(function)
        assert all(token not in function_source for token in legacy_substring_tokens)
