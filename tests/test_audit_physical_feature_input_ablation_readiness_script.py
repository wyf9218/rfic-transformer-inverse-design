import csv
import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_input_ablation_readiness.py"
    spec = importlib.util.spec_from_file_location("audit_physical_feature_input_ablation_readiness_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_dataset(path: Path, *, rows: int, q_mismatch: bool = False, frequency_mismatch: bool = False) -> None:
    fieldnames = [
        "ok",
        "touchstone_path",
        "lp_nh_center",
        "ls_nh_center",
        "q_center",
        "k_abs_center",
        "qp_center",
        "qs_center",
        "sparam_freq_start_hz",
        "sparam_freq_stop_hz",
        "sparam_freq_step_hz",
        "sparam_freq_points",
        "geom__width_um",
        "geom__height_um",
        *(
            "lp_nh_min",
            "lp_nh_max",
            "ls_nh_min",
            "ls_nh_max",
            "qp_min",
            "qp_max",
            "qs_min",
            "qs_max",
            "k_min",
            "k_max",
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index in range(rows):
            qp = 12.0 + index * 0.01
            qs = 10.0 + index * 0.02
            q = min(qp, qs)
            if q_mismatch and index == rows - 1:
                q += 0.5
            writer.writerow(
                {
                    "ok": "true",
                    "touchstone_path": f"sample_{index}.s4p",
                    "lp_nh_center": 0.7 + index * 0.001,
                    "ls_nh_center": 0.9 + index * 0.001,
                    "q_center": q,
                    "k_abs_center": 0.4 + index * 0.0001,
                    "qp_center": qp,
                    "qs_center": qs,
                    "sparam_freq_start_hz": 5.0e9,
                    "sparam_freq_stop_hz": 59.5e9 if frequency_mismatch and index == rows - 1 else 60.0e9,
                    "sparam_freq_step_hz": 0.5e9,
                    "sparam_freq_points": 111,
                    "geom__width_um": 200.0 + index,
                    "geom__height_um": 220.0 + index,
                    "lp_nh_min": 0.5,
                    "lp_nh_max": 2.0,
                    "ls_nh_min": 0.6,
                    "ls_nh_max": 2.2,
                    "qp_min": -2.0,
                    "qp_max": 14.0,
                    "qs_min": -3.0,
                    "qs_max": 13.0,
                    "k_min": -0.7,
                    "k_max": 0.7,
                }
            )


def _run(module, dataset: Path, out_dir: Path, min_rows: int) -> tuple[int, dict]:
    status = module.main(
        [
            "--dataset-csv",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--min-rows",
            str(min_rows),
            "--no-fail-exit",
        ]
    )
    summary = json.loads((out_dir / "physical_feature_input_ablation_readiness_summary.json").read_text(encoding="utf-8"))
    return status, summary


def test_qp_qs_ablation_readiness_passes_only_with_complete_real_contract(tmp_path):
    module = _load_module()
    dataset = tmp_path / "dataset_rows.csv"
    _write_dataset(dataset, rows=64)

    status, summary = _run(module, dataset, tmp_path / "out", 50)

    assert status == 0
    assert summary["overall_status"] == "PASS"
    assert summary["checks"]["q_equals_min_qp_qs"] is True
    assert summary["checks"]["frequency_contract_exact"] is True
    assert summary["checks"]["geometry_unique"] is True
    assert summary["input_ablations"]["shared_split_required"] is True


def test_readiness_waits_before_200k_without_hiding_evidence(tmp_path):
    module = _load_module()
    dataset = tmp_path / "dataset_rows.csv"
    _write_dataset(dataset, rows=24)

    status, summary = _run(module, dataset, tmp_path / "out", 50)

    assert status == 0
    assert summary["overall_status"] == "WAITING_FOR_200K"
    assert summary["checks"]["minimum_rows_reached"] is False
    assert summary["counts"]["qp_qs_valid"] == 24


def test_readiness_rejects_q_semantics_or_frequency_drift(tmp_path):
    module = _load_module()
    q_dataset = tmp_path / "q_bad.csv"
    f_dataset = tmp_path / "f_bad.csv"
    _write_dataset(q_dataset, rows=64, q_mismatch=True)
    _write_dataset(f_dataset, rows=64, frequency_mismatch=True)

    _, q_summary = _run(module, q_dataset, tmp_path / "q_out", 50)
    _, f_summary = _run(module, f_dataset, tmp_path / "f_out", 50)

    assert q_summary["overall_status"] == "FAIL"
    assert q_summary["checks"]["q_equals_min_qp_qs"] is False
    assert q_summary["q_mismatch_examples"]
    assert f_summary["overall_status"] == "FAIL"
    assert f_summary["checks"]["frequency_contract_exact"] is False
