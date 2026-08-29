from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    canonical_geometry_sha256,
    contract_fingerprint,
    primary_cell_for_values,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _load_module():
    path = ROOT / "scripts" / "finalize_broadband56_training_readiness.py"
    spec = importlib.util.spec_from_file_location(
        "finalize_broadband56_training_readiness", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path, *, count: int = 8, state: str = "COMPLETE_200K") -> dict[str, Path]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    accepted_rows: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    cell_counts = {index: 0 for index in range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)}
    group_by_index = [0, 0, 0, 0, 1, 1, 1, 2]
    for geometry_index in range(count):
        geometry_id = f"g{geometry_index:04d}"
        geometry = {
            "primary_outer_width_um": 220.0 + geometry_index,
            "primary_outer_height_um": 221.0,
            "secondary_outer_width_um": 210.0,
            "secondary_outer_height_um": 211.0,
            "line_width_um": 8.0,
            "primary_terminal_y_span_um": 50.0,
            "secondary_terminal_y_span_um": 51.0,
            "offset_um": float(geometry_index),
            "primary_feed_extension_um": 150.0,
            "secondary_feed_extension_um": 151.0,
        }
        assert tuple(geometry) == GEOMETRY_FIELDS
        geometry_hash = canonical_geometry_sha256(geometry)
        accepted_rows.append(
            {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_hash,
                "campaign_contract_fingerprint": fingerprint,
                **{f"geom__{name}": value for name, value in geometry.items()},
            }
        )

        group = group_by_index[geometry_index]
        lp_nh = (0.34, 0.46, 0.56)[group]
        ls_nh = (0.38, 0.51, 0.61)[group]
        qmin = (6.0, 14.0, 25.0)[group]
        k_abs = (0.12, 0.42, 0.72)[group]
        for frequency_hz in FREQUENCY_GRID_HZ:
            xp = 2.0 * math.pi * frequency_hz * lp_nh * 1.0e-9
            xs = 2.0 * math.pi * frequency_hz * ls_nh * 1.0e-9
            feature_rows.append(
                {
                    "geometry_id": geometry_id,
                    "geometry_sha256": geometry_hash,
                    "campaign_contract_fingerprint": fingerprint,
                    "frequency_hz": frequency_hz,
                    "broadband_descriptor_valid": "true",
                    "xp_ohm": xp,
                    "xs_ohm": xs,
                    "qmin": qmin,
                    "k_abs": k_abs,
                }
            )
            anchor_ghz = frequency_hz // 1_000_000_000
            if anchor_ghz in ANCHOR_FREQUENCIES_GHZ:
                cell = primary_cell_for_values(
                    anchor_ghz=anchor_ghz,
                    xp_ohm=xp,
                    xs_ohm=xs,
                    qmin=qmin,
                    k_abs=k_abs,
                )
                assert cell is not None
                conditioned = (
                    ANCHOR_FREQUENCIES_GHZ.index(anchor_ghz) * PRIMARY_CELLS_PER_ANCHOR
                    + cell.local_index
                )
                cell_counts[conditioned] += 1

    accepted_path = tmp_path / "accepted.csv"
    features_path = tmp_path / "features.csv"
    _write_csv(accepted_path, accepted_rows)
    _write_csv(features_path, feature_rows)

    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    cells_path = checkpoint / "physical_coverage_cells_by_anchor.csv"
    _write_csv(
        cells_path,
        [
            {
                "conditioned_cell_index": index,
                "actual_count": cell_counts[index],
            }
            for index in range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)
        ],
    )
    status_path = checkpoint / "CHECKPOINT_STATUS.json"
    status_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "checkpoint_status": state,
                "audit_mode": "checkpoint",
                "coverage_status": "COVERAGE_PARTIAL",
                "accepted_geometries": count,
                "s4p_artifacts": count,
                "geometry_frequency_rows": count * 56,
            }
        ),
        encoding="utf-8",
    )
    coverage_path = checkpoint / "COVERAGE_SUMMARY.json"
    coverage_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "expected_accepted_geometries": count,
                "feature_row_count": count * 56,
                "coverage_status": "COVERAGE_PARTIAL",
            }
        ),
        encoding="utf-8",
    )
    receipt_path = checkpoint / "CHECKPOINT_RECEIPT.json"
    receipt_path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "USE_CHECKPOINT",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "expected_accepted": count,
                "audit_mode": "checkpoint",
                "checks": [{"name": "synthetic_terminal_fixture", "pass": True}],
                "inputs": {
                    "contract": _file_evidence(contract_path),
                    "accepted_geometries": _file_evidence(accepted_path),
                    "long_features": _file_evidence(features_path),
                },
                "outputs": {
                    "coverage_cells": _file_evidence(cells_path),
                    "checkpoint_status": _file_evidence(status_path),
                    "coverage_summary": _file_evidence(coverage_path),
                },
            }
        ),
        encoding="utf-8",
    )
    _write_sha256s(checkpoint)
    return {
        "contract": contract_path,
        "accepted": accepted_path,
        "features": features_path,
        "checkpoint": checkpoint,
    }


