#!/usr/bin/env python3
"""Freeze and preflight the broadband56 balanced-200k production contract.

The script performs no Cadence, Calibre, or EMX work.  It refuses to prepare a
campaign unless the supplied production configuration is identical to the
approved previous broadband56 configuration except for the already-approved
frequency-grid fields, and that grid is exactly 5-60 GHz in 1-GHz steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import load_run_config  # noqa: E402
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    build_phase_plan,
    contract_fingerprint,
    primary_bin_edges,
    validate_contract,
)


FREQUENCY_FIELDS = {
    "frequency_start_hz",
    "frequency_stop_hz",
    "frequency_step_hz",
    "band_points",
}


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

    for error in validate_contract(override):
        checks.append(_check(f"override_contract::{error}", False, error))

    previous_cfg = _load_config(previous_config_path, checks, "previous_config")
    production_cfg = _load_config(production_config_path, checks, "production_config")
    if previous_cfg is not None and production_cfg is not None:
        _validate_inherited_configuration(checks, previous_cfg, production_cfg)
        _validate_private_runtime_paths(checks, production_cfg)

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


def _validate_inherited_configuration(checks: list[dict[str, Any]], previous: Any, production: Any) -> None:
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
