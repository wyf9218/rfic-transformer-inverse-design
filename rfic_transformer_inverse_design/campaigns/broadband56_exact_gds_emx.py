"""Run fresh EMX on one exact, already Calibre-audited GDS artifact.

This module is deliberately narrower than the Cadence round-trip evaluator.
It never invokes Cadence or Calibre and never copies or regenerates GDS.  A
successful receipt proves that the exact GDS bytes bound by a zero-blocking
Calibre receipt were passed to EMX and produced an exact-grid four-port S4P.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import gdstk
import numpy as np

from ..core.defaults import load_run_config
from ..core.types import TransformerLayoutExport
from ..execution.zeus_cadence import _run_emx, load_emx_layout_manifest
from ..sim.touchstone import load_touchstone
from .broadband56_balanced200k import (
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    TARGET_ACCEPTED_GEOMETRIES,
)
from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT
from .broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
    PORT_AND_GROUNDING_CONTRACT,
)


CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_calibre_zero_blocking_receipt.v1"
)
CALIBRE_ZERO_BLOCKING_PASS_DECISION = "USE_EXACT_ZERO_BLOCKING_GDS_FOR_FRESH_EMX"
EXACT_GDS_EMX_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_exact_audited_gds_fresh_emx_receipt.v1"
)
EXACT_GDS_EMX_PASS_DECISION = "ACCEPT_EXACT_AUDITED_GDS_FRESH_EMX"
EXACT_GDS_EMX_RECEIPT_NAME = "EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json"
EXACT_GDS_EMX_FAILURE_NAME = "EXACT_AUDITED_GDS_FRESH_EMX_FAILURE.json"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ExactAuditedGdsEmxError(RuntimeError):
    """The exact audited-GDS EMX contract failed closed."""


@dataclass(frozen=True)
class ImmutableFilePin:
    path: str
    size_bytes: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int

    def public_record(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def run_exact_audited_gds_fresh_emx(
    *,
    config_path: Path,
    expected_config_sha256: str,
    gds_path: Path,
    expected_gds_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    calibre_receipt_path: Path,
    expected_calibre_receipt_sha256: str,
    full_campaign_receipt_path: Path,
    expected_full_campaign_receipt_sha256: str,
    candidate_id_sha256: str,
    geometry_identity_sha256: str,
    out_dir: Path,
    run_emx_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute one no-regeneration EMX run and write a hash-bound receipt."""

    candidate_id = _require_sha256(candidate_id_sha256, "candidate_id_sha256")
    geometry_id = _require_sha256(
        geometry_identity_sha256, "geometry_identity_sha256"
    )
    output = _absolute_lexical(out_dir)
    if output.exists():
        raise ExactAuditedGdsEmxError(f"refusing existing output directory: {output}")
    if _path_has_symlink_component(output.parent):
        raise ExactAuditedGdsEmxError(
            f"output parent contains a symlink component: {output.parent}"
        )

    config_pin, _ = _pin_regular_file(
        config_path,
        expected_sha256=expected_config_sha256,
        label="private configuration",
    )
    gds_pin, _ = _pin_regular_file(
        gds_path,
        expected_sha256=expected_gds_sha256,
        label="Calibre-audited GDS",
    )
    manifest_pin, _ = _pin_regular_file(
        manifest_path,
        expected_sha256=expected_manifest_sha256,
        label="layout manifest",
    )
    calibre_pin, calibre_bytes = _pin_regular_file(
        calibre_receipt_path,
        expected_sha256=expected_calibre_receipt_sha256,
        label="Calibre zero-blocking receipt",
        capture_bytes=True,
    )
    authorization_pin, authorization_bytes = _pin_regular_file(
        full_campaign_receipt_path,
        expected_sha256=expected_full_campaign_receipt_sha256,
        label="FULL_CAMPAIGN authorization receipt",
        capture_bytes=True,
    )
    source_pins = [
        config_pin,
        gds_pin,
        manifest_pin,
        calibre_pin,
        authorization_pin,
    ]

    authorization = _decode_json_object(
        authorization_bytes, "FULL_CAMPAIGN authorization receipt"
    )
    _validate_full_campaign_receipt(authorization)

    run_config = load_run_config(Path(config_pin.path))
    _reverify_pin(config_pin, "private configuration")
    _validate_run_config(run_config)

    manifest = load_emx_layout_manifest(Path(manifest_pin.path))
    _reverify_pin(manifest_pin, "layout manifest")
    manifest_contract = _validate_manifest(manifest, run_config=run_config)

    calibre_receipt = _decode_json_object(
        calibre_bytes, "Calibre zero-blocking receipt"
    )
    calibre_report_pin = _validate_calibre_receipt(
        calibre_receipt,
        receipt_path=Path(calibre_pin.path),
        candidate_id=candidate_id,
        geometry_id=geometry_id,
        config_pin=config_pin,
        gds_pin=gds_pin,
        manifest_pin=manifest_pin,
        top_cell=manifest.top_cell,
    )
    source_pins.append(calibre_report_pin)

    _validate_gds_top_cell(Path(gds_pin.path), manifest.top_cell)
    _reverify_pins(source_pins)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700, exist_ok=False)
    emx_attempted = False
    try:
        _reverify_pins(source_pins)
        layout = TransformerLayoutExport(
            gds_path=Path(gds_pin.path),
            manifest_path=Path(manifest_pin.path),
            preview_path=Path(gds_pin.path).with_suffix(".png"),
            debug_preview_path=Path(gds_pin.path).with_name(
                "transformer_port_debug.png"
            ),
            top_cell=manifest.top_cell,
        )
        emx_attempted = True
        runner = _run_emx if run_emx_fn is None else run_emx_fn
        emx_payload = dict(
            runner(
                run_config=run_config,
                work_dir=output,
                layout=layout,
                manifest=manifest,
            )
        )
        _reverify_pins(source_pins)

        output_contract = _validate_emx_output(
            output=output,
            emx_payload=emx_payload,
            exact_gds=Path(gds_pin.path),
        )
        _reverify_pins(source_pins)
        forbidden_output = _scan_forbidden_output(output)
        if forbidden_output["gds_files"]:
            raise ExactAuditedGdsEmxError(
                "EMX output contains a copied or generated GDS artifact"
            )
        if forbidden_output["forbidden_directories"]:
            raise ExactAuditedGdsEmxError(
                "EMX output contains a Cadence, Calibre, or streamout directory"
            )
        if forbidden_output["symlinks"]:
            raise ExactAuditedGdsEmxError("EMX output contains a symlink")

        receipt = {
            "schema": EXACT_GDS_EMX_RECEIPT_SCHEMA,
            "overall_status": "PASS",
            "decision": EXACT_GDS_EMX_PASS_DECISION,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "candidate_id_sha256": candidate_id,
            "geometry_identity_sha256": geometry_id,
            "full_campaign_authorization_receipt": authorization_pin.public_record(),
            "private_configuration": config_pin.public_record(),
            "source_calibre_zero_blocking_receipt": calibre_pin.public_record(),
            "source_calibre_report": calibre_report_pin.public_record(),
            "source_exact_gds": gds_pin.public_record(),
            "source_layout_manifest": manifest_pin.public_record(),
            "top_cell": manifest.top_cell,
            "manifest_contract": manifest_contract,
            "frequency_contract": {
                "start_hz": FREQUENCY_GRID_HZ[0],
                "stop_hz": FREQUENCY_GRID_HZ[-1],
                "step_hz": 1_000_000_000,
                "points": len(FREQUENCY_GRID_HZ),
                "exact_hz": list(FREQUENCY_GRID_HZ),
            },
            "emx_output": output_contract,
            "source_pins_unchanged_after_emx": True,
            "cadence_executed_by_this_runner": False,
            "calibre_executed_by_this_runner": False,
            "gds_generated_or_copied_by_this_runner": False,
            "fresh_real_emx_executed": True,
            "proxy_or_historical_label_used": False,
            "simulator_action_taken": True,
            "forbidden_output_scan": forbidden_output,
        }
        receipt_path = output / EXACT_GDS_EMX_RECEIPT_NAME
        _write_json_new(receipt_path, receipt)
        _reverify_pins(source_pins)
        sums_path = output / SHA256SUMS_NAME
        _write_sums_new(
            sums_path,
            [
                receipt_path,
                Path(output_contract["touchstone_path"]),
                Path(output_contract["emx_command_path"]),
            ],
            root=output,
        )
        return {
            "overall_status": "PASS",
            "receipt_path": str(receipt_path),
            "receipt_sha256": _sha256(receipt_path),
            "sha256s_path": str(sums_path),
            "touchstone_path": output_contract["touchstone_path"],
            "touchstone_sha256": output_contract["touchstone_sha256"],
        }
    except Exception as exc:
        failure_path = output / EXACT_GDS_EMX_FAILURE_NAME
        if not failure_path.exists():
            _write_json_new(
                failure_path,
                {
                    "schema": EXACT_GDS_EMX_RECEIPT_SCHEMA,
                    "overall_status": "FAIL",
                    "decision": "REJECT_EXACT_AUDITED_GDS_FRESH_EMX",
                    "campaign_id": CAMPAIGN_ID,
                    "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                    "candidate_id_sha256": candidate_id,
                    "geometry_identity_sha256": geometry_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "emx_attempted": emx_attempted,
                    "cadence_executed_by_this_runner": False,
                    "calibre_executed_by_this_runner": False,
                    "gds_generated_or_copied_by_this_runner": False,
                    "simulator_action_taken": emx_attempted,
                },
            )
        raise


