#!/usr/bin/env python3
"""Darwin-safe hostile tests for result_free_runtime_smoke_v10.

The production Linux smoke is deliberately not launched here.  These tests
exercise strict JSON, exact authorization/argv binding, the exact build PASS
receipt emitted by the sibling v10 builder, nofollow helpers, and digest logic
using only temporary local fixtures.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import io
import json
import os
import shutil
import runpy
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable, Iterator


HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.fspath(HERE))
import build_result_free_transport_runtime_v10 as builder  # noqa: E402
import result_free_runtime_smoke_v10 as smoke  # noqa: E402


def sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def minimal_elf64_dynamic_fixture() -> bytes:
    """Build parser-only ELF64 bytes with NEEDED, SONAME, and $ORIGIN RUNPATH."""
    data = bytearray(0x400)
    data[:16] = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    struct.pack_into(
        "<HHIQQQIHHHHHH",
        data,
        16,
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
    struct.pack_into(
        "<IIQQQQQQ", data, 64, 1, 5, 0, 0x400000, 0, len(data), len(data), 0x1000
    )
    dynamic_entries = [
        (smoke.DT_STRTAB, 0x400300),
        (smoke.DT_STRSZ, 48),
        (smoke.DT_NEEDED, 1),
        (smoke.DT_SONAME, 11),
        (smoke.DT_RUNPATH, 26),
        (smoke.DT_NULL, 0),
    ]
    struct.pack_into(
        "<IIQQQQQQ",
        data,
        64 + 56,
        2,
        6,
        0x200,
        0x400200,
        0,
        len(dynamic_entries) * 16,
        len(dynamic_entries) * 16,
        8,
    )
    for index, entry in enumerate(dynamic_entries):
        struct.pack_into("<QQ", data, 0x200 + index * 16, *entry)
    strings = b"\0libdep.so\0libconsumer.so\0$ORIGIN/deps\0"
    data[0x300 : 0x300 + len(strings)] = strings
    return bytes(data)


def elf_fixture_with_duplicate_dynamic_tag(tag: int, value: int) -> bytes:
    """Replace RUNPATH with a duplicate singleton tag while retaining DT_NULL."""
    data = bytearray(minimal_elf64_dynamic_fixture())
    struct.pack_into("<QQ", data, 0x200 + 4 * 16, tag, value)
    return bytes(data)


def elf_fixture_strtab_exceeds_file_backed_load(*, trailing_load: bool) -> bytes:
    """Keep the strtab in-file but make it cross its mapping PT_LOAD boundary."""
    data = bytearray(minimal_elf64_dynamic_fixture())
    struct.pack_into(
        "<IIQQQQQQ",
        data,
        64,
        smoke.PT_LOAD,
        5,
        0,
        0x400000,
        0,
        0x320,
        len(data),
        0x1000,
    )
    if trailing_load:
        struct.pack_into("<H", data, 56, 3)
        struct.pack_into(
            "<IIQQQQQQ",
            data,
            64 + 2 * 56,
            smoke.PT_LOAD,
            4,
            0x320,
            0x400320,
            0,
            len(data) - 0x320,
            len(data) - 0x320,
            0x1000,
        )
    return bytes(data)


class NoopMutationGuard:
    def assert_clean(self, _phase: str) -> None:
        return None


def manifest_record(relative: str, path: Path) -> dict[str, Any]:
    info = os.stat(path, follow_symlinks=False)
    return {
        "relative_path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": info.st_size,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
    }


def linux_real_native_origin_fixture(
    base: Path,
) -> tuple[dict[str, bool], str, dict[str, Any]]:
    """Compile and exercise a real held-FD CPython extension on Linux only."""
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {
        "compiler_subprocess_used": False,
        "external_subprocess_scope": "NONE",
        "external_process_inspection_or_control_used": False,
        "signals_used": False,
        "self_proc_maps_read_by_smoke_logic": False,
    }
    if not sys.platform.startswith("linux"):
        return checks, "NOT_RUN_NON_LINUX", details

    compiler = shutil.which("cc")
    if compiler is None:
        return checks, "NOT_RUN_LINUX_CC_UNAVAILABLE", details
    include_text = sysconfig.get_path("include")
    if type(include_text) is not str or not (Path(include_text) / "Python.h").is_file():
        details["python_include"] = include_text
        return checks, "NOT_RUN_LINUX_PYTHON_HEADERS_UNAVAILABLE", details
    extension_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if type(extension_suffix) is not str or not extension_suffix.endswith(".so"):
        details["extension_suffix"] = extension_suffix
        return checks, "NOT_RUN_LINUX_EXT_SUFFIX_UNAVAILABLE", details

    module_name = "_smoke_v10_heldext"
    dependency_soname = "libsmoke_v10_helddep.so"
    extension_relative = f"{module_name}{extension_suffix}"
    dependency_relative = f"deps/{dependency_soname}"
    source_root = base / "linux-native-build-source"
    private_root = base / "linux-native-private-runtime"
    dependency_root = private_root / "deps"
    source_root.mkdir()
    dependency_root.mkdir(parents=True)
    dependency_source = source_root / "helddep.c"
    extension_source = source_root / "heldext.c"
    dependency_path = private_root / dependency_relative
    extension_path = private_root / extension_relative
    dependency_source.write_text(
        "int smoke_v10_held_dep_value(void) { return 37; }\n",
        encoding="utf-8",
    )
    extension_source.write_text(
        """#define PY_SSIZE_T_CLEAN
#include <Python.h>

extern int smoke_v10_held_dep_value(void);

static PyObject *held_value(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    return PyLong_FromLong(smoke_v10_held_dep_value());
}

