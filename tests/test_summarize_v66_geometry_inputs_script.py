from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_v66_geometry_inputs.py"
    spec = importlib.util.spec_from_file_location("summarize_v66_geometry_inputs_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_payload(path: Path) -> None:
    payload = {
        "sample_id": "sample_a",
        "source_files": {"emx_s8p": str(path.parent / "emx_reference.s8p"), "gds": str(path.parent / "layout.gds")},
        "frequency_grid": {"setup_frequency_ghz": 15.0, "start_ghz": 5.0, "stop_ghz": 60.0, "step_ghz": 0.5, "points": 111},
        "ports": [
            {
                "port_name": f"P{i:03d}",
                "role": role,
                "ground_name": f"P{i:03d}_G",
                "signal_metal": "metal10" if i in {1, 4, 7, 8} else "metal9",
                "ground_metal": "metal5",
                "signal_label": {"origin_um": [float(i), float(i + 1)]},
                "ground_label": {"origin_um": [float(i), float(i + 1)]},
                "signal_z_um": 714.0,
                "ground_z_um": 703.0,
                "port_sheet_width_um": 2.5,
                "port_sheet_axis": "x",
            }
            for i, role in enumerate(
                [
                    "primary_top",
                    "left_power_top",
                    "left_power_bottom",
                    "primary_bottom",
                    "secondary_bottom",
                    "secondary_top",
                    "right_power_top",
                    "right_power_bottom",
                ],
                start=1,
            )
        ],
        "power_line_8port_geometry": {
            "labels": {"primary_top": "P001", "secondary_top": "P006"},
            "line_width_um": 2.5,
            "bridge_width_um": 2.5,
            "vertical_length_um": 300.0,
            "max_outer_height_um": 200.0,
            "vertical_length_diameter_ratio": 1.5,
            "primary_power_line": {"center_x_um": 100.0, "width_um": 2.5, "height_um": 300.0},
            "secondary_power_line": {"center_x_um": -100.0, "width_um": 2.5, "height_um": 300.0},
            "primary_bridge": {
                "width_um": 2.5,
                "length_um": 20.0,
                "is_horizontal": True,
                "extends_away_from_coil_interior": True,
            },
            "secondary_bridge": {
                "width_um": 2.5,
                "length_um": 20.0,
                "is_horizontal": True,
                "extends_away_from_coil_interior": True,
            },
            "primary_power_line_clearance": {"other_coil_boundary_clearance_um": 12.0},
            "secondary_power_line_clearance": {"other_coil_boundary_clearance_um": 12.0},
        },
        "differential_port_pairs": [
            {"plus_port_name": "P001", "minus_port_name": "P004"},
            {"plus_port_name": "P005", "minus_port_name": "P006"},
        ],
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_config(path: Path) -> None:
    path.write_text(
        """
target:
  topology_mode: 1t1t
bounds:
  primary:
    outer_width_um: [100.0, 200.0]
    outer_height_um: [100.0, 200.0]
    trace_width_um: [1.0, 5.0]
    spacing_um: [2.0, 6.0]
    terminal_y_span_um: [20.0, 80.0]
    feed_extension_um: [10.0, 100.0]
  secondary:
    outer_width_um: [100.0, 200.0]
    outer_height_um: [100.0, 200.0]
    trace_width_um: [1.0, 5.0]
    spacing_um: [2.0, 6.0]
    terminal_y_span_um: [20.0, 80.0]
    feed_extension_um: [10.0, 100.0]
  offset_um: [-50.0, 50.0]
""",
        encoding="utf-8",
    )


def test_summarizes_v66_geometry_inputs_and_ports(tmp_path):
    mod = _load_module()
    payload = tmp_path / "variants" / "v66a" / "sample_a" / "hfss_s8p_build_payload.json"
    _write_payload(payload)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "variants": [{"name": "v66a", "payload_json": str(payload)}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    _write_config(config)
    out = tmp_path / "out"

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--config",
            str(config),
            "--out-dir",
            str(out),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((out / "v66_geometry_input_contract_summary.json").read_text(encoding="utf-8"))
    assert summary["physical_model_inputs"] == ["lp_nh_center", "ls_nh_center", "q_center", "k_center"]
    assert summary["variants"][0]["port_order"] == [f"P{i:03d}" for i in range(1, 9)]
    assert summary["variants"][0]["line_width_um"] == 2.5
    assert summary["geometry_contract"]["status"] == "PASS"
    assert (out / "v66_port_map.csv").is_file()
    assert (out / "V66_GEOMETRY_INPUT_CONTRACT_SUMMARY.md").is_file()