def _validate_full_campaign_receipt(receipt: Mapping[str, Any]) -> None:
    checks = {
        "schema": receipt.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA,
        "overall_status": receipt.get("overall_status") == "PASS",
        "decision": receipt.get("decision") == FULL_CAMPAIGN_PASS_DECISION,
        "authorization_scope": receipt.get("authorization_scope")
        == FULL_CAMPAIGN_APPROVAL_SCOPE,
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract_fingerprint": receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "approved_by": bool(str(receipt.get("approved_by") or "").strip()),
        "emx_authorized": receipt.get("emx_authorized_within_current_stage") is True,
        "campaign_200k_authorized": receipt.get("campaign_200k_authorized") is True,
        "simulator_geometry_limit": receipt.get("simulator_geometry_limit")
        == TARGET_ACCEPTED_GEOMETRIES,
    }
    _require_checks(checks, "FULL_CAMPAIGN authorization")


def _validate_run_config(run_config: Any) -> None:
    frequencies = tuple(int(value) for value in run_config.target.frequency_points_hz())
    port_contract = PORT_AND_GROUNDING_CONTRACT
    checks = {
        "frequency_grid_exact": frequencies == FREQUENCY_GRID_HZ,
        "port_mode_exact": run_config.emx.port_mode == port_contract["port_mode"],
        "cadence_pin_purpose_exact": run_config.emx.cadence_pin_purpose
        == port_contract["cadence_pin_purpose"],
        "touchstone_mode_exact": run_config.emx.power_line_8port.touchstone_mode
        == port_contract["touchstone_mode"],
        "port_order_exact": tuple(run_config.emx.power_line_8port.port_map)
        == tuple(port_contract["port_order"]),
        "ground_unused_aux_ports_false": run_config.emx.ground_unused_s8p_ports
        is False,
        "local_execution": run_config.emx.execution_mode == "local",
    }
    _require_checks(checks, "private configuration")


