from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_v66_postrun_gate_evidence.py"
    spec = importlib.util.spec_from_file_location("summarize_v66_postrun_gate_evidence_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(path: Path, variants_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "variants": [
                    {"name": "v66a", "postrun_out_dir": str(variants_root / "v66a")},
                    {"name": "v66b", "postrun_out_dir": str(variants_root / "v66b")},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_file(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\n", encoding="utf-8")
    return str(path)


def _write_pass_postrun(out_dir: Path, *, worst: float) -> None:
    artifacts_dir = out_dir / "artifacts"
    emx_s8p = _write_file(artifacts_dir / "emx.s8p")
    hfss_s8p = _write_file(artifacts_dir / "hfss.s8p")
    compare = _write_file(artifacts_dir / "compare.json")
    marker = artifacts_dir / "marker.csv"
    marker.write_text(
        "metric,percent_error\nlp_nh,1.0\nls_nh,2.0\nq,3.0\nk,4.0\nkw,4.0\n",
        encoding="utf-8",
    )
    plot_summary = artifacts_dir / "plot_summary.json"
    plot_summary.write_text(
        json.dumps(
            {
                "artifacts": {
                    "emx_common_plot": _write_file(artifacts_dir / "emx.png"),
                    "hfss_common_plot": _write_file(artifacts_dir / "hfss.png"),
                    "overlay_common_plot": _write_file(artifacts_dir / "overlay.png"),
                    "metric_csv": _write_file(artifacts_dir / "metrics.csv"),
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "s8p_hfss_postrun_validation_summary.json").write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION",
                "frequency_grid_mode": "final_5_60_0p5_111",
                "final_acceptance_candidate": True,
                "sample_count": 1,
                "records": [
                    {
                        "status": "PASS",
                        "evaluation": "sample_a",
                        "emx_s8p": emx_s8p,
                        "hfss_s8p": hfss_s8p,
                        "target_marker_csv": str(marker),
                        "compare_summary": compare,
                        "ads_style_plot_summary": str(plot_summary),
                        "worst_metric": "kw",
                        "worst_percent_error": worst,
                        "port_pairs": "1,4:5,6",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_waiting_postrun(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "s8p_hfss_postrun_validation_summary.json").write_text(
        json.dumps(
            {
                "overall_status": "WAITING_FOR_HFSS",
                "decision": "WAIT_FOR_EXPORTED_HFSS_S8P",
                "frequency_grid_mode": "final_5_60_0p5_111",
                "final_acceptance_candidate": True,
                "records": [{"status": "WAITING_FOR_HFSS", "worst_percent_error": None}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_reports_waiting_when_hfss_exports_are_missing(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants = tmp_path / "variants"
    _write_plan(plan, variants)
    _write_waiting_postrun(variants / "v66a")
    _write_waiting_postrun(variants / "v66b")

    status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_postrun_gate_evidence_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["decision"] == "WAIT_FOR_V66_EXPORTED_HFSS_S8P"
    assert summary["selected_variant"] == {}


def test_selects_best_passing_variant_with_artifacts(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants = tmp_path / "variants"
    _write_plan(plan, variants)
    _write_pass_postrun(variants / "v66a", worst=4.0)
    _write_pass_postrun(variants / "v66b", worst=2.0)

    status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_postrun_gate_evidence_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "V66_EMX_HFSS_GATE_EVIDENCE_READY"
    assert summary["selected_variant"]["name"] == "v66b"
    assert summary["selected_variant"]["passes_acceptance_gate"] is True
    assert (tmp_path / "out" / "v66_postrun_gate_variant_summary.csv").is_file()
