from __future__ import annotations

import hashlib
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "scripts" / "build_controlled_real10k_20k_runtime_bundle.py"
BOOTSTRAP_PATH = (
    ROOT
    / "rfic_transformer_inverse_design"
    / "controlled_real10k_20k_runtime_bootstrap.py"
)
PACKAGE_BUILDER_PATH = ROOT / "scripts" / "build_controlled_real10k_20k_mars_package.py"


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


gen = _load(GENERATOR_PATH, "_runtime_bundle_generator_under_test")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@contextlib.contextmanager
def _without_numpy_modules():
    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "numpy" or name.startswith("numpy.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == "numpy" or name.startswith("numpy."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


def _fake_elf(*, soname: str | None, needed: tuple[str, ...] = ()) -> bytes:
    """Create the minimum bounded ELF accepted by the production parser."""

    base = 0x400000
    dynamic_offset = 0x200
    strings_offset = 0x400
    strings = bytearray(b"\x00")

    def add(value: str) -> int:
        offset = len(strings)
        strings.extend(value.encode("ascii") + b"\x00")
        return offset

    needed_offsets = [add(value) for value in needed]
    soname_offset = add(soname) if soname is not None else None
    entries = [(5, base + strings_offset), (10, len(strings))]
    entries.extend((1, offset) for offset in needed_offsets)
    if soname_offset is not None:
        entries.append((14, soname_offset))
    entries.append((0, 0))
    dynamic_size = len(entries) * 16
    total_size = strings_offset + len(strings) + 32
    payload = bytearray(total_size)
    ident = bytearray(16)
    ident[:4] = b"\x7fELF"
    ident[4] = 2
    ident[5] = 1
    ident[6] = 1
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        bytes(ident),
        3,
        62,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        2,
        0,
        0,
        0,
    )
    payload[:64] = header
    payload[64:120] = struct.pack(
        "<IIQQQQQQ", 1, 5, 0, base, 0, total_size, total_size, 0x1000
    )
    payload[120:176] = struct.pack(
        "<IIQQQQQQ",
        2,
        6,
        dynamic_offset,
        base + dynamic_offset,
        0,
        dynamic_size,
        dynamic_size,
        8,
    )
    for index, entry in enumerate(entries):
        struct.pack_into("<qQ", payload, dynamic_offset + index * 16, *entry)
    payload[strings_offset : strings_offset + len(strings)] = strings
    return bytes(payload)


def _tree_file(relative: str, payload: bytes):
    return SimpleNamespace(relative=relative, payload=payload, sha256=_sha(payload))


def _held_source(path: Path, payload: bytes):
    return SimpleNamespace(path=path, payload=payload, sha256=_sha(payload))


def _artifact_fixture(tmp_path: Path, *, unknown_dependency: bool = False):
    project = {}
    for role in gen.PROJECT_SOURCE_ROLES:
        payload = b"" if role == "runtime_package_init_code" else b"VALUE = 1\n"
        project[role] = _held_source(tmp_path / f"{role}.py", payload)
    suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    extension_needed = ("libprivate.so", "libnottrusted.so") if unknown_dependency else (
        "libprivate.so",
        "libc.so.6",
    )
    numpy_files = [
        _tree_file("__init__.py", b"VALUE = 1\n"),
        _tree_file("version.py", b'version = "2.5.0"\n__version__ = version\n'),
        _tree_file("typing.pyi", b"VALUE: int\n"),
        _tree_file("_core/__init__.py", b"VALUE = 1\n"),
        _tree_file(
            f"_core/_multiarray_umath{suffix}",
            _fake_elf(soname=None, needed=extension_needed),
        ),
    ]
    library_files = [
        _tree_file(
            "libprivate-file.so",
            _fake_elf(soname="libprivate.so", needed=("libc.so.6",)),
        )
    ]
    numpy_tree = SimpleNamespace(files=numpy_files, directories=set(), path=tmp_path / "numpy")
    libs_tree = SimpleNamespace(files=library_files, directories=set(), path=tmp_path / "numpy.libs")
    spec = {
        "python": {
            "implementation": "CPython",
            "version": "3.12.13",
            "abi_tag": "cpython-312-x86_64-linux-gnu",
            "platform": "linux-x86_64",
            "executable_sha256": "1" * 64,
        },
        "numpy": {"version": "2.5.0"},
    }
    return spec, project, numpy_tree, libs_tree


