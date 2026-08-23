#!/usr/bin/env python3
"""Build a small tool/config bundle to copy from local macOS to MARS.

This is the reverse of `package_mars_dataset_run.py`: it packages the scripts,
configs, and runbook needed to audit/pull the old final-500 run and launch the
wideband 500 pilot on MARS. It intentionally excludes generated datasets,
credentials, and large result artifacts.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SCRIPT_NAMES = (
    "build_mars_handoff_bundle.py",
    "verify_mars_handoff_install.py",
    "discover_and_verify_mars_emx_return.py",
    "watch_mars_emx_return.py",
    "verify_target_emx_postrun_package.py",
    "audit_mars_run_progress.py",
    "watch_mars_run_progress.py",
    "package_mars_dataset_run.py",
    "verify_mars_dataset_package.py",
    "discover_mars_emx_cadence_paths.py",
    "patch_mars_config_paths.py",
    "preflight_dataset_config.py",
    "prepare_mars_wideband_config.py",
    "prepare_target_emx_wideband_rerun.py",
    "prepare_target_emx_postrun_validation.py",
    "backfill_dataset_frequency_metadata.py",
    "backfill_ground_clearance_audit.py",
    "validate_dataset.py",
    "visualize_dataset_quality.py",
    "audit_dataset_touchstones.py",
    "audit_touchstone_transformer.py",
    "compare_emx_hfss_ads.py",
    "build_validation_chain_decision_card.py",
    "build_mars_next_action_packet.py",
    "run_accepted_emx_hfss_ads_validation.py",
    "verify_accepted_emx_hfss_ads_figures.py",
    "run_package_selfcheck_compare.py",
    "audit_geometry_quality.py",
    "audit_sampling_distribution.py",
    "extract_touchstone_response_features.py",
    "audit_response_feature_coverage.py",
    "audit_zin_coverage.py",
    "audit_zin_sweep_coverage.py",
    "select_hfss_validation_samples.py",
    "run_hfss_emx_validation_batch.py",
    "audit_248k_launch_readiness.py",
    "diagnose_cm_mismatch.py",
    "build_emx_first_validation_gate.py",
    "audit_photo_matched_vs_target_geometry.py",
    "run_dataset_quality_gates.py",
)
FIXED_BUNDLE_TIMESTAMP = int(datetime(2026, 6, 13, tzinfo=timezone.utc).timestamp())
FIXED_GENERATED_UTC = datetime.fromtimestamp(FIXED_BUNDLE_TIMESTAMP, timezone.utc).isoformat(timespec="seconds")

DEFAULT_CONFIG_NAMES = (
    "mars_dataset_248k_template.yaml",
    "mars_smoke10_safe_template.yaml",
    "mars_template.yaml",
    "response_target_envelopes_template_20260614.json",
    "zin_target_envelope_template_20260614.json",
)

DEFAULT_PACKAGE_SOURCE_NAMES = (
    "rfic_transformer_inverse_design/dataset.py",
    "rfic_transformer_inverse_design/execution/evaluator.py",
    "rfic_transformer_inverse_design/layout/export.py",
)

WIDEBAND_CONFIG_NAME = "mars_dataset_500_wideband_20260613.yaml"
WIDEBAND_COMMAND_NAME = "mars_dataset_500_wideband_20260613.commands.sh"

DEFAULT_PROJECT_FILES = (
    "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md",
    WIDEBAND_CONFIG_NAME,
    "mars_dataset_500_wideband_20260613.summary.json",
    "mars_dataset_500_wideband_20260613_preflight.md",
    "mars_dataset_500_wideband_20260613_preflight.json",
    "mars_dataset_500_wideband_20260613_preflight_strict_paths.md",
    "mars_dataset_500_wideband_20260613_preflight_strict_paths.json",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    staging = Path(args.staging_dir).expanduser().resolve() if args.staging_dir else out_path.with_suffix("").with_suffix("")
    if staging.exists() and any(staging.iterdir()) and not args.force:
        raise SystemExit(f"Staging directory is not empty; pass --force to replace: {staging}")
    if staging.exists() and args.force:
        _remove_tree_contents(staging)
    staging.mkdir(parents=True, exist_ok=True)

    inventory: list[dict[str, Any]] = []
    _copy_repo_scripts(repo_root, staging, inventory)
    _copy_repo_configs(repo_root, staging, inventory)
    _copy_repo_package_sources(repo_root, staging, inventory)
    _copy_project_files(project_root, staging, inventory)
    _add_mars_wideband_pilot_files(project_root, staging, inventory)
    _add_target_emx_wideband_rerun_files(project_root, staging, inventory)
    readme_path = staging / "README_MARS_HANDOFF_20260613.md"
    readme_path.write_text(_render_readme(), encoding="utf-8")
    inventory.append(_record_file(readme_path, staging))

    inventory_path = staging / "MARS_HANDOFF_INVENTORY_20260613.json"
    inventory_summary = {
        "generated_utc": FIXED_GENERATED_UTC,
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "file_count": len(inventory),
        "files": inventory,
        "limitations": [
            "This bundle contains local helper scripts and configs only.",
            "It does not contain credentials, MARS login state, generated datasets, or proof that any MARS run completed.",
            "Run preflight checks on MARS before launching EMX jobs.",
        ],
    }
    inventory_path.write_text(json.dumps(inventory_summary, indent=2), encoding="utf-8")

    sha_path = staging / "SHA256SUMS.txt"
    _write_sha_manifest(sha_path, staging)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_deterministic_tar(out_path, staging)
    tar_sha = _sha256(out_path)
    out_sha_path = out_path.with_suffix(out_path.suffix + ".sha256")
    out_sha_path.write_text(f"{tar_sha}  {out_path.name}\n", encoding="utf-8")

    print(f"bundle={out_path}")
    print(f"bundle_sha256={tar_sha}")
    print(f"bundle_sha256_file={out_sha_path}")
    print(f"staging_dir={staging}")
    print(f"inventory={inventory_path}")
    print(f"file_count={inventory_summary['file_count']}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_repo = Path(__file__).resolve().parents[1]
    default_project = default_repo.parent
    default_out = default_project / "mars_handoff_bundle_20260613.tar.gz"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(default_repo))
    parser.add_argument("--project-root", default=str(default_project))
    parser.add_argument("--out", default=str(default_out))
    parser.add_argument("--staging-dir")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _copy_repo_scripts(repo_root: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    target_dir = staging / "scripts"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in DEFAULT_SCRIPT_NAMES:
        _copy_one(repo_root / "scripts" / name, target_dir / name, staging, inventory)


def _copy_repo_configs(repo_root: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    target_dir = staging / "configs"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in DEFAULT_CONFIG_NAMES:
        _copy_one(repo_root / "configs" / name, target_dir / name, staging, inventory)


def _copy_repo_package_sources(repo_root: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    for name in DEFAULT_PACKAGE_SOURCE_NAMES:
        source = repo_root / name
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_one(source, target, staging, inventory)


def _copy_project_files(project_root: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    target_dir = staging / "project_runbook"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in DEFAULT_PROJECT_FILES:
        source = project_root / name
        if source.exists():
            _copy_one(source, target_dir / name, staging, inventory)


def _add_mars_wideband_pilot_files(project_root: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    config_source = project_root / WIDEBAND_CONFIG_NAME
    if not config_source.exists():
        return
    config_dest = staging / "configs" / WIDEBAND_CONFIG_NAME
    _copy_one(config_source, config_dest, staging, inventory)

    command_dest = staging / "project_runbook" / WIDEBAND_COMMAND_NAME
    command_dest.write_text(_render_mars_wideband_commands(), encoding="utf-8")
    command_dest.chmod(0o755)
    inventory.append(_record_file(command_dest, staging))


def _add_target_emx_wideband_rerun_files(project_root: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    source_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
    )
    if not source_dir.exists():
        return
    target_dir = staging / "project_runbook" / "target_emx_wideband_rerun_20260613"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "target_emx_wideband_rerun.commands.sh",
        "target_emx_wideband_rerun_command.json",
        "target_emx_wideband_frequency_grid.csv",
        "target_emx_wideband_rerun_summary.json",
        "target_emx_wideband_rerun_report.md",
        "target_emx_wideband_postrun_validation.commands.sh",
        "target_emx_wideband_postrun_validation_summary.json",
        "target_emx_wideband_postrun_validation_report.md",
    ):
        source = source_dir / name
        if source.exists():
            dest = target_dir / name
            _copy_one(source, dest, staging, inventory)
            if dest.suffix == ".sh":
                dest.chmod(0o755)


def _copy_one(source: Path, dest: Path, staging: Path, inventory: list[dict[str, Any]]) -> None:
    if not source.exists():
        raise SystemExit(f"Required handoff file is missing: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    inventory.append(_record_file(dest, staging))


def _record_file(path: Path, staging: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "relative_path": str(path.relative_to(staging)),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_sha_manifest(path: Path, staging: Path) -> None:
    files = sorted(item for item in staging.rglob("*") if item.is_file() and item.name != "SHA256SUMS.txt")
    with path.open("w", encoding="utf-8") as handle:
        for file_path in files:
            handle.write(f"{_sha256(file_path)}  {file_path.relative_to(staging)}\n")


def _write_deterministic_tar(out_path: Path, staging: Path) -> None:
    paths = [staging, *sorted(staging.rglob("*"), key=lambda item: item.relative_to(staging).as_posix())]
    with out_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for path in paths:
                    arcname = Path(staging.name) if path == staging else Path(staging.name) / path.relative_to(staging)
                    info = tar.gettarinfo(str(path), arcname=arcname.as_posix())
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = FIXED_BUNDLE_TIMESTAMP
                    info.pax_headers = {}
                    if path.is_dir():
                        info.mode = 0o755
                        tar.addfile(info)
                    elif path.is_file():
                        info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                        with path.open("rb") as handle:
                            tar.addfile(info, handle)


def _render_readme() -> str:
    return """# MARS Handoff Bundle

