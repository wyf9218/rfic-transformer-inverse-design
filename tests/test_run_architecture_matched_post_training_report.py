from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_architecture_matched_post_training_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("post_training_report_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fake_builder(module, *, n: int = 3):
    def build_statistics(_run_dir, out_dir, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=False)
        _write_json(out_dir / "EVALUATION_CONTRACT.json", {"schema": "fixture_contract_v1"})
        _write_json(
            out_dir / "INPUT_IDENTITY_AUDIT.json",
            {
                "schema": "fixture_identity_v1",
                "release_gate_status": "PASS",
                "gates": {"training": True, "evaluation": True, "identity": True},
            },
        )
        _write_json(out_dir / "MODEL_CONTRACT_COMPARISON.json", {"schema": "fixture_models_v1"})
        _write_csv(
            out_dir / "per_target_paired_errors.csv",
            [{"target_id": f"t{index}", "value": index} for index in range(n)],
        )
        for filename in (
            "feature_metrics_long.csv",
            "joint_metrics.csv",
            "paired_delta_summary.csv",
            "paired_bootstrap_sensitivity.csv",
            "training_curves_long.csv",
        ):
            _write_csv(out_dir / filename, [{"fixture": 1}])
        _write_json(out_dir / "geometry_feasibility_summary.json", {"schema": "fixture_geometry_v1"})
        _write_json(out_dir / "training_runtime_summary.json", {"schema": "fixture_runtime_v1"})
        (out_dir / "ADVISOR_REPORT_NOTES.md").write_text("fixture\n", encoding="utf-8")
        outputs = {}
        for path in out_dir.iterdir():
            if path.is_file():
                record = {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": path.stat().st_size,
                }
                if path.suffix == ".csv":
                    with path.open(newline="", encoding="utf-8") as handle:
                        record["row_count"] = sum(1 for _ in csv.DictReader(handle))
                outputs[path.name] = record
        _write_json(
            out_dir / "REPORT_SUMMARY.json",
            {
                "schema": "architecture_matched_fixed8k_report_summary_v1",
                "report_status": "STATISTICS_PASS_FIGURES_PENDING",
                "comparison": {
                    "reference_name": module.statistics_builder.REFERENCE_DISPLAY_NAME,
                    "candidate_name": module.statistics_builder.CANDIDATE_DISPLAY_NAME,
                    "n": n,
                    "panel": module.statistics_builder.PANEL,
                    "evidence_label": module.statistics_builder.EVIDENCE_LABEL,
                },
                "outputs": outputs,
            },
        )
        return {"out_dir": out_dir, "n": n}

    return build_statistics


def _fake_renderer(module):
    def render_report(report_dir):
        figures = report_dir / "figures"
        figures.mkdir()
        for basename in module.FIGURE_BASENAMES:
            (figures / f"{basename}.png").write_bytes(b"synthetic png")
            (figures / f"{basename}.svg").write_text("<svg>synthetic</svg>\n", encoding="utf-8")
        return {"figure_count": 20}

    return render_report


def _run(module, tmp_path: Path, monkeypatch, *, generated=None):
    run_dir = tmp_path / "fixture_run"
    run_dir.mkdir(exist_ok=True)
    report_root = tmp_path / "fixture_reports"
    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(module.statistics_builder, "build_statistics", _fake_builder(module))
    monkeypatch.setattr(module.report_renderer, "render_report", _fake_renderer(module))
    return module.run_post_training_report(
        run_dir,
        report_root=report_root,
        expected_run_id="fixture_run",
        expected_trainer_pid=123,
        synthetic_fixture=True,
        synthetic_expected_targets_sha256="a" * 64,
        synthetic_expected_target_rows=5,
        synthetic_expected_legacy_rows=3,
        synthetic_bootstrap_replicates=7,
        synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
        generated_utc=generated or datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc),
    )