def test_terminal_fixture_writes_exact_derived_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(module, "TARGET_ACCEPTED_GEOMETRIES", 8)
    out_dir = tmp_path / "readiness"

    status = module.main(
        [
            "--contract",
            str(fixture["contract"]),
            "--checkpoint-dir",
            str(fixture["checkpoint"]),
            "--accepted-geometries",
            str(fixture["accepted"]),
            "--long-features",
            str(fixture["features"]),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert status == 0
    assert {
        "full_200k_training_weights.csv",
        "maximal_balanced_subset.csv",
        "future_split_manifest.json",
        "future_split_assignments.csv",
        "TRAINING_READINESS_RECEIPT.json",
        "SHA256SUMS.txt",
    } == {path.name for path in out_dir.iterdir()}
    with (out_dir / "full_200k_training_weights.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        weights = list(csv.DictReader(handle))
    assert len(weights) == 8
    numeric_weights = [float(row["training_weight"]) for row in weights]
    assert sum(numeric_weights) / len(numeric_weights) == pytest.approx(1.0)
    assert min(numeric_weights) >= 0.25
    assert max(numeric_weights) <= 4.0
    assert all(
        row["evidence_class"] == "derived_from_actual_fresh_real_emx_anchor_cells"
        for row in weights
    )

    with (out_dir / "maximal_balanced_subset.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        subset = list(csv.DictReader(handle))
    assert len(subset) == 3
    assert len({row["geometry_sha256"] for row in subset}) == 3
    assert {int(row["balanced_subset_equal_quota"]) for row in subset} == {1}

    manifest = json.loads((out_dir / "future_split_manifest.json").read_text(encoding="utf-8"))
    assert manifest["split_counts"] == {"train": 6, "validation": 1, "test": 1}
    assert manifest["all_56_frequency_rows_from_one_geometry_remain_in_one_split"] is True
    receipt = json.loads(
        (out_dir / "TRAINING_READINESS_RECEIPT.json").read_text(encoding="utf-8")
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["maximal_balanced_subset"]["does_not_claim_global_multi_anchor_hypergraph_optimum"] is True
    assert receipt["checks"]["no_final_model_training_performed"] is True
    for evidence in receipt["outputs"].values():
        assert Path(evidence["path"]).is_file()
        assert _sha256(Path(evidence["path"])) == evidence["sha256"]
    assert Path(manifest["assignment_artifact"]["path"]).is_file()


def test_tampered_bound_input_fails_without_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(module, "TARGET_ACCEPTED_GEOMETRIES", 8)
    fixture["accepted"].write_text(
        fixture["accepted"].read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    out_dir = tmp_path / "must_not_exist"

    status = module.main(
        [
            "--contract",
            str(fixture["contract"]),
            "--checkpoint-dir",
            str(fixture["checkpoint"]),
            "--accepted-geometries",
            str(fixture["accepted"]),
            "--long-features",
            str(fixture["features"]),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert status == 2
    assert not out_dir.exists()


def test_nonterminal_checkpoint_fails_without_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    fixture = _fixture(tmp_path, state="CHECKPOINT_COMPLETE")
    monkeypatch.setattr(module, "TARGET_ACCEPTED_GEOMETRIES", 8)
    out_dir = tmp_path / "must_not_exist"

    status = module.main(
        [
            "--contract",
            str(fixture["contract"]),
            "--checkpoint-dir",
            str(fixture["checkpoint"]),
            "--accepted-geometries",
            str(fixture["accepted"]),
            "--long-features",
            str(fixture["features"]),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert status == 2
    assert not out_dir.exists()


def test_clipped_weight_normalization_is_positive_bounded_and_mean_one() -> None:
    module = _load_module()
    values = [0.0001, 0.1, 1.0, 10.0, 10000.0]
    weights = module._mean_one_clipped_weights(values, low=0.25, high=4.0)
    assert min(weights) >= 0.25
    assert max(weights) <= 4.0
    assert sum(weights) / len(weights) == pytest.approx(1.0)


def test_split_counts_use_deterministic_largest_remainder() -> None:
    module = _load_module()
    assert module._largest_remainder_counts(8, module.FUTURE_SPLIT_RATIOS) == {
        "train": 6,
        "validation": 1,
        "test": 1,
    }
    assert module._largest_remainder_counts(200_000, module.FUTURE_SPLIT_RATIOS) == {
        "train": 160_000,
        "validation": 20_000,
        "test": 20_000,
    }
