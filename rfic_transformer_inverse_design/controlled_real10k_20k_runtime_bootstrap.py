#!/usr/bin/env python3
"""Descriptor-closed runtime bootstrap for the controlled real 10K/20K run.

The production process starts CPython with ``-I -B -S`` and executes a sealed
snapshot of this file through ``/proc/self/fd``.  Every executable third-party
or project byte is then consumed from an inherited, sealed descriptor:

* pure Python and package data live in one deterministic, stored ZIP;
* NumPy extension modules live in individual sealed memfds and are loaded by
  :class:`importlib.machinery.ExtensionFileLoader` from ``/proc/self/fd``;
* manifest-bound ``numpy.libs`` objects are preloaded from sealed memfds in the
  frozen dependency order.

Normal filesystem paths are used only by the trusted parent to create sealed
snapshots.  The child never adds a package directory with ``site`` and never
falls back to an installed NumPy or project package.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import json
import os
import platform
import stat
import sys
import sysconfig
import types
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


RUNTIME_CLOSURE_SCHEMA = "controlled_real10k_20k_runtime_closure_v1"
RUNTIME_LAUNCH_REQUEST_SCHEMA = "controlled_real10k_20k_runtime_launch_request_v1"
RUNTIME_ATTESTATION_SCHEMA = "controlled_real10k_20k_runtime_attestation_v1"
BOOTSTRAP_MODULE = (
    "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap"
)
CONTROLLED_PACKAGE = "rfic_transformer_inverse_design"
PURE_ARCHIVE_PATH = "pure/RUNTIME_PURE.zip"
PURE_ARCHIVE_FORMAT = "zip"
PURE_ARCHIVE_COMPRESSION = "ZIP_STORED"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_CREATE_SYSTEM = 3
ZIP_VERSION = 20
ZIP_EXTERNAL_ATTR = (stat.S_IFREG | 0o444) << 16
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
PYTHON_ISOLATION_FLAGS = ("-I", "-B", "-S")
# Exact x86-64 glibc host boundary for the frozen MARS deployment.  This is
# not caller-extensible: NumPy wheel-private objects must be vendored rather
# than relabelled as trusted system dependencies.
TRUSTED_SYSTEM_LIBRARY_ALLOWLIST = (
    "ld-linux-x86-64.so.2",
    "libc.so.6",
    "libdl.so.2",
    "libgcc_s.so.1",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
    "libstdc++.so.6",
)

# Stable child descriptor numbers make the frozen process argv independent of
# the parent's transient descriptor allocation.  Production refuses to replace
# an already-open target descriptor in the parent.
BOOTSTRAP_FD = 200
REQUEST_FD = 201
MANIFEST_FD = 202
PURE_ARCHIVE_FD = 203
ATTESTATION_FD = 204
NATIVE_FD_BASE = 205

_ACTIVE_RUNTIME_STATE: dict[str, Any] | None = None
_ACTIVE_RUNTIME_FINDER: Any = None
_ACTIVE_RUNTIME_MANIFEST: dict[str, Any] | None = None

TOP_LEVEL_KEYS = {
    "schema",
    "bootstrap",
    "python",
    "numpy",
    "pure_archive",
    "members",
    "native_extensions",
    "native_libraries",
    "system_library_allowlist",
    "entrypoints",
}
MEMBER_ROLES = {
    "package_init_code",
    "runtime_bootstrap_code",
    "shared_contract_code",
    "splitter_code",
    "runner_code",
    "trainer_code",
    "materialization_gate_code",
    "materialization_builder_code",
    "evaluator_code",
    "native_smoke_test",
    "numpy_pure",
}
ENTRYPOINT_ROLES = {
    "materialization": "materialization_gate_code",
    "runner": "runner_code",
    "trainer": "trainer_code",
    "evaluator": "evaluator_code",
    "native_smoke": "native_smoke_test",
}
FORBIDDEN_BASENAMES = {"sitecustomize.py", "usercustomize.py"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".pth"}
REQUIRED_SEALS = (
    getattr(fcntl, "F_SEAL_SEAL", 0)
    | getattr(fcntl, "F_SEAL_SHRINK", 0)
    | getattr(fcntl, "F_SEAL_GROW", 0)
    | getattr(fcntl, "F_SEAL_WRITE", 0)
)


class RuntimeClosureError(RuntimeError):
    """A runtime-closure identity, isolation, or descriptor check failed."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_sha256(value: Any, label: str) -> str:
    if not _is_sha256(value):
        raise RuntimeClosureError(f"{label} is not a lowercase SHA-256")
    return value


def _require_exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected or any(
        type(key) is not str for key in value
    ):
        raise RuntimeClosureError(f"{label} keyset is not exact")
    return value


def _require_string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise RuntimeClosureError(f"{label} is not an exact string")
    return value


def _require_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RuntimeClosureError(f"{label} is not an exact integer >= {minimum}")
    return value


