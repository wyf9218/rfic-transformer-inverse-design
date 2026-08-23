"""Path helpers for rfic-transformer-inverse-design."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


def package_root() -> Path:
    return Path(__file__).resolve().parent


def bundled_proc_dir() -> Path:
    return package_root() / "process" / "assets" / "proc"


def default_proc_path() -> Path:
    return bundled_proc_dir() / "default_typical.proc"


def runtime_root() -> Path:
    return (Path.cwd() / "tmp" / "rfic_transformer_inverse_design").resolve()


def resolve_local_path(raw: str | Path, *, extra_roots: tuple[Path, ...] = ()) -> Path:
    candidate = Path(raw).expanduser()
    windows_candidate = PureWindowsPath(str(raw))
    is_windows_absolute = windows_candidate.is_absolute()
    if candidate.is_absolute() or is_windows_absolute:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
        source_name = windows_candidate.name if is_windows_absolute else candidate.name
        search_roots = (Path.cwd(), *extra_roots)
        for root in search_roots:
            by_name = (Path(root) / source_name).resolve()
            if by_name.exists():
                return by_name
        return resolved

    search_roots = (Path.cwd(), *extra_roots)
    for root in search_roots:
        resolved = (Path(root) / candidate).resolve()
        if resolved.exists():
            return resolved
    return (Path.cwd() / candidate).resolve()