def test_strict_json_rejects_duplicate_nonfinite_and_nonobject() -> None:
    with pytest.raises(gen.BundleBuildError, match="duplicate"):
        gen._strict_json_object(b'{"a":1,"a":2}', "spec")
    with pytest.raises(gen.BundleBuildError, match="non-finite"):
        gen._strict_json_object(b'{"a":NaN}', "spec")
    with pytest.raises(gen.BundleBuildError, match="object"):
        gen._strict_json_object(b"[]", "spec")


def test_exact_types_reject_bool_integer_aliases() -> None:
    assert gen._exact_string("x", "value") == "x"
    with pytest.raises(gen.BundleBuildError, match="string"):
        gen._exact_string(True, "value")
    with pytest.raises(gen.BundleBuildError, match="string"):
        gen._exact_string(1, "value")
    with pytest.raises(gen.BundleBuildError, match="SHA"):
        gen._exact_sha(True, "value")
    malformed = {
        "schema": gen.INPUT_SPEC_SCHEMA,
        "build_identity": False,
        "python": {},
        "numpy": {},
        "project_sources": {},
        "package_extra_file_roles": {},
    }
    with pytest.raises(gen.BundleBuildError, match="build_identity"):
        gen._parse_input_spec(gen._json_bytes(malformed))


def test_deterministic_zip_has_exact_metadata() -> None:
    entries = {"z/data.txt": b"z", "a.py": b"a = 1\n"}
    first = gen._deterministic_zip(entries)
    second = gen._deterministic_zip(dict(reversed(list(entries.items()))))
    assert first == second
    with gen.zipfile.ZipFile(gen.io.BytesIO(first), "r") as archive:
        infos = archive.infolist()
        assert [item.filename for item in infos] == ["a.py", "z/data.txt"]
        for info in infos:
            assert info.date_time == gen.ZIP_TIMESTAMP
            assert info.compress_type == gen.zipfile.ZIP_STORED
            assert info.external_attr == gen.ZIP_EXTERNAL_ATTR
            assert info.extra == b""
            assert info.comment == b""


def test_elf_parser_extracts_soname_and_needed() -> None:
    identity = gen.parse_elf64_x86_64_dynamic(
        _fake_elf(soname="libprivate.so", needed=("libc.so.6", "libm.so.6")),
        "fixture",
    )
    assert identity.soname == "libprivate.so"
    assert identity.needed == ("libc.so.6", "libm.so.6")


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload[:4],
        lambda payload: b"BAD!" + payload[4:],
        lambda payload: payload[:18] + b"\x02\x00" + payload[20:],
        lambda payload: payload[:56] + struct.pack("<H", 5000) + payload[58:],
    ],
)
def test_elf_parser_rejects_corruption(mutator) -> None:
    payload = _fake_elf(soname="libprivate.so")
    with pytest.raises(gen.BundleBuildError):
        gen.parse_elf64_x86_64_dynamic(mutator(payload), "fixture")


def test_dependency_order_is_topological_and_cycle_fails() -> None:
    graph = {
        "liba.so": gen.ElfDynamicIdentity("liba.so", ("libb.so",)),
        "libb.so": gen.ElfDynamicIdentity("libb.so", ()),
    }
    assert gen._dependency_order(graph) == ["libb.so", "liba.so"]
    graph["libb.so"] = gen.ElfDynamicIdentity("libb.so", ("liba.so",))
    with pytest.raises(gen.BundleBuildError, match="cycle"):
        gen._dependency_order(graph)


