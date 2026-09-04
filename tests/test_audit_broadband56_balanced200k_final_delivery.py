from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    contract_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _load_module():
    path = ROOT / "scripts" / "audit_broadband56_balanced200k_final_delivery.py"
    spec = importlib.util.spec_from_file_location(
        "audit_broadband56_balanced200k_final_delivery", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(path: Path, *, recorded_path: Path | None = None) -> dict[str, object]:
    return {
        "path": str(recorded_path if recorded_path is not None else path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_index(directory: Path, *, recursive: bool) -> None:
    index = directory / "SHA256SUMS.txt"
    paths = directory.rglob("*") if recursive else directory.iterdir()
    lines = [
        f"{_sha256(path)}  {path.relative_to(directory)}"
        for path in sorted(paths)
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _patch_small_contract(module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "TARGET_ACCEPTED_GEOMETRIES", 4)
    monkeypatch.setattr(module, "EXPECTED_FEATURE_ROWS", 4 * len(FREQUENCY_GRID_HZ))
    monkeypatch.setattr(module, "EXPECTED_HISTORY_AUDITS", 2)
    monkeypatch.setattr(module, "EXPECTED_ACQUISITION_ROUNDS", 2)
    monkeypatch.setattr(module, "PRIMARY_FREQUENCY_CONDITIONED_CELLS", 3)


def _fixture(tmp_path: Path, module) -> dict[str, Path]:
    contract = json.loads(PUBLIC_CONTRACT.read_text(encoding="utf-8"))
    evidence_dir = tmp_path / "physical_evidence"
    evidence_dir.mkdir()
    production_config_path = evidence_dir / "production.yaml"
    production_config_path.write_text("synthetic: true\n", encoding="utf-8")
    process_sha = _sha256(production_config_path)
    contract["inherited_contract_evidence"] = {
        "previous_campaign_id": "synthetic_v1",
        "previous_contract_sha256": "b" * 64,
        "previous_config_sha256": "c" * 64,
        "production_config_sha256": process_sha,
        "private_runtime_paths_not_for_publication": True,
    }
    contract["preparation_status"] = "PASS"
    contract["contract_fingerprint_sha256"] = contract_fingerprint(contract)
    fingerprint = contract["contract_fingerprint_sha256"]
    contract_path = tmp_path / "campaign_contract_frozen.json"
    _write_json(contract_path, contract)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    accepted_path = raw_dir / "accepted_geometry_200k.csv"
    features_path = raw_dir / "broadband_features_11p2m_long.csv"
    artifact_path = raw_dir / "sparameter_artifact_index_200k.csv"
    provenance_path = raw_dir / "geometry_provenance_200k.csv"
    funnel_path = raw_dir / "failure_funnel.csv"

    accepted_rows = []
    artifact_rows = []
    provenance_rows = []
    for index in range(4):
        geometry_id = f"g{index:04d}"
        geometry_sha = hashlib.sha256(f"geometry-{index}".encode()).hexdigest()
        candidate_path = evidence_dir / f"candidate_{index}.csv"
        gds_path = evidence_dir / f"design_{index}.gds"
        calibre_path = evidence_dir / f"calibre_{index}.txt"
        emx_path = evidence_dir / f"emx_{index}.log"
        s4p_path = evidence_dir / f"design_{index}.s4p"
        candidate_path.write_text(f"candidate,{index}\n", encoding="utf-8")
        gds_path.write_bytes(f"gds-{index}".encode())
        calibre_path.write_text(f"calibre-{index}\n", encoding="utf-8")
        emx_path.write_text(f"emx-{index}\n", encoding="utf-8")
        s4p_path.write_text(f"s4p-{index}\n", encoding="utf-8")
        s4p_sha = _sha256(s4p_path)
        accepted_rows.append(
            {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_sha,
                "campaign_contract_fingerprint": fingerprint,
            }
        )
        artifact_rows.append(
            {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_sha,
                "campaign_contract_fingerprint": fingerprint,
                "s4p_path": str(s4p_path),
                "s4p_sha256": s4p_sha,
                "frequency_points": len(FREQUENCY_GRID_HZ),
                "emx_status": "PASS",
                "calibre_status": "PASS",
                "calibre_blocking_violations": 0,
            }
        )
        provenance_rows.append(
            {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_sha,
                "campaign_contract_fingerprint": fingerprint,
                "production_config_sha256": process_sha,
                "production_config_path": str(production_config_path),
                "candidate_source_path": str(candidate_path),
                "candidate_source_sha256": _sha256(candidate_path),
                "gds_path": str(gds_path),
                "gds_sha256": _sha256(gds_path),
                "calibre_report_path": str(calibre_path),
                "calibre_report_sha256": _sha256(calibre_path),
                "emx_log_path": str(emx_path),
                "emx_log_sha256": _sha256(emx_path),
                "s4p_path": str(s4p_path),
                "s4p_sha256": s4p_sha,
                "frequency_points": len(FREQUENCY_GRID_HZ),
                "calibre_status": "PASS",
                "calibre_blocking_violations": 0,
                "emx_status": "PASS",
                "fresh_real_emx": "true",
            }
        )
    _write_csv(accepted_path, accepted_rows)
    _write_csv(artifact_path, artifact_rows)
    _write_csv(provenance_path, provenance_rows)
    _write_csv(
        features_path,
        [
            {"geometry_id": row["geometry_id"], "frequency_hz": frequency_hz}
            for row in accepted_rows
            for frequency_hz in FREQUENCY_GRID_HZ
        ],
    )
    _write_csv(
        funnel_path,
        [
            {"stage": stage, "count": 8 if stage == "raw_geometry_candidates" else (4 if stage == "accepted_geometries" else 0)}
            for stage in sorted(module.FAILURE_FUNNEL_STAGES)
        ],
    )
    raw_receipt = raw_dir / "RAW_PRODUCTS_RECEIPT.json"
    _write_json(
        raw_receipt,
        {
            "schema": "broadband56_raw_products_receipt_v1",
            "overall_status": "PASS",
            "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "counts": {
                "accepted_geometries": 4,
                "geometry_frequency_rows": 4 * len(FREQUENCY_GRID_HZ),
            },
            "checks": {"synthetic_raw_products": True},
            "inputs": {
                "production_config": _evidence(production_config_path),
                "production_config_authorization": {
                    "mode": "FROZEN_CONTRACT_DIRECT",
                    "frozen_config_sha256": process_sha,
                    "effective_config_sha256": process_sha,
                    "full_campaign_receipt": None,
                    "corrected_foundry_layout_approval_receipt": None,
                },
            },
        },
    )
    _write_index(raw_dir, recursive=False)

    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()
    coverage_path = checkpoint_dir / "COVERAGE_SUMMARY.json"
    status_path = checkpoint_dir / "CHECKPOINT_STATUS.json"
    geometry_summary_path = checkpoint_dir / "GEOMETRY_COVERAGE_SUMMARY.json"
    _write_json(
        coverage_path,
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "expected_accepted_geometries": 4,
            "feature_row_count": 4 * len(FREQUENCY_GRID_HZ),
            "coverage_status": "COVERAGE_PARTIAL",
        },
    )
    _write_json(
        status_path,
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "checkpoint_status": "COMPLETE_200K",
            "audit_mode": "checkpoint",
            "coverage_status": "COVERAGE_PARTIAL",
            "accepted_geometries": 4,
            "s4p_artifacts": 4,
            "geometry_frequency_rows": 4 * len(FREQUENCY_GRID_HZ),
        },
    )
    _write_json(geometry_summary_path, {"status": "PASS"})
    checkpoint_csv_names = (
        "physical_coverage_cells_by_anchor.csv",
        "physical_coverage_by_frequency.csv",
        "physical_coverage_marginals.csv",
        "physical_coverage_pairwise.csv",
        "geometry_coverage_marginals.csv",
        "geometry_coverage_pairwise.csv",
        "FAILURE_FUNNEL.csv",
    )
    for name in checkpoint_csv_names:
        source = funnel_path if name == "FAILURE_FUNNEL.csv" else None
        if source is not None:
            (checkpoint_dir / name).write_bytes(source.read_bytes())
        else:
            _write_csv(checkpoint_dir / name, [{"metric": name, "value": 1}])
    output_map = {
        "coverage_cells": "physical_coverage_cells_by_anchor.csv",
        "coverage_by_frequency": "physical_coverage_by_frequency.csv",
        "coverage_marginals": "physical_coverage_marginals.csv",
        "coverage_pairwise": "physical_coverage_pairwise.csv",
        "geometry_coverage_summary": "GEOMETRY_COVERAGE_SUMMARY.json",
        "geometry_coverage_marginals": "geometry_coverage_marginals.csv",
        "geometry_coverage_pairwise": "geometry_coverage_pairwise.csv",
        "coverage_summary": "COVERAGE_SUMMARY.json",
        "checkpoint_status": "CHECKPOINT_STATUS.json",
        "failure_funnel": "FAILURE_FUNNEL.csv",
    }
    checkpoint_receipt = checkpoint_dir / "CHECKPOINT_RECEIPT.json"
    _write_json(
        checkpoint_receipt,
        {
            "overall_status": "PASS",
            "decision": "USE_CHECKPOINT",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "expected_accepted": 4,
            "audit_mode": "checkpoint",
            "checks": [{"name": "synthetic_terminal", "pass": True}],
            "inputs": {
                "contract": _evidence(contract_path),
                "accepted_geometries": _evidence(accepted_path),
                "long_features": _evidence(features_path),
                "artifact_index": _evidence(artifact_path),
                "failure_funnel": _evidence(funnel_path),
            },
            "outputs": {
                key: _evidence(checkpoint_dir / name) for key, name in output_map.items()
            },
        },
    )
    _write_index(checkpoint_dir, recursive=False)

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    history_paths = {
        "coverage_deficit_history": history_dir / "coverage_deficit_history.csv",
        "acquisition_round_history": history_dir / "acquisition_round_history.csv",
        "acquisition_source_by_geometry": history_dir / "acquisition_source_by_geometry.csv",
        "coverage_summary_200k": history_dir / "coverage_summary_200k.json",
    }
    _write_csv(history_paths["coverage_deficit_history"], [{"cell": index} for index in range(6)])
    _write_csv(history_paths["acquisition_round_history"], [{"round": index} for index in range(2)])
    _write_csv(history_paths["acquisition_source_by_geometry"], [{"geometry_id": f"g{index:04d}"} for index in range(4)])
    history_paths["coverage_summary_200k"].write_bytes(coverage_path.read_bytes())
    history_receipt = history_dir / "CAMPAIGN_HISTORY_RECEIPT.json"
    _write_json(
        history_receipt,
        {
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_CAMPAIGN_HISTORY",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "terminal_counts": {
                "accepted_geometries": 4,
                "s4p_artifacts": 4,
                "geometry_frequency_rows": 4 * len(FREQUENCY_GRID_HZ),
            },
            "audit_counts": [2, 4],
            "inputs": {"accepted_geometries": _evidence(accepted_path)},
            "checks": {"synthetic_history": True},
            "outputs": {key: _evidence(path) for key, path in history_paths.items()},
        },
    )
    _write_index(history_dir, recursive=False)

    training_dir = tmp_path / "training"
    training_dir.mkdir()
    training_paths = {
        "full_200k_training_weights": training_dir / "full_200k_training_weights.csv",
        "maximal_balanced_subset": training_dir / "maximal_balanced_subset.csv",
        "future_split_manifest": training_dir / "future_split_manifest.json",
        "future_split_assignments": training_dir / "future_split_assignments.csv",
    }
    _write_csv(training_paths["full_200k_training_weights"], [{"geometry_id": f"g{index:04d}", "weight": 1} for index in range(4)])
    _write_csv(training_paths["maximal_balanced_subset"], [{"geometry_id": f"g{index:04d}"} for index in range(2)])
    _write_csv(training_paths["future_split_assignments"], [{"geometry_id": f"g{index:04d}", "split": "train"} for index in range(4)])
    _write_json(
        training_paths["future_split_manifest"],
        {
            "geometry_count": 4,
            "all_56_frequency_rows_from_one_geometry_remain_in_one_split": True,
        },
    )
    training_receipt = training_dir / "TRAINING_READINESS_RECEIPT.json"
    _write_json(
        training_receipt,
        {
            "overall_status": "PASS",
            "decision": "USE_DERIVED_PRODUCTS_FOR_FUTURE_TRAINING_PREPARATION_ONLY",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "counts": {
                "accepted_geometries": 4,
                "geometry_frequency_rows": 4 * len(FREQUENCY_GRID_HZ),
            },
            "inputs": {
                "accepted_geometries": _evidence(accepted_path),
                "long_features": _evidence(features_path),
            },
            "checks": {"synthetic_training_readiness": True},
            "outputs": {key: _evidence(path) for key, path in training_paths.items()},
        },
    )
    _write_index(training_dir, recursive=False)

    figure_dir = tmp_path / "figures"
    figure_dir.mkdir()
    for count in module.FIGURE_CHECKPOINT_COUNTS:
        checkpoint = figure_dir / f"checkpoint_{count:06d}"
        checkpoint.mkdir()
        figure_rows = []
        for figure_id in module.FIGURE_IDS:
            png = checkpoint / f"{figure_id}.png"
            svg = checkpoint / f"{figure_id}.svg"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"p" * 1_100)
            svg.write_text("<svg>" + "s" * 1_100 + "</svg>", encoding="utf-8")
            figure_rows.append(
                {
                    "figure_id": figure_id,
                    "files": {
                        "png": _evidence(png, recorded_path=checkpoint / png.name),
                        "svg": _evidence(svg, recorded_path=checkpoint / svg.name),
                    },
                }
            )
        _write_json(
            checkpoint / "FIGURE_MANIFEST.json",
            {
                "accepted_geometries": count,
                "campaign_contract_fingerprint_sha256": fingerprint,
                "production_process_config_sha256": process_sha,
                "figures": figure_rows,
                "checks": {"synthetic_manifest": True},
            },
        )
    figure_receipt = figure_dir / "FIGURE_RECEIPT.json"
    _write_json(
        figure_receipt,
        {
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_STATIC_CHECKPOINT_FIGURES",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "production_process_config_sha256": process_sha,
            "checkpoint_counts": list(module.FIGURE_CHECKPOINT_COUNTS),
            "logical_figure_ids": list(module.FIGURE_IDS),
            "counts": {
                "logical_figures": 56,
                "png_files": 56,
                "svg_files": 56,
                "rendered_files": 112,
            },
            "checks": {"synthetic_figure_receipt": True},
        },
    )
    _write_index(figure_dir, recursive=True)
    return {
        "contract": contract_path,
        "raw": raw_dir,
        "checkpoint": checkpoint_dir,
        "history": history_dir,
        "training": training_dir,
        "figures": figure_dir,
        "provenance": provenance_path,
        "first_gds": evidence_dir / "design_0.gds",
    }


