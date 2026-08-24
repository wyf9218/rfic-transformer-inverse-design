from __future__ import annotations

import hashlib
import importlib.machinery
import io
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import venv
import zipfile
from pathlib import Path
from typing import Any

import pytest

from rfic_transformer_inverse_design import (
    controlled_real10k_20k_runtime_bootstrap as bootstrap,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _python_record(executable: Path) -> dict[str, str]:
    source = (
        "import json,platform,sysconfig;"
        "print(json.dumps({'implementation':platform.python_implementation(),"
        "'version':platform.python_version(),'abi_tag':sysconfig.get_config_var('SOABI'),"
        "'platform':sysconfig.get_platform()}))"
    )
    observed = json.loads(
        subprocess.check_output(
            [str(executable), "-I", "-B", "-S", "-c", source], text=True
        )
    )
    return {**observed, "executable_sha256": _sha(executable.read_bytes())}


def _zip_bytes(members: list[dict[str, Any]], payloads: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = b""
        for record in members:
            info = zipfile.ZipInfo(record["path"], bootstrap.ZIP_TIMESTAMP)
            info.create_system = bootstrap.ZIP_CREATE_SYSTEM
            info.create_version = bootstrap.ZIP_VERSION
            info.extract_version = bootstrap.ZIP_VERSION
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = bootstrap.ZIP_EXTERNAL_ATTR
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, payloads[record["path"]])
    return buffer.getvalue()


def _fixture(
    root: Path,
    *,
    python: Path | None = None,
    runner_source: bytes | None = None,
) -> dict[str, Any]:
    executable = (python or Path(sys.executable)).resolve(strict=True)
    bootstrap_path = Path(bootstrap.__file__).resolve(strict=True)
    bootstrap_payload = bootstrap_path.read_bytes()
    runner_source = runner_source or b"import numpy\nprint('SEALED_OK')\n"
    sources: dict[str, tuple[bytes, str, str | None, bool]] = {
        "rfic_transformer_inverse_design/__init__.py": (
            b"", "package_init_code", "rfic_transformer_inverse_design", True
        ),
        "rfic_transformer_inverse_design/controlled_real10k_20k_runtime_bootstrap.py": (
            bootstrap_payload,
            "runtime_bootstrap_code",
            bootstrap.BOOTSTRAP_MODULE,
            False,
        ),
        "rfic_transformer_inverse_design/controlled_real10k_20k_contract.py": (
            b"VALUE = 1\n",
            "shared_contract_code",
            "rfic_transformer_inverse_design.controlled_real10k_20k_contract",
            False,
        ),
        "rfic_transformer_inverse_design/model_splitting.py": (
            b"VALUE = 2\n",
            "splitter_code",
            "rfic_transformer_inverse_design.model_splitting",
            False,
        ),
        "numpy/__init__.py": (b"__version__ = 'fixture-1'\n", "numpy_pure", "numpy", True),
        "controlled_entrypoints/build_controlled_real10k_20k_nested.py": (
            b"VALUE = 'builder'\n", "materialization_builder_code", None, False
        ),
        "controlled_entrypoints/run_controlled_real10k_20k_materialization.py": (
            b"VALUE = 'materialization'\n", "materialization_gate_code", None, False
        ),
        "controlled_entrypoints/run_controlled_real10k_20k_paired.py": (
            runner_source, "runner_code", None, False
        ),
        "controlled_entrypoints/train_physical_feature_tandem_inverse.py": (
            b"VALUE = 'trainer'\n", "trainer_code", None, False
        ),
        "controlled_entrypoints/evaluate_controlled_real10k_20k_common.py": (
            b"VALUE = 'evaluator'\n", "evaluator_code", None, False
        ),
        "controlled_entrypoints/controlled_real10k_20k_mars_native_smoke.py": (
            b"VALUE = 'smoke'\n", "native_smoke_test", None, False
        ),
    }
    members = [
        {
            "path": path,
            "sha256": _sha(payload),
            "size_bytes": len(payload),
            "kind": "python_source",
            "module": module,
            "is_package": is_package,
            "role": role,
        }
        for path, (payload, role, module, is_package) in sorted(sources.items())
    ]
    payloads = {path: value[0] for path, value in sources.items()}
    archive_payload = _zip_bytes(members, payloads)
    tree = root / "dependencies"
    archive_path = tree / bootstrap.PURE_ARCHIVE_PATH
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(archive_payload)
    entrypoint_members = {
        "materialization": "controlled_entrypoints/run_controlled_real10k_20k_materialization.py",
        "runner": "controlled_entrypoints/run_controlled_real10k_20k_paired.py",
        "trainer": "controlled_entrypoints/train_physical_feature_tandem_inverse.py",
        "evaluator": "controlled_entrypoints/evaluate_controlled_real10k_20k_common.py",
        "native_smoke": "controlled_entrypoints/controlled_real10k_20k_mars_native_smoke.py",
    }
    by_path = {record["path"]: record for record in members}
    manifest = {
        "schema": bootstrap.RUNTIME_CLOSURE_SCHEMA,
        "bootstrap": {
            "module": bootstrap.BOOTSTRAP_MODULE,
            "sha256": _sha(bootstrap_payload),
            "size_bytes": len(bootstrap_payload),
        },
        "python": _python_record(executable),
        "numpy": {"version": "fixture-1"},
        "pure_archive": {
            "path": bootstrap.PURE_ARCHIVE_PATH,
            "sha256": _sha(archive_payload),
            "size_bytes": len(archive_payload),
            "format": "zip",
            "compression": "ZIP_STORED",
        },
        "members": members,
        "native_extensions": [],
        "native_libraries": [],
        "system_library_allowlist": list(bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
        "entrypoints": {
            name: {
                "member": member,
                "sha256": by_path[member]["sha256"],
                "display_path": (
                    "runtime/project/tests/entry.py"
                    if name == "native_smoke"
                    else f"runtime/project/scripts/{name}.py"
                ),
                "role": bootstrap.ENTRYPOINT_ROLES[name],
            }
            for name, member in entrypoint_members.items()
        },
    }
    manifest_path = root / "RUNTIME_CLOSURE.json"
    manifest_payload = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("ascii")
    manifest_path.write_bytes(manifest_payload)
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha": _sha(manifest_payload),
        "tree": tree,
        "bootstrap_path": bootstrap_path,
        "bootstrap_sha": _sha(bootstrap_payload),
        "python": executable,
    }


def _audit(fixture: dict[str, Any]) -> dict[str, Any]:
    return bootstrap.audit_runtime_closure_paths(
        fixture["manifest_path"],
        fixture["manifest_sha"],
        fixture["tree"],
        fixture["bootstrap_path"],
        fixture["bootstrap_sha"],
    )


def test_exact_manifest_archive_and_path_closure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    identity = _audit(fixture)
    assert identity["zero_path_fallback"] is True
    assert identity["system_library_allowlist"] == list(
        bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
    )
    assert identity["role_bindings"]["runner_code"]["sha256"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "archive_swap", "manifest_corrupt"])
