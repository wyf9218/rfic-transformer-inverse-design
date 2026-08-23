from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys

from tests.test_plan_tandem_local_refinement_benchmark_script import (
    GEOMETRY_COLUMNS,
    _args as planner_args,
    _write_fixture,
)


ARMS = ("inverse_only", "inverse_lbfgsb")
FEATURE_COLUMNS = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")


def _load_script(name: str):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _build_plan_and_results(root: Path, *, budget: int = 8, missing_s4p: bool = False):
    planner = _load_script("plan_tandem_local_refinement_benchmark")
    summary, weights, predictions = _write_fixture(root)
    plan_dir = root / "plan"
    assert planner.main(planner_args(summary, weights, predictions, plan_dir, count=budget)) == 0
    plan_summary = plan_dir / "tandem_local_refinement_plan_summary.json"
    result_paths = {}
    for arm in ARMS:
        source_rows = list(csv.DictReader((plan_dir / f"arm_{arm}_candidates.csv").open()))
        output_rows = []
        for index, row in enumerate(source_rows):
            target = np.asarray([float(row[f"target__{name}"]) for name in FEATURE_COLUMNS])
            proxy = np.asarray([float(row[f"proxy__{name}"]) for name in FEATURE_COLUMNS])
            real = proxy.copy()
            if arm == "inverse_lbfgsb":
                real = target + 0.02 * (proxy - target)
            touchstone = root / f"{arm}_{index:03d}.s4p"
            if not missing_s4p:
                touchstone.write_text("! synthetic nonempty real EMX fixture\n", encoding="ascii")
            output_rows.append(
                {
                    **row,
                    "ok": "true",
                    "drc_status": "PASS",
                    "touchstone_path": str(touchstone),
                    "qp_center": float(real[2]),
                    "qs_center": float(real[2] + 0.05),
                    **{name: float(real[column]) for column, name in enumerate(FEATURE_COLUMNS)},
                }
            )
        result_path = root / f"result_{arm}.csv"
        _write_rows(result_path, output_rows)
        result_paths[arm] = result_path
    return plan_summary, result_paths


def _arguments(plan: Path, results: dict[str, Path], out_dir: Path, budget: int) -> list[str]:
    args = [
        "--plan-summary",
        str(plan),
        "--out-dir",
        str(out_dir),
        "--expected-arm-budget",
        str(budget),
        "--min-success-fraction",
        "1.0",
        "--bootstrap-repeats",
        "500",
    ]
    for arm in ARMS:
        args.extend(["--arm-result", f"{arm}={results[arm]}"])
    return args


def test_evaluator_uses_only_pair_matched_real_s4p_and_supports_improvement(tmp_path):
    module = _load_script("evaluate_tandem_local_refinement_real_emx")
    plan, results = _build_plan_and_results(tmp_path, budget=8)
    out_dir = tmp_path / "evaluation"

    assert module.main(_arguments(plan, results, out_dir, 8)) == 0
    summary = json.loads((out_dir / "tandem_local_refinement_real_emx_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["outcome_status"] == "REAL_EMX_REFINEMENT_IMPROVEMENT_SUPPORTED"
    assert summary["analysis"]["paired_count"] == 8
    assert summary["analysis"]["material_improvement_supported"] is True
    assert summary["checks"]["real_s4p_evidence_complete"] is True
    assert (out_dir / "tandem_local_refinement_real_emx_comparison.png").is_file()


def test_evaluator_rejects_proxy_rows_without_nonempty_real_s4p(tmp_path):
    module = _load_script("evaluate_tandem_local_refinement_real_emx")
    plan, results = _build_plan_and_results(tmp_path, budget=6, missing_s4p=True)
    out_dir = tmp_path / "evaluation"

    assert module.main(_arguments(plan, results, out_dir, 6)) == 2
    summary = json.loads((out_dir / "tandem_local_refinement_real_emx_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["outcome_status"] == "INCOMPLETE"
    assert summary["checks"]["real_s4p_evidence_complete"] is False
    assert summary["checks"]["paired_real_emx_analysis_available"] is False
