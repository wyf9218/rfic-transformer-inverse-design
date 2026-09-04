from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_raw_products_identity import (
    CORRECTED_MODE,
    DIRECT_MODE,
    RawProductsIdentityError,
    resolve_effective_production_config,
)


FINGERPRINT = "a" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_raw_receipt(
    raw_dir: Path,
    *,
    config: Path,
    authorization: dict[str, object],
) -> Path:
    raw_dir.mkdir()
    receipt = raw_dir / "RAW_PRODUCTS_RECEIPT.json"
    _write_json(
        receipt,
        {
            "schema": "broadband56_raw_products_receipt_v1",
            "overall_status": "PASS",
            "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": FINGERPRINT,
            "counts": {
                "accepted_geometries": 1,
                "geometry_frequency_rows": 56,
            },
            "checks": {"identity_chain_verified": True},
            "inputs": {
                "production_config": _identity(config),
                "production_config_authorization": authorization,
            },
        },
    )
    (raw_dir / "SHA256SUMS.txt").write_text(
        f"{_sha256(receipt)}  {receipt.name}\n",
        encoding="utf-8",
    )
    return receipt


def test_resolves_frozen_contract_direct_config(tmp_path: Path) -> None:
    config = tmp_path / "production.yaml"
    config.write_text("frequency_points: 56\n", encoding="utf-8")
    digest = _sha256(config)
    raw_dir = tmp_path / "raw"
    _write_raw_receipt(
        raw_dir,
        config=config,
        authorization={
            "mode": DIRECT_MODE,
            "frozen_config_sha256": digest,
            "effective_config_sha256": digest,
            "full_campaign_receipt": None,
            "corrected_foundry_layout_approval_receipt": None,
        },
    )

    result = resolve_effective_production_config(
        raw_dir=raw_dir,
        campaign_id=CAMPAIGN_ID,
        contract_fingerprint_sha256=FINGERPRINT,
        frozen_config_sha256=digest,
        expected_accepted=1,
        expected_feature_rows=56,
    )

    assert result.mode == DIRECT_MODE
    assert result.sha256 == digest
    assert result.config_path == config.resolve()


def _corrected_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    previous = tmp_path / "previous.yaml"
    corrected = tmp_path / "corrected.yaml"
    previous.write_text("frequency_points: 56\n", encoding="utf-8")
    corrected.write_text(
        "frequency_points: 56\nfoundry_layout:\n  enabled: true\n",
        encoding="utf-8",
    )
    corrected_receipt = tmp_path / "CORRECTED_FOUNDRY_LAYOUT_AUTHORIZATION_RECEIPT.json"
    _write_json(
        corrected_receipt,
        {
            "schema": "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1",
            "overall_status": "PASS",
            "authorization_scope": (
                "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_"
                "THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
            ),
            "restore_corrected_foundry_layout_contract_authorized": True,
            "verified_bound_files": {
                "previous_private_configuration": _identity(previous),
                "corrected_private_configuration": _identity(corrected),
            },
        },
    )
    full_receipt = tmp_path / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    _write_json(
        full_receipt,
        {
            "overall_status": "PASS",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": FINGERPRINT,
            "campaign_200k_authorized": True,
            "authorization_composition": {
                "corrected_foundry_layout_approval_receipt": _identity(
                    corrected_receipt
                )
            },
        },
    )
    raw_dir = tmp_path / "raw"
    _write_raw_receipt(
        raw_dir,
        config=corrected,
        authorization={
            "mode": CORRECTED_MODE,
            "frozen_config_sha256": _sha256(previous),
            "effective_config_sha256": _sha256(corrected),
            "full_campaign_receipt": _identity(full_receipt),
            "corrected_foundry_layout_approval_receipt": _identity(
                corrected_receipt
            ),
        },
    )
    return raw_dir, previous, corrected


def test_resolves_approved_corrected_foundry_layout_config(tmp_path: Path) -> None:
    raw_dir, previous, corrected = _corrected_fixture(tmp_path)

    result = resolve_effective_production_config(
        raw_dir=raw_dir,
        campaign_id=CAMPAIGN_ID,
        contract_fingerprint_sha256=FINGERPRINT,
        frozen_config_sha256=_sha256(previous),
        expected_accepted=1,
        expected_feature_rows=56,
    )

    assert result.mode == CORRECTED_MODE
    assert result.sha256 == _sha256(corrected)


def test_corrected_config_tamper_fails_closed(tmp_path: Path) -> None:
    raw_dir, previous, corrected = _corrected_fixture(tmp_path)
    corrected.write_text(corrected.read_text(encoding="utf-8") + "tamper: true\n")

    with pytest.raises(RawProductsIdentityError, match="identity does not match bytes"):
        resolve_effective_production_config(
            raw_dir=raw_dir,
            campaign_id=CAMPAIGN_ID,
            contract_fingerprint_sha256=FINGERPRINT,
            frozen_config_sha256=_sha256(previous),
        )
