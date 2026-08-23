from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_emx_hfss_marker_gate.py"
    spec = importlib.util.spec_from_file_location("audit_emx_hfss_marker_gate_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_minimal_s8p(path, freqs_hz=(5.0e9, 5.5e9), reference_ohm=50.0):
    zeros = " ".join(["0"] * (8 * 8 * 2))
    lines = [f"# Hz S RI R {reference_ohm}"]
    lines.extend(f"{freq:.0f} {zeros}" for freq in freqs_hz)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_marker_gate_blocks_when_l_fails_even_if_kw_passes(tmp_path):
    module = _load_module()
    marker = tmp_path / "marker.csv"
    marker.write_text(
        "\n".join(
            [
                "metric,emx,hfss_ads,abs_error,percent_error,nearest_frequency_ghz",
                "lp_nh,1.9,1.2,0.7,35.0,15.0",
                "ls_nh,2.4,1.4,1.0,40.0,15.0",
                "q,10.0,12.0,2.0,17.0,15.0",
                "kw,0.48,0.47,0.01,1.5,15.0",
            ]
        ),
        encoding="utf-8",
    )

    result = module.audit_marker_gate(marker, max_percent_error=10.0)

    assert result["overall_status"] == "FAIL"
    assert result["block_large_scale_generation"] is True
    assert {item["metric"]: item["status"] for item in result["metrics"]} == {
        "lp_nh": "FAIL",
        "ls_nh": "FAIL",
        "q": "FAIL",
        "kw": "PASS",
    }


def test_marker_gate_passes_when_required_metrics_are_within_threshold(tmp_path):
    module = _load_module()
    marker = tmp_path / "marker.csv"
    marker.write_text(
        "\n".join(
            [
                "metric,emx,hfss_ads,abs_error,percent_error,nearest_frequency_ghz",
                "lp_nh,1.9,1.8,0.1,5.0,15.0",
                "ls_nh,2.4,2.3,0.1,4.0,15.0",
                "q,10.0,9.5,0.5,5.0,15.0",
                "k,0.48,0.47,0.01,2.0,15.0",
            ]
        ),
        encoding="utf-8",
    )

    result = module.audit_marker_gate(marker, max_percent_error=10.0)

    assert result["overall_status"] == "PASS"
    assert result["block_large_scale_generation"] is False
    assert result["final_evidence_verified"] is False


def test_marker_gate_requires_s8p_touchstones_for_final_evidence(tmp_path):
    module = _load_module()
    marker = tmp_path / "marker.csv"
    marker.write_text(
        "\n".join(
            [
                "metric,emx,hfss_ads,abs_error,percent_error,nearest_frequency_ghz",
                "lp_nh,1.9,1.8,0.1,5.0,15.0",
                "ls_nh,2.4,2.3,0.1,4.0,15.0",
                "q,10.0,9.5,0.5,5.0,15.0",
                "kw,0.48,0.47,0.01,2.0,15.0",
            ]
        ),
        encoding="utf-8",
    )

    result = module.audit_marker_gate(marker, max_percent_error=10.0, require_s8p_touchstones=True)

    assert result["overall_status"] == "FAIL"
    assert result["block_large_scale_generation"] is True
    assert result["final_evidence_verified"] is False
    assert {item["name"]: item["reason"] for item in result["touchstone_contract"]} == {
        "emx_s8p": "missing_required_path",
        "hfss_s8p": "missing_required_path",
    }


def test_marker_gate_final_evidence_passes_with_matching_s8p_files(tmp_path):
    module = _load_module()
    marker = tmp_path / "marker.csv"
    marker.write_text(
        "\n".join(
            [
                "metric,emx,hfss_ads,abs_error,percent_error,nearest_frequency_ghz",
                "lp_nh,1.9,1.8,0.1,5.0,15.0",
                "ls_nh,2.4,2.3,0.1,4.0,15.0",
                "q,10.0,9.5,0.5,5.0,15.0",
                "kw,0.48,0.47,0.01,2.0,15.0",
            ]
        ),
        encoding="utf-8",
    )
    emx = tmp_path / "emx.s8p"
    hfss = tmp_path / "hfss.s8p"
    _write_minimal_s8p(emx)
    _write_minimal_s8p(hfss)

    result = module.audit_marker_gate(
        marker,
        max_percent_error=10.0,
        emx_touchstone=emx,
        hfss_touchstone=hfss,
        require_s8p_touchstones=True,
    )

    assert result["overall_status"] == "PASS"
    assert result["block_large_scale_generation"] is False
    assert result["final_evidence_verified"] is True


def test_marker_gate_final_evidence_fails_when_s8p_frequency_grids_differ(tmp_path):
    module = _load_module()
    marker = tmp_path / "marker.csv"
    marker.write_text(
        "\n".join(
            [
                "metric,emx,hfss_ads,abs_error,percent_error,nearest_frequency_ghz",
                "lp_nh,1.9,1.8,0.1,5.0,15.0",
                "ls_nh,2.4,2.3,0.1,4.0,15.0",
                "q,10.0,9.5,0.5,5.0,15.0",
                "kw,0.48,0.47,0.01,2.0,15.0",
            ]
        ),
        encoding="utf-8",
    )
    emx = tmp_path / "emx.s8p"
    hfss = tmp_path / "hfss.s8p"
    _write_minimal_s8p(emx, freqs_hz=(5.0e9, 5.5e9))
    _write_minimal_s8p(hfss, freqs_hz=(5.0e9, 6.0e9))

    result = module.audit_marker_gate(
        marker,
        max_percent_error=10.0,
        emx_touchstone=emx,
        hfss_touchstone=hfss,
        require_s8p_touchstones=True,
    )

    assert result["overall_status"] == "FAIL"
    assert result["final_evidence_verified"] is False
    assert result["touchstone_contract"][-1]["reason"] == "frequency_grid_mismatch"
