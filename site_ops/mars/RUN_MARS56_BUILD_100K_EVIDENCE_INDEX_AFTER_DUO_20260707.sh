#!/usr/bin/env bash
set -euo pipefail

# Build a remote evidence index for formal 100k MARS56 checkpoints.
#
# Usage after Duo is available:
#   bash RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh
#
# Local dry-run, no SSH:
#   LOCAL_DRY_RUN=1 bash RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh
#
# This script does not run EMX or model tests. It only scans existing 100k
# datasets and checkpoint outputs, then writes a JSON/Markdown evidence index
# under the campaign status directory.

JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
USER_NAME="${USER_NAME:-researcher}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"

EXPECTED_PER_CHUNK="${EXPECTED_PER_CHUNK:-100000}"
EXPECTED_CHUNKS="${EXPECTED_CHUNKS:-10}"

case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
if [[ "$SSH_CONTROL_PATH" == *$'\n'* || "$SSH_CONTROL_PATH" == *$'\r'* ]]; then
  echo "ERROR: SSH_CONTROL_PATH contains unsupported newline characters." >&2
  exit 2
fi
for value in "$EXPECTED_PER_CHUNK" "$EXPECTED_CHUNKS"; do
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "ERROR: EXPECTED_PER_CHUNK and EXPECTED_CHUNKS must be positive decimal integers." >&2
    exit 2
  fi
done

SSH_ARGS=(-tt)
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_RUN <<'REMOTE' || true
set -euo pipefail

BASE=/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256
PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi
EXPECTED_PER_CHUNK="${EXPECTED_PER_CHUNK:-100000}"
EXPECTED_CHUNKS="${EXPECTED_CHUNKS:-10}"
STATUS_DIR=$BASE/status/100k_checkpoint_evidence_index_20260707
JSON_OUT=$STATUS_DIR/mars56_100k_checkpoint_evidence_index.json
MD_OUT=$STATUS_DIR/mars56_100k_checkpoint_evidence_index.md

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
printf 'status_dir=%s\n' "$STATUS_DIR"

mkdir -p "$STATUS_DIR"
"$PY" - "$BASE" "$EXPECTED_PER_CHUNK" "$EXPECTED_CHUNKS" "$JSON_OUT" "$MD_OUT" <<'PY'
from __future__ import annotations

import json
import csv
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

base = Path(sys.argv[1])
expected_per_chunk = int(sys.argv[2])
expected_chunks = int(sys.argv[3])
json_out = Path(sys.argv[4])
md_out = Path(sys.argv[5])

REQUIRED_STEPS = {
    "stable_index",
    "response_features",
    "enrichment",
    "uniformity",
    "uniformity_manifest",
    "training",
    "model",
    "traceability",
}
EXPECTED_PHYSICAL_RANGES = {
    "lp": (0.5, 3.0),
    "ls": (0.5, 3.0),
    "q": (5.0, 25.0),
    "k": (0.0, 0.8),
}
EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION = 0.50


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": type(exc).__name__, "_path": str(path)}


def json_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    data = load_json(path)
    if "_parse_error" in data:
        return f"PARSE_ERROR:{data['_parse_error']}"
    return str(data.get("overall_status") or "NO_STATUS")


def nonempty_s4p_count(dataset: Path) -> int:
    if not dataset.is_dir():
        return 0
    count = 0
    for path in dataset.rglob("*.s4p"):
        try:
            if path.stat().st_size > 0:
                count += 1
        except OSError:
            pass
    return count