def _validate_manifest(manifest: Any, *, run_config: Any) -> dict[str, Any]:
    expected_ports = tuple(PORT_AND_GROUNDING_CONTRACT["port_order"])
    port_names = tuple(str(port.name) for port in manifest.ports)
    signal_labels = tuple(
        str(label) for port in manifest.ports for label in port.signal_labels
    )
    checks = {
        "port_count_exact_four": len(manifest.ports) == 4,
        "port_names_exact": port_names == expected_ports,
        "signal_labels_exact": signal_labels == expected_ports,
        "cadence_pin_purpose_exact": manifest.cadence_pin_purpose
        == PORT_AND_GROUNDING_CONTRACT["cadence_pin_purpose"],
        "top_cell_exact": str(manifest.top_cell)
        == str(run_config.emx.top_cell_prefix),
    }
    _require_checks(checks, "layout manifest")
    return {
        "port_count": len(manifest.ports),
        "port_order": list(port_names),
        "signal_labels": list(signal_labels),
        "cadence_pin_purpose": manifest.cadence_pin_purpose,
        "top_cell": manifest.top_cell,
        "checks": checks,
    }


def _validate_calibre_receipt(
    receipt: Mapping[str, Any],
    *,
    receipt_path: Path,
    candidate_id: str,
    geometry_id: str,
    config_pin: ImmutableFilePin,
    gds_pin: ImmutableFilePin,
    manifest_pin: ImmutableFilePin,
    top_cell: str,
) -> ImmutableFilePin:
    report_path = _receipt_artifact_path(
        receipt_path, receipt.get("calibre_report_path"), "calibre_report_path"
    )
    checks = {
        "schema": receipt.get("schema") == CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
        "overall_status": receipt.get("overall_status") == "PASS",
        "decision": receipt.get("decision")
        == CALIBRE_ZERO_BLOCKING_PASS_DECISION,
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract_fingerprint": receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate_id": str(receipt.get("candidate_id_sha256") or "").lower()
        == candidate_id,
        "geometry_identity": str(
            receipt.get("geometry_identity_sha256") or ""
        ).lower()
        == geometry_id,
        "config_path": _receipt_artifact_path(
            receipt_path, receipt.get("config_path"), "config_path"
        )
        == Path(config_pin.path),
        "config_size": receipt.get("config_size_bytes") == config_pin.size_bytes,
        "config_sha256": str(receipt.get("config_sha256") or "").lower()
        == config_pin.sha256,
        "gds_path": _receipt_artifact_path(
            receipt_path, receipt.get("gds_path"), "gds_path"
        )
        == Path(gds_pin.path),
        "gds_size": receipt.get("gds_size_bytes") == gds_pin.size_bytes,
        "gds_sha256": str(receipt.get("gds_sha256") or "").lower()
        == gds_pin.sha256,
        "manifest_path": _receipt_artifact_path(
            receipt_path, receipt.get("manifest_path"), "manifest_path"
        )
        == Path(manifest_pin.path),
        "manifest_size": receipt.get("manifest_size_bytes")
        == manifest_pin.size_bytes,
        "manifest_sha256": str(receipt.get("manifest_sha256") or "").lower()
        == manifest_pin.sha256,
        "top_cell": str(receipt.get("top_cell") or "") == str(top_cell),
        "cadence_streamout_complete": receipt.get("cadence_streamout_complete")
        is True,
        "calibre_executed": receipt.get("calibre_executed") is True,
        "zero_blocking": receipt.get("calibre_blocking_violations") == 0,
    }
    _require_checks(checks, "Calibre zero-blocking receipt")
    return _pin_regular_file(
        report_path,
        expected_sha256=str(receipt.get("calibre_report_sha256") or ""),
        expected_size=receipt.get("calibre_report_size_bytes"),
        label="Calibre report",
    )[0]


