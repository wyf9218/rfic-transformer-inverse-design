#!/usr/bin/env python3
"""Run the reproducible public test suite without private site evidence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def entries(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ignored = entries(root / "tests" / "site_integration_tests.txt")
    deselected = entries(root / "tests" / "public_ci_deselected_nodes.txt")
    command = [sys.executable, "-m", "pytest", "-q"]
    command.extend(f"--ignore={item}" for item in ignored)
    command.extend(f"--deselect={item}" for item in deselected)
    command.extend(sys.argv[1:])
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
