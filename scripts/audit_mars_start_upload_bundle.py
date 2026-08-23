#!/usr/bin/env python3
"""Audit the local MARS start-current upload bundle.

This is a local-only preflight. It verifies the upload bundle tarball,
the bundle SHA sidecar, internal SHA256SUMS, current sync-packet SHA, and
the start-current launcher contract. It does not connect to MARS or run EMX.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE_NAME = "next_gen_s8p_mars_start_current_upload_bundle_20260620.tar.gz"
DEFAULT_SYNC_NAME = "next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz"
DEFAULT_START_SCRIPT = "NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh"
DEFAULT_README = "NEXT_GEN_S8P_MARS_START_UPLOAD_BUNDLE_README_20260620_CN.md"
REQUIRED_FILES = (
    DEFAULT_SYNC_NAME,
    f"{DEFAULT_SYNC_NAME}.sha256",
    DEFAULT_START_SCRIPT,
    DEFAULT_README,
    "SHA256SUMS.txt",
)
START_SCRIPT_MARKERS = (
    DEFAULT_SYNC_NAME,
    "locate_packet",
    "SHA_PATH",
    "sha256sum -c",
    "NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh",
    "RUN_REAL_EMX",
)
README_MARKERS = (
    DEFAULT_SYNC_NAME,
    DEFAULT_START_SCRIPT,
    "bash NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh",
    "500",
    ".s8p",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bundle = Path(args.bundle_tar).expanduser().resolve()
    bundle_sha = Path(args.bundle_sha).expanduser().resolve() if args.bundle_sha else bundle.with_suffix(bundle.suffix + ".sha256")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, str]] = []
    checks.append(_check("bundle tar exists", bundle.is_file(), str(bundle)))
    checks.append(_check("bundle sha sidecar exists", bundle_sha.is_file(), str(bundle_sha)))

    bundle_digest = _sha256(bundle) if bundle.is_file() else None
    sidecar_digest = _read_sidecar_digest(bundle_sha) if bundle_sha.is_file() else None
    checks.append(
        _check(
            "bundle sha sidecar matches tar",
            bundle_digest is not None and sidecar_digest is not None and bundle_digest == sidecar_digest,
            f"actual={bundle_digest}, sidecar={sidecar_digest}",
        )
    )

    extracted: Path | None = None
    with tempfile.TemporaryDirectory(prefix="mars_start_bundle_audit_") as tmp:
        tmpdir = Path(tmp)
        if bundle.is_file():
            safe, detail = _safe_extract(bundle, tmpdir)
            checks.append(_check("bundle tar safely extracts", safe, detail))
            extracted = tmpdir if safe else None
        else:
            checks.append(_check("bundle tar safely extracts", False, "bundle missing"))

        if extracted is not None:
            checks.extend(_bundle_content_checks(extracted, args))
        else:
            for name in REQUIRED_FILES:
                checks.append(_check(f"bundle contains {name}", False, "bundle was not extracted"))

    overall_status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    decision = "MARS_START_UPLOAD_BUNDLE_READY" if overall_status == "PASS" else "DO_NOT_UPLOAD_MARS_START_BUNDLE"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "bundle_tar": str(bundle),
        "bundle_sha": str(bundle_sha),
        "bundle_sha256": bundle_digest,
        "checks": checks,
        "limitations": [
            "This audit verifies local upload files only.",
            "It does not prove MARS upload, MARS EMX execution, HFSS export, or EMX/HFSS agreement.",
        ],
    }
    summary_path = out_dir / "mars_start_upload_bundle_audit_summary.json"
    report_path = out_dir / "MARS_START_UPLOAD_BUNDLE_AUDIT.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-tar", default=str(DEFAULT_PROJECT / DEFAULT_BUNDLE_NAME))
    parser.add_argument("--bundle-sha", default="")
    parser.add_argument("--expected-sync-sha", default="")
    parser.add_argument("--out-dir", default=str(DEFAULT_PROJECT / "outputs" / "mars_start_upload_bundle_audit_current"))
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _bundle_content_checks(root: Path, args: argparse.Namespace) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for name in REQUIRED_FILES:
        checks.append(_check(f"bundle contains {name}", (root / name).is_file(), str(root / name)))

    sums = root / "SHA256SUMS.txt"
    checks.append(_check("internal SHA256SUMS verifies", _verify_sha256sums(sums, root), str(sums)))

    sync_tar = root / DEFAULT_SYNC_NAME
    sync_sidecar = root / f"{DEFAULT_SYNC_NAME}.sha256"
    sync_digest = _sha256(sync_tar) if sync_tar.is_file() else None
    sync_sidecar_digest = _read_sidecar_digest(sync_sidecar) if sync_sidecar.is_file() else None
    checks.append(
        _check(
            "sync tar sha sidecar matches",
            sync_digest is not None and sync_sidecar_digest is not None and sync_digest == sync_sidecar_digest,
            f"actual={sync_digest}, sidecar={sync_sidecar_digest}",
        )
    )
    if args.expected_sync_sha:
        checks.append(
            _check(
                "sync tar matches expected sha",
                sync_digest == args.expected_sync_sha,
                f"actual={sync_digest}, expected={args.expected_sync_sha}",
            )
        )

    start_script = root / DEFAULT_START_SCRIPT
    start_text = start_script.read_text(encoding="utf-8") if start_script.is_file() else ""
    for marker in START_SCRIPT_MARKERS:
        checks.append(_check(f"start script contains {marker}", marker in start_text, marker))

    readme = root / DEFAULT_README
    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    for marker in README_MARKERS:
        checks.append(_check(f"bundle README contains {marker}", marker in readme_text, marker))

    return checks


def _safe_extract(bundle: Path, target: Path) -> tuple[bool, str]:
    try:
        with tarfile.open(bundle, "r:gz") as tar:
            for member in tar.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    return False, f"unsafe member path: {member.name}"
            tar.extractall(target, filter="data")
        return True, str(target)
    except (tarfile.TarError, OSError) as exc:
        return False, str(exc)


def _verify_sha256sums(path: Path, root: Path) -> bool:
    if not path.is_file():
        return False
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if len(parts) < 2:
                continue
            expected, rel = parts[0], parts[1]
            candidate = root / rel
            if not candidate.is_file() or _sha256(candidate) != expected:
                return False
        return True
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sidecar_digest(path: Path) -> str | None:
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if parts:
                return parts[0]
    except OSError:
        return None
    return None


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if ok else "FAIL", "name": name, "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS Start Upload Bundle Audit",
        "",
        f"- overall_status: `{summary['overall_status']}`",
        f"- decision: `{summary['decision']}`",
        f"- bundle_tar: `{summary['bundle_tar']}`",
        f"- bundle_sha256: `{summary.get('bundle_sha256')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "|---|---|---|",
    ]
    for item in summary["checks"]:
        lines.append(f"| {item['status']} | {item['name']} | {item['detail']} |")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This audit is local-only.",
            "- It does not prove MARS EMX, HFSS export, or EMX/HFSS comparison completion.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
