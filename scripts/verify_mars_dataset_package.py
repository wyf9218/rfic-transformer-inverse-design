#!/usr/bin/env python3
"""Verify a downloaded MARS dataset transfer package before unpacking it.

This validates the tarball SHA record, package inventory, tar path safety, and
per-file SHA/size evidence written by package_mars_dataset_run.py. It can also
run audit_mars_run_progress.py on a temporary extraction to prove the package is
ready for downstream local quality gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    tarball = Path(args.tarball).expanduser().resolve()
    inventory_path = Path(args.inventory).expanduser().resolve() if args.inventory else _default_inventory_path(tarball)
    inventory_report_path = (
        Path(args.inventory_report).expanduser().resolve() if args.inventory_report else _default_inventory_report_path(tarball)
    )
    sha_path = Path(args.sha256_file).expanduser().resolve() if args.sha256_file else _default_sha_path(tarball)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    inventory: dict[str, Any] = {}
    checks.append(_progress_audit_contract_check(args))
    checks.append(_tarball_exists_check(tarball))
    if tarball.exists():
        actual_tar_sha = _sha256(tarball)
        checks.append(_sha_record_check(tarball, sha_path, actual_tar_sha))
        inventory = _read_json(inventory_path)
        checks.append(_inventory_check(inventory_path, inventory, actual_tar_sha))
        if "_parse_error" not in inventory and "_missing" not in inventory:
            checks.append(_inventory_report_check(inventory_report_path, inventory, actual_tar_sha))
            checks.extend(_tar_inventory_checks(tarball, inventory, args))
        else:
            checks.append(Check("FAIL", "tar inventory contents", "inventory missing or unparsable"))
    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "tarball": str(tarball),
        "inventory": str(inventory_path),
        "inventory_report": str(inventory_report_path),
        "sha256_file": str(sha_path),
        "inventory_category_counts": inventory.get("category_counts") if isinstance(inventory.get("category_counts"), dict) else None,
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This verifies package integrity and local filesystem evidence only.",
            "A PASS does not prove EMX, HFSS, ADS, or final training quality.",
            "Run run_dataset_quality_gates.py after extracting the package for physics/data coverage gates.",
        ],
    }
    summary_path = out_dir / "mars_dataset_package_verify_summary.json"
    report_path = out_dir / "mars_dataset_package_verify_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tarball", help="Downloaded .tar.gz from package_mars_dataset_run.py")
    parser.add_argument("--inventory", help="Inventory JSON from package_mars_dataset_run.py")
    parser.add_argument("--inventory-report", help="Human-readable inventory Markdown report from package_mars_dataset_run.py")
    parser.add_argument("--sha256-file", help="Tarball .sha256 file")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run-progress-audit", action="store_true", help="Run audit_mars_run_progress.py on a temporary extraction")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-frequency-checks", type=int, default=25)
    parser.add_argument("--expected-touchstone-ports", type=int, help="Forwarded to audit_mars_run_progress.py, e.g. 8 for .s8p")
    parser.add_argument("--required-touchstone-extension", help="Forwarded to audit_mars_run_progress.py, e.g. .s8p")
    parser.add_argument(
        "--require-clearance-audit",
        action="store_true",
        help="Require extracted run-progress audit to find final500_ground_clearance_audit.json",
    )
    parser.add_argument(
        "--require-geometry-quality",
        action="store_true",
        help="Require extracted run-progress audit to validate manifest geometry_quality evidence",
    )
    parser.add_argument("--internal-angle-deg", type=float, default=135.0)
    parser.add_argument("--terminal-angle-deg", type=float, default=90.0)
    parser.add_argument("--require-emx-command", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--expected-port-mode")
    parser.add_argument("--expected-pin-purpose", type=int)
    parser.add_argument(
        "--require-quality-gates",
        action="store_true",
        help="Require packaged dataset_quality_gates*/dataset_quality_gates_summary.json files to be PASS",
    )
    parser.add_argument(
        "--require-s8p-quality-gates",
        action="store_true",
        help="Require packaged next-gen S8P physical-feature quality gates, scalar-Q derivation, and validation sample selection to be PASS",
    )
    parser.add_argument(
        "--require-next-gen-s8p-status",
        action="store_true",
        help="Require packaged next-gen S8P run-status and objective-acceptance summaries for completion-traceability.",
    )
    parser.add_argument(
        "--require-run-config",
        action="store_true",
        help="Require packaged final_s8p_physical_feature_500*.yaml so after-import target layout smoke can rebuild geometry.",
    )
    parser.add_argument(
        "--require-hfss-validation-assets",
        action="store_true",
        help="Require packaged selected-sample HFSS handoff/AEDT/payload/postrun assets.",
    )
    parser.add_argument(
        "--require-quality-figures",
        action="store_true",
        help="Require packaged PNG/SVG figures from dataset_quality_gates* for report-ready visual evidence",
    )
    parser.add_argument(
        "--require-progress-evidence",
        action="store_true",
        help="Require packaged mars_run_progress_audit* or mars_run_progress_watch* summary evidence",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _progress_audit_contract_check(args: argparse.Namespace) -> Check:
    expected_fields = {
        "expected_count": args.expected_count,
        "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
        "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
        "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
        "expected_frequency_points": args.expected_frequency_points,
        "expected_touchstone_ports": args.expected_touchstone_ports,
        "required_touchstone_extension": args.required_touchstone_extension,
        "require_clearance_audit": args.require_clearance_audit,
        "require_emx_command": args.require_emx_command,
        "expected_port_mode": args.expected_port_mode,
        "expected_pin_purpose": args.expected_pin_purpose,
    }
    supplied = [name for name, value in expected_fields.items() if value not in (None, False)]
    if supplied and not args.run_progress_audit:
        return Check(
            "FAIL",
            "progress audit contract",
            f"expected run constraints supplied but --run-progress-audit is missing: {supplied}",
        )
    if args.run_progress_audit and supplied:
        return Check("PASS", "progress audit contract", f"run-progress audit will enforce {supplied}")
    if args.run_progress_audit:
        return Check("PASS", "progress audit contract", "run-progress audit enabled without explicit expected constraints")
    return Check("PASS", "progress audit contract", "no run-progress expectations supplied")


def _default_inventory_path(tarball: Path) -> Path:
    return tarball.with_suffix(tarball.suffix + ".inventory.json")


def _default_inventory_report_path(tarball: Path) -> Path:
    return tarball.with_suffix(tarball.suffix + ".inventory.md")


def _default_sha_path(tarball: Path) -> Path:
    return tarball.with_suffix(tarball.suffix + ".sha256")


def _tarball_exists_check(tarball: Path) -> Check:
    if not tarball.exists():
        return Check("FAIL", "tarball exists", f"missing: {tarball}")
    if not tarball.is_file():
        return Check("FAIL", "tarball exists", f"not a file: {tarball}")
    return Check("PASS", "tarball exists", f"{tarball} ({tarball.stat().st_size} bytes)")


def _sha_record_check(tarball: Path, sha_path: Path, actual_tar_sha: str) -> Check:
    if not sha_path.exists():
        return Check("FAIL", "tarball SHA256 file", f"missing: {sha_path}")
    text = sha_path.read_text(encoding="utf-8").strip()
    expected = text.split()[0] if text else ""
    if expected == actual_tar_sha:
        return Check("PASS", "tarball SHA256 file", actual_tar_sha)
    return Check("FAIL", "tarball SHA256 file", f"expected={expected}, actual={actual_tar_sha}")


def _inventory_check(inventory_path: Path, inventory: dict[str, Any], actual_tar_sha: str) -> Check:
    if "_missing" in inventory:
        return Check("FAIL", "inventory JSON", str(inventory["_missing"]))
    if "_parse_error" in inventory:
        return Check("FAIL", "inventory JSON", str(inventory["_parse_error"]))
    failures: list[str] = []
    if inventory.get("tarball_sha256") != actual_tar_sha:
        failures.append("tarball_sha256 mismatch")
    file_count = inventory.get("file_count")
    files = inventory.get("files")
    if not isinstance(files, list):
        failures.append("files is not a list")
    elif file_count != len(files):
        failures.append(f"file_count={file_count}, len(files)={len(files)}")
    if failures:
        return Check("FAIL", "inventory JSON", "; ".join(failures))
    return Check("PASS", "inventory JSON", f"{inventory_path}, file_count={file_count}")


def _inventory_report_check(report_path: Path, inventory: dict[str, Any], actual_tar_sha: str) -> Check:
    if not report_path.exists():
        return Check("FAIL", "inventory Markdown report", f"missing: {report_path}")
    try:
        text = report_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return Check("FAIL", "inventory Markdown report", f"{type(exc).__name__}: {exc}")

    failures: list[str] = []
    required_fragments = [
        "# MARS Dataset Transfer Inventory",
        "## Category Counts",
        "## Boundaries",
        actual_tar_sha,
        str(inventory.get("file_count")),
    ]
    for fragment in required_fragments:
        if fragment not in text:
            failures.append(f"missing fragment: {fragment}")
    category_counts = inventory.get("category_counts")
    if not isinstance(category_counts, dict):
        failures.append("inventory category_counts missing or not object")
    else:
        for key, value in sorted(category_counts.items()):
            row = f"| `{key}` | {value} |"
            if row not in text:
                failures.append(f"missing category row: {row}")
                if len(failures) >= 8:
                    break
    if failures:
        return Check("FAIL", "inventory Markdown report", "; ".join(failures[:8]))
    return Check("PASS", "inventory Markdown report", f"{report_path} matches inventory summary and category counts")


def _tar_inventory_checks(tarball: Path, inventory: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    with tempfile.TemporaryDirectory(prefix="mars_dataset_pkg_") as tmpdir:
        extract_dir = Path(tmpdir)
        try:
            with tarfile.open(tarball, "r:gz") as archive:
                members = archive.getmembers()
                safe, reason = _safe_members(members)
                if not safe:
                    return [Check("FAIL", "tar path safety", reason)]
                names = {member.name for member in members}
                file_names = {member.name for member in members if member.isfile()}
                checks.append(Check("PASS", "tar path safety", f"{len(members)} safe members"))
                checks.append(_tar_metadata_cache_hygiene_check(names))
                checks.append(_tar_duplicate_member_check(members))
                checks.append(_required_tar_members_check(file_names, inventory))
                checks.append(_tar_inventory_exactness_check(file_names, inventory))
                _safe_extractall_compat(archive, extract_dir)
        except Exception as exc:  # noqa: BLE001
            return [Check("FAIL", "tar extraction", f"{type(exc).__name__}: {exc}")]

        checks.append(Check("PASS", "tar extraction", f"extracted to temporary directory from {tarball.name}"))
        checks.append(_inventory_file_hash_check(extract_dir, inventory))
        checks.append(_inventory_nonempty_files_check(inventory))
        checks.append(_inventory_category_counts_check(inventory))
        if args.require_run_config:
            checks.append(_run_config_inventory_check(inventory))
        if args.require_quality_figures:
            checks.append(_quality_figures_check(inventory))
        if args.require_hfss_validation_assets:
            checks.append(_hfss_validation_assets_inventory_check(inventory))
        checks.append(_expected_count_category_evidence_check(inventory, args.expected_count))
        if args.require_clearance_audit:
            checks.append(_clearance_audit_inventory_check(inventory))
        run_root = _extracted_run_root(extract_dir, inventory)
        if run_root is None:
            checks.append(Check("FAIL", "extracted run root", "could not infer run root from inventory"))
        else:
            checks.append(Check("PASS", "extracted run root", str(run_root)))
            if args.run_progress_audit:
                checks.append(_run_progress_audit(run_root, args))
            if args.require_progress_evidence:
                checks.append(_progress_evidence_check(run_root))
            if args.require_quality_gates:
                checks.append(_quality_gates_check(run_root))
            if args.require_s8p_quality_gates:
                checks.append(_s8p_quality_gates_check(run_root))
            if args.require_next_gen_s8p_status:
                checks.append(_next_gen_s8p_status_check(run_root))
    return checks


def _safe_members(members: list[tarfile.TarInfo]) -> tuple[bool, str]:
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            return False, f"unsafe tar member path: {member.name}"
        if member.issym() or member.islnk():
            return False, f"tar member link is not allowed: {member.name}"
    return True, "safe"


def _safe_extractall_compat(archive: tarfile.TarFile, destination: Path) -> None:
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        # Python < 3.12 has no extraction filter argument. Path/link safety is
        # already enforced by _safe_members before this fallback is reached.
        archive.extractall(destination)


def _required_tar_members_check(names: set[str], inventory: dict[str, Any]) -> Check:
    required = {
        str(item.get("relative_to_run_parent", ""))
        for item in inventory.get("files", [])
        if isinstance(item, dict)
    }
    missing = sorted(item for item in required if item and item not in names)
    base_required = sorted(name for name in required if name.endswith(("dataset_manifest.json", "dataset_rows.csv")))
    if missing:
        return Check("FAIL", "tar inventory members", f"missing={missing[:8]}")
    if len(base_required) < 2:
        return Check("FAIL", "tar inventory members", "dataset_manifest.json or dataset_rows.csv missing from inventory")
    return Check("PASS", "tar inventory members", f"{len(required)} inventory files present in tar")


def _tar_inventory_exactness_check(file_names: set[str], inventory: dict[str, Any]) -> Check:
    required = {
        str(item.get("relative_to_run_parent", ""))
        for item in inventory.get("files", [])
        if isinstance(item, dict) and str(item.get("relative_to_run_parent", ""))
    }
    extra = sorted(file_names - required)
    missing = sorted(required - file_names)
    if extra or missing:
        return Check("FAIL", "tar inventory exactness", f"extra={extra[:8]}, missing={missing[:8]}")
    return Check("PASS", "tar inventory exactness", f"{len(file_names)} regular files exactly match inventory")


def _tar_metadata_cache_hygiene_check(names: set[str]) -> Check:
    offenders = sorted(name for name in names if _is_metadata_or_cache_member(name))
    if offenders:
        return Check("FAIL", "tar metadata/cache hygiene", f"metadata or generated cache members={offenders[:8]}")
    return Check("PASS", "tar metadata/cache hygiene", "no __MACOSX, .DS_Store, AppleDouble, __pycache__, .pyc, or .pyo members")


def _tar_duplicate_member_check(members: list[tarfile.TarInfo]) -> Check:
    seen: set[str] = set()
    duplicates: list[str] = []
    for member in members:
        if not member.isfile():
            continue
        if member.name in seen:
            duplicates.append(member.name)
            continue
        seen.add(member.name)
    if duplicates:
        return Check("FAIL", "tar duplicate member hygiene", f"duplicate regular-file members={duplicates[:8]}")
    return Check("PASS", "tar duplicate member hygiene", f"{len(seen)} unique regular-file members")


def _is_metadata_or_cache_member(name: str) -> bool:
    parts = [part for part in name.split("/") if part]
    return any(
        part == "__MACOSX"
        or part == ".DS_Store"
        or part.startswith("._")
        or part == "__pycache__"
        for part in parts
    ) or name.endswith((".pyc", ".pyo"))


def _inventory_file_hash_check(extract_dir: Path, inventory: dict[str, Any]) -> Check:
    failures: list[str] = []
    checked = 0
    for item in inventory.get("files", []):
        if not isinstance(item, dict):
            failures.append("non-object inventory file entry")
            continue
        rel = str(item.get("relative_to_run_parent", ""))
        if not rel:
            failures.append("empty relative_to_run_parent")
            continue
        path = extract_dir / rel
        if not path.exists():
            failures.append(f"{rel}: missing after extraction")
            continue
        expected_size = item.get("size_bytes")
        if expected_size is not None and int(expected_size) != path.stat().st_size:
            failures.append(f"{rel}: size expected={expected_size}, actual={path.stat().st_size}")
            continue
        expected_sha = item.get("sha256")
        if expected_sha and _sha256(path) != expected_sha:
            failures.append(f"{rel}: sha mismatch")
            continue
        checked += 1
    if failures:
        return Check("FAIL", "inventory file hashes", "; ".join(failures[:8]))
    return Check("PASS", "inventory file hashes", f"{checked} files matched inventory SHA/size")


def _inventory_nonempty_files_check(inventory: dict[str, Any]) -> Check:
    empty: list[str] = []
    checked = 0
    for item in inventory.get("files", []):
        if not isinstance(item, dict):
            continue
        rel = str(item.get("relative_to_run_parent", ""))
        try:
            size = int(item.get("size_bytes"))
        except (TypeError, ValueError):
            empty.append(f"{rel or '<missing path>'}: invalid size={item.get('size_bytes')!r}")
            continue
        checked += 1
        if size <= 0:
            empty.append(rel or "<missing path>")
    if empty:
        return Check("FAIL", "inventory non-empty files", f"empty_or_invalid={empty[:8]}")
    return Check("PASS", "inventory non-empty files", f"{checked} inventory files are non-empty")


def _inventory_category_counts_check(inventory: dict[str, Any]) -> Check:
    expected = inventory.get("category_counts")
    if not isinstance(expected, dict):
        return Check("FAIL", "inventory category counts", "category_counts missing or not an object")
    actual = _category_counts_from_inventory(inventory)
    mismatches = {
        key: {"expected": expected.get(key), "actual": value}
        for key, value in actual.items()
        if expected.get(key) != value
    }
    extra_keys = sorted(set(expected) - set(actual))
    if mismatches or extra_keys:
        detail = f"mismatches={mismatches}; extra_keys={extra_keys}"
        return Check("FAIL", "inventory category counts", detail[:800])
    return Check("PASS", "inventory category counts", ", ".join(f"{key}={value}" for key, value in actual.items()))


def _expected_count_category_evidence_check(inventory: dict[str, Any], expected_count: int | None) -> Check:
    if expected_count is None:
        return Check("PASS", "inventory expected-count evidence", "no --expected-count supplied")
    if expected_count < 0:
        return Check("FAIL", "inventory expected-count evidence", f"--expected-count must be nonnegative: {expected_count}")
    actual = _category_counts_from_inventory(inventory)
    minimums = {
        "dataset_manifest": 1,
        "dataset_rows": 1,
        "evaluation_summaries": expected_count,
        "touchstone_files": expected_count,
        "emx_command_files": expected_count,
        "layout_json_files": expected_count,
    }
    failures = [
        f"{key}={actual.get(key, 0)} < {minimum}"
        for key, minimum in minimums.items()
        if actual.get(key, 0) < minimum
    ]
    if failures:
        return Check("FAIL", "inventory expected-count evidence", "; ".join(failures))
    return Check(
        "PASS",
        "inventory expected-count evidence",
        ", ".join(f"{key}>={minimum}" for key, minimum in minimums.items()),
    )


def _clearance_audit_inventory_check(inventory: dict[str, Any]) -> Check:
    actual = _category_counts_from_inventory(inventory)
    count = int(actual.get("clearance_audit_files", 0))
    if count <= 0:
        return Check(
            "FAIL",
            "inventory clearance-audit evidence",
            "clearance_audit_files=0; final500_ground_clearance_audit.json is required",
        )
    return Check("PASS", "inventory clearance-audit evidence", f"clearance_audit_files={count}")


def _run_config_inventory_check(inventory: dict[str, Any]) -> Check:
    actual = _category_counts_from_inventory(inventory)
    count = int(actual.get("run_config_files", 0))
    if count <= 0:
        return Check(
            "FAIL",
            "packaged final S8P run config",
            "run_config_files=0; final_s8p_physical_feature_500*.yaml is required for after-import target layout smoke",
        )
    return Check("PASS", "packaged final S8P run config", f"run_config_files={count}")


def _quality_figures_check(inventory: dict[str, Any]) -> Check:
    actual = _category_counts_from_inventory(inventory)
    figure_count = int(actual.get("quality_gate_figure_files", 0))
    include_flag = bool(inventory.get("include_quality_figures"))
    if figure_count <= 0:
        return Check(
            "FAIL",
            "packaged quality-gate figures",
            f"quality_gate_figure_files={figure_count}, include_quality_figures={include_flag}",
        )
    return Check(
        "PASS",
        "packaged quality-gate figures",
        f"quality_gate_figure_files={figure_count}, include_quality_figures={include_flag}",
    )


def _hfss_validation_assets_inventory_check(inventory: dict[str, Any]) -> Check:
    actual = _category_counts_from_inventory(inventory)
    asset_count = int(actual.get("hfss_validation_asset_files", 0))
    script_count = int(actual.get("hfss_validation_script_files", 0))
    include_flag = bool(inventory.get("include_hfss_validation_assets"))
    failures = []
    if asset_count <= 0:
        failures.append(f"hfss_validation_asset_files={asset_count}")
    if script_count <= 0:
        failures.append(f"hfss_validation_script_files={script_count}")
    if not include_flag:
        failures.append(f"include_hfss_validation_assets={include_flag}")
    if failures:
        return Check("FAIL", "packaged HFSS validation assets", ", ".join(failures))
    return Check(
        "PASS",
        "packaged HFSS validation assets",
        f"hfss_validation_asset_files={asset_count}, hfss_validation_script_files={script_count}, include_hfss_validation_assets={include_flag}",
    )


def _category_counts_from_inventory(inventory: dict[str, Any]) -> dict[str, int]:
    counts = {
        "dataset_manifest": 0,
        "dataset_rows": 0,
        "run_config_files": 0,
        "evaluation_summaries": 0,
        "touchstone_files": 0,
        "emx_command_files": 0,
        "layout_json_files": 0,
        "clearance_audit_files": 0,
        "gds_files": 0,
        "layout_preview_files": 0,
        "quality_gate_top_summaries": 0,
        "quality_gate_summary_files": 0,
        "quality_gate_report_files": 0,
        "quality_gate_csv_files": 0,
        "quality_gate_figure_files": 0,
        "progress_audit_summary_files": 0,
        "progress_audit_report_files": 0,
        "progress_audit_csv_files": 0,
        "progress_watch_summary_files": 0,
        "progress_watch_history_files": 0,
        "progress_watch_snapshot_files": 0,
        "next_gen_run_status_summary_files": 0,
        "next_gen_run_status_report_files": 0,
        "next_gen_run_status_csv_files": 0,
        "objective_acceptance_summary_files": 0,
        "objective_acceptance_report_files": 0,
        "objective_acceptance_csv_files": 0,
        "hfss_validation_asset_files": 0,
        "hfss_validation_touchstone_files": 0,
        "hfss_validation_script_files": 0,
    }
    for item in inventory.get("files", []):
        if not isinstance(item, dict):
            continue
        rel_parent = str(item.get("relative_to_run_parent", ""))
        rel_path = Path(rel_parent)
        rel = Path(*rel_path.parts[1:]) if len(rel_path.parts) > 1 else rel_path
        rel_text = rel.as_posix()
        quality_gate_file = bool(rel.parts and rel.parts[0].startswith("dataset_quality_gates"))
        progress_audit_file = bool(rel.parts and rel.parts[0].startswith("mars_run_progress_audit"))
        progress_watch_file = bool(rel.parts and rel.parts[0].startswith("mars_run_progress_watch"))
        next_gen_status_file = any(part.startswith("next_gen_s8p_mars_run_status") for part in rel.parts)
        objective_acceptance_file = any(part.startswith("next_gen_s8p_objective_acceptance") for part in rel.parts)
        hfss_validation_asset_file = _is_hfss_validation_asset(rel)
        if rel_text == "dataset_manifest.json":
            counts["dataset_manifest"] += 1
        if rel_text == "dataset_rows.csv":
            counts["dataset_rows"] += 1
        if rel_text.startswith("final_s8p_physical_feature_500") and rel.suffix.lower() in {".yaml", ".yml"}:
            counts["run_config_files"] += 1
        if rel.match("evaluations/*/summary.json"):
            counts["evaluation_summaries"] += 1
        if rel.match("evaluations/*/emx/*.s*p"):
            counts["touchstone_files"] += 1
        if rel.match("evaluations/*/emx/emx_command.json"):
            counts["emx_command_files"] += 1
        if rel.match("evaluations/*/layout/*.json") or rel.match("evaluations/*/*.layout.json"):
            counts["layout_json_files"] += 1
        if rel_text == "final500_ground_clearance_audit.json":
            counts["clearance_audit_files"] += 1
        if rel.match("evaluations/*/layout/*.gds") or rel.match("evaluations/*/*.gds"):
            counts["gds_files"] += 1
        if (
            rel.match("evaluations/*/layout/*.png")
            or rel.match("evaluations/*/layout/*.svg")
            or rel.match("evaluations/*/*.png")
            or rel.match("evaluations/*/*.svg")
        ):
            counts["layout_preview_files"] += 1
        if quality_gate_file and rel.match("dataset_quality_gates*/dataset_quality_gates_summary.json"):
            counts["quality_gate_top_summaries"] += 1
        if quality_gate_file and rel.suffix == ".json" and "summary" in rel.name:
            counts["quality_gate_summary_files"] += 1
        if quality_gate_file and rel.suffix == ".md" and "report" in rel.name:
            counts["quality_gate_report_files"] += 1
        if quality_gate_file and rel.suffix == ".csv":
            counts["quality_gate_csv_files"] += 1
        if quality_gate_file and rel.suffix.lower() in {".png", ".svg"}:
            counts["quality_gate_figure_files"] += 1
        if progress_audit_file and rel.name == "mars_run_progress_summary.json":
            counts["progress_audit_summary_files"] += 1
        if progress_audit_file and rel.name == "mars_run_progress_report.md":
            counts["progress_audit_report_files"] += 1
        if progress_audit_file and rel.suffix == ".csv":
            counts["progress_audit_csv_files"] += 1
        if progress_watch_file and rel.name == "mars_run_progress_watch_summary.json":
            counts["progress_watch_summary_files"] += 1
        if progress_watch_file and rel.name in {"mars_run_progress_watch_history.csv", "mars_run_progress_watch_history.jsonl"}:
            counts["progress_watch_history_files"] += 1
        if progress_watch_file and "snapshots" in rel.parts:
            counts["progress_watch_snapshot_files"] += 1
        if next_gen_status_file and rel.name == "next_gen_s8p_mars_run_status_summary.json":
            counts["next_gen_run_status_summary_files"] += 1
        if next_gen_status_file and rel.suffix == ".md" and "report" in rel.name:
            counts["next_gen_run_status_report_files"] += 1
        if next_gen_status_file and rel.suffix == ".csv":
            counts["next_gen_run_status_csv_files"] += 1
        if objective_acceptance_file and rel.name == "next_gen_s8p_objective_acceptance_summary.json":
            counts["objective_acceptance_summary_files"] += 1
        if objective_acceptance_file and rel.suffix == ".md":
            counts["objective_acceptance_report_files"] += 1
        if objective_acceptance_file and rel.suffix == ".csv":
            counts["objective_acceptance_csv_files"] += 1
        if hfss_validation_asset_file:
            counts["hfss_validation_asset_files"] += 1
        if hfss_validation_asset_file and rel.suffix.lower() in {".s8p", ".s4p"}:
            counts["hfss_validation_touchstone_files"] += 1
        if hfss_validation_asset_file and rel.suffix.lower() in {".py", ".ps1", ".sh"}:
            counts["hfss_validation_script_files"] += 1
    return counts


def _is_hfss_validation_asset(rel: Path) -> bool:
    parts = rel.parts
    if not parts or not parts[0].startswith("dataset_quality_gates"):
        return False
    return any(
        part
        in {
            "selected_s8p_hfss_handoff",
            "selected_s8p_hfss_aedt_scripts",
            "selected_s8p_hfss_payload_views",
            "selected_s8p_hfss_postrun_validation",
        }
        for part in parts
    )


def _extracted_run_root(extract_dir: Path, inventory: dict[str, Any]) -> Path | None:
    run_name = Path(str(inventory.get("run_dir", ""))).name
    if run_name and (extract_dir / run_name).is_dir():
        return extract_dir / run_name
    roots = {
        Path(str(item.get("relative_to_run_parent", ""))).parts[0]
        for item in inventory.get("files", [])
        if isinstance(item, dict) and str(item.get("relative_to_run_parent", ""))
    }
    if len(roots) == 1:
        candidate = extract_dir / next(iter(roots))
        if candidate.is_dir():
            return candidate
    return None


def _run_progress_audit(run_root: Path, args: argparse.Namespace) -> Check:
    out_dir = run_root.parent / "mars_run_progress_audit_from_package"
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "audit_mars_run_progress.py"),
        str(run_root),
        "--out-dir",
        str(out_dir),
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--max-touchstone-frequency-checks",
        str(args.max_touchstone_frequency_checks),
    ]
    _append_optional(cmd, "--expected-count", args.expected_count)
    _append_optional(cmd, "--expected-frequency-start-ghz", args.expected_frequency_start_ghz)
    _append_optional(cmd, "--expected-frequency-stop-ghz", args.expected_frequency_stop_ghz)
    _append_optional(cmd, "--expected-frequency-step-ghz", args.expected_frequency_step_ghz)
    _append_optional(cmd, "--expected-frequency-points", args.expected_frequency_points)
    _append_optional(cmd, "--expected-touchstone-ports", args.expected_touchstone_ports)
    _append_optional(cmd, "--required-touchstone-extension", args.required_touchstone_extension)
    if args.require_clearance_audit:
        cmd.append("--require-clearance-audit")
    if args.require_geometry_quality:
        cmd.extend(
            [
                "--require-geometry-quality",
                "--internal-angle-deg",
                str(args.internal_angle_deg),
                "--terminal-angle-deg",
                str(args.terminal_angle_deg),
            ]
        )
    if args.require_emx_command:
        cmd.append("--require-emx-command")
    _append_optional(cmd, "--expected-port-mode", args.expected_port_mode)
    _append_optional(cmd, "--expected-pin-purpose", args.expected_pin_purpose)
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    summary_path = out_dir / "mars_run_progress_summary.json"
    summary = _read_json(summary_path)
    if completed.returncode == 0 and summary.get("overall_status") == "PASS":
        return Check("PASS", "extracted run progress audit", f"PASS: {summary_path}")
    detail = _progress_failure_detail(completed, summary)
    return Check("FAIL", "extracted run progress audit", detail)


def _quality_gates_check(run_root: Path) -> Check:
    summary_paths = sorted(run_root.glob("dataset_quality_gates*/dataset_quality_gates_summary.json"))
    if not summary_paths:
        return Check(
            "FAIL",
            "packaged dataset quality gates",
            "missing dataset_quality_gates*/dataset_quality_gates_summary.json",
        )
    raw_clearance_path = run_root / "final500_ground_clearance_audit.json"
    if not raw_clearance_path.is_file():
        return Check(
            "FAIL",
            "packaged dataset quality gates",
            "missing raw final500_ground_clearance_audit.json required for clearance traceability",
        )

    failures: list[str] = []
    pass_count = 0
    for summary_path in summary_paths:
        summary = _read_json(summary_path)
        label = str(summary_path.relative_to(run_root))
        if summary.get("_missing") or summary.get("_parse_error"):
            failures.append(f"{label}: {summary.get('_missing') or summary.get('_parse_error')}")
            continue
        if summary.get("overall_status") != "PASS":
            failures.append(f"{label}: overall_status={summary.get('overall_status')!r}")
            continue
        steps = summary.get("steps")
        if not isinstance(steps, list) or not steps:
            failures.append(f"{label}: steps missing or empty")
            continue
        bad_steps = [
            str(step.get("name", f"step_{index}"))
            for index, step in enumerate(steps)
            if not isinstance(step, dict) or step.get("status") != "PASS"
        ]
        if bad_steps:
            failures.append(f"{label}: non-PASS steps={bad_steps[:5]}")
            continue
        clearance_failures = _quality_gate_clearance_contract_failures(summary, label)
        if clearance_failures:
            failures.extend(clearance_failures)
            continue
        pass_count += 1

    if failures:
        return Check("FAIL", "packaged dataset quality gates", "; ".join(failures[:5]))
    return Check("PASS", "packaged dataset quality gates", f"{pass_count} quality-gate summary file(s) PASS with required clearance audit")


def _s8p_quality_gates_check(run_root: Path) -> Check:
    summary_paths = sorted(run_root.glob("dataset_quality_gates*/dataset_quality_gates_summary.json"))
    if not summary_paths:
        return Check(
            "FAIL",
            "packaged S8P physical-feature quality gates",
            "missing dataset_quality_gates*/dataset_quality_gates_summary.json",
        )
    required_steps = {
        "S8P physical-feature dataset audit",
        "scalar Q feature derivation",
        "physical-feature validation sample selection",
    }
    failures: list[str] = []
    pass_count = 0
    for summary_path in summary_paths:
        summary = _read_json(summary_path)
        label = str(summary_path.relative_to(run_root))
        if summary.get("_missing") or summary.get("_parse_error"):
            failures.append(f"{label}: {summary.get('_missing') or summary.get('_parse_error')}")
            continue
        if summary.get("overall_status") != "PASS":
            failures.append(f"{label}: overall_status={summary.get('overall_status')!r}")
            continue
        steps = summary.get("steps")
        if not isinstance(steps, list) or not steps:
            failures.append(f"{label}: steps missing or empty")
            continue
        step_by_name = {str(step.get("name", "")).strip(): step for step in steps if isinstance(step, dict)}
        missing_steps = sorted(required_steps - set(step_by_name))
        bad_steps = sorted(name for name in required_steps & set(step_by_name) if step_by_name[name].get("status") != "PASS")
        if missing_steps or bad_steps:
            failures.append(f"{label}: missing_steps={missing_steps}, non_pass_required_steps={bad_steps}")
            continue
        artifact_failures = _s8p_quality_artifact_failures(summary_path.parent)
        if artifact_failures:
            failures.extend(f"{label}: {item}" for item in artifact_failures)
            continue
        pass_count += 1
    if failures:
        return Check("FAIL", "packaged S8P physical-feature quality gates", "; ".join(failures[:6]))
    return Check(
        "PASS",
        "packaged S8P physical-feature quality gates",
        f"{pass_count} S8P physical-feature quality-gate summary file(s) PASS with scalar-Q and selected-sample evidence",
    )


def _s8p_quality_artifact_failures(quality_dir: Path) -> list[str]:
    artifact_specs = [
        (
            quality_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_dataset_audit_summary.json",
            "S8P audit summary",
        ),
        (
            quality_dir / "scalar_q_feature_dataset" / "scalar_q_feature_summary.json",
            "scalar-Q summary",
        ),
        (
            quality_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_sample_summary.json",
            "validation sample summary",
        ),
    ]
    failures: list[str] = []
    for path, label in artifact_specs:
        data = _read_json(path)
        if data.get("_missing") or data.get("_parse_error"):
            failures.append(f"{label} missing/unreadable: {path}")
        elif data.get("overall_status") != "PASS":
            failures.append(f"{label} overall_status={data.get('overall_status')!r}")
    selected_csv = quality_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_samples.csv"
    if not selected_csv.is_file() or selected_csv.stat().st_size <= 0:
        failures.append(f"validation sample CSV missing/empty: {selected_csv}")
    return failures


def _next_gen_s8p_status_check(run_root: Path) -> Check:
    run_status_paths = sorted(run_root.glob("**/next_gen_s8p_mars_run_status_summary.json"))
    objective_paths = sorted(run_root.glob("**/next_gen_s8p_objective_acceptance_summary.json"))
    failures: list[str] = []
    if not run_status_paths:
        failures.append("missing next_gen_s8p_mars_run_status_summary.json")
    if not objective_paths:
        failures.append("missing next_gen_s8p_objective_acceptance_summary.json")
    run_status_summaries = [_read_json(path) for path in run_status_paths]
    objective_summaries = [_read_json(path) for path in objective_paths]
    bad_run_status = [
        f"{path.relative_to(run_root)}: overall_status={summary.get('overall_status')!r}"
        for path, summary in zip(run_status_paths, run_status_summaries)
        if summary.get("_missing")
        or summary.get("_parse_error")
        or str(summary.get("overall_status") or "") in {"", "NOT_READY"}
    ]
    bad_objective = [
        f"{path.relative_to(run_root)}: overall_status={summary.get('overall_status')!r}, decision={summary.get('decision')!r}"
        for path, summary in zip(objective_paths, objective_summaries)
        if summary.get("_missing")
        or summary.get("_parse_error")
        or str(summary.get("overall_status") or "") == "FAIL"
    ]
    failures.extend(bad_run_status)
    failures.extend(bad_objective)
    if failures:
        return Check("FAIL", "packaged next-gen S8P status evidence", "; ".join(failures[:6]))
    run_statuses = [summary.get("overall_status") for summary in run_status_summaries]
    objective_statuses = [summary.get("overall_status") for summary in objective_summaries]
    return Check(
        "PASS",
        "packaged next-gen S8P status evidence",
        f"run_statuses={run_statuses}, objective_statuses={objective_statuses}",
    )


def _quality_gate_clearance_contract_failures(summary: dict[str, Any], label: str) -> list[str]:
    arguments = summary.get("arguments")
    if not isinstance(arguments, dict) or arguments.get("require_clearance_audit") is not True:
        return [f"{label}: arguments.require_clearance_audit is not true"]
    steps = summary.get("steps") if isinstance(summary.get("steps"), list) else []
    geometry_steps = [
        step
        for step in steps
        if isinstance(step, dict) and str(step.get("name", "")).strip().lower() == "geometry quality audit"
    ]
    if not geometry_steps:
        return [f"{label}: geometry quality audit step missing"]
    failures: list[str] = []
    for step in geometry_steps:
        command_text = _command_text(step.get("command"))
        if "--require-clearance-audit" not in command_text:
            failures.append(f"{label}: geometry quality audit missing --require-clearance-audit")
    return failures


def _command_text(command: Any) -> str:
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    if isinstance(command, str):
        return command
    return ""


def _progress_evidence_check(run_root: Path) -> Check:
    progress_summaries = sorted(run_root.glob("mars_run_progress_audit*/**/mars_run_progress_summary.json"))
    watch_summaries = sorted(run_root.glob("mars_run_progress_watch*/**/mars_run_progress_watch_summary.json"))
    summaries = progress_summaries + watch_summaries
    if not summaries:
        return Check(
            "FAIL",
            "packaged run progress evidence",
            "missing mars_run_progress_audit*/mars_run_progress_summary.json or mars_run_progress_watch*/mars_run_progress_watch_summary.json",
        )

    statuses: list[str] = []
    pass_count = 0
    for summary_path in summaries:
        summary = _read_json(summary_path)
        label = str(summary_path.relative_to(run_root))
        if summary.get("_missing") or summary.get("_parse_error"):
            statuses.append(f"{label}: {summary.get('_missing') or summary.get('_parse_error')}")
            continue
        status = str(summary.get("overall_status", "UNKNOWN"))
        statuses.append(f"{label}: {status}")
        if status == "PASS":
            pass_count += 1
    if pass_count:
        return Check("PASS", "packaged run progress evidence", f"{pass_count}/{len(summaries)} progress/watch summary file(s) PASS")
    return Check("FAIL", "packaged run progress evidence", "; ".join(statuses[:5]))


def _append_optional(cmd: list[str], flag: str, value: Any) -> None:
    if value is not None:
        cmd.extend([flag, str(value)])


def _progress_failure_detail(completed: subprocess.CompletedProcess[str], summary: dict[str, Any]) -> str:
    failures = [
        f"{item.get('name')}: {item.get('detail')}"
        for item in summary.get("checks", [])
        if item.get("status") != "PASS"
    ]
    if failures:
        return "; ".join(failures[:5])
    if summary.get("_missing") or summary.get("_parse_error"):
        return str(summary.get("_missing") or summary.get("_parse_error"))
    return (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()[-500:]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS Dataset Package Verification",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Tarball: `{summary['tarball']}`",
        f"- Inventory: `{summary['inventory']}`",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    category_counts = summary.get("inventory_category_counts")
    if isinstance(category_counts, dict):
        lines.extend(
            [
                "",
                "## Inventory Category Counts",
                "",
                "| Category | Count |",
                "| --- | ---: |",
            ]
        )
        for key in sorted(category_counts):
            lines.append(f"| `{key}` | {category_counts[key]} |")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- This verifies transfer-package integrity and local filesystem evidence only.",
            "- It does not claim EMX/HFSS/ADS agreement, Zin coverage, or training readiness.",
        ]
    )
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
