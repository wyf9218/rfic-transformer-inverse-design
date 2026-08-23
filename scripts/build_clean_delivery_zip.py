#!/usr/bin/env python3
"""Rebuild a delivery package SHA manifest and clean deterministic zip.

The zip hash is written outside the package to avoid circular hashes. The zip
writer intentionally omits macOS resource metadata such as __MACOSX, .DS_Store,
AppleDouble ._* files, and Python bytecode/cache files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


FIXED_ZIP_TIMESTAMP = (2026, 6, 13, 0, 0, 0)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package_dir = Path(args.package_dir).expanduser().resolve()
    zip_path = Path(args.zip_path).expanduser().resolve()
    zip_sha_record = Path(args.zip_sha_record).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    out_json = Path(args.out_json).expanduser().resolve() if args.out_json else None

    if not args.skip_sync_project_artifacts:
        _sync_project_artifacts(project_root=project_root, package_dir=package_dir)
        _sync_validation_scripts(project_root=project_root, package_dir=package_dir)
    summary = _build_clean_zip(package_dir, zip_path, zip_sha_record)
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"package_dir={summary['package_dir']}")
    print(f"sha_manifest={summary['sha_manifest']}")
    print(f"package_file_count={summary['package_file_count']}")
    print(f"zip_path={summary['zip_path']}")
    print(f"zip_sha256={summary['zip_sha256']}")
    print(f"zip_entry_count={summary['zip_entry_count']}")
    print(f"metadata_entry_count={summary['metadata_entry_count']}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_package = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
    default_project = Path("/home/researcher/Documents/模拟变压器AI反向建模")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(default_package))
    parser.add_argument("--zip-path", default=str(default_package.with_suffix(".zip")))
    parser.add_argument("--project-root", default=str(default_project))
    parser.add_argument(
        "--zip-sha-record",
        default=str(
            default_project
            / "hfss_validation"
            / "final500_ec6698dfc575950b"
            / "FINAL_DESKTOP_PACKAGE_HASH_20260613.txt"
        ),
    )
    parser.add_argument("--out-json", help="Optional JSON summary path")
    parser.add_argument("--skip-sync-project-artifacts", action="store_true")
    return parser.parse_args(argv)


def _sync_project_artifacts(*, project_root: Path, package_dir: Path) -> None:
    directory_names = (
        "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613",
        "mars_handoff_bundle_20260613",
        "mars_next_action_packet_20260614",
    )
    validation_directory_names = (
        "geometry_quality_audit_final500_selected_20260613",
        "ads_metric_formula_consistency_20260614",
        "emx_first_validation_gate_20260613",
        "target_emx_wideband_rerun_20260613",
        "mars_emx_return_discovery_20260614",
        "mars_emx_return_watch_20260614",
    )
    file_names = (
        "README_START_HERE_20260613_CN.md",
        "MORNING_STATUS_20260614_CN.md",
        "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md",
        "AUTONOMOUS_PROGRESS_20260613_TOUCHSTONE_GATES_CN.md",
        "EMX_FIRST_CURRENT_DECISION_20260613_CN.md",
        "EMX_REFERENCE_SOURCE_RECOVERY_20260613_CN.md",
        "STRICT_EMX_HFSS_ADS_VALIDATION_SEQUENCE_20260613_CN.md",
        "CURRENT_EMX_HFSS_ADS_VALIDATION_DECISION_20260614_CN.md",
        "EMX_HFSS_ADS_PHYSICS_VALIDATION_METHOD_20260614_CN.md",
        "NEXT_MARS_WIDEBAND_EMX_ACTION_20260614_CN.md",
        "ACCEPTANCE_MATRIX_20260613_CN.md",
        "ACCEPTANCE_MATRIX_20260613.md",
        "acceptance_matrix_20260613.json",
        "mars_handoff_bundle_20260613.tar.gz",
        "mars_handoff_bundle_20260613.tar.gz.sha256",
    )
    for name in directory_names:
        source = project_root / name
        target = package_dir / name
        _sync_directory(source, target)
    validation_root = project_root / "hfss_validation" / "final500_ec6698dfc575950b"
    for name in validation_directory_names:
        _sync_directory(validation_root / name, package_dir / name)
    for name in file_names:
        source = project_root / name
        target = package_dir / name
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _sync_directory(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _sync_validation_scripts(*, project_root: Path, package_dir: Path) -> None:
    source_dir = project_root / "rfic-transformer-inverse-design" / "scripts"
    if not source_dir.is_dir():
        return
    target_dir = package_dir / "validation_scripts"
    target_dir.mkdir(parents=True, exist_ok=True)
    source_files = {path.name: path for path in source_dir.glob("*.py")}
    for stale in target_dir.glob("*.py"):
        if stale.name not in source_files:
            stale.unlink()
    for name, source in sorted(source_files.items()):
        shutil.copy2(source, target_dir / name)


def _build_clean_zip(package_dir: Path, zip_path: Path, zip_sha_record: Path) -> dict[str, Any]:
    if not package_dir.is_dir():
        raise FileNotFoundError(f"package directory is missing: {package_dir}")

    sha_manifest = package_dir / "SHA256SUMS.txt"
    pruned_metadata_count = _prune_metadata_files(package_dir)
    pruned_cache_count = _prune_python_cache(package_dir)
    package_files = _package_files(package_dir, include_sha_manifest=False)
    sha_manifest.write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(package_dir).as_posix()}\n" for path in package_files),
        encoding="utf-8",
    )

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    zip_files = _package_files(package_dir, include_sha_manifest=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in zip_files:
            rel = file_path.relative_to(package_dir)
            arcname = PurePosixPath(package_dir.name) / PurePosixPath(rel.as_posix())
            info = zipfile.ZipInfo(str(arcname), date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, file_path.read_bytes())

    zip_sha = _sha256(zip_path)
    zip_sha_record.parent.mkdir(parents=True, exist_ok=True)
    zip_sha_record.write_text(f"{zip_sha}  {zip_path}\n", encoding="utf-8")

    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        names = archive.namelist()
    if bad_member:
        raise ValueError(f"bad zip member after write: {bad_member}")
    metadata_entries = _metadata_entries(names)
    if metadata_entries:
        raise ValueError(f"clean zip contains metadata entries: {metadata_entries[:8]}")

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "package_dir": str(package_dir),
        "sha_manifest": str(sha_manifest),
        "package_file_count": len(package_files),
        "pruned_python_cache_count": pruned_cache_count,
        "zip_path": str(zip_path),
        "zip_sha_record": str(zip_sha_record),
        "zip_sha256": zip_sha,
        "zip_entry_count": len(names),
        "metadata_entry_count": len(metadata_entries),
        "pruned_metadata_count": pruned_metadata_count,
    }


def _package_files(package_dir: Path, *, include_sha_manifest: bool) -> list[Path]:
    files = []
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir)
        if not include_sha_manifest and path.name == "SHA256SUMS.txt":
            continue
        if _is_metadata_path(rel) or _is_python_cache_path(rel):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(package_dir).as_posix())


def _prune_python_cache(package_dir: Path) -> int:
    pruned = 0
    for cache_dir in sorted((path for path in package_dir.rglob("__pycache__") if path.is_dir()), reverse=True):
        shutil.rmtree(cache_dir)
        pruned += 1
    for bytecode in package_dir.rglob("*"):
        if bytecode.is_file() and bytecode.suffix in {".pyc", ".pyo"}:
            bytecode.unlink()
            pruned += 1
    return pruned


def _prune_metadata_files(package_dir: Path) -> int:
    pruned = 0
    for path in sorted(package_dir.rglob("*"), reverse=True):
        rel = path.relative_to(package_dir)
        if _is_metadata_path(rel):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            pruned += 1
    return pruned


def _is_metadata_path(rel_path: Path) -> bool:
    parts = rel_path.as_posix().split("/")
    return bool(parts and (parts[0] == "__MACOSX" or any(part == ".DS_Store" or part.startswith("._") for part in parts)))


def _is_python_cache_path(rel_path: Path) -> bool:
    parts = rel_path.as_posix().split("/")
    return any(part == "__pycache__" for part in parts) or rel_path.suffix in {".pyc", ".pyo"}


def _metadata_entries(names: list[str]) -> list[str]:
    entries: list[str] = []
    for name in names:
        parts = [part for part in name.split("/") if part]
        if parts and (parts[0] == "__MACOSX" or any(part == ".DS_Store" or part.startswith("._") for part in parts)):
            entries.append(name)
    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
