import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_mars56_s4p_million_chunk_checkpoint.py"
    spec = importlib.util.spec_from_file_location("audit_mars56_s4p_million_chunk_identity", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _build_artifacts(tmp_path: Path, module, expected: int = 3):
    candidate_dir = tmp_path / "candidate"
    dataset_dir = tmp_path / "dataset"
    checkpoint_dir = tmp_path / "checkpoint"
    candidate = {
        "overall_status": "PASS",
        "sample_count": expected,
        "require_unique_geometry": True,
        "require_unique_source_id": True,
        "canonical_geometry_fields": list(module.CANONICAL_GEOMETRY_FIELDS),
        "geometry_fingerprint_schema": module.GEOMETRY_FINGERPRINT_SCHEMA,
        "geometry_fingerprint_quantization_um": module.GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
        "identity_audit": {
            "row_count": expected,
            "unique_geometry_fingerprint_count": expected,
            "duplicate_geometry_extra_row_count": 0,
            "duplicate_geometry_group_count": 0,
            "missing_geometry_fingerprint_count": 0,
            "unique_source_candidate_id_count": expected,
            "missing_source_candidate_id_count": 0,
            "duplicate_source_candidate_id_extra_row_count": 0,
            "duplicate_source_candidate_id_group_count": 0,
        },
    }
    _write_json(candidate_dir / "mars56_grounded_s4p_candidate_queue_summary.json", candidate)
    _write_json(
        dataset_dir / "parallel_candidate_queue_dataset_summary.json",
        {
            "overall_status": "PASS",
            "merged_row_count": expected,
            "shard_count": 2,
            "pass_shard_count": 2,
            "touchstone_output_contract": {
                "ok_row_count": expected,
                "nonzero_file_count": expected,
                "extension_match_count": expected,
                "port_error_count": 0,
                "frequency_error_count": 0,
            },
        },
    )
    _write_json(checkpoint_dir / "mars56_s4p_physical_checkpoint_pipeline_summary.json", {"overall_status": "PASS"})
    _write_json(
        checkpoint_dir / "response_features" / "response_feature_extraction_summary.json",
        {"overall_status": "PASS", "counts": {"touchstone_candidates": expected, "ok_rows": expected}},
    )
    _write_json(
        checkpoint_dir / "enriched_geometry" / "geometry_enrichment_manifest.json",
        {"overall_status": "PASS", "input_row_count": expected, "enriched_row_count": expected},
    )
    _write_json(
        checkpoint_dir / "physical_feature_uniformity" / "physical_feature_uniformity_summary.json",
        {"overall_status": "PASS", "row_count": expected, "valid_feature_count": expected},
    )
    _write_json(
        checkpoint_dir / "physical_feature_uniformity" / "physical_feature_uniformity_manifest.json",
        {"overall_status": "PASS", "visual_artifact_count": 3},
    )
    _write_json(
        checkpoint_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
        {"overall_status": "PASS", "training_count": expected},
    )
    _write_json(
        checkpoint_dir
        / "physical_feature_inverse_checkpoint_test"
        / "physical_feature_inverse_checkpoint_test_summary.json",
        {"overall_status": "PASS", "usable_row_count": expected, "test_row_count": 1},
    )
    return candidate_dir, dataset_dir, checkpoint_dir, candidate


def _run(module, tmp_path: Path, candidate_dir: Path, dataset_dir: Path, checkpoint_dir: Path) -> int:
    return module.main(
        [
            "--chunk-index",
            "1",
            "--expected-sample-count",
            "3",
            "--candidate-dir",
            str(candidate_dir),
            "--dataset-dir",
            str(dataset_dir),
            "--checkpoint-dir",
            str(checkpoint_dir),
            "--out-dir",
            str(tmp_path / "audit"),
        ]
    )


def test_chunk_checkpoint_requires_and_accepts_complete_identity_contract(tmp_path):
    module = _load_module()
    candidate_dir, dataset_dir, checkpoint_dir, _ = _build_artifacts(tmp_path, module)

    assert _run(module, tmp_path, candidate_dir, dataset_dir, checkpoint_dir) == 0
    summary = json.loads((tmp_path / "audit" / "mars56_s4p_million_chunk_checkpoint_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    identity_checks = [item for item in summary["checks"] if "candidate" in item["name"]]
    assert identity_checks and all(item["status"] == "PASS" for item in identity_checks)


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("root", "require_unique_geometry", False),
        ("root", "geometry_fingerprint_schema", "wrong_schema"),
        ("root", "geometry_fingerprint_quantization_um", 1.0e-5),
        ("identity", "unique_geometry_fingerprint_count", 2),
        ("identity", "duplicate_geometry_extra_row_count", 1),
        ("identity", "missing_geometry_fingerprint_count", 1),
        ("identity", "unique_source_candidate_id_count", 2),
    ],
)
def test_chunk_checkpoint_rejects_missing_or_inconsistent_identity_proof(tmp_path, location, field, value):
    module = _load_module()
    candidate_dir, dataset_dir, checkpoint_dir, candidate = _build_artifacts(tmp_path, module)
    target = candidate if location == "root" else candidate["identity_audit"]
    target[field] = value
    _write_json(candidate_dir / "mars56_grounded_s4p_candidate_queue_summary.json", candidate)

    assert _run(module, tmp_path, candidate_dir, dataset_dir, checkpoint_dir) == 2
    summary = json.loads((tmp_path / "audit" / "mars56_s4p_million_chunk_checkpoint_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "STOP_OR_REPAIR_THIS_100K_S4P_CHUNK"
