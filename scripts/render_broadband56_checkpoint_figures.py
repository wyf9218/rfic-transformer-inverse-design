#!/usr/bin/env python3
"""Render the frozen broadband56 checkpoint figure set from audited evidence.

The renderer is deliberately downstream-only.  It consumes PASS checkpoint
audits and the PASS campaign-history finalizer output; it does not run a
simulator, infer missing records, train a model, or alter campaign data.  All
inputs are validated before a no-clobber staging directory is created.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ADAPTIVE_ROUND_END_COUNTS,
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    SECONDARY_FEATURES,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    occupancy_metrics,
    phase_for_accepted_sequence,
    validate_contract,
)


FIGURE_CHECKPOINT_COUNTS = (50_000, 100_000, 150_000, 200_000)
REQUIRED_HISTORY_AUDIT_COUNTS = (
    100,
    1_000,
    5_000,
    20_000,
    50_000,
    *tuple(ADAPTIVE_ROUND_END_COUNTS),
)
REQUIRED_ACQUISITION_ENDPOINTS = (50_000, *tuple(ADAPTIVE_ROUND_END_COUNTS))
FIGURE_IDS = (
    "01_geometry_sampling_coverage",
    "02_physical_marginals_by_frequency",
    "03_lp_ls_joint_coverage",
    "04_qp_qs_joint_coverage",
    "05_qmin_k_joint_coverage",
    "06_primary_4d_cell_occupancy",
    "07_underfilled_cell_count_vs_accepted_samples",
    "08_uniformity_entropy_vs_accepted_samples",
    "09_acquisition_phase_contribution",
    "10_valid_feature_fraction_vs_frequency",
    "11_broad_vs_practical_panel_coverage",
    "12_failure_funnel",
    "13_boundary_coverage",
    "14_response_coverage_before_and_after_active_repair",
)

AUDIT_SOURCE_FILES = {
    "geometry_marginals": "geometry_coverage_marginals.csv",
    "geometry_pairwise": "geometry_coverage_pairwise.csv",
    "physical_by_frequency": "physical_coverage_by_frequency.csv",
    "physical_marginals": "physical_coverage_marginals.csv",
    "physical_pairwise": "physical_coverage_pairwise.csv",
    "primary_cells": "physical_coverage_cells_by_anchor.csv",
    "failure_funnel": "FAILURE_FUNNEL.csv",
}
AUDIT_OUTPUT_KEYS = {
    "geometry_marginals": "geometry_coverage_marginals",
    "geometry_pairwise": "geometry_coverage_pairwise",
    "physical_by_frequency": "coverage_by_frequency",
    "physical_marginals": "coverage_marginals",
    "physical_pairwise": "coverage_pairwise",
    "primary_cells": "coverage_cells",
    "failure_funnel": "failure_funnel",
}
HISTORY_SOURCE_FILES = {
    "coverage_deficit_history": "coverage_deficit_history.csv",
    "acquisition_round_history": "acquisition_round_history.csv",
    "acquisition_source_by_geometry": "acquisition_source_by_geometry.csv",
}

BLUE = "#1677d2"
ORANGE = "#e86a17"
TEAL = "#008f78"
PURPLE = "#6f4bd8"
INK = "#142033"
MUTED = "#5d6b7e"
GRID = "#dce2e8"
SOURCE_COLORS = {
    "base_space_filling": BLUE,
    "underfilled_response_repair": ORANGE,
    "rare_or_underfilled_response_repair": "#a94b00",
    "ensemble_uncertainty": PURPLE,
    "maximin_geometry_exploration": TEAL,
}


class FigureBuildError(RuntimeError):
    """Raised when source evidence cannot support the required figure set."""


@dataclass(frozen=True)
class SourceEvidence:
    label: str
    filename: str
    sha256: str
    size_bytes: int
    row_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class AuditBundle:
    accepted_count: int
    phase: str
    directory: Path
    receipt_sha256: str
    status_sha256: str
    sources: Mapping[str, SourceEvidence]


@dataclass(frozen=True)
class HistoryPoint:
    accepted_count: int
    actual_counts: np.ndarray
    observed_cells: int
    underfilled_cells: int
    unobserved_cells: int
    at_or_above_target_cells: int
    normalized_entropy: float


@dataclass(frozen=True)
class HistoryBundle:
    directory: Path
    receipt_sha256: str
    sources: Mapping[str, SourceEvidence]
    coverage_points: Mapping[int, HistoryPoint]
    acquisition_rows: tuple[dict[str, str], ...]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=output path already exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        audit_dirs = (
            _discover_audit_dirs(
                Path(args.campaign_root).expanduser().resolve(),
                required_counts=FIGURE_CHECKPOINT_COUNTS,
            )
            if args.campaign_root
            else [Path(value).expanduser().resolve() for value in args.audit_dir]
        )
        result = render_checkpoint_figures(
            contract_path=Path(args.contract).expanduser().resolve(),
            history_dir=Path(args.history_dir).expanduser().resolve(),
            audit_dirs=audit_dirs,
            out_dir=out_dir,
        )
    except FigureBuildError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"checkpoint_count={result['checkpoint_count']}")
    print(f"logical_figure_count={result['logical_figure_count']}")
    print(f"rendered_file_count={result['rendered_file_count']}")
    print(f"receipt={out_dir / 'FIGURE_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--history-dir", required=True)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument(
        "--audit-dir",
        action="append",
        help="Repeat for the exact 50k, 100k, 150k, and 200k PASS checkpoint audits.",
    )
    sources.add_argument(
        "--campaign-root",
        help="Discover exact hash-closed checkpoint audits below this campaign root.",
    )
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def render_checkpoint_figures(
    *,
    contract_path: Path,
    history_dir: Path,
    audit_dirs: Sequence[Path],
    out_dir: Path,
) -> dict[str, int]:
    """Validate sources and atomically render all required checkpoint figures."""

    if out_dir.exists():
        raise FigureBuildError(f"output path already exists (no-clobber): {out_dir}")
    contract, fingerprint, process_sha = _load_contract(contract_path)
    history = _load_history(history_dir, fingerprint=fingerprint)
    audits = _load_audits(audit_dirs, contract_path=contract_path, fingerprint=fingerprint)
    expected_counts = set(FIGURE_CHECKPOINT_COUNTS)
    if set(audits) != expected_counts:
        raise FigureBuildError(
            "checkpoint audit count mismatch: "
            f"missing={sorted(expected_counts - set(audits))}, "
            f"extra={sorted(set(audits) - expected_counts)}"
        )
    for count in FIGURE_CHECKPOINT_COUNTS:
        if count not in history.coverage_points:
            raise FigureBuildError(f"history does not contain checkpoint {count}")

    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise FigureBuildError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    checkpoint_manifest_records: list[dict[str, Any]] = []
    try:
        _configure_matplotlib()
        for accepted_count in FIGURE_CHECKPOINT_COUNTS:
            checkpoint_dir = staging / f"checkpoint_{accepted_count:06d}"
            checkpoint_dir.mkdir()
            manifest = _render_one_checkpoint(
                audit=audits[accepted_count],
                history=history,
                checkpoint_dir=checkpoint_dir,
                final_checkpoint_dir=out_dir / checkpoint_dir.name,
                fingerprint=fingerprint,
                process_sha=process_sha,
            )
            manifest_path = checkpoint_dir / "FIGURE_MANIFEST.json"
            _write_json(manifest_path, manifest)
            checkpoint_manifest_records.append(
                {
                    "accepted_geometries": accepted_count,
                    "filename": f"{checkpoint_dir.name}/FIGURE_MANIFEST.json",
                    "sha256": _sha256(manifest_path),
                    "size_bytes": manifest_path.stat().st_size,
                }
            )

        logical_count = len(FIGURE_CHECKPOINT_COUNTS) * len(FIGURE_IDS)
        rendered_count = logical_count * 2
        receipt = {
            "schema": "broadband56_checkpoint_figure_receipt_v1",
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_STATIC_CHECKPOINT_FIGURES",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "production_process_config_sha256": process_sha,
            "checkpoint_counts": list(FIGURE_CHECKPOINT_COUNTS),
            "logical_figure_ids": list(FIGURE_IDS),
            "counts": {
                "checkpoint_manifests": len(checkpoint_manifest_records),
                "logical_figures": logical_count,
                "png_files": logical_count,
                "svg_files": logical_count,
                "rendered_files": rendered_count,
            },
            "inputs": {
                "contract": _file_record(contract_path, label="frozen_campaign_contract"),
                "history_receipt_sha256": history.receipt_sha256,
                "checkpoint_receipts": [
                    {
                        "accepted_geometries": count,
                        "receipt_sha256": audits[count].receipt_sha256,
                        "status_sha256": audits[count].status_sha256,
                    }
                    for count in FIGURE_CHECKPOINT_COUNTS
                ],
            },
            "outputs": {"checkpoint_manifests": checkpoint_manifest_records},
            "checks": {
                "four_required_checkpoints_exact": len(checkpoint_manifest_records)
                == len(FIGURE_CHECKPOINT_COUNTS),
                "fourteen_logical_figures_per_checkpoint": logical_count
                == len(FIGURE_CHECKPOINT_COUNTS) * 14,
                "png_and_svg_written_for_every_figure": rendered_count == logical_count * 2,
                "every_figure_binds_csv_sha_denominator_frequency_validity_phase_process_contract": True,
                "source_receipts_and_sha_indexes_pass": True,
                "simulator_model_training_and_remote_execution_not_run": True,
            },
            "scientific_boundary": (
                "Figures summarize SHA-bound fresh-real-EMX checkpoint audits. Record-weighted "
                "frequency plots retain within-geometry correlation and never replace the "
                "geometry-unique anchor metric. The renderer performs no simulation or training."
            ),
            "contract_snapshot": {
                "frequency_points": len(FREQUENCY_GRID_HZ),
                "anchors_ghz": list(ANCHOR_FREQUENCIES_GHZ),
                "target_accepted_geometries": int(
                    (contract.get("terminal_goal") or {}).get("accepted_geometries") or 0
                ),
            },
        }
        if not all(value is True for value in receipt["checks"].values()):
            raise FigureBuildError(f"terminal figure checks failed: {receipt['checks']}")
        _write_json(staging / "FIGURE_RECEIPT.json", receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "checkpoint_count": len(FIGURE_CHECKPOINT_COUNTS),
        "logical_figure_count": logical_count,
        "rendered_file_count": rendered_count,
    }


def _load_contract(path: Path) -> tuple[dict[str, Any], str, str]:
    contract = _read_json(path)
    errors = validate_contract(contract)
    if errors:
        raise FigureBuildError(f"campaign contract is invalid: {errors}")
    fingerprint = str(contract.get("contract_fingerprint_sha256") or "")
    if not _is_sha256(fingerprint) or fingerprint != contract_fingerprint(contract):
        raise FigureBuildError("campaign contract fingerprint is absent or does not recompute")
    inherited = contract.get("inherited_contract_evidence") or {}
    process_sha = str(inherited.get("production_config_sha256") or "")
    if not _is_sha256(process_sha):
        raise FigureBuildError("frozen production process/config SHA-256 is absent")
    return contract, fingerprint, process_sha


def _load_audits(
    directories: Sequence[Path], *, contract_path: Path, fingerprint: str
) -> dict[int, AuditBundle]:
    by_count: dict[int, AuditBundle] = {}
    for directory in directories:
        bundle = _load_audit(directory, contract_path=contract_path, fingerprint=fingerprint)
        if bundle.accepted_count in by_count:
            raise FigureBuildError(f"duplicate checkpoint audit: {bundle.accepted_count}")
        by_count[bundle.accepted_count] = bundle
    return by_count


def _discover_audit_dirs(
    campaign_root: Path,
    *,
    required_counts: Sequence[int],
) -> list[Path]:
    root = Path(campaign_root).expanduser().resolve()
    stages_root = root / "stages"
    if not stages_root.is_dir():
        raise FigureBuildError(
            f"campaign stages directory does not exist: {stages_root}"
        )
    expected = set(int(value) for value in required_counts)
    by_count: dict[int, Path] = {}
    for status_path in sorted(stages_root.glob("**/CHECKPOINT_STATUS.json")):
        directory = status_path.parent.resolve()
        try:
            directory.relative_to(stages_root)
        except ValueError as exc:
            raise FigureBuildError(
                f"discovered checkpoint escapes campaign root: {directory}"
            ) from exc
        status = _read_json(status_path)
        try:
            count = _as_int(status.get("accepted_geometries"), "accepted_geometries")
        except FigureBuildError:
            continue
        if count not in expected or status.get("audit_mode") != "checkpoint":
            continue
        prior = by_count.get(count)
        if prior is not None and prior != directory:
            raise FigureBuildError(
                f"multiple distinct checkpoint audits discovered at {count}: "
                f"{prior}, {directory}"
            )
        by_count[count] = directory
    missing = expected - set(by_count)
    if missing:
        raise FigureBuildError(
            f"campaign-root checkpoint discovery is incomplete: missing={sorted(missing)}"
        )
    return [by_count[count] for count in sorted(expected)]


def _load_audit(directory: Path, *, contract_path: Path, fingerprint: str) -> AuditBundle:
    if not directory.is_dir():
        raise FigureBuildError(f"checkpoint audit directory does not exist: {directory}")
    _verify_sha_index(directory)
    receipt_path = directory / "CHECKPOINT_RECEIPT.json"
    status_path = directory / "CHECKPOINT_STATUS.json"
    coverage_path = directory / "COVERAGE_SUMMARY.json"
    receipt = _read_json(receipt_path)
    status = _read_json(status_path)
    coverage = _read_json(coverage_path)
    accepted_count = _as_int(receipt.get("expected_accepted"), "expected_accepted")
    expected_phase = phase_for_accepted_sequence(accepted_count)
    if receipt.get("overall_status") != "PASS" or receipt.get("decision") != "USE_CHECKPOINT":
        raise FigureBuildError(f"checkpoint {accepted_count} receipt is not PASS/USE_CHECKPOINT")
    if receipt.get("audit_mode") != "checkpoint":
        raise FigureBuildError(f"checkpoint {accepted_count} is not a formal checkpoint audit")
    if receipt.get("contract_fingerprint_sha256") != fingerprint:
        raise FigureBuildError(f"checkpoint {accepted_count} receipt fingerprint mismatch")
    if any(item.get("pass") is not True for item in receipt.get("checks") or []):
        raise FigureBuildError(f"checkpoint {accepted_count} contains failed audit checks")
    expected_rows = accepted_count * len(FREQUENCY_GRID_HZ)
    if (
        status.get("contract_fingerprint_sha256") != fingerprint
        or _as_int(status.get("accepted_geometries"), "accepted_geometries") != accepted_count
        or _as_int(status.get("s4p_artifacts"), "s4p_artifacts") != accepted_count
        or _as_int(status.get("geometry_frequency_rows"), "geometry_frequency_rows")
        != expected_rows
    ):
        raise FigureBuildError(f"checkpoint {accepted_count} status counts/fingerprint mismatch")
    if (
        coverage.get("contract_fingerprint_sha256") != fingerprint
        or _as_int(coverage.get("expected_accepted_geometries"), "coverage accepted")
        != accepted_count
        or _as_int(coverage.get("feature_row_count"), "feature_row_count") != expected_rows
    ):
        raise FigureBuildError(f"checkpoint {accepted_count} coverage counts/fingerprint mismatch")
    _verify_evidence(
        (receipt.get("inputs") or {}).get("contract"),
        contract_path,
        f"checkpoint {accepted_count} contract input",
    )
    outputs = receipt.get("outputs") or {}
    sources: dict[str, SourceEvidence] = {}
    for label, filename in AUDIT_SOURCE_FILES.items():
        path = directory / filename
        _verify_evidence(outputs.get(AUDIT_OUTPUT_KEYS[label]), path, f"{accepted_count}:{label}")
        row_count = _csv_row_count(path)
        sources[label] = SourceEvidence(
            label=f"checkpoint_{accepted_count}:{label}",
            filename=filename,
            sha256=_sha256(path),
            size_bytes=path.stat().st_size,
            row_count=row_count,
        )
    return AuditBundle(
        accepted_count=accepted_count,
        phase=expected_phase,
        directory=directory,
        receipt_sha256=_sha256(receipt_path),
        status_sha256=_sha256(status_path),
        sources=sources,
    )


def _load_history(directory: Path, *, fingerprint: str) -> HistoryBundle:
    if not directory.is_dir():
        raise FigureBuildError(f"campaign history directory does not exist: {directory}")
    _verify_sha_index(directory)
    receipt_path = directory / "CAMPAIGN_HISTORY_RECEIPT.json"
    receipt = _read_json(receipt_path)
    if (
        receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "USE_AS_AUDITED_CAMPAIGN_HISTORY"
        or receipt.get("contract_fingerprint_sha256") != fingerprint
    ):
        raise FigureBuildError("campaign history receipt is not PASS or fingerprint-bound")
    if tuple(receipt.get("audit_counts") or ()) != tuple(REQUIRED_HISTORY_AUDIT_COUNTS):
        raise FigureBuildError("campaign history audit-count contract mismatch")
    terminal = receipt.get("terminal_counts") or {}
    if (
        _as_int(terminal.get("accepted_geometries"), "terminal accepted")
        != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(terminal.get("s4p_artifacts"), "terminal s4p")
        != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(terminal.get("geometry_frequency_rows"), "terminal rows")
        != TARGET_ACCEPTED_GEOMETRIES * len(FREQUENCY_GRID_HZ)
    ):
        raise FigureBuildError("campaign history terminal counts are not exact")
    if any(value is not True for value in (receipt.get("checks") or {}).values()):
        raise FigureBuildError("campaign history receipt contains failed checks")
    outputs = receipt.get("outputs") or {}
    sources: dict[str, SourceEvidence] = {}
    for label, filename in HISTORY_SOURCE_FILES.items():
        path = directory / filename
        _verify_evidence(outputs.get(label), path, f"history:{label}")
        sources[label] = SourceEvidence(
            label=f"history:{label}",
            filename=filename,
            sha256=_sha256(path),
            size_bytes=path.stat().st_size,
            row_count=_csv_row_count(path),
        )
    coverage_points = _load_coverage_history(
        directory / HISTORY_SOURCE_FILES["coverage_deficit_history"], fingerprint=fingerprint
    )
    acquisition_rows = _load_acquisition_history(
        directory / HISTORY_SOURCE_FILES["acquisition_round_history"]
    )
    return HistoryBundle(
        directory=directory,
        receipt_sha256=_sha256(receipt_path),
        sources=sources,
        coverage_points=coverage_points,
        acquisition_rows=acquisition_rows,
    )


def _load_coverage_history(path: Path, *, fingerprint: str) -> dict[int, HistoryPoint]:
    grouped: dict[int, dict[int, tuple[int, str]]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "accepted_geometries",
            "conditioned_cell_index",
            "actual_count",
            "target_count",
            "deficit",
            "cell_status",
            "campaign_contract_fingerprint",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise FigureBuildError(f"coverage history missing columns: {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            count = _as_int(row.get("accepted_geometries"), f"history line {line} count")
            index = _as_int(row.get("conditioned_cell_index"), f"history line {line} cell")
            actual = _as_int(row.get("actual_count"), f"history line {line} actual")
            target = _as_float(row.get("target_count"), f"history line {line} target")
            deficit = _as_float(row.get("deficit"), f"history line {line} deficit")
            status = str(row.get("cell_status") or "")
            if row.get("campaign_contract_fingerprint") != fingerprint:
                raise FigureBuildError(f"coverage history line {line} fingerprint mismatch")
            if not 0 <= index < PRIMARY_FREQUENCY_CONDITIONED_CELLS or index in grouped[count]:
                raise FigureBuildError(f"coverage history line {line} cell identity invalid")
            expected_target = count / float(PRIMARY_CELLS_PER_ANCHOR)
            expected_deficit = max(expected_target - actual, 0.0)
            expected_status = (
                "unobserved_under_current_geometry_contract"
                if actual == 0
                else ("underfilled" if actual < expected_target else "observed")
            )
            if (
                not math.isclose(target, expected_target, rel_tol=0.0, abs_tol=1.0e-9)
                or not math.isclose(deficit, expected_deficit, rel_tol=0.0, abs_tol=1.0e-9)
                or status != expected_status
            ):
                raise FigureBuildError(f"coverage history line {line} arithmetic/status mismatch")
            grouped[count][index] = (actual, status)
    if set(grouped) != set(REQUIRED_HISTORY_AUDIT_COUNTS):
        raise FigureBuildError("coverage history accepted-count set mismatch")
    points: dict[int, HistoryPoint] = {}
    for count, rows in grouped.items():
        if set(rows) != set(range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)):
            raise FigureBuildError(f"coverage history {count} does not enumerate all cells")
        actual = np.asarray([rows[index][0] for index in range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)])
        statuses = [rows[index][1] for index in range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)]
        metrics = occupancy_metrics(actual, accepted_count=count * len(ANCHOR_FREQUENCIES_GHZ))
        entropy = metrics.get("normalized_entropy")
        if entropy is None or not math.isfinite(float(entropy)):
            raise FigureBuildError(f"coverage history {count} entropy is not finite")
        points[count] = HistoryPoint(
            accepted_count=count,
            actual_counts=actual,
            observed_cells=int(np.sum(actual > 0)),
            underfilled_cells=sum(value != "observed" for value in statuses),
            unobserved_cells=statuses.count("unobserved_under_current_geometry_contract"),
            at_or_above_target_cells=statuses.count("observed"),
            normalized_entropy=float(entropy),
        )
    return points


def _load_acquisition_history(path: Path) -> tuple[dict[str, str], ...]:
    rows = _read_csv(path)
    if {_as_int(row.get("accepted_end"), "accepted_end") for row in rows} != set(
        REQUIRED_ACQUISITION_ENDPOINTS
    ):
        raise FigureBuildError("acquisition history endpoint set mismatch")
    for row in rows:
        batch_size = _as_int(row.get("batch_size"), "batch_size")
        counts = json.loads(str(row.get("actual_source_counts_json") or "{}"))
        if not isinstance(counts, dict) or sum(int(value) for value in counts.values()) != batch_size:
            raise FigureBuildError("acquisition history source counts do not sum to batch size")
    return tuple(sorted(rows, key=lambda row: int(row["accepted_end"])))


def _render_one_checkpoint(
    *,
    audit: AuditBundle,
    history: HistoryBundle,
    checkpoint_dir: Path,
    final_checkpoint_dir: Path,
    fingerprint: str,
    process_sha: str,
) -> dict[str, Any]:
    count = audit.accepted_count
    geometry_rows = _load_geometry_marginals(
        audit.directory / AUDIT_SOURCE_FILES["geometry_marginals"], count=count
    )
    frequency_rows = _read_csv(audit.directory / AUDIT_SOURCE_FILES["physical_by_frequency"])
    pairwise_rows = _read_csv(audit.directory / AUDIT_SOURCE_FILES["physical_pairwise"])
    primary_rows = _load_primary_cells(
        audit.directory / AUDIT_SOURCE_FILES["primary_cells"], count=count
    )
    funnel_rows = _load_failure_funnel(
        audit.directory / AUDIT_SOURCE_FILES["failure_funnel"]
    )

    footer_base = (
        f"fresh real-EMX audit | N={count:,} unique geometries | phase={audit.phase} | "
        f"process={process_sha[:12]} | contract={fingerprint[:12]}"
    )
    records: list[dict[str, Any]] = []

    def add(
        figure_id: str,
        figure: Any,
        *,
        question: str,
        family: str,
        sources: Sequence[SourceEvidence],
        denominator: str,
        frequency_or_anchor: str,
        validity_definition: str,
    ) -> None:
        if figure_id != FIGURE_IDS[len(records)]:
            raise FigureBuildError(f"figure order/id mismatch: {figure_id}")
        png_path = checkpoint_dir / f"{figure_id}.png"
        svg_path = checkpoint_dir / f"{figure_id}.svg"
        _save_figure(figure, png_path, svg_path)
        records.append(
            {
                "figure_id": figure_id,
                "analytical_question": question,
                "chart_family": family,
                "files": {
                    "png": _file_record(
                        png_path,
                        label=figure_id,
                        recorded_filename=f"{final_checkpoint_dir.name}/{png_path.name}",
                    ),
                    "svg": _file_record(
                        svg_path,
                        label=figure_id,
                        recorded_filename=f"{final_checkpoint_dir.name}/{svg_path.name}",
                    ),
                },
                "source_csvs": [source.as_dict() for source in sources],
                "denominator": denominator,
                "frequency_or_anchor": frequency_or_anchor,
                "validity_definition": validity_definition,
                "campaign_phase": audit.phase,
                "production_process_config_sha256": process_sha,
                "campaign_contract_fingerprint_sha256": fingerprint,
                "checkpoint_receipt_sha256": audit.receipt_sha256,
            }
        )

    add(
        FIGURE_IDS[0],
        _plot_geometry_sampling(geometry_rows, footer_base),
        question="How evenly do the accepted geometries occupy each frozen 10-D marginal?",
        family="matrix_heatmap",
        sources=[audit.sources["geometry_marginals"]],
        denominator=f"{count:,} canonical geometry-unique accepted samples per dimension",
        frequency_or_anchor="geometry-only; no frequency",
        validity_definition="accepted after all frozen analytical/GDS/DRC/EMX/S4P/feature gates",
    )
    add(
        FIGURE_IDS[1],
        _plot_physical_marginals(frequency_rows, count=count, footer=footer_base),
        question="How do broadband-valid physical-feature marginals change across 5–60 GHz?",
        family="small_multiple_line_and_band",
        sources=[audit.sources["physical_by_frequency"]],
        denominator="broadband_descriptor_valid records at each exact frequency",
        frequency_or_anchor="5–60 GHz inclusive, exact 1 GHz steps (56 points)",
        validity_definition="broadband_descriptor_valid=true; ALL campaign phases",
    )
    for offset, pair in enumerate((("lp_nh", "ls_nh"), ("qp", "qs"), ("qmin", "k_abs")), start=2):
        add(
            FIGURE_IDS[offset],
            _plot_pairwise_anchor_heatmaps(
                pairwise_rows,
                pair=pair,
                count=count,
                footer=footer_base,
            ),
            question=f"How is joint {pair[0]}–{pair[1]} response coverage distributed by anchor?",
            family="faceted_heatmap",
            sources=[audit.sources["physical_pairwise"]],
            denominator="broadband_descriptor_valid geometry records at each anchor",
            frequency_or_anchor=f"exact anchors {list(ANCHOR_FREQUENCIES_GHZ)} GHz",
            validity_definition="broadband_descriptor_valid=true; ALL campaign phases",
        )
    add(
        FIGURE_IDS[5],
        _plot_primary_cells(primary_rows, count=count, footer=footer_base),
        question="Which frozen Xp/Xs/Qmin/|K| primary cells are occupied at each anchor?",
        family="faceted_heatmap",
        sources=[audit.sources["primary_cells"]],
        denominator=f"{count:,} geometry-unique broadband-valid records per anchor",
        frequency_or_anchor=f"exact anchors {list(ANCHOR_FREQUENCIES_GHZ)} GHz",
        validity_definition="broadband_descriptor_valid=true; one actual cell per geometry per anchor",
    )
    add(
        FIGURE_IDS[6],
        _plot_underfilled_history(history.coverage_points, count=count, footer=footer_base),
        question="How many fixed primary cells remain below target as accepted samples accumulate?",
        family="multi_series_line",
        sources=[history.sources["coverage_deficit_history"]],
        denominator=f"{PRIMARY_FREQUENCY_CONDITIONED_CELLS:,} frozen anchor-conditioned cells per audit",
        frequency_or_anchor=f"combined exact anchors {list(ANCHOR_FREQUENCIES_GHZ)} GHz",
        validity_definition="actual broadband-valid fresh-real-EMX primary-cell occupancy",
    )
    add(
        FIGURE_IDS[7],
        _plot_entropy_history(history.coverage_points, count=count, footer=footer_base),
        question="Does geometry-unique primary-cell occupancy become more uniform with sample count?",
        family="line_with_reference",
        sources=[history.sources["coverage_deficit_history"]],
        denominator=f"{PRIMARY_FREQUENCY_CONDITIONED_CELLS:,} frozen cells at each audited count",
        frequency_or_anchor=f"combined exact anchors {list(ANCHOR_FREQUENCIES_GHZ)} GHz",
        validity_definition="normalized Shannon entropy of actual broadband-valid occupancy",
    )
    add(
        FIGURE_IDS[8],
        _plot_acquisition_contribution(history.acquisition_rows, count=count, footer=footer_base),
        question="Which frozen acquisition sources contributed accepted geometries by phase?",
        family="stacked_horizontal_bar",
        sources=[history.sources["acquisition_round_history"]],
        denominator=f"all {count:,} accepted geometries through this checkpoint",
        frequency_or_anchor="geometry acquisition provenance; no frequency",
        validity_definition="accepted-ledger provenance; proxy predictions excluded from labels",
    )
    add(
        FIGURE_IDS[9],
        _plot_valid_fraction(frequency_rows, count=count, footer=footer_base),
        question="What fraction of parseable records satisfies broad and strict validity by frequency?",
        family="multi_series_line",
        sources=[audit.sources["physical_by_frequency"]],
        denominator=f"{count:,} all_parseable_emx_records at each exact frequency",
        frequency_or_anchor="5–60 GHz inclusive, exact 1 GHz steps (56 points)",
        validity_definition="broadband_descriptor_valid and strict_lumped_valid shown separately",
    )
    add(
        FIGURE_IDS[10],
        _plot_panel_fraction(frequency_rows, count=count, footer=footer_base),
        question="What share of parseable records lies inside the broad and literature-practical panels?",
        family="multi_series_line",
        sources=[audit.sources["physical_by_frequency"]],
        denominator=f"{count:,} all_parseable_emx_records at each exact frequency",
        frequency_or_anchor="5–60 GHz inclusive, exact 1 GHz steps (56 points)",
        validity_definition="inside_broad_response_envelope and inside_literature_practical_panel",
    )
    add(
        FIGURE_IDS[11],
        _plot_failure_funnel(funnel_rows, footer=footer_base),
        question="How many candidates are represented at each recorded generation/failure stage?",
        family="horizontal_stage_bar",
        sources=[audit.sources["failure_funnel"]],
        denominator="raw_geometry_candidates from the checkpoint failure-funnel ledger",
        frequency_or_anchor="campaign-wide; exact frequency completion is a separate funnel stage",
        validity_definition="recorded stage counts; failures are not silently removed",
    )
    add(
        FIGURE_IDS[12],
        _plot_boundary_coverage(geometry_rows, count=count, footer=footer_base),
        question="How much accepted geometry mass reaches both sides of each frozen design bound?",
        family="grouped_horizontal_bar",
        sources=[audit.sources["geometry_marginals"]],
        denominator=f"{count:,} canonical geometry-unique accepted samples per dimension",
        frequency_or_anchor="geometry-only; no frequency",
        validity_definition="within 10% of either frozen geometry bound",
    )
    add(
        FIGURE_IDS[13],
        _plot_before_after_repair(history.coverage_points, count=count, footer=footer_base),
        question="How does response-cell coverage compare with the 50k Phase-A baseline?",
        family="grouped_bar",
        sources=[
            history.sources["coverage_deficit_history"],
            history.sources["acquisition_round_history"],
        ],
        denominator=f"{PRIMARY_FREQUENCY_CONDITIONED_CELLS:,} cells at 50k and {count:,} accepted samples",
        frequency_or_anchor=f"combined exact anchors {list(ANCHOR_FREQUENCIES_GHZ)} GHz",
        validity_definition=(
            "actual broadband-valid occupancy; active-mixture and maximin-fallback provenance retained"
        ),
    )

    if len(records) != len(FIGURE_IDS):
        raise FigureBuildError(f"checkpoint {count} rendered {len(records)} logical figures")
    return {
        "schema": "broadband56_checkpoint_figure_manifest_v1",
        "campaign_id": CAMPAIGN_ID,
        "accepted_geometries": count,
        "campaign_phase": audit.phase,
        "evidence_class": "fresh_real_emx_checkpoint_audit_derived_static_figures",
        "production_process_config_sha256": process_sha,
        "campaign_contract_fingerprint_sha256": fingerprint,
        "checkpoint_receipt_sha256": audit.receipt_sha256,
        "history_receipt_sha256": history.receipt_sha256,
        "figures": records,
        "checks": {
            "logical_figure_count_exact_14": len(records) == 14,
            "png_and_svg_for_each": all(
                set(record["files"]) == {"png", "svg"} for record in records
            ),
            "all_sources_are_sha_bound_csvs": all(
                record["source_csvs"]
                and all(_is_sha256(source["sha256"]) for source in record["source_csvs"])
                for record in records
            ),
            "all_required_evidence_dimensions_present": all(
                record["denominator"]
                and record["frequency_or_anchor"]
                and record["validity_definition"]
                and record["campaign_phase"]
                and _is_sha256(record["production_process_config_sha256"])
                and _is_sha256(record["campaign_contract_fingerprint_sha256"])
                for record in records
            ),
        },
        "scientific_boundary": (
            "Figures are descriptive audits of accepted fresh-real-EMX evidence. They do not "
            "convert record-weighted rows into independent samples, and they do not claim "
            "model accuracy, HFSS agreement, measurement agreement, or silicon validation."
        ),
    }


def _load_geometry_marginals(path: Path, *, count: int) -> list[dict[str, str]]:
    rows = _read_csv(path)
    if len(rows) != len(GEOMETRY_FIELDS) or {row.get("field") for row in rows} != set(
        GEOMETRY_FIELDS
    ):
        raise FigureBuildError("geometry marginal table does not contain exact 10-D fields")
    for row in rows:
        if _as_int(row.get("geometry_count"), "geometry_count") != count:
            raise FigureBuildError("geometry marginal denominator mismatch")
        counts = _json_int_array(row.get("bin_counts_json"), "geometry bin counts")
        if len(counts) != GEOMETRY_COVERAGE_BINS_PER_DIMENSION or sum(counts) != count:
            raise FigureBuildError("geometry marginal bin counts mismatch")
    return sorted(rows, key=lambda row: GEOMETRY_FIELDS.index(str(row["field"])))


def _load_primary_cells(path: Path, *, count: int) -> list[dict[str, str]]:
    rows = _read_csv(path)
    if len(rows) != PRIMARY_FREQUENCY_CONDITIONED_CELLS:
        raise FigureBuildError("primary cell table row count mismatch")
    seen: set[int] = set()
    target = count / float(PRIMARY_CELLS_PER_ANCHOR)
    for row in rows:
        index = _as_int(row.get("conditioned_cell_index"), "conditioned_cell_index")
        anchor_index, local = divmod(index, PRIMARY_CELLS_PER_ANCHOR)
        if (
            index in seen
            or not 0 <= index < PRIMARY_FREQUENCY_CONDITIONED_CELLS
            or _as_int(row.get("anchor_ghz"), "anchor_ghz")
            != ANCHOR_FREQUENCIES_GHZ[anchor_index]
            or _as_int(row.get("local_cell_index"), "local_cell_index") != local
        ):
            raise FigureBuildError("primary cell table identity mismatch")
        if not math.isclose(
            _as_float(row.get("target_count"), "target_count"),
            target,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ):
            raise FigureBuildError("primary cell target mismatch")
        seen.add(index)
    return rows


def _load_failure_funnel(path: Path) -> list[dict[str, str]]:
    rows = _read_csv(path)
    if not rows or any(not row.get("stage") for row in rows):
        raise FigureBuildError("failure funnel is empty or has missing stages")
    for row in rows:
        if _as_int(row.get("count"), "failure funnel count") < 0:
            raise FigureBuildError("failure funnel contains a negative count")
    return rows


def _plot_geometry_sampling(rows: Sequence[Mapping[str, str]], footer: str) -> Any:
    matrix = np.asarray(
        [_json_int_array(row.get("bin_counts_json"), "geometry counts") for row in rows],
        dtype=float,
    )
    matrix /= np.maximum(matrix.sum(axis=1, keepdims=True), 1.0)
    fig, ax = plt.subplots(figsize=(12.4, 6.4))
    image = ax.imshow(matrix, aspect="auto", cmap="Blues", vmin=0.0, vmax=max(0.15, matrix.max()))
    ax.set_yticks(range(len(rows)), [_short_geometry_label(str(row["field"])) for row in rows])
    ax.set_xticks(range(matrix.shape[1]), [str(index + 1) for index in range(matrix.shape[1])])
    ax.set_xlabel("Frozen normalized marginal bin")
    ax.set_ylabel("Geometry dimension")
    _title(fig, "Geometry sampling coverage", "Row-normalized occupancy across the frozen 10-D bounds")
    fig.colorbar(image, ax=ax, label="Fraction of accepted geometries")
    _finish(fig, footer)
    return fig


def _plot_physical_marginals(
    rows: Sequence[Mapping[str, str]], *, count: int, footer: str
) -> Any:
    selected = _frequency_feature_rows(rows, population="broadband_descriptor_valid")
    fig, axes = plt.subplots(3, 3, figsize=(14.5, 10.0), sharex=True)
    for ax, feature in zip(axes.flat, SECONDARY_FEATURES):
        feature_rows = selected[feature]
        frequency = np.asarray([int(row["frequency_hz"]) / 1.0e9 for row in feature_rows])
        records = np.asarray([_as_int(row.get("record_count"), "record_count") for row in feature_rows])
        if np.any(records < 0) or np.any(records > count):
            raise FigureBuildError(f"invalid broadband-valid record count for {feature}")
        mean = np.asarray(
            [
                _as_float(row.get("mean"), "mean") if record_count > 0 else math.nan
                for row, record_count in zip(feature_rows, records)
            ]
        )
        std = np.asarray(
            [
                _as_float(row.get("std"), "std") if record_count > 0 else math.nan
                for row, record_count in zip(feature_rows, records)
            ]
        )
        ax.plot(frequency, mean, color=BLUE, linewidth=1.8)
        ax.fill_between(frequency, mean - std, mean + std, color=BLUE, alpha=0.15, linewidth=0)
        if not np.any(np.isfinite(mean)):
            ax.text(
                0.5,
                0.5,
                "No broadband-valid records",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=MUTED,
                fontsize=8,
            )
        ax.set_title(_feature_label(feature), fontsize=10, color=INK)
        ax.grid(True, axis="y", color=GRID, linewidth=0.7)
        ax.tick_params(labelsize=8)
    for ax in axes[-1, :]:
        ax.set_xlabel("Frequency (GHz)")
    _title(
        fig,
        "Physical marginals by frequency",
        "Mean ±1 standard deviation for broadband-descriptor-valid fresh-EMX records",
    )
    _finish(fig, footer)
    return fig


def _plot_pairwise_anchor_heatmaps(
    rows: Sequence[Mapping[str, str]],
    *,
    pair: tuple[str, str],
    count: int,
    footer: str,
) -> Any:
    matrices: list[np.ndarray] = []
    totals: list[int] = []
    for anchor in ANCHOR_FREQUENCIES_GHZ:
        matches = [
            row
            for row in rows
            if row.get("coverage_scope") == "feature_pair_at_exact_frequency"
            and row.get("population") == "broadband_descriptor_valid"
            and row.get("campaign_phase") == "ALL"
            and _as_optional_int(row.get("frequency_hz")) == anchor * 1_000_000_000
            and row.get("left_feature") == pair[0]
            and row.get("right_feature") == pair[1]
        ]
        if len(matches) != 1:
            raise FigureBuildError(f"missing/duplicate pairwise row for {pair} at {anchor} GHz")
        matrix = _matrix_from_row(matches[0])
        total = int(matrix.sum())
        if total < 0 or total > count:
            raise FigureBuildError(f"pairwise denominator invalid for {pair} at {anchor} GHz")
        matrices.append(matrix)
        totals.append(total)
    vmax = max(1.0, max(float(np.log1p(matrix).max()) for matrix in matrices))
    fig = plt.figure(figsize=(14.2, 7.4))
    grid = fig.add_gridspec(
        2,
        5,
        width_ratios=(1.0, 1.0, 1.0, 1.0, 0.06),
        left=0.065,
        right=0.94,
        bottom=0.12,
        top=0.84,
        wspace=0.20,
        hspace=0.30,
    )
    axes = np.asarray(
        [[fig.add_subplot(grid[row, column]) for column in range(4)] for row in range(2)]
    )
    color_axis = fig.add_subplot(grid[:, 4])
    image = None
    for ax, anchor, matrix, total in zip(axes.flat, ANCHOR_FREQUENCIES_GHZ, matrices, totals):
        image = ax.imshow(np.log1p(matrix), origin="lower", cmap="Blues", vmin=0.0, vmax=vmax)
        ax.set_title(f"{anchor} GHz · n={total:,}", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
    assert image is not None
    fig.colorbar(image, cax=color_axis, label="log(1 + records per joint cell)")
    _title(
        fig,
        f"{_feature_label(pair[0])}–{_feature_label(pair[1])} joint coverage",
        "Underflow and overflow bins retained; each panel is one exact acquisition anchor",
    )
    _finish(fig, footer, manual_layout=True)
    return fig


def _plot_primary_cells(
    rows: Sequence[Mapping[str, str]], *, count: int, footer: str
) -> Any:
    matrices: list[np.ndarray] = []
    for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ):
        values = np.zeros(PRIMARY_CELLS_PER_ANCHOR, dtype=float)
        for row in rows:
            if _as_int(row.get("anchor_ghz"), "anchor_ghz") != anchor:
                continue
            local = _as_int(row.get("local_cell_index"), "local_cell_index")
            values[local] = _as_int(row.get("actual_count"), "actual_count")
        if values.sum() > count:
            raise FigureBuildError(f"primary-cell count exceeds checkpoint denominator at {anchor}")
        matrices.append(values.reshape(36, 36))
    vmax = max(1.0, max(float(np.log1p(matrix).max()) for matrix in matrices))
    fig = plt.figure(figsize=(14.2, 7.4))
    grid = fig.add_gridspec(
        2,
        5,
        width_ratios=(1.0, 1.0, 1.0, 1.0, 0.06),
        left=0.065,
        right=0.94,
        bottom=0.12,
        top=0.84,
        wspace=0.20,
        hspace=0.30,
    )
    axes = np.asarray(
        [[fig.add_subplot(grid[row, column]) for column in range(4)] for row in range(2)]
    )
    color_axis = fig.add_subplot(grid[:, 4])
    image = None
    for ax, anchor, matrix in zip(axes.flat, ANCHOR_FREQUENCIES_GHZ, matrices):
        image = ax.imshow(np.log1p(matrix), origin="lower", cmap="Blues", vmin=0.0, vmax=vmax)
        ax.set_title(f"{anchor} GHz", fontsize=9)
        ax.set_xlabel("Qmin × |K| cells", fontsize=8)
        ax.set_ylabel("Xp × Xs cells", fontsize=8)
        ax.set_xticks([])
        ax.set_yticks([])
    assert image is not None
    fig.colorbar(image, cax=color_axis, label="log(1 + geometries per 4-D cell)")
    _title(
        fig,
        "Primary 4-D cell occupancy",
        "Geometry-unique Xp/Xs/Qmin/|K| occupancy; 1,296 fixed cells per anchor",
    )
    _finish(fig, footer, manual_layout=True)
    return fig


def _plot_underfilled_history(
    points: Mapping[int, HistoryPoint], *, count: int, footer: str
) -> Any:
    selected = [points[value] for value in sorted(points) if value <= count]
    x = np.asarray([item.accepted_count for item in selected])
    underfilled = np.asarray([item.underfilled_cells for item in selected])
    unobserved = np.asarray([item.unobserved_cells for item in selected])
    fig, ax = plt.subplots(figsize=(11.8, 6.4))
    ax.plot(x, underfilled, color=ORANGE, marker="o", markersize=3.5, label="Below target")
    ax.plot(x, unobserved, color=MUTED, marker="s", markersize=3.0, label="Unobserved")
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Accepted geometries")
    ax.set_ylabel("Anchor-conditioned cells")
    ax.grid(True, axis="y", color=GRID)
    ax.legend(frameon=False)
    _title(
        fig,
        "Underfilled cell count vs accepted samples",
        f"Exact audits through {count:,}; denominator = {PRIMARY_FREQUENCY_CONDITIONED_CELLS:,} cells",
    )
    _finish(fig, footer)
    return fig


def _plot_entropy_history(
    points: Mapping[int, HistoryPoint], *, count: int, footer: str
) -> Any:
    selected = [points[value] for value in sorted(points) if value <= count]
    x = np.asarray([item.accepted_count for item in selected])
    y = np.asarray([item.normalized_entropy for item in selected])
    fig, ax = plt.subplots(figsize=(11.8, 6.4))
    ax.plot(x, y, color=BLUE, marker="o", markersize=3.5)
    ax.axhline(1.0, color=INK, linestyle="--", linewidth=1.0, label="Uniform occupancy = 1")
    ax.set_ylim(0.0, 1.02)
    ax.set_xlabel("Accepted geometries")
    ax.set_ylabel("Normalized entropy")
    ax.grid(True, axis="y", color=GRID)
    ax.legend(frameon=False)
    _title(
        fig,
        "Uniformity entropy vs accepted samples",
        "Shannon entropy over actual geometry-unique primary-cell occupancy",
    )
    _finish(fig, footer)
    return fig


def _plot_acquisition_contribution(
    rows: Sequence[Mapping[str, str]], *, count: int, footer: str
) -> Any:
    by_phase: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        end = _as_int(row.get("accepted_end"), "accepted_end")
        if end > count:
            continue
        phase = str(row.get("campaign_phase") or "")
        values = json.loads(str(row.get("actual_source_counts_json") or "{}"))
        by_phase[phase].update({str(key): int(value) for key, value in values.items()})
    phases = [phase for phase in ("PHASE_A", "PHASE_B", "PHASE_C") if sum(by_phase[phase].values())]
    if sum(sum(counter.values()) for counter in by_phase.values()) != count:
        raise FigureBuildError(f"acquisition provenance does not sum to {count}")
    sources = sorted({source for counter in by_phase.values() for source in counter})
    fig, ax = plt.subplots(figsize=(12.0, 6.0))
    left = np.zeros(len(phases), dtype=float)
    for source in sources:
        values = np.asarray([by_phase[phase][source] for phase in phases], dtype=float)
        ax.barh(
            phases,
            values,
            left=left,
            color=SOURCE_COLORS.get(source, MUTED),
            edgecolor="white",
            label=source.replace("_", " "),
        )
        left += values
    ax.set_xlabel("Accepted geometries")
    ax.grid(True, axis="x", color=GRID)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2)
    _title(
        fig,
        "Acquisition phase contribution",
        "Exact accepted-ledger source counts; proxy/uncertainty terms rank candidates only",
    )
    _finish(fig, footer, bottom=0.20)
    return fig


def _plot_valid_fraction(
    rows: Sequence[Mapping[str, str]], *, count: int, footer: str
) -> Any:
    all_counts = _population_counts(rows, "all_parseable_emx_records")
    broad_counts = _population_counts(rows, "broadband_descriptor_valid")
    strict_counts = _population_counts(rows, "strict_lumped_valid")
    _require_all_frequency_denominator(all_counts, count=count)
    frequency = np.asarray(sorted(all_counts)) / 1.0e9
    denominator = np.asarray([all_counts[value] for value in sorted(all_counts)], dtype=float)
    broad = np.asarray([broad_counts[value] for value in sorted(all_counts)]) / denominator
    strict = np.asarray([strict_counts[value] for value in sorted(all_counts)]) / denominator
    fig, ax = plt.subplots(figsize=(11.8, 6.4))
    ax.plot(frequency, broad * 100.0, color=BLUE, linewidth=2.0, label="Broad descriptor valid")
    ax.plot(frequency, strict * 100.0, color=ORANGE, linewidth=2.0, label="Strict lumped valid")
    ax.set_ylim(0.0, 101.0)
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("Fraction of parseable records (%)")
    ax.grid(True, axis="y", color=GRID)
    ax.legend(frameon=False)
    _title(fig, "Valid feature fraction vs frequency", "Two validity definitions shown separately")
    _finish(fig, footer)
    return fig


def _plot_panel_fraction(
    rows: Sequence[Mapping[str, str]], *, count: int, footer: str
) -> Any:
    all_counts = _population_counts(rows, "all_parseable_emx_records")
    broad_counts = _population_counts(rows, "inside_broad_response_envelope")
    practical_counts = _population_counts(rows, "inside_literature_practical_panel")
    _require_all_frequency_denominator(all_counts, count=count)
    frequencies = sorted(all_counts)
    x = np.asarray(frequencies) / 1.0e9
    denominator = np.asarray([all_counts[value] for value in frequencies], dtype=float)
    broad = np.asarray([broad_counts[value] for value in frequencies]) / denominator
    practical = np.asarray([practical_counts[value] for value in frequencies]) / denominator
    fig, ax = plt.subplots(figsize=(11.8, 6.4))
    ax.plot(x, broad * 100.0, color=BLUE, linewidth=2.0, label="Broad response envelope")
    ax.plot(x, practical * 100.0, color=TEAL, linewidth=2.0, label="Literature practical panel")
    ax.set_ylim(0.0, 101.0)
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("Fraction of parseable records (%)")
    ax.grid(True, axis="y", color=GRID)
    ax.legend(frameon=False)
    _title(fig, "Broad vs practical panel coverage", "Target-independent physical response panels")
    _finish(fig, footer)
    return fig


def _plot_failure_funnel(rows: Sequence[Mapping[str, str]], *, footer: str) -> Any:
    stages = [str(row["stage"]).replace("_", " ") for row in rows]
    values = np.asarray([_as_int(row.get("count"), "funnel count") for row in rows])
    colors = [BLUE if "accepted" in stage else ORANGE if "failure" in stage else MUTED for stage in stages]
    fig, ax = plt.subplots(figsize=(12.0, max(6.0, 0.48 * len(rows) + 2.0)))
    bars = ax.barh(stages[::-1], values[::-1], color=colors[::-1], edgecolor="white")
    ax.bar_label(bars, labels=[f"{value:,}" for value in values[::-1]], padding=4, fontsize=8)
    ax.set_xlim(0, max(values.max() * 1.16, 1.0))
    ax.set_xlabel("Recorded count")
    ax.grid(True, axis="x", color=GRID)
    _title(
        fig,
        "Failure funnel",
        "Recorded raw, failure-category, and accepted counts; no failed stage is hidden",
    )
    _finish(fig, footer)
    return fig


def _plot_boundary_coverage(
    rows: Sequence[Mapping[str, str]], *, count: int, footer: str
) -> Any:
    labels = [_short_geometry_label(str(row["field"])) for row in rows]
    lower = np.asarray([_as_int(row.get("lower_boundary_count"), "lower boundary") for row in rows])
    upper = np.asarray([_as_int(row.get("upper_boundary_count"), "upper boundary") for row in rows])
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(12.2, 7.0))
    height = 0.36
    ax.barh(y - height / 2, lower / count * 100.0, height=height, color=BLUE, label="Lower 10%")
    ax.barh(y + height / 2, upper / count * 100.0, height=height, color=ORANGE, label="Upper 10%")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Fraction of accepted geometries (%)")
    ax.grid(True, axis="x", color=GRID)
    ax.legend(frameon=False)
    _title(fig, "Boundary coverage", "Mass within 10% of each frozen lower and upper geometry bound")
    _finish(fig, footer)
    return fig


def _plot_before_after_repair(
    points: Mapping[int, HistoryPoint], *, count: int, footer: str
) -> Any:
    baseline = points[50_000]
    current = points[count]
    denominator = float(PRIMARY_FREQUENCY_CONDITIONED_CELLS)
    baseline_values = np.asarray(
        [
            baseline.observed_cells / denominator,
            baseline.normalized_entropy,
            baseline.at_or_above_target_cells / denominator,
        ]
    )
    current_values = np.asarray(
        [
            current.observed_cells / denominator,
            current.normalized_entropy,
            current.at_or_above_target_cells / denominator,
        ]
    )
    labels = ["Observed cells", "Normalized entropy", "At/above target"]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(11.8, 6.4))
    width = 0.36
    ax.bar(x - width / 2, baseline_values * 100.0, width, color=MUTED, label="50k Phase-A baseline")
    current_label = "50k baseline (no adaptive rounds yet)" if count == 50_000 else f"{count // 1000}k checkpoint"
    ax.bar(x + width / 2, current_values * 100.0, width, color=BLUE, label=current_label)
    ax.set_xticks(x, labels)
    ax.set_ylim(0.0, 101.0)
    ax.set_ylabel("Coverage score (%)")
    ax.grid(True, axis="y", color=GRID)
    ax.legend(frameon=False)
    _title(
        fig,
        "Response coverage before and after active repair",
        "50k space-filling baseline vs cumulative audited occupancy; fallback rounds remain visible",
    )
    _finish(fig, footer)
    return fig


def _frequency_feature_rows(
    rows: Sequence[Mapping[str, str]], *, population: str
) -> dict[str, list[Mapping[str, str]]]:
    selected: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("population") == population and row.get("campaign_phase") == "ALL":
            feature = str(row.get("feature") or "")
            if feature in SECONDARY_FEATURES:
                selected[feature].append(row)
    for feature in SECONDARY_FEATURES:
        selected[feature].sort(key=lambda row: int(row["frequency_hz"]))
        frequencies = tuple(int(row["frequency_hz"]) for row in selected[feature])
        if frequencies != tuple(FREQUENCY_GRID_HZ):
            raise FigureBuildError(f"frequency rows are incomplete for {population}:{feature}")
    return selected


def _population_counts(rows: Sequence[Mapping[str, str]], population: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in rows:
        if (
            row.get("population") == population
            and row.get("campaign_phase") == "ALL"
            and row.get("feature") == SECONDARY_FEATURES[0]
        ):
            frequency = _as_int(row.get("frequency_hz"), "frequency_hz")
            if frequency in counts:
                raise FigureBuildError(f"duplicate frequency count for population {population}")
            counts[frequency] = _as_int(row.get("record_count"), "record_count")
    if set(counts) != set(FREQUENCY_GRID_HZ):
        raise FigureBuildError(f"frequency count set incomplete for population {population}")
    return counts


def _require_all_frequency_denominator(counts: Mapping[int, int], *, count: int) -> None:
    if any(value != count for value in counts.values()):
        raise FigureBuildError("all_parseable_emx_records denominator is not exact at every frequency")


def _matrix_from_row(row: Mapping[str, str]) -> np.ndarray:
    shape = _json_int_array(row.get("matrix_shape_json"), "matrix shape")
    if len(shape) != 2 or any(value <= 0 for value in shape):
        raise FigureBuildError("pairwise matrix shape is invalid")
    values = _json_int_array(row.get("cell_counts_row_major_json"), "pairwise cells")
    if len(values) != shape[0] * shape[1]:
        raise FigureBuildError("pairwise matrix payload length mismatch")
    return np.asarray(values, dtype=float).reshape(shape)


def _configure_matplotlib() -> None:
    matplotlib.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.edgecolor": MUTED,
            "axes.labelcolor": INK,
            "text.color": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.hashsalt": "broadband56_checkpoint_figures_v1",
        }
    )


def _title(fig: Any, title: str, subtitle: str) -> None:
    fig.suptitle(title, x=0.055, y=0.985, ha="left", fontsize=17, fontweight="bold", color=INK)
    fig.text(0.055, 0.945, subtitle, ha="left", va="top", fontsize=9.5, color=MUTED)


def _finish(
    fig: Any, footer: str, *, bottom: float = 0.10, manual_layout: bool = False
) -> None:
    fig.text(0.055, 0.025, footer, ha="left", va="bottom", fontsize=7.2, color=MUTED)
    if not manual_layout:
        fig.tight_layout(rect=(0.045, bottom, 0.985, 0.915))


def _save_figure(fig: Any, png_path: Path, svg_path: Path) -> None:
    try:
        fig.savefig(png_path, dpi=180, metadata={"Software": "broadband56 checkpoint renderer"})
        fig.savefig(svg_path, metadata={"Date": None, "Creator": "broadband56 checkpoint renderer"})
    finally:
        plt.close(fig)
    if png_path.stat().st_size <= 1_000 or svg_path.stat().st_size <= 1_000:
        raise FigureBuildError(f"rendered figure is empty or too small: {png_path.name}")


def _verify_sha_index(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    if not index.is_file():
        raise FigureBuildError(f"SHA256SUMS.txt is missing: {directory}")
    entries = 0
    for line_number, raw in enumerate(index.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            digest, relative = raw.split("  ", 1)
        except ValueError as exc:
            raise FigureBuildError(f"invalid SHA index line {line_number}: {index}") from exc
        path = directory / relative
        if not _is_sha256(digest) or not path.is_file() or _sha256(path) != digest:
            raise FigureBuildError(f"SHA index mismatch for {relative} in {directory.name}")
        entries += 1
    if entries == 0:
        raise FigureBuildError(f"SHA index is empty: {index}")


def _verify_evidence(evidence: Any, path: Path, label: str) -> None:
    if not isinstance(evidence, Mapping) or not path.is_file():
        raise FigureBuildError(f"missing file evidence: {label}")
    digest = str(evidence.get("sha256") or "")
    if Path(str(evidence.get("path") or "")).name != path.name:
        raise FigureBuildError(f"evidence filename mismatch: {label}")
    if digest != _sha256(path) or _as_int(evidence.get("size_bytes"), "size_bytes") != path.stat().st_size:
        raise FigureBuildError(f"evidence hash/size mismatch: {label}")


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path != index:
            lines.append(f"{_sha256(path)}  {path.relative_to(directory)}")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FigureBuildError(f"CSV does not exist: {path.name}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise FigureBuildError(f"CSV has no header: {path.name}")
        return [dict(row) for row in reader]


def _csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration as exc:
            raise FigureBuildError(f"CSV is empty: {path.name}") from exc
        return sum(1 for _ in reader)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FigureBuildError(f"JSON does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FigureBuildError(f"JSON parse failed: {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise FigureBuildError(f"JSON root is not an object: {path.name}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _file_record(
    path: Path, *, label: str, recorded_filename: str | None = None
) -> dict[str, Any]:
    return {
        "label": label,
        "filename": recorded_filename or path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())


def _as_int(value: Any, label: str) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError) as exc:
        raise FigureBuildError(f"invalid integer for {label}: {value!r}") from exc
    return number


def _as_optional_int(value: Any) -> int | None:
    if value in {None, "", "None"}:
        return None
    return _as_int(value, "optional integer")


def _as_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FigureBuildError(f"invalid float for {label}: {value!r}") from exc
    if not math.isfinite(number):
        raise FigureBuildError(f"non-finite float for {label}: {value!r}")
    return number


def _json_int_array(value: Any, label: str) -> list[int]:
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise FigureBuildError(f"invalid JSON array for {label}") from exc
    if not isinstance(parsed, list):
        raise FigureBuildError(f"expected JSON array for {label}")
    return [_as_int(item, label) for item in parsed]


def _short_geometry_label(field: str) -> str:
    replacements = {
        "primary_outer_width_um": "Primary outer W",
        "primary_outer_height_um": "Primary outer H",
        "secondary_outer_width_um": "Secondary outer W",
        "secondary_outer_height_um": "Secondary outer H",
        "line_width_um": "Line width",
        "primary_terminal_y_span_um": "Primary terminal span",
        "secondary_terminal_y_span_um": "Secondary terminal span",
        "offset_um": "Offset",
        "primary_feed_extension_um": "Primary feed extension",
        "secondary_feed_extension_um": "Secondary feed extension",
    }
    return replacements.get(field, field)


def _feature_label(feature: str) -> str:
    return {
        "xp_ohm": "Xp (Ω)",
        "xs_ohm": "Xs (Ω)",
        "lp_nh": "Lp (nH)",
        "ls_nh": "Ls (nH)",
        "qp": "Qp",
        "qs": "Qs",
        "qmin": "Qmin",
        "k_abs": "|K|",
        "ls_over_lp": "Ls/Lp",
    }.get(feature, feature)


if __name__ == "__main__":
    raise SystemExit(main())
