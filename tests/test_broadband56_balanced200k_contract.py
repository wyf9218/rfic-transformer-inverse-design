from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS,
    ADAPTIVE_ROUND_END_COUNTS,
    ADAPTIVE_ROUND_START_COUNTS,
    ANCHOR_FREQUENCIES_GHZ,
    EXPECTED_FEATURE_ROWS,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    SECONDARY_FEATURES,
    TARGET_ACCEPTED_GEOMETRIES,
    adaptive_round_for_current_accepted,
    adaptive_round_spec,
    build_phase_plan,
    canonical_geometry_sha256,
    contract_fingerprint,
    frozen_checkpoint_start,
    matrix_columns,
    next_frozen_accepted_boundary,
    occupancy_metrics,
    primary_bin_edges,
    primary_cell_for_values,
    prorate_adaptive_source_quotas,
    secondary_coverage_contract,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_coverage import (
    BIN_CLASS_COUNT,
    StreamingPhysicalCoverage,
    extended_bin_index,
    population_memberships,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (
    GeometryCoverageAudit,
    geometry_bounds_payload,
)
from rfic_transformer_inverse_design.campaigns.broadband56_adaptive_selection import (
    selection_policy_contract,
)
from rfic_transformer_inverse_design.sim.base import SParameterResult


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"
TEMPLATE = ROOT / "configs" / "mars_s4p_grounded_powerline_broadband56_balanced200k_v2_template.yaml"
RECONSTRUCTED_CANDIDATE = (
    ROOT / "docs" / "research" / "BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_20260829.json"
)


def _load_audit_module():
    path = ROOT / "scripts" / "audit_broadband56_balanced200k_checkpoint.py"
    spec = importlib.util.spec_from_file_location("audit_broadband56_balanced200k_checkpoint", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_queue_module():
    path = ROOT / "scripts" / "build_broadband56_phase_a_queue.py"
    spec = importlib.util.spec_from_file_location("build_broadband56_phase_a_queue", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_prepare_module():
    path = ROOT / "scripts" / "prepare_broadband56_balanced200k_campaign.py"
    spec = importlib.util.spec_from_file_location("prepare_broadband56_balanced200k_campaign", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_resource_estimator_module():
    path = ROOT / "scripts" / "estimate_broadband56_balanced200k_resources.py"
    spec = importlib.util.spec_from_file_location("estimate_broadband56_balanced200k_resources", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_adaptive_round_module():
    path = ROOT / "scripts" / "stage_broadband56_adaptive_round.py"
    spec = importlib.util.spec_from_file_location("stage_broadband56_adaptive_round", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_adaptive_audit_fixture(root: Path, *, accepted_count: int, fingerprint: str) -> Path:
    audit_dir = root / f"audit_{accepted_count}"
    audit_dir.mkdir()
    accepted_path = audit_dir / "accepted_geometries.csv"
    accepted_path.write_text("geometry_id,fixture_count\nfixture,%d\n" % accepted_count, encoding="utf-8")
    bounds_path = audit_dir / "GEOMETRY_BOUNDS_FROZEN.json"
    bounds_path.write_text(
        json.dumps(
            geometry_bounds_payload(
                bounds=_test_geometry_bounds(),
                contract_fingerprint_sha256=fingerprint,
            )
        ),
        encoding="utf-8",
    )
    features_path = audit_dir / "broadband_features_long.csv"
    features_path.write_text("geometry_id,frequency_hz\nfixture,8000000000\n", encoding="utf-8")
    cells_path = audit_dir / "physical_coverage_cells_by_anchor.csv"
    with cells_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "anchor_ghz",
            "local_cell_index",
            "conditioned_cell_index",
            "actual_count",
            "target_count",
            "deficit",
            "cell_status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        target = accepted_count / 1_296.0
        for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ):
            for local in range(1_296):
                writer.writerow(
                    {
                        "anchor_ghz": anchor,
                        "local_cell_index": local,
                        "conditioned_cell_index": anchor_index * 1_296 + local,
                        "actual_count": 0,
                        "target_count": target,
                        "deficit": target,
                        "cell_status": "unobserved_under_current_geometry_contract",
                    }
                )
    coverage_path = audit_dir / "COVERAGE_SUMMARY.json"
    coverage_path.write_text(json.dumps({"coverage_status": "COVERAGE_PARTIAL"}), encoding="utf-8")
    mode = "checkpoint" if accepted_count in (50_000, 75_000, 100_000, 125_000, 150_000, 175_000) else "round"
    state = "CHECKPOINT_COMPLETE" if mode == "checkpoint" else f"ROUND_{accepted_count}_COMPLETE"
    status_path = audit_dir / "CHECKPOINT_STATUS.json"
    status_path.write_text(
        json.dumps(
            {
                "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
                "contract_fingerprint_sha256": fingerprint,
                "checkpoint_status": state,
                "audit_mode": mode,
                "coverage_status": "COVERAGE_PARTIAL",
                "accepted_geometries": accepted_count,
                "s4p_artifacts": accepted_count,
                "geometry_frequency_rows": accepted_count * 56,
            }
        ),
        encoding="utf-8",
    )
    receipt_path = audit_dir / "CHECKPOINT_RECEIPT.json"
    receipt_path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "USE_CHECKPOINT",
                "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
                "contract_fingerprint_sha256": fingerprint,
                "expected_accepted": accepted_count,
                "audit_mode": mode,
                "checks": [{"name": "fixture", "pass": True}],
                "inputs": {
                    "accepted_geometries": {"path": str(accepted_path.resolve()), "sha256": _sha256(accepted_path)},
                    "geometry_bounds": {"path": str(bounds_path.resolve()), "sha256": _sha256(bounds_path)},
                    "long_features": {"path": str(features_path.resolve()), "sha256": _sha256(features_path)},
                },
                "outputs": {
                    "checkpoint_status": {"path": str(status_path.resolve()), "sha256": _sha256(status_path)},
                    "coverage_cells": {"path": str(cells_path.resolve()), "sha256": _sha256(cells_path)},
                    "coverage_summary": {"path": str(coverage_path.resolve()), "sha256": _sha256(coverage_path)},
                },
            }
        ),
        encoding="utf-8",
    )
    return audit_dir


def _write_ensemble_fixture(
    root: Path,
    *,
    fingerprint: str,
    audit_dir: Path,
    accepted_count: int,
    duplicate_seed: bool = False,
) -> Path:
    training = root / "ensemble_training_rows.csv"
    training.write_text("geometry_id\ng0\n", encoding="utf-8")
    members = []
    for index in range(5):
        model = root / f"ensemble_member_{index}.bin"
        model.write_bytes(f"member-{index}".encode("ascii"))
        members.append(
            {
                "seed": 100 if duplicate_seed else 100 + index,
                "model_sha256": _sha256(model),
                "model_file": {"path": str(model.resolve()), "sha256": _sha256(model)},
                "training_geometry_count": int(accepted_count * 0.8),
            }
        )
    accepted_path = audit_dir / "accepted_geometries.csv"
    features_path = audit_dir / "broadband_features_long.csv"
    bounds_path = audit_dir / "GEOMETRY_BOUNDS_FROZEN.json"
    checkpoint_receipt_path = audit_dir / "CHECKPOINT_RECEIPT.json"
    receipt = root / ("ensemble_duplicate_seed.json" if duplicate_seed else "ensemble_pass.json")
    receipt.write_text(
        json.dumps(
            {
                "schema": "broadband56_acquisition_ensemble_receipt_v1",
                "overall_status": "PASS",
                "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
                "campaign_contract_fingerprint": fingerprint,
                "training_label_source": "FRESH_REAL_EMX_ONLY",
                "split_unit": "canonical_geometry_sha256",
                "validation_sealed": True,
                "validation_used_for_training": False,
                "validation_used_for_uncertainty_calibration": False,
                "validation_status": "PASS",
                "uncertainty_calibration_status": "PASS",
                "candidate_priority_only": True,
                "predictions_are_final_labels": False,
                "anchor_frequencies_ghz": list(ANCHOR_FREQUENCIES_GHZ),
                "predicted_features": [
                    "xp_ohm",
                    "xs_ohm",
                    "qp",
                    "qs",
                    "qmin",
                    "k_abs",
                    "feature_validity_probability",
                ],
                "member_count": 5,
                "members": members,
                "source_accepted_count": accepted_count,
                "training_geometry_count": int(accepted_count * 0.8),
                "calibration_geometry_count": int(accepted_count * 0.1),
                "validation_geometry_count": accepted_count - int(accepted_count * 0.8) - int(accepted_count * 0.1),
                "split_identity_sha256": {
                    "train": "a" * 64,
                    "calibration": "b" * 64,
                    "validation": "c" * 64,
                },
                "source_checkpoint_receipt": {
                    "path": str(checkpoint_receipt_path.resolve()),
                    "sha256": _sha256(checkpoint_receipt_path),
                },
                "source_accepted_geometries": {
                    "path": str(accepted_path.resolve()),
                    "sha256": _sha256(accepted_path),
                },
                "source_long_features": {
                    "path": str(features_path.resolve()),
                    "sha256": _sha256(features_path),
                },
                "source_geometry_bounds": {
                    "path": str(bounds_path.resolve()),
                    "sha256": _sha256(bounds_path),
                },
                "uncertainty_calibration": {
                    "feature_scales": {
                        "xp_ohm": 1.0,
                        "xs_ohm": 1.0,
                        "qp": 1.0,
                        "qs": 1.0,
                        "qmin": 1.0,
                        "k_abs": 1.0,
                        "feature_validity_probability": 1.0,
                    }
                },
                "validation": {
                    "overall_status": "PASS",
                    "gates": {"fixture_gate": True},
                },
                "training_table": {"path": str(training.resolve()), "sha256": _sha256(training)},
            }
        ),
        encoding="utf-8",
    )
    return receipt


def _write_resource_pilot_fixture(
    root: Path,
    *,
    count: int,
    fingerprint: str,
    elapsed_seconds: float,
    active_worker_seconds: float,
    reused_shards: int = 0,
) -> tuple[Path, Path]:
    run_summary_path = root / f"pilot_{count}_parallel_summary.json"
    shard_count = 4 if count == 32 else 48
    run_summary_path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "PARALLEL_CANDIDATE_QUEUE_DATASET_READY",
                "input_row_count": count,
                "merged_row_count": count,
                "fail_shard_count": 0,
                "shard_count": shard_count,
                "pending_shard_count": shard_count - reused_shards,
                "reused_shard_count": reused_shards,
                "elapsed_seconds": elapsed_seconds,
                "active_worker_elapsed_seconds_sum": active_worker_seconds,
                "rows_per_second_effective": count / elapsed_seconds,
                "parallel_efficiency": active_worker_seconds / elapsed_seconds / shard_count,
                "jobs_requested": shard_count,
                "run_emx": True,
                "create_only": False,
                "campaign_identity": {
                    "input_campaign_contract_fingerprints": [fingerprint],
                    "merged_campaign_contract_fingerprints": [fingerprint],
                    "input_geometry_sha256_present_count": count,
                    "input_geometry_sha256_unique_count": count,
                    "merged_geometry_sha256_present_count": count,
                    "merged_geometry_sha256_unique_count": count,
                    "geometry_sha256_sets_match": True,
                },
                "touchstone_output_contract": {
                    "checked": True,
                    "expected_extension": ".s4p",
                    "expected_ports": 4,
                    "parse_error_count": 0,
                    "port_error_count": 0,
                    "frequency_error_count": 0,
                    "expected_frequency": {
                        "start_ghz": 5.0,
                        "stop_ghz": 60.0,
                        "step_ghz": 1.0,
                        "points": 56,
                    },
                },
                "checks": [{"name": "fixture", "pass": True, "detail": "synthetic software test only"}],
            }
        ),
        encoding="utf-8",
    )
    audit_dir = root / f"pilot_{count}_audit"
    audit_dir.mkdir()
    status_path = audit_dir / "CHECKPOINT_STATUS.json"
    status_path.write_text(
        json.dumps(
            {
                "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
                "contract_fingerprint_sha256": fingerprint,
                "checkpoint_status": f"PILOT_{count}_COMPLETE",
                "audit_mode": "pilot",
                "accepted_geometries": count,
                "s4p_artifacts": count,
                "geometry_frequency_rows": count * 56,
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "CHECKPOINT_RECEIPT.json").write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "USE_CHECKPOINT",
                "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
                "contract_fingerprint_sha256": fingerprint,
                "expected_accepted": count,
                "audit_mode": "pilot",
                "checks": [{"name": "fixture", "pass": True, "detail": "synthetic software test only"}],
                "outputs": {
                    "checkpoint_status": {
                        "path": str(status_path.resolve()),
                        "sha256": _sha256(status_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return run_summary_path, audit_dir


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    names = fieldnames or list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def _test_geometry_bounds() -> dict[str, tuple[float, float]]:
    return {
        "primary_outer_width_um": (0.0, 600.0),
        "primary_outer_height_um": (0.0, 600.0),
        "secondary_outer_width_um": (0.0, 600.0),
        "secondary_outer_height_um": (0.0, 600.0),
        "line_width_um": (0.0, 20.0),
        "primary_terminal_y_span_um": (0.0, 120.0),
        "secondary_terminal_y_span_um": (0.0, 120.0),
        "offset_um": (-100.0, 100.0),
        "primary_feed_extension_um": (0.0, 400.0),
        "secondary_feed_extension_um": (0.0, 400.0),
    }


def test_frozen_contract_has_exact_counts_grid_bins_and_phase_mixtures() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert validate_contract(contract) == []
    assert TARGET_ACCEPTED_GEOMETRIES == 200_000
    assert len(FREQUENCY_GRID_HZ) == 56
    assert EXPECTED_FEATURE_ROWS == 11_200_000
    assert PRIMARY_CELLS_PER_ANCHOR == 1_296
    assert PRIMARY_FREQUENCY_CONDITIONED_CELLS == 10_368
    assert tuple(contract["primary_uniformity"]["anchors_ghz"]) == ANCHOR_FREQUENCIES_GHZ
    assert contract["secondary_coverage"] == secondary_coverage_contract()

    edges = primary_bin_edges()
    assert len(edges["xp_ohm"]) == 7
    assert edges["xp_ohm"][0] == 10.0
    assert edges["xp_ohm"][-1] == 250.0
    assert all(math.isclose(edges["xp_ohm"][i], 10.0 * 25.0 ** (i / 6.0)) for i in range(7))

    plan = build_phase_plan()
    assert plan["phase_b"]["round_count"] == 20
    assert plan["phase_b"]["mixture_fractions"] == {
        "underfilled_response_repair": 0.60,
        "ensemble_uncertainty": 0.20,
        "maximin_geometry_exploration": 0.20,
    }
    assert plan["phase_c"]["round_count"] == 10
    assert plan["phase_c"]["mixture_fractions"]["maximin_geometry_exploration"] == 0.15

    assert len(ADAPTIVE_ROUND_START_COUNTS) == 30
    assert ADAPTIVE_ROUND_START_COUNTS[0] == 50_000
    assert ADAPTIVE_ROUND_START_COUNTS[-1] == 195_000
    assert ADAPTIVE_ROUND_END_COUNTS[-1] == 200_000
    assert not set(ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS).intersection(plan["checkpoints"])

    first_b = adaptive_round_spec(50_000)
    assert first_b.phase == "PHASE_B"
    assert first_b.phase_round_index == 1
    assert first_b.accepted_target == 55_000
    assert dict(first_b.source_quotas) == {
        "underfilled_response_repair": 3_000,
        "ensemble_uncertainty": 1_000,
        "maximin_geometry_exploration": 1_000,
    }
    last_c = adaptive_round_spec(195_000)
    assert last_c.phase == "PHASE_C"
    assert last_c.phase_round_index == 10
    assert last_c.accepted_target == 200_000
    assert dict(last_c.source_quotas) == {
        "rare_or_underfilled_response_repair": 3_250,
        "ensemble_uncertainty": 1_000,
        "maximin_geometry_exploration": 750,
    }
    assert dict(last_c.fallback_source_quotas) == {"maximin_geometry_exploration": 5_000}


def test_adaptive_replenishment_closes_exact_boundary_and_prorates_sources() -> None:
    round_spec, remaining = adaptive_round_for_current_accepted(54_900)

    assert round_spec.accepted_start == 50_000
    assert round_spec.accepted_target == 55_000
    assert remaining == 100
    assert dict(prorate_adaptive_source_quotas(round_spec.source_quotas, remaining)) == {
        "underfilled_response_repair": 60,
        "ensemble_uncertainty": 20,
        "maximin_geometry_exploration": 20,
    }
    assert dict(
        prorate_adaptive_source_quotas(round_spec.fallback_source_quotas, remaining)
    ) == {"maximin_geometry_exploration": 100}


def test_adaptive_replenishment_uses_deterministic_largest_remainders() -> None:
    assert prorate_adaptive_source_quotas(
        (("first", 3), ("second", 1), ("third", 1)),
        2,
    ) == (("first", 1), ("second", 1))

    with np.testing.assert_raises(ValueError):
        adaptive_round_for_current_accepted(200_000)


def test_frozen_boundaries_prevent_nonadaptive_checkpoint_skips() -> None:
    assert next_frozen_accepted_boundary(32, cumulative_target=1_000) == 100
    assert next_frozen_accepted_boundary(99, cumulative_target=1_000) == 100
    assert next_frozen_accepted_boundary(100, cumulative_target=1_000) == 1_000
    assert next_frozen_accepted_boundary(1_000, cumulative_target=50_000) == 5_000
    assert next_frozen_accepted_boundary(4_999, cumulative_target=50_000) == 5_000
    assert next_frozen_accepted_boundary(5_000, cumulative_target=50_000) == 20_000
    assert next_frozen_accepted_boundary(50_000, cumulative_target=150_000) == 55_000
    assert next_frozen_accepted_boundary(54_900, cumulative_target=150_000) == 55_000

    assert frozen_checkpoint_start(
        32,
        stage_base_accepted=32,
        cumulative_target=1_000,
    ) == 32
    assert frozen_checkpoint_start(
        99,
        stage_base_accepted=32,
        cumulative_target=1_000,
    ) == 32
    assert frozen_checkpoint_start(
        100,
        stage_base_accepted=32,
        cumulative_target=1_000,
    ) == 100
    assert frozen_checkpoint_start(
        19_999,
        stage_base_accepted=1_000,
        cumulative_target=50_000,
    ) == 5_000


def test_geometry_identity_is_ordered_quantized_and_primary_bins_include_upper_edges() -> None:
    geometry = {name: float(index + 1) for index, name in enumerate(GEOMETRY_FIELDS)}
    reordered = dict(reversed(list(geometry.items())))
    assert canonical_geometry_sha256(geometry) == canonical_geometry_sha256(reordered)

    below_quantization = dict(geometry)
    below_quantization[GEOMETRY_FIELDS[0]] += 4.0e-10
    assert canonical_geometry_sha256(geometry) == canonical_geometry_sha256(below_quantization)

    cell = primary_cell_for_values(anchor_ghz=15, xp_ohm=250.0, xs_ohm=250.0, qmin=35.0, k_abs=0.85)
    assert cell is not None
    assert (cell.xp_bin, cell.xs_bin, cell.qmin_bin, cell.k_abs_bin) == (5, 5, 5, 5)
    assert cell.local_index == 1_295


def test_uniformity_metrics_keep_empty_cells_visible() -> None:
    metrics = occupancy_metrics([4, 0, 0, 0], accepted_count=4)

    assert metrics["observed_cells"] == 1
    assert metrics["observed_cell_fraction"] == 0.25
    assert metrics["underfilled_cells"] == 3
    assert metrics["normalized_entropy"] == 0.0
    assert metrics["gini_coefficient"] == 0.75


def test_secondary_coverage_retains_underflow_overflow_and_separates_phases() -> None:
    contract = secondary_coverage_contract()
    edges = contract["bin_edges"]
    assert BIN_CLASS_COUNT == 8
    assert extended_bin_index(0.01, edges["lp_nh"]) == 0
    assert extended_bin_index(8.0, edges["lp_nh"]) == 6
    assert extended_bin_index(9.0, edges["lp_nh"]) == 7

    coverage = StreamingPhysicalCoverage()
    values = {name: 1.0 for name in SECONDARY_FEATURES}
    values.update({"xp_ohm": 20.0, "xs_ohm": 25.0, "qp": 10.0, "qs": 11.0, "qmin": 10.0, "k_abs": 0.4})
    coverage.add_record(
        frequency_hz=8_000_000_000,
        values=values,
        populations=population_memberships(
            broadband_descriptor_valid=True,
            strict_lumped_valid=False,
            inside_broad_response_envelope=True,
            inside_literature_practical_panel=True,
        ),
        campaign_phase="PHASE_A",
    )

    assert coverage.internal_errors() == []
    summary = coverage.summary()
    all_parseable = next(
        row for row in summary["groups"]
        if row["population"] == "all_parseable_emx_records" and row["campaign_phase"] == "ALL"
    )
    phase_a = next(
        row for row in summary["groups"]
        if row["population"] == "all_parseable_emx_records" and row["campaign_phase"] == "PHASE_A"
    )
    strict = next(
        row for row in summary["groups"]
        if row["population"] == "strict_lumped_valid" and row["campaign_phase"] == "ALL"
    )
    assert all_parseable["geometry_frequency_records"] == 1
    assert phase_a["geometry_frequency_records"] == 1
    assert strict["geometry_frequency_records"] == 0
    primary = coverage.primary_summary()
    primary_all = next(
        row for row in primary["groups"]
        if row["population"] == "all_parseable_emx_records" and row["campaign_phase"] == "ALL"
    )
    assert primary_all["anchor_record_count"] == 1
    assert primary_all["in_primary_cells"] == 1


def test_geometry_coverage_uses_frozen_bounds_and_geometry_unique_counts() -> None:
    unit = np.linspace(0.05, 0.95, 20, dtype=float).reshape(2, 10)
    bounds = _test_geometry_bounds()
    lower = np.asarray([bounds[name][0] for name in GEOMETRY_FIELDS], dtype=float)
    upper = np.asarray([bounds[name][1] for name in GEOMETRY_FIELDS], dtype=float)
    matrix = lower + unit * (upper - lower)
    audit = GeometryCoverageAudit(
        matrix_um=matrix,
        bounds=bounds,
        geometry_hashes=("a" * 64, "b" * 64),
    )

    assert audit.internal_errors() == []
    summary = audit.summary()
    assert summary["geometry_count"] == 2
    assert summary["canonical_hash_unique_count"] == 2
    assert summary["nearest_neighbor_distance"]["status"] == "PASS"
    assert len(audit.marginal_rows()) == 10
    assert len(audit.pairwise_rows()) == 45


def test_golden_pilot_round_and_checkpoint_targets_are_disjoint_and_frozen() -> None:
    module = _load_audit_module()

    assert module._audit_target_allowed("golden", 1)[0]
    assert not module._audit_target_allowed("golden", 32)[0]
    assert module._audit_target_allowed("pilot", 32)[0]
    assert module._audit_target_allowed("pilot", 1_000)[0]
    assert not module._audit_target_allowed("pilot", 100)[0]
    assert module._audit_target_allowed("round", 55_000)[0]
    assert module._audit_target_allowed("round", 195_000)[0]
    assert not module._audit_target_allowed("round", 50_000)[0]
    assert not module._audit_target_allowed("round", 75_000)[0]
    assert module._audit_target_allowed("checkpoint", 100)[0]
    assert module._audit_target_allowed("checkpoint", 200_000)[0]
    assert module._successful_audit_state("golden", 1, False) == "GOLDEN_COMPLETE"
    assert module._successful_audit_state("pilot", 32, False) == "PILOT_32_COMPLETE"
    assert module._successful_audit_state("round", 55_000, False) == "ROUND_55000_COMPLETE"
    assert module._successful_audit_state("checkpoint", 200_000, True) == "COMPLETE_200K"


def test_synthetic_golden_audit_writes_golden_complete_without_claiming_physical_files(
    tmp_path: Path,
) -> None:
    module = _load_audit_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = module.contract_fingerprint(contract)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    bounds_path = tmp_path / "bounds.json"
    bounds_path.write_text(
        json.dumps(
            geometry_bounds_payload(
                bounds=_test_geometry_bounds(),
                contract_fingerprint_sha256=fingerprint,
            )
        ),
        encoding="utf-8",
    )
    geometry = {
        "primary_outer_width_um": 220.0,
        "primary_outer_height_um": 221.0,
        "secondary_outer_width_um": 210.0,
        "secondary_outer_height_um": 211.0,
        "line_width_um": 8.0,
        "primary_terminal_y_span_um": 50.0,
        "secondary_terminal_y_span_um": 51.0,
        "offset_um": 0.0,
        "primary_feed_extension_um": 150.0,
        "secondary_feed_extension_um": 151.0,
    }
    geometry_hash = canonical_geometry_sha256(geometry)
    accepted: dict[str, object] = {
        "geometry_id": "golden_000001",
        "geometry_sha256": geometry_hash,
        "campaign_contract_fingerprint": fingerprint,
        "accepted_sequence": 1,
        "campaign_phase": "PHASE_A",
        "acquisition_source": "base_space_filling",
        "calibre_blocking_violations": 0,
    }
    accepted.update({field: "PASS" for field in module.ACCEPTANCE_STATUS_FIELDS})
    accepted.update({f"geom__{name}": value for name, value in geometry.items()})
    accepted_path = tmp_path / "accepted.csv"
    _write_csv(accepted_path, [accepted])
    artifact_path = tmp_path / "artifacts.csv"
    _write_csv(
        artifact_path,
        [
            {
                "geometry_id": "golden_000001",
                "geometry_sha256": geometry_hash,
                "campaign_contract_fingerprint": fingerprint,
                "s4p_path": str(tmp_path / "synthetic_not_created.s4p"),
                "s4p_sha256": "0" * 64,
                "frequency_points": 56,
                "emx_status": "PASS",
                "calibre_status": "PASS",
                "calibre_blocking_violations": 0,
            }
        ],
    )
    features = []
    for frequency_hz in FREQUENCY_GRID_HZ:
        lp_nh = 0.5
        ls_nh = 0.5
        features.append(
            {
                "geometry_id": "golden_000001",
                "geometry_sha256": geometry_hash,
                "campaign_contract_fingerprint": fingerprint,
                "frequency_hz": frequency_hz,
                "lp_nh": lp_nh,
                "ls_nh": ls_nh,
                "qp": 12.0,
                "qs": 13.0,
                "qmin": 12.0,
                "mutual_inductance_h": 0.1e-9,
                "signed_k": 0.2,
                "k_abs": 0.2,
                "ls_over_lp": 1.0,
                "xp_ohm": 2.0 * math.pi * frequency_hz * lp_nh * 1.0e-9,
                "xs_ohm": 2.0 * math.pi * frequency_hz * ls_nh * 1.0e-9,
                "broadband_descriptor_valid": "true",
                "strict_lumped_valid": "true",
                "srf_status": "CENSORED_ABOVE_60_GHZ",
                "passivity_status": "PASS",
                "reciprocity_status": "PASS",
                "inside_broad_response_envelope": "true",
                "inside_literature_practical_panel": "true",
                "outside_envelope_reason": "",
            }
        )
    features_path = tmp_path / "features.csv"
    _write_csv(features_path, features)
    funnel_path = tmp_path / "funnel.csv"
    _write_csv(
        funnel_path,
        [
            {"stage": stage, "count": 1 if stage in {"raw_geometry_candidates", "accepted_geometries"} else 0}
            for stage in (
                "raw_geometry_candidates",
                "analytical_failures",
                "topology_failures",
                "cadence_failures",
                "calibre_failures",
                "emx_failures",
                "incomplete_frequency_failures",
                "s4p_parsing_failures",
                "feature_extraction_failures",
                "accepted_geometries",
            )
        ],
    )
    out_dir = tmp_path / "golden_audit"

    status = module.main(
        [
            "--contract", str(contract_path),
            "--geometry-bounds", str(bounds_path),
            "--accepted-geometries", str(accepted_path),
            "--long-features", str(features_path),
            "--artifact-index", str(artifact_path),
            "--failure-funnel", str(funnel_path),
            "--audit-mode", "golden",
            "--expected-accepted", "1",
            "--allow-missing-matrix-columns",
            "--skip-s4p-file-hash-check",
            "--out-dir", str(out_dir),
        ]
    )

    receipt = json.loads((out_dir / "CHECKPOINT_RECEIPT.json").read_text(encoding="utf-8"))
    checkpoint_status = json.loads((out_dir / "CHECKPOINT_STATUS.json").read_text(encoding="utf-8"))
    assert status == 0
    assert receipt["overall_status"] == "PASS"
    assert receipt["audit_mode"] == "golden"
    assert checkpoint_status["checkpoint_status"] == "GOLDEN_COMPLETE"
    assert checkpoint_status["accepted_geometries"] == 1
    assert checkpoint_status["geometry_frequency_rows"] == 56


def test_phase_a_queue_is_exact_10d_unique_and_label_free(tmp_path: Path) -> None:
    module = _load_queue_module()
    out_dir = tmp_path / "queue"

    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--config", str(TEMPLATE),
            "--out-dir", str(out_dir),
            "--count", "32",
            "--sampler", "sobol",
            "--seed", "20260828",
        ]
    )

    with (out_dir / "broadband56_candidate_queue.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads((out_dir / "broadband56_candidate_queue_summary.json").read_text(encoding="utf-8"))
    assert status == 0
    assert summary["overall_status"] == "PASS"
    assert len(rows) == 32
    assert len({row["geometry_sha256"] for row in rows}) == 32
    assert {name.removeprefix("geom__") for name in rows[0] if name.startswith("geom__")} == set(GEOMETRY_FIELDS)
    assert not any(name.startswith(("pred_", "metrics__", "objective__")) for name in rows[0])
    for row in rows:
        geometry = {name: row[f"geom__{name}"] for name in GEOMETRY_FIELDS}
        assert row["geometry_sha256"] == canonical_geometry_sha256(geometry)
        assert row["candidate_id_sha256"] == row["geometry_sha256"]
        assert row["candidate_geometry_identity_sha256"] == row["geometry_sha256"]
        assert row["candidate_identity_schema"] == "canonical_10d_geometry_sha256_alias_v1"
        assert row["analytical_status"] == "PASS"
        assert row["topology_status"] == "PASS"
        assert row["top_metal_drc_status"] == "PASS"
        assert row["candidate_generation_mode"] == "sobol_normalized_10d"


def test_phase_a_queue_rejects_non_phase_a_source_contract(tmp_path: Path) -> None:
    module = _load_queue_module()
    out_dir = tmp_path / "wrong_phase_queue"

    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--config", str(TEMPLATE),
            "--out-dir", str(out_dir),
            "--count", "1",
            "--sampler", "sobol",
            "--seed", "20260828",
            "--phase", "PHASE_B",
            "--acquisition-source", "maximin_geometry_exploration",
        ]
    )

    summary = json.loads((out_dir / "broadband56_candidate_queue_summary.json").read_text(encoding="utf-8"))
    assert status == 2
    assert summary["overall_status"] == "FAIL"
    failed = {item["name"] for item in summary["checks"] if not item["pass"]}
    assert failed == {"phase_is_frozen_phase_a", "acquisition_source_is_base_space_filling"}


def test_phase_a_production_queue_excludes_prior_campaign_geometry(tmp_path: Path) -> None:
    module = _load_queue_module()
    seed_out = tmp_path / "seed_queue"
    assert module.main(
        [
            "--contract", str(CONTRACT),
            "--config", str(TEMPLATE),
            "--out-dir", str(seed_out),
            "--count", "1",
            "--sampler", "sobol",
            "--seed", "20260828",
        ]
    ) == 0
    with (seed_out / "broadband56_candidate_queue.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        prior_geometry = next(csv.DictReader(handle))["geometry_sha256"]

    campaign_root = tmp_path / "campaign"
    stage_dir = campaign_root / "stages" / "000001_golden" / "backend" / "raw"
    stage_dir.mkdir(parents=True)
    accepted_path = stage_dir / "accepted.csv"
    rejected_path = stage_dir / "rejected.csv"
    _write_csv(accepted_path, [{"geometry_sha256": prior_geometry}])
    _write_csv(rejected_path, [], fieldnames=["geometry_sha256"])
    receipt = {
        "overall_status": "PASS",
        "artifacts": {
            "accepted_geometry_index": {
                "path": str(accepted_path.resolve()),
                "size_bytes": accepted_path.stat().st_size,
                "sha256": _sha256(accepted_path),
            },
            "rejected_geometry_index": {
                "path": str(rejected_path.resolve()),
                "size_bytes": rejected_path.stat().st_size,
                "sha256": _sha256(rejected_path),
            },
        },
    }
    receipt_path = campaign_root / "stages" / "000001_golden" / "STAGE_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")

    out_dir = tmp_path / "pilot_queue"
    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--config", str(TEMPLATE),
            "--out-dir", str(out_dir),
            "--count", "31",
            "--sampler", "sobol",
            "--seed", "20260828",
            "--campaign-root", str(campaign_root),
            "--stage", "PILOT_32",
            "--current-accepted", "1",
        ]
    )

    with (out_dir / "broadband56_candidate_queue.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads(
        (out_dir / "broadband56_candidate_queue_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert status == 0
    assert summary["overall_status"] == "PASS"
    assert summary["excluded_prior_geometry_count"] == 1
    assert len(rows) == 31
    assert prior_geometry not in {row["geometry_sha256"] for row in rows}


def test_checkpoint_acceptance_rejects_wrong_sequence_phase_and_source(tmp_path: Path) -> None:
    module = _load_audit_module()
    fingerprint = "f" * 64
    rows: list[dict[str, object]] = []
    for index, sequence in enumerate((2, 1)):
        geometry = {name: float(position + 1 + index) for position, name in enumerate(GEOMETRY_FIELDS)}
        row: dict[str, object] = {
            "geometry_id": f"g{index}",
            "geometry_sha256": canonical_geometry_sha256(geometry),
            "campaign_contract_fingerprint": fingerprint,
            "accepted_sequence": sequence,
            "campaign_phase": "PHASE_B" if index == 0 else "PHASE_A",
            "acquisition_source": "not_a_frozen_source" if index == 0 else "base_space_filling",
            "calibre_blocking_violations": 0,
        }
        row.update({field: "PASS" for field in module.ACCEPTANCE_STATUS_FIELDS})
        row.update({f"geom__{name}": value for name, value in geometry.items()})
        rows.append(row)
    path = tmp_path / "accepted.csv"
    _write_csv(path, rows)
    checks: list[dict[str, object]] = []

    module._audit_accepted_geometries(
        path,
        fingerprint,
        2,
        checks,
        geometry_bounds=_test_geometry_bounds(),
    )

    by_name = {str(item["name"]): item for item in checks}
    assert not by_name["accepted_sequence_exact_contiguous_order"]["pass"]
    assert not by_name["accepted_geometry_contract_and_gates"]["pass"]


def _write_preparation_config_fixture(tmp_path: Path) -> tuple[Path, Path]:
    emx = tmp_path / "emx"
    proc = tmp_path / "process.proc"
    cadence = tmp_path / "cadence"
    cds_lib = tmp_path / "cds.lib"
    layer_map = tmp_path / "layers.map"
    emx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(emx, 0o755)
    proc.write_text("test proc\n", encoding="utf-8")
    cadence.mkdir()
    cds_lib.write_text("DEFINE test .\n", encoding="utf-8")
    layer_map.write_text("test layer map\n", encoding="utf-8")

    config_text = TEMPLATE.read_text(encoding="utf-8")
    config_text = config_text.replace("/REPLACE/WITH/APPROVED/PRIVATE/EMX/BINARY", str(emx))
    config_text = config_text.replace("/REPLACE/WITH/APPROVED/PRIVATE/TSMC65.proc", str(proc))
    config_text = config_text.replace("/REPLACE/WITH/APPROVED/PRIVATE/CADENCE/ROOT", str(cadence))
    config_text = config_text.replace("/REPLACE/WITH/APPROVED/PRIVATE/PDK/cds.lib", str(cds_lib))
    config_text = config_text.replace("/REPLACE/WITH/APPROVED/PRIVATE/PDK/layers.layermap", str(layer_map))
    config_text = config_text.replace("REPLACE_WITH_APPROVED_PRIVATE_TECH_LIB", "test_tech")
    previous_config = tmp_path / "previous.yaml"
    production_config = tmp_path / "production.yaml"
    previous_config.write_text(config_text, encoding="utf-8")
    production_config.write_text(config_text, encoding="utf-8")
    return previous_config, production_config


def test_preparation_requires_hash_bound_previous_contract_and_identical_private_config(tmp_path: Path) -> None:
    module = _load_prepare_module()
    previous_config, production_config = _write_preparation_config_fixture(tmp_path)

    previous_contract = tmp_path / "previous_contract.json"
    previous_contract.write_text(
        json.dumps(
            {
                "campaign_id": "broadband56_real_emx_tsmc65_v1",
                "frequency_grid": {"start_ghz": 5.0, "stop_ghz": 60.0, "step_ghz": 1.0, "points": 56},
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "prepared"
    status = module.main(
        [
            "--override-contract", str(CONTRACT),
            "--previous-contract", str(previous_contract),
            "--previous-contract-sha256", _sha256(previous_contract),
            "--previous-config", str(previous_config),
            "--production-config", str(production_config),
            "--out-dir", str(out_dir),
        ]
    )

    receipt = json.loads((out_dir / "PREPARATION_RECEIPT.json").read_text(encoding="utf-8"))
    frozen = json.loads((out_dir / "campaign_contract_frozen.json").read_text(encoding="utf-8"))
    assert status == 0
    assert receipt["overall_status"] == "PASS"
    assert frozen["preparation_status"] == "PASS"
    assert frozen["inherited_contract_evidence"]["previous_contract_sha256"] == _sha256(previous_contract)
    frozen_bounds = json.loads((out_dir / "GEOMETRY_BOUNDS_FROZEN.json").read_text(encoding="utf-8"))
    assert frozen_bounds["preparation_status"] == "PASS"
    assert tuple(frozen_bounds["field_bounds_um"]) == GEOMETRY_FIELDS


def test_preparation_rejects_matching_configs_without_foundry_layout_contract(
    tmp_path: Path,
) -> None:
    module = _load_prepare_module()
    previous_config, production_config = _write_preparation_config_fixture(tmp_path)
    foundry_block = (
        "  foundry_layout:\n"
        "    enabled: true\n"
        "    manufacturing_grid_um: 0.005\n"
        "    power_line_stitch_pad_depth_um: 6.0\n"
        "    shield_strap_width_um: 10.0\n"
        "    shield_strap_pitch_um: 20.0\n"
    )
    for path in (previous_config, production_config):
        config_text = path.read_text(encoding="utf-8")
        assert foundry_block in config_text
        path.write_text(config_text.replace(foundry_block, ""), encoding="utf-8")

    previous_contract = tmp_path / "previous_contract.json"
    previous_contract.write_text(
        json.dumps(
            {
                "campaign_id": "broadband56_real_emx_tsmc65_v1",
                "frequency_grid": {
                    "start_ghz": 5.0,
                    "stop_ghz": 60.0,
                    "step_ghz": 1.0,
                    "points": 56,
                },
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "missing_foundry_layout"

    status = module.main(
        [
            "--override-contract",
            str(CONTRACT),
            "--previous-contract",
            str(previous_contract),
            "--previous-contract-sha256",
            _sha256(previous_contract),
            "--previous-config",
            str(previous_config),
            "--production-config",
            str(production_config),
            "--out-dir",
            str(out_dir),
        ]
    )

    receipt = json.loads((out_dir / "PREPARATION_RECEIPT.json").read_text())
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert status == 2
    assert receipt["overall_status"] == "FAIL"
    assert "previous_config_foundry_layout_contract_exact" in failed
    assert "production_config_foundry_layout_contract_exact" in failed


def test_reconstructed_baseline_requires_explicit_matching_approval_receipt(tmp_path: Path) -> None:
    module = _load_prepare_module()
    previous_config, production_config = _write_preparation_config_fixture(tmp_path)
    candidate_sha = _sha256(RECONSTRUCTED_CANDIDATE)

    missing_out = tmp_path / "missing_approval"
    missing_status = module.main(
        [
            "--override-contract", str(CONTRACT),
            "--previous-contract", str(RECONSTRUCTED_CANDIDATE),
            "--previous-contract-sha256", candidate_sha,
            "--previous-config", str(previous_config),
            "--production-config", str(production_config),
            "--out-dir", str(missing_out),
        ]
    )
    missing_receipt = json.loads((missing_out / "PREPARATION_RECEIPT.json").read_text(encoding="utf-8"))
    missing_failed = {item["name"] for item in missing_receipt["checks"] if not item["pass"]}
    assert missing_status == 2
    assert missing_receipt["overall_status"] == "FAIL"
    assert "reconstructed_baseline_approval_receipt_provided" in missing_failed

    candidate = json.loads(RECONSTRUCTED_CANDIDATE.read_text(encoding="utf-8"))
    approval = {
        "schema": "rfic_transformer.broadband56_reconstructed_baseline_approval.v1",
        "overall_status": "PASS",
        "decision": "APPROVE_V2_PREPARATION_PREFLIGHT_ONLY",
        "approved_by": "unit-test-project-owner",
        "approved_utc": "2026-08-29T12:00:00Z",
        "approval_source": "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION",
        "approval_reference": "unit-test explicit approval fixture",
        "approved_contract": {
            "campaign_id": candidate["campaign_id"],
            "sha256": candidate_sha,
        },
        "preparation_preflight_authorized": True,
        "automatic_command_authorized": False,
        "golden_authorized": False,
        "simulator_authorized": False,
    }
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    approved_out = tmp_path / "approved"
    approved_status = module.main(
        [
            "--override-contract", str(CONTRACT),
            "--previous-contract", str(RECONSTRUCTED_CANDIDATE),
            "--previous-contract-sha256", candidate_sha,
            "--previous-contract-approval-receipt", str(approval_path),
            "--previous-config", str(previous_config),
            "--production-config", str(production_config),
            "--out-dir", str(approved_out),
        ]
    )
    approved_receipt = json.loads((approved_out / "PREPARATION_RECEIPT.json").read_text(encoding="utf-8"))
    approved_contract = json.loads((approved_out / "campaign_contract_frozen.json").read_text(encoding="utf-8"))
    assert approved_status == 0
    assert approved_receipt["overall_status"] == "PASS"
    assert approved_contract["inherited_contract_evidence"]["previous_contract_approval_receipt_sha256"] == _sha256(approval_path)

    approval["approved_contract"]["sha256"] = "0" * 64
    mismatch_path = tmp_path / "mismatch_approval.json"
    mismatch_path.write_text(json.dumps(approval), encoding="utf-8")
    mismatch_out = tmp_path / "mismatch"
    mismatch_status = module.main(
        [
            "--override-contract", str(CONTRACT),
            "--previous-contract", str(RECONSTRUCTED_CANDIDATE),
            "--previous-contract-sha256", candidate_sha,
            "--previous-contract-approval-receipt", str(mismatch_path),
            "--previous-config", str(previous_config),
            "--production-config", str(production_config),
            "--out-dir", str(mismatch_out),
        ]
    )
    mismatch_receipt = json.loads((mismatch_out / "PREPARATION_RECEIPT.json").read_text(encoding="utf-8"))
    mismatch_failed = {item["name"] for item in mismatch_receipt["checks"] if not item["pass"]}
    assert mismatch_status == 2
    assert mismatch_receipt["overall_status"] == "FAIL"
    assert "reconstructed_baseline_approval_contract_sha256" in mismatch_failed

    approval["approved_contract"] = []
    malformed_path = tmp_path / "malformed_approval.json"
    malformed_path.write_text(json.dumps(approval), encoding="utf-8")
    malformed_out = tmp_path / "malformed"
    malformed_status = module.main(
        [
            "--override-contract", str(CONTRACT),
            "--previous-contract", str(RECONSTRUCTED_CANDIDATE),
            "--previous-contract-sha256", candidate_sha,
            "--previous-contract-approval-receipt", str(malformed_path),
            "--previous-config", str(previous_config),
            "--production-config", str(production_config),
            "--out-dir", str(malformed_out),
        ]
    )
    malformed_receipt = json.loads((malformed_out / "PREPARATION_RECEIPT.json").read_text(encoding="utf-8"))
    malformed_failed = {item["name"] for item in malformed_receipt["checks"] if not item["pass"]}
    assert malformed_status == 2
    assert malformed_receipt["overall_status"] == "FAIL"
    assert "reconstructed_baseline_approved_contract_is_object" in malformed_failed


def test_resource_estimator_requires_contract_bound_fresh_real_emx_pilots(tmp_path: Path) -> None:
    module = _load_resource_estimator_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    pilot_32_summary, pilot_32_audit = _write_resource_pilot_fixture(
        tmp_path,
        count=32,
        fingerprint=fingerprint,
        elapsed_seconds=64.0,
        active_worker_seconds=200.0,
    )
    pilot_1000_summary, pilot_1000_audit = _write_resource_pilot_fixture(
        tmp_path,
        count=1_000,
        fingerprint=fingerprint,
        elapsed_seconds=1_000.0,
        active_worker_seconds=40_000.0,
    )
    out_dir = tmp_path / "resource_estimate"

    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--pilot-32-run-summary", str(pilot_32_summary),
            "--pilot-32-audit-dir", str(pilot_32_audit),
            "--pilot-1000-run-summary", str(pilot_1000_summary),
            "--pilot-1000-audit-dir", str(pilot_1000_audit),
            "--out-dir", str(out_dir),
        ]
    )

    estimate = json.loads((out_dir / "RESOURCE_ESTIMATE.json").read_text(encoding="utf-8"))
    assert status == 0
    assert estimate["overall_status"] == "PASS"
    assert estimate["decision"] == "RESOURCE_ESTIMATE_READY"
    assert estimate["estimate"]["current_audited_accepted_geometries"] == 1_000
    assert estimate["estimate"]["remaining_accepted_geometries"] == 199_000
    assert estimate["estimate"]["nominal_remaining_wall_hours"] == 199_000 / 3_600.0


def test_resource_estimator_rejects_resumed_pilot_timing(tmp_path: Path) -> None:
    module = _load_resource_estimator_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    pilot_32_summary, pilot_32_audit = _write_resource_pilot_fixture(
        tmp_path,
        count=32,
        fingerprint=fingerprint,
        elapsed_seconds=64.0,
        active_worker_seconds=200.0,
    )
    pilot_1000_summary, pilot_1000_audit = _write_resource_pilot_fixture(
        tmp_path,
        count=1_000,
        fingerprint=fingerprint,
        elapsed_seconds=1_000.0,
        active_worker_seconds=40_000.0,
        reused_shards=1,
    )
    out_dir = tmp_path / "resource_estimate_fail"

    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--pilot-32-run-summary", str(pilot_32_summary),
            "--pilot-32-audit-dir", str(pilot_32_audit),
            "--pilot-1000-run-summary", str(pilot_1000_summary),
            "--pilot-1000-audit-dir", str(pilot_1000_audit),
            "--out-dir", str(out_dir),
        ]
    )

    estimate = json.loads((out_dir / "RESOURCE_ESTIMATE.json").read_text(encoding="utf-8"))
    assert status == 2
    assert estimate["overall_status"] == "FAIL"
    assert estimate["estimate"] is None
    failed = {item["name"] for item in estimate["checks"] if not item["pass"]}
    assert "pilot_1000_run_is_fresh_real_emx" in failed


def test_adaptive_round_stager_uses_maximin_fallback_without_ensemble(tmp_path: Path) -> None:
    module = _load_adaptive_round_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    audit_dir = _write_adaptive_audit_fixture(tmp_path, accepted_count=50_000, fingerprint=fingerprint)
    out_dir = tmp_path / "adaptive_fallback"

    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--audit-dir", str(audit_dir),
            "--out-dir", str(out_dir),
        ]
    )

    staged = json.loads((out_dir / "ADAPTIVE_ROUND_CONTRACT.json").read_text(encoding="utf-8"))
    assert status == 0
    assert staged["overall_status"] == "PASS"
    assert staged["decision"] == "USE_MAXIMIN_FALLBACK_FOR_ROUND"
    assert staged["acquisition_mode"] == "FALLBACK_MAXIMIN"
    assert staged["round"]["accepted_start"] == 50_000
    assert staged["round"]["accepted_target"] == 55_000
    assert staged["active_source_quotas"] == {"maximin_geometry_exploration": 5_000}
    assert staged["ensemble_gate"]["status"] == "NOT_PROVIDED"
    assert staged["candidate_selection_policy"] == selection_policy_contract()
    assert staged["preceding_real_emx_audit"]["accepted_geometries_sha256"] == _sha256(
        audit_dir / "accepted_geometries.csv"
    )
    assert staged["preceding_real_emx_audit"]["geometry_bounds_sha256"] == _sha256(
        audit_dir / "GEOMETRY_BOUNDS_FROZEN.json"
    )


def test_adaptive_round_stager_enforces_ensemble_gate_and_phase_b_mixture(tmp_path: Path) -> None:
    module = _load_adaptive_round_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    audit_dir = _write_adaptive_audit_fixture(tmp_path, accepted_count=50_000, fingerprint=fingerprint)
    ensemble = _write_ensemble_fixture(
        tmp_path,
        fingerprint=fingerprint,
        audit_dir=audit_dir,
        accepted_count=50_000,
    )
    out_dir = tmp_path / "adaptive_ensemble"

    status = module.main(
        [
            "--contract", str(CONTRACT),
            "--audit-dir", str(audit_dir),
            "--ensemble-receipt", str(ensemble),
            "--out-dir", str(out_dir),
        ]
    )

    staged = json.loads((out_dir / "ADAPTIVE_ROUND_CONTRACT.json").read_text(encoding="utf-8"))
    assert status == 0
    assert staged["decision"] == "USE_ENSEMBLE_ACQUISITION_FOR_ROUND"
    assert staged["acquisition_mode"] == "ENSEMBLE_ACQUISITION"
    assert staged["active_source_quotas"] == {
        "underfilled_response_repair": 3_000,
        "ensemble_uncertainty": 1_000,
        "maximin_geometry_exploration": 1_000,
    }
    assert staged["ensemble_gate"]["status"] == "PASS"
    assert staged["candidate_selection_policy"] == selection_policy_contract()

    invalid_ensemble = _write_ensemble_fixture(
        tmp_path,
        fingerprint=fingerprint,
        audit_dir=audit_dir,
        accepted_count=50_000,
        duplicate_seed=True,
    )
    invalid_out = tmp_path / "adaptive_invalid_ensemble"
    invalid_status = module.main(
        [
            "--contract", str(CONTRACT),
            "--audit-dir", str(audit_dir),
            "--ensemble-receipt", str(invalid_ensemble),
            "--out-dir", str(invalid_out),
        ]
    )
    invalid = json.loads((invalid_out / "ADAPTIVE_ROUND_CONTRACT.json").read_text(encoding="utf-8"))
    assert invalid_status == 0
    assert invalid["decision"] == "USE_MAXIMIN_FALLBACK_FOR_ROUND"
    assert invalid["ensemble_gate"]["status"] == "FAIL"
    assert "ensemble seeds are missing or duplicated" in invalid["ensemble_gate"]["errors"]


def test_adaptive_round_stager_replenishes_only_remaining_rows(tmp_path: Path) -> None:
    module = _load_adaptive_round_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = contract_fingerprint(contract)
    audit_dir = _write_adaptive_audit_fixture(
        tmp_path,
        accepted_count=50_000,
        fingerprint=fingerprint,
    )
    ensemble = _write_ensemble_fixture(
        tmp_path,
        fingerprint=fingerprint,
        audit_dir=audit_dir,
        accepted_count=50_000,
    )
    out_dir = tmp_path / "adaptive_partial_replenishment"

    status = module.main(
        [
            "--contract",
            str(CONTRACT),
            "--audit-dir",
            str(audit_dir),
            "--ensemble-receipt",
            str(ensemble),
            "--current-accepted",
            "54900",
            "--out-dir",
            str(out_dir),
        ]
    )

    staged = json.loads(
        (out_dir / "ADAPTIVE_ROUND_CONTRACT.json").read_text(encoding="utf-8")
    )
    assert status == 0
    assert staged["checkpoint_accepted"] == 50_000
    assert staged["current_accepted"] == 54_900
    assert staged["raw_selection_count"] == 100
    assert staged["round"]["accepted_target"] == 55_000
    assert staged["active_source_quotas"] == {
        "underfilled_response_repair": 60,
        "ensemble_uncertainty": 20,
        "maximin_geometry_exploration": 20,
    }
    assert staged["candidate_pool_requirement"]["minimum_pool_count"] == 400


def test_checkpoint_audit_accepts_exact_100_geometry_real_emx_shaped_fixture(tmp_path: Path) -> None:
    module = _load_audit_module()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = module.contract_fingerprint(contract)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    geometry_bounds_path = tmp_path / "geometry_bounds.json"
    geometry_bounds_path.write_text(
        json.dumps(
            geometry_bounds_payload(
                bounds=_test_geometry_bounds(),
                contract_fingerprint_sha256=fingerprint,
            )
        ),
        encoding="utf-8",
    )

    accepted_rows: list[dict[str, object]] = []
    artifact_rows: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    s_matrix = np.zeros((56, 4, 4), dtype=np.complex128)
    freqs = np.asarray(FREQUENCY_GRID_HZ, dtype=float)

    for geometry_index in range(100):
        geometry_id = f"g{geometry_index:06d}"
        geometry = {
            "primary_outer_width_um": 220.0 + geometry_index * 0.001,
            "primary_outer_height_um": 221.0,
            "secondary_outer_width_um": 210.0,
            "secondary_outer_height_um": 211.0,
            "line_width_um": 8.0,
            "primary_terminal_y_span_um": 50.0,
            "secondary_terminal_y_span_um": 51.0,
            "offset_um": 0.0,
            "primary_feed_extension_um": 150.0,
            "secondary_feed_extension_um": 151.0,
        }
        geometry_hash = canonical_geometry_sha256(geometry)
        accepted_row: dict[str, object] = {
            "geometry_id": geometry_id,
            "geometry_sha256": geometry_hash,
            "campaign_contract_fingerprint": fingerprint,
            "accepted_sequence": geometry_index + 1,
            "campaign_phase": "PHASE_A",
            "acquisition_source": "base_space_filling",
            "calibre_blocking_violations": 0,
        }
        accepted_row.update({field: "PASS" for field in module.ACCEPTANCE_STATUS_FIELDS})
        accepted_row.update({f"geom__{name}": value for name, value in geometry.items()})
        accepted_rows.append(accepted_row)

        s4p_path = tmp_path / f"{geometry_id}.s4p"
        SParameterResult(freqs_hz=freqs, s_matrix=s_matrix).to_touchstone(s4p_path)
        artifact_rows.append(
            {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_hash,
                "campaign_contract_fingerprint": fingerprint,
                "s4p_path": str(s4p_path),
                "s4p_sha256": _sha256(s4p_path),
                "frequency_points": 56,
                "emx_status": "PASS",
                "calibre_status": "PASS",
                "calibre_blocking_violations": 0,
            }
        )

        lp_nh = 0.5
        ls_nh = 0.5
        for frequency_hz in FREQUENCY_GRID_HZ:
            xp = 2.0 * math.pi * frequency_hz * lp_nh * 1.0e-9
            xs = 2.0 * math.pi * frequency_hz * ls_nh * 1.0e-9
            feature: dict[str, object] = {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_hash,
                "campaign_contract_fingerprint": fingerprint,
                "frequency_hz": frequency_hz,
                "lp_nh": lp_nh,
                "ls_nh": ls_nh,
                "qp": 12.0,
                "qs": 13.0,
                "qmin": 12.0,
                "mutual_inductance_h": 0.1e-9,
                "signed_k": 0.2,
                "k_abs": 0.2,
                "ls_over_lp": 1.0,
                "xp_ohm": xp,
                "xs_ohm": xs,
                "broadband_descriptor_valid": "true",
                "strict_lumped_valid": "true",
                "srf_status": "CENSORED_ABOVE_60_GHZ",
                "passivity_status": "PASS",
                "reciprocity_status": "PASS",
                "inside_broad_response_envelope": "true",
                "inside_literature_practical_panel": "true",
                "outside_envelope_reason": "",
            }
            feature.update({name: 0.0 for name in matrix_columns()})
            feature_rows.append(feature)

    accepted_path = tmp_path / "accepted.csv"
    artifacts_path = tmp_path / "artifacts.csv"
    features_path = tmp_path / "features.csv"
    funnel_path = tmp_path / "funnel.csv"
    _write_csv(accepted_path, accepted_rows)
    _write_csv(artifacts_path, artifact_rows)
    _write_csv(features_path, feature_rows)
    _write_csv(
        funnel_path,
        [
            {"stage": "raw_geometry_candidates", "count": 100},
            {"stage": "analytical_failures", "count": 0},
            {"stage": "topology_failures", "count": 0},
            {"stage": "cadence_failures", "count": 0},
            {"stage": "calibre_failures", "count": 0},
            {"stage": "emx_failures", "count": 0},
            {"stage": "incomplete_frequency_failures", "count": 0},
            {"stage": "s4p_parsing_failures", "count": 0},
            {"stage": "feature_extraction_failures", "count": 0},
            {"stage": "accepted_geometries", "count": 100},
        ],
    )

    out_dir = tmp_path / "checkpoint"
    status = module.main(
        [
            "--contract", str(contract_path),
            "--geometry-bounds", str(geometry_bounds_path),
            "--accepted-geometries", str(accepted_path),
            "--long-features", str(features_path),
            "--artifact-index", str(artifacts_path),
            "--failure-funnel", str(funnel_path),
            "--expected-accepted", "100",
            "--out-dir", str(out_dir),
        ]
    )

    receipt = json.loads((out_dir / "CHECKPOINT_RECEIPT.json").read_text(encoding="utf-8"))
    coverage = json.loads((out_dir / "COVERAGE_SUMMARY.json").read_text(encoding="utf-8"))
    assert status == 0
    assert receipt["overall_status"] == "PASS"
    assert receipt["audit_mode"] == "checkpoint"
    assert coverage["feature_row_count"] == 5_600
    assert coverage["coverage_status"] == "COVERAGE_PARTIAL"
    assert coverage["geometry_unique_anchor_coverage"]["observed_cells"] == 8
    assert sum(1 for _ in (out_dir / "physical_coverage_cells_by_anchor.csv").open(encoding="utf-8")) == 10_369
    assert (out_dir / "physical_coverage_by_frequency.csv").stat().st_size > 0
    assert (out_dir / "physical_coverage_marginals.csv").stat().st_size > 0
    assert (out_dir / "physical_coverage_pairwise.csv").stat().st_size > 0
    assert (out_dir / "GEOMETRY_COVERAGE_SUMMARY.json").stat().st_size > 0
    assert sum(1 for _ in (out_dir / "geometry_coverage_marginals.csv").open(encoding="utf-8")) == 11
    assert sum(1 for _ in (out_dir / "geometry_coverage_pairwise.csv").open(encoding="utf-8")) == 46
    secondary = coverage["record_weighted_secondary_coverage"]
    all_records = next(
        row for row in secondary["groups"]
        if row["population"] == "all_parseable_emx_records" and row["campaign_phase"] == "ALL"
    )
    assert all_records["geometry_frequency_records"] == 5_600
    primary_groups = coverage["geometry_unique_anchor_coverage_by_population_phase"]["groups"]
    primary_broadband = next(
        row for row in primary_groups
        if row["population"] == "broadband_descriptor_valid" and row["campaign_phase"] == "ALL"
    )
    assert primary_broadband["anchor_record_count"] == 800
    assert primary_broadband["in_primary_cells"] == 800