def test_runtime_artifacts_match_bootstrap_schema_and_are_deterministic(tmp_path: Path) -> None:
    numpy_before = {
        name for name in sys.modules if name == "numpy" or name.startswith("numpy.")
    }
    spec, project, numpy_tree, libs_tree = _artifact_fixture(tmp_path)
    first = gen._build_runtime_artifacts(spec, project, numpy_tree, libs_tree)
    second = gen._build_runtime_artifacts(spec, project, numpy_tree, libs_tree)
    assert first[0] == second[0]
    assert gen._json_bytes(first[1]) == gen._json_bytes(second[1])
    assert first[2] == second[2]
    closure = first[1]
    assert closure["system_library_allowlist"] == sorted(gen.FROZEN_SYSTEM_LIBRARY_POLICY)
    assert [item["load_order"] for item in closure["native_libraries"]] == [0]
    assert set(closure["entrypoints"]) == set(gen.ENTRYPOINT_BINDINGS)
    bootstrap = _load(BOOTSTRAP_PATH, "_runtime_bootstrap_schema_test")
    bootstrap.validate_runtime_manifest(closure)
    bootstrap.validate_pure_archive_bytes(first[0], closure)
    assert {
        name for name in sys.modules if name == "numpy" or name.startswith("numpy.")
    } == numpy_before


def test_runtime_artifacts_reject_unknown_system_dependency(tmp_path: Path) -> None:
    spec, project, numpy_tree, libs_tree = _artifact_fixture(
        tmp_path, unknown_dependency=True
    )
    with pytest.raises(gen.BundleBuildError, match="outside the frozen system allowlist"):
        gen._build_runtime_artifacts(spec, project, numpy_tree, libs_tree)


def test_package_build_spec_has_exact_frozen_builder_v5_21_roles(tmp_path: Path) -> None:
    held = {}
    for role in gen.PROJECT_SOURCE_ROLES | gen.PACKAGE_EXTRA_FILE_ROLES:
        if role == "package_builder_code":
            payload = PACKAGE_BUILDER_PATH.read_bytes()
            path = PACKAGE_BUILDER_PATH.resolve()
        else:
            payload = role.encode("ascii")
            path = (tmp_path / role).resolve()
        held[role] = _held_source(path, payload)
    package_spec = gen._package_build_spec({}, held, tmp_path.resolve(), "a" * 64)
    assert package_spec["schema"] == gen.PACKAGE_BUILD_SPEC_SCHEMA
    assert package_spec["package_version"] == "v5"
    assert len(package_spec["roles"]) == 21
    builder = _load(PACKAGE_BUILDER_PATH, "_package_builder_schema_test")
    assert gen.FROZEN_PACKAGE_BUILDER_SHA256 == _sha(PACKAGE_BUILDER_PATH.read_bytes())
    assert builder.PACKAGE_VERSION == gen.PACKAGE_VERSION == "v5"
    assert builder.QA_REQUIRED_SCHEMA == gen.PACKAGE_QA_REQUIRED_SCHEMA
    assert builder.PACKAGE_COMMIT_SCHEMA == gen.PACKAGE_COMMIT_SCHEMA
    assert builder.BUILD_ATTEMPT_BODY_SCHEMA == gen.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA
    assert (
        builder.BUILD_ATTEMPT_COMMIT_SCHEMA
        == gen.PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA
    )
    assert set(package_spec["roles"]) == set(builder.REQUIRED_ROLES)
    tree = package_spec["roles"]["runtime_dependency_closure_tree"]
    inventory = package_spec["roles"]["runtime_dependency_closure_json"]
    assert tree["inventory_path"] == inventory["source_path"]
    assert tree["inventory_sha256"] == inventory["sha256"]


def test_package_build_spec_rejects_stale_or_unfrozen_package_builder(tmp_path: Path) -> None:
    held = {
        role: _held_source((tmp_path / role).resolve(), role.encode("ascii"))
        for role in gen.PROJECT_SOURCE_ROLES | gen.PACKAGE_EXTRA_FILE_ROLES
    }
    with pytest.raises(gen.BundleBuildError, match="frozen package builder"):
        gen._package_build_spec({}, held, tmp_path.resolve(), "a" * 64)


def test_held_file_detects_in_place_mutation(tmp_path: Path) -> None:
    source = (tmp_path / "source.py").resolve()
    source.write_bytes(b"before")
    held = gen.HeldFile.open(str(source), "source", expected_sha256=_sha(b"before"))
    try:
        source.write_bytes(b"after!")
        with pytest.raises(gen.BundleBuildError, match="mutated"):
            held.verify("source")
    finally:
        held.close()


