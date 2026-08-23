from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_script_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_emx_residual_queue_from_completed.py"
    )
    spec = importlib.util.spec_from_file_location(
        "build_emx_residual_queue_from_completed_script", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GEOMETRY_COLUMNS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)


def _geometry(seed: float) -> dict[str, float]:
    return {
        "primary_outer_width_um": 240.0 + seed,
        "primary_outer_height_um": 220.0 + seed,
        "secondary_outer_width_um": 180.0 + seed,
        "secondary_outer_height_um": 170.0 + seed,
        "line_width_um": 6.0 + seed * 0.01,
        "primary_terminal_y_span_um": 42.0 + seed,
        "secondary_terminal_y_span_um": 38.0 + seed,
        "offset_um": 3.0 + seed,
        "primary_feed_extension_um": 130.0 + seed,
        "secondary_feed_extension_um": 140.0 + seed,
    }


def _write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["candidate_id", *GEOMETRY_COLUMNS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _overlap_evidence(
    *,
    expected_um: float = 10.0,
    measured_um: float = 10.0,
    label_overrides: dict[str, float] | None = None,
) -> dict[str, object]:
    ports = {
        f"P{index:03d}": {"measured_overlap_um": measured_um}
        for index in range(1, 9)
    }
    for label, value in (label_overrides or {}).items():
        ports[label] = {"measured_overlap_um": value}
    return {"expected_um": expected_um, "ports": ports}


def _write_complete_evaluation(
    dataset: Path,
    evaluation: str,
    geometry: dict[str, float],
    *,
    overlap_evidence: dict[str, object] | None = None,
    include_overlap_evidence: bool = True,
) -> None:
    evaluation_dir = dataset / "evaluations" / evaluation
    touchstone = evaluation_dir / "emx" / "emx.s4p"
    touchstone.parent.mkdir(parents=True)
    freqs_hz = np.arange(5.0e9, 60.0e9 + 0.25e9, 0.5e9)
    s_matrix = np.zeros((len(freqs_hz), 4, 4), dtype=np.complex128)
    SParameterResult(freqs_hz=freqs_hz, s_matrix=s_matrix).to_touchstone(touchstone)
    summary = {
        "ok": True,
        "error": None,
        "geometry": geometry,
        "command": ["emx", "--parallel=2"],
    }
    if include_overlap_evidence:
        summary["geometry_check"] = {
            "power_line_8port_geometry_audit": {
                "port_ground_overlap_evidence": overlap_evidence
                if overlap_evidence is not None
                else _overlap_evidence()
            }
        }
    (evaluation_dir / "summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )


def test_failed_or_unstarted_rows_remain_in_ordered_residual(tmp_path: Path) -> None:
    mod = _load_script_module()
    queue = tmp_path / "queue.csv"
    geometries = [_geometry(float(index)) for index in range(3)]
    rows = [
        {"candidate_id": f"candidate_{index}", **{k: str(v) for k, v in geometry.items()}}
        for index, geometry in enumerate(geometries)
    ]
    _write_queue(queue, rows)
    dataset = tmp_path / "dataset"
    _write_complete_evaluation(dataset, "completed", geometries[1])
    out_dir = tmp_path / "out"

    status = mod.main(
        [
            "--candidate-csv",
            str(queue),
            "--dataset-dir",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--expected-count",
            "3",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "residual_queue_partition_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["overall_status"] == "PASS"
    assert summary["completed_verified_count"] == 1
    assert summary["residual_count"] == 2
    with (out_dir / "residual_candidate_queue.csv").open(newline="") as handle:
        residual = list(csv.DictReader(handle))
    assert [row["candidate_id"] for row in residual] == [
        "candidate_0",
        "candidate_2",
    ]
    with (out_dir / "completed_touchstone_manifest.csv").open(newline="") as handle:
        completed = list(csv.DictReader(handle))
    assert completed[0]["port_ground_overlap_verified"] == "True"
    assert float(completed[0]["port_ground_overlap_max_abs_error_um"]) == 0.0


def test_touchstone_without_successful_summary_is_not_counted_complete(tmp_path: Path) -> None:
    mod = _load_script_module()
    queue = tmp_path / "queue.csv"
    geometry = _geometry(0.0)
    _write_queue(
        queue,
        [{"candidate_id": "candidate_0", **{k: str(v) for k, v in geometry.items()}}],
    )
    dataset = tmp_path / "dataset"
    _write_complete_evaluation(dataset, "failed", geometry)
    summary_path = dataset / "evaluations" / "failed" / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["ok"] = False
    summary["error"] = "preflight_failed"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    out_dir = tmp_path / "out"

    status = mod.main(
        [
            "--candidate-csv",
            str(queue),
            "--dataset-dir",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--expected-count",
            "1",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "residual_queue_partition_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["completed_verified_count"] == 0
    assert summary["residual_count"] == 1


def test_touchstone_with_bad_port_overlap_remains_in_residual(tmp_path: Path) -> None:
    mod = _load_script_module()
    queue = tmp_path / "queue.csv"
    geometry = _geometry(0.0)
    _write_queue(
        queue,
        [{"candidate_id": "candidate_0", **{k: str(v) for k, v in geometry.items()}}],
    )
    dataset = tmp_path / "dataset"
    _write_complete_evaluation(
        dataset,
        "bad_overlap",
        geometry,
        overlap_evidence=_overlap_evidence(label_overrides={"P003": 9.5}),
    )
    out_dir = tmp_path / "out"

    status = mod.main(
        [
            "--candidate-csv",
            str(queue),
            "--dataset-dir",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--expected-count",
            "1",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "residual_queue_partition_summary.json").read_text()
    )
    assert summary["completed_verified_count"] == 0
    assert summary["residual_count"] == 1
    assert summary["rejected_or_incomplete_touchstone_reason_counts"] == {
        "port_ground_overlap_label_mismatch:P003": 1
    }
    with (out_dir / "residual_candidate_queue.csv").open(newline="") as handle:
        residual = list(csv.DictReader(handle))
    assert [row["candidate_id"] for row in residual] == ["candidate_0"]


def test_touchstone_without_port_overlap_evidence_remains_in_residual(
    tmp_path: Path,
) -> None:
    mod = _load_script_module()
    queue = tmp_path / "queue.csv"
    geometry = _geometry(0.0)
    _write_queue(
        queue,
        [{"candidate_id": "candidate_0", **{k: str(v) for k, v in geometry.items()}}],
    )
    dataset = tmp_path / "dataset"
    _write_complete_evaluation(
        dataset,
        "missing_overlap",
        geometry,
        include_overlap_evidence=False,
    )
    out_dir = tmp_path / "out"

    status = mod.main(
        [
            "--candidate-csv",
            str(queue),
            "--dataset-dir",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--expected-count",
            "1",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "residual_queue_partition_summary.json").read_text()
    )
    assert summary["completed_verified_count"] == 0
    assert summary["residual_count"] == 1
    assert summary["rejected_or_incomplete_touchstone_reason_counts"] == {
        "missing_port_ground_overlap_evidence": 1
    }


def test_touchstone_with_wrong_recorded_overlap_contract_remains_in_residual(
    tmp_path: Path,
) -> None:
    mod = _load_script_module()
    queue = tmp_path / "queue.csv"
    geometry = _geometry(0.0)
    _write_queue(
        queue,
        [{"candidate_id": "candidate_0", **{k: str(v) for k, v in geometry.items()}}],
    )
    dataset = tmp_path / "dataset"
    _write_complete_evaluation(
        dataset,
        "wrong_contract",
        geometry,
        overlap_evidence=_overlap_evidence(expected_um=9.0),
    )
    out_dir = tmp_path / "out"

    status = mod.main(
        [
            "--candidate-csv",
            str(queue),
            "--dataset-dir",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--expected-count",
            "1",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "residual_queue_partition_summary.json").read_text()
    )
    assert summary["completed_verified_count"] == 0
    assert summary["residual_count"] == 1
    assert summary["rejected_or_incomplete_touchstone_reason_counts"] == {
        "port_ground_overlap_expected_mismatch": 1
    }
