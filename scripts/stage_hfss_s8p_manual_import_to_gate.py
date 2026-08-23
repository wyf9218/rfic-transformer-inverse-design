#!/usr/bin/env python3
"""Stage a manually exported HFSS .s8p into a current V66/V67 gate directory.

This helper is intentionally conservative.  It only copies a file when:

* ``--apply`` is provided;
* the source is a real ``.s8p`` file;
* the Touchstone contract is 8 ports, 5-60 GHz, 1.0 GHz, 56 points;
* the target variant/sample directory already exists; and
* the target ``hfss_solve_export_results`` directory has no existing ``.s8p``
  unless ``--overwrite`` is explicitly provided.

Staging a file does not pass the EMX/HFSS gate.  It only makes the file visible
to the existing postrun validator, which still performs the physical metric
comparison before million-sample generation can unlock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


PROJECT_ROOT = REPO_ROOT.parent
GATE_ROOTS = {
    "v66": PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current",
    "v67": PROJECT_ROOT / "outputs" / "hfss_v67_material_mesh_calibration_plan_current",
}
DEFAULT_SAMPLE_ID = "26cb45d70af3cfd0"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_s8p_manual_import_to_gate_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source = Path(args.source_s8p).expanduser().resolve()
    target = _target_path(args, source)
    checks = _checks(source, target, args)
    executable = all(check["status"] == "PASS" for check in checks)
    copied = False
    if executable and args.apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied = True
        _write_import_manifest(target.parent / "hfss_s8p_manual_import_manifest.json", source, target, args)

    overall_status, decision = _decision(executable, copied, bool(args.apply))
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "source_s8p": str(source),
        "target_s8p": str(target),
        "target_results_dir": str(target.parent),
        "apply": bool(args.apply),
        "overwrite": bool(args.overwrite),
        "copied": copied,
        "checks": checks,
        "next_commands": _next_commands(args) if copied else [],
        "safety_notes": [
            "This import only stages a Touchstone file for the existing postrun validator.",
            "It does not create a PASS result and does not bypass the EMX/HFSS <=10% metric gate.",
            "Use --apply only when the source .s8p is the HFSS export for the selected variant and sample.",
        ],
    }
    summary_path = out_dir / "hfss_s8p_manual_import_to_gate_summary.json"
    report_path = out_dir / "HFSS_S8P_MANUAL_IMPORT_TO_GATE_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"copied={copied}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-s8p", required=True)
    parser.add_argument("--gate", choices=sorted(GATE_ROOTS), required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--sample-id", default=DEFAULT_SAMPLE_ID)
    parser.add_argument("--target-file-name")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--expected-reference-ohm", type=float, default=50.0)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--reference-tolerance-ohm", type=float, default=1.0e-6)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _target_path(args: argparse.Namespace, source: Path) -> Path:
    gate_root = GATE_ROOTS[str(args.gate)]
    sample_dir = gate_root / "variants" / str(args.variant) / str(args.sample_id)
    target_dir = sample_dir / "hfss_solve_export_results"
    name = str(args.target_file_name or f"{args.sample_id}_{args.gate}_{args.variant}_manual_hfss_import.s8p")
    if not name.lower().endswith(".s8p"):
        name += ".s8p"
    return target_dir / name


def _checks(source: Path, target: Path, args: argparse.Namespace) -> list[dict[str, str]]:
    sample_dir = target.parent.parent
    existing = sorted(path for path in target.parent.glob("*.s8p") if path.is_file()) if target.parent.is_dir() else []
    checks = [
        _check("source .s8p exists", source.is_file(), str(source)),
        _check("source suffix is .s8p", source.suffix.lower() == ".s8p", source.name),
        _check("target sample directory exists", sample_dir.is_dir(), str(sample_dir)),
        _check("target packet summary exists", any(sample_dir.glob("hfss_*_single_variant_packet_summary.json")), str(sample_dir)),
        _check("target directory has no existing .s8p or overwrite enabled", bool(args.overwrite) or not existing, f"existing={len(existing)}"),
        _check("target file does not exist or overwrite enabled", bool(args.overwrite) or not target.exists(), str(target)),
    ]
    checks.extend(_touchstone_contract_checks(source, args))
    return checks


def _touchstone_contract_checks(path: Path, args: argparse.Namespace) -> list[dict[str, str]]:
    if not path.is_file():
        return [_check("source Touchstone parses", False, "missing")]
    try:
        data = load_touchstone(path)
    except Exception as exc:
        return [_check("source Touchstone parses", False, f"{type(exc).__name__}: {exc}")]
    freqs = data.freqs_hz
    ports = int(data.s_matrix.shape[1]) if data.s_matrix.ndim == 3 else 0
    reference = data.reference_impedance_ohm
    start_hz = float(args.expected_frequency_start_ghz) * 1.0e9
    stop_hz = float(args.expected_frequency_stop_ghz) * 1.0e9
    step_hz = float(args.expected_frequency_step_ghz) * 1.0e9
    tolerance_hz = float(args.frequency_tolerance_hz)
    expected_reference = float(args.expected_reference_ohm)
    reference_tolerance = float(args.reference_tolerance_ohm)
    step_ok = False
    max_step_error = None
    if len(freqs) >= 2:
        steps = [float(freqs[index + 1] - freqs[index]) for index in range(len(freqs) - 1)]
        max_step_error = max(abs(step - step_hz) for step in steps)
        step_ok = max_step_error <= tolerance_hz
    return [
        _check("source Touchstone parses", True, str(path)),
        _check("source port count is expected", ports == int(args.expected_ports), f"ports={ports}"),
        _check("source reference impedance is expected", _reference_matches(reference, expected_reference, reference_tolerance), f"reference_ohm={reference}"),
        _check("source frequency point count is expected", len(freqs) == int(args.expected_frequency_points), f"points={len(freqs)}"),
        _check("source frequency start is expected", bool(len(freqs)) and math.isclose(float(freqs[0]), start_hz, abs_tol=tolerance_hz), f"start_hz={float(freqs[0]) if len(freqs) else 'missing'}"),
        _check("source frequency stop is expected", bool(len(freqs)) and math.isclose(float(freqs[-1]), stop_hz, abs_tol=tolerance_hz), f"stop_hz={float(freqs[-1]) if len(freqs) else 'missing'}"),
        _check("source frequency step is expected", step_ok, "missing" if max_step_error is None else f"max_step_error_hz={max_step_error:g}"),
    ]


def _reference_matches(reference: Any, expected: float, tolerance: float) -> bool:
    try:
        import numpy as np

        values = np.asarray(reference, dtype=float)
        return bool(values.size > 0 and np.all(np.isclose(values, expected, atol=tolerance, rtol=0.0)))
    except Exception:
        return False


def _write_import_manifest(path: Path, source: Path, target: Path, args: argparse.Namespace) -> None:
    payload = {
        "schema": "rfic_transformer_hfss_s8p_manual_import_manifest.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_s8p": str(source),
        "target_s8p": str(target),
        "source_sha256": _sha256(source),
        "target_sha256": _sha256(target),
        "gate": args.gate,
        "variant": args.variant,
        "sample_id": args.sample_id,
        "method_note": "Manual staging only; run the existing postrun validator before using as EMX/HFSS evidence.",
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decision(executable: bool, copied: bool, apply: bool) -> tuple[str, str]:
    if not executable:
        return "FAIL", "DO_NOT_STAGE_HFSS_S8P_FIX_CHECKS"
    if not apply:
        return "DRY_RUN", "READY_TO_STAGE_HFSS_S8P_ADD_APPLY"
    if copied:
        return "PASS", "HFSS_S8P_STAGED_RUN_POSTRUN_VALIDATION"
    return "FAIL", "STAGING_DID_NOT_COPY"


def _next_commands(args: argparse.Namespace) -> list[str]:
    monitor = REPO_ROOT / "scripts" / "monitor_s8p_validation_to_million_autopipeline.py"
    return [f".venv/bin/python {monitor} --no-fail-exit"]


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS S8P Manual Import To Gate",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Source: `{summary['source_s8p']}`",
        f"- Target: `{summary['target_s8p']}`",
        f"- Copied: `{summary['copied']}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {check['status']}: {check['name']} - {check['detail']}" for check in summary["checks"])
    if summary["next_commands"]:
        lines.extend(["", "## Next Commands", ""])
        lines.extend(f"- `{command}`" for command in summary["next_commands"])
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