This bundle contains helper scripts, wideband pilot config drafts, and the runbook
needed to audit/pull the old final-500 run and prepare the 5-50 GHz wideband 500
pilot on MARS.

## First Checks On MARS

```bash
cd <unpacked-bundle>
sha256sum -c SHA256SUMS.txt
python3 scripts/audit_mars_run_progress.py --help
python3 scripts/watch_mars_run_progress.py --help
python3 scripts/verify_mars_dataset_package.py --help
python3 scripts/discover_and_verify_mars_emx_return.py --help
python3 scripts/watch_mars_emx_return.py --help
python3 scripts/verify_target_emx_postrun_package.py --help
python3 scripts/compare_emx_hfss_ads.py --help
python3 scripts/build_validation_chain_decision_card.py --help
python3 scripts/build_mars_next_action_packet.py --help
python3 scripts/run_accepted_emx_hfss_ads_validation.py --help
python3 scripts/verify_accepted_emx_hfss_ads_figures.py --help
python3 scripts/run_hfss_emx_validation_batch.py --help
python3 scripts/audit_248k_launch_readiness.py --help
python3 scripts/build_emx_first_validation_gate.py --help
python3 scripts/audit_photo_matched_vs_target_geometry.py --help
python3 scripts/prepare_target_emx_wideband_rerun.py --help
python3 scripts/prepare_target_emx_postrun_validation.py --help
python3 scripts/discover_mars_emx_cadence_paths.py --help
python3 scripts/preflight_dataset_config.py --help
python3 scripts/run_dataset_quality_gates.py --help
python3 scripts/verify_mars_handoff_install.py . --out-dir mars_handoff_verify_20260613
```

