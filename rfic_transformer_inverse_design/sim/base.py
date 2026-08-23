"""Minimal simulation data types used by rfic_transformer_inverse_design."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from ..network_analysis import s_to_z


class SolverType(Enum):
    FREQUENCY_DOMAIN = "frequency"
    TIME_DOMAIN = "time"
    EIGENMODE = "eigenmode"
    INTEGRAL = "integral"


@dataclass
class SParameterResult:
    freqs_hz: np.ndarray
    s_matrix: np.ndarray
    reference_impedance_ohm: float | np.ndarray = 50.0

    def __post_init__(self) -> None:
        self.freqs_hz = np.asarray(self.freqs_hz, dtype=float)
        self.s_matrix = np.asarray(self.s_matrix, dtype=np.complex128)
        if self.s_matrix.ndim == 1:
            self.s_matrix = self.s_matrix.reshape(-1, 1, 1)
        elif self.s_matrix.ndim == 2:
            if len(self.freqs_hz) == 1 and self.s_matrix.shape[0] == self.s_matrix.shape[1]:
                self.s_matrix = self.s_matrix.reshape(1, self.s_matrix.shape[0], self.s_matrix.shape[1])
            elif len(self.freqs_hz) == self.s_matrix.shape[0] and self.s_matrix.shape[1] == 1:
                self.s_matrix = self.s_matrix.reshape(len(self.freqs_hz), 1, 1)
            else:
                raise ValueError(
                    "2D S-matrix input must be either a single square multi-port matrix "
                    "with one frequency point or an (N,1) one-port frequency series; "
                    f"got shape {self.s_matrix.shape} for {len(self.freqs_hz)} frequency points"
                )
        elif self.s_matrix.ndim == 3:
            if self.s_matrix.shape[1] != self.s_matrix.shape[2]:
                raise ValueError(f"S-matrix port dimensions must be square, got shape {self.s_matrix.shape}")
        else:
            raise ValueError(f"S-matrix must be 1D, 2D, or 3D, got shape {self.s_matrix.shape}")
        if len(self.freqs_hz) != self.s_matrix.shape[0]:
            raise ValueError(
                f"Frequency array length ({len(self.freqs_hz)}) must match "
                f"S-matrix first dimension ({self.s_matrix.shape[0]})"
            )
        ref = np.asarray(self.reference_impedance_ohm, dtype=float)
        if ref.ndim == 0:
            self.reference_impedance_ohm = float(ref)
        elif ref.ndim == 1 and ref.shape[0] == self.num_ports:
            self.reference_impedance_ohm = ref
        else:
            raise ValueError(
                "reference_impedance_ohm must be a scalar or one value per port; "
                f"got shape {ref.shape} for {self.num_ports} ports"
            )

    @property
    def num_ports(self) -> int:
        return int(self.s_matrix.shape[1])

    @property
    def num_freqs(self) -> int:
        return int(len(self.freqs_hz))

    def s11(self) -> np.ndarray:
        return self.s_matrix[:, 0, 0]

    def s11_db(self) -> np.ndarray:
        return 20.0 * np.log10(np.abs(self.s11()) + 1e-12)

    def s21(self) -> np.ndarray:
        if self.num_ports < 2:
            raise ValueError("S21 requires at least 2 ports")
        return self.s_matrix[:, 1, 0]

    def s21_db(self) -> np.ndarray:
        return 20.0 * np.log10(np.abs(self.s21()) + 1e-12)

    def to_z_parameters(self, z0: float | np.ndarray | None = None) -> np.ndarray:
        return s_to_z(self.s_matrix, z0=self.reference_impedance_ohm if z0 is None else z0)

    def to_touchstone(self, path: Path, format: str = "RI") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        n_ports = self.num_ports
        if path.suffix.lower() != f".s{n_ports}p":
            path = path.with_suffix(f".s{n_ports}p")
        freqs_ghz = self.freqs_hz / 1e9
        ref = np.asarray(self.reference_impedance_ohm, dtype=float)
        if ref.ndim == 0:
            ref_text = f"{float(ref):g}"
        else:
            ref_text = " ".join(f"{float(value):g}" for value in ref)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(f"! {n_ports}-port S-parameters\n")
            handle.write(f"# GHz S {format} R {ref_text}\n")
            for idx, freq in enumerate(freqs_ghz):
                values = [f"{freq}"]
                for row, col in _touchstone_output_order(n_ports):
                    s = complex(self.s_matrix[idx, row, col])
                    if format.upper() == "RI":
                        values.extend([f"{s.real}", f"{s.imag}"])
                    elif format.upper() == "DB":
                        values.extend([f"{20.0 * np.log10(np.abs(s) + 1e-12)}", f"{np.angle(s, deg=True)}"])
                    else:
                        raise ValueError(f"Unsupported format: {format}")
                handle.write(" ".join(values) + "\n")


def _touchstone_output_order(n_ports: int) -> list[tuple[int, int]]:
    if n_ports == 2:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    return [(row, col) for row in range(n_ports) for col in range(n_ports)]
