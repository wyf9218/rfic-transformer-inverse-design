#!/usr/bin/env python3
"""Discover candidate EMX/Cadence paths on MARS without mutating configs.

This helper is intentionally read-only. It scans PATH, selected environment
variables, and bounded filesystem roots to produce candidate paths plus a
reviewable `patch_mars_config_paths.py` command. The generated command is only
a draft; strict preflight must still pass before launching EMX.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REQUIRED_PATH_KINDS = (
    "emx_binary",
    "emx_process_file",
    "cadence_install_root",
    "cadence_pdk_cds_lib",
    "cadence_layer_map",
)
TECH_LIB_ENV_VARS = ("CADENCE_TECH_LIB", "PDK_TECH_LIB", "TECH_LIB_NAME")
DISCOVERY_ENV_VARS = {
    "emx_binary": ("EMX_BINARY",),
    "emx_process_file": ("EMX_PROCESS_FILE", "PDK_PROC_FILE", "PROC_FILE"),
    "cadence_install_root": ("CADENCE_INSTALL_ROOT", "CDS_INST_DIR", "CADENCE_HOME", "CDSHOME"),
    "cadence_pdk_cds_lib": ("CADENCE_PDK_CDS_LIB", "PDK_CDS_LIB", "CDS_LIB_PATH"),
    "cadence_layer_map": ("CADENCE_LAYER_MAP", "PDK_LAYER_MAP", "LAYER_MAP"),
}
DEFAULT_ROOTS = (
    "/cae",
    "/software",
    "/apps",
    "/opt",
    "/usr/local",
    "/shared/research",
    "/home/researcher",
)
DEFAULT_HINT_COMMANDS = (
    "project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh",
)
DRY_RUN_PATH_MARKERS = (
    "/tmp/mars_dryrun",
    "mars_dryrun",
    "/tmp/dryrun",
    "dry-run",
)
NON_EMX_PROC_MARKERS = (
    "/patran",
    "/samcef",
    "pat3samcef",
    "samcef.proc",
)
PREFERRED_TSMC65_PROC_MARKERS = (
    "tsmc65_05_12_26",
    "rc_ircx_crn65g",
    "msrf_general_purpose_plus",
    "mim_typical.proc",
)
REJECTED_LAYER_MAP_SUFFIXES = (
    ".html",
    ".htm",
    ".pdf",
    ".txt",
    ".md",
    ".json",
)


@dataclass(frozen=True)
class Candidate:
    kind: str
    path: str
    source: str
    exists: bool
    is_file: bool
    is_dir: bool
    executable: bool
    score: int

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "source": self.source,
            "exists": self.exists,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
            "executable": self.executable,
            "score": self.score,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = _candidate_roots(args)
    hint_commands = _hint_command_paths(args)
    candidates, scan_stats = _discover_candidates(roots, args)
    _add_command_hint_candidates(candidates, hint_commands)
    candidates = _dedupe_candidates(candidates)
    tech_lib_candidates = _discover_tech_lib_candidates(candidates, args)
    selected = _select_best_candidates(candidates)
    rejected_dry_run_candidates = _dry_run_candidate_records(candidates)
    ready_to_patch = _selected_candidates_are_launch_ready(selected, tech_lib_candidates)
    overall_status = "PASS" if ready_to_patch else "INCOMPLETE"

    command = _patch_command(args.config, selected, tech_lib_candidates[0] if tech_lib_candidates else None)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "ready_to_patch": ready_to_patch,
        "config": str(Path(args.config).expanduser()) if args.config else None,
        "roots": [str(root) for root in roots],
        "hint_command_files": [str(path) for path in hint_commands],
        "max_depth": int(args.max_depth),
        "max_candidates_per_kind": int(args.max_candidates_per_kind),
        "max_paths_scanned": int(args.max_paths_scanned),
        "scan_stats": scan_stats,
        "missing_candidate_kinds": [kind for kind in REQUIRED_PATH_KINDS if kind not in selected],
        "rejected_dry_run_candidates": rejected_dry_run_candidates,
        "tech_lib_candidates": tech_lib_candidates,
        "selected_candidates": {kind: candidate.as_dict() for kind, candidate in selected.items()},
        "candidates": {
            kind: [candidate.as_dict() for candidate in _ranked(kind_candidates)[: int(args.max_candidates_per_kind)]]
            for kind, kind_candidates in candidates.items()
        },
        "review_required": [
            "This script is read-only and does not prove that EMX/Cadence will run.",
            "Review the selected candidates before running the suggested patch command.",
            "After patching, run patch_mars_config_paths.py --check-paths and preflight_dataset_config.py --check-emx-paths.",
        ],
        "suggested_patch_command": command,
    }
    summary_path = out_dir / "mars_emx_cadence_path_discovery_summary.json"
    report_path = out_dir / "mars_emx_cadence_path_discovery_report.md"
    command_path = out_dir / "mars_emx_cadence_path_patch_suggestion.sh"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    command_path.write_text(_render_command_script(command), encoding="utf-8")
    command_path.chmod(0o755)

    print(f"overall_status={overall_status}")
    print(f"ready_to_patch={ready_to_patch}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"patch_suggestion={command_path}")
    if summary["missing_candidate_kinds"]:
        print("missing_candidate_kinds=" + ",".join(str(item) for item in summary["missing_candidate_kinds"]))
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/mars_dataset_500_wideband_20260613.yaml",
        help="Config that will later be patched; it is not modified by this script.",
    )
    parser.add_argument("--out-dir", default="mars_emx_cadence_path_discovery_20260613")
    parser.add_argument(
        "--root",
        action="append",
        help="Filesystem root to scan. Can be supplied multiple times. Defaults to existing common MARS roots.",
    )
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--max-candidates-per-kind", type=int, default=20)
    parser.add_argument("--max-paths-scanned", type=int, default=200_000)
    parser.add_argument("--tech-lib-hint", help="Explicit Cadence tech-library name to include in the patch suggestion.")
    parser.add_argument(
        "--hint-command",
        action="append",
        help=(
            "Shell command file to mine for existing EMX binary and .proc path hints. "
            "Defaults to the target EMX wideband rerun command when present."
        ),
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _candidate_roots(args: argparse.Namespace) -> list[Path]:
    raw_roots: list[str] = []
    if args.root:
        raw_roots.extend(args.root)
    env_roots = os.environ.get("MARS_PATH_DISCOVERY_ROOTS")
    if env_roots:
        raw_roots.extend(item for item in env_roots.split(os.pathsep) if item)
    if not raw_roots:
        raw_roots.extend(DEFAULT_ROOTS)
        raw_roots.append(str(Path.cwd()))
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        path = Path(raw).expanduser()
        if not path.exists():
            continue
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            roots.append(Path(resolved))
    return roots


def _hint_command_paths(args: argparse.Namespace) -> list[Path]:
    raw_paths: list[str] = []
    if args.hint_command:
        raw_paths.extend(str(item) for item in args.hint_command)
    else:
        raw_paths.extend(DEFAULT_HINT_COMMANDS)
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not _safe_exists(path):
            continue
        resolved = _safe_resolve(path)
        if resolved not in seen:
            seen.add(resolved)
            paths.append(Path(resolved))
    return paths


def _discover_candidates(roots: list[Path], args: argparse.Namespace) -> tuple[dict[str, list[Candidate]], dict[str, int]]:
    candidates = {kind: [] for kind in REQUIRED_PATH_KINDS}
    _add_environment_candidates(candidates)
    _add_path_candidates(candidates)
    scanned = 0
    pruned_dirs = 0
    for root in roots:
        for path, depth in _walk_bounded(root, int(args.max_depth)):
            scanned += 1
            if scanned > int(args.max_paths_scanned):
                pruned_dirs += 1
                break
            _classify_path(path, depth, candidates)
        if scanned > int(args.max_paths_scanned):
            break
    return _dedupe_candidates(candidates), {"roots_scanned": len(roots), "paths_scanned": scanned, "pruned_roots": pruned_dirs}


def _add_command_hint_candidates(candidates: dict[str, list[Candidate]], command_paths: list[Path]) -> None:
    for command_path in command_paths:
        for tokens in _command_tokens(command_path):
            for token in tokens:
                path = Path(token).expanduser()
                if not path.is_absolute():
                    continue
                name = path.name.lower()
                if name in {"emx", "emx.exe"}:
                    candidates["emx_binary"].append(
                        _candidate(
                            "emx_binary",
                            path,
                            f"hint-command:{command_path.name}",
                            95 + _executable_bonus(path),
                        )
                    )
                if name.endswith(".proc"):
                    score = _emx_process_score(path, 93)
                    candidates["emx_process_file"].append(
                        _candidate("emx_process_file", path, f"hint-command:{command_path.name}", score)
                    )


def _command_tokens(command_path: Path) -> list[list[str]]:
    token_rows: list[list[str]] = []
    try:
        lines = command_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return token_rows
    continued = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if continued:
            line = continued + " " + line
            continued = ""
        if line.endswith("\\"):
            continued = line[:-1].strip()
            continue
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if tokens:
            token_rows.append(tokens)
    if continued:
        try:
            tokens = shlex.split(continued, comments=True, posix=True)
        except ValueError:
            tokens = []
        if tokens:
            token_rows.append(tokens)
    return token_rows


def _add_environment_candidates(candidates: dict[str, list[Candidate]]) -> None:
    for kind, env_names in DISCOVERY_ENV_VARS.items():
        for env_name in env_names:
            value = os.environ.get(env_name)
            if value:
                candidates[kind].append(_candidate(kind, Path(value).expanduser(), f"env:{env_name}", 200))


def _add_path_candidates(candidates: dict[str, list[Candidate]]) -> None:
    for name in ("emx", "emx.exe"):
        value = shutil.which(name)
        if value:
            candidates["emx_binary"].append(_candidate("emx_binary", Path(value), "PATH", 110))


def _walk_bounded(root: Path, max_depth: int) -> Iterable[tuple[Path, int]]:
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth or not _safe_is_dir(current) or _skip_dir(current):
            continue
        try:
            children = sorted(current.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            continue
        for child in reversed(children):
            stack.append((child, depth + 1))


def _skip_dir(path: Path) -> bool:
    return path.name in {".git", ".venv", "__pycache__", ".pytest_cache"}


def _classify_path(path: Path, depth: int, candidates: dict[str, list[Candidate]]) -> None:
    name = path.name.lower()
    is_file = _safe_is_file(path)
    is_dir = _safe_is_dir(path)
    if is_file and name in {"emx", "emx.exe"}:
        candidates["emx_binary"].append(_candidate("emx_binary", path, f"scan:depth={depth}", 80 + _executable_bonus(path)))
    if is_file and name.endswith(".proc"):
        candidates["emx_process_file"].append(
            _candidate("emx_process_file", path, f"scan:depth={depth}", _emx_process_score(path, 75))
        )
    if is_file and name == "cds.lib":
        candidates["cadence_pdk_cds_lib"].append(_candidate("cadence_pdk_cds_lib", path, f"scan:depth={depth}", 75))
    if is_file and _looks_like_layer_map(path):
        candidates["cadence_layer_map"].append(_candidate("cadence_layer_map", path, f"scan:depth={depth}", 70))
    if is_dir and (
        _safe_exists(path / "bin" / "virtuoso")
        or _safe_exists(path / "tools" / "dfII" / "bin" / "virtuoso")
    ):
        candidates["cadence_install_root"].append(
            _candidate("cadence_install_root", path, f"scan:depth={depth}", 80 + _cadence_install_bonus(path))
        )


def _looks_like_layer_map(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(REJECTED_LAYER_MAP_SUFFIXES):
        return False
    if name.endswith(".layermap"):
        return True
    if name in {"layers.layermap", "layer.map", "streamout.map", "strmout.map", "gds.map"}:
        return True
    if name.endswith(".map") and any(marker in name for marker in ("layer", "stream", "strm", "gds")):
        return True
    return False


def _emx_process_score(path: Path, base_score: int) -> int:
    text = str(path).lower()
    score = int(base_score)
    if any(marker in text for marker in PREFERRED_TSMC65_PROC_MARKERS):
        score += 80
    if "pdk" in text or "process" in text:
        score += 10
    if any(marker in text for marker in NON_EMX_PROC_MARKERS):
        score -= 200
    return score


def _candidate(kind: str, path: Path, source: str, score: int) -> Candidate:
    exists = _safe_exists(path)
    return Candidate(
        kind=kind,
        path=str(path),
        source=source,
        exists=exists,
        is_file=_safe_is_file(path),
        is_dir=_safe_is_dir(path),
        executable=os.access(path, os.X_OK),
        score=int(score),
    )


def _executable_bonus(path: Path) -> int:
    return 10 if os.access(path, os.X_OK) else 0


def _cadence_install_bonus(path: Path) -> int:
    required_tools = ("dbAccess", "strmin", "strmout")
    found_tools = sum(1 for tool in required_tools if _safe_exists(path / "bin" / tool) and os.access(path / "bin" / tool, os.X_OK))
    if found_tools == len(required_tools):
        return 50
    if found_tools:
        return 10 * found_tools
    return -30


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_resolve(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _dedupe_candidates(candidates: dict[str, list[Candidate]]) -> dict[str, list[Candidate]]:
    deduped: dict[str, list[Candidate]] = {}
    for kind, values in candidates.items():
        by_path: dict[str, Candidate] = {}
        for candidate in values:
            key = str(Path(candidate.path).expanduser())
            previous = by_path.get(key)
            if previous is None or candidate.score > previous.score:
                by_path[key] = candidate
        deduped[kind] = _ranked(list(by_path.values()))
    return deduped


def _ranked(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda item: (-item.score, item.path))


def _select_best_candidates(candidates: dict[str, list[Candidate]]) -> dict[str, Candidate]:
    selected: dict[str, Candidate] = {}
    for kind in REQUIRED_PATH_KINDS:
        ranked = _ranked(
            [
                candidate
                for candidate in candidates.get(kind, [])
                if candidate.exists and not _looks_like_dry_run_path(kind, candidate.path)
                and _candidate_has_valid_kind_path(kind, candidate.path)
            ]
        )
        if ranked:
            selected[kind] = ranked[0]
    return selected


def _candidate_has_valid_kind_path(kind: str, value: str) -> bool:
    path = Path(value)
    lowered = str(value).lower()
    if kind == "emx_process_file":
        if any(marker in lowered for marker in NON_EMX_PROC_MARKERS):
            return False
        return path.name.lower().endswith(".proc")
    if kind == "cadence_layer_map":
        return _looks_like_layer_map(path)
    return True


def _selected_candidates_are_launch_ready(selected: dict[str, Candidate], tech_lib_candidates: list[str]) -> bool:
    if not all(kind in selected for kind in REQUIRED_PATH_KINDS):
        return False
    if not tech_lib_candidates:
        return False
    cadence = selected.get("cadence_install_root")
    if cadence is None:
        return False
    return _cadence_install_bonus(Path(cadence.path)) >= 50


def _dry_run_candidate_records(candidates: dict[str, list[Candidate]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for kind, values in candidates.items():
        for candidate in values:
            if _looks_like_dry_run_path(kind, candidate.path):
                records.append(candidate.as_dict())
    return records


def _looks_like_dry_run_path(kind: str, value: str) -> bool:
    text = str(value).strip()
    lowered = text.lower()
    if kind == "emx_binary" and text == "/usr/bin/true":
        return True
    return any(marker in lowered for marker in DRY_RUN_PATH_MARKERS)


def _discover_tech_lib_candidates(candidates: dict[str, list[Candidate]], args: argparse.Namespace) -> list[str]:
    names: list[str] = []
    if args.tech_lib_hint:
        names.append(str(args.tech_lib_hint))
    for env_name in TECH_LIB_ENV_VARS:
        value = os.environ.get(env_name)
        if value:
            names.append(value)
    for candidate in candidates.get("cadence_pdk_cds_lib", []):
        names.extend(_parse_cds_lib_defines(Path(candidate.path)))
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        clean = str(name).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _parse_cds_lib_defines(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    names: list[str] = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0].upper() == "DEFINE":
            names.append(parts[1])
    return names


def _patch_command(config: str, selected: dict[str, Candidate], tech_lib: str | None) -> list[str]:
    config_path = str(Path(config).expanduser()) if config else "configs/mars_dataset_500_wideband_20260613.yaml"
    patched = str(Path(config_path).with_suffix(Path(config_path).suffix + ".patched.yaml"))
    cmd = [
        ".venv/bin/python",
        "scripts/patch_mars_config_paths.py",
        config_path,
        "--out-config",
        patched,
    ]
    for kind in REQUIRED_PATH_KINDS:
        value = selected.get(kind).path if kind in selected else f"<FILL_{kind.upper()}>"
        cmd.extend([f"--{kind.replace('_', '-')}", value])
    cmd.extend(["--cadence-tech-lib", tech_lib or "<FILL_CADENCE_TECH_LIB>"])
    cmd.append("--check-paths")
    return cmd


def _render_command_script(command: list[str]) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Review every path before running. This script is a suggestion only.",
            " ".join(shlex.quote(part) for part in command),
            "",
        ]
    )


def _render_report(summary: dict[str, object]) -> str:
    lines = [
        "# MARS EMX/Cadence Path Discovery",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Ready to patch: `{summary['ready_to_patch']}`",
        f"- Config: `{summary.get('config')}`",
        f"- Hint command files: `{summary.get('hint_command_files')}`",
        "",
        "## Selected Candidates",
        "",
        "| Field | Path | Source |",
        "| --- | --- | --- |",
    ]
    selected = summary.get("selected_candidates", {})
    if isinstance(selected, dict):
        for kind in REQUIRED_PATH_KINDS:
            item = selected.get(kind)
            if isinstance(item, dict):
                lines.append(f"| {kind} | `{item.get('path')}` | {item.get('source')} |")
            else:
                lines.append(f"| {kind} | **missing** |  |")
    lines.extend(
        [
            "",
            "## Cadence Tech-Lib Candidates",
            "",
        ]
    )
    tech_libs = summary.get("tech_lib_candidates", [])
    if isinstance(tech_libs, list) and tech_libs:
        for name in tech_libs:
            lines.append(f"- `{name}`")
    else:
        lines.append("- **missing**")
    rejected = summary.get("rejected_dry_run_candidates", [])
    lines.extend(["", "## Rejected Dry-Run Candidates", ""])
    if isinstance(rejected, list) and rejected:
        for item in rejected:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('kind')}`: `{item.get('path')}` from {item.get('source')}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Suggested Patch Command",
            "",
            "```bash",
            " ".join(shlex.quote(str(part)) for part in summary.get("suggested_patch_command", [])),
            "```",
            "",
            "## Required Follow-Up",
            "",
            "1. Review every selected path and tech-library name.",
            "2. Run the suggested patch command only after review.",
            "3. Run `scripts/preflight_dataset_config.py <patched-config> --check-emx-paths`.",
            "4. Launch the wideband pilot only after strict preflight is PASS.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
