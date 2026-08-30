#!/usr/bin/env python3
"""Build exact 56-point four-port S4P QA and feature products."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_s4p_qa import (  # noqa: E402
    Broadband56S4pQaError,
    build_exact56_s4p_qa_products,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate exact-GDS fresh-EMX receipts and build native four-port "
            "5-60 GHz/1 GHz S4P, Z, and physical-feature QA products."
        )
    )
    parser.add_argument("--input-index", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-geometry-count", type=int)
    args = parser.parse_args(argv)
    try:
        result = build_exact56_s4p_qa_products(
            input_index_path=Path(args.input_index).expanduser(),
            out_dir=Path(args.out_dir).expanduser(),
            expected_geometry_count=args.expected_geometry_count,
        )
    except Broadband56S4pQaError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"geometry_count={result['geometry_count']}")
    print(f"geometry_frequency_rows={result['geometry_frequency_rows']}")
    print(f"receipt={result['receipt_path']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
