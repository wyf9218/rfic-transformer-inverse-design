#!/usr/bin/env python3
"""Package one real EMX S4P sample for professor-side HFSS validation.

This script is intentionally post-run only. It reads an existing EMX dataset
directory, selects one row with a real `.s4p`, copies traceable artifacts, and
plots ADS-style Lp/Ls/Q/|K| curves from that `.s4p`.

It does not run EMX, HFSS, ADS, or invent any physical-feature values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from plot_emx_hfss_ads_style_metrics import (  # noqa: E402
    _extract_metric_curves,
    _slice_window,
    _write_single_panel,
    _write_source_csv,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset_csv = dataset_dir / "dataset_rows.csv"
    rows = _read_rows(dataset_csv)
    candidates, candidate_checks = _valid_candidates(rows, dataset_dir, args)
    selected = _select_one(candidates, int(args.seed))

    checks = [
        _check("dataset_rows_csv_exists", dataset_csv.is_file(), str(dataset_csv)),
        _check("dataset_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("valid_real_s4p_candidates_present", bool(candidates), f"candidates={len(candidates)}"),
        *candidate_checks,
        _check("selected_sample_present", selected is not None, "" if selected else "no valid sample selected"),
    ]

    artifacts: dict[str, Any] = {}
    selected_summary: dict[str, Any] | None = None
    if selected is not None:
        sample_dir = out_dir / f"sample_{selected['evaluation']}"
        sample_dir.mkdir(parents=True, exist_ok=True)
        touchstone_src = Path(selected["touchstone_path"])
        touchstone_dst = sample_dir / f"{selected['evaluation']}_EMX_reference.s4p"
        shutil.copy2(touchstone_src, touchstone_dst)

        curves = _slice_window(
            _extract_metric_curves(
                "EMX",
                touchstone_dst,
                str(args.port_pairs),
                ground_unused_ports=False,
            ),
            float(args.start_ghz),
            float(args.stop_ghz),
        )
        metric_plot = sample_dir / f"{selected['evaluation']}_EMX_Lp_Ls_Q_K_5_60GHz.png"
        metric_csv = sample_dir / f"{selected['evaluation']}_EMX_Lp_Ls_Q_K_5_60GHz.csv"
        _write_single_panel(
            metric_plot,
            curves,
            float(args.target_ghz),
            int(args.dpi),
            core_only=True,
            plot_signed_k=False,
        )
        _write_source_csv(metric_csv, [curves])

        layout_artifacts = _collect_layout_artifacts(
            work_dir=Path(selected["work_dir"]) if selected.get("work_dir") else None,
            sample_dir=sample_dir,
            max_gds=int(args.max_gds),
            max_png=int(args.max_png),
        )
        selected_summary = _selected_summary(selected, curves, args)
        artifacts = {
            "sample_dir": str(sample_dir),
            "emx_touchstone": _file_record(touchstone_dst),
            "emx_metric_plot": _file_record(metric_plot),
            "emx_metric_csv": _file_record(metric_csv),
            "layout_artifacts": layout_artifacts,
        }
        checks.extend(layout_artifacts["checks"])

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_SAMPLE_FOR_PROFESSOR_HFSS_VALIDATION" if status == "PASS" else "DO_NOT_PRESENT_UNTIL_CHECKS_PASS",
        "dataset_dir": str(dataset_dir),
        "dataset_rows_csv": _file_record(dataset_csv),
        "out_dir": str(out_dir),
        "seed": int(args.seed),
        "selection_mode": "deterministic_random_from_valid_real_s4p_rows",
        "candidate_count": len(candidates),
        "selected": selected_summary,
        "artifacts": artifacts,
        "checks": checks,
        "requirements": {
            "touchstone_extension": ".s4p",
            "ports": 4,
            "frequency_start_ghz": float(args.start_ghz),
            "frequency_stop_ghz": float(args.stop_ghz),
            "frequency_step_ghz": float(args.step_ghz),
            "frequency_points": int(args.frequency_points),
            "port_pairs": str(args.port_pairs),
        },
        "limitations": [
            "All plotted physical curves are extracted from the copied real EMX .s4p.",
            "This package does not contain HFSS results; the professor will build HFSS separately and compare exported .s4p curves.",
            "If GDS/layout artifacts are missing from the EMX work_dir, the package fails rather than inventing a layout.",
        ],
    }
    summary_path = out_dir / "mars56_grounded_s4p_validation_sample_summary.json"
    report_path = out_dir / "mars56_grounded_s4p_validation_sample_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if selected is not None:
        print(f"sample_dir={artifacts['sample_dir']}")
        print(f"touchstone={artifacts['emx_touchstone']['path']}")
        print(f"metric_plot={artifacts['emx_metric_plot']['path']}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--start-ghz", type=float, default=5.0)
    parser.add_argument("--stop-ghz", type=float, default=60.0)
    parser.add_argument("--step-ghz", type=float, default=1.0)
    parser.add_argument("--frequency-points", type=int, default=56)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--max-gds", type=int, default=8)
    parser.add_argument("--max-png", type=int, default=8)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _valid_candidates(
    rows: list[dict[str, str]],
    dataset_dir: Path,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        touchstone_raw = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        touchstone = _resolve(dataset_dir, touchstone_raw) if touchstone_raw else None
        if touchstone is None or not touchstone.is_file() or touchstone.suffix.lower() != ".s4p":
            continue
        try:
            result = load_touchstone(touchstone)
            _assert_touchstone_contract(result, args)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"row {index} {touchstone}: {type(exc).__name__}: {exc}")
            continue
        work_dir_raw = (row.get("work_dir") or "").strip()
        work_dir = _resolve(dataset_dir, work_dir_raw) if work_dir_raw else None
        candidates.append(
            {
                "row_index": index,
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{index:06d}",
                "touchstone_path": str(touchstone),
                "work_dir": "" if work_dir is None else str(work_dir),
                "row": row,
            }
        )
    checks = [
        _check("candidate_touchstone_contract_failures_absent", not failures, failures[:20] if failures else "all candidate .s4p files passed"),
    ]
    return candidates, checks


def _assert_touchstone_contract(result: Any, args: argparse.Namespace) -> None:
    freqs_hz = np.asarray(result.freqs_hz, dtype=float)
    if int(result.s_matrix.shape[1]) != 4:
        raise ValueError(f"expected 4 ports, got {result.s_matrix.shape[1]}")
    expected = np.arange(float(args.start_ghz), float(args.stop_ghz) + 0.5 * float(args.step_ghz), float(args.step_ghz)) * 1.0e9
    if len(freqs_hz) != int(args.frequency_points):
        raise ValueError(f"expected {args.frequency_points} frequency points, got {len(freqs_hz)}")
    if len(freqs_hz) != len(expected) or not np.allclose(freqs_hz, expected, atol=1.0, rtol=0.0):
        raise ValueError("frequency grid is not 5-60 GHz at 1 GHz spacing")


def _select_one(candidates: list[dict[str, Any]], seed: int) -> dict[str, Any] | None:
    if not candidates:
        return None
    rng = np.random.default_rng(int(seed))
    index = int(rng.integers(0, len(candidates)))
    item = dict(candidates[index])
    item["selection_reason"] = "deterministic_random_seeded_real_s4p"
    return item


def _collect_layout_artifacts(work_dir: Path | None, sample_dir: Path, max_gds: int, max_png: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    out = {
        "work_dir": "" if work_dir is None else str(work_dir),
        "copied_gds": [],
        "copied_png": [],
        "copied_json": [],
        "checks": checks,
    }
    if work_dir is None or not work_dir.is_dir():
        checks.append(_check("work_dir_exists", False, "" if work_dir is None else str(work_dir)))
        return out
    checks.append(_check("work_dir_exists", True, str(work_dir)))
    artifact_dir = sample_dir / "layout_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    gds_files = sorted(work_dir.rglob("*.gds"), key=lambda p: (0 if "cadpins" in p.name.lower() else 1, len(str(p)), str(p)))
    png_files = sorted(
        [p for p in work_dir.rglob("*.png") if any(token in p.name.lower() for token in ("layout", "preview", "port", "debug"))],
        key=lambda p: (len(str(p)), str(p)),
    )
    json_files = sorted(
        [p for p in work_dir.rglob("*.json") if any(token in p.name.lower() for token in ("manifest", "power_line", "summary", "cadence", "geometry"))],
        key=lambda p: (len(str(p)), str(p)),
    )
    for src in gds_files[: max(0, int(max_gds))]:
        dst = artifact_dir / _safe_artifact_name(work_dir, src)
        shutil.copy2(src, dst)
        out["copied_gds"].append(_file_record(dst))
    for src in png_files[: max(0, int(max_png))]:
        dst = artifact_dir / _safe_artifact_name(work_dir, src)
        shutil.copy2(src, dst)
        out["copied_png"].append(_file_record(dst))
    for src in json_files[:20]:
        dst = artifact_dir / _safe_artifact_name(work_dir, src)
        shutil.copy2(src, dst)
        out["copied_json"].append(_file_record(dst))
    if not out["copied_png"] and out["copied_gds"]:
        rendered = _try_render_layout_preview_from_gds(out, artifact_dir)
        if rendered is not None:
            out["copied_png"].append(_file_record(rendered))
            checks.append(_check("layout_png_rendered_from_real_gds", True, str(rendered)))
        else:
            checks.append(
                _check(
                    "layout_png_rendered_from_real_gds",
                    False,
                    "no copied layout PNG was present and rendering from real GDS failed",
                )
            )
    checks.append(_check("gds_artifact_present", bool(out["copied_gds"]), f"gds_count={len(out['copied_gds'])}"))
    checks.append(_check("layout_or_debug_png_present", bool(out["copied_png"]), f"png_count={len(out['copied_png'])}"))
    return out


def _try_render_layout_preview_from_gds(layout_artifacts: dict[str, Any], artifact_dir: Path) -> Path | None:
    gds_records = layout_artifacts.get("copied_gds") or []
    if not gds_records:
        return None
    gds_path = Path(gds_records[0]["path"])
    if not gds_path.is_file():
        return None
    manifest_path: Path | None = None
    for record in layout_artifacts.get("copied_json") or []:
        path = Path(record["path"])
        if path.is_file() and "manifest" in path.name.lower():
            manifest_path = path
            break
    out_path = artifact_dir / f"{gds_path.stem}_rendered_from_real_gds.png"
    try:
        from rfic_transformer_inverse_design.sim.emx.render import render_emx_layout_preview

        render_emx_layout_preview(gds_path, out_path, manifest_path=manifest_path)
    except Exception:  # noqa: BLE001 - package must fail cleanly rather than fabricate an image.
        return None
    return out_path if out_path.is_file() else None


def _selected_summary(selected: dict[str, Any], curves: Any, args: argparse.Namespace) -> dict[str, Any]:
    freq_ghz = np.asarray(curves.freq_hz, dtype=float) / 1.0e9
    target_idx = int(np.argmin(np.abs(freq_ghz - float(args.target_ghz))))
    row = selected.get("row") or {}
    geometry = {key: row[key] for key in sorted(row) if key.startswith("geom__")}
    return {
        "row_index": int(selected["row_index"]),
        "evaluation": selected["evaluation"],
        "selection_reason": selected.get("selection_reason", ""),
        "source_touchstone": selected["touchstone_path"],
        "source_work_dir": selected.get("work_dir", ""),
        "target_marker_ghz": float(freq_ghz[target_idx]),
        "lp_nh_at_target": float(curves.lp_nh[target_idx]),
        "ls_nh_at_target": float(curves.ls_nh[target_idx]),
        "q_at_target": float(curves.q[target_idx]),
        "k_abs_at_target": float(abs(curves.k[target_idx])),
        "geometry_columns": geometry,
    }


def _safe_artifact_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    return "__".join(rel.parts)


def _resolve(dataset_dir: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else dataset_dir / path


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _file_record(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    out.update({"size_bytes": path.stat().st_size, "sha256": _sha256(path)})
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    selected = summary.get("selected") or {}
    lines = [
        "# MARS56 Grounded S4P Validation Sample",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Candidate count: `{summary['candidate_count']}`",
    ]
    if selected:
        lines.extend(
            [
                f"- Selected sample: `{selected.get('evaluation')}`",
                f"- Source `.s4p`: `{selected.get('source_touchstone')}`",
                f"- Source work_dir: `{selected.get('source_work_dir')}`",
                f"- 15 GHz marker: Lp={selected.get('lp_nh_at_target'):.6g} nH, "
                f"Ls={selected.get('ls_nh_at_target'):.6g} nH, "
                f"Q={selected.get('q_at_target'):.6g}, |K|={selected.get('k_abs_at_target'):.6g}",
            ]
        )
    lines.extend(["", "## Checks"])
    for item in summary["checks"]:
        mark = "PASS" if item["pass"] else "FAIL"
        lines.append(f"- {mark}: {item['name']} - {item['detail']}")
    lines.extend(["", "## Artifacts", ""])
    artifacts = summary.get("artifacts") or {}
    for key, value in artifacts.items():
        if isinstance(value, dict) and "path" in value:
            lines.append(f"- {key}: `{value['path']}`")
        elif key == "layout_artifacts" and isinstance(value, dict):
            lines.append(f"- layout GDS count: `{len(value.get('copied_gds') or [])}`")
            lines.append(f"- layout PNG count: `{len(value.get('copied_png') or [])}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