def test_held_file_rejects_hardlink_and_symlink(tmp_path: Path) -> None:
    source = (tmp_path / "source").resolve()
    source.write_bytes(b"x")
    hardlink = (tmp_path / "hardlink").resolve()
    os.link(source, hardlink)
    with pytest.raises(gen.BundleBuildError, match="nlink=1"):
        gen.HeldFile.open(str(source), "source")
    source.unlink()
    target = (tmp_path / "target").resolve()
    target.write_bytes(b"x")
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    with pytest.raises(gen.BundleBuildError, match="canonical"):
        gen.HeldFile.open(str(symlink), "symlink")


@pytest.mark.parametrize("forbidden", ["bad.pth", "sitecustomize.py", "bad.pyc"])
def test_held_tree_rejects_forbidden_members(tmp_path: Path, forbidden: str) -> None:
    root = (tmp_path / "numpy").resolve()
    root.mkdir()
    (root / forbidden).write_bytes(b"x")
    with pytest.raises(gen.BundleBuildError, match="forbidden"):
        gen.HeldTree.open(str(root), "tree")


def test_held_tree_keeps_legitimate_hidden_numpy_data(tmp_path: Path) -> None:
    root = (tmp_path / "numpy").resolve()
    data = root / "f2py" / "tests" / "src" / "f2cmap" / ".f2py_f2cmap"
    data.parent.mkdir(parents=True)
    data.write_bytes(b"dict(real=dict(kind=8))\n")
    held = gen.HeldTree.open(str(root), "tree")
    try:
        assert [item.relative for item in held.files] == [
            "f2py/tests/src/f2cmap/.f2py_f2cmap"
        ]
    finally:
        held.close()


def test_held_tree_rejects_symlink_hardlink_and_special(tmp_path: Path) -> None:
    for kind in ("symlink", "hardlink", "fifo"):
        root = (tmp_path / kind / "numpy").resolve()
        root.mkdir(parents=True)
        target = root / "target.py"
        target.write_bytes(b"x")
        if kind == "symlink":
            (root / "bad.py").symlink_to(target)
        elif kind == "hardlink":
            os.link(target, root / "bad.py")
        else:
            os.mkfifo(root / "bad")
        with pytest.raises(gen.BundleBuildError, match="link or special"):
            gen.HeldTree.open(str(root), "tree")


def test_held_tree_detects_root_swap_and_extra_member(tmp_path: Path) -> None:
    root = (tmp_path / "numpy").resolve()
    root.mkdir()
    (root / "__init__.py").write_bytes(b"x")
    held = gen.HeldTree.open(str(root), "tree")
    moved = tmp_path / "numpy.moved"
    root.rename(moved)
    root.mkdir()
    try:
        with pytest.raises(gen.BundleBuildError, match="root was replaced"):
            held.verify("tree")
    finally:
        held.close()

    root2 = (tmp_path / "numpy2").resolve()
    root2.mkdir()
    (root2 / "__init__.py").write_bytes(b"x")
    held2 = gen.HeldTree.open(str(root2), "tree2")
    try:
        (root2 / "extra.txt").write_bytes(b"extra")
        with pytest.raises(gen.BundleBuildError, match="membership changed"):
            held2.verify("tree2")
    finally:
        held2.close()


def test_output_root_is_create_once_and_detects_swap(tmp_path: Path) -> None:
    root_path = (tmp_path / "out").resolve()
    root = gen.OutputRoot.reserve(str(root_path), "out")
    try:
        with pytest.raises(gen.BundleBuildError, match="already exists"):
            gen.OutputRoot.reserve(str(root_path), "out")
        moved = tmp_path / "out.moved"
        root_path.rename(moved)
        root_path.mkdir()
        with pytest.raises(gen.BundleBuildError, match="replaced"):
            root.verify("out")
    finally:
        root.close()


