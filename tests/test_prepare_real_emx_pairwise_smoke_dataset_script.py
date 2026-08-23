from tests.rfic_transformer_inverse_design.shared import *

import csv
import hashlib
import importlib.util
import sys


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "prepare_real_emx_pairwise_smoke_dataset.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prepare_real_emx_pairwise_smoke_dataset_script",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(root: Path, *, synthetic_marker: bool = False, corrupt_hash: bool = False):
    count = 12
    s4p_dir = root / "s4p"
    s4p_dir.mkdir()
    sample_items = []
    geometry_items = []
    frequencies = np.asarray([5.0e9, 10.0e9, 15.0e9])
    geometry_columns = (
        "primary_outer_width_um",
        "primary_outer_height_um",
        "primary_terminal_y_span_um",
        "primary_feed_extension_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "secondary_terminal_y_span_um",
        "secondary_feed_extension_um",
        "shared_trace_width_um",
        "offset_um",
    )
    for index in range(count):
        matrix = np.zeros((len(frequencies), 4, 4), dtype=np.complex128)
        for frequency_index, frequency in enumerate(frequencies):
            phase = frequency / frequencies[-1]
            diagonal = 0.08 + 1j * (0.10 + 0.01 * phase + index * 0.0002)
            coupling = 0.01 + 1j * (0.03 + index * 0.0001)
            matrix[frequency_index] = np.eye(4) * diagonal
            matrix[frequency_index, 0, 1] = matrix[frequency_index, 1, 0] = 0.3 * coupling
            matrix[frequency_index, 2, 3] = matrix[frequency_index, 3, 2] = 0.2 * coupling
            matrix[frequency_index, 0, 2] = matrix[frequency_index, 2, 0] = coupling
            matrix[frequency_index, 1, 3] = matrix[frequency_index, 3, 1] = 0.8 * coupling
        path = s4p_dir / f"{index:06d}.s4p"
        _write_touchstone(path, frequencies, matrix)
        header = (
            "! Touchstone simulation data from EMX version 2025.1.0\n"
            "! EMX was run on mars.example.edu as:\n"
            "! /cae/apps/emx layout.gds TRANSFORMER /PDKs/TSMC65_05_12_26/process.proc\n"
            "! --cadence-pins=51 --port=P001=P001:P001_G --port=P002=P002:P002_G\n"
            "! --port=P003=P003:P003_G --port=P004=P004:P004_G\n"
        )
        if synthetic_marker and index == 3:
            header += "! synthetic contract fixture\n"
        path.write_text(header + path.read_text(encoding="utf-8"), encoding="utf-8")
        content_hash = _sha256(path)
        if corrupt_hash and index == 5:
            content_hash = "0" * 64
        sample_items.append(
            {
                "index": index,
                "evaluation": f"sample_{index:04d}",
                "destination": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": content_hash,
            }
        )
        geometry = {
            column: float(index + position / 100.0 + 1.0)
            for position, column in enumerate(geometry_columns)
        }
        geometry_items.append({"index": index, "geometry": geometry})
    sample_manifest = root / "sample_manifest.json"
    sample_manifest.write_text(
        json.dumps(
            {
                "selection_rule": "round(...) on the formal raw-80000 stable index",
                "selection_is_response_blind": True,
                "sample_count": count,
                "samples": sample_items,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    geometry_manifest = root / "geometry_manifest.json"
    geometry_manifest.write_text(
        json.dumps(
            {
                "sample_count": count,
                "geometry_columns": list(geometry_columns),
                "shared_trace_width_contract_pass": True,
                "samples": geometry_items,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return sample_manifest, geometry_manifest, s4p_dir


def _args(root: Path, sample_manifest: Path, geometry_manifest: Path, s4p_dir: Path):
    return [
        "--sample-manifest",
        str(sample_manifest),
        "--geometry-manifest",
        str(geometry_manifest),
        "--s4p-dir",
        str(s4p_dir),
        "--output-csv",
        str(root / "training.csv"),
        "--output-manifest",
        str(root / "training_manifest.json"),
        "--min-rows",
        "10",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "15",
        "--expected-frequency-step-ghz",
        "5",
        "--expected-frequency-points",
        "3",
        "--target-frequency-ghz",
        "10",
    ]


def test_builds_response_blind_real_emx_smoke_table(tmp_path):
    module = _load_module()
    sample_manifest, geometry_manifest, s4p_dir = _fixture(tmp_path)
    assert module.main(_args(tmp_path, sample_manifest, geometry_manifest, s4p_dir)) == 0
    manifest = json.loads((tmp_path / "training_manifest.json").read_text())
    assert manifest["overall_status"] == "PASS"
    assert manifest["training_count"] == 12
    assert manifest["selection_is_response_blind"] is True
    assert manifest["eligible_for_model_success_claim"] is False
    with (tmp_path / "training.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 12
    assert len([column for column in rows[0] if column.startswith("geom__")]) == 10
    assert all(float(row["input__k_abs_center"]) >= 0.0 for row in rows)


def test_rejects_synthetic_marker_even_when_hash_matches(tmp_path):
    module = _load_module()
    sample_manifest, geometry_manifest, s4p_dir = _fixture(
        tmp_path,
        synthetic_marker=True,
    )
    arguments = _args(tmp_path, sample_manifest, geometry_manifest, s4p_dir)
    arguments.append("--no-fail-exit")
    assert module.main(arguments) == 0
    manifest = json.loads((tmp_path / "training_manifest.json").read_text())
    assert manifest["overall_status"] == "FAIL"
    assert any("synthetic fixture marker" in item["reason"] for item in manifest["rejects"])
    assert not (tmp_path / "training.csv").exists()


def test_rejects_touchstone_hash_mismatch(tmp_path):
    module = _load_module()
    sample_manifest, geometry_manifest, s4p_dir = _fixture(tmp_path, corrupt_hash=True)
    arguments = _args(tmp_path, sample_manifest, geometry_manifest, s4p_dir)
    arguments.append("--no-fail-exit")
    assert module.main(arguments) == 0
    manifest = json.loads((tmp_path / "training_manifest.json").read_text())
    assert manifest["overall_status"] == "FAIL"
    assert any("SHA256 differs" in item["reason"] for item in manifest["rejects"])
    assert not (tmp_path / "training.csv").exists()
