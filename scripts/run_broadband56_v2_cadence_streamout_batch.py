#!/usr/bin/env python3
"""Run bounded candidate-bound Cadence streamout and preserve every outcome.

The hash-bound delegate remains the only Cadence implementation.  This role
wrapper fixes the production arguments, runs one-candidate shards, and turns a
mixed delegate result into a complete PASS/FAIL terminal partition.  Only
byte-identical hard links from successful shard directories are exposed to the
downstream GDS-index role; no GDS bytes are edited or synthesized here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_cadence_streamout_batch.v2"
RECEIPT_NAME = "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json"
PASS_QUEUE_NAME = "CADENCE_PASS_CANDIDATE_QUEUE.csv"
FAILURE_INDEX_NAME = "CADENCE_STREAMOUT_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "CADENCE_STREAMOUT_DELEGATE_EVIDENCE_INDEX.csv"
PASSED_DATASET_DIRNAME = "cadence_pass_dataset"
DATASET_ROWS_NAME = "dataset_rows.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "campaign_id",
    "campaign_contract_fingerprint",
    "campaign_phase",
    "acquisition_source",
    "geometry_sha256",
    "analytical_status",
    "topology_status",
)

FAILURE_FIELDS = (
    "submitted_sequence",
    "candidate_id_sha256",
    "geometry_sha256",
    "terminal_stage",
    "error",
    "delegate_shard_summary_path",
    "delegate_shard_summary_sha256",
)

EVIDENCE_FIELDS = (
    "submitted_sequence",
    "candidate_id_sha256",
    "geometry_sha256",
    "overall_status",
    "delegate_shard_index",
    "delegate_shard_summary_path",
    "delegate_shard_summary_sha256",
    "source_evaluation_dir",
    "source_gds_path",
    "source_gds_sha256",
    "source_foundry_layout_audit_path",
    "source_foundry_layout_audit_sha256",
)


class CadenceBatchError(RuntimeError):
    """Raised when Cadence outcomes cannot be partitioned without ambiguity."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(
            f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = run_batch(args, out_dir=out_dir)
    except (CadenceBatchError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"cadence_pass_count={receipt['cadence_pass_count']}")
    print(f"cadence_fail_count={receipt['cadence_fail_count']}")
    print(f"receipt={out_dir / RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--input-role-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--max-concurrency", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def run_batch(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    stage = str(args.stage).strip().upper()
    if stage not in {item.name for item in STAGES}:
        raise CadenceBatchError(f"unknown campaign stage: {stage}")
    max_concurrency = int(args.max_concurrency)
    if max_concurrency < 1:
        raise CadenceBatchError("max_concurrency must be positive")

    manifest_path = _regular_file(
        Path(args.backend_identity_manifest), "backend identity manifest"
    )
    manifest_sha = _sha256(manifest_path)
    manifest = _read_json(manifest_path, "backend identity manifest")
    _validate_manifest(manifest)
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    runtimes = _mapping(manifest.get("runtime_identities"), "runtime identities")
    self_path = _identity_path(
        scripts.get("cadence_streamout_runner"), "Cadence batch role"
    )
    if self_path != Path(__file__).resolve():
        raise CadenceBatchError("Cadence batch self-identity mismatch")
    delegate_path = _identity_path(
        scripts.get("cadence_streamout_delegate"), "Cadence streamout delegate"
    )
    python_path = _identity_path(runtimes.get("python_executable"), "Python runtime")
    config_path = _identity_path(
        runtimes.get("private_configuration"), "private configuration"
    )
    requested_config = _regular_file(Path(args.config), "private configuration")
    if requested_config != config_path:
        raise CadenceBatchError("private configuration path mismatches manifest")
    if not os.access(python_path, os.X_OK):
        raise CadenceBatchError("Python runtime is not executable")

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(authorization, manifest_sha=manifest_sha)
    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "acquisition role receipt"
    )
    input_receipt = _read_json(input_receipt_path, "acquisition role receipt")
    candidate_record = _candidate_queue_record(input_receipt)
    candidate_path = _identity_path(candidate_record, "candidate queue")
    candidate_rows, candidate_fields = _read_csv(candidate_path)
    _validate_candidates(candidate_rows, candidate_fields)

    pinned = {
        "manifest": (manifest_path, manifest_sha),
        "delegate": (delegate_path, _sha256(delegate_path)),
        "python": (python_path, _sha256(python_path)),
        "config": (config_path, _sha256(config_path)),
        "authorization": (authorization_path, _sha256(authorization_path)),
        "input_receipt": (input_receipt_path, _sha256(input_receipt_path)),
        "candidate_queue": (candidate_path, _sha256(candidate_path)),
    }

    out_dir.mkdir(parents=True, mode=0o700)
    delegate_dir = out_dir / "delegate_run"
    command = [
        str(python_path),
        str(delegate_path),
        "--candidate-csv",
        str(candidate_path),
        "--out-dir",
        str(delegate_dir),
        "--config",
        str(config_path),
        "--expected-config-sha256",
        pinned["config"][1],
        "--jobs",
        str(max_concurrency),
        "--chunk-size",
        "1",
        "--expected-count",
        str(len(candidate_rows)),
        "--expected-jobs",
        str(max_concurrency),
        "--batch-size",
        "1",
        "--cadence-streamout-only",
        "--force-port-mode",
        "single_ended_shield_grounded",
        "--force-cadence-pin-purpose",
        "51",
        "--force-wideband-5-60-1p0",
        "--expected-port-mode",
        "single_ended_shield_grounded",
        "--expected-pin-purpose",
        "51",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "60",
        "--expected-frequency-step-ghz",
        "1",
        "--expected-frequency-points",
        "56",
        "--expected-touchstone-extension",
        ".s4p",
        "--expected-ports",
        "4",
        "--no-fail-exit",
    ]
    command_sha = hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    started_utc = _utc_now()
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    finished_utc = _utc_now()
    stdout_path = out_dir / "delegate_stdout.log"
    stderr_path = out_dir / "delegate_stderr.log"
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise CadenceBatchError(
            f"Cadence delegate exited with return code {result.returncode}"
        )
    delegate_summary_path = _regular_file(
        delegate_dir / "parallel_candidate_queue_dataset_summary.json",
        "Cadence delegate summary",
    )
    delegate_summary = _read_json(delegate_summary_path, "Cadence delegate summary")
    shard_records = delegate_summary.get("shards")
    if not isinstance(shard_records, list) or len(shard_records) != len(candidate_rows):
        raise CadenceBatchError("Cadence delegate shard partition is incomplete")
    by_index: dict[int, Mapping[str, Any]] = {}
    for record in shard_records:
        if not isinstance(record, Mapping):
            raise CadenceBatchError("Cadence delegate shard record is not an object")
        index = _integer(record.get("index"), "delegate shard index")
        if index in by_index:
            raise CadenceBatchError("Cadence delegate shard indexes are duplicated")
        by_index[index] = record
    if set(by_index) != set(range(len(candidate_rows))):
        raise CadenceBatchError("Cadence delegate shard indexes are not contiguous")

    passed_dataset = out_dir / PASSED_DATASET_DIRNAME
    evaluations_dir = passed_dataset / "evaluations"
    evaluations_dir.mkdir(parents=True)
    pass_candidates: list[dict[str, str]] = []
    pass_dataset_rows: list[dict[str, str]] = []
    failures: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for sequence, candidate in enumerate(candidate_rows, start=1):
        terminal = _classify_shard(
            record=by_index[sequence - 1],
            candidate=candidate,
            submitted_sequence=sequence,
            aggregate_evaluations_dir=evaluations_dir,
        )
        evidence.append({field: terminal.get(field, "") for field in EVIDENCE_FIELDS})
        if terminal["overall_status"] == "PASS":
            pass_candidates.append(candidate)
            pass_dataset_rows.append(terminal["dataset_row"])
        else:
            failures.append({field: terminal.get(field, "") for field in FAILURE_FIELDS})

    pass_queue_path = out_dir / PASS_QUEUE_NAME
    dataset_rows_path = passed_dataset / DATASET_ROWS_NAME
    failure_path = out_dir / FAILURE_INDEX_NAME
    evidence_path = out_dir / EVIDENCE_INDEX_NAME
    _write_csv(pass_queue_path, candidate_fields, pass_candidates)
    dataset_fields = (
        list(pass_dataset_rows[0])
        if pass_dataset_rows
        else ["queue__candidate_id_sha256", "ok", "evaluation"]
    )
    _write_csv(dataset_rows_path, dataset_fields, pass_dataset_rows)
    _write_csv(failure_path, list(FAILURE_FIELDS), failures)
    _write_csv(evidence_path, list(EVIDENCE_FIELDS), evidence)

    for label, (path, digest) in pinned.items():
        _require_unchanged(path, digest, label)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "TERMINAL_PARTITION_CANDIDATE_BOUND_CADENCE_STREAMOUT",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "submitted_count": len(candidate_rows),
        "terminal_count": len(evidence),
        "cadence_pass_count": len(pass_candidates),
        "cadence_fail_count": len(failures),
        "candidate_failures_counted_as_accepted": False,
        "backend_identity_manifest": _file_record(manifest_path),
        "input_role_receipt": _file_record(input_receipt_path),
        "private_configuration": _file_record(config_path),
        "full_campaign_authorization_receipt": _file_record(
            authorization_path
        ),
        "input_candidate_queue": _file_record(candidate_path),
        "pass_candidate_queue": _file_record(pass_queue_path),
        "pass_dataset_dir": str(passed_dataset),
        "pass_dataset_rows": _file_record(dataset_rows_path),
        "failure_index": _file_record(failure_path),
        "delegate_evidence_index": _file_record(evidence_path),
        "delegate_summary": _file_record(delegate_summary_path),
        "delegate_command_argv_sha256": command_sha,
        "delegate_started_utc": started_utc,
        "delegate_finished_utc": finished_utc,
        "delegate_return_code": int(result.returncode),
        "delegate_stdout": _file_record(stdout_path),
        "delegate_stderr": _file_record(stderr_path),
        "gds_bytes_modified": False,
        "aggregate_dataset_uses_byte_identical_hard_links": True,
        "simulator_action_taken": True,
    }
    receipt_path = out_dir / RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(out_dir)
    return receipt