def latest_checkpoint_summary(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = list(root.rglob("mars56_s4p_physical_checkpoint_pipeline_summary.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def checkpoint_proof(path: Path | None, expected: int) -> tuple[str, list[str], dict]:
    if path is None or not path.exists():
        return "MISSING", ["summary_missing"], {}
    data = load_json(path)
    if "_parse_error" in data:
        return "FAIL", [f"summary_parse_error:{data['_parse_error']}"], data
    reasons: list[str] = []
    if data.get("overall_status") != "PASS":
        reasons.append(f"overall_status={data.get('overall_status')!r}")
    try:
        actual_expected = int(data.get("expected_count"))
    except Exception:
        actual_expected = None
    if actual_expected != expected:
        reasons.append(f"expected_count={actual_expected!r}")
    try:
        actual_min_valid = int(data.get("min_valid"))
    except Exception:
        actual_min_valid = None
    if actual_min_valid != expected:
        reasons.append(f"min_valid={actual_min_valid!r}")
    statuses = data.get("statuses") if isinstance(data.get("statuses"), dict) else {}
    missing_steps = sorted(REQUIRED_STEPS.difference(statuses))
    bad_steps = {
        key: statuses.get(key)
        for key in sorted(REQUIRED_STEPS.intersection(statuses))
        if statuses.get(key) != "PASS"
    }
    if missing_steps:
        reasons.append("missing_steps=" + ",".join(missing_steps))
    if bad_steps:
        reasons.append("bad_steps=" + ",".join(f"{key}:{value}" for key, value in bad_steps.items()))
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    physical_uniformity_gate = data.get("physical_uniformity_gate") if isinstance(data.get("physical_uniformity_gate"), dict) else {}
    if physical_uniformity_gate.get("require_four_d_gate") is not True:
        reasons.append(f"physical_uniformity_gate.require_four_d_gate={physical_uniformity_gate.get('require_four_d_gate')!r}")
    expected_uniformity_thresholds = {
        "min_1d_occupied_fraction": 0.90,
        "min_1d_entropy_fraction": 0.90,
        "max_1d_bin_imbalance": 2.50,
        "min_pair_occupied_fraction": 0.65,
        "min_pair_entropy_fraction": 0.80,
    }
    parsed_uniformity_thresholds: dict[str, float] = {}
    for threshold_name, expected_value in expected_uniformity_thresholds.items():
        try:
            actual_value = float(physical_uniformity_gate.get(threshold_name))
        except Exception:
            reasons.append(f"physical_uniformity_gate.{threshold_name}={physical_uniformity_gate.get(threshold_name)!r}")
            continue
        parsed_uniformity_thresholds[threshold_name] = actual_value
        if not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=1e-12):
            reasons.append(f"physical_uniformity_gate.{threshold_name}={actual_value},expected={expected_value}")
    try:
        gate_min_four_d = float(physical_uniformity_gate.get("min_four_d_occupied_fraction"))
    except Exception:
        reasons.append(f"physical_uniformity_gate.min_four_d_occupied_fraction={physical_uniformity_gate.get('min_four_d_occupied_fraction')!r}")
    else:
        if not math.isclose(gate_min_four_d, EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION, rel_tol=0.0, abs_tol=1e-12):
            reasons.append(
                f"physical_uniformity_gate.min_four_d_occupied_fraction={gate_min_four_d},expected={EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION}"
            )
    for step in ("uniformity", "training", "model"):
        step_details = details.get(step) if isinstance(details.get(step), dict) else {}
        for key in ("valid_feature_count", "training_count", "usable_row_count"):
            if key in step_details:
                try:
                    value = int(step_details[key])
                except Exception:
                    reasons.append(f"{step}.{key}={step_details[key]!r}")
                else:
                    if value < expected:
                        reasons.append(f"{step}.{key}={value}")
    model_details = details.get("model") if isinstance(details.get("model"), dict) else {}
    try:
        model_test_rows = int(model_details.get("test_row_count"))
    except Exception:
        reasons.append(f"model.test_row_count={model_details.get('test_row_count')!r}")
        model_test_rows = None
    else:
        if model_test_rows <= 0:
            reasons.append(f"model.test_row_count={model_test_rows}")
    model_metrics = model_details.get("metrics") if isinstance(model_details.get("metrics"), dict) else {}
    if not model_metrics:
        reasons.append("model.metrics=MISSING")
    else:
        try:
            metric_test_count = int(model_metrics.get("test_count"))
        except Exception:
            reasons.append(f"model.metrics.test_count={model_metrics.get('test_count')!r}")
        else:
            if metric_test_count <= 0:
                reasons.append(f"model.metrics.test_count={metric_test_count}")
            if model_test_rows is not None and metric_test_count != model_test_rows:
                reasons.append(f"model.metrics.test_count={metric_test_count},expected={model_test_rows}")
        try:
            geometry_count = int(model_metrics.get("geometry_count"))
        except Exception:
            reasons.append(f"model.metrics.geometry_count={model_metrics.get('geometry_count')!r}")
        else:
            if geometry_count <= 0:
                reasons.append(f"model.metrics.geometry_count={geometry_count}")
        for metric_key in ("max_normalized_mae", "max_normalized_rmse", "mean_normalized_mae", "mean_normalized_rmse"):
            try:
                metric_value = float(model_metrics.get(metric_key))
            except Exception:
                reasons.append(f"model.metrics.{metric_key}={model_metrics.get(metric_key)!r}")
            else:
                if not math.isfinite(metric_value):
                    reasons.append(f"model.metrics.{metric_key}={metric_value!r}")
    trace_details = details.get("traceability") if isinstance(details.get("traceability"), dict) else {}
    if not trace_details:
        reasons.append("traceability.details_missing")
    for key in ("stable_manifest_rows", "stable_unique_evaluations", "response_feature_rows", "response_unique_evaluations", "response_dataset_rows", "response_dataset_unique_evaluations", "enriched_rows", "enriched_unique_evaluations", "training_rows", "training_unique_evaluations"):
        if key not in trace_details:
            reasons.append(f"traceability.{key}=MISSING")
            continue
        try:
            value = int(trace_details[key])
        except Exception:
            reasons.append(f"traceability.{key}={trace_details[key]!r}")
        else:
            if value < expected:
                reasons.append(f"traceability.{key}={value}")
    manifest_details = details.get("uniformity_manifest") if isinstance(details.get("uniformity_manifest"), dict) else {}
    try:
        visual_artifact_count = int(manifest_details.get("visual_artifact_count"))
    except Exception:
        reasons.append(f"uniformity_manifest.visual_artifact_count={manifest_details.get('visual_artifact_count')!r}")
    else:
        if visual_artifact_count < 3:
            reasons.append(f"uniformity_manifest.visual_artifact_count={visual_artifact_count}")
    if manifest_details.get("require_plots") is not True:
        reasons.append(f"uniformity_manifest.require_plots={manifest_details.get('require_plots')!r}")
    uniformity_details = details.get("uniformity") if isinstance(details.get("uniformity"), dict) else {}
    k_diag = uniformity_details.get("k_sign_diagnostics") if isinstance(uniformity_details.get("k_sign_diagnostics"), dict) else None
    if uniformity_details.get("k_mode") != "magnitude":
        reasons.append(f"uniformity.k_mode={uniformity_details.get('k_mode')!r}")
    range_details = uniformity_details.get("ranges") if isinstance(uniformity_details.get("ranges"), dict) else {}
    if not range_details:
        reasons.append("uniformity.ranges=MISSING")
    for feature_name, (target_min, target_max) in EXPECTED_PHYSICAL_RANGES.items():
        item = range_details.get(feature_name) if isinstance(range_details.get(feature_name), dict) else {}
        if not item:
            reasons.append(f"uniformity.ranges.{feature_name}=MISSING")
            continue
        if item.get("explicit") is not True or item.get("source") != "explicit":
            reasons.append(f"uniformity.ranges.{feature_name}.explicit={item.get('explicit')!r},source={item.get('source')!r}")
        try:
            actual_min = float(item.get("min"))
            actual_max = float(item.get("max"))
        except Exception:
            reasons.append(f"uniformity.ranges.{feature_name}.bounds={item!r}")
        else:
            if not (
                math.isclose(actual_min, target_min, rel_tol=0.0, abs_tol=1e-12)
                and math.isclose(actual_max, target_max, rel_tol=0.0, abs_tol=1e-12)
            ):
                reasons.append(
                    f"uniformity.ranges.{feature_name}=({actual_min},{actual_max}),expected=({target_min},{target_max})"
                )
    four_d = uniformity_details.get("four_dimensional_uniformity") if isinstance(uniformity_details.get("four_dimensional_uniformity"), dict) else {}
    one_d = uniformity_details.get("one_dimensional_uniformity") if isinstance(uniformity_details.get("one_dimensional_uniformity"), dict) else {}
    if not one_d:
        reasons.append("uniformity.one_dimensional_uniformity=MISSING")
    for feature_name in EXPECTED_PHYSICAL_RANGES:
        item = one_d.get(feature_name) if isinstance(one_d.get(feature_name), dict) else {}
        if not item:
            reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}=MISSING")
            continue
        for metric_name, threshold_name, direction in (
            ("occupied_fraction", "min_1d_occupied_fraction", "min"),
            ("normalized_entropy", "min_1d_entropy_fraction", "min"),
            ("max_to_min_nonzero_ratio", "max_1d_bin_imbalance", "max"),
        ):
            try:
                metric_value = float(item.get(metric_name))
            except Exception:
                reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={item.get(metric_name)!r}")
                continue
            threshold_value = parsed_uniformity_thresholds.get(threshold_name)
            if threshold_value is None:
                continue
            if direction == "min" and metric_value < threshold_value:
                reasons.append(
                    f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={metric_value:.6g},required={threshold_value}"
                )
            if direction == "max" and metric_value > threshold_value:
                reasons.append(
                    f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={metric_value:.6g},limit={threshold_value}"
                )
    pairwise = uniformity_details.get("pairwise_uniformity") if isinstance(uniformity_details.get("pairwise_uniformity"), dict) else {}
    if not pairwise:
        reasons.append("uniformity.pairwise_uniformity=MISSING")
    for pair_name, item in pairwise.items():
        if not isinstance(item, dict):
            reasons.append(f"uniformity.pairwise_uniformity.{pair_name}={item!r}")
            continue
        for metric_name, threshold_name in (
            ("occupied_fraction", "min_pair_occupied_fraction"),
            ("normalized_entropy", "min_pair_entropy_fraction"),
        ):
            try:
                metric_value = float(item.get(metric_name))
            except Exception:
                reasons.append(f"uniformity.pairwise_uniformity.{pair_name}.{metric_name}={item.get(metric_name)!r}")
                continue
            threshold_value = parsed_uniformity_thresholds.get(threshold_name)
            if threshold_value is not None and metric_value < threshold_value:
                reasons.append(
                    f"uniformity.pairwise_uniformity.{pair_name}.{metric_name}={metric_value:.6g},required={threshold_value}"
                )
    if not four_d:
        reasons.append("uniformity.four_dimensional_uniformity=MISSING")
    else:
        try:
            occupied_fraction = float(four_d.get("occupied_fraction"))
        except Exception:
            reasons.append(f"uniformity.four_dimensional_uniformity.occupied_fraction={four_d.get('occupied_fraction')!r}")
        else:
            if occupied_fraction < EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION:
                reasons.append(
                    f"uniformity.four_dimensional_uniformity.occupied_fraction={occupied_fraction:.6g},required={EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION}"
                )
    if not k_diag:
        reasons.append("uniformity.k_sign_diagnostics=MISSING")
    else:
        if k_diag.get("uniformity_k_axis") != "|K|":
            reasons.append(f"uniformity.k_sign_diagnostics.uniformity_k_axis={k_diag.get('uniformity_k_axis')!r}")
        try:
            signed_k_count = int(k_diag.get("signed_k_count"))
        except Exception:
            reasons.append(f"uniformity.k_sign_diagnostics.signed_k_count={k_diag.get('signed_k_count')!r}")
        else:
            if signed_k_count < expected:
                reasons.append(f"uniformity.k_sign_diagnostics.signed_k_count={signed_k_count}")
    return ("PASS" if not reasons else "FAIL"), reasons, data


def step_path(summary: dict, step: str, default: Path) -> Path:
    details = summary.get("details") if isinstance(summary.get("details"), dict) else {}
    item = details.get(step) if isinstance(details.get(step), dict) else {}
    raw = item.get("path")
    return Path(raw) if raw else default


def exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def artifact_record(path: Path) -> dict:
    record = {"path": str(path), "exists": exists(path)}
    if record["exists"]:
        try:
            stat = path.stat()
            record["bytes"] = stat.st_size
            record["mtime_utc"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            pass
    return record


def evaluation_value(row: dict[str, str], row_index: int) -> str:
    for key in ("evaluation", "sample_id", "id", "row_index"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return f"row_{row_index}"


def training_csv_evaluations(path: Path) -> tuple[dict, list[str]]:
    record = artifact_record(path)
    record.update(
        {
            "row_count": 0,
            "unique_evaluation_count": 0,
            "duplicate_evaluation_count": 0,
            "duplicate_examples": [],
            "parse_status": "NOT_READ",
        }
    )
    if not path.exists():
        record["parse_status"] = "MISSING"
        return record, []
    values: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row_index, row in enumerate(reader):
                values.append(evaluation_value(row, row_index))
    except Exception as exc:  # noqa: BLE001
        record["parse_status"] = f"PARSE_ERROR:{type(exc).__name__}"
        return record, []
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and len(duplicates) < 10:
            duplicates.append(value)
        seen.add(value)
    record.update(
        {
            "row_count": len(values),
            "unique_evaluation_count": len(seen),
            "duplicate_evaluation_count": len(values) - len(seen),
            "duplicate_examples": duplicates,
            "parse_status": "PASS",
        }
    )
    return record, values


def collect_checkpoint_artifacts(summary_path: Path | None, summary: dict, tag: str) -> dict:
    if summary_path is None:
        root = base / "model_tests" / tag
    else:
        root = summary_path.parent
    uniformity_dir = step_path(summary, "uniformity", root / "physical_feature_uniformity")
    training_dir = step_path(summary, "training", root / "physical_feature_inverse_training_table")
    model_dir = step_path(summary, "model", root / "physical_feature_inverse_checkpoint_test")
    traceability_dir = step_path(summary, "traceability", root / "physical_checkpoint_traceability")
    artifacts = {
        "pipeline_summary": artifact_record(summary_path) if summary_path else {"path": "", "exists": False},
        "command_log": artifact_record(root / "mars56_s4p_physical_checkpoint_pipeline_commands.log"),
        "uniformity_summary": artifact_record(uniformity_dir / "physical_feature_uniformity_summary.json"),
        "uniformity_report": artifact_record(uniformity_dir / "physical_feature_uniformity_report.md"),
        "uniformity_manifest": artifact_record(uniformity_dir / "physical_feature_uniformity_manifest.json"),
        "plot_marginal_histograms": artifact_record(uniformity_dir / "physical_feature_marginal_histograms.png"),
        "plot_pair_scatter": artifact_record(uniformity_dir / "physical_feature_pair_scatter.png"),
        "plot_pair_occupancy_heatmaps": artifact_record(uniformity_dir / "physical_feature_pair_occupancy_heatmaps.png"),
        "training_manifest": artifact_record(training_dir / "physical_feature_inverse_training_manifest.json"),
        "training_csv": artifact_record(training_dir / "physical_feature_inverse_training_table.csv"),
        "model_summary": artifact_record(model_dir / "physical_feature_inverse_checkpoint_test_summary.json"),
        "traceability_summary": artifact_record(traceability_dir / "physical_checkpoint_traceability_summary.json"),
        "traceability_report": artifact_record(traceability_dir / "physical_checkpoint_traceability_report.md"),
    }
    return artifacts


def required_artifact_failures(artifacts: dict, required_artifacts: list[str]) -> tuple[list[str], list[str]]:
    missing = []
    empty = []
    for key in required_artifacts:
        artifact = artifacts.get(key) if isinstance(artifacts.get(key), dict) else {}
        if not artifact.get("exists"):
            missing.append(key)
            continue
        try:
            byte_count = int(artifact.get("bytes"))
        except Exception:
            empty.append(key)
            continue
        if byte_count <= 0:
            empty.append(key)
    return missing, empty


def checkpoint_item(dataset: Path) -> dict:
    tag = dataset.name
    ds_summary = dataset / "parallel_candidate_queue_dataset_summary.json"
    nonempty = nonempty_s4p_count(dataset)
    cp_summary = latest_checkpoint_summary(base / "model_tests" / tag)
    proof, reasons, cp_data = checkpoint_proof(cp_summary, expected_per_chunk)
    artifacts = collect_checkpoint_artifacts(cp_summary, cp_data, tag)
    required_artifacts = [
        "pipeline_summary",
        "uniformity_summary",
        "uniformity_manifest",
        "plot_marginal_histograms",
        "plot_pair_scatter",
        "plot_pair_occupancy_heatmaps",
        "training_manifest",
        "training_csv",
        "model_summary",
        "traceability_summary",
        "traceability_report",
    ]
    missing_artifacts, empty_artifacts = required_artifact_failures(artifacts, required_artifacts)
    training_csv_stats, _ = training_csv_evaluations(Path(artifacts["training_csv"]["path"]))
    if proof == "PASS" and not missing_artifacts and not empty_artifacts and nonempty >= expected_per_chunk and json_status(ds_summary) == "PASS":
        evidence_status = "PASS"
    elif nonempty >= expected_per_chunk and json_status(ds_summary) == "PASS":
        evidence_status = "NEEDS_CHECKPOINT_OR_ARTIFACT_REPAIR"
    else:
        evidence_status = "WAIT_DATASET_COMPLETE"
    return {
        "tag": tag,
        "dataset": str(dataset),
        "nonempty_s4p_count": nonempty,
        "dataset_summary": str(ds_summary),
        "dataset_summary_status": json_status(ds_summary),
        "checkpoint_summary": str(cp_summary) if cp_summary else None,
        "checkpoint_proof": proof,
        "checkpoint_proof_reasons": reasons,
        "evidence_status": evidence_status,
        "missing_required_artifacts": missing_artifacts,
        "empty_required_artifacts": empty_artifacts,
        "required_artifact_status": "PASS" if not missing_artifacts and not empty_artifacts else "FAIL",
        "artifacts": artifacts,
        "training_csv_evaluation_stats": training_csv_stats,
    }


def cumulative_item(summary_path: Path) -> dict:
    tag = summary_path.parent.parent.name if summary_path.parent.name == "physical_checkpoint" else summary_path.parent.name
    expected = None
    tag_match = re.fullmatch(r"cumulative_(\d{4})k_after_chunk08_pass", tag)
    if tag_match:
        expected = (int(tag_match.group(1)) // 100) * expected_per_chunk
    try:
        if expected is None:
            expected = int(load_json(summary_path).get("expected_count"))
    except Exception:
        expected = None
    proof, reasons, data = checkpoint_proof(summary_path, expected or 0)
    artifacts = collect_checkpoint_artifacts(summary_path, data, tag)
    required_artifacts = [
        "pipeline_summary",
        "uniformity_summary",
        "uniformity_manifest",
        "plot_marginal_histograms",
        "plot_pair_scatter",
        "plot_pair_occupancy_heatmaps",
        "training_manifest",
        "training_csv",
        "model_summary",
        "traceability_summary",
        "traceability_report",
    ]
    missing, empty = required_artifact_failures(artifacts, required_artifacts)
    return {
        "tag": tag,
        "expected_count": expected,
        "summary": str(summary_path),
        "checkpoint_proof": proof,
        "checkpoint_proof_reasons": reasons,
        "missing_required_artifacts": missing,
        "empty_required_artifacts": empty,
        "required_artifact_status": "PASS" if not missing and not empty else "FAIL",
        "evidence_status": "PASS" if proof == "PASS" and not missing and not empty else "NEEDS_CHECKPOINT_OR_ARTIFACT_REPAIR",
        "artifacts": artifacts,
    }


def production_rate_artifact() -> dict:
    json_path = base / "status" / "mars56_production_rate_eta_latest.json"
    md_path = base / "status" / "mars56_production_rate_eta_latest_CN.md"
    json_artifact = artifact_record(json_path)
    md_artifact = artifact_record(md_path)
    data = load_json(json_path) if json_path.exists() else {}
    interpreted = data.get("interpreted") if isinstance(data.get("interpreted"), dict) else {}
    contract = data.get("contract") if isinstance(data.get("contract"), dict) else {}
    return {
        "json": str(json_path),
        "json_exists": json_artifact.get("exists"),
        "json_bytes": json_artifact.get("bytes", 0),
        "md": str(md_path),
        "md_exists": md_artifact.get("exists"),
        "md_bytes": md_artifact.get("bytes", 0),
        "audit_mode": data.get("audit_mode"),
        "return_code": data.get("return_code"),
        "production_rate_target_status": interpreted.get("production_rate_target_status"),
        "production_rate_audit_status": interpreted.get("production_rate_audit_status"),
        "latest_parallel_jobs": interpreted.get("latest_parallel_jobs"),
        "measured_seconds_per_accepted_row": interpreted.get("measured_seconds_per_accepted_row"),
        "eta_days_per_100k": interpreted.get("eta_days_per_100k"),
        "eta_days_for_1m_at_same_rate": interpreted.get("eta_days_for_1m_at_same_rate"),
        "expected_parallel_jobs": contract.get("expected_parallel_jobs"),
        "target_seconds_per_accepted_row": contract.get("target_seconds_per_accepted_row"),
        "target_days_per_100k": contract.get("target_days_per_100k"),
        "artifact_boundary": data.get("artifact_boundary"),
    }


def global_training_evaluation_proof(items: list[dict]) -> dict:
    expected_total = expected_per_chunk * expected_chunks
    pass_items = [item for item in items if item.get("evidence_status") == "PASS"]
    all_values: list[str] = []
    first_seen: dict[str, str] = {}
    duplicate_examples: list[dict] = []
    chunk_records: list[dict] = []
    missing_or_bad_csv = 0
    for item in pass_items:
        tag = str(item.get("tag") or "")
        artifacts = item.get("artifacts") if isinstance(item.get("artifacts"), dict) else {}
        training_artifact = artifacts.get("training_csv") if isinstance(artifacts.get("training_csv"), dict) else {}
        csv_path = Path(str(training_artifact.get("path") or ""))
        csv_stats, values = training_csv_evaluations(csv_path)
        if csv_stats.get("parse_status") != "PASS":
            missing_or_bad_csv += 1
        chunk_records.append(
            {
                "tag": tag,
                "training_csv": str(csv_path),
                "parse_status": csv_stats.get("parse_status"),
                "row_count": csv_stats.get("row_count"),
                "unique_evaluation_count": csv_stats.get("unique_evaluation_count"),
                "duplicate_evaluation_count": csv_stats.get("duplicate_evaluation_count"),
            }
        )
        for value in values:
            all_values.append(value)
            if value in first_seen:
                if len(duplicate_examples) < 10:
                    duplicate_examples.append(
                        {
                            "evaluation": value,
                            "first_chunk": first_seen[value],
                            "duplicate_chunk": tag,
                        }
                    )
            else:
                first_seen[value] = tag
    unique_count = len(first_seen)
    duplicate_count = len(all_values) - unique_count
    if len(pass_items) < expected_chunks:
        status_value = "IN_PROGRESS"
    elif missing_or_bad_csv:
        status_value = "FAIL"
    elif len(all_values) < expected_total or unique_count < expected_total or duplicate_count != 0:
        status_value = "FAIL"
    else:
        status_value = "PASS"
    reasons: list[str] = []
    if len(pass_items) < expected_chunks:
        reasons.append(f"formal_pass_chunk_count={len(pass_items)},expected={expected_chunks}")
    if missing_or_bad_csv:
        reasons.append(f"missing_or_bad_training_csv_count={missing_or_bad_csv}")
    if len(all_values) < expected_total:
        reasons.append(f"training_row_count={len(all_values)},expected={expected_total}")
    if unique_count < expected_total:
        reasons.append(f"unique_training_evaluation_count={unique_count},expected={expected_total}")
    if duplicate_count:
        reasons.append(f"duplicate_evaluation_count={duplicate_count}")
    return {
        "status": status_value,
        "expected_total": expected_total,
        "expected_chunks": expected_chunks,
        "expected_per_chunk": expected_per_chunk,
        "formal_pass_chunk_count": len(pass_items),
        "training_row_count": len(all_values),
        "unique_training_evaluation_count": unique_count,
        "duplicate_evaluation_count": duplicate_count,
        "missing_or_bad_training_csv_count": missing_or_bad_csv,
        "duplicate_examples": duplicate_examples,
        "reasons": reasons,
        "chunks": chunk_records,
    }


expected_formal_tags = [
    f"chunk_{idx:03d}_100k_after_chunk08_pass"
    for idx in range(1, expected_chunks + 1)
]
expected_formal_tag_set = set(expected_formal_tags)
datasets_all = []
datasets_root = base / "datasets"
if datasets_root.exists():
    for dataset in sorted(datasets_root.glob("*_100k_after_chunk08_pass")):
        if dataset.name.startswith("chunk_01_n100000"):
            continue
        if dataset.is_dir():
            datasets_all.append(checkpoint_item(dataset))
datasets_by_tag = {item["tag"]: item for item in datasets_all}
datasets = [datasets_by_tag[tag] for tag in expected_formal_tags if tag in datasets_by_tag]
observed_formal_tags = sorted(datasets_by_tag)
missing_expected_formal_tags = [tag for tag in expected_formal_tags if tag not in datasets_by_tag]
unexpected_formal_tags = [tag for tag in observed_formal_tags if tag not in expected_formal_tag_set]

def item_mtime(item: dict) -> float:
    try:
        return Path(str(item.get("summary") or "")).stat().st_mtime
    except OSError:
        return -1.0


expected_cumulative_tags = [
    f"cumulative_{idx * 100:04d}k_after_chunk08_pass"
    for idx in range(1, expected_chunks + 1)
]
expected_cumulative_tag_set = set(expected_cumulative_tags)
cumulative_all = []
cum_root = base / "cumulative_model_tests"
if cum_root.exists():
    for path in sorted(cum_root.rglob("mars56_s4p_physical_checkpoint_pipeline_summary.json")):
        cumulative_all.append(cumulative_item(path))
cumulative_by_tag: dict[str, dict] = {}
cumulative_paths_by_tag: dict[str, list[str]] = {}
for item in cumulative_all:
    tag = str(item.get("tag") or "")
    cumulative_paths_by_tag.setdefault(tag, []).append(str(item.get("summary") or ""))
    if tag not in cumulative_by_tag or item_mtime(item) >= item_mtime(cumulative_by_tag[tag]):
        cumulative_by_tag[tag] = item
cumulative = [cumulative_by_tag[tag] for tag in expected_cumulative_tags if tag in cumulative_by_tag]
observed_cumulative_tags = sorted(cumulative_by_tag)
missing_expected_cumulative_tags = [tag for tag in expected_cumulative_tags if tag not in cumulative_by_tag]
unexpected_cumulative_tags = [tag for tag in observed_cumulative_tags if tag not in expected_cumulative_tag_set]
duplicate_cumulative_checkpoint_tags = sorted(
    tag for tag, paths in cumulative_paths_by_tag.items()
    if tag in expected_cumulative_tag_set and len(paths) > 1
)
duplicate_cumulative_checkpoint_summaries = {
    tag: sorted(paths)
    for tag, paths in cumulative_paths_by_tag.items()
    if tag in duplicate_cumulative_checkpoint_tags
}

pass_chunks = sum(1 for item in datasets if item["evidence_status"] == "PASS")
complete_chunks = sum(1 for item in datasets if item["nonempty_s4p_count"] >= expected_per_chunk)
pass_cumulative = sum(1 for item in cumulative if item["evidence_status"] == "PASS")
global_training_proof = global_training_evaluation_proof(datasets)
formal_tag_status = "PASS" if not missing_expected_formal_tags and not unexpected_formal_tags else "IN_PROGRESS"
cumulative_tag_status = "PASS" if (
    not missing_expected_cumulative_tags
    and not unexpected_cumulative_tags
    and not duplicate_cumulative_checkpoint_tags
) else "IN_PROGRESS"
status = "PASS" if formal_tag_status == "PASS" and cumulative_tag_status == "PASS" and pass_chunks >= expected_chunks and pass_cumulative >= expected_chunks and global_training_proof["status"] == "PASS" else "IN_PROGRESS"
rate_artifact = production_rate_artifact()
index = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "base": str(base),
    "expected_per_chunk": expected_per_chunk,
    "expected_chunks": expected_chunks,
    "expected_total": expected_per_chunk * expected_chunks,
    "expected_formal_100k_tags": expected_formal_tags,
    "observed_formal_100k_tags": observed_formal_tags,
    "missing_expected_formal_100k_tags": missing_expected_formal_tags,
    "unexpected_formal_100k_tags": unexpected_formal_tags,
    "formal_100k_tag_status": formal_tag_status,
    "expected_cumulative_checkpoint_tags": expected_cumulative_tags,
    "observed_cumulative_checkpoint_tags": observed_cumulative_tags,
    "missing_expected_cumulative_checkpoint_tags": missing_expected_cumulative_tags,
    "unexpected_cumulative_checkpoint_tags": unexpected_cumulative_tags,
    "duplicate_cumulative_checkpoint_tags": duplicate_cumulative_checkpoint_tags,
    "duplicate_cumulative_checkpoint_summaries": duplicate_cumulative_checkpoint_summaries,
    "cumulative_checkpoint_tag_status": cumulative_tag_status,
    "strict_evidence_contract": {
        "required_checkpoint_steps": sorted(REQUIRED_STEPS),
        "required_count_details": [
            "uniformity.valid_feature_count",
            "training.training_count",
            "model.usable_row_count",
            "traceability.stable_manifest_rows",
            "traceability.stable_unique_evaluations",
            "traceability.response_feature_rows",
            "traceability.response_unique_evaluations",
            "traceability.response_dataset_rows",
            "traceability.response_dataset_unique_evaluations",
            "traceability.enriched_rows",
            "traceability.enriched_unique_evaluations",
            "traceability.training_rows",
            "traceability.training_unique_evaluations",
        ],
        "required_k_contract": {
            "k_mode": "magnitude",
            "uniformity_k_axis": "|K|",
            "signed_k_count_min": expected_per_chunk,
        },
        "required_four_dimensional_uniformity": {
            "require_four_d_gate": True,
            "min_four_d_occupied_fraction": EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION,
        },
        "required_physical_ranges": {
            feature: {"min": bounds[0], "max": bounds[1], "source": "explicit"}
            for feature, bounds in EXPECTED_PHYSICAL_RANGES.items()
        },
        "required_artifacts": [
            "pipeline_summary",
            "uniformity_summary",
            "uniformity_manifest",
            "plot_marginal_histograms",
            "plot_pair_scatter",
            "plot_pair_occupancy_heatmaps",
            "training_manifest",
            "training_csv",
            "model_summary",
            "traceability_summary",
            "traceability_report",
        ],
        "required_global_training_evaluation_proof": {
            "status": "PASS",
            "unique_training_evaluation_count_min": expected_per_chunk * expected_chunks,
            "duplicate_evaluation_count": 0,
        },
        "required_formal_100k_tags": expected_formal_tags,
        "required_cumulative_checkpoint_tags": expected_cumulative_tags,
    },
    "overall_status": status,
    "formal_100k_dataset_count": len(datasets),
    "all_formal_100k_dataset_count": len(datasets_all),
    "formal_100k_complete_count": complete_chunks,
    "formal_100k_evidence_pass_count": pass_chunks,
    "cumulative_evidence_count": len(cumulative),
    "all_cumulative_evidence_count": len(cumulative_all),
    "cumulative_evidence_pass_count": pass_cumulative,
    "production_rate_artifact": rate_artifact,
    "global_training_evaluation_proof": global_training_proof,
    "formal_100k": datasets,
    "cumulative": cumulative,
}

json_out.parent.mkdir(parents=True, exist_ok=True)
json_out.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

lines = [
    "# MARS56 100k Checkpoint Evidence Index",
    "",
    f"Generated UTC: {index['generated_utc']}",
    f"Base: `{base}`",
    "",
    "## Summary",
    "",
    f"- Overall status: `{status}`",
    f"- Formal 100k datasets found: `{len(datasets)}`",
    f"- All formal-like datasets found: `{len(datasets_all)}`",
    f"- Expected formal tag status: `{formal_tag_status}`",
    f"- Missing expected formal tags: `{', '.join(missing_expected_formal_tags) if missing_expected_formal_tags else 'none'}`",
    f"- Unexpected formal-like tags: `{', '.join(unexpected_formal_tags) if unexpected_formal_tags else 'none'}`",
    f"- Complete formal 100k datasets: `{complete_chunks}`",
    f"- Formal 100k evidence PASS: `{pass_chunks}` / `{expected_chunks}`",
    f"- Expected cumulative checkpoint tag status: `{cumulative_tag_status}`",
    f"- Missing expected cumulative checkpoint tags: `{', '.join(missing_expected_cumulative_tags) if missing_expected_cumulative_tags else 'none'}`",
    f"- Unexpected cumulative-like tags: `{', '.join(unexpected_cumulative_tags) if unexpected_cumulative_tags else 'none'}`",
    f"- Duplicate cumulative checkpoint tags: `{', '.join(duplicate_cumulative_checkpoint_tags) if duplicate_cumulative_checkpoint_tags else 'none'}`",
    f"- Cumulative evidence PASS: `{pass_cumulative}` / `{expected_chunks}`",
    f"- Global training evaluation proof: `{global_training_proof['status']}`",
    f"- Global unique training evaluations: `{global_training_proof['unique_training_evaluation_count']}` / `{global_training_proof['expected_total']}`",
    f"- Global duplicate evaluations: `{global_training_proof['duplicate_evaluation_count']}`",
    f"- 4D Lp/Ls/Q/|K| occupied-bin gate: `required`, min fraction `{EXPECTED_MIN_FOUR_D_OCCUPIED_FRACTION}`",
    f"- Production rate artifact: `{rate_artifact['production_rate_audit_status']}` ({'json found' if rate_artifact['json_exists'] else 'json missing'})",
    "",
    "## Production Rate / ETA Artifact",
    "",
    f"- JSON exists: `{rate_artifact['json_exists']}`",
    f"- JSON bytes: `{rate_artifact['json_bytes']}`",
    f"- Markdown exists: `{rate_artifact['md_exists']}`",
    f"- Markdown bytes: `{rate_artifact['md_bytes']}`",
    f"- Audit mode: `{rate_artifact['audit_mode']}`",
    f"- Return code: `{rate_artifact['return_code']}`",
    f"- Target status: `{rate_artifact['production_rate_target_status']}`",
    f"- Gate status: `{rate_artifact['production_rate_audit_status']}`",
    f"- Latest parallel jobs: `{rate_artifact['latest_parallel_jobs']}`",
    f"- Seconds/accepted row: `{rate_artifact['measured_seconds_per_accepted_row']}`",
    f"- ETA days/100k: `{rate_artifact['eta_days_per_100k']}`",
    f"- JSON: `{rate_artifact['json']}`",
    "",
    "## Formal 100k Chunks",
    "",
]
if not datasets:
    lines.append("- No formal `_100k_after_chunk08_pass` datasets found yet.")
else:
    for item in datasets:
        lines.extend(
            [
                f"### {item['tag']}",
                "",
                f"- Evidence status: `{item['evidence_status']}`",
                f"- Non-empty `.s4p`: `{item['nonempty_s4p_count']}`",
                f"- Dataset summary status: `{item['dataset_summary_status']}`",
                f"- Checkpoint proof: `{item['checkpoint_proof']}`",
                f"- Required artifact status: `{item['required_artifact_status']}`",
                f"- Missing required artifacts: `{', '.join(item['missing_required_artifacts']) if item['missing_required_artifacts'] else 'none'}`",
                f"- Empty required artifacts: `{', '.join(item['empty_required_artifacts']) if item['empty_required_artifacts'] else 'none'}`",
                f"- Dataset: `{item['dataset']}`",
                f"- Checkpoint summary: `{item['checkpoint_summary']}`",
                "",
            ]
        )
lines.extend(["## Cumulative Checkpoints", ""])
if not cumulative:
    lines.append("- No cumulative checkpoint summaries found yet.")
else:
    for item in cumulative:
        lines.extend(
            [
                f"### {item['tag']}",
                "",
                f"- Evidence status: `{item['evidence_status']}`",
                f"- Expected count: `{item['expected_count']}`",
                f"- Checkpoint proof: `{item['checkpoint_proof']}`",
                f"- Required artifact status: `{item['required_artifact_status']}`",
                f"- Missing required artifacts: `{', '.join(item['missing_required_artifacts']) if item['missing_required_artifacts'] else 'none'}`",
                f"- Empty required artifacts: `{', '.join(item['empty_required_artifacts']) if item['empty_required_artifacts'] else 'none'}`",
                f"- Summary: `{item['summary']}`",
                "",
            ]
        )
md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"EVIDENCE_INDEX_STATUS={status}")
print(f"formal_100k_tag_status={formal_tag_status}")
print("missing_expected_formal_100k_tags=" + (",".join(missing_expected_formal_tags) if missing_expected_formal_tags else "none"))
print("unexpected_formal_100k_tags=" + (",".join(unexpected_formal_tags) if unexpected_formal_tags else "none"))
print(f"cumulative_checkpoint_tag_status={cumulative_tag_status}")
print("missing_expected_cumulative_checkpoint_tags=" + (",".join(missing_expected_cumulative_tags) if missing_expected_cumulative_tags else "none"))
print("unexpected_cumulative_checkpoint_tags=" + (",".join(unexpected_cumulative_tags) if unexpected_cumulative_tags else "none"))
print("duplicate_cumulative_checkpoint_tags=" + (",".join(duplicate_cumulative_checkpoint_tags) if duplicate_cumulative_checkpoint_tags else "none"))
print(f"formal_100k_dataset_count={len(datasets)}")
print(f"all_formal_100k_dataset_count={len(datasets_all)}")
print(f"formal_100k_complete_count={complete_chunks}")
print(f"formal_100k_evidence_pass_count={pass_chunks}")
print(f"cumulative_evidence_count={len(cumulative)}")
print(f"all_cumulative_evidence_count={len(cumulative_all)}")
print(f"cumulative_evidence_pass_count={pass_cumulative}")
print(f"global_training_evaluation_status={global_training_proof['status']}")
print(f"global_training_evaluation_unique_count={global_training_proof['unique_training_evaluation_count']}")
print(f"global_training_evaluation_duplicate_count={global_training_proof['duplicate_evaluation_count']}")
print(f"production_rate_artifact_status={rate_artifact['production_rate_audit_status']}")
print(f"production_rate_artifact_json_exists={rate_artifact['json_exists']}")
print(f"production_rate_artifact_json_bytes={rate_artifact['json_bytes']}")
print(f"production_rate_artifact_md_bytes={rate_artifact['md_bytes']}")
print(f"evidence_index_json={json_out}")
print(f"evidence_index_md={md_out}")
PY
REMOTE

if [ "$LOCAL_DRY_RUN" = "1" ]; then
  echo "EVIDENCE_INDEX_LOCAL_DRY_RUN=1"
  echo "expected_per_chunk=$EXPECTED_PER_CHUNK"
  echo "expected_chunks=$EXPECTED_CHUNKS"
  echo "remote_audit_contains=EVIDENCE_INDEX_STATUS"
  echo "remote_audit_contains=physical_feature_uniformity_manifest.json"
  echo "remote_audit_contains=physical_feature_marginal_histograms.png"
  echo "remote_audit_contains=physical_feature_pair_scatter.png"
  echo "remote_audit_contains=physical_feature_pair_occupancy_heatmaps.png"
  echo "remote_audit_contains=physical_checkpoint_traceability_summary.json"
  echo "remote_audit_contains=physical_checkpoint_traceability_report.md"
  echo "remote_audit_contains=traceability_summary"
  echo "remote_audit_contains=traceability_report"
  echo "remote_audit_contains=physical_feature_inverse_training_table.csv"
  echo "remote_audit_contains=training_csv"
  echo "remote_audit_contains=physical_feature_inverse_checkpoint_test_summary.json"
  echo "remote_audit_contains=strict_evidence_contract"
  echo "remote_audit_contains=required_count_details"
  echo "remote_audit_contains=required_k_contract"
  echo "remote_audit_contains=uniformity.valid_feature_count"
  echo "remote_audit_contains=training.training_count"
  echo "remote_audit_contains=model.usable_row_count"
  echo "remote_audit_contains=traceability.training_rows"
  echo "remote_audit_contains=uniformity.k_sign_diagnostics.uniformity_k_axis"
  echo "remote_audit_contains=production_rate_artifact"
  echo "remote_audit_contains=mars56_production_rate_eta_latest.json"
  echo "remote_audit_contains=production_rate_artifact_status"
  exit 0
fi

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
echo "Then this will build a remote checkpoint evidence index without rerunning EMX/model tests."
ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" \
  "ssh -tt ${MARS_HOST} 'EXPECTED_PER_CHUNK='\\''${EXPECTED_PER_CHUNK}'\\'' EXPECTED_CHUNKS='\\''${EXPECTED_CHUNKS}'\\'' bash -s'" \
  <<<"$REMOTE_RUN"
