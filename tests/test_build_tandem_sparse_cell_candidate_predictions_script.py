from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


INPUT_COLUMNS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]
FEATURE_NAMES = [column.removeprefix("input__") for column in INPUT_COLUMNS]
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


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / "build_tandem_sparse_cell_candidate_predictions.py"
    spec = importlib.util.spec_from_file_location("build_tandem_sparse_cell_candidate_predictions_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, *, feature_columns=INPUT_COLUMNS, allocation: int = 8):
    summary = root / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "training_count": 100,
                "input_columns": feature_columns,
                "geometry_columns": GEOMETRY_COLUMNS,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    forward_weight = np.zeros((10, 4), dtype=float)
    forward_weight[:4, :4] = np.eye(4)
    inverse_weight = np.zeros((4, 10), dtype=float)
    inverse_weight[:4, :4] = np.eye(4) * 0.7
    weights = root / "weights.npz"
    np.savez_compressed(
        weights,
        forward_weight_0=forward_weight,
        forward_bias_0=np.zeros(4),
        inverse_weight_0=inverse_weight,
        inverse_bias_0=np.zeros(10),
        normalization__x_mean=np.zeros(4),
        normalization__x_scale=np.ones(4),
        normalization__y_mean=np.zeros(10),
        normalization__y_scale=np.ones(10),
        normalization__geometry_lower=np.zeros(10),
        normalization__geometry_upper=np.ones(10),
        normalization__response_loss_physical_spans=np.ones(4),
    )
    targets = root / "targets.csv"
    rows = []
    for rank, (lo, hi) in enumerate(((0.10, 0.35), (0.35, 0.65)), start=1):
        row = {
            "bin_key": f"bin_{rank}",
            "rank": rank,
            "recommended_new_samples": allocation // 2,
            "deficit": 10,
            "current_count": 0,
            "priority_weight": 1.0,
        }
        for feature in FEATURE_NAMES:
            row[f"{feature}__min"] = lo
            row[f"{feature}__max"] = hi
            row[f"{feature}__target"] = 0.5 * (lo + hi)
        rows.append(row)
    with targets.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return summary, weights, targets


def _args(summary: Path, weights: Path, targets: Path, out_dir: Path, count: int = 8):
    return [
        "--tandem-summary",
        str(summary),
        "--weights-npz",
        str(weights),
        "--targets-csv",
        str(targets),
        "--out-dir",
        str(out_dir),
        "--candidate-count",
        str(count),
        "--min-source-rows",
        "100",
        "--seed",
        "77",
    ]


def test_sparse_cell_arm_is_deterministic_bounded_and_unlabeled(tmp_path):
    module = _load_module()
    summary, weights, targets = _write_fixture(tmp_path)
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"

    assert module.main(_args(summary, weights, targets, out_a)) == 0
    assert module.main(_args(summary, weights, targets, out_b)) == 0
    result = json.loads((out_a / "tandem_sparse_cell_candidate_predictions_summary.json").read_text())
    assert result["overall_status"] == "PASS"
    assert result["outcome_status"] == "AWAITING_REAL_EMX"
    assert result["analysis"]["selected_candidate_count"] == 8
    assert all(result["checks"].values())

    rows_a = list(csv.DictReader((out_a / "tandem_sparse_cell_candidate_predictions.csv").open()))
    rows_b = list(csv.DictReader((out_b / "tandem_sparse_cell_candidate_predictions.csv").open()))
    assert rows_a == rows_b
    assert len(rows_a) == 8
    assert {row["label_status"] for row in rows_a} == {"AWAITING_REAL_EMX"}
    assert {row["drc_status"] for row in rows_a} == {"NOT_EVALUATED_REQUIRED_BEFORE_REAL_EMX"}
    assert not any(column.startswith("pred_uncertainty_") for column in rows_a[0])
    assert len({row["geometry_digest_sha256"] for row in rows_a}) == 8
    for row in rows_a:
        geometry = np.asarray([float(row[column]) for column in GEOMETRY_COLUMNS])
        assert np.all(geometry >= 0.0)
        assert np.all(geometry <= 1.0)
        for feature in FEATURE_NAMES:
            assert float(row[f"target__{feature}__min"]) <= float(row[f"target__{feature}"])
            assert float(row[f"target__{feature}"]) <= float(row[f"target__{feature}__max"])


def test_sparse_cell_arm_rejects_feature_schema_mismatch(tmp_path):
    module = _load_module()
    summary, weights, targets = _write_fixture(tmp_path, feature_columns=INPUT_COLUMNS[:-1] + ["input__k_center"])
    out_dir = tmp_path / "out"

    assert module.main(_args(summary, weights, targets, out_dir)) == 2
    result = json.loads((out_dir / "tandem_sparse_cell_candidate_predictions_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["input_contract_is_lp_ls_q_absk"] is False
    assert result["checks"]["candidate_budget_exact"] is False


def test_sparse_cell_arm_rejects_missing_or_insufficient_evidence(tmp_path):
    module = _load_module()
    summary, weights, targets = _write_fixture(tmp_path, allocation=4)
    weights.unlink()
    out_dir = tmp_path / "out"

    assert module.main(_args(summary, weights, targets, out_dir, count=8)) == 2
    result = json.loads((out_dir / "tandem_sparse_cell_candidate_predictions_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["weights_model_available"] is False
    assert result["checks"]["target_allocations_cover_requested_budget"] is False
    assert result["analysis"]["selected_candidate_count"] == 0


def test_sparse_cell_arm_rejects_collapsed_duplicate_geometry(tmp_path):
    module = _load_module()
    summary, weights, targets = _write_fixture(tmp_path)
    with np.load(weights) as archive:
        values = {key: np.asarray(archive[key]) for key in archive.files}
    values["inverse_weight_0"] = np.zeros_like(values["inverse_weight_0"])
    np.savez_compressed(weights, **values)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary, weights, targets, out_dir)) == 2
    result = json.loads((out_dir / "tandem_sparse_cell_candidate_predictions_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["candidate_budget_exact"] is False
    assert result["checks"]["independent_geometry_vectors_unique"] is False
    assert result["analysis"]["selected_candidate_count"] == 1
