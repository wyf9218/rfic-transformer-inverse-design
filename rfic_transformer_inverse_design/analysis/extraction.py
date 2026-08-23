"""Differential network extraction helpers for transformer evaluation."""

from __future__ import annotations

import math

import numpy as np

from ..network_analysis import reduce_s_params_by_shorting, s_to_z, z_to_s
from ..sim.base import SParameterResult

from ..core.types import TransformerMetrics, TransformerTargetSpec


def _external_to_internal_port_order() -> tuple[int, int, int, int]:
    """Map EMX port order to the mixed-mode transformer polarity convention.

    External EMX order remains P001, P002, P003, P004.
    Internally we treat the mirrored secondary as P004, P003 so positive k
    corresponds to the intended winding orientation.
    """
    return (0, 1, 3, 2)


def _reorder_single_ended_ports(matrix: np.ndarray, order: tuple[int, ...]) -> np.ndarray:
    matrix = np.asarray(matrix)
    return matrix[:, order, :][:, :, order]


def _mixed_mode_voltage_matrix() -> np.ndarray:
    return np.array(
        [
            [1.0, -1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, -1.0],
            [0.5, 0.5, 0.0, 0.0],
            [0.0, 0.0, 0.5, 0.5],
        ],
        dtype=float,
    )


def _mixed_mode_current_matrix() -> np.ndarray:
    return np.array(
        [
            [0.5, -0.5, 0.0, 0.0],
            [0.0, 0.0, 0.5, -0.5],
            [1.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 1.0],
        ],
        dtype=float,
    )


def single_ended_to_differential_z(z_single_ended: np.ndarray) -> np.ndarray:
    """Reduce a 4-terminal single-ended Z matrix to a 2-port differential Z matrix."""
    z_single_ended = np.asarray(z_single_ended, dtype=np.complex128)
    if z_single_ended.ndim != 3 or z_single_ended.shape[1:] != (4, 4):
        raise ValueError(f"Expected Z matrix with shape (N,4,4), got {z_single_ended.shape}")

    z_single_ended = _reorder_single_ended_ports(z_single_ended, _external_to_internal_port_order())
    a_v = _mixed_mode_voltage_matrix()
    a_i_inv = np.linalg.inv(_mixed_mode_current_matrix())
    z_diff = np.empty((z_single_ended.shape[0], 2, 2), dtype=np.complex128)
    for idx, z_f in enumerate(z_single_ended):
        z_mixed = a_v @ z_f @ a_i_inv
        z_diff[idx] = z_mixed[:2, :2]
    return z_diff


def multiport_single_ended_to_differential_z(
    z_single_ended: np.ndarray,
    port_pairs: tuple[tuple[int, int], tuple[int, int]],
) -> np.ndarray:
    """Project selected single-ended terminal pairs into a 2-port differential Z matrix.

    ``port_pairs`` are zero-based physical port indices, for example
    ``((0, 1), (6, 7))`` for an 8-port result. The first terminal of each pair is
    the positive terminal. Ports outside the two selected pairs carry zero
    current in this Z-domain projection, matching an open unused terminal.
    """

    z = np.asarray(z_single_ended, dtype=np.complex128)
    if z.ndim != 3 or z.shape[1] != z.shape[2]:
        raise ValueError(f"Expected square n-port Z with shape (N,P,P), got {z.shape}")
    n_ports = int(z.shape[1])
    flat_ports = [port for pair in port_pairs for port in pair]
    if len(set(flat_ports)) != 4:
        raise ValueError(f"Port pairs must use four distinct ports, got {port_pairs}")
    if any(port < 0 or port >= n_ports for port in flat_ports):
        raise ValueError(f"Port pairs {port_pairs} exceed available port count {n_ports}")

    transform = np.zeros((n_ports, 2), dtype=np.complex128)
    transform[port_pairs[0][0], 0] = 1.0
    transform[port_pairs[0][1], 0] = -1.0
    transform[port_pairs[1][0], 1] = 1.0
    transform[port_pairs[1][1], 1] = -1.0
    return np.einsum("ai,fab,bj->fij", transform, z, transform)