def _safe_relative_path(raw: Any, label: str) -> str:
    path = _require_string(raw, label)
    if not path.isascii() or "\\" in path or path.startswith("/"):
        raise RuntimeClosureError(f"{label} is not a portable ASCII relative path")
    pure = PurePosixPath(path)
    if pure.as_posix() != path or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise RuntimeClosureError(f"{label} is not canonical")
    if any(part == "__pycache__" for part in pure.parts):
        raise RuntimeClosureError(f"{label} contains __pycache__")
    if pure.name in FORBIDDEN_BASENAMES or pure.suffix in FORBIDDEN_SUFFIXES:
        raise RuntimeClosureError(f"{label} is an executable site/bytecode artifact")
    return path


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    def reject_constant(raw: str) -> Any:
        raise ValueError(f"non-finite JSON constant {raw}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeClosureError(f"cannot parse {label}: {exc}") from exc
    if type(value) is not dict:
        raise RuntimeClosureError(f"{label} is not a JSON object")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("ascii")


def validate_runtime_manifest(value: Any) -> dict[str, Any]:
    """Validate and return the exact ``RUNTIME_CLOSURE.json`` object."""

    manifest = _require_exact_keys(value, TOP_LEVEL_KEYS, "runtime manifest")
    if manifest["schema"] != RUNTIME_CLOSURE_SCHEMA:
        raise RuntimeClosureError("runtime manifest schema mismatch")

    bootstrap = _require_exact_keys(
        manifest["bootstrap"], {"module", "sha256", "size_bytes"}, "bootstrap"
    )
    if bootstrap["module"] != BOOTSTRAP_MODULE:
        raise RuntimeClosureError("bootstrap module mismatch")
    _require_sha256(bootstrap["sha256"], "bootstrap SHA-256")
    _require_int(bootstrap["size_bytes"], "bootstrap size", minimum=1)

    python = _require_exact_keys(
        manifest["python"],
        {"implementation", "version", "abi_tag", "platform", "executable_sha256"},
        "python runtime",
    )
    if python["implementation"] != "CPython":
        raise RuntimeClosureError("only CPython is accepted")
    for key in ("version", "abi_tag", "platform"):
        _require_string(python[key], f"python {key}")
    _require_sha256(python["executable_sha256"], "python executable SHA-256")

    numpy = _require_exact_keys(manifest["numpy"], {"version"}, "NumPy runtime")
    _require_string(numpy["version"], "NumPy version")

    archive = _require_exact_keys(
        manifest["pure_archive"],
        {"path", "sha256", "size_bytes", "format", "compression"},
        "pure archive",
    )
    if _safe_relative_path(archive["path"], "pure archive path") != PURE_ARCHIVE_PATH:
        raise RuntimeClosureError("pure archive path is not frozen")
    _require_sha256(archive["sha256"], "pure archive SHA-256")
    _require_int(archive["size_bytes"], "pure archive size", minimum=1)
    if archive["format"] != PURE_ARCHIVE_FORMAT or archive["compression"] != PURE_ARCHIVE_COMPRESSION:
        raise RuntimeClosureError("pure archive format/compression mismatch")

    members = manifest["members"]
    if type(members) is not list or not members:
        raise RuntimeClosureError("runtime member list is empty/not a list")
    member_paths: set[str] = set()
    module_names: set[str] = set()
    member_by_role: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(members):
        record = _require_exact_keys(
            raw,
            {"path", "sha256", "size_bytes", "kind", "module", "is_package", "role"},
            f"member[{index}]",
        )
        path = _safe_relative_path(record["path"], f"member[{index}].path")
        if path in member_paths:
            raise RuntimeClosureError("duplicate runtime member path")
        member_paths.add(path)
        _require_sha256(record["sha256"], f"member[{index}] SHA-256")
        _require_int(record["size_bytes"], f"member[{index}] size")
        if record["kind"] not in {"python_source", "data"}:
            raise RuntimeClosureError("runtime member kind is invalid")
        if type(record["is_package"]) is not bool:
            raise RuntimeClosureError("runtime member is_package is not a JSON boolean")
        module = record["module"]
        if module is not None:
            module = _require_string(module, f"member[{index}] module")
            if module in module_names:
                raise RuntimeClosureError("duplicate runtime module name")
            module_names.add(module)
        if record["kind"] == "python_source" and not path.endswith(".py"):
            raise RuntimeClosureError("Python source member does not end in .py")
        if record["kind"] == "data" and (module is not None or record["is_package"]):
            raise RuntimeClosureError("data member advertises module/package semantics")
        if record["role"] not in MEMBER_ROLES:
            raise RuntimeClosureError("runtime member role is invalid")
        member_by_role.setdefault(record["role"], []).append(record)
    if [record["path"] for record in members] != sorted(member_paths):
        raise RuntimeClosureError("runtime members are not in frozen lexical order")

    exact_single_roles = {
        "package_init_code",
        "runtime_bootstrap_code",
        "shared_contract_code",
        "splitter_code",
        "runner_code",
        "trainer_code",
        "materialization_gate_code",
        "materialization_builder_code",
        "evaluator_code",
        "native_smoke_test",
    }
    for role in exact_single_roles:
        if len(member_by_role.get(role, [])) != 1:
            raise RuntimeClosureError(f"runtime role {role} is not exactly singular")
    package_init = member_by_role["package_init_code"][0]
    if (
        package_init["path"] != f"{CONTROLLED_PACKAGE}/__init__.py"
        or package_init["module"] != CONTROLLED_PACKAGE
        or package_init["is_package"] is not True
        or package_init["size_bytes"] != 0
        or package_init["sha256"] != EMPTY_SHA256
    ):
        raise RuntimeClosureError("controlled package initializer is not inert zero-byte code")
    bootstrap_member = member_by_role["runtime_bootstrap_code"][0]
    if (
        bootstrap_member["module"] != BOOTSTRAP_MODULE
        or bootstrap_member["sha256"] != bootstrap["sha256"]
        or bootstrap_member["size_bytes"] != bootstrap["size_bytes"]
    ):
        raise RuntimeClosureError("ZIP bootstrap member does not cross-bind bootstrap identity")
    required_modules = {
        "numpy",
        f"{CONTROLLED_PACKAGE}.controlled_real10k_20k_contract",
        f"{CONTROLLED_PACKAGE}.model_splitting",
        BOOTSTRAP_MODULE,
    }
    if not required_modules.issubset(module_names):
        raise RuntimeClosureError("runtime pure closure lacks NumPy/project modules")

    allowlist = manifest["system_library_allowlist"]
    if (
        type(allowlist) is not list
        or any(type(item) is not str or not item for item in allowlist)
        or allowlist != list(TRUSTED_SYSTEM_LIBRARY_ALLOWLIST)
    ):
        raise RuntimeClosureError("system library allowlist is not the frozen trusted-host set")
    system_libraries = set(allowlist)

    libraries = manifest["native_libraries"]
    if type(libraries) is not list:
        raise RuntimeClosureError("native_libraries is not a list")
    sonames: set[str] = set()
    native_paths: set[str] = set()
    for index, raw in enumerate(libraries):
        record = _require_exact_keys(
            raw,
            {"soname", "path", "basename", "sha256", "size_bytes", "dt_needed", "load_order"},
            f"native_library[{index}]",
        )
        soname = _require_string(record["soname"], "native library SONAME")
        basename = _require_string(record["basename"], "native library basename")
        path = _safe_relative_path(record["path"], "native library path")
        if path != f"native/libraries/{soname}/{basename}":
            raise RuntimeClosureError("native library path is not canonical")
        if soname in sonames or path in native_paths:
            raise RuntimeClosureError("duplicate native library SONAME/path")
        sonames.add(soname)
        native_paths.add(path)
        _require_sha256(record["sha256"], "native library SHA-256")
        _require_int(record["size_bytes"], "native library size", minimum=1)
        if _require_int(record["load_order"], "native library load_order") != index:
            raise RuntimeClosureError("native library load order is not consecutive/list ordered")
        needed = record["dt_needed"]
        if type(needed) is not list or needed != sorted(set(needed)) or any(
            type(item) is not str or not item for item in needed
        ):
            raise RuntimeClosureError("native library DT_NEEDED is not sorted/unique")

    extensions = manifest["native_extensions"]
    if type(extensions) is not list:
        raise RuntimeClosureError("native_extensions is not a list")
    extension_modules: set[str] = set()
    for index, raw in enumerate(extensions):
        record = _require_exact_keys(
            raw,
            {"module", "path", "basename", "sha256", "size_bytes", "init_symbol", "dt_needed"},
            f"native_extension[{index}]",
        )
        module = _require_string(record["module"], "native extension module")
        basename = _require_string(record["basename"], "native extension basename")
        path = _safe_relative_path(record["path"], "native extension path")
        if path != f"native/extensions/{module}/{basename}":
            raise RuntimeClosureError("native extension path is not canonical")
        if module in module_names or module in extension_modules or path in native_paths:
            raise RuntimeClosureError("duplicate pure/native module or native path")
        extension_modules.add(module)
        native_paths.add(path)
        _require_sha256(record["sha256"], "native extension SHA-256")
        _require_int(record["size_bytes"], "native extension size", minimum=1)
        expected_init = "PyInit_" + module.rsplit(".", 1)[-1]
        if record["init_symbol"] != expected_init:
            raise RuntimeClosureError("native extension init symbol mismatch")
        needed = record["dt_needed"]
        if type(needed) is not list or needed != sorted(set(needed)) or any(
            type(item) is not str or not item for item in needed
        ):
            raise RuntimeClosureError("native extension DT_NEEDED is not sorted/unique")
    if [record["module"] for record in extensions] != sorted(extension_modules):
        raise RuntimeClosureError("native extensions are not sorted by module")

    for index, record in enumerate(libraries):
        for needed in record["dt_needed"]:
            if needed not in sonames and needed not in system_libraries:
                raise RuntimeClosureError(f"unbound native library dependency: {needed}")
            if needed in sonames:
                dependency_index = next(
                    item["load_order"] for item in libraries if item["soname"] == needed
                )
                if dependency_index >= index:
                    raise RuntimeClosureError("native library dependency order is not topological")
    for record in extensions:
        if any(
            needed not in sonames and needed not in system_libraries
            for needed in record["dt_needed"]
        ):
            raise RuntimeClosureError("native extension has an unbound DT_NEEDED dependency")

    entrypoints = _require_exact_keys(
        manifest["entrypoints"], set(ENTRYPOINT_ROLES), "runtime entrypoints"
    )
    member_by_path = {record["path"]: record for record in members}
    for name in ENTRYPOINT_ROLES:
        record = _require_exact_keys(
            entrypoints[name], {"member", "sha256", "display_path", "role"}, f"{name} entrypoint"
        )
        member = _safe_relative_path(record["member"], f"{name} entrypoint member")
        display = _safe_relative_path(record["display_path"], f"{name} display path")
        expected_display_prefix = (
            "runtime/project/tests/"
            if name == "native_smoke"
            else "runtime/project/scripts/"
        )
        if not display.startswith(expected_display_prefix):
            raise RuntimeClosureError("entrypoint display path is outside the frozen project tree")
        if record["role"] != ENTRYPOINT_ROLES[name]:
            raise RuntimeClosureError("entrypoint role mismatch")
        bound = member_by_path.get(member)
        if (
            bound is None
            or bound["role"] != record["role"]
            or bound["sha256"] != _require_sha256(record["sha256"], "entrypoint SHA-256")
            or bound["kind"] != "python_source"
            or bound["module"] is not None
            or bound["is_package"] is not False
        ):
            raise RuntimeClosureError("entrypoint does not bind one exact source member")

    return manifest


def parse_runtime_manifest_bytes(payload: bytes, expected_sha256: str) -> dict[str, Any]:
    if _sha256_bytes(payload) != _require_sha256(expected_sha256, "runtime manifest expected SHA-256"):
        raise RuntimeClosureError("runtime manifest SHA-256 mismatch")
    return validate_runtime_manifest(_json_object(payload, "runtime manifest"))


def _pread_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        offset += len(block)


def _require_sealed_descriptor(descriptor: int, label: str) -> bytes:
    if not sys.platform.startswith("linux") or not Path("/proc/self/fd").is_dir():
        raise RuntimeClosureError(f"{label} requires Linux /proc descriptor execution")
    if REQUIRED_SEALS == 0 or not hasattr(fcntl, "F_GET_SEALS"):
        raise RuntimeClosureError(f"{label} requires Linux memfd seals")
    try:
        seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeClosureError(f"cannot inspect {label} descriptor") from exc
    if seals & REQUIRED_SEALS != REQUIRED_SEALS:
        raise RuntimeClosureError(f"{label} descriptor is not fully sealed")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeClosureError(f"{label} descriptor is not regular")
    return _pread_all(descriptor)


def validate_pure_archive_bytes(payload: bytes, manifest: Mapping[str, Any]) -> None:
    archive = manifest["pure_archive"]
    if len(payload) != archive["size_bytes"] or _sha256_bytes(payload) != archive["sha256"]:
        raise RuntimeClosureError("pure archive size/SHA-256 mismatch")
    if len(payload) < 22 or not payload.startswith(b"PK\x03\x04") or payload[-22:-18] != b"PK\x05\x06":
        raise RuntimeClosureError("pure archive has prefix/trailing/EOCD ambiguity")
    if payload[-2:] != b"\x00\x00":
        raise RuntimeClosureError("pure archive has a non-empty archive comment")
    expected = {record["path"]: record for record in manifest["members"]}
    observed_payloads: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive_file:
            infos = archive_file.infolist()
            if archive_file.comment != b"" or [item.filename for item in infos] != sorted(expected):
                raise RuntimeClosureError("pure archive member order/set is not exact")
            if len({item.filename for item in infos}) != len(infos):
                raise RuntimeClosureError("pure archive contains duplicate member names")
            for info in infos:
                record = expected[info.filename]
                if (
                    info.is_dir()
                    or not info.filename.isascii()
                    or info.date_time != ZIP_TIMESTAMP
                    or info.create_system != ZIP_CREATE_SYSTEM
                    or info.create_version != ZIP_VERSION
                    or info.extract_version != ZIP_VERSION
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.external_attr != ZIP_EXTERNAL_ATTR
                    or info.internal_attr != 0
                    or info.flag_bits != 0
                    or info.extra != b""
                    or info.comment != b""
                    or info.file_size != record["size_bytes"]
                    or info.compress_size != record["size_bytes"]
                ):
                    raise RuntimeClosureError(f"pure archive metadata mismatch: {info.filename}")
                member = archive_file.read(info)
                if _sha256_bytes(member) != record["sha256"]:
                    raise RuntimeClosureError(f"pure archive member SHA mismatch: {info.filename}")
                observed_payloads[info.filename] = member
            if archive_file.testzip() is not None:
                raise RuntimeClosureError("pure archive CRC check failed")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, RuntimeClosureError):
            raise
        raise RuntimeClosureError(f"cannot validate pure archive: {exc}") from exc
    # Re-encoding closes local-header/central-directory discrepancies that
    # ZipInfo alone does not expose (for example a local-only extra field).
    expected_buffer = io.BytesIO()
    with zipfile.ZipFile(expected_buffer, "w") as expected_archive:
        expected_archive.comment = b""
        for record in manifest["members"]:
            info = zipfile.ZipInfo(record["path"], ZIP_TIMESTAMP)
            info.create_system = ZIP_CREATE_SYSTEM
            info.create_version = ZIP_VERSION
            info.extract_version = ZIP_VERSION
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = ZIP_EXTERNAL_ATTR
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            expected_archive.writestr(info, observed_payloads[record["path"]])
    if expected_buffer.getvalue() != payload:
        raise RuntimeClosureError("pure archive is not the exact deterministic encoding")


