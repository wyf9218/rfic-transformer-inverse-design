from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import itertools
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_uniformity.py"
    spec = importlib.util.spec_from_file_location("audit_physical_feature_uniformity_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(indices: tuple[int, int, int, int]) -> dict[str, float]:
    lp, ls, q, k = indices
    return {
        "lp_nh_center": 0.5 + (lp + 0.5) * (2.5 / 4.0),
        "ls_nh_center": 0.5 + (ls + 0.5) * (2.5 / 4.0),
        "q_center": 5.0 + (q + 0.5) * 5.0,
        "k_center": (k + 0.5) * 0.2,
    }


def _write_rows(path: Path, rows: list[dict[str, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _run(module, csv_path: Path, out_dir: Path) -> int:
    return module.main(
        [
            "--training-csv",
            str(csv_path),
            "--out-dir",
            str(out_dir),
            "--min-valid-count",
            "1",
            "--bins",
            "4",
            "--pair-bins",
            "4",
            "--four-d-bins",
            "4",
            "--min-1d-occupied-frac",
            "0",
            "--min-1d-entropy-frac",
            "0",
            "--max-1d-bin-imbalance",
            "1000000000",
            "--min-pair-occupied-frac",
            "0",
            "--min-pair-entropy-frac",
            "0",
            "--min-four-d-occupied-frac",
            "0.5",
            "--min-four-d-entropy-frac",
            "0.8",
            "--max-four-d-bin-imbalance",
            "4",
            "--lp-min-nh",
            "0.5",
            "--lp-max-nh",
            "3",
            "--ls-min-nh",
            "0.5",
            "--ls-max-nh",
            "3",
            "--q-min",
            "5",
            "--q-max",
            "25",
            "--k-min",
            "0",
            "--k-max",
            "0.8",
            "--require-explicit-ranges",
            "--require-four-d-gate",
            "--no-plots",
            "--no-fail-exit",
        ]
    )


def test_uniform_4d_counts_pass_occupancy_entropy_and_imbalance(tmp_path):
    module = _load_module()
    rows = [_row(indices) for indices in itertools.product(range(4), repeat=4) for _ in range(2)]
    csv_path = tmp_path / "uniform.csv"
    _write_rows(csv_path, rows)

    assert _run(module, csv_path, tmp_path / "out") == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_uniformity_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    four_d = summary["four_dimensional_uniformity"]
    assert four_d["occupied_fraction"] == 1.0
    assert four_d["normalized_entropy"] == 1.0
    assert four_d["max_to_min_nonzero_ratio"] == 1.0


def test_4d_concentration_fails_even_when_occupancy_reaches_half(tmp_path):
    module = _load_module()
    cells = [indices for indices in itertools.product(range(4), repeat=4) if sum(indices) % 2 == 0]
    rows = [_row(indices) for indices in cells]
    rows.extend([_row(cells[0])] * 1000)
    csv_path = tmp_path / "concentrated.csv"
    _write_rows(csv_path, rows)

    assert _run(module, csv_path, tmp_path / "out") == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_uniformity_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    four_d = summary["four_dimensional_uniformity"]
    assert four_d["occupied_fraction"] == 0.5
    assert four_d["normalized_entropy"] < 0.8
    failed = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "Lp/Ls/Q/K 4D normalized entropy" in failed


def test_4d_nonzero_bin_imbalance_is_an_independent_gate(tmp_path):
    module = _load_module()
    cells = [indices for indices in itertools.product(range(4), repeat=4) if sum(indices) % 2 == 0]
    rows = [_row(indices) for indices in cells]
    rows.extend([_row(cells[0])] * 4)
    csv_path = tmp_path / "imbalanced.csv"
    _write_rows(csv_path, rows)

    assert _run(module, csv_path, tmp_path / "out") == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_uniformity_summary.json").read_text())
    four_d = summary["four_dimensional_uniformity"]
    assert four_d["occupied_fraction"] == 0.5
    assert four_d["normalized_entropy"] >= 0.8
    assert four_d["max_to_min_nonzero_ratio"] == 5.0
    failed = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "Lp/Ls/Q/K 4D nonzero-bin imbalance" in failed


def test_marginal_plot_annotation_uses_a_real_line_break():
    module = _load_module()

    annotation = module._marginal_annotation(
        {"normalized_entropy": 0.7824, "occupied_fraction": 1.0}
    )

    assert annotation == "H=0.782\nocc=1.00"
    assert "\\n" not in annotation