def multiport_s_to_grounded_differential_z(
    s_matrix: np.ndarray,
    z0: float | np.ndarray,
    port_pairs: tuple[tuple[int, int], tuple[int, int]],
) -> np.ndarray:
    """Short unused S-parameter ports to ground, then extract differential Z."""

    s = np.asarray(s_matrix, dtype=np.complex128)
    if s.ndim != 3 or s.shape[1] != s.shape[2]:
        raise ValueError(f"Expected square n-port S with shape (N,P,P), got {s.shape}")
    n_ports = int(s.shape[1])
    flat_ports = [port for pair in port_pairs for port in pair]
    if len(set(flat_ports)) != 4:
        raise ValueError(f"Port pairs must use four distinct ports, got {port_pairs}")
    if any(port < 0 or port >= n_ports for port in flat_ports):
        raise ValueError(f"Port pairs {port_pairs} exceed available port count {n_ports}")

    ports_to_keep = list(flat_ports)
    ports_to_short = [port for port in range(n_ports) if port not in set(ports_to_keep)]
    reduced_s = reduce_s_params_by_shorting(
        s,
        ports_to_short=ports_to_short,
        ports_to_keep=ports_to_keep,
    )
    reduced_z0 = _select_reference_impedance(z0, ports_to_keep)
    reduced_z = s_to_z(reduced_s, z0=reduced_z0)
    return multiport_single_ended_to_differential_z(reduced_z, ((0, 1), (2, 3)))


def _select_reference_impedance(z0: float | np.ndarray, ports: list[int]) -> float | np.ndarray:
    z0_arr = np.asarray(z0, dtype=float)
    if z0_arr.ndim == 0:
        return float(z0_arr)
    return z0_arr[np.asarray(ports, dtype=int)]


def differential_2port_to_4port_z(
    z_diff: np.ndarray,
    common_mode_impedance_ohm: float = 1.0e6,
) -> np.ndarray:
    """Embed a 2-port differential Z matrix in a full 4x4 mixed/common-mode network."""
    z_diff = np.asarray(z_diff, dtype=np.complex128)
    if z_diff.ndim != 3 or z_diff.shape[1:] != (2, 2):
        raise ValueError(f"Expected differential Z matrix with shape (N,2,2), got {z_diff.shape}")

    a_v_inv = np.linalg.inv(_mixed_mode_voltage_matrix())
    a_i = _mixed_mode_current_matrix()
    z_single = np.empty((z_diff.shape[0], 4, 4), dtype=np.complex128)
    common = np.eye(2, dtype=np.complex128) * complex(common_mode_impedance_ohm)
    for idx, z_f in enumerate(z_diff):
        z_mixed = np.zeros((4, 4), dtype=np.complex128)
        z_mixed[:2, :2] = z_f
        z_mixed[2:, 2:] = common
        z_single[idx] = a_v_inv @ z_mixed @ a_i
    internal_to_external = tuple(np.argsort(_external_to_internal_port_order()))
    return _reorder_single_ended_ports(z_single, internal_to_external)


def differential_2port_to_4port_s(
    freqs_hz: np.ndarray,
    s_diff: np.ndarray,
    diff_z0_ohm: float = 100.0,
    single_z0_ohm: float = 50.0,
) -> SParameterResult:
    """Embed a differential 2-port S matrix into a 4-port single-ended result."""
    z_diff = s_to_z(np.asarray(s_diff, dtype=np.complex128), z0=diff_z0_ohm)
    z_single = differential_2port_to_4port_z(z_diff)
    s_single = z_to_s(z_single, z0=single_z0_ohm)
    return SParameterResult(freqs_hz=np.asarray(freqs_hz, dtype=float), s_matrix=s_single, reference_impedance_ohm=single_z0_ohm)


def build_lumped_transformer_sparameters(
    freqs_hz: np.ndarray,
    target: TransformerTargetSpec,
    q_primary: float | None = None,
    q_secondary: float | None = None,
) -> SParameterResult:
    """Build the narrow-band coupled-inductor lumped model used for the sanity fit."""
    freqs_hz = np.asarray(freqs_hz, dtype=float)
    omega = 2.0 * math.pi * freqs_hz
    q_p = float(q_primary if q_primary is not None else 1.0)
    q_s = float(q_secondary if q_secondary is not None else 1.0)
    r_p = omega * target.lp_h / max(q_p, 1e-9)
    r_s = omega * target.ls_h / max(q_s, 1e-9)
    mutual_h = target.k_target * math.sqrt(target.lp_h * target.ls_h)

    z = np.zeros((len(freqs_hz), 2, 2), dtype=np.complex128)
    z[:, 0, 0] = r_p + 1j * omega * target.lp_h
    z[:, 1, 1] = r_s + 1j * omega * target.ls_h
    z[:, 0, 1] = 1j * omega * mutual_h
    z[:, 1, 0] = 1j * omega * mutual_h
    s = z_to_s(z, z0=target.differential_reference_impedance_ohm)
    return SParameterResult(
        freqs_hz=freqs_hz,
        s_matrix=s,
        reference_impedance_ohm=target.differential_reference_impedance_ohm,
    )


