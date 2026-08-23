from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "reports/current_validation_status_20260614/build_mars_hex_sync_upload_plan.py"


def test_build_mars_hex_sync_upload_plan(tmp_path: Path) -> None:
    packet = tmp_path / "packet.tgz"
    packet.write_bytes(bytes(range(32)))
    out_sh = tmp_path / "upload.sh"
    out_json = tmp_path / "summary.json"
    out_md = tmp_path / "summary.md"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--packet",
            str(packet),
            "--chunk-size",
            "16",
            "--out-sh",
            str(out_sh),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads(out_json.read_text())
    assert json.loads(proc.stdout)["status"] == "HEX_UPLOAD_PLAN_READY"
    assert summary["packet_size_bytes"] == 32
    assert summary["hex_length"] == 64
    assert summary["chunk_size"] == 16
    assert summary["chunk_count"] == 4

    shell_text = out_sh.read_text()
    assert "EXPECTED_SHA256=" in shell_text
    assert 'test "$ACTUAL_SHA256" = "$EXPECTED_SHA256"' in shell_text
    assert "INSTALL_ON_MARS.sh" in shell_text
    chunks = [
        line.split("'", 2)[1]
        for line in shell_text.splitlines()
        if line.startswith("printf '")
    ]
    assert chunks
    assert all(set(chunk) <= set("0123456789abcdef") for chunk in chunks)
    assert "".join(chunks) == packet.read_bytes().hex()

    assert "MARS hex sync upload plan" in out_md.read_text()
