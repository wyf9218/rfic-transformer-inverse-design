from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys

import pytest


OUTER_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
)


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_transformer_ground_ring_spacing.py"
    spec = importlib.util.spec_from_file_location("audit_transformer_ground_ring_spacing_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, maximum_dimensions: list[float], *, include_margin: bool = True) -> None:
    rows = []
    for index, maximum in enumerate(maximum_dimensions):
        row = {
            "evaluation": f"sample_{index}",
            OUTER_COLUMNS[0]: maximum,
            OUTER_COLUMNS[1]: maximum - 4.0,
            OUTER_COLUMNS[2]: maximum - 18.0,
            OUTER_COLUMNS[3]: maximum - 22.0,
            "lp_nh_center": 0.8 + 0.2 * index,
            "ls_nh_center": 1.0 + 0.2 * index,
            "q_center": 8.0 + 2.0 * index,
            "k_abs_center": 0.25 + 0.05 * index,
        }
        if include_margin:
            row["geom__shield_margin_um"] = 100.0
        rows.append(row)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _run(module, source: Path, out_dir: Path, *extra: str) -> int:
    return module.main(
        [
            "--training-csv",
            str(source),
            "--out-dir",
            str(out_dir),
            "--min-rows",
            "4",
            *extra,
        ]
    )


def test_ground_ring_spacing_audit_measures_actual_row_level_ratio(tmp_path):
    module = _load_module()
    source = tmp_path / "dataset_rows.csv"
    _write_rows(source, [240.0, 300.0, 360.0, 200.0])
    out_dir = tmp_path / "out"

    assert _run(module, source, out_dir, "--max-row-artifact", "2", "--max-plot-points", "3") == 0
    summary = json.loads((out_dir / "ground_ring_spacing_audit_summary.json").read_text())

    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "REVIEW_LOW_MARGIN_STRATUM_WITH_REAL_EMX_DO_NOT_AUTO_REJECT"
    assert summary["valid_row_count"] == 4
    assert summary["row_artifact_count"] == 2
    assert summary["response_plot_point_count"] == 3
    assert summary["analysis"]["below_recommended_count"] == 1
    assert summary["analysis"]["below_recommended_fraction"] == pytest.approx(0.25)
    assert summary["analysis"]["spacing_ratio"]["min"] == pytest.approx(100.0 / 360.0)
    assert summary["analysis"]["spacing_ratio"]["max"] == pytest.approx(0.5)
    assert summary["analysis"]["unique_shield_margin_count"] == 1
    assert summary["analysis"]["feature_diagnostics"]["q"]["available"] is True
    assert summary["checks"]["shield_margin_is_row_level_evidence"] is True
    assert len(summary["training_csv_sha256"]) == 64
    assert (out_dir / "ground_ring_spacing_audit_rows.csv").is_file()
    assert (out_dir / "ground_ring_spacing_ratio_histogram.png").is_file()
    assert (out_dir / "ground_ring_spacing_response_scatter.png").is_file()
    assert (out_dir / "ground_ring_spacing_audit_report.md").is_file()
    with (out_dir / "ground_ring_spacing_audit_rows.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 2


def test_ground_ring_spacing_audit_keeps_heuristic_separate_from_completion(tmp_path):
    module = _load_module()
    source = tmp_path / "dataset_rows.csv"
    _write_rows(source, [240.0, 280.0, 300.0, 200.0])
    out_dir = tmp_path / "out"

    assert _run(module, source, out_dir) == 0
    summary = json.loads((out_dir / "ground_ring_spacing_audit_summary.json").read_text())

    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "ADVISORY_GROUND_RING_SPACING_HEURISTIC_SATISFIED"
    assert summary["analysis"]["below_recommended_fraction"] == 0.0
    assert "not DRC" in summary["geometry_contract"]["recommended_interpretation"]
    assert "does not establish causality" in summary["scientific_boundary"]


def test_ground_ring_spacing_audit_coalesces_mixed_feature_schemas_per_row(tmp_path):
    module = _load_module()
    source = tmp_path / "dataset_rows.csv"
    _write_rows(source, [240.0, 280.0, 300.0, 200.0])
    rows = list(csv.DictReader(source.open(newline="", encoding="utf-8")))
    for index, row in enumerate(rows):
        row["input__lp_nh_center"] = row["lp_nh_center"] if index % 2 == 0 else ""
        if index % 2 == 0:
            row["lp_nh_center"] = ""
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    out_dir = tmp_path / "out"

    assert _run(module, source, out_dir) == 0
    summary = json.loads((out_dir / "ground_ring_spacing_audit_summary.json").read_text())

    assert summary["overall_status"] == "PASS"
    assert summary["analysis"]["feature_diagnostics"]["lp_nh"]["all_rows"]["count"] == 4
    assert summary["geometry_contract"]["feature_columns_found"]["lp_nh"] == [
        "input__lp_nh_center",
        "lp_nh_center",
    ]


def test_ground_ring_spacing_audit_fails_without_row_level_margin(tmp_path):
    module = _load_module()
    source = tmp_path / "dataset_rows.csv"
    _write_rows(source, [240.0, 280.0, 300.0, 200.0], include_margin=False)
    out_dir = tmp_path / "out"

    assert _run(module, source, out_dir, "--no-fail-exit") == 0
    summary = json.loads((out_dir / "ground_ring_spacing_audit_summary.json").read_text())

    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_GEOMETRY_EVIDENCE"
    assert summary["checks"]["required_geometry_columns_present"] is False
    assert summary["valid_row_count"] == 0


def _attach_evaluation_metadata(
    tmp_path: Path,
    source: Path,
    *,
    primary_width_delta_um: float = 0.0,
) -> None:
    rows = list(csv.DictReader(source.open(newline="", encoding="utf-8")))
    for index, row in enumerate(rows):
        evaluation = tmp_path / "evaluations" / f"sample_{index}"
        (evaluation / "emx").mkdir(parents=True)
        (evaluation / "layout").mkdir()
        touchstone = evaluation / "emx" / "emx.s4p"
        touchstone.write_text("! test evidence\n", encoding="utf-8")
        maximum = float(row[OUTER_COLUMNS[0]])
        metadata = {
            "primary": {
                "geometry": {
                    "outer_width_um": maximum + primary_width_delta_um,
                    "outer_height_um": maximum - 4.0,
                }
            },
            "secondary": {
                "geometry": {
                    "outer_width_um": maximum - 18.0,
                    "outer_height_um": maximum - 22.0,
                }
            },
            "shield": {"enabled": True, "kind": "ring", "margin_um": 100.0, "width_um": 10.0},
        }
        (evaluation / "layout" / "geometry.json").write_text(json.dumps(metadata), encoding="utf-8")
        row["touchstone_path"] = str(touchstone)
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_ground_ring_spacing_audit_recovers_margin_from_matching_evaluation_metadata(tmp_path):
    module = _load_module()
    source = tmp_path / "dataset_rows.csv"
    _write_rows(source, [240.0, 280.0, 300.0, 200.0], include_margin=False)
    _attach_evaluation_metadata(tmp_path, source)
    out_dir = tmp_path / "out"

    assert _run(module, source, out_dir, "--recover-margin-from-evaluation-metadata") == 0
    summary = json.loads((out_dir / "ground_ring_spacing_audit_summary.json").read_text())

    assert summary["overall_status"] == "PASS"
    assert summary["valid_row_count"] == 4
    assert summary["rejected_row_count"] == 0
    assert summary["geometry_contract"]["margin_evidence_counts"] == {
        "evaluation_metadata:layout/geometry.json:shield.margin_um": 4
    }
    with (out_dir / "ground_ring_spacing_audit_rows.csv").open(newline="", encoding="utf-8") as handle:
        artifact_rows = list(csv.DictReader(handle))
    assert all(row["shield_margin_evidence_path"].endswith("layout/geometry.json") for row in artifact_rows)


def test_ground_ring_spacing_audit_rejects_mismatched_evaluation_metadata(tmp_path):
    module = _load_module()
    source = tmp_path / "dataset_rows.csv"
    _write_rows(source, [240.0, 280.0, 300.0, 200.0], include_margin=False)
    _attach_evaluation_metadata(tmp_path, source, primary_width_delta_um=1.0)
    out_dir = tmp_path / "out"

    assert _run(
        module,
        source,
        out_dir,
        "--recover-margin-from-evaluation-metadata",
        "--no-fail-exit",
    ) == 0
    summary = json.loads((out_dir / "ground_ring_spacing_audit_summary.json").read_text())

    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_GEOMETRY_EVIDENCE"
    assert summary["valid_row_count"] == 0
    assert summary["rejected_row_count"] == 4
    assert "do not match" in summary["rejected_rows"][0]["reason"]