def _classify_shard(
    *,
    record: Mapping[str, Any],
    candidate: Mapping[str, str],
    submitted_sequence: int,
    aggregate_evaluations_dir: Path,
) -> dict[str, Any]:
    candidate_sha = _sha_value(candidate.get("candidate_id_sha256"), "candidate")
    geometry_sha = _sha_value(candidate.get("geometry_sha256"), "geometry")
    summary_path = Path(str(record.get("summary_path") or "")).expanduser().resolve()
    summary_sha = _sha256(summary_path) if summary_path.is_file() else ""
    base = {
        "submitted_sequence": submitted_sequence,
        "candidate_id_sha256": candidate_sha,
        "geometry_sha256": geometry_sha,
        "delegate_shard_index": record.get("index", ""),
        "delegate_shard_summary_path": str(summary_path) if summary_path else "",
        "delegate_shard_summary_sha256": summary_sha,
        "source_evaluation_dir": "",
        "source_gds_path": "",
        "source_gds_sha256": "",
        "source_foundry_layout_audit_path": "",
        "source_foundry_layout_audit_sha256": "",
    }
    try:
        if not summary_path.is_file():
            raise CadenceBatchError("delegate shard summary is missing")
        summary = _read_json(summary_path, "Cadence shard summary")
        contract = summary.get("cadence_streamout_output_contract")
        if not (
            int(record.get("returncode") or 0) == 0
            and record.get("overall_status") == "PASS"
            and summary.get("overall_status") == "PASS"
            and summary.get("run_emx") is False
            and summary.get("create_only") is False
            and summary.get("cadence_streamout_only") is True
            and isinstance(contract, Mapping)
            and contract.get("checked") is True
            and int(contract.get("valid_candidate_bound_gds_count") or 0) == 1
        ):
            raise CadenceBatchError("delegate shard did not pass Cadence-only contract")
        dataset_path = _regular_file(
            Path(str(record.get("dataset_rows_csv") or "")),
            "Cadence shard dataset rows",
        )
        rows, _ = _read_csv(dataset_path)
        if len(rows) != 1:
            raise CadenceBatchError("Cadence shard dataset row count is not one")
        dataset_row = rows[0]
        if not _truthy(dataset_row.get("ok")):
            raise CadenceBatchError("Cadence shard dataset row is not successful")
        observed_id = str(
            dataset_row.get("queue__candidate_id_sha256")
            or dataset_row.get("candidate_id_sha256")
            or ""
        ).strip().lower()
        if observed_id != candidate_sha:
            raise CadenceBatchError("Cadence shard candidate identity mismatch")
        shard_out = Path(str(record.get("out_dir") or "")).expanduser().resolve()
        gds_paths = sorted(
            shard_out.glob("evaluations/*/streamout/transformer_layout_cadpins.gds")
        )
        if len(gds_paths) != 1:
            raise CadenceBatchError(
                f"Cadence shard has {len(gds_paths)} candidate-bound GDS files"
            )
        gds_path = _regular_file(gds_paths[0], "candidate-bound Cadence GDS")
        evaluation_dir = gds_path.parents[1]
        foundry_audit_path = _regular_file(
            evaluation_dir / "layout" / "foundry_layout_audit.json",
            "foundry-layout audit",
        )
        foundry_audit = _read_json(foundry_audit_path, "foundry-layout audit")
        _validate_foundry_layout_audit(foundry_audit)
        foundry_audit_sha = _sha256(foundry_audit_path)
        destination = aggregate_evaluations_dir / evaluation_dir.name
        if destination.exists():
            raise CadenceBatchError("aggregate evaluation key collision")
        shutil.copytree(evaluation_dir, destination, copy_function=os.link)
        copied_gds = destination / "streamout" / gds_path.name
        if _sha256(copied_gds) != _sha256(gds_path):
            raise CadenceBatchError("hard-linked aggregate GDS hash mismatch")
        copied_foundry_audit = destination / "layout" / foundry_audit_path.name
        if _sha256(copied_foundry_audit) != foundry_audit_sha:
            raise CadenceBatchError("hard-linked foundry-layout audit hash mismatch")
        return {
            **base,
            "overall_status": "PASS",
            "terminal_stage": "cadence_streamout",
            "error": "",
            "source_evaluation_dir": str(evaluation_dir),
            "source_gds_path": str(gds_path),
            "source_gds_sha256": _sha256(gds_path),
            "source_foundry_layout_audit_path": str(foundry_audit_path),
            "source_foundry_layout_audit_sha256": foundry_audit_sha,
            "dataset_row": dataset_row,
        }
    except (CadenceBatchError, OSError, json.JSONDecodeError) as exc:
        return {
            **base,
            "overall_status": "FAIL",
            "terminal_stage": "cadence_streamout",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _validate_foundry_layout_audit(audit: Mapping[str, Any]) -> None:
    grid = _mapping(audit.get("grid_canonicalization"), "foundry grid audit")
    frame = _mapping(audit.get("ground_frame"), "foundry ground-frame audit")
    bridges = _mapping(
        audit.get("power_line_bridge_connections"),
        "foundry bridge audit",
    )
    primary = _mapping(bridges.get("primary_bridge"), "primary bridge audit")
    secondary = _mapping(bridges.get("secondary_bridge"), "secondary bridge audit")
    exact = (
        audit.get("schema") == "rfic_transformer_foundry_layout_audit.v1"
        and audit.get("enabled") is True
        and audit.get("overall_status") == "PASS"
        and _close_float(audit.get("manufacturing_grid_um"), 0.005)
        and grid.get("schema")
        == "rfic_transformer_foundry_grid_canonicalization.v1"
        and grid.get("overall_status") == "PASS"
        and _close_float(grid.get("grid_um"), 0.005)
        and frame.get("schema")
        == "rfic_transformer_foundry_slotted_ground_frame.v1"
        and _close_float(frame.get("manufacturing_grid_um"), 0.005)
        and _close_float(frame.get("strap_width_um"), 10.0)
        and _close_float(frame.get("strap_pitch_um"), 20.0)
        and int(frame.get("polygon_count") or 0) > 0
        and bridges.get("schema")
        == "rfic_transformer_foundry_bridge_connections.v1"
        and bridges.get("overall_status") == "PASS"
        and primary.get("overall_status") == "PASS"
        and secondary.get("overall_status") == "PASS"
        and primary.get("same_connected_component_after_grid_snap") is True
        and secondary.get("same_connected_component_after_grid_snap") is True
    )
    if not exact:
        raise CadenceBatchError(
            "foundry-layout audit does not satisfy the frozen production contract"
        )


def _candidate_queue_record(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    if receipt.get("overall_status") != "PASS":
        raise CadenceBatchError("acquisition role receipt is not PASS")
    campaign = receipt.get("campaign_id")
    if campaign is not None and campaign != CAMPAIGN_ID:
        raise CadenceBatchError("acquisition receipt campaign mismatch")
    fingerprint = receipt.get("contract_fingerprint_sha256")
    if fingerprint is None:
        fingerprint = receipt.get("campaign_contract_fingerprint")
    if fingerprint is not None and fingerprint != SCIENTIFIC_CONTRACT_FINGERPRINT:
        raise CadenceBatchError("acquisition receipt contract mismatch")
    direct = receipt.get("candidate_queue")
    if isinstance(direct, Mapping):
        return direct
    outputs = receipt.get("outputs")
    if isinstance(outputs, Mapping) and isinstance(outputs.get("candidate_queue"), Mapping):
        return outputs["candidate_queue"]
    raise CadenceBatchError("acquisition receipt lacks candidate_queue evidence")


def _validate_candidates(
    rows: list[dict[str, str]], fields: list[str]
) -> None:
    missing = sorted(set(REQUIRED_CANDIDATE_FIELDS) - set(fields))
    if missing:
        raise CadenceBatchError(f"candidate queue lacks columns: {missing}")
    if not rows:
        raise CadenceBatchError("candidate queue is empty")
    candidate_ids: set[str] = set()
    geometry_ids: set[str] = set()
    for index, row in enumerate(rows, start=2):
        candidate = _sha_value(row.get("candidate_id_sha256"), f"line {index} candidate")
        geometry = _sha_value(row.get("geometry_sha256"), f"line {index} geometry")
        if candidate != _sha_value(
            row.get("candidate_geometry_identity_sha256"),
            f"line {index} candidate geometry",
        ):
            raise CadenceBatchError(f"line {index} candidate/geometry identity mismatch")
        if row.get("campaign_id") != CAMPAIGN_ID:
            raise CadenceBatchError(f"line {index} campaign mismatch")
        if row.get("campaign_contract_fingerprint") != SCIENTIFIC_CONTRACT_FINGERPRINT:
            raise CadenceBatchError(f"line {index} contract mismatch")
        if row.get("analytical_status") != "PASS" or row.get("topology_status") != "PASS":
            raise CadenceBatchError(f"line {index} is not analytically/topologically PASS")
        if candidate in candidate_ids or geometry in geometry_ids:
            raise CadenceBatchError("candidate or geometry identities are duplicated")
        candidate_ids.add(candidate)
        geometry_ids.add(geometry)


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not (
        manifest.get("campaign_id") == CAMPAIGN_ID
        and manifest.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise CadenceBatchError("backend manifest campaign or contract mismatch")


def _validate_authorization(
    receipt: Mapping[str, Any], *, manifest_sha: str
) -> None:
    if not (
        receipt.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == FULL_CAMPAIGN_PASS_DECISION
        and receipt.get("authorization_scope") == FULL_CAMPAIGN_APPROVAL_SCOPE
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and receipt.get("backend_identity_manifest", {}).get("sha256")
        == manifest_sha
        and receipt.get("cadence_authorized_within_current_stage") is True
    ):
        raise CadenceBatchError("FULL_CAMPAIGN Cadence authorization mismatch")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CadenceBatchError(f"{label} is not an object")
    return value


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if record.get("size_bytes") != path.stat().st_size or record.get("sha256") != _sha256(path):
        raise CadenceBatchError(f"{label} identity mismatch")
    return path


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise CadenceBatchError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CadenceBatchError(f"{label} is not a JSON object")
    return value


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _write_csv(
    path: Path, fields: list[str], rows: list[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_sums(root: Path) -> None:
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != SHA256SUMS_NAME
    )
    (root / SHA256SUMS_NAME).write_text(
        "\n".join(f"{_sha256(path)}  {path.relative_to(root)}" for path in files)
        + "\n",
        encoding="utf-8",
    )


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha_value(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise CadenceBatchError(f"{label} is not SHA-256")
    return digest


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CadenceBatchError(f"{label} is not an integer") from exc


def _close_float(value: Any, expected: float, tolerance: float = 1e-12) -> bool:
    try:
        return abs(float(value) - expected) <= tolerance
    except (TypeError, ValueError):
        return False


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass"}


def _require_unchanged(path: Path, expected: str, label: str) -> None:
    if _sha256(path) != expected:
        raise CadenceBatchError(f"{label} changed during Cadence batch")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
