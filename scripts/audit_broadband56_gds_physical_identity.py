#!/usr/bin/env python3
"""Audit direct-layout versus Cadence GDS identity for any positive batch.

This command is read-only with respect to its inputs and writes one new
no-clobber evidence directory. It never invokes Cadence, Calibre, EMX, a queue,
or a supervisor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (  # noqa: E402
    AUDIT_SCHEMA,
    GdsIdentityError,
    audit_gds_physical_identity,
)


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    try:
        result = audit_gds_physical_identity(
            candidate_csv=Path(args.candidate_csv),
            dataset_dir=Path(args.dataset_dir),
            input_index_csv=Path(args.input_index_csv),
            out_dir=out_dir,
            expected_count=args.expected_count,
            expected_candidate_sha256=args.expected_candidate_sha256,
            expected_dataset_rows_sha256=args.expected_dataset_rows_sha256,
            expected_index_sha256=args.expected_index_sha256,
        )
    except (GdsIdentityError, OSError, json.JSONDecodeError) as exc:
        _write_failure_once(out_dir, exc)
        print("overall_status=FAIL")
        print(f"error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"overall_status={result['overall_status']}")
    print(f"decision={result['decision']}")
    print(f"summary={result['summary_path']}")
    print("simulator_action_taken=false")
    return 0 if result["overall_status"] == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--input-index-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--expected-dataset-rows-sha256", required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    args = parser.parse_args(argv)
    if args.expected_count < 1:
        parser.error("--expected-count must be positive")
    for field in (
        "expected_candidate_sha256",
        "expected_dataset_rows_sha256",
        "expected_index_sha256",
    ):
        value = str(getattr(args, field) or "").strip().lower()
        if SHA256_PATTERN.fullmatch(value) is None:
            parser.error(f"--{field.replace('_', '-')} must be SHA-256")
        setattr(args, field, value)
    return args


def _write_failure_once(out_dir: Path, exc: Exception) -> None:
    if out_dir.exists():
        return
    try:
        out_dir.mkdir(parents=True, mode=0o700)
        path = out_dir / "GDS_PHYSICAL_IDENTITY_AUDIT_SUMMARY.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema": AUDIT_SCHEMA,
                    "overall_status": "FAIL",
                    "decision": "DO_NOT_RUN_CALIBRE_GDS_IDENTITY_PREFLIGHT_FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "automatic_calibre_authorized": False,
                    "automatic_emx_authorized": False,
                    "simulator_action_taken": False,
                },
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
    except OSError:
        return


if __name__ == "__main__":
    raise SystemExit(main())
