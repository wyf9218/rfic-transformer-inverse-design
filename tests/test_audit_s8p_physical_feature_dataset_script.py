import csv
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from rfic_transformer_inverse_design.network_analysis import z_to_s


def _load_s8p_audit_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_s8p_physical_feature_dataset.py"
    spec = importlib.util.spec_from_file_location("audit_s8p_physical_feature_dataset_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_touchstone(path: Path, freqs_hz: np.ndarray, n_ports: int, s_matrix: np.ndarray | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if s_matrix is None:
        s_matrix = np.zeros((len(freqs_hz), n_ports, n_ports), dtype=np.complex128)
    with path.open("w", encoding="ascii") as handle:
        handle.write(f"! synthetic {n_ports}-port data\n")
        handle.write("# GHz S RI R 50\n")
        for idx, freq_hz in enumerate(freqs_hz):
            values = [f"{freq_hz / 1e9:.12g}"]
            for row in range(n_ports):
                for col in range(n_ports):
                    value = s_matrix[idx, row, col]
                    values.extend([f"{value.real:.16e}", f"{value.imag:.16e}"])
            handle.write(" ".join(values) + "\n")


def _known_transformer_s8p(freqs_hz: np.ndarray, *, lp_h: float, ls_h: float, k: float, qp: float, qs: float) -> tuple[np.ndarray, dict[str, float]]:
    omega = 2.0 * np.pi * freqs_hz
    mutual_h = float(k * np.sqrt(abs(lp_h * ls_h)))
    z_diff = np.zeros((len(freqs_hz), 2, 2), dtype=np.complex128)
    z_diff[:, 0, 0] = (omega * lp_h / qp) + 1j * omega * lp_h
    z_diff[:, 1, 1] = (omega * ls_h / qs) + 1j * omega * ls_h
    z_diff[:, 0, 1] = 1j * omega * mutual_h
    z_diff[:, 1, 0] = 1j * omega * mutual_h
    transform = np.zeros((8, 2), dtype=np.complex128)
    # "The best.s8p" convention: P001/P004 are primary (Lp),
    # P005/P006 are secondary (Ls).
    transform[0, 0] = 1.0
    transform[3, 0] = -1.0
    transform[4, 1] = 1.0
    transform[5, 1] = -1.0
    z_single = np.einsum("ai,fij,bj->fab", transform, z_diff, transform) / 4.0
    s_matrix = z_to_s(z_single, z0=50.0)
    center_idx = int(np.argmin(np.abs(freqs_hz - 5.1e9)))
    labels = {
        "physical_feature_center_freq_hz": float(freqs_hz[center_idx]),
        "lp_nh_center": float(lp_h * 1.0e9),
        "ls_nh_center": float(ls_h * 1.0e9),
        "m_nh_center": float(mutual_h * 1.0e9),
        "k_center": float(k),
        "qp_center": float(qp),
        "qs_center": float(qs),
    }
    return s_matrix, labels


def _write_dataset(
    root: Path,
    *,
    extension: str = ".s8p",
    n_ports: int = 8,
    include_power_line: bool = True,
    include_scalar_q: bool = True,
    bridge_width_um: float = 10.0,
    ground_frame_width_um: float | None = 100.0,
    ground_frame_policy: str | None = "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
    differential_port_pairs: list[list[int]] | None = None,
    port_map: list[str] | None = None,
) -> None:
    freqs = np.asarray([5.0e9, 5.1e9, 5.2e9])
    rows = []
    for index in range(2):
        touchstone = root / "evaluations" / f"s{index}" / "emx" / f"emx{extension}"
        if n_ports == 8 and extension.lower() == ".s8p":
            s_matrix, labels = _known_transformer_s8p(
                freqs,
                lp_h=(1.0 + index * 0.1) * 1.0e-9,
                ls_h=(1.2 + index * 0.1) * 1.0e-9,
                k=-0.4 + index * 0.01,
                qp=12.0 + index,
                qs=13.0 + index,
            )
        else:
            s_matrix = None
            labels = {
                "physical_feature_center_freq_hz": float(freqs[1]),
                "lp_nh_center": 1.0 + index * 0.1,
                "ls_nh_center": 1.2 + index * 0.1,
                "m_nh_center": -0.4 + index * 0.01,
                "k_center": -0.4 + index * 0.01,
                "qp_center": 12.0 + index,
                "qs_center": 13.0 + index,
            }
        _write_touchstone(touchstone, freqs, n_ports, s_matrix=s_matrix)
        rows.append(
            {
                "evaluation": f"s{index}",
                "ok": "true",
                "touchstone_path": str(touchstone.relative_to(root)),
                **labels,
                **({"q_center": min(labels["qp_center"], labels["qs_center"])} if include_scalar_q else {}),
            }
        )
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "requested_count": 2,
        "ok_count": 2,
        "fail_count": 0,
        "port_mode": "single_ended_shield_grounded",
        "differential_port_pairs": differential_port_pairs or [[1, 4], [5, 6]],
        "power_line_8port": {
            "enabled": bool(include_power_line),
            "bridge_width_um": bridge_width_um,
            "vertical_length_diameter_ratio": 1.5,
            "bridge_y_policy": "center",
            "bridge_motion_axis": "x_only",
            "port_ground_reference": "shield",
            "port_map": port_map or ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"],
            "role_labels": {
                "primary_top": "P001",
                "left_power_top": "P002",
                "left_power_bottom": "P003",
                "primary_bottom": "P004",
                "secondary_bottom": "P005",
                "secondary_top": "P006",
                "right_power_top": "P007",
                "right_power_bottom": "P008",
            },
        },
    }
    if ground_frame_width_um is not None:
        manifest["power_line_8port"]["ground_frame_width_um"] = ground_frame_width_um
    if ground_frame_policy is not None:
        manifest["power_line_8port"]["ground_frame_policy"] = ground_frame_policy
    (root / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def test_s8p_physical_feature_dataset_audit_passes_complete_contract() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--max-touchstone-checks",
                "2",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))
        assert status == 0
        assert summary["overall_status"] == "PASS"
        assert summary["decision"] == "S8P_PHYSICAL_FEATURE_DATASET_READY"
        assert summary["features"]["complete_finite_row_count"] == 2
        assert summary["touchstone"]["pass_count"] == 2
        assert summary["feature_label_recompute"]["fail_count"] == 0
        assert summary["feature_label_recompute"]["sample_count"] == 2
        assert summary["feature_label_recompute"]["max_relative_error"] < 1.0e-6
        assert summary["coverage"]["finite_count_min"] == 2
        assert "marginal_histograms" in summary["coverage_artifacts"]
        assert Path(summary["coverage_artifacts"]["marginal_histograms"]).is_file()
        assert Path(summary["coverage_artifacts"]["pairwise_scatter"]).is_file()
        assert Path(summary["coverage_artifacts"]["pair_heatmaps"]).is_file()


