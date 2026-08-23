#!/usr/bin/env python3
"""Safely import a MARS validation export bundle.

The MARS handoff script creates ``mars_validation_export_latest.tar.gz`` as a
single transfer artifact containing the corrected Zin acquisition plan and,
when available, the real wideband EMX S4P. This importer is intentionally
conservative: it verifies an optional SHA256 file, extracts without allowing
tar path traversal, copies known artifacts into local working locations, and
writes a traceable JSON/Markdown summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SAMPLE_ID = "ec6698dfc575950b"
DEFAULT_EMX_NAME = f"{DEFAULT_SAMPLE_ID}_MARS_EMX_WIDEBAND_5_50GHz_step0p1.s4p"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bundle = Path(args.bundle).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    local_emx = Path(args.local_emx).expanduser().resolve() if args.local_emx else None
    local_zin_plan_dir = Path(args.local_zin_plan_dir).expanduser().resolve() if args.local_zin_plan_dir else None
    local_zin_uniformity_audit_dir = (
        Path(args.local_zin_uniformity_audit_dir).expanduser().resolve()
        if args.local_zin_uniformity_audit_dir
        else None
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, str]] = []
    checks.append(_check("PASS" if bundle.is_file() else "FAIL", "bundle exists", str(bundle)))
    bundle_sha = _sha256(bundle) if bundle.is_file() else None
    checks.append(_check("PASS" if bundle_sha else "FAIL", "bundle sha256 computed", bundle_sha or "missing"))

    expected_sha = _read_expected_sha(args)
    if expected_sha:
        checks.append(
            _check(
                "PASS" if bundle_sha == expected_sha else "FAIL",
                "bundle sha256 matches expected",
                f"actual={bundle_sha}, expected={expected_sha}",
            )
        )

    extracted_root: Path | None = None
    extract_error: str | None = None
    if bundle.is_file():
        try:
            extracted_root = _safe_extract(bundle, out_dir)
            checks.append(_check("PASS", "safe bundle extraction", str(extracted_root)))
        except Exception as exc:  # noqa: BLE001 - record exact import failure.
            extract_error = f"{type(exc).__name__}: {exc}"
            checks.append(_check("FAIL", "safe bundle extraction", extract_error))

    zin_plan = _find_zin_plan(out_dir) if extracted_root else None
    zin_uniformity_audit = _find_zin_uniformity_audit(out_dir) if extracted_root else None
    emx_s4p = _find_emx_s4p(out_dir, args.expected_sample_id) if extracted_root else None
    export_manifest = _find_export_manifest(out_dir) if extracted_root else None

    checks.append(_check("PASS" if zin_plan else "FAIL", "zin plan directory", str(zin_plan) if zin_plan else "missing"))
    for rel in (
        "zin_balanced_acquisition_plan_summary.json",
        "zin_balanced_acquisition_plan_verification_summary.json",
        "zin_balanced_acquisition_targets.csv",
        "zin_balanced_acquisition_bins.csv",
        "01_zin_bin_deficit_heatmap.png",
        "02_next_zin_targets_overlay.png",
    ):
        path = zin_plan / rel if zin_plan else None
        checks.append(_check("PASS" if path and path.is_file() else "FAIL", f"zin plan file {rel}", str(path) if path else "missing"))

    zin_uniformity_status = "PASS" if zin_uniformity_audit else ("FAIL" if args.require_zin_uniformity_audit else "WARN")
    checks.append(
        _check(
            zin_uniformity_status,
            "Zin uniformity audit directory",
            str(zin_uniformity_audit) if zin_uniformity_audit else "missing",
        )
    )
    for rel in (
        "zin_coverage_audit_summary.json",
        "zin_coverage_bins.csv",
        "zin_center_scatter.png",
        "zin_center_histograms.png",
    ):
        path = zin_uniformity_audit / rel if zin_uniformity_audit else None
        status = "PASS" if path and path.is_file() else ("FAIL" if args.require_zin_uniformity_audit else "WARN")
        checks.append(_check(status, f"Zin uniformity audit file {rel}", str(path) if path else "missing"))

    emx_status = "PASS" if emx_s4p else ("FAIL" if args.require_emx else "WARN")
    checks.append(_check(emx_status, "wideband EMX S4P in bundle", str(emx_s4p) if emx_s4p else "missing"))
    checks.append(_check("PASS" if export_manifest else "WARN", "export manifest", str(export_manifest) if export_manifest else "missing"))

    copied: dict[str, str] = {}
    if emx_s4p and local_emx:
        local_emx.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(emx_s4p, local_emx)
        copied["emx_s4p"] = str(local_emx)
        checks.append(_check("PASS", "copied EMX S4P", str(local_emx)))
    if zin_plan and local_zin_plan_dir:
        if local_zin_plan_dir.exists():
            shutil.rmtree(local_zin_plan_dir)
        shutil.copytree(zin_plan, local_zin_plan_dir)
        copied["zin_plan"] = str(local_zin_plan_dir)
        checks.append(_check("PASS", "copied Zin plan", str(local_zin_plan_dir)))
    if zin_uniformity_audit and local_zin_uniformity_audit_dir:
        if local_zin_uniformity_audit_dir.exists():
            shutil.rmtree(local_zin_uniformity_audit_dir)
        shutil.copytree(zin_uniformity_audit, local_zin_uniformity_audit_dir)
        copied["zin_uniformity_audit"] = str(local_zin_uniformity_audit_dir)
        checks.append(_check("PASS", "copied Zin uniformity audit", str(local_zin_uniformity_audit_dir)))

    overall_status = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "ACCEPT_MARS_VALIDATION_EXPORT_BUNDLE" if overall_status == "PASS" else "REJECT_MARS_VALIDATION_EXPORT_BUNDLE",
        "bundle": str(bundle),
        "bundle_sha256": bundle_sha,
        "expected_sha256": expected_sha,
        "out_dir": str(out_dir),
        "extracted_root": str(extracted_root) if extracted_root else None,
        "extract_error": extract_error,
        "zin_plan_dir": str(zin_plan) if zin_plan else None,
        "zin_uniformity_audit_dir": str(zin_uniformity_audit) if zin_uniformity_audit else None,
        "emx_s4p": str(emx_s4p) if emx_s4p else None,
        "export_manifest": str(export_manifest) if export_manifest else None,
        "copied": copied,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This importer verifies and copies a returned bundle; it does not run EMX, HFSS, or ADS.",
            "The EMX S4P grid is validated later by discover_and_verify_mars_emx_return.py.",
            "The Zin plan concentration gate is validated later by publish_verified_zin_balanced_plan.py.",
            "The final Zin uniformity gate is validated later by publish_verified_zin_uniformity_result.py.",
        ],
    }
    out_json = out_dir / "mars_validation_export_import_summary.json"
    out_md = out_dir / "MARS_VALIDATION_EXPORT_IMPORT_REPORT.md"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    out_md.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={out_json}")
    print(f"report={out_md}")
    for check in checks:
        print(f"{check['status']:5s} {check['name']}: {check['detail']}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle")
    parser.add_argument("--sha256-file")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--local-emx")
    parser.add_argument("--local-zin-plan-dir")
    parser.add_argument("--local-zin-uniformity-audit-dir")
    parser.add_argument("--expected-sample-id", default=DEFAULT_SAMPLE_ID)
    parser.add_argument("--require-emx", action="store_true")
    parser.add_argument("--require-zin-uniformity-audit", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_expected_sha(args: argparse.Namespace) -> str | None:
    if args.expected_sha256:
        return str(args.expected_sha256).strip().split()[0]
    if args.sha256_file:
        path = Path(args.sha256_file).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8").strip().split()[0]
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(bundle: Path, out_dir: Path) -> Path:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            _validate_member(member, out_dir)
        tar.extractall(out_dir, members=members, filter="data")
    top_dirs = [path for path in out_dir.iterdir() if path.is_dir()]
    if len(top_dirs) == 1:
        return top_dirs[0]
    return out_dir


def _validate_member(member: tarfile.TarInfo, out_dir: Path) -> None:
    name = member.name
    if name.startswith("/") or name == "" or Path(name).is_absolute():
        raise ValueError(f"unsafe absolute tar path: {name!r}")
    target = (out_dir / name).resolve()
    if not _is_relative_to(target, out_dir.resolve()):
        raise ValueError(f"unsafe tar path traversal: {name!r}")
    if member.issym() or member.islnk():
        raise ValueError(f"unsafe tar link member: {name!r}")
    if not (member.isdir() or member.isfile()):
        raise ValueError(f"unsupported tar member type: {name!r}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _find_zin_plan(root: Path) -> Path | None:
    candidates = [path for path in root.rglob("zin_balanced_acquisition_plan_summary.json") if path.is_file()]
    for summary in candidates:
        parent = summary.parent
        if parent.name == "zin_plan":
            return parent
    return candidates[0].parent if candidates else None


def _find_zin_uniformity_audit(root: Path) -> Path | None:
    candidates = [path for path in root.rglob("zin_coverage_audit_summary.json") if path.is_file()]
    for summary in candidates:
        parent = summary.parent
        if parent.name == "zin_uniformity_audit":
            return parent
    return candidates[0].parent if candidates else None


def _find_emx_s4p(root: Path, sample_id: str) -> Path | None:
    exact = root / DEFAULT_EMX_NAME
    if exact.is_file():
        return exact
    matches = [path for path in root.rglob("*.s4p") if path.is_file() and sample_id in str(path)]
    return matches[0] if matches else None


def _find_export_manifest(root: Path) -> Path | None:
    matches = [path for path in root.rglob("EXPORT_MANIFEST.txt") if path.is_file()]
    return matches[0] if matches else None


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS Validation Export Bundle Import",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Bundle: `{summary['bundle']}`",
        f"- Bundle SHA256: `{summary.get('bundle_sha256')}`",
        f"- Extracted root: `{summary.get('extracted_root')}`",
        f"- Zin plan: `{summary.get('zin_plan_dir')}`",
        f"- Zin uniformity audit: `{summary.get('zin_uniformity_audit_dir')}`",
        f"- EMX S4P: `{summary.get('emx_s4p')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
