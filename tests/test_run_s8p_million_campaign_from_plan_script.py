from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


NN_INPUT_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center"


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_s8p_million_campaign_from_plan.py"
    spec = importlib.util.spec_from_file_location("run_s8p_million_campaign_from_plan_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(root: Path, *, status: str = "PASS", allow_real_emx: bool = False, chunks: int = 10, drop_command: str | None = None) -> Path:
    payload_chunks = []
    for index in range(chunks):
        chunk_dir = root / f"chunk_{index + 1:02d}"
        checkpoint_dir = chunk_dir / "chunk_checkpoint"
        commands = {
            "build_candidate_queue": [
                "python",
                "scripts/build_s8p_geometry_bootstrap_candidate_queue.py",
                "--count",
                "100000",
                "--expected-count",
                "100000",
            ],
            "run_emx_parallel": [
                "python",
                "scripts/run_candidate_queue_dataset_parallel.py",
                "--chunk",
                str(index + 1),
                "--expected-touchstone-extension",
                ".s8p",
                "--expected-ports",
                "8",
                "--expected-count",
                "100000",
            ],
            "run_quality_gates": [
                "python",
                "scripts/run_dataset_quality_gates.py",
                "--touchstone-expected-ports",
                "8",
                "--touchstone-all",
                "--extract-response-features",
                "--build-physical-feature-inverse-training-table",
            ],
            "train_inverse_model": [
                "python",
                "scripts/train_physical_feature_inverse_model.py",
                "--min-training-rows",
                "8",
            ],
            "audit_inverse_model": [
                "python",
                "scripts/audit_physical_feature_inverse_model_quality.py",
                "--min-training-rows",
                "8",
            ],
            "plan_nn_architecture_search": [
                "python",
                "scripts/plan_physical_feature_inverse_nn_architecture_search.py",
                "--input-columns",
                NN_INPUT_COLUMNS,
                "--min-training-rows",
                "100000",
            ],
            "train_nn_architecture_search": [
                "python",
                "scripts/train_physical_feature_inverse_nn_architecture_search.py",
                "--candidate-csv",
                str(chunk_dir / "physical_feature_inverse_nn_architecture_candidates.csv"),
                "--input-columns",
                NN_INPUT_COLUMNS,
                "--min-training-rows",
                "100000",
            ],
            "audit_chunk_checkpoint": [
                "python",
                "scripts/audit_s8p_million_chunk_checkpoint.py",
                "--expected-sample-count",
                "100000",
                "--out-dir",
                str(checkpoint_dir),
                "--min-training-rows",
                "100000",
            ],
        }
        if drop_command:
            commands.pop(drop_command, None)
        payload_chunks.append(
            {
                "chunk_index": index + 1,
                "sample_start": index * 100_000 + 1,
                "sample_stop": (index + 1) * 100_000,
                "sample_count": 100_000,
                "checkpoint_dir": str(checkpoint_dir),
                "commands": commands,
            }
        )
    plan = {
        "overall_status": status,
        "decision": "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN" if status == "PASS" else "DO_NOT_START_MILLION_SAMPLE_CAMPAIGN_UNTIL_EMX_HFSS_S8P_GATE_PASSES",
        "validation_gate": {"status": status},
        "total_requested_samples": 1_000_000,
        "chunk_size": 100_000,
        "chunk_count": len(payload_chunks) if status == "PASS" else 0,
        "allow_real_emx": allow_real_emx,
        "chunks": payload_chunks if status == "PASS" else [],
    }
    path = root / "s8p_million_sample_campaign_plan_summary.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path


def _write_checkpoint(path: Path, *, status: str = "PASS") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": "ACCEPT_100K_CHUNK_AND_ALLOW_NEXT_CHUNK" if status == "PASS" else "STOP_MILLION_CAMPAIGN_FIX_THIS_100K_CHUNK",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_failed_plan_does_not_execute_commands(tmp_path):
    mod = _load_module()
    plan = _write_plan(tmp_path, status="FAIL")

    with mock.patch.object(mod.subprocess, "run") as mocked_run:
        status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out"), "--no-fail-exit"])

    assert status == 0
    assert mocked_run.call_count == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "DO_NOT_EXECUTE_MILLION_CAMPAIGN_PLAN_NOT_READY"


def test_passed_plan_defaults_to_dry_run_without_subprocess_calls(tmp_path):
    mod = _load_module()
    plan = _write_plan(tmp_path, status="PASS", allow_real_emx=False)

    with mock.patch.object(mod.subprocess, "run") as mocked_run:
        status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    assert mocked_run.call_count == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["selected_chunk_count"] == 10
    assert summary["chunk_results"][0]["overall_status"] == "DRY_RUN"
    assert summary["chunk_results"][0]["command_count"] == 8


def test_real_execution_requires_plan_allow_real_emx(tmp_path):
    mod = _load_module()
    plan = _write_plan(tmp_path, status="PASS", allow_real_emx=False)

    with mock.patch.object(mod.subprocess, "run") as mocked_run:
        status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out"), "--allow-real-emx", "--no-fail-exit"])

    assert status == 0
    assert mocked_run.call_count == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["plan_checks"] if check["status"] == "FAIL"]
    assert "plan was created for real EMX" in failed


