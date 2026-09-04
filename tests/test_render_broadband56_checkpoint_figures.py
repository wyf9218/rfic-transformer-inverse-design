from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    SECONDARY_FEATURES,
    contract_fingerprint,
    phase_for_accepted_sequence,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"
HISTORY_COUNTS = (50_000, 100_000, 150_000, 200_000)


def _load_module():
    path = ROOT / "scripts" / "render_broadband56_checkpoint_figures.py"
    spec = importlib.util.spec_from_file_location("render_broadband56_checkpoint_figures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_campaign_root_discovers_exact_figure_checkpoints(tmp_path: Path) -> None:
    module = _load_module()
    campaign = tmp_path / "campaign"
    for index, count in enumerate(HISTORY_COUNTS, start=1):
        directory = campaign / "stages" / f"{index:06d}_stage" / "checkpoint"
        directory.mkdir(parents=True)
        _write_json(
            directory / "CHECKPOINT_STATUS.json",
            {"accepted_geometries": count, "audit_mode": "checkpoint"},
        )
    discovered = module._discover_audit_dirs(
        campaign,
        required_counts=HISTORY_COUNTS,
    )
    assert [
        json.loads((path / "CHECKPOINT_STATUS.json").read_text())["accepted_geometries"]
        for path in discovered
    ] == list(HISTORY_COUNTS)


def test_campaign_root_figure_discovery_fails_on_missing_count(
    tmp_path: Path,
) -> None:
    module = _load_module()
    directory = tmp_path / "campaign" / "stages" / "one" / "checkpoint"
    directory.mkdir(parents=True)
    _write_json(
        directory / "CHECKPOINT_STATUS.json",
        {"accepted_geometries": 50_000, "audit_mode": "checkpoint"},
    )
    with pytest.raises(module.FigureBuildError, match="missing"):
        module._discover_audit_dirs(
            tmp_path / "campaign",
            required_counts=HISTORY_COUNTS,
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
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


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _frozen_contract(tmp_path: Path) -> tuple[Path, str, Path]:
    production_config = tmp_path / "production_config.yaml"
    production_config.write_text("synthetic: true\n", encoding="utf-8")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["inherited_contract_evidence"] = {
        "previous_campaign_id": "synthetic_previous_campaign",
        "previous_contract_sha256": "1" * 64,
        "previous_config_sha256": "2" * 64,
        "production_config_sha256": _sha256(production_config),
        "private_runtime_paths_not_for_publication": True,
    }
    contract["preparation_status"] = "PASS"
    contract["contract_fingerprint_sha256"] = contract_fingerprint(contract)
    path = tmp_path / "campaign_contract_frozen.json"
    _write_json(path, contract)
    return path, contract["contract_fingerprint_sha256"], production_config


def _write_raw_products(
    root: Path, *, fingerprint: str, production_config: Path
) -> Path:
    directory = root / "raw_products"
    directory.mkdir()
    receipt = directory / "RAW_PRODUCTS_RECEIPT.json"
    config_sha = _sha256(production_config)
    _write_json(
        receipt,
        {
            "schema": "broadband56_raw_products_receipt_v1",
            "overall_status": "PASS",
            "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": fingerprint,
            "counts": {
                "accepted_geometries": 200_000,
                "geometry_frequency_rows": 11_200_000,
            },
            "checks": {"synthetic_raw_products": True},
            "inputs": {
                "production_config": _evidence(production_config),
                "production_config_authorization": {
                    "mode": "FROZEN_CONTRACT_DIRECT",
                    "frozen_config_sha256": config_sha,
                    "effective_config_sha256": config_sha,
                    "full_campaign_receipt": None,
                    "corrected_foundry_layout_approval_receipt": None,
                },
            },
        },
    )
    _write_sha256s(directory)
    return directory


def _distributed_counts(total: int, bins: int) -> list[int]:
    quotient, remainder = divmod(total, bins)
    return [quotient + int(index < remainder) for index in range(bins)]


def _geometry_marginals(count: int) -> list[dict[str, object]]:
    counts = _distributed_counts(count, 10)
    return [
        {
            "counting_basis": "geometry_unique",
            "field": field,
            "geometry_count": count,
            "bin_counts_json": json.dumps(counts),
            "lower_boundary_count": counts[0],
            "upper_boundary_count": counts[-1],
        }
        for field in GEOMETRY_FIELDS
    ]


def _physical_frequency_rows(count: int) -> list[dict[str, object]]:
    population_fraction = {
        "all_parseable_emx_records": 1.00,
        "broadband_descriptor_valid": 0.92,
        "strict_lumped_valid": 0.70,
        "inside_broad_response_envelope": 0.84,
        "inside_literature_practical_panel": 0.58,
    }
    rows: list[dict[str, object]] = []
    for population, fraction in population_fraction.items():
        for frequency_hz in FREQUENCY_GRID_HZ:
            frequency_ghz = frequency_hz / 1.0e9
            for feature_index, feature in enumerate(SECONDARY_FEATURES):
                record_count = count if population == "all_parseable_emx_records" else int(
                    count * fraction * (0.98 + 0.02 * math.sin(frequency_ghz / 8.0))
                )
                rows.append(
                    {
                        "counting_basis": "record_weighted_coverage",
                        "population": population,
                        "campaign_phase": "ALL",
                        "frequency_hz": frequency_hz,
                        "feature": feature,
                        "record_count": record_count,
                        "mean": 0.5 + feature_index + frequency_ghz * 0.02,
                        "std": 0.1 + feature_index * 0.01,
                        "min": 0.1,
                        "max": 20.0,
                    }
                )
    return rows


def _pairwise_rows(count: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pairs = (("lp_nh", "ls_nh"), ("qp", "qs"), ("qmin", "k_abs"))
    valid_count = int(count * 0.90)
    values = _distributed_counts(valid_count, 64)
    for anchor in ANCHOR_FREQUENCIES_GHZ:
        for left, right in pairs:
            rows.append(
                {
                    "coverage_scope": "feature_pair_at_exact_frequency",
                    "population": "broadband_descriptor_valid",
                    "campaign_phase": "ALL",
                    "frequency_hz": anchor * 1_000_000_000,
                    "left_feature": left,
                    "right_feature": right,
                    "record_count": valid_count,
                    "matrix_shape_json": json.dumps([8, 8]),
                    "cell_counts_row_major_json": json.dumps(values),
                }
            )
    return rows


def _primary_cells(count: int) -> list[dict[str, object]]:
    target = count / float(PRIMARY_CELLS_PER_ANCHOR)
    per_anchor = _distributed_counts(count, PRIMARY_CELLS_PER_ANCHOR)
    rows: list[dict[str, object]] = []
    for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ):
        for local, actual in enumerate(per_anchor):
            conditioned = anchor_index * PRIMARY_CELLS_PER_ANCHOR + local
            rows.append(
                {
                    "anchor_ghz": anchor,
                    "cell_id": f"synthetic_{conditioned}",
                    "local_cell_index": local,
                    "conditioned_cell_index": conditioned,
                    "actual_count": actual,
                    "target_count": target,
                    "deficit": max(target - actual, 0.0),
                    "cell_status": "underfilled" if actual < target else "observed",
                }
            )
    return rows


def _write_audit(root: Path, *, count: int, fingerprint: str, contract_path: Path) -> Path:
    directory = root / f"audit_{count}"
    directory.mkdir()
    paths = {
        "geometry_marginals": directory / "geometry_coverage_marginals.csv",
        "geometry_pairwise": directory / "geometry_coverage_pairwise.csv",
        "physical_by_frequency": directory / "physical_coverage_by_frequency.csv",
        "physical_marginals": directory / "physical_coverage_marginals.csv",
        "physical_pairwise": directory / "physical_coverage_pairwise.csv",
        "primary_cells": directory / "physical_coverage_cells_by_anchor.csv",
        "failure_funnel": directory / "FAILURE_FUNNEL.csv",
    }
    _write_csv(paths["geometry_marginals"], _geometry_marginals(count))
    _write_csv(paths["geometry_pairwise"], [{"left_field": "a", "right_field": "b"}])
    _write_csv(paths["physical_by_frequency"], _physical_frequency_rows(count))
    _write_csv(paths["physical_marginals"], [{"feature": "lp_nh", "record_count": count}])
    _write_csv(paths["physical_pairwise"], _pairwise_rows(count))
    _write_csv(paths["primary_cells"], _primary_cells(count))
    _write_csv(
        paths["failure_funnel"],
        [
            {"stage": "raw_geometry_candidates", "count": int(count * 1.20)},
            {"stage": "analytical_failures", "count": int(count * 0.05)},
            {"stage": "cadence_failures", "count": int(count * 0.03)},
            {"stage": "calibre_failures", "count": int(count * 0.02)},
            {"stage": "emx_failures", "count": int(count * 0.01)},
            {"stage": "accepted_geometries", "count": count},
        ],
    )

    geometry_summary = directory / "GEOMETRY_COVERAGE_SUMMARY.json"
    coverage_summary = directory / "COVERAGE_SUMMARY.json"
    status_path = directory / "CHECKPOINT_STATUS.json"
    _write_json(geometry_summary, {"status": "PASS", "geometry_count": count})
    _write_json(
        coverage_summary,
        {
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": fingerprint,
            "expected_accepted_geometries": count,
            "feature_row_count": count * 56,
            "coverage_status": "COVERAGE_PARTIAL",
        },
    )
    _write_json(
        status_path,
        {
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": fingerprint,
            "checkpoint_status": "CHECKPOINT_COMPLETE",
            "audit_mode": "checkpoint",
            "coverage_status": "COVERAGE_PARTIAL",
            "accepted_geometries": count,
            "s4p_artifacts": count,
            "geometry_frequency_rows": count * 56,
        },
    )
    outputs = {
        "coverage_cells": _evidence(paths["primary_cells"]),
        "coverage_by_frequency": _evidence(paths["physical_by_frequency"]),
        "coverage_marginals": _evidence(paths["physical_marginals"]),
        "coverage_pairwise": _evidence(paths["physical_pairwise"]),
        "geometry_coverage_summary": _evidence(geometry_summary),
        "geometry_coverage_marginals": _evidence(paths["geometry_marginals"]),
        "geometry_coverage_pairwise": _evidence(paths["geometry_pairwise"]),
        "coverage_summary": _evidence(coverage_summary),
        "checkpoint_status": _evidence(status_path),
        "failure_funnel": _evidence(paths["failure_funnel"]),
    }
    receipt_path = directory / "CHECKPOINT_RECEIPT.json"
    _write_json(
        receipt_path,
        {
            "overall_status": "PASS",
            "decision": "USE_CHECKPOINT",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": fingerprint,
            "expected_accepted": count,
            "audit_mode": "checkpoint",
            "checks": [{"name": "synthetic_closed_audit", "pass": True}],
            "inputs": {"contract": _evidence(contract_path)},
            "outputs": outputs,
        },
    )
    _write_sha256s(directory)
    return directory


def _history_cells(count: int) -> list[int]:
    observed = min(PRIMARY_FREQUENCY_CONDITIONED_CELLS, 2_000 + count // 25)
    return _distributed_counts(count * len(ANCHOR_FREQUENCIES_GHZ), observed) + [
        0
    ] * (PRIMARY_FREQUENCY_CONDITIONED_CELLS - observed)


def _write_history(root: Path, *, fingerprint: str) -> Path:
    directory = root / "history"
    directory.mkdir()
    deficit_path = directory / "coverage_deficit_history.csv"
    deficit_rows: list[dict[str, object]] = []
    for count in HISTORY_COUNTS:
        target = count / float(PRIMARY_CELLS_PER_ANCHOR)
        for index, actual in enumerate(_history_cells(count)):
            status = (
                "unobserved_under_current_geometry_contract"
                if actual == 0
                else ("underfilled" if actual < target else "observed")
            )
            deficit_rows.append(
                {
                    "accepted_geometries": count,
                    "conditioned_cell_index": index,
                    "actual_count": actual,
                    "target_count": target,
                    "deficit": max(target - actual, 0.0),
                    "cell_status": status,
                    "campaign_contract_fingerprint": fingerprint,
                }
            )
    _write_csv(deficit_path, deficit_rows)

    acquisition_path = directory / "acquisition_round_history.csv"
    acquisition_rows: list[dict[str, object]] = []
    previous = 0
    for count in HISTORY_COUNTS:
        batch = count - previous
        phase = phase_for_accepted_sequence(count)
        source = {
            "PHASE_A": "base_space_filling",
            "PHASE_B": "underfilled_response_repair",
            "PHASE_C": "rare_or_underfilled_response_repair",
        }[phase]
        acquisition_rows.append(
            {
                "round_id": f"synthetic_{count}",
                "campaign_phase": phase,
                "accepted_start": previous,
                "accepted_end": count,
                "batch_size": batch,
                "execution_mode": "ACTIVE_MIXTURE",
                "actual_source_counts_json": json.dumps({source: batch}),
            }
        )
        previous = count
    _write_csv(acquisition_path, acquisition_rows)
    source_path = directory / "acquisition_source_by_geometry.csv"
    _write_csv(source_path, [{"geometry_id": "synthetic", "acquisition_source": "synthetic"}])
    summary_path = directory / "coverage_summary_200k.json"
    _write_json(summary_path, {"contract_fingerprint_sha256": fingerprint})
    receipt_path = directory / "CAMPAIGN_HISTORY_RECEIPT.json"
    _write_json(
        receipt_path,
        {
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_CAMPAIGN_HISTORY",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": fingerprint,
            "terminal_counts": {
                "accepted_geometries": 200_000,
                "s4p_artifacts": 200_000,
                "geometry_frequency_rows": 11_200_000,
            },
            "audit_counts": list(HISTORY_COUNTS),
            "checks": {"synthetic_closed_history": True},
            "outputs": {
                "coverage_deficit_history": _evidence(deficit_path),
                "acquisition_round_history": _evidence(acquisition_path),
                "acquisition_source_by_geometry": _evidence(source_path),
                "coverage_summary_200k": _evidence(summary_path),
            },
        },
    )
    _write_sha256s(directory)
    return directory


def _fixture(tmp_path: Path) -> dict[str, Path]:
    contract_path, fingerprint, production_config = _frozen_contract(tmp_path)
    return {
        "contract": contract_path,
        "raw": _write_raw_products(
            tmp_path,
            fingerprint=fingerprint,
            production_config=production_config,
        ),
        "history": _write_history(tmp_path, fingerprint=fingerprint),
        "audit": _write_audit(
            tmp_path,
            count=50_000,
            fingerprint=fingerprint,
            contract_path=contract_path,
        ),
    }


def _patch_single_render(module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "FIGURE_CHECKPOINT_COUNTS", (50_000,))
    monkeypatch.setattr(module, "REQUIRED_HISTORY_AUDIT_COUNTS", HISTORY_COUNTS)
    monkeypatch.setattr(module, "REQUIRED_ACQUISITION_ENDPOINTS", HISTORY_COUNTS)


def test_production_figure_contract_is_exact() -> None:
    module = _load_module()
    assert module.FIGURE_CHECKPOINT_COUNTS == (50_000, 100_000, 150_000, 200_000)
    assert len(module.FIGURE_IDS) == 14
    assert module.FIGURE_IDS[0] == "01_geometry_sampling_coverage"
    assert module.FIGURE_IDS[-1] == "14_response_coverage_before_and_after_active_repair"


def test_physical_marginals_render_explicit_zero_valid_frequency_as_missing() -> None:
    module = _load_module()
    rows = _physical_frequency_rows(100)
    for row in rows:
        if (
            row["population"] == "broadband_descriptor_valid"
            and row["frequency_hz"] == FREQUENCY_GRID_HZ[-1]
        ):
            row["record_count"] = 0
            row["mean"] = ""
            row["std"] = ""

    figure = module._plot_physical_marginals(rows, count=100, footer="synthetic")

    assert len(figure.axes) == 9
    module.plt.close(figure)


def test_renders_fourteen_hash_bound_png_and_svg_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_single_render(module, monkeypatch)
    fixture = _fixture(tmp_path)
    out_dir = tmp_path / "figures"

    result = module.render_checkpoint_figures(
        contract_path=fixture["contract"],
        raw_dir=fixture["raw"],
        history_dir=fixture["history"],
        audit_dirs=[fixture["audit"]],
        out_dir=out_dir,
    )

    assert result == {"checkpoint_count": 1, "logical_figure_count": 14, "rendered_file_count": 28}
    manifest = json.loads(
        (out_dir / "checkpoint_050000" / "FIGURE_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["checks"]["logical_figure_count_exact_14"] is True
    assert [record["figure_id"] for record in manifest["figures"]] == list(module.FIGURE_IDS)
    for record in manifest["figures"]:
        assert record["source_csvs"]
        assert record["denominator"]
        assert record["frequency_or_anchor"]
        assert record["validity_definition"]
        assert record["campaign_phase"] == "PHASE_A"
        assert len(record["production_process_config_sha256"]) == 64
        assert len(record["campaign_contract_fingerprint_sha256"]) == 64
        for source in record["source_csvs"]:
            assert len(source["sha256"]) == 64
            assert source["row_count"] > 0
        for kind, artifact in record["files"].items():
            path = out_dir / artifact["filename"]
            assert path.is_file() and path.stat().st_size > 1_000
            assert artifact["sha256"] == _sha256(path)
            assert path.suffix == f".{kind}"

    png = out_dir / "checkpoint_050000" / "01_geometry_sampling_coverage.png"
    pixels = mpimg.imread(png)
    assert pixels.size > 0
    assert float(np.std(pixels)) > 0.01
    svg = out_dir / "checkpoint_050000" / "01_geometry_sampling_coverage.svg"
    assert "<svg" in svg.read_text(encoding="utf-8")[:1_000]
    receipt = json.loads((out_dir / "FIGURE_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["checks"]["simulator_model_training_and_remote_execution_not_run"] is True
    assert (out_dir / "SHA256SUMS.txt").is_file()


def test_tampered_checkpoint_csv_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_single_render(module, monkeypatch)
    fixture = _fixture(tmp_path)
    source = fixture["audit"] / "geometry_coverage_marginals.csv"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    out_dir = tmp_path / "blocked_figures"

    with pytest.raises(module.FigureBuildError, match="SHA index mismatch"):
        module.render_checkpoint_figures(
            contract_path=fixture["contract"],
            raw_dir=fixture["raw"],
            history_dir=fixture["history"],
            audit_dirs=[fixture["audit"]],
            out_dir=out_dir,
        )

    assert not out_dir.exists()
