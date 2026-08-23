#!/usr/bin/env python3
"""Build chunked paste scripts for launching the MARS S8P 20-pilot.

The one-file Guacamole paste launcher is convenient, but several-megabyte
clipboard pastes are fragile. This helper splits the sync packet base64 payload
into small shell snippets that can be pasted one-by-one, then verified before
the final script installs the packet and starts the real EMX pilot.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
from pathlib import Path


DEFAULT_WORK_DIR = "/shared/research/researcher/codex_next_gen_s8p_ssh_20260620"
DEFAULT_PROJECT = "/shared/research/researcher/rfic-transformer-inverse-design"
DEFAULT_PACKET = "next_gen_s8p_mars_sync_packet_20260626_final_candidate_gate.tar.gz"
DEFAULT_SYNC_DIR = "next_gen_s8p_mars_sync_packet_20260626_final_candidate_gate"
DEFAULT_LAUNCH_SCRIPT = "MARS_S8P_20_AFTER_UNLOCK_20260626.sh"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    packet = Path(args.packet).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not packet.is_file():
        raise SystemExit(f"Packet file not found: {packet}")
    if out_dir.exists() and args.force:
        shutil.rmtree(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"Output directory is not empty; pass --force to replace: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    packet_name = args.packet_name or packet.name
    packet_sha = _sha256_bytes(packet.read_bytes())
    payload = base64.b64encode(packet.read_bytes()).decode("ascii")
    chunks = [payload[i : i + args.chunk_size] for i in range(0, len(payload), args.chunk_size)]
    if not chunks:
        raise SystemExit("No payload chunks were produced")

    init_path = out_dir / "00_INIT_MARS_FINAL_CANDIDATE_GATE_UPLOAD.sh"
    verify_path = out_dir / "98_VERIFY_REASSEMBLE_ONLY.sh"
    finalize_path = out_dir / "99_FINALIZE_INSTALL_AND_RUN_20_PILOT.sh"
    runbook_path = out_dir / "README_CHUNKED_MARS_PASTE_20260626_CN.md"
    manifest_path = out_dir / "chunked_mars_paste_manifest.json"

    init_path.write_text(
        _render_init_script(
            work_dir=args.work_dir,
            packet_name=packet_name,
            packet_sha=packet_sha,
            chunk_count=len(chunks),
        ),
        encoding="utf-8",
    )
    _chmod_executable(init_path)

    part_paths = []
    width = max(3, len(str(len(chunks))))
    for index, chunk in enumerate(chunks, start=1):
        part_name = f"{index:0{width}d}"
        part_path = out_dir / f"PART_{part_name}_OF_{len(chunks):0{width}d}.sh"
        part_path.write_text(
            _render_part_script(
                work_dir=args.work_dir,
                packet_name=packet_name,
                chunk=chunk,
                index=index,
                count=len(chunks),
                width=width,
            ),
            encoding="utf-8",
        )
        _chmod_executable(part_path)
        part_paths.append(part_path)

    verify_path.write_text(
        _render_reassemble_script(
            work_dir=args.work_dir,
            packet_name=packet_name,
            packet_sha=packet_sha,
            chunk_count=len(chunks),
            width=width,
            mode="verify",
            project=args.project,
            sync_dir=args.sync_dir,
            launch_script=args.launch_script,
        ),
        encoding="utf-8",
    )
    finalize_path.write_text(
        _render_reassemble_script(
            work_dir=args.work_dir,
            packet_name=packet_name,
            packet_sha=packet_sha,
            chunk_count=len(chunks),
            width=width,
            mode="finalize",
            project=args.project,
            sync_dir=args.sync_dir,
            launch_script=args.launch_script,
        ),
        encoding="utf-8",
    )
    _chmod_executable(verify_path)
    _chmod_executable(finalize_path)

    manifest = {
        "status": "PASS",
        "decision": "CHUNKED_MARS_PASTE_LAUNCHER_READY",
        "packet": str(packet),
        "packet_name": packet_name,
        "packet_sha256": packet_sha,
        "out_dir": str(out_dir),
        "chunk_size": args.chunk_size,
        "chunk_count": len(chunks),
        "work_dir": args.work_dir,
        "project": args.project,
        "sync_dir": args.sync_dir,
        "launch_script": args.launch_script,
        "scripts": [p.name for p in [init_path, *part_paths, verify_path, finalize_path]],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    runbook_path.write_text(_render_runbook(manifest), encoding="utf-8")

    print(f"status={manifest['status']}")
    print(f"decision={manifest['decision']}")
    print(f"out_dir={out_dir}")
    print(f"packet_sha256={packet_sha}")
    print(f"chunk_count={len(chunks)}")
    print(f"chunk_size={args.chunk_size}")
    print(f"manifest={manifest_path}")
    print(f"runbook={runbook_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_project_root = Path(__file__).resolve().parents[2]
    default_packet_path = default_project_root / DEFAULT_PACKET
    default_out_dir = default_project_root / "mars_final_candidate_gate_chunked_paste_20260626"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", default=str(default_packet_path))
    parser.add_argument("--packet-name")
    parser.add_argument("--out-dir", default=str(default_out_dir))
    parser.add_argument("--chunk-size", type=int, default=80_000)
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--sync-dir", default=DEFAULT_SYNC_DIR)
    parser.add_argument("--launch-script", default=DEFAULT_LAUNCH_SCRIPT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.chunk_size < 1024:
        raise SystemExit("--chunk-size must be at least 1024")
    return args


def _render_init_script(*, work_dir: str, packet_name: str, packet_sha: str, chunk_count: int) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${{WORK_DIR:-{work_dir}}}"
PACKET="{packet_name}"
EXPECTED_SHA="{packet_sha}"
EXPECTED_PARTS="{chunk_count}"

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
rm -f "${{PACKET}}.b64" "${{PACKET}}.b64".part* "$PACKET"
printf '%s\\n' "$EXPECTED_SHA  $PACKET" > "${{PACKET}}.sha256"
echo "CODEX_CHUNKED_INIT_READY"
echo "WORK_DIR=$WORK_DIR"
echo "PACKET=$PACKET"
echo "EXPECTED_PARTS=$EXPECTED_PARTS"
"""


