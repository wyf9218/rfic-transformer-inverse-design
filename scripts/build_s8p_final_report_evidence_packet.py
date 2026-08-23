#!/usr/bin/env python3
"""Build a report-evidence packet for the next-generation S8P validation flow.

This is a read-only packaging gate. It does not run EMX, HFSS, ADS, Cadence, or
plotting scripts. It verifies that the artifacts needed for a professor-facing
report already exist and are traceable:

* physical-feature distribution figures;
* selected EMX/layout structure evidence;
* Lp/Ls/Q/K -> geometry inverse-model training/prediction evidence;
* HFSS payload/model render figures;
* EMX, HFSS, and overlay Lp/Ls/Q/K/Kw curves;
* target-frequency metrics and configured percent-error comparison summaries.
* objective-level PASS/WAITING audit artifacts when they have been generated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_METRICS = ("lp_nh", "ls_nh", "q", "k", "kw")
REQUIRED_COVERAGE_FIGURES = ("marginal_histograms", "pairwise_scatter", "bin_coverage_heatmap")
REQUIRED_PLOT_KEYS = ("emx_common_plot", "hfss_common_plot", "overlay_common_plot", "metric_csv")


@dataclass(frozen=True)
class Artifact:
    category: str
    key: str
    path: str
    status: str
    bytes: int | None
    sha256: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "key": self.key,
            "path": self.path,
            "status": self.status,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    quality_dir = Path(args.quality_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    coverage_summary_path = _path_or_default(
        args.coverage_summary,
        quality_dir / "physical_feature_balanced_acquisition_plan" / "physical_feature_acquisition_plan_summary.json",
    )
    layout_summary_path = _path_or_default(
        args.layout_audit_summary,
        quality_dir / "selected_power_line_8port_layout_audit" / "selected_power_line_8port_layout_audit_summary.json",
    )
    hfss_render_summary_path = _path_or_default(
        args.hfss_render_summary,
        quality_dir / "selected_s8p_hfss_payload_views" / "hfss_payload_geometry_render_batch_summary.json",
    )
    aedt_summary_path = _path_or_default(
        args.aedt_summary,
        quality_dir / "selected_s8p_hfss_aedt_scripts" / "hfss_s8p_aedt_script_packet_summary.json",
    )
    inverse_training_manifest_path = _path_or_default(
        args.inverse_training_manifest,
        quality_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
    )
    inverse_model_quality_summary_path = _path_or_default(
        args.inverse_model_quality_summary,
        quality_dir / "physical_feature_inverse_model_quality" / "physical_feature_inverse_model_quality_summary.json",
    )
    saved_inverse_model_summary_path = _path_or_default(
        args.saved_inverse_model_summary,
        quality_dir / "physical_feature_saved_inverse_model" / "physical_feature_inverse_model_training_summary.json",
    )
    saved_inverse_target_layout_smoke_summary_path = _path_or_default(
        args.saved_inverse_target_layout_smoke_summary,
        quality_dir / "physical_feature_saved_inverse_target_layout_smoke" / "candidate_queue_dataset_summary.json",
    )
    postrun_summary_path = _path_or_default(
        args.postrun_summary,
        quality_dir / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json",
    )
    objective_acceptance_summary_path = _path_or_default(
        args.objective_acceptance_summary,
        quality_dir / "next_gen_s8p_objective_acceptance" / "next_gen_s8p_objective_acceptance_summary.json",
    )
    scalar_q_summary_path = _path_or_default(
        args.scalar_q_summary,
        quality_dir / "scalar_q_feature_dataset" / "scalar_q_feature_summary.json",
    )

    artifacts: list[Artifact] = []
    checks: list[Check] = []
    scalar_q_summary = _read_json(scalar_q_summary_path)
    coverage_summary = _read_json(coverage_summary_path)
    layout_summary = _read_json(layout_summary_path)
    hfss_render_summary = _read_json(hfss_render_summary_path)
    aedt_summary = _read_json(aedt_summary_path)
    inverse_training_manifest = _read_json(inverse_training_manifest_path)
    inverse_model_quality_summary = _read_json(inverse_model_quality_summary_path)
    saved_inverse_model_summary = _read_json(saved_inverse_model_summary_path)
    saved_inverse_target_layout_smoke_summary = _read_json(saved_inverse_target_layout_smoke_summary_path)
    postrun_summary = _read_json(postrun_summary_path)
    objective_acceptance_summary = _read_json(objective_acceptance_summary_path)

    checks.extend(_summary_checks("scalar-Q feature derivation", scalar_q_summary_path, scalar_q_summary))
    checks.extend(_summary_checks("physical-feature coverage plan", coverage_summary_path, coverage_summary))
    checks.extend(_summary_checks("selected EMX/layout audit", layout_summary_path, layout_summary))
    checks.extend(_summary_checks("physical-feature inverse training table", inverse_training_manifest_path, inverse_training_manifest))
    checks.extend(_summary_checks("physical-feature inverse model quality", inverse_model_quality_summary_path, inverse_model_quality_summary))
    checks.extend(_summary_checks("saved physical-feature inverse model", saved_inverse_model_summary_path, saved_inverse_model_summary))
    if _requires_target_layout_smoke(saved_inverse_model_summary) or saved_inverse_target_layout_smoke_summary_path.is_file():
        checks.extend(
            _summary_checks(
                "saved inverse target layout smoke",
                saved_inverse_target_layout_smoke_summary_path,
                saved_inverse_target_layout_smoke_summary,
            )
        )
    checks.extend(_summary_checks("HFSS AEDT build/solve packet", aedt_summary_path, aedt_summary))
    checks.extend(_summary_checks("HFSS payload render", hfss_render_summary_path, hfss_render_summary))
    checks.extend(_summary_checks("S8P EMX/HFSS postrun validation", postrun_summary_path, postrun_summary))

    artifacts.extend(_scalar_q_artifacts(scalar_q_summary_path, scalar_q_summary))
    artifacts.extend(_coverage_artifacts(coverage_summary_path, coverage_summary))
    artifacts.extend(_layout_artifacts(layout_summary_path, layout_summary))
    artifacts.extend(
        _inverse_model_artifacts(
            inverse_training_manifest_path,
            inverse_training_manifest,
            inverse_model_quality_summary_path,
            inverse_model_quality_summary,
            saved_inverse_model_summary_path,
            saved_inverse_model_summary,
            saved_inverse_target_layout_smoke_summary_path,
            saved_inverse_target_layout_smoke_summary,
        )
    )
    artifacts.extend(_aedt_script_artifacts(aedt_summary_path, aedt_summary))
    artifacts.extend(_hfss_render_artifacts(hfss_render_summary_path, hfss_render_summary))
    artifacts.extend(_postrun_artifacts(postrun_summary_path, postrun_summary))
    artifacts.extend(_objective_acceptance_artifacts(objective_acceptance_summary_path, objective_acceptance_summary))
    checks.extend(_artifact_presence_checks(artifacts))
    checks.extend(
        _inverse_model_contract_checks(
            inverse_training_manifest,
            inverse_model_quality_summary,
            saved_inverse_model_summary,
        )
    )
    checks.extend(_scalar_q_feature_checks(scalar_q_summary))
    checks.extend(_postrun_metric_checks(postrun_summary, args))
    checks.extend(_postrun_port_manifest_checks(postrun_summary))

    status_counts = _status_counts(checks)
    artifact_status_counts = _artifact_status_counts(artifacts)
    overall_status = _overall_status(checks, artifacts)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": _decision(overall_status),
        "quality_dir": str(quality_dir),
        "out_dir": str(out_dir),
        "inputs": {
            "coverage_summary": str(coverage_summary_path),
            "scalar_q_summary": str(scalar_q_summary_path),
            "layout_audit_summary": str(layout_summary_path),
            "inverse_training_manifest": str(inverse_training_manifest_path),
            "inverse_model_quality_summary": str(inverse_model_quality_summary_path),
            "saved_inverse_model_summary": str(saved_inverse_model_summary_path),
            "saved_inverse_target_layout_smoke_summary": str(saved_inverse_target_layout_smoke_summary_path),
            "aedt_summary": str(aedt_summary_path),
            "hfss_render_summary": str(hfss_render_summary_path),
            "postrun_summary": str(postrun_summary_path),
            "objective_acceptance_summary": str(objective_acceptance_summary_path),
        },
        "requirements": {
            "metrics": list(CORE_METRICS),
            "max_percent_error": float(args.max_percent_error),
            "target_ghz": float(args.target_ghz),
            "required_coverage_figures": list(REQUIRED_COVERAGE_FIGURES),
            "required_plot_keys": list(REQUIRED_PLOT_KEYS),
        },
        "status_counts": status_counts,
        "artifact_status_counts": artifact_status_counts,
        "artifact_count": len(artifacts),
        "checks": [check.as_dict() for check in checks],
        "artifacts": [artifact.as_dict() for artifact in artifacts],
        "limitations": [
            "This packet verifies report evidence only; it does not run simulators or create substitute figures.",
            "PASS requires existing artifacts and PASS summaries from the upstream S8P workflow.",
            "WAITING means a required upstream artifact is not present yet and must not be presented as completed work.",
        ],
    }

    summary_path = out_dir / "s8p_final_report_evidence_packet_summary.json"
    report_path = out_dir / "S8P_FINAL_REPORT_EVIDENCE_PACKET_CN.md"
    manifest_csv = out_dir / "s8p_final_report_artifact_manifest.csv"
    checks_csv = out_dir / "s8p_final_report_evidence_checks.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_artifact_csv(manifest_csv, artifacts)
    _write_check_csv(checks_csv, checks)

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"artifact_manifest={manifest_csv}")
    print(f"checks_csv={checks_csv}")
    return 2 if overall_status in {"FAIL", "WAITING"} and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-dir", required=True)
    parser.add_argument("--coverage-summary")
    parser.add_argument("--scalar-q-summary")
    parser.add_argument("--layout-audit-summary")
    parser.add_argument("--inverse-training-manifest")
    parser.add_argument("--inverse-model-quality-summary")
    parser.add_argument("--saved-inverse-model-summary")
    parser.add_argument("--saved-inverse-target-layout-smoke-summary")
    parser.add_argument("--aedt-summary")
    parser.add_argument("--hfss-render-summary")
    parser.add_argument("--postrun-summary")
    parser.add_argument("--objective-acceptance-summary")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _path_or_default(raw: str | None, default: Path) -> Path:
    return Path(raw).expanduser().resolve() if raw else default.expanduser().resolve()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}
    return payload if isinstance(payload, dict) else {}


def _summary_checks(name: str, path: Path, summary: dict[str, Any]) -> list[Check]:
    if not path.is_file():
        return [Check("WAITING", f"{name} summary exists", f"missing: {path}")]
    if summary.get("_json_error"):
        return [Check("FAIL", f"{name} summary parses", f"invalid JSON: {path}")]
    status = str(summary.get("overall_status") or "")
    if not status:
        return [Check("FAIL", f"{name} summary has status", f"missing overall_status in {path}")]
    return [
        Check("PASS", f"{name} summary exists", str(path)),
        Check("PASS" if status == "PASS" else ("WAITING" if status.startswith("WAITING") or status == "NOT_READY" else "FAIL"), f"{name} summary passes", f"overall_status={status}"),
    ]


def _coverage_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    visual = summary.get("visual_evidence") or {}
    figures = visual.get("figures") or {}
    artifacts = []
    for key in REQUIRED_COVERAGE_FIGURES:
        artifacts.append(_artifact("physical_feature_distribution", key, _resolve(summary_path.parent, figures.get(key)), expect_png=True))
    return artifacts


def _scalar_q_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    return [
        _artifact("physical_feature_scalar_q_dataset", "scalar_q_summary", summary_path, expect_png=False),
        _artifact(
            "physical_feature_scalar_q_dataset",
            "scalar_q_report",
            _resolve(summary_path.parent, summary.get("report")) or summary_path.parent / "scalar_q_feature_report.md",
            expect_png=False,
        ),
        _artifact(
            "physical_feature_scalar_q_dataset",
            "derived_dataset_rows",
            _resolve(summary_path.parent, summary.get("output_rows_csv")) or summary_path.parent / "dataset_rows.csv",
            expect_png=False,
        ),
        _artifact(
            "physical_feature_scalar_q_dataset",
            "derived_dataset_manifest",
            _resolve(summary_path.parent, summary.get("output_manifest")) or summary_path.parent / "dataset_manifest.json",
            expect_png=False,
        ),
    ]


def _layout_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for index, result in enumerate(summary.get("sample_results") or [], start=1):
        label = str(result.get("evaluation") or result.get("sample_id") or f"sample_{index:02d}")
        layout_json = _resolve(summary_path.parent, result.get("layout_json_path"))
        power_json = _resolve(summary_path.parent, result.get("power_line_8port_geometry_json_path"))
        artifacts.append(_artifact("emx_layout_structure", f"{label}:layout_json", layout_json, expect_png=False))
        artifacts.append(_artifact("emx_layout_structure", f"{label}:power_line_geometry_json", power_json, expect_png=False))
        if layout_json is not None:
            for png in _discover_layout_pngs(layout_json.parent):
                artifacts.append(_artifact("emx_layout_structure", f"{label}:{png.name}", png, expect_png=True))
    if not artifacts:
        artifacts.append(_artifact("emx_layout_structure", "selected_layout_evidence", None, expect_png=False))
    return artifacts


def _inverse_model_artifacts(
    training_manifest_path: Path,
    training_manifest: dict[str, Any],
    quality_summary_path: Path,
    quality_summary: dict[str, Any],
    saved_summary_path: Path,
    saved_summary: dict[str, Any],
    target_layout_smoke_summary_path: Path,
    target_layout_smoke_summary: dict[str, Any],
) -> list[Artifact]:
    artifacts: list[Artifact] = []
    artifacts.append(_artifact("physical_feature_inverse_training_data", "training_manifest", training_manifest_path, expect_png=False))
    artifacts.append(
        _artifact(
            "physical_feature_inverse_training_data",
            "training_table_csv",
            _resolve(training_manifest_path.parent, training_manifest.get("training_csv")),
            expect_png=False,
        )
    )
    artifacts.append(
        _artifact(
            "physical_feature_inverse_training_data",
            "training_report",
            training_manifest_path.parent / "physical_feature_inverse_training_report.md",
            expect_png=False,
            required=False,
        )
    )

    artifacts.append(_artifact("physical_feature_inverse_model_quality", "quality_summary", quality_summary_path, expect_png=False))
    artifacts.append(
        _artifact(
            "physical_feature_inverse_model_quality",
            "quality_report",
            _resolve(quality_summary_path.parent, quality_summary.get("report")) or quality_summary_path.parent / "physical_feature_inverse_model_quality_report.md",
            expect_png=False,
        )
    )
    for key in ("cv_predictions_csv", "geometry_errors_csv"):
        artifacts.append(
            _artifact(
                "physical_feature_inverse_model_quality",
                key,
                _resolve(quality_summary_path.parent, quality_summary.get(key)),
                expect_png=False,
            )
        )

    artifacts.append(_artifact("physical_feature_inverse_saved_model", "training_summary", saved_summary_path, expect_png=False))
    artifacts.append(
        _artifact(
            "physical_feature_inverse_saved_model",
            "model_json",
            _resolve(saved_summary_path.parent, saved_summary.get("model_json")),
            expect_png=False,
        )
    )
    artifacts.append(
        _artifact(
            "physical_feature_inverse_saved_model",
            "training_report",
            _resolve(saved_summary_path.parent, saved_summary.get("report")),
            expect_png=False,
        )
    )
    for key in ("cv_predictions_csv", "geometry_errors_csv"):
        artifacts.append(
            _artifact(
                "physical_feature_inverse_saved_model",
                key,
                _resolve(saved_summary_path.parent, saved_summary.get(key)),
                expect_png=False,
            )
        )
    target_predictions_required = _requires_target_layout_smoke(saved_summary)
    target_predictions_csv = _resolve(saved_summary_path.parent, saved_summary.get("target_predictions_csv"))
    if target_predictions_required or (target_predictions_csv is not None and target_predictions_csv.is_file() and target_predictions_csv.stat().st_size > 0):
        artifacts.append(
            _artifact(
                "physical_feature_inverse_saved_model",
                "target_predictions_csv",
                target_predictions_csv,
                expect_png=False,
                required=target_predictions_required,
            )
        )
    else:
        artifacts.append(
            _artifact(
                "physical_feature_inverse_saved_model",
                "target_predictions_csv",
                None,
                expect_png=False,
                required=False,
            )
        )

    artifacts.extend(
        _target_layout_smoke_artifacts(
            target_layout_smoke_summary_path,
            target_layout_smoke_summary,
            required=target_predictions_required,
        )
    )
    return artifacts


def _target_layout_smoke_artifacts(summary_path: Path, summary: dict[str, Any], *, required: bool) -> list[Artifact]:
    artifacts: list[Artifact] = [
        _artifact("physical_feature_inverse_target_structure", "layout_smoke_summary", summary_path, expect_png=False, required=required)
    ]
    if not summary:
        return artifacts
    dataset_rows = _resolve(summary_path.parent, summary.get("dataset_rows_csv")) or summary_path.parent / "dataset_rows.csv"
    dataset_manifest = _resolve(summary_path.parent, summary.get("dataset_manifest")) or summary_path.parent / "dataset_manifest.json"
    artifacts.append(
        _artifact(
            "physical_feature_inverse_target_structure",
            "layout_smoke_dataset_rows",
            dataset_rows,
            expect_png=False,
            required=required,
        )
    )
    artifacts.append(
        _artifact(
            "physical_feature_inverse_target_structure",
            "layout_smoke_dataset_manifest",
            dataset_manifest,
            expect_png=False,
            required=required,
        )
    )
    for row_index, row in enumerate(_read_csv_rows(dataset_rows)[:3], start=1):
        work_dir = _resolve(dataset_rows.parent, row.get("work_dir"))
        layout_dir = None if work_dir is None else work_dir / "layout"
        if layout_dir is None:
            continue
        label = str(row.get("evaluation") or f"target_{row_index:02d}")
        artifacts.append(
            _artifact(
                "physical_feature_inverse_target_structure",
                f"{label}:layout_json",
                layout_dir / "transformer_layout.layout.json",
                expect_png=False,
                required=False,
            )
        )
        artifacts.append(
            _artifact(
                "physical_feature_inverse_target_structure",
                f"{label}:power_line_geometry_json",
                layout_dir / "power_line_8port_geometry.json",
                expect_png=False,
                required=False,
            )
        )
        for png in _discover_layout_pngs(layout_dir):
            artifacts.append(
                _artifact(
                    "physical_feature_inverse_target_structure",
                    f"{label}:{png.name}",
                    png,
                    expect_png=True,
                    required=False,
                )
            )
    return artifacts


def _hfss_render_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    summary_paths = summary.get("summary_paths") or []
    if not summary_paths and summary.get("image_paths"):
        summary_paths = [str(summary_path)]
    for index, raw in enumerate(summary_paths, start=1):
        render_summary_path = _resolve(summary_path.parent, raw)
        artifacts.append(_artifact("hfss_model_structure", f"render_summary_{index:02d}", render_summary_path, expect_png=False))
        render_summary = _read_json(render_summary_path) if render_summary_path else {}
        for image_index, image_raw in enumerate(render_summary.get("image_paths") or [], start=1):
            artifacts.append(
                _artifact(
                    "hfss_model_structure",
                    f"{render_summary.get('sample_id') or index}:image_{image_index:02d}",
                    _resolve(render_summary_path.parent if render_summary_path else summary_path.parent, image_raw),
                    expect_png=True,
                )
            )
    if not artifacts:
        artifacts.append(_artifact("hfss_model_structure", "hfss_payload_images", None, expect_png=True))
    return artifacts


def _aedt_script_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    commands_path = summary_path.parent / "run_generated_hfss_s8p_scripts.commands.ps1"
    artifacts.append(_artifact("hfss_aedt_rebuild_scripts", "run_generated_hfss_s8p_scripts.commands.ps1", commands_path, expect_png=False))
    for index, sample in enumerate(summary.get("sample_results") or [], start=1):
        label = str(sample.get("evaluation") or f"sample_{index:02d}")
        script_dir = _resolve(summary_path.parent, sample.get("script_dir"))
        artifacts.append(
            _artifact(
                "hfss_aedt_rebuild_scripts",
                f"{label}:hfss_s8p_build_payload.json",
                _resolve(summary_path.parent, sample.get("payload_json")),
                expect_png=False,
            )
        )
        artifacts.append(
            _artifact(
                "hfss_aedt_rebuild_scripts",
                f"{label}:build_hfss_s8p_from_payload.py",
                _resolve(summary_path.parent, sample.get("build_script")),
                expect_png=False,
            )
        )
        artifacts.append(
            _artifact(
                "hfss_aedt_rebuild_scripts",
                f"{label}:solve_export_hfss_s8p.py",
                _resolve(summary_path.parent, sample.get("solve_script")),
                expect_png=False,
            )
        )
        artifacts.append(
            _artifact(
                "hfss_aedt_rebuild_scripts",
                f"{label}:source_geometry.gds",
                None if script_dir is None else script_dir / "source_geometry.gds",
                expect_png=False,
                required=False,
            )
        )
        artifacts.append(
            _artifact(
                "hfss_aedt_rebuild_scripts",
                f"{label}:script_packet_readme",
                _resolve(summary_path.parent, sample.get("sample_report")),
                expect_png=False,
                required=False,
            )
        )
    if len(artifacts) == 1 and not summary:
        artifacts.append(_artifact("hfss_aedt_rebuild_scripts", "hfss_aedt_packet", None, expect_png=False))
    return artifacts


def _postrun_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for index, record in enumerate(summary.get("records") or [], start=1):
        label = str(record.get("evaluation") or f"sample_{index:02d}")
        compare_summary_path = _resolve(summary_path.parent, record.get("compare_summary"))
        target_marker_csv = _resolve(summary_path.parent, record.get("target_marker_csv"))
        plot_summary_path = _resolve(summary_path.parent, record.get("ads_style_plot_summary"))
        emx_s8p = _resolve(summary_path.parent, record.get("emx_s8p"))
        hfss_s8p = _resolve(summary_path.parent, record.get("hfss_s8p"))
        hfss_port_manifest = _resolve(summary_path.parent, record.get("hfss_port_manifest"))
        emx_audit_summary = _resolve(summary_path.parent, record.get("emx_audit_summary"))
        hfss_audit_summary = _resolve(summary_path.parent, record.get("hfss_audit_summary"))
        artifacts.append(_artifact("emx_hfss_touchstone_sources", f"{label}:emx_s8p", emx_s8p, expect_png=False))
        artifacts.append(_artifact("emx_hfss_touchstone_sources", f"{label}:hfss_s8p", hfss_s8p, expect_png=False))
        artifacts.append(_artifact("hfss_rebuild_port_trace", f"{label}:hfss_s8p_build_port_manifest", hfss_port_manifest, expect_png=False))
        artifacts.append(_artifact("emx_hfss_touchstone_sources", f"{label}:emx_touchstone_audit", emx_audit_summary, expect_png=False, required=False))
        artifacts.append(_artifact("emx_hfss_touchstone_sources", f"{label}:hfss_touchstone_audit", hfss_audit_summary, expect_png=False, required=False))
        artifacts.append(_artifact("emx_hfss_physical_curves", f"{label}:compare_summary", compare_summary_path, expect_png=False))
        artifacts.append(_artifact("emx_hfss_physical_curves", f"{label}:target_marker_csv", target_marker_csv, expect_png=False))
        artifacts.append(_artifact("emx_hfss_physical_curves", f"{label}:plot_summary", plot_summary_path, expect_png=False))
        if compare_summary_path is not None:
            compare_dir = compare_summary_path.parent
            for filename in (
                "emx_hfss_ads_curves.csv",
                "emx_hfss_ads_metric_errors.csv",
                "emx_hfss_ads_target_marker_metrics.csv",
            ):
                artifacts.append(_artifact("emx_hfss_physical_curves", f"{label}:{filename}", compare_dir / filename, expect_png=False))
            for metric in CORE_METRICS:
                artifacts.append(_artifact("emx_hfss_physical_curves", f"{label}:{metric}_comparison.png", compare_dir / f"{metric}_comparison.png", expect_png=True, required=False))
        plot_summary = _read_json(plot_summary_path) if plot_summary_path else {}
        plot_paths = {}
        plot_paths.update(plot_summary.get("artifact_paths") or {})
        plot_paths.update({f"window_{key}": value for key, value in (plot_summary.get("window_named_artifact_paths") or {}).items()})
        for key in REQUIRED_PLOT_KEYS:
            artifacts.append(_artifact("emx_hfss_ads_style_report_figures", f"{label}:{key}", _resolve(plot_summary_path.parent if plot_summary_path else summary_path.parent, plot_paths.get(key)), expect_png=key.endswith("_plot")))
        for key, raw in plot_paths.items():
            if key in REQUIRED_PLOT_KEYS:
                continue
            artifacts.append(_artifact("emx_hfss_ads_style_report_figures", f"{label}:{key}", _resolve(plot_summary_path.parent if plot_summary_path else summary_path.parent, raw), expect_png=str(raw).lower().endswith(".png"), required=False))
    if not artifacts:
        artifacts.append(_artifact("emx_hfss_physical_curves", "postrun_records", None, expect_png=False))
    return artifacts


def _objective_acceptance_artifacts(summary_path: Path, summary: dict[str, Any]) -> list[Artifact]:
    # This audit is generated after the first report-evidence pass, so it is
    # collected as contextual evidence when available and never creates a
    # circular prerequisite for the report-evidence packet itself.
    return [
        _artifact(
            "objective_acceptance_audit",
            "objective_acceptance_summary",
            summary_path,
            expect_png=False,
            required=False,
        ),
        _artifact(
            "objective_acceptance_audit",
            "objective_acceptance_report",
            summary_path.parent / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md",
            expect_png=False,
            required=False,
        ),
        _artifact(
            "objective_acceptance_audit",
            "objective_acceptance_evidence_csv",
            summary_path.parent / "next_gen_s8p_objective_acceptance_evidence.csv",
            expect_png=False,
            required=False,
        ),
    ]


def _discover_layout_pngs(layout_dir: Path) -> list[Path]:
    names = [
        "transformer_layout_preview.png",
        "transformer_port_debug.png",
        "transformer_layout_preview_labeled_roles.png",
        "transformer_layout_preview_inside_shield_labeled_roles.png",
        "transformer_layout_preview_topology_locked_labeled_roles.png",
    ]
    found = [layout_dir / name for name in names if (layout_dir / name).is_file()]
    found.extend(sorted(layout_dir.glob("transformer_layout_annotated*.png")))
    return sorted({path.resolve() for path in found})


def _resolve(base: Path, raw: Any) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _artifact(category: str, key: str, path: Path | None, *, expect_png: bool, required: bool = True) -> Artifact:
    if path is None:
        status = "WAITING" if required else "OPTIONAL_MISSING"
        return Artifact(category, key, "", status, None, "", "missing path")
    if not path.is_file():
        status = "WAITING" if required else "OPTIONAL_MISSING"
        return Artifact(category, key, str(path), status, None, "", "file not found")
    data = path.read_bytes()
    detail = "exists"
    status = "PASS"
    if not data:
        status = "FAIL"
        detail = "empty file"
    elif expect_png and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        status = "FAIL"
        detail = "not a PNG file"
    return Artifact(category, key, str(path), status, len(data), hashlib.sha256(data).hexdigest(), detail)


def _artifact_presence_checks(artifacts: list[Artifact]) -> list[Check]:
    required = [item for item in artifacts if item.status != "OPTIONAL_MISSING"]
    failures = [item for item in required if item.status == "FAIL"]
    waiting = [item for item in required if item.status == "WAITING"]
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "report artifact files are valid",
            f"failures={len(failures)}",
        ),
        Check(
            "PASS" if not waiting else "WAITING",
            "required report artifact files exist",
            f"waiting={len(waiting)}",
        ),
    ]


def _postrun_metric_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    if not summary:
        return [Check("WAITING", "postrun metric records exist", "missing postrun summary")]
    records = summary.get("records") or []
    if not records:
        return [Check("WAITING", "postrun metric records exist", "records=0")]
    checks = []
    for record in records:
        label = str(record.get("evaluation") or record.get("selection_rank") or "sample")
        compare_summary_path = _resolve(Path("."), record.get("compare_summary"))
        compare_summary = _read_json(compare_summary_path) if compare_summary_path else {}
        metric_failures = []
        for metric in CORE_METRICS:
            item = (compare_summary.get("metrics") or {}).get(metric) or {}
            error = item.get("max_percent_error")
            if item.get("status") != "PASS" or not isinstance(error, (int, float)) or float(error) > float(args.max_percent_error):
                metric_failures.append(f"{metric}={error}")
        marker = compare_summary.get("target_marker") or {}
        marker_failures = []
        for metric in CORE_METRICS:
            item = (marker.get("metrics") or {}).get(metric) or {}
            error = item.get("percent_error")
            if item.get("status") != "PASS" or not isinstance(error, (int, float)) or float(error) > float(args.max_percent_error):
                marker_failures.append(f"{metric}={error}")
        checks.append(
            Check(
                "PASS" if compare_summary.get("overall_status") == "PASS" and not metric_failures else "FAIL",
                f"{label} EMX/HFSS <= {args.max_percent_error:g}% full-window metrics",
                "PASS" if not metric_failures else "; ".join(metric_failures),
            )
        )
        checks.append(
            Check(
                "PASS" if marker.get("status") == "PASS" and not marker_failures else "FAIL",
                f"{label} {args.target_ghz:g}GHz marker metrics",
                "PASS" if not marker_failures else "; ".join(marker_failures),
            )
        )
    return checks


def _postrun_port_manifest_checks(summary: dict[str, Any]) -> list[Check]:
    if not summary:
        return [Check("WAITING", "postrun HFSS port manifest checks passed", "missing postrun summary")]
    required_names = (
        "HFSS build port manifest exists",
        "HFSS build port manifest schema",
        "HFSS build port manifest has 8 ports",
        "HFSS build port manifest port order is P001-P008",
        "HFSS build port manifest ground names are P001_G-P008_G",
        "HFSS build port manifest records integration lines",
    )
    statuses = {
        str(item.get("name", "")): str(item.get("status", ""))
        for item in summary.get("checks") or []
        if item.get("name")
    }
    missing_or_failed = [name for name in required_names if statuses.get(name) != "PASS"]
    return [
        Check(
            "PASS" if not missing_or_failed else "FAIL",
            "postrun HFSS port manifest checks passed",
            "PASS" if not missing_or_failed else f"missing_or_failed={missing_or_failed}",
        )
    ]


def _inverse_model_contract_checks(
    training_manifest: dict[str, Any],
    quality_summary: dict[str, Any],
    saved_summary: dict[str, Any],
) -> list[Check]:
    return [
        _input_contract_check("inverse training table", training_manifest),
        _input_contract_check("inverse model quality audit", quality_summary),
        _input_contract_check("saved inverse model", saved_summary),
        _saved_model_artifact_check(saved_summary),
    ]


def _scalar_q_feature_checks(summary: dict[str, Any]) -> list[Check]:
    if not summary:
        return [Check("WAITING", "scalar-Q derived dataset uses q_center", "missing scalar-Q summary")]
    args = summary.get("arguments") if isinstance(summary.get("arguments"), dict) else {}
    definition = summary.get("definition") or args.get("q_definition")
    output_column = summary.get("output_column") or args.get("output_column")
    valid_count = _to_int(summary.get("valid_q_count"))
    fail_count = _to_int(summary.get("fail_count"))
    return [
        Check(
            "PASS" if definition else "FAIL",
            "scalar-Q definition is recorded",
            f"definition={definition}",
        ),
        Check(
            "PASS" if output_column == "q_center" else "FAIL",
            "scalar-Q output column is q_center",
            f"output_column={output_column}",
        ),
        Check(
            "PASS" if valid_count is not None and valid_count > 0 and fail_count == 0 else "FAIL",
            "scalar-Q derivation has valid rows and no failures",
            f"valid_q_count={valid_count}, fail_count={fail_count}",
        ),
    ]


def _input_contract_check(label: str, summary: dict[str, Any]) -> Check:
    if not summary:
        return Check("WAITING", f"{label} input contract is physical features", "missing summary")
    contract = summary.get("input_feature_contract") or {}
    if not isinstance(contract, dict) or not contract:
        return Check("FAIL", f"{label} input contract is physical features", "missing input_feature_contract")
    zin_columns = list(contract.get("zin_columns") or [])
    required = {
        "lp": bool(contract.get("lp_columns")),
        "ls": bool(contract.get("ls_columns")),
        "q": bool(contract.get("q_columns")),
        "k": bool(contract.get("k_columns")),
    }
    passed = not zin_columns and all(required.values())
    return Check(
        "PASS" if passed else "FAIL",
        f"{label} input contract is physical features",
        f"zin_columns={zin_columns}, required={required}",
    )


def _saved_model_artifact_check(summary: dict[str, Any]) -> Check:
    if not summary:
        return Check("WAITING", "saved inverse model has reusable model JSON", "missing summary")
    model_json = _resolve(Path("."), summary.get("model_json"))
    return Check(
        "PASS" if summary.get("overall_status") == "PASS" and model_json is not None and model_json.is_file() else "FAIL",
        "saved inverse model has reusable model JSON",
        f"overall_status={summary.get('overall_status')}, model_json={model_json}, exists={model_json.is_file() if model_json else False}",
    )


def _requires_target_layout_smoke(saved_summary: dict[str, Any]) -> bool:
    return _to_int(saved_summary.get("target_prediction_count")) is not None and _to_int(saved_summary.get("target_prediction_count")) > 0


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in checks:
        counts[item.status] = counts.get(item.status, 0) + 1
    return dict(sorted(counts.items()))


def _artifact_status_counts(artifacts: list[Artifact]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in artifacts:
        counts[item.status] = counts.get(item.status, 0) + 1
    return dict(sorted(counts.items()))


def _overall_status(checks: list[Check], artifacts: list[Artifact]) -> str:
    statuses = {item.status for item in checks} | {item.status for item in artifacts}
    if "FAIL" in statuses:
        return "FAIL"
    if "WAITING" in statuses:
        return "WAITING"
    return "PASS"


def _decision(status: str) -> str:
    if status == "PASS":
        return "READY_TO_USE_S8P_FINAL_REPORT_EVIDENCE"
    if status == "WAITING":
        return "WAIT_FOR_MISSING_S8P_REPORT_EVIDENCE"
    return "DO_NOT_USE_S8P_FINAL_REPORT_EVIDENCE"


def _write_artifact_csv(path: Path, artifacts: list[Artifact]) -> None:
    fields = ["category", "key", "status", "path", "bytes", "sha256", "detail"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in artifacts:
            writer.writerow({field: item.as_dict().get(field) for field in fields})


def _write_check_csv(path: Path, checks: list[Check]) -> None:
    fields = ["status", "name", "detail"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in checks:
            writer.writerow(item.as_dict())


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Final Report Evidence Packet",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Artifact count: `{summary['artifact_count']}`",
        f"- Check status counts: `{summary['status_counts']}`",
        f"- Artifact status counts: `{summary['artifact_status_counts']}`",
        "",
        "## Required Evidence",
        "",
        "- Physical-feature distribution figures: marginal histograms, pairwise scatter, bin coverage heatmap.",
        "- Scalar-Q derivation evidence: q_center dataset rows, manifest, report, and recorded Q definition.",
        "- EMX/layout structure evidence: selected layout JSON, power-line geometry JSON, and available layout preview PNGs.",
        "- Physical-feature inverse model evidence: inverse training table, no-Zin Lp/Ls/Q/K input contract, model-quality CSVs, saved model JSON, and target-geometry layout smoke when targets were predicted.",
        "- HFSS rebuild evidence: AEDT payload JSON, build/solve scripts, source GDS, build-time port manifest, and rendered payload/model images from the same HFSS build payload.",
        "- Touchstone source evidence: EMX `.s8p` and exported HFSS `.s8p` files used for the comparison.",
        "- EMX/HFSS physical curves: EMX-only, HFSS-only, overlay plots, metric CSV, target marker table, and compare summaries.",
        "- Objective-level acceptance audit artifacts when available; final completion is still decided by that audit, not by this packaging step alone.",
        "",
        "## Checks",
        "",
    ]
    for check in summary.get("checks", []):
        lines.append(f"- `{check['status']}` {check['name']}: {check['detail']}")
    lines.extend(["", "## Artifacts", ""])
    for item in summary.get("artifacts", []):
        lines.append(f"- `{item['status']}` `{item['category']}` `{item['key']}`: `{item['path']}`")
    lines.extend(["", "## Limitations", ""])
    for note in summary.get("limitations", []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
