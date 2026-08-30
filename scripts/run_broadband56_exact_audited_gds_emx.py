#!/usr/bin/env python3
"""Run fresh EMX on one exact zero-blocking Calibre-audited GDS."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx as exact_gds_emx


SCRIPT_PATH = Path(__file__).absolute()
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _require_file_sha256(
            SCRIPT_PATH,
            args.expected_runner_sha256,
            "exact audited-GDS runner",
        )
        _require_file_sha256(
            Path(exact_gds_emx.__file__).absolute(),
            args.expected_module_sha256,
            "exact audited-GDS module",
        )
        result = exact_gds_emx.run_exact_audited_gds_fresh_emx(
            config_path=Path(args.config),
            expected_config_sha256=args.expected_config_sha256,
            gds_path=Path(args.gds),
            expected_gds_sha256=args.expected_gds_sha256,
            manifest_path=Path(args.manifest),
            expected_manifest_sha256=args.expected_manifest_sha256,
            calibre_receipt_path=Path(args.calibre_receipt),
            expected_calibre_receipt_sha256=args.expected_calibre_receipt_sha256,
            full_campaign_receipt_path=Path(args.full_campaign_receipt),
            expected_full_campaign_receipt_sha256=(
                args.expected_full_campaign_receipt_sha256
            ),
            candidate_id_sha256=args.candidate_id_sha256,
            geometry_identity_sha256=args.geometry_identity_sha256,
            out_dir=Path(args.out_dir),
        )
    except Exception as exc:  # noqa: BLE001 - CLI must retain exact failure.
        print("overall_status=FAIL")
        print(f"error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"receipt={result['receipt_path']}")
    print(f"touchstone={result['touchstone_path']}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--gds", required=True)
    parser.add_argument("--expected-gds-sha256", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--calibre-receipt", required=True)
    parser.add_argument("--expected-calibre-receipt-sha256", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--expected-full-campaign-receipt-sha256", required=True)
    parser.add_argument("--candidate-id-sha256", required=True)
    parser.add_argument("--geometry-identity-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-runner-sha256", required=True)
    parser.add_argument("--expected-module-sha256", required=True)
    args = parser.parse_args(argv)
    for name in (
        "expected_config_sha256",
        "expected_gds_sha256",
        "expected_manifest_sha256",
        "expected_calibre_receipt_sha256",
        "expected_full_campaign_receipt_sha256",
        "candidate_id_sha256",
        "geometry_identity_sha256",
        "expected_runner_sha256",
        "expected_module_sha256",
    ):
        value = str(getattr(args, name) or "").strip().lower()
        if SHA256_PATTERN.fullmatch(value) is None:
            parser.error(f"--{name.replace('_', '-')} must be a SHA-256 digest")
        setattr(args, name, value)
    return args


def _require_file_sha256(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise exact_gds_emx.ExactAuditedGdsEmxError(f"{label} is missing")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise exact_gds_emx.ExactAuditedGdsEmxError(
            f"{label} SHA-256 mismatch"
        )


if __name__ == "__main__":
    raise SystemExit(main())
