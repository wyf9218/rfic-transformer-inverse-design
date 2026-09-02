#!/usr/bin/env python3
"""Materialize the approved rescue-Golden anchor, then delegate later queues.

This wrapper is deliberately narrow.  For ``GOLDEN`` it verifies and copies
the exact geometry-only safe-anchor queue into a fresh role directory.  It
does not copy a GDS, S-parameter file, or label.  For every later stage it
hash-verifies and invokes the existing deterministic queue builder.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
CONTRACT_FINGERPRINT = (
    "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e"
)
CORRECTED_APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1"
)
CORRECTED_APPROVAL_SCOPE = (
    "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_"
    "THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
)
CORRECTED_APPROVAL_DECISION = "APPROVE_" + CORRECTED_APPROVAL_SCOPE
SAFE_ANCHOR_SOURCE_SCHEMA = "rfic_transformer.broadband56_v2_safe_anchor_source.v1"
OUTPUT_SCHEMA = "rfic_transformer.broadband56_v2_bound_safe_anchor_queue.v1"
OUTPUT_NAME = "broadband56_candidate_queue_summary.json"
QUEUE_NAME = "broadband56_candidate_queue.csv"
GEOMETRY_FIELDS = (
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


class BoundQueueError(RuntimeError):
    """Fail-closed error for the bound queue wrapper."""


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        private, delegated_argv = _parse_private_args(raw_argv)
        stage = _option(delegated_argv, "--stage").upper()
        if stage == "GOLDEN":
            receipt = _materialize_golden(private, delegated_argv)
            print("overall_status=PASS")
            print("decision=USE_EXACT_BOUND_SAFE_ANCHOR_FOR_RESCUE_GOLDEN")
            print(f"receipt={receipt}")
            return 0
        return _run_delegate(private, delegated_argv)
    except (BoundQueueError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2


def _parse_private_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--delegate-script", required=True)
    parser.add_argument("--delegate-sha256", required=True)
    parser.add_argument("--safe-anchor-source-receipt", required=True)
    parser.add_argument("--safe-anchor-source-sha256", required=True)
    parser.add_argument("--safe-anchor-queue", required=True)
    parser.add_argument("--safe-anchor-queue-sha256", required=True)
    parser.add_argument("--corrected-approval-receipt", required=True)
    parser.add_argument("--corrected-approval-sha256", required=True)
    parser.add_argument("--corrected-config-sha256", required=True)
    parser.add_argument("--expected-safe-anchor-geometry-sha256", required=True)
    parser.add_argument("--expected-safe-anchor-id", required=True)
    private, remaining = parser.parse_known_args(list(argv))
    for name, value in vars(private).items():
        if name.endswith("sha256") and not _is_sha256(value):
            raise BoundQueueError(f"{name} is not a lowercase SHA-256 digest")
    return private, remaining


def _materialize_golden(private: argparse.Namespace, argv: list[str]) -> Path:
    out_dir = _regular_output_path(_option(argv, "--out-dir"))
    if out_dir.exists():
        raise BoundQueueError(f"no-clobber output exists: {out_dir}")
    if _option(argv, "--count") != "1":
        raise BoundQueueError("rescue GOLDEN requires count=1")
    if _option(argv, "--current-accepted") != "0":
        raise BoundQueueError("rescue GOLDEN requires current_accepted=0")
    if _option(argv, "--phase") != "PHASE_A":
        raise BoundQueueError("rescue GOLDEN must preserve PHASE_A geometry contract")

    config_path = _regular_file(_option(argv, "--config"), "corrected config")
    _require_sha(config_path, private.corrected_config_sha256, "corrected config")
    approval_path = _regular_file(
        private.corrected_approval_receipt, "corrected approval receipt"
    )
    _require_sha(
        approval_path, private.corrected_approval_sha256, "corrected approval receipt"
    )
    approval = _read_json(approval_path, "corrected approval receipt")
    verified_files = approval.get("verified_bound_files")
    if not (
        approval.get("schema") == CORRECTED_APPROVAL_SCHEMA
        and approval.get("overall_status") == "PASS"
        and approval.get("decision") == CORRECTED_APPROVAL_DECISION
        and approval.get("authorization_scope") == CORRECTED_APPROVAL_SCOPE
        and approval.get("restore_corrected_foundry_layout_contract_authorized") is True
        and approval.get("one_corrected_rescue_golden_authorized") is True
        and approval.get("nn_training_authorized") is False
        and isinstance(verified_files, Mapping)
    ):
        raise BoundQueueError("corrected foundry-layout approval is not exact PASS")
    _require_record_identity(
        config_path,
        verified_files.get("corrected_private_configuration"),
        "approval-bound corrected config",
    )

    source_path = _regular_file(
        private.safe_anchor_source_receipt, "safe-anchor source receipt"
    )
    _require_sha(
        source_path, private.safe_anchor_source_sha256, "safe-anchor source receipt"
    )
    source = _read_json(source_path, "safe-anchor source receipt")
    expected_geometry = private.expected_safe_anchor_geometry_sha256
    expected_id = private.expected_safe_anchor_id
    if not (
        source.get("schema") == SAFE_ANCHOR_SOURCE_SCHEMA
        and source.get("overall_status") == "PASS"
        and source.get("campaign_id") == CAMPAIGN_ID
        and source.get("decision")
        == "USE_GEOMETRY_PARAMETERS_ONLY_REGENERATE_WITH_CURRENT_FROZEN_GENERATOR"
        and source.get("historical_candidate_id") == expected_id
        and source.get("current_canonical_geometry_sha256") == expected_geometry
        and source.get("geometry_vector_order") == list(GEOMETRY_FIELDS)
        and source.get("analytical_gate", {}).get("status") == "PASS"
        and source.get("analytical_gate", {}).get("topology_mode") == "1t1t"
        and source.get("historical_gds_reused") is False
        and source.get("historical_labels_reused") is False
    ):
        raise BoundQueueError("safe-anchor source receipt identity or gates mismatch")

    queue_source = _regular_file(private.safe_anchor_queue, "safe-anchor queue")
    _require_sha(queue_source, private.safe_anchor_queue_sha256, "safe-anchor queue")
    rows = _read_csv(queue_source)
    if len(rows) != 1:
        raise BoundQueueError("safe-anchor queue must contain exactly one row")
    row = rows[0]
    identity_fields = (
        "candidate_id_sha256",
        "candidate_geometry_identity_sha256",
        "geometry_id",
        "geometry_sha256",
        "geometry_fingerprint_sha256",
    )
    if any(row.get(field) != expected_geometry for field in identity_fields):
        raise BoundQueueError("safe-anchor queue geometry identity aliases mismatch")
    if not (
        row.get("campaign_id") == CAMPAIGN_ID
        and row.get("campaign_contract_fingerprint") == CONTRACT_FINGERPRINT
        and row.get("analytical_status") == "PASS"
        and row.get("topology_status") == "PASS"
        and row.get("top_metal_drc_status") == "PASS"
    ):
        raise BoundQueueError("safe-anchor queue contract or analytical gates mismatch")
    geometry = source.get("geometry")
    if not isinstance(geometry, Mapping) or set(geometry) != set(GEOMETRY_FIELDS):
        raise BoundQueueError("safe-anchor source geometry fields mismatch")
    for field in GEOMETRY_FIELDS:
        try:
            queue_value = Decimal(str(row[f"geom__{field}"]))
            source_value = Decimal(str(geometry[field]))
        except (KeyError, InvalidOperation) as exc:
            raise BoundQueueError(f"safe-anchor geometry value is invalid: {field}") from exc
        if queue_value != source_value:
            raise BoundQueueError(f"safe-anchor geometry value mismatch: {field}")

    out_dir.mkdir(parents=True, mode=0o700)
    queue_path = out_dir / QUEUE_NAME
    shutil.copyfile(queue_source, queue_path)
    _require_sha(queue_path, private.safe_anchor_queue_sha256, "materialized queue")
    receipt_path = out_dir / OUTPUT_NAME
    receipt = {
        "schema": OUTPUT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "USE_EXACT_BOUND_SAFE_ANCHOR_FOR_RESCUE_GOLDEN",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": CONTRACT_FINGERPRINT,
        "stage": "GOLDEN",
        "queue_count": 1,
        "candidate_queue": _file_record(queue_path),
        "safe_anchor_id": expected_id,
        "safe_anchor_geometry_sha256": expected_geometry,
        "safe_anchor_source_receipt": _file_record(source_path),
        "corrected_foundry_layout_approval_receipt": _file_record(approval_path),
        "corrected_private_configuration": _file_record(config_path),
        "geometry_only_source_reused": True,
        "historical_gds_reused": False,
        "historical_s4p_reused": False,
        "proxy_or_physical_labels_present": False,
        "simulator_action_taken": False,
    }
    _write_json(receipt_path, receipt)
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{_sha256(queue_path)}  {queue_path.name}\n"
        f"{_sha256(receipt_path)}  {receipt_path.name}\n",
        encoding="utf-8",
    )
    return receipt_path


def _run_delegate(private: argparse.Namespace, argv: list[str]) -> int:
    delegate = _regular_file(private.delegate_script, "queue-builder delegate")
    _require_sha(delegate, private.delegate_sha256, "queue-builder delegate")
    result = subprocess.run(
        [sys.executable, str(delegate), *argv],
        stdin=subprocess.DEVNULL,
        shell=False,
        check=False,
    )
    _require_sha(delegate, private.delegate_sha256, "queue-builder delegate")
    return int(result.returncode)


def _option(argv: Sequence[str], name: str) -> str:
    indexes = [index for index, value in enumerate(argv) if value == name]
    if len(indexes) != 1 or indexes[0] + 1 >= len(argv):
        raise BoundQueueError(f"required option is missing or duplicated: {name}")
    return str(argv[indexes[0] + 1])


def _regular_output_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise BoundQueueError(f"output path must not be a symlink: {path}")
    return path.resolve()


def _regular_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise BoundQueueError(f"{label} must not be a symlink")
    resolved = path.resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise BoundQueueError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BoundQueueError(f"{label} is not a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise BoundQueueError("safe-anchor queue lacks a header")
        return [dict(row) for row in reader]


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _require_record_identity(path: Path, value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        raise BoundQueueError(f"{label} identity is missing")
    record = _file_record(path)
    if any(value.get(field) != record[field] for field in record):
        raise BoundQueueError(f"{label} identity mismatch")


def _require_sha(path: Path, expected: str, label: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise BoundQueueError(f"{label} SHA-256 mismatch")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
