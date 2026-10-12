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
        self.runtime_checkpoint_attestation: dict[str, Any] | None = None
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
        return cls._load_contract(root, contract, contract_file)

    @classmethod
    def _load_contract(
        cls, root: Path, contract: dict[str, Any], contract_file: Path
    ) -> "FrozenTandemMLP":
        """Load a public contract or an explicitly adapted frozen bundle."""
        artifacts = contract.get("artifacts") or {}
        summary_path = _verified_file(root, artifacts.get("summary") or {})
        weights_path = _verified_file(root, artifacts.get("weights") or {})
        summary = _read_json(summary_path)
        arrays = _load_npz(weights_path)
        model = cls(
            contract=contract,
            summary=summary,
            arrays=arrays,
            model_dir=root,
        )
        model.contract_path = contract_file
        _freeze_loaded_arrays(model.arrays)
        model.runtime_checkpoint_attestation = {
            "schema": "rfic_frozen_tandem_mlp_runtime_attestation.v1",
            "status": "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED",
            "contract_sha256": _sha256(contract_file),
            "summary_sha256": _sha256(summary_path),
            "weights_sha256": _sha256(weights_path),
            "inverse_mlp": list(_layer_widths(model.arrays["inverse_weights"])),
            "forward_surrogate": list(
                _layer_widths(model.arrays["forward_weights"])
            ),
            "arrays_write_protected": True,
            "geometry_projection": model.arrays["projection_mode"],
            "hidden_activation": "gelu",
            "q_target_semantics": contract.get("q_target_semantics", "exact"),
        }
        return model

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
        if (
            not np.isfinite(self.support_lower).all()
            or not np.isfinite(self.support_upper).all()
            or np.any(self.support_upper <= self.support_lower)
        ):
            raise ValueError("declared support is invalid")
        expected = self.contract.get("architecture") or {}
        if expected.get("hidden_activation", "gelu") != "gelu":
            raise ValueError("unsupported hidden activation; this runtime implements GELU")
        declared_projection = expected.get("geometry_projection", "independent_sigmoid")
        if declared_projection == "sigmoid_to_training_envelope":
            declared_projection = "independent_sigmoid"
        if declared_projection not in {"independent_sigmoid", "hard_feasible_topology_v1"}:
            raise ValueError("unsupported geometry projection in model contract")
        if self.arrays.get("projection_mode", "independent_sigmoid") != declared_projection:
            raise ValueError("geometry projection does not match the model contract")
        if declared_projection == "hard_feasible_topology_v1":
            _validate_topology_contract(self.arrays.get("topology_contract") or {})
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
        if np.any(self.arrays["x_scale"] <= 0) or np.any(self.arrays["y_scale"] <= 0):
            raise ValueError("normalization scales must be strictly positive")
        if np.any(self.arrays["geometry_upper"] <= self.arrays["geometry_lower"]):
            raise ValueError("invalid geometry envelope")

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
        normalized_geometry = _project_geometry(raw_geometry, self.arrays)
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
        if not np.isfinite(geometry).all() or not np.isfinite(proxy_features).all():
            raise FloatingPointError("non-finite decoded geometry or proxy feature")
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
        result["projection_mode"] = _archive_string(
            archive, "inverse_geometry_projection__mode", "independent_sigmoid"
        )
        topology = _archive_string(
            archive, "inverse_geometry_projection__topology_contract_json", "{}"
        )
        result["topology_contract"] = json.loads(topology)
    for label in ("forward", "inverse"):
        _validate_layers(result[f"{label}_weights"], result[f"{label}_biases"], label)
    return result


def _archive_string(archive: Any, name: str, default: str) -> str:
    if name not in archive:
        return default
    value = np.asarray(archive[name]).reshape(-1)
    if value.size != 1 or value.dtype.kind not in {"U", "S"}:
        raise ValueError(f"invalid scalar model metadata: {name}")
    item = value[0]
    return item.decode("utf-8") if isinstance(item, bytes) else str(item)


def _validate_topology_contract(contract: dict[str, Any]) -> None:
    """Validate the coupled decoder metadata before accepting the checkpoint."""
    if not isinstance(contract, dict) or not contract.get("available"):
        raise ValueError("hard-feasible projection requires a topology contract")
    expected_index = {
        name.removeprefix("geom__"): index
        for index, name in enumerate(GEOMETRY_COLUMNS)
    }
    if contract.get("index_by_semantic") != expected_index:
        raise ValueError("hard-feasible topology geometry order does not match")
    if tuple(contract.get("geometry_columns") or ()) != GEOMETRY_COLUMNS:
        raise ValueError("hard-feasible topology geometry columns do not match")
    power = contract.get("power_line_port_ground_overlap") or {}
    if not power.get("enabled"):
        raise ValueError("power-line ground-overlap contract is not enabled")
    for name in ("bar_offset_um", "shield_opening_clearance_um", "training_safety_margin_um"):
        value = float(power.get(name, 0.0) if name == "training_safety_margin_um" else power[name])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid hard-feasible topology value: {name}")