def test_atomic_synthetic_release_has_all_artifacts_and_checksums(tmp_path, monkeypatch):
    module = _load_module()
    final_dir = _run(module, tmp_path, monkeypatch)

    assert final_dir.name == "architecture_matched_100k_vs_200k_fixed8k_v1_20260824T010203Z"
    assert final_dir.parent == (tmp_path / "fixture_reports").resolve()
    for filename in module.REQUIRED_ROOT_FILES:
        assert (final_dir / filename).is_file()
    assert (final_dir / "SHA256SUMS.txt").is_file()
    assert len(list((final_dir / "figures").glob("*.png"))) == 10
    assert len(list((final_dir / "figures").glob("*.svg"))) == 10

    receipt = json.loads((final_dir / "FINAL_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS_RELEASE_READY"
    assert receipt["atomic_publish"] is True
    assert receipt["synthetic_fixture"] is True
    command = (final_dir / "POST_TRAINING_COMMAND.txt").read_text(encoding="utf-8")
    assert str(SCRIPT) in command
    assert "--synthetic-fixture" in command

    declared = {}
    for line in (final_dir / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, relative = line.split(maxsplit=1)
        declared[relative] = digest
    actual_files = {
        path.relative_to(final_dir).as_posix()
        for path in final_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    assert set(declared) == actual_files
    for relative, digest in declared.items():
        assert hashlib.sha256((final_dir / relative).read_bytes()).hexdigest() == digest


def test_existing_final_directory_is_no_clobber(tmp_path, monkeypatch):
    module = _load_module()
    report_root = tmp_path / "fixture_reports"
    report_root.mkdir()
    final_dir = report_root / "architecture_matched_100k_vs_200k_fixed8k_v1_20260824T010203Z"
    final_dir.mkdir()
    sentinel = final_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", lambda *_args, **_kwargs: {"ok": True})

    with pytest.raises(module.PostTrainingReportError, match="no-clobber"):
        module.run_post_training_report(
            tmp_path / "unused_run",
            report_root=report_root,
            expected_run_id="fixture_run",
            expected_trainer_pid=123,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
            generated_utc=datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc),
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(report_root.glob("*.publish.lock")) == []


def test_existing_publication_lock_is_fail_closed_and_preserved(tmp_path, monkeypatch):
    module = _load_module()
    report_root = tmp_path / "fixture_reports"
    report_root.mkdir()
    timestamp = "20260824T010203Z"
    final_name = f"{module.REPORT_PREFIX}{timestamp}"
    lock = report_root / f".{final_name}.publish.lock"
    lock.write_text("pid=someone-else\n", encoding="ascii")
    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", lambda *_args, **_kwargs: {"ok": True})

    with pytest.raises(module.PostTrainingReportError, match="publication lock"):
        module.run_post_training_report(
            tmp_path / "fixture_run",
            report_root=report_root,
            expected_run_id="fixture_run",
            expected_trainer_pid=123,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
            generated_utc=datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc),
        )
    assert lock.read_text(encoding="ascii") == "pid=someone-else\n"


def test_terminal_gate_failure_creates_no_report_root(tmp_path, monkeypatch):
    module = _load_module()
    report_root = tmp_path / "fixture_reports"

    def blocked(*_args, **_kwargs):
        raise module.statistics_builder.ContractError("training terminal is not PASS")

    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", blocked)
    with pytest.raises(module.statistics_builder.ContractError, match="not PASS"):
        module.run_post_training_report(
            tmp_path / "fixture_run",
            report_root=report_root,
            expected_run_id="fixture_run",
            expected_trainer_pid=123,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
        )
    assert not report_root.exists()


def test_renderer_failure_removes_owned_staging(tmp_path, monkeypatch):
    module = _load_module()
    run_dir = tmp_path / "fixture_run"
    run_dir.mkdir()
    report_root = tmp_path / "fixture_reports"
    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(module.statistics_builder, "build_statistics", _fake_builder(module))
    monkeypatch.setattr(module.report_renderer, "render_report", lambda _path: (_ for _ in ()).throw(ValueError("render failed")))
    with pytest.raises(ValueError, match="render failed"):
        module.run_post_training_report(
            run_dir,
            report_root=report_root,
            expected_run_id="fixture_run",
            expected_trainer_pid=123,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_expected_legacy_rows=3,
            synthetic_bootstrap_replicates=7,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
            generated_utc=datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc),
        )
    assert report_root.exists()
    assert list(report_root.iterdir()) == []


def test_synthetic_mode_rejects_formal_reports_directory(tmp_path, monkeypatch):
    module = _load_module()
    called = False

    def discover(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", discover)
    with pytest.raises(module.PostTrainingReportError, match="forbidden"):
        module.run_post_training_report(
            tmp_path / "fixture_run",
            report_root=module.FORMAL_REPORT_ROOT,
            expected_run_id="fixture_run",
            expected_trainer_pid=123,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
        )
    assert called is False


def test_synthetic_mode_rejects_persistent_repository_directory(tmp_path, monkeypatch):
    module = _load_module()
    called = False

    def discover(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(module.statistics_builder, "discover_controller_bundle", discover)
    with pytest.raises(module.PostTrainingReportError, match="platform temporary directory"):
        module.run_post_training_report(
            tmp_path / "fixture_run",
            report_root=ROOT / "docs" / "__forbidden_synthetic_fixed8k_reports__",
            expected_run_id="fixture_run",
            expected_trainer_pid=123,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.02},
        )
    assert called is False


def test_formal_command_receipt_uses_private_path_free_placeholders():
    module = _load_module()
    command = module.portable_formal_post_training_command(
        module.statistics_builder.EXPECTED_RUN_ID,
        module.statistics_builder.EXPECTED_TRAINER_PID,
    )
    assert str(ROOT) not in command
    assert "/Users/" not in command
    assert "$RFIC_STATISTICS_REPO" in command
    assert "$RFIC_CONTROLLER_RUN_DIR" in command
    assert module.statistics_builder.EXPECTED_RUN_ID in command
    assert str(module.statistics_builder.EXPECTED_TRAINER_PID) in command
