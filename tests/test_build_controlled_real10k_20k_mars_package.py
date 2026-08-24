from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_controlled_real10k_20k_mars_package.py"
INERT_INIT = (
    ROOT
    / "rfic_transformer_inverse_design"
    / "controlled_real10k_20k_runtime_init.py"
)
SPEC = importlib.util.spec_from_file_location("controlled_package_builder_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _json_write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _thaw(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    directories = [item for item in path.rglob("*") if item.is_dir() and not item.is_symlink()]
    for directory in sorted(directories, key=lambda item: len(item.parts)):
        directory.chmod(0o755)
    if path.is_dir():
        path.chmod(0o755)


@pytest.fixture(autouse=True)
def _restore_tmp_permissions(tmp_path: Path):
    yield
    _thaw(tmp_path)


def _zip_payload(members: list[dict[str, object]], bodies: dict[str, bytes]) -> bytes:
    import io

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.comment = b""
        for member in members:
            name = str(member["path"])
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, bodies[name])
    return stream.getvalue()


@dataclass
class Case:
    tmp: Path
    sources: Path
    tree: Path
    closure_path: Path
    spec_path: Path
    spec: dict[str, object]
    members: list[dict[str, object]]
    member_bodies: dict[str, bytes]

    @property
    def builder_sha(self) -> str:
        return _sha(SCRIPT)

    @property
    def spec_sha(self) -> str:
        return _sha(self.spec_path)

    def rewrite_spec(self) -> None:
        _json_write(self.spec_path, self.spec)

    def rewrite_closure(self, closure: dict[str, object]) -> None:
        _json_write(self.closure_path, closure)
        roles = self.spec["roles"]
        assert isinstance(roles, dict)
        inventory = roles["runtime_dependency_closure_json"]
        tree_role = roles["runtime_dependency_closure_tree"]
        assert isinstance(inventory, dict) and isinstance(tree_role, dict)
        inventory["sha256"] = _sha(self.closure_path)
        tree_role["inventory_sha256"] = _sha(self.closure_path)
        self.rewrite_spec()