def _project_geometry(raw: np.ndarray, arrays: dict[str, Any]) -> np.ndarray:
    """Execute the checkpoint decoder; the input Q is neither shifted nor clipped."""
    lower = arrays["geometry_lower"]
    upper = arrays["geometry_upper"]
    sigmoid = 1.0 / (1.0 + np.exp(-np.clip(raw, -40.0, 40.0)))
    if arrays.get("projection_mode", "independent_sigmoid") == "independent_sigmoid":
        return lower + (upper - lower) * sigmoid
    if arrays["projection_mode"] != "hard_feasible_topology_v1":
        raise ValueError("unsupported geometry projection")

    # Equivalent to the frozen deployed-100k implementation. Terminal spans and
    # feed lengths depend on the other decoded dimensions, not independent bounds.
    contract = arrays["topology_contract"]
    index = contract["index_by_semantic"]
    power = contract["power_line_port_ground_overlap"]
    y_mean = arrays["y_mean"]
    y_scale = arrays["y_scale"]
    physical_lower = lower * y_scale + y_mean
    physical_upper = upper * y_scale + y_mean
    physical = physical_lower[None, :] + (
        physical_upper - physical_lower
    )[None, :] * sigmoid
    row_count = physical.shape[0]
    chamfer_factor = math.sqrt(2.0) - 1.0
    for role in ("primary", "secondary"):
        width = physical[:, index[f"{role}_outer_width_um"]]
        height = physical[:, index[f"{role}_outer_height_um"]]
        terminal_index = index[f"{role}_terminal_y_span_um"]
        straight_cap = height - chamfer_factor * np.minimum(width, height)
        cap = np.min(
            np.column_stack((
                np.full(row_count, physical_upper[terminal_index]),
                width, height, straight_cap,
            )), axis=1,
        )
        terminal_lower = float(physical_lower[terminal_index])
        if np.any(cap < terminal_lower - 1.0e-9):
            raise RuntimeError(f"projection envelope cannot satisfy {role} terminal constraints")
        cap = np.maximum(cap, terminal_lower)
        physical[:, terminal_index] = terminal_lower + sigmoid[:, terminal_index] * (
            cap - terminal_lower
        )

    primary_width = physical[:, index["primary_outer_width_um"]]
    secondary_width = physical[:, index["secondary_outer_width_um"]]
    line_width = physical[:, index["line_width_um"]]
    offset = physical[:, index["offset_um"]]
    primary_feed_index = index["primary_feed_extension_um"]
    secondary_feed_index = index["secondary_feed_extension_um"]
    fixed_margin = (
        float(power["bar_offset_um"])
        + float(power["shield_opening_clearance_um"])
        + float(power.get("training_safety_margin_um") or 0.0)
    )
    primary_own_left = -0.5 * primary_width
    secondary_left = offset - 0.5 * secondary_width
    primary_required = np.max(np.column_stack((
        np.full(row_count, physical_lower[primary_feed_index]),
        -offset,
        primary_own_left - np.minimum(primary_own_left, secondary_left) + fixed_margin + line_width,
    )), axis=1)
    secondary_own_right = offset + 0.5 * secondary_width
    primary_right = 0.5 * primary_width
    secondary_required = np.max(np.column_stack((
        np.full(row_count, physical_lower[secondary_feed_index]),
        offset,
        np.maximum(primary_right, secondary_own_right) - secondary_own_right + fixed_margin + line_width,
    )), axis=1)
    for feed_index, required, role in (
        (primary_feed_index, primary_required, "primary"),
        (secondary_feed_index, secondary_required, "secondary"),
    ):
        feed_upper = float(physical_upper[feed_index])
        if np.any(required > feed_upper + 1.0e-9):
            raise RuntimeError(f"projection envelope cannot satisfy {role} feed constraints")
        required = np.minimum(required, feed_upper)
        physical[:, feed_index] = required + sigmoid[:, feed_index] * (
            feed_upper - required
        )
    return (physical - y_mean[None, :]) / y_scale[None, :]


def _freeze_loaded_arrays(arrays: dict[str, Any]) -> None:
    """Make the checkpoint arrays read-only after hash-bound loading."""

    for value in arrays.values():
        if isinstance(value, list):
            for array in value:
                np.asarray(array).setflags(write=False)
        elif isinstance(value, np.ndarray):
            value.setflags(write=False)


def _predict(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
) -> np.ndarray:
    output = np.asarray(values, dtype=float)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        for index, (weight, bias) in enumerate(zip(weights, biases)):
            output = output @ weight + bias
            if index != len(weights) - 1:
                constant = math.sqrt(2.0 / math.pi)
                output = 0.5 * output * (
                    1.0
                    + np.tanh(constant * (output + 0.044715 * output**3))
                )
            if not np.isfinite(output).all():
                raise FloatingPointError(
                    f"non-finite value produced by frozen MLP layer {index}"
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
