from tests.rfic_transformer_inverse_design.shared import *

import argparse
import csv
import importlib.util
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "materialize_physical_feature_targeted_s4p_queue.py"
    spec = importlib.util.spec_from_file_location("materialize_physical_feature_targeted_s4p_queue_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _selected_row(module, candidate_id: str, *, offset_delta: float = 0.0) -> dict[str, object]:
    values = {
        "primary_outer_width_um": 260.0,
        "primary_outer_height_um": 250.0,
        "secondary_outer_width_um": 220.0,
        "secondary_outer_height_um": 210.0,
        "line_width_um": 6.0,
        "primary_width_um": 91.0,
        "secondary_width_um": 92.0,
        "primary_terminal_y_span_um": 100.0,
        "secondary_terminal_y_span_um": 98.0,
        "offset_um": 2.0 + offset_delta,
        "primary_feed_extension_um": 150.0,
        "secondary_feed_extension_um": 148.0,
    }
    return {
        "candidate_id": candidate_id,
        "inside_target_bin": "true",
        **{f"candidate__geom__{field}": values[field] for field in module.GEOMETRY_FIELDS},
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_unique_canonical_geometries_pass_and_emit_traceable_sha256(tmp_path):
    module = _load_module()
    selection = tmp_path / "selection.csv"
    _write_rows(selection, [_selected_row(module, "source-a"), _selected_row(module, "source-b", offset_delta=0.1)])
    out_dir = tmp_path / "queue"

    assert module.main(
        ["--selection-csv", str(selection), "--out-dir", str(out_dir), "--expected-count", "2"]
    ) == 0
    summary = json.loads((out_dir / "mars56_grounded_s4p_candidate_queue_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["canonical_geometry_fields"] == list(module.CANONICAL_GEOMETRY_FIELDS)
    assert summary["geometry_fingerprint_quantization_um"] == pytest.approx(1.0e-6)
    assert summary["identity_audit"]["unique_geometry_fingerprint_count"] == 2
    rows = list(csv.DictReader((out_dir / "mars56_grounded_s4p_candidate_queue.csv").open()))
    assert len({row["geometry_fingerprint_sha256"] for row in rows}) == 2
    assert all(len(row["geometry_fingerprint_sha256"]) == 64 for row in rows)
    assert all(row["line_width_um"] == row["primary_width_um"] == row["secondary_width_um"] for row in rows)


def test_duplicate_geometry_with_different_ids_is_retained_as_failed_evidence(tmp_path):
    module = _load_module()
    selection = tmp_path / "selection.csv"
    _write_rows(selection, [_selected_row(module, "source-a"), _selected_row(module, "source-b")])
    out_dir = tmp_path / "queue"

    assert module.main(
        ["--selection-csv", str(selection), "--out-dir", str(out_dir), "--expected-count", "2"]
    ) == 2
    summary = json.loads((out_dir / "mars56_grounded_s4p_candidate_queue_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["sample_count"] == 2
    assert summary["identity_audit"]["duplicate_geometry_extra_row_count"] == 1
    assert any("duplicate canonical geometry" in item for item in summary["errors"])


def test_width_aliases_do_not_change_canonical_geometry_identity():
    module = _load_module()
    first = _selected_row(module, "source-a")
    second = _selected_row(module, "source-b")
    second["candidate__geom__primary_width_um"] = 7.0
    second["candidate__geom__secondary_width_um"] = 8.0
    args = argparse.Namespace(candidate_id_prefix="queue", sync_widths=True)

    queue, errors = module._materialize_rows([first, second], args)
    assert errors == []
    assert queue[0]["geometry_fingerprint_sha256"] == queue[1]["geometry_fingerprint_sha256"]
    assert queue[0]["primary_width_um"] == queue[0]["secondary_width_um"] == queue[0]["line_width_um"]
    assert queue[1]["primary_width_um"] == queue[1]["secondary_width_um"] == queue[1]["line_width_um"]


def test_quantization_boundary_is_explicit_and_deterministic():
    module = _load_module()
    base = {field: 100.0 for field in module.CANONICAL_GEOMETRY_FIELDS}
    inside_same_cell = dict(base)
    inside_same_cell["offset_um"] += 0.4e-6
    next_cell = dict(base)
    next_cell["offset_um"] += 0.6e-6

    base_sha = module._geometry_fingerprint(base, 1.0e-6)
    assert base_sha == module._geometry_fingerprint(inside_same_cell, 1.0e-6)
    assert base_sha != module._geometry_fingerprint(next_cell, 1.0e-6)


def test_duplicate_source_candidate_id_fails_even_when_geometry_is_unique(tmp_path):
    module = _load_module()
    selection = tmp_path / "selection.csv"
    _write_rows(
        selection,
        [_selected_row(module, "same-source"), _selected_row(module, "same-source", offset_delta=0.1)],
    )
    out_dir = tmp_path / "queue"

    assert module.main(
        ["--selection-csv", str(selection), "--out-dir", str(out_dir), "--expected-count", "2"]
    ) == 2
    summary = json.loads((out_dir / "mars56_grounded_s4p_candidate_queue_summary.json").read_text())
    assert summary["identity_audit"]["duplicate_source_candidate_id_extra_row_count"] == 1
    assert any("duplicate source_candidate_id" in item for item in summary["errors"])