def _make_case(tmp_path: Path) -> Case:
    sources = tmp_path / "sources"
    sources.mkdir()
    roles: dict[str, dict[str, object]] = {}
    role_paths: dict[str, Path] = {}
    for index, role in enumerate(sorted(builder.FILE_ROLES)):
        if role == "package_builder_code":
            path = SCRIPT
        elif role == "runtime_package_init_code":
            path = INERT_INIT
        else:
            suffix = ".py" if role in builder.PYTHON_CODE_ROLES else ".json"
            if role.endswith("_csv"):
                suffix = ".csv"
            path = sources / f"{role}{suffix}"
            if suffix == ".py":
                path.write_text(f"ROLE = {role!r}\nINDEX = {index}\n", encoding="utf-8")
            elif suffix == ".csv":
                path.write_text(f"role,index\n{role},{index}\n", encoding="utf-8")
            elif role == "process_singleton_contract_json":
                _json_write(path, builder._expected_process_singleton_contract())
            else:
                _json_write(path, {"fixture_role": role, "index": index})
        path = path.resolve(strict=True)
        role_paths[role] = path
        roles[role] = {"kind": "file", "source_path": str(path), "sha256": _sha(path)}

    member_specs = [
        (
            "controlled_entrypoints/build_controlled_real10k_20k_nested.py",
            "materialization_builder_code",
            None,
            False,
        ),
        (
            "controlled_entrypoints/controlled_real10k_20k_mars_native_smoke.py",
            "native_smoke_test",
            None,
            False,
        ),
        (
            "controlled_entrypoints/run_controlled_real10k_20k_materialization.py",
            "materialization_gate_code",
            None,
            False,
        ),
        (
            "controlled_entrypoints/evaluate_controlled_real10k_20k_common.py",
            "evaluator_code",
            None,
            False,
        ),
        (
            "controlled_entrypoints/run_controlled_real10k_20k_paired.py",
            "runner_code",
            None,
            False,
        ),
        (
            "controlled_entrypoints/train_physical_feature_tandem_inverse.py",
            "trainer_code",
            None,
            False,
        ),
        (
            "rfic_transformer_inverse_design/__init__.py",
            "runtime_package_init_code",
            "rfic_transformer_inverse_design",
            True,
        ),
        (
            "rfic_transformer_inverse_design/controlled_real10k_20k_contract.py",
            "shared_contract_code",
            "rfic_transformer_inverse_design.controlled_real10k_20k_contract",
            False,
        ),
        (
            "rfic_transformer_inverse_design/controlled_real10k_20k_runtime_bootstrap.py",
            "runtime_bootstrap_code",
            "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap",
            False,
        ),
        (
            "rfic_transformer_inverse_design/model_splitting.py",
            "splitter_code",
            "rfic_transformer_inverse_design.model_splitting",
            False,
        ),
    ]
    closure_role = {
        "runtime_package_init_code": "package_init_code",
        "runtime_bootstrap_code": "runtime_bootstrap_code",
        "shared_contract_code": "shared_contract_code",
        "splitter_code": "splitter_code",
        "materialization_builder_code": "materialization_builder_code",
        "materialization_gate_code": "materialization_gate_code",
        "runner_code": "runner_code",
        "trainer_code": "trainer_code",
        "evaluator_code": "evaluator_code",
        "native_smoke_test": "native_smoke_test",
    }
    member_bodies: dict[str, bytes] = {}
    members: list[dict[str, object]] = []
    for path, source_role, module, is_package in member_specs:
        payload = role_paths[source_role].read_bytes()
        member_bodies[path] = payload
        members.append(
            {
                "path": path,
                "sha256": _sha_bytes(payload),
                "size_bytes": len(payload),
                "kind": "python_source",
                "module": module,
                "is_package": is_package,
                "role": closure_role[source_role],
            }
        )
    numpy_path = "numpy/__init__.py"
    numpy_payload = b'__version__ = "2.5.0"\n'
    member_bodies[numpy_path] = numpy_payload
    members.append(
        {
            "path": numpy_path,
            "sha256": _sha_bytes(numpy_payload),
            "size_bytes": len(numpy_payload),
            "kind": "python_source",
            "module": "numpy",
            "is_package": True,
            "role": "numpy_pure",
        }
    )
    members.sort(key=lambda item: str(item["path"]))

    tree = tmp_path / "runtime_tree"
    pure = tree / "pure" / "RUNTIME_PURE.zip"
    pure.parent.mkdir(parents=True)
    pure.write_bytes(_zip_payload(members, member_bodies))
    extension = (
        tree
        / "native"
        / "extensions"
        / "numpy._core._multiarray_umath"
        / "_multiarray_umath.fixture.so"
    )
    extension.parent.mkdir(parents=True)
    extension.write_bytes(b"fixture native extension bytes\n")
    library = tree / "native" / "libraries" / "libfixture.so" / "libfixture.so"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"fixture native library bytes\n")

    closure = {
        "schema": builder.RUNTIME_CLOSURE_SCHEMA,
        "bootstrap": {
            "module": "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap",
            "sha256": _sha(role_paths["runtime_bootstrap_code"]),
            "size_bytes": role_paths["runtime_bootstrap_code"].stat().st_size,
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.13",
            "abi_tag": "cpython-312-x86_64-linux-gnu",
            "platform": "linux-x86_64",
            "executable_sha256": "0" * 64,
        },
        "numpy": {"version": "2.5.0"},
        "pure_archive": {
            "path": "pure/RUNTIME_PURE.zip",
            "sha256": _sha(pure),
            "size_bytes": pure.stat().st_size,
            "format": "zip",
            "compression": "ZIP_STORED",
        },
        "members": members,
        "native_extensions": [
            {
                "module": "numpy._core._multiarray_umath",
                "path": "native/extensions/numpy._core._multiarray_umath/_multiarray_umath.fixture.so",
                "basename": "_multiarray_umath.fixture.so",
                "sha256": _sha(extension),
                "size_bytes": extension.stat().st_size,
                "init_symbol": "PyInit__multiarray_umath",
                "dt_needed": ["libfixture.so"],
            }
        ],
        "native_libraries": [
            {
                "soname": "libfixture.so",
                "path": "native/libraries/libfixture.so/libfixture.so",
                "basename": "libfixture.so",
                "sha256": _sha(library),
                "size_bytes": library.stat().st_size,
                "dt_needed": ["libc.so.6"],
                "load_order": 0,
            }
        ],
        "system_library_allowlist": list(builder.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
        "entrypoints": {
            "materialization": {
                "member": "controlled_entrypoints/run_controlled_real10k_20k_materialization.py",
                "sha256": _sha(role_paths["materialization_gate_code"]),
                "display_path": "runtime/project/scripts/run_controlled_real10k_20k_materialization.py",
                "role": "materialization_gate_code",
            },
            "runner": {
                "member": "controlled_entrypoints/run_controlled_real10k_20k_paired.py",
                "sha256": _sha(role_paths["runner_code"]),
                "display_path": "runtime/project/scripts/run_controlled_real10k_20k_paired.py",
                "role": "runner_code",
            },
            "trainer": {
                "member": "controlled_entrypoints/train_physical_feature_tandem_inverse.py",
                "sha256": _sha(role_paths["trainer_code"]),
                "display_path": "runtime/project/scripts/train_physical_feature_tandem_inverse.py",
                "role": "trainer_code",
            },
            "evaluator": {
                "member": "controlled_entrypoints/evaluate_controlled_real10k_20k_common.py",
                "sha256": _sha(role_paths["evaluator_code"]),
                "display_path": "runtime/project/scripts/evaluate_controlled_real10k_20k_common.py",
                "role": "evaluator_code",
            },
            "native_smoke": {
                "member": "controlled_entrypoints/controlled_real10k_20k_mars_native_smoke.py",
                "sha256": _sha(role_paths["native_smoke_test"]),
                "display_path": "runtime/project/tests/controlled_real10k_20k_mars_native_smoke.py",
                "role": "native_smoke_test",
            },
        },
    }
    closure_path = sources / "RUNTIME_CLOSURE.json"
    _json_write(closure_path, closure)
    roles["runtime_dependency_closure_json"] = {
        "kind": "file",
        "source_path": str(closure_path.resolve()),
        "sha256": _sha(closure_path),
    }
    roles["runtime_dependency_closure_tree"] = {
        "kind": "tree",
        "source_root": str(tree.resolve()),
        "inventory_path": str(closure_path.resolve()),
        "inventory_sha256": _sha(closure_path),
    }
    spec = {
        "schema": builder.BUILD_SPEC_SCHEMA,
        "package_version": builder.PACKAGE_VERSION,
        "roles": roles,
    }
    spec_path = tmp_path / "PACKAGE_BUILD_SPEC.json"
    _json_write(spec_path, spec)
    return Case(tmp_path, sources, tree, closure_path, spec_path, spec, members, member_bodies)


def _build(case: Case, name: str = "package", attempt: str = "attempt"):
    return builder.build_package(
        (case.tmp / name).resolve(),
        case.spec_path.resolve(),
        (case.tmp / attempt).resolve(),
        expected_package_spec_sha256=case.spec_sha,
        expected_builder_sha256=case.builder_sha,
        invocation_argv=[str(SCRIPT), "--fixture"],
    )


def _failed_attempt(root: Path) -> dict[str, Any]:
    return json.loads((root / builder.BUILD_ATTEMPT_FAILED_NAME).read_text())


def _ambiguous_attempt(root: Path) -> dict[str, Any]:
    return json.loads((root / builder.BUILD_ATTEMPT_AMBIGUOUS_NAME).read_text())


def _assert_committed_attempt(paths: Mapping[str, Path]) -> None:
    body_path = paths["build_attempt_receipt"]
    committed_path = paths["build_attempt_committed"]
    body = json.loads(body_path.read_text())
    committed = json.loads(committed_path.read_text())
    assert set(body) == {
        "schema",
        "status",
        "started_utc",
        "completed_utc",
        "invocation",
        "observed_identity",
        "package",
        "partial_output_preserved",
        "authorities",
        "execution_authorized",
    }
    assert body["schema"] == builder.BUILD_ATTEMPT_BODY_SCHEMA
    assert body["status"] == "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
    assert set(committed) == {
        "schema",
        "status",
        "committed_utc",
        "body",
        "package_commit",
        "package_root",
        "attempt_root",
        "attempt_parent",
        "publication",
        "authorities",
        "execution_authorized",
    }
    assert committed["schema"] == builder.BUILD_ATTEMPT_COMMIT_SCHEMA
    assert committed["status"] == "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED"
    assert committed["body"] == {
        "path": str(body_path),
        "sha256": _sha(body_path),
        "schema": builder.BUILD_ATTEMPT_BODY_SCHEMA,
        "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
    }
    package_commit_path = paths["package_commit"]
    package_commit = json.loads(package_commit_path.read_text())
    assert package_commit["schema"] == builder.PACKAGE_COMMIT_SCHEMA
    assert (
        package_commit["status"]
        == "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT"
    )
    assert committed["package_commit"] == {
        "path": str(package_commit_path),
        "sha256": _sha(package_commit_path),
        "schema": builder.PACKAGE_COMMIT_SCHEMA,
        "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
    }
    assert committed["publication"] == {
        "body_file_fsync": True,
        "attempt_root_fsync": True,
        "attempt_parent_fsync": True,
        "attempt_root_frozen": True,
        "continuity_verified": True,
        "terminal_inode_reserved_create_once_before_freeze": True,
        "terminal_bytes_published_after_durability": True,
        "post_commit_attempt_file_creation_permitted": False,
    }
    for key, path in (
        ("package_root", paths["package_root"]),
        ("attempt_root", body_path.parent),
        ("attempt_parent", body_path.parent.parent),
    ):
        metadata = path.stat()
        assert committed[key] == {
            "path": str(path),
            "st_dev": metadata.st_dev,
            "st_ino": metadata.st_ino,
            "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
        }
    assert stat.S_IMODE(body_path.parent.stat().st_mode) == 0o555
    assert stat.S_IMODE(body_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(committed_path.stat().st_mode) == 0o444
    assert {item.name for item in body_path.parent.iterdir()} == {
        builder.BUILD_ATTEMPT_RECEIPT_NAME,
        builder.BUILD_ATTEMPT_COMMITTED_NAME,
    }


def test_builds_exact_21_role_immutable_package_v5(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    paths = _build(case)
    package = paths["package_root"]
    manifest = json.loads(paths["manifest"].read_text())
    qa = json.loads(paths["independent_qa_required"].read_text())
    commit = json.loads(paths["package_commit"].read_text())
    attempt = json.loads(paths["build_attempt_receipt"].read_text())
    attempt_commit = json.loads(paths["build_attempt_committed"].read_text())

    assert manifest["schema"] == builder.SCHEMA
    assert manifest["package_version"] == "v5"
    assert qa["schema"] == builder.QA_REQUIRED_SCHEMA
    assert len(manifest["role_identity"]) == 21
    assert manifest["role_destinations"] == builder.ROLE_DESTINATIONS
    assert manifest["runtime"]["entrypoints"] == builder.RUNTIME_ENTRYPOINTS
    assert set(manifest["runtime"]["entrypoints"]) == {
        "preflight",
        "materialization",
        "runner",
        "trainer",
        "evaluator",
        "native_smoke",
    }
    assert set(manifest["authorities"].values()) == {False}
    assert manifest["runtime"]["dependency_closure"]["numpy"]["version"] == "2.5.0"
    assert qa["required_go_receipt"]["required_binding_keys"] == list(
        builder.REQUIRED_GO_BINDING_KEYS
    )
    assert qa["required_go_receipt"]["maximum_age_seconds"] == 21600
    assert qa["required_go_receipt"]["exact_binding_keyset_required"] is True
    assert set(commit) == {
        "schema",
        "status",
        "package_version",
        "manifest",
        "receipt",
        "independent_qa_required",
        "sha256sums",
        "required_external_pass_attempt",
        "creation_order_contract",
        "authorities",
        "execution_authorized",
    }
    assert commit["schema"] == builder.PACKAGE_COMMIT_SCHEMA
    assert commit["status"] == "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT"
    assert commit["creation_order_contract"]["this_member_created_last"] is True
    assert commit["required_external_pass_attempt"] == {
        "body": {
            "path": str(paths["build_attempt_receipt"]),
            "schema": builder.BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        },
        "committed": {
            "path": str(paths["build_attempt_committed"]),
            "schema": builder.BUILD_ATTEMPT_COMMIT_SCHEMA,
            "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
        },
    }
    assert attempt["status"] == "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
    assert attempt["schema"] == builder.BUILD_ATTEMPT_BODY_SCHEMA
    assert attempt_commit["schema"] == builder.BUILD_ATTEMPT_COMMIT_SCHEMA
    assert attempt_commit["status"] == "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED"
    assert attempt_commit["body"]["sha256"] == _sha(paths["build_attempt_receipt"])
    assert attempt_commit["package_commit"]["sha256"] == _sha(paths["package_commit"])
    assert set(attempt_commit["publication"].values()) == {True, False}
    assert attempt_commit["publication"]["post_commit_attempt_file_creation_permitted"] is False
    _assert_committed_attempt(paths)
    assert attempt["invocation"]["environment"]["raw_values_recorded"] is False
    assert "keys" in attempt["invocation"]["environment"]
    assert "key_value_map_sha256" in attempt["invocation"]["environment"]

    index_paths = {
        line.split("  ", 1)[1]
        for line in paths["sha_index"].read_text(encoding="ascii").splitlines()
    }
    observed_paths = {
        item.relative_to(package).as_posix() for item in package.rglob("*") if item.is_file()
    }
    assert builder.SHA_INDEX_NAME not in index_paths
    assert builder.COMMIT_NAME not in index_paths
    assert observed_paths == index_paths | {builder.SHA_INDEX_NAME, builder.COMMIT_NAME}
    assert stat.S_IMODE(package.stat().st_mode) == 0o555
    for item in package.rglob("*"):
        mode = stat.S_IMODE(item.lstat().st_mode)
        if item.is_dir():
            assert mode == 0o555
        else:
            assert stat.S_ISREG(item.lstat().st_mode)
            assert mode == 0o444
            assert item.stat().st_nlink == 1


def test_cli_runs_under_isolated_no_site_python(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    output = tmp_path / "cli_package"
    attempt = tmp_path / "cli_attempt"
    command = [
        sys.executable,
        "-I",
        "-B",
        "-S",
        str(SCRIPT),
        "--out-dir",
        str(output),
        "--failure-receipt-dir",
        str(attempt),
        "--package-spec",
        str(case.spec_path),
        "--expected-package-spec-sha256",
        case.spec_sha,
        "--expected-builder-sha256",
        case.builder_sha,
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    returned = json.loads(result.stdout)
    assert returned["package_root"] == str(output.resolve())
    receipt = json.loads((attempt / builder.BUILD_ATTEMPT_RECEIPT_NAME).read_text())
    assert receipt["invocation"]["python"]["flags"]["isolated"] == 1
    assert receipt["invocation"]["python"]["flags"]["no_site"] == 1


def test_cli_rejects_missing_exact_isolation_flags() -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-S", str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "requires exact -I -B -S isolation" in result.stderr


@pytest.mark.parametrize("mutation", ["missing", "extra", "destination"])
def test_spec_role_keyset_and_destinations_are_not_caller_controlled(
    tmp_path: Path, mutation: str
) -> None:
    case = _make_case(tmp_path)
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    if mutation == "missing":
        roles.pop("evaluator_code")
        match = "role set must be exact"
    elif mutation == "extra":
        roles["unreviewed_extra"] = dict(roles["evaluator_code"])
        match = "role set must be exact"
    else:
        role = roles["evaluator_code"]
        assert isinstance(role, dict)
        role["destination"] = "attacker/chosen.py"
        match = "keyset mismatch"
    case.rewrite_spec()
    with pytest.raises(builder.PackageError, match=match):
        _build(case)
    receipt = _failed_attempt(tmp_path / "attempt")
    assert receipt["status"] == "FAIL_NO_GO_PRESERVED"
    assert receipt["partial_output_preserved"] is False


@pytest.mark.parametrize("attack", ["duplicate_key", "nonfinite"])
def test_rejects_ambiguous_package_spec_json(tmp_path: Path, attack: str) -> None:
    case = _make_case(tmp_path)
    if attack == "duplicate_key":
        raw = case.spec_path.read_text(encoding="utf-8")
        needle = f'"schema": "{builder.BUILD_SPEC_SCHEMA}"'
        assert raw.count(needle) == 1
        case.spec_path.write_text(
            raw.replace(needle, f"{needle},\n  {needle}", 1), encoding="utf-8"
        )
        match = "duplicate JSON object name"
    else:
        case.spec["package_version"] = float("nan")
        case.rewrite_spec()
        match = "non-finite JSON constant"
    with pytest.raises(builder.PackageError, match=match):
        _build(case)


@pytest.mark.parametrize("field", ["pure_size", "member_size", "library_load_order"])
def test_rejects_json_bool_aliases_in_runtime_integer_fields(
    tmp_path: Path, field: str
) -> None:
    case = _make_case(tmp_path)
    closure = json.loads(case.closure_path.read_text())
    if field == "pure_size":
        closure["pure_archive"]["size_bytes"] = True
    elif field == "member_size":
        closure["members"][0]["size_bytes"] = True
    else:
        closure["native_libraries"][0]["load_order"] = False
    case.rewrite_closure(closure)
    with pytest.raises(builder.PackageError):
        _build(case)


def test_rejects_duplicate_runtime_closure_key_even_when_rebound(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    raw = case.closure_path.read_text(encoding="utf-8")
    needle = f'"schema": "{builder.RUNTIME_CLOSURE_SCHEMA}"'
    assert raw.count(needle) == 1
    case.closure_path.write_text(
        raw.replace(needle, f"{needle},\n  {needle}", 1), encoding="utf-8"
    )
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    inventory = roles["runtime_dependency_closure_json"]
    tree_role = roles["runtime_dependency_closure_tree"]
    assert isinstance(inventory, dict) and isinstance(tree_role, dict)
    inventory["sha256"] = _sha(case.closure_path)
    tree_role["inventory_sha256"] = _sha(case.closure_path)
    case.rewrite_spec()
    with pytest.raises(builder.PackageError, match="duplicate JSON object name"):
        _build(case)


@pytest.mark.parametrize("duplicate", ["path", "sha"])
def test_rejects_duplicate_file_source_path_or_sha(tmp_path: Path, duplicate: str) -> None:
    case = _make_case(tmp_path)
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    first = roles["evaluator_code"]
    second = roles["preflight_code"]
    assert isinstance(first, dict) and isinstance(second, dict)
    if duplicate == "path":
        second["source_path"] = first["source_path"]
        second["sha256"] = first["sha256"]
        match = "duplicate artifact source path"
    else:
        second_path = Path(str(second["source_path"]))
        second_path.write_bytes(Path(str(first["source_path"])).read_bytes())
        second["sha256"] = first["sha256"]
        match = "duplicate artifact SHA-256"
    case.rewrite_spec()
    with pytest.raises(builder.PackageError, match=match):
        _build(case)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_rejects_file_role_symlink_or_hardlink_source(tmp_path: Path, kind: str) -> None:
    case = _make_case(tmp_path)
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    record = roles["evaluator_code"]
    assert isinstance(record, dict)
    original = Path(str(record["source_path"]))
    if kind == "symlink":
        attack = case.sources / "evaluator_link.py"
        attack.symlink_to(original)
        record["source_path"] = str(attack.absolute())
        match = "symlink"
    else:
        attack = case.sources / "evaluator_hardlink.py"
        os.link(original, attack)
        match = "nlink=1"
    case.rewrite_spec()
    with pytest.raises(builder.PackageError, match=match):
        _build(case)


def test_process_singleton_contract_content_is_exact_not_filename_inferred(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path)
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    record = roles["process_singleton_contract_json"]
    assert isinstance(record, dict)
    contract_path = Path(str(record["source_path"]))
    contract = json.loads(contract_path.read_text())
    contract["proc_audit"]["substring_matching_allowed"] = True
    _json_write(contract_path, contract)
    record["sha256"] = _sha(contract_path)
    case.rewrite_spec()
    with pytest.raises(builder.PackageError, match="singleton contract does not exactly match"):
        _build(case)


@pytest.mark.parametrize(
    "relative",
    ["extra.pyc", "bad.pth", "sitecustomize.py", "usercustomize.py", "__pycache__/x.py"],
)
def test_rejects_forbidden_runtime_tree_members(tmp_path: Path, relative: str) -> None:
    case = _make_case(tmp_path)
    extra = case.tree / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"forbidden\n")
    with pytest.raises(builder.PackageError, match="forbidden"):
        _build(case)


def test_rejects_unindexed_regular_runtime_tree_extra(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    (case.tree / "unindexed_regular.bin").write_bytes(b"extra\n")
    with pytest.raises(builder.PackageError, match="tree file set mismatch"):
        _build(case)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "empty_directory"])
def test_rejects_runtime_tree_links_specials_and_extra_directories(
    tmp_path: Path, kind: str
) -> None:
    case = _make_case(tmp_path)
    target = case.tree / "pure" / "RUNTIME_PURE.zip"
    if kind == "symlink":
        (case.tree / "runtime_link").symlink_to(target)
        match = "symlink"
    elif kind == "hardlink":
        os.link(target, case.tree / "hard_link")
        match = "hard-linked"
    elif kind == "fifo":
        os.mkfifo(case.tree / "special_fifo")
        match = "special file"
    else:
        (case.tree / "unindexed_empty_directory").mkdir()
        match = "directory set mismatch"
    with pytest.raises(builder.PackageError, match=match):
        _build(case)


def test_rejects_non_deterministic_zip_metadata_even_when_hash_is_rebound(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path)
    pure = case.tree / "pure" / "RUNTIME_PURE.zip"
    import io

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in case.members:
            archive.writestr(str(member["path"]), case.member_bodies[str(member["path"])])
    pure.write_bytes(stream.getvalue())
    closure = json.loads(case.closure_path.read_text())
    closure["pure_archive"]["sha256"] = _sha(pure)
    closure["pure_archive"]["size_bytes"] = pure.stat().st_size
    case.rewrite_closure(closure)
    with pytest.raises(builder.PackageError, match="ZIP"):
        _build(case)


def test_rejects_nonconsecutive_native_library_load_order(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    closure = json.loads(case.closure_path.read_text())
    closure["native_libraries"][0]["load_order"] = 1
    case.rewrite_closure(closure)
    with pytest.raises(builder.PackageError, match="consecutive from zero"):
        _build(case)


def test_rejects_caller_expanded_system_library_allowlist(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    closure = json.loads(case.closure_path.read_text())
    closure["system_library_allowlist"].append("libattacker.so")
    closure["system_library_allowlist"].sort()
    case.rewrite_closure(closure)
    with pytest.raises(builder.PackageError, match="frozen host boundary"):
        _build(case)


def test_rejects_nonzero_runtime_initializer_even_when_fully_rebound(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    replacement = case.sources / "nonzero_init.py"
    replacement.write_bytes(b"# not inert\n")
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    init_role = roles["runtime_package_init_code"]
    assert isinstance(init_role, dict)
    init_role["source_path"] = str(replacement.resolve())
    init_role["sha256"] = _sha(replacement)
    member_path = "rfic_transformer_inverse_design/__init__.py"
    case.member_bodies[member_path] = replacement.read_bytes()
    for member in case.members:
        if member["path"] == member_path:
            member["sha256"] = _sha(replacement)
            member["size_bytes"] = replacement.stat().st_size
    pure = case.tree / "pure" / "RUNTIME_PURE.zip"
    pure.write_bytes(_zip_payload(case.members, case.member_bodies))
    closure = json.loads(case.closure_path.read_text())
    closure["members"] = case.members
    closure["pure_archive"]["sha256"] = _sha(pure)
    closure["pure_archive"]["size_bytes"] = pure.stat().st_size
    case.rewrite_closure(closure)
    with pytest.raises(builder.PackageError, match="zero bytes"):
        _build(case)


def test_no_clobber_output_preserves_external_failure_receipt(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    _build(case, attempt="first_attempt")
    with pytest.raises(FileExistsError, match="no-clobber"):
        _build(case, attempt="second_attempt")
    second = _failed_attempt(tmp_path / "second_attempt")
    assert second["status"] == "FAIL_NO_GO_PRESERVED"
    assert second["partial_output_preserved"] is False
    assert second["failure"]["type"] == "FileExistsError"


def test_mid_copy_failure_preserves_partial_output(tmp_path: Path, monkeypatch) -> None:
    case = _make_case(tmp_path)

    def fail_after_one(output_descriptor, held_roles, tree_walk):
        builder._write_file_at(output_descriptor, builder.PurePosixPath("partial.txt"), b"one")
        raise RuntimeError("injected mid-copy failure")

    monkeypatch.setattr(builder, "_copy_payload", fail_after_one)
    with pytest.raises(RuntimeError, match="injected mid-copy failure"):
        _build(case)
    receipt = _failed_attempt(tmp_path / "attempt")
    assert receipt["status"] == "FAIL_NO_GO_PRESERVED"
    assert receipt["partial_output_preserved"] is True
    regular = [
        item for item in receipt["partial_output"]["entries"] if item["type"] == "regular"
    ]
    assert [item["path"] for item in regular] == ["partial.txt"]
    assert (tmp_path / "package" / "partial.txt").read_bytes() == b"one"


def test_failure_after_commit_never_emits_pass_receipt(tmp_path: Path, monkeypatch) -> None:
    case = _make_case(tmp_path)

    def fail_barrier(output_parent, output_root):
        raise OSError("injected final durability failure")

    monkeypatch.setattr(builder, "_durable_package_barrier", fail_barrier)
    with pytest.raises(OSError, match="durability"):
        _build(case)
    receipt = _failed_attempt(tmp_path / "attempt")
    assert receipt["status"] == "FAIL_NO_GO_PRESERVED"
    assert receipt["partial_output_preserved"] is True
    assert (tmp_path / "package" / builder.COMMIT_NAME).is_file()
    assert not any(
        item.get("status") == "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
        for item in [receipt]
    )


def _same_open_directory(descriptor: int, path: Path) -> bool:
    try:
        opened = os.fstat(descriptor)
        current = path.stat()
    except (FileNotFoundError, OSError):
        return False
    return (
        stat.S_ISDIR(opened.st_mode)
        and (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
    )


@pytest.mark.parametrize(
    "fault",
    [
        "attempt_root_body_fsync",
        "attempt_root_fchmod",
        "attempt_root_frozen_fsync",
        "attempt_parent_fsync",
        "attempt_root_continuity",
        "attempt_parent_continuity",
    ],
)
def test_six_late_attempt_faults_leave_no_acceptable_pass_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    case = _make_case(tmp_path)
    attempt_root = tmp_path / "attempt"
    body_path = attempt_root / builder.BUILD_ATTEMPT_RECEIPT_NAME
    triggered = False

    def fail_once() -> None:
        nonlocal triggered
        triggered = True
        raise OSError(f"late-{fault}")

    if "fsync" in fault:
        original_fsync = builder.os.fsync

        def hostile_fsync(descriptor: int) -> None:
            if not triggered and body_path.exists():
                if fault == "attempt_parent_fsync" and _same_open_directory(
                    descriptor, tmp_path
                ):
                    fail_once()
                if _same_open_directory(descriptor, attempt_root):
                    mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
                    if fault == "attempt_root_body_fsync" and mode == 0o755:
                        fail_once()
                    if fault == "attempt_root_frozen_fsync" and mode == 0o555:
                        fail_once()
            original_fsync(descriptor)

        monkeypatch.setattr(builder.os, "fsync", hostile_fsync)
    elif fault == "attempt_root_fchmod":
        original_fchmod = builder.os.fchmod

        def hostile_fchmod(descriptor: int, mode: int) -> None:
            if (
                not triggered
                and body_path.exists()
                and mode == 0o555
                and _same_open_directory(descriptor, attempt_root)
            ):
                fail_once()
            original_fchmod(descriptor, mode)

        monkeypatch.setattr(builder.os, "fchmod", hostile_fchmod)
    else:
        original_continuity = builder.HeldDirectory.assert_continuity

        def hostile_continuity(directory) -> None:
            if not triggered and body_path.exists():
                if fault == "attempt_root_continuity" and directory.path == attempt_root:
                    fail_once()
                if fault == "attempt_parent_continuity" and directory.path == tmp_path:
                    if stat.S_IMODE(attempt_root.stat().st_mode) == 0o555:
                        fail_once()
            original_continuity(directory)

        monkeypatch.setattr(
            builder.HeldDirectory, "assert_continuity", hostile_continuity
        )

    with pytest.raises(OSError, match=f"late-{fault}"):
        _build(case)
    assert triggered is True
    body = json.loads(body_path.read_text())
    assert body["schema"] == builder.BUILD_ATTEMPT_BODY_SCHEMA
    assert body["status"] == "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
    committed = attempt_root / builder.BUILD_ATTEMPT_COMMITTED_NAME
    assert committed.is_file()
    assert committed.stat().st_size == 0
    ambiguous = _ambiguous_attempt(attempt_root)
    assert ambiguous["schema"] == builder.BUILD_ATTEMPT_AMBIGUOUS_SCHEMA
    assert ambiguous["status"] == "AMBIGUOUS_NO_GO_PASS_BODY_NOT_COMMITTED"
    assert not (attempt_root / builder.BUILD_ATTEMPT_FAILED_NAME).exists()
    assert stat.S_IMODE(attempt_root.stat().st_mode) == 0o555


def test_body_only_is_no_go_and_original_error_is_not_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _make_case(tmp_path)

    def fail_before_terminal_bytes(_descriptor: int, _payload: bytes):
        raise RuntimeError("injected body-only terminal failure")

    monkeypatch.setattr(
        builder, "_publish_reserved_attempt_terminal", fail_before_terminal_bytes
    )
    with pytest.raises(RuntimeError, match="injected body-only terminal failure"):
        _build(case)
    attempt_root = tmp_path / "attempt"
    body = json.loads(
        (attempt_root / builder.BUILD_ATTEMPT_RECEIPT_NAME).read_text()
    )
    assert body["status"] == "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
    assert (attempt_root / builder.BUILD_ATTEMPT_COMMITTED_NAME).stat().st_size == 0
    assert _ambiguous_attempt(attempt_root)["status"] == (
        "AMBIGUOUS_NO_GO_PASS_BODY_NOT_COMMITTED"
    )


@pytest.mark.parametrize("mutation", ["missing", "corrupt"])
def test_committed_marker_missing_or_corrupt_is_rejected(
    tmp_path: Path, mutation: str
) -> None:
    case = _make_case(tmp_path)
    paths = _build(case)
    _assert_committed_attempt(paths)
    attempt_root = paths["build_attempt_receipt"].parent
    attempt_root.chmod(0o755)
    marker = paths["build_attempt_committed"]
    if mutation == "missing":
        marker.unlink()
    else:
        marker.chmod(0o600)
        marker.write_bytes(b"{}\n")
        marker.chmod(0o444)
    attempt_root.chmod(0o555)
    with pytest.raises((AssertionError, FileNotFoundError, json.JSONDecodeError)):
        _assert_committed_attempt(paths)


@pytest.mark.parametrize("replacement", ["attempt_root_copy", "attempt_parent"])
def test_attempt_root_or_parent_replacement_copy_is_rejected(
    tmp_path: Path, replacement: str
) -> None:
    case = _make_case(tmp_path)
    paths = _build(case)
    _assert_committed_attempt(paths)
    attempt_root = paths["build_attempt_receipt"].parent
    if replacement == "attempt_root_copy":
        original = tmp_path / "original_attempt"
        attempt_root.rename(original)
        shutil.copytree(original, attempt_root, copy_function=shutil.copy2)
    else:
        original_parent = tmp_path.with_name(tmp_path.name + "_original_parent")
        tmp_path.rename(original_parent)
        shutil.copytree(original_parent, tmp_path, copy_function=shutil.copy2)
    try:
        with pytest.raises(AssertionError):
            _assert_committed_attempt(paths)
    finally:
        if replacement == "attempt_parent":
            for directory, _subdirectories, files in os.walk(
                original_parent, topdown=False
            ):
                for name in files:
                    (Path(directory) / name).chmod(0o600)
                Path(directory).chmod(0o700)
            shutil.rmtree(original_parent)


@pytest.mark.parametrize(
    ("field", "stale"),
    [
        ("schema", "controlled_real10k_20k_mars_package_commit_v1"),
        ("status", "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_RECEIPT"),
    ],
)
def test_stale_package_commit_schema_or_status_is_rejected(
    tmp_path: Path, field: str, stale: str
) -> None:
    case = _make_case(tmp_path)
    paths = _build(case)
    package_root = paths["package_root"]
    commit_path = paths["package_commit"]
    package_root.chmod(0o755)
    commit_path.chmod(0o600)
    commit = json.loads(commit_path.read_text())
    commit[field] = stale
    _json_write(commit_path, commit)
    commit_path.chmod(0o444)
    package_root.chmod(0o555)
    with pytest.raises(AssertionError):
        _assert_committed_attempt(paths)


def test_stale_v4_build_spec_is_rejected(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    case.spec["package_version"] = "v4"
    case.rewrite_spec()
    with pytest.raises(builder.PackageError, match="schema/version"):
        _build(case)
    assert _failed_attempt(tmp_path / "attempt")["status"] == "FAIL_NO_GO_PRESERVED"


def test_detects_source_mutation_after_copy(tmp_path: Path, monkeypatch) -> None:
    case = _make_case(tmp_path)
    original = builder._copy_payload
    roles = case.spec["roles"]
    assert isinstance(roles, dict)
    record = roles["evaluator_code"]
    assert isinstance(record, dict)
    victim = Path(str(record["source_path"]))

    def mutate_after_copy(output_descriptor, held_roles, tree_walk):
        result = original(output_descriptor, held_roles, tree_walk)
        victim.write_bytes(victim.read_bytes() + b"# changed\n")
        return result

    monkeypatch.setattr(builder, "_copy_payload", mutate_after_copy)
    with pytest.raises(builder.PackageError, match="held inode changed|pathname identity changed"):
        _build(case)
    receipt = _failed_attempt(tmp_path / "attempt")
    assert receipt["status"] == "FAIL_NO_GO_PRESERVED"
