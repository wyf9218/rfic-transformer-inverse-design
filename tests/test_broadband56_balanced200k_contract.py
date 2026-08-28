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
    ANCHOR_FREQUENCIES_GHZ,
    EXPECTED_FEATURE_ROWS,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    SECONDARY_FEATURES,
    TARGET_ACCEPTED_GEOMETRIES,
    build_phase_plan,
    canonical_geometry_sha256,
    matrix_columns,
    occupancy_metrics,
    primary_bin_edges,
    primary_cell_for_values,
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
from rfic_transformer_inverse_design.sim.base import SParameterResult


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"
TEMPLATE = ROOT / "configs" / "mars_s4p_grounded_powerline_broadband56_balanced200k_v2_template.yaml"


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


def test_preparation_requires_hash_bound_previous_contract_and_identical_private_config(tmp_path: Path) -> None:
    module = _load_prepare_module()
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