def test_s8p_physical_feature_dataset_audit_rejects_wrong_touchstone_extension_and_ports() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, extension=".s4p", n_ports=4)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--max-touchstone-checks",
                "2",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failures = summary["touchstone"]["failures"]
    assert failures
    assert ".s8p" in failures[0]["reason"]
    assert "ports expected 8" in failures[0]["reason"]


def test_s8p_physical_feature_dataset_audit_rejects_csv_labels_that_do_not_match_s8p() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root)
        csv_path = root / "dataset_rows.csv"
        rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
        rows[0]["lp_nh_center"] = str(float(rows[0]["lp_nh_center"]) * 1.25)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--max-touchstone-checks",
                "2",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "feature labels match sampled touchstones" in failed_names
    failures = summary["feature_label_recompute"]["failures"]
    assert failures
    assert failures[0]["metric"] == "lp_nh_center"


def test_s8p_physical_feature_dataset_audit_rejects_missing_scalar_q_center() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, include_scalar_q=False)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--max-touchstone-checks",
                "2",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "physical feature columns" in failed_names
    assert "scalar Q q_center present and finite" in failed_names


def test_s8p_physical_feature_dataset_audit_rejects_scalar_q_inconsistent_with_qp_qs() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root)
        csv_path = root / "dataset_rows.csv"
        rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
        rows[0]["q_center"] = str(float(rows[0]["q_center"]) + 2.0)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--max-touchstone-checks",
                "2",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "scalar Q matches Qp/Qs definition" in failed_names
    assert "feature labels match sampled touchstones" in failed_names
    failures = summary["feature_label_recompute"]["failures"]
    assert any(item["metric"] == "q_center" for item in failures)


def test_s8p_physical_feature_dataset_audit_requires_power_line_manifest_contract() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, include_power_line=False)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "manifest power_line_8port enabled" in failed_names


def test_s8p_physical_feature_dataset_audit_rejects_old_10nm_bridge_contract() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, bridge_width_um=0.01)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "manifest power_line_8port bridge width" in failed_names


def test_s8p_physical_feature_dataset_audit_rejects_missing_ground_frame_contract() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, ground_frame_width_um=None, ground_frame_policy=None)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "manifest power_line_8port ground frame width" in failed_names
    assert "manifest power_line_8port ground frame policy" in failed_names


def test_s8p_physical_feature_dataset_audit_rejects_wrong_differential_port_pairs() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, differential_port_pairs=[[3, 4], [5, 6]])

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "manifest differential port pairs match approved contract" in failed_names


def test_s8p_physical_feature_dataset_audit_rejects_wrong_power_line_port_map() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, port_map=["P003", "P004", "P005", "P006", "P001", "P002", "P007", "P008"])

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "manifest power_line_8port port map" in failed_names


def test_s8p_physical_feature_dataset_audit_rejects_missing_power_line_role_labels() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root)
        manifest_path = root / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["power_line_8port"].pop("role_labels")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--no-fail-exit",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "FAIL"
    failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
    assert "manifest power_line_8port role labels" in failed_names


def test_s8p_physical_feature_dataset_audit_prefers_scalar_q_for_coverage() -> None:
    audit = _load_s8p_audit_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_dataset(root, include_scalar_q=True)

        status = audit.main(
            [
                str(root),
                "--out-dir",
                str(root / "audit"),
                "--expected-count",
                "2",
                "--expected-frequency-stop-ghz",
                "5.2",
                "--expected-frequency-step-ghz",
                "0.1",
                "--expected-frequency-points",
                "3",
                "--max-touchstone-checks",
                "2",
            ]
        )

        summary = json.loads((root / "audit" / "s8p_physical_feature_dataset_audit_summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert summary["overall_status"] == "PASS"
    assert summary["coverage"]["feature_columns"] == ["lp_nh_center", "ls_nh_center", "q_center", "k_center"]
    assert summary["coverage"]["metrics"]["q_center"]["finite_count"] == 2
