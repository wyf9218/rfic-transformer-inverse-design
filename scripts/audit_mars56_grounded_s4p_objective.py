#!/usr/bin/env python3
"""Audit the grounded-power-line S4P objective before launching MARS/EMX."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _check(status: bool, name: str, detail: str, fix: str) -> dict[str, str]:
    return {
        "status": "PASS" if status else "FAIL",
        "name": name,
        "detail": detail,
        "fix": fix,
    }


def _role_map(cfg) -> dict[str, str]:
    return dict(cfg.emx.power_line_8port.role_labels)


def _freq_summary(freqs_hz) -> dict[str, float | int]:
    return {
        "count": int(len(freqs_hz)),
        "start_ghz": float(freqs_hz[0]) / 1.0e9,
        "stop_ghz": float(freqs_hz[-1]) / 1.0e9,
        "step_ghz": float(freqs_hz[1] - freqs_hz[0]) / 1.0e9 if len(freqs_hz) > 1 else 0.0,
    }


def _export_smoke(cfg, out_dir: Path) -> dict[str, Any]:
    from rfic_transformer_inverse_design.paths import default_proc_path
    from rfic_transformer_inverse_design.layout.export import export_transformer_layout
    import gdstk

    smoke_proc_fallback = None
    if not Path(cfg.emx.emx_process_file).expanduser().exists():
        smoke_proc_fallback = str(default_proc_path())
        cfg = replace(cfg, emx=replace(cfg.emx, emx_process_file=smoke_proc_fallback))

    layout_dir = out_dir / "layout_smoke"
    layout_dir.mkdir(parents=True, exist_ok=True)
    layout = export_transformer_layout(cfg.bounds.midpoint(), cfg, layout_dir, validate_geometry=False)
    manifest = json.loads(Path(layout.manifest_path).read_text(encoding="utf-8"))
    audit = json.loads((layout_dir / "power_line_8port_geometry.json").read_text(encoding="utf-8"))
    ports = [str(port["name"]) for port in manifest.get("ports", [])]
    port_reference_label_pattern = re.compile(r"^P\d{3}(?:_G)?$")
    labels = sorted(
        str(label.text)
        for cell in gdstk.read_gds(str(layout.gds_path)).cells
        for label in cell.labels
        if port_reference_label_pattern.match(str(label.text))
    )
    return {
        "layout_dir": str(layout_dir),
        "gds_path": str(layout.gds_path),
        "manifest_path": str(layout.manifest_path),
        "preview_path": str(layout.preview_path),
        "ports": ports,
        "gds_port_reference_labels": labels,
        "manifest_cadence_pin_purpose": manifest.get("cadence_pin_purpose"),
        "smoke_proc_fallback": smoke_proc_fallback,
        "port_ground_labels": {str(port["name"]): list(port.get("ground_labels", [])) for port in manifest.get("ports", [])},
        "audit_touchstone_mode": audit.get("touchstone_mode"),
        "auxiliary_ground_reference_labels": audit.get("auxiliary_ground_reference_labels"),
        "power_line_ground_stitches": audit.get("power_line_ground_stitches"),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else _repo_root()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    import sys

    sys.path.insert(0, str(repo_root))
    from rfic_transformer_inverse_design.core.defaults import load_run_config
    from rfic_transformer_inverse_design.layout.drc_rules import audit_tsmc65_top_metal_search_space

    cfg = load_run_config(Path(args.config).expanduser())
    spec = cfg.emx.power_line_8port
    roles = _role_map(cfg)
    freqs_hz = cfg.target.frequency_points_hz()
    freq = _freq_summary(freqs_hz)
    drc_search = audit_tsmc65_top_metal_search_space(cfg)
    zeus_cadence_path = repo_root / "rfic_transformer_inverse_design" / "execution" / "zeus_cadence.py"
    signal_roles = {"primary_top", "primary_bottom", "secondary_top", "secondary_bottom"}
    aux_roles = {"left_power_top", "left_power_bottom", "right_power_top", "right_power_bottom"}
    aux_role_order = ("left_power_top", "left_power_bottom", "right_power_top", "right_power_bottom")
    expected_roles = signal_roles | aux_roles

    checks: list[dict[str, str]] = [
        _check(
            freq == {"count": 111, "start_ghz": 5.0, "stop_ghz": 60.0, "step_ghz": 0.5},
            "frequency grid is 5-60 GHz with 0.5 GHz step",
            json.dumps(freq, sort_keys=True),
            "Set target.frequency_start_hz=5e9, stop=60e9, step=0.5e9, band_points=111.",
        ),
        _check(
            bool(drc_search["ok"]),
            "TSMC65 top-metal DRC search-space gate passes",
            "; ".join(drc_search["errors"]) if drc_search["errors"] else "width/spacing bounds satisfy Mu/AP-MD gate",
            "Set shared trace-width bounds to 3-12 um and same-layer spacing lower bound to at least 2 um.",
        ),
        _check(
            cfg.emx.port_mode == "single_ended_shield_grounded",
            "EMX port mode uses shield/M5-referenced single-ended ports",
            str(cfg.emx.port_mode),
            "Set emx.port_mode: single_ended_shield_grounded.",
        ),
        _check(
            cfg.emx.uses_cadence_pins() and int(cfg.emx.cadence_pin_purpose or -1) == 51,
            "Cadence pin placement is enabled with pin purpose 51",
            f"cadence_pin_purpose={cfg.emx.cadence_pin_purpose}, uses_cadence_pins={cfg.emx.uses_cadence_pins()}",
            "Set emx.cadence_pin_purpose: 51 so EMX uses Cadence pin figures instead of plain labels.",
        ),
        _check(
            zeus_cadence_path.exists(),
            "CAE-adapted zeus_cadence.py round-trip module is present",
            str(zeus_cadence_path),
            "Keep rfic_transformer_inverse_design/execution/zeus_cadence.py in the MARS project for Cadence pin creation.",
        ),
        _check(
            bool(spec.enabled) and spec.touchstone_mode == "signal_4_grounded_aux",
            "power-line topology exports signal-only S4P",
            f"enabled={spec.enabled}, touchstone_mode={spec.touchstone_mode}",
            "Set emx.power_line_8port.enabled=true and touchstone_mode=signal_4_grounded_aux.",
        ),
        _check(
            list(spec.port_map) == ["P001", "P002", "P003", "P004"],
            "Touchstone output order is four signal ports P001-P004",
            ",".join(spec.port_map),
            "Use emx.power_line_8port.port_map: [P001, P002, P003, P004].",
        ),
        _check(
            set(roles) == expected_roles and len(set(roles.values())) == 8,
            "eight physical roles are mapped distinctly",
            json.dumps(roles, sort_keys=True),
            "Map primary/secondary signal roles plus four auxiliary power-line roles.",
        ),
        _check(
            {roles.get(role) for role in signal_roles} == set(spec.port_map),
            "signal roles are exactly the exported S4P ports",
            json.dumps({role: roles.get(role) for role in sorted(signal_roles)}, sort_keys=True),
            "Map primary/secondary top/bottom to P001-P004 only.",
        ),
        _check(
            not ({roles.get(role) for role in aux_roles} & set(spec.port_map)),
            "auxiliary power-line roles are grounded references, not exported ports",
            json.dumps({role: roles.get(role) for role in sorted(aux_roles)}, sort_keys=True),
            "Keep left/right power top/bottom outside the exported P001-P004 port_map.",
        ),
    ]

    smoke: dict[str, Any] | None = None
    if args.export_smoke:
        smoke = _export_smoke(cfg, out_dir)
        expected_gds_labels = [f"P{idx:03d}" for idx in range(1, 9)] + [f"P{idx:03d}_G" for idx in range(1, 5)]
        forbidden_aux_ground_labels = [f"P{idx:03d}_G" for idx in range(5, 9)]
        checks.extend(
            [
                _check(
                    smoke["ports"] == ["P001", "P002", "P003", "P004"],
                    "layout smoke manifest has four EMX ports",
                    ",".join(smoke["ports"]),
                    "Fix layout export so auxiliary power-line endpoints do not enter manifest.ports.",
                ),
                _check(
                    int(smoke["manifest_cadence_pin_purpose"] or -1) == 51,
                    "layout smoke manifest records Cadence pin purpose 51",
                    str(smoke["manifest_cadence_pin_purpose"]),
                    "Ensure export_transformer_layout propagates cadence_pin_purpose into the EMX manifest.",
                ),
                _check(
                    smoke["audit_touchstone_mode"] == "signal_4_grounded_aux",
                    "layout smoke audit records S4P mode",
                    str(smoke["audit_touchstone_mode"]),
                    "Ensure power_line_8port_geometry.json records touchstone_mode.",
                ),
                _check(
                    smoke["auxiliary_ground_reference_labels"] == ["P005", "P006", "P007", "P008"],
                    "vertical power-line endpoints are ground-only labels P005-P008",
                    ",".join(smoke["auxiliary_ground_reference_labels"] or []),
                    "In S4P mode, do not create separate P005_G-P008_G labels; P005-P008 are M5 ground references.",
                ),
                _check(
                    set(smoke["gds_port_reference_labels"]) == set(expected_gds_labels)
                    and len(smoke["gds_port_reference_labels"]) == len(expected_gds_labels),
                    "GDS has exactly 12 physical port/reference labels",
                    ",".join(smoke["gds_port_reference_labels"]),
                    "Expected P001-P008 plus P001_G-P004_G; no auxiliary P005_G-P008_G labels.",
                ),
                _check(
                    not (set(smoke["gds_port_reference_labels"]) & set(forbidden_aux_ground_labels)),
                    "vertical ground-only labels do not create extra auxiliary grounds",
                    ",".join(sorted(set(smoke["gds_port_reference_labels"]) & set(forbidden_aux_ground_labels))) or "none",
                    "Remove P005_G-P008_G labels in signal_4_grounded_aux mode.",
                ),
                _check(
                    all(
                        set(smoke["port_ground_labels"].get(signal_label, [])) == {f"{signal_label}_G"}
                        for signal_label in ("P001", "P002", "P003", "P004")
                    ),
                    "each exported signal port references its own local shield ground only",
                    json.dumps(smoke["port_ground_labels"], sort_keys=True),
                    "Each P001-P004 EMXPort should use only Pxxx_G; P005-P008 are physically stitched to M5 and must not be repeated across GSG ground groups.",
                ),
                _check(
                    isinstance(smoke.get("power_line_ground_stitches"), list)
                    and len(smoke["power_line_ground_stitches"]) == 4
                    and all(str(item.get("target_ground_metal")) == "metal5" for item in smoke["power_line_ground_stitches"]),
                    "four vertical endpoints have explicit M5 ground-stitch stacks",
                    json.dumps(
                        [
                            {
                                "label": item.get("label"),
                                "ground": item.get("ground_label"),
                                "target": item.get("target_ground_metal"),
                                "via_layers": [via.get("layer") for via in item.get("via_stack", [])],
                            }
                            for item in (smoke.get("power_line_ground_stitches") or [])
                        ],
                        sort_keys=True,
                    ),
                    "Draw and audit a metal/via stack from each vertical endpoint down to metal5.",
                ),
            ]
        )

    overall_status = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "READY_FOR_MARS_S4P_EMX_QUEUE" if overall_status == "PASS" else "FIX_S4P_CONTRACT_BEFORE_MARS",
        "config": str(Path(args.config).expanduser().resolve()),
        "repo_root": str(repo_root),
        "frequency": freq,
        "drc_search_space": drc_search,
        "touchstone_contract": {
            "physical_port_reference_labels": 12,
            "exported_signal_ports": list(spec.port_map),
            "signal_port_ground_labels": [f"{label}_G" for label in spec.port_map],
            "vertical_ground_only_labels": [roles[role] for role in aux_role_order],
            "expected_touchstone_suffix": ".s4p",
            "cadence_pin_purpose": cfg.emx.cadence_pin_purpose,
            "zeus_cadence_module": str(zeus_cadence_path),
            "role_labels": roles,
        },
        "checks": checks,
        "layout_smoke": smoke,
    }
    summary_path = out_dir / "mars56_grounded_s4p_objective_audit_summary.json"
    report_path = out_dir / "MARS56_GROUNDED_S4P_OBJECTIVE_AUDIT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 Grounded S4P Objective Audit",
        "",
        f"- Status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        "- Port contract: 12 physical port/reference labels = 4 RF signal ports, 4 RF local ground references, and 4 vertical power-line ground-only labels; final EMX output is `.s4p`.",
        f"- Cadence pins: `cadence_pin_purpose={summary['touchstone_contract'].get('cadence_pin_purpose')}` via `{summary['touchstone_contract'].get('zeus_cadence_module')}`.",
        f"- Frequency grid: {summary['frequency']['start_ghz']:.1f}-{summary['frequency']['stop_ghz']:.1f} GHz, step {summary['frequency']['step_ghz']:.1f} GHz, {summary['frequency']['count']} points.",
        "",
        "## Checks",
    ]
    for check in summary["checks"]:
        lines.append(f"- `{check['status']}` {check['name']}: {check['detail']}")
    if summary.get("layout_smoke"):
        smoke = summary["layout_smoke"]
        lines.extend(
            [
                "",
                "## Layout Smoke",
                f"- GDS: `{smoke['gds_path']}`",
                f"- Manifest: `{smoke['manifest_path']}`",
                f"- Preview: `{smoke['preview_path']}`",
                f"- EMX ports: `{', '.join(smoke['ports'])}`",
                f"- Cadence pin purpose: `{smoke['manifest_cadence_pin_purpose']}`",
                f"- GDS port/reference labels: `{', '.join(smoke['gds_port_reference_labels'])}`",
                f"- Auxiliary ground references: `{', '.join(smoke['auxiliary_ground_reference_labels'] or [])}`",
                f"- M5 ground-stitch stacks: `{len(smoke.get('power_line_ground_stitches') or [])}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--repo-root")
    parser.add_argument("--export-smoke", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
