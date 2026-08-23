from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "stage_hfss_s8p_manual_import_to_gate.py"
    spec = importlib.util.spec_from_file_location("stage_hfss_s8p_manual_import_to_gate_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_s8p(
    path: Path,
    *,
    points: int = 111,
    start_ghz: float = 5.0,
    step_ghz: float = 0.5,
    ports: int = 8,
    reference_ohm: float = 50.0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_count = 2 * ports * ports
    lines = [f"# GHz S RI R {reference_ohm:g}\n"]
    for index in range(points):
        freq = start_ghz + index * step_ghz
        lines.append(f"{freq:.12g} " + " ".join(["0"] * value_count) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def _make_gate(tmp_path: Path, mod, *, gate: str = "v67", variant: str = "v67a_tight_mesh_baseline", sample_id: str = "26cb45d70af3cfd0") -> Path:
    root = tmp_path / gate
    mod.GATE_ROOTS[gate] = root
    sample_dir = root / "variants" / variant / sample_id
    sample_dir.mkdir(parents=True)
    (sample_dir / f"hfss_{gate}_single_variant_packet_summary.json").write_text("{}", encoding="utf-8")
    return sample_dir


def test_dry_run_valid_source_does_not_copy(tmp_path):
    mod = _load_module()
    _make_gate(tmp_path, mod)
    source = tmp_path / "manual" / "hfss_export.s8p"
    _write_s8p(source)

    status = mod.main(
        [
            "--source-s8p",
            str(source),
            "--gate",
            "v67",
            "--variant",
            "v67a_tight_mesh_baseline",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_manual_import_to_gate_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["decision"] == "READY_TO_STAGE_HFSS_S8P_ADD_APPLY"
    assert summary["copied"] is False
    assert not Path(summary["target_s8p"]).exists()


def test_apply_valid_source_copies_and_writes_manifest(tmp_path):
    mod = _load_module()
    sample_dir = _make_gate(tmp_path, mod)
    source = tmp_path / "manual" / "hfss_export.s8p"
    _write_s8p(source)

    status = mod.main(
        [
            "--source-s8p",
            str(source),
            "--gate",
            "v67",
            "--variant",
            "v67a_tight_mesh_baseline",
            "--out-dir",
            str(tmp_path / "out"),
            "--apply",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_manual_import_to_gate_summary.json").read_text(encoding="utf-8"))
    target = Path(summary["target_s8p"])
    assert summary["overall_status"] == "PASS"
    assert target.is_file()
    manifest = json.loads((sample_dir / "hfss_solve_export_results" / "hfss_s8p_manual_import_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_s8p"] == str(source.resolve())
    assert manifest["target_sha256"] == manifest["source_sha256"]


def test_wrong_frequency_grid_is_rejected(tmp_path):
    mod = _load_module()
    _make_gate(tmp_path, mod)
    source = tmp_path / "manual" / "hfss_export.s8p"
    _write_s8p(source, points=2, start_ghz=15.0)

    status = mod.main(
        [
            "--source-s8p",
            str(source),
            "--gate",
            "v67",
            "--variant",
            "v67a_tight_mesh_baseline",
            "--out-dir",
            str(tmp_path / "out"),
            "--apply",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_manual_import_to_gate_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "source frequency point count is expected" in failed
    assert "source frequency start is expected" in failed


def test_wrong_reference_impedance_is_rejected(tmp_path):
    mod = _load_module()
    _make_gate(tmp_path, mod)
    source = tmp_path / "manual" / "hfss_export.s8p"
    _write_s8p(source, reference_ohm=75.0)

    status = mod.main(
        [
            "--source-s8p",
            str(source),
            "--gate",
            "v67",
            "--variant",
            "v67a_tight_mesh_baseline",
            "--out-dir",
            str(tmp_path / "out"),
            "--apply",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_manual_import_to_gate_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "source reference impedance is expected" in failed


def test_existing_target_s8p_blocks_import_without_overwrite(tmp_path):
    mod = _load_module()
    sample_dir = _make_gate(tmp_path, mod)
    _write_s8p(sample_dir / "hfss_solve_export_results" / "existing.s8p")
    source = tmp_path / "manual" / "hfss_export.s8p"
    _write_s8p(source)

    status = mod.main(
        [
            "--source-s8p",
            str(source),
            "--gate",
            "v67",
            "--variant",
            "v67a_tight_mesh_baseline",
            "--out-dir",
            str(tmp_path / "out"),
            "--apply",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_manual_import_to_gate_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "target directory has no existing .s8p or overwrite enabled" in failed
