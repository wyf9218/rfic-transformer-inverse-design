"""Touchstone parsing helpers for rfic_transformer_inverse_design."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .base import SParameterResult


def load_touchstone(path: str | Path) -> SParameterResult:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\.s(\d+)p$", path.name, re.IGNORECASE)
    n_ports = int(match.group(1)) if match else None

    freq_unit = 1.0
    parameter_type = "s"
    fmt = "ri"
    reference: float | list[float] = 50.0
    two_port_data_order = "21_12"
    matrix_format = "full"
    rows: list[list[float]] = []
    current_row: list[float] = []
    pending_reference = False
    in_information_block = False

    for line in text.splitlines():
        line = line.split("!")[0].strip()
        if not line:
            continue
        if in_information_block:
            keyword_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
            if keyword_match and keyword_match.group(1).strip().lower() == "end information":
                in_information_block = False
            continue
        if pending_reference and not line.startswith(("[", "#")):
            values = _parse_float_tokens(line.split())
            if values:
                reference = values
            pending_reference = False
            continue
        if line.startswith("["):
            keyword_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
            if not keyword_match:
                continue
            keyword = keyword_match.group(1).strip().lower()
            rest = keyword_match.group(2).strip()
            if keyword == "number of ports":
                n_ports = int(rest.split()[0])
            elif keyword == "reference":
                values = _parse_float_tokens(rest.split())
                if values:
                    reference = values
                else:
                    pending_reference = True
            elif keyword == "two-port data order":
                if rest:
                    two_port_data_order = rest.split()[0].strip().lower()
            elif keyword == "matrix format":
                if rest:
                    matrix_format = rest.split()[0].strip().lower()
            elif keyword == "begin information":
                in_information_block = True
            elif keyword == "end":
                break
            continue
        if line.startswith("#"):
            parts = line[1:].lower().split()
            index = 0
            while index < len(parts):
                part = parts[index]
                if part in ("hz", "khz", "mhz", "ghz"):
                    freq_unit = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}[part]
                elif part in ("s", "y", "z", "g", "h"):
                    parameter_type = part
                elif part in ("ri", "ma", "db"):
                    fmt = part
                elif part == "r":
                    values: list[float] = []
                    lookahead = index + 1
                    while lookahead < len(parts):
                        try:
                            values.append(_float_token(parts[lookahead]))
                        except ValueError:
                            break
                        lookahead += 1
                    if values:
                        reference = values
                        index = lookahead - 1
                index += 1
            continue
        if n_ports is None:
            raise ValueError(f"Cannot infer Touchstone port count from filename or [Number of Ports] in {path}")
        current_row.extend(_float_token(token) for token in line.split())
        expected = 1 + 2 * _network_value_count(n_ports, matrix_format)
        while len(current_row) >= expected:
            rows.append(current_row[:expected])
            current_row = current_row[expected:]

    if n_ports is None:
        raise ValueError(f"Cannot infer Touchstone port count from filename or [Number of Ports] in {path}")
    if parameter_type != "s":
        raise ValueError(f"Unsupported Touchstone parameter type {parameter_type!r}; expected S-parameters")
    if current_row:
        raise ValueError(
            f"Incomplete Touchstone data row in {path}: got {len(current_row)} values after the last complete row"
        )
    if not rows:
        raise ValueError(f"No numeric Touchstone data rows found in {path}")

    arr = np.asarray(rows, dtype=float)
    freqs_hz = arr[:, 0] * freq_unit
    pairs = arr[:, 1:].reshape(len(freqs_hz), _network_value_count(n_ports, matrix_format), 2)
    if fmt == "ri":
        s_flat = pairs[:, :, 0] + 1j * pairs[:, :, 1]
    elif fmt == "ma":
        s_flat = pairs[:, :, 0] * np.exp(1j * np.deg2rad(pairs[:, :, 1]))
    else:
        s_flat = 10 ** (pairs[:, :, 0] / 20.0) * np.exp(1j * np.deg2rad(pairs[:, :, 1]))
    return SParameterResult(
        freqs_hz=freqs_hz,
        s_matrix=_reshape_network_data(s_flat, n_ports, two_port_data_order, matrix_format),
        reference_impedance_ohm=_normalize_reference(reference, n_ports),
    )


def _parse_float_tokens(tokens: list[str]) -> list[float]:
    values: list[float] = []
    for token in tokens:
        try:
            values.append(_float_token(token))
        except ValueError:
            break
    return values


def _float_token(token: str) -> float:
    return float(token.replace("D", "E").replace("d", "e"))


def _normalize_reference(reference: float | list[float], n_ports: int) -> float | np.ndarray:
    values = np.asarray(reference, dtype=float)
    if values.ndim == 0:
        return float(values)
    if values.size == 1:
        return float(values[0])
    if values.size == n_ports:
        return values.astype(float, copy=False)
    raise ValueError(f"Touchstone reference impedance must be scalar or length {n_ports}, got {values.size} values")


def _network_value_count(n_ports: int, matrix_format: str) -> int:
    normalized = matrix_format.lower()
    if normalized == "full":
        return n_ports * n_ports
    if normalized in {"lower", "upper"}:
        return n_ports * (n_ports + 1) // 2
    raise ValueError(f"Unsupported [Matrix Format] {matrix_format!r}; expected Full, Lower, or Upper")


def _reshape_network_data(
    s_flat: np.ndarray,
    n_ports: int,
    two_port_data_order: str,
    matrix_format: str,
) -> np.ndarray:
    normalized_format = matrix_format.lower()
    if normalized_format in {"lower", "upper"}:
        return _expand_triangular_network_data(s_flat, n_ports, normalized_format)
    if normalized_format != "full":
        raise ValueError(f"Unsupported [Matrix Format] {matrix_format!r}; expected Full, Lower, or Upper")
    if n_ports != 2:
        return s_flat.reshape(len(s_flat), n_ports, n_ports)
    normalized = two_port_data_order.lower()
    if normalized == "12_21":
        return s_flat.reshape(len(s_flat), 2, 2)
    if normalized != "21_12":
        raise ValueError(f"Unsupported [Two-Port Data Order] {two_port_data_order!r}; expected 21_12 or 12_21")
    matrix = np.empty((len(s_flat), 2, 2), dtype=np.complex128)
    matrix[:, 0, 0] = s_flat[:, 0]
    matrix[:, 1, 0] = s_flat[:, 1]
    matrix[:, 0, 1] = s_flat[:, 2]
    matrix[:, 1, 1] = s_flat[:, 3]
    return matrix


def _expand_triangular_network_data(s_flat: np.ndarray, n_ports: int, matrix_format: str) -> np.ndarray:
    matrix = np.zeros((len(s_flat), n_ports, n_ports), dtype=np.complex128)
    value_index = 0
    if matrix_format == "lower":
        pairs = [(row, col) for row in range(n_ports) for col in range(row + 1)]
    else:
        pairs = [(row, col) for row in range(n_ports) for col in range(row, n_ports)]
    for row, col in pairs:
        value = s_flat[:, value_index]
        matrix[:, row, col] = value
        matrix[:, col, row] = value
        value_index += 1
    return matrix
