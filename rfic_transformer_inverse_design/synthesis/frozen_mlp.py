"""Hash-bound NumPy runtime for the frozen tandem MLP checkpoint."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)


@dataclass(frozen=True)
class MLPBatchPrediction:
    """Physical geometry and frozen-forward reconstruction for one batch."""

    geometry: np.ndarray
    proxy_features: np.ndarray


class FrozenTandemMLP:
    """Execute one immutable inverse MLP and its frozen forward surrogate."""

    def __init__(
        self,
        *,
        contract: dict[str, Any],
        summary: dict[str, Any],
        arrays: dict[str, Any],
        model_dir: Path,
    ) -> None:
        self.contract = contract
        self.summary = summary
        self.arrays = arrays
        self.model_dir = model_dir
        self.model_id = str(contract["model_id"])
        self.model_seed = int(contract["model_seed"])
        self.target_frequency_ghz = float(contract["target_frequency_ghz"])
        self.support_lower = np.asarray(
            contract["declared_support_lower"], dtype=float
        )
        self.support_upper = np.asarray(
            contract["declared_support_upper"], dtype=float
        )
        self._validate_contract()

    @classmethod
    def load(
        cls,
        model_dir: str | Path,
        *,
        contract_path: str | Path | None = None,
    ) -> "FrozenTandemMLP":
        root = Path(model_dir).expanduser().resolve()
        contract_file = (
            Path(contract_path).expanduser().resolve()
            if contract_path is not None
            else Path(__file__).with_name("real10k_model_contract.json")
        )
        contract = _read_json(contract_file)
        artifacts = contract.get("artifacts") or {}
        summary_path = _verified_file(root, artifacts.get("summary") or {})
        weights_path = _verified_file(root, artifacts.get("weights") or {})
        summary = _read_json(summary_path)
        arrays = _load_npz(weights_path)
        return cls(
            contract=contract,
            summary=summary,
            arrays=arrays,
            model_dir=root,
        )

    def _validate_contract(self) -> None:
        if self.contract.get("schema") != "rfic_frozen_tandem_mlp_public_contract.v1":
            raise ValueError("unsupported frozen-model contract schema")
        if tuple(self.contract.get("input_columns") or ()) != INPUT_COLUMNS:
            raise ValueError("public contract input columns do not match the runtime")
        if tuple(self.contract.get("geometry_columns") or ()) != GEOMETRY_COLUMNS:
            raise ValueError("public contract geometry columns do not match the runtime")
        if tuple(self.summary.get("input_columns") or ()) != INPUT_COLUMNS:
            raise ValueError("private summary input columns do not match the contract")
        if tuple(self.summary.get("geometry_columns") or ()) != GEOMETRY_COLUMNS:
            raise ValueError("private summary geometry columns do not match the contract")
        if self.support_lower.shape != (4,) or self.support_upper.shape != (4,):
            raise ValueError("declared support must contain four dimensions")
        if np.any(self.support_upper <= self.support_lower):
            raise ValueError("declared support is invalid")
        expected = self.contract.get("architecture") or {}
        if _layer_widths(self.arrays["inverse_weights"]) != tuple(
            expected.get("inverse_mlp") or ()
        ):
            raise ValueError("inverse MLP architecture mismatch")
        if _layer_widths(self.arrays["forward_weights"]) != tuple(
            expected.get("forward_surrogate") or ()
        ):
            raise ValueError("forward surrogate architecture mismatch")
        for key, expected_size in (
            ("x_mean", 4),
            ("x_scale", 4),
            ("y_mean", 10),
            ("y_scale", 10),
            ("geometry_lower", 10),
            ("geometry_upper", 10),
        ):
            value = self.arrays[key]
            if value.shape != (expected_size,) or not np.isfinite(value).all():
                raise ValueError(f"invalid normalization array: {key}")

    def predict(self, physical_targets: np.ndarray) -> MLPBatchPrediction:
        """Predict 10-D geometry and reconstruct the 4-D target through the proxy."""

        targets = np.asarray(physical_targets, dtype=float)
        if targets.ndim == 1:
            targets = targets[None, :]
        if targets.ndim != 2 or targets.shape[1] != 4:
            raise ValueError("physical targets must have shape (N, 4)")
        if not np.isfinite(targets).all():
            raise ValueError("physical targets contain non-finite values")
        if np.any(targets < self.support_lower) or np.any(
            targets > self.support_upper
        ):
            raise ValueError(
                "target is outside the frozen model support: "
                f"lower={self.support_lower.tolist()}, "
                f"upper={self.support_upper.tolist()}"
            )
        standardized = (targets - self.arrays["x_mean"]) / self.arrays["x_scale"]
        raw_geometry = _predict(
            standardized,
            self.arrays["inverse_weights"],
            self.arrays["inverse_biases"],
        )
        clipped = np.clip(raw_geometry, -40.0, 40.0)
        sigmoid = 1.0 / (1.0 + np.exp(-clipped))
        lower = self.arrays["geometry_lower"]
        upper = self.arrays["geometry_upper"]
        normalized_geometry = lower + (upper - lower) * sigmoid
        geometry = (
            normalized_geometry * self.arrays["y_scale"] + self.arrays["y_mean"]
        )
        proxy_standardized = _predict(
            normalized_geometry,
            self.arrays["forward_weights"],
            self.arrays["forward_biases"],
        )
        proxy_features = (
            proxy_standardized * self.arrays["x_scale"] + self.arrays["x_mean"]
        )
        return MLPBatchPrediction(geometry=geometry, proxy_features=proxy_features)


def _load_npz(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as archive:
        result: dict[str, Any] = {
            "forward_weights": _numbered_arrays(archive, "forward_weight_"),
            "forward_biases": _numbered_arrays(archive, "forward_bias_"),
            "inverse_weights": _numbered_arrays(archive, "inverse_weight_"),
            "inverse_biases": _numbered_arrays(archive, "inverse_bias_"),
        }
        for name in (
            "x_mean",
            "x_scale",
            "y_mean",
            "y_scale",
            "geometry_lower",
            "geometry_upper",
        ):
            result[name] = np.asarray(
                archive[f"normalization__{name}"], dtype=float
            )
    for label in ("forward", "inverse"):
        _validate_layers(result[f"{label}_weights"], result[f"{label}_biases"], label)
    return result


def _predict(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
) -> np.ndarray:
    output = np.asarray(values, dtype=float)
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        output = output @ weight + bias
        if index != len(weights) - 1:
            constant = math.sqrt(2.0 / math.pi)
            output = 0.5 * output * (
                1.0
                + np.tanh(constant * (output + 0.044715 * output**3))
            )
    return output


def _numbered_arrays(archive: Any, prefix: str) -> list[np.ndarray]:
    arrays: list[np.ndarray] = []
    index = 0
    while f"{prefix}{index}" in archive:
        arrays.append(np.asarray(archive[f"{prefix}{index}"], dtype=float))
        index += 1
    return arrays


def _validate_layers(
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    label: str,
) -> None:
    if not weights or len(weights) != len(biases):
        raise ValueError(f"{label} model arrays are incomplete")
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        if weight.ndim != 2 or bias.shape != (weight.shape[1],):
            raise ValueError(f"invalid {label} layer {index}")
        if index and weights[index - 1].shape[1] != weight.shape[0]:
            raise ValueError(f"{label} layer {index} width mismatch")
        if not np.isfinite(weight).all() or not np.isfinite(bias).all():
            raise ValueError(f"non-finite value in {label} model")


def _layer_widths(weights: list[np.ndarray]) -> tuple[int, ...]:
    if not weights:
        return ()
    return (int(weights[0].shape[0]),) + tuple(
        int(weight.shape[1]) for weight in weights
    )


def _verified_file(root: Path, record: dict[str, Any]) -> Path:
    path = (root / str(record.get("filename") or "")).resolve()
    if not path.is_relative_to(root):
        raise ValueError("model artifact escaped the model directory")
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = str(record.get("sha256") or "").lower()
    actual = _sha256(path)
    if len(expected) != 64 or actual != expected:
        raise ValueError(
            f"model artifact hash mismatch: expected={expected}, actual={actual}"
        )
    return path


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