def _validate_gds_top_cell(gds_path: Path, top_cell: str) -> None:
    try:
        library = gdstk.read_gds(str(gds_path))
    except Exception as exc:  # noqa: BLE001 - preserve parser failure evidence.
        raise ExactAuditedGdsEmxError(f"GDS parse failed: {exc}") from exc
    names = tuple(cell.name for cell in library.cells)
    if top_cell not in names:
        raise ExactAuditedGdsEmxError(
            f"GDS does not contain manifest top cell {top_cell!r}"
        )


def _validate_emx_output(
    *,
    output: Path,
    emx_payload: Mapping[str, Any],
    exact_gds: Path,
) -> dict[str, Any]:
    touchstone_raw = emx_payload.get("touchstone_path")
    if not touchstone_raw:
        raise ExactAuditedGdsEmxError("EMX payload lacks touchstone_path")
    touchstone_path = _absolute_lexical(Path(str(touchstone_raw)))
    if not _is_within(touchstone_path, output):
        raise ExactAuditedGdsEmxError("EMX Touchstone output is outside no-clobber output")
    touchstone_pin, _ = _pin_regular_file(
        touchstone_path,
        expected_sha256=None,
        label="fresh EMX Touchstone",
    )
    if touchstone_path.suffix.lower() != ".s4p":
        raise ExactAuditedGdsEmxError("fresh EMX output is not .s4p")
    parsed = load_touchstone(touchstone_path)
    expected_frequency = np.asarray(FREQUENCY_GRID_HZ, dtype=np.float64)
    checks = {
        "port_count_exact_four": int(parsed.num_ports) == 4,
        "frequency_count_exact_56": int(parsed.num_freqs) == 56,
        "frequency_vector_exact": np.array_equal(
            np.asarray(parsed.freqs_hz, dtype=np.float64), expected_frequency
        ),
        "s_matrix_shape_exact": tuple(parsed.s_matrix.shape) == (56, 4, 4),
        "s_matrix_finite": bool(
            np.isfinite(np.asarray(parsed.s_matrix).real).all()
            and np.isfinite(np.asarray(parsed.s_matrix).imag).all()
        ),
    }
    _require_checks(checks, "fresh EMX S4P")

    command_path = output / "emx" / "emx_command.json"
    command_pin, command_bytes = _pin_regular_file(
        command_path,
        expected_sha256=None,
        label="EMX command",
        capture_bytes=True,
    )
    command = _decode_json_array(command_bytes, "EMX command")
    gds_arguments = [
        _absolute_lexical(Path(str(value)))
        for value in command
        if str(value).lower().endswith(".gds")
    ]
    if gds_arguments != [exact_gds]:
        raise ExactAuditedGdsEmxError(
            "EMX command is not bound to exactly one exact audited GDS path"
        )
    return {
        "touchstone_path": touchstone_pin.path,
        "touchstone_size_bytes": touchstone_pin.size_bytes,
        "touchstone_sha256": touchstone_pin.sha256,
        "emx_command_path": command_pin.path,
        "emx_command_size_bytes": command_pin.size_bytes,
        "emx_command_sha256": command_pin.sha256,
        "num_ports": int(parsed.num_ports),
        "num_frequency_points": int(parsed.num_freqs),
        "frequency_start_hz": int(parsed.freqs_hz[0]),
        "frequency_stop_hz": int(parsed.freqs_hz[-1]),
        "frequency_step_hz": int(parsed.freqs_hz[1] - parsed.freqs_hz[0]),
        "checks": checks,
    }


