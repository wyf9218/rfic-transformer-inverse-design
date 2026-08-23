#!/usr/bin/env python3
"""Run fresh EMX directly on immutable candidate-bound audited GDS files.

The runner never exports layout and never invokes Cadence, GDS generation,
physical auditing, or Calibre.  It binds an exact candidate CSV to an already
physically audited GDS index, loads the original immutable port manifest, and
passes that exact GDS path to EMX.  Its create-once output contains only EMX
and derived electrical evidence; no GDS is copied or regenerated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(os.path.abspath(os.path.expanduser(str(__file__))))
SCRIPT_DIR = SCRIPT_PATH.parent


def _bootstrap_required_cli_pin(flag: str) -> str:
    values: list[str] = []
    argv = list(sys.argv[1:])
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == flag:
            if index + 1 >= len(argv):
                raise SystemExit(f"{flag} requires a value")
            values.append(argv[index + 1])
            index += 2
            continue
        prefix = flag + "="
        if token.startswith(prefix):
            values.append(token[len(prefix) :])
        index += 1
    if len(values) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", values[0]):
        raise SystemExit(f"exactly one valid {flag} is required")
    return values[0].lower()


def _bootstrap_read_pinned_file(path: Path, expected: str, label: str) -> None:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise SystemExit(f"{label} path contains a symlink component")
        except OSError as exc:
            raise SystemExit(f"{label} path is missing") from exc
    before = os.lstat(absolute)
    descriptor = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if not stat.S_ISREG(opened.st_mode) or identity != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise SystemExit(f"{label} identity changed before read")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after_fd = os.fstat(descriptor)
        after_path = os.lstat(absolute)
        if identity != (
            after_fd.st_dev,
            after_fd.st_ino,
            after_fd.st_size,
            after_fd.st_mtime_ns,
            after_fd.st_ctime_ns,
        ) or identity != (
            after_path.st_dev,
            after_path.st_ino,
            after_path.st_size,
            after_path.st_mtime_ns,
            after_path.st_ctime_ns,
        ):
            raise SystemExit(f"{label} identity changed during read")
    finally:
        os.close(descriptor)
    if opened.st_size <= 0 or digest.hexdigest() != expected:
        raise SystemExit(f"{label} SHA-256 pin mismatch")


if __name__ == "__main__":
    if not sys.flags.isolated:
        raise SystemExit(
            "direct runner requires Python isolated mode (-I) before any "
            "repository import"
        )
    _BOOTSTRAP_EXPECTED_DIRECT_SHA256 = _bootstrap_required_cli_pin(
        "--expected-direct-runner-sha256"
    )
    _BOOTSTRAP_EXPECTED_SUPERVISOR_SHA256 = _bootstrap_required_cli_pin(
        "--expected-stage2-supervisor-sha256"
    )
    _bootstrap_read_pinned_file(
        SCRIPT_PATH, _BOOTSTRAP_EXPECTED_DIRECT_SHA256, "direct runner snapshot"
    )
    _bootstrap_read_pinned_file(
        SCRIPT_DIR / "run_high_k_q_overlap_stage2_supervisor.py",
        _BOOTSTRAP_EXPECTED_SUPERVISOR_SHA256,
        "stage-2 supervisor snapshot",
    )

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))
CONFIGURED_RUNTIME_REPO_ROOT = os.environ.get("RFIC_STAGE2_RUNTIME_REPO_ROOT")
if CONFIGURED_RUNTIME_REPO_ROOT:
    runtime_repo_root = Path(CONFIGURED_RUNTIME_REPO_ROOT).expanduser().resolve()
    for import_root in (runtime_repo_root / "scripts", runtime_repo_root):
        if str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))
# The separately hash-audited no-clobber tool bundle owns the stage contract;
# the frozen runtime repository supplies only its production package closure.
if str(SCRIPT_DIR) in sys.path:
    sys.path.remove(str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

import current_foundry_one_shot_config_identity as foundry_identity  # noqa: E402
import run_candidate_queue_dataset as candidate_queue  # noqa: E402
import run_high_k_q_overlap_stage2_supervisor as stage2_contract  # noqa: E402
from rfic_transformer_inverse_design.api import (  # noqa: E402
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.core.types import TransformerLayoutExport  # noqa: E402
from rfic_transformer_inverse_design.dataset import (  # noqa: E402
    result_to_dataset_row,
    write_dataset_csv,
)
from rfic_transformer_inverse_design.execution.zeus_cadence import (  # noqa: E402
    _run_emx,
    load_emx_layout_manifest,
    result_from_roundtrip_payload,
)


SUMMARY_NAME = "candidate_bound_existing_gds_fresh_emx_summary.json"
DATASET_NAME = "dataset_rows.csv"
SUMMARY_SCHEMA = "candidate_bound_existing_audited_gds_fresh_emx.v1"
EXPECTED_COUNT = 14
PHYSICAL_RECEIPT_SCHEMA = "current_foundry_gds_physical_identity_audit.v1"


class ExistingGdsEmxFailure(RuntimeError):
    """A no-regeneration EMX contract failure."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(os.path.abspath(os.path.expanduser(str(args.out_dir))))
    if stage2_contract._path_has_symlink_component(
        out_dir.parent
    ) or out_dir.is_symlink():
        print("overall_status=FAIL")
        print(
            f"error=ExistingGdsEmxFailure: output path contains a symlink component: {out_dir}",
            file=sys.stderr,
        )
        return 2
    preexisting = out_dir.exists()
    creation_state = {"owned": False}
    try:
        result = run_existing_gds_fresh_emx(args, creation_state=creation_state)
    except Exception as exc:  # noqa: BLE001 - retain a create-once FAIL receipt.
        owned = bool(creation_state.get("owned"))
        if not preexisting and not out_dir.exists():
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                out_dir.mkdir(exist_ok=False)
                owned = True
            except FileExistsError:
                owned = False
        if not preexisting and owned and out_dir.is_dir():
            _write_json_atomic(
                out_dir / SUMMARY_NAME,
                {
                    "schema": SUMMARY_SCHEMA,
                    "overall_status": "FAIL",
                    "decision": "STOP_EXISTING_GDS_FRESH_EMX_FAILED_CLOSED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "existing_audited_gds_reuse": True,
                    "cadence_executed": False,
                    "gds_generated_or_copied": False,
                    "calibre_executed": False,
                    "automatic_merge_authorized": False,
                },
            )
        print("overall_status=FAIL")
        print(f"error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"dataset_rows={result['dataset_rows_path']}")
    print(f"summary={result['summary_path']}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--audited-index", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--expected-audited-index-sha256", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--expected-source-manifest-aggregate-sha256", required=True)
    parser.add_argument("--expected-direct-runner-sha256", required=True)
    parser.add_argument("--expected-stage2-supervisor-sha256", required=True)
    parser.add_argument("--expected-emx-binary-sha256", required=True)
    parser.add_argument("--expected-emx-process-file-sha256", required=True)
    parser.add_argument("--expected-cadence-pdk-cds-lib-sha256", required=True)
    parser.add_argument("--expected-cadence-layer-map-sha256", required=True)
    parser.add_argument("--expected-count", type=int, choices=(EXPECTED_COUNT,), required=True)
    args = parser.parse_args(argv)
    for name in (
        "expected_candidate_sha256",
        "expected_audited_index_sha256",
        "expected_config_sha256",
        "expected_source_manifest_aggregate_sha256",
        "expected_direct_runner_sha256",
        "expected_stage2_supervisor_sha256",
        "expected_emx_binary_sha256",
        "expected_emx_process_file_sha256",
        "expected_cadence_pdk_cds_lib_sha256",
        "expected_cadence_layer_map_sha256",
    ):
        value = str(getattr(args, name) or "").lower()
        if not _is_sha256(value):
            parser.error(f"--{name.replace('_', '-')} must be a SHA-256 digest")
        setattr(args, name, value)
    return args


def run_existing_gds_fresh_emx(
    args: argparse.Namespace,
    *,
    creation_state: dict[str, bool] | None = None,
) -> dict[str, Any]:
    candidate_csv = Path(
        os.path.abspath(os.path.expanduser(str(args.candidate_csv)))
    )
    audited_index = Path(
        os.path.abspath(os.path.expanduser(str(args.audited_index)))
    )
    config_path = Path(os.path.abspath(os.path.expanduser(str(args.config))))
    out_dir = Path(os.path.abspath(os.path.expanduser(str(args.out_dir))))
    stage2_supervisor_path = SCRIPT_DIR / "run_high_k_q_overlap_stage2_supervisor.py"
    if out_dir.exists():
        raise ExistingGdsEmxFailure(f"refusing existing output directory: {out_dir}")
    if stage2_contract._path_has_symlink_component(
        out_dir.parent
    ) or out_dir.is_symlink():
        raise ExistingGdsEmxFailure(
            f"output path contains a symlink component: {out_dir}"
        )
    for label, path, expected in (
        ("candidate CSV", candidate_csv, args.expected_candidate_sha256),
        ("audited GDS index", audited_index, args.expected_audited_index_sha256),
        ("config", config_path, args.expected_config_sha256),
    ):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ExistingGdsEmxFailure(f"{label} is missing: {path}")
        if stage2_contract._path_has_symlink_component(path):
            raise ExistingGdsEmxFailure(
                f"{label} path contains a symlink component: {path}"
            )
        if _sha256(path) != str(expected):
            raise ExistingGdsEmxFailure(f"{label} SHA-256 mismatch")
    if Path(str(stage2_contract.__file__)).resolve() != stage2_supervisor_path.resolve():
        raise ExistingGdsEmxFailure(
            "stage-2 supervisor was imported outside the private tool directory"
        )
    tool_execution_files = [
        _operator_execution_record(
            role="direct_runner_snapshot",
            path=SCRIPT_PATH,
            expected_sha256=str(args.expected_direct_runner_sha256),
        ),
        _operator_execution_record(
            role="stage2_supervisor_snapshot",
            path=stage2_supervisor_path,
            expected_sha256=str(args.expected_stage2_supervisor_sha256),
        ),
    ]
    _assert_tool_execution_files(tool_execution_files)

    candidate_rows, candidate_fields = _read_csv(candidate_csv)
    audited_rows, audited_fields = _read_csv(audited_index)
    candidate_by_id = _unique_rows(candidate_rows, "candidate_id_sha256", "candidate")
    audited_by_id = _unique_rows(audited_rows, "candidate_id_sha256", "audited GDS")
    required_audited = {
        "candidate_id_sha256",
        "candidate_geometry_identity_sha256",
        "gds_path",
        "gds_sha256",
        "gds_timestamp_normalized_sha256",
        "candidate_physical_identity_sha256",
        "physical_identity_audit_status",
        "physical_identity_audit_path",
        "physical_identity_audit_sha256",
    }
    preflight_checks = {
        "expected_count_frozen14": int(args.expected_count) == EXPECTED_COUNT,
        "candidate_count_exact14": len(candidate_rows) == EXPECTED_COUNT,
        "audited_count_exact14": len(audited_rows) == EXPECTED_COUNT,
        "identity_sets_exact": set(candidate_by_id) == set(audited_by_id),
        "candidate_geometry_identity_present": "candidate_geometry_identity_sha256"
        in candidate_fields,
        "audited_fields_complete": required_audited.issubset(audited_fields),
    }
    _require_checks(preflight_checks, "existing-GDS EMX preflight")

    run_config = load_run_config(config_path)
    config_identity = foundry_identity.audit_config_identity(
        config_path,
        expected_config_sha256=str(args.expected_config_sha256),
    )
    config_checks = {
        "current_foundry_identity_pass": config_identity.get("overall_status") == "PASS"
        and not config_identity.get("errors")
        and bool(config_identity.get("checks"))
        and all((config_identity.get("checks") or {}).values()),
        "center_frequency_exact_15ghz": abs(float(run_config.target.f0_hz) - 15.0e9)
        <= 1.0,
        "frequency_start_exact_5ghz": abs(
            float(run_config.target.frequency_points_hz()[0]) - 5.0e9
        )
        <= 1.0,
        "frequency_stop_exact_60ghz": abs(
            float(run_config.target.frequency_points_hz()[-1]) - 60.0e9
        )
        <= 1.0,
        "frequency_points_exact111": len(run_config.target.frequency_points_hz()) == 111,
        "frequency_step_exact_0p5ghz": all(
            abs(float(right - left) - 0.5e9) <= 1.0
            for left, right in zip(
                run_config.target.frequency_points_hz()[:-1],
                run_config.target.frequency_points_hz()[1:],
            )
        ),
        "signal_four_port_mode": run_config.emx.power_line_8port.touchstone_mode
        == "signal_4_grounded_aux",
        "extra_args_cannot_override_canonical_argv": _extra_args_are_safe(
            [str(value) for value in run_config.emx.extra_args]
        ),
    }
    _require_checks(config_checks, "existing-GDS EMX config")
    external_execution_files = _validate_external_execution_files(
        run_config=run_config,
        expected_sha256_by_role={
            "emx_binary": str(args.expected_emx_binary_sha256),
            "emx_process_file": str(args.expected_emx_process_file_sha256),
            "cadence_pdk_cds_lib": str(args.expected_cadence_pdk_cds_lib_sha256),
            "cadence_layer_map": str(args.expected_cadence_layer_map_sha256),
        },
    )
    external_by_role = {
        str(record["role"]): record for record in external_execution_files
    }
    stage2_contract._assert_no_conflicting_processes(
        extra_executable_names={Path(str(run_config.emx.emx_binary)).name}
    )

    adapter = TransformerOptimizationAdapter(run_config.bounds)
    geometries, queue_metadata, geometry_checks = candidate_queue._geometry_from_rows(
        candidate_rows, adapter, run_config
    )
    if not all(item.get("pass") is True for item in geometry_checks):
        raise ExistingGdsEmxFailure(f"candidate geometry checks failed: {geometry_checks}")

    # Rehash all immutable inputs and source evidence before creating any output.
    source_bindings: list[dict[str, Any]] = []
    manifest_binding_records: list[dict[str, Any]] = []
    for candidate_id in sorted(candidate_by_id):
        candidate = candidate_by_id[candidate_id]
        audited = audited_by_id[candidate_id]
        receipt_path_lexical = _lexical_artifact(
            audited_index, audited.get("physical_identity_audit_path")
        )
        receipt_path = receipt_path_lexical.resolve()
        receipt = _read_json(receipt_path)
        source_gds_lexical = _lexical_artifact(audited_index, audited.get("gds_path"))
        source_gds = source_gds_lexical.resolve()
        pre_gds = _resolve_artifact(receipt_path, receipt.get("pre_cadence_gds_path"))
        manifest_path = pre_gds.parent / "transformer_layout.layout.json"
        manifest = load_emx_layout_manifest(manifest_path)
        manifest_record, derived_pre_gds, derived_manifest_path, manifest_checks = (
            stage2_contract._source_manifest_binding_record(
                candidate_id_sha256=candidate_id,
                physical_receipt_path=receipt_path,
                physical_receipt=receipt,
            )
        )
        geometry_id = str(candidate.get("candidate_geometry_identity_sha256") or "").lower()
        signal_labels = tuple(
            label
            for port in manifest.ports
            for label in tuple(port.signal_labels)
        )
        binding_checks = {
            "geometry_identity_exact": geometry_id
            == str(audited.get("candidate_geometry_identity_sha256") or "").lower()
            == str(receipt.get("candidate_geometry_identity_sha256") or "").lower(),
            "physical_status_pass": str(
                audited.get("physical_identity_audit_status") or ""
            ).upper()
            == "PASS"
            and receipt.get("overall_status") == "PASS",
            "physical_receipt_schema_exact": receipt.get("schema")
            == PHYSICAL_RECEIPT_SCHEMA,
            "physical_receipt_candidate_exact": str(
                receipt.get("candidate_id_sha256") or ""
            ).lower()
            == candidate_id,
            "physical_receipt_hash_exact": _sha256(receipt_path)
            == str(audited.get("physical_identity_audit_sha256") or "").lower(),
            "physical_receipt_regular_non_symlink": receipt_path.is_file()
            and receipt_path.stat().st_size > 0
            and not stage2_contract._path_has_symlink_component(
                receipt_path_lexical
            ),
            "source_gds_regular_non_symlink": source_gds.is_file()
            and source_gds.stat().st_size > 0
            and not stage2_contract._path_has_symlink_component(
                source_gds_lexical
            ),
            "source_gds_live_hash_exact": source_gds.is_file()
            and _sha256(source_gds) == str(audited.get("gds_sha256") or "").lower()
            == str(receipt.get("cadence_gds_sha256") or "").lower(),
            "source_gds_path_exact_physical_receipt": source_gds
            == _resolve_artifact(receipt_path, receipt.get("cadence_gds_path")),
            "pre_cadence_gds_path_exact": derived_pre_gds == pre_gds,
            "pre_cadence_gds_live_hash_exact": pre_gds.is_file()
            and _sha256(pre_gds)
            == str(receipt.get("pre_cadence_gds_sha256") or "").lower(),
            "manifest_path_exact_from_physical_receipt": derived_manifest_path
            == manifest_path,
            "manifest_semantic_contract_all_pass": bool(manifest_checks)
            and all(manifest_checks.values())
            and manifest_record.get("semantic_contract_pass") is True,
            "manifest_live_nonempty": manifest_path.is_file()
            and manifest_path.stat().st_size > 0,
            "manifest_top_cell_exact": str(manifest.top_cell)
            == str(run_config.emx.top_cell_prefix),
            "manifest_cadence_pin_purpose_exact": manifest.cadence_pin_purpose
            == run_config.emx.cadence_pin_purpose,
            "manifest_signal_ports_exact": signal_labels
            == tuple(run_config.emx.power_line_8port.port_map),
            "manifest_effective_port_count_four": len(manifest.ports) == 4,
        }
        _require_checks(binding_checks, f"source binding {candidate_id}")
        manifest_binding_records.append(manifest_record)
        source_bindings.append(
            {
                "candidate_id_sha256": candidate_id,
                "candidate_geometry_identity_sha256": geometry_id,
                "gds_path": str(source_gds),
                "gds_sha256": _sha256(source_gds),
                "gds_timestamp_normalized_sha256": str(
                    audited.get("gds_timestamp_normalized_sha256") or ""
                ).lower(),
                "candidate_physical_identity_sha256": str(
                    audited.get("candidate_physical_identity_sha256") or ""
                ).lower(),
                "manifest_path": str(manifest_path.resolve()),
                "manifest_sha256": _sha256(manifest_path),
                "pre_cadence_gds_path": str(pre_gds),
                "pre_cadence_gds_sha256": _sha256(pre_gds),
                "physical_receipt_path": str(receipt_path),
                "physical_receipt_sha256": _sha256(receipt_path),
            }
        )
    manifest_aggregate_sha256 = stage2_contract._source_manifest_aggregate_sha256(
        manifest_binding_records
    )
    if manifest_aggregate_sha256 != str(
        args.expected_source_manifest_aggregate_sha256
    ).lower():
        raise ExistingGdsEmxFailure(
            "source manifest aggregate SHA-256 mismatch: "
            f"expected={args.expected_source_manifest_aggregate_sha256}, "
            f"actual={manifest_aggregate_sha256}"
        )
    binding_by_id = {item["candidate_id_sha256"]: item for item in source_bindings}
    initial_pins = {
        str(path): _sha256(path)
        for path in (candidate_csv, audited_index, config_path)
    }
    for record in external_execution_files:
        initial_pins[str(record["path"])] = str(record["sha256"])
    for record in tool_execution_files:
        initial_pins[str(record["path"])] = str(record["sha256"])
    for item in source_bindings:
        for path_key, sha_key in (
            ("gds_path", "gds_sha256"),
            ("manifest_path", "manifest_sha256"),
            ("pre_cadence_gds_path", "pre_cadence_gds_sha256"),
            ("physical_receipt_path", "physical_receipt_sha256"),
        ):
            path = str(item[path_key])
            digest = str(item[sha_key])
            previous = initial_pins.get(path)
            if previous is not None and previous != digest:
                raise ExistingGdsEmxFailure(f"conflicting immutable pin: {path}")
            initial_pins[path] = digest
    _assert_pins(initial_pins)
    _assert_external_execution_files(external_execution_files)
    _assert_tool_execution_files(tool_execution_files)

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(exist_ok=False)
    if creation_state is not None:
        creation_state["owned"] = True
    evaluations_dir = out_dir / "evaluations"
    evaluations_dir.mkdir()

    geometry_by_id = {
        str(row.get("candidate_id_sha256") or "").lower(): (geometry, metadata)
        for row, geometry, metadata in zip(candidate_rows, geometries, queue_metadata)
    }
    results = []
    dataset_rows: list[dict[str, object]] = []
    execution_records: list[dict[str, Any]] = []
    for candidate_id in sorted(candidate_by_id):
        _assert_pins(initial_pins)
        _assert_external_execution_files(external_execution_files)
        _assert_tool_execution_files(tool_execution_files)
        geometry, metadata = geometry_by_id[candidate_id]
        binding = binding_by_id[candidate_id]
        evaluation = candidate_id[:16]
        if not re.fullmatch(r"[0-9a-f]{16}", evaluation):
            raise ExistingGdsEmxFailure("invalid candidate-bound evaluation key")
        work_dir = evaluations_dir / evaluation
        work_dir.mkdir(exist_ok=False)
        source_gds = Path(binding["gds_path"])
        manifest_path = Path(binding["manifest_path"])
        manifest = load_emx_layout_manifest(manifest_path)
        layout = TransformerLayoutExport(
            gds_path=source_gds,
            manifest_path=manifest_path,
            preview_path=source_gds.with_suffix(".png"),
            debug_preview_path=source_gds.with_name("transformer_port_debug.png"),
            top_cell=str(manifest.top_cell),
        )
        emx_payload = _run_emx(
            run_config=run_config,
            work_dir=work_dir,
            layout=layout,
            manifest=manifest,
        )
        _assert_pins(initial_pins)
        _assert_external_execution_files(external_execution_files)
        _assert_tool_execution_files(tool_execution_files)
        payload = {
            **emx_payload,
            "ok": True,
            "artifacts": {
                "cadence_gds": str(source_gds),
                "export_manifest": str(manifest_path),
                "cadence_preview": str(layout.preview_path),
                "cadence_debug_preview": str(layout.debug_preview_path),
                "top_cell": layout.top_cell,
            },
        }
        result = result_from_roundtrip_payload(
            payload=payload,
            geometry=geometry,
            run_config=run_config,
            work_dir=work_dir,
            cache_key=evaluation,
            geometry_check={"ok": True, "backend": "reused_physical_calibre_evidence"},
        )
        if not result.ok() or result.touchstone_path is None:
            raise ExistingGdsEmxFailure(f"fresh EMX failed for {candidate_id}: {result.error}")
        row = candidate_queue._merge_queue_metadata(
            result_to_dataset_row(result, z_load_ohm=50.0), metadata
        )
        try:
            row["k_abs_center"] = abs(float(row["k_center"]))
            row["q_center"] = min(float(row["qp_center"]), float(row["qs_center"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExistingGdsEmxFailure(
                f"canonical 15 GHz feature labels are missing for {candidate_id}"
            ) from exc
        command_path = work_dir / "emx" / "emx_command.json"
        command = _read_command(command_path)
        expected_command = _expected_emx_command(
            emx_binary=Path(str(external_by_role["emx_binary"]["path"])),
            source_gds=source_gds,
            process_file=Path(
                str(external_by_role["emx_process_file"]["path"])
            ),
            touchstone_path=Path(result.touchstone_path),
            extra_args=[str(value) for value in run_config.emx.extra_args],
        )
        if command != expected_command:
            raise ExistingGdsEmxFailure(
                f"EMX command contract differs for {candidate_id}"
            )
        row.update(
            {
                "source_audited_gds_path": str(source_gds),
                "source_audited_gds_sha256": binding["gds_sha256"],
                "source_audited_gds_timestamp_normalized_sha256": binding[
                    "gds_timestamp_normalized_sha256"
                ],
                "source_audited_gds_physical_identity_sha256": binding[
                    "candidate_physical_identity_sha256"
                ],
                "source_manifest_path": str(manifest_path),
                "source_manifest_sha256": binding["manifest_sha256"],
                "emx_command_path": str(command_path),
                "emx_command_sha256": _sha256(command_path),
            }
        )
        dataset_rows.append(row)
        results.append(result)
        record = {
            "schema": "candidate_bound_existing_audited_gds_fresh_emx_row.v1",
            "overall_status": "PASS",
            "candidate_id_sha256": candidate_id,
            "evaluation": evaluation,
            **binding,
            "work_dir": str(work_dir),
            "touchstone_path": str(result.touchstone_path),
            "touchstone_sha256": _sha256(result.touchstone_path),
            "emx_command_path": str(command_path),
            "emx_command_sha256": _sha256(command_path),
            "cadence_executed": False,
            "gds_generated_or_copied": False,
            "calibre_executed": False,
            "automatic_merge_authorized": False,
        }
        record_path = work_dir / "existing_gds_fresh_emx_receipt.json"
        _write_json_atomic(record_path, record)
        execution_records.append({**record, "receipt_path": str(record_path), "receipt_sha256": _sha256(record_path)})

    dataset_path = out_dir / DATASET_NAME
    write_dataset_csv(dataset_rows, dataset_path)
    touchstone_contract = candidate_queue._touchstone_output_contract(
        out_dir=out_dir,
        rows=dataset_rows,
        create_only=False,
        cadence_streamout_only=False,
        expected_extension=".s4p",
        expected_ports=4,
        expected_frequency_start_ghz=5.0,
        expected_frequency_stop_ghz=60.0,
        expected_frequency_step_ghz=0.5,
        expected_frequency_points=111,
        frequency_tolerance_hz=1.0,
        max_touchstone_checks=EXPECTED_COUNT,
    )
    forbidden_tree = stage2_contract._scan_fresh_output_tree(out_dir)
    output_gds = list(forbidden_tree["gds_files"])
    checks = [*geometry_checks, *touchstone_contract["checks"]]
    checks.extend(
        {"name": name, "pass": bool(value), "detail": value}
        for name, value in {**preflight_checks, **config_checks}.items()
    )
    checks.extend(
        (
            {"name": "fresh_emx_count_exact14", "pass": len(results) == EXPECTED_COUNT, "detail": len(results)},
            {"name": "fresh_emx_results_all_ok", "pass": all(item.ok() for item in results), "detail": len(results)},
            {"name": "no_gds_generated_or_copied", "pass": not output_gds, "detail": output_gds},
            {
                "name": "fresh_output_has_no_symlinks",
                "pass": not forbidden_tree["symlinks"],
                "detail": forbidden_tree["symlinks"],
            },
            {
                "name": "fresh_output_has_no_cadence_or_calibre_directories",
                "pass": not forbidden_tree["forbidden_directories"],
                "detail": forbidden_tree["forbidden_directories"],
            },
            {"name": "immutable_sources_unchanged", "pass": _pins_match(initial_pins), "detail": len(initial_pins)},
        )
    )
    overall_status = "PASS" if all(item.get("pass") is True for item in checks) else "FAIL"
    summary = {
        "schema": SUMMARY_SCHEMA,
        "overall_status": overall_status,
        "decision": (
            "EXISTING_AUDITED_GDS_FRESH_EMX_COMPLETE"
            if overall_status == "PASS"
            else "DO_NOT_USE_EXISTING_GDS_FRESH_EMX_OUTPUT"
        ),
        "candidate_source": _file_record(candidate_csv),
        "audited_index_source": _file_record(audited_index),
        "config_source": _file_record(config_path),
        "dataset_rows_source": _file_record(dataset_path),
        "input_row_count": len(candidate_rows),
        "selected_row_count": len(candidate_rows),
        "geometry_count": len(geometries),
        "result_count": len(results),
        "ok_count": sum(item.ok() for item in results),
        "fail_count": sum(not item.ok() for item in results),
        "run_emx": True,
        "create_only": False,
        "cadence_streamout_only": False,
        "existing_audited_gds_reuse": True,
        "cadence_executed": False,
        "gds_generated_or_copied": False,
        "calibre_executed": False,
        "touchstone_output_contract": touchstone_contract["summary"],
        "source_bindings": source_bindings,
        "source_manifest_bindings": manifest_binding_records,
        "source_manifest_aggregate_sha256": manifest_aggregate_sha256,
        "external_execution_files": external_execution_files,
        "operator_tool_execution_files": tool_execution_files,
        "execution_records": execution_records,
        "output_gds_files": output_gds,
        "forbidden_fresh_output_tree": forbidden_tree,
        "checks": checks,
        "automatic_production_authorized": False,
        "automatic_merge_authorized": False,
        "scientific_boundary": (
            "PASS proves fresh EMX was run on the exact already audited GDS paths "
            "without Cadence, GDS regeneration/copying, or Calibre. It does not merge "
            "or authorize production/training."
        ),
    }
    summary_path = out_dir / SUMMARY_NAME
    _write_json_atomic(summary_path, summary)
    _assert_pins(initial_pins)
    _assert_external_execution_files(external_execution_files)
    _assert_tool_execution_files(tool_execution_files)
    if overall_status != "PASS":
        raise ExistingGdsEmxFailure("fresh EMX output checks failed")
    return {
        "overall_status": "PASS",
        "dataset_rows_path": str(dataset_path),
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _validate_external_execution_files(
    *,
    run_config: Any,
    expected_sha256_by_role: dict[str, str],
) -> list[dict[str, Any]]:
    values = {
        "emx_binary": run_config.emx.emx_binary,
        "emx_process_file": run_config.emx.emx_process_file,
        "cadence_pdk_cds_lib": run_config.emx.cadence_pdk_cds_lib,
        "cadence_layer_map": run_config.emx.cadence_layer_map,
    }
    records: list[dict[str, Any]] = []
    for role, raw in values.items():
        path = Path(str(raw or "")).expanduser()
        expected = str(expected_sha256_by_role.get(role) or "").lower()
        if not path.is_absolute():
            raise ExistingGdsEmxFailure(
                f"external execution dependency is not absolute: {role}={path}"
            )
        path = Path(os.path.abspath(str(path)))
        safe_record = _read_operator_pinned_file(
            path,
            expected,
            role,
            require_executable=role == "emx_binary",
        )
        checks = {
            "expected_sha256_valid": True,
            "regular_nonempty": True,
            "not_symlink": True,
            "sha256_exact": True,
            "executable_when_required": True,
        }
        _require_checks(checks, f"external execution dependency {role}")
        records.append(
            {
                "role": role,
                "path": str(path),
                "sha256": expected,
                "size_bytes": safe_record["size_bytes"],
                "is_symlink": False,
                "checks": checks,
            }
        )
    return records


def _read_operator_pinned_file(
    path: Path,
    expected_sha256: str,
    label: str,
    *,
    require_executable: bool = False,
) -> dict[str, Any]:
    expected = str(expected_sha256 or "").lower()
    if not _is_sha256(expected):
        raise ExistingGdsEmxFailure(f"invalid operator SHA pin for {label}")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ExistingGdsEmxFailure(f"external dependency is missing: {label}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ExistingGdsEmxFailure(
            f"external dependency must be a regular non-symlink: {label}"
        )
    if stage2_contract._path_has_symlink_component(path):
        raise ExistingGdsEmxFailure(
            f"external dependency path contains a symlink component: {label}"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExistingGdsEmxFailure(
            f"external dependency cannot be opened safely: {label}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if not stat.S_ISREG(opened.st_mode) or identity != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise ExistingGdsEmxFailure(
                f"external dependency identity changed before open: {label}"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if opened.st_size <= 0 or digest.hexdigest() != expected:
            raise ExistingGdsEmxFailure(
                f"external dependency SHA-256 mismatch: {label}"
            )
        try:
            after = os.lstat(path)
        except OSError as exc:
            raise ExistingGdsEmxFailure(
                f"external dependency disappeared after pinned read: {label}"
            ) from exc
        after_fd = os.fstat(descriptor)
        if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode) or identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or identity != (
            after_fd.st_dev,
            after_fd.st_ino,
            after_fd.st_size,
            after_fd.st_mtime_ns,
            after_fd.st_ctime_ns,
        ):
            raise ExistingGdsEmxFailure(
                f"external dependency identity changed after read: {label}"
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if require_executable and opened.st_mode & 0o111 == 0:
        raise ExistingGdsEmxFailure(f"external dependency is not executable: {label}")
    return {"path": str(path), "size_bytes": int(opened.st_size), "sha256": expected}


def _assert_external_execution_files(records: list[dict[str, Any]]) -> None:
    for record in records:
        role = str(record.get("role") or "")
        _read_operator_pinned_file(
            Path(str(record.get("path") or "")),
            str(record.get("sha256") or ""),
            role,
            require_executable=role == "emx_binary",
        )


def _operator_execution_record(
    *, role: str, path: Path, expected_sha256: str
) -> dict[str, Any]:
    safe = _read_operator_pinned_file(
        path, expected_sha256, role
    )
    return {
        "role": role,
        "path": safe["path"],
        "sha256": str(expected_sha256).lower(),
        "size_bytes": safe["size_bytes"],
        "is_symlink": False,
        "private_snapshot": True,
    }


def _assert_tool_execution_files(records: list[dict[str, Any]]) -> None:
    roles = {str(record.get("role") or "") for record in records}
    if roles != {"direct_runner_snapshot", "stage2_supervisor_snapshot"}:
        raise ExistingGdsEmxFailure("private tool execution roles are not exact")
    for record in records:
        _read_operator_pinned_file(
            Path(str(record.get("path") or "")),
            str(record.get("sha256") or ""),
            str(record.get("role") or ""),
        )


def _expected_emx_command(
    *,
    emx_binary: Path,
    source_gds: Path,
    process_file: Path,
    touchstone_path: Path,
    extra_args: list[str],
) -> list[str]:
    return [
        str(emx_binary),
        str(source_gds),
        "TRANSFORMER",
        str(process_file),
        "--touchstone",
        "--s-impedance=50",
        "-s",
        str(touchstone_path),
        "--include-command-line",
        *extra_args,
        "--cadence-pins=51",
        "--port=P001=P001:P001_G",
        "--port=P002=P002:P002_G",
        "--port=P003=P003:P003_G",
        "--port=P004=P004:P004_G",
        "--sweep",
        "5000000000",
        "60000000000",
        "--sweep-stepsize",
        "500000000",
    ]


def _extra_args_are_safe(values: list[str]) -> bool:
    reserved_exact = {
        "-s",
        "--touchstone",
        "--include-command-line",
        "--sweep",
        "--sweep-stepsize",
    }
    reserved_prefixes = (
        "--s-impedance",
        "--cadence-pins",
        "--port",
        "--touchstone",
        "--include-command-line",
        "--sweep",
        "--sweep-stepsize",
        "-s=",
    )
    return len(values) == len(set(values)) and all(
        value
        and "\x00" not in value
        and "\n" not in value
        and "\r" not in value
        and value not in reserved_exact
        and not value.startswith(reserved_prefixes)
        for value in values
    )


def _read_command(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExistingGdsEmxFailure(f"EMX command is unreadable: {path}") from exc
    if not isinstance(payload, list) or not all(
        isinstance(token, str) for token in payload
    ):
        raise ExistingGdsEmxFailure("EMX command must be a JSON string array")
    return payload


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        return [], set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], set(reader.fieldnames or [])


def _unique_rows(rows: list[dict[str, str]], field: str, label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = str(row.get(field) or "").lower()
        if not _is_sha256(key) or key in result:
            raise ExistingGdsEmxFailure(f"{label} has invalid/duplicate {field}")
        result[key] = row
    return result


def _resolve_artifact(owner: Path, raw: Any) -> Path:
    path = Path(str(raw or "")).expanduser()
    return path.resolve() if path.is_absolute() else (owner.parent / path).resolve()


def _lexical_artifact(owner: Path, raw: Any) -> Path:
    path = Path(str(raw or "")).expanduser()
    combined = path if path.is_absolute() else owner.parent / path
    return Path(os.path.abspath(str(combined)))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ExistingGdsEmxFailure(f"JSON is not an object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")))


def _pins_match(pins: dict[str, str]) -> bool:
    return all(_sha256(Path(path)) == expected for path, expected in pins.items())


def _assert_pins(pins: dict[str, str]) -> None:
    if not _pins_match(pins):
        drift = {
            path: {"expected": expected, "actual": _sha256(Path(path))}
            for path, expected in pins.items()
            if _sha256(Path(path)) != expected
        }
        raise ExistingGdsEmxFailure(f"immutable source drift detected: {drift}")


def _require_checks(checks: dict[str, bool], label: str) -> None:
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ExistingGdsEmxFailure(f"{label} checks failed: {failed}")


if __name__ == "__main__":
    raise SystemExit(main())
