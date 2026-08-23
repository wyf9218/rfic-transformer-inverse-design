import csv
import importlib.util
import json
import sys
from pathlib import Path

from rfic_transformer_inverse_design.api import load_run_config


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "mars_s4p_grounded_powerline_physical_feature_500_template.yaml"


def _load_module():
    script = REPO / "scripts" / "audit_tandem_predicted_geometry_feasibility.py"
    spec = importlib.util.spec_from_file_location("audit_tandem_predicted_geometry_feasibility_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prediction_row(**overrides):
    config = load_run_config(CONFIG)
    flat = config.bounds.midpoint().flat_dict()
    names = (
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "primary_terminal_y_span_um",
        "secondary_terminal_y_span_um",
        "offset_um",
        "primary_feed_extension_um",
        "secondary_feed_extension_um",
    )
    row = {f"predicted_geometry__{name}": flat[name] for name in names}
    row["predicted_geometry__line_width_um"] = flat["line_width_um"]
    row.update(overrides)
    return row


def _write_predictions(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_valid_shared_width_prediction_passes_all_analytical_gates(tmp_path):
    module = _load_module()
    predictions = tmp_path / "predictions.csv"
    _write_predictions(predictions, [_prediction_row()])

    rc = module.main(
        [
            "--predictions-csv",
            str(predictions),
            "--config",
            str(CONFIG),
            "--out-dir",
            str(tmp_path / "audit"),
        ]
    )

    assert rc == 0
    summary = json.loads(
        (tmp_path / "audit" / "tandem_predicted_geometry_feasibility_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["valid_fraction"] == 1.0
    assert summary["checks"]["all_predictions_satisfy_coupled_topology"] is True
    assert summary["checks"]["all_predictions_satisfy_tsmc65_top_metal_gate"] is True


def test_coupled_terminal_span_violation_fails_even_inside_scalar_bounds(tmp_path):
    module = _load_module()
    predictions = tmp_path / "predictions.csv"
    permissive_config = tmp_path / "permissive_terminal_bounds.yaml"
    permissive_config.write_text(
        CONFIG.read_text(encoding="utf-8").replace(
            "terminal_y_span_um: [20.0, 90.0]",
            "terminal_y_span_um: [20.0, 150.0]",
        ),
        encoding="utf-8",
    )
    _write_predictions(
        predictions,
        [
            _prediction_row(
                predicted_geometry__line_width_um=12.0,
                predicted_geometry__primary_outer_width_um=160.0,
                predicted_geometry__primary_outer_height_um=160.0,
                predicted_geometry__secondary_outer_width_um=160.0,
                predicted_geometry__secondary_outer_height_um=160.0,
                predicted_geometry__primary_terminal_y_span_um=120.0,
                predicted_geometry__secondary_terminal_y_span_um=120.0,
            )
        ],
    )

    rc = module.main(
        [
            "--predictions-csv",
            str(predictions),
            "--config",
            str(permissive_config),
            "--out-dir",
            str(tmp_path / "audit"),
            "--no-fail-exit",
        ]
    )

    assert rc == 0
    summary = json.loads(
        (tmp_path / "audit" / "tandem_predicted_geometry_feasibility_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["bounds_failure_count"] == 0
    assert summary["topology_failure_count"] == 1
    assert summary["valid_fraction"] == 0.0


def test_missing_shared_width_is_not_silently_replaced(tmp_path):
    module = _load_module()
    predictions = tmp_path / "predictions.csv"
    row = _prediction_row()
    row.pop("predicted_geometry__line_width_um")
    _write_predictions(predictions, [row])

    module.main(
        [
            "--predictions-csv",
            str(predictions),
            "--config",
            str(CONFIG),
            "--out-dir",
            str(tmp_path / "audit"),
            "--no-fail-exit",
        ]
    )

    summary = json.loads(
        (tmp_path / "audit" / "tandem_predicted_geometry_feasibility_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["missing_field_count"] == 1
    assert summary["checks"]["all_rows_rebuilt"] is False
