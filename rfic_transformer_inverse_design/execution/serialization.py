"""JSON serialization helpers for transformer execution artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

def _json_default(value: Any) -> Any:
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