def test_passed_plan_missing_nn_command_is_not_executable(tmp_path):
    mod = _load_module()
    plan = _write_plan(tmp_path, status="PASS", allow_real_emx=True, drop_command="train_nn_architecture_search")

    with mock.patch.object(mod.subprocess, "run") as mocked_run:
        status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out"), "--allow-real-emx", "--no-fail-exit"])

    assert status == 0
    assert mocked_run.call_count == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["plan_checks"] if check["status"] == "FAIL"]
    assert "chunk 1 command train_nn_architecture_search present" in failed


def test_real_execution_runs_chunks_and_stops_on_failed_checkpoint(tmp_path):
    mod = _load_module()
    plan = _write_plan(tmp_path, status="PASS", allow_real_emx=True, chunks=10)
    seen: list[str] = []

    def fake_run(command, **kwargs):
        joined = " ".join(str(item) for item in command)
        seen.append(joined)
        if "audit_s8p_million_chunk_checkpoint.py" in joined:
            out_index = command.index("--out-dir") + 1
            checkpoint = Path(command[out_index]) / "s8p_million_chunk_checkpoint_summary.json"
            status = "FAIL" if "chunk_01" in str(checkpoint) else "PASS"
            _write_checkpoint(checkpoint, status=status)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--plan-summary",
                str(plan),
                "--out-dir",
                str(tmp_path / "out"),
                "--allow-real-emx",
                "--no-fail-exit",
            ]
        )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "STOP_MILLION_CAMPAIGN_FIX_FAILED_CHUNK"
    assert summary["completed_chunk_count"] == 0
    assert summary["chunk_results"][0]["checkpoint_summary"]["overall_status"] == "FAIL"
    assert any("chunk 1" in item or "--chunk 1" in item for item in seen)
    assert not any("chunk 2" in item or "--chunk 2" in item for item in seen)


def test_real_execution_passes_selected_chunk_range(tmp_path):
    mod = _load_module()
    plan = _write_plan(tmp_path, status="PASS", allow_real_emx=True, chunks=10)

    def fake_run(command, **kwargs):
        joined = " ".join(str(item) for item in command)
        if "audit_s8p_million_chunk_checkpoint.py" in joined:
            out_index = command.index("--out-dir") + 1
            checkpoint = Path(command[out_index]) / "s8p_million_chunk_checkpoint_summary.json"
            _write_checkpoint(checkpoint, status="PASS")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--plan-summary",
                str(plan),
                "--out-dir",
                str(tmp_path / "out"),
                "--start-chunk",
                "2",
                "--stop-chunk",
                "2",
                "--allow-real-emx",
            ]
        )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["completed_chunk_count"] == 1
    assert summary["chunk_results"][0]["chunk_index"] == 2