def _args(fixture: dict[str, Path], out_dir: Path) -> list[str]:
    return [
        "--contract",
        str(fixture["contract"]),
        "--raw-dir",
        str(fixture["raw"]),
        "--checkpoint-dir",
        str(fixture["checkpoint"]),
        "--history-dir",
        str(fixture["history"]),
        "--training-readiness-dir",
        str(fixture["training"]),
        "--figure-dir",
        str(fixture["figures"]),
        "--out-dir",
        str(out_dir),
    ]


def test_hash_closed_terminal_products_emit_complete_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_small_contract(module, monkeypatch)
    fixture = _fixture(tmp_path, module)
    out_dir = tmp_path / "delivery"

    assert module.main(_args(fixture, out_dir)) == 0
    status = json.loads((out_dir / "FINAL_COMPLETION_STATUS.json").read_text())
    receipt = json.loads((out_dir / "FINAL_DELIVERY_RECEIPT.json").read_text())
    assert status["completion_status"] == "COMPLETE_200K"
    assert status["coverage_status"] == "COVERAGE_PARTIAL"
    assert receipt["overall_status"] == "PASS"
    assert receipt["checks"]["proxy_predictions_not_counted_as_labels"] is True
    assert {path.name for path in out_dir.iterdir()} == {
        "FINAL_COMPLETION_STATUS.json",
        "FINAL_DELIVERY_RECEIPT.json",
        "SHA256SUMS.txt",
    }


