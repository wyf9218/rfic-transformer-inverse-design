#!/usr/bin/env python3
"""Combine independent forward-model predictions into ensemble disagreement.

Every member must predict the same candidate IDs and the same quantized
geometry. The output mean and standard deviation are acquisition provenance
only; they are never EMX labels or physical-distribution evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_FEATURES = "lp_nh_center,ls_nh_center,q_center,k_abs_center"
DECISION = "USE_DEEP_ENSEMBLE_DISAGREEMENT_FOR_CANDIDATE_ABLATION_ONLY"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    member_paths = [Path(value).expanduser().resolve() for value in args.member_csv]
    features = _columns(args.feature_columns)
    requested_geometry = _columns(args.geometry_columns)
    members = [_read_member(path, args.id_column) for path in member_paths]
    geometry_columns = requested_geometry or _infer_geometry_columns(members)

    checks = {
        "expected_member_count": len(member_paths) == int(args.expected_members),
        "all_member_files_exist": all(path.is_file() for path in member_paths),
        "id_column_present": bool(members)
        and all(args.id_column in member["fields"] for member in members),
        "feature_columns_present": bool(features)
        and all(
            all(f"pred_{feature}" in member["fields"] for feature in features)
            for member in members
        ),
        "geometry_columns_present": bool(geometry_columns)
        and all(all(column in member["fields"] for column in geometry_columns) for member in members),
        "finite_geometry_values": bool(geometry_columns)
        and all(
            all(
                _finite(row.get(column)) is not None
                for row in member["rows"].values()
                for column in geometry_columns
            )
            for member in members
        ),
        "candidate_ids_unique": all(not member["duplicate_ids"] for member in members),
        "candidate_rows_present": bool(members) and bool(members[0]["order"]),
    }
    reference_ids = members[0]["order"] if members else []
    checks["candidate_id_sets_match"] = bool(reference_ids) and all(
        set(member["order"]) == set(reference_ids) for member in members
    )

    member_geometry_sha: list[str] = []
    member_prediction_sha: list[str] = []
    if all(checks.values()):
        for member in members:
            member_geometry_sha.append(
                _geometry_digest(
                    member["rows"],
                    reference_ids,
                    geometry_columns,
                    float(args.geometry_quantum),
                )
            )
            member_prediction_sha.append(
                _prediction_digest(member["rows"], reference_ids, features)
            )
        checks["candidate_geometry_sha_match"] = len(set(member_geometry_sha)) == 1
        checks["member_predictions_independent"] = len(set(member_prediction_sha)) == len(
            member_prediction_sha
        )
    else:
        checks["candidate_geometry_sha_match"] = False
        checks["member_predictions_independent"] = False

    rows: list[dict[str, Any]] = []
    if all(checks.values()):
        rows = _combine(members, reference_ids, features)
        checks["finite_combined_predictions"] = _combined_finite(rows, features)
        checks["minimum_candidate_count"] = len(rows) >= int(args.min_candidates)
    else:
        checks["finite_combined_predictions"] = False
        checks["minimum_candidate_count"] = False

    status = "PASS" if all(checks.values()) else "FAIL"
    paths = {
        "candidate_csv": out_dir / "deep_ensemble_candidate_predictions.csv",
        "summary": out_dir / "deep_ensemble_candidate_prediction_summary.json",
        "report": out_dir / "deep_ensemble_candidate_prediction_report.md",
    }
    _write_csv(paths["candidate_csv"], rows)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": DECISION if status == "PASS" else "DO_NOT_USE_ENSEMBLE_PREDICTIONS",
        "member_count": len(member_paths),
        "expected_member_count": int(args.expected_members),
        "candidate_count": len(rows),
        "id_column": args.id_column,
        "feature_columns": features,
        "geometry_columns": geometry_columns,
        "geometry_quantum_um": float(args.geometry_quantum),
        "candidate_id_sha256": _digest_lines(reference_ids) if reference_ids else "",
        "candidate_geometry_sha256": member_geometry_sha[0] if member_geometry_sha else "",
        "members": [
            {
                "path": str(path),
                "sha256": _file_sha(path),
                "row_count": len(member["order"]),
                "geometry_sha256": member_geometry_sha[index] if index < len(member_geometry_sha) else "",
                "prediction_sha256": member_prediction_sha[index] if index < len(member_prediction_sha) else "",
            }
            for index, (path, member) in enumerate(zip(member_paths, members))
        ],
        "checks": checks,
        "artifacts": {name: str(path) for name, path in paths.items()},
        "scientific_boundary": (
            "Ensemble mean and disagreement only prioritize future candidates. "
            "They do not count as EMX labels, accepted samples, uniformity evidence, or model completion."
        ),
    }
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["report"].write_text(_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"candidate_csv={paths['candidate_csv']}")
    print(f"summary={paths['summary']}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--member-csv", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-members", type=int, default=5)
    parser.add_argument("--min-candidates", type=int, default=1)
    parser.add_argument("--id-column", default="candidate_id")
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURES)
    parser.add_argument("--geometry-columns", default="")
    parser.add_argument("--geometry-quantum", type=float, default=1.0e-6)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.expected_members) < 2:
        parser.error("--expected-members must be at least 2")
    if float(args.geometry_quantum) <= 0.0:
        parser.error("--geometry-quantum must be positive")
    return args


def _columns(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _read_member(path: Path, id_column: str) -> dict[str, Any]:
    if not path.is_file():
        return {"fields": [], "rows": {}, "order": [], "duplicate_ids": []}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows: dict[str, dict[str, str]] = {}
        order: list[str] = []
        duplicate_ids: list[str] = []
        for row_index, row in enumerate(reader):
            candidate_id = str(row.get(id_column) or f"row_{row_index:08d}")
            if candidate_id in rows:
                duplicate_ids.append(candidate_id)
                continue
            rows[candidate_id] = dict(row)
            order.append(candidate_id)
    return {"fields": fields, "rows": rows, "order": order, "duplicate_ids": duplicate_ids}


def _infer_geometry_columns(members: list[dict[str, Any]]) -> list[str]:
    if not members:
        return []
    fields = members[0]["fields"]
    return sorted(
        field
        for field in fields
        if field.startswith("geom__") or field.startswith("candidate__geom__")
    )


def _geometry_digest(
    rows: dict[str, dict[str, str]],
    candidate_ids: list[str],
    columns: list[str],
    quantum: float,
) -> str:
    lines = ["columns=" + ",".join(columns), f"quantum={float(quantum).hex()}"]
    for candidate_id in sorted(candidate_ids):
        values = []
        for column in columns:
            value = _finite(rows[candidate_id].get(column))
            if value is None:
                values.append("NONFINITE")
            else:
                values.append(str(int(round(value / quantum))))
        lines.append(candidate_id + "|" + "|".join(values))
    return _digest_lines(lines)


def _prediction_digest(
    rows: dict[str, dict[str, str]],
    candidate_ids: list[str],
    features: list[str],
) -> str:
    lines = ["features=" + ",".join(features)]
    for candidate_id in sorted(candidate_ids):
        values = [_finite(rows[candidate_id].get(f"pred_{feature}")) for feature in features]
        lines.append(
            candidate_id
            + "|"
            + "|".join("NONFINITE" if value is None else float(value).hex() for value in values)
        )
    return _digest_lines(lines)


def _combine(
    members: list[dict[str, Any]],
    candidate_ids: list[str],
    features: list[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        row: dict[str, Any] = dict(members[0]["rows"][candidate_id])
        row["pred_source"] = "deep_ensemble_disagreement_for_candidate_priority_only"
        row["pred_ensemble_members"] = len(members)
        for feature in features:
            values = np.asarray(
                [
                    _finite(member["rows"][candidate_id].get(f"pred_{feature}"))
                    for member in members
                ],
                dtype=float,
            )
            row[f"pred_{feature}"] = float(np.mean(values))
            row[f"pred_uncertainty_{feature}"] = float(np.std(values, ddof=0))
            row[f"pred_member_min_{feature}"] = float(np.min(values))
            row[f"pred_member_max_{feature}"] = float(np.max(values))
        output.append(row)
    return output


def _combined_finite(rows: list[dict[str, Any]], features: list[str]) -> bool:
    for row in rows:
        for feature in features:
            for prefix in ("pred_", "pred_uncertainty_"):
                value = _finite(row.get(prefix + feature))
                if value is None or (prefix == "pred_uncertainty_" and value < 0.0):
                    return False
    return True


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _file_sha(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(str(line).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _report(payload: dict[str, Any]) -> str:
    failed = [name for name, passed in payload["checks"].items() if not passed]
    return "\n".join(
        [
            "# Deep Ensemble Candidate Disagreement",
            "",
            f"- Status: `{payload['overall_status']}`",
            f"- Decision: `{payload['decision']}`",
            f"- Members: `{payload['member_count']}`",
            f"- Candidates: `{payload['candidate_count']}`",
            f"- Geometry SHA256: `{payload['candidate_geometry_sha256']}`",
            f"- Failed checks: `{','.join(failed) if failed else 'none'}`",
            "",
            "## Boundary",
            "",
            payload["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