def test_failure_closure_is_immutable_and_has_no_pass_commit(tmp_path: Path) -> None:
    root_path = (tmp_path / "failure").resolve()
    root = gen.OutputRoot.reserve(str(root_path), "failure")
    try:
        gen._best_effort_failure_closure(
            root,
            error=gen.BundleBuildError("expected failure"),
            build_identity="fixture",
            label="failure",
        )
        failure = json.loads((root_path / gen.FAILURE_RELATIVE).read_text())
        terminal = json.loads((root_path / gen.FAILURE_COMMIT_RELATIVE).read_text())
        assert failure["status"] == "FAIL"
        assert terminal["status"] == "FAIL"
        assert not (root_path / gen.BUNDLE_COMMIT_RELATIVE).exists()
        assert stat.S_IMODE(root_path.stat().st_mode) == gen.DIRECTORY_MODE
        assert all(
            stat.S_IMODE(path.stat().st_mode) == gen.FILE_MODE
            for path in root_path.iterdir()
        )
    finally:
        root.close()


def test_durability_failure_precedes_pass_publication(tmp_path: Path, monkeypatch) -> None:
    root_path = (tmp_path / "durability").resolve()
    root = gen.OutputRoot.reserve(str(root_path), "durability")
    original = gen._fsync_fd

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(gen, "_fsync_fd", fail_fsync)
    try:
        with pytest.raises(OSError, match="injected"):
            gen._write_output_file(root.descriptor, "artifact.bin", b"payload")
        assert not (root_path / gen.BUNDLE_COMMIT_RELATIVE).exists()
    finally:
        monkeypatch.setattr(gen, "_fsync_fd", original)
        gen._best_effort_failure_closure(
            root,
            error=gen.BundleBuildError("durability failed"),
            build_identity="fixture",
            label="durability",
        )
        root.close()
    assert json.loads((root_path / gen.FAILURE_COMMIT_RELATIVE).read_text())["status"] == "FAIL"


def test_output_verifier_rejects_extra_member(tmp_path: Path) -> None:
    root_path = (tmp_path / "output").resolve()
    root = gen.OutputRoot.reserve(str(root_path), "output")
    try:
        record = gen._write_output_file(root.descriptor, "expected.bin", b"x")
        gen._write_output_file(root.descriptor, "extra.bin", b"y")
        with pytest.raises(gen.BundleBuildError, match="extra"):
            gen._verify_output_records(root.descriptor, {record["path"]: record["sha256"]})
    finally:
        root.close()


