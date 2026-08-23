#!/usr/bin/env python3
"""Refresh the campaign's acquisition-only proxy-to-real calibration.

Completed real-EMX rounds are discovered in order. Older queue formats without
raw prediction provenance are ignored. One compatible source uses a geometry
hash holdout; two or more sources train on history and hold out the latest
round. A failed or waiting audit removes the active mapping rather than leaving
stale calibration in production.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_proxy_to_real_physical_feature_calibration as calibration_audit  # noqa: E402
import audit_proxy_uncertainty_real_emx_reliability as uncertainty_audit  # noqa: E402


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rounds_root = Path(args.rounds_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    active_json = Path(args.active_json).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    active_json.parent.mkdir(parents=True, exist_ok=True)

    sources, ignored = _discover_sources(rounds_root)
    audit_dir = out_dir / "audit"
    audit_summary = audit_dir / "proxy_to_real_physical_calibration_summary.json"
    uncertainty_dir = out_dir / "uncertainty_reliability"
    uncertainty_summary = uncertainty_dir / "proxy_uncertainty_real_emx_reliability_summary.json"
    if not sources:
        active_json.unlink(missing_ok=True)
        payload = _summary(
            args=args,
            rounds_root=rounds_root,
            sources=sources,
            ignored=ignored,
            audit_summary=audit_summary,
            active_json=active_json,
            overall_status="WAITING",
            decision="WAIT_FOR_PAIRED_REAL_EMX_RETURNS",
            holdout_mode=None,
            audit_returncode=None,
            calibration_active=False,
        )
        payload["uncertainty_reliability"] = {
            "overall_status": "WAITING",
            "decision": "WAIT_FOR_UNCERTAINTY_AND_REAL_EMX_PAIRS",
            "eligible_for_acquisition_ablation": False,
            "summary": str(uncertainty_summary),
        }
        return _finish(out_dir, payload, args.no_fail_exit)

    holdout_mode = "latest-source" if len(sources) >= 2 else "hash"
    audit_args: list[str] = []
    for source in sources:
        audit_args.extend(["--paired-csv", str(source)])
    audit_args.extend(
        [
            "--out-dir",
            str(audit_dir),
            "--holdout-mode",
            holdout_mode,
            "--min-independent-geometries",
            str(args.min_independent_geometries),
            "--min-holdout-geometries",
            str(args.min_holdout_geometries),
            "--no-fail-exit",
        ]
    )
    audit_returncode = calibration_audit.main(audit_args)
    audit_payload = _read_json(audit_summary)
    uncertainty_args: list[str] = []
    for source in sources:
        uncertainty_args.extend(["--paired-csv", str(source)])
    uncertainty_args.extend(
        [
            "--out-dir",
            str(uncertainty_dir),
            "--holdout-mode",
            holdout_mode,
            "--min-independent-geometries",
            str(args.min_independent_geometries),
            "--min-holdout-geometries",
            str(args.min_holdout_geometries),
            "--no-fail-exit",
        ]
    )
    uncertainty_returncode = uncertainty_audit.main(uncertainty_args)
    uncertainty_payload = _read_json(uncertainty_summary)
    approved = (
        audit_returncode == 0
        and audit_payload.get("overall_status") == "PASS"
        and audit_payload.get("decision") == "USE_CALIBRATION_FOR_ACQUISITION_ONLY"
        and audit_payload.get("eligible_for_selector") is True
    )
    if approved:
        temporary = active_json.with_suffix(active_json.suffix + ".tmp")
        shutil.copyfile(audit_summary, temporary)
        temporary.replace(active_json)
        decision = "ACTIVATE_CALIBRATION_FOR_NEXT_ACQUISITION_ONLY"
    else:
        active_json.unlink(missing_ok=True)
        decision = "USE_RAW_PROXY_FOR_NEXT_ACQUISITION"

    payload = _summary(
        args=args,
        rounds_root=rounds_root,
        sources=sources,
        ignored=ignored,
        audit_summary=audit_summary,
        active_json=active_json,
        overall_status="PASS",
        decision=decision,
        holdout_mode=holdout_mode,
        audit_returncode=audit_returncode,
        calibration_active=approved,
    )
    payload["audit_outcome"] = {
        "overall_status": audit_payload.get("overall_status"),
        "decision": audit_payload.get("decision"),
        "eligible_for_selector": audit_payload.get("eligible_for_selector"),
        "improvements": audit_payload.get("improvements"),
    }
    payload["uncertainty_reliability"] = {
        "returncode": uncertainty_returncode,
        "overall_status": uncertainty_payload.get("overall_status"),
        "decision": uncertainty_payload.get("decision"),
        "eligible_for_acquisition_ablation": uncertainty_payload.get(
            "eligible_for_acquisition_ablation"
        ),
        "holdout_metrics": uncertainty_payload.get("holdout_metrics"),
        "summary": str(uncertainty_summary),
        "automatic_ranking_change": False,
    }
    return _finish(out_dir, payload, args.no_fail_exit)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--active-json", required=True)
    parser.add_argument("--trigger-round", type=int, default=0)
    parser.add_argument("--min-independent-geometries", type=int, default=80)
    parser.add_argument("--min-holdout-geometries", type=int, default=20)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _discover_sources(rounds_root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    sources: list[Path] = []
    ignored: list[dict[str, Any]] = []
    for round_dir in sorted(rounds_root.glob("round_*")):
        complete = round_dir / "real_emx" / "round.complete"
        csv_path = round_dir / "real_emx" / "dataset" / "dataset_rows.csv"
        if not complete.is_file():
            ignored.append({"round": round_dir.name, "reason": "round_not_complete", "path": str(csv_path)})
            continue
        compatible, detail = _compatible_header(csv_path)
        if not compatible:
            ignored.append({"round": round_dir.name, "reason": detail, "path": str(csv_path)})
            continue
        sources.append(csv_path)
    return sources, ignored


def _compatible_header(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "dataset_rows_missing"
    with path.open(newline="", encoding="utf-8-sig") as handle:
        fields = set(csv.DictReader(handle).fieldnames or [])
    prediction_ok = all(
        any(
            alias in fields
            for alias in (
                f"raw_pred_{feature}",
                f"queue__raw_pred_{feature}",
                f"pred_{feature}",
                f"queue__pred_{feature}",
            )
        )
        for feature in FEATURES
    )
    real_ok = (
        {"lp_nh_center", "ls_nh_center"}.issubset(fields)
        and ("q_center" in fields or {"qp_center", "qs_center"}.issubset(fields))
        and ("k_abs_center" in fields or "k_center" in fields)
    )
    geometry_ok = len([field for field in fields if field.startswith("geom__")]) >= 10
    if not prediction_ok:
        return False, "raw_prediction_provenance_missing"
    if not real_ok:
        return False, "real_physical_features_missing"
    if not geometry_ok:
        return False, "independent_geometry_columns_missing"
    return True, "compatible"


def _summary(
    *,
    args: argparse.Namespace,
    rounds_root: Path,
    sources: list[Path],
    ignored: list[dict[str, Any]],
    audit_summary: Path,
    active_json: Path,
    overall_status: str,
    decision: str,
    holdout_mode: str | None,
    audit_returncode: int | None,
    calibration_active: bool,
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "trigger_round": int(args.trigger_round),
        "rounds_root": str(rounds_root),
        "compatible_source_count": len(sources),
        "compatible_sources": [
            {"path": str(path), "sha256": _sha256(path), "round": path.parents[2].name} for path in sources
        ],
        "ignored_sources": ignored,
        "holdout_mode": holdout_mode,
        "audit_returncode": audit_returncode,
        "audit_summary": str(audit_summary),
        "active_json": str(active_json),
        "calibration_active": calibration_active,
        "scientific_boundary": (
            "Calibration can change candidate priority only. A missing, waiting, rejected, or shifted audit removes the active mapping; uncertainty reliability is diagnostic and cannot change ranking automatically; real EMX labels and final uniformity gates are unchanged."
        ),
    }


def _finish(out_dir: Path, payload: dict[str, Any], no_fail_exit: bool) -> int:
    summary_path = out_dir / "campaign_proxy_to_real_calibration_refresh_summary.json"
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"overall_status={payload['overall_status']}")
    print(f"decision={payload['decision']}")
    print(f"calibration_active={str(payload['calibration_active']).lower()}")
    print(f"summary={summary_path}")
    if payload["overall_status"] in {"PASS", "WAITING"}:
        return 0 if payload["overall_status"] == "PASS" or no_fail_exit else 2
    return 0 if no_fail_exit else 2


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
