#!/usr/bin/env python3
"""Bind one real Calibre zero-blocking result to the exact Cadence GDS.

This command is a post-Calibre identity boundary. It never invokes Cadence,
Calibre, EMX, a queue, or a supervisor. A PASS receipt is written only when a
hash-bound production Calibre summary reports PASS and zero blocking
violations for the exact candidate, geometry, GDS, manifest, and top cell.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (  # noqa: E402
    CALIBRE_ZERO_BLOCKING_PASS_DECISION,
    CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
)


PRODUCTION_CALIBRE_SUMMARY_SCHEMA = (
    "candidate_bound_tsmc65_calibre_macro_ip_back_end_drc_v1"
)
RECEIPT_NAME = "CALIBRE_ZERO_BLOCKING_RECEIPT.json"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class CalibreReceiptError(RuntimeError):
    """Raised when real Calibre evidence cannot support a PASS receipt."""


@dataclass(frozen=True)
class FilePin:
    path: Path
    size_bytes: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int

    def record(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_receipt(
            config_path=Path(args.config),
            expected_config_sha256=args.expected_config_sha256,
            gds_path=Path(args.gds),
            expected_gds_sha256=args.expected_gds_sha256,
            manifest_path=Path(args.manifest),
            expected_manifest_sha256=args.expected_manifest_sha256,
            calibre_summary_path=Path(args.calibre_summary),
            expected_calibre_summary_sha256=args.expected_calibre_summary_sha256,
            candidate_id_sha256=args.candidate_id_sha256,
            geometry_identity_sha256=args.geometry_identity_sha256,
            out_dir=Path(args.out_dir),
        )
    except (CalibreReceiptError, OSError, json.JSONDecodeError) as exc:
        print("overall_status=FAIL")
        print(f"error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"receipt={result['receipt_path']}")
    print("simulator_action_taken=false")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--gds", required=True)
    parser.add_argument("--expected-gds-sha256", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--calibre-summary", required=True)
    parser.add_argument("--expected-calibre-summary-sha256", required=True)
    parser.add_argument("--candidate-id-sha256", required=True)
    parser.add_argument("--geometry-identity-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    for field in (
        "expected_config_sha256",
        "expected_gds_sha256",
        "expected_manifest_sha256",
        "expected_calibre_summary_sha256",
        "candidate_id_sha256",
        "geometry_identity_sha256",
    ):
        value = str(getattr(args, field) or "").strip().lower()
        if SHA256_PATTERN.fullmatch(value) is None:
            parser.error(f"--{field.replace('_', '-')} must be a SHA-256 digest")
        setattr(args, field, value)
    return args


def build_receipt(
    *,
    config_path: Path,
    expected_config_sha256: str,
    gds_path: Path,
    expected_gds_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    calibre_summary_path: Path,
    expected_calibre_summary_sha256: str,
    candidate_id_sha256: str,
    geometry_identity_sha256: str,
    out_dir: Path,
) -> dict[str, str]:
    """Validate immutable Calibre evidence and atomically write one receipt."""

    candidate_id = _sha256_value(candidate_id_sha256, "candidate_id_sha256")
    geometry_id = _sha256_value(
        geometry_identity_sha256, "geometry_identity_sha256"
    )
    output = _absolute(out_dir)
    if output.exists():
        raise CalibreReceiptError(f"refusing existing output directory: {output}")
    if _has_symlink_component(output.parent):
        raise CalibreReceiptError("output parent contains a symlink component")

    config_pin, _ = _pin_file(
        config_path, expected_config_sha256, "private configuration"
    )
    gds_pin, _ = _pin_file(gds_path, expected_gds_sha256, "Cadence GDS")
    manifest_pin, manifest_bytes = _pin_file(
        manifest_path,
        expected_manifest_sha256,
        "layout manifest",
        capture=True,
    )
    summary_pin, summary_bytes = _pin_file(
        calibre_summary_path,
        expected_calibre_summary_sha256,
        "Calibre candidate summary",
        capture=True,
    )
    manifest = _json_object(manifest_bytes, "layout manifest")
    summary = _json_object(summary_bytes, "Calibre candidate summary")
    top_cell = str(manifest.get("top_cell") or "").strip()
    if not top_cell:
        raise CalibreReceiptError("layout manifest lacks top_cell")

    _require_summary(
        summary,
        summary_path=summary_pin.path,
        candidate_id=candidate_id,
        geometry_id=geometry_id,
        gds_pin=gds_pin,
        top_cell=top_cell,
    )
    report_path = _summary_path(
        summary_pin.path, summary.get("drc_report_path"), "drc_report_path"
    )
    report_pin, _ = _pin_file(
        report_path,
        str(summary.get("drc_report_sha256") or ""),
        "Calibre DRC report",
    )
    geometry_audit_path = _summary_path(
        summary_pin.path,
        summary.get("geometry_audit_path"),
        "geometry_audit_path",
    )
    geometry_audit_pin, geometry_audit_bytes = _pin_file(
        geometry_audit_path,
        str(summary.get("geometry_audit_sha256") or ""),
        "candidate-bound geometry audit",
        capture=True,
    )
    geometry_audit = _json_object(
        geometry_audit_bytes, "candidate-bound geometry audit"
    )
    if geometry_audit.get("overall_status") != "PASS":
        raise CalibreReceiptError("candidate-bound geometry audit is not PASS")

    pins = (
        config_pin,
        gds_pin,
        manifest_pin,
        summary_pin,
        report_pin,
        geometry_audit_pin,
    )
    _reverify_pins(pins)
    receipt = {
        "schema": CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
        "overall_status": "PASS",
        "decision": CALIBRE_ZERO_BLOCKING_PASS_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate_id_sha256": candidate_id,
        "geometry_identity_sha256": geometry_id,
        "config_path": str(config_pin.path),
        "config_size_bytes": config_pin.size_bytes,
        "config_sha256": config_pin.sha256,
        "gds_path": str(gds_pin.path),
        "gds_size_bytes": gds_pin.size_bytes,
        "gds_sha256": gds_pin.sha256,
        "manifest_path": str(manifest_pin.path),
        "manifest_size_bytes": manifest_pin.size_bytes,
        "manifest_sha256": manifest_pin.sha256,
        "top_cell": top_cell,
        "cadence_streamout_complete": True,
        "calibre_executed": True,
        "calibre_blocking_violations": 0,
        "calibre_total_violations": int(summary["drc_violation_count"]),
        "calibre_documented_warnings": int(summary["documented_warning_count"]),
        "calibre_report_path": str(report_pin.path),
        "calibre_report_size_bytes": report_pin.size_bytes,
        "calibre_report_sha256": report_pin.sha256,
        "source_calibre_summary": summary_pin.record(),
        "source_geometry_audit": geometry_audit_pin.record(),
        "source_files_unchanged": True,
        "gds_generated_or_modified_by_this_builder": False,
        "cadence_executed_by_this_builder": False,
        "calibre_executed_by_this_builder": False,
        "emx_executed_by_this_builder": False,
        "simulator_action_taken": False,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.staging.{os.getpid()}")
    if staging.exists():
        raise CalibreReceiptError(f"staging path exists: {staging}")
    staging.mkdir(mode=0o700)
    try:
        receipt_path = staging / RECEIPT_NAME
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sums_path = staging / SHA256SUMS_NAME
        sums_path.write_text(
            f"{_sha256_file(receipt_path)}  {RECEIPT_NAME}\n",
            encoding="utf-8",
        )
        _reverify_pins(pins)
        os.replace(staging, output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "receipt_path": str(output / RECEIPT_NAME),
        "receipt_sha256": _sha256_file(output / RECEIPT_NAME),
    }


def _require_summary(
    summary: Mapping[str, Any],
    *,
    summary_path: Path,
    candidate_id: str,
    geometry_id: str,
    gds_pin: FilePin,
    top_cell: str,
) -> None:
    checks = {
        "schema": summary.get("schema") == PRODUCTION_CALIBRE_SUMMARY_SCHEMA,
        "overall_status": summary.get("overall_status") == "PASS",
        "candidate_id": str(summary.get("candidate_id_sha256") or "").lower()
        == candidate_id,
        "geometry_identity": str(
            summary.get("candidate_geometry_identity_sha256") or ""
        ).lower()
        == geometry_id,
        "gds_path": _summary_path(
            summary_path, summary.get("gds_path"), "gds_path"
        )
        == gds_pin.path,
        "gds_sha256": str(summary.get("gds_sha256") or "").lower()
        == gds_pin.sha256,
        "gds_top_cell": str(summary.get("gds_top_cell") or "") == top_cell,
        "blocking_zero": summary.get("blocking_drc_violation_count") == 0,
        "drc_count_integer": _is_nonnegative_int(summary.get("drc_violation_count")),
        "warning_count_integer": _is_nonnegative_int(
            summary.get("documented_warning_count")
        ),
        "production_modification_not_authorized": summary.get(
            "production_campaign_modification_authorized"
        )
        is False,
    }
    failed = [name for name, passed in checks.items() if passed is not True]
    if failed:
        raise CalibreReceiptError(
            "Calibre candidate summary failed: " + ",".join(failed)
        )


def _pin_file(
    path: Path,
    expected_sha256: str,
    label: str,
    *,
    capture: bool = False,
) -> tuple[FilePin, bytes]:
    expected = _sha256_value(expected_sha256, f"expected {label} SHA-256")
    absolute = _absolute(path)
    if _has_symlink_component(absolute):
        raise CalibreReceiptError(f"{label} path contains a symlink component")
    try:
        before = os.lstat(absolute)
    except OSError as exc:
        raise CalibreReceiptError(f"{label} is missing") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise CalibreReceiptError(f"{label} is not a nonempty regular file")
    fd = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        opened = os.fstat(fd)
        identity = _stat_identity(opened)
        if identity != _stat_identity(before):
            raise CalibreReceiptError(f"{label} changed before read")
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if capture:
                chunks.append(chunk)
        if identity != _stat_identity(os.fstat(fd)) or identity != _stat_identity(
            os.lstat(absolute)
        ):
            raise CalibreReceiptError(f"{label} changed during read")
    finally:
        os.close(fd)
    actual = digest.hexdigest()
    if actual != expected:
        raise CalibreReceiptError(f"{label} SHA-256 mismatch")
    return (
        FilePin(
            path=absolute,
            size_bytes=int(opened.st_size),
            sha256=actual,
            device=int(opened.st_dev),
            inode=int(opened.st_ino),
            mtime_ns=int(opened.st_mtime_ns),
            ctime_ns=int(opened.st_ctime_ns),
        ),
        b"".join(chunks),
    )


def _reverify_pins(pins: tuple[FilePin, ...]) -> None:
    for pin in pins:
        current, _ = _pin_file(pin.path, pin.sha256, pin.path.name)
        if current != pin:
            raise CalibreReceiptError(f"source identity changed: {pin.path.name}")


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibreReceiptError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CalibreReceiptError(f"{label} is not a JSON object")
    return value


def _summary_path(summary_path: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CalibreReceiptError(f"Calibre summary lacks {field}")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = summary_path.parent / candidate
    return _absolute(candidate)


def _sha256_value(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise CalibreReceiptError(f"{label} is not SHA-256")
    return normalized


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _has_symlink_component(path: Path) -> bool:
    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            break
    return False


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
