from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_existing_hfss_s8p_recompare_failure.py"
    spec = importlib.util.spec_from_file_location("diagnose_existing_hfss_s8p_recompare_failure_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_target_marker_csv(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "emx_hfss_ads_target_marker_metrics.csv").write_text(
        "requested_frequency_ghz,nearest_frequency_ghz,frequency_error_ghz,status,metric,metric_status,emx,hfss_ads,abs_error,percent_error\n"
        "15.0,15.0,0.0,FAIL,lp_nh,FAIL,2.0,0.1,1.9,95.0\n"
        "15.0,15.0,0.0,FAIL,ls_nh,FAIL,3.0,-0.05,3.05,101.7\n"
        "15.0,15.0,0.0,FAIL,q,FAIL,10.0,-1.0,11.0,110.0\n"
        "15.0,15.0,0.0,FAIL,k,FAIL,0.5,-0.4,0.9,180.0\n"
        "15.0,15.0,0.0,FAIL,kw,FAIL,0.5,-0.4,0.9,180.0\n"
        "15.0,15.0,0.0,FAIL,qp,FAIL,11.0,0.5,10.5,95.5\n"
        "15.0,15.0,0.0,FAIL,qs,FAIL,10.0,-1.0,11.0,110.0\n",
        encoding="utf-8",
    )


def test_diagnoses_small_inductance_sign_and_q_failures(tmp_path):
    mod = _load_module()
    compare_dir = tmp_path / "comparison"
    _write_target_marker_csv(compare_dir)
    strict_summary = tmp_path / "strict.json"
    strict_summary.write_text(
        json.dumps(
            {
                "candidate_count": 1,
                "pass_count": 0,
                "max_percent_error_limit": 10.0,
                "best": {"worst_percent_error": 1476.0, "worst_metric": "q"},
                "target15_best": {
                    "hfss_s8p": str(tmp_path / "hfss.s8p"),
                    "out_dir": str(compare_dir),
                    "target15_worst_metric": "k",
                    "target15_worst_percent_error": 180.0,
                    "target15_core_percent_errors": {"lp_nh": 95.0, "ls_nh": 101.7, "q": 110.0, "k": 180.0, "kw": 180.0},
                },
                "records": [
                    {
                        "hfss_s8p": str(tmp_path / "hfss.s8p"),
                        "out_dir": str(compare_dir),
                        "overall_status": "FAIL",
                        "worst_metric": "q",
                        "worst_percent_error": 1476.0,
                    }
                ],
                "target15_records": [
                    {
                        "hfss_s8p": str(tmp_path / "hfss.s8p"),
                        "out_dir": str(compare_dir),
                        "target15_worst_metric": "k",
                        "target15_worst_percent_error": 180.0,
                        "target15_core_percent_errors": {"lp_nh": 95.0, "ls_nh": 101.7, "q": 110.0, "k": 180.0, "kw": 180.0},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    status = mod.main(["--strict-recompare-summary", str(strict_summary), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    summary = json.loads((tmp_path / "out" / "existing_hfss_s8p_failure_diagnosis_summary.json").read_text(encoding="utf-8"))
    modes = summary["failure_mode_counts"]
    assert summary["overall_status"] == "DIAGNOSIS_READY"
    assert modes["HFSS_INDUCTANCE_SCALE_TOO_SMALL_CHECK_GEOMETRY_UNITS_OR_METAL_STACK"] == 1
    assert modes["COUPLING_SIGN_MISMATCH_CHECK_PORT_ORDER_POLARITY_WINDING_DIRECTION"] == 1
    assert modes["NON_POSITIVE_Q_CHECK_LOSS_MODEL_TERMINAL_REFERENCE_OR_GROUND"] == 1
    assert summary["hfss_to_emx_ratio_statistics"]["lp_nh"]["median"] == 0.05
    assert summary["sign_mismatch_counts"]["k"] == 1


def test_existing_pass_count_is_reported_without_unlocking_logic(tmp_path):
    mod = _load_module()
    strict_summary = tmp_path / "strict.json"
    strict_summary.write_text(
        json.dumps(
            {
                "candidate_count": 1,
                "pass_count": 1,
                "best": {"worst_percent_error": 2.0, "worst_metric": "k"},
                "target15_best": {"target15_worst_percent_error": 2.0, "target15_worst_metric": "k"},
                "records": [],
                "target15_records": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    status = mod.main(["--strict-recompare-summary", str(strict_summary), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    summary = json.loads((tmp_path / "out" / "existing_hfss_s8p_failure_diagnosis_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "HISTORICAL_HFSS_RECOMPARE_HAS_PASSING_CANDIDATE"
