#!/usr/bin/env python3
"""Freeze the unchanged Cadence-PASS subset of the historical-200k 10k replay.

This is a second, stricter funnel gate.  It never repairs a geometry: every
source row is retained as either an exact candidate-bound GDS PASS or an
explicit Cadence/gdstk FAIL with its original error.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "historical_200k_fixed10k_cadence_streamout_readiness_v1"
ROW_SCHEMA = "historical_200k_fixed10k_cadence_streamout_readiness_row_v1"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    queue_path = Path(args.candidate_csv).expanduser().resolve()
    streamout_dir = Path(args.streamout_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise SystemExit(f"refusing to overwrite output directory: {out_dir}")
    queue_sha = _sha256(queue_path)
    if queue_sha != args.expected_candidate_csv_sha256:
        raise SystemExit(
            f"candidate CSV SHA mismatch: {queue_sha} != {args.expected_candidate_csv_sha256}"
        )
    queue_rows = _read_csv(queue_path)
    if len(queue_rows) != int(args.expected_count):
        raise SystemExit(f"candidate count mismatch: {len(queue_rows)}")
    candidate_ids = [str(row.get("candidate_id") or "") for row in queue_rows]
    if any(not value for value in candidate_ids) or len(set(candidate_ids)) != len(
        candidate_ids
    ):
        raise SystemExit("candidate IDs are missing or non-unique")

    parallel_summary_path = (
        streamout_dir / "parallel_candidate_queue_dataset_summary.json"
    )
    parallel_summary = _read_json(parallel_summary_path)
    merged_rows_path = streamout_dir / "dataset_rows.csv"
    merged_rows = _read_csv(merged_rows_path)
    merged_by_id: dict[str, dict[str, str]] = {}
    for row in merged_rows:
        candidate_id = str(row.get("queue__candidate_id") or "")
        if not candidate_id or candidate_id in merged_by_id:
            raise SystemExit("merged dataset candidate IDs are missing or duplicated")
        merged_by_id[candidate_id] = row

    checks: dict[str, bool] = {
        "parallel_summary_exists": parallel_summary_path.is_file(),
        "parallel_summary_is_expected_failed_closed_parent": (
            parallel_summary.get("overall_status") == "FAIL"
        ),
        "parallel_run_is_cadence_streamout_only": (
            parallel_summary.get("run_emx") is False
            and parallel_summary.get("create_only") is False
            and parallel_summary.get("cadence_streamout_only") is True
        ),
        "parallel_requested_jobs_exact": int(
            parallel_summary.get("jobs_requested") or 0
        )
        == int(args.expected_jobs),
        "parallel_input_count_exact": int(
            parallel_summary.get("input_row_count") or 0
        )
        == int(args.expected_count),
        "parallel_merged_count_exact": int(
            parallel_summary.get("merged_row_count") or 0
        )
        == int(args.expected_count),
        "parallel_shard_count_exact": int(
            parallel_summary.get("shard_count") or 0
        )
        == int(args.expected_count),
        "merged_dataset_row_count_exact": len(merged_rows)
        == int(args.expected_count),
        "merged_candidate_set_exact": set(merged_by_id) == set(candidate_ids),
    }
    if not all(checks.values()):
        raise SystemExit(f"parent streamout evidence gate failed: {_failed(checks)}")

    readiness_rows: list[dict[str, Any]] = []
    pass_queue: list[dict[str, str]] = []
    fail_queue: list[dict[str, str]] = []
    pass_dataset: list[dict[str, str]] = []
    failure_categories: Counter[str] = Counter()
    invalid_evidence: list[str] = []
    seen_shards: set[int] = set()
    for position, candidate in enumerate(queue_rows, start=1):
        candidate_id = str(candidate["candidate_id"])
        dataset_row = merged_by_id[candidate_id]
        shard_text = str(dataset_row.get("parallel_shard") or "")
        try:
            shard_index = int(shard_text)
        except ValueError:
            invalid_evidence.append(f"{candidate_id}: invalid shard {shard_text!r}")
            continue
        if shard_index in seen_shards:
            invalid_evidence.append(f"{candidate_id}: duplicate shard {shard_index}")
            continue
        seen_shards.add(shard_index)
        shard_dir = streamout_dir / "parallel_shards" / f"shard_{shard_index:03d}"
        shard_queue = _read_csv(shard_dir / "candidate_queue_shard.csv")
        shard_rows = _read_csv(shard_dir / "dataset_rows.csv")
        shard_summary_path = shard_dir / "candidate_queue_dataset_summary.json"
        shard_summary = _read_json(shard_summary_path)
        if len(shard_queue) != 1 or len(shard_rows) != 1:
            invalid_evidence.append(f"{candidate_id}: shard is not exactly one row")
            continue
        if str(shard_queue[0].get("candidate_id") or "") != candidate_id:
            invalid_evidence.append(f"{candidate_id}: shard candidate mismatch")
            continue
        if str(shard_rows[0].get("queue__candidate_id") or "") != candidate_id:
            invalid_evidence.append(f"{candidate_id}: shard result candidate mismatch")
            continue
        if str(dataset_row.get("queue__candidate_id_sha256") or "").lower() != str(
            candidate.get("candidate_id_sha256") or ""
        ).lower():
            invalid_evidence.append(f"{candidate_id}: candidate SHA mismatch")
            continue
        if str(
            dataset_row.get("queue__candidate_geometry_identity_sha256") or ""
        ).lower() != str(
            candidate.get("candidate_geometry_identity_sha256") or ""
        ).lower():
            invalid_evidence.append(f"{candidate_id}: geometry SHA mismatch")
            continue
        config_contract = shard_summary.get("current_foundry_identity_contract") or {}
        config_checks = config_contract.get("checks") or {}
        if (
            config_contract.get("overall_status") != "PASS"
            or not config_checks
            or not all(value is True for value in config_checks.values())
            or str(
                (shard_summary.get("config_source") or {}).get("sha256") or ""
            ).lower()
            != args.expected_config_sha256
        ):
            invalid_evidence.append(f"{candidate_id}: config identity failed")
            continue
        if not (
            shard_summary.get("run_emx") is False
            and shard_summary.get("create_only") is False
            and shard_summary.get("cadence_streamout_only") is True
            and int(shard_summary.get("result_count") or 0) == 1
        ):
            invalid_evidence.append(f"{candidate_id}: shard execution mode mismatch")
            continue

        row_ok = _truthy(dataset_row.get("ok"))
        shard_pass = shard_summary.get("overall_status") == "PASS"
        work_dir = Path(str(dataset_row.get("work_dir") or "")).expanduser().resolve()
        gds_path = work_dir / "streamout" / "transformer_layout_cadpins.gds"
        gds_ok = gds_path.is_file() and gds_path.stat().st_size > 0
        if shard_pass and row_ok and gds_ok:
            status = "PASS"
            category = ""
            error = ""
            pass_queue.append(candidate)
            pass_dataset.append(dataset_row)
        elif (not shard_pass) and (not row_ok):
            status = "FAIL"
            error = str(dataset_row.get("error") or "").strip()
            category = _failure_category(error)
            failure_categories[category] += 1
            fail_queue.append(candidate)
        else:
            invalid_evidence.append(
                f"{candidate_id}: inconsistent status shard={shard_pass}, row={row_ok}, gds={gds_ok}"
            )
            continue
        readiness_rows.append(
            {
                "schema": ROW_SCHEMA,
                "source_queue_position": position,
                "source_row_index": candidate.get("source_row_index", ""),
                "selection_rank": candidate.get("selection_rank", ""),
                "target_id": candidate.get("target_id", ""),
                "panel": candidate.get("panel", ""),
                "candidate_id": candidate_id,
                "candidate_id_sha256": candidate.get("candidate_id_sha256", ""),
                "candidate_geometry_identity_sha256": candidate.get(
                    "candidate_geometry_identity_sha256", ""
                ),
                "source_geometry_digest_sha256": candidate.get(
                    "source_geometry_digest_sha256", ""
                ),
                "frozen_target_plus_geometry_identity_sha256": candidate.get(
                    "frozen_target_plus_geometry_identity_sha256", ""
                ),
                "parallel_shard": shard_index,
                "cadence_streamout_status": status,
                "failure_category": category,
                "error": error,
                "work_dir": str(work_dir),
                "gds_path": str(gds_path) if gds_ok else "",
                "gds_exists_nonzero": gds_ok,
                "geometry_projection_or_repair_used": candidate.get(
                    "geometry_projection_or_repair_used", ""
                ),
                "candidate_search_or_ranking_used": candidate.get(
                    "candidate_search_or_ranking_used", ""
                ),
            }
        )

    pass_count = len(pass_queue)
    fail_count = len(fail_queue)
    checks.update(
        {
            "all_source_rows_have_valid_shard_evidence": (
                not invalid_evidence
                and len(readiness_rows) == int(args.expected_count)
                and len(seen_shards) == int(args.expected_count)
            ),
            "pass_count_matches_expected": pass_count
            == int(args.expected_pass_count),
            "fail_count_matches_expected": fail_count
            == int(args.expected_fail_count),
            "pass_plus_fail_matches_source": pass_count + fail_count
            == int(args.expected_count),
            "only_expected_failure_category": set(failure_categories)
            == {"cadence_gdstk_center_tapped_terminal_span"},
            "all_pass_rows_have_candidate_bound_gds": all(
                row["gds_exists_nonzero"] is True
                for row in readiness_rows
                if row["cadence_streamout_status"] == "PASS"
            ),
            "no_source_geometry_projection_or_repair": all(
                str(row.get("geometry_projection_or_repair_used") or "").lower()
                == "false"
                for row in queue_rows
            ),
            "pass_candidate_ids_unique": len(
                {row["candidate_id"] for row in pass_queue}
            )
            == pass_count,
            "pass_geometry_ids_unique": len(
                {row["candidate_geometry_identity_sha256"] for row in pass_queue}
            )
            == pass_count,
        }
    )

    out_dir.mkdir(parents=True)
    readiness_path = out_dir / "cadence_streamout_readiness_rows.csv"
    pass_queue_path = out_dir / "cadence_pass_unchanged_one_shot_queue.csv"
    fail_queue_path = out_dir / "cadence_fail_unchanged_one_shot_queue.csv"
    pass_dataset_path = out_dir / "cadence_pass_dataset_rows.csv"
    _write_csv(readiness_path, readiness_rows)
    _write_csv(pass_queue_path, pass_queue)
    _write_csv(fail_queue_path, fail_queue)
    _write_csv(pass_dataset_path, pass_dataset)
    status = "PASS" if checks and all(checks.values()) else "FAIL"
    summary = {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "GO_UNCHANGED_CADENCE_PASS_ONLY_NO_GO_CADENCE_FAILED"
            if status == "PASS"
            else "DO_NOT_ADVANCE_CADENCE_EVIDENCE_INCOMPLETE"
        ),
        "source_candidate_count": len(queue_rows),
        "cadence_pass_count": pass_count,
        "cadence_fail_count": fail_count,
        "cadence_pass_fraction_of_analytical_pass": (
            pass_count / len(queue_rows) if queue_rows else 0.0
        ),
        "cadence_pass_fraction_of_original_10000": pass_count / 10000.0,
        "prior_analytical_fail_count": 2074,
        "end_to_end_not_advanced_count": 2074 + fail_count,
        "failure_categories": dict(sorted(failure_categories.items())),
        "checks": checks,
        "invalid_evidence": invalid_evidence,
        "source_pins": {
            "candidate_csv": _file_record(queue_path, len(queue_rows)),
            "parallel_summary": _file_record(parallel_summary_path),
            "merged_dataset_rows": _file_record(merged_rows_path, len(merged_rows)),
            "config_sha256": args.expected_config_sha256,
        },
        "artifacts": {
            "readiness_rows": _file_record(readiness_path, len(readiness_rows)),
            "cadence_pass_queue": _file_record(pass_queue_path, pass_count),
            "cadence_fail_queue": _file_record(fail_queue_path, fail_count),
            "cadence_pass_dataset_rows": _file_record(
                pass_dataset_path, len(pass_dataset)
            ),
        },
        "passing_identity_aggregate_sha256": _aggregate(
            [
                str(row["frozen_target_plus_geometry_identity_sha256"])
                for row in pass_queue
            ]
        ),
        "execution_authorization": {
            "foundry_gds_binding_allowed_only_for_unchanged_cadence_pass_queue": status
            == "PASS",
            "allowed_candidate_count": pass_count,
            "cadence_failed_candidate_count": fail_count,
            "prior_analytical_failed_candidate_count": 2074,
            "repair_or_projection_authorized": False,
            "literal_10000_fresh_emx_authorized": False,
            "foundry_calibre_required_before_fresh_emx": True,
        },
        "scientific_boundary": (
            "This stage records a stricter Cadence/gdstk manufacturability funnel. "
            "It does not modify, project, repair, search, or rerank any geometry. "
            "Only exact source rows with candidate-bound GDS may advance."
        ),
    }
    summary_path = out_dir / "cadence_streamout_readiness_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={status}")
    print(f"cadence_pass_count={pass_count}")
    print(f"cadence_fail_count={fail_count}")
    print(f"summary={summary_path}")
    print(f"pass_queue={pass_queue_path}")
    print(f"pass_dataset_rows={pass_dataset_path}")
    return 0 if status == "PASS" else 2


def _failure_category(error: str) -> str:
    text = str(error or "")
    if "center-tapped terminal span" in text and "is below" in text:
        return "cadence_gdstk_center_tapped_terminal_span"
    token = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:120]
    return f"unclassified_{token or 'missing_error'}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--streamout-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-candidate-csv-sha256", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--expected-count", type=int, default=7926)
    parser.add_argument("--expected-pass-count", type=int, default=7373)
    parser.add_argument("--expected-fail-count", type=int, default=553)
    parser.add_argument("--expected-jobs", type=int, default=48)
    args = parser.parse_args(argv)
    for name in (
        "expected_candidate_csv_sha256",
        "expected_config_sha256",
    ):
        value = str(getattr(args, name)).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            parser.error(f"--{name.replace('_', '-')} must be SHA-256")
        setattr(args, name, value)
    if (
        int(args.expected_count) < 1
        or int(args.expected_pass_count) < 1
        or int(args.expected_fail_count) < 1
        or int(args.expected_pass_count) + int(args.expected_fail_count)
        != int(args.expected_count)
    ):
        parser.error("expected pass/fail counts must be positive and sum to expected count")
    return args


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"CSV is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["candidate_id", "cadence_streamout_status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"JSON is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON is not an object: {path}")
    return payload


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "ok"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aggregate(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _file_record(path: Path, row_count: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path) if path.is_file() else "",
    }
    if row_count is not None:
        record["row_count"] = int(row_count)
    return record


def _failed(checks: dict[str, bool]) -> list[str]:
    return [name for name, passed in checks.items() if not passed]


if __name__ == "__main__":
    raise SystemExit(main())
