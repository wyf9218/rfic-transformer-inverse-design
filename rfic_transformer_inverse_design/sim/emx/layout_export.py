"""EMX layout manifest types used by rfic_transformer_inverse_design."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EMXPort:
    name: str
    signal_labels: tuple[str, ...]
    ground_labels: tuple[str, ...]
    internal_size_um: tuple[float, float]
    signal_internal_size_um: tuple[float, float] | None = None
    ground_internal_size_um: tuple[float, float] | None = None
    internal_signal_labels: bool = True
    internal_ground_labels: bool = True


@dataclass(frozen=True)
class EMXLayoutManifest:
    layout_path: str
    top_cell: str
    ports: tuple[EMXPort, ...]
    metal_layer: int
    metal_datatype: int
    ground_layer: int | None
    ground_datatype: int | None
    label_layer: int
    label_datatype: int
    cadence_pin_purpose: int | None = None
    layer_draw_order: tuple[int, ...] | None = None
    process_layer_summary: dict[str, Any] | None = None

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="ascii")
