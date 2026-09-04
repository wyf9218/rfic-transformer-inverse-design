"""Standard-library-only identity boundary for the exact-GDS EMX child."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import sysconfig
from pathlib import Path


SCHEMA = "rfic_transformer.broadband56_emx_python_runtime.v1"
PACKAGE = "rfic_transformer_inverse_design"
REQUIRED_MODULES = (
    "numpy",
    "gdstk",
    "yaml",
    PACKAGE,
    f"{PACKAGE}.campaigns",
    f"{PACKAGE}.campaigns.broadband56_exact_gds_emx",
    f"{PACKAGE}.core.defaults",
    f"{PACKAGE}.execution.zeus_cadence",
    f"{PACKAGE}.sim.emx.simulation",
    f"{PACKAGE}.sim.touchstone",
)


class EmxRuntimeIdentityError(RuntimeError):
    """An interpreter or imported module differs from its immutable binding."""


def pinned_file(record: dict, label: str) -> Path:
    path = Path(str(record.get("path", "")))
    if not path.is_absolute() or not path.is_file():
        raise EmxRuntimeIdentityError(f"{label}: missing absolute file")
    if path.stat().st_size != record.get("size_bytes"):
        raise EmxRuntimeIdentityError(f"{label}: size mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
        raise EmxRuntimeIdentityError(f"{label}: SHA-256 mismatch")
    return path


def load_identity(path: Path, expected_sha256: str) -> dict:
    if not path.is_absolute() or not path.is_file():
        raise EmxRuntimeIdentityError("runtime identity requires an absolute file")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise EmxRuntimeIdentityError("runtime identity SHA-256 mismatch")
    identity = json.loads(payload)
    if identity.get("schema") != SCHEMA:
        raise EmxRuntimeIdentityError("runtime identity schema mismatch")
    for key in ("python_launcher", "python_runtime", "entrypoint", "bootstrap"):
        pinned_file(identity[key], key)
    launcher = Path(identity["python_launcher"]["path"])
    if not os.access(launcher, os.X_OK):
        raise EmxRuntimeIdentityError("approved private Python is not executable")
    root = Path(identity["runtime_root"])
    if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
        raise EmxRuntimeIdentityError("runtime root identity mismatch")
    if not set(REQUIRED_MODULES).issubset(identity.get("modules", {})):
        raise EmxRuntimeIdentityError("required module bindings missing")
    for value in identity["dependency_roots"]:
        dependency = Path(value)
        if not dependency.is_absolute() or not dependency.is_dir():
            raise EmxRuntimeIdentityError("dependency root is not an absolute directory")
    environment = identity.get("environment")
    if not isinstance(environment, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in environment.items()
    ):
        raise EmxRuntimeIdentityError("explicit approved environment missing")
    if any(k.startswith("PYTHON") for k in environment):
        raise EmxRuntimeIdentityError("ambient Python environment is forbidden")
    if not environment.get("PATH") or any(
        not Path(item).is_absolute() for item in environment["PATH"].split(os.pathsep)
    ):
        raise EmxRuntimeIdentityError("approved PATH contains a relative component")
    return identity


def launch_spec(identity_path: Path, expected_sha256: str, arguments: list[str]) -> dict:
    """The identical launch construction is used by production and preflight."""
    identity = load_identity(identity_path, expected_sha256)
    return {
        "args": [
            identity["python_launcher"]["path"], "-I", "-B", "-S",
            identity["entrypoint"]["path"],
            "--runtime-identity", str(identity_path),
            "--expected-runtime-identity-sha256", expected_sha256,
            *arguments,
        ],
        "env": dict(identity["environment"]),
        "cwd": identity["runtime_root"],
        "shell": False,
    }


def activate_and_verify(identity: dict) -> dict:
    """Ignore site hooks; pin the private executable and all imported sources."""
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise EmxRuntimeIdentityError("EMX child requires isolated -I -B -S startup")
    executable = Path(sys.executable).absolute()
    runtime = identity["python_runtime"]
    if str(executable) != runtime["path"]:
        raise EmxRuntimeIdentityError("private Python executable path mismatch")
    if str(executable.resolve()) != runtime["realpath"]:
        raise EmxRuntimeIdentityError("private Python executable realpath mismatch")
    pinned_file(runtime, "executing private Python")
    if list(sys.version_info[:3]) != runtime["version"]:
        raise EmxRuntimeIdentityError("private Python version mismatch")

    root = Path(identity["runtime_root"])
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    # -S suppresses editable-install .pth execution and user/site customizations.
    # Retain only interpreter-provided stdlib entries, then add exact bound roots.
    standard_paths = [
        item for item in sys.path
        if item and (Path(item).resolve().is_relative_to(stdlib)
                     or Path(item).name == f"python{sys.version_info.major}{sys.version_info.minor}.zip")
    ]
    sys.path[:] = [str(root), *standard_paths, *identity["dependency_roots"]]
    for name in REQUIRED_MODULES:
        importlib.import_module(name)
    modules = verify_loaded_modules(identity, stdlib=stdlib)
    return {
        "schema": SCHEMA,
        "overall_status": "PASS",
        "approved_private_python": "PASS",
        "numpy_import": "PASS",
        "campaigns_module_import": "PASS",
        "module_root_identity": "PASS",
        "modules": modules,
        "simulator_action_taken": False,
    }


def verify_loaded_modules(identity: dict, *, stdlib: Path | None = None) -> list[dict]:
    root = Path(identity["runtime_root"])
    stdlib = stdlib or Path(sysconfig.get_path("stdlib")).resolve()
    allowed_dependencies = {Path(item).resolve() for item in identity["dependency_roots"]}
    results = []
    for name, module in sorted(tuple(sys.modules.items())):
        filename = getattr(module, "__file__", None)
        if not filename or filename.startswith("<"):
            continue
        path = Path(filename).resolve()
        is_dependency = any(path.is_relative_to(item) for item in allowed_dependencies)
        if path.is_relative_to(stdlib) and not is_dependency and not name.startswith(PACKAGE):
            continue
        if path == Path(identity["entrypoint"]["path"]).resolve():
            pinned_file(identity["entrypoint"], "EMX entrypoint")
            continue
        if path == Path(identity["bootstrap"]["path"]).resolve():
            pinned_file(identity["bootstrap"], "EMX bootstrap")
            continue
        record = identity["modules"].get(name)
        if not isinstance(record, dict):
            raise EmxRuntimeIdentityError(f"unbound imported module: {name} ({path})")
        expected_root = Path(record["expected_root"]).resolve()
        if name == PACKAGE or name.startswith(PACKAGE + "."):
            if expected_root != root:
                raise EmxRuntimeIdentityError(f"project module root mismatch: {name}")
        elif expected_root not in allowed_dependencies:
            raise EmxRuntimeIdentityError(f"unapproved dependency root: {name}")
        if not path.is_relative_to(expected_root) or path != Path(record["path"]).resolve():
            raise EmxRuntimeIdentityError(f"loaded module identity mismatch: {name}")
        pinned_file(record, f"module {name}")
        results.append({
            "import_name": name, "resolved_file": str(path),
            "sha256": record["sha256"], "expected_runtime_root": str(expected_root),
            "belongs_to_approved_runtime": True, "import_status": "PASS",
        })
    return results


def describe_loaded_runtime(
    *, launcher: Path, entrypoint: Path, runtime_root: Path,
    dependency_roots: list[Path], environment: dict[str, str],
) -> dict:
    """Packaging-only snapshot; callers must first use the approved -I -B -S Python.

    This is not a production fallback. The resulting bytes require exact-SHA
    authorization, and production only verifies them via activate_and_verify.
    """
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise EmxRuntimeIdentityError("snapshot requires isolated -I -B -S startup")
    root = runtime_root.resolve(strict=True)
    dependencies = [item.resolve(strict=True) for item in dependency_roots]
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    sys.path[:0] = [str(root), *map(str, dependencies)]
    for name in REQUIRED_MODULES:
        importlib.import_module(name)

    def record(path: Path) -> dict:
        return {"path": str(path), "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    modules = {}
    for name, module in sorted(tuple(sys.modules.items())):
        filename = getattr(module, "__file__", None)
        if not filename or filename.startswith("<"):
            continue
        path = Path(filename).resolve()
        if path in {Path(__file__).resolve(), entrypoint.resolve()}:
            continue
        expected_root = root if name == PACKAGE or name.startswith(PACKAGE + ".") else next(
            (item for item in dependencies if path.is_relative_to(item)), None
        )
        if expected_root is None:
            if path.is_relative_to(stdlib):
                continue
            raise EmxRuntimeIdentityError(f"unapproved snapshot module: {name} ({path})")
        if not path.is_relative_to(expected_root):
            raise EmxRuntimeIdentityError(f"snapshot module outside runtime: {name}")
        modules[name] = {**record(path), "expected_root": str(expected_root)}
    executable = Path(sys.executable).absolute()
    return {
        "schema": SCHEMA,
        "python_launcher": record(launcher.absolute()),
        "python_runtime": {**record(executable), "realpath": str(executable.resolve()),
                           "version": list(sys.version_info[:3])},
        "entrypoint": record(entrypoint.resolve()),
        "bootstrap": record(Path(__file__).resolve()),
        "runtime_root": str(root),
        "dependency_roots": list(map(str, dependencies)),
        "environment": environment,
        "modules": modules,
    }
