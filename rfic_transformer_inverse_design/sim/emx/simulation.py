"""Minimal EMX subprocess wrapper used by rfic_transformer_inverse_design."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

from ...paths import resolve_local_path
from ..base import SParameterResult, SolverType
from ..touchstone import load_touchstone
from .layout_export import EMXLayoutManifest

logger = logging.getLogger(__name__)


class EMXSimulation:
    """Run Cadence EMX on a pre-exported layout."""

    def __init__(
        self,
        *,
        emx_binary: str | None = None,
        process_file: str | None = None,
        top_cell: str | None = None,
        layout_path: str | None = None,
        extra_args: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._emx_binary = emx_binary or "emx"
        self._emx_home: str | None = None
        self._process_file = process_file
        self._top_cell = top_cell or "TOP"
        self._layout_path = Path(layout_path).resolve() if layout_path else None
        self._extra_args = list(extra_args or ())
        self._use_cadence_license_env: bool = True
        self._license_file: str | None = None
        self._cdslmd_license_file: str | None = None
        self._skip_os_check: bool = True
        self._project_dir: Path | None = None
        self._layout_manifest: EMXLayoutManifest | None = None
        self._solver_config: dict[str, object] = {}
        self._last_touchstone_path: Path | None = None
        self._last_result: SParameterResult | None = None

    def connect(self) -> None:
        binary_path = None
        if self._emx_home:
            home_root = Path(self._emx_home)
            direct_candidates = sorted(home_root.glob("tools.*/emx/bin/64bit/emx"))
            if direct_candidates:
                binary_path = str(direct_candidates[0].resolve())
            else:
                home_candidate = home_root / "bin" / Path(self._emx_binary).name
                if home_candidate.exists():
                    binary_path = str(home_candidate.resolve())
        if binary_path is None:
            binary_path = shutil.which(self._emx_binary) if self._emx_binary else None
        if binary_path is None and self._emx_binary and Path(self._emx_binary).exists():
            binary_path = str(Path(self._emx_binary).resolve())
        if binary_path is None:
            raise RuntimeError(f"EMX executable not found: {self._emx_binary}")
        self._emx_binary = binary_path

    def disconnect(self) -> None:
        return None

    def create_project(self, project_path: Path | None = None) -> None:
        project_dir = Path.cwd() / "tmp" / "emx" if project_path is None else Path(project_path)
        self._project_dir = project_dir.resolve()
        self._project_dir.mkdir(parents=True, exist_ok=True)
        self._last_touchstone_path = None
        self._last_result = None

    def save_project(self, path: Path | None = None) -> None:
        if self._project_dir is None:
            return
        target = Path(path) if path is not None else self._project_dir / "emx_project_state.json"
        payload = {
            "emx_binary": self._emx_binary,
            "emx_home": self._emx_home,
            "process_file": self._process_file,
            "top_cell": self._top_cell,
            "layout_path": str(self._layout_path) if self._layout_path else None,
            "solver_config": self._solver_config,
        }
        target.write_text(json.dumps(payload, indent=2), encoding="ascii")

    def configure_solver(
        self,
        solver_type: SolverType,
        freq_start_hz: float,
        freq_stop_hz: float,
        num_freq_points: int = 81,
        freq_points_hz: np.ndarray | list[float] | tuple[float, ...] | None = None,
        **kwargs,
    ) -> None:
        self._solver_config = {
            "solver_type": solver_type.value,
            "freq_start_hz": float(freq_start_hz),
            "freq_stop_hz": float(freq_stop_hz),
            "num_freq_points": int(num_freq_points),
            "freq_points_hz": (
                None
                if freq_points_hz is None
                else [float(freq) for freq in np.asarray(freq_points_hz, dtype=float).ravel()]
            ),
            "kwargs": kwargs,
        }

    def run_solver(self) -> None:
        if self._project_dir is None:
            raise RuntimeError("Call create_project() before run_solver().")
        if not self._solver_config:
            raise RuntimeError("Call configure_solver() before run_solver().")
        layout_path = self._resolve_layout_path()
        cmd = self._build_emx_command(layout_path)
        logger.info("Running EMX: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=str(self._project_dir),
            capture_output=True,
            text=True,
            check=False,
            env=self._build_subprocess_env(),
        )
        (self._project_dir / "emx_stdout.log").write_text(result.stdout, encoding="utf-8")
        (self._project_dir / "emx_stderr.log").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(
                f"EMX failed with exit code {result.returncode}. See {(self._project_dir / 'emx_stderr.log')} for details."
            )
        self._last_touchstone_path = self._find_touchstone_output()
        self._last_result = load_touchstone(self._last_touchstone_path)
        if self._layout_manifest is not None and self._last_result.num_ports != self._num_ports_for_output():
            raise RuntimeError(
                "EMX Touchstone port count does not match layout manifest: "
                f"expected {self._num_ports_for_output()}, got {self._last_result.num_ports} "
                f"from {self._last_touchstone_path}"
            )

    def get_s_parameters(self) -> SParameterResult:
        if self._last_result is None:
            raise RuntimeError("No EMX result available. Call run_solver() first.")
        return self._last_result

    def _resolve_layout_path(self) -> Path:
        if self._layout_path is not None:
            return self._layout_path
        raise RuntimeError("No EMX-readable layout is available.")

    def _resolve_process_path(self) -> Path:
        if not self._process_file:
            raise RuntimeError("EMX process file not configured.")
        return resolve_local_path(self._process_file)

    def _build_emx_command(self, layout_path: Path) -> list[str]:
        if self._project_dir is None:
            raise RuntimeError("Call create_project() before building the EMX command.")
        process_path = self._resolve_process_path()
        if not process_path.exists():
            raise RuntimeError(f"EMX process file does not exist: {process_path}")
        solver_type = str(self._solver_config["solver_type"])
        if solver_type not in {SolverType.FREQUENCY_DOMAIN.value, SolverType.INTEGRAL.value}:
            logger.warning("EMX is a frequency-domain planar solver; requested solver=%s", solver_type)
        explicit_freqs = self._solver_config.get("freq_points_hz")
        freqs = (
            np.asarray(explicit_freqs, dtype=float)
            if explicit_freqs is not None
            else np.linspace(
                float(self._solver_config["freq_start_hz"]),
                float(self._solver_config["freq_stop_hz"]),
                int(self._solver_config["num_freq_points"]),
            )
        )
        return [
            str(self._emx_binary),
            str(layout_path),
            self._top_cell,
            str(process_path),
            "--touchstone",
            f"--s-impedance={self._get_reference_impedance_ohm():.12g}",
            "-s",
            str(self._expected_touchstone_path()),
            "--include-command-line",
            *self._extra_args,
            *self._build_port_args(),
            *self._build_frequency_args(freqs),
        ]

    def _build_frequency_args(self, freqs: np.ndarray) -> list[str]:
        """Use EMX sweep syntax for evenly spaced grids; fall back to explicit points."""
        freqs = np.asarray(freqs, dtype=float).ravel()
        if freqs.size < 2:
            return [f"{freq:.12g}" for freq in freqs]
        steps = np.diff(freqs)
        step = float(steps[0])
        if step > 0.0 and np.allclose(steps, step, rtol=1.0e-9, atol=1.0):
            return [
                "--sweep",
                f"{float(freqs[0]):.12g}",
                f"{float(freqs[-1]):.12g}",
                "--sweep-stepsize",
                f"{step:.12g}",
            ]
        return [f"{freq:.12g}" for freq in freqs]

    def _build_port_args(self) -> list[str]:
        if self._layout_manifest is None:
            return []
        args: list[str] = []
        cadence_pin_purpose = getattr(self._layout_manifest, "cadence_pin_purpose", None)
        if cadence_pin_purpose is not None:
            args.append(f"--cadence-pins={int(cadence_pin_purpose)}")
        for port in self._layout_manifest.ports:
            signal = ",".join(port.signal_labels)
            if port.ground_labels:
                args.append(f"--port={port.name}={signal}:{','.join(port.ground_labels)}")
            else:
                args.append(f"--port={port.name}" if signal == port.name else f"--port={port.name}={signal}")
            if cadence_pin_purpose is not None:
                continue
            default_w_um, default_h_um = port.internal_size_um
            if getattr(port, "internal_signal_labels", True):
                signal_size = getattr(port, "signal_internal_size_um", None) or (default_w_um, default_h_um)
                for label in port.signal_labels:
                    args.append(f"--internal={label},{float(signal_size[0]):.6g},{float(signal_size[1]):.6g}")
            if getattr(port, "internal_ground_labels", True):
                ground_size = getattr(port, "ground_internal_size_um", None) or (default_w_um, default_h_um)
                for label in port.ground_labels:
                    args.append(f"--internal={label},{float(ground_size[0]):.6g},{float(ground_size[1]):.6g}")
        return args

    def _get_reference_impedance_ohm(self) -> float:
        return 50.0

    def _find_touchstone_output(self) -> Path:
        assert self._project_dir is not None
        expected = self._expected_touchstone_path()
        if expected.exists():
            return expected
        touchstones = sorted(self._project_dir.glob("*.s*p"))
        if not touchstones:
            raise RuntimeError(f"EMX completed but no Touchstone output was found in {self._project_dir}.")
        if self._layout_manifest is not None:
            available = ", ".join(path.name for path in touchstones)
            raise RuntimeError(
                "EMX completed but did not produce the manifest-matched Touchstone output "
                f"{expected.name} for {self._num_ports_for_output()} ports. "
                f"Available Touchstone files: {available}"
            )
        return touchstones[0]

    def _expected_touchstone_path(self) -> Path:
        assert self._project_dir is not None
        return self._project_dir / f"emx.s{self._num_ports_for_output()}p"

    def _num_ports_for_output(self) -> int:
        return len(self._layout_manifest.ports) if self._layout_manifest is not None else 1

    def _build_subprocess_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._emx_home:
            env["EMXHOME"] = self._emx_home
            home_root = Path(self._emx_home)
            path_entries: list[str] = []
            tools_bin_candidates = sorted(home_root.glob("tools.*/bin"))
            if tools_bin_candidates:
                path_entries.append(str(tools_bin_candidates[0]))
            path_entries.append(str(home_root / "bin"))
            if env.get("PATH"):
                path_entries.append(env["PATH"])
            env["PATH"] = os.pathsep.join(path_entries)
        if self._use_cadence_license_env:
            if self._license_file:
                env["LM_LICENSE_FILE"] = self._license_file
                env["CDS_LIC_FILE"] = self._license_file
            if self._cdslmd_license_file:
                env["CDSLMD_LICENSE_FILE"] = self._cdslmd_license_file
            elif self._license_file and "CDSLMD_LICENSE_FILE" not in env:
                env["CDSLMD_LICENSE_FILE"] = self._license_file
        if self._skip_os_check:
            env["CDS_SKIP_OS_CHECK_ON_STARTUP"] = "1"
        return env
