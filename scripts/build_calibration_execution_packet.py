#!/usr/bin/env python3
"""Build EMX and HFSS execution inputs for calibration structures."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import shutil
from pathlib import Path
from typing import Any

import gdstk


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD_SCRIPT = Path("/home/researcher/Documents/hfss_v39_src_26cb45/build_hfss_s8p_from_payload.py")
DEFAULT_SOLVE_SCRIPT = Path("/home/researcher/Documents/hfss_v39_src_26cb45/run_hfss_explicit_sweep_export.py")
DEFAULT_EMX_BINARY = "/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design/mars_local_bin/emx_cae_singularity"
DEFAULT_PROC = "/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc"
DEFAULT_REMOTE_ROOT = "/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design/runs/emx_hfss_calibration_20260626"

LAYER_TO_METAL = {
    (35, 0): "metal5",
    (135, 0): "metal5",
    (39, 60): "metal9",
    (139, 0): "metal9",
    (74, 0): "metal10",
    (126, 0): "metal10",
}

HFSS_CALIBRATION_VARIANTS = [
    {
        "name": "air_baseline",
        "m5_shield_boundary": "finite",
        "port_reference_mode": "local_ground_bbox",
        "unite_strategy": "connected_by_bbox",
        "dielectric_z_min_um": "700",
        "dielectric_z_max_um": "700",
        "dielectric_conductivity_mode": "ignore",
        "note": "No dielectric volume; reproduces the v39-style baseline used to expose the L/Q gap.",
    },
    {
        "name": "m5_united_air",
        "m5_shield_boundary": "finite",
        "port_reference_mode": "all_m5",
        "unite_strategy": "all_by_metal",
        "dielectric_z_min_um": "700",
        "dielectric_z_max_um": "700",
        "dielectric_conductivity_mode": "ignore",
        "note": "No dielectric volume, but unite the M5 shield/tie network and use the whole M5 conductor as the port reference.",
    },
    {
        "name": "m5_perfecte_air",
        "m5_shield_boundary": "perfecte",
        "port_reference_mode": "local_ground_bbox",
        "unite_strategy": "connected_by_bbox",
        "dielectric_z_min_um": "700",
        "dielectric_z_max_um": "700",
        "dielectric_conductivity_mode": "ignore",
        "note": "No dielectric volume, but force the M5 shield/reference frame to ideal ground; diagnoses floating-local-ground port behavior.",
    },
    {
        "name": "substrate_conductivity",
        "m5_shield_boundary": "finite",
        "port_reference_mode": "local_ground_bbox",
        "unite_strategy": "connected_by_bbox",
        "dielectric_z_min_um": "0",
        "dielectric_z_max_um": "700",
        "dielectric_conductivity_mode": "conductivity",
        "note": "Local lossy silicon substrate only; tests whether substrate return/loss drives the EMX/HFSS gap.",
    },
    {
        "name": "beol_lossless_dielectric",
        "m5_shield_boundary": "finite",
        "port_reference_mode": "local_ground_bbox",
        "unite_strategy": "connected_by_bbox",
        "dielectric_z_min_um": "700",
        "dielectric_z_max_um": "718.643",
        "dielectric_conductivity_mode": "ignore",
        "note": "Local oxide/passivation stack above the substrate; tests capacitance/loading without substrate loss.",
    },
    {
        "name": "full_local_stack_loss_tangent",
        "m5_shield_boundary": "finite",
        "port_reference_mode": "local_ground_bbox",
        "unite_strategy": "connected_by_bbox",
        "dielectric_z_min_um": "0",
        "dielectric_z_max_um": "718.643",
        "dielectric_conductivity_mode": "loss_tangent",
        "note": "Local substrate plus BEOL dielectric stack; conductive substrate is represented by tan(delta) at setup frequency.",
    },
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_path = Path(args.calibration_manifest).expanduser().resolve()
    stack_payload_path = Path(args.reference_hfss_payload).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    hfss_variants = _selected_hfss_variants(args)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stack_payload = json.loads(stack_payload_path.read_text(encoding="utf-8"))
    stack = stack_payload["stack"]

    entries = []
    for structure in manifest["structures"]:
        if args.stage and structure["stage"] not in set(args.stage):
            continue
        if args.structure and structure["name"] not in set(args.structure):
            continue
        entry = _write_structure_packet(structure, stack, manifest, out_dir, args)
        entries.append(entry)

    compare_script = _copy_compare_script(out_dir)
    _write_mars_emx_script(out_dir / "mars_run_emx_calibration.sh", entries, args)
    _write_windows_hfss_script(out_dir / "windows_run_hfss_calibration.ps1", entries, args, hfss_variants)
    _write_windows_copy_results_script(out_dir / "windows_copy_hfss_results_to_mac.ps1", entries, out_dir, hfss_variants)
    _write_index(out_dir / "calibration_execution_index.csv", entries)
    summary = {
        "schema": "rfic_transformer_calibration_execution_packet.v1",
        "calibration_manifest": str(manifest_path),
        "reference_hfss_payload": str(stack_payload_path),
        "out_dir": str(out_dir),
        "structure_count": len(entries),
        "emx_binary": args.emx_binary,
        "emx_process_file": args.emx_process_file,
        "remote_root": args.remote_root,
        "calibration_compare_script": str(compare_script) if compare_script else None,
        "windows_copy_results_script": str(out_dir / "windows_copy_hfss_results_to_mac.ps1"),
        "hfss_calibration_variants": hfss_variants,
        "structures": entries,
    }
    (out_dir / "calibration_execution_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_report(out_dir / "CALIBRATION_EXECUTION_PACKET_CN.md", summary)
    print(f"summary={out_dir / 'calibration_execution_summary.json'}")
    print(f"structures={len(entries)}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-manifest", required=True)
    parser.add_argument("--reference-hfss-payload", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--build-script", default=str(DEFAULT_BUILD_SCRIPT))
    parser.add_argument("--solve-script", default=str(DEFAULT_SOLVE_SCRIPT))
    parser.add_argument("--emx-binary", default=DEFAULT_EMX_BINARY)
    parser.add_argument("--emx-process-file", default=DEFAULT_PROC)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--stage", action="append", help="Only include structures from this stage")
    parser.add_argument("--structure", action="append", help="Only include named structure(s)")
    parser.add_argument("--hfss-variant", action="append", help="Only include named HFSS variant(s)")
    return parser.parse_args(argv)


def _selected_hfss_variants(args: argparse.Namespace) -> list[dict[str, str]]:
    if not args.hfss_variant:
        return HFSS_CALIBRATION_VARIANTS
    requested = set(args.hfss_variant)
    variants = [variant for variant in HFSS_CALIBRATION_VARIANTS if variant["name"] in requested]
    found = {variant["name"] for variant in variants}
    missing = sorted(requested - found)
    if missing:
        raise ValueError(f"Unknown HFSS calibration variant(s): {', '.join(missing)}")
    return variants


def _write_structure_packet(
    structure: dict[str, Any],
    stack: dict[str, Any],
    manifest: dict[str, Any],
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    name = structure["name"]
    struct_dir = out_dir / name
    struct_dir.mkdir(parents=True, exist_ok=True)
    gds_src = Path(structure["gds"])
    ports_src = Path(structure["port_map"])
    gds_dst = struct_dir / gds_src.name
    ports_dst = struct_dir / ports_src.name
    shutil.copy2(gds_src, gds_dst)
    shutil.copy2(ports_src, ports_dst)
    shutil.copy2(Path(args.build_script), struct_dir / "build_hfss_s8p_from_payload.py")
    solve_script_dst = struct_dir / "run_hfss_explicit_sweep_export.py"
    _write_touchstone_export_script(Path(args.solve_script), solve_script_dst)

    port_map = json.loads(ports_src.read_text(encoding="utf-8"))
    conductor_polygons, bbox = _extract_conductor_polygons(gds_src)
    ports = _build_ports(port_map, stack)
    port_count = len(ports)
    frequency_grid = manifest["frequency_grid"]
    payload = {
        "schema": "rfic_transformer_calibration_hfss_payload.v1",
        "sample_id": name,
        "source_files": {
            "gds": str(gds_dst),
            "port_map": str(ports_dst),
        },
        "hfss": {
            "project_name": f"{name}_hfss_calibration",
            "design_name": f"{name}_3dmodel",
            "solution_type": "Terminal",
            "version": "2025.1",
            "setup_name": "Setup_15GHz",
            "sweep_name": "Sweep_15p0_15p5",
            "expected_touchstone_suffix": f".s{port_count}p",
        },
        "frequency_grid": {
            "setup_frequency_ghz": frequency_grid["target_ghz"],
            "start_ghz": frequency_grid["start_ghz"],
            "stop_ghz": frequency_grid["stop_ghz"],
            "step_ghz": frequency_grid["step_ghz"],
            "points": int(round((frequency_grid["stop_ghz"] - frequency_grid["start_ghz"]) / frequency_grid["step_ghz"])) + 1,
        },
        "stack": stack,
        "bbox_um": bbox,
        "conductor_polygons": conductor_polygons,
        "ports": ports,
        "calibration": {
            "stage": structure["stage"],
            "purpose": structure["purpose"],
            "expected_metrics": structure["expected_metrics"],
            "gate_percent_error": manifest["gate"]["target_percent_error"],
        },
        "differential_port_pairs": _default_port_pairs(port_count),
        "acceptance_note": "Calibration payload only; compare EMX/HFSS before using full 8-port training data.",
    }
    payload_path = struct_dir / "hfss_s8p_build_payload.json"
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    remote_dir = f"{args.remote_root.rstrip('/')}/{name}"
    emx_out = f"{remote_dir}/emx/{name}.s{port_count}p"
    emx_command = _emx_command(structure, port_map, args, remote_dir, emx_out, frequency_grid)
    (struct_dir / "emx_command.json").write_text(json.dumps(emx_command, indent=2), encoding="utf-8")

    return {
        "name": name,
        "stage": structure["stage"],
        "top_cell": structure["top_cell"],
        "port_count": port_count,
        "gds": str(gds_dst),
        "port_map": str(ports_dst),
        "hfss_payload": str(payload_path),
        "hfss_build_script": str(struct_dir / "build_hfss_s8p_from_payload.py"),
        "hfss_solve_script": str(solve_script_dst),
        "emx_command_json": str(struct_dir / "emx_command.json"),
        "remote_dir": remote_dir,
        "remote_emx_output": emx_out,
        "diff_pairs": payload["differential_port_pairs"],
    }


def _extract_conductor_polygons(gds_path: Path) -> tuple[list[dict[str, Any]], list[float]]:
    lib = gdstk.read_gds(str(gds_path))
    top = lib.top_level()[0]
    raw_polygons = top.get_polygons()
    records: list[dict[str, Any]] = []
    xs: list[float] = []
    ys: list[float] = []
    for idx, polygon in enumerate(raw_polygons):
        key = (int(polygon.layer), int(polygon.datatype))
        metal = LAYER_TO_METAL.get(key)
        if metal is None:
            continue
        pts = [[float(x), float(y)] for x, y in polygon.points]
        if not pts:
            continue
        px = [pt[0] for pt in pts]
        py = [pt[1] for pt in pts]
        xs.extend(px)
        ys.extend(py)
        records.append(
            {
                "index": len(records),
                "metal": metal,
                "layer": key[0],
                "datatype": key[1],
                "points_um": pts,
                "bbox_um": [min(px), min(py), max(px), max(py)],
            }
        )
    if not records:
        raise ValueError(f"No supported conductor polygons found in {gds_path}")
    return records, [min(xs), min(ys), max(xs), max(ys)]


def _build_ports(port_map: dict[str, Any], stack: dict[str, Any]) -> list[dict[str, Any]]:
    conductors = stack["conductors"]
    ports = []
    for item in port_map["ports"]:
        signal_layer = (int(item["signal_layer"]["layer"]), int(item["signal_layer"]["datatype"]))
        ground_layer = (int(item["ground_layer"]["layer"]), int(item["ground_layer"]["datatype"]))
        signal_metal = LAYER_TO_METAL[signal_layer]
        ground_metal = LAYER_TO_METAL[ground_layer]
        sx, sy = [float(v) for v in item["signal_xy_um"]]
        gx, gy = [float(v) for v in item["ground_xy_um"]]
        width = max(4.0, min(12.0, abs(gx - sx) + abs(gy - sy)) * 0.08)
        ports.append(
            {
                "port_name": item["name"],
                "role": item["name"],
                "ground_name": item["ground"],
                "signal_metal": signal_metal,
                "ground_metal": ground_metal,
                "signal_label": {"name": item["name"], "origin_um": [sx, sy]},
                "ground_label": {"name": item["ground"], "origin_um": [gx, gy]},
                "signal_z_um": float(conductors[signal_metal]["z_top_um"]),
                "ground_z_um": float(conductors[ground_metal]["z_top_um"]),
                "port_sheet_width_um": width,
                "port_sheet_axis": "y",
            }
        )
    return ports


def _default_port_pairs(port_count: int) -> str:
    if port_count == 2:
        return "1,2"
    if port_count == 4:
        return "1,2:3,4"
    return ""


def _emx_command(
    structure: dict[str, Any],
    port_map: dict[str, Any],
    args: argparse.Namespace,
    remote_dir: str,
    emx_out: str,
    frequency_grid: dict[str, float],
) -> list[str]:
    gds_remote = f"{remote_dir}/{Path(structure['gds']).name}"
    command = [
        args.emx_binary,
        gds_remote,
        structure["top_cell"],
        args.emx_process_file,
        "--touchstone",
        "--s-impedance=50",
        "-s",
        emx_out,
        "--include-command-line",
        "--edge-width=1",
        "--accuracy=standard",
        "--verbose=2",
        "--cadence-pins=51",
    ]
    command.extend(item["emx_port_argument"] for item in port_map["ports"])
    start = int(round(float(frequency_grid["start_ghz"]) * 1e9))
    stop = int(round(float(frequency_grid["stop_ghz"]) * 1e9))
    step = int(round(float(frequency_grid["step_ghz"]) * 1e9))
    command.extend(str(freq) for freq in range(start, stop + step // 2, step))
    return command


def _write_touchstone_export_script(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8")
    hardcoded_line = '    output_file = RESULTS_DIR / f"{PROJECT.stem}_{SETUP_NAME}_{SWEEP_NAME}.s8p"'
    dynamic_lines = "\n".join(
        [
            '    suffix = str(PAYLOAD["hfss"].get("expected_touchstone_suffix", f".s{len(PAYLOAD.get(\'ports\', []))}p"))',
            '    output_file = RESULTS_DIR / f"{PROJECT.stem}_{SETUP_NAME}_{SWEEP_NAME}{suffix}"',
            '    log("expected_touchstone_suffix=" + repr(suffix))',
        ]
    )
    if hardcoded_line not in text:
        raise ValueError(f"Touchstone output line not found in {src}")
    dst.write_text(text.replace(hardcoded_line, dynamic_lines), encoding="utf-8")
    dst.chmod(src.stat().st_mode)


def _copy_compare_script(out_dir: Path) -> Path | None:
    src = REPO_ROOT / "scripts" / "compare_calibration_s2p_rlc.py"
    if not src.is_file():
        return None
    dst = out_dir / src.name
    shutil.copy2(src, dst)
    return dst


def _write_mars_emx_script(path: Path, entries: list[dict[str, Any]], args: argparse.Namespace) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        f"ROOT={shlex.quote(args.remote_root)}",
        "mkdir -p \"$ROOT\"",
        "",
    ]
    for entry in entries:
        name = entry["name"]
        remote_dir = entry["remote_dir"]
        local_gds = f'"${{SCRIPT_DIR}}/{name}/{Path(entry["gds"]).name}"'
        command = json.loads(Path(entry["emx_command_json"]).read_text(encoding="utf-8"))
        lines.extend(
            [
                f"mkdir -p {shlex.quote(remote_dir + '/emx')}",
                f"cp {local_gds} {shlex.quote(remote_dir + '/' + Path(entry['gds']).name)}",
                " ".join(shlex.quote(token) for token in command),
                f"test -s {shlex.quote(entry['remote_emx_output'])}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o755)


def _write_windows_hfss_script(path: Path, entries: list[dict[str, Any]], args: argparse.Namespace, variants: list[dict[str, str]]) -> None:
    lines = [
        '$ErrorActionPreference = "Stop"',
        '$python = "C:\\Program Files\\ANSYS Inc\\v251\\AnsysEM\\common\\commonfiles\\CPython\\3_10\\winx64\\python\\python.exe"',
        '$statusCsv = "C:\\hfss_runs\\calibration_stage1_status.csv"',
        'New-Item -ItemType Directory -Force -Path "C:\\hfss_runs" | Out-Null',
        '"structure,variant,status,message" | Set-Content -Path $statusCsv',
        "",
    ]
    for entry in entries:
        name = entry["name"]
        source_dir = str(Path(entry["hfss_payload"]).parent)
        lines.append(f'$src = "{_windows_mac_path(source_dir)}"')
        for variant in variants:
            variant_name = variant["name"]
            dst = f"C:\\hfss_runs\\calibration_{name}_{variant_name}"
            lines.extend(
                [
                    f'# {name} / {variant_name}: {variant["note"]}',
                    "try {",
                    f'$dst = "{dst}"',
                    "if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }",
                    "New-Item -ItemType Directory -Force -Path $dst | Out-Null",
                    "Get-ChildItem -Path $src -Force | Copy-Item -Destination $dst -Recurse -Force",
                    "Set-Location $dst",
                    f'$env:HFSS_PORT_REFERENCE_MODE = "{variant.get("port_reference_mode", "local_ground_bbox")}"',
                    f'$env:HFSS_M5_SHIELD_BOUNDARY = "{variant.get("m5_shield_boundary", "finite")}"',
                    f'$env:HFSS_UNITE_STRATEGY = "{variant.get("unite_strategy", "connected_by_bbox")}"',
                    f'$env:HFSS_DIELECTRIC_CONDUCTIVITY_MODE = "{variant["dielectric_conductivity_mode"]}"',
                    f'$env:HFSS_DIELECTRIC_Z_MIN_UM = "{variant["dielectric_z_min_um"]}"',
                    f'$env:HFSS_DIELECTRIC_Z_MAX_UM = "{variant["dielectric_z_max_um"]}"',
                    '$env:HFSS_SETUP_MAX_DELTA_S = "0.08"',
                    '$env:HFSS_SETUP_MAX_PASSES = "12"',
                    '$env:HFSS_SETUP_MIN_PASSES = "2"',
                    '$env:HFSS_SWEEP_TYPE = "Discrete"',
                    f'$env:HFSS_SAVE_PATH = Join-Path $dst "{name}_hfss_calibration_{variant_name}.aedt"',
                    "& $python .\\build_hfss_s8p_from_payload.py",
                    f'if ($LASTEXITCODE -ne 0) {{ throw "build_hfss failed for {name}/{variant_name} with exit code $LASTEXITCODE" }}',
                    '$env:HFSS_PROJECT = $env:HFSS_SAVE_PATH',
                    '$env:HFSS_PAYLOAD = Join-Path $dst "hfss_s8p_build_payload.json"',
                    '$env:HFSS_RESULTS_DIR = Join-Path $dst "hfss_direct_results"',
                    '$env:HFSS_LOG = Join-Path $dst "hfss_direct.log"',
                    '$env:HFSS_SWEEP = "Sweep_15p0_15p5_direct"',
                    '$env:HFSS_FORCE_NEW_SWEEP = "1"',
                    '$env:HFSS_NEW_SWEEP_TYPE = "Discrete"',
                    '$env:HFSS_SWEEP_START_GHZ = "15.0"',
                    '$env:HFSS_SWEEP_STOP_GHZ = "15.5"',
                    '$env:HFSS_SWEEP_STEP_GHZ = "0.5"',
                    '$env:HFSS_DIRECT_ANALYZE_SWEEP = "1"',
                    '$env:HFSS_SKIP_TOUCHSTONE_DATA = "1"',
                    '$env:HFSS_SKIP_PROFILE_EXPORT = "1"',
                    '$env:HFSS_CORES = "8"',
                    "& $python .\\run_hfss_explicit_sweep_export.py",
                    f'if ($LASTEXITCODE -ne 0) {{ throw "solve_export failed for {name}/{variant_name} with exit code $LASTEXITCODE" }}',
                    f'Add-Content -Path $statusCsv -Value "{name},{variant_name},PASS,ok"',
                    "} catch {",
                    '$msg = $_.Exception.Message.Replace(",", ";").Replace("`r", " ").Replace("`n", " ")',
                    f'Add-Content -Path $statusCsv -Value "{name},{variant_name},FAIL,$msg"',
                    f'Write-Host "FAILED {name} {variant_name}: $msg"',
                    "}",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_windows_copy_results_script(path: Path, entries: list[dict[str, Any]], out_dir: Path, variants: list[dict[str, str]]) -> None:
    dest_root = _windows_mac_path(str(out_dir / "windows_results"))
    lines = [
        '$ErrorActionPreference = "Stop"',
        '$srcRoot = "C:\\hfss_runs"',
        f'$dstRoot = "{dest_root}"',
        "New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null",
        'if (Test-Path (Join-Path $srcRoot "calibration_stage1_status.csv")) {',
        '  Copy-Item -Force (Join-Path $srcRoot "calibration_stage1_status.csv") (Join-Path $dstRoot "calibration_stage1_status.csv")',
        "}",
        "",
    ]
    for entry in entries:
        name = entry["name"]
        for variant in variants:
            variant_name = variant["name"]
            run_name = f"calibration_{name}_{variant_name}"
            lines.extend(
                [
                    f'$runSrc = Join-Path $srcRoot "{run_name}"',
                    f'$runDst = Join-Path $dstRoot "{run_name}"',
                    "if (Test-Path $runSrc) {",
                    "  New-Item -ItemType Directory -Force -Path $runDst | Out-Null",
                    "  Get-ChildItem -Path $runSrc -File -Force | Where-Object { $_.Name -match '\\.(s2p|log|json|csv|txt)$' -or $_.Name -like '*.prof' -or $_.Name -like '*.conv' } | Copy-Item -Destination $runDst -Force",
                    "  if (Test-Path (Join-Path $runSrc 'hfss_direct_results')) {",
                    "    Copy-Item -Recurse -Force (Join-Path $runSrc 'hfss_direct_results') (Join-Path $runDst 'hfss_direct_results')",
                    "  }",
                    "}",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def _windows_mac_path(posix_path: str) -> str:
    path = Path(posix_path)
    home = Path.home()
    try:
        rel = path.resolve().relative_to(home)
        return "\\\\Mac\\Home\\" + str(rel).replace("/", "\\")
    except ValueError:
        return str(path).replace("/", "\\")


def _write_index(path: Path, entries: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["name", "stage", "port_count", "hfss_payload", "emx_command_json", "remote_emx_output"],
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow({key: entry[key] for key in writer.fieldnames})


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# 校准执行包",
        "",
        "用途：先跑 M9/M10 straight line，再进入 single inductor / simple transformer，最终回完整 8-port。",
        "",
        f"- EMX binary: `{summary['emx_binary']}`",
        f"- EMX proc: `{summary['emx_process_file']}`",
        f"- Remote root: `{summary['remote_root']}`",
        f"- Calibration compare script: `{summary.get('calibration_compare_script')}`",
        f"- Windows result copy script: `{summary.get('windows_copy_results_script')}`",
        "",
        "HFSS variants:",
        "",
        "| Variant | z window (um) | Conductivity mode | Purpose |",
        "|---|---:|---|---|",
    ]
    for variant in summary.get("hfss_calibration_variants", []):
        lines.append(
            f"| `{variant['name']}` | {variant['dielectric_z_min_um']}-{variant['dielectric_z_max_um']} | `{variant['dielectric_conductivity_mode']}` | {variant['note']} |"
        )
    lines.extend(
        [
            "",
        "| Structure | Stage | Ports | HFSS payload | EMX output |",
        "|---|---|---:|---|---|",
        ]
    )
    for item in summary["structures"]:
        lines.append(
            f"| `{item['name']}` | {item['stage']} | {item['port_count']} | `{item['hfss_payload']}` | `{item['remote_emx_output']}` |"
        )
    lines.extend(["", "当前只应先执行 Stage 1 straight-line。未通过 10% 前，不启动 100 万 EMX 数据生成。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