def _read_path_once(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeClosureError(f"{label} is not a regular nlink=1 non-symlink file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise RuntimeClosureError(f"{label} changed while opening")
            return _pread_all(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise RuntimeClosureError(f"cannot read {label}: {path}") from exc


def _tree_files(root: Path) -> set[str]:
    if not root.is_absolute():
        raise RuntimeClosureError("runtime closure tree root must be absolute")
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise RuntimeClosureError("runtime closure tree root is missing") from exc
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeClosureError("runtime closure tree root is not a non-symlink directory")
    observed: set[str] = set()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            candidate = current_path / name
            item = candidate.lstat()
            if candidate.is_symlink() or not stat.S_ISDIR(item.st_mode):
                raise RuntimeClosureError("runtime closure tree contains a linked/non-directory entry")
        for name in files:
            candidate = current_path / name
            item = candidate.lstat()
            if candidate.is_symlink() or not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                raise RuntimeClosureError("runtime closure tree contains a linked/non-regular file")
            relative = candidate.relative_to(root).as_posix()
            _safe_relative_path(relative, "runtime closure tree member")
            observed.add(relative)
    return observed


def audit_runtime_closure_paths(
    manifest_path: Path,
    expected_manifest_sha256: str,
    tree_root: Path,
    bootstrap_path: Path,
    expected_bootstrap_sha256: str,
) -> dict[str, Any]:
    """Path-level audit used before freezing the descriptor snapshots."""

    manifest_payload = _read_path_once(manifest_path, "runtime closure manifest")
    manifest = parse_runtime_manifest_bytes(manifest_payload, expected_manifest_sha256)
    bootstrap_payload = _read_path_once(bootstrap_path, "runtime bootstrap")
    bootstrap_sha = _sha256_bytes(bootstrap_payload)
    if (
        bootstrap_sha != _require_sha256(expected_bootstrap_sha256, "bootstrap expected SHA-256")
        or bootstrap_sha != manifest["bootstrap"]["sha256"]
        or len(bootstrap_payload) != manifest["bootstrap"]["size_bytes"]
    ):
        raise RuntimeClosureError("runtime bootstrap identity mismatch")
    expected_tree_files = {manifest["pure_archive"]["path"]} | {
        record["path"] for record in manifest["native_extensions"]
    } | {record["path"] for record in manifest["native_libraries"]}
    if _tree_files(tree_root) != expected_tree_files:
        raise RuntimeClosureError("runtime closure tree has missing or extra files")
    archive_payload = _read_path_once(tree_root / PURE_ARCHIVE_PATH, "pure runtime archive")
    validate_pure_archive_bytes(archive_payload, manifest)
    for label, records in (
        ("native extension", manifest["native_extensions"]),
        ("native library", manifest["native_libraries"]),
    ):
        for record in records:
            payload = _read_path_once(tree_root / record["path"], label)
            if len(payload) != record["size_bytes"] or _sha256_bytes(payload) != record["sha256"]:
                raise RuntimeClosureError(f"{label} size/SHA-256 mismatch: {record['path']}")
    role_bindings = {
        role: {
            "member": records[0]["path"],
            "sha256": records[0]["sha256"],
            "size_bytes": records[0]["size_bytes"],
        }
        for role, records in {
            role: [record for record in manifest["members"] if record["role"] == role]
            for role in (
                "package_init_code",
                "runtime_bootstrap_code",
                "shared_contract_code",
                "splitter_code",
                "runner_code",
                "trainer_code",
                "materialization_gate_code",
                "materialization_builder_code",
                "evaluator_code",
                "native_smoke_test",
            )
        }.items()
    }
    return {
        "schema": RUNTIME_CLOSURE_SCHEMA,
        "manifest": {
            "path": str(manifest_path),
            "sha256": _sha256_bytes(manifest_payload),
            "size_bytes": len(manifest_payload),
        },
        "tree_root": str(tree_root),
        "bootstrap": {
            "path": str(bootstrap_path),
            "sha256": bootstrap_sha,
            "size_bytes": len(bootstrap_payload),
        },
        "pure_archive": dict(manifest["pure_archive"]),
        "member_count": len(manifest["members"]),
        "native_extension_count": len(manifest["native_extensions"]),
        "native_library_count": len(manifest["native_libraries"]),
        "native_extensions": [dict(record) for record in manifest["native_extensions"]],
        "native_libraries": [dict(record) for record in manifest["native_libraries"]],
        "system_library_allowlist": list(manifest["system_library_allowlist"]),
        "python": dict(manifest["python"]),
        "numpy": dict(manifest["numpy"]),
        "entrypoints": dict(manifest["entrypoints"]),
        "role_bindings": role_bindings,
        "zero_path_fallback": True,
    }


def _sealed_memfd(name: str, payload: bytes) -> int:
    if not sys.platform.startswith("linux") or not hasattr(os, "memfd_create"):
        raise RuntimeClosureError("production runtime launch requires Linux memfd_create")
    flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
    descriptor = os.memfd_create(name, flags)
    try:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            offset += os.write(descriptor, view[offset:])
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
        _require_sealed_descriptor(descriptor, name)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass
class SealedRuntimeLaunch:
    """Held sealed source FDs and their deterministic child FD mapping."""

    process_argv_suffix: list[str]
    source_to_target: list[tuple[int, int]]
    manifest: dict[str, Any]
    request: dict[str, Any]
    _owned_sources: list[int]
    _installed_targets: list[int]

    @property
    def pass_fds(self) -> tuple[int, ...]:
        if not self._installed_targets:
            raise RuntimeClosureError("runtime launch target descriptors are not installed")
        return tuple(self._installed_targets)

    def install_parent_targets(self) -> tuple[int, ...]:
        if self._installed_targets:
            raise RuntimeClosureError("runtime launch target descriptors are already installed")
        targets = [target for _source, target in self.source_to_target]
        for target in targets:
            try:
                os.fstat(target)
            except OSError:
                continue
            raise RuntimeClosureError(f"frozen child descriptor {target} is already open")
        try:
            for source, target in self.source_to_target:
                os.dup2(source, target, inheritable=True)
                self._installed_targets.append(target)
            return tuple(self._installed_targets)
        except BaseException:
            self.close_parent_targets()
            raise

    def close_parent_targets(self) -> None:
        for descriptor in reversed(self._installed_targets):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        self._installed_targets.clear()

    def close(self) -> None:
        self.close_parent_targets()
        for descriptor in reversed(self._owned_sources):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        self._owned_sources.clear()

    def __enter__(self) -> "SealedRuntimeLaunch":
        self.install_parent_targets()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()


def prepare_sealed_runtime_launch(
    *,
    manifest_path: Path,
    expected_manifest_sha256: str,
    tree_root: Path,
    bootstrap_path: Path,
    expected_bootstrap_sha256: str,
    entrypoint: str,
    entrypoint_argv: Sequence[str],
    attestation_output_fd: int,
) -> SealedRuntimeLaunch:
    """Copy one audited closure into sealed memfds for a child process."""

    if entrypoint not in ENTRYPOINT_ROLES:
        raise RuntimeClosureError("runtime entrypoint is invalid")
    if not entrypoint_argv or any(type(token) is not str for token in entrypoint_argv):
        raise RuntimeClosureError("runtime entrypoint argv is empty/non-string")
    identity = audit_runtime_closure_paths(
        manifest_path,
        expected_manifest_sha256,
        tree_root,
        bootstrap_path,
        expected_bootstrap_sha256,
    )
    manifest_payload = _read_path_once(manifest_path, "runtime closure manifest")
    manifest = parse_runtime_manifest_bytes(manifest_payload, expected_manifest_sha256)
    bootstrap_payload = _read_path_once(bootstrap_path, "runtime bootstrap")
    archive_payload = _read_path_once(tree_root / PURE_ARCHIVE_PATH, "pure runtime archive")
    owned: list[int] = []
    mappings: list[tuple[int, int]] = []
    try:
        bootstrap_source = _sealed_memfd("controlled-runtime-bootstrap", bootstrap_payload)
        manifest_source = _sealed_memfd("controlled-runtime-manifest", manifest_payload)
        archive_source = _sealed_memfd("controlled-runtime-pure-archive", archive_payload)
        owned.extend((bootstrap_source, manifest_source, archive_source))
        mappings.extend(
            (
                (bootstrap_source, BOOTSTRAP_FD),
                (manifest_source, MANIFEST_FD),
                (archive_source, PURE_ARCHIVE_FD),
            )
        )
        native_library_fds: dict[str, int] = {}
        native_extension_fds: dict[str, int] = {}
        target = NATIVE_FD_BASE
        for record in manifest["native_libraries"]:
            payload = _read_path_once(tree_root / record["path"], "native runtime library")
            source = _sealed_memfd("controlled-lib-" + record["soname"], payload)
            owned.append(source)
            mappings.append((source, target))
            native_library_fds[record["soname"]] = target
            target += 1
        for record in manifest["native_extensions"]:
            payload = _read_path_once(tree_root / record["path"], "native runtime extension")
            source = _sealed_memfd("controlled-ext-" + record["module"], payload)
            owned.append(source)
            mappings.append((source, target))
            native_extension_fds[record["module"]] = target
            target += 1
        try:
            os.fstat(attestation_output_fd)
        except OSError as exc:
            raise RuntimeClosureError("runtime attestation output descriptor is invalid") from exc
        mappings.append((attestation_output_fd, ATTESTATION_FD))
        request = {
            "schema": RUNTIME_LAUNCH_REQUEST_SCHEMA,
            "entrypoint": entrypoint,
            "entrypoint_argv": list(entrypoint_argv),
            "expected_bootstrap_sha256": expected_bootstrap_sha256,
            "expected_manifest_sha256": expected_manifest_sha256,
            "expected_pure_archive_sha256": manifest["pure_archive"]["sha256"],
            "bootstrap_fd": BOOTSTRAP_FD,
            "manifest_fd": MANIFEST_FD,
            "pure_archive_fd": PURE_ARCHIVE_FD,
            "attestation_fd": ATTESTATION_FD,
            "native_library_fds": native_library_fds,
            "native_extension_fds": native_extension_fds,
        }
        request_source = _sealed_memfd(
            "controlled-runtime-launch-request", _canonical_json_bytes(request)
        )
        owned.append(request_source)
        mappings.insert(1, (request_source, REQUEST_FD))
        return SealedRuntimeLaunch(
            process_argv_suffix=[
                f"/proc/self/fd/{BOOTSTRAP_FD}",
                "--request-fd",
                str(REQUEST_FD),
                "--entrypoint",
                entrypoint,
            ],
            source_to_target=mappings,
            manifest=manifest,
            request={**request, "closure_identity": identity},
            _owned_sources=owned,
            _installed_targets=[],
        )
    except BaseException:
        for descriptor in reversed(owned):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise


class _ArchiveResourceReader(importlib.abc.ResourceReader):
    def __init__(self, loader: "_DescriptorClosureFinder", package: str) -> None:
        self.loader = loader
        self.prefix = package.replace(".", "/") + "/"

    def open_resource(self, resource: str):  # type: ignore[no-untyped-def]
        return io.BytesIO(self.loader.member_bytes(self.prefix + resource))

    def resource_path(self, resource: str) -> str:
        raise FileNotFoundError("descriptor ZIP resources have no filesystem path")

    def is_resource(self, name: str) -> bool:
        path = self.prefix + name
        return path in self.loader.members and self.loader.members[path]["kind"] == "data"

    def contents(self) -> Iterable[str]:
        children: set[str] = set()
        for path in self.loader.members:
            if path.startswith(self.prefix):
                tail = path[len(self.prefix) :]
                if tail and "/" not in tail:
                    children.add(tail)
        return iter(sorted(children))


class _DescriptorClosureFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(
        self,
        archive_fd: int,
        manifest: Mapping[str, Any],
        extension_fds: Mapping[str, int],
    ) -> None:
        self.archive_fd = archive_fd
        self.members = {record["path"]: record for record in manifest["members"]}
        self.modules = {
            record["module"]: record
            for record in manifest["members"]
            if record["module"] is not None
        }
        self.extensions = {
            record["module"]: (record, extension_fds[record["module"]])
            for record in manifest["native_extensions"]
        }

    def member_bytes(self, path: str) -> bytes:
        record = self.members.get(path)
        if record is None:
            raise FileNotFoundError(path)
        with zipfile.ZipFile(f"/proc/self/fd/{self.archive_fd}", "r") as archive:
            payload = archive.read(path)
        if len(payload) != record["size_bytes"] or _sha256_bytes(payload) != record["sha256"]:
            raise RuntimeClosureError(f"descriptor archive member changed: {path}")
        return payload

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):  # type: ignore[no-untyped-def]
        record = self.modules.get(fullname)
        if record is not None:
            origin = f"descriptor-zip:/proc/self/fd/{self.archive_fd}!/{record['path']}"
            return importlib.util.spec_from_loader(
                fullname,
                self,
                origin=origin,
                is_package=record["is_package"],
            )
        extension = self.extensions.get(fullname)
        if extension is not None:
            _record, descriptor = extension
            origin = f"/proc/self/fd/{descriptor}"
            loader = importlib.machinery.ExtensionFileLoader(fullname, origin)
            return importlib.util.spec_from_file_location(fullname, origin, loader=loader)
        return None

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        record = self.modules[module.__name__]
        payload = self.member_bytes(record["path"])
        code = compile(payload.decode("utf-8"), module.__spec__.origin, "exec")
        exec(code, module.__dict__)

    def get_filename(self, fullname: str) -> str:
        record = self.modules[fullname]
        return f"descriptor-zip:/proc/self/fd/{self.archive_fd}!/{record['path']}"

    def get_source(self, fullname: str) -> str:
        return self.member_bytes(self.modules[fullname]["path"]).decode("utf-8")

    def get_code(self, fullname: str):  # type: ignore[no-untyped-def]
        return compile(self.get_source(fullname), self.get_filename(fullname), "exec")

    def is_package(self, fullname: str) -> bool:
        return bool(self.modules[fullname]["is_package"])

    def get_data(self, path: str) -> bytes:
        marker = "!/"
        member = path.split(marker, 1)[1] if marker in path else path.lstrip("/")
        return self.member_bytes(member)

    def get_resource_reader(self, fullname: str):  # type: ignore[no-untyped-def]
        record = self.modules.get(fullname)
        if record is None or not record["is_package"]:
            return None
        return _ArchiveResourceReader(self, fullname)


class _ZeroFallbackFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None):  # type: ignore[no-untyped-def]
        if (
            fullname == "numpy"
            or fullname.startswith("numpy.")
            or fullname == CONTROLLED_PACKAGE
            or fullname.startswith(CONTROLLED_PACKAGE + ".")
        ):
            raise ModuleNotFoundError(
                f"controlled runtime forbids external fallback for {fullname}", name=fullname
            )
        return None


def _validate_launch_request(value: Any) -> dict[str, Any]:
    request = _require_exact_keys(
        value,
        {
            "schema",
            "entrypoint",
            "entrypoint_argv",
            "expected_bootstrap_sha256",
            "expected_manifest_sha256",
            "expected_pure_archive_sha256",
            "bootstrap_fd",
            "manifest_fd",
            "pure_archive_fd",
            "attestation_fd",
            "native_library_fds",
            "native_extension_fds",
        },
        "runtime launch request",
    )
    if request["schema"] != RUNTIME_LAUNCH_REQUEST_SCHEMA:
        raise RuntimeClosureError("runtime launch request schema mismatch")
    if request["entrypoint"] not in ENTRYPOINT_ROLES:
        raise RuntimeClosureError("runtime launch entrypoint mismatch")
    argv = request["entrypoint_argv"]
    if type(argv) is not list or not argv or any(type(item) is not str for item in argv):
        raise RuntimeClosureError("runtime launch entrypoint argv is invalid")
    for key in (
        "expected_bootstrap_sha256",
        "expected_manifest_sha256",
        "expected_pure_archive_sha256",
    ):
        _require_sha256(request[key], key)
    expected_fds = {
        "bootstrap_fd": BOOTSTRAP_FD,
        "manifest_fd": MANIFEST_FD,
        "pure_archive_fd": PURE_ARCHIVE_FD,
        "attestation_fd": ATTESTATION_FD,
    }
    for key, expected in expected_fds.items():
        if _require_int(request[key], key) != expected:
            raise RuntimeClosureError(f"runtime launch {key} is not frozen")
    for key in ("native_library_fds", "native_extension_fds"):
        mapping = request[key]
        if type(mapping) is not dict or any(
            type(name) is not str or not name or type(descriptor) is not int
            for name, descriptor in mapping.items()
        ):
            raise RuntimeClosureError(f"runtime launch {key} is invalid")
    all_fds = [
        request["bootstrap_fd"],
        request["manifest_fd"],
        request["pure_archive_fd"],
        request["attestation_fd"],
        *request["native_library_fds"].values(),
        *request["native_extension_fds"].values(),
    ]
    if len(all_fds) != len(set(all_fds)):
        raise RuntimeClosureError("runtime launch descriptor numbers overlap")
    expected_native_fds = list(range(NATIVE_FD_BASE, NATIVE_FD_BASE + len(all_fds) - 4))
    observed_native_fds = sorted(
        [
            *request["native_library_fds"].values(),
            *request["native_extension_fds"].values(),
        ]
    )
    if observed_native_fds != expected_native_fds:
        raise RuntimeClosureError("runtime native descriptor mapping is not consecutive/frozen")
    return request


def _module_attestation(
    manifest: Mapping[str, Any], finder: _DescriptorClosureFinder
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    pure_by_module = {
        record["module"]: record
        for record in manifest["members"]
        if record["module"] is not None
    }
    native_by_module = {record["module"]: record for record in manifest["native_extensions"]}
    for name, module in sorted(sys.modules.items()):
        if module is None or not (
            name == "numpy"
            or name.startswith("numpy.")
            or name == CONTROLLED_PACKAGE
            or name.startswith(CONTROLLED_PACKAGE + ".")
        ):
            continue
        if name in pure_by_module:
            record = pure_by_module[name]
            expected_origin = finder.get_filename(name)
            kind = "sealed_pure_zip"
        elif name in native_by_module:
            record = native_by_module[name]
            descriptor = finder.extensions[name][1]
            expected_origin = f"/proc/self/fd/{descriptor}"
            kind = "sealed_native_extension"
        else:
            raise RuntimeClosureError(f"loaded controlled module is not manifest-bound: {name}")
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None)
        if origin != expected_origin:
            raise RuntimeClosureError(f"controlled module origin mismatch: {name}")
        records[name] = {
            "kind": kind,
            "origin": origin,
            "sha256": record["sha256"],
        }
    return records


def _write_attestation(descriptor: int, value: Mapping[str, Any]) -> None:
    payload = _canonical_json_bytes(value)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeClosureError("runtime attestation descriptor is not a regular file")
        os.lseek(descriptor, 0, os.SEEK_END)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    except OSError as exc:
        raise RuntimeClosureError("cannot write runtime attestation") from exc


def _execute_request(request: dict[str, Any]) -> int:
    if (
        sys.flags.isolated != 1
        or sys.flags.no_site != 1
        or sys.dont_write_bytecode is not True
    ):
        raise RuntimeClosureError("runtime bootstrap lacks exact -I -B -S isolation")
    if not sys.platform.startswith("linux") or not Path("/proc/self/fd").is_dir():
        raise RuntimeClosureError("production runtime bootstrap requires native Linux /proc")
    bootstrap_payload = _require_sealed_descriptor(request["bootstrap_fd"], "bootstrap")
    if _sha256_bytes(bootstrap_payload) != request["expected_bootstrap_sha256"]:
        raise RuntimeClosureError("executed bootstrap descriptor SHA-256 mismatch")
    manifest_payload = _require_sealed_descriptor(request["manifest_fd"], "manifest")
    manifest = parse_runtime_manifest_bytes(
        manifest_payload, request["expected_manifest_sha256"]
    )
    executable_path = Path("/proc/self/exe")
    executable_payload = executable_path.read_bytes()
    if (
        manifest["python"]["implementation"] != platform.python_implementation()
        or manifest["python"]["version"] != platform.python_version()
        or manifest["python"]["abi_tag"] != sysconfig.get_config_var("SOABI")
        or manifest["python"]["platform"] != sysconfig.get_platform()
        or manifest["python"]["executable_sha256"]
        != _sha256_bytes(executable_payload)
    ):
        raise RuntimeClosureError("active CPython ABI/version/executable identity mismatch")
    if (
        manifest["bootstrap"]["sha256"] != request["expected_bootstrap_sha256"]
        or len(bootstrap_payload) != manifest["bootstrap"]["size_bytes"]
    ):
        raise RuntimeClosureError("manifest bootstrap cross-binding mismatch")
    archive_payload = _require_sealed_descriptor(request["pure_archive_fd"], "pure archive")
    if request["expected_pure_archive_sha256"] != manifest["pure_archive"]["sha256"]:
        raise RuntimeClosureError("launch request pure archive binding mismatch")
    validate_pure_archive_bytes(archive_payload, manifest)

    expected_library_names = [record["soname"] for record in manifest["native_libraries"]]
    expected_extension_names = [record["module"] for record in manifest["native_extensions"]]
    expected_library_fds = {
        name: NATIVE_FD_BASE + index for index, name in enumerate(expected_library_names)
    }
    expected_extension_fds = {
        name: NATIVE_FD_BASE + len(expected_library_names) + index
        for index, name in enumerate(expected_extension_names)
    }
    if request["native_library_fds"] != expected_library_fds:
        raise RuntimeClosureError("native library descriptor key/order mismatch")
    if request["native_extension_fds"] != expected_extension_fds:
        raise RuntimeClosureError("native extension descriptor key/order mismatch")
    for record in manifest["native_libraries"]:
        payload = _require_sealed_descriptor(
            request["native_library_fds"][record["soname"]], "native library"
        )
        if len(payload) != record["size_bytes"] or _sha256_bytes(payload) != record["sha256"]:
            raise RuntimeClosureError("native library descriptor identity mismatch")
    for record in manifest["native_extensions"]:
        payload = _require_sealed_descriptor(
            request["native_extension_fds"][record["module"]], "native extension"
        )
        if len(payload) != record["size_bytes"] or _sha256_bytes(payload) != record["sha256"]:
            raise RuntimeClosureError("native extension descriptor identity mismatch")

    # Remove any preloaded controlled namespace before installing the only two
    # accepted descriptor loaders.  With -S there should be none; fail if an
    # implementation/runtime injected one before bootstrap.
    preloaded = [
        name
        for name in sys.modules
        if name == "numpy"
        or name.startswith("numpy.")
        or name == CONTROLLED_PACKAGE
        or name.startswith(CONTROLLED_PACKAGE + ".")
    ]
    if preloaded:
        raise RuntimeClosureError("controlled namespace was loaded before descriptor bootstrap")

    native_handles: list[Any] = []
    mode = getattr(os, "RTLD_NOW", 2) | getattr(os, "RTLD_GLOBAL", 0x100)
    for record in manifest["native_libraries"]:
        descriptor = request["native_library_fds"][record["soname"]]
        try:
            native_handles.append(ctypes.CDLL(f"/proc/self/fd/{descriptor}", mode=mode))
        except OSError as exc:
            raise RuntimeClosureError(
                f"cannot preload manifest-bound native library {record['soname']}"
            ) from exc

    finder = _DescriptorClosureFinder(
        request["pure_archive_fd"], manifest, request["native_extension_fds"]
    )
    sys.meta_path.insert(0, _ZeroFallbackFinder())
    sys.meta_path.insert(0, finder)
    import importlib

    numpy_module = importlib.import_module("numpy")
    if getattr(numpy_module, "__version__", None) != manifest["numpy"]["version"]:
        raise RuntimeClosureError("descriptor NumPy version mismatch")
    for module_name in (
        f"{CONTROLLED_PACKAGE}.controlled_real10k_20k_contract",
        f"{CONTROLLED_PACKAGE}.model_splitting",
        BOOTSTRAP_MODULE,
    ):
        importlib.import_module(module_name)

    active_state = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": request["entrypoint"],
        "manifest_sha256": request["expected_manifest_sha256"],
        "pure_archive_sha256": request["expected_pure_archive_sha256"],
        "bootstrap_sha256": request["expected_bootstrap_sha256"],
    }
    imported_bootstrap = sys.modules[BOOTSTRAP_MODULE]
    imported_bootstrap._ACTIVE_RUNTIME_STATE = dict(active_state)
    # This file is first executed as ``__main__`` and is then imported again
    # from the sealed ZIP under ``BOOTSTRAP_MODULE``.  The two executions own
    # distinct class identities, so handing the ``__main__`` finder to the
    # public imported-module API would make its exact ``isinstance`` guard
    # fail.  Give that API a reader created by its own sealed class instead;
    # both readers consume the same already-validated archive descriptor.
    imported_bootstrap._ACTIVE_RUNTIME_FINDER = imported_bootstrap._DescriptorClosureFinder(
        request["pure_archive_fd"], manifest, request["native_extension_fds"]
    )
    imported_bootstrap._ACTIVE_RUNTIME_MANIFEST = manifest

    entrypoint = manifest["entrypoints"][request["entrypoint"]]
    finder.member_bytes(entrypoint["member"])
    module_records = _module_attestation(manifest, finder)
    attestation = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
        "entrypoint": request["entrypoint"],
        "entrypoint_sha256": entrypoint["sha256"],
        "manifest_sha256": request["expected_manifest_sha256"],
        "pure_archive_sha256": request["expected_pure_archive_sha256"],
        "bootstrap_sha256": request["expected_bootstrap_sha256"],
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "abi_tag": sysconfig.get_config_var("SOABI"),
            "platform": sysconfig.get_platform(),
        },
        "python_flags": {"isolated": 1, "no_site": 1, "dont_write_bytecode": True},
        "numpy_version": manifest["numpy"]["version"],
        "module_origins": module_records,
        "native_library_sha256": {
            record["soname"]: record["sha256"] for record in manifest["native_libraries"]
        },
        "native_extension_sha256": {
            record["module"]: record["sha256"] for record in manifest["native_extensions"]
        },
        "system_library_allowlist": list(TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
        "site_initialization_disabled": True,
        "external_package_fallback_allowed": False,
    }
    _write_attestation(request["attestation_fd"], attestation)

    source = finder.member_bytes(entrypoint["member"])
    # The unchanged trainer hashes ``Path(__file__).resolve()``.  Execution is
    # still from sealed archive bytes, while this evidence-only display path is
    # the separately manifest-cross-bound trainer path supplied in argv.
    display_path = request["entrypoint_argv"][0]
    if not os.path.isabs(display_path):
        raise RuntimeClosureError("runtime entrypoint argv[0] must be absolute")
    sys.argv = list(request["entrypoint_argv"])
    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "__file__": display_path,
        # Every historical raw CLI has a compatibility branch which prepends
        # ``Path(__file__).parents[1]`` when ``__package__`` is empty.  Under
        # descriptor execution that display path is evidence only and must
        # never become an import authority.  A real sealed package identity
        # keeps those unchanged sources out of the raw-CLI branch; all their
        # absolute controlled imports still resolve through the two sealed
        # finders installed above.
        "__package__": CONTROLLED_PACKAGE,
        "__cached__": None,
        "__loader__": finder,
    }
    exit_code = 0
    try:
        exec(compile(source.decode("utf-8"), display_path, "exec"), namespace)
    except SystemExit as exc:
        raw_code = exc.code
        if raw_code is None:
            exit_code = 0
        elif type(raw_code) is int:
            exit_code = raw_code
        else:
            exit_code = 1
        terminal = {
            "schema": RUNTIME_ATTESTATION_SCHEMA,
            "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL" if exit_code == 0 else "FAIL_ENTRYPOINT_EXIT",
            "entrypoint": request["entrypoint"],
            "exit_code": exit_code,
            "manifest_sha256": request["expected_manifest_sha256"],
            "pure_archive_sha256": request["expected_pure_archive_sha256"],
            "bootstrap_sha256": request["expected_bootstrap_sha256"],
            "module_origins": _module_attestation(manifest, finder),
            "system_library_allowlist": list(TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
            "external_package_fallback_allowed": False,
        }
        _write_attestation(request["attestation_fd"], terminal)
        raise SystemExit(exit_code) from None
    except BaseException:
        terminal = {
            "schema": RUNTIME_ATTESTATION_SCHEMA,
            "status": "FAIL_ENTRYPOINT_EXCEPTION",
            "entrypoint": request["entrypoint"],
            "exit_code": 1,
            "manifest_sha256": request["expected_manifest_sha256"],
            "pure_archive_sha256": request["expected_pure_archive_sha256"],
            "bootstrap_sha256": request["expected_bootstrap_sha256"],
            "module_origins": _module_attestation(manifest, finder),
            "system_library_allowlist": list(TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
            "external_package_fallback_allowed": False,
        }
        _write_attestation(request["attestation_fd"], terminal)
        raise
    terminal = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
        "entrypoint": request["entrypoint"],
        "exit_code": 0,
        "manifest_sha256": request["expected_manifest_sha256"],
        "pure_archive_sha256": request["expected_pure_archive_sha256"],
        "bootstrap_sha256": request["expected_bootstrap_sha256"],
        "module_origins": _module_attestation(manifest, finder),
        "system_library_allowlist": list(TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
        "external_package_fallback_allowed": False,
    }
    _write_attestation(request["attestation_fd"], terminal)
    return 0


def require_active_runtime(entrypoint: str, expected_manifest_sha256: str) -> dict[str, Any]:
    """Fail unless this module was imported by the active descriptor bootstrap."""

    state = _ACTIVE_RUNTIME_STATE
    if (
        type(state) is not dict
        or set(state)
        != {
            "schema",
            "entrypoint",
            "manifest_sha256",
            "pure_archive_sha256",
            "bootstrap_sha256",
        }
        or state.get("schema") != RUNTIME_ATTESTATION_SCHEMA
        or state.get("entrypoint") != entrypoint
        or state.get("manifest_sha256")
        != _require_sha256(expected_manifest_sha256, "active runtime manifest SHA-256")
        or not _is_sha256(state.get("pure_archive_sha256"))
        or not _is_sha256(state.get("bootstrap_sha256"))
    ):
        raise RuntimeClosureError("process was not started by the exact descriptor runtime")
    return dict(state)


def active_member_source(role: str, expected_sha256: str) -> tuple[bytes, str]:
    """Return one manifest-bound pure source member from the active sealed ZIP."""

    finder = _ACTIVE_RUNTIME_FINDER
    manifest = _ACTIVE_RUNTIME_MANIFEST
    expected = _require_sha256(expected_sha256, "active member expected SHA-256")
    if not isinstance(finder, _DescriptorClosureFinder) or type(manifest) is not dict:
        raise RuntimeClosureError("active descriptor runtime member reader is unavailable")
    records = [record for record in manifest["members"] if record["role"] == role]
    if len(records) != 1 or records[0]["kind"] != "python_source":
        raise RuntimeClosureError(f"active runtime role is not one exact Python source: {role}")
    record = records[0]
    if record["sha256"] != expected:
        raise RuntimeClosureError(f"active runtime role SHA mismatch: {role}")
    payload = finder.member_bytes(record["path"])
    origin = f"descriptor-zip:/proc/self/fd/{finder.archive_fd}!/{record['path']}"
    return payload, origin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-fd", type=int, required=True)
    parser.add_argument("--entrypoint", choices=tuple(ENTRYPOINT_ROLES), required=True)
    args = parser.parse_args(argv)
    if args.request_fd != REQUEST_FD:
        parser.error(f"--request-fd must be exactly {REQUEST_FD}")
    request_payload = _require_sealed_descriptor(args.request_fd, "launch request")
    request = _validate_launch_request(_json_object(request_payload, "runtime launch request"))
    if args.entrypoint != request["entrypoint"]:
        parser.error("--entrypoint differs from the sealed launch request")
    return _execute_request(request)


if __name__ == "__main__":
    raise SystemExit(main())
