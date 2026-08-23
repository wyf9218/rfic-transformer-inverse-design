#!/usr/bin/env python3
"""Discover and verify a MARS-returned target EMX S4P package.

This helper is deliberately conservative. It only discovers files, checks the
candidate Touchstone grid, and dispatches the stricter post-run import verifier.
It does not generate simulator data and it does not accept a HFSS comparison by
itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_TARGET_DIR = (
    DEFAULT_PROJECT_ROOT
    / "hfss_validation"
    / "final500_ec6698dfc575950b"
    / "target_emx_postrun_download_20260613"
)
DEFAULT_OUT_DIR = (
    DEFAULT_PROJECT_ROOT
    / "hfss_validation"
    / "final500_ec6698dfc575950b"
    / "mars_emx_return_discovery_20260614"
)
DEFAULT_TARBALL_PATTERNS = (
    "validation_20260613_transfer.tar.gz",
    "*validation*_transfer.tar.gz",
)
DEFAULT_S4P_PATTERNS = (
    "emx.s4p",
    "*emx*.s4p",
    "*.s4p",
)
DEFAULT_EXPECTED_SAMPLE_ID = "ec6698dfc575950b"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    roots = _search_roots(args.search_root)
    checks: list[Check] = []
    checks.extend(_search_root_checks(roots))

    tarball_candidates = _discover_tarballs(roots, args.tarball_pattern, args)
    s4p_candidates = _discover_s4p_candidates(roots, args)
    selected_s4p = _select_s4p_candidate(s4p_candidates)
    selected_tarball = _select_tarball_candidate(tarball_candidates, selected_s4p)

    checks.append(_candidate_selection_check("MARS validation tarball", selected_tarball, tarball_candidates))
    checks.append(_candidate_selection_check("MARS returned EMX S4P", selected_s4p, s4p_candidates))

    verifier_result: dict[str, Any] | None = None
    verifier_command = _verifier_command(args, out_dir, selected_tarball, selected_s4p)
    if selected_tarball and selected_s4p:
        if args.dry_run or args.skip_verifier:
            checks.append(Check("WARN", "post-run import verifier", "not run because --dry-run/--skip-verifier was supplied"))
        else:
            verifier_result = _run_verifier(verifier_command, out_dir)
            checks.append(_verifier_check(verifier_result))
    else:
        checks.append(Check("WARN", "post-run import verifier", "not run because tarball or EMX S4P is not selected"))

    overall_status, decision = _overall_decision(checks, selected_tarball, selected_s4p, verifier_result, args)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "search_roots": [str(root) for root in roots],
        "selected": {
            "tarball": selected_tarball,
            "emx_s4p": selected_s4p,
        },
        "verifier_command": verifier_command,
        "verifier_result": verifier_result,
        "checks": [check.as_dict() for check in checks],
        "status_counts": _status_counts(checks),
        "tarball_candidates": tarball_candidates,
        "s4p_candidates": s4p_candidates,
        "requirements": {
            "expected_ports": int(args.expected_ports),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
            "frequency_tolerance_hz": float(args.frequency_tolerance_hz),
            "expected_sample_id": str(args.expected_sample_id),
            "reject_hfss_source_kind": True,
        },
        "method_notes": [
            "This script is a discovery and dispatch gate only; it does not run EMX, HFSS, or ADS.",
            "A candidate S4P is selected only when it is four-port, not HFSS-labeled, and has the exact 5-50 GHz / 0.1 GHz / 451-point grid.",
            "The returned S4P and validation tarball path must contain the expected target sample id unless --expected-sample-id is set to an empty string.",
            "The selected tarball must have a matching external SHA256 record before the stricter post-run import verifier is run.",
            "Final EMX acceptance still requires verify_target_emx_postrun_package.py to return decision=ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS.",
            "A WAITING_FOR_MARS_RETURN result is intentional when the real wideband EMX S4P or validation tarball has not been pulled back locally.",
        ],
    }
    summary_path = out_dir / "mars_emx_return_discovery_summary.json"
    report_path = out_dir / "MARS_EMX_RETURN_DISCOVERY_REPORT.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if verifier_command:
        print("verifier_command=" + " ".join(shlex.quote(part) for part in verifier_command))
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 0 if overall_status == "PASS" or args.no_fail_exit or overall_status == "WAITING_FOR_MARS_RETURN" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", action="append", help="Directory to scan; may be supplied more than once")
    parser.add_argument("--tarball-pattern", action="append", help="Tarball filename glob; may be supplied more than once")
    parser.add_argument("--s4p-pattern", action="append", help="S4P filename glob; may be supplied more than once")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--expected-frequency-points", type=int, default=451)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument(
        "--expected-sample-id",
        default=DEFAULT_EXPECTED_SAMPLE_ID,
        help="Target sample id that must appear in selected returned EMX/tarball paths; empty disables this check.",
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable used to run the verifier")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--dry-run", action="store_true", help="Discover files and write the verifier command without running it")
    parser.add_argument("--skip-verifier", action="store_true", help="Alias-like guard for command generation only")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _search_roots(raw_roots: list[str] | None) -> list[Path]:
    roots = raw_roots or [str(DEFAULT_TARGET_DIR)]
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw in roots:
        path = Path(raw).expanduser().resolve()
        if path not in seen:
            seen.add(path)
            resolved.append(path)
    return resolved


def _search_root_checks(roots: Iterable[Path]) -> list[Check]:
    checks: list[Check] = []
    for root in roots:
        if root.is_dir():
            checks.append(Check("PASS", "search root", str(root)))
        else:
            checks.append(Check("WARN", "search root", f"missing directory: {root}"))
    return checks


def _discover_tarballs(roots: list[Path], patterns: list[str] | None, args: argparse.Namespace) -> list[dict[str, Any]]:
    paths = _discover_paths(roots, tuple(patterns or DEFAULT_TARBALL_PATTERNS))
    records = [_tarball_candidate(path, args) for path in paths]
    return sorted(records, key=lambda row: (row["status"] != "PASS", -float(row.get("mtime", 0.0)), row["path"]))


def _discover_s4p_candidates(roots: list[Path], args: argparse.Namespace) -> list[dict[str, Any]]:
    paths = _discover_paths(roots, tuple(args.s4p_pattern or DEFAULT_S4P_PATTERNS))
    records = [_s4p_candidate(path, args) for path in paths]
    return sorted(records, key=lambda row: (row["status"] != "PASS", _candidate_rank(row), -float(row.get("mtime", 0.0)), row["path"]))


def _discover_paths(roots: list[Path], patterns: tuple[str, ...]) -> list[Path]:
    found: dict[Path, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            try:
                iterator = root.rglob(pattern)
                for path in iterator:
                    if path.is_file():
                        try:
                            resolved = path.resolve()
                        except OSError:
                            continue
                        found[resolved] = resolved
            except OSError:
                continue
    return sorted(found.values())


def _tarball_candidate(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    sha_path = Path(str(path) + ".sha256")
    record: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "mtime": path.stat().st_mtime if path.exists() else 0.0,
        "sha_record": str(sha_path),
        "status": "FAIL",
        "expected_sample_id": str(args.expected_sample_id),
        "sample_status": "PASS",
        "reasons": [],
    }
    reasons: list[str] = []
    sample_failures = _sample_id_failures(path, args)
    if sample_failures:
        record["sample_status"] = "FAIL"
        reasons.extend(sample_failures)
    if not path.is_file() or path.stat().st_size <= 0:
        reasons.append("tarball is missing or empty")
    if not sha_path.is_file() or sha_path.stat().st_size <= 0:
        reasons.append("external SHA256 record is missing or empty")
    else:
        try:
            expected = sha_path.read_text(encoding="utf-8").split()[0]
            actual = _sha256(path)
            record["sha256"] = actual
            record["sha256_expected"] = expected
            if expected != actual:
                reasons.append(f"tarball SHA mismatch expected={expected} actual={actual}")
        except Exception as exc:  # noqa: BLE001 - persist exact file issue.
            reasons.append(f"could not verify tarball SHA: {type(exc).__name__}: {exc}")
    record["reasons"] = reasons
    record["status"] = "PASS" if not reasons else "FAIL"
    return record


def _s4p_candidate(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "mtime": path.stat().st_mtime if path.exists() else 0.0,
        "status": "FAIL",
        "source_kind": _source_kind(path),
        "source_status": "WARN",
        "expected_sample_id": str(args.expected_sample_id),
        "sample_status": "PASS",
        "reasons": [],
    }
    reasons: list[str] = []
    if not path.is_file() or path.stat().st_size <= 0:
        reasons.append("S4P is missing or empty")
        record["reasons"] = reasons
        return record
    sample_failures = _sample_id_failures(path, args)
    if sample_failures:
        record["sample_status"] = "FAIL"
        reasons.extend(sample_failures)
    if record["source_kind"] == "HFSS":
        reasons.append("candidate is HFSS-labeled; EMX reference required")
        record["source_status"] = "FAIL"
    elif record["source_kind"] == "EMX":
        record["source_status"] = "PASS"
    try:
        touchstone = load_touchstone(path)
        freqs = np.asarray(touchstone.freqs_hz, dtype=float)
        record["port_count"] = int(touchstone.num_ports)
        record["frequency"] = _frequency_record(freqs)
        reasons.extend(_frequency_failures(freqs, touchstone.num_ports, args))
        record["sha256"] = _sha256(path)
    except Exception as exc:  # noqa: BLE001 - report exact parse failure.
        reasons.append(f"Touchstone parse failed: {type(exc).__name__}: {exc}")
    record["reasons"] = reasons
    record["status"] = "PASS" if not reasons else "FAIL"
    return record


def _source_kind(path: Path) -> str:
    text_parts = [" ".join(path.parts)]
    try:
        comments: list[str] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if line.startswith("!"):
                    comments.append(line[1:].strip())
                    continue
                if line.startswith("#") or not line:
                    continue
                break
        text_parts.append(" ".join(comments[:100]))
    except OSError:
        pass
    lowered = " ".join(text_parts).lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if "hfss" in lowered or ".aedt" in lowered or "ansys" in lowered:
        return "HFSS"
    if any(token == "emx" or token.startswith("emx") for token in tokens):
        return "EMX"
    if "advanced design system" in lowered or "keysight" in lowered or any(token == "ads" for token in tokens):
        return "ADS"
    return "UNKNOWN"


def _sample_id_failures(path: Path, args: argparse.Namespace) -> list[str]:
    expected = str(args.expected_sample_id or "").strip()
    if not expected:
        return []
    if expected.lower() not in str(path).lower():
        return [f"expected sample id {expected} not found in path"]
    return []


def _frequency_record(freqs: np.ndarray) -> dict[str, Any]:
    record: dict[str, Any] = {
        "points": int(len(freqs)),
        "start_hz": float(freqs[0]) if len(freqs) else None,
        "stop_hz": float(freqs[-1]) if len(freqs) else None,
        "start_ghz": float(freqs[0] / 1.0e9) if len(freqs) else None,
        "stop_ghz": float(freqs[-1] / 1.0e9) if len(freqs) else None,
    }
    if len(freqs) > 1:
        steps = np.diff(freqs)
        record.update(
            {
                "min_step_hz": float(np.min(steps)),
                "max_step_hz": float(np.max(steps)),
                "median_step_hz": float(np.median(steps)),
            }
        )
    return record


def _frequency_failures(freqs: np.ndarray, port_count: int, args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    tolerance = float(args.frequency_tolerance_hz)
    expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
    expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
    expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
    if int(port_count) != int(args.expected_ports):
        failures.append(f"port count expected {args.expected_ports}, got {port_count}")
    if len(freqs) != int(args.expected_frequency_points):
        failures.append(f"frequency points expected {args.expected_frequency_points}, got {len(freqs)}")
    if len(freqs) == 0:
        failures.append("frequency grid is empty")
        return failures
    if abs(float(freqs[0]) - expected_start) > tolerance:
        failures.append(f"frequency start expected {expected_start:g} Hz, got {float(freqs[0]):g} Hz")
    if abs(float(freqs[-1]) - expected_stop) > tolerance:
        failures.append(f"frequency stop expected {expected_stop:g} Hz, got {float(freqs[-1]):g} Hz")
    if len(freqs) > 1:
        steps = np.diff(freqs)
        if np.any(steps <= 0.0):
            failures.append("frequency grid is not strictly increasing")
        bad_steps = [float(step) for step in steps if abs(float(step) - expected_step) > tolerance]
        if bad_steps:
            failures.append(f"frequency step expected {expected_step:g} Hz, bad_step_count={len(bad_steps)}")
    return failures


def _candidate_rank(record: dict[str, Any]) -> int:
    text = str(record.get("path", "")).lower()
    score = 0
    if "ec6698dfc575950b" not in text:
        score += 4
    if "emx_wideband_5_50_0p1" not in text:
        score += 2
    if not text.endswith("/emx.s4p"):
        score += 1
    return score


def _select_s4p_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    passing = [candidate for candidate in candidates if candidate.get("status") == "PASS"]
    return passing[0] if passing else None


def _select_tarball_candidate(
    candidates: list[dict[str, Any]],
    selected_s4p: dict[str, Any] | None,
) -> dict[str, Any] | None:
    passing = [candidate for candidate in candidates if candidate.get("status") == "PASS"]
    if not passing:
        return None
    if selected_s4p:
        s4p_parent = Path(str(selected_s4p["path"])).parent
        same_parent = [candidate for candidate in passing if Path(str(candidate["path"])).parent == s4p_parent]
        if same_parent:
            return same_parent[0]
    return passing[0]


def _candidate_selection_check(
    name: str,
    selected: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> Check:
    if selected:
        return Check("PASS", name, str(selected.get("path")))
    rejected = len(candidates)
    if rejected:
        return Check("WARN", name, f"no passing candidate selected; rejected_candidates={rejected}")
    return Check("WARN", name, "no candidate file found")


def _verifier_command(
    args: argparse.Namespace,
    out_dir: Path,
    selected_tarball: dict[str, Any] | None,
    selected_s4p: dict[str, Any] | None,
) -> list[str]:
    if not selected_tarball or not selected_s4p:
        return []
    repo_root = Path(args.repo_root).expanduser().resolve()
    verifier = repo_root / "scripts" / "verify_target_emx_postrun_package.py"
    verifier_out = out_dir / "target_emx_postrun_import"
    return [
        str(Path(args.python).expanduser()),
        str(verifier),
        "--tarball",
        str(selected_tarball["path"]),
        "--sha-record",
        str(selected_tarball["sha_record"]),
        "--emx-s4p",
        str(selected_s4p["path"]),
        "--require-emx-s4p",
        "--out-dir",
        str(verifier_out),
        "--no-fail-exit",
    ]


def _run_verifier(command: list[str], out_dir: Path) -> dict[str, Any]:
    verifier_out = out_dir / "target_emx_postrun_import"
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    summary_path = verifier_out / "target_emx_postrun_import_summary.json"
    summary: dict[str, Any] | None = None
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            summary = {"_parse_error": str(exc)}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _verifier_check(result: dict[str, Any]) -> Check:
    summary = result.get("summary") or {}
    if result.get("returncode") == 0 and summary.get("decision") == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS":
        return Check("PASS", "post-run import verifier", f"accepted: {result.get('summary_path')}")
    return Check(
        "FAIL",
        "post-run import verifier",
        f"returncode={result.get('returncode')}, decision={summary.get('decision')}, summary={result.get('summary_path')}",
    )


def _overall_decision(
    checks: list[Check],
    selected_tarball: dict[str, Any] | None,
    selected_s4p: dict[str, Any] | None,
    verifier_result: dict[str, Any] | None,
    args: argparse.Namespace,
) -> tuple[str, str]:
    if verifier_result:
        summary = verifier_result.get("summary") or {}
        if summary.get("overall_status") == "PASS" and summary.get("decision") == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS":
            return "PASS", "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS"
        return "FAIL", "DO_NOT_IMPORT_TARGET_EMX_REFERENCE"
    if selected_tarball and selected_s4p:
        if args.dry_run or args.skip_verifier:
            return "READY_TO_VERIFY", "RUN_TARGET_EMX_POSTRUN_IMPORT_VERIFIER"
        return "FAIL", "DO_NOT_IMPORT_TARGET_EMX_REFERENCE"
    if any(check.status == "FAIL" for check in checks):
        return "WAITING_FOR_MARS_RETURN", "WAIT_FOR_VALID_MARS_WIDEBAND_EMX_RETURN"
    return "WAITING_FOR_MARS_RETURN", "WAIT_FOR_MARS_WIDEBAND_EMX_RETURN"


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return dict(sorted(counts.items()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS EMX Return Discovery Report",
        "",
        f"- overall_status: `{summary['overall_status']}`",
        f"- decision: `{summary['decision']}`",
        "",
        "## Selected Files",
        "",
    ]
    selected = summary.get("selected") or {}
    for key in ("tarball", "emx_s4p"):
        value = selected.get(key)
        path = value.get("path") if isinstance(value, dict) else None
        lines.append(f"- {key}: `{path or 'NOT_SELECTED'}`")
    lines.extend(["", "## Checks", ""])
    for check in summary.get("checks", []):
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    command = summary.get("verifier_command") or []
    if command:
        lines.extend(
            [
                "",
                "## Verifier Command",
                "",
                "```bash",
                " ".join(shlex.quote(str(part)) for part in command),
                "```",
            ]
        )
    lines.extend(["", "## S4P Candidates", ""])
    for candidate in summary.get("s4p_candidates", [])[:20]:
        freq = candidate.get("frequency") or {}
        reasons = "; ".join(candidate.get("reasons") or []) or "accepted"
        lines.append(
            "- "
            f"{candidate.get('status')}: `{candidate.get('path')}` "
            f"ports={candidate.get('port_count')} "
            f"points={freq.get('points')} "
            f"start={freq.get('start_ghz')} GHz "
            f"stop={freq.get('stop_ghz')} GHz "
            f"source={candidate.get('source_kind')} "
            f"reason={reasons}"
        )
    lines.extend(["", "## Method Notes", ""])
    for note in summary.get("method_notes", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