## Install Into The MARS Project Checkout

Run these commands from the verified unpacked bundle directory:

```bash
MARS_PROJECT=/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design
rsync -a scripts/ "$MARS_PROJECT/scripts/"
rsync -a configs/ "$MARS_PROJECT/configs/"
rsync -a rfic_transformer_inverse_design/ "$MARS_PROJECT/rfic_transformer_inverse_design/"
mkdir -p "$MARS_PROJECT/project_runbook"
rsync -a project_runbook/ "$MARS_PROJECT/project_runbook/"
cd "$MARS_PROJECT"
```

Optionally run the read-only discovery helper to collect candidate EMX/Cadence
paths and a reviewable patch command:

```bash
python3 scripts/discover_mars_emx_cadence_paths.py \
  --config configs/mars_dataset_500_wideband_20260613.yaml \
  --hint-command project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh \
  --out-dir mars_emx_cadence_path_discovery_20260613 \
  --no-fail-exit
```

Then patch real MARS EMX/Cadence paths in
`configs/mars_dataset_500_wideband_20260613.yaml`, run strict preflight, and run:

```bash
bash project_runbook/mars_dataset_500_wideband_20260613.commands.sh
```

For the current target sample EMX reference recovery, run:

```bash
bash project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh
bash project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_postrun_validation.commands.sh
```

## Important

- This bundle does not include credentials or generated datasets.
- It includes the package source files needed for automatic signal-to-shield
  clearance sidecar generation and final500 clearance-audit aggregation.
- Patch real MARS EMX/Cadence paths before launching EMX.
- Use `project_runbook/mars_dataset_500_wideband_20260613.commands.sh` from the
  repository root on MARS; it references `configs/mars_dataset_500_wideband_20260613.yaml`
  and does not depend on local macOS paths.
- Do not start 248k until the wideband 500 pilot passes file, geometry, sampling,
  Touchstone, Zin, and sampled HFSS/ADS-vs-EMX gates.
- The target-sample EMX rerun command only regenerates the candidate `.s4p`; run
  `project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_postrun_validation.commands.sh`
  on the resulting file before ADS plotting or HFSS comparison. It runs the
  Touchstone physical gate and the EMX-first gate, then packages the evidence.
