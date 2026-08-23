from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from rfic_transformer_inverse_design.analysis.extraction import differential_2port_to_4port_s
from rfic_transformer_inverse_design.network_analysis import z_to_s
from rfic_transformer_inverse_design.sim.base import SParameterResult


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_real_emx_port_permutation_symmetry.py"


def test_verified_port_permutation_pair_passes(tmp_path: Path) -> None:
    reference, transformed = _write_pair(tmp_path)
    pairs = tmp_path / "pairs.csv"
    _write_manifest(pairs, reference, transformed, permutation="2,1,3,4")
    output = tmp_path / "audit"

    completed = _run(pairs, output)

    assert completed.returncode == 0, completed.stderr
    summary = _summary(output)
    assert summary["overall_status"] == "PASS"
    assert summary["pair_count"] == 1
    assert summary["pair_pass_count"] == 1
    assert summary["checks"]["real_emx_sources_only"] is True
    assert summary["aggregate_metrics"]["complex_s_rmse"]["max"] < 1.0e-12
    assert summary["paired_physical_cell_bootstrap"]["physical_cell_count"] == 1
    assert summary["paired_physical_cell_bootstrap"]["status"] == "PASS"
    assert "zero to the real-sample count" in summary["scientific_boundary"]


def test_wrong_port_permutation_fails_numeric_equivalence(tmp_path: Path) -> None:
    reference, transformed = _write_pair(tmp_path)
    pairs = tmp_path / "pairs.csv"
    _write_manifest(pairs, reference, transformed, permutation="1,2,3,4")
    output = tmp_path / "audit"

    completed = _run(pairs, output)

    assert completed.returncode == 2
    summary = _summary(output)
    assert summary["overall_status"] == "FAIL"
    rows = list(csv.DictReader((output / "real_emx_port_permutation_symmetry_pairs.csv").open()))
    assert "complex_s" in rows[0]["failure_reasons"]


def test_self_pair_and_non_emx_source_are_rejected(tmp_path: Path) -> None:
    reference, _ = _write_pair(tmp_path)
    pairs = tmp_path / "pairs.csv"
    with pairs.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "pair_id",
                "reference_touchstone",
                "transformed_touchstone",
                "reference_geometry_id",
                "transformed_geometry_id",
                "physical_cell_id",
                "reference_source_kind",
                "transformed_source_kind",
                "transformed_ports_for_reference",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "bad",
                "reference_touchstone": reference.name,
                "transformed_touchstone": reference.name,
                "reference_geometry_id": "same",
                "transformed_geometry_id": "same",
                "physical_cell_id": "cell-000",
                "reference_source_kind": "EMX",
                "transformed_source_kind": "HFSS",
                "transformed_ports_for_reference": "1,2,3,4",
            }
        )
    output = tmp_path / "audit"

    completed = _run(pairs, output)

    assert completed.returncode == 2
    summary = _summary(output)
    assert summary["checks"]["no_self_pairs"] is False
    assert summary["checks"]["independent_geometry_ids"] is False
    assert summary["checks"]["real_emx_sources_only"] is False


def test_emx_manifest_declaration_without_embedded_solver_evidence_is_rejected(tmp_path: Path) -> None:
    reference, transformed = _write_pair(tmp_path)
    transformed.write_text(
        "\n".join(
            line
            for line in transformed.read_text(encoding="utf-8").splitlines()
            if not line.startswith("! Touchstone simulation data from EMX")
            and not line.startswith("! EMX was run")
            and "/emx/bin/64bit/emx" not in line
            and "TSMC65_05_12_26" not in line
            and "--include-command-line" not in line
            and "--port=P00" not in line
            and "--sweep " not in line
        )
        + "\n",
        encoding="utf-8",
    )
    pairs = tmp_path / "pairs.csv"
    _write_manifest(pairs, reference, transformed, permutation="2,1,3,4")
    output = tmp_path / "audit"

    completed = _run(pairs, output)

    assert completed.returncode == 2
    summary = _summary(output)
    assert summary["checks"]["real_emx_sources_only"] is False
    rows = list(csv.DictReader((output / "real_emx_port_permutation_symmetry_pairs.csv").open()))
    assert rows[0]["declared_emx_sources"] == "True"
    assert rows[0]["embedded_emx_evidence"] == "False"


