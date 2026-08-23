#!/usr/bin/env python3
"""Build a stable Touchstone index from completed numeric S-parameter files.

Parallel EMX runs can briefly expose header-only `.s4p` files while a worker is
still writing. This helper creates a standard `evaluations/<id>/emx/emx.s4p`
index using only files that already contain numeric Touchstone data rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FLOAT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?$")


def main() -> int:
    args = _parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    max_count = int(args.max_count)
    min_count = int(args.min_count if args.min_count is not None else max_count)

    if out_dir.exists() and args.clean:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eval_root = out_dir / "evaluations"
    eval_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    skipped = 0
    for path in _discover_touchstones(dataset_dir):
        numeric_rows = _count_numeric_touchstone_rows(path, max_scan_lines=int(args.max_scan_lines))
        if numeric_rows <= 0:
            skipped += 1
            continue
        evaluation = _evaluation_id(path, dataset_dir, len(rows))
        target_dir = eval_root / evaluation / "emx"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if args.copy:
            shutil.copy2(path, target)
        else:
            _safe_symlink(path, target)
        rows.append(
            {
                "index": len(rows),
                "evaluation": evaluation,
                "source_path": str(path),
                "indexed_path": str(target),
                "size_bytes": path.stat().st_size,
                "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "numeric_rows_detected": numeric_rows,
            }
        )
        if len(rows) >= max_count:
            break

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "requested_max_count": max_count,
        "requested_min_count": min_count,
        "indexed_count": len(rows),
        "skipped_nonnumeric_or_incomplete": skipped,
        "status": "PASS" if len(rows) >= min_count else "FAIL",
        "copy_mode": bool(args.copy),
    }
    _write_csv(out_dir / "stable_touchstone_index_manifest.csv", rows)
    (out_dir / "stable_touchstone_index_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (out_dir / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "dataset_kind": "stable_touchstone_index",
                "source_dataset_dir": str(dataset_dir),
                "indexed_count": len(rows),
                "generated_utc": summary["generated_utc"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"status={summary['status']}")
    print(f"indexed_count={len(rows)}")
    print(f"skipped_nonnumeric_or_incomplete={skipped}")
    print(f"out_dir={out_dir}")
    return 0 if summary["status"] == "PASS" or args.no_fail_exit else 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-count", type=int, required=True)
    parser.add_argument("--min-count", type=int)
    parser.add_argument("--max-scan-lines", type=int, default=2000)
    parser.add_argument("--copy", action="store_true", help="Copy files instead of creating symlinks")
    parser.add_argument("--clean", action="store_true", help="Remove out-dir before rebuilding")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args()


def _discover_touchstones(dataset_dir: Path) -> list[Path]:
    patterns = [
        "**/parallel_shards/shard_*/evaluations/*/emx/*.s*p",
        "**/evaluations/*/emx/*.s*p",
        "*.s*p",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(dataset_dir.glob(pattern)):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            if not _is_touchstone_extension(resolved):
                continue
            seen.add(resolved)
            paths.append(resolved)
    return paths


def _is_touchstone_extension(path: Path) -> bool:
    suffix = path.suffix.lower()
    return bool(re.fullmatch(r"\.s\d+p", suffix))


def _count_numeric_touchstone_rows(path: Path, *, max_scan_lines: int) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line_no, raw in enumerate(handle):
            if line_no >= max_scan_lines and count:
                break
            line = raw.split("!", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.replace(",", " ").split()
            numeric_tokens = [token for token in tokens if FLOAT_RE.match(token)]
            if len(numeric_tokens) >= 3:
                count += 1
    return count


def _evaluation_id(path: Path, dataset_dir: Path, index: int) -> str:
    try:
        rel_parts = path.relative_to(dataset_dir).parts
    except ValueError:
        rel_parts = path.parts

    shard = next((part for part in rel_parts if part.startswith("shard_")), "")
    evaluation = ""
    if "evaluations" in rel_parts:
        eval_pos = rel_parts.index("evaluations")
        if eval_pos + 1 < len(rel_parts):
            evaluation = rel_parts[eval_pos + 1]
    if not evaluation:
        evaluation = path.stem
    prefix = f"{shard}__" if shard else ""
    return _sanitize_id(f"{index:06d}__{prefix}{evaluation}")


def _sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value)


def _safe_symlink(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    rel_source = os.path.relpath(source, start=target.parent)
    target.symlink_to(rel_source)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
