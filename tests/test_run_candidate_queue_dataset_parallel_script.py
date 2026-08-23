import csv
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock


def _load_parallel_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_candidate_queue_dataset_parallel.py"
    spec = importlib.util.spec_from_file_location("run_candidate_queue_dataset_parallel_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_candidate_csv(path: Path, count: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "candidate__geom__w_um"])
        writer.writeheader()
        for index in range(count):
            writer.writerow({"candidate_id": f"c{index}", "candidate__geom__w_um": 1.0 + index})


def _write_touchstone(path: Path, *, ports: int = 8, freqs_ghz: tuple[float, ...] = (5.0, 5.1, 5.2)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"! synthetic {ports}-port test file\n")
        handle.write("# GHz S RI R 50\n")
        for freq in freqs_ghz:
            values = [f"{freq:g}"]
            for row in range(ports):
                for col in range(ports):
                    real = 0.05 if row == col else 0.001 * (row + 1) * (col + 1)
                    imag = -0.01 if row == col else 0.0
                    values.extend([f"{real:g}", f"{imag:g}"])
            handle.write(" ".join(values) + "\n")


def test_split_rows_uses_requested_worker_count_when_rows_exceed_jobs() -> None:
    mod = _load_parallel_module()
    rows = [{"candidate_id": f"c{index}"} for index in range(20)]

    shards = mod._split_rows(rows, 8)

    assert len(shards) == 8
    assert [len(shard) for shard in shards] == [3, 3, 3, 3, 2, 2, 2, 2]
    assert [row["candidate_id"] for shard in shards for row in shard] == [f"c{index}" for index in range(20)]


def test_parallel_runner_splits_shards_and_merges_dataset_rows() -> None:
    mod = _load_parallel_module()

    def fake_run_shard(index, row_count, csv_path, out_dir, args):
        out_dir.mkdir(parents=True, exist_ok=True)
        with Path(csv_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        dataset_csv = out_dir / "dataset_rows.csv"
        with dataset_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "ok"])
            writer.writeheader()
            for row in rows:
                writer.writerow({"candidate_id": row["candidate_id"], "ok": "true"})
        summary_path = out_dir / "candidate_queue_dataset_summary.json"
        summary = {"overall_status": "PASS", "dataset_rows_csv": str(dataset_csv)}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return mod.ShardRun(
            index=index,
            row_count=row_count,
            csv_path=Path(csv_path),
            out_dir=Path(out_dir),
            command=["fake"],
            returncode=0,
            stdout="",
            stderr="",
            summary_path=summary_path,
            summary=summary,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_csv = root / "candidates.csv"
        _write_candidate_csv(candidate_csv, 6)

        with mock.patch.object(mod, "_run_shard", side_effect=fake_run_shard):
            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--jobs",
                    "3",
                    "--max-count",
                    "5",
                    "--expected-count",
                    "5",
                    "--expected-jobs",
                    "3",
                    "--create-only",
                ]
            )

        summary = json.loads((root / "out" / "parallel_candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))
        with (root / "out" / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
            merged_rows = list(csv.DictReader(handle))

    assert status == 0
    assert summary["overall_status"] == "PASS"
    assert summary["jobs_requested"] == 3
    assert summary["expected_count"] == 5
    assert summary["expected_jobs"] == 3
    assert summary["input_row_count"] == 5
    assert summary["shard_count"] == 3
    assert [item["input_rows"] for item in summary["shards"]] == [2, 2, 1]
    assert len(merged_rows) == 5
    assert {row["parallel_shard"] for row in merged_rows} == {"000", "001", "002"}
    checks = {item["name"]: item for item in summary["checks"]}
    assert checks["merged_row_count_matches_input"]["pass"]
    assert checks["merged_count_matches_expected"]["pass"]


def test_parallel_runner_fails_without_candidate_rows() -> None:
    mod = _load_parallel_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_csv = root / "empty.csv"
        _write_candidate_csv(candidate_csv, 0)

        status = mod.main(
            [
                "--candidate-csv",
                str(candidate_csv),
                "--out-dir",
                str(root / "out"),
                "--jobs",
                "8",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "out" / "parallel_candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    assert summary["shard_count"] == 0
    assert summary["merged_row_count"] == 0


def test_parallel_runner_fails_when_a_shard_drops_rows() -> None:
    mod = _load_parallel_module()

    def fake_run_shard(index, row_count, csv_path, out_dir, args):
        out_dir.mkdir(parents=True, exist_ok=True)
        with Path(csv_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        dataset_csv = out_dir / "dataset_rows.csv"
        with dataset_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "ok"])
            writer.writeheader()
            output_rows = rows[:-1] if index == 1 else rows
            for row in output_rows:
                writer.writerow({"candidate_id": row["candidate_id"], "ok": "true"})
        summary_path = out_dir / "candidate_queue_dataset_summary.json"
        summary = {"overall_status": "PASS", "dataset_rows_csv": str(dataset_csv)}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return mod.ShardRun(
            index=index,
            row_count=row_count,
            csv_path=Path(csv_path),
            out_dir=Path(out_dir),
            command=["fake"],
            returncode=0,
            stdout="",
            stderr="",
            summary_path=summary_path,
            summary=summary,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_csv = root / "candidates.csv"
        _write_candidate_csv(candidate_csv, 6)

        with mock.patch.object(mod, "_run_shard", side_effect=fake_run_shard):
            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--jobs",
                    "3",
                    "--expected-count",
                    "6",
                    "--expected-jobs",
                    "3",
                    "--create-only",
                    "--no-fail-exit",
                ]
            )

        summary = json.loads((root / "out" / "parallel_candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    checks = {item["name"]: item for item in summary["checks"]}
    assert not checks["merged_row_count_matches_input"]["pass"]
    assert not checks["merged_count_matches_expected"]["pass"]


def test_parallel_runner_resume_completed_reuses_only_complete_matching_shards() -> None:
    mod = _load_parallel_module()

    def fake_run_shard(index, row_count, csv_path, out_dir, args):
        out_dir.mkdir(parents=True, exist_ok=True)
        with Path(csv_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        dataset_csv = out_dir / "dataset_rows.csv"
        with dataset_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "ok"])
            writer.writeheader()
            for row in rows:
                writer.writerow({"candidate_id": row["candidate_id"], "ok": "true"})
        summary_path = out_dir / "candidate_queue_dataset_summary.json"
        summary = {"overall_status": "PASS", "dataset_rows_csv": str(dataset_csv)}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return mod.ShardRun(
            index=index,
            row_count=row_count,
            csv_path=Path(csv_path),
            out_dir=Path(out_dir),
            command=["fake"],
            returncode=0,
            stdout="",
            stderr="",
            summary_path=summary_path,
            summary=summary,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_csv = root / "candidates.csv"
        _write_candidate_csv(candidate_csv, 4)
        completed = root / "out" / "parallel_shards" / "shard_000"
        completed.mkdir(parents=True)
        with (completed / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "ok"])
            writer.writeheader()
            writer.writerow({"candidate_id": "c0", "ok": "true"})
            writer.writerow({"candidate_id": "c1", "ok": "true"})
        (completed / "candidate_queue_dataset_summary.json").write_text(
            json.dumps({"overall_status": "PASS"}),
            encoding="utf-8",
        )

        with mock.patch.object(mod, "_run_shard", side_effect=fake_run_shard) as patched:
            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--jobs",
                    "2",
                    "--expected-count",
                    "4",
                    "--expected-jobs",
                    "2",
                    "--resume-completed",
                    "--create-only",
                ]
            )

        summary = json.loads((root / "out" / "parallel_candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))
        with (root / "out" / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
            merged_rows = list(csv.DictReader(handle))

    assert status == 0
    assert patched.call_count == 1
    assert summary["overall_status"] == "PASS"
    assert summary["resume_completed"] is True
    assert summary["reused_shard_count"] == 1
    assert summary["pending_shard_count"] == 1
    assert summary["shards"][0]["reused_existing"] is True
    assert len(merged_rows) == 4
    assert [row["candidate_id"] for row in merged_rows] == ["c0", "c1", "c2", "c3"]


def test_parallel_runner_passes_non_create_only_when_s8p_files_are_valid() -> None:
    mod = _load_parallel_module()

    def fake_run_shard(index, row_count, csv_path, out_dir, args):
        out_dir.mkdir(parents=True, exist_ok=True)
        with Path(csv_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        dataset_csv = out_dir / "dataset_rows.csv"
        with dataset_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "ok", "touchstone_path"])
            writer.writeheader()
            for row in rows:
                relative = Path("evaluations") / row["candidate_id"] / f"{row['candidate_id']}.s8p"
                _write_touchstone(Path(out_dir) / relative)
                writer.writerow({"candidate_id": row["candidate_id"], "ok": "true", "touchstone_path": str(relative)})
        summary_path = out_dir / "candidate_queue_dataset_summary.json"
        summary = {"overall_status": "PASS", "dataset_rows_csv": str(dataset_csv)}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return mod.ShardRun(
            index=index,
            row_count=row_count,
            csv_path=Path(csv_path),
            out_dir=Path(out_dir),
            command=["fake"],
            returncode=0,
            stdout="",
            stderr="",
            summary_path=summary_path,
            summary=summary,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_csv = root / "candidates.csv"
        _write_candidate_csv(candidate_csv, 2)

        with mock.patch.object(mod, "_run_shard", side_effect=fake_run_shard):
            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--jobs",
                    "2",
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--expected-ports",
                    "8",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--max-touchstone-checks",
                    "2",
                ]
            )

        summary = json.loads((root / "out" / "parallel_candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))

    checks = {item["name"]: item for item in summary["checks"]}
    assert status == 0
    assert summary["overall_status"] == "PASS"
    assert summary["touchstone_output_contract"]["checked"] is True
    assert summary["touchstone_output_contract"]["parsed_count"] == 2
    assert checks["merged_touchstone_files_exist"]["pass"]
    assert checks["sampled_touchstone_ports_match_expected"]["pass"]
    assert checks["sampled_touchstone_frequency_grid_matches_expected"]["pass"]


def test_parallel_runner_fails_non_create_only_when_s8p_file_is_empty() -> None:
    mod = _load_parallel_module()

    def fake_run_shard(index, row_count, csv_path, out_dir, args):
        out_dir.mkdir(parents=True, exist_ok=True)
        with Path(csv_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        dataset_csv = out_dir / "dataset_rows.csv"
        with dataset_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "ok", "touchstone_path"])
            writer.writeheader()
            for row in rows:
                relative = Path("evaluations") / row["candidate_id"] / f"{row['candidate_id']}.s8p"
                touchstone = Path(out_dir) / relative
                touchstone.parent.mkdir(parents=True, exist_ok=True)
                touchstone.write_text("", encoding="utf-8")
                writer.writerow({"candidate_id": row["candidate_id"], "ok": "true", "touchstone_path": str(relative)})
        summary_path = out_dir / "candidate_queue_dataset_summary.json"
        summary = {"overall_status": "PASS", "dataset_rows_csv": str(dataset_csv)}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return mod.ShardRun(
            index=index,
            row_count=row_count,
            csv_path=Path(csv_path),
            out_dir=Path(out_dir),
            command=["fake"],
            returncode=0,
            stdout="",
            stderr="",
            summary_path=summary_path,
            summary=summary,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_csv = root / "candidates.csv"
        _write_candidate_csv(candidate_csv, 1)

        with mock.patch.object(mod, "_run_shard", side_effect=fake_run_shard):
            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--jobs",
                    "1",
                    "--expected-count",
                    "1",
                    "--expected-jobs",
                    "1",
                    "--expected-ports",
                    "8",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--no-fail-exit",
                ]
            )

        summary = json.loads((root / "out" / "parallel_candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))

    checks = {item["name"]: item for item in summary["checks"]}
    assert status == 0
    assert summary["overall_status"] == "FAIL"
    assert summary["touchstone_output_contract"]["existing_file_count"] == 1
    assert summary["touchstone_output_contract"]["nonzero_file_count"] == 0
    assert not checks["merged_touchstone_files_nonzero"]["pass"]