def test_provenance_s4p_identity_tamper_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_small_contract(module, monkeypatch)
    fixture = _fixture(tmp_path, module)
    rows = list(csv.DictReader(fixture["provenance"].open(newline="", encoding="utf-8")))
    rows[0]["s4p_sha256"] = "f" * 64
    _write_csv(fixture["provenance"], rows)
    out_dir = tmp_path / "delivery"

    assert module.main(_args(fixture, out_dir)) == 2
    assert not out_dir.exists()


def test_figure_tamper_fails_closed_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_small_contract(module, monkeypatch)
    fixture = _fixture(tmp_path, module)
    target = (
        fixture["figures"]
        / "checkpoint_050000"
        / f"{module.FIGURE_IDS[0]}.png"
    )
    target.write_bytes(target.read_bytes() + b"tamper")
    out_dir = tmp_path / "delivery"

    assert module.main(_args(fixture, out_dir)) == 2
    assert not out_dir.exists()


def test_physical_evidence_byte_tamper_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_small_contract(module, monkeypatch)
    fixture = _fixture(tmp_path, module)
    fixture["first_gds"].write_bytes(fixture["first_gds"].read_bytes() + b"tamper")
    out_dir = tmp_path / "delivery"

    assert module.main(_args(fixture, out_dir)) == 2
    assert not out_dir.exists()


def test_existing_output_directory_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_small_contract(module, monkeypatch)
    fixture = _fixture(tmp_path, module)
    out_dir = tmp_path / "delivery"
    out_dir.mkdir()
    marker = out_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    assert module.main(_args(fixture, out_dir)) == 2
    assert marker.read_text(encoding="utf-8") == "keep"