def _scan_forbidden_output(output: Path) -> dict[str, list[str]]:
    gds_files: list[str] = []
    symlinks: list[str] = []
    forbidden_directories: list[str] = []
    for path in output.rglob("*"):
        if path.is_symlink():
            symlinks.append(str(path))
            continue
        if path.is_file() and path.suffix.lower() == ".gds":
            gds_files.append(str(path))
        if path.is_dir() and path.name.lower() in {"cadence", "calibre", "streamout"}:
            forbidden_directories.append(str(path))
    return {
        "gds_files": sorted(gds_files),
        "symlinks": sorted(symlinks),
        "forbidden_directories": sorted(forbidden_directories),
    }


def _pin_regular_file(
    path: Path,
    *,
    expected_sha256: str | None,
    label: str,
    expected_size: Any | None = None,
    capture_bytes: bool = False,
) -> tuple[ImmutableFilePin, bytes]:
    absolute = _absolute_lexical(path)
    if _path_has_symlink_component(absolute):
        raise ExactAuditedGdsEmxError(f"{label} path contains a symlink component")
    try:
        before = os.lstat(absolute)
    except OSError as exc:
        raise ExactAuditedGdsEmxError(f"{label} is missing: {absolute}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise ExactAuditedGdsEmxError(f"{label} is not a nonempty regular file")
    descriptor = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    captured: list[bytes] = []
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        identity = _stat_identity(opened)
        if identity != _stat_identity(before):
            raise ExactAuditedGdsEmxError(f"{label} identity changed before read")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if capture_bytes:
                captured.append(chunk)
        after_fd = os.fstat(descriptor)
        after_path = os.lstat(absolute)
        if identity != _stat_identity(after_fd) or identity != _stat_identity(after_path):
            raise ExactAuditedGdsEmxError(f"{label} identity changed during read")
    finally:
        os.close(descriptor)
    actual_sha256 = digest.hexdigest()
    if expected_sha256 is not None:
        expected = _require_sha256(expected_sha256, f"expected {label} SHA-256")
        if actual_sha256 != expected:
            raise ExactAuditedGdsEmxError(f"{label} SHA-256 mismatch")
    if expected_size is not None and expected_size != opened.st_size:
        raise ExactAuditedGdsEmxError(f"{label} size mismatch")
    return (
        ImmutableFilePin(
            path=str(absolute),
            size_bytes=int(opened.st_size),
            sha256=actual_sha256,
            device=int(opened.st_dev),
            inode=int(opened.st_ino),
            mtime_ns=int(opened.st_mtime_ns),
            ctime_ns=int(opened.st_ctime_ns),
        ),
        b"".join(captured),
    )


def _reverify_pin(pin: ImmutableFilePin, label: str) -> None:
    current, _ = _pin_regular_file(
        Path(pin.path),
        expected_sha256=pin.sha256,
        expected_size=pin.size_bytes,
        label=label,
    )
    if current != pin:
        raise ExactAuditedGdsEmxError(f"{label} inode identity changed")


def _reverify_pins(pins: list[ImmutableFilePin]) -> None:
    for pin in pins:
        _reverify_pin(pin, Path(pin.path).name)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _path_has_symlink_component(path: Path) -> bool:
    absolute = _absolute_lexical(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
        except FileNotFoundError:
            continue
    return False


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _receipt_artifact_path(receipt_path: Path, value: Any, field: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ExactAuditedGdsEmxError(f"Calibre receipt lacks {field}")
    path = Path(text)
    if not path.is_absolute():
        path = receipt_path.parent / path
    return _absolute_lexical(path)


def _decode_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - retain exact parse failure.
        raise ExactAuditedGdsEmxError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ExactAuditedGdsEmxError(f"{label} must be a JSON object")
    return value


def _decode_json_array(raw: bytes, label: str) -> list[Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - retain exact parse failure.
        raise ExactAuditedGdsEmxError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise ExactAuditedGdsEmxError(f"{label} must be a nonempty JSON array")
    return value


def _require_sha256(value: Any, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise ExactAuditedGdsEmxError(f"{label} is not SHA-256")
    return normalized


def _require_checks(checks: Mapping[str, bool], label: str) -> None:
    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if failed:
        raise ExactAuditedGdsEmxError(
            f"{label} failed checks: {', '.join(failed)}"
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_sums_new(path: Path, files: list[Path], *, root: Path) -> None:
    lines = [f"{_sha256(item)}  {item.relative_to(root)}" for item in files]
    with Path(path).open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


__all__ = [
    "CALIBRE_ZERO_BLOCKING_PASS_DECISION",
    "CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA",
    "EXACT_GDS_EMX_FAILURE_NAME",
    "EXACT_GDS_EMX_PASS_DECISION",
    "EXACT_GDS_EMX_RECEIPT_NAME",
    "EXACT_GDS_EMX_RECEIPT_SCHEMA",
    "ExactAuditedGdsEmxError",
    "ImmutableFilePin",
    "run_exact_audited_gds_fresh_emx",
]