def test_full_result_blind_bundle_publication_and_external_attempt_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = (tmp_path / "sources").resolve()
    source_root.mkdir()
    held_files = []
    held_trees = []
    bundle = attempt = None
    try:
        project = {}
        for role in sorted(gen.PROJECT_SOURCE_ROLES):
            if role == "runtime_bootstrap_code":
                path = BOOTSTRAP_PATH.resolve()
            else:
                path = source_root / f"{role}.py"
                path.write_bytes(
                    b""
                    if role == "runtime_package_init_code"
                    else f"VALUE_{role.upper()} = 1\n".encode("ascii")
                )
            held = gen.HeldFile.open(str(path), role)
            held_files.append(held)
            project[role] = held
        extras = {}
        for role in sorted(gen.PACKAGE_EXTRA_FILE_ROLES):
            if role == "package_builder_code":
                path = PACKAGE_BUILDER_PATH.resolve()
            else:
                suffix = ".json" if role.endswith("_json") else ".bin"
                path = source_root / f"{role}{suffix}"
                path.write_bytes(
                    gen._json_bytes({"role": role})
                    if suffix == ".json"
                    else role.encode("ascii")
                )
            held = gen.HeldFile.open(str(path), role)
            held_files.append(held)
            extras[role] = held

        numpy_root = (tmp_path / "numpy").resolve()
        libraries_root = (tmp_path / "numpy.libs").resolve()
        (numpy_root / "_core").mkdir(parents=True)
        libraries_root.mkdir()
        (numpy_root / "__init__.py").write_bytes(b"VALUE = 1\n")
        (numpy_root / "version.py").write_bytes(
            b'version = "2.5.0"\n__version__ = version\n'
        )
        (numpy_root / "_core" / "__init__.py").write_bytes(b"VALUE = 1\n")
        suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
        (numpy_root / "_core" / f"_multiarray_umath{suffix}").write_bytes(
            _fake_elf(soname=None, needed=("libprivate.so", "libc.so.6"))
        )
        (libraries_root / "libprivate-file.so").write_bytes(
            _fake_elf(soname="libprivate.so", needed=("libc.so.6",))
        )
        numpy_tree = gen.HeldTree.open(str(numpy_root), "numpy")
        libs_tree = gen.HeldTree.open(str(libraries_root), "numpy.libs")
        held_trees.extend([numpy_tree, libs_tree])

        spec_path = source_root / "input_spec.json"
        spec_path.write_bytes(b"{}\n")
        spec_file = gen.HeldFile.open(str(spec_path), "spec")
        generator = gen.HeldFile.open(str(GENERATOR_PATH.resolve()), "generator")
        executable_path = Path(sys.executable).resolve(strict=True)
        executable = gen.HeldFile.open(str(executable_path), "python")
        held_files.extend([spec_file, generator, executable])
        spec = {
            "build_identity": "fixture-build",
            "python": {
                "implementation": "CPython",
                "version": "3.12.13",
                "abi_tag": "cpython-312-x86_64-linux-gnu",
                "platform": "linux-x86_64",
                "executable_sha256": executable.sha256,
            },
            "numpy": {"version": "2.5.0"},
        }
        bundle_path = (tmp_path / "bundle").resolve()
        attempt_path = (tmp_path / "attempt").resolve()
        bundle = gen.OutputRoot.reserve(str(bundle_path), "bundle")
        attempt = gen.OutputRoot.reserve(str(attempt_path), "attempt")
        bootstrap = _load(BOOTSTRAP_PATH, "_runtime_bootstrap_full_publication_test")
        monkeypatch.setattr(gen, "_load_bootstrap_for_audit", lambda _held: bootstrap)
        with _without_numpy_modules():
            result = gen._build_success(
                spec,
                spec_file,
                generator,
                executable,
                project,
                extras,
                numpy_tree,
                libs_tree,
                bundle,
                attempt,
            )
        assert len(result["runtime_closure_sha256"]) == 64
        commit = json.loads((bundle_path / gen.BUNDLE_COMMIT_RELATIVE).read_text())
        external = json.loads((attempt_path / gen.ATTEMPT_COMMIT_RELATIVE).read_text())
        package_spec = json.loads((bundle_path / gen.PACKAGE_SPEC_RELATIVE).read_text())
        closure = json.loads((bundle_path / gen.RUNTIME_CLOSURE_RELATIVE).read_text())
        assert commit["status"] == "PASS"
        assert external["status"] == "PASS"
        assert len(package_spec["roles"]) == 21
        assert closure["system_library_allowlist"] == list(
            bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
        )
        assert not (bundle_path / gen.FAILURE_COMMIT_RELATIVE).exists()
        assert stat.S_IMODE(bundle_path.stat().st_mode) == gen.DIRECTORY_MODE
        assert stat.S_IMODE(attempt_path.stat().st_mode) == gen.DIRECTORY_MODE
    finally:
        if bundle is not None:
            bundle.close()
        if attempt is not None:
            attempt.close()
        for tree in reversed(held_trees):
            tree.close()
        for held in reversed(held_files):
            held.close()


def test_cli_requires_exact_isolation_flags() -> None:
    python312 = Path(
        "/Users/wyf/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    )
    if not python312.exists():
        pytest.skip("bundled CPython 3.12 is unavailable")
    without = subprocess.run(
        [str(python312), str(GENERATOR_PATH), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert without.returncode == 1
    assert "-I -B -S" in without.stderr
    isolated = subprocess.run(
        [str(python312), "-I", "-B", "-S", str(GENERATOR_PATH), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert isolated.returncode == 0, isolated.stderr
    assert "--expected-generator-sha256" in isolated.stdout


def test_generator_source_contains_no_external_elf_execution() -> None:
    source = GENERATOR_PATH.read_text()
    forbidden = ("readelf", "ldd ", "subprocess.", "import numpy", "from numpy")
    assert not any(token in source for token in forbidden)
    assert "parse_elf64_x86_64_dynamic" in source