def test_physical_cell_diversity_gate_cannot_be_satisfied_by_repeated_pairs(tmp_path: Path) -> None:
    reference, transformed = _write_pair(tmp_path)
    pairs = tmp_path / "pairs.csv"
    _write_manifest(pairs, reference, transformed, permutation="2,1,3,4")
    output = tmp_path / "audit"

    completed = _run(pairs, output, min_physical_cells=2)

    assert completed.returncode == 2
    summary = _summary(output)
    assert summary["pair_pass_count"] == 1
    assert summary["checks"]["physical_cell_count_meets_minimum"] is False


def _write_pair(root: Path) -> tuple[Path, Path]:
    freqs_hz = np.linspace(5.0e9, 60.0e9, 111)
    omega = 2.0 * np.pi * freqs_hz
    lp_h = 1.2e-9
    ls_h = 1.5e-9
    mutual_h = 0.5 * np.sqrt(lp_h * ls_h)
    r_primary = 2.0 * np.pi * 15.0e9 * lp_h / 10.0
    r_secondary = 2.0 * np.pi * 15.0e9 * ls_h / 10.0
    z_diff = np.zeros((len(freqs_hz), 2, 2), dtype=np.complex128)
    z_diff[:, 0, 0] = r_primary + 1j * omega * lp_h
    z_diff[:, 1, 1] = r_secondary + 1j * omega * ls_h
    z_diff[:, 0, 1] = 1j * omega * mutual_h
    z_diff[:, 1, 0] = z_diff[:, 0, 1]
    differential_s = z_to_s(z_diff, z0=100.0)
    reference_result = differential_2port_to_4port_s(freqs_hz, differential_s)
    permutation = np.asarray([1, 0, 2, 3], dtype=int)
    transformed_result = SParameterResult(
        freqs_hz=freqs_hz,
        s_matrix=reference_result.s_matrix[:, permutation][:, :, permutation],
        reference_impedance_ohm=50.0,
    )
    reference = root / "reference.s4p"
    transformed = root / "transformed.s4p"
    reference_result.to_touchstone(reference)
    transformed_result.to_touchstone(transformed)
    _prepend_emx_header(reference)
    _prepend_emx_header(transformed)
    return reference, transformed


def _prepend_emx_header(path: Path) -> None:
    header = "\n".join(
        [
            "! Touchstone simulation data from EMX version 2025.1.0",
            "! EMX was run on test-host as:",
            "! /opt/cadence/EMX/tools.lnx86/emx/bin/64bit/emx",
            "! /pdk/TSMC65_05_12_26/RC_IRCX_typical.proc",
            "! --include-command-line --cadence-pins=51 --s-impedance=50",
            "! --accuracy=standard --parallel=2",
            "! --port=P001=P001:P001_G --port=P002=P002:P002_G",
            "! --port=P003=P003:P003_G --port=P004=P004:P004_G",
            "! --sweep 5000000000 60000000000 --sweep-stepsize 500000000",
            "",
        ]
    )
    path.write_text(header + path.read_text(encoding="utf-8"), encoding="utf-8")


def _write_manifest(pairs: Path, reference: Path, transformed: Path, *, permutation: str) -> None:
    with pairs.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "pair_id",
                "reference_touchstone",
                "transformed_touchstone",
                "reference_geometry_id",
                "transformed_geometry_id",
                "physical_cell_id",
                "reference_source_kind",
                "transformed_source_kind",
                "transformed_ports_for_reference",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "pair-001",
                "reference_touchstone": reference.name,
                "transformed_touchstone": transformed.name,
                "reference_geometry_id": "geometry-reference",
                "transformed_geometry_id": "geometry-transformed",
                "physical_cell_id": "cell-000",
                "reference_source_kind": "EMX",
                "transformed_source_kind": "EMX",
                "transformed_ports_for_reference": permutation,
            }
        )


def _run(pairs: Path, output: Path, *, min_physical_cells: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pairs-csv",
            str(pairs),
            "--out-dir",
            str(output),
            "--min-pairs",
            "1",
            "--min-physical-cells",
            str(min_physical_cells),
            "--bootstrap-repetitions",
            "100",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _summary(output: Path) -> dict[str, object]:
    return json.loads(
        (output / "real_emx_port_permutation_symmetry_summary.json").read_text(encoding="utf-8")
    )
