from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import itertools
import sys

from matplotlib import image as mpl_image


INPUT_COLUMNS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]
GEOMETRY_COLUMNS = [
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
]
RANGES = np.asarray(((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8)))


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_inverse_one_to_many_geometry_multiplicity.py"
    spec = importlib.util.spec_from_file_location("audit_inverse_one_to_many_geometry_multiplicity_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training(path: Path, *, two_modes: bool) -> None:
    rng = np.random.default_rng(73 if two_modes else 79)
    rows = []
    bins = 4
    spans = RANGES[:, 1] - RANGES[:, 0]
    for cell in itertools.product(range(bins), repeat=4):
        center = RANGES[:, 0] + (np.asarray(cell, dtype=float) + 0.5) / bins * spans
        for row_index in range(20):
            physical = center + rng.normal(0.0, 0.015, size=4) * spans / bins
            physical = np.clip(physical, RANGES[:, 0] + 1.0e-9, RANGES[:, 1] - 1.0e-9)
            normalized = (physical - RANGES[:, 0]) / spans
            geometry = np.asarray([100.0 + 10.0 * index + 5.0 * normalized[index % 4] for index in range(10)])
            if two_modes:
                direction = -1.0 if row_index % 2 == 0 else 1.0
                geometry[0] += 8.0 * direction
                geometry[1] -= 8.0 * direction
            geometry += rng.normal(0.0, 0.08, size=10)
            rows.append(
                {
                    **{name: physical[index] for index, name in enumerate(INPUT_COLUMNS)},
                    **{name: geometry[index] for index, name in enumerate(GEOMETRY_COLUMNS)},
                }
            )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_physical_drift_training(path: Path) -> None:
    rng = np.random.default_rng(83)
    rows = []
    bins = 4
    spans = RANGES[:, 1] - RANGES[:, 0]
    for cell in itertools.product(range(bins), repeat=4):
        center = RANGES[:, 0] + (np.asarray(cell, dtype=float) + 0.5) / bins * spans
        for row_index in range(20):
            direction = -1.0 if row_index % 2 == 0 else 1.0
            physical = center.copy()
            physical[0] += direction * 0.18 * spans[0] / bins
            physical += rng.normal(0.0, 0.006, size=4) * spans / bins
            physical = np.clip(physical, RANGES[:, 0] + 1.0e-9, RANGES[:, 1] - 1.0e-9)
            normalized = (physical - RANGES[:, 0]) / spans
            geometry = np.asarray(
                [100.0 + 10.0 * index + 5.0 * normalized[index % 4] for index in range(10)]
            )
            geometry[0] += 120.0 * (physical[0] - center[0]) / spans[0]
            geometry[1] -= 120.0 * (physical[0] - center[0]) / spans[0]
            geometry += rng.normal(0.0, 0.05, size=10)
            rows.append(
                {
                    **{name: physical[index] for index, name in enumerate(INPUT_COLUMNS)},
                    **{name: geometry[index] for index, name in enumerate(GEOMETRY_COLUMNS)},
                }
            )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_nonlinear_physical_drift_training(path: Path) -> None:
    rng = np.random.default_rng(89)
    rows = []
    bins = 4
    spans = RANGES[:, 1] - RANGES[:, 0]
    offsets = (-0.40, -0.32, -0.06, 0.06, 0.32, 0.40)
    for cell in itertools.product(range(bins), repeat=4):
        center = RANGES[:, 0] + (np.asarray(cell, dtype=float) + 0.5) / bins * spans
        for row_index in range(24):
            local_offset = offsets[row_index % len(offsets)]
            physical = center.copy()
            physical[0] += local_offset * spans[0] / bins
            physical += rng.normal(0.0, 0.004, size=4) * spans / bins
            physical = np.clip(physical, RANGES[:, 0] + 1.0e-9, RANGES[:, 1] - 1.0e-9)
            normalized = (physical - RANGES[:, 0]) / spans
            geometry = np.asarray(
                [100.0 + 10.0 * index + 5.0 * normalized[index % 4] for index in range(10)]
            )
            physical_region = 1.0 if abs(local_offset) > 0.2 else -1.0
            geometry[0] += 8.0 * physical_region
            geometry[1] -= 8.0 * physical_region
            geometry += rng.normal(0.0, 0.05, size=10)
            rows.append(
                {
                    **{name: physical[index] for index, name in enumerate(INPUT_COLUMNS)},
                    **{name: geometry[index] for index, name in enumerate(GEOMETRY_COLUMNS)},
                }
            )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _args(training: Path, out_dir: Path) -> list[str]:
    return [
        "--training-csv",
        str(training),
        "--out-dir",
        str(out_dir),
        "--min-source-rows",
        "5000",
        "--physical-cell-bins",
        "4",
        "--min-cell-rows",
        "12",
        "--max-rows-per-cell",
        "32",
        "--min-analyzed-cells",
        "200",
        "--max-analyzed-cells",
        "256",
    ]


def test_geometry_multiplicity_audit_detects_two_modes_inside_tight_physical_cells(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_training(training, two_modes=True)
    out_dir = tmp_path / "out"

    assert module.main(_args(training, out_dir)) == 0
    summary = json.loads((out_dir / "inverse_geometry_multiplicity_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["recommendation"]["decision"] == "SUPPORTS_TOP_K_GENERATIVE_MODEL_ABLATION_AT_500K"
    assert summary["evidence_stage"] == "confirmatory_fine"
    assert summary["recommendation"]["eligible_for_top_k_ablation"] is True
    assert summary["recommendation"]["eligible_for_model_replacement"] is False
    assert summary["analysis"]["multimodal_cell_fraction"] > 0.8
    figure = out_dir / "inverse_geometry_multiplicity.png"
    assert figure.is_file()
    pixels = mpl_image.imread(figure)
    assert float(np.mean(pixels[0, 0, :3])) > 0.95


def test_geometry_multiplicity_audit_does_not_force_generative_model_for_single_mode_cells(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_training(training, two_modes=False)
    out_dir = tmp_path / "out"

    assert module.main(_args(training, out_dir)) == 0
    summary = json.loads((out_dir / "inverse_geometry_multiplicity_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["recommendation"]["decision"] == "WEAK_MULTIMODAL_EVIDENCE_KEEP_DETERMINISTIC_BASELINE"
    assert summary["analysis"]["multimodal_cell_fraction"] < 0.15


def test_geometry_multiplicity_audit_rejects_geometry_clusters_explained_by_physical_drift(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_physical_drift_training(training)
    out_dir = tmp_path / "out"

    assert module.main(_args(training, out_dir)) == 0
    summary = json.loads((out_dir / "inverse_geometry_multiplicity_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["recommendation"]["decision"] == "WEAK_MULTIMODAL_EVIDENCE_KEEP_DETERMINISTIC_BASELINE"
    assert summary["analysis"]["multimodal_cell_fraction"] < 0.15
    assert summary["analysis"]["median_residualization_explained_fraction"] > 0.5


def test_geometry_multiplicity_audit_rejects_nonlinear_physical_target_modes(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_nonlinear_physical_drift_training(training)
    out_dir = tmp_path / "out"

    assert module.main(_args(training, out_dir)) == 0
    summary = json.loads((out_dir / "inverse_geometry_multiplicity_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["recommendation"]["decision"] == "WEAK_MULTIMODAL_EVIDENCE_KEEP_DETERMINISTIC_BASELINE"
    assert summary["analysis"]["multimodal_cell_fraction"] < 0.15
    assert summary["analysis"]["median_cluster_physical_knn_balanced_accuracy"] > 0.75


def test_coarse_100k_stage_never_authorizes_top_k_model(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_training(training, two_modes=True)
    out_dir = tmp_path / "out"
    args = _args(training, out_dir) + ["--evidence-stage", "exploratory_coarse"]

    assert module.main(args) == 0
    summary = json.loads((out_dir / "inverse_geometry_multiplicity_summary.json").read_text())
    assert summary["evidence_stage"] == "exploratory_coarse"
    assert summary["recommendation"]["decision"] == "COARSE_MULTIMODAL_SIGNAL_CONTINUE_TANDEM_AND_PLAN_FINE_AUDIT"
    assert summary["recommendation"]["eligible_for_top_k_ablation"] is False
    assert summary["recommendation"]["eligible_for_model_replacement"] is False