static PyMethodDef held_methods[] = {
    {"value", held_value, METH_NOARGS, "Return the private dependency value."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef held_module = {
    PyModuleDef_HEAD_INIT,
    "_smoke_v10_heldext",
    NULL,
    -1,
    held_methods
};

PyMODINIT_FUNC PyInit__smoke_v10_heldext(void) {
    return PyModule_Create(&held_module);
}
""",
        encoding="utf-8",
    )
    compile_commands = [
        [
            compiler,
            "-shared",
            "-fPIC",
            "-O0",
            f"-Wl,-soname,{dependency_soname}",
            "-o",
            os.fspath(dependency_path),
            os.fspath(dependency_source),
        ],
        [
            compiler,
            "-shared",
            "-fPIC",
            "-O0",
            f"-I{include_text}",
            os.fspath(extension_source),
            f"-L{dependency_root}",
            "-lsmoke_v10_helddep",
            "-Wl,-rpath,$ORIGIN/deps",
            f"-Wl,-soname,{extension_relative}",
            "-o",
            os.fspath(extension_path),
        ],
    ]
    details.update(
        {
            "compiler": compiler,
            "python_include": include_text,
            "extension_suffix": extension_suffix,
            "compile_commands": compile_commands,
        }
    )

    root_parent_fd = -1
    root_fd = -1
    guard: smoke.RecursiveInotifyGuard | None = None
    closure: smoke.PrivateElfClosure | None = None
    finder: smoke.HeldVerifiedRuntimeFinder | None = None
    try:
        compile_receipts: list[dict[str, Any]] = []
        for command in compile_commands:
            details["compiler_subprocess_used"] = True
            details["external_subprocess_scope"] = (
                "LOCAL_CC_COMPILE_TWO_TEMP_ELF_FIXTURES_ONLY"
            )
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            compile_receipts.append(
                {
                    "argv": command,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
        details["compile_receipts"] = compile_receipts
        os.chmod(dependency_path, 0o444)
        os.chmod(extension_path, 0o444)
        files = {
            dependency_relative: manifest_record(
                dependency_relative, dependency_path
            ),
            extension_relative: manifest_record(extension_relative, extension_path),
        }
        details["manifest_records"] = [files[key] for key in sorted(files)]

        root_parent_fd, root_fd, _ = smoke.open_absolute_directory(
            os.fspath(private_root.resolve()), "synthetic Linux private runtime"
        )
        guard = smoke.RecursiveInotifyGuard(root_fd)
        details["self_proc_maps_read_by_smoke_logic"] = True
        closure = smoke.PrivateElfClosure(root_fd, files, guard)
        extension_dynamic = closure.entries[extension_relative]["dynamic"]
        expected_relation = {
            "owner": extension_relative,
            "needed": dependency_soname,
            "target": dependency_relative,
        }
        checks["linux_real_elf_binds_private_needed_and_origin_runpath"] = (
            dependency_soname in extension_dynamic["needed"]
            and "$ORIGIN/deps"
            in [*extension_dynamic["rpath"], *extension_dynamic["runpath"]]
            and expected_relation in closure.private_needed_relations
            and expected_relation in closure.rpath_relations
            and closure.preload_paths == [dependency_relative]
        )
        checks["linux_real_elf_origin_search_resolves_inside_private_root"] = any(
            item.get("resolved_private_relative_directory") == "deps"
            for item in extension_dynamic["validated_search_paths"]
        )

        closure.preload()
        finder = smoke.HeldVerifiedRuntimeFinder(
            root_fd, files, {"deps"}, guard, closure
        )
        spec = finder.find_spec(module_name)
        if spec is None or spec.loader is None:
            raise smoke.SmokeError("real Linux native fixture spec was not found")
        held_origin = spec.loader.origin
        module = smoke.importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        guard.assert_clean("real Linux native extension fixture complete")
        evidence = closure.evidence()
        details["held_loader_origin"] = held_origin
        details["private_elf_evidence"] = evidence
        checks["linux_real_cpython_extension_executes_from_held_fd"] = (
            module.value() == 37
            and held_origin == f"/proc/self/fd/{spec.loader.fd}"
            and finder.loaded[module_name]["execution_kind"] == "extension"
            and finder.loaded[module_name][
                "executed_from_held_verified_bytes_or_proc_fd"
            ]
        )
        mapped_relatives = {
            item["relative_path"]
            for item in evidence["maps"]["private_mappings"]
        }
        checks["linux_real_private_dependency_and_extension_map_held_inodes"] = (
            mapped_relatives == {dependency_relative, extension_relative}
            and evidence["required_extension_paths"] == [extension_relative]
            and evidence["maps"]["same_soname_external_escape_absent"]
        )
        checks["linux_real_native_fixture_inotify_remains_clean"] = True
    except subprocess.CalledProcessError as exc:
        details["compile_failure"] = {
            "argv": exc.cmd,
            "returncode": exc.returncode,
            "stdout": (exc.stdout or "")[-4000:],
            "stderr": (exc.stderr or "")[-4000:],
        }
        checks.setdefault("linux_real_native_fixture_completed", False)
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
        checks.setdefault("linux_real_native_fixture_completed", False)
    finally:
        if finder is not None:
            finder.close()
        if closure is not None:
            closure.close()
        if guard is not None:
            guard.close()
        if root_fd >= 0:
            os.close(root_fd)
        if root_parent_fd >= 0:
            os.close(root_parent_fd)
        sys.modules.pop(module_name, None)

    if checks and all(checks.values()):
        return checks, "PASS_LINUX_REAL_HELD_EXTENSION_ORIGIN_FIXTURE", details
    return checks, "FAIL_LINUX_REAL_HELD_EXTENSION_ORIGIN_FIXTURE", details


def rejected(call: Callable[[], Any]) -> bool:
    try:
        call()
    except (smoke.SmokeError, SystemExit, OSError):
        return True
    return False


def cli_rejected(argv: list[str]) -> bool:
    with contextlib.redirect_stderr(io.StringIO()):
        return rejected(lambda: smoke._parse_cli(argv))


@contextlib.contextmanager
def argv_as(value: list[str]) -> Iterator[None]:
    old = sys.argv
    sys.argv = list(value)
    try:
        yield
    finally:
        sys.argv = old


@contextlib.contextmanager
def installed_fixed_held_fds(
    interpreter_path: Path, smoke_source_path: Path
) -> Iterator[dict[str, Any]]:
    originals: dict[int, tuple[int, bool] | None] = {}
    opened: list[int] = []
    for target in (smoke.INTERPRETER_FD, smoke.SMOKE_SOURCE_FD):
        try:
            backup = os.dup(target)
            originals[target] = (backup, os.get_inheritable(target))
        except OSError:
            originals[target] = None
    try:
        interpreter_fd = os.open(interpreter_path, os.O_RDONLY)
        source_fd = os.open(smoke_source_path, os.O_RDONLY)
        opened.extend((interpreter_fd, source_fd))
        os.dup2(interpreter_fd, smoke.INTERPRETER_FD, inheritable=False)
        os.dup2(source_fd, smoke.SMOKE_SOURCE_FD, inheritable=False)
        source_info = os.fstat(smoke.SMOKE_SOURCE_FD)
        yield {
            "source_identity": {
                "device": source_info.st_dev,
                "inode": source_info.st_ino,
                "size_bytes": source_info.st_size,
                "mtime_ns": source_info.st_mtime_ns,
                "ctime_ns": source_info.st_ctime_ns,
                "mode": f"{stat.S_IMODE(source_info.st_mode):04o}",
                "nlink": source_info.st_nlink,
            },
            "source_sha256": hashlib.sha256(
                smoke_source_path.read_bytes()
            ).hexdigest(),
            "interpreter_sha256": hashlib.sha256(
                interpreter_path.read_bytes()
            ).hexdigest(),
        }
    finally:
        for target in (smoke.INTERPRETER_FD, smoke.SMOKE_SOURCE_FD):
            original = originals[target]
            if original is None:
                try:
                    os.close(target)
                except OSError:
                    pass
            else:
                backup, inheritable = original
                os.dup2(backup, target, inheritable=inheritable)
                os.close(backup)
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


class ImmediateTimeoutSelector:
    def __init__(self) -> None:
        self.members: dict[int, Any] = {}

    def register(self, stream: Any, _events: int, data: str) -> None:
        self.members[stream.fileno()] = (stream, data)

    def get_map(self) -> dict[int, Any]:
        return self.members

    def select(self, _timeout: float) -> list[Any]:
        return []

    def close(self) -> None:
        self.members.clear()


class FakeTimeoutProcess:
    def __init__(self) -> None:
        stdout_read, self.stdout_write = os.pipe()
        stderr_read, self.stderr_write = os.pipe()
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        return -9 if self.waited else None

    def kill(self) -> None:
        self.killed = True
        for fd in (self.stdout_write, self.stderr_write):
            try:
                os.close(fd)
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waited = True
        return -9


def run_linux_real_held_bootstrap_fixture(
    base: Path,
) -> tuple[str, dict[str, bool], dict[str, Any]]:
    if not sys.platform.startswith("linux") or not Path("/proc/self/fd").is_dir():
        return "NOT_RUN_NON_LINUX", {}, {
            "status": "NOT_RUN_NON_LINUX",
            "local_subprocess_used": False,
            "signals_used": False,
        }

    fixture = base / "linux-real-held-bootstrap"
    fixture.mkdir(mode=0o700)
    interpreter_real = Path(os.path.realpath(sys.executable))
    interpreter_evidence = fixture / "python-original-evidence"
    interpreter_evidence.symlink_to(interpreter_real)
    source_evidence = fixture / "smoke-original-evidence.py"
    source_evidence.write_text(
        "import fcntl,json,os,stat\n"
        "def held_byte_bootstrap_main(argv,context):\n"
        " p=argv[argv.index('--synthetic-python-evidence-path')+1]\n"
        " ps=os.stat(context['original_smoke_evidence_path'],follow_symlinks=False)\n"
        " pi=os.stat(p,follow_symlinks=False)\n"
        " hs=os.fstat(198); hi=os.fstat(197)\n"
        " print(json.dumps({'entry':'HELD_STUB_V1','module_file':__file__,"
        "'source_path_matches_held':(ps.st_dev,ps.st_ino)==(hs.st_dev,hs.st_ino),"
        "'python_path_matches_held':(pi.st_dev,pi.st_ino)==(hi.st_dev,hi.st_ino),"
        "'fd197_cloexec':bool(fcntl.fcntl(197,fcntl.F_GETFD)&fcntl.FD_CLOEXEC),"
        "'fd198_cloexec':bool(fcntl.fcntl(198,fcntl.F_GETFD)&fcntl.FD_CLOEXEC),"
        "'argv0':context['actual_cmdline'][0],"
        "'bootstrap_token_sha':__import__('hashlib').sha256(context['actual_cmdline'][5].encode()).hexdigest(),"
        "'provenance_policy':'HELD_BYTES_EXECUTE;PRODUCTION_SOURCE_REJECTS_REPLACED_EVIDENCE_PATH'}))\n"
        " return 0\n",
        encoding="utf-8",
    )
    os.chmod(source_evidence, 0o444)
    authorization_path = fixture / "smoke-authorization.json"
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {
        "status": "STARTED",
        "local_subprocess_used": True,
        "signals_used": False,
    }
    with installed_fixed_held_fds(
        interpreter_evidence, source_evidence
    ) as held_fixture:
        held_source_backup = fixture / "held-source-preserved.py"
        os.rename(source_evidence, held_source_backup)
        source_evidence.write_text(
            "raise RuntimeError('PATH_EXECUTED')\n", encoding="utf-8"
        )
        os.chmod(source_evidence, 0o444)
        interpreter_evidence.unlink()
        interpreter_evidence.write_bytes(b"not an interpreter\n")
        os.chmod(interpreter_evidence, 0o444)
        held_fixture["source_identity"] = smoke._stat_full_file_identity(
            os.fstat(smoke.SMOKE_SOURCE_FD)
        )
        placeholder_cli = [
            "--smoke-authorization", os.fspath(authorization_path),
            "--trusted-smoke-authorization-sha256", smoke.AUTH_SHA_PLACEHOLDER,
            "--expected-python-sha256", held_fixture["interpreter_sha256"],
            "--synthetic-python-evidence-path", os.fspath(interpreter_evidence),
        ]
        template = smoke.build_held_smoke_argv(
            held_fixture["source_identity"],
            held_fixture["source_sha256"],
            os.fspath(source_evidence),
            placeholder_cli,
        )
        authorization = {
            "schema": smoke.AUTHORIZATION_SCHEMA,
            "status": smoke.AUTHORIZATION_STATUS,
            "decision_id": "linux-real-held-bootstrap-synthetic-v1",
            "scope": "RESULT_FREE_RUNTIME_LAYOUT_SMOKE_ONLY",
            "authority": {
                "runtime_layout_smoke_authorized": True,
                "scratch_write_authorized": True,
                "root_write_authorized": False,
                "transport_or_build_authorized": False,
                "controller_or_outer_main_authorized": False,
                "result_access_authorized": False,
                "signals_authorized": False,
                "deployment_or_resume_authorized": False,
            },
            "paths": {
                "smoke_authorization": os.fspath(authorization_path),
                "build_pass_receipt": os.fspath(fixture / "unused-build.json"),
                "final_root": os.fspath(fixture / "unused-root"),
                "scratch_dir": os.fspath(fixture / "unused-scratch"),
                "source_python": os.fspath(interpreter_evidence),
                "smoke_script": os.fspath(source_evidence),
            },
            "identities": {
                "final_root": {"device": 1, "inode": 2},
                "scratch": {"device": 3, "inode": 4},
            },
            "expected": {
                "build_pass_receipt_sha256": sha("unused-build"),
                "build_authorization_sha256": sha("unused-build-auth"),
                "build_commit_intent_sha256": sha("unused-intent"),
                "runtime_manifest_sha256": sha("unused-manifest"),
                "files_only_runtime_root_digest": sha("unused-runtime"),
                "files_only_private_root_digest": sha("unused-private"),
                "structural_private_tree_digest": sha("unused-private-struct"),
                "files_only_full_root_digest": sha("unused-full"),
                "structural_full_root_digest": sha("unused-full-struct"),
                "empty_scratch_inventory_digest": smoke.EMPTY_SHA256,
                "source_python_sha256": held_fixture["interpreter_sha256"],
                "smoke_script_sha256": held_fixture["source_sha256"],
            },
            "bound_v8": {
                "directory_name": smoke.V8_DIRECTORY_NAME,
                "prepared_receipt_sha256": smoke.V8_PREPARED_RECEIPT_SHA256,
                "bundle_manifest_sha256": smoke.V8_BUNDLE_MANIFEST_SHA256,
                "sha256_index_sha256": smoke.V8_SHA256_INDEX_SHA256,
                "top_level_count": smoke.V8_TOP_LEVEL_COUNT,
                "indexed_count": smoke.V8_INDEXED_COUNT,
            },
            "held_byte_bootstrap": smoke.held_smoke_authorization_binding(
                held_fixture["source_identity"],
                held_fixture["source_sha256"],
                os.fspath(source_evidence),
            ),
            "exact_process_argv_template": template,
            "exact_isolation_flags": ["-I", "-B", "-S"],
            "imports_exact": ["numpy", "matplotlib"],
            "environment_policy": {
                "keys": list(smoke.ENVIRONMENT_KEYS),
                "target": "HELD_SCRATCH_DIRFD_PROC_PATH",
                "scratch_must_be_precreated_empty": True,
                "global_no_write_claim": False,
            },
            "capability": smoke.CAPABILITY,
        }
        authorization_bytes = builder.canonical_json_bytes(authorization)
        authorization_sha = hashlib.sha256(authorization_bytes).hexdigest()
        authorization_path.write_bytes(authorization_bytes)
        os.chmod(authorization_path, 0o444)
        actual_cli = list(placeholder_cli)
        actual_cli[actual_cli.index(smoke.AUTH_SHA_PLACEHOLDER)] = authorization_sha

        result = smoke.run_held_smoke_child(
            held_fixture["source_identity"],
            held_fixture["source_sha256"],
            os.fspath(source_evidence),
            actual_cli,
            timeout_seconds=30,
            capture_limit_bytes=1024 * 1024,
        )
        evidence = json.loads(result["stdout"])
        details["success_evidence"] = evidence
        checks["linux_real_bootstrap_executes_held_fd198_after_original_path_replacement"] = (
            evidence["entry"] == "HELD_STUB_V1"
            and evidence["module_file"] == "/proc/self/fd/198"
            and evidence["source_path_matches_held"] is False
        )
        checks["linux_real_bootstrap_executes_held_fd197_after_interpreter_path_replacement"] = (
            evidence["argv0"] == "/proc/self/fd/197"
            and evidence["python_path_matches_held"] is False
        )
        checks["linux_real_bootstrap_child_fd_inheritance_and_cloexec_exact"] = (
            evidence["fd197_cloexec"] is False
            and evidence["fd198_cloexec"] is False
        )
        checks["linux_real_bootstrap_full_text_and_proc_argv_bound"] = (
            evidence["bootstrap_token_sha"] == smoke.HELD_SMOKE_BOOTSTRAP_SHA256
        )
        checks["linux_real_replaced_path_provenance_policy_explicit"] = (
            evidence["provenance_policy"]
            == "HELD_BYTES_EXECUTE;PRODUCTION_SOURCE_REJECTS_REPLACED_EVIDENCE_PATH"
        )

        actual_command = smoke.build_held_smoke_argv(
            held_fixture["source_identity"],
            held_fixture["source_sha256"],
            os.fspath(source_evidence),
            actual_cli,
        )
        inherited_before = os.get_inheritable(smoke.INTERPRETER_FD)
        source_inherited_before = os.get_inheritable(smoke.SMOKE_SOURCE_FD)
        try:
            os.set_inheritable(smoke.INTERPRETER_FD, True)
            os.set_inheritable(smoke.SMOKE_SOURCE_FD, False)
            cloexec_failure = subprocess.run(
                actual_command,
                executable="/proc/self/fd/197",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=False,
                timeout=15,
                check=False,
            )
        finally:
            os.set_inheritable(smoke.INTERPRETER_FD, inherited_before)
            os.set_inheritable(smoke.SMOKE_SOURCE_FD, source_inherited_before)
        checks["linux_real_bootstrap_rejects_cloexec_missing_fd198_before_compile"] = (
            cloexec_failure.returncode == 2
            and b"Bad file descriptor" in cloexec_failure.stderr
            and b"HELD_STUB_V1" not in cloexec_failure.stdout
        )
        details["cloexec_failure_returncode"] = cloexec_failure.returncode
    details["status"] = "PASS" if all(checks.values()) else "FAIL"
    return details["status"], checks, details


def args_fixture(base: Path) -> Namespace:
    decision_id = "smoke-v10-synthetic-decision"
    journal = base / f".result-free-transport-v10.{decision_id}"
    return Namespace(
        smoke_authorization=os.fspath(base / "smoke-authorization.json"),
        trusted_smoke_authorization_sha256=sha("smoke-authorization"),
        build_pass_receipt=os.fspath(journal / "TERMINAL.json"),
        trusted_build_pass_receipt_sha256=sha("build-pass-receipt"),
        expected_build_authorization_sha256=sha("build-authorization"),
        expected_build_commit_intent_sha256=sha("commit-intent"),
        final_root=os.fspath(base / "fixed-runtime-root"),
        expected_final_root_device=101,
        expected_final_root_inode=202,
        expected_runtime_manifest_sha256=sha("runtime-manifest"),
        expected_files_only_runtime_root_digest=sha("private-files"),
        expected_files_only_private_root_digest=sha("private-files"),
        expected_structural_private_tree_digest=sha("private-structural"),
        expected_files_only_full_root_digest=sha("full-root-files"),
        expected_structural_full_root_digest=sha("full-root-structural"),
        scratch_dir=os.fspath(base / "scratch"),
        expected_scratch_device=303,
        expected_scratch_inode=404,
        expected_empty_scratch_digest=smoke.EMPTY_SHA256,
        expected_python_sha256=sha("source-python"),
        expected_smoke_script_sha256=sha("smoke-script"),
        execute=smoke.CAPABILITY,
    )


def smoke_cli(args: Namespace) -> list[str]:
    return [
        "--smoke-authorization",
        args.smoke_authorization,
        "--trusted-smoke-authorization-sha256",
        args.trusted_smoke_authorization_sha256,
        "--build-pass-receipt",
        args.build_pass_receipt,
        "--trusted-build-pass-receipt-sha256",
        args.trusted_build_pass_receipt_sha256,
        "--expected-build-authorization-sha256",
        args.expected_build_authorization_sha256,
        "--expected-build-commit-intent-sha256",
        args.expected_build_commit_intent_sha256,
        "--final-root",
        args.final_root,
        "--expected-final-root-device",
        str(args.expected_final_root_device),
        "--expected-final-root-inode",
        str(args.expected_final_root_inode),
        "--expected-runtime-manifest-sha256",
        args.expected_runtime_manifest_sha256,
        "--expected-files-only-runtime-root-digest",
        args.expected_files_only_runtime_root_digest,
        "--expected-files-only-private-root-digest",
        args.expected_files_only_private_root_digest,
        "--expected-structural-private-tree-digest",
        args.expected_structural_private_tree_digest,
        "--expected-files-only-full-root-digest",
        args.expected_files_only_full_root_digest,
        "--expected-structural-full-root-digest",
        args.expected_structural_full_root_digest,
        "--scratch-dir",
        args.scratch_dir,
        "--expected-scratch-device",
        str(args.expected_scratch_device),
        "--expected-scratch-inode",
        str(args.expected_scratch_inode),
        "--expected-empty-scratch-digest",
        args.expected_empty_scratch_digest,
        "--expected-python-sha256",
        args.expected_python_sha256,
        "--expected-smoke-script-sha256",
        args.expected_smoke_script_sha256,
        "--execute",
        smoke.CAPABILITY,
    ]


def synthetic_smoke_source_identity() -> dict[str, Any]:
    return {
        "device": 1701,
        "inode": 1702,
        "size_bytes": 1703,
        "mtime_ns": 1704,
        "ctime_ns": 1705,
        "mode": "0444",
        "nlink": 1,
    }


def authorization_fixture(args: Namespace) -> tuple[dict[str, Any], list[str]]:
    source_python = "/opt/frozen-python/bin/python"
    smoke_script = "/opt/frozen-package/result_free_runtime_smoke_v10.py"
    command = smoke.build_held_smoke_argv(
        synthetic_smoke_source_identity(),
        args.expected_smoke_script_sha256,
        smoke_script,
        smoke_cli(args),
    )
    template = list(command)
    template[template.index(args.trusted_smoke_authorization_sha256)] = (
        smoke.AUTH_SHA_PLACEHOLDER
    )
    authorization = {
        "schema": smoke.AUTHORIZATION_SCHEMA,
        "status": smoke.AUTHORIZATION_STATUS,
        "decision_id": "smoke-v10-synthetic-decision",
        "scope": "RESULT_FREE_RUNTIME_LAYOUT_SMOKE_ONLY",
        "authority": {
            "runtime_layout_smoke_authorized": True,
            "scratch_write_authorized": True,
            "root_write_authorized": False,
            "transport_or_build_authorized": False,
            "controller_or_outer_main_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "deployment_or_resume_authorized": False,
        },
        "paths": {
            "smoke_authorization": args.smoke_authorization,
            "build_pass_receipt": args.build_pass_receipt,
            "final_root": args.final_root,
            "scratch_dir": args.scratch_dir,
            "source_python": source_python,
            "smoke_script": smoke_script,
        },
        "identities": {
            "final_root": {
                "device": args.expected_final_root_device,
                "inode": args.expected_final_root_inode,
            },
            "scratch": {
                "device": args.expected_scratch_device,
                "inode": args.expected_scratch_inode,
            },
        },
        "expected": {
            "build_pass_receipt_sha256": args.trusted_build_pass_receipt_sha256,
            "build_authorization_sha256": args.expected_build_authorization_sha256,
            "build_commit_intent_sha256": args.expected_build_commit_intent_sha256,
            "runtime_manifest_sha256": args.expected_runtime_manifest_sha256,
            "files_only_runtime_root_digest": args.expected_files_only_runtime_root_digest,
            "files_only_private_root_digest": args.expected_files_only_private_root_digest,
            "structural_private_tree_digest": args.expected_structural_private_tree_digest,
            "files_only_full_root_digest": args.expected_files_only_full_root_digest,
            "structural_full_root_digest": args.expected_structural_full_root_digest,
            "empty_scratch_inventory_digest": args.expected_empty_scratch_digest,
            "source_python_sha256": args.expected_python_sha256,
            "smoke_script_sha256": args.expected_smoke_script_sha256,
        },
        "bound_v8": {
            "directory_name": smoke.V8_DIRECTORY_NAME,
            "prepared_receipt_sha256": smoke.V8_PREPARED_RECEIPT_SHA256,
            "bundle_manifest_sha256": smoke.V8_BUNDLE_MANIFEST_SHA256,
            "sha256_index_sha256": smoke.V8_SHA256_INDEX_SHA256,
            "top_level_count": smoke.V8_TOP_LEVEL_COUNT,
            "indexed_count": smoke.V8_INDEXED_COUNT,
        },
        "held_byte_bootstrap": smoke.held_smoke_authorization_binding(
            synthetic_smoke_source_identity(),
            args.expected_smoke_script_sha256,
            smoke_script,
        ),
        "exact_process_argv_template": template,
        "exact_isolation_flags": ["-I", "-B", "-S"],
        "imports_exact": ["numpy", "matplotlib"],
        "environment_policy": {
            "keys": list(smoke.ENVIRONMENT_KEYS),
            "target": "HELD_SCRATCH_DIRFD_PROC_PATH",
            "scratch_must_be_precreated_empty": True,
            "global_no_write_claim": False,
        },
        "capability": smoke.CAPABILITY,
    }
    return authorization, command


def validated_authorization(
    authorization: dict[str, Any], args: Namespace, command: list[str]
) -> dict[str, Any]:
    smoke_start = 6 + 2 * len(smoke.HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS)
    bootstrap_context = {
        "smoke_source_identity": synthetic_smoke_source_identity(),
        "smoke_source_sha256": args.expected_smoke_script_sha256,
        "original_smoke_evidence_path": authorization["paths"]["smoke_script"],
    }
    return smoke.validate_smoke_authorization(
        authorization,
        args,
        command,
        bootstrap_context,
        command[smoke_start:],
    )


def core_receipt_fixture(args: Namespace, auth: dict[str, Any]) -> dict[str, Any]:
    v10_package = {
        "builder_sha256": sha("v10-builder"),
        "test_sha256": sha("v10-test"),
        "smoke_sha256": args.expected_smoke_script_sha256,
        "smoke_test_sha256": sha("v10-smoke-test"),
        "bundle_manifest_sha256": sha("v10-bundle-manifest"),
        "sha256_index_sha256": sha("v10-sha-index"),
        "prepared_receipt_sha256": sha("v10-prepared-receipt"),
    }
    build_authorization = {
        "decision_id": auth["decision_id"],
        "final_root": args.final_root,
        "source_python": auth["paths"]["source_python"],
        "source_python_sha256": args.expected_python_sha256,
        "source_site_packages": "/opt/frozen-python/lib/python3.12/site-packages",
        "source_bundle": {
            "path": f"/opt/frozen-bundles/{builder.V8_BINDING['directory_name']}",
            **builder.V8_BINDING,
        },
        "source_inventory": {
            "source_root_identity": {"device": 505, "inode": 606},
            "inventory_digest": sha("source-inventory"),
        },
        "logical_builder_argv": ["/opt/frozen-python/bin/python", "-I", "-B", "-S"],
        "journal": {
            "directory": os.fspath(Path(args.final_root).parent / ".result-free-transport-v10.smoke-v10-synthetic-decision"),
            "parent_path": os.fspath(Path(args.final_root).parent),
            "begin": os.fspath(Path(args.final_root).parent / ".result-free-transport-v10.smoke-v10-synthetic-decision" / "BEGIN.json"),
            "intent": os.fspath(Path(args.final_root).parent / ".result-free-transport-v10.smoke-v10-synthetic-decision" / "COMMIT_INTENT.json"),
            "terminal": args.build_pass_receipt,
            "lock": os.fspath(Path(args.final_root).parent / ".result-free-transport-v10.smoke-v10-synthetic-decision" / "LOCK"),
        },
        "bindings": {
            "v10_package": v10_package,
            "v10_builder_independent_audit": {
                "receipt_sha256": sha("v10-independent-audit")
            },
            "v9_builder_negative_independent_audit": {
                f"{stem}_sha256": digest
                for stem, digest in smoke.V9_NEGATIVE_QA_SHA256.items()
            },
            "v8_builder_negative_independent_audit": {
                f"{stem}_sha256": digest
                for stem, digest in smoke.V8_NEGATIVE_QA_SHA256.items()
            },
            "v7_builder_negative_independent_audit": {
                f"{stem}_sha256": digest
                for stem, digest in smoke.V7_NEGATIVE_QA_SHA256.items()
            },
        },
    }
    outer_process_argv = [
        "/opt/frozen-python/bin/python", "-I", "-B", "-S",
        "/opt/frozen-package/preflight-v2.py",
    ]
    trusted_launch = {
        "schema": builder.HELD_BUILDER_LAUNCH_SCHEMA,
        "status": builder.HELD_BUILDER_LAUNCH_STATUS,
        "method": builder.HELD_BUILDER_LAUNCH_METHOD,
        "interpreter_fd": builder.HELD_INTERPRETER_FD,
        "builder_source_fd": builder.HELD_BUILDER_SOURCE_FD,
        "interpreter_proc_path": f"/proc/self/fd/{builder.HELD_INTERPRETER_FD}",
        "builder_source_proc_path": f"/proc/self/fd/{builder.HELD_BUILDER_SOURCE_FD}",
        "interpreter_fd_inheritable": True,
        "builder_source_fd_inheritable": False,
        "interpreter_identity": {
            "device": 1301, "inode": 1302, "size_bytes": 1303,
            "mtime_ns": 1304, "mode": "0555", "nlink": 1,
        },
        "builder_source_identity": {
            "device": 1401, "inode": 1402, "size_bytes": 1403,
            "mtime_ns": 1404, "mode": "0444", "nlink": 1,
        },
        "interpreter_sha256": args.expected_python_sha256,
        "builder_source_sha256": v10_package["builder_sha256"],
        "builder_original_evidence_path": (
            "/opt/frozen-package/build_result_free_transport_runtime_v10.py"
        ),
        "outer_launch_receipt_path": "/opt/receipts/preflight-v2-launch.json",
        "outer_launch_receipt_sha256": sha("outer-launch-receipt"),
        "outer_process_argv": outer_process_argv,
        "outer_process_argv_sha256": builder.sha256_bytes(
            builder.canonical_json_bytes(outer_process_argv)
        ),
        "root_launch_authorization_path": "/opt/authorizations/root-launch-v2.json",
        "root_launch_authorization_sha256": sha("root-launch-v2"),
        "preflight_package_manifest_path": "/opt/preflight-v2/BUNDLE_MANIFEST.json",
        "preflight_package_manifest_sha256": sha("preflight-v2-manifest"),
        "preflight_package_index_path": "/opt/preflight-v2/SHA256SUMS",
        "preflight_package_index_sha256": sha("preflight-v2-index"),
        "preflight_independent_audit_receipt_path": "/opt/preflight-v2-qa/INDEPENDENT_QA_RECEIPT.json",
        "preflight_independent_audit_receipt_sha256": sha("preflight-v2-qa-receipt"),
        "preflight_independent_audit_index_path": "/opt/preflight-v2-qa/SHA256SUMS",
        "preflight_independent_audit_index_sha256": sha("preflight-v2-qa-index"),
    }
    build_authorization["trusted_launch"] = trusted_launch
    evidence = {
        "runtime_manifest_sha256": args.expected_runtime_manifest_sha256,
        "files_only_runtime_root_digest": args.expected_files_only_runtime_root_digest,
        "files_only_private_root_digest": args.expected_files_only_private_root_digest,
        "structural_private_tree_digest": args.expected_structural_private_tree_digest,
        "support_files": {
            name: {
                "path": os.fspath(Path(args.final_root) / name),
                "device": 2000 + index,
                "inode": 3000 + index,
                "sha256": smoke.SUPPORT_SHA256[name],
                "size_bytes": 4000 + index,
            }
            for index, name in enumerate(sorted(smoke.SUPPORT_SHA256))
        },
        "external_record_exclusions": builder.external_record_exclusion_evidence(),
    }
    intent = {
        "core": {
            "build": evidence,
            "receipt_created_utc": "2026-08-22T15:30:00Z",
            "authorization_path": "/opt/authorizations/build-v10.json",
            "authorization_sha256": args.expected_build_authorization_sha256,
            "logical_builder_argv": list(build_authorization["logical_builder_argv"]),
            "trusted_launch": trusted_launch,
            "begin_sha256": sha("begin"),
            "staging_identity": {
                "device": args.expected_final_root_device,
                "inode": args.expected_final_root_inode,
            },
            "private_identity": {"device": 707, "inode": 808},
            "bundle_identity": {"device": 809, "inode": 810},
            "journal_identity": {"device": 909, "inode": 1001},
            "lock_identity": {"device": 1002, "inode": 1003},
            "parent_identity": {"device": 1102, "inode": 1203},
            "linux_integration": "PASS_LINUX_RENAMEAT2_NOREPLACE",
            "terminal_publication_method": (
                builder.PRODUCTION_TERMINAL_PUBLICATION_METHOD
            ),
            "terminal_canonical_visibility_rule": (
                builder.TERMINAL_CANONICAL_VISIBILITY_RULE
            ),
        }
    }
    publish = {
        "final_root_identity": {
            "device": args.expected_final_root_device,
            "inode": args.expected_final_root_inode,
        },
        "final_inode_equals_staging": True,
        "files_only_full_root_digest": args.expected_files_only_full_root_digest,
        "structural_full_root_digest": args.expected_structural_full_root_digest,
    }
    return builder.build_pass_receipt(
        build_authorization,
        intent,
        args.expected_build_commit_intent_sha256,
        publish,
    )


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="runtime-smoke-v10-synthetic-") as raw:
        # macOS spells the temporary root through /var -> /private/var.
        # Resolve that platform alias so the nofollow absolute-path helper is
        # tested against a component chain containing no symlink.
        base = Path(raw).resolve()
        args = args_fixture(base)
        authorization, command = authorization_fixture(args)

        checks["builder_smoke_top_schema_constants_exact"] = (
            smoke.BUILD_RECEIPT_TOP_KEYS == builder.BUILD_RECEIPT_TOP_KEYS
        )
        checks["builder_smoke_nested_schema_constants_exact"] = (
            smoke.BUILD_RECEIPT_NESTED_KEYS == builder.BUILD_RECEIPT_NESTED_KEYS
        )
        checks["builder_smoke_support_schema_constants_exact"] = (
            smoke.SUPPORT_FILE_RECEIPT_KEYS == builder.SUPPORT_FILE_RECEIPT_KEYS
        )
        checks["builder_smoke_digest_algorithm_constants_exact"] = (
            smoke.FILES_ONLY_DIGEST_ALGORITHM == builder.FILES_ONLY_DIGEST_ALGORITHM
            and smoke.STRUCTURAL_DIGEST_ALGORITHM == builder.STRUCTURAL_DIGEST_ALGORITHM
        )
        checks["builder_smoke_terminal_publication_constants_exact"] = (
            smoke.TERMINAL_PUBLICATION_METHOD
            == builder.PRODUCTION_TERMINAL_PUBLICATION_METHOD
            and smoke.TERMINAL_CANONICAL_VISIBILITY_RULE
            == builder.TERMINAL_CANONICAL_VISIBILITY_RULE
        )
        elf_dynamic = smoke.parse_elf64_dynamic_bytes(
            minimal_elf64_dynamic_fixture(), "synthetic ELF dynamic fixture"
        )
        checks["elf64_dynamic_parser_binds_needed_soname_and_origin_runpath"] = (
            elf_dynamic["machine"] == 62
            and elf_dynamic["needed"] == ["libdep.so"]
            and elf_dynamic["soname"] == "libconsumer.so"
            and elf_dynamic["runpath"] == ["$ORIGIN/deps"]
        )
        checks["elf64_dynamic_duplicate_dt_strtab_rejected"] = rejected(
            lambda: smoke.parse_elf64_dynamic_bytes(
                elf_fixture_with_duplicate_dynamic_tag(
                    smoke.DT_STRTAB, 0x400300
                ),
                "duplicate DT_STRTAB fixture",
            )
        )
        checks["elf64_dynamic_duplicate_dt_strsz_rejected"] = rejected(
            lambda: smoke.parse_elf64_dynamic_bytes(
                elf_fixture_with_duplicate_dynamic_tag(smoke.DT_STRSZ, 48),
                "duplicate DT_STRSZ fixture",
            )
        )
        checks["elf64_dynamic_strtab_beyond_pt_load_file_backing_rejected"] = (
            rejected(
                lambda: smoke.parse_elf64_dynamic_bytes(
                    elf_fixture_strtab_exceeds_file_backed_load(
                        trailing_load=False
                    ),
                    "strtab exceeds file-backed PT_LOAD fixture",
                )
            )
        )
        checks["elf64_dynamic_strtab_cross_segment_rejected"] = rejected(
            lambda: smoke.parse_elf64_dynamic_bytes(
                elf_fixture_strtab_exceeds_file_backed_load(
                    trailing_load=True
                ),
                "strtab crosses PT_LOAD segments fixture",
            )
        )
        nested_origin_search = smoke._validate_private_elf_search_paths(
            "numpy/_core/x.so",
            {"rpath": [], "runpath": ["$ORIGIN/../../numpy.libs"]},
        )
        checks["origin_search_path_nested_parent_normalizes_inside_private_root"] = (
            nested_origin_search
            == [
                {
                    "tag": "runpath",
                    "raw": "$ORIGIN/../../numpy.libs",
                    "resolved_private_relative_directory": "numpy.libs",
                }
            ]
        )
        checks["origin_search_path_escape_above_private_root_rejected"] = rejected(
            lambda: smoke._validate_private_elf_search_paths(
                "numpy/_core/x.so",
                {"rpath": [], "runpath": ["$ORIGIN/../../../escape"]},
            )
        )
        smoke_source = (HERE / "result_free_runtime_smoke_v10.py").read_text(
            encoding="utf-8"
        )
        checks["private_dso_preload_and_maps_contract_present"] = (
            'f"/proc/self/fd/{self.private_fd}/{relative}"' in smoke_source
            and "RTLD_NOW" in smoke_source
            and "RTLD_GLOBAL" in smoke_source
            and "read_proc_self_maps" in smoke_source
            and "same_soname_external_escape_absent" in smoke_source
            and "system_dependency_providers" in smoke_source
        )
        checks["regular_inode_inotify_watch_contract_present"] = (
            'f"/proc/self/fd/{file_fd}"' in smoke_source
            and "INOTIFY_REGULAR_FILE_WATCH_MASK" in smoke_source
            and "self._add_regular_file(child, child_relative)" in smoke_source
            and "regular inode changed while its watch was installed" in smoke_source
        )
        checks["inotify_claim_is_mask_scoped_with_digest_inventory_backstops"] = (
            "explicitly configured failure masks" in smoke_source
            and "without claiming that every" in smoke_source
            and "possible inotify event is observable" in smoke_source
            and "full ROOT and" in smoke_source
            and "private inventories are recomputed at the end" in smoke_source
            and "fails if any create/delete/move/modify/attrib/overflow event races setup"
            not in smoke_source
        )
        checks["inotify_known_masks_are_exact"] = (
            smoke.INOTIFY_FAILURE_MASK == 0x0000EFCE
            and smoke.INOTIFY_DIRECTORY_WATCH_MASK == 0x07000FCE
            and smoke.INOTIFY_REGULAR_FILE_WATCH_MASK == 0x00002C0E
        )
        synthetic_mask_guard = object.__new__(smoke.RecursiveInotifyGuard)
        synthetic_mask_guard.events = lambda: [
            {"mask": smoke.IN_ACCESS, "watch": {}, "cookie": 0, "name": ""}
        ]
        access_only_is_clean = True
        try:
            synthetic_mask_guard.assert_clean("synthetic unconfigured access event")
        except smoke.SmokeError:
            access_only_is_clean = False
        synthetic_mask_guard.events = lambda: [
            {"mask": smoke.IN_MODIFY, "watch": {}, "cookie": 0, "name": ""}
        ]
        checks["inotify_failure_filter_is_mask_scoped_not_any_event"] = (
            access_only_is_clean
            and rejected(
                lambda: synthetic_mask_guard.assert_clean(
                    "synthetic configured modify event"
                )
            )
        )
        inotify_failure_file = base / "inotify-add-failure.bin"
        inotify_failure_file.write_bytes(b"held bytes\n")
        inotify_failure_fd = os.open(inotify_failure_file, os.O_RDONLY)
        synthetic_inotify_guard = object.__new__(smoke.RecursiveInotifyGuard)
        synthetic_inotify_guard.fd = -1
        synthetic_inotify_guard._add = lambda *_args: -1
        synthetic_inotify_guard.watch_table = {}
        try:
            checks["regular_inode_inotify_add_failure_is_fail_closed"] = rejected(
                lambda: synthetic_inotify_guard._add_regular_file(
                    inotify_failure_fd, "inotify-add-failure.bin"
                )
            )
        finally:
            os.close(inotify_failure_fd)
        synthetic_closure = object.__new__(smoke.PrivateElfClosure)
        synthetic_closure.entries = {
            "private.libs/libdep.so": {
                "identity": {"device": 11, "inode": 22},
                "sha256": sha("private-libdep"),
                "dynamic": {
                    "soname": "libdep.so",
                    "needed": [],
                    "rpath": [],
                    "runpath": [],
                },
            }
        }
        synthetic_closure.alias_to_relative = {
            "libdep.so": "private.libs/libdep.so"
        }
        synthetic_closure.preload_paths = ["private.libs/libdep.so"]
        synthetic_closure.baseline_identities = {(33, 44)}
        synthetic_closure.private_root_realpath = Path("/held")
        synthetic_maps = [
            {
                "device": 11,
                "inode": 22,
                "paths": ["/held/private.libs/libdep.so"],
                "deleted": False,
                "segments": [
                    {
                        "start": "1",
                        "end": "2",
                        "permissions": "r-xp",
                        "offset": "0",
                    }
                ],
            },
            {
                "device": 33,
                "inode": 44,
                "paths": ["/usr/lib64/libdep.so"],
                "deleted": False,
                "segments": [
                    {
                        "start": "3",
                        "end": "4",
                        "permissions": "r-xp",
                        "offset": "0",
                    }
                ],
            },
        ]
        original_maps_reader = smoke.read_proc_self_maps
        original_mapped_metadata = smoke._mapped_elf_metadata
        smoke.read_proc_self_maps = lambda: synthetic_maps
        smoke._mapped_elf_metadata = lambda _mapping: {
            "soname": "libdep.so",
            "needed": [],
            "rpath": [],
            "runpath": [],
        }
        try:
            synthetic_closure.baseline_maps = synthetic_maps
            checks["same_soname_baseline_external_preload_is_rejected_before_preload"] = (
                rejected(synthetic_closure._reject_baseline_private_alias_conflicts)
            )
            checks["same_soname_external_preload_is_rejected"] = rejected(
                lambda: synthetic_closure.verify_mappings(
                    {"private.libs/libdep.so"}, "synthetic same-SONAME collision"
                )
            )
        finally:
            smoke.read_proc_self_maps = original_maps_reader
            smoke._mapped_elf_metadata = original_mapped_metadata

        checks["strict_json_valid_object"] = smoke.strict_json_bytes(
            b'{"a":1,"b":false}', "valid"
        ) == {"a": 1, "b": False}
        checks["strict_json_duplicate_key_rejected"] = rejected(
            lambda: smoke.strict_json_bytes(b'{"a":1,"a":2}', "duplicate")
        )
        checks["strict_json_nan_rejected"] = rejected(
            lambda: smoke.strict_json_bytes(b'{"a":NaN}', "nan")
        )
        checks["strict_json_float_rejected"] = rejected(
            lambda: smoke.strict_json_bytes(b'{"a":1.25}', "float")
        )
        checks["strict_json_null_rejected"] = rejected(
            lambda: smoke.strict_json_bytes(b'{"a":null}', "null")
        )
        checks["json_bool_is_not_integer"] = rejected(
            lambda: smoke.exact_int(True, "bool-as-int")
        )

        validated = validated_authorization(authorization, args, command)
        checks["exact_authorization_and_placeholder_accept"] = (
            validated["decision_id"] == authorization["decision_id"]
        )
        duplicate_placeholder = copy.deepcopy(authorization)
        duplicate_placeholder["exact_process_argv_template"].append(
            smoke.AUTH_SHA_PLACEHOLDER
        )
        checks["duplicate_self_sha_placeholder_rejected"] = rejected(
            lambda: validated_authorization(duplicate_placeholder, args, command)
        )
        wrong_actual = list(command)
        wrong_actual[wrong_actual.index(args.trusted_smoke_authorization_sha256)] = sha(
            "wrong-auth-sha"
        )
        checks["wrong_actual_self_sha_slot_rejected"] = rejected(
            lambda: validated_authorization(authorization, args, wrong_actual)
        )
        bool_identity = copy.deepcopy(authorization)
        bool_identity["identities"]["final_root"]["device"] = True
        checks["authorization_identity_bool_confusion_rejected"] = rejected(
            lambda: validated_authorization(bool_identity, args, command)
        )
        wrong_receipt_binding = copy.deepcopy(authorization)
        wrong_receipt_binding["expected"]["build_pass_receipt_sha256"] = sha(
            "other-receipt"
        )
        checks["authorization_build_receipt_sha_tamper_rejected"] = rejected(
            lambda: validated_authorization(wrong_receipt_binding, args, command)
        )

        checks["held_bootstrap_text_compiles_and_frozen_sha_matches"] = (
            compile(
                smoke.HELD_SMOKE_BOOTSTRAP_TEXT,
                "<held-smoke-bootstrap>",
                "exec",
                dont_inherit=True,
                optimize=0,
            ) is not None
            and len(smoke.HELD_SMOKE_BOOTSTRAP_TEXT.encode("utf-8")) == 12667
            and hashlib.sha256(
                smoke.HELD_SMOKE_BOOTSTRAP_TEXT.encode("utf-8")
            ).hexdigest() == smoke.HELD_SMOKE_BOOTSTRAP_SHA256
        )
        checks["ordwr_guard_is_present_at_bootstrap_spawn_and_authenticated_gates"] = (
            "fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY"
            in smoke.HELD_SMOKE_BOOTSTRAP_TEXT
            and "require_readonly_fd(fd, f\"held {label} before spawn\")"
            in inspect.getsource(smoke.run_held_smoke_child)
            and "require_readonly_fd(fd, f\"held {label}\")"
            in inspect.getsource(smoke._validate_held_bootstrap_context)
        )
        readonly_probe_path = base / "fd-access-mode-probe.bin"
        readonly_probe_path.write_bytes(b"held-fd-access-mode-probe")
        readonly_probe_fd = os.open(readonly_probe_path, os.O_RDONLY)
        readwrite_probe_fd = os.open(readonly_probe_path, os.O_RDWR)
        try:
            checks["held_fd_access_mode_helper_accepts_only_o_rdonly"] = (
                smoke.require_readonly_fd(
                    readonly_probe_fd, "synthetic readonly probe"
                ) & os.O_ACCMODE == os.O_RDONLY
                and rejected(lambda: smoke.require_readonly_fd(
                    readwrite_probe_fd, "synthetic readwrite probe"
                ))
            )
        finally:
            os.close(readwrite_probe_fd)
            os.close(readonly_probe_fd)
        smoke_start = 6 + 2 * len(smoke.HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS)
        checks["held_bootstrap_exact_prefix_envelope_and_smoke_suffix"] = (
            command[:6] == [
                "/proc/self/fd/197", "-I", "-B", "-S", "-c",
                smoke.HELD_SMOKE_BOOTSTRAP_TEXT,
            ]
            and command[6:smoke_start:2]
            == list(smoke.HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS)
            and command[smoke_start:] == smoke_cli(args)
            and not any(
                token.startswith(flag + "=")
                for token in command
                for flag in smoke.HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS
            )
        )
        parsed_cli = smoke._parse_cli(smoke_cli(args))
        checks["smoke_cli_exact_separate_tokens_accept"] = (
            parsed_cli.final_root == args.final_root
            and parsed_cli.execute == smoke.CAPABILITY
        )
        inline_cli = smoke_cli(args)
        inline_index = inline_cli.index("--final-root")
        inline_cli[inline_index:inline_index + 2] = [
            f"--final-root={args.final_root}"
        ]
        checks["smoke_cli_inline_equals_rejected"] = cli_rejected(inline_cli)
        duplicate_cli = [
            *smoke_cli(args), "--final-root", args.final_root
        ]
        checks["smoke_cli_duplicate_security_option_rejected"] = cli_rejected(
            duplicate_cli
        )
        prefix_cli = [*smoke_cli(args), "--final-root-extra", args.final_root]
        checks["smoke_cli_prefix_collision_rejected"] = cli_rejected(prefix_cli)
        checks["smoke_cli_bare_double_dash_rejected"] = cli_rejected(
            [*smoke_cli(args), "--"]
        )
        wrong_bootstrap_contract = copy.deepcopy(authorization)
        wrong_bootstrap_contract["held_byte_bootstrap"]["contract"][
            "bootstrap_sha256"
        ] = sha("wrong-bootstrap")
        checks["authorization_bootstrap_sha_tamper_rejected"] = rejected(
            lambda: validated_authorization(
                wrong_bootstrap_contract, args, command
            )
        )
        wrong_source_identity = copy.deepcopy(authorization)
        wrong_source_identity["held_byte_bootstrap"]["smoke_source_identity"][
            "inode"
        ] += 1
        checks["authorization_held_source_inode_replacement_rejected"] = rejected(
            lambda: validated_authorization(wrong_source_identity, args, command)
        )
        bool_source_identity = copy.deepcopy(authorization)
        bool_source_identity["held_byte_bootstrap"]["smoke_source_identity"][
            "nlink"
        ] = True
        checks["authorization_held_source_bool_confusion_rejected"] = rejected(
            lambda: validated_authorization(bool_source_identity, args, command)
        )
        wrong_bootstrap_token = list(command)
        wrong_bootstrap_token[5] += "\n# tampered"
        checks["actual_bootstrap_text_token_tamper_rejected"] = rejected(
            lambda: validated_authorization(
                authorization, args, wrong_bootstrap_token
            )
        )
        wrong_interpreter_prefix = list(command)
        wrong_interpreter_prefix[0] = authorization["paths"]["source_python"]
        checks["pathname_interpreter_prefix_rejected"] = rejected(
            lambda: validated_authorization(
                authorization, args, wrong_interpreter_prefix
            )
        )
        checks["bootstrap_inline_envelope_form_rejected_by_builder"] = rejected(
            lambda: smoke.build_held_smoke_argv(
                synthetic_smoke_source_identity(),
                args.expected_smoke_script_sha256,
                authorization["paths"]["smoke_script"],
                [*smoke_cli(args), "--held-smoke-source-fd=198"],
            )
        )
        checks["bootstrap_duplicate_envelope_flag_rejected_by_builder"] = rejected(
            lambda: smoke.build_held_smoke_argv(
                synthetic_smoke_source_identity(),
                args.expected_smoke_script_sha256,
                authorization["paths"]["smoke_script"],
                [*smoke_cli(args), "--held-smoke-source-fd", "198"],
            )
        )
        checks["direct_main_rejected_before_smoke_body"] = rejected(smoke.main)
        original_smoke_sys = smoke.sys
        original_platform_check_for_entry = smoke._require_platform_fd_features
        original_context_validator = smoke._validate_held_bootstrap_context
        original_authenticated_body = smoke._authenticated_smoke_main
        body_called = False

        def body_sentinel(*_args: Any, **_kwargs: Any) -> int:
            nonlocal body_called
            body_called = True
            return 0

        smoke.sys = Namespace(
            flags=Namespace(isolated=1, no_site=1, dont_write_bytecode=1)
        )
        smoke._require_platform_fd_features = lambda: None
        smoke._validate_held_bootstrap_context = (
            lambda *_a, **_k: (_ for _ in ()).throw(
                smoke.SmokeError("synthetic bootstrap context rejection")
            )
        )
        smoke._authenticated_smoke_main = body_sentinel
        try:
            invalid_context_rejected = rejected(
                lambda: smoke.held_byte_bootstrap_main(smoke_cli(args), {})
            )
        finally:
            smoke.sys = original_smoke_sys
            smoke._require_platform_fd_features = original_platform_check_for_entry
            smoke._validate_held_bootstrap_context = original_context_validator
            smoke._authenticated_smoke_main = original_authenticated_body
        checks["invalid_bootstrap_context_rejected_before_smoke_body"] = (
            invalid_context_rejected and body_called is False
        )
        direct_stderr = io.StringIO()
        direct_exit: int | None = None
        try:
            with contextlib.redirect_stderr(direct_stderr):
                runpy.run_path(
                    os.fspath(HERE / "result_free_runtime_smoke_v10.py"),
                    run_name="__main__",
                )
        except SystemExit as exc:
            direct_exit = exc.code
        checks["direct_path___main___fails_closed_before_cli_or_body"] = (
            direct_exit == 2
            and "direct pathname smoke execution is forbidden"
            in direct_stderr.getvalue()
        )
        held_auth_path = base / "held-auth-entry.json"
        held_auth_path.write_bytes(b'{"held":true}\n')
        os.chmod(held_auth_path, 0o444)
        held_auth_fd = os.open(held_auth_path, os.O_RDONLY)
        held_auth_backup = base / "held-auth-preserved.json"
        os.rename(held_auth_path, held_auth_backup)
        replaced_auth_bytes = b'{"path":"replacement"}\n'
        held_auth_path.write_bytes(replaced_auth_bytes)
        os.chmod(held_auth_path, 0o444)
        held_auth_args = copy.copy(args)
        held_auth_args.smoke_authorization = os.fspath(held_auth_path)
        held_auth_args.trusted_smoke_authorization_sha256 = hashlib.sha256(
            replaced_auth_bytes
        ).hexdigest()
        held_auth_context = {
            "authorization_fd": held_auth_fd,
            "authorization_identity": smoke._stat_full_file_identity(
                os.fstat(held_auth_fd)
            ),
        }
        original_platform_check = smoke._require_platform_fd_features
        smoke._require_platform_fd_features = lambda: None
        held_auth_error = ""
        try:
            try:
                smoke._authenticated_smoke_main(
                    held_auth_args, [], [], held_auth_context
                )
            except smoke.SmokeError as exc:
                held_auth_error = str(exc)
        finally:
            smoke._require_platform_fd_features = original_platform_check
            os.close(held_auth_fd)
        checks["authenticated_body_consumes_bootstrap_held_auth_fd_not_replaced_path"] = (
            held_auth_error == "single-open smoke authorization SHA mismatch"
        )

        held_source_path = base / "held-smoke-source.py"
        held_source_path.write_bytes(b"# synthetic held smoke source\n")
        os.chmod(held_source_path, 0o444)
        interpreter_path = Path(os.path.realpath(sys.executable))
        with installed_fixed_held_fds(
            interpreter_path, held_source_path
        ) as held_fixture:
            held_args = copy.copy(args)
            held_args.expected_python_sha256 = held_fixture["interpreter_sha256"]
            held_args.expected_smoke_script_sha256 = held_fixture["source_sha256"]
            held_cli = smoke_cli(held_args)
            popen_calls: list[dict[str, Any]] = []

            def inheritance_popen(
                requested_command: list[str], **kwargs: Any
            ) -> subprocess.Popen[bytes]:
                popen_calls.append({"command": requested_command, **kwargs})
                child_code = (
                    "import fcntl,json,os;"
                    "print(json.dumps({'fd197':os.fstat(197).st_ino,"
                    "'fd198':os.fstat(198).st_ino,"
                    "'cloexec197':bool(fcntl.fcntl(197,fcntl.F_GETFD)&fcntl.FD_CLOEXEC),"
                    "'cloexec198':bool(fcntl.fcntl(198,fcntl.F_GETFD)&fcntl.FD_CLOEXEC)}))"
                )
                return subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    stdin=kwargs["stdin"],
                    stdout=kwargs["stdout"],
                    stderr=kwargs["stderr"],
                    close_fds=kwargs["close_fds"],
                    pass_fds=kwargs["pass_fds"],
                    shell=False,
                    env=kwargs["env"],
                    text=False,
                    start_new_session=False,
                )

            parent_inheritable_before = (
                os.get_inheritable(smoke.INTERPRETER_FD),
                os.get_inheritable(smoke.SMOKE_SOURCE_FD),
            )
            child_result = smoke.run_held_smoke_child(
                held_fixture["source_identity"],
                held_fixture["source_sha256"],
                os.fspath(held_source_path),
                held_cli,
                timeout_seconds=10,
                capture_limit_bytes=65536,
                _popen_factory=inheritance_popen,
            )
            child_evidence = json.loads(child_result["stdout"])
            parent_inheritable_after = (
                os.get_inheritable(smoke.INTERPRETER_FD),
                os.get_inheritable(smoke.SMOKE_SOURCE_FD),
            )
            checks["spawn_helper_exact_executable_flags_and_pass_fds"] = (
                len(popen_calls) == 1
                and popen_calls[0]["command"][:6]
                == [
                    "/proc/self/fd/197", "-I", "-B", "-S", "-c",
                    smoke.HELD_SMOKE_BOOTSTRAP_TEXT,
                ]
                and popen_calls[0]["executable"] == "/proc/self/fd/197"
                and popen_calls[0]["pass_fds"] == (197, 198)
                and popen_calls[0]["close_fds"] is True
                and popen_calls[0]["shell"] is False
            )
            checks["spawn_helper_child_inherits_only_explicit_held_fds_without_cloexec"] = (
                child_evidence["fd197"] == os.fstat(197).st_ino
                and child_evidence["fd198"] == os.fstat(198).st_ino
                and child_evidence["cloexec197"] is False
                and child_evidence["cloexec198"] is False
                and parent_inheritable_before == (False, False)
                and parent_inheritable_after == parent_inheritable_before
            )
            fake_timeout = FakeTimeoutProcess()
            timeout_error = rejected(
                lambda: smoke.run_held_smoke_child(
                    held_fixture["source_identity"],
                    held_fixture["source_sha256"],
                    os.fspath(held_source_path),
                    held_cli,
                    timeout_seconds=1,
                    capture_limit_bytes=1024,
                    _popen_factory=lambda *_a, **_k: fake_timeout,
                    _selector_factory=ImmediateTimeoutSelector,
                    _monotonic=lambda: 0.0,
                )
            )
            checks["spawn_helper_timeout_kills_and_waits_owned_child"] = (
                timeout_error and fake_timeout.killed and fake_timeout.waited
            )
            fake_overflow = FakeTimeoutProcess()
            os.write(fake_overflow.stdout_write, b"x" * 65)
            os.close(fake_overflow.stdout_write)
            os.close(fake_overflow.stderr_write)
            overflow_error = rejected(
                lambda: smoke.run_held_smoke_child(
                    held_fixture["source_identity"],
                    held_fixture["source_sha256"],
                    os.fspath(held_source_path),
                    held_cli,
                    timeout_seconds=10,
                    capture_limit_bytes=64,
                    _popen_factory=lambda *_a, **_k: fake_overflow,
                )
            )
            checks["spawn_helper_capture_limit_kills_and_waits_owned_child"] = (
                overflow_error and fake_overflow.killed and fake_overflow.waited
            )
            checks["spawn_helper_spawn_exception_fails_closed"] = rejected(
                lambda: smoke.run_held_smoke_child(
                    held_fixture["source_identity"],
                    held_fixture["source_sha256"],
                    os.fspath(held_source_path),
                    held_cli,
                    timeout_seconds=10,
                    _popen_factory=lambda *_a, **_k: (_ for _ in ()).throw(
                        OSError("synthetic spawn failure")
                    ),
                )
            )
            saved_source_fd = os.dup(smoke.SMOKE_SOURCE_FD)
            os.close(smoke.SMOKE_SOURCE_FD)
            try:
                missing_fd_rejected = rejected(
                    lambda: smoke.run_held_smoke_child(
                        held_fixture["source_identity"],
                        held_fixture["source_sha256"],
                        os.fspath(held_source_path),
                        held_cli,
                        timeout_seconds=10,
                        _popen_factory=inheritance_popen,
                    )
                )
            finally:
                os.dup2(
                    saved_source_fd, smoke.SMOKE_SOURCE_FD, inheritable=False
                )
                os.close(saved_source_fd)
            checks["spawn_helper_missing_fixed_fd_rejected_before_spawn"] = (
                missing_fd_rejected and len(popen_calls) == 1
            )
            odrdw_popen_calls: list[dict[str, Any]] = []

            def odrdw_forbidden_popen(*_args: Any, **kwargs: Any) -> Any:
                odrdw_popen_calls.append(dict(kwargs))
                raise AssertionError("O_RDWR held FD reached Popen")

            def authenticated_ordwr_error(authenticated_args: Namespace) -> str:
                original_name = smoke.__name__
                original_file = smoke.__file__
                inheritable = {
                    fd: os.get_inheritable(fd)
                    for fd in (smoke.INTERPRETER_FD, smoke.SMOKE_SOURCE_FD)
                }
                context = {
                    "protocol": smoke.HELD_SMOKE_BOOTSTRAP_PROTOCOL,
                    "interpreter_fd": smoke.INTERPRETER_FD,
                    "smoke_source_fd": smoke.SMOKE_SOURCE_FD,
                    "interpreter_identity": smoke._stat_full_file_identity(
                        os.fstat(smoke.INTERPRETER_FD)
                    ),
                    "interpreter_sha256": authenticated_args.expected_python_sha256,
                    "smoke_source_identity": smoke._stat_full_file_identity(
                        os.fstat(smoke.SMOKE_SOURCE_FD)
                    ),
                    "smoke_source_sha256": (
                        authenticated_args.expected_smoke_script_sha256
                    ),
                    "original_smoke_evidence_path": os.fspath(held_source_path),
                    "bootstrap_sha256": smoke.HELD_SMOKE_BOOTSTRAP_SHA256,
                    "actual_cmdline": [],
                    "authorization_fd": 0,
                    "authorization_identity": {},
                    "authorization_sha256": "0" * 64,
                }
                try:
                    smoke.__name__ = "_result_free_runtime_smoke_v10_held_bytes__"
                    smoke.__file__ = f"/proc/self/fd/{smoke.SMOKE_SOURCE_FD}"
                    for fd in inheritable:
                        os.set_inheritable(fd, True)
                    try:
                        smoke._validate_held_bootstrap_context(
                            smoke_cli(authenticated_args), context, authenticated_args
                        )
                    except smoke.SmokeError as exc:
                        return str(exc)
                    return ""
                finally:
                    for fd, value in inheritable.items():
                        os.set_inheritable(fd, value)
                    smoke.__file__ = original_file
                    smoke.__name__ = original_name

            bootstrap_prefix = smoke.HELD_SMOKE_BOOTSTRAP_TEXT.split(
                "afd=-1\ntry:", 1
            )[0]
            bootstrap_namespace: dict[str, Any] = {}
            exec(
                compile(
                    bootstrap_prefix,
                    "<held-smoke-bootstrap-ordwr-gate>",
                    "exec",
                    dont_inherit=True,
                ),
                bootstrap_namespace,
                bootstrap_namespace,
            )
            bootstrap_ordwr_guard = next(
                line.strip()
                for line in smoke.HELD_SMOKE_BOOTSTRAP_TEXT.splitlines()
                if "F_GETFL)&os.O_ACCMODE!=os.O_RDONLY" in line
            )
            bootstrap_ordwr_guard_code = compile(
                bootstrap_ordwr_guard,
                "<held-smoke-bootstrap-exact-ordwr-guard>",
                "exec",
                dont_inherit=True,
            )

            def bootstrap_ordwr_rejected(fd: int, label: str) -> bool:
                with contextlib.redirect_stderr(io.StringIO()):
                    try:
                        bootstrap_namespace["fd"] = fd
                        bootstrap_namespace["label"] = label
                        exec(
                            bootstrap_ordwr_guard_code,
                            bootstrap_namespace,
                            bootstrap_namespace,
                        )
                    except SystemExit:
                        return True
                return False

            odrdw_source_path = base / "held-smoke-source-ordwr.py"
            odrdw_source_path.write_bytes(b"# synthetic O_RDWR held smoke source\n")
            odrdw_source_fd = os.open(odrdw_source_path, os.O_RDWR)
            os.chmod(odrdw_source_path, 0o444)
            saved_fixed_source_fd = os.dup(smoke.SMOKE_SOURCE_FD)
            try:
                os.dup2(
                    odrdw_source_fd, smoke.SMOKE_SOURCE_FD, inheritable=False
                )
                odrdw_source_info = os.fstat(smoke.SMOKE_SOURCE_FD)
                odrdw_source_identity = smoke._stat_full_file_identity(
                    odrdw_source_info
                )
                odrdw_source_sha = hashlib.sha256(
                    odrdw_source_path.read_bytes()
                ).hexdigest()
                odrdw_source_args = copy.copy(held_args)
                odrdw_source_args.expected_smoke_script_sha256 = odrdw_source_sha
                odrdw_source_rejected = rejected(
                    lambda: smoke.run_held_smoke_child(
                        odrdw_source_identity,
                        odrdw_source_sha,
                        os.fspath(odrdw_source_path),
                        smoke_cli(odrdw_source_args),
                        timeout_seconds=10,
                        _popen_factory=odrdw_forbidden_popen,
                    )
                )
                authenticated_source_ordwr_error = authenticated_ordwr_error(
                    odrdw_source_args
                )
                bootstrap_source_ordwr_rejected = bootstrap_ordwr_rejected(
                    smoke.SMOKE_SOURCE_FD, "smoke source"
                )
            finally:
                os.dup2(
                    saved_fixed_source_fd,
                    smoke.SMOKE_SOURCE_FD,
                    inheritable=False,
                )
                os.close(saved_fixed_source_fd)
                os.close(odrdw_source_fd)
            checks["spawn_path_rejects_fd198_ordwr_before_popen"] = (
                odrdw_source_rejected and odrdw_popen_calls == []
            )
            checks["authenticated_gate_rejects_fd198_ordwr"] = (
                authenticated_source_ordwr_error
                == "held smoke source FD is not O_RDONLY"
            )
            checks["bootstrap_gate_rejects_fd198_ordwr"] = (
                bootstrap_source_ordwr_rejected
            )

            odrdw_interpreter_path = base / "held-interpreter-ordwr.bin"
            odrdw_interpreter_path.write_bytes(interpreter_path.read_bytes())
            odrdw_interpreter_fd = os.open(odrdw_interpreter_path, os.O_RDWR)
            os.chmod(odrdw_interpreter_path, 0o755)
            saved_fixed_interpreter_fd = os.dup(smoke.INTERPRETER_FD)
            try:
                os.dup2(
                    odrdw_interpreter_fd,
                    smoke.INTERPRETER_FD,
                    inheritable=False,
                )
                odrdw_interpreter_args = copy.copy(held_args)
                odrdw_interpreter_args.expected_python_sha256 = hashlib.sha256(
                    odrdw_interpreter_path.read_bytes()
                ).hexdigest()
                odrdw_interpreter_rejected = rejected(
                    lambda: smoke.run_held_smoke_child(
                        held_fixture["source_identity"],
                        held_fixture["source_sha256"],
                        os.fspath(held_source_path),
                        smoke_cli(odrdw_interpreter_args),
                        timeout_seconds=10,
                        _popen_factory=odrdw_forbidden_popen,
                    )
                )
                authenticated_interpreter_ordwr_error = authenticated_ordwr_error(
                    odrdw_interpreter_args
                )
                bootstrap_interpreter_ordwr_rejected = bootstrap_ordwr_rejected(
                    smoke.INTERPRETER_FD, "interpreter"
                )
            finally:
                os.dup2(
                    saved_fixed_interpreter_fd,
                    smoke.INTERPRETER_FD,
                    inheritable=False,
                )
                os.close(saved_fixed_interpreter_fd)
                os.close(odrdw_interpreter_fd)
            checks["spawn_path_rejects_fd197_ordwr_before_popen"] = (
                odrdw_interpreter_rejected and odrdw_popen_calls == []
            )
            checks["authenticated_gate_rejects_fd197_ordwr"] = (
                authenticated_interpreter_ordwr_error
                == "held interpreter FD is not O_RDONLY"
            )
            checks["bootstrap_gate_rejects_fd197_ordwr"] = (
                bootstrap_interpreter_ordwr_rejected
            )

        receipt = core_receipt_fixture(args, authorization)
        try:
            build = smoke.validate_build_receipt(receipt, args, validated)
            core_receipt_accepted = build["decision_id"] == authorization["decision_id"]
            details["core_receipt_error"] = None
        except smoke.SmokeError as exc:
            core_receipt_accepted = False
            details["core_receipt_error"] = str(exc)
        checks["exact_core_v10_build_pass_receipt_schema_accept"] = core_receipt_accepted

        wrong_outer_argv = copy.deepcopy(receipt)
        wrong_outer_argv["trusted_launch"]["outer_process_argv"].append("tampered")
        checks["build_receipt_trusted_launch_outer_argv_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_outer_argv, args, validated)
        )
        wrong_launch_inheritance = copy.deepcopy(receipt)
        wrong_launch_inheritance["trusted_launch"][
            "builder_source_fd_inheritable"
        ] = True
        checks["build_receipt_trusted_launch_inheritance_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(
                wrong_launch_inheritance, args, validated
            )
        )
        wrong_outer_receipt_type = copy.deepcopy(receipt)
        wrong_outer_receipt_type["trusted_launch"][
            "outer_launch_receipt_sha256"
        ] = True
        checks["build_receipt_outer_launch_sha_bool_confusion_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(
                wrong_outer_receipt_type, args, validated
            )
        )
        outer_receipt_fixture = base / "outer-launch-receipt.json"
        outer_receipt_fixture.write_bytes(b'{"status":"PASS"}\n')
        os.chmod(outer_receipt_fixture, 0o444)
        outer_parent_fd, outer_fd, outer_name = smoke.open_absolute_regular(
            os.fspath(outer_receipt_fixture), "outer receipt synthetic"
        )
        try:
            outer_before = os.fstat(outer_fd)
            outer_sha = hashlib.sha256(
                smoke.read_fd_bytes(
                    outer_fd, "outer receipt synthetic", maximum_size=4096
                )
            ).hexdigest()
            replacement_outer = base / "outer-launch-receipt-replacement.json"
            replacement_outer.write_bytes(b'{"status":"PASS"}\n')
            os.chmod(replacement_outer, 0o444)
            os.replace(replacement_outer, outer_receipt_fixture)
            outer_current = os.stat(
                outer_name, dir_fd=outer_parent_fd, follow_symlinks=False
            )
            checks["outer_launch_receipt_held_inode_unlink_change_detected"] = rejected(
                lambda: smoke.read_fd_bytes(
                    outer_fd,
                    "outer receipt held final",
                    maximum_size=4096,
                )
            )
            checks["outer_launch_receipt_identical_path_replacement_detected"] = (
                (outer_current.st_dev, outer_current.st_ino)
                != (outer_before.st_dev, outer_before.st_ino)
            )
        finally:
            os.close(outer_fd)
            os.close(outer_parent_fd)

        extra = copy.deepcopy(receipt)
        extra["unexpected"] = False
        checks["build_receipt_extra_top_key_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(extra, args, validated)
        )
        wrong_decision = copy.deepcopy(receipt)
        wrong_decision["decision_id"] = "different-decision"
        checks["build_receipt_decision_cross_binding_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_decision, args, validated)
        )
        wrong_v8 = copy.deepcopy(receipt)
        wrong_v8["bound_v8"]["sha256_index_sha256"] = sha("wrong-v8-index")
        checks["build_receipt_v8_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_v8, args, validated)
        )
        wrong_python = copy.deepcopy(receipt)
        wrong_python["source_runtime"]["python_sha256"] = sha("wrong-python")
        checks["build_receipt_source_python_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_python, args, validated)
        )
        wrong_smoke = copy.deepcopy(receipt)
        wrong_smoke["package_binding"]["v10_smoke_sha256"] = sha("wrong-smoke")
        checks["build_receipt_package_smoke_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_smoke, args, validated)
        )
        wrong_v9_negative = copy.deepcopy(receipt)
        wrong_v9_negative["package_binding"][
            "v9_negative_qa_receipt_sha256"
        ] = sha("wrong-v9-negative-receipt")
        checks["build_receipt_v9_negative_binding_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(
                wrong_v9_negative, args, validated
            )
        )
        wrong_v8_negative = copy.deepcopy(receipt)
        wrong_v8_negative["package_binding"][
            "v8_negative_qa_receipt_sha256"
        ] = sha("wrong-v8-negative-receipt")
        checks["build_receipt_v8_negative_binding_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(
                wrong_v8_negative, args, validated
            )
        )
        wrong_root_digest = copy.deepcopy(receipt)
        wrong_root_digest["publication"]["structural_full_root_digest"] = sha("wrong-root")
        checks["build_receipt_structural_full_root_digest_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_root_digest, args, validated)
        )
        wrong_manifest = copy.deepcopy(receipt)
        wrong_manifest["runtime"]["manifest_sha256"] = sha("wrong-manifest")
        checks["build_receipt_manifest_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_manifest, args, validated)
        )
        wrong_files_digest = copy.deepcopy(receipt)
        wrong_files_digest["publication"]["files_only_full_root_digest"] = sha(
            "wrong-files-root"
        )
        checks["build_receipt_files_only_full_root_digest_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_files_digest, args, validated)
        )
        wrong_private_structural = copy.deepcopy(receipt)
        wrong_private_structural["runtime"]["structural_private_tree_digest"] = sha(
            "wrong-private-structural"
        )
        checks["build_receipt_private_structural_digest_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_private_structural, args, validated)
        )
        wrong_lock_method = copy.deepcopy(receipt)
        wrong_lock_method["journal"]["lock_method"] = "path-only-lock"
        checks["build_receipt_lock_method_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_lock_method, args, validated)
        )
        missing_terminal_method = copy.deepcopy(receipt)
        del missing_terminal_method["journal"]["terminal_publication_method"]
        checks["build_receipt_terminal_publication_method_missing_rejected"] = (
            rejected(
                lambda: smoke.validate_build_receipt(
                    missing_terminal_method, args, validated
                )
            )
        )
        missing_visibility_rule = copy.deepcopy(receipt)
        del missing_visibility_rule["journal"][
            "terminal_canonical_visibility_rule"
        ]
        checks["build_receipt_terminal_visibility_rule_missing_rejected"] = (
            rejected(
                lambda: smoke.validate_build_receipt(
                    missing_visibility_rule, args, validated
                )
            )
        )
        wrong_terminal_method = copy.deepcopy(receipt)
        wrong_terminal_method["journal"]["terminal_publication_method"] = (
            builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
        )
        checks["build_receipt_nonproduction_terminal_publication_rejected"] = (
            rejected(
                lambda: smoke.validate_build_receipt(
                    wrong_terminal_method, args, validated
                )
            )
        )
        wrong_visibility_rule = copy.deepcopy(receipt)
        wrong_visibility_rule["journal"]["terminal_canonical_visibility_rule"] = (
            "CANONICAL_PATH_MAY_BE_PARTIAL"
        )
        checks["build_receipt_terminal_visibility_rule_tamper_rejected"] = (
            rejected(
                lambda: smoke.validate_build_receipt(
                    wrong_visibility_rule, args, validated
                )
            )
        )
        support_name = sorted(smoke.SUPPORT_SHA256)[0]
        wrong_support_sha = copy.deepcopy(receipt)
        wrong_support_sha["support_files"][support_name]["sha256"] = sha(
            "wrong-support"
        )
        checks["build_receipt_support_sha_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_support_sha, args, validated)
        )
        wrong_support_schema = copy.deepcopy(receipt)
        wrong_support_schema["support_files"][support_name]["extra"] = False
        checks["build_receipt_support_extra_key_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_support_schema, args, validated)
        )
        wrong_exclusions = copy.deepcopy(receipt)
        wrong_exclusions["external_record_exclusions"]["entries"][0][
            "relative_path"
        ] = "../../../bin/not-frozen"
        checks["build_receipt_exact7_exclusion_tamper_rejected"] = rejected(
            lambda: smoke.validate_build_receipt(wrong_exclusions, args, validated)
        )

        regular = base / "regular.json"
        regular.write_text("{}\n", encoding="utf-8")
        os.chmod(regular, 0o444)
        parent_fd, file_fd, _ = smoke.open_absolute_regular(
            os.fspath(regular), "regular"
        )
        os.close(file_fd)
        os.close(parent_fd)
        link = base / "regular-link.json"
        link.symlink_to(regular)
        checks["nofollow_absolute_regular_rejects_symlink"] = rejected(
            lambda: smoke.open_absolute_regular(os.fspath(link), "symlink")
        )

        tree = base / "tree"
        tree.mkdir()
        (tree / "one.bin").write_bytes(b"one")
        (tree / "nested").mkdir()
        (tree / "nested" / "two.bin").write_bytes(b"two")
        tree_fd = os.open(tree, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            smoke_structural = smoke.inventory_tree(
                tree_fd,
                "synthetic tree",
                include_root=True,
                require_frozen_modes=False,
            )
            builder_structural = builder.inventory_structural(tree_fd, include_root=True)
            smoke_files = smoke.files_only_records(smoke_structural)
            builder_files = builder.files_only_records(builder_structural)
            smoke_file_digest = smoke.files_only_digest(smoke_files)
            builder_file_digest = builder.files_only_digest(builder_files)
            smoke_structural_digest = smoke.structural_digest(smoke_structural)
            builder_structural_digest = builder.structural_digest(builder_structural)
            cursor = smoke.fresh_directory_cursor(tree_fd, "synthetic fresh cursor")
            try:
                fresh_cursor_same_inode = (
                    os.fstat(cursor).st_dev,
                    os.fstat(cursor).st_ino,
                ) == (os.fstat(tree_fd).st_dev, os.fstat(tree_fd).st_ino)
            finally:
                os.close(cursor)
        finally:
            os.close(tree_fd)
        checks["files_only_records_match_core_v10_algorithm"] = (
            smoke_files == builder_files
        )
        checks["files_only_digest_matches_core_v10_algorithm"] = (
            smoke_file_digest == builder_file_digest
        )
        checks["structural_records_match_core_v10_algorithm"] = (
            smoke_structural == builder_structural
        )
        checks["structural_digest_matches_core_v10_algorithm"] = (
            smoke_structural_digest == builder_structural_digest
        )
        checks["fresh_directory_cursor_pins_held_inode"] = fresh_cursor_same_inode

        os.chmod(tree / "nested", 0o700)
        tree_fd = os.open(tree, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            mode_tampered = smoke.inventory_tree(
                tree_fd,
                "synthetic tree mode tamper",
                include_root=True,
                require_frozen_modes=False,
            )
        finally:
            os.close(tree_fd)
        checks["directory_mode_tamper_changes_structural_digest"] = (
            smoke.structural_digest(mode_tampered) != smoke_structural_digest
        )
        checks["directory_mode_tamper_does_not_change_files_only_digest"] = (
            smoke.files_only_digest(smoke.files_only_records(mode_tampered))
            == smoke_file_digest
        )

        identity_root = base / "identity-replacement"
        identity_root.mkdir()
        (identity_root / "support.bin").write_bytes(b"identical support bytes")
        (identity_root / "bundle").mkdir()
        identity_root_fd = os.open(
            identity_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        held_support = smoke.open_child(
            identity_root_fd, "support.bin", "held support", directory=False
        )
        held_bundle = smoke.open_child(
            identity_root_fd, "bundle", "held bundle", directory=True
        )
        try:
            os.rename(identity_root / "support.bin", identity_root / "support.old")
            (identity_root / "support.bin").write_bytes(b"identical support bytes")
            checks["identical_byte_support_inode_replacement_rejected"] = rejected(
                lambda: smoke._revalidate_named_child(
                    identity_root_fd,
                    "support.bin",
                    held_support,
                    "support replacement",
                )
            )
            os.rename(identity_root / "bundle", identity_root / "bundle.old")
            (identity_root / "bundle").mkdir()
            checks["identical_bundle_directory_inode_replacement_rejected"] = rejected(
                lambda: smoke._revalidate_named_child(
                    identity_root_fd,
                    "bundle",
                    held_bundle,
                    "bundle replacement",
                )
            )
        finally:
            os.close(held_bundle)
            os.close(held_support)
            os.close(identity_root_fd)

        loader_root = base / "held-loader"
        loader_root.mkdir()
        source_path = loader_root / "held_demo.py"
        source_path.write_bytes(b"VALUE = 17\n")
        os.chmod(source_path, 0o444)
        loader_root_fd = os.open(
            loader_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        source_finder = smoke.HeldVerifiedRuntimeFinder(
            loader_root_fd,
            {"held_demo.py": manifest_record("held_demo.py", source_path)},
            set(),
            NoopMutationGuard(),
        )
        try:
            source_spec = source_finder.find_spec("held_demo")
            source_module = smoke.importlib.util.module_from_spec(source_spec)
            source_spec.loader.exec_module(source_module)
            checks["source_executes_from_held_verified_bytes"] = (
                source_module.VALUE == 17
                and source_finder.loaded["held_demo"]["execution_kind"] == "source"
            )
        finally:
            source_finder.close()

        bytecode_relative = "held_bytecode.pyc"
        bytecode_path = loader_root / bytecode_relative
        bytecode_code = compile("VALUE = 23\n", "held_bytecode.py", "exec")
        bytecode_path.write_bytes(
            smoke.importlib.util.MAGIC_NUMBER
            + (0).to_bytes(4, "little")
            + (0).to_bytes(8, "little")
            + smoke.marshal.dumps(bytecode_code)
        )
        os.chmod(bytecode_path, 0o444)
        bytecode_finder = smoke.HeldVerifiedRuntimeFinder(
            loader_root_fd,
            {bytecode_relative: manifest_record(bytecode_relative, bytecode_path)},
            set(),
            NoopMutationGuard(),
        )
        try:
            bytecode_spec = bytecode_finder.find_spec("held_bytecode")
            bytecode_module = smoke.importlib.util.module_from_spec(bytecode_spec)
            bytecode_spec.loader.exec_module(bytecode_module)
            checks["bytecode_executes_from_held_verified_bytes"] = (
                bytecode_module.VALUE == 23
                and bytecode_finder.loaded["held_bytecode"]["execution_kind"]
                == "bytecode"
            )
        finally:
            bytecode_finder.close()

        replace_path = loader_root / "replace_demo.py"
        replace_path.write_bytes(b"VALUE = 19\n")
        os.chmod(replace_path, 0o444)
        replacement_finder = smoke.HeldVerifiedRuntimeFinder(
            loader_root_fd,
            {"replace_demo.py": manifest_record("replace_demo.py", replace_path)},
            set(),
            NoopMutationGuard(),
        )
        try:
            replacement_spec = replacement_finder.find_spec("replace_demo")
            replacement_loader = replacement_spec.loader
            os.rename(replace_path, loader_root / "replace_demo.old")
            replace_path.write_bytes(b"VALUE = 19\n")
            os.chmod(replace_path, 0o444)
            checks["identical_source_inode_replacement_rejected_before_exec"] = rejected(
                lambda: replacement_loader._verify_held_and_named(
                    "synthetic identical replacement"
                )
            )
        finally:
            replacement_finder.close()

        native_suffix = smoke.importlib.machinery.EXTENSION_SUFFIXES[0]
        native_relative = f"held_native{native_suffix}"
        native_path = loader_root / native_relative
        native_path.write_bytes(b"not a native object")
        os.chmod(native_path, 0o444)
        native_finder = smoke.HeldVerifiedRuntimeFinder(
            loader_root_fd,
            {native_relative: manifest_record(native_relative, native_path)},
            set(),
            NoopMutationGuard(),
        )
        try:
            native_spec = native_finder.find_spec("held_native")
            native_loader = native_spec.loader
            checks["native_loader_origin_is_held_proc_fd"] = (
                native_loader.kind == "extension"
                and native_loader.origin == f"/proc/self/fd/{native_loader.fd}"
            )
            try:
                smoke.importlib.util.module_from_spec(native_spec)
            except smoke.SmokeError as exc:
                native_fail_closed = "no pathname fallback" in str(exc)
            else:
                native_fail_closed = False
            checks["invalid_native_load_fails_closed_without_path_fallback"] = (
                native_fail_closed
            )
        finally:
            native_finder.close()
            os.close(loader_root_fd)

        checks["scratch_delta_reports_added_file"] = smoke.scratch_delta(
            [],
            [
                {
                    "relative_path": "cache/item",
                    "kind": "regular",
                    "sha256": sha("cache-item"),
                    "size_bytes": 1,
                    "mode": "0600",
                }
            ],
        )["added"][0]["relative_path"] == "cache/item"

        native_fixture_checks, native_fixture_status, native_fixture_details = (
            linux_real_native_origin_fixture(base)
        )
        checks.update(native_fixture_checks)
        checks["linux_real_native_fixture_status_is_explicit"] = (
            native_fixture_status == "PASS_LINUX_REAL_HELD_EXTENSION_ORIGIN_FIXTURE"
            or native_fixture_status.startswith("NOT_RUN_")
        )
        details["linux_real_native_origin_fixture"] = {
            "status": native_fixture_status,
            **native_fixture_details,
        }

        if sys.platform.startswith("linux"):
            watched = base / "inotify-watch"
            watched.mkdir()
            (watched / "nested").mkdir()
            watched_payload = watched / "payload.bin"
            watched_payload.write_bytes(b"original-bytes\n")
            watched_fd = os.open(
                watched, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            mutation_guard = smoke.RecursiveInotifyGuard(watched_fd)
            try:
                os.chmod(watched / "nested", 0o700)
                checks["linux_inotify_detects_directory_mode_tamper"] = rejected(
                    lambda: mutation_guard.assert_clean("synthetic directory mode tamper")
                )
            finally:
                mutation_guard.close()
                os.close(watched_fd)

            hardlink_root = base / "inotify-hardlink-watch"
            hardlink_root.mkdir()
            hardlink_payload = hardlink_root / "payload.bin"
            original_bytes = b"original-bytes\n"
            hardlink_payload.write_bytes(original_bytes)
            os.chmod(hardlink_payload, 0o644)
            hardlink_fd = os.open(
                hardlink_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            hardlink_guard = smoke.RecursiveInotifyGuard(hardlink_fd)
            hardlink_assert_guard = smoke.RecursiveInotifyGuard(hardlink_fd)
            outside_alias = base / "outside-hardlink-alias.bin"
            try:
                file_watch_count = sum(
                    entry["kind"] == "regular"
                    for entry in hardlink_guard.watch_table.values()
                )
                directory_watch_count = sum(
                    entry["kind"] == "directory"
                    for entry in hardlink_guard.watch_table.values()
                )
                os.link(hardlink_payload, outside_alias)
                os.chmod(outside_alias, 0o600)
                with outside_alias.open("r+b", buffering=0) as handle:
                    handle.seek(0)
                    handle.write(b"tampered-byte!\n")
                    os.fsync(handle.fileno())
                    handle.seek(0)
                    handle.write(original_bytes)
                    handle.truncate(len(original_bytes))
                    os.fsync(handle.fileno())
                os.chmod(outside_alias, 0o644)
                outside_alias.unlink()
                assert_clean_rejected = rejected(
                    lambda: hardlink_assert_guard.assert_clean(
                        "synthetic external hardlink modify/restore"
                    )
                )
                hardlink_events = hardlink_guard.events()
                regular_file_events = [
                    event
                    for event in hardlink_events
                    if event["watch"]["kind"] == "regular"
                    and event["watch"]["relative_path"] == "payload.bin"
                ]
                regular_event_masks = [
                    event["mask"] for event in regular_file_events
                ]
                restored_info = os.stat(hardlink_payload, follow_symlinks=False)
                details["linux_external_hardlink_event_evidence"] = {
                    "event_count": len(hardlink_events),
                    "regular_file_event_masks": regular_event_masks,
                    "saw_modify": any(
                        mask & smoke.IN_MODIFY for mask in regular_event_masks
                    ),
                    "saw_close_write": any(
                        mask & smoke.IN_CLOSE_WRITE for mask in regular_event_masks
                    ),
                    "restored_size_bytes": restored_info.st_size,
                    "restored_mode": f"{stat.S_IMODE(restored_info.st_mode):04o}",
                    "restored_link_count": restored_info.st_nlink,
                    "assert_clean_rejected": assert_clean_rejected,
                }
                checks["linux_inotify_regular_inode_detects_external_hardlink_modify_restore"] = (
                    file_watch_count == 1
                    and directory_watch_count == 1
                    and assert_clean_rejected
                    and any(
                        mask & smoke.IN_MODIFY for mask in regular_event_masks
                    )
                    and any(
                        mask & smoke.IN_CLOSE_WRITE for mask in regular_event_masks
                    )
                    and hardlink_payload.read_bytes() == original_bytes
                    and stat.S_IMODE(restored_info.st_mode) == 0o644
                    and restored_info.st_nlink == 1
                )
            finally:
                if outside_alias.exists():
                    outside_alias.unlink()
                hardlink_assert_guard.close()
                hardlink_guard.close()
                os.close(hardlink_fd)
            linux_status = "NOT_RUN_NO_AUTHORIZED_LOCAL_PRODUCTION_FIXTURE"
        else:
            linux_status = "NOT_RUN_NON_LINUX"

        held_bootstrap_status, held_bootstrap_checks, held_bootstrap_details = (
            run_linux_real_held_bootstrap_fixture(base)
        )
        checks.update(held_bootstrap_checks)
        details["linux_real_held_bootstrap_fixture"] = held_bootstrap_details

    checks["linux_actual_smoke_status_is_explicit"] = linux_status in {
        "NOT_RUN_NON_LINUX",
        "NOT_RUN_NO_AUTHORIZED_LOCAL_PRODUCTION_FIXTURE",
    }
    checks["linux_real_held_bootstrap_status_is_explicit"] = (
        held_bootstrap_status == "PASS"
        if sys.platform.startswith("linux")
        else held_bootstrap_status == "NOT_RUN_NON_LINUX"
    )
    pass_count = sum(value is True for value in checks.values())
    payload = {
        "schema": "historical_200k_fixed10k_result_free_runtime_smoke_v10_synthetic_test_v1",
        "status": "PASS" if pass_count == len(checks) else "FAIL",
        "platform": sys.platform,
        "linux_actual_smoke": linux_status,
        "linux_real_native_origin_fixture": native_fixture_status,
        "linux_real_held_bootstrap_fixture": held_bootstrap_status,
        "gate_count": len(checks),
        "pass_count": pass_count,
        "fail_count": len(checks) - pass_count,
        "failed_gates": sorted(name for name, value in checks.items() if value is not True),
        "checks": checks,
        "details": details,
        "scope": {
            "temporary_local_fixtures_only": True,
            "production_builder_executed": False,
            "production_smoke_executed": False,
            "remote_accessed": False,
            "results_accessed": False,
            "local_compiler_subprocess_used": native_fixture_details[
                "compiler_subprocess_used"
            ],
            "local_held_child_subprocess_used": True,
            "external_subprocess_scope": (
                "LOCAL_SYNTHETIC_FD_INHERITANCE_CHILD_ONLY"
                if not native_fixture_details["compiler_subprocess_used"]
                else native_fixture_details["external_subprocess_scope"]
            ),
            "external_process_inspection_or_control_used": False,
            "self_proc_maps_read_by_smoke_logic": native_fixture_details[
                "self_proc_maps_read_by_smoke_logic"
            ],
            "signals_or_process_control_used": False,
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
