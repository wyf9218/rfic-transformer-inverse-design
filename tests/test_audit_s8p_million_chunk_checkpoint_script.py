from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_s8p_million_chunk_checkpoint.py"
    spec = importlib.util.spec_from_file_location("audit_s8p_million_chunk_checkpoint_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _args(root: Path, out_dir: Path, *, expected_count: int = 10) -> list[str]:
    return [
        "--chunk-index",
        "1",
        "--expected-sample-count",
        str(expected_count),
        "--dataset-dir",
        str(root / "emx_dataset"),
        "--quality-dir",
        str(root / "dataset_quality_gates"),
        "--model-dir",
        str(root / "inverse_model_training"),
        "--audit-dir",
        str(root / "inverse_model_quality_audit"),
        "--nn-architecture-dir",
        str(root / "inverse_nn_architecture_search"),
        "--nn-training-dir",
        str(root / "inverse_nn_architecture_training"),
        "--out-dir",
        str(out_dir),
        "--min-training-rows",
        str(expected_count),
        "--no-fail-exit",
    ]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_complete_chunk(root: Path, *, expected_count: int = 10, fail_nn: bool = False) -> None:
    _write_json(root / "emx_dataset" / "dataset_manifest.json", {"requested_count": expected_count, "ok_count": expected_count, "fail_count": 0})
    _write_json(root / "dataset_quality_gates" / "dataset_quality_gates_summary.json", {"overall_status": "PASS"})
    _write_json(
        root / "dataset_quality_gates" / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
        {"overall_status": "PASS", "training_count": expected_count},
    )
    _write_json(root / "inverse_model_training" / "physical_feature_inverse_model_training_summary.json", {"overall_status": "PASS", "training_count": expected_count})
    _write_json(root / "inverse_model_quality_audit" / "physical_feature_inverse_model_quality_summary.json", {"overall_status": "PASS", "training_count": expected_count})
    _write_json(root / "inverse_nn_architecture_search" / "physical_feature_inverse_nn_architecture_search_summary.json", {"overall_status": "PASS", "architecture_candidate_count": 4})
    _write_json(
        root / "inverse_nn_architecture_training" / "physical_feature_inverse_nn_architecture_search_training_summary.json",
        {
            "overall_status": "FAIL" if fail_nn else "PASS",
            "training_count": expected_count,
            "trained_candidate_count": 4,
            "selected_candidate": {"candidate_id": "mlp_001"} if not fail_nn else {},
        },
    )


def test_waits_when_chunk_artifacts_are_missing(tmp_path):
    module = _load_module()

    status = module.main(_args(tmp_path / "chunk", tmp_path / "out"))

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_chunk_checkpoint_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_CHUNK_ARTIFACTS"
    assert summary["decision"] == "WAIT_FOR_100K_CHUNK_ARTIFACTS"


def test_passes_complete_chunk_checkpoint(tmp_path):
    module = _load_module()
    chunk = tmp_path / "chunk"
    _write_complete_chunk(chunk, expected_count=10)

    status = module.main(_args(chunk, tmp_path / "out", expected_count=10))

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_chunk_checkpoint_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "ACCEPT_100K_CHUNK_AND_ALLOW_NEXT_CHUNK"
    assert summary["artifact_statuses"]["nn_architecture_train"] == "PASS"


def test_fails_when_nn_training_fails(tmp_path):
    module = _load_module()
    chunk = tmp_path / "chunk"
    _write_complete_chunk(chunk, expected_count=10, fail_nn=True)

    status = module.main(_args(chunk, tmp_path / "out", expected_count=10))

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_chunk_checkpoint_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "STOP_MILLION_CAMPAIGN_FIX_THIS_100K_CHUNK"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "NN architecture training PASS" in failed
    assert "NN selected candidate present" in failed
