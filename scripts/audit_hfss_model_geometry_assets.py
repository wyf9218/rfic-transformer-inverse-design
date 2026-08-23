#!/usr/bin/env python3
"""Audit HFSS model-view PNGs and STEP export assets.

This is a provenance/asset-quality gate for the HFSS geometry evidence. It does
not prove EM correctness, coupling, or agreement with EMX. It verifies that the
reported HFSS model images are real decodable images with nontrivial content
and that the STEP file looks like a non-empty STEP export.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PACKAGE_DIR = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
DEFAULT_OUT_DIR_NAME = "hfss_model_geometry_asset_audit_20260614"
FIXED_GENERATED_UTC = datetime(2026, 6, 14, tzinfo=timezone.utc).isoformat(timespec="seconds")
MIN_PNG_WIDTH = 640
MIN_PNG_HEIGHT = 360
MIN_PNG_BYTES = 2048
MIN_PNG_COLOR_DELTA = 2
MIN_STEP_BYTES = 4096
MIN_STEP_ENTITY_COUNT = 20
STEP_REQUIRED_TOKENS = (
    "ISO-10303-21;",
    "HEADER;",
    "DATA;",
    "ENDSEC;",
    "END-ISO-10303-21;",
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package_dir = Path(args.package_dir).expanduser().resolve()
    model_dir = package_dir / "hfss_model_views"
    out_dir = (
        Path(args.out_dir).expanduser().resolve()
        if args.out_dir
        else package_dir / DEFAULT_OUT_DIR_NAME
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    top_png = _resolve_arg_path(args.top_png, model_dir / "hfss_payload_geometry_top_annotated.png")
    iso_png = _resolve_arg_path(args.isometric_png, model_dir / "hfss_payload_geometry_isometric.png")
    quality_png = _resolve_arg_path(args.quality_png, model_dir / "hfss_payload_geometry_quality_checks.png")
    step_path = _resolve_arg_path(args.step, model_dir / "ec6698dfc575950b_hfss_model_no_air.step")

    checks = [
        _png_check(
            top_png,
            "HFSS top-view PNG",
            min_width=args.min_png_width,
            min_height=args.min_png_height,
            min_bytes=args.min_png_bytes,
            min_color_delta=args.min_png_color_delta,
        ),
        _png_check(
            iso_png,
            "HFSS isometric-view PNG",
            min_width=args.min_png_width,
            min_height=args.min_png_height,
            min_bytes=args.min_png_bytes,
            min_color_delta=args.min_png_color_delta,
        ),
        _png_check(
            quality_png,
            "HFSS geometry-quality PNG",
            min_width=args.min_png_width,
            min_height=args.min_png_height,
            min_bytes=args.min_png_bytes,
            min_color_delta=args.min_png_color_delta,
        ),
        _step_check(
            step_path,
            min_bytes=args.min_step_bytes,
            min_entity_count=args.min_step_entity_count,
        ),
    ]
    overall_status = "PASS" if all(check.status == "PASS" for check in checks) else "FAIL"
    decision = (
        "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS"
        if overall_status == "PASS"
        else "DO_NOT_USE_HFSS_MODEL_GEOMETRY_ASSETS"
    )

    summary = {
        "generated_utc": FIXED_GENERATED_UTC,
        "overall_status": overall_status,
        "decision": decision,
        "scope": (
            "HFSS geometry asset audit only; this does not validate EMX correctness, "
            "HFSS physical metrics, ADS curves, or EMX-vs-HFSS agreement."
        ),
        "package_dir": str(package_dir),
        "artifacts": {
            "top_png": str(top_png),
            "isometric_png": str(iso_png),
            "quality_png": str(quality_png),
            "step": str(step_path),
        },
        "thresholds": {
            "min_png_width": args.min_png_width,
            "min_png_height": args.min_png_height,
            "min_png_bytes": args.min_png_bytes,
            "min_png_color_delta": args.min_png_color_delta,
            "min_step_bytes": args.min_step_bytes,
            "min_step_entity_count": args.min_step_entity_count,
            "step_required_tokens": list(STEP_REQUIRED_TOKENS),
        },
        "checks": [check.as_dict() for check in checks],
    }
    summary_path = out_dir / "hfss_model_geometry_asset_audit_summary.json"
    report_path = out_dir / "hfss_model_geometry_asset_audit_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(DEFAULT_PACKAGE_DIR))
    parser.add_argument("--out-dir", help="Directory for audit summary/report")
    parser.add_argument("--top-png", help="Override HFSS top-view PNG path")
    parser.add_argument("--isometric-png", help="Override HFSS isometric PNG path")
    parser.add_argument("--quality-png", help="Override HFSS geometry-quality PNG path")
    parser.add_argument("--step", help="Override HFSS STEP path")
    parser.add_argument("--min-png-width", type=int, default=MIN_PNG_WIDTH)
    parser.add_argument("--min-png-height", type=int, default=MIN_PNG_HEIGHT)
    parser.add_argument("--min-png-bytes", type=int, default=MIN_PNG_BYTES)
    parser.add_argument("--min-png-color-delta", type=int, default=MIN_PNG_COLOR_DELTA)
    parser.add_argument("--min-step-bytes", type=int, default=MIN_STEP_BYTES)
    parser.add_argument("--min-step-entity-count", type=int, default=MIN_STEP_ENTITY_COUNT)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _resolve_arg_path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser().resolve() if value else default.resolve()


def _png_check(
    path: Path,
    name: str,
    *,
    min_width: int,
    min_height: int,
    min_bytes: int,
    min_color_delta: int,
) -> Check:
    failures, measurements = _png_failures(
        path,
        min_width=min_width,
        min_height=min_height,
        min_bytes=min_bytes,
        min_color_delta=min_color_delta,
    )
    if failures:
        return Check("FAIL", name, "; ".join(failures))
    return Check(
        "PASS",
        name,
        (
            f"path={path}; size={measurements['width']}x{measurements['height']}; "
            f"bytes={measurements['bytes']}; max_channel_delta={measurements['max_channel_delta']}"
        ),
    )


def _png_failures(
    path: Path,
    *,
    min_width: int,
    min_height: int,
    min_bytes: int,
    min_color_delta: int,
) -> tuple[list[str], dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"unreadable PNG ({type(exc).__name__}: {exc})"], {}
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return ["missing PNG signature"], {"bytes": len(data)}
    if len(data) < 33:
        return ["missing PNG IHDR chunk"], {"bytes": len(data)}
    ihdr_length = int.from_bytes(data[8:12], "big")
    ihdr_type = data[12:16]
    if ihdr_length != 13 or ihdr_type != b"IHDR":
        return ["invalid PNG IHDR chunk"], {"bytes": len(data)}
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    measurements: dict[str, Any] = {"width": width, "height": height, "bytes": len(data)}
    failures: list[str] = []
    if width < min_width or height < min_height:
        failures.append(f"PNG dimensions {width}x{height} below minimum {min_width}x{min_height}")
    if len(data) < min_bytes:
        failures.append(f"PNG bytes {len(data)} below minimum {min_bytes}")
    if failures:
        return failures, measurements

    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # noqa: BLE001
        return [f"Pillow unavailable for nonblank PNG check ({type(exc).__name__}: {exc})"], measurements
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            extrema = ImageStat.Stat(rgb).extrema
    except Exception as exc:  # noqa: BLE001
        return [f"unreadable PNG image data ({type(exc).__name__}: {exc})"], measurements
    max_delta = max((high - low) for low, high in extrema)
    measurements["max_channel_delta"] = max_delta
    if max_delta <= min_color_delta:
        failures.append(f"blank or nearly constant PNG (max_channel_delta={max_delta})")
    return failures, measurements


def _step_check(path: Path, *, min_bytes: int, min_entity_count: int) -> Check:
    try:
        data = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return Check("FAIL", "HFSS STEP model", f"unreadable STEP ({type(exc).__name__}: {exc})")
    failures: list[str] = []
    byte_count = path.stat().st_size
    if byte_count < min_bytes:
        failures.append(f"STEP bytes {byte_count} below minimum {min_bytes}")
    missing_tokens = [token for token in STEP_REQUIRED_TOKENS if token not in data]
    if missing_tokens:
        failures.append(f"missing STEP tokens={missing_tokens}")
    entity_count = sum(1 for line in data.splitlines() if line.lstrip().startswith("#"))
    if entity_count < min_entity_count:
        failures.append(f"STEP entity count {entity_count} below minimum {min_entity_count}")
    if failures:
        return Check("FAIL", "HFSS STEP model", "; ".join(failures))
    return Check(
        "PASS",
        "HFSS STEP model",
        f"path={path}; bytes={byte_count}; entity_count={entity_count}; required STEP tokens present",
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS Model Geometry Asset Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Scope: {summary['scope']}",
        f"- Package: `{summary['package_dir']}`",
        "",
        "## Checks",
        "",
    ]
    for check in summary["checks"]:
        lines.append(f"- **{check['status']}** {check['name']}: {check['detail']}")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "A PASS here only means the HFSS geometry evidence assets are real and inspectable. "
            "It must be combined with the EMX-first golden-reference gate, HFSS physical S4P gate, "
            "and accepted EMX-vs-HFSS/ADS Lp/Ls/Q/K comparison before any final validation claim.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
