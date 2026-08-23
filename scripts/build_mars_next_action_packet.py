#!/usr/bin/env python3
"""Build a concise MARS next-action packet for the blocked EMX reference state.

The packet is a runbook index, not simulator evidence. It reads the current
validation-chain, target EMX rerun, post-run validation, and handoff summaries
and states whether the next safe action is to run the target wideband EMX rerun
on MARS. It deliberately keeps HFSS comparison blocked while EMX-first is not
accepted.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_PACKAGE_DIR = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
DEFAULT_OUT_DIR = DEFAULT_PROJECT_ROOT / "mars_next_action_packet_20260614"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(args.project_root).expanduser().resolve()
    package_dir = Path(args.package_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = _evidence_paths(project_root, package_dir)
    validation_chain = _read_json(paths["validation_chain_summary"])
    rerun_summary = _read_json(paths["rerun_summary"])
    postrun_summary = _read_json(paths["postrun_summary"])
    handoff_verify = _read_json(paths["handoff_verify_summary"])
    acceptance_matrix = _read_json(paths["acceptance_matrix"])
    package_manifest = _read_json(paths["package_report_manifest"])

    checks = [
        _validation_chain_check(validation_chain),
        _target_rerun_check(rerun_summary, paths["rerun_command"]),
        _target_postrun_check(postrun_summary, paths["postrun_command"]),
        _handoff_verify_check(handoff_verify, paths["handoff_tar"], paths["handoff_sha"]),
        _acceptance_boundary_check(acceptance_matrix),
        _report_asset_boundary_check(package_manifest),
    ]
    status_counts = _status_counts(checks)
    overall_status, decision = _overall_decision(checks, validation_chain)
    expected_transfer_files = _expected_transfer_files(postrun_summary)
    mars_commands = _mars_command_cards(paths, rerun_summary, postrun_summary)
    local_after_mars_commands = _local_after_mars_command_cards(project_root, postrun_summary)
    local_postrun_import_requirements = _local_postrun_import_requirements()
    final_hfss_ads_evidence_requirements = _final_hfss_ads_evidence_requirements()

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "project_root": str(project_root),
        "package_dir": str(package_dir),
        "checks": [check.as_dict() for check in checks],
        "status_counts": status_counts,
        "evidence": {key: str(path) for key, path in paths.items()},
        "mars_commands": mars_commands,
        "expected_transfer_files_after_postrun": expected_transfer_files,
        "local_after_mars_commands": local_after_mars_commands,
        "local_postrun_import_requirements": local_postrun_import_requirements,
        "final_hfss_ads_evidence_requirements": final_hfss_ads_evidence_requirements,
        "guardrails": [
            "Do not run HFSS comparison until EMX-first accepts a regenerated target EMX S4P.",
            "The target rerun command is command provenance only; it is not an EMX result.",
            "After MARS post-run validation, pull the tarball, SHA256 file, and EMX S4P locally, then run verify_target_emx_postrun_package.py and require ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS.",
            "The local post-run import verifier must keep the approved port-pair CSV gate PASS: 24 ordered pairings checked and approved pair 1,2:3,4 PASS with <=5% ADS-photo error.",
            "Do not use final Lp/Ls/Qp/Qs/K figures or the 15 GHz marker table in reports until verify_accepted_emx_hfss_ads_figures.py returns ACCEPT_FINAL_LP_LS_Q_K_FIGURES.",
            "Report BLOCKED_AS_FINAL_EVIDENCE plots as blocked diagnostics only until the validation-chain decision is PASS.",
        ],
    }

    summary_path = out_dir / "mars_next_action_packet_summary.json"
    report_path = out_dir / "MARS_NEXT_ACTION_PACKET_20260614_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--package-dir", default=str(DEFAULT_PACKAGE_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _evidence_paths(project_root: Path, package_dir: Path) -> dict[str, Path]:
    target_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "target_emx_wideband_rerun_20260613"
    handoff_target_dir = project_root / "mars_handoff_bundle_20260613" / "project_runbook" / "target_emx_wideband_rerun_20260613"
    return {
        "validation_chain_summary": project_root
        / "validation_chain_decision_20260614"
        / "validation_chain_decision_summary.json",
        "rerun_summary": target_dir / "target_emx_wideband_rerun_summary.json",
        "rerun_command": handoff_target_dir / "target_emx_wideband_rerun.commands.sh",
        "postrun_summary": target_dir / "target_emx_wideband_postrun_validation_summary.json",
        "postrun_command": handoff_target_dir / "target_emx_wideband_postrun_validation.commands.sh",
        "handoff_verify_summary": project_root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json",
        "handoff_tar": project_root / "mars_handoff_bundle_20260613.tar.gz",
        "handoff_sha": project_root / "mars_handoff_bundle_20260613.tar.gz.sha256",
        "acceptance_matrix": project_root / "acceptance_matrix_20260613.json",
        "package_report_manifest": package_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json",
    }


def _validation_chain_check(summary: dict[str, Any]) -> Check:
    if summary.get("_missing") or summary.get("_parse_error"):
        return Check("FAIL", "validation-chain evidence", str(summary.get("_missing") or summary.get("_parse_error")))
    stages = {stage.get("name"): stage for stage in summary.get("stages", []) if isinstance(stage, dict)}
    if (
        summary.get("overall_status") == "BLOCKED_BY_EMX_REFERENCE"
        and summary.get("decision") == "DO_NOT_USE_HFSS_COMPARISON"
        and stages.get("HFSS geometry asset traceability", {}).get("status") == "PASS_DIAGNOSTIC_ONLY"
        and stages.get("HFSS physical S4P gate", {}).get("status") == "PASS_DIAGNOSTIC_ONLY"
    ):
        return Check(
            "PASS",
            "validation-chain blocks HFSS comparison",
            "EMX-first is blocked and HFSS geometry/physical PASS evidence is diagnostic-only.",
        )
    if summary.get("overall_status") == "PASS" and summary.get("decision") == "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN":
        return Check("PASS", "validation-chain already accepted", "All validation-chain stages are PASS.")
    return Check(
        "FAIL",
        "validation-chain evidence",
        f"unexpected status={summary.get('overall_status')}, decision={summary.get('decision')}",
    )


def _target_rerun_check(summary: dict[str, Any], command_path: Path) -> Check:
    generated = summary.get("generated_frequency_hz") or {}
    ok = (
        summary.get("overall_status") == "PASS"
        and summary.get("decision") == "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY"
        and generated.get("start") == 5.0e9
        and generated.get("stop") == 50.0e9
        and generated.get("step") == 1.0e8
        and generated.get("points") == 451
        and command_path.exists()
    )
    if ok:
        return Check("PASS", "target EMX rerun command", f"5-50 GHz / 0.1 GHz / 451 points; command={command_path}")
    return Check("FAIL", "target EMX rerun command", "rerun summary/command is missing or not on the required grid")


def _target_postrun_check(summary: dict[str, Any], command_path: Path) -> Check:
    checks = summary.get("checks") or []
    failed = [check for check in checks if check.get("status") == "FAIL"]
    ok = (
        summary.get("overall_status") == "PASS"
        and summary.get("decision") == "READY_FOR_MARS_POSTRUN_VALIDATION"
        and not failed
        and command_path.exists()
    )
    if ok:
        return Check("PASS", "target EMX post-run validation command", f"command={command_path}")
    return Check("FAIL", "target EMX post-run validation command", "post-run summary/command is missing or has failing checks")


def _handoff_verify_check(summary: dict[str, Any], tar_path: Path, sha_path: Path) -> Check:
    if summary.get("overall_status") == "PASS" and tar_path.exists() and sha_path.exists():
        return Check(
            "PASS",
            "MARS handoff bundle readiness",
            f"{len(summary.get('checks', []))} verifier checks PASS; tar and SHA exist.",
        )
    return Check("FAIL", "MARS handoff bundle readiness", "handoff verifier, tarball, or SHA record is missing/not PASS")


def _acceptance_boundary_check(summary: dict[str, Any]) -> Check:
    counts = summary.get("status_counts") or {}
    if summary.get("overall_status") == "INCOMPLETE" and counts.get("BLOCKED", 0) >= 1:
        return Check("PASS", "acceptance boundary remains conservative", f"overall=INCOMPLETE, counts={counts}")
    if summary.get("overall_status") == "PASS":
        return Check("PASS", "acceptance boundary complete", "acceptance matrix reports PASS")
    return Check("FAIL", "acceptance boundary", f"unexpected acceptance status={summary.get('overall_status')}")


def _report_asset_boundary_check(manifest: dict[str, Any]) -> Check:
    counts = manifest.get("asset_usage_counts") or {}
    if counts.get("BLOCKED_AS_FINAL_EVIDENCE", 0) > 0 and counts.get("DIAGNOSTIC_ONLY", 0) > 0:
        return Check("PASS", "report asset-use boundaries", f"asset_usage_counts={counts}")
    return Check("FAIL", "report asset-use boundaries", "report manifest does not record blocked/diagnostic visual evidence")


def _overall_decision(checks: list[Check], validation_chain: dict[str, Any]) -> tuple[str, str]:
    if any(check.status == "FAIL" for check in checks):
        return "NOT_READY", "FIX_LOCAL_HANDOFF_OR_PACKET_INPUTS"
    if validation_chain.get("overall_status") == "PASS":
        return "PASS", "VALIDATION_CHAIN_ALREADY_ACCEPTED"
    return "PASS", "READY_FOR_MARS_TARGET_EMX_RERUN"


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return dict(sorted(counts.items()))


def _expected_transfer_files(postrun_summary: dict[str, Any]) -> list[str]:
    validation_dir = str(postrun_summary.get("default_validation_dir") or "")
    emx_s4p = str(postrun_summary.get("expected_emx_s4p") or "")
    if not validation_dir:
        return []
    base = Path(validation_dir)
    transfer_tarball = Path(f"{validation_dir.rstrip('/')}_transfer.tar.gz")
    expected = [
        emx_s4p,
        str(transfer_tarball),
        str(Path(f"{transfer_tarball}.sha256")),
        str(base / "emx_wideband.s4p.sha256"),
        str(base / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json"),
        str(base / "touchstone_physical_gate" / "touchstone_transformer_audit_report.md"),
        str(base / "touchstone_physical_gate" / "touchstone_transformer_metrics.csv"),
        str(base / "touchstone_physical_gate" / "touchstone_ads_equivalent_metrics.png"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_report.md"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_metrics.csv"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_ads_style_metrics.png"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_core_metrics.png"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_port_pair_sensitivity.csv"),
        str(base / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_port_pair_sensitivity.png"),
    ]
    return [item for item in expected if item]


def _mars_command_cards(paths: dict[str, Path], rerun_summary: dict[str, Any], postrun_summary: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "step": "1",
            "name": "Verify handoff on MARS",
            "command": "python3 scripts/verify_mars_handoff_install.py . --out-dir mars_handoff_verify_on_mars",
            "purpose": "Confirm scripts, runbooks, tar-safe paths, and validation contracts after unpacking on MARS.",
        },
        {
            "step": "2",
            "name": "Run target EMX wideband rerun",
            "command_file": str(paths["rerun_command"]),
            "command": "bash project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh",
            "expected_output": str(rerun_summary.get("generated_output_s4p") or ""),
        },
        {
            "step": "3",
            "name": "Run target EMX post-run validation",
            "command_file": str(paths["postrun_command"]),
            "command": "bash project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_postrun_validation.commands.sh",
            "expected_output": str(postrun_summary.get("default_validation_dir") or ""),
        },
    ]


def _local_after_mars_command_cards(project_root: Path, postrun_summary: dict[str, Any]) -> list[dict[str, str]]:
    emx_s4p = str(postrun_summary.get("expected_emx_s4p") or "")
    validation_dir = str(postrun_summary.get("default_validation_dir") or "")
    if not emx_s4p or not validation_dir:
        return []
    remote_tarball = f"{validation_dir.rstrip('/')}_transfer.tar.gz"
    remote_tarball_sha = f"{remote_tarball}.sha256"
    local_download_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_postrun_download_20260613"
    )
    local_import_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_postrun_import_20260613"
    )
    accepted_validation_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "accepted_emx_hfss_ads_validation_20260613"
    )
    final_figure_verify_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "accepted_final_figure_verification_20260613"
    )
    local_emx = local_download_dir / "emx.s4p"
    local_tarball = local_download_dir / Path(remote_tarball).name
    local_tarball_sha = local_download_dir / Path(remote_tarball_sha).name
    discovery_summary = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "mars_emx_return_discovery_20260614"
        / "mars_emx_return_discovery_summary.json"
    )
    import_summary = local_import_dir / "target_emx_postrun_import_summary.json"
    accepted_summary = accepted_validation_dir / "accepted_emx_hfss_ads_validation_summary.json"
    return [
        {
            "step": "4",
            "name": "Pull target EMX post-run files to local desktop",
            "command": "\n".join(
                [
                    'export MARS_LOGIN="researcher@<MARS_HOST>"',
                    f'export LOCAL_TARGET_EMX_DIR="{local_download_dir}"',
                    'mkdir -p "$LOCAL_TARGET_EMX_DIR"',
                    f'rsync -av --progress "$MARS_LOGIN:{emx_s4p}" "$LOCAL_TARGET_EMX_DIR/emx.s4p"',
                    f'rsync -av --progress "$MARS_LOGIN:{remote_tarball}" "$LOCAL_TARGET_EMX_DIR/"',
                    f'rsync -av --progress "$MARS_LOGIN:{remote_tarball_sha}" "$LOCAL_TARGET_EMX_DIR/"',
                ]
            ),
            "expected_output": str(local_download_dir),
            "purpose": "Pull the EMX S4P plus post-run validation tarball/SHA; do not use HFSS comparison before the next import gate passes.",
        },
        {
            "step": "5",
            "name": "Auto-discover and verify local target EMX return",
            "command": "\n".join(
                [
                    ".venv/bin/python scripts/discover_and_verify_mars_emx_return.py \\",
                    f"  --search-root {local_download_dir} \\",
                    f"  --out-dir {discovery_summary.parent}",
                ]
            ),
            "expected_output": str(discovery_summary),
            "purpose": "Select only a real 4-port 5-50 GHz / 0.1 GHz / 451-point EMX S4P plus validation tarball/SHA, then dispatch the strict post-run import verifier.",
        },
        {
            "step": "6",
            "name": "Verify local accepted EMX import bundle",
            "command": "\n".join(
                [
                    ".venv/bin/python scripts/verify_target_emx_postrun_package.py \\",
                    f"  --tarball {local_tarball} \\",
                    f"  --sha-record {local_tarball_sha} \\",
                    f"  --emx-s4p {local_emx} \\",
                    "  --require-emx-s4p \\",
                    f"  --out-dir {local_import_dir}",
                ]
            ),
            "expected_output": str(import_summary),
            "purpose": "Require decision=ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS and accepted_emx_reference_bundle.status=READY_FOR_HFSS.",
        },
        {
            "step": "7",
            "name": "Run accepted EMX vs HFSS/ADS validation after HFSS export exists",
            "command": "\n".join(
                [
                    'export HFSS_S4P="/path/to/HFSS_exported_5_50_0p1.s4p"',
                    'export HFSS_GEOMETRY_SUMMARY="/path/to/hfss_model_geometry_asset_audit_summary.json"',
                    ".venv/bin/python scripts/run_accepted_emx_hfss_ads_validation.py \\",
                    f"  --emx-import-summary {import_summary} \\",
                    f"  --emx-s4p {local_emx} \\",
                    '  --hfss-s4p "$HFSS_S4P" \\',
                    '  --hfss-geometry-summary "$HFSS_GEOMETRY_SUMMARY" \\',
                    f"  --out-dir {accepted_validation_dir}",
                    "",
                    ".venv/bin/python scripts/verify_accepted_emx_hfss_ads_figures.py \\",
                    f"  --accepted-summary {accepted_summary} \\",
                    f"  --out-dir {final_figure_verify_dir}",
                ]
            ),
            "expected_output": str(accepted_summary),
            "purpose": "Only after a real HFSS S4P exists, require ACCEPT_HFSS_VALIDATION_SAMPLE and ACCEPT_FINAL_LP_LS_Q_K_FIGURES before report use.",
        },
    ]


def _local_postrun_import_requirements() -> list[str]:
    return [
        "verify_target_emx_postrun_package.py returns decision=ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
        "local EMX .s4p SHA256 matches the MARS-recorded emx_wideband.s4p.sha256",
        "Touchstone physical gate PASS with 4 ports and 5-50 GHz / 0.1 GHz / 451-point grid",
        "EMX-first gate PASS with decision=ACCEPT_AS_GOLDEN_EMX_REFERENCE",
        "EMX-first ADS no-extrapolation plot grid PASS: every 5-50 GHz / 0.1 GHz ADS plotting point is present in the EMX Touchstone file",
        "EMX-first metrics CSV files contain finite numeric Lp/Ls/Qp/Qs/K/Cm values on the 451-point grid",
        "EMX-first PNG plots are decodable, sufficiently large, and nonblank",
        "EMX-first port-pair sensitivity CSV gate PASS: 24 ordered four-port pairings checked, approved pair 1,2:3,4 PASS, and max_percent_error <= 5%",
        "target import summary contains accepted_emx_reference_bundle.status=READY_FOR_HFSS with EMX S4P SHA and EMX-first artifact paths for downstream HFSS/ADS validation",
    ]


def _final_hfss_ads_evidence_requirements() -> list[str]:
    return [
        "run_accepted_emx_hfss_ads_validation.py returns decision=ACCEPT_HFSS_VALIDATION_SAMPLE",
        "local EMX import summary, HFSS S4P source identity, and compare summary all refer to the same target sample",
        "HFSS geometry asset audit summary exists and returns decision=ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS for the model used to export the HFSS S4P",
        "ADS no-extrapolation coverage = PASS on the 5-50 GHz / 0.1 GHz / 451-point plotting grid",
        "finite K/Qp/Qs/Lp/Ls plot_data arrays exist for EMX and HFSS/ADS across the 451-point grid",
        "K/Qp/Qs/Lp/Ls max_percent_error values are all <= 5%",
        "EMX, HFSS, and overlay Lp/Ls/Qp/Qs/K PNG figures are decodable, sufficiently large, and nonblank",
        "ads_style_target_marker_values_15ghz.csv and ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md exist and record exact-grid 15 GHz Lp/Ls/Qp/Qs/K values",
        "verify_accepted_emx_hfss_ads_figures.py returns decision=ACCEPT_FINAL_LP_LS_Q_K_FIGURES before final report use",
    ]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS 下一步操作包 2026-06-14",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Generated UTC: `{summary['generated_utc']}`",
        "",
        "## 当前结论",
        "",
        "当前本地链路仍然保持保守：EMX-first 没有接受黄金参考，因此 HFSS 对比不能作为最终 5% 验证结论。下一步安全动作是去 MARS 重新生成目标样本自己的 5-50 GHz / 0.1 GHz EMX `.s4p`，然后立刻运行 post-run validation。",
        "",
        "## 本地检查",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## MARS 执行顺序", ""])
    for command in summary["mars_commands"]:
        lines.append(f"### Step {command['step']}: {command['name']}")
        lines.append("")
        if command.get("command_file"):
            lines.append(f"- Source command file: `{command['command_file']}`")
        lines.append("```bash")
        lines.append(command["command"])
        lines.append("```")
        if command.get("expected_output"):
            lines.append(f"- Expected output: `{command['expected_output']}`")
        if command.get("purpose"):
            lines.append(f"- Purpose: {command['purpose']}")
        lines.append("")
    lines.extend(["## Post-run 需要拉回的文件", ""])
    for item in summary["expected_transfer_files_after_postrun"]:
        lines.append(f"- `{item}`")
    lines.extend(["", "## 本地拉回/导入/最终验证命令模板", ""])
    for command in summary.get("local_after_mars_commands", []):
        lines.append(f"### Step {command['step']}: {command['name']}")
        lines.append("")
        lines.append("```bash")
        lines.append(command["command"])
        lines.append("```")
        if command.get("expected_output"):
            lines.append(f"- Expected output: `{command['expected_output']}`")
        if command.get("purpose"):
            lines.append(f"- Purpose: {command['purpose']}")
        lines.append("")
    lines.extend(["", "## 本地 post-run import 必须通过的门禁", ""])
    for item in summary.get("local_postrun_import_requirements", []):
        lines.append(f"- {item}")
    lines.extend(["", "## EMX 通过后最终 HFSS/ADS 证据门禁", ""])
    for item in summary.get("final_hfss_ads_evidence_requirements", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Guardrails", ""])
    lines.extend(f"- {item}" for item in summary["guardrails"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
