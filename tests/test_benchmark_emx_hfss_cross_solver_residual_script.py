from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys

from matplotlib import image as mpl_image
from tests.rfic_transformer_inverse_design.shared import _write_touchstone


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_emx_hfss_cross_solver_residual.py"
    spec = importlib.util.spec_from_file_location("benchmark_emx_hfss_cross_solver_residual_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matrix(sample_index: int) -> tuple[np.ndarray, np.ndarray]:
    frequency = np.linspace(5.0e9, 60.0e9, 111)
    normalized = (frequency - frequency[0]) / (frequency[-1] - frequency[0])
    matrix = np.zeros((len(frequency), 4, 4), dtype=np.complex128)
    scale = 1.0 + 0.025 * sample_index
    for row in range(4):
        matrix[:, row, row] = scale * (0.08 + 0.01 * np.cos(2.0 * np.pi * normalized + 0.2 * row))
    for row in range(4):
        for column in range(row + 1, 4):
            amplitude = scale * (0.04 + 0.01 * (row + column))
            phase = (0.3 + 0.05 * sample_index + 0.04 * row) * normalized
            values = amplitude * np.exp(-1j * phase)
            matrix[:, row, column] = values
            matrix[:, column, row] = values
    return frequency, matrix


def _bias_template(frequency: np.ndarray) -> np.ndarray:
    normalized = (frequency - frequency[0]) / (frequency[-1] - frequency[0])
    delta = np.zeros((len(frequency), 4, 4), dtype=np.complex128)
    for row in range(4):
        for column in range(row, 4):
            values = (0.004 + 0.001 * (row + column)) * (
                np.sin(np.pi * normalized) + 0.5j * np.cos(np.pi * normalized)
            )
            delta[:, row, column] = values
            delta[:, column, row] = values
    return delta


def _write_record(root: Path, index: int, *, alternating_bias: bool = False, duplicate_geometry: str | None = None) -> Path:
    sample = root / f"sample_{index}"
    sample.mkdir(parents=True, exist_ok=True)
    frequency, emx_matrix = _matrix(index)
    direction = -1.0 if alternating_bias and index % 2 else 1.0
    hfss_matrix = 0.94 * emx_matrix + direction * _bias_template(frequency)
    emx = sample / "emx.s4p"
    hfss = sample / "hfss.s4p"
    _write_touchstone(emx, frequency, emx_matrix)
    _write_touchstone(hfss, frequency, hfss_matrix)
    target_metrics = {
        name: {"status": "PASS", "emx": 1.0, "hfss_ads": 1.04, "percent_error": 4.0}
        for name in ("lp_nh", "ls_nh", "q", "k")
    }
    full = sample / "full.json"
    full.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "emx_source": str(emx),
                "hfss_ads_source": str(hfss),
                "target_marker": {
                    "frequency_status": "PASS",
                    "nearest_frequency_hz": 15.0e9,
                    "metrics": target_metrics,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    geometry_sha = duplicate_geometry or hashlib.sha256(f"geometry-{index}".encode()).hexdigest()
    record = sample / "record.json"
    record.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "sample_id": f"sample-{index}",
                "full_grid_comparison_summary": str(full),
                "contract": {
                    "same_geometry_verified": True,
                    "same_process_stack_verified": True,
                    "same_port_mapping_verified": True,
                    "independent_geometry": True,
                    "geometry_contract_sha256": geometry_sha,
                    "expected_touchstone_suffix": ".s4p",
                    "expected_port_count": 4,
                    "port_pairs": "1,2:3,4",
                    "emx_touchstone_sha256": _sha(emx),
                    "hfss_touchstone_sha256": _sha(hfss),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return record


def _args(records: list[Path], out_dir: Path) -> list[str]:
    argv = ["--out-dir", str(out_dir), "--min-samples", "5"]
    for record in records:
        argv.extend(["--hfss-validation-record", str(record)])
    return argv


def test_stable_paired_bias_supports_leave_one_geometry_out_residual_review(tmp_path):
    module = _load_module()
    records = [_write_record(tmp_path, index) for index in range(6)]
    out_dir = tmp_path / "out"

    assert module.main(_args(records, out_dir)) == 0
    summary = json.loads((out_dir / "emx_hfss_cross_solver_residual_summary.json").read_text())
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["decision"] == "REVIEW_CROSS_SOLVER_RESIDUAL_FOR_CANDIDATE_RANKING_ONLY"
    assert summary["metrics"]["fullband_relative_improvement"] > 0.1
    assert summary["metrics"]["target_relative_improvement"] > 0.1
    assert all(summary["decision_checks"].values())
    figure = out_dir / "emx_hfss_cross_solver_residual_frequency_errors.png"
    assert figure.is_file()
    pixels = mpl_image.imread(figure)
    assert float(np.mean(pixels[0, 0, :3])) > 0.95


def test_geometry_dependent_bias_retains_raw_emx_baseline(tmp_path):
    module = _load_module()
    records = [_write_record(tmp_path, index, alternating_bias=True) for index in range(6)]
    out_dir = tmp_path / "out"

    assert module.main(_args(records, out_dir)) == 0
    summary = json.loads((out_dir / "emx_hfss_cross_solver_residual_summary.json").read_text())
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["decision"] == "KEEP_RAW_EMX_BASELINE_NO_RESIDUAL_MODEL"
    assert not all(summary["decision_checks"].values())


def test_insufficient_pairs_waits_without_fabricating_model_result(tmp_path):
    module = _load_module()
    records = [_write_record(tmp_path, index) for index in range(4)]
    out_dir = tmp_path / "out"

    assert module.main(_args(records, out_dir)) == 2
    summary = json.loads((out_dir / "emx_hfss_cross_solver_residual_summary.json").read_text())
    assert summary["overall_status"] == "WAITING_FOR_PAIRED_EMX_HFSS"
    assert summary["decision"] == "WAIT_FOR_MINIMUM_INDEPENDENT_REAL_S4P_PAIRS"
    assert not (out_dir / "emx_hfss_cross_solver_residual_frequency_errors.png").exists()


def test_duplicate_geometry_contract_is_rejected(tmp_path):
    module = _load_module()
    duplicate = hashlib.sha256(b"same-geometry").hexdigest()
    records = [
        _write_record(tmp_path, index, duplicate_geometry=duplicate if index < 2 else None)
        for index in range(5)
    ]
    out_dir = tmp_path / "out"

    assert module.main(_args(records, out_dir)) == 2
    summary = json.loads((out_dir / "emx_hfss_cross_solver_residual_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["geometry_contracts_unique"] is False
