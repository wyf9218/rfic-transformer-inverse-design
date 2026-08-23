from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys
from types import SimpleNamespace


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_extraction_frequency_stability.py"
    spec = importlib.util.spec_from_file_location(
        "audit_physical_feature_extraction_frequency_stability_script",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_transformer(path: Path, frequencies: np.ndarray) -> None:
    target = default_target_spec()
    differential = build_lumped_transformer_sparameters(
        freqs_hz=frequencies,
        target=target,
        q_primary=18.0,
        q_secondary=16.0,
    )
    single = differential_2port_to_4port_s(
        freqs_hz=frequencies,
        s_diff=differential.s_matrix,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


def _write_dataset(root: Path, *, bad_last_grid: bool = False) -> None:
    rows = []
    full_grid = np.linspace(5.0e9, 60.0e9, 111)
    for index in range(4):
        path = root / f"sample_{index}.s4p"
        frequencies = full_grid[:-1] if bad_last_grid and index == 3 else full_grid
        _write_transformer(path, frequencies)
        rows.append({"evaluation": f"sample_{index}", "ok": "true", "touchstone_path": str(path)})
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_frequency_stability_audit_uses_same_real_s4p_grid(tmp_path):
    module = _load_module()
    _write_dataset(tmp_path)
    out_dir = tmp_path / "out"

    status = module.main(
        [
            "--dataset-dir",
            str(tmp_path),
            "--out-dir",
            str(out_dir),
            "--min-files",
            "2",
            "--max-files",
            "4",
        ]
    )

    assert status == 0
    summary = json.loads((out_dir / "physical_feature_frequency_stability_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["sampled_touchstone_count"] == 4
    assert summary["successful_touchstone_count"] == 4
    assert set(summary["target_summaries"]) == {"f5ghz", "f15ghz"}
    assert summary["target_summaries"]["f5ghz"]["plausible_fraction"] == 1.0
    assert summary["target_summaries"]["f15ghz"]["plausible_fraction"] == 1.0
    assert summary["recommendation"]["status"] == "AUDIT_ONLY_NO_AUTOMATIC_CONTRACT_CHANGE"
    assert (out_dir / "physical_feature_frequency_stability_rows.csv").is_file()
    assert (out_dir / "physical_feature_frequency_stability_report.md").is_file()


def test_frequency_stability_audit_rejects_grid_failure(tmp_path):
    module = _load_module()
    _write_dataset(tmp_path, bad_last_grid=True)
    out_dir = tmp_path / "out"

    status = module.main(
        [
            "--dataset-dir",
            str(tmp_path),
            "--out-dir",
            str(out_dir),
            "--min-files",
            "2",
            "--max-files",
            "4",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((out_dir / "physical_feature_frequency_stability_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["touchstone_success_fraction"] is False
    assert summary["successful_touchstone_count"] == 3


def test_recommendation_requires_material_worst_feature_improvement():
    module = _load_module()
    args = SimpleNamespace(material_improvement_fraction=0.10)
    summaries = {
        "f5ghz": {"worst_feature_p95_forward_relative_step": 0.05},
        "f15ghz": {"worst_feature_p95_forward_relative_step": 0.10},
    }

    result = module._recommendation(summaries, (5.0, 15.0), args)

    assert result["decision"] == "LOW_FREQUENCY_MORE_STABLE_RUN_SHARED_ROW_MODEL_ABLATION"
    assert result["status"] == "AUDIT_ONLY_NO_AUTOMATIC_CONTRACT_CHANGE"
