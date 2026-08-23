from pathlib import Path

import pytest

from rfic_transformer_inverse_design.process import parse_proc_file


def test_parse_default_proc_extracts_synthetic_metal_thickness_gds_and_stack_heights() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    proc_path = repo_root / "rfic_transformer_inverse_design" / "process" / "assets" / "proc" / "default_typical.proc"

    info = parse_proc_file(proc_path)

    metal10 = info.conductor_named("metal10")
    metal9 = info.conductor_named("metal9")
    metal5 = info.conductor_named("metal5")

    assert metal10 is not None
    assert metal9 is not None
    assert metal5 is not None
    assert metal10.thickness_um == pytest.approx(2.8)
    assert metal9.thickness_um == pytest.approx(3.4)
    assert metal5.thickness_um == pytest.approx(0.22)
    assert metal10.z_bottom_um == pytest.approx(715.01, abs=1.0e-3)
    assert metal10.z_top_um == pytest.approx(717.81, abs=1.0e-3)
    assert metal9.z_bottom_um == pytest.approx(709.61, abs=1.0e-3)
    assert metal9.z_top_um == pytest.approx(713.01, abs=1.0e-3)
    assert metal5.z_bottom_um == pytest.approx(703.72, abs=1.0e-3)
    assert metal5.z_top_um == pytest.approx(703.94, abs=1.0e-3)
    assert 74 in metal10.gds_layers
    assert 126 in metal10.gds_layers
    assert 39 in metal9.gds_layers
    assert 139 in metal9.gds_layers
    assert info.summary_for_gds_layer(74) == "metal10 (2.800 um conductor); role=drawing, datatype=0"
    assert info.summary_for_gds_layer(85) == "via9 (via definition); datatype=0"
    assert info.display_label_for_gds_layer(74) == "M10 / metal10 drawing [74]"
    assert info.display_label_for_gds_layer(126) == "M10 / metal10 pin [126]"
    assert info.display_label_for_gds_layer(85) == "V9 / via9 [85]"
    assert ("M10 / metal10 drawing [74]", 74) in info.selectable_layer_options()
    assert info.gds_layer_for_metal_number(10) == 74
    assert info.gds_layer_for_metal_number(9) == 39
    assert info.preferred_draw_pair_for_layer(139).layer == 39
    assert info.preferred_draw_pair_for_layer(139).datatype == 60
    assert info.preferred_pin_pair_for_layer(39).layer == 139
    assert info.preferred_pin_pair_for_layer(39).datatype == 0
    assert len(info.dielectrics) > 0
    assert info.dielectrics[0].thickness_um == pytest.approx(700.0)
    assert info.dielectrics[0].z_bottom_um == pytest.approx(0.0)
    assert info.dielectrics[0].z_top_um == pytest.approx(700.0)


def test_parse_proc_tracks_cumulative_dielectric_and_conductor_heights(tmp_path: Path) -> None:
    proc_path = tmp_path / "toy.proc"
    proc_path.write_text(
        "\n".join(
            [
                "define metal1 = l1t0",
                "layer 800 4.4 # substrate",
                "conductor 35 0.01 metal1",
            ]
        ),
        encoding="utf-8",
    )

    info = parse_proc_file(proc_path)

    assert len(info.dielectrics) == 1
    assert info.dielectrics[0].thickness_um == pytest.approx(800.0)
    assert info.dielectrics[0].epsilon_r == pytest.approx(4.4)
    assert info.dielectrics[0].conductivity_s_per_m == pytest.approx(0.0)
    assert info.dielectrics[0].z_bottom_um == pytest.approx(0.0)
    assert info.dielectrics[0].z_top_um == pytest.approx(800.0)

    metal1 = info.conductor_named("metal1")
    assert metal1 is not None
    assert metal1.thickness_um == pytest.approx(35.0)
    assert metal1.gds_layers == (1,)
    assert metal1.z_bottom_um == pytest.approx(800.0)
    assert metal1.z_top_um == pytest.approx(835.0)


def test_parse_proc_preserves_dielectric_conductivity_for_lossy_substrate(tmp_path: Path) -> None:
    proc_path = tmp_path / "lossy_substrate.proc"
    proc_path.write_text(
        "\n".join(
            [
                "define metal1 = l1t0",
                "layer 700 11.9 conductivity 10 S/m # substrate",
                "layer 0.3 3.9 # FOX",
                "conductor 0.18 0.0775 metal1",
            ]
        ),
        encoding="utf-8",
    )

    info = parse_proc_file(proc_path)

    assert len(info.dielectrics) == 2
    assert info.dielectrics[0].name == "substrate"
    assert info.dielectrics[0].thickness_um == pytest.approx(700.0)
    assert info.dielectrics[0].epsilon_r == pytest.approx(11.9)
    assert info.dielectrics[0].conductivity_s_per_m == pytest.approx(10.0)
    assert info.dielectrics[1].name == "FOX"
    assert info.dielectrics[1].conductivity_s_per_m == pytest.approx(0.0)


def test_parse_proc_applies_position_directive_before_conductor(tmp_path: Path) -> None:
    proc_path = tmp_path / "positioned.proc"
    proc_path.write_text(
        "\n".join(
            [
                "define metal1 = l1t0",
                "layer 10 4.0 # base",
                "layer 1.2 4.0 # dielectric_slot",
                "position -0.4",
                "conductor 0.4 0.02 metal1",
            ]
        ),
        encoding="utf-8",
    )

    info = parse_proc_file(proc_path)
    metal1 = info.conductor_named("metal1")

    assert metal1 is not None
    assert metal1.z_bottom_um == pytest.approx(10.8)
    assert metal1.z_top_um == pytest.approx(11.2)
