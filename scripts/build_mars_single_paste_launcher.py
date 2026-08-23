#!/usr/bin/env python3
"""Build a one-file Guacamole paste launcher for a MARS sync packet."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path


DEFAULT_WORK_DIR = "/shared/research/researcher/codex_next_gen_s8p_ssh_20260620"
DEFAULT_PROJECT = "/shared/research/researcher/rfic-transformer-inverse-design"
DEFAULT_PACKET = "next_gen_s8p_mars_sync_packet_20260626_final_candidate_gate.tar.gz"
DEFAULT_SYNC_DIR = "next_gen_s8p_mars_sync_packet_20260626_final_candidate_gate"
DEFAULT_OUT = "PASTE_MARS_FINAL_CANDIDATE_GATE_20_PILOT_20260626.sh"
DEFAULT_LAUNCH_SCRIPT = "MARS_S8P_20_AFTER_UNLOCK_20260626.sh"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    packet = Path(args.packet).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    bootstrap = Path(args.bootstrap).expanduser().resolve() if args.bootstrap else None
    if not packet.is_file():
        raise SystemExit(f"Packet file not found: {packet}")
    packet_bytes = packet.read_bytes()
    packet_sha = _sha256_bytes(packet_bytes)
    bootstrap_sha = _sha256_bytes(bootstrap.read_bytes()) if bootstrap and bootstrap.is_file() else ""
    out_path.write_text(
        _render_script(
            packet_name=args.packet_name or packet.name,
            payload=base64.b64encode(packet_bytes).decode("ascii"),
            packet_sha=packet_sha,
            bootstrap_sha=bootstrap_sha,
            work_dir=args.work_dir,
            project=args.project,
            sync_dir=args.sync_dir,
            launch_script=args.launch_script,
        ),
        encoding="utf-8",
    )
    out_path.chmod(0o755)
    print(f"launcher={out_path}")
    print(f"packet_sha256={packet_sha}")
    if bootstrap_sha:
        print(f"bootstrap_sha256={bootstrap_sha}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", default=str(default_project_root / DEFAULT_PACKET))
    parser.add_argument("--packet-name")
    parser.add_argument("--bootstrap", default=str(default_project_root / f"{DEFAULT_SYNC_DIR}_BOOTSTRAP.sh"))
    parser.add_argument("--out", default=str(default_project_root / DEFAULT_OUT))
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--sync-dir", default=DEFAULT_SYNC_DIR)
    parser.add_argument("--launch-script", default=DEFAULT_LAUNCH_SCRIPT)
    return parser.parse_args(argv)


def _render_script(
    *,
    packet_name: str,
    payload: str,
    packet_sha: str,
    bootstrap_sha: str,
    work_dir: str,
    project: str,
    sync_dir: str,
    launch_script: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Paste this whole file into a MARS/Guacamole terminal.
# It restores the selected sync packet, installs it into the repo, and starts
# the configured MARS launcher script.

WORK_DIR="${{WORK_DIR:-{work_dir}}}"
PROJECT="${{PROJECT:-{project}}}"
PACKET="{packet_name}"
EXPECTED_SHA="{packet_sha}"
EXPECTED_BOOTSTRAP_SHA="{bootstrap_sha}"

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
cat > "${{PACKET}}.b64" <<'CODEX_FINAL_CANDIDATE_GATE_B64'
{payload}
CODEX_FINAL_CANDIDATE_GATE_B64

if ! base64 -d < "${{PACKET}}.b64" > "$PACKET" 2>/dev/null; then
  base64 -D -i "${{PACKET}}.b64" -o "$PACKET"
fi

if command -v sha256sum >/dev/null 2>&1; then
  printf "%s  %s\\n" "$EXPECTED_SHA" "$PACKET" | sha256sum -c -
else
  printf "%s  %s\\n" "$EXPECTED_SHA" "$PACKET" | shasum -a 256 -c -
fi

rm -rf {sync_dir}
tar -xzf "$PACKET"
if command -v sha256sum >/dev/null 2>&1; then
  (cd {sync_dir} && sha256sum -c SHA256SUMS.txt)
else
  (cd {sync_dir} && shasum -a 256 -c SHA256SUMS.txt)
fi

PROJECT="$PROJECT" bash {sync_dir}/INSTALL_ON_MARS.sh
cd "$PROJECT"
chmod +x {launch_script}
bash {launch_script}

echo "CODEX_FINAL_CANDIDATE_GATE_20_PILOT_DONE"
echo "WORK_DIR=$WORK_DIR"
echo "PROJECT=$PROJECT"
echo "LAUNCH_SCRIPT={launch_script}"
echo "EXPECTED_56PT_RETURN=$WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.tar.gz"
echo "EXPECTED_56PT_RETURN_SHA256=$WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.tar.gz.sha256"
echo "EXPECTED_56PT_RETURN_INVENTORY=$WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.inventory.json"
echo "EXPECTED_56PT_RETURN_VERIFY=$WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest_verify_summary.json"
"""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
