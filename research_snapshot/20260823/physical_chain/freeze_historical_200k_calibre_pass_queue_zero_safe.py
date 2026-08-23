#!/usr/bin/env python3
"""Freeze the unchanged zero-blocking Calibre subset for fresh EMX.

This is a fail-closed successor to the historical-200k fixed-10k Calibre
classification stage.  It accepts a fully classified batch even when the
parent batch status is FAIL because of real DRC violations, but it rejects
incomplete scheduling, runtime/license exceptions, identity drift, artifact
drift, or any candidate result that is not fully accounted by Calibre.

No geometry is repaired, projected, searched, or reranked.  The PASS and FAIL
queues are byte-value-preserving row subsets of the frozen one-shot queue.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import socket
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Private production source SHA-256:
# 676571897e1725a4491a1284f4ef3228aa5552e7415e00e56d3898c1191e4c80
# Only the site-specific default hostname is removed in this research snapshot.


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for candidate in (SCRIPT_DIR, PROJECT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from rfic_transformer_inverse_design.layout.gds_hash import (  # noqa: E402
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
    gds_timestamp_normalized_sha256,
)


SCHEMA = "historical_200k_fixed10k_calibre_pass_freeze_zero_safe_v2"
ROW_SCHEMA = "historical_200k_fixed10k_calibre_readiness_row_zero_safe_v2"
RECEIPT_SCHEMA = "historical_200k_fixed10k_calibre_pass_freeze_zero_safe_receipt_v2"
SHA_FIELDS = (
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "gds_sha256",
    "gds_timestamp_normalized_sha256",
)
ALLOWED_DRC_FALSE_CHECKS = {
    "foundry_drc_pass",
    "no_blocking_drc_violations",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started_utc = _utc_now()
    script_path = Path(__file__).resolve()
    sources = {
        "candidate_csv": Path(args.candidate_csv).expanduser().resolve(),
        "gds_index_csv": Path(args.gds_index_csv).expanduser().resolve(),
        "calibre_summary": Path(args.calibre_summary).expanduser().resolve(),
        "calibre_index_csv": Path(args.calibre_index_csv).expanduser().resolve(),
        "calibre_receipt": Path(args.calibre_receipt).expanduser().resolve(),
    }
    expected_hashes = {
        "candidate_csv": args.expected_candidate_csv_sha256,
        "gds_index_csv": args.expected_gds_index_sha256,
        "calibre_summary": args.expected_calibre_summary_sha256,
        "calibre_index_csv": args.expected_calibre_index_sha256,
        "calibre_receipt": args.expected_calibre_receipt_sha256,
    }
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise SystemExit(f"refusing to overwrite output directory: {out_dir}")

    checks: dict[str, bool] = {
        "required_host_matches": socket.getfqdn() == args.required_host,
        "expected_total_decomposition_exact": (
            int(args.analytical_fail_count)
            + int(args.cadence_fail_count)
            + int(args.expected_input_count)
            == int(args.original_target_count)
        ),
    }
    source_records: dict[str, dict[str, Any]] = {}
    for name, path in sources.items():
        record = _file_record(path)
        source_records[name] = record
        checks[f"{name}_exists_nonzero"] = bool(
            record["exists"] and record["size_bytes"] > 0
        )
        checks[f"{name}_sha256_exact"] = record["sha256"] == expected_hashes[name]
    if not all(checks.values()):
        raise SystemExit(f"source hash gate failed: {_failed(checks)}")

    queue_rows, queue_fields = _read_csv(sources["candidate_csv"])
    gds_rows, gds_fields = _read_csv(sources["gds_index_csv"])
    drc_rows, drc_fields = _read_csv(sources["calibre_index_csv"])
    batch = _read_json(sources["calibre_summary"])
    receipt = _read_json(sources["calibre_receipt"])
    expected_count = int(args.expected_input_count)

    queue_by_id = _unique_index(queue_rows, "candidate_id_sha256")
    gds_by_id = _unique_index(gds_rows, "candidate_id_sha256")
    drc_by_id = _unique_index(drc_rows, "candidate_id_sha256")
    expected_ids = set(queue_by_id)
    summary_checks = batch.get("checks") or {}
    checks.update(
        {
            "queue_count_exact": len(queue_rows) == expected_count,
            "gds_index_count_exact": len(gds_rows) == expected_count,
            "calibre_index_count_exact": len(drc_rows) == expected_count,
            "queue_candidate_ids_unique_sha256": len(queue_by_id) == expected_count,
            "gds_candidate_ids_unique_sha256": len(gds_by_id) == expected_count,
            "calibre_candidate_ids_unique_sha256": len(drc_by_id) == expected_count,
            "candidate_sets_exact": expected_ids == set(gds_by_id) == set(drc_by_id),
            "queue_target_ids_unique": _unique_nonempty(queue_rows, "target_id"),
            "queue_geometry_ids_unique_sha256": _unique_sha256(
                queue_rows, "candidate_geometry_identity_sha256"
            ),
            "gds_geometry_ids_unique_sha256": _unique_sha256(
                gds_rows, "candidate_geometry_identity_sha256"
            ),
            "batch_input_index_hash_matches": str(
                batch.get("input_index_sha256") or ""
            ).lower()
            == expected_hashes["gds_index_csv"],
            "batch_candidate_count_exact": _integer(batch.get("candidate_count"))
            == expected_count,
            "batch_completed_count_exact": _integer(
                batch.get("completed_candidate_count")
            )
            == expected_count,
            "batch_pending_unrun_zero": _integer(
                batch.get("pending_unrun_candidate_count")
            )
            == 0,
            "batch_scheduler_not_stopped_early": batch.get("stopped_early") is False,
            "batch_candidate_set_complete": summary_checks.get(
                "candidate_set_complete"
            )
            is True,
            "batch_scheduler_check_pass": summary_checks.get(
                "scheduler_did_not_stop_early"
            )
            is True,
            "batch_checkpoint_index_hash_matches": str(
                (batch.get("checkpoint_drc_index_csv") or {}).get("sha256") or ""
            ).lower()
            == expected_hashes["calibre_index_csv"],
            "receipt_final_status_present": receipt.get("final_status") in {
                "PASS",
                "FAIL",
            },
            "receipt_summary_hash_matches": str(
                (receipt.get("summary") or {}).get("sha256") or ""
            ).lower()
            == expected_hashes["calibre_summary"],
            "receipt_index_hash_matches": str(
                (receipt.get("checkpoint_drc_index") or {}).get("sha256") or ""
            ).lower()
            == expected_hashes["calibre_index_csv"],
            "receipt_input_index_hash_matches": str(
                receipt.get("input_index_sha256") or ""
            ).lower()
            == expected_hashes["gds_index_csv"],
            "receipt_finished": bool(receipt.get("finished_utc")),
        }
    )
    if not all(checks.values()):
        raise SystemExit(f"batch completion/identity gate failed: {_failed(checks)}")

    readiness_rows: list[dict[str, Any]] = []
    pass_ids: list[str] = []
    fail_ids: list[str] = []
    diagnostics: list[str] = []
    blocking_rule_candidate_counts: Counter[str] = Counter()
    blocking_rule_violation_counts: Counter[str] = Counter()
    warning_rule_candidate_counts: Counter[str] = Counter()
    warning_rule_result_counts: Counter[str] = Counter()
    failure_signatures: Counter[str] = Counter()
    panel_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for position, queue_row in enumerate(queue_rows, start=1):
        candidate_id = str(queue_row["candidate_id_sha256"]).lower()
        try:
            gds_row = gds_by_id[candidate_id]
            drc_row = drc_by_id[candidate_id]
            result = _validate_candidate(
                queue_row=queue_row,
                gds_row=gds_row,
                drc_row=drc_row,
                gds_index_path=sources["gds_index_csv"],
                drc_index_path=sources["calibre_index_csv"],
                expected_process_token=args.expected_process_token,
                expected_deck_sha256=args.expected_deck_sha256,
            )
            status = result["overall_status"]
            if status == "PASS":
                pass_ids.append(candidate_id)
            else:
                fail_ids.append(candidate_id)
                blocking_rules = result["blocking_rules"]
                signature = ";".join(sorted(blocking_rules))
                failure_signatures[signature] += 1
                for rule, count in blocking_rules.items():
                    blocking_rule_candidate_counts[rule] += 1
                    blocking_rule_violation_counts[rule] += int(count)
            for rule, count in result["warning_rules"].items():
                warning_rule_candidate_counts[rule] += 1
                warning_rule_result_counts[rule] += int(count)
            panel = str(queue_row.get("panel") or "UNSPECIFIED")
            panel_counts[panel]["input"] += 1
            panel_counts[panel][status.lower()] += 1
            readiness_rows.append(
                {
                    "schema": ROW_SCHEMA,
                    "source_queue_position": position,
                    "source_row_index": queue_row.get("source_row_index", ""),
                    "selection_rank": queue_row.get("selection_rank", ""),
                    "target_id": queue_row.get("target_id", ""),
                    "panel": panel,
                    "candidate_id": queue_row.get("candidate_id", ""),
                    "candidate_id_sha256": candidate_id,
                    "candidate_geometry_identity_sha256": queue_row.get(
                        "candidate_geometry_identity_sha256", ""
                    ),
                    "source_geometry_digest_sha256": queue_row.get(
                        "source_geometry_digest_sha256", ""
                    ),
                    "frozen_target_plus_geometry_identity_sha256": queue_row.get(
                        "frozen_target_plus_geometry_identity_sha256", ""
                    ),
                    "gds_sha256": gds_row.get("gds_sha256", ""),
                    "gds_timestamp_normalized_sha256": gds_row.get(
                        "gds_timestamp_normalized_sha256", ""
                    ),
                    "calibre_drc_status": status,
                    "blocking_drc_violation_count": result["blocking_count"],
                    "documented_warning_count": result["warning_count"],
                    "blocking_rule_counts_json": json.dumps(
                        result["blocking_rules"], sort_keys=True, separators=(",", ":")
                    ),
                    "documented_warning_rules_json": json.dumps(
                        result["warning_rules"], sort_keys=True, separators=(",", ":")
                    ),
                    "drc_summary_path": result["summary_path"],
                    "drc_summary_sha256": result["summary_sha256"],
                    "geometry_projection_or_repair_used": "false",
                    "candidate_search_or_ranking_used": "false",
                }
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed audit diagnostic.
            diagnostics.append(
                f"{candidate_id}: {type(exc).__name__}: {exc}"
            )
            if len(diagnostics) >= 100:
                break

    pass_set = set(pass_ids)
    fail_set = set(fail_ids)
    pass_queue = [
        row
        for row in queue_rows
        if str(row["candidate_id_sha256"]).lower() in pass_set
    ]
    fail_queue = [
        row
        for row in queue_rows
        if str(row["candidate_id_sha256"]).lower() in fail_set
    ]
    pass_gds = [
        row
        for row in gds_rows
        if str(row["candidate_id_sha256"]).lower() in pass_set
    ]
    fail_gds = [
        row
        for row in gds_rows
        if str(row["candidate_id_sha256"]).lower() in fail_set
    ]
    pass_drc = [
        row
        for row in drc_rows
        if str(row["candidate_id_sha256"]).lower() in pass_set
    ]
    fail_drc = [
        row
        for row in drc_rows
        if str(row["candidate_id_sha256"]).lower() in fail_set
    ]

    reported_pass = _integer(batch.get("pass_count"))
    reported_fail = _integer(batch.get("fail_count"))
    error_ids, error_parse_ok = _batch_error_ids(batch.get("errors") or [])
    checks.update(
        {
            "no_candidate_validation_diagnostics": not diagnostics,
            "all_candidates_validated": len(readiness_rows) == expected_count,
            "pass_plus_fail_exact": len(pass_ids) + len(fail_ids) == expected_count,
            "pass_fail_disjoint": not (pass_set & fail_set),
            "pass_fail_set_complete": pass_set | fail_set == expected_ids,
            "batch_pass_count_matches": reported_pass == len(pass_ids),
            "batch_fail_count_matches": reported_fail == len(fail_ids),
            "batch_overall_status_consistent": batch.get("overall_status")
            == ("PASS" if not fail_ids else "FAIL"),
            "receipt_final_status_consistent": receipt.get("final_status")
            == batch.get("overall_status"),
            "batch_errors_are_only_accounted_drc_fails": error_parse_ok
            and error_ids == fail_set,
            "pass_queue_count_exact": len(pass_queue) == len(pass_ids),
            "fail_queue_count_exact": len(fail_queue) == len(fail_ids),
            "pass_gds_count_exact": len(pass_gds) == len(pass_ids),
            "fail_gds_count_exact": len(fail_gds) == len(fail_ids),
            "pass_drc_count_exact": len(pass_drc) == len(pass_ids),
            "fail_drc_count_exact": len(fail_drc) == len(fail_ids),
            "zero_blocking_pass_nonempty": bool(pass_ids),
            "no_geometry_repair_projection_or_rerank": all(
                _false_text(row.get("geometry_projection_or_repair_used"))
                and _false_text(row.get("candidate_search_or_ranking_used"))
                for row in queue_rows
            ),
        }
    )
    if not all(checks.values()):
        raise SystemExit(
            "candidate DRC freeze gate failed: "
            + json.dumps(
                {"failed": _failed(checks), "diagnostics": diagnostics[:20]},
                sort_keys=True,
            )
        )

    output_paths = {
        "readiness_rows": out_dir / "calibre_drc_readiness_rows.csv",
        "pass_queue": out_dir / "calibre_zero_blocking_pass_unchanged_queue.csv",
        "fail_queue": out_dir / "calibre_blocking_fail_unchanged_queue.csv",
        "pass_gds_index": out_dir / "calibre_zero_blocking_pass_gds_index.csv",
        "fail_gds_index": out_dir / "calibre_blocking_fail_gds_index.csv",
        "pass_drc_index": out_dir / "calibre_zero_blocking_pass_drc_index.csv",
        "fail_drc_index": out_dir / "calibre_blocking_fail_drc_index.csv",
        "summary": out_dir / "calibre_pass_freeze_summary.json",
        "receipt": out_dir / "calibre_pass_freeze_receipt.json",
    }
    out_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(output_paths["readiness_rows"], readiness_rows)
    _write_csv(output_paths["pass_queue"], pass_queue, queue_fields)
    _write_csv(output_paths["fail_queue"], fail_queue, queue_fields)
    _write_csv(output_paths["pass_gds_index"], pass_gds, gds_fields)
    _write_csv(output_paths["fail_gds_index"], fail_gds, gds_fields)
    _write_csv(output_paths["pass_drc_index"], pass_drc, drc_fields)
    _write_csv(output_paths["fail_drc_index"], fail_drc, drc_fields)

    artifact_records = {
        name: _file_record(path)
        for name, path in output_paths.items()
        if name not in {"summary", "receipt"}
    }
    all_identity = _identity_aggregate(queue_rows, gds_by_id)
    pass_identity = _identity_aggregate(pass_queue, gds_by_id)
    fail_identity = _identity_aggregate(fail_queue, gds_by_id)
    original_accounting = {
        "original_target_count": int(args.original_target_count),
        "analytical_preflight_fail": int(args.analytical_fail_count),
        "cadence_streamout_fail": int(args.cadence_fail_count),
        "calibre_drc_blocking_fail": len(fail_ids),
        "fresh_emx_eligible": len(pass_ids),
        "accounted_total": int(args.analytical_fail_count)
        + int(args.cadence_fail_count)
        + len(fail_ids)
        + len(pass_ids),
    }
    summary = {
        "schema": SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "ZERO_BLOCKING_UNCHANGED_SUBSET_FROZEN_FOR_FRESH_EMX",
        "host": socket.getfqdn(),
        "script": _file_record(script_path),
        "source_artifacts": source_records,
        "source_batch_overall_status": batch.get("overall_status"),
        "counts": {
            "calibre_classified": expected_count,
            "calibre_zero_blocking_pass": len(pass_ids),
            "calibre_blocking_fail": len(fail_ids),
        },
        "original_10000_denominator_accounting": original_accounting,
        "panel_counts": {
            panel: dict(sorted(counter.items()))
            for panel, counter in sorted(panel_counts.items())
        },
        "blocking_rule_candidate_counts": dict(
            sorted(blocking_rule_candidate_counts.items())
        ),
        "blocking_rule_violation_counts": dict(
            sorted(blocking_rule_violation_counts.items())
        ),
        "blocking_failure_signature_candidate_counts": dict(
            sorted(failure_signatures.items())
        ),
        "documented_warning_rule_candidate_counts": dict(
            sorted(warning_rule_candidate_counts.items())
        ),
        "documented_warning_rule_result_counts": dict(
            sorted(warning_rule_result_counts.items())
        ),
        "identity_aggregates": {
            "schema": (
                "sha256(sorted(target_id,candidate_id_sha256,"
                "candidate_geometry_identity_sha256,"
                "gds_timestamp_normalized_sha256) newline records)"
            ),
            "all_classified_sha256": all_identity,
            "zero_blocking_pass_sha256": pass_identity,
            "blocking_fail_sha256": fail_identity,
        },
        "artifacts": artifact_records,
        "checks": checks,
        "scientific_boundary": (
            "Only the unchanged zero-blocking PASS rows are eligible for fresh "
            "EMX. Blocking DRC rows remain failures in the original 10,000-row "
            "denominator. No repair, projection, search, or reranking is used."
        ),
        "automatic_model_or_production_promotion_authorized": False,
    }
    _write_json(output_paths["summary"], summary)
    receipt_payload = {
        "schema": RECEIPT_SCHEMA,
        "started_utc": started_utc,
        "finished_utc": _utc_now(),
        "host": socket.getfqdn(),
        "pid": os.getpid(),
        "argv": sys.argv,
        "script": _file_record(script_path),
        "source_artifacts": source_records,
        "summary": _file_record(output_paths["summary"]),
        "artifacts": {
            **artifact_records,
            "summary": _file_record(output_paths["summary"]),
        },
        "final_status": "PASS",
    }
    _write_json(output_paths["receipt"], receipt_payload)

    print("overall_status=PASS")
    print(f"classified_count={expected_count}")
    print(f"zero_blocking_pass_count={len(pass_ids)}")
    print(f"blocking_fail_count={len(fail_ids)}")
    print(f"summary={output_paths['summary']}")
    print(f"receipt={output_paths['receipt']}")
    print(f"pass_queue={output_paths['pass_queue']}")
    print(f"pass_gds_index={output_paths['pass_gds_index']}")
    print(f"pass_drc_index={output_paths['pass_drc_index']}")
    return 0


def _validate_candidate(
    *,
    queue_row: dict[str, str],
    gds_row: dict[str, str],
    drc_row: dict[str, str],
    gds_index_path: Path,
    drc_index_path: Path,
    expected_process_token: str,
    expected_deck_sha256: str,
) -> dict[str, Any]:
    candidate_id = str(queue_row["candidate_id_sha256"]).lower()
    geometry_id = str(queue_row["candidate_geometry_identity_sha256"]).lower()
    if str(gds_row.get("candidate_geometry_identity_sha256") or "").lower() != geometry_id:
        raise ValueError("GDS index geometry identity mismatch")
    if str(drc_row.get("candidate_geometry_identity_sha256") or "").lower() != geometry_id:
        raise ValueError("Calibre index geometry identity mismatch")
    if str(gds_row.get("overall_status") or "") != "PASS":
        raise ValueError("source candidate-bound GDS index is not PASS")

    for field in ("gds_sha256", "gds_timestamp_normalized_sha256"):
        expected = str(gds_row.get(field) or "").lower()
        observed = str(drc_row.get(field) or "").lower()
        if not _is_sha256(expected) or observed != expected:
            raise ValueError(f"Calibre/GDS index mismatch: {field}")
    algorithm = str(gds_row.get("gds_timestamp_normalization_algorithm") or "")
    if (
        algorithm != GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        or str(drc_row.get("gds_timestamp_normalization_algorithm") or "")
        != algorithm
    ):
        raise ValueError("GDS normalization algorithm mismatch")

    gds_path = _resolve_artifact(gds_index_path, gds_row.get("gds_path"))
    if not gds_path.is_file() or gds_path.stat().st_size <= 0:
        raise ValueError("candidate GDS missing or empty")
    if _sha256(gds_path) != str(gds_row["gds_sha256"]).lower():
        raise ValueError("candidate GDS raw hash drift")
    if gds_timestamp_normalized_sha256(gds_path) != str(
        gds_row["gds_timestamp_normalized_sha256"]
    ).lower():
        raise ValueError("candidate GDS normalized hash drift")

    summary_path = _resolve_artifact(drc_index_path, drc_row.get("drc_summary_path"))
    if not summary_path.is_file():
        raise ValueError("candidate DRC summary missing")
    summary_sha = _sha256(summary_path)
    if summary_sha != str(drc_row.get("drc_summary_sha256") or "").lower():
        raise ValueError("candidate DRC summary hash drift")
    payload = _read_json(summary_path)
    if str(payload.get("candidate_id_sha256") or "").lower() != candidate_id:
        raise ValueError("candidate DRC identity mismatch")
    if str(payload.get("candidate_geometry_identity_sha256") or "").lower() != geometry_id:
        raise ValueError("candidate DRC geometry identity mismatch")
    if str(payload.get("gds_sha256") or "").lower() != str(
        gds_row["gds_sha256"]
    ).lower():
        raise ValueError("candidate DRC raw GDS hash mismatch")
    if str(payload.get("gds_timestamp_normalized_sha256") or "").lower() != str(
        gds_row["gds_timestamp_normalized_sha256"]
    ).lower():
        raise ValueError("candidate DRC normalized GDS hash mismatch")
    if payload.get("gds_timestamp_normalization_algorithm") != algorithm:
        raise ValueError("candidate DRC GDS normalization algorithm mismatch")
    if str(payload.get("process_token") or "") != expected_process_token:
        raise ValueError("candidate DRC process token mismatch")
    if str(payload.get("drc_source_rule_deck_sha256") or "").lower() != expected_deck_sha256:
        raise ValueError("candidate DRC source deck hash mismatch")
    if payload.get("calibre_report_rule_count_sum") != payload.get("drc_violation_count"):
        raise ValueError("Calibre report result accounting mismatch")

    raw_count = _integer(payload.get("drc_violation_count"))
    blocking_count = _integer(payload.get("blocking_drc_violation_count"))
    warning_count = _integer(payload.get("documented_warning_count"))
    if raw_count != blocking_count + warning_count:
        raise ValueError("raw/blocking/warning DRC counts do not reconcile")
    if _integer(drc_row.get("drc_violation_count")) != raw_count:
        raise ValueError("Calibre index raw result count mismatch")
    if _integer(drc_row.get("blocking_drc_violation_count")) != blocking_count:
        raise ValueError("Calibre index blocking result count mismatch")
    if _integer(drc_row.get("documented_warning_count")) != warning_count:
        raise ValueError("Calibre index warning count mismatch")

    nonzero_rules = _count_map(payload.get("nonzero_rule_counts") or {})
    warning_rules = _count_map(payload.get("documented_warning_rules") or {})
    if sum(nonzero_rules.values()) != raw_count or sum(warning_rules.values()) != warning_count:
        raise ValueError("candidate rule counts do not reconcile")
    blocking_rules: dict[str, int] = {}
    for rule, count in nonzero_rules.items():
        remaining = count - warning_rules.get(rule, 0)
        if remaining < 0:
            raise ValueError("documented warning count exceeds raw rule count")
        if remaining:
            blocking_rules[rule] = remaining
    if sum(blocking_rules.values()) != blocking_count:
        raise ValueError("blocking rule counts do not reconcile")

    payload_checks = payload.get("checks") or {}
    if payload_checks.get("calibre_result_accounting_complete") is not True:
        raise ValueError("Calibre result accounting check is not PASS")
    false_checks = {name for name, value in payload_checks.items() if value is not True}
    status = str(payload.get("overall_status") or "")
    if status == "PASS":
        if blocking_count != 0 or false_checks:
            raise ValueError("Calibre PASS contains blockers or failed checks")
    elif status == "FAIL":
        if blocking_count <= 0:
            raise ValueError("Calibre FAIL has no blocking violation")
        if false_checks != ALLOWED_DRC_FALSE_CHECKS:
            raise ValueError(
                f"Calibre FAIL has abnormal failed checks: {sorted(false_checks)}"
            )
    else:
        raise ValueError(f"invalid Calibre status: {status!r}")
    if str(drc_row.get("overall_status") or "") != status:
        raise ValueError("Calibre index/payload status mismatch")

    for path_field, hash_field in (
        ("drc_report_path", "drc_report_sha256"),
        ("drc_rule_deck_path", "drc_rule_deck_sha256"),
        ("calibre_stdout_path", "calibre_stdout_sha256"),
        ("calibre_stderr_path", "calibre_stderr_sha256"),
    ):
        artifact = _resolve_artifact(summary_path, payload.get(path_field))
        if not artifact.is_file() or _sha256(artifact) != str(
            payload.get(hash_field) or ""
        ).lower():
            raise ValueError(f"candidate DRC artifact hash drift: {path_field}")
    results = _resolve_artifact(summary_path, payload.get("drc_results_database_path"))
    if not results.exists() or _artifact_sha256(results) != str(
        payload.get("drc_results_database_sha256") or ""
    ).lower():
        raise ValueError("candidate Calibre results database hash drift")

    return {
        "overall_status": status,
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "blocking_rules": dict(sorted(blocking_rules.items())),
        "warning_rules": dict(sorted(warning_rules.items())),
        "summary_path": str(summary_path),
        "summary_sha256": summary_sha,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--gds-index-csv", required=True)
    parser.add_argument("--calibre-summary", required=True)
    parser.add_argument("--calibre-index-csv", required=True)
    parser.add_argument("--calibre-receipt", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-candidate-csv-sha256", required=True)
    parser.add_argument("--expected-gds-index-sha256", required=True)
    parser.add_argument("--expected-calibre-summary-sha256", required=True)
    parser.add_argument("--expected-calibre-index-sha256", required=True)
    parser.add_argument("--expected-calibre-receipt-sha256", required=True)
    parser.add_argument("--expected-input-count", type=int, default=7373)
    parser.add_argument("--original-target-count", type=int, default=10000)
    parser.add_argument("--analytical-fail-count", type=int, default=2074)
    parser.add_argument("--cadence-fail-count", type=int, default=553)
    parser.add_argument("--required-host", required=True)
    parser.add_argument(
        "--expected-process-token",
        required=True,
        help="Explicit site-local process identity; no foundry default is bundled.",
    )
    parser.add_argument(
        "--expected-deck-sha256",
        default="8252a77efecf92d3b187d83f7047df45433ce662c53683996c175b2aa80653ef",
    )
    args = parser.parse_args(argv)
    for name in (
        "expected_candidate_csv_sha256",
        "expected_gds_index_sha256",
        "expected_calibre_summary_sha256",
        "expected_calibre_index_sha256",
        "expected_calibre_receipt_sha256",
        "expected_deck_sha256",
    ):
        value = str(getattr(args, name) or "").strip().lower()
        if not _is_sha256(value):
            parser.error(f"--{name.replace('_', '-')} must be SHA-256")
        setattr(args, name, value)
    for name in (
        "expected_input_count",
        "original_target_count",
        "analytical_fail_count",
        "cadence_fail_count",
    ):
        if int(getattr(args, name)) < 0:
            parser.error(f"--{name.replace('_', '-')} must be nonnegative")
    if int(args.expected_input_count) < 1:
        parser.error("--expected-input-count must be positive")
    return args


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader), list(reader.fieldnames)


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str] | None = None,
) -> None:
    if fields is None:
        fields = []
        for row in rows:
            for name in row:
                if name not in fields:
                    fields.append(name)
    if not fields:
        raise ValueError(f"refusing to write headerless empty CSV: {path}")
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON is not an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _unique_index(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = str(row.get(field) or "").lower()
        if not _is_sha256(value) or value in result:
            return {}
        result[value] = row
    return result


def _unique_nonempty(rows: list[dict[str, str]], field: str) -> bool:
    values = [str(row.get(field) or "") for row in rows]
    return bool(values) and all(values) and len(set(values)) == len(values)


def _unique_sha256(rows: list[dict[str, str]], field: str) -> bool:
    values = [str(row.get(field) or "").lower() for row in rows]
    return bool(values) and all(_is_sha256(value) for value in values) and len(
        set(values)
    ) == len(values)


def _batch_error_ids(errors: Any) -> tuple[set[str], bool]:
    if not isinstance(errors, list):
        return set(), False
    ids: set[str] = set()
    for item in errors:
        text = str(item)
        match = re.fullmatch(
            r"([0-9a-fA-F]{64}): Calibre reported [1-9][0-9]* raw results, "
            r"[1-9][0-9]* blocking violations",
            text,
        )
        if match is None:
            return set(), False
        candidate_id = match.group(1).lower()
        if not _is_sha256(candidate_id) or candidate_id in ids:
            return set(), False
        ids.add(candidate_id)
    return ids, True


def _identity_aggregate(
    queue_rows: list[dict[str, str]],
    gds_by_id: dict[str, dict[str, str]],
) -> str:
    records: list[str] = []
    for row in queue_rows:
        candidate_id = str(row["candidate_id_sha256"]).lower()
        records.append(
            ",".join(
                (
                    str(row["target_id"]),
                    candidate_id,
                    str(row["candidate_geometry_identity_sha256"]).lower(),
                    str(
                        gds_by_id[candidate_id][
                            "gds_timestamp_normalized_sha256"
                        ]
                    ).lower(),
                )
            )
        )
    payload = "".join(f"{line}\n" for line in sorted(records)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("rule counts are not an object")
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        count = _integer(raw_count)
        if not str(key) or count <= 0:
            raise ValueError("rule count must be positive")
        result[str(key)] = count
    return result


def _resolve_artifact(source: Path, raw: Any) -> Path:
    path = Path(str(raw or "")).expanduser()
    return path.resolve() if path.is_absolute() else (source.parent / path).resolve()


def _artifact_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    if path.is_dir():
        digest = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(_sha256(child)))
        return digest.hexdigest()
    return ""


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path) if path.is_file() else "",
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _integer(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -1
    if not math.isfinite(number) or not math.isclose(
        number, round(number), rel_tol=0.0, abs_tol=1.0e-9
    ):
        return -1
    return int(round(number))


def _false_text(value: Any) -> bool:
    return str(value or "").strip().lower() in {"false", "0", "no"}


def _failed(checks: dict[str, bool]) -> list[str]:
    return [name for name, passed in checks.items() if not passed]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