def _extract_metrics_from_differential_z(
    *,
    freqs_hz: np.ndarray,
    z_diff: np.ndarray,
    target: TransformerTargetSpec,
    differential_sparams: SParameterResult,
) -> tuple[TransformerMetrics, SParameterResult, np.ndarray]:
    center_idx = int(np.argmin(np.abs(freqs_hz - target.f0_hz)))
    f0 = float(freqs_hz[center_idx])
    omega0 = 2.0 * math.pi * f0
    z0 = z_diff[center_idx]
    lp_h = float(np.imag(z0[0, 0]) / omega0)
    ls_h = float(np.imag(z0[1, 1]) / omega0)
    mutual_h = float(np.imag(z0[1, 0]) / omega0)
    denom = math.sqrt(max(lp_h * ls_h, 1e-30))
    k = float(mutual_h / denom)
    q_primary = float(np.imag(z0[0, 0]) / max(np.real(z0[0, 0]), 1e-12))
    q_secondary = float(np.imag(z0[1, 1]) / max(np.real(z0[1, 1]), 1e-12))

    metrics = TransformerMetrics(
        center_frequency_hz=f0,
        lp_h=lp_h,
        ls_h=ls_h,
        mutual_h=mutual_h,
        k=k,
        q_primary=q_primary,
        q_secondary=q_secondary,
        real_z11_ohm=float(np.real(z0[0, 0])),
        real_z22_ohm=float(np.real(z0[1, 1])),
        z_diff_center=((complex(z0[0, 0]), complex(z0[0, 1])), (complex(z0[1, 0]), complex(z0[1, 1]))),
    )
    return metrics, differential_sparams, z_diff


def extract_transformer_metrics_from_differential(
    differential_sparams: SParameterResult,
    target: TransformerTargetSpec,
) -> tuple[TransformerMetrics, SParameterResult, np.ndarray]:
    """Extract transformer metrics directly from a 2-port differential EMX result."""
    if differential_sparams.num_ports != 2:
        raise ValueError(
            "Differential transformer extraction expects a 2-port differential result, "
            f"got {differential_sparams.num_ports}"
        )

    z_diff = differential_sparams.to_z_parameters(z0=target.differential_reference_impedance_ohm)
    return _extract_metrics_from_differential_z(
        freqs_hz=differential_sparams.freqs_hz,
        z_diff=z_diff,
        target=target,
        differential_sparams=differential_sparams,
    )


def extract_transformer_metrics_from_single_ended_pairs(
    single_ended_sparams: SParameterResult,
    target: TransformerTargetSpec,
    port_pairs: tuple[tuple[int, int], tuple[int, int]],
    *,
    ground_unused_ports: bool = False,
) -> tuple[TransformerMetrics, SParameterResult, np.ndarray]:
    """Extract metrics from explicit single-ended differential port pairs.

    This is the intended extraction path for new multiport topologies such as
    the 8-port vertical power-line structure, after the physical port map has
    been confirmed and recorded.
    """

    if single_ended_sparams.num_ports < 4:
        raise ValueError(
            "Explicit pair extraction expects at least a 4-port single-ended result, "
            f"got {single_ended_sparams.num_ports}"
        )
    if ground_unused_ports:
        z_diff = multiport_s_to_grounded_differential_z(
            single_ended_sparams.s_matrix,
            single_ended_sparams.reference_impedance_ohm,
            port_pairs,
        )
    else:
        z_single = single_ended_sparams.to_z_parameters()
        z_diff = multiport_single_ended_to_differential_z(z_single, port_pairs)
    s_diff = z_to_s(z_diff, z0=target.differential_reference_impedance_ohm)
    diff_result = SParameterResult(
        freqs_hz=single_ended_sparams.freqs_hz,
        s_matrix=s_diff,
        reference_impedance_ohm=target.differential_reference_impedance_ohm,
    )
    return _extract_metrics_from_differential_z(
        freqs_hz=diff_result.freqs_hz,
        z_diff=z_diff,
        target=target,
        differential_sparams=diff_result,
    )


def extract_transformer_metrics(
    single_ended_sparams: SParameterResult,
    target: TransformerTargetSpec,
) -> tuple[TransformerMetrics, SParameterResult, np.ndarray]:
    """Extract differential transformer metrics from a 4-port EMX result."""
    if single_ended_sparams.num_ports != 4:
        raise ValueError(
            f"Transformer extraction expects a 4-port single-ended result, got {single_ended_sparams.num_ports}"
        )

    z_single = single_ended_sparams.to_z_parameters()
    z_diff = single_ended_to_differential_z(z_single)
    s_diff = z_to_s(z_diff, z0=target.differential_reference_impedance_ohm)
    diff_result = SParameterResult(freqs_hz=single_ended_sparams.freqs_hz, s_matrix=s_diff)
    return _extract_metrics_from_differential_z(
        freqs_hz=diff_result.freqs_hz,
        z_diff=z_diff,
        target=target,
        differential_sparams=diff_result,
    )