def test_path_closure_rejects_missing_extra_corrupt_and_swap(
    tmp_path: Path, mutation: str
) -> None:
    fixture = _fixture(tmp_path)
    archive_path = fixture["tree"] / bootstrap.PURE_ARCHIVE_PATH
    if mutation == "missing":
        archive_path.unlink()
    elif mutation == "extra":
        (fixture["tree"] / "unexpected.bin").write_bytes(b"extra")
    elif mutation == "archive_swap":
        archive_path.write_bytes(archive_path.read_bytes() + b"swap")
    else:
        fixture["manifest_path"].write_bytes(
            fixture["manifest_path"].read_bytes().replace(b'"schema"', b'"schema_bad"', 1)
        )
    with pytest.raises(bootstrap.RuntimeClosureError):
        _audit(fixture)


def test_manifest_rejects_arbitrary_system_allowlist_and_bool_integer_alias(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    manifest = fixture["manifest"]
    manifest["system_library_allowlist"] = ["libanything.so"]
    with pytest.raises(bootstrap.RuntimeClosureError, match="trusted-host"):
        bootstrap.validate_runtime_manifest(manifest)
    manifest["system_library_allowlist"] = list(
        bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
    )
    manifest["pure_archive"]["size_bytes"] = True
    with pytest.raises(bootstrap.RuntimeClosureError, match="exact integer"):
        bootstrap.validate_runtime_manifest(manifest)


def test_manifest_and_zip_reject_site_initialization_payload(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest = fixture["manifest"]
    poison = {
        "path": "numpy/poison.pth",
        "sha256": _sha(b"import poison\n"),
        "size_bytes": len(b"import poison\n"),
        "kind": "data",
        "module": None,
        "is_package": False,
        "role": "numpy_pure",
    }
    manifest["members"] = sorted([*manifest["members"], poison], key=lambda item: item["path"])
    with pytest.raises(bootstrap.RuntimeClosureError, match="site/bytecode"):
        bootstrap.validate_runtime_manifest(manifest)


def test_native_extension_loader_protocol_uses_only_proc_descriptor() -> None:
    manifest = {
        "members": [],
        "native_extensions": [
            {
                "module": "numpy._core._fixture",
                "path": "native/extensions/numpy._core._fixture/_fixture.so",
                "basename": "_fixture.so",
                "sha256": "0" * 64,
                "size_bytes": 1,
                "init_symbol": "PyInit__fixture",
                "dt_needed": [],
            }
        ],
    }
    finder = bootstrap._DescriptorClosureFinder(
        310, manifest, {"numpy._core._fixture": 311}
    )
    spec = finder.find_spec("numpy._core._fixture")
    assert spec is not None
    assert isinstance(spec.loader, importlib.machinery.ExtensionFileLoader)
    assert spec.origin == "/proc/self/fd/311"
    with pytest.raises(ModuleNotFoundError):
        bootstrap._ZeroFallbackFinder().find_spec(
            "rfic_transformer_inverse_design.hostile"
        )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="exact memfd/ELF launch is MARS-native Linux QA")
def test_linux_venv_site_poison_hostile_package_and_terminal_attestation(
    tmp_path: Path,
) -> None:
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "bin" / "python"
    site = next((environment / "lib").glob("python*/site-packages"))
    marker = tmp_path / "site_poison_ran"
    poison_source = f"from pathlib import Path; Path({str(marker)!r}).write_text('poison')\n"
    (site / "sitecustomize.py").write_text(poison_source, encoding="utf-8")
    (site / "poison.pth").write_text("import sitecustomize\n", encoding="utf-8")
    subprocess.run([str(python), "-I", "-B", "-c", "pass"], check=True)
    assert marker.is_file(), "control must prove the disposable venv poison is live"
    marker.unlink()
    hostile = tmp_path / "hostile"
    hostile_package = hostile / "rfic_transformer_inverse_design"
    hostile_package.mkdir(parents=True)
    (hostile_package / "hostile.py").write_text("raise AssertionError('fallback')\n")
    runner = (
        "import json, sys, numpy\n"
        "from rfic_transformer_inverse_design import controlled_real10k_20k_runtime_bootstrap as rb\n"
        "assert __package__ == 'rfic_transformer_inverse_design'\n"
        "state = rb.require_active_runtime('runner', sys.argv[1])\n"
        "source, origin = rb.active_member_source('runner_code', sys.argv[2])\n"
        "assert source and origin.startswith('descriptor-zip:/proc/self/fd/')\n"
        "try:\n import rfic_transformer_inverse_design.hostile\n"
        "except ModuleNotFoundError:\n pass\n"
        "else:\n raise AssertionError('external fallback')\n"
        "print(json.dumps({'numpy': numpy.__version__, 'sealed': True}, sort_keys=True))\n"
    ).encode("utf-8")
    fixture = _fixture(tmp_path / "closure", python=python, runner_source=runner)
    attestation = tmp_path / "attestation.jsonl"
    descriptor = os.open(attestation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        launch = bootstrap.prepare_sealed_runtime_launch(
            manifest_path=fixture["manifest_path"],
            expected_manifest_sha256=fixture["manifest_sha"],
            tree_root=fixture["tree"],
            bootstrap_path=fixture["bootstrap_path"],
            expected_bootstrap_sha256=fixture["bootstrap_sha"],
            entrypoint="runner",
            entrypoint_argv=[
                str(tmp_path / "project" / "runner.py"),
                fixture["manifest_sha"],
                fixture["manifest"]["entrypoints"]["runner"]["sha256"],
            ],
            attestation_output_fd=descriptor,
        )
        with launch:
            result = subprocess.run(
                [str(python), "-I", "-B", "-S", *launch.process_argv_suffix],
                cwd=tmp_path,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(hostile)},
                pass_fds=launch.pass_fds,
                text=True,
                capture_output=True,
                check=False,
            )
    finally:
        os.close(descriptor)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"numpy": "fixture-1", "sealed": True}
    assert not marker.exists()
    records = [json.loads(line) for line in attestation.read_text().splitlines()]
    assert [record["status"] for record in records] == [
        "PASS_DESCRIPTOR_CLOSED_STARTUP",
        "PASS_DESCRIPTOR_CLOSED_TERMINAL",
    ]
    assert all(record["external_package_fallback_allowed"] is False for record in records)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SystemExit attestation needs Linux sealed descriptors")
def test_linux_system_exit_records_terminal_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "closure", runner_source=b"raise SystemExit(7)\n")
    attestation = tmp_path / "attestation.jsonl"
    descriptor = os.open(attestation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        launch = bootstrap.prepare_sealed_runtime_launch(
            manifest_path=fixture["manifest_path"],
            expected_manifest_sha256=fixture["manifest_sha"],
            tree_root=fixture["tree"],
            bootstrap_path=fixture["bootstrap_path"],
            expected_bootstrap_sha256=fixture["bootstrap_sha"],
            entrypoint="runner",
            entrypoint_argv=[str(tmp_path / "runner.py")],
            attestation_output_fd=descriptor,
        )
        with launch:
            result = subprocess.run(
                [str(fixture["python"]), "-I", "-B", "-S", *launch.process_argv_suffix],
                pass_fds=launch.pass_fds,
                capture_output=True,
                check=False,
            )
    finally:
        os.close(descriptor)
    assert result.returncode == 7
    records = [json.loads(line) for line in attestation.read_text().splitlines()]
    assert records[-1]["status"] == "FAIL_ENTRYPOINT_EXIT"
    assert records[-1]["exit_code"] == 7
