#!/usr/bin/env python3
"""Build a deterministic SHA-256 manifest of all publishable repository files."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


OUTPUT_NAME = "CODE_SNAPSHOT_SHA256.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publishable_paths(root: Path) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8")
        if relative == OUTPUT_NAME:
            continue
        path = root / relative
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    rows = [
        "# SHA-256 manifest for the sanitized research-code snapshot.",
        "# Format: <sha256>  <repository-relative path>",
    ]
    paths = publishable_paths(root)
    rows.extend(
        f"{sha256(path)}  {path.relative_to(root).as_posix()}" for path in paths
    )
    output = root / OUTPUT_NAME
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {output} with {len(paths)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
