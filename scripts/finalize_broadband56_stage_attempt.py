#!/usr/bin/env python3
"""Finalize one bounded broadband56 attempt without launching a simulator.

The command validates the current shard's terminal partition and exact 56-row
feature grain.  A shortfall writes a SHA-linked nonterminal progress receipt.
An exact stage target writes cumulative CSV inputs for the terminal raw-product
and checkpoint roles.  Overshoot and identity drift fail closed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
    STAGE_BY_NAME,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    PRODUCTION_BACKEND_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (  # noqa: E402
    ATTEMPT_FAILURE_ACCOUNTING_FIELDS,
    STAGE_PROGRESS_ARTIFACT_FIELDS,
    STAGE_PROGRESS_DECISION,
    STAGE_PROGRESS_SCHEMA,
    STAGE_PROGRESS_SAFEGUARDS,
    STAGE_PROGRESS_STATUS,
    STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA,
    STAGE_ATTEMPT_TARGET_REACHED_DECISION,
    accepted_after_progress,
    validate_stage_progress_chain,
)


ROLE_RECEIPT_SCHEMA = STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA
ROLE_RECEIPT_NAME = "STAGE_ATTEMPT_FINALIZER_RECEIPT.json"
PROGRESS_RECEIPT_NAME = "STAGE_PROGRESS_RECEIPT.json"
ATTEMPT_ARTIFACT_DIR_NAME = "attempt_artifacts"
CUMULATIVE_DIR_NAME = "cumulative_stage_inputs"
CSV_ARTIFACT_FIELDS = STAGE_PROGRESS_ARTIFACT_FIELDS[:-1]
MINIMUM_COLUMNS = {
    "attempt_ledger": {"attempt_id", "geometry_sha256", "terminal_stage"},
    "accepted_geometry_increment": {"geometry_sha256"},
    "rejected_geometry_increment": {"geometry_sha256"},
    "exact_gds_emx_receipt_index": {"candidate_id_sha256", "geometry_sha256"},
    "s4p_artifact_index": {"geometry_sha256"},
    "long_features": {"geometry_sha256", "frequency_hz"},
}


class StageAttemptFinalizationError(RuntimeError):
    """Raised when an attempt cannot support progress or exact completion."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        result = finalize_stage_attempt(args, out_dir=out_dir)
    except StageAttemptFinalizationError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"decision={result['decision']}")
    print(f"accepted_after={result['accepted_after']}")
    print(f"receipt={out_dir / ROLE_RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--attempt-ledger", required=True)
    parser.add_argument("--accepted-geometry-increment", required=True)
    parser.add_argument("--rejected-geometry-increment", required=True)
    parser.add_argument("--exact-gds-emx-receipt-index", required=True)
    parser.add_argument("--s4p-artifact-index", required=True)
    parser.add_argument("--long-features", required=True)
    parser.add_argument("--failure-funnel", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--simulator-action-taken", action="store_true")
    return parser.parse_args(argv)


def finalize_stage_attempt(
    args: argparse.Namespace,
    *,
    out_dir: Path,
) -> dict[str, Any]:
    stage = str(args.stage).upper()
    spec = STAGE_BY_NAME.get(stage)
    if spec is None:
        raise StageAttemptFinalizationError(f"unknown stage: {stage}")
    campaign_root = Path(args.campaign_root).expanduser().resolve()
    backend_path = _required_file(Path(args.backend_identity_manifest), "backend manifest")
    authorization_path = _required_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    backend_sha256 = _sha256(backend_path)
    authorization_sha256 = _sha256(authorization_path)
    artifacts = {
        field: _required_file(Path(getattr(args, field)), field)
        for field in STAGE_PROGRESS_ARTIFACT_FIELDS
    }

    prior_records = _progress_records(campaign_root, stage=stage)
    base_accepted = _stage_base_accepted(stage)
    progress_errors = validate_stage_progress_chain(
        prior_records,
        stage=stage,
        base_accepted=base_accepted,
        backend_manifest_sha256=backend_sha256,
        authorization_receipt_sha256=authorization_sha256,
        verify_artifacts=True,
    )
    if progress_errors:
        raise StageAttemptFinalizationError(
            "prior progress chain failed validation: " + "; ".join(progress_errors[:10])
        )
    accepted_before = accepted_after_progress(
        prior_records,
        base_accepted=base_accepted,
    )
    attempt_index = len(prior_records) + 1

    tables = {
        field: _read_csv(artifacts[field], field)
        for field in CSV_ARTIFACT_FIELDS
    }
    funnel = _read_failure_funnel(artifacts["failure_funnel"])
    raw_count = len(tables["attempt_ledger"][1])
    accepted_count = len(tables["accepted_geometry_increment"][1])
    rejected_count = len(tables["rejected_geometry_increment"][1])
    if raw_count <= 0:
        raise StageAttemptFinalizationError("attempt ledger must contain at least one terminal row")
    if accepted_count + rejected_count != raw_count:
        raise StageAttemptFinalizationError(
            "accepted and rejected increments do not partition the attempt ledger"
        )
    if len(tables["exact_gds_emx_receipt_index"][1]) != accepted_count:
        raise StageAttemptFinalizationError("exact GDS/EMX index count mismatch")
    if len(tables["s4p_artifact_index"][1]) != accepted_count:
        raise StageAttemptFinalizationError("S4P artifact index count mismatch")
    if len(tables["long_features"][1]) != accepted_count * len(FREQUENCY_GRID_HZ):
        raise StageAttemptFinalizationError("long-feature row count is not accepted_count times 56")
    _validate_geometry_identity_sets(tables)
    _validate_failure_funnel(
        funnel,
        raw_count=raw_count,
        accepted_count=accepted_count,
    )

    accepted_after = accepted_before + accepted_count
    if accepted_after > spec.cumulative_target:
        raise StageAttemptFinalizationError(
            f"accepted count overshoots {stage}: {accepted_after}>{spec.cumulative_target}"
        )

    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise StageAttemptFinalizationError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        staged_artifacts, artifact_records = _stage_attempt_artifacts(
            staging / ATTEMPT_ARTIFACT_DIR_NAME,
            published_dir=out_dir / ATTEMPT_ARTIFACT_DIR_NAME,
            source_artifacts=artifacts,
        )
        if accepted_after < spec.cumulative_target:
            progress_path = staging / PROGRESS_RECEIPT_NAME
            progress = _progress_receipt(
                stage=stage,
                attempt_index=attempt_index,
                accepted_before=accepted_before,
                accepted_count=accepted_count,
                raw_count=raw_count,
                funnel=funnel,
                artifact_records=artifact_records,
                prior_records=prior_records,
                backend_sha256=backend_sha256,
                authorization_sha256=authorization_sha256,
                simulator_action_taken=bool(args.simulator_action_taken),
            )
            _write_json(progress_path, progress)
            cumulative_outputs = None
            decision = STAGE_PROGRESS_DECISION
        else:
            cumulative_outputs = _write_cumulative_inputs(
                staging / CUMULATIVE_DIR_NAME,
                published_out_dir=out_dir / CUMULATIVE_DIR_NAME,
                prior_records=prior_records,
                current_artifacts=staged_artifacts,
            )
            progress_path = None
            decision = STAGE_ATTEMPT_TARGET_REACHED_DECISION

        role_receipt = {
            "schema": ROLE_RECEIPT_SCHEMA,
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": decision,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "backend_id": PRODUCTION_BACKEND_ID,
            "stage": stage,
            "attempt_index": attempt_index,
            "accepted_before": accepted_before,
            "accepted_this_attempt": accepted_count,
            "accepted_after": accepted_after,
            "cumulative_target": spec.cumulative_target,
            "raw_candidates_this_attempt": raw_count,
            "simulator_action_taken": bool(args.simulator_action_taken),
            "attempt_artifacts": artifact_records,
            "progress_receipt": (
                _published_file_record(
                    progress_path,
                    out_dir / PROGRESS_RECEIPT_NAME,
                )
                if progress_path
                else None
            ),
            "cumulative_stage_inputs": cumulative_outputs,
            "simulator_invoked_by_finalizer": False,
        }
        _write_json(staging / ROLE_RECEIPT_NAME, role_receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return role_receipt


def _progress_receipt(
    *,
    stage: str,
    attempt_index: int,
    accepted_before: int,
    accepted_count: int,
    raw_count: int,
    funnel: Mapping[str, int],
    artifact_records: Mapping[str, Mapping[str, Any]],
    prior_records: Sequence[tuple[Path, Mapping[str, Any]]],
    backend_sha256: str,
    authorization_sha256: str,
    simulator_action_taken: bool,
) -> dict[str, Any]:
    target = STAGE_BY_NAME[stage].cumulative_target
    accepted_after = accepted_before + accepted_count
    return {
        "schema": STAGE_PROGRESS_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": STAGE_PROGRESS_STATUS,
        "decision": STAGE_PROGRESS_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "attempt_index": attempt_index,
        "cumulative_target": target,
        "accepted_before": accepted_before,
        "accepted_this_attempt": accepted_count,
        "accepted_after": accepted_after,
        "remaining_after": target - accepted_after,
        "raw_candidates_this_attempt": raw_count,
        "terminal_attempts_this_attempt": raw_count,
        "prior_progress_receipt_sha256": (
            _sha256(prior_records[-1][0]) if prior_records else None
        ),
        "backend_identity_manifest_sha256": backend_sha256,
        "full_campaign_authorization_receipt_sha256": authorization_sha256,
        "safeguards": dict(STAGE_PROGRESS_SAFEGUARDS),
        "failure_accounting": dict(funnel),
        "artifacts": {field: dict(record) for field, record in artifact_records.items()},
        "simulator_action_taken": simulator_action_taken,
        "stage_pass_receipt_created": False,
        "evidence_preserved": True,
    }


def _write_cumulative_inputs(
    out_dir: Path,
    *,
    published_out_dir: Path,
    prior_records: Sequence[tuple[Path, Mapping[str, Any]]],
    current_artifacts: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    out_dir.mkdir()
    sources: dict[str, list[Path]] = {field: [] for field in CSV_ARTIFACT_FIELDS}
    funnels: list[Path] = []
    for _, receipt in prior_records:
        records = receipt.get("artifacts")
        if not isinstance(records, Mapping):
            raise StageAttemptFinalizationError("prior progress receipt lacks artifacts")
        for field in CSV_ARTIFACT_FIELDS:
            sources[field].append(Path(str(records[field]["path"])).resolve())
        funnels.append(Path(str(records["failure_funnel"]["path"])).resolve())
    for field in CSV_ARTIFACT_FIELDS:
        sources[field].append(current_artifacts[field])
    funnels.append(current_artifacts["failure_funnel"])

    outputs: dict[str, dict[str, Any]] = {}
    for field, paths in sources.items():
        destination = out_dir / f"{field}.csv"
        _merge_csv(paths, destination)
        outputs[field] = _published_file_record(
            destination,
            published_out_dir / destination.name,
        )
    combined = Counter({field: 0 for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS})
    for path in funnels:
        combined.update(_read_failure_funnel(path))
    funnel_path = out_dir / "failure_funnel.csv"
    with funnel_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stage", "count"])
        writer.writeheader()
        for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS:
            writer.writerow({"stage": field, "count": combined[field]})
    outputs["failure_funnel"] = _published_file_record(
        funnel_path,
        published_out_dir / funnel_path.name,
    )
    return outputs


def _stage_attempt_artifacts(
    out_dir: Path,
    *,
    published_dir: Path,
    source_artifacts: Mapping[str, Path],
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    """Preserve each attempt input beneath its immutable no-clobber result."""

    out_dir.mkdir()
    staged: dict[str, Path] = {}
    records: dict[str, dict[str, Any]] = {}
    for field in STAGE_PROGRESS_ARTIFACT_FIELDS:
        source = _required_file(source_artifacts[field], field)
        destination = out_dir / f"{field}.csv"
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        staged[field] = destination
        records[field] = _published_file_record(
            destination,
            published_dir / destination.name,
        )
    return staged, records


def _progress_records(
    campaign_root: Path,
    *,
    stage: str,
) -> list[tuple[Path, dict[str, Any]]]:
    records = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_PROGRESS_RECEIPT.json")):
        value = _read_json(path, "stage progress receipt")
        if str(value.get("stage") or "").upper() == stage:
            records.append((path.resolve(), value))
    return records


def _stage_base_accepted(stage: str) -> int:
    names = [item.name for item in STAGES]
    index = names.index(stage)
    return 0 if index == 0 else STAGES[index - 1].cumulative_target


def _read_csv(path: Path, label: str) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        required = MINIMUM_COLUMNS[label]
        if not required.issubset(fields):
            raise StageAttemptFinalizationError(
                f"{label} lacks columns: {sorted(required - set(fields))}"
            )
        rows = [{key: str(value or "").strip() for key, value in row.items()} for row in reader]
    return fields, rows


def _read_failure_funnel(path: Path) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"stage", "count"}.issubset(reader.fieldnames or []):
            raise StageAttemptFinalizationError("failure_funnel lacks stage,count columns")
        counts: dict[str, int] = {}
        for line, row in enumerate(reader, start=2):
            stage = str(row.get("stage") or "").strip()
            if stage in counts:
                raise StageAttemptFinalizationError(
                    f"failure_funnel repeats stage {stage!r} at line {line}"
                )
            try:
                count = int(str(row.get("count") or "").strip())
            except ValueError as exc:
                raise StageAttemptFinalizationError(
                    f"failure_funnel count is invalid at line {line}"
                ) from exc
            if count < 0:
                raise StageAttemptFinalizationError("failure_funnel count is negative")
            counts[stage] = count
    if set(counts) != set(ATTEMPT_FAILURE_ACCOUNTING_FIELDS):
        raise StageAttemptFinalizationError("failure_funnel fields mismatch")
    return counts


def _validate_failure_funnel(
    funnel: Mapping[str, int],
    *,
    raw_count: int,
    accepted_count: int,
) -> None:
    if funnel["raw_geometry_candidates"] != raw_count:
        raise StageAttemptFinalizationError("failure_funnel raw count mismatch")
    if funnel["accepted_geometries"] != accepted_count:
        raise StageAttemptFinalizationError("failure_funnel accepted count mismatch")
    if sum(
        count for field, count in funnel.items() if field != "raw_geometry_candidates"
    ) != raw_count:
        raise StageAttemptFinalizationError("failure_funnel does not partition raw candidates")


def _validate_geometry_identity_sets(
    tables: Mapping[str, tuple[list[str], list[dict[str, str]]]],
) -> None:
    accepted = [row["geometry_sha256"] for row in tables["accepted_geometry_increment"][1]]
    rejected = [row["geometry_sha256"] for row in tables["rejected_geometry_increment"][1]]
    if len(set(accepted)) != len(accepted):
        raise StageAttemptFinalizationError("accepted increment contains duplicate geometry")
    if set(accepted) & set(rejected):
        raise StageAttemptFinalizationError("accepted and rejected geometry sets overlap")
    for field in ("exact_gds_emx_receipt_index", "s4p_artifact_index"):
        values = [row["geometry_sha256"] for row in tables[field][1]]
        if set(values) != set(accepted) or len(values) != len(accepted):
            raise StageAttemptFinalizationError(f"{field} geometry set mismatch")
    feature_counts = Counter(row["geometry_sha256"] for row in tables["long_features"][1])
    if set(feature_counts) != set(accepted) or any(
        count != len(FREQUENCY_GRID_HZ) for count in feature_counts.values()
    ):
        raise StageAttemptFinalizationError("long-feature geometry grain mismatch")


def _merge_csv(paths: Sequence[Path], destination: Path) -> None:
    fields: list[str] | None = None
    with destination.open("w", newline="", encoding="utf-8") as target:
        writer: csv.DictWriter[str] | None = None
        for path in paths:
            with path.open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                current = list(reader.fieldnames or [])
                if fields is None:
                    fields = current
                    writer = csv.DictWriter(target, fieldnames=fields)
                    writer.writeheader()
                elif current != fields:
                    raise StageAttemptFinalizationError(
                        f"cumulative CSV header mismatch: {path}"
                    )
                assert writer is not None
                writer.writerows(reader)


def _required_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise StageAttemptFinalizationError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageAttemptFinalizationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageAttemptFinalizationError(f"{label} is not an object")
    return value


def _published_file_record(staged_path: Path, published_path: Path) -> dict[str, Any]:
    resolved = _required_file(staged_path, "identity file")
    return {
        "path": str(published_path.expanduser().resolve()),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_sha256s(root: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
