"""Verify the effective production-config identity in raw-product receipts.

The frozen broadband56 contract records the original 56-point configuration.
A separately approved foundry-layout correction may replace those exact bytes
without changing the scientific contract.  Downstream consumers must therefore
resolve the effective configuration through the hash-bound raw-products receipt
instead of assuming that the original configuration SHA is still active.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RAW_RECEIPT_SCHEMA = "broadband56_raw_products_receipt_v1"
RAW_RECEIPT_DECISION = "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS"
DIRECT_MODE = "FROZEN_CONTRACT_DIRECT"
CORRECTED_MODE = "APPROVED_CORRECTED_FOUNDRY_LAYOUT_REPLACEMENT"
CORRECTED_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1"
)
CORRECTED_SCOPE = (
    "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_THEN_"
    "AUTO_CONTINUE_FULL_CAMPAIGN"
)


class RawProductsIdentityError(ValueError):
    """Raised when raw products do not bind one authorized config identity."""


@dataclass(frozen=True)
class EffectiveProductionConfig:
    """Hash-closed production-config evidence resolved from raw products."""

    sha256: str
    mode: str
    receipt_path: Path
    receipt_sha256: str
    config_path: Path
    config_size_bytes: int


def resolve_effective_production_config(
    *,
    raw_dir: Path,
    campaign_id: str,
    contract_fingerprint_sha256: str,
    frozen_config_sha256: str,
    expected_accepted: int | None = None,
    expected_feature_rows: int | None = None,
) -> EffectiveProductionConfig:
    """Return the only config identity authorized by one raw-products receipt."""

    directory = Path(raw_dir).expanduser().resolve()
    if not directory.is_dir():
        raise RawProductsIdentityError(
            f"raw-products directory does not exist: {directory}"
        )
    receipt_path = directory / "RAW_PRODUCTS_RECEIPT.json"
    receipt = _read_json(receipt_path, "raw-products receipt")
    _verify_sha_index_entry(directory, receipt_path)
    if not (
        receipt.get("schema") == RAW_RECEIPT_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == RAW_RECEIPT_DECISION
        and receipt.get("campaign_id") == campaign_id
        and receipt.get("contract_fingerprint_sha256")
        == contract_fingerprint_sha256
    ):
        raise RawProductsIdentityError(
            "raw-products receipt is not exact PASS evidence for this campaign"
        )
    checks = receipt.get("checks")
    if not isinstance(checks, Mapping) or not checks or any(
        value is not True for value in checks.values()
    ):
        raise RawProductsIdentityError("raw-products receipt contains failed checks")
    counts = receipt.get("counts")
    if not isinstance(counts, Mapping):
        raise RawProductsIdentityError("raw-products receipt lacks counts")
    if expected_accepted is not None and _as_int(
        counts.get("accepted_geometries"), "accepted_geometries"
    ) != int(expected_accepted):
        raise RawProductsIdentityError("raw-products accepted count mismatch")
    if expected_feature_rows is not None and _as_int(
        counts.get("geometry_frequency_rows"), "geometry_frequency_rows"
    ) != int(expected_feature_rows):
        raise RawProductsIdentityError("raw-products feature-row count mismatch")

    inputs = receipt.get("inputs")
    if not isinstance(inputs, Mapping):
        raise RawProductsIdentityError("raw-products receipt lacks inputs")
    config_path = _verified_identity_path(
        inputs.get("production_config"), label="effective production config"
    )
    config_sha = _sha256(config_path)
    authorization = inputs.get("production_config_authorization")
    if not isinstance(authorization, Mapping):
        raise RawProductsIdentityError(
            "raw-products receipt lacks production-config authorization"
        )
    mode = str(authorization.get("mode") or "")
    if mode == DIRECT_MODE:
        _validate_direct_authorization(
            authorization,
            frozen_config_sha256=frozen_config_sha256,
            config_sha256=config_sha,
        )
    elif mode == CORRECTED_MODE:
        _validate_corrected_authorization(
            authorization,
            campaign_id=campaign_id,
            contract_fingerprint_sha256=contract_fingerprint_sha256,
            frozen_config_sha256=frozen_config_sha256,
            config_path=config_path,
            config_sha256=config_sha,
        )
    else:
        raise RawProductsIdentityError(
            f"unknown production-config authorization mode: {mode or '<empty>'}"
        )
    return EffectiveProductionConfig(
        sha256=config_sha,
        mode=mode,
        receipt_path=receipt_path,
        receipt_sha256=_sha256(receipt_path),
        config_path=config_path,
        config_size_bytes=config_path.stat().st_size,
    )


def _validate_direct_authorization(
    authorization: Mapping[str, Any],
    *,
    frozen_config_sha256: str,
    config_sha256: str,
) -> None:
    if not (
        config_sha256 == frozen_config_sha256
        and authorization.get("frozen_config_sha256") == frozen_config_sha256
        and authorization.get("effective_config_sha256") == config_sha256
        and authorization.get("full_campaign_receipt") is None
        and authorization.get("corrected_foundry_layout_approval_receipt") is None
    ):
        raise RawProductsIdentityError(
            "direct production-config authorization does not bind frozen bytes"
        )


def _validate_corrected_authorization(
    authorization: Mapping[str, Any],
    *,
    campaign_id: str,
    contract_fingerprint_sha256: str,
    frozen_config_sha256: str,
    config_path: Path,
    config_sha256: str,
) -> None:
    if not (
        authorization.get("frozen_config_sha256") == frozen_config_sha256
        and authorization.get("effective_config_sha256") == config_sha256
        and config_sha256 != frozen_config_sha256
    ):
        raise RawProductsIdentityError(
            "corrected production-config authorization SHA binding is invalid"
        )
    full_path = _verified_identity_path(
        authorization.get("full_campaign_receipt"),
        label="FULL_CAMPAIGN receipt",
    )
    corrected_path = _verified_identity_path(
        authorization.get("corrected_foundry_layout_approval_receipt"),
        label="corrected foundry-layout approval receipt",
    )
    full = _read_json(full_path, "FULL_CAMPAIGN receipt")
    if not (
        full.get("overall_status") == "PASS"
        and full.get("campaign_id") == campaign_id
        and full.get("contract_fingerprint_sha256")
        == contract_fingerprint_sha256
        and full.get("campaign_200k_authorized") is True
    ):
        raise RawProductsIdentityError(
            "FULL_CAMPAIGN receipt does not authorize this campaign"
        )
    composition = full.get("authorization_composition")
    if not isinstance(composition, Mapping):
        raise RawProductsIdentityError(
            "FULL_CAMPAIGN receipt lacks authorization composition"
        )
    composed_corrected_path = _verified_identity_path(
        composition.get("corrected_foundry_layout_approval_receipt"),
        label="composed corrected foundry-layout approval receipt",
    )
    if composed_corrected_path != corrected_path:
        raise RawProductsIdentityError(
            "raw-products and FULL_CAMPAIGN receipts bind different corrections"
        )
    corrected = _read_json(corrected_path, "corrected foundry-layout approval")
    if not (
        corrected.get("schema") == CORRECTED_SCHEMA
        and corrected.get("overall_status") == "PASS"
        and corrected.get("authorization_scope") == CORRECTED_SCOPE
        and corrected.get("restore_corrected_foundry_layout_contract_authorized")
        is True
    ):
        raise RawProductsIdentityError(
            "corrected foundry-layout approval is not exact PASS authorization"
        )
    bound = corrected.get("verified_bound_files")
    if not isinstance(bound, Mapping):
        raise RawProductsIdentityError(
            "corrected foundry-layout approval lacks bound files"
        )
    previous_path = _verified_identity_path(
        bound.get("previous_private_configuration"),
        label="previous private configuration",
    )
    corrected_config_path = _verified_identity_path(
        bound.get("corrected_private_configuration"),
        label="corrected private configuration",
    )
    if _sha256(previous_path) != frozen_config_sha256:
        raise RawProductsIdentityError(
            "corrected approval previous config does not match frozen bytes"
        )
    if corrected_config_path != config_path or _sha256(corrected_config_path) != config_sha256:
        raise RawProductsIdentityError(
            "corrected approval does not bind the effective production config"
        )


def _verified_identity_path(record: Any, *, label: str) -> Path:
    if not isinstance(record, Mapping):
        raise RawProductsIdentityError(f"{label} identity is missing")
    path = Path(str(record.get("path") or "")).expanduser().resolve()
    digest = str(record.get("sha256") or "").lower()
    expected_size = _as_int(record.get("size_bytes"), f"{label} size")
    if not (
        path.is_file()
        and expected_size > 0
        and path.stat().st_size == expected_size
        and _is_sha256(digest)
        and _sha256(path) == digest
    ):
        raise RawProductsIdentityError(f"{label} identity does not match bytes")
    return path


def _verify_sha_index_entry(directory: Path, target: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    if not index.is_file():
        raise RawProductsIdentityError(f"raw-products SHA256SUMS.txt is missing: {directory}")
    expected_name = target.name
    expected_digest = _sha256(target)
    matches = 0
    for line_number, raw in enumerate(
        index.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2 or not _is_sha256(parts[0]):
            raise RawProductsIdentityError(
                f"invalid raw-products SHA index line {line_number}"
            )
        digest, relative = parts[0].lower(), parts[1]
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(directory)
        except ValueError as exc:
            raise RawProductsIdentityError(
                f"raw-products SHA index path escapes directory: {relative}"
            ) from exc
        if not candidate.is_file() or _sha256(candidate) != digest:
            raise RawProductsIdentityError(
                f"raw-products SHA index mismatch: {relative}"
            )
        if relative == expected_name and digest == expected_digest:
            matches += 1
    if matches != 1:
        raise RawProductsIdentityError(
            "raw-products receipt is not bound exactly once by SHA256SUMS.txt"
        )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RawProductsIdentityError(f"{label} is missing or empty: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RawProductsIdentityError(f"{label} cannot be parsed: {path}") from exc
    if not isinstance(payload, dict):
        raise RawProductsIdentityError(f"{label} root is not an object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _as_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RawProductsIdentityError(f"{label} is not an integer") from exc


__all__ = [
    "CORRECTED_MODE",
    "DIRECT_MODE",
    "EffectiveProductionConfig",
    "RawProductsIdentityError",
    "resolve_effective_production_config",
]
