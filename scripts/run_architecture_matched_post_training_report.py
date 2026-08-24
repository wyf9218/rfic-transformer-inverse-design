#!/usr/bin/env python3
"""Atomically build the post-training architecture-matched fixed8k report.

The runner is deliberately terminal-only: it validates completed controller
receipts, builds statistics in a hidden staging directory, renders all figures,
writes the final receipt and checksum index, and only then atomically publishes
the new no-clobber report directory.  It never inspects a live process or sends
signals, and it never writes into the controller run directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_architecture_matched_fixed8k_statistics as statistics_builder
import render_architecture_matched_fixed8k_report as report_renderer


REPORT_PREFIX = "architecture_matched_100k_vs_200k_fixed8k_v1_"
FORMAL_REPORT_ROOT = (PROJECT_ROOT / "reports").resolve()
REQUIRED_ROOT_FILES = (
    "EVALUATION_CONTRACT.json",
    "INPUT_IDENTITY_AUDIT.json",
    "MODEL_CONTRACT_COMPARISON.json",
    "per_target_paired_errors.csv",
    "feature_metrics_long.csv",
    "joint_metrics.csv",
    "paired_delta_summary.csv",
    "paired_bootstrap_sensitivity.csv",
    "training_curves_long.csv",
    "geometry_feasibility_summary.json",
    "training_runtime_summary.json",
    "REPORT_SUMMARY.json",
    "ADVISOR_REPORT_NOTES.md",
    "POST_TRAINING_COMMAND.txt",
    "FINAL_RECEIPT.json",
)
FIGURE_BASENAMES = (
    "01_model_architecture_and_data_counts",
    "02_training_curves",
    "03_feature_mae_comparison",
    "04_feature_rmse_comparison",
    "05_target_vs_prediction_four_panel",
    "06_absolute_error_cdf_four_panel",
    "07_p50_p90_p95_tail_error",
    "08_q_target_met_and_shortfall",
    "09_success_rate_vs_tolerance",
    "10_geometry_feasibility_and_runtime",
)


class PostTrainingReportError(RuntimeError):
    """A release-blocking orchestration error."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PostTrainingReportError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    _require(path.is_file() and path.stat().st_size > 0, f"missing JSON: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PostTrainingReportError(f"invalid JSON {path.name}: {exc}") from exc
    _require(isinstance(value, dict), f"JSON root is not an object: {path.name}")
    return value


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def _atomic_replace_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _utc_timestamp(now: Optional[datetime] = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_fixture_utc(value: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PostTrainingReportError("synthetic UTC must have YYYYmmddTHHMMSSZ format") from exc
    return parsed


def _relative_file_records(root: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            relative = path.relative_to(root).as_posix()
            records[relative] = {
                "sha256": _sha256(path),
                "size_bytes": int(path.stat().st_size),
            }
    return records


def _write_sha256s(path: Path, records: Mapping[str, Mapping[str, Any]]) -> None:
    with path.open("x", encoding="ascii", newline="\n") as handle:
        for relative in sorted(records):
            _require(not relative.startswith("/") and ".." not in Path(relative).parts, "unsafe checksum relative path")
            handle.write(f"{records[relative]['sha256']}  {relative}\n")


def _verify_sha256s(root: Path) -> int:
    sums_path = root / "SHA256SUMS.txt"
    _require(sums_path.is_file() and sums_path.stat().st_size > 0, "SHA256SUMS.txt is absent")
    seen = set()
    for line_number, line in enumerate(sums_path.read_text(encoding="ascii").splitlines(), 1):
        if not line:
            continue
        fields = line.split(maxsplit=1)
        _require(len(fields) == 2, f"malformed SHA256SUMS line {line_number}")
        digest, relative = fields
        relative = relative.lstrip("*")
        _require(relative not in seen, f"duplicate SHA256SUMS path: {relative}")
        seen.add(relative)
        target = (root / relative).resolve()
        _require(_is_within(target, root) and target.is_file(), f"unsafe or missing checksum target: {relative}")
        _require(_sha256(target) == digest, f"checksum mismatch: {relative}")
    expected = set(_relative_file_records(root))
    _require(seen == expected, "SHA256SUMS coverage is not exact")
    return len(seen)


def _csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _row in csv.DictReader(handle))


def canonical_post_training_argv(
    controller_run_dir: Path,
    expected_run_id: str,
    expected_trainer_pid: int,
    report_root: Path = FORMAL_REPORT_ROOT,
) -> Sequence[str]:
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--controller-run-dir",
        str(controller_run_dir.expanduser().resolve()),
        "--expected-run-id",
        expected_run_id,
        "--expected-trainer-pid",
        str(expected_trainer_pid),
    ]
    if report_root.expanduser().resolve() != FORMAL_REPORT_ROOT:
        argv.extend(["--report-root", str(report_root.expanduser().resolve())])
    return argv


def portable_formal_post_training_command(expected_run_id: str, expected_trainer_pid: int) -> str:
    """Return a private-path-free command for the formal report receipt."""
    relative_script = Path("scripts") / Path(__file__).name
    return (
        ': "${RFIC_STATISTICS_REPO:?set RFIC_STATISTICS_REPO to this clone}"; '
        ': "${RFIC_CONTROLLER_RUN_DIR:?set RFIC_CONTROLLER_RUN_DIR to the completed controller run}"; '
        f'python3 "$RFIC_STATISTICS_REPO/{relative_script.as_posix()}" '
        ' --controller-run-dir "$RFIC_CONTROLLER_RUN_DIR"'
        f" --expected-run-id {shlex.quote(expected_run_id)}"
        f" --expected-trainer-pid {int(expected_trainer_pid)}\n"
    )


def _validate_complete_staging(staging: Path, expected_n: int) -> None:
    for filename in REQUIRED_ROOT_FILES:
        path = staging / filename
        _require(path.is_file() and path.stat().st_size > 0, f"required report artifact is missing: {filename}")
    figures = staging / "figures"
    _require(figures.is_dir(), "figures directory is absent")
    expected_figures = {
        f"{basename}.{extension}"
        for basename in FIGURE_BASENAMES
        for extension in ("png", "svg")
    }
    actual_figures = {path.name for path in figures.iterdir() if path.is_file()}
    _require(actual_figures == expected_figures, "figure file set is not exact")
    for name in expected_figures:
        _require((figures / name).stat().st_size > 0, f"empty figure: {name}")

    identity = _read_json(staging / "INPUT_IDENTITY_AUDIT.json")
    _require(identity.get("release_gate_status") == "PASS", "identity audit is not PASS")
    gates = identity.get("gates")
    _require(isinstance(gates, dict) and gates and all(value is True for value in gates.values()), "identity audit contains a failed gate")
    summary = _read_json(staging / "REPORT_SUMMARY.json")
    _require(summary.get("report_status") == "READY_FOR_RELEASE", "report summary is not release ready")
    _require(int((summary.get("comparison") or {}).get("n") or 0) == expected_n, "report summary n differs")
    _require(_csv_row_count(staging / "per_target_paired_errors.csv") == expected_n, "paired target CSV row count differs")


def run_post_training_report(
    controller_run_dir: Path,
    report_root: Path = FORMAL_REPORT_ROOT,
    expected_run_id: str = statistics_builder.EXPECTED_RUN_ID,
    expected_trainer_pid: int = statistics_builder.EXPECTED_TRAINER_PID,
    synthetic_fixture: bool = False,
    synthetic_expected_targets_sha256: Optional[str] = None,
    synthetic_expected_target_rows: int = statistics_builder.EXPECTED_TARGET_FRAME_ROWS,
    synthetic_expected_legacy_rows: int = statistics_builder.EXPECTED_LEGACY_ROWS,
    synthetic_bootstrap_replicates: int = 64,
    synthetic_inference_seconds: Optional[Mapping[str, float]] = None,
    generated_utc: Optional[datetime] = None,
) -> Path:
    run_dir = controller_run_dir.expanduser().resolve()
    root = report_root.expanduser().resolve()
    if synthetic_fixture:
        _require(not _is_within(root, FORMAL_REPORT_ROOT), "synthetic fixture report root is forbidden inside formal reports")
        _require(
            _is_within(root, Path(tempfile.gettempdir()).resolve()),
            "synthetic fixture report root must be inside the platform temporary directory",
        )
        _require(
            _is_within(run_dir, Path(tempfile.gettempdir()).resolve()),
            "synthetic controller fixture must be inside the platform temporary directory",
        )
        _require(synthetic_expected_targets_sha256 is not None, "synthetic target SHA is required")
        _require(synthetic_inference_seconds is not None, "synthetic inference timings are required")
        bootstrap_replicates = int(synthetic_bootstrap_replicates)
    else:
        _require(root == FORMAL_REPORT_ROOT, "formal reports must use the repository reports directory")
        _require(expected_run_id == statistics_builder.EXPECTED_RUN_ID, "formal run_id is fixed to the authorized controller run")
        _require(expected_trainer_pid == statistics_builder.EXPECTED_TRAINER_PID, "formal trainer pid receipt identity is fixed")
        bootstrap_replicates = statistics_builder.BOOTSTRAP_REPLICATES

    # This is the first operation that touches run artifacts.  It only reads
    # terminal receipts; no output or staging path exists yet.
    statistics_builder.discover_controller_bundle(run_dir, expected_run_id, expected_trainer_pid)

    root.mkdir(parents=True, exist_ok=True)
    timestamp = _utc_timestamp(generated_utc)
    final_dir = root / f"{REPORT_PREFIX}{timestamp}"
    publish_lock = root / f".{final_dir.name}.publish.lock"
    try:
        with publish_lock.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise PostTrainingReportError(
            f"no-clobber publication lock already exists: {publish_lock.name}"
        ) from exc
    staging = root / f".{REPORT_PREFIX}{timestamp}.staging-{os.getpid()}-{time.time_ns()}"

    try:
        _require(not final_dir.exists(), f"no-clobber formal report already exists: {final_dir.name}")
        _require(not staging.exists(), "staging directory collision")
        build_result = statistics_builder.build_statistics(
            run_dir,
            staging,
            expected_run_id=expected_run_id,
            expected_trainer_pid=expected_trainer_pid,
            bootstrap_replicates=bootstrap_replicates,
            synthetic_fixture=synthetic_fixture,
            synthetic_expected_targets_sha256=synthetic_expected_targets_sha256,
            synthetic_expected_target_rows=synthetic_expected_target_rows,
            synthetic_expected_legacy_rows=synthetic_expected_legacy_rows,
            synthetic_inference_seconds=synthetic_inference_seconds,
        )
        render_result = report_renderer.render_report(staging)

        summary_path = staging / "REPORT_SUMMARY.json"
        report_summary = _read_json(summary_path)
        figures = {}
        for path in sorted((staging / "figures").iterdir()):
            if path.is_file():
                figures[path.name] = {"sha256": _sha256(path), "size_bytes": int(path.stat().st_size)}
        report_summary["report_status"] = "READY_FOR_RELEASE"
        report_summary["figures"] = figures
        report_summary["figure_count"] = len(figures)
        report_summary["renderer_result"] = {
            "status": "PASS",
            "figure_count": int(render_result.get("figure_count", len(figures))) if isinstance(render_result, dict) else len(figures),
        }
        report_summary["canonical_payload_sha256_without_self"] = statistics_builder._canonical_sha256(
            {key: value for key, value in report_summary.items() if key != "canonical_payload_sha256_without_self"}
        )
        _atomic_replace_json(summary_path, report_summary)

        command_argv = canonical_post_training_argv(run_dir, expected_run_id, expected_trainer_pid, root)
        if synthetic_fixture:
            command_argv = [
                *command_argv,
                "--synthetic-fixture",
                "--synthetic-expected-targets-sha256",
                str(synthetic_expected_targets_sha256),
                "--synthetic-expected-target-rows",
                str(synthetic_expected_target_rows),
                "--synthetic-expected-legacy-rows",
                str(synthetic_expected_legacy_rows),
                "--synthetic-bootstrap-replicates",
                str(bootstrap_replicates),
                "--synthetic-inference-runtime-100k-seconds",
                str(synthetic_inference_seconds["100k"]),
                "--synthetic-inference-runtime-200k-seconds",
                str(synthetic_inference_seconds["200k"]),
                "--synthetic-generated-utc",
                timestamp,
            ]
        command_text = (
            shlex.join(command_argv) + "\n"
            if synthetic_fixture
            else portable_formal_post_training_command(expected_run_id, expected_trainer_pid)
        )
        command_path = staging / "POST_TRAINING_COMMAND.txt"
        _write_text_exclusive(command_path, command_text)

        pre_receipt_records = _relative_file_records(staging)
        final_receipt = {
            "schema": "architecture_matched_fixed8k_post_training_report_receipt_v1",
            "overall_status": "PASS_RELEASE_READY",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "report_directory_name": final_dir.name,
            "run_id": expected_run_id,
            "trainer_pid_receipt_identity": expected_trainer_pid,
            "comparison": {
                "reference_name": statistics_builder.REFERENCE_DISPLAY_NAME,
                "candidate_name": statistics_builder.CANDIDATE_DISPLAY_NAME,
                "panel": statistics_builder.PANEL,
                "n": build_result["n"],
                "evidence_label": statistics_builder.EVIDENCE_LABEL,
            },
            "release_gates_pass": True,
            "statistics_report_summary_sha256": _sha256(summary_path),
            "artifact_count_before_final_receipt": len(pre_receipt_records),
            "figure_count": len(figures),
            "no_clobber": True,
            "no_clobber_publication_lock": publish_lock.name,
            "atomic_publish": True,
            "post_training_command_contains_private_local_path": synthetic_fixture,
            "synthetic_fixture": synthetic_fixture,
        }
        _write_json_exclusive(staging / "FINAL_RECEIPT.json", final_receipt)
        checksum_records = _relative_file_records(staging)
        _write_sha256s(staging / "SHA256SUMS.txt", checksum_records)
        checksum_count = _verify_sha256s(staging)
        _require(checksum_count == len(checksum_records), "checksum verification count differs")
        _validate_complete_staging(staging, build_result["n"])

        # The exclusive per-destination lock covers the final existence check
        # and same-filesystem atomic publication for every instance of this tool.
        _require(not final_dir.exists(), "formal report target appeared during staging")
        os.rename(staging, final_dir)
        return final_dir
    except Exception:
        if staging.exists() and _is_within(staging, root) and staging.name.startswith(f".{REPORT_PREFIX}"):
            shutil.rmtree(staging)
        raise
    finally:
        if publish_lock.exists() and _is_within(publish_lock, root):
            publish_lock.unlink()


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-run-dir", required=True)
    parser.add_argument("--report-root", default=str(FORMAL_REPORT_ROOT))
    parser.add_argument("--expected-run-id", default=statistics_builder.EXPECTED_RUN_ID)
    parser.add_argument("--expected-trainer-pid", type=int, default=statistics_builder.EXPECTED_TRAINER_PID)
    parser.add_argument("--synthetic-fixture", action="store_true")
    parser.add_argument("--synthetic-expected-targets-sha256", default="")
    parser.add_argument("--synthetic-expected-target-rows", type=int, default=statistics_builder.EXPECTED_TARGET_FRAME_ROWS)
    parser.add_argument("--synthetic-expected-legacy-rows", type=int, default=statistics_builder.EXPECTED_LEGACY_ROWS)
    parser.add_argument("--synthetic-bootstrap-replicates", type=int, default=64)
    parser.add_argument("--synthetic-inference-runtime-100k-seconds", type=float, default=0.0)
    parser.add_argument("--synthetic-inference-runtime-200k-seconds", type=float, default=0.0)
    parser.add_argument("--synthetic-generated-utc", default="")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    generated_utc = None
    synthetic_timings = None
    if args.synthetic_fixture:
        generated_utc = _parse_fixture_utc(args.synthetic_generated_utc) if args.synthetic_generated_utc else datetime(2026, 8, 24, tzinfo=timezone.utc)
        synthetic_timings = {
            "100k": args.synthetic_inference_runtime_100k_seconds,
            "200k": args.synthetic_inference_runtime_200k_seconds,
        }
    result = run_post_training_report(
        Path(args.controller_run_dir),
        report_root=Path(args.report_root),
        expected_run_id=args.expected_run_id,
        expected_trainer_pid=args.expected_trainer_pid,
        synthetic_fixture=args.synthetic_fixture,
        synthetic_expected_targets_sha256=args.synthetic_expected_targets_sha256 or None,
        synthetic_expected_target_rows=args.synthetic_expected_target_rows,
        synthetic_expected_legacy_rows=args.synthetic_expected_legacy_rows,
        synthetic_bootstrap_replicates=args.synthetic_bootstrap_replicates,
        synthetic_inference_seconds=synthetic_timings,
        generated_utc=generated_utc,
    )
    print(str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
