from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import shutil
import sys
import tarfile
import zipfile
import warnings
from unittest import mock

from PIL import Image, ImageDraw


def _load_delivery_audit_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_delivery_package.py"
    spec = importlib.util.spec_from_file_location("audit_delivery_package_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _block_yaml_import(name, *args, **kwargs):
    if name == "yaml":
        raise ModuleNotFoundError("No module named yaml")
    return _REAL_IMPORT(name, *args, **kwargs)


_REAL_IMPORT = __import__


def _target_emx_frequency_tokens(*, wrong: bool = False) -> str:
    if wrong:
        return "5000000000 5100000000 50000000000"
    return " ".join(str(freq) for freq in range(5_000_000_000, 50_000_000_000 + 100_000_000, 100_000_000))


def _required_validation_script_names() -> tuple[str, ...]:
    audit = _load_delivery_audit_module()
    return tuple(audit.REQUIRED_VALIDATION_SCRIPTS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_png(path: Path, *, blank: bool = False, size: tuple[int, int] = (16, 16)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    image = Image.new("RGB", (width, height), "white")
    if not blank:
        draw = ImageDraw.Draw(image)
        for x in range(0, width, max(4, width // 12)):
            draw.line([(x, 0), (x, height - 1)], fill=(220, 220, 220))
        for y in range(0, height, max(4, height // 8)):
            draw.line([(0, y), (width - 1, y)], fill=(220, 220, 220))
        for offset, color in ((0, (20, 80, 180)), (height // 6, (180, 40, 60)), (height // 3, (30, 130, 70))):
            points = []
            for x in range(width):
                normalized = x / max(1, width - 1)
                y = int(height * (0.7 - 0.45 * normalized) + offset / 2)
                y += (x % max(3, width // 20)) - max(3, width // 20) // 2
                points.append((x, max(0, min(height - 1, y))))
            draw.line(points, fill=color, width=max(1, width // 80))
    image.save(path, compress_level=1)


def _write_sha_manifest(root: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
    lines = [f"{_sha256(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_handoff_tar(package: Path) -> None:
    handoff = package / "mars_handoff_bundle_20260613"
    tar_path = package / "mars_handoff_bundle_20260613.tar.gz"
    if tar_path.exists():
        tar_path.unlink()
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(handoff, arcname=handoff.name)
    (package / "mars_handoff_bundle_20260613.tar.gz.sha256").write_text(
        f"{_sha256(tar_path)}  {tar_path.name}\n",
        encoding="utf-8",
    )


def _write_zip(package: Path, zip_path: Path, zip_sha: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(Path(package.name) / path.relative_to(package)))
    zip_sha.write_text(f"{_sha256(zip_path)}  {zip_path.name}\n", encoding="utf-8")


def _refresh_delivery_archives(package: Path, zip_path: Path, zip_sha: Path) -> None:
    _write_handoff_tar(package)
    _write_sha_manifest(package)
    _write_zip(package, zip_path, zip_sha)


def _make_delivery_fixture(root: Path) -> tuple[Path, Path, Path]:
    package = root / "package"
    package.mkdir()

    validation_scripts = package / "validation_scripts"
    validation_scripts.mkdir()
    required_scripts = _required_validation_script_names()
    for name in required_scripts:
        (validation_scripts / name).write_text(f'"""fixture script: {name}"""\nVALUE = 1\n', encoding="utf-8")
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "package_mars_dataset_run.py",
        validation_scripts / "package_mars_dataset_run.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "verify_mars_dataset_package.py",
        validation_scripts / "verify_mars_dataset_package.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "run_dataset_quality_gates.py",
        validation_scripts / "run_dataset_quality_gates.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "audit_zin_coverage.py",
        validation_scripts / "audit_zin_coverage.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "audit_response_feature_coverage.py",
        validation_scripts / "audit_response_feature_coverage.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "run_hfss_emx_validation_batch.py",
        validation_scripts / "run_hfss_emx_validation_batch.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "build_validation_chain_decision_card.py",
        validation_scripts / "build_validation_chain_decision_card.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "run_accepted_emx_hfss_ads_validation.py",
        validation_scripts / "run_accepted_emx_hfss_ads_validation.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "verify_accepted_emx_hfss_ads_figures.py",
        validation_scripts / "verify_accepted_emx_hfss_ads_figures.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "audit_248k_launch_readiness.py",
        validation_scripts / "audit_248k_launch_readiness.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "build_emx_first_validation_gate.py",
        validation_scripts / "build_emx_first_validation_gate.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "verify_target_emx_postrun_package.py",
        validation_scripts / "verify_target_emx_postrun_package.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "audit_touchstone_transformer.py",
        validation_scripts / "audit_touchstone_transformer.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "watch_mars_emx_return.py",
        validation_scripts / "watch_mars_emx_return.py",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts" / "run_local_project_health_check.py",
        validation_scripts / "run_local_project_health_check.py",
    )
    for index in range(max(0, 33 - len(required_scripts))):
        (validation_scripts / f"extra_fixture_{index:02d}.py").write_text("VALUE = 1\n", encoding="utf-8")

    report_dir = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613"
    asset_names = [
        "asset.png",
        "23_package_selfcheck_k_comparison.png",
        "24_package_selfcheck_qp_comparison.png",
        "25_package_selfcheck_qs_comparison.png",
        "26_package_selfcheck_lp_comparison.png",
        "27_package_selfcheck_ls_comparison.png",
        "30a_emx_first_gate_core_metrics.png",
    ]
    assets = []
    for name in asset_names:
        asset_path = report_dir / "assets" / name
        _write_png(asset_path)
        evidence_use = (
            "BLOCKED_AS_FINAL_EVIDENCE"
            if name.startswith(("23_", "24_", "25_", "26_", "27_", "30a_"))
            else "ACCEPTED_FOR_CURRENT_CLAIM"
        )
        assets.append(
            {
                "title": name,
                "file": f"assets/{name}",
                "status": "OK",
                "sha256": _sha256(asset_path),
                "evidence_use": evidence_use,
                "usage_note": "fixture report asset usage contract",
            }
        )
    usage_counts = {}
    for asset in assets:
        usage_counts[asset["evidence_use"]] = usage_counts.get(asset["evidence_use"], 0) + 1
    (report_dir / "index.html").write_text(
        "<html><body>"
        + "".join(f'<figure><img src="assets/{name}"></figure>' for name in asset_names)
        + "</body></html>\n",
        encoding="utf-8",
    )
    _write_json(
        report_dir / "report_manifest.json",
        {
            "asset_count": len(assets),
            "asset_usage_counts": usage_counts,
            "card_count": 2,
            "source_summary_count": 1,
            "cards": [
                {
                    "name": "Local project health check",
                    "status": "PASS",
                    "detail": (
                        "Latest local run covered 16 steps. Full pytest gate: 451 passed, 52 skipped in 25.32s; "
                        "optional extras are represented as pytest skips when unavailable. "
                        "It is a local reproducibility gate only and does not run MARS/HFSS/ADS/EMX."
                    ),
                },
                {
                    "name": "EMX/HFSS/ADS validation-chain decision",
                    "status": "BLOCKED_BY_EMX_REFERENCE",
                    "detail": "overall=BLOCKED_BY_EMX_REFERENCE, decision=DO_NOT_USE_HFSS_COMPARISON",
                },
            ],
            "assets": assets,
        },
    )

    handoff = package / "mars_handoff_bundle_20260613"
    command = handoff / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    config = handoff / "configs" / "mars_dataset_500_wideband_20260613.yaml"
    gate = handoff / "scripts" / "run_dataset_quality_gates.py"
    emx_first_gate = handoff / "scripts" / "build_emx_first_validation_gate.py"
    target_import_verifier = handoff / "scripts" / "verify_target_emx_postrun_package.py"
    touchstone_audit = handoff / "scripts" / "audit_touchstone_transformer.py"
    emx_return_discovery = handoff / "scripts" / "discover_and_verify_mars_emx_return.py"
    emx_return_watcher = handoff / "scripts" / "watch_mars_emx_return.py"
    path_discovery = handoff / "scripts" / "discover_mars_emx_cadence_paths.py"
    patcher = handoff / "scripts" / "patch_mars_config_paths.py"
    verifier = handoff / "scripts" / "verify_mars_handoff_install.py"
    target_prepare = handoff / "scripts" / "prepare_target_emx_wideband_rerun.py"
    target_postrun_prepare = handoff / "scripts" / "prepare_target_emx_postrun_validation.py"
    backfill = handoff / "scripts" / "backfill_ground_clearance_audit.py"
    batch_runner = handoff / "scripts" / "run_hfss_emx_validation_batch.py"
    validation_chain = handoff / "scripts" / "build_validation_chain_decision_card.py"
    mars_next_action = handoff / "scripts" / "build_mars_next_action_packet.py"
    accepted_runner = handoff / "scripts" / "run_accepted_emx_hfss_ads_validation.py"
    accepted_figure_verifier = handoff / "scripts" / "verify_accepted_emx_hfss_ads_figures.py"
    zin_audit = handoff / "scripts" / "audit_zin_coverage.py"
    response_audit = handoff / "scripts" / "audit_response_feature_coverage.py"
    readiness = handoff / "scripts" / "audit_248k_launch_readiness.py"
    command.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    gate.parent.mkdir(parents=True)
    quality_args = "".join(
        f"  {fragment} \\\n"
        for fragment in _load_delivery_audit_module().QUALITY_GATE_REQUIRED_FRAGMENTS
    ).rstrip(" \\\n") + "\n"
    progress_args = "".join(
        f"  {fragment} \\\n"
        for fragment in _load_delivery_audit_module().PROGRESS_AUDIT_REQUIRED_FRAGMENTS[1:]
    ).rstrip(" \\\n") + "\n"
    command.write_text(
        "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}\n"
        ".venv/bin/python scripts/preflight_dataset_config.py \"$CONFIG\"\n"
        ".venv/bin/python scripts/audit_mars_run_progress.py \"$RUN_DIR\" \\\n"
        + progress_args
        + ".venv/bin/python scripts/run_dataset_quality_gates.py \"$RUN_DIR\" \\\n"
        + quality_args,
        encoding="utf-8",
    )
    compare_args = "".join(
        f"  {fragment} \\\n"
        for fragment in _load_delivery_audit_module().COMPARE_GATE_REQUIRED_FRAGMENTS[1:]
    ).rstrip(" \\\n") + "\n"
    (handoff / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md").write_text(
        "## HFSS/EMX compare\n\n"
        ".venv/bin/python scripts/compare_emx_hfss_ads.py \\\n"
        + compare_args
        + "\n"
        + ".venv/bin/python scripts/verify_accepted_emx_hfss_ads_figures.py \\\n"
        + "  --accepted-summary /path/to/accepted_emx_hfss_ads_validation_summary.json\n"
        + ".venv/bin/python scripts/run_accepted_emx_hfss_ads_validation.py \\\n"
        + "  --hfss-geometry-summary /path/to/hfss_model_geometry_asset_audit_summary.json\n\n"
        + "overall_status=PASS\n"
        + "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS\n"
        + "decision=ACCEPT_FINAL_LP_LS_Q_K_FIGURES\n"
        + "DO_NOT_USE_FINAL_LP_LS_Q_K_FIGURES\n"
        + "ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md\n"
        + "Lp/Ls/Qp/Qs/K figures must remain diagnostic or blocked\n",
        encoding="utf-8",
    )
    target_emx_command = handoff / "project_runbook" / "target_emx_wideband_rerun_20260613" / "target_emx_wideband_rerun.commands.sh"
    target_emx_command.parent.mkdir(parents=True, exist_ok=True)
    target_emx_command.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "mkdir -p /home/researcher/project/runs/evaluations/ec6698dfc575950b/emx_wideband_5_50_0p1\n"
        "/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx "
        "/home/researcher/project/runs/cadence_batches/batch/streamout/transformer_layout_cadpins.gds "
        "TRANSFORMER_021_ec6698df /path/to/pdk/proc.proc "
        "--touchstone --s-impedance=50 -s "
        "/home/researcher/project/runs/evaluations/ec6698dfc575950b/emx_wideband_5_50_0p1/emx.s4p "
        "--include-command-line --edge-width=1 --accuracy=standard --verbose=2 --cadence-pins=51 "
        "--port=P001=P001:P001_G --port=P002=P002:P002_G --port=P003=P003:P003_G --port=P004=P004:P004_G "
        f"{_target_emx_frequency_tokens()}\n",
        encoding="utf-8",
    )
    target_emx_postrun_command = (
        handoff
        / "project_runbook"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_postrun_validation.commands.sh"
    )
    target_emx_postrun_command.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -s "$EMX_S4P"\n'
        'sha256sum "$EMX_S4P"\n'
        ".venv/bin/python scripts/audit_touchstone_transformer.py \"$EMX_S4P\" "
        "--expected-source-kind EMX "
        "--expected-frequency-start-ghz 5.0 --expected-frequency-stop-ghz 50.0 "
        "--expected-frequency-step-ghz 0.1 --expected-frequency-points 451 "
        "--positive-window-start-ghz 5.0 --positive-window-stop-ghz 30.0 "
        "--min-target-abs-k 0.05 --min-window-abs-k 0.05 "
        "--shape-window-start-ghz 5.0 --shape-window-stop-ghz 30.0\n"
        ".venv/bin/python scripts/build_emx_first_validation_gate.py --emx-s4p \"$EMX_S4P\" "
        "--required-sweep-start-ghz 5.0 --required-sweep-stop-ghz 50.0 "
        "--required-sweep-step-ghz 0.1 --required-sweep-points 451 "
        "--physical-window-start-ghz 5.0 --physical-window-stop-ghz 30.0 "
        "--max-shape-spike-ratio 4 --max-shape-relative-step 0.25 "
        "--photo-max-percent-error 5.0\n"
        'tar -czf "$TRANSFER_TARBALL" "$OUT_DIR"\n',
        encoding="utf-8",
    )
    config.write_text(
        "target:\n"
        "  frequency_start_hz: 5000000000.0\n"
        "  frequency_stop_hz: 50000000000.0\n"
        "  frequency_step_hz: 100000000.0\n"
        "  band_points: 451\n"
        "transformer:\n"
        "  shield:\n"
        "    enabled: true\n"
        "    kind: ring\n"
        "emx:\n"
        "  port_mode: single_ended_shield_grounded\n"
        "  cadence_pin_purpose: 51\n",
        encoding="utf-8",
    )
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "run_dataset_quality_gates.py", gate)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "build_emx_first_validation_gate.py", emx_first_gate)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "verify_target_emx_postrun_package.py", target_import_verifier)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "audit_touchstone_transformer.py", touchstone_audit)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "discover_and_verify_mars_emx_return.py", emx_return_discovery)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "watch_mars_emx_return.py", emx_return_watcher)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "discover_mars_emx_cadence_paths.py", path_discovery)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "patch_mars_config_paths.py", patcher)
    verifier.write_text("# verifier\n", encoding="utf-8")
    target_prepare.write_text("# target prepare\n", encoding="utf-8")
    target_postrun_prepare.write_text("# target postrun prepare\n", encoding="utf-8")
    backfill.write_text("# backfill\n", encoding="utf-8")
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "run_hfss_emx_validation_batch.py", batch_runner)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "build_validation_chain_decision_card.py", validation_chain)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "build_mars_next_action_packet.py", mars_next_action)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "run_accepted_emx_hfss_ads_validation.py", accepted_runner)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "verify_accepted_emx_hfss_ads_figures.py", accepted_figure_verifier)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "audit_zin_coverage.py", zin_audit)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "audit_response_feature_coverage.py", response_audit)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "audit_248k_launch_readiness.py", readiness)
    source_fragments = {
        handoff / "rfic_transformer_inverse_design" / "dataset.py": (
            "GROUND_CLEARANCE_AUDIT_FILENAME\n"
            "def write_ground_clearance_audit(): pass\n"
            "ground_clearance_quality = True\n"
        ),
        handoff / "rfic_transformer_inverse_design" / "execution" / "evaluator.py": (
            "SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME\n"
            "def _attach_signal_shield_clearance_audit(): pass\n"
            "layout is not None and error is None\n"
        ),
        handoff / "rfic_transformer_inverse_design" / "layout" / "export.py": (
            "SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME\n"
            "def _signal_shield_clearance_report(): pass\n"
            "def _write_signal_shield_clearance_audit(): pass\n"
        ),
    }
    for path, text in source_fragments.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _write_json(handoff / "MARS_HANDOFF_INVENTORY_20260613.json", {"file_count": 3})
    _write_sha_manifest(handoff)

    _write_json(
        package / "acceptance_matrix_20260613.json",
        {
            "overall_status": "INCOMPLETE",
            "status_counts": {"PASS": 1, "PENDING": 1},
            "items": [
                {
                    "requirement": "MARS pull, progress, path, local gate, and handoff helpers exist",
                    "status": "PASS",
                }
            ],
        },
    )
    _write_json(
        package / "package_selfcheck_compare_window_20260613" / "emx_hfss_ads_comparison_summary.json",
        {
            "overall_status": "PASS",
            "frequency_window_hz": {"min": 13.5e9, "max": 16.5e9, "count": 9},
            "frequency_grid_checks": {
                "comparison point count": {"status": "PASS", "detail": "count=9"},
                "expected frequency points": {"status": "PASS", "detail": "expected=9, actual=9"},
                "expected frequency step": {
                    "status": "PASS",
                    "detail": "expected_step_hz=375000000.0, max_step_error_hz=0.0, tolerance_hz=100000.0",
                },
                "expected window start point": {"status": "PASS", "detail": "error_hz=0.0"},
                "expected window stop point": {"status": "PASS", "detail": "error_hz=0.0"},
            },
            "metrics": {
                "k": {"status": "PASS", "max_percent_error": 4.0},
                "qp": {"status": "PASS", "max_percent_error": 4.1},
                "qs": {"status": "PASS", "max_percent_error": 4.2},
                "lp_nh": {"status": "PASS", "max_percent_error": 0.4},
                "ls_nh": {"status": "PASS", "max_percent_error": 4.5},
            },
        },
    )
    _write_json(
        package / "package_selfcheck_compare_window_20260613" / "package_selfcheck_compare_run_summary.json",
        {
            "overall_status": "PASS",
            "scope": "NARROWBAND_PACKAGE_SELF_CONSISTENCY_ONLY",
            "decision": "NOT_A_GOLDEN_EMX_REFERENCE_GATE",
            "evidence_use": "NOT_FINAL_LP_LS_Q_K_EVIDENCE",
        },
    )
    _write_json(
        package / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json",
        {
            "decision": "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
            "checks": [
                {
                    "status": "PASS",
                    "name": "basic numeric physics sanity",
                    "detail": "finite/plausibly bounded only",
                },
                {
                    "status": "PASS",
                    "name": "ADS no-extrapolation plot grid",
                    "detail": "required grid present",
                },
                {
                    "status": "PASS",
                    "name": "physical metric window",
                    "detail": "ok",
                },
                {
                    "status": "PASS",
                    "name": "smooth transformer metric window",
                    "detail": "ok",
                },
            ],
            "method_notes": [
                "The basic numeric physics sanity check only confirms finite, plausibly bounded L/Q/K values; it is not a golden-reference acceptance by itself."
            ],
        },
    )
    formula_dir = package / "ads_metric_formula_consistency_20260614"
    _write_json(
        formula_dir / "ads_metric_formula_consistency_summary.json",
        {
            "overall_status": "PASS",
            "decision": "ADS_FORMULA_IMPLEMENTATION_ACCEPTED",
            "port_pairs": "1,2:3,4",
            "frequency_ghz": {"start": 5.0, "stop": 50.0, "step": 0.1, "points": 451},
            "checks": [
                {"status": "PASS", "name": "helper formula equals direct ADS expression", "detail": "ok"},
                {"status": "PASS", "name": "known transformer metric recovery", "detail": "ok"},
                {"status": "PASS", "name": "formula audit frequency grid", "detail": "ok"},
                {"status": "PASS", "name": "ADS Data Display equation template", "detail": "ok"},
            ],
            "metric_recovery_errors": {
                "qp": {"max_percent_error": 1.0e-12},
                "k": {"max_percent_error": 5.0e-13},
            },
        },
    )
    (formula_dir / "ads_metric_formula_consistency_report.md").write_text("# formula\n", encoding="utf-8")
    (formula_dir / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md").write_text(
        "\n".join(
            [
                "# ADS Data Display Lp/Ls/Q/K Template",
                "ADS Data Display equation template",
                "Touchstone reference impedance",
                "port pairs 1,2:3,4",
                "Zp = Z11 - Z12 + Z22 - Z21",
                "Zs = Z33 - Z34 + Z44 - Z43",
                "Zm = Z31 - Z32 + Z42 - Z41",
                "Lp = imag(Zp) / omega",
                "Ls = imag(Zs) / omega",
                "M  = imag(Zm) / omega",
                "K  = M / sqrt(Lp*Ls)",
                "Qp = imag(Zp) / real(Zp)",
                "Qs = imag(Zs) / real(Zs)",
                "target_marker_ghz = 15",
                "5-50 GHz / 0.1 GHz / 451 points",
                "no ADS extrapolation",
            ]
        ),
        encoding="utf-8",
    )
    _write_png(formula_dir / "ads_metric_formula_consistency_curves.png", size=(360, 220))

    zip_path = root / "package.zip"
    zip_sha = root / "package.zip.sha256"
    _refresh_delivery_archives(package, zip_path, zip_sha)
    return package, zip_path, zip_sha


