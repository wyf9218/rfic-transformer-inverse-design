"""Network-parameter helpers used by rfic_transformer_inverse_design."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def _prepare_z0(z0: np.ndarray | float, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z0_arr = np.asarray(z0, dtype=np.float64)
    if z0_arr.ndim == 0:
        z0_vec = np.full(n, float(z0_arr), dtype=np.float64)
    elif z0_arr.ndim == 1 and z0_arr.shape[0] == n:
        z0_vec = z0_arr.astype(np.float64, copy=False)
    else:
        raise ValueError(f"z0 must be scalar or length-{n} vector, got shape {z0_arr.shape}")
    if np.any(z0_vec <= 0):
        raise ValueError("All z0 values must be positive")
    z0_diag = np.diag(z0_vec.astype(np.complex128))
    sqrt_z0 = np.diag(np.sqrt(z0_vec).astype(np.complex128))
    sqrt_z0_inv = np.diag((1.0 / np.sqrt(z0_vec)).astype(np.complex128))
    return z0_diag, sqrt_z0, sqrt_z0_inv


def s_to_z(s_matrix: np.ndarray, z0: float = 50.0) -> np.ndarray:
    s_matrix = np.asarray(s_matrix, dtype=np.complex128)
    if s_matrix.ndim == 2:
        n_ports = s_matrix.shape[0]
        identity = np.eye(n_ports, dtype=np.complex128)
        _, sqrt_z0, _ = _prepare_z0(z0, n_ports)
        return sqrt_z0 @ ((identity + s_matrix) @ np.linalg.inv(identity - s_matrix)) @ sqrt_z0
    if s_matrix.ndim == 3:
        num_freqs, n_ports, _ = s_matrix.shape
        identity = np.eye(n_ports, dtype=np.complex128)
        _, sqrt_z0, _ = _prepare_z0(z0, n_ports)
        result = np.zeros_like(s_matrix, dtype=np.complex128)
        for idx in range(num_freqs):
            result[idx] = sqrt_z0 @ ((identity + s_matrix[idx]) @ np.linalg.inv(identity - s_matrix[idx])) @ sqrt_z0
        return result
    raise ValueError(f"S-matrix must be 2D or 3D, got shape {s_matrix.shape}")


def z_to_s(z_matrix: np.ndarray, z0: float = 50.0) -> np.ndarray:
    z_matrix = np.asarray(z_matrix, dtype=np.complex128)
    if z_matrix.ndim == 2:
        n_ports = z_matrix.shape[0]
        z0_diag, sqrt_z0, sqrt_z0_inv = _prepare_z0(z0, n_ports)
        return sqrt_z0_inv @ ((z_matrix - z0_diag) @ np.linalg.inv(z_matrix + z0_diag)) @ sqrt_z0
    if z_matrix.ndim == 3:
        num_freqs, n_ports, _ = z_matrix.shape
        z0_diag, sqrt_z0, sqrt_z0_inv = _prepare_z0(z0, n_ports)
        result = np.zeros_like(z_matrix, dtype=np.complex128)
        for idx in range(num_freqs):
            result[idx] = sqrt_z0_inv @ ((z_matrix[idx] - z0_diag) @ np.linalg.inv(z_matrix[idx] + z0_diag)) @ sqrt_z0
        return result
    raise ValueError(f"Z-matrix must be 2D or 3D, got shape {z_matrix.shape}")


def reduce_s_params_by_shorting(
    s_matrix: np.ndarray,
    ports_to_short: list[int],
    ports_to_keep: list[int],
    gamma_load: complex = -1.0,
) -> np.ndarray:
    s_matrix = np.asarray(s_matrix, dtype=np.complex128)
    single_freq = s_matrix.ndim == 2
    if single_freq:
        s_matrix = s_matrix[np.newaxis, :, :]
    if s_matrix.ndim != 3:
        raise ValueError(f"S-matrix must be 2D or 3D, got shape {s_matrix.shape}")

    num_freqs, n_total, n_total_check = s_matrix.shape
    if n_total != n_total_check:
        raise ValueError(f"S-matrix must be square, got shape {s_matrix.shape}")
    if set(ports_to_short) & set(ports_to_keep):
        raise ValueError("ports_to_short and ports_to_keep must not overlap")
    if set(ports_to_short) | set(ports_to_keep) != set(range(n_total)):
        raise ValueError("ports_to_short and ports_to_keep must partition all ports")

    reduced = np.zeros((num_freqs, len(ports_to_keep), len(ports_to_keep)), dtype=np.complex128)
    gamma = complex(gamma_load)
    eye_short = np.eye(len(ports_to_short), dtype=np.complex128)

    for idx in range(num_freqs):
        s_f = s_matrix[idx]
        s_aa = s_f[np.ix_(ports_to_keep, ports_to_keep)]
        if not ports_to_short:
            reduced[idx] = s_aa
            continue
        s_ab = s_f[np.ix_(ports_to_keep, ports_to_short)]
        s_ba = s_f[np.ix_(ports_to_short, ports_to_keep)]
        s_bb = s_f[np.ix_(ports_to_short, ports_to_short)]
        reduced[idx] = s_aa + s_ab @ np.linalg.inv(eye_short - gamma * s_bb) @ (gamma * s_ba)

    return reduced[0] if single_freq else reduced