- After pulling EMX-first/HFSS/final-runner summaries, run
  `scripts/build_validation_chain_decision_card.py` so the EMX-first, HFSS
  diagnostic, and final comparison boundary remains machine-readable.
- Use `scripts/build_mars_next_action_packet.py` locally before the next
  Guacamole/MARS session to regenerate the command checklist from current
  validation-chain, target rerun, post-run validation, and handoff evidence.
- Use `scripts/audit_248k_launch_readiness.py` before launching 248k; it must
  report PASS before the generated launch commands are used.
- Read `project_runbook/MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md` for the
  exact command order.
"""


def _render_mars_wideband_commands() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail

CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}
RUN_DIR=${RUN_DIR:-runs/dataset500_wideband_grounded_20260613}

# 1) Fill real MARS EMX/Cadence paths in $CONFIG, then require strict path preflight.
.venv/bin/python scripts/preflight_dataset_config.py "$CONFIG" \
  --check-emx-paths \
  --report "${RUN_DIR}_config_preflight.md" \
  --summary "${RUN_DIR}_config_preflight.json"

# 2) Launch the wideband sample-dataset pilot only after strict preflight passes.
MPLCONFIGDIR=$PWD/.mplconfig \
.venv/bin/python -m rfic_transformer_inverse_design.interfaces.cli sample-dataset \
  --config "$CONFIG" \
  --count 500 \
  --batch-size 10 \
  --sampler lhs_optimized \
  --seed 20260613 \
  --z-load-ohm 50.0 \
  --out-dir "$RUN_DIR" \
  --fail-on-error

# 3) After the pilot finishes, prove file completeness and EMX command semantics.
.venv/bin/python scripts/audit_mars_run_progress.py "$RUN_DIR" \
  --out-dir "$RUN_DIR/mars_run_progress_audit_20260613" \
  --expected-count 500 \
  --expected-frequency-start-ghz 5.0 \
  --expected-frequency-stop-ghz 50.0 \
  --expected-frequency-step-ghz 0.1 \
  --expected-frequency-points 451 \
  --max-touchstone-frequency-checks 500 \
  --require-clearance-audit \
  --require-geometry-quality \
  --internal-angle-deg 135 \
  --terminal-angle-deg 90 \
  --require-emx-command \
  --expected-port-mode single_ended_shield_grounded \
  --expected-pin-purpose 51

# 4) Run all local acceptance gates before using the data.
.venv/bin/python scripts/run_dataset_quality_gates.py "$RUN_DIR" \
  --out-dir "$RUN_DIR/dataset_quality_gates_20260613" \
  --require-emx \
  --expected-port-mode single_ended_shield_grounded \
  --expected-pin-purpose 51 \
  --require-clearance-audit \
  --expected-frequency-start-ghz 5.0 \
  --expected-frequency-stop-ghz 50.0 \
  --expected-frequency-step-ghz 0.1 \
  --expected-frequency-points 451 \
  --max-touchstone-frequency-checks 500 \
  --audit-sampling-distribution \
  --sampling-require-uniform-closer-than-normal \
  --sampling-min-uniform-vs-normal-fields-fraction 0.8 \
  --sampling-min-histogram-entropy-frac 0.85 \
  --sampling-max-min-norm 0.05 \
  --sampling-min-max-norm 0.95 \
  --sampling-space-filling-strata 20 \
  --sampling-max-space-filling-empty-strata-frac 0 \
  --sampling-max-space-filling-duplicate-frac 0 \
  --touchstone-all \
  --touchstone-target-frequency-ghz 15 \
  --touchstone-positive-window-start-ghz 5.0 \
  --touchstone-positive-window-stop-ghz 30 \
  --touchstone-shape-window-start-ghz 5.0 \
  --touchstone-shape-window-stop-ghz 30 \
  --touchstone-max-shape-spike-ratio 4 \
  --touchstone-max-shape-relative-step 0.25 \
  --extract-response-features \
  --audit-response-feature-coverage \
  --response-require-cm \
  --response-min-valid-count 500 \
  --audit-zin-coverage \
  --zin-min-valid-count 500 \
  --audit-zin-sweep-coverage \
  --zin-sweep-frequency-slices-ghz 5,10,15,20,25,30,35,40,45,50 \
  --zin-sweep-min-valid-count 500 \
  --zin-sweep-min-entropy-frac 0.70 \
  --select-hfss-samples \
  --hfss-sample-count 8
"""


def _remove_tree_contents(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _remove_tree_contents(child)
            child.rmdir()
        else:
            child.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