class AuditDeliveryPackageScriptTest(TransformerToolboxTestBase):
    def test_delivery_package_audit_passes_complete_fixture(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["ADS metric formula consistency evidence"]["status"], "PASS")
            self.assertIn("worst_recovery=qp", checks["ADS metric formula consistency evidence"]["detail"])
            self.assertEqual(checks["target EMX post-run import verifier contract"]["status"], "PASS")
            self.assertEqual(checks["Touchstone transformer audit contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff target EMX post-run import verifier contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted target EMX post-run import verifier contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff Touchstone transformer audit contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted Touchstone transformer audit contract"]["status"], "PASS")
            self.assertEqual(checks["target EMX return watcher contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff target EMX return watcher contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted target EMX return watcher contract"]["status"], "PASS")
            self.assertEqual(checks["accepted final figure verifier contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff accepted final figure verifier contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted accepted final figure verifier contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff accepted final figure verifier runbook contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted accepted final figure verifier runbook contract"]["status"], "PASS")
            self.assertEqual(checks["target-envelope quality scripts contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff target-envelope quality scripts contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted target-envelope quality scripts contract"]["status"], "PASS")

    def test_delivery_package_audit_extracts_handoff_when_tarfile_filter_argument_is_unavailable(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            original_extractall = tarfile.TarFile.extractall
            calls: list[object] = []

            def fake_extractall(self, path=".", members=None, *, numeric_owner=False, filter=None):
                calls.append(filter)
                if filter is not None:
                    raise TypeError("extractall() got an unexpected keyword argument 'filter'")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    return original_extractall(self, path, members, numeric_owner=numeric_owner)

            with mock.patch.object(tarfile.TarFile, "extractall", fake_extractall):
                status = audit.main(
                    [
                        "--package-dir",
                        str(package),
                        "--zip-path",
                        str(zip_path),
                        "--zip-sha-record",
                        str(zip_sha),
                        "--out-dir",
                        str(root / "audit"),
                        "--min-report-assets",
                        "1",
                        "--min-status-cards",
                        "1",
                        "--min-source-summaries",
                        "1",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(calls.count("data"), 1)
            self.assertEqual(calls.count(None), 1)

    def test_delivery_package_audit_fails_on_stale_sha_manifest(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            (package / "acceptance_matrix_20260613.json").write_text("tampered\n", encoding="utf-8")

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["package SHA manifest"]["status"], "FAIL")

    def test_delivery_package_audit_fails_when_selfcheck_compare_fails(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            summary_path = package / "package_selfcheck_compare_window_20260613" / "emx_hfss_ads_comparison_summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            data["metrics"]["k"]["max_percent_error"] = 5.5
            summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["package selfcheck compare gate"]["status"], "FAIL")

    def test_delivery_package_audit_fails_when_selfcheck_grid_check_fails(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            summary_path = package / "package_selfcheck_compare_window_20260613" / "emx_hfss_ads_comparison_summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            data["frequency_grid_checks"]["expected frequency step"]["status"] = "FAIL"
            summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["package selfcheck compare gate"]["status"], "FAIL")
            self.assertIn("expected frequency step", checks["package selfcheck compare gate"]["detail"])

    def test_delivery_package_audit_fails_when_selfcheck_wrapper_omits_scope_boundary(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            wrapper_path = package / "package_selfcheck_compare_window_20260613" / "package_selfcheck_compare_run_summary.json"
            data = json.loads(wrapper_path.read_text(encoding="utf-8"))
            data["decision"] = "ACCEPT_AS_GOLDEN_EMX_REFERENCE"
            wrapper_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["package selfcheck compare gate"]["status"], "FAIL")
            self.assertIn("wrapper_decision", checks["package selfcheck compare gate"]["detail"])

    def test_delivery_package_audit_fails_when_selfcheck_wrapper_omits_evidence_boundary(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            wrapper_path = package / "package_selfcheck_compare_window_20260613" / "package_selfcheck_compare_run_summary.json"
            data = json.loads(wrapper_path.read_text(encoding="utf-8"))
            data.pop("evidence_use")
            wrapper_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["package selfcheck compare gate"]["status"], "FAIL")
            self.assertIn("wrapper_evidence_use", checks["package selfcheck compare gate"]["detail"])

    def test_delivery_package_audit_fails_when_emx_gate_evidence_is_stale(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            _write_json(
                package / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json",
                {
                    "decision": "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
                    "checks": [{"status": "PASS", "name": "target transformer sanity", "detail": "stale"}],
                    "method_notes": [],
                },
            )
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first package gate evidence"]["status"], "FAIL")
            self.assertIn("stale target transformer sanity", checks["EMX-first package gate evidence"]["detail"])

    def test_delivery_package_audit_fails_when_formula_evidence_is_stale_or_blank(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            formula_dir = package / "ads_metric_formula_consistency_20260614"
            summary_path = formula_dir / "ads_metric_formula_consistency_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["frequency_ghz"]["points"] = 450
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            _write_png(formula_dir / "ads_metric_formula_consistency_curves.png", blank=True, size=(120, 80))
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["ADS metric formula consistency evidence"]["status"], "FAIL")
            self.assertIn("freq_points=450", checks["ADS metric formula consistency evidence"]["detail"])
            self.assertIn("blank or nearly constant PNG", checks["ADS metric formula consistency evidence"]["detail"])

    def test_delivery_package_audit_fails_when_ads_template_is_incomplete(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            template = (
                package
                / "ads_metric_formula_consistency_20260614"
                / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md"
            )
            template.write_text("ADS Data Display equation template\n", encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["ADS metric formula consistency evidence"]["status"], "FAIL")
            self.assertIn("ADS template missing fragments", checks["ADS metric formula consistency evidence"]["detail"])
            self.assertIn("Touchstone reference impedance", checks["ADS metric formula consistency evidence"]["detail"])

    def test_delivery_package_audit_fails_when_target_emx_import_verifier_is_stale(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            stale_source = (
                "def old_import_verifier():\n"
                "    return 'ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS without sha format or non-empty artifact gates'\n"
            )
            (package / "validation_scripts" / "verify_target_emx_postrun_package.py").write_text(
                stale_source,
                encoding="utf-8",
            )
            (
                package
                / "mars_handoff_bundle_20260613"
                / "scripts"
                / "verify_target_emx_postrun_package.py"
            ).write_text(stale_source, encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target EMX post-run import verifier contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff target EMX post-run import verifier contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted target EMX post-run import verifier contract"]["status"], "FAIL")
            self.assertIn("accepted_emx_reference_bundle", checks["target EMX post-run import verifier contract"]["detail"])

    def test_delivery_package_audit_fails_when_touchstone_audit_loses_per_step_grid_gate(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            stale_source = "def old_touchstone_gate():\n    return 'median step only'\n"
            for script in (
                package / "validation_scripts" / "audit_touchstone_transformer.py",
                package / "mars_handoff_bundle_20260613" / "scripts" / "audit_touchstone_transformer.py",
            ):
                script.write_text(stale_source, encoding="utf-8")
            _write_sha_manifest(package / "mars_handoff_bundle_20260613")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Touchstone transformer audit contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff Touchstone transformer audit contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted Touchstone transformer audit contract"]["status"], "FAIL")
            self.assertIn("bad_step_count", checks["Touchstone transformer audit contract"]["detail"])

    def test_delivery_package_audit_fails_when_target_emx_return_watcher_is_stale(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            stale_source = (
                "def old_watcher():\n"
                "    return 'only checks whether an S4P file exists'\n"
            )
            (package / "validation_scripts" / "watch_mars_emx_return.py").write_text(
                stale_source,
                encoding="utf-8",
            )
            (
                package
                / "mars_handoff_bundle_20260613"
                / "scripts"
                / "watch_mars_emx_return.py"
            ).write_text(stale_source, encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target EMX return watcher contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff target EMX return watcher contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted target EMX return watcher contract"]["status"], "FAIL")
            self.assertIn("discover_and_verify_mars_emx_return.py", checks["target EMX return watcher contract"]["detail"])

    def test_delivery_package_audit_fails_when_report_html_omits_selfcheck_plot(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            html_path = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "index.html"
            html_path.write_text(
                html_path.read_text(encoding="utf-8").replace(
                    '<figure><img src="assets/27_package_selfcheck_ls_comparison.png"></figure>',
                    "",
                ),
                encoding="utf-8",
            )
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["report html image references"]["status"], "FAIL")

    def test_delivery_package_audit_fails_when_report_html_omits_emx_first_core_plot(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            html_path = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "index.html"
            html_path.write_text(
                html_path.read_text(encoding="utf-8").replace(
                    '<figure><img src="assets/30a_emx_first_gate_core_metrics.png"></figure>',
                    "",
                ),
                encoding="utf-8",
            )
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["report html image references"]["status"], "FAIL")
            self.assertIn(
                "30a_emx_first_gate_core_metrics.png",
                checks["report html image references"]["detail"],
            )

    def test_delivery_package_audit_fails_when_blocked_final_plot_is_marked_accepted(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            manifest_path = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for asset in manifest["assets"]:
                if asset["file"] == "assets/23_package_selfcheck_k_comparison.png":
                    asset["evidence_use"] = "ACCEPTED_FOR_CURRENT_CLAIM"
                    asset["usage_note"] = "incorrectly promoted final comparison evidence"
                    break
            usage_counts = {}
            for asset in manifest["assets"]:
                usage_counts[asset["evidence_use"]] = usage_counts.get(asset["evidence_use"], 0) + 1
            manifest["asset_usage_counts"] = usage_counts
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["report asset usage contract"]["status"], "FAIL")
            self.assertIn("final comparison asset must stay BLOCKED_AS_FINAL_EVIDENCE", checks["report asset usage contract"]["detail"])

    def test_delivery_package_audit_fails_when_report_omits_full_pytest_gate(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            manifest_path = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            for card in data["cards"]:
                if card["name"] == "Local project health check":
                    card["detail"] = "Latest local run covered 15 steps. It is a local reproducibility gate only."
            manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["report local health pytest gate"]["status"], "FAIL")
            self.assertIn("Full pytest gate", checks["report local health pytest gate"]["detail"])

    def test_delivery_package_audit_fails_when_required_validation_script_missing(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            (package / "validation_scripts" / "run_local_project_health_check.py").unlink()
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["validation scripts inventory"]["status"], "FAIL")
            self.assertIn("run_local_project_health_check.py", checks["validation scripts inventory"]["detail"])

    def test_delivery_package_audit_fails_when_validation_script_has_syntax_error(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            bad_script = package / "validation_scripts" / "compare_emx_hfss_ads.py"
            bad_script.write_text("def bad(:\n", encoding="utf-8")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["validation scripts syntax"]["status"], "FAIL")
            self.assertIn("compare_emx_hfss_ads.py", checks["validation scripts syntax"]["detail"])

    def test_delivery_package_audit_fails_when_health_check_runner_contract_is_old(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            old_runner = package / "validation_scripts" / "run_local_project_health_check.py"
            old_runner.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--rebuild-delivery-zip', action='store_true')\n"
                "if __name__ == '__main__':\n"
                "    parser.parse_args()\n",
                encoding="utf-8",
            )
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["local health-check runner contract"]["status"], "FAIL")
            self.assertIn("watch_mars_emx_return.py", checks["local health-check runner contract"]["detail"])

    def test_delivery_package_audit_fails_when_dataset_verifier_contract_is_old(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            old_verifier = package / "validation_scripts" / "verify_mars_dataset_package.py"
            old_verifier.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('tarball')\n"
                "if __name__ == '__main__':\n"
                "    parser.parse_args()\n",
                encoding="utf-8",
            )
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS dataset package verifier contract"]["status"], "FAIL")
            self.assertIn("--require-quality-gates", checks["MARS dataset package verifier contract"]["detail"])

    def test_delivery_package_audit_fails_when_dataset_package_helper_contract_is_old(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            old_helper = package / "validation_scripts" / "package_mars_dataset_run.py"
            old_helper.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('run_dir')\n"
                "if __name__ == '__main__':\n"
                "    parser.parse_args()\n",
                encoding="utf-8",
            )
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS dataset package helper contract"]["status"], "FAIL")
            self.assertIn("--report", checks["MARS dataset package helper contract"]["detail"])

    def test_delivery_package_audit_fails_when_handoff_tar_contains_local_path(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            handoff = package / "mars_handoff_bundle_20260613"
            command = handoff / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
            command.write_text(
                "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}\n"
                "/home/researcher/.venv/bin/python scripts/run_dataset_quality_gates.py \"$RUN_DIR\"\n"
                "--touchstone-shape-window-start-ghz 5.0\n"
                "--touchstone-shape-window-stop-ghz 30\n"
                "--touchstone-max-shape-spike-ratio 4\n"
                "--touchstone-max-shape-relative-step 0.25\n",
                encoding="utf-8",
            )
            _write_sha_manifest(handoff)
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff portable commands"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted portable commands"]["status"], "FAIL")

    def test_delivery_package_audit_fails_when_handoff_config_contract_changes(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            handoff = package / "mars_handoff_bundle_20260613"
            config = handoff / "configs" / "mars_dataset_500_wideband_20260613.yaml"
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "frequency_stop_hz: 50000000000.0",
                    "frequency_stop_hz: 40000000000.0",
                ),
                encoding="utf-8",
            )
            _write_sha_manifest(handoff)
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff config contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted config contract"]["status"], "FAIL")
            self.assertIn("frequency_stop_hz", checks["MARS handoff config contract"]["detail"])

    def test_delivery_package_audit_fails_when_target_emx_frequency_list_is_not_451_points(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            command = (
                package
                / "mars_handoff_bundle_20260613"
                / "project_runbook"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_rerun.commands.sh"
            )
            command.write_text(
                command.read_text(encoding="utf-8").replace(_target_emx_frequency_tokens(), _target_emx_frequency_tokens(wrong=True)),
                encoding="utf-8",
            )
            _write_sha_manifest(package / "mars_handoff_bundle_20260613")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff target EMX rerun command contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted target EMX rerun command contract"]["status"], "FAIL")
            self.assertIn("451 points", checks["MARS handoff target EMX rerun command contract"]["detail"])

    def test_delivery_package_audit_fails_when_handoff_quality_gate_contract_changes(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            handoff = package / "mars_handoff_bundle_20260613"
            command = handoff / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
            command.write_text(
                command.read_text(encoding="utf-8").replace("  --audit-zin-coverage \\\n", ""),
                encoding="utf-8",
            )
            _write_sha_manifest(handoff)
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff quality-gate contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted quality-gate contract"]["status"], "FAIL")
            self.assertIn("--audit-zin-coverage", checks["MARS handoff quality-gate contract"]["detail"])

    def test_delivery_package_audit_passes_config_contract_without_pyyaml(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)

            with mock.patch("builtins.__import__", side_effect=_block_yaml_import):
                status = audit.main(
                    [
                        "--package-dir",
                        str(package),
                        "--zip-path",
                        str(zip_path),
                        "--zip-sha-record",
                        str(zip_sha),
                        "--out-dir",
                        str(root / "audit"),
                        "--min-report-assets",
                        "1",
                        "--min-status-cards",
                        "1",
                        "--min-source-summaries",
                        "1",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff config contract"]["status"], "PASS")
            self.assertEqual(checks["MARS handoff extracted config contract"]["status"], "PASS")

    def test_delivery_package_audit_fails_when_handoff_omits_shape_gate(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            handoff = package / "mars_handoff_bundle_20260613"
            command = handoff / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
            command.write_text(
                "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}\n"
                ".venv/bin/python scripts/run_dataset_quality_gates.py \"$RUN_DIR\"\n",
                encoding="utf-8",
            )
            _write_sha_manifest(handoff)
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff shape-window gate"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted shape-window gate"]["status"], "FAIL")

    def test_delivery_package_audit_fails_when_final_runner_omits_verifier_evidence_gate(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            for runner in (
                package / "validation_scripts" / "run_accepted_emx_hfss_ads_validation.py",
                package / "mars_handoff_bundle_20260613" / "scripts" / "run_accepted_emx_hfss_ads_validation.py",
            ):
                runner.write_text(
                    runner.read_text(encoding="utf-8").replace("accepted EMX import verifier evidence", ""),
                    encoding="utf-8",
                )
            _write_sha_manifest(package / "mars_handoff_bundle_20260613")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX/HFSS final runner contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff accepted EMX/HFSS final runner contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted accepted EMX/HFSS final runner contract"]["status"], "FAIL")

    def test_delivery_package_audit_fails_when_target_envelope_quality_scripts_are_stale(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            for response_audit in (
                package / "validation_scripts" / "audit_response_feature_coverage.py",
                package / "mars_handoff_bundle_20260613" / "scripts" / "audit_response_feature_coverage.py",
            ):
                response_audit.write_text(
                    response_audit.read_text(encoding="utf-8").replace("--target-k-min", ""),
                    encoding="utf-8",
                )
            _write_sha_manifest(package / "mars_handoff_bundle_20260613")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target-envelope quality scripts contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff target-envelope quality scripts contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted target-envelope quality scripts contract"]["status"], "FAIL")
            self.assertIn("audit_response_feature_coverage.py", checks["target-envelope quality scripts contract"]["detail"])

    def test_delivery_package_audit_fails_when_emx_first_gate_script_is_stale(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            stale_source = "def marker():\n    return 'target transformer sanity'\n"
            for gate in (
                package / "validation_scripts" / "build_emx_first_validation_gate.py",
                package / "mars_handoff_bundle_20260613" / "scripts" / "build_emx_first_validation_gate.py",
            ):
                gate.write_text(stale_source, encoding="utf-8")
            _write_sha_manifest(package / "mars_handoff_bundle_20260613")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first gate script contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff EMX-first gate script contract"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted EMX-first gate script contract"]["status"], "FAIL")
            self.assertIn("target transformer sanity", checks["MARS handoff EMX-first gate script contract"]["detail"])

    def test_delivery_package_audit_fails_when_handoff_omits_compare_grid_gate(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            handoff = package / "mars_handoff_bundle_20260613"
            runbook = handoff / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
            runbook.write_text(runbook.read_text(encoding="utf-8").replace("  --require-matching-frequency-grid \\\n", ""), encoding="utf-8")
            _write_sha_manifest(handoff)
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS handoff HFSS/EMX compare grid gate"]["status"], "FAIL")
            self.assertEqual(checks["MARS handoff extracted HFSS/EMX compare grid gate"]["status"], "FAIL")
            self.assertIn("--require-matching-frequency-grid", checks["MARS handoff HFSS/EMX compare grid gate"]["detail"])

    def test_delivery_package_audit_fails_on_macos_zip_metadata(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            with zipfile.ZipFile(zip_path, "a") as archive:
                archive.writestr("__MACOSX/package/._README.md", "resource fork")
            zip_sha.write_text(f"{_sha256(zip_path)}  {zip_path.name}\n", encoding="utf-8")

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["desktop zip clean metadata"]["status"], "FAIL")

    def test_delivery_package_audit_fails_on_python_bytecode_cache(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            cache_file = package / "validation_scripts" / "__pycache__" / "tool.cpython-312.pyc"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_bytes(b"bytecode")
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["package bytecode/cache hygiene"]["status"], "FAIL")
            self.assertEqual(checks["desktop zip bytecode/cache hygiene"]["status"], "FAIL")

    def test_delivery_package_audit_fails_on_blank_report_image(self) -> None:
        audit = _load_delivery_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package, zip_path, zip_sha = _make_delivery_fixture(root)
            asset = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "assets" / "asset.png"
            _write_png(asset, blank=True)
            manifest = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["assets"][0]["sha256"] = _sha256(asset)
            _write_json(manifest, data)
            _refresh_delivery_archives(package, zip_path, zip_sha)

            status = audit.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-report-assets",
                    "1",
                    "--min-status-cards",
                    "1",
                    "--min-source-summaries",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "delivery_package_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["report image nonblank"]["status"], "FAIL")
