#!/usr/bin/env python3
"""Freeze and preflight the broadband56 balanced-200k production contract.

The script performs no Cadence, Calibre, or EMX work.  It refuses to prepare a
campaign unless the supplied production configuration is identical to the
approved previous broadband56 configuration except for the already-approved
frequency-grid fields, and that grid is exactly 5-60 GHz in 1-GHz steps.  A
newly reconstructed non-historical baseline additionally requires a separate,
exactly SHA-bound approval receipt limited to preparation preflight.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import TransformerOptimizationAdapter, load_run_config  # noqa: E402
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    build_phase_plan,
    canonical_geometry_bounds,
    contract_fingerprint,
    primary_bin_edges,
    secondary_coverage_contract,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (  # noqa: E402
    geometry_bounds_payload,
)


FREQUENCY_FIELDS = {
    "frequency_start_hz",
    "frequency_stop_hz",
    "frequency_step_hz",
    "band_points",
}
FOUNDRY_LAYOUT_CONTRACT = {
    "enabled": True,
    "manufacturing_grid_um": 0.005,
    "power_line_stitch_pad_depth_um": 6.0,
    "shield_strap_width_um": 10.0,
    "shield_strap_pitch_um": 20.0,
}
RECONSTRUCTED_BASELINE_ORIGIN = "NEW_RECONSTRUCTION_NOT_HISTORICAL_V1"
RECONSTRUCTED_APPROVAL_SCHEMA = "rfic_transformer.broadband56_reconstructed_baseline_approval.v1"
RECONSTRUCTED_APPROVAL_DECISION = "APPROVE_V2_PREPARATION_PREFLIGHT_ONLY"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    override_path = Path(args.override_contract).expanduser().resolve()
    previous_contract_path = Path(args.previous_contract).expanduser().resolve()
    previous_config_path = Path(args.previous_config).expanduser().resolve()
    production_config_path = Path(args.production_config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    _require_new_output_directory(out_dir)
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    override = _read_json(override_path, checks, "override_contract")
    previous_contract = _read_json(previous_contract_path, checks, "previous_contract")
    _check_file_sha(
        checks,
        "previous_contract_sha256_matches_approved",
        previous_contract_path,
        str(args.previous_contract_sha256),
    )
    approval_receipt_path: Path | None = None
    approval_receipt_sha256: str | None = None
    if str(previous_contract.get("contract_origin") or "") == RECONSTRUCTED_BASELINE_ORIGIN:
        if args.previous_contract_approval_receipt:
            approval_receipt_path = Path(args.previous_contract_approval_receipt).expanduser().resolve()
            approval_receipt = _read_json(
                approval_receipt_path,
                checks,
                "previous_contract_approval_receipt",
            )
            _validate_reconstructed_baseline_approval(
                checks,
                approval_receipt,
                previous_contract,
                previous_contract_path,
            )
            if approval_receipt_path.is_file():
                approval_receipt_sha256 = _sha256(approval_receipt_path)
        else:
            checks.append(
                _check(
                    "reconstructed_baseline_approval_receipt_provided",
                    False,
                    "a reconstructed non-historical baseline requires an independently SHA-bound approval receipt",
                )
            )

    for error in validate_contract(override):
        checks.append(_check(f"override_contract::{error}", False, error))

    previous_cfg = _load_config(previous_config_path, checks, "previous_config")
    production_cfg = _load_config(production_config_path, checks, "production_config")
    previous_raw = _load_raw_config(previous_config_path, checks, "previous_config")
    production_raw = _load_raw_config(production_config_path, checks, "production_config")
    geometry_bounds: dict[str, tuple[float, float]] = {}
    if (
        previous_cfg is not None
        and production_cfg is not None
        and previous_raw is not None
        and production_raw is not None
    ):
        _validate_inherited_configuration(
            checks,
            previous_cfg,
            production_cfg,
            previous_raw=previous_raw,
            production_raw=production_raw,
        )
        _validate_private_runtime_paths(checks, production_cfg)
        try:
            geometry_bounds = canonical_geometry_bounds(
                TransformerOptimizationAdapter(production_cfg.bounds)
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                _check(
                    "canonical_geometry_bounds_freeze",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            checks.append(
                _check(
                    "canonical_geometry_bounds_freeze",
                    True,
                    "exact normalized 10-D bounds derived from production config",
                )
            )

    previous_campaign_id = str(previous_contract.get("campaign_id") or "")
    checks.append(
        _check(
            "previous_contract_is_not_v2_self_reference",
            bool(previous_campaign_id) and previous_campaign_id != CAMPAIGN_ID,
            f"campaign_id={previous_campaign_id!r}",
        )
    )
    previous_grid = previous_contract.get("frequency_grid") or {}
    checks.append(
        _check(
            "previous_contract_is_broadband56",
            int(previous_grid.get("points") or 0) == 56
            and float(previous_grid.get("start_ghz") or 0.0) == 5.0
            and float(previous_grid.get("stop_ghz") or 0.0) == 60.0
            and float(previous_grid.get("step_ghz") or 0.0) == 1.0,
            json.dumps(previous_grid, sort_keys=True),
        )
    )

    status = "PASS" if checks and all(item["pass"] for item in checks) else "FAIL"
    inherited_evidence = {
        "previous_campaign_id": previous_campaign_id or None,
        "previous_contract_sha256": _sha256(previous_contract_path) if previous_contract_path.is_file() else None,
        "previous_contract_origin": str(previous_contract.get("contract_origin") or "HISTORICAL_OR_UNSPECIFIED"),
        "previous_contract_approval_receipt_sha256": approval_receipt_sha256,
        "previous_config_sha256": _sha256(previous_config_path) if previous_config_path.is_file() else None,
        "production_config_sha256": _sha256(production_config_path) if production_config_path.is_file() else None,
        "private_runtime_paths_not_for_publication": True,
    }
    frozen_contract = dict(override)
    frozen_contract["inherited_contract_evidence"] = inherited_evidence
    frozen_contract["preparation_status"] = status
    frozen_contract["contract_fingerprint_sha256"] = contract_fingerprint(frozen_contract)

    frozen_path = out_dir / "campaign_contract_frozen.json"
    bins_path = out_dir / "PRIMARY_BINS_FROZEN.json"
    secondary_bins_path = out_dir / "SECONDARY_COVERAGE_FROZEN.json"
    geometry_bounds_path = out_dir / "GEOMETRY_BOUNDS_FROZEN.json"
    phase_path = out_dir / "PHASE_PLAN_FROZEN.json"
    receipt_path = out_dir / "PREPARATION_RECEIPT.json"
    frozen_path.write_text(json.dumps(frozen_contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    bins_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": frozen_contract["contract_fingerprint_sha256"],
                "frozen_before_production": True,
                "bin_edges": primary_bin_edges(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    secondary_bins_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": frozen_contract["contract_fingerprint_sha256"],
                "frozen_before_production": True,
                "secondary_coverage": secondary_coverage_contract(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if geometry_bounds:
        bounds_payload = geometry_bounds_payload(
            bounds=geometry_bounds,
            contract_fingerprint_sha256=frozen_contract["contract_fingerprint_sha256"],
        )
        bounds_payload["preparation_status"] = status
    else:
        bounds_payload = {
            "schema": "rfic_transformer.broadband56_geometry_bounds.v1",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": frozen_contract["contract_fingerprint_sha256"],
            "preparation_status": "FAIL",
            "field_bounds_um": {},
            "contains_private_runtime_paths": False,
        }
    geometry_bounds_path.write_text(
        json.dumps(bounds_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    phase_path.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": frozen_contract["contract_fingerprint_sha256"],
                "phase_plan": build_phase_plan(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "PREPARED_FOR_GOLDEN_GATE" if status == "PASS" else "DO_NOT_START_CADENCE_CALIBRE_OR_EMX",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": frozen_contract["contract_fingerprint_sha256"],
        "checks": checks,
        "artifacts": {
            "frozen_contract": _file_evidence(frozen_path),
            "primary_bins": _file_evidence(bins_path),
            "secondary_coverage": _file_evidence(secondary_bins_path),
            "geometry_bounds": _file_evidence(geometry_bounds_path),
            "phase_plan": _file_evidence(phase_path),
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)

    print(f"overall_status={status}")
    print(f"decision={receipt['decision']}")
    print(f"contract_fingerprint={frozen_contract['contract_fingerprint_sha256']}")
    print(f"receipt={receipt_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--override-contract",
        default=str(root / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"),
    )
    parser.add_argument("--previous-contract", required=True)
    parser.add_argument("--previous-contract-sha256", required=True)
    parser.add_argument(
        "--previous-contract-approval-receipt",
        help="Required only for a NEW_RECONSTRUCTION_NOT_HISTORICAL_V1 baseline; must authorize preparation preflight only.",
    )
    parser.add_argument("--previous-config", required=True)
    parser.add_argument("--production-config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"no-clobber output already exists: {path}")


def _read_json(path: Path, checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(f"{name}_exists", False, str(path)))
        return {}
    checks.append(_check(f"{name}_exists", True, str(path)))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - exact parse failure belongs in the receipt.
        checks.append(_check(f"{name}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    valid = isinstance(value, dict)
    checks.append(_check(f"{name}_parses", valid, type(value).__name__))
    return value if valid else {}


def _load_config(path: Path, checks: list[dict[str, Any]], name: str) -> Any | None:
    checks.append(_check(f"{name}_exists", path.is_file(), str(path)))
    if not path.is_file():
        return None
    try:
        config = load_run_config(path)
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(f"{name}_loads", False, f"{type(exc).__name__}: {exc}"))
        return None
    checks.append(_check(f"{name}_loads", True, str(path)))
    return config


def _load_raw_config(
    path: Path,
    checks: list[dict[str, Any]],
    name: str,
) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check(
                f"{name}_raw_yaml_parses",
                False,
                f"{type(exc).__name__}: {exc}",
            )
        )
        return None
    valid = isinstance(payload, dict)
    checks.append(_check(f"{name}_raw_yaml_parses", valid, type(payload).__name__))
    return payload if valid else None


def _without_frequency_fields(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = copy.deepcopy(payload)
    target_value = canonical.get("target")
    target = dict(target_value) if isinstance(target_value, dict) else {}
    for field in FREQUENCY_FIELDS:
        target.pop(field, None)
    canonical["target"] = target
    return canonical


def _validate_foundry_layout_contract(
    checks: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    name: str,
) -> None:
    emx_value = payload.get("emx")
    emx = emx_value if isinstance(emx_value, dict) else {}
    foundry_value = emx.get("foundry_layout")
    foundry = foundry_value if isinstance(foundry_value, dict) else {}
    observed = {field: foundry.get(field) for field in FOUNDRY_LAYOUT_CONTRACT}
    checks.append(
        _check(
            f"{name}_foundry_layout_contract_exact",
            observed == FOUNDRY_LAYOUT_CONTRACT,
            json.dumps(observed, sort_keys=True, separators=(",", ":")),
        )
    )


def _validate_inherited_configuration(
    checks: list[dict[str, Any]],
    previous: Any,
    production: Any,
    *,
    previous_raw: dict[str, Any],
    production_raw: dict[str, Any],
) -> None:
    previous_payload = asdict(previous)
    production_payload = asdict(production)
    previous_target = dict(previous_payload.get("target") or {})
    production_target = dict(production_payload.get("target") or {})
    for field in FREQUENCY_FIELDS:
        previous_target.pop(field, None)
        production_target.pop(field, None)
    previous_payload["target"] = previous_target
    production_payload["target"] = production_target
    checks.append(
        _check(
            "all_non_frequency_configuration_is_identical_to_previous_broadband56",
            previous_payload == production_payload,
            "canonical dataclass comparison with only four frequency fields removed",
        )
    )
    checks.append(
        _check(
            "all_non_frequency_raw_configuration_is_identical_to_previous_broadband56",
            _without_frequency_fields(previous_raw)
            == _without_frequency_fields(production_raw),
            "raw YAML mapping comparison with only four frequency fields removed",
        )
    )
    _validate_foundry_layout_contract(
        checks,
        previous_raw,
        name="previous_config",
    )
    _validate_foundry_layout_contract(
        checks,
        production_raw,
        name="production_config",
    )
    grid = tuple(int(round(value)) for value in production.target.frequency_points_hz())
    checks.append(_check("production_frequency_grid_exact_56", grid == FREQUENCY_GRID_HZ, str(grid)))
    checks.append(
        _check(
            "production_port_mode_grounded_s4p",
            str(production.emx.port_mode) == "single_ended_shield_grounded"
            and str(production.emx.power_line_8port.touchstone_mode) == "signal_4_grounded_aux"
            and tuple(production.emx.power_line_8port.port_map) == ("P001", "P002", "P003", "P004"),
            f"port_mode={production.emx.port_mode}, touchstone_mode={production.emx.power_line_8port.touchstone_mode}",
        )
    )
    checks.append(_check("production_pin_purpose_51", int(production.emx.cadence_pin_purpose) == 51, production.emx.cadence_pin_purpose))


def _validate_private_runtime_paths(checks: list[dict[str, Any]], config: Any) -> None:
    paths = {
        "emx_binary": Path(config.emx.emx_binary).expanduser(),
        "emx_process_file": Path(config.emx.emx_process_file).expanduser(),
        "cadence_install_root": Path(config.emx.cadence_install_root).expanduser(),
        "cadence_pdk_cds_lib": Path(config.emx.cadence_pdk_cds_lib).expanduser(),
        "cadence_layer_map": Path(config.emx.cadence_layer_map).expanduser(),
    }
    for name, path in paths.items():
        expected = path.is_dir() if name == "cadence_install_root" else path.is_file()
        checks.append(_check(f"private_runtime::{name}_exists", expected, str(path)))
    checks.append(
        _check(
            "private_runtime::emx_binary_executable",
            paths["emx_binary"].is_file() and os.access(paths["emx_binary"], os.X_OK),
            str(paths["emx_binary"]),
        )
    )


def _validate_reconstructed_baseline_approval(
    checks: list[dict[str, Any]],
    approval: dict[str, Any],
    previous_contract: dict[str, Any],
    previous_contract_path: Path,
) -> None:
    approved_contract_value = approval.get("approved_contract")
    approved_contract = approved_contract_value if isinstance(approved_contract_value, dict) else {}
    approved_by = str(approval.get("approved_by") or "").strip()
    approved_utc = str(approval.get("approved_utc") or "").strip()
    approval_reference = str(approval.get("approval_reference") or "").strip()
    checks.extend(
        [
            _check(
                "reconstructed_baseline_approved_contract_is_object",
                isinstance(approved_contract_value, dict),
                type(approved_contract_value).__name__,
            ),
            _check(
                "reconstructed_baseline_approval_schema",
                approval.get("schema") == RECONSTRUCTED_APPROVAL_SCHEMA,
                approval.get("schema"),
            ),
            _check(
                "reconstructed_baseline_approval_status",
                approval.get("overall_status") == "PASS",
                approval.get("overall_status"),
            ),
            _check(
                "reconstructed_baseline_approval_decision",
                approval.get("decision") == RECONSTRUCTED_APPROVAL_DECISION,
                approval.get("decision"),
            ),
            _check(
                "reconstructed_baseline_approval_identity",
                bool(approved_by) and approved_by.upper() not in {"TBD", "UNKNOWN", "PLACEHOLDER"},
                approved_by or "missing",
            ),
            _check(
                "reconstructed_baseline_approval_utc",
                _is_timezone_aware_iso8601(approved_utc),
                approved_utc or "missing",
            ),
            _check(
                "reconstructed_baseline_approval_source",
                approval.get("approval_source") == "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION",
                approval.get("approval_source"),
            ),
            _check(
                "reconstructed_baseline_approval_reference",
                bool(approval_reference)
                and approval_reference.upper() not in {"TBD", "UNKNOWN", "PLACEHOLDER"},
                approval_reference or "missing",
            ),
            _check(
                "reconstructed_baseline_approval_campaign_id",
                approved_contract.get("campaign_id") == previous_contract.get("campaign_id"),
                approved_contract.get("campaign_id"),
            ),
            _check(
                "reconstructed_baseline_approval_contract_sha256",
                str(approved_contract.get("sha256") or "").strip().lower()
                == _sha256(previous_contract_path),
                approved_contract.get("sha256"),
            ),
            _check(
                "reconstructed_baseline_preparation_only_authorized",
                approval.get("preparation_preflight_authorized") is True,
                approval.get("preparation_preflight_authorized"),
            ),
            _check(
                "reconstructed_baseline_automatic_execution_forbidden",
                approval.get("automatic_command_authorized") is False,
                approval.get("automatic_command_authorized"),
            ),
            _check(
                "reconstructed_baseline_golden_forbidden",
                approval.get("golden_authorized") is False,
                approval.get("golden_authorized"),
            ),
            _check(
                "reconstructed_baseline_simulator_forbidden",
                approval.get("simulator_authorized") is False,
                approval.get("simulator_authorized"),
            ),
            _check(
                "reconstructed_baseline_contract_automatic_execution_forbidden",
                previous_contract.get("automatic_command_authorized") is False,
                previous_contract.get("automatic_command_authorized"),
            ),
            _check(
                "reconstructed_baseline_contract_production_use_forbidden",
                previous_contract.get("production_use_authorized") is False,
                previous_contract.get("production_use_authorized"),
            ),
        ]
    )


def _is_timezone_aware_iso8601(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _check_file_sha(checks: list[dict[str, Any]], name: str, path: Path, expected: str) -> None:
    expected_normalized = expected.strip().lower()
    actual = _sha256(path) if path.is_file() else None
    checks.append(_check(name, actual == expected_normalized, f"expected={expected_normalized}, actual={actual}"))


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": str(name), "pass": bool(passed), "detail": str(detail)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _write_sha256s(out_dir: Path) -> None:
    index = out_dir / "SHA256SUMS.txt"
    lines = []
    for path in sorted(item for item in out_dir.iterdir() if item.is_file() and item != index):
        lines.append(f"{_sha256(path)}  {path.name}")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
