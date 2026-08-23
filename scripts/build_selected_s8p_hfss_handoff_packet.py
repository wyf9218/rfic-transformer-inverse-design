#!/usr/bin/env python3
"""Build an auditable HFSS handoff packet for selected S8P validation samples.

This script is a traceability bridge between the EMX-selected validation row and
the later HFSS rebuild. It does not run EMX, HFSS, ADS, or Cadence. A PASS here
means the selected sample has enough recorded geometry/port evidence for a human
or automation script to rebuild the same 8-port power-line topology in HFSS.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_ROLES = (
    "left_power_top",
    "left_power_bottom",
    "primary_top",
    "primary_bottom",
    "secondary_top",
    "secondary_bottom",
    "right_power_top",
    "right_power_bottom",
)
EXPECTED_ROLE_LABELS = {
    "primary_top": "P001",
    "left_power_top": "P002",
    "left_power_bottom": "P003",
    "primary_bottom": "P004",
    "secondary_bottom": "P005",
    "secondary_top": "P006",
    "right_power_top": "P007",
    "right_power_bottom": "P008",
}
POWER_LINE_EXPECTED_GROUND_FRAME_POLICY = (
    "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"
)


@dataclass(frozen=True)
class Check:
    sample: str
    evaluation: str
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "sample": self.sample,
            "evaluation": self.evaluation,
            "status": self.status,
            "name": self.name,
            "detail": self.detail,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    samples_csv = Path(args.samples_csv).expanduser().resolve()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve() if args.dataset_dir else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(samples_csv)
    if args.max_samples is not None:
        rows = rows[: max(0, int(args.max_samples))]
    port_pairs, pair_errors = _parse_port_pairs(args.port_pairs)

    global_checks = [
        _check("", "", "samples_csv_exists", samples_csv.is_file(), str(samples_csv)),
        _check("", "", "selected_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("", "", "port_pairs_parse", not pair_errors, "; ".join(pair_errors) or args.port_pairs),
    ]
    global_checks.append(
        _layout_audit_summary_check(
            args.layout_audit_summary,
            allow_missing=bool(args.allow_missing_layout_audit),
        )
    )

    sample_results = [
        _build_sample_packet(row, index, dataset_dir, out_dir, port_pairs, args)
        for index, row in enumerate(rows, start=1)
    ]
    sample_check_objects = []
    for result in sample_results:
        sample_check_objects.extend(result.pop("_check_objects", []))
    all_checks = global_checks + sample_check_objects
    fail_count = sum(1 for result in sample_results if result["overall_status"] == "FAIL")
    overall_status = "FAIL" if any(check.status == "FAIL" for check in all_checks) or fail_count else "PASS"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "READY_FOR_HFSS_REBUILD_HANDOFF" if overall_status == "PASS" else "DO_NOT_BUILD_HFSS_MODEL_FROM_THIS_HANDOFF",
        "samples_csv": str(samples_csv),
        "dataset_dir": None if dataset_dir is None else str(dataset_dir),
        "out_dir": str(out_dir),
        "selected_count": len(rows),
        "pass_count": sum(1 for result in sample_results if result["overall_status"] == "PASS"),
        "fail_count": fail_count,
        "expected_bridge_width_um": None
        if args.expected_bridge_width_um is None
        else float(args.expected_bridge_width_um),
        "expected_ground_frame_width_um": float(args.expected_ground_frame_width_um),
        "expected_vertical_length_ratio": float(args.expected_vertical_length_ratio),
        "bridge_tolerance_um": float(args.bridge_tolerance_um),
        "ground_frame_tolerance_um": float(args.ground_frame_tolerance_um),
        "port_pairs": _port_pairs_as_dicts(port_pairs),
        "layout_audit_summary": "" if not args.layout_audit_summary else str(Path(args.layout_audit_summary).expanduser().resolve()),
        "sample_results": sample_results,
        "checks": [check.as_dict() for check in all_checks],
        "artifacts": {
            "summary": str(out_dir / "selected_s8p_hfss_handoff_summary.json"),
            "report": str(out_dir / "selected_s8p_hfss_handoff_report.md"),
            "port_map_csv": str(out_dir / "hfss_port_map.csv"),
            "bridge_geometry_csv": str(out_dir / "hfss_bridge_geometry.csv"),
            "differential_port_pairs_csv": str(out_dir / "hfss_differential_port_pairs.csv"),
            "ads_formula_trace": str(out_dir / "hfss_ads_formula_trace.md"),
            "rebuild_checklist": str(out_dir / "hfss_rebuild_checklist.md"),
        },
        "limitations": [
            "This handoff packet does not run HFSS, ADS, EMX, or Cadence.",
            "A PASS only means the selected EMX sample has traceable S8P, layout, port, and bridge evidence for HFSS rebuild.",
            "Final acceptance still requires HFSS EM simulation, HFSS .s8p export, ADS/Python Lp/Ls/Q/K curve extraction, and EMX-vs-HFSS error analysis.",
        ],
    }

    _write_port_map_csv(out_dir / "hfss_port_map.csv", sample_results, port_pairs)
    _write_bridge_geometry_csv(out_dir / "hfss_bridge_geometry.csv", sample_results)
    _write_pair_csv(out_dir / "hfss_differential_port_pairs.csv", sample_results, port_pairs)
    (out_dir / "hfss_ads_formula_trace.md").write_text(_render_ads_formula_trace(summary), encoding="utf-8")
    (out_dir / "hfss_rebuild_checklist.md").write_text(_render_rebuild_checklist(summary), encoding="utf-8")
    (out_dir / "selected_s8p_hfss_handoff_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "selected_s8p_hfss_handoff_report.md").write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(out_dir / "selected_s8p_hfss_handoff_checks.csv", all_checks)

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={out_dir / 'selected_s8p_hfss_handoff_summary.json'}")
    print(f"report={out_dir / 'selected_s8p_hfss_handoff_report.md'}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-csv", required=True, help="physical_feature_validation_samples.csv")
    parser.add_argument("--dataset-dir", help="Dataset run directory used to resolve relative sample paths")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--port-pairs", required=True, help="Differential S8P port pairs, e.g. 1,4:5,6")
    parser.add_argument(
        "--layout-audit-summary",
        help="Required selected_power_line_8port_layout_audit_summary.json unless --allow-missing-layout-audit is set for debugging.",
    )
    parser.add_argument(
        "--allow-missing-layout-audit",
        action="store_true",
        help="Debug-only bypass. Final HFSS handoff must provide a PASS selected layout audit summary.",
    )
    parser.add_argument(
        "--expected-bridge-width-um",
        type=float,
        default=None,
        help=(
            "Optional fixed bridge width contract. Omit for the shared-line-width "
            "S8P flow, where each sample records its own expected_width_um/line_width_um."
        ),
    )
    parser.add_argument("--expected-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--expected-vertical-length-ratio", type=float, default=1.5)
    parser.add_argument("--bridge-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument("--ground-frame-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--copy-artifacts", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _build_sample_packet(
    row: dict[str, str],
    index: int,
    dataset_dir: Path | None,
    out_dir: Path,
    port_pairs: list[tuple[int, int]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    sample_id = str(row.get("selection_rank") or index)
    evaluation = row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{index}"
    source = _resolve_sample_source(row, dataset_dir)
    evaluation_dir = _evaluation_dir_from_source(source) if source is not None else None
    layout_path = None if source is None else _find_layout_json(source)
    power_line_path = None if source is None else _find_power_line_geometry_json(source)
    touchstone_path = _resolve_touchstone(row, dataset_dir, source, evaluation_dir)
    gds_path = None if source is None else _find_optional_file(source, evaluation_dir, ["transformer_layout.gds", "*.gds"])
    preview_path = None if source is None else _find_optional_file(source, evaluation_dir, ["*.png"])

    layout = _read_json_if_possible(layout_path)
    power_line = _read_json_if_possible(power_line_path)
    labels = power_line.get("labels", {}) if isinstance(power_line, dict) else {}
    label_to_role = {str(label): role for role, label in labels.items() if label}
    layout_port_records = _layout_port_records(layout)
    port_names = _layout_port_names(layout)
    bridge_records = _bridge_records(power_line) if isinstance(power_line, dict) else []

    checks = [
        _check(sample_id, evaluation, "sample source resolved", source is not None, "" if source is None else str(source)),
        _check(sample_id, evaluation, "sample source directory exists", source is not None and source.is_dir(), "" if source is None else str(source)),
        _check(sample_id, evaluation, "EMX S8P exists", touchstone_path is not None and touchstone_path.is_file(), "" if touchstone_path is None else str(touchstone_path)),
        _check(sample_id, evaluation, "EMX Touchstone suffix is .s8p", touchstone_path is not None and touchstone_path.suffix.lower() == ".s8p", "" if touchstone_path is None else str(touchstone_path)),
        _check(sample_id, evaluation, "layout json exists", layout_path is not None and layout_path.is_file(), "" if layout_path is None else str(layout_path)),
        _check(sample_id, evaluation, "power_line_8port geometry json exists", power_line_path is not None and power_line_path.is_file(), "" if power_line_path is None else str(power_line_path)),
        _check(sample_id, evaluation, "8 port labels present", set(port_names) == {f"P{i:03d}" for i in range(1, 9)}, ",".join(port_names)),
        _check(
            sample_id,
            evaluation,
            "8 port signal labels present",
            _layout_ports_have_expected_labels(layout_port_records, suffix=""),
            _missing_layout_port_label_detail(layout_port_records, suffix=""),
        ),
        _check(
            sample_id,
            evaluation,
            "8 port ground labels present",
            _layout_ports_have_expected_labels(layout_port_records, suffix="_G"),
            _missing_layout_port_label_detail(layout_port_records, suffix="_G"),
        ),
        _check(sample_id, evaluation, "8 role labels present", all(role in labels for role in EXPECTED_ROLES), json.dumps(labels, sort_keys=True)),
        _check(
            sample_id,
            evaluation,
            "8 role labels match approved P001-P008 order",
            _role_labels_match_approved(labels),
            f"expected={EXPECTED_ROLE_LABELS}, actual={labels}",
        ),
        _check(sample_id, evaluation, "port pairs valid for S8P", _port_pairs_valid(port_pairs), str(_port_pairs_as_dicts(port_pairs))),
        _check(sample_id, evaluation, "port pairs labels exist in layout", _pair_labels_exist(port_pairs, port_names), ",".join(port_names)),
    ]
    checks.extend(_power_line_checks(sample_id, evaluation, power_line, args))

    sample_dir = out_dir / "samples" / f"{index:02d}_{_slug(evaluation)}"
    copied: dict[str, str] = {}
    if args.copy_artifacts:
        sample_dir.mkdir(parents=True, exist_ok=True)
        copied = _copy_sample_artifacts(sample_dir, touchstone_path, layout_path, power_line_path, gds_path, preview_path)
        (sample_dir / "hfss_sample_rebuild_checklist.md").write_text(
            _render_sample_checklist(evaluation, labels, port_pairs, bridge_records),
            encoding="utf-8",
        )
        manifest = {
            "selection_rank": row.get("selection_rank", str(index)),
            "row_index": row.get("row_index", ""),
            "evaluation": evaluation,
            "source": "" if source is None else str(source),
            "evaluation_dir": "" if evaluation_dir is None else str(evaluation_dir),
            "touchstone_path": "" if touchstone_path is None else str(touchstone_path),
            "layout_json_path": "" if layout_path is None else str(layout_path),
            "power_line_8port_geometry_json_path": "" if power_line_path is None else str(power_line_path),
            "gds_path": "" if gds_path is None else str(gds_path),
            "preview_path": "" if preview_path is None else str(preview_path),
            "copied_artifacts": copied,
            "port_labels": labels,
            "port_pairs": _port_pairs_as_dicts(port_pairs),
            "bridge_records": bridge_records,
            "input_features": _row_features(row),
        }
        (sample_dir / "sample_handoff_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    return {
        "selection_rank": row.get("selection_rank", str(index)),
        "row_index": row.get("row_index", ""),
        "evaluation": evaluation,
        "overall_status": status,
        "source": "" if source is None else str(source),
        "evaluation_dir": "" if evaluation_dir is None else str(evaluation_dir),
        "touchstone_path": "" if touchstone_path is None else str(touchstone_path),
        "touchstone_sha256": "" if touchstone_path is None or not touchstone_path.is_file() else _sha256(touchstone_path),
        "layout_json_path": "" if layout_path is None else str(layout_path),
        "power_line_8port_geometry_json_path": "" if power_line_path is None else str(power_line_path),
        "gds_path": "" if gds_path is None else str(gds_path),
        "preview_path": "" if preview_path is None else str(preview_path),
        "handoff_sample_dir": "" if not args.copy_artifacts else str(sample_dir),
        "copied_artifacts": copied,
        "port_labels": labels,
        "port_name_to_role": label_to_role,
        "port_pairs": _port_pairs_as_dicts(port_pairs),
        "bridge_records": bridge_records,
        "input_features": _row_features(row),
        "checks": [check.as_dict() for check in checks],
        "_check_objects": checks,
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_sample_source(row: dict[str, str], dataset_dir: Path | None) -> Path | None:
    work_dir = _resolve_optional_path(row.get("work_dir"), dataset_dir)
    if work_dir is not None:
        if (work_dir / "layout").is_dir():
            return (work_dir / "layout").resolve()
        return work_dir.resolve()

    touchstone = _resolve_optional_path(row.get("touchstone_path"), dataset_dir)
    if touchstone is not None:
        eval_dir = _evaluation_dir_from_touchstone(touchstone)
        if eval_dir is not None:
            return (eval_dir / "layout").resolve() if (eval_dir / "layout").is_dir() else eval_dir.resolve()

    evaluation = (row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or "").strip()
    if evaluation and dataset_dir is not None:
        eval_dir = dataset_dir / "evaluations" / evaluation
        return (eval_dir / "layout").resolve() if (eval_dir / "layout").is_dir() else eval_dir.resolve()
    return None


def _resolve_optional_path(raw: str | None, dataset_dir: Path | None) -> Path | None:
    text = (raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path.resolve() if path.is_absolute() else None if dataset_dir is None else (dataset_dir / path).resolve()


def _resolve_touchstone(
    row: dict[str, str],
    dataset_dir: Path | None,
    source: Path | None,
    evaluation_dir: Path | None,
) -> Path | None:
    direct = _resolve_optional_path(row.get("touchstone_path"), dataset_dir)
    if direct is not None:
        return direct
    candidates: list[Path] = []
    if source is not None:
        candidates.extend(sorted(source.glob("*.s8p")))
        candidates.extend(sorted((source / "emx").glob("*.s8p")) if (source / "emx").is_dir() else [])
    if evaluation_dir is not None:
        candidates.extend(sorted((evaluation_dir / "emx").glob("*.s8p")) if (evaluation_dir / "emx").is_dir() else [])
        candidates.extend(sorted(evaluation_dir.glob("*.s8p")))
    return candidates[0].resolve() if candidates else None


def _evaluation_dir_from_touchstone(path: Path) -> Path | None:
    if path.parent.name == "emx":
        return path.parent.parent
    parts = path.parts
    if "evaluations" in parts:
        idx = parts.index("evaluations")
        if idx + 1 < len(parts):
            return Path(*parts[: idx + 2])
    return None


def _evaluation_dir_from_source(source: Path) -> Path:
    return source.parent if source.name == "layout" else source


def _find_layout_json(source: Path) -> Path | None:
    candidates = [
        source / "transformer_layout.layout.json",
        source / "layout" / "transformer_layout.layout.json",
        source / "layout.json",
    ]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    matches = sorted(source.glob("layout/*.layout.json")) + sorted(source.glob("*.layout.json"))
    return matches[0].resolve() if matches else None


def _find_power_line_geometry_json(source: Path) -> Path | None:
    candidates = [
        source / "power_line_8port_geometry.json",
        source / "layout" / "power_line_8port_geometry.json",
    ]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def _find_optional_file(source: Path, evaluation_dir: Path | None, patterns: list[str]) -> Path | None:
    roots = [source]
    if evaluation_dir is not None and evaluation_dir != source:
        roots.append(evaluation_dir)
    for root in roots:
        for pattern in patterns:
            direct = root / pattern
            if "*" not in pattern and direct.is_file():
                return direct.resolve()
            matches = sorted(root.glob(pattern))
            if matches:
                return matches[0].resolve()
    return None


def _read_json_if_possible(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _layout_port_names(layout: dict[str, Any]) -> list[str]:
    return list(_layout_port_records(layout))


def _layout_port_records(layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ports = layout.get("ports", [])
    if not isinstance(ports, list):
        return {}
    return {
        str(port.get("name")): port
        for port in ports
        if isinstance(port, dict) and port.get("name")
    }


def _layout_ports_have_expected_labels(port_records: dict[str, dict[str, Any]], *, suffix: str) -> bool:
    return not _missing_layout_port_labels(port_records, suffix=suffix)


def _missing_layout_port_labels(port_records: dict[str, dict[str, Any]], *, suffix: str) -> list[dict[str, str]]:
    label_key = "ground_labels" if suffix == "_G" else "signal_labels"
    missing: list[dict[str, str]] = []
    for index in range(1, 9):
        port_name = f"P{index:03d}"
        expected = f"{port_name}{suffix}"
        labels = _string_list((port_records.get(port_name) or {}).get(label_key))
        if expected not in labels:
            missing.append({"port": port_name, "expected": expected, "actual": ",".join(labels)})
    return missing


def _missing_layout_port_label_detail(port_records: dict[str, dict[str, Any]], *, suffix: str) -> str:
    return json.dumps(_missing_layout_port_labels(port_records, suffix=suffix), ensure_ascii=False, sort_keys=True)


def _role_labels_match_approved(labels: Any) -> bool:
    if not isinstance(labels, dict):
        return False
    normalized = {str(role): str(label) for role, label in labels.items()}
    return normalized == EXPECTED_ROLE_LABELS


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    if value in (None, ""):
        return []
    return [str(value)]


def _power_line_checks(sample: str, evaluation: str, power_line: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    if not power_line:
        return [_check(sample, evaluation, "power_line_8port geometry parses", False, "missing or invalid JSON")]
    tol = float(args.bridge_tolerance_um)
    checks = [
        _check(sample, evaluation, "power_line_8port enabled", bool(power_line.get("enabled")), str(power_line.get("enabled"))),
        _check(sample, evaluation, "power_line_8port schema recorded", "power_line_8port" in str(power_line.get("schema", "")), str(power_line.get("schema", ""))),
        _check(sample, evaluation, "primary and secondary power lines exist", isinstance(power_line.get("primary_power_line"), dict) and isinstance(power_line.get("secondary_power_line"), dict), ""),
    ]
    ground_frame_width = _as_float(power_line.get("ground_frame_width_um"))
    ground_edges = _ground_frame_edges_from_power_line(power_line)
    ground_tol = float(args.ground_frame_tolerance_um)
    checks.extend(
        [
            _check(
                sample,
                evaluation,
                "power_line_8port ground frame policy",
                str(power_line.get("ground_frame_policy", "")) == POWER_LINE_EXPECTED_GROUND_FRAME_POLICY,
                str(power_line.get("ground_frame_policy", "")),
            ),
            _check(
                sample,
                evaluation,
                "power_line_8port ground frame width matches contract",
                ground_frame_width is not None and abs(ground_frame_width - float(args.expected_ground_frame_width_um)) <= ground_tol,
                f"ground_frame_width_um={ground_frame_width}, expected={args.expected_ground_frame_width_um}",
            ),
            _check(
                sample,
                evaluation,
                "power_line_8port shield outer bbox expands inner window by ground frame width",
                ground_frame_width is not None
                and bool(ground_edges)
                and all(value is not None and abs(float(value) - ground_frame_width) <= ground_tol for value in ground_edges.values()),
                f"ground_frame_width_um={ground_frame_width}, edges={ground_edges}",
            ),
        ]
    )
    primary = power_line.get("primary_power_line", {}) if isinstance(power_line.get("primary_power_line"), dict) else {}
    secondary = power_line.get("secondary_power_line", {}) if isinstance(power_line.get("secondary_power_line"), dict) else {}
    vertical_length = _as_float(power_line.get("vertical_length_um"))
    max_outer_height = _as_float(power_line.get("max_outer_height_um"))
    if max_outer_height is None:
        max_outer_height = _as_float(power_line.get("max_outer_diameter_um"))
    ratio = _as_float(power_line.get("vertical_length_diameter_ratio"))
    expected_vertical_length = _as_float(power_line.get("expected_vertical_length_um"))
    ph = _as_float(primary.get("height_um"))
    sh = _as_float(secondary.get("height_um"))
    expected_ratio = float(args.expected_vertical_length_ratio)
    computed_vertical_length = None if max_outer_height is None else float(max_outer_height) * expected_ratio
    checks.extend(
        [
            _check(
                sample,
                evaluation,
                "power_line_8port vertical length positive",
                vertical_length is not None and vertical_length > 0.0,
                f"vertical_length_um={vertical_length}",
            ),
            _check(
                sample,
                evaluation,
                "power_line_8port max outer height positive",
                max_outer_height is not None and max_outer_height > 0.0,
                f"max_outer_height_um={max_outer_height}",
            ),
            _check(
                sample,
                evaluation,
                "power_line_8port vertical length ratio",
                ratio is not None and abs(float(ratio) - expected_ratio) <= 1.0e-12,
                f"expected={expected_ratio}, actual={ratio}",
            ),
            _check(
                sample,
                evaluation,
                "power_line_8port vertical length equals 1.5*max coil height",
                vertical_length is not None
                and computed_vertical_length is not None
                and abs(float(vertical_length) - float(computed_vertical_length)) <= tol,
                f"vertical_length_um={vertical_length}, max_outer_height_um={max_outer_height}, computed={computed_vertical_length}",
            ),
            _check(
                sample,
                evaluation,
                "power_line_8port stored expected vertical length matches",
                vertical_length is not None
                and expected_vertical_length is not None
                and abs(float(vertical_length) - float(expected_vertical_length)) <= tol,
                f"vertical_length_um={vertical_length}, expected_vertical_length_um={expected_vertical_length}",
            ),
            _check(
                sample,
                evaluation,
                "left/right power-line heights equal vertical length",
                vertical_length is not None
                and ph is not None
                and sh is not None
                and abs(ph - float(vertical_length)) <= tol
                and abs(sh - float(vertical_length)) <= tol
                and abs(ph - sh) <= tol,
                f"vertical={vertical_length}, primary={ph}, secondary={sh}",
            ),
        ]
    )
    physical_left, physical_right, primary_is_left = _physical_left_right_power_lines(power_line)
    checks.append(
        _check(
            sample,
            evaluation,
            "physical left/right power-line order is explicit",
            primary_is_left in {True, False},
            f"primary_x={_as_float(primary.get('center_x_um'))}, secondary_x={_as_float(secondary.get('center_x_um'))}, primary_is_left={primary_is_left}",
        )
    )
    labels = power_line.get("labels") if isinstance(power_line.get("labels"), dict) else {}
    endpoint_expectations = {
        "physical left top power port": (physical_left, "top_port_label", labels.get("left_power_top")),
        "physical left bottom power port": (physical_left, "bottom_port_label", labels.get("left_power_bottom")),
        "physical right top power port": (physical_right, "top_port_label", labels.get("right_power_top")),
        "physical right bottom power port": (physical_right, "bottom_port_label", labels.get("right_power_bottom")),
        "physical left top ground": (physical_left, "top_ground_label", f"{labels.get('left_power_top')}_G" if labels.get("left_power_top") else None),
        "physical left bottom ground": (physical_left, "bottom_ground_label", f"{labels.get('left_power_bottom')}_G" if labels.get("left_power_bottom") else None),
        "physical right top ground": (physical_right, "top_ground_label", f"{labels.get('right_power_top')}_G" if labels.get("right_power_top") else None),
        "physical right bottom ground": (physical_right, "bottom_ground_label", f"{labels.get('right_power_bottom')}_G" if labels.get("right_power_bottom") else None),
    }
    for name, (section, key, expected) in endpoint_expectations.items():
        actual = section.get(key) if isinstance(section, dict) else None
        checks.append(_check(sample, evaluation, name, expected is not None and actual == expected, f"expected={expected}, actual={actual}"))
    checks.extend(_bridge_checks(sample, evaluation, "primary_bridge", power_line.get("primary_bridge"), args))
    checks.extend(_bridge_checks(sample, evaluation, "secondary_bridge", power_line.get("secondary_bridge"), args))
    return checks


def _physical_left_right_power_lines(power_line: Any) -> tuple[dict[str, Any], dict[str, Any], bool | None]:
    if not isinstance(power_line, dict):
        return {}, {}, None
    primary = power_line.get("primary_power_line", {}) if isinstance(power_line.get("primary_power_line"), dict) else {}
    secondary = power_line.get("secondary_power_line", {}) if isinstance(power_line.get("secondary_power_line"), dict) else {}
    primary_x = _as_float(primary.get("center_x_um"))
    secondary_x = _as_float(secondary.get("center_x_um"))
    if primary_x is None or secondary_x is None or primary_x == secondary_x:
        return {}, {}, None
    if primary_x < secondary_x:
        return primary, secondary, True
    return secondary, primary, False


def _bridge_checks(sample: str, evaluation: str, name: str, raw: Any, args: argparse.Namespace) -> list[Check]:
    if not isinstance(raw, dict):
        return [_check(sample, evaluation, f"{name} evidence exists", False, "missing")]
    tol = float(args.bridge_tolerance_um)
    width = _as_float(raw.get("width_um"))
    delta_y = _as_float(raw.get("delta_y_um"))
    center_y = _as_float(raw.get("center_y_um"))
    length = _as_float(raw.get("length_um"))
    align = _as_float(raw.get("power_line_edge_alignment_error_um"))
    left_edge = _as_float(raw.get("power_line_left_edge_x_um"))
    right_edge = _as_float(raw.get("power_line_right_edge_x_um"))
    power_line_width = None if left_edge is None or right_edge is None else abs(right_edge - left_edge)
    extends_away = raw.get("extends_away_from_coil_interior")
    return [
        _check(sample, evaluation, f"{name} evidence exists", True, "present"),
        _bridge_width_contract_check(sample, evaluation, name, width, raw, args, tol),
        _check(
            sample,
            evaluation,
            f"{name} width matches recorded power-line edge width",
            width is not None and power_line_width is not None and abs(width - power_line_width) <= tol,
            f"width_um={width}, power_line_width_from_edges_um={power_line_width}",
        ),
        _check(sample, evaluation, f"{name} is horizontal", bool(raw.get("is_horizontal")) and delta_y is not None and abs(delta_y) <= tol, f"is_horizontal={raw.get('is_horizontal')}, delta_y_um={delta_y}"),
        _check(sample, evaluation, f"{name} centered at y=0", center_y is not None and abs(center_y) <= tol, f"center_y_um={center_y}"),
        _check(sample, evaluation, f"{name} length positive", length is not None and length > 0.0, f"length_um={length}"),
        _check(sample, evaluation, f"{name} touches nearest power-line edge", align is not None and abs(align) <= tol, f"edge_alignment_error_um={align}"),
        _check(
            sample,
            evaluation,
            f"{name} stays outside coil interior",
            extends_away is True,
            f"extends_away_from_coil_interior={extends_away}",
        ),
    ]


def _bridge_width_contract_check(
    sample: str,
    evaluation: str,
    name: str,
    width: float | None,
    bridge: dict[str, Any],
    args: argparse.Namespace,
    tol: float,
) -> Check:
    explicit_expected = _as_float(args.expected_bridge_width_um)
    if explicit_expected is not None:
        return _check(
            sample,
            evaluation,
            f"{name} width matches explicit expected bridge width",
            width is not None and abs(width - explicit_expected) <= tol,
            f"width_um={width}, expected_bridge_width_um={explicit_expected}",
        )
    recorded_expected = _as_float(bridge.get("expected_width_um"))
    if recorded_expected is None:
        recorded_expected = _as_float(bridge.get("line_width_um"))
    return _check(
        sample,
        evaluation,
        f"{name} width matches recorded shared line width",
        width is not None and recorded_expected is not None and abs(width - recorded_expected) <= tol,
        f"width_um={width}, recorded_expected_width_um={recorded_expected}",
    )


def _bridge_records(power_line: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for role in ("primary_bridge", "secondary_bridge"):
        bridge = power_line.get(role, {}) if isinstance(power_line.get(role), dict) else {}
        rows.append(
            {
                "bridge": role,
                "coil_anchor_x_um": _nested_float(bridge, "coil_anchor", "x_um"),
                "coil_anchor_y_um": _nested_float(bridge, "coil_anchor", "y_um"),
                "power_line_edge_x_um": _nested_float(bridge, "power_line_edge", "x_um"),
                "power_line_edge_y_um": _nested_float(bridge, "power_line_edge", "y_um"),
                "width_um": bridge.get("width_um", ""),
                "length_um": bridge.get("length_um", ""),
                "delta_y_um": bridge.get("delta_y_um", ""),
                "center_y_um": bridge.get("center_y_um", ""),
                "power_line_edge_alignment_error_um": bridge.get("power_line_edge_alignment_error_um", ""),
                "is_horizontal": bridge.get("is_horizontal", ""),
                "extends_away_from_coil_interior": bridge.get("extends_away_from_coil_interior", ""),
            }
        )
    return rows


def _copy_sample_artifacts(
    sample_dir: Path,
    touchstone: Path | None,
    layout: Path | None,
    power_line: Path | None,
    gds: Path | None,
    preview: Path | None,
) -> dict[str, str]:
    copied = {}
    for key, path, filename in (
        ("emx_s8p", touchstone, "emx_reference.s8p"),
        ("layout_json", layout, "transformer_layout.layout.json"),
        ("power_line_8port_geometry_json", power_line, "power_line_8port_geometry.json"),
        ("gds", gds, None if gds is None else gds.name),
        ("preview", preview, None if preview is None else preview.name),
    ):
        if path is None or not path.is_file() or filename is None:
            continue
        dest = sample_dir / filename
        shutil.copy2(path, dest)
        copied[key] = str(dest)
    return copied


def _write_port_map_csv(path: Path, sample_results: list[dict[str, Any]], port_pairs: list[tuple[int, int]]) -> None:
    rows = []
    pair_role = _pair_role_map(port_pairs)
    for result in sample_results:
        labels = result.get("port_labels", {})
        role_by_label = {str(label): role for role, label in labels.items()}
        for idx in range(1, 9):
            port_name = f"P{idx:03d}"
            rows.append(
                {
                    "sample": result.get("selection_rank", ""),
                    "evaluation": result.get("evaluation", ""),
                    "port_name": port_name,
                    "role": role_by_label.get(port_name, ""),
                    "hfss_terminal_name": port_name,
                    "ground_reference": f"{port_name}_G",
                    "differential_pair_role": pair_role.get(idx, ""),
                    "source_touchstone": result.get("touchstone_path", ""),
                }
            )
    _write_csv(path, rows)


def _write_bridge_geometry_csv(path: Path, sample_results: list[dict[str, Any]]) -> None:
    rows = []
    for result in sample_results:
        for record in result.get("bridge_records", []):
            row = {
                "sample": result.get("selection_rank", ""),
                "evaluation": result.get("evaluation", ""),
            }
            row.update(record)
            rows.append(row)
    _write_csv(path, rows)


def _write_pair_csv(path: Path, sample_results: list[dict[str, Any]], port_pairs: list[tuple[int, int]]) -> None:
    rows = []
    for result in sample_results:
        labels = result.get("port_labels", {})
        role_by_label = {str(label): role for role, label in labels.items()}
        for index, (plus, minus) in enumerate(port_pairs, start=1):
            plus_name = f"P{plus:03d}"
            minus_name = f"P{minus:03d}"
            rows.append(
                {
                    "sample": result.get("selection_rank", ""),
                    "evaluation": result.get("evaluation", ""),
                    "pair_index": index,
                    "plus_port": plus_name,
                    "plus_role": role_by_label.get(plus_name, ""),
                    "minus_port": minus_name,
                    "minus_role": role_by_label.get(minus_name, ""),
                    "touchstone_pair_syntax": f"{plus},{minus}",
                }
            )
    _write_csv(path, rows)


def _write_checks_csv(path: Path, checks: list[Check]) -> None:
    _write_csv(path, [check.as_dict() for check in checks])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _layout_audit_summary_check(path_raw: str | None, *, allow_missing: bool = False) -> Check:
    if not path_raw:
        return _check(
            "",
            "",
            "selected layout audit summary supplied",
            bool(allow_missing),
            "missing --layout-audit-summary; use --allow-missing-layout-audit only for debug packets",
        )
    path = Path(path_raw).expanduser().resolve()
    if not path.is_file():
        return _check("", "", "selected layout audit summary exists", False, str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _check("", "", "selected layout audit summary parses", False, f"{type(exc).__name__}: {exc}")
    passed = data.get("overall_status") == "PASS" and data.get("decision") == "SELECTED_SAMPLE_LAYOUTS_READY_FOR_HFSS_ADS_VALIDATION"
    return _check("", "", "selected layout audit already passed", passed, f"overall_status={data.get('overall_status')}, decision={data.get('decision')}")


def _parse_port_pairs(text: str) -> tuple[list[tuple[int, int]], list[str]]:
    pairs = []
    errors = []
    for item in str(text).split(":"):
        item = item.strip()
        if not item:
            continue
        pieces = [piece.strip() for piece in item.split(",") if piece.strip()]
        if len(pieces) != 2:
            errors.append(f"pair {item!r} must have two ports")
            continue
        try:
            left, right = int(pieces[0]), int(pieces[1])
        except ValueError:
            errors.append(f"pair {item!r} contains non-integer ports")
            continue
        pairs.append((left, right))
    if not pairs:
        errors.append("no port pairs supplied")
    seen = [port for pair in pairs for port in pair]
    duplicates = sorted({port for port in seen if seen.count(port) > 1})
    if duplicates:
        errors.append(f"duplicate ports in pairs: {duplicates}")
    if not _port_pairs_valid(pairs):
        errors.append("ports must be in 1..8")
    return pairs, errors


def _port_pairs_valid(port_pairs: list[tuple[int, int]]) -> bool:
    return bool(port_pairs) and all(1 <= left <= 8 and 1 <= right <= 8 and left != right for left, right in port_pairs)


def _pair_labels_exist(port_pairs: list[tuple[int, int]], port_names: list[str]) -> bool:
    names = set(port_names)
    return all(f"P{left:03d}" in names and f"P{right:03d}" in names for left, right in port_pairs)


def _port_pairs_as_dicts(port_pairs: list[tuple[int, int]]) -> list[dict[str, Any]]:
    return [
        {
            "pair_index": index,
            "plus_port_index": left,
            "minus_port_index": right,
            "plus_port_name": f"P{left:03d}",
            "minus_port_name": f"P{right:03d}",
        }
        for index, (left, right) in enumerate(port_pairs, start=1)
    ]


def _pair_role_map(port_pairs: list[tuple[int, int]]) -> dict[int, str]:
    out = {}
    for index, (left, right) in enumerate(port_pairs, start=1):
        out[left] = f"pair_{index}_plus"
        out[right] = f"pair_{index}_minus"
    return out


def _row_features(row: dict[str, str]) -> dict[str, str]:
    suffixes = ("_center", "_nh", "_um")
    prefixes = ("lp", "ls", "q", "k", "width", "spacing", "diameter", "turn")
    return {
        key: value
        for key, value in row.items()
        if key.lower().startswith(prefixes) or key.lower().endswith(suffixes)
    }


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _bbox_dict(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for key in ("min_x_um", "min_y_um", "max_x_um", "max_y_um"):
        value = _as_float(raw.get(key))
        if value is None:
            return None
        out[key] = float(value)
    return out


def _ground_frame_edges_from_power_line(power_line: dict[str, Any]) -> dict[str, float] | None:
    inner = _bbox_dict(power_line.get("shield_inner_bbox_um"))
    outer = _bbox_dict(power_line.get("shield_outer_bbox_um"))
    if inner is None or outer is None:
        return None
    return {
        "left_um": float(inner["min_x_um"]) - float(outer["min_x_um"]),
        "right_um": float(outer["max_x_um"]) - float(inner["max_x_um"]),
        "bottom_um": float(inner["min_y_um"]) - float(outer["min_y_um"]),
        "top_um": float(outer["max_y_um"]) - float(inner["max_y_um"]),
    }


def _nested_float(data: dict[str, Any], key: str, child: str) -> Any:
    raw = data.get(key, {})
    if not isinstance(raw, dict):
        return ""
    return raw.get(child, "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    allowed = []
    for char in str(value):
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        else:
            allowed.append("_")
    text = "".join(allowed).strip("_")
    return text[:80] or "sample"


def _check(sample: str, evaluation: str, name: str, passed: bool, detail: Any) -> Check:
    return Check(str(sample), str(evaluation), "PASS" if passed else "FAIL", name, str(detail))


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Selected S8P HFSS Handoff Packet",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Samples CSV: `{summary['samples_csv']}`",
        f"- Dataset dir: `{summary['dataset_dir']}`",
        f"- Selected/pass/fail: {summary['selected_count']} / {summary['pass_count']} / {summary['fail_count']}",
        f"- Port pairs: `{_pair_text(summary['port_pairs'])}`",
        "",
        "## Samples",
        "",
        "| Rank | Evaluation | Status | EMX S8P | Layout JSON | Power-Line JSON | Handoff Folder |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in summary["sample_results"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(result["selection_rank"]),
                    _cell(result["evaluation"]),
                    _cell(result["overall_status"]),
                    f"`{_cell(result['touchstone_path'])}`",
                    f"`{_cell(result['layout_json_path'])}`",
                    f"`{_cell(result['power_line_8port_geometry_json_path'])}`",
                    f"`{_cell(result['handoff_sample_dir'])}`",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Required HFSS Rebuild Evidence", ""])
    lines.extend(
        [
            "- `hfss_port_map.csv`: P001-P008 role names, ground labels, and differential-pair polarity.",
            "- `hfss_bridge_geometry.csv`: exact same-width bridge anchor coordinates and center-y checks.",
            "- `hfss_differential_port_pairs.csv`: port-pair syntax used later for Lp/Ls/Q/K extraction.",
            "- `hfss_ads_formula_trace.md`: port-pair-to-formula trace for ADS/Python Lp/Ls/Q/K extraction.",
            "- `samples/*/sample_handoff_manifest.json`: per-sample source paths, hashes, copied evidence, and input features.",
        ]
    )
    lines.extend(["", "## Checks", "", "| Status | Sample | Evaluation | Check | Detail |", "| --- | --- | --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['status'])} | {_cell(check.get('sample', ''))} | {_cell(check.get('evaluation', ''))} | {_cell(check['name'])} | {_cell(check['detail'])} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _render_rebuild_checklist(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# HFSS Rebuild Checklist for Selected S8P Sample",
            "",
            "Use this checklist after the handoff packet is PASS.",
            "",
            "1. Open the selected sample folder under `samples/` and read `sample_handoff_manifest.json`.",
            "2. Rebuild or import the transformer geometry from `transformer_layout.layout.json` and, if available, the copied GDS.",
            "3. Use `power_line_8port_geometry.json` to place the left/right vertical power lines, same-width centered horizontal bridges, and P001-P008 ports.",
            "4. Confirm the two bridge rows in `hfss_bridge_geometry.csv`: width matches the recorded shared line width / vertical power-line width, y is centered at 0, and edge alignment error is zero within tolerance.",
            "4a. Confirm each bridge row has `extends_away_from_coil_interior=True`, so the horizontal connector does not intrude into the coil interior.",
            "5. Assign all eight single-ended ports with shield ground references matching `hfss_port_map.csv`.",
            "6. Run HFSS from 5 GHz to 60 GHz with 1.0 GHz output spacing and export the selected sample as `.s8p`.",
            "7. Extract Lp/Ls/Q/K with the exact port pairs in `hfss_differential_port_pairs.csv`.",
            "8. Use `hfss_ads_formula_trace.md` as the port-pair/formula reference for ADS/Python curve extraction.",
            "9. Compare EMX and HFSS curves with the accepted comparison script and keep the <=10% error table for reporting.",
            "",
            f"Current handoff decision: **{summary['decision']}**",
        ]
    ) + "\n"


def _render_sample_checklist(
    evaluation: str,
    labels: dict[str, Any],
    port_pairs: list[tuple[int, int]],
    bridge_records: list[dict[str, Any]],
) -> str:
    lines = [
        f"# HFSS Rebuild Notes: {evaluation}",
        "",
        "## Port Labels",
        "",
        "| Role | Port | Ground |",
        "| --- | --- | --- |",
    ]
    for role in EXPECTED_ROLES:
        port = str(labels.get(role, ""))
        lines.append(f"| {role} | {port} | {port}_G |")
    lines.extend(["", "## Differential Pairs", "", "| Pair | Plus | Minus |", "| --- | --- | --- |"])
    for index, (plus, minus) in enumerate(port_pairs, start=1):
        lines.append(f"| {index} | P{plus:03d} | P{minus:03d} |")
    lines.extend(["", "## Bridge Geometry", "", "| Bridge | Coil anchor x/y (um) | Power-line edge x/y (um) | Width (um) | Length (um) |", "| --- | --- | --- | --- | --- |"])
    for record in bridge_records:
        lines.append(
            f"| {record['bridge']} | {record['coil_anchor_x_um']}, {record['coil_anchor_y_um']} | "
            f"{record['power_line_edge_x_um']}, {record['power_line_edge_y_um']} | {record['width_um']} | {record['length_um']} |"
        )
    return "\n".join(lines) + "\n"


def _render_ads_formula_trace(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS/ADS Formula Trace for Selected S8P Sample",
        "",
        f"- Handoff decision: **{summary['decision']}**",
        f"- Port-pair syntax: `{_pair_text(summary['port_pairs'])}`",
        f"- Frequency contract: `5 GHz to 60 GHz, 1.0 GHz step, 56 points`",
        "",
        "## Differential Port Definition",
        "",
        "The single-ended `.s8p` ports are converted to a 2-port differential impedance matrix with the recorded pair polarity.",
        "",
        "```text",
        "Z_diff = transpose(T) * Z_single * T",
        "pair_1 = primary differential terminal",
        "pair_2 = secondary differential terminal",
        "```",
        "",
        "## Recorded Port Pairs",
        "",
        "| Pair | Plus Port | Plus Role | Minus Port | Minus Role | Touchstone Syntax |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in summary.get("sample_results", []):
        role_by_label = {str(label): role for role, label in (result.get("port_labels") or {}).items()}
        for pair in summary.get("port_pairs", []):
            plus = str(pair.get("plus_port_name", ""))
            minus = str(pair.get("minus_port_name", ""))
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(pair.get("pair_index", "")),
                        plus,
                        role_by_label.get(plus, ""),
                        minus,
                        role_by_label.get(minus, ""),
                        f"{pair.get('plus_port_index')},{pair.get('minus_port_index')}",
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## ADS/Python Metric Equations",
            "",
            "Use the same differential `Zdiff` ordering in ADS Data Display or Python post-processing.",
            "",
            "```text",
            "Zp = Zdiff[1,1]",
            "Zs = Zdiff[2,2]",
            "Zm = Zdiff[2,1]",
            "Lp = imag(Zdiff[1,1]) / omega",
            "Ls = imag(Zdiff[2,2]) / omega",
            "M  = imag(Zdiff[2,1]) / omega",
            "Qp = imag(Zdiff[1,1]) / real(Zdiff[1,1])",
            "Qs = imag(Zdiff[2,2]) / real(Zdiff[2,2])",
            "Q  = min(Qp, Qs)",
            "K  = M / sqrt(abs(Lp * Ls))",
            "Kw = K",
            "k  = M / sqrt(abs(Lp * Ls))",
            "```",
            "",
            "## Validation Rule",
            "",
            "The EMX `.s8p` and HFSS `.s8p` must use the same port-pair syntax, the same 5-60 GHz / 1.0 GHz grid, and the same equations above before any <=10% Lp/Ls/Q/K/Kw comparison is accepted.",
        ]
    )
    return "\n".join(lines) + "\n"


def _pair_text(pairs: list[dict[str, Any]]) -> str:
    return ":".join(f"{item['plus_port_index']},{item['minus_port_index']}" for item in pairs)


if __name__ == "__main__":
    raise SystemExit(main())