def _render_part_script(
    *,
    work_dir: str,
    packet_name: str,
    chunk: str,
    index: int,
    count: int,
    width: int,
) -> str:
    part_name = f"{index:0{width}d}"
    return f"""#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${{WORK_DIR:-{work_dir}}}"
PACKET="{packet_name}"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
cat > "${{PACKET}}.b64.part{part_name}" <<'CODEX_B64_PART_{part_name}'
{chunk}
CODEX_B64_PART_{part_name}
echo "CODEX_CHUNKED_PART_WRITTEN {part_name}/{count:0{width}d} $(wc -c < "${{PACKET}}.b64.part{part_name}") bytes"
"""


def _render_reassemble_script(
    *,
    work_dir: str,
    packet_name: str,
    packet_sha: str,
    chunk_count: int,
    width: int,
    mode: str,
    project: str,
    sync_dir: str,
    launch_script: str,
) -> str:
    if mode not in {"verify", "finalize"}:
        raise ValueError(mode)
    action = ""
    done_message = "CODEX_CHUNKED_REASSEMBLE_VERIFY_PASS"
    if mode == "finalize":
        done_message = "CODEX_FINAL_CANDIDATE_GATE_20_PILOT_STARTED"
        action = f"""
rm -rf {sync_dir}
tar -xzf "$PACKET"
if command -v sha256sum >/dev/null 2>&1; then
  (cd {sync_dir} && sha256sum -c SHA256SUMS.txt)
else
  (cd {sync_dir} && shasum -a 256 -c SHA256SUMS.txt)
fi

PROJECT="${{PROJECT:-{project}}}"
PROJECT="$PROJECT" bash {sync_dir}/INSTALL_ON_MARS.sh
cd "$PROJECT"
chmod +x {launch_script}
bash {launch_script}
"""
    return f"""#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${{WORK_DIR:-{work_dir}}}"
PACKET="{packet_name}"
EXPECTED_SHA="{packet_sha}"
EXPECTED_PARTS="{chunk_count}"

cd "$WORK_DIR"
rm -f "${{PACKET}}.b64"
for i in $(seq -f "%0{width}g" 1 "$EXPECTED_PARTS"); do
  part="${{PACKET}}.b64.part${{i}}"
  if [ ! -s "$part" ]; then
    echo "Missing or empty part: $part" >&2
    exit 11
  fi
  cat "$part" >> "${{PACKET}}.b64"
done

if ! base64 -d < "${{PACKET}}.b64" > "$PACKET" 2>/dev/null; then
  base64 -D -i "${{PACKET}}.b64" -o "$PACKET"
fi

if command -v sha256sum >/dev/null 2>&1; then
  printf '%s  %s\\n' "$EXPECTED_SHA" "$PACKET" | sha256sum -c -
else
  printf '%s  %s\\n' "$EXPECTED_SHA" "$PACKET" | shasum -a 256 -c -
fi
{action}
echo "{done_message}"
echo "WORK_DIR=$WORK_DIR"
echo "PACKET=$PACKET"
"""


def _render_runbook(manifest: dict[str, object]) -> str:
    scripts = manifest["scripts"]
    assert isinstance(scripts, list)
    parts = [name for name in scripts if str(name).startswith("PART_")]
    launch_script = str(manifest.get("launch_script", DEFAULT_LAUNCH_SCRIPT))
    return "\n".join(
        [
            "# MARS 分片粘贴启动说明",
            "",
            "用途：在 Guacamole/MARS terminal 不能稳定粘贴单文件脚本时，分片恢复当前 sync packet 并启动指定 MARS launcher。",
            "",
            f"- packet SHA256: `{manifest['packet_sha256']}`",
            f"- chunk count: `{manifest['chunk_count']}`",
            f"- chunk size: `{manifest['chunk_size']}`",
            f"- work dir: `{manifest['work_dir']}`",
            f"- project: `{manifest['project']}`",
            "",
            "执行顺序：",
            "",
            "1. 先粘贴/运行 `00_INIT_MARS_FINAL_CANDIDATE_GATE_UPLOAD.sh`。",
            f"2. 按文件名顺序粘贴/运行所有 `PART_*.sh`，共 `{len(parts)}` 个。",
            "3. 先粘贴/运行 `98_VERIFY_REASSEMBLE_ONLY.sh`，确认 tar SHA 正确。",
            f"4. 验证通过后粘贴/运行 `99_FINALIZE_INSTALL_AND_RUN_20_PILOT.sh`，安装并启动 `{launch_script}`。",
            "",
            "注意：`98_VERIFY` 不会启动 EMX，只检查分片恢复是否正确；`99_FINALIZE` 才会真正启动 20 条 pilot。",
            "",
            "脚本列表：",
            "",
            *[f"- `{name}`" for name in scripts],
            "",
        ]
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chmod_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


if __name__ == "__main__":
    raise SystemExit(main())
