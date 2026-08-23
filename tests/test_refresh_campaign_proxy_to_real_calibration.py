from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import json
import sys


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
RANGES = ((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8))


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh_campaign_proxy_to_real_calibration.py"
    spec = importlib.util.spec_from_file_location("refresh_campaign_proxy_calibration_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_round(rounds: Path, number: int, *, count: int, offset: int, mode: str) -> Path:
    rng = np.random.default_rng(100 + number)
    real_emx = rounds / f"round_{number:03d}_checkpoint_01" / "real_emx"
    dataset = real_emx / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(count):
        sample = offset + index
        real = np.asarray([rng.uniform(lower, upper) for lower, upper in RANGES], dtype=float)
        if mode == "biased":
            predicted = np.asarray(
                [0.72 * real[0] + 0.30, 1.18 * real[1] - 0.20, 0.70 * real[2] + 3.0, 0.76 * real[3] + 0.08]
            )
        elif mode == "already_calibrated":
            predicted = np.array(real, copy=True)
        else:
            raise ValueError(mode)
        touchstone = dataset / f"sample_{sample:05d}.s4p"
        touchstone.write_text("! synthetic fixture, not scientific evidence\n", encoding="ascii")
        row: dict[str, object] = {
            "evaluation": f"sample_{sample:05d}",
            "ok": "true",
            "touchstone_path": str(touchstone),
            "lp_nh_center": real[0],
            "ls_nh_center": real[1],
            "qp_center": real[2],
            "qs_center": min(25.0, real[2] + 0.1),
            "k_center": -real[3],
        }
        for geometry_index in range(10):
            row[f"geom__g{geometry_index}"] = sample + geometry_index * 0.001
        for feature_index, feature in enumerate(FEATURES):
            row[f"queue__raw_pred_{feature}"] = predicted[feature_index]
        rows.append(row)
    with (dataset / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (real_emx / "round.complete").touch()
    return dataset / "dataset_rows.csv"


def test_campaign_refresh_activates_stable_bias_then_revokes_on_latest_round_shift(tmp_path):
    module = _load()
    rounds = tmp_path / "rounds"
    active = tmp_path / "calibration" / "active.json"
    _write_round(rounds, 1, count=120, offset=0, mode="biased")

    first_out = tmp_path / "calibration" / "round_001"
    assert module.main(
        [
            "--rounds-root",
            str(rounds),
            "--out-dir",
            str(first_out),
            "--active-json",
            str(active),
            "--trigger-round",
            "1",
        ]
    ) == 0
    first = json.loads((first_out / "campaign_proxy_to_real_calibration_refresh_summary.json").read_text())
    assert first["holdout_mode"] == "hash"
    assert first["decision"] == "ACTIVATE_CALIBRATION_FOR_NEXT_ACQUISITION_ONLY"
    assert first["calibration_active"] is True
    assert first["uncertainty_reliability"]["overall_status"] == "WAITING"
    assert first["uncertainty_reliability"]["automatic_ranking_change"] is False
    assert active.is_file()

    _write_round(rounds, 2, count=80, offset=1000, mode="already_calibrated")
    second_out = tmp_path / "calibration" / "round_002"
    assert module.main(
        [
            "--rounds-root",
            str(rounds),
            "--out-dir",
            str(second_out),
            "--active-json",
            str(active),
            "--trigger-round",
            "2",
        ]
    ) == 0
    second = json.loads((second_out / "campaign_proxy_to_real_calibration_refresh_summary.json").read_text())
    assert second["holdout_mode"] == "latest-source"
    assert second["compatible_source_count"] == 2
    assert second["decision"] == "USE_RAW_PROXY_FOR_NEXT_ACQUISITION"
    assert second["calibration_active"] is False
    assert not active.exists()


def test_campaign_refresh_removes_stale_mapping_when_no_compatible_real_returns(tmp_path):
    module = _load()
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    active = tmp_path / "calibration" / "active.json"
    active.parent.mkdir()
    active.write_text('{"stale": true}\n', encoding="utf-8")
    out_dir = tmp_path / "calibration" / "empty"

    assert module.main(
        [
            "--rounds-root",
            str(rounds),
            "--out-dir",
            str(out_dir),
            "--active-json",
            str(active),
            "--no-fail-exit",
        ]
    ) == 0
    summary = json.loads((out_dir / "campaign_proxy_to_real_calibration_refresh_summary.json").read_text())
    assert summary["overall_status"] == "WAITING"
    assert summary["decision"] == "WAIT_FOR_PAIRED_REAL_EMX_RETURNS"
    assert summary["uncertainty_reliability"]["overall_status"] == "WAITING"
    assert not active.exists()
