from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys

import pytest

from rfic_transformer_inverse_design.network_analysis import z_to_s


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_low_frequency_coupled_rl_consistency.py"
    spec = importlib.util.spec_from_file_location(
        "audit_low_frequency_coupled_rl_consistency_script",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_ideal_transformer(path: Path, frequencies: np.ndarray) -> None:
    target = default_target_spec()
    differential = build_lumped_transformer_sparameters(
        freqs_hz=frequencies,
        target=target,
        q_primary=18.0,
        q_secondary=16.0,
    )
    single = differential_2port_to_4port_s(
        freqs_hz=frequencies,
        s_diff=differential.s_matrix,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


def _write_unphysical_transformer(path: Path, frequencies: np.ndarray) -> None:
    target = default_target_spec()
    omega = 2.0 * np.pi * frequencies
    lp_h = 1.0e-9
    ls_h = 1.4e-9
    mutual_h = 1.35 * np.sqrt(lp_h * ls_h)
    z = np.zeros((len(frequencies), 2, 2), dtype=np.complex128)
    z[:, 0, 0] = 0.4 + 1j * omega * lp_h
    z[:, 1, 1] = 0.5 + 1j * omega * ls_h
    z[:, 0, 1] = 1j * omega * mutual_h
    z[:, 1, 0] = 1j * omega * mutual_h
    s_diff = z_to_s(z, z0=target.differential_reference_impedance_ohm)
    single = differential_2port_to_4port_s(
        freqs_hz=frequencies,
        s_diff=s_diff,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


def _write_dataset(root: Path, *, unphysical: bool = False) -> None:
    frequencies = np.linspace(5.0e9, 60.0e9, 111)
    rows = []
    for index in range(4):
        path = root / f"sample_{index}.s4p"
        if unphysical:
            _write_unphysical_transformer(path, frequencies)
        else:
            _write_ideal_transformer(path, frequencies)
        rows.append({"evaluation": f"sample_{index}", "ok": "true", "touchstone_path": str(path)})
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _run(module, root: Path, out_dir: Path, *, no_fail_exit: bool = False) -> int:
    argv = [
        "--dataset-dir",
        str(root),
        "--out-dir",
        str(out_dir),
        "--min-files",
        "2",
        "--max-files",
        "4",
    ]
    if no_fail_exit:
        argv.append("--no-fail-exit")
    return module.main(argv)


def test_low_frequency_coupled_rl_audit_accepts_passive_lumped_transformer(tmp_path):
    module = _load_module()
    _write_dataset(tmp_path)
    out_dir = tmp_path / "out"

    assert _run(module, tmp_path, out_dir) == 0
    summary = json.loads((out_dir / "low_frequency_coupled_rl_consistency_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["analysis"]["physically_plausible_fraction"] == 1.0
    assert summary["analysis"]["reference_below_half_srf_fraction"] == 1.0
    assert summary["analysis"]["coupled_rl_relative_residual"]["p95"] < 0.02
    assert summary["recommendation"]["decision"] == "COUPLED_RL_AUXILIARY_PHYSICS_LOSS_ABLATION_READY"
    assert (out_dir / "low_frequency_coupled_rl_consistency_rows.csv").is_file()
    assert (out_dir / "low_frequency_coupled_rl_consistency_report.md").is_file()


def test_low_frequency_coupled_rl_audit_rejects_indefinite_coupling_matrix(tmp_path):
    module = _load_module()
    _write_dataset(tmp_path, unphysical=True)
    out_dir = tmp_path / "out"

    assert _run(module, tmp_path, out_dir, no_fail_exit=True) == 0
    summary = json.loads((out_dir / "low_frequency_coupled_rl_consistency_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["physical_plausibility_fraction"] is False
    assert summary["analysis"]["physically_plausible_fraction"] == 0.0
    assert summary["analysis"]["minimum_inductance_eigenvalue_nh"]["minimum"] < 0.0


def test_srf_interpolation_and_half_srf_boundary():
    module = _load_module()
    frequencies = np.asarray([5.0e9, 10.0e9, 15.0e9, 20.0e9])
    reactance = np.asarray([4.0, 2.0, -2.0, -4.0])

    srf = module._first_srf_ghz(frequencies, reactance, 0)

    assert srf == pytest.approx(12.5)
    assert module._below_half_srf(5.0, srf, None, 20.0) is True
    assert module._below_half_srf(10.0, srf, None, 20.0) is False
