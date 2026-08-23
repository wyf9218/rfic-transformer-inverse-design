from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import json
import sys


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
RANGES = ((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8))


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_proxy_uncertainty_real_emx_reliability.py"
    spec = importlib.util.spec_from_file_location("proxy_uncertainty_audit_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _paired_source(root: Path, *, count: int, offset: int, informative: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(19 + offset)
    rows = []
    for local_index in range(count):
        index = offset + local_index
        progress = (local_index + 0.5) / count
        uncertainty_fraction = 0.002 + 0.15 * progress
        error_fraction = 0.80 * uncertainty_fraction if informative else 0.12 * (1.0 - progress) + 0.002
        real = np.asarray(
            [rng.uniform(low + 0.20 * (high - low), high - 0.20 * (high - low)) for low, high in RANGES],
            dtype=float,
        )
        signs = np.asarray([1.0, -1.0, 1.0 if index % 2 == 0 else -1.0, -1.0])
        spans = np.asarray([high - low for low, high in RANGES], dtype=float)
        uncertainty = uncertainty_fraction * spans
        predicted = real + signs * error_fraction * spans
        touchstone = root / f"sample_{index:05d}.s4p"
        touchstone.write_text("! synthetic contract fixture, not scientific evidence\n", encoding="ascii")
        row: dict[str, object] = {
            "candidate_id": f"candidate_{index:05d}",
            "ok": "true",
            "touchstone_path": str(touchstone),
        }
        for geometry_index in range(10):
            row[f"geom__g{geometry_index}"] = index + 0.001 * geometry_index
        for axis, feature in enumerate(FEATURES):
            row[feature] = real[axis]
            row[f"queue__raw_pred_{feature}"] = predicted[axis]
            row[f"queue__pred_uncertainty_{feature}"] = uncertainty[axis]
        rows.append(row)
    path = root / "dataset_rows.csv"
    _write_csv(path, rows)
    return path


def test_informative_uncertainty_is_approved_on_latest_real_emx_round(tmp_path):
    audit = _load()
    history = _paired_source(tmp_path / "history", count=120, offset=0, informative=True)
    latest = _paired_source(tmp_path / "latest", count=80, offset=1000, informative=True)
    out_dir = tmp_path / "audit"

    assert audit.main(
        [
            "--paired-csv",
            str(history),
            "--paired-csv",
            str(latest),
            "--out-dir",
            str(out_dir),
            "--holdout-mode",
            "latest-source",
            "--no-plots",
        ]
    ) == 0
    summary = json.loads((out_dir / "proxy_uncertainty_real_emx_reliability_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "UNCERTAINTY_RELIABLE_FOR_TARGET_HIT_ABLATION_ONLY"
    assert summary["eligible_for_acquisition_ablation"] is True
    assert summary["split"]["train_geometry_count"] == 120
    assert summary["split"]["holdout_geometry_count"] == 80
    assert len(summary["split"]["train_real_target_sha256"]) == 64
    assert len(summary["split"]["holdout_real_target_sha256"]) == 64
    metrics = summary["holdout_metrics"]
    assert metrics["aggregate_spearman_uncertainty_vs_error"] > 0.9
    assert metrics["high_vs_low_uncertainty_error_ratio"] > 2.0
    assert metrics["low_minus_high_uncertainty_mean_1d_bin_accuracy"] > 0.05


def test_anti_informative_uncertainty_is_rejected(tmp_path):
    audit = _load()
    history = _paired_source(tmp_path / "history", count=120, offset=0, informative=False)
    latest = _paired_source(tmp_path / "latest", count=80, offset=1000, informative=False)
    out_dir = tmp_path / "audit"

    assert audit.main(
        [
            "--paired-csv",
            str(history),
            "--paired-csv",
            str(latest),
            "--out-dir",
            str(out_dir),
            "--holdout-mode",
            "latest-source",
            "--no-plots",
        ]
    ) == 0
    summary = json.loads((out_dir / "proxy_uncertainty_real_emx_reliability_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "DO_NOT_USE_UNCERTAINTY_FOR_TARGET_HIT_RANKING"
    assert summary["eligible_for_acquisition_ablation"] is False
    assert summary["holdout_metrics"]["aggregate_spearman_uncertainty_vs_error"] < 0.0


def test_missing_uncertainty_provenance_waits(tmp_path):
    audit = _load()
    source = _paired_source(tmp_path / "source", count=100, offset=0, informative=True)
    rows = list(csv.DictReader(source.open(newline="", encoding="utf-8")))
    for row in rows:
        for feature in FEATURES:
            row.pop(f"queue__pred_uncertainty_{feature}", None)
    _write_csv(source, rows)
    out_dir = tmp_path / "audit"

    assert audit.main(["--paired-csv", str(source), "--out-dir", str(out_dir), "--no-plots"]) == 2
    summary = json.loads((out_dir / "proxy_uncertainty_real_emx_reliability_summary.json").read_text())
    assert summary["overall_status"] == "WAITING"
    assert summary["decision"] == "WAIT_FOR_UNCERTAINTY_AND_REAL_EMX_PAIRS"
    assert summary["eligible_for_acquisition_ablation"] is False
    assert summary["input_stats"]["source_contract_failure_count"] == 1
