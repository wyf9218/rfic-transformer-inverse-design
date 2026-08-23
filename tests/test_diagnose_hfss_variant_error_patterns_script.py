from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import json
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_hfss_variant_error_patterns.py"
    spec = importlib.util.spec_from_file_location("diagnose_hfss_variant_error_patterns_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_diagnosis_ranks_patterns_and_blocks_large_scale_generation(tmp_path):
    module = _load_module()
    scan_csv = tmp_path / "scan.csv"
    fieldnames = [
        "variant",
        "status",
        "target_max_pct",
        "core_sum_pct",
        "lp_nh_err_pct",
        "ls_nh_err_pct",
        "q_err_pct",
        "k_err_pct",
        "kw_err_pct",
        "qp_err_pct",
        "qs_err_pct",
        "emx_lp_nh",
        "hfss_lp_nh",
        "emx_ls_nh",
        "hfss_ls_nh",
        "emx_q",
        "hfss_q",
        "emx_k",
        "hfss_k",
        "hfss_source",
        "summary",
    ]
    rows = [
        {
            "variant": "diagnostics_v52_local_air_direct_keep_frame_3pt_ground_unused_compare",
            "status": "FAIL",
            "target_max_pct": "29",
            "core_sum_pct": "87",
            "lp_nh_err_pct": "23",
            "ls_nh_err_pct": "29",
            "q_err_pct": "17",
            "k_err_pct": "18",
            "kw_err_pct": "18",
            "qp_err_pct": "37",
            "qs_err_pct": "17",
            "emx_lp_nh": "1.9",
            "hfss_lp_nh": "1.46",
            "emx_ls_nh": "2.5",
            "hfss_ls_nh": "1.75",
            "emx_q": "10",
            "hfss_q": "12",
            "emx_k": "0.48",
            "hfss_k": "0.39",
            "hfss_source": "v52.s8p",
            "summary": "v52.json",
        },
        {
            "variant": "diagnostics_v40_m5united_allm5_5_60_ground_unused_compare",
            "status": "FAIL",
            "target_max_pct": "70",
            "core_sum_pct": "221",
            "lp_nh_err_pct": "56",
            "ls_nh_err_pct": "70",
            "q_err_pct": "58",
            "k_err_pct": "36",
            "kw_err_pct": "36",
            "qp_err_pct": "50",
            "qs_err_pct": "58",
            "emx_lp_nh": "1.9",
            "hfss_lp_nh": "0.84",
            "emx_ls_nh": "2.5",
            "hfss_ls_nh": "0.75",
            "emx_q": "10",
            "hfss_q": "16",
            "emx_k": "0.48",
            "hfss_k": "0.31",
            "hfss_source": "v40.s8p",
            "summary": "v40.json",
        },
        {
            "variant": "diagnostics_v48_sidecar_stack_local_air_3pt_ground_unused_compare",
            "status": "FAIL",
            "target_max_pct": "31",
            "core_sum_pct": "83",
            "lp_nh_err_pct": "24",
            "ls_nh_err_pct": "31",
            "q_err_pct": "9",
            "k_err_pct": "18",
            "kw_err_pct": "18",
            "qp_err_pct": "33",
            "qs_err_pct": "9",
            "emx_lp_nh": "1.9",
            "hfss_lp_nh": "1.44",
            "emx_ls_nh": "2.5",
            "hfss_ls_nh": "1.72",
            "emx_q": "10",
            "hfss_q": "11",
            "emx_k": "0.48",
            "hfss_k": "0.39",
            "hfss_source": "v48.s8p",
            "summary": "v48.json",
        },
    ]
    with scan_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    status = module.main(["--scan-csv", str(scan_csv), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    result = json.loads((tmp_path / "out" / "hfss_variant_error_pattern_diagnosis.json").read_text(encoding="utf-8"))
    assert result["overall_status"] == "FAIL"
    assert result["best_overall"]["variant"].startswith("diagnostics_v52")
    assert result["groups"]["all-M5 or connected-M5 global reference"]["best"]["target_max_pct"] == 70.0
    assert result["final_gate_metric_floor"]["Lp"]["min_error_pct"] == 23.0
    assert result["final_gate_metric_floor"]["Qs"]["any_variant_within_gate"] is True
    assert result["systematic_bias_hfss_over_emx"]["Lp"]["median_hfss_over_emx"] < 0.8
    recommendations = " ".join(item["name"] for item in result["recommendations"])
    assert "Do not launch 1M EMX generation yet" in recommendations
    assert "Treat K-only agreement as insufficient evidence" not in recommendations
    assert (tmp_path / "out" / "HFSS_VARIANT_ERROR_PATTERN_DIAGNOSIS_CN.md").is_file()


def test_diagnosis_warns_when_k_alone_matches_but_inductance_does_not(tmp_path):
    module = _load_module()
    scan_csv = tmp_path / "scan.csv"
    fieldnames = [
        "variant",
        "status",
        "target_max_pct",
        "core_sum_pct",
        "lp_nh_err_pct",
        "ls_nh_err_pct",
        "q_err_pct",
        "k_err_pct",
        "kw_err_pct",
        "qp_err_pct",
        "qs_err_pct",
        "hfss_source",
        "summary",
    ]
    rows = [
        {
            "variant": "k_close_but_l_bad",
            "status": "FAIL",
            "target_max_pct": "35",
            "core_sum_pct": "90",
            "lp_nh_err_pct": "24",
            "ls_nh_err_pct": "35",
            "q_err_pct": "12",
            "k_err_pct": "1",
            "kw_err_pct": "1",
            "qp_err_pct": "20",
            "qs_err_pct": "12",
            "hfss_source": "bad.s8p",
            "summary": "bad.json",
        }
    ]
    with scan_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    module.main(["--scan-csv", str(scan_csv), "--out-dir", str(tmp_path / "out")])

    result = json.loads((tmp_path / "out" / "hfss_variant_error_pattern_diagnosis.json").read_text(encoding="utf-8"))
    recommendations = " ".join(item["name"] for item in result["recommendations"])
    assert "Treat K-only agreement as insufficient evidence" in recommendations
