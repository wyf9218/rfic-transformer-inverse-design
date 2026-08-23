from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys

import gdstk


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_emx_hfss_calibration_structures.py"
    spec = importlib.util.spec_from_file_location("build_emx_hfss_calibration_structures_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_builds_calibration_structure_manifest_and_assets(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "calibration"

    assert module.main(["--out-dir", str(out_dir)]) == 0

    manifest = json.loads((out_dir / "calibration_structures_manifest.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in manifest["structures"]}
    assert names == {
        "m9_straight_line",
        "m10_straight_line",
        "m9_single_inductor",
        "m10_single_inductor",
        "simple_2port_transformer",
    }
    assert manifest["gate"]["target_percent_error"] == 10.0
    for item in manifest["structures"]:
        assert Path(item["gds"]).is_file()
        assert Path(item["preview"]).is_file()
        assert Path(item["port_map"]).is_file()


def test_port_labels_land_on_draw_layer_conductors(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "calibration"

    assert module.main(["--out-dir", str(out_dir)]) == 0
    manifest = json.loads((out_dir / "calibration_structures_manifest.json").read_text(encoding="utf-8"))
    draw_layer_for_pin = {
        (135, 0): (35, 0),
        (139, 0): (39, 60),
        (126, 0): (74, 0),
    }
    for item in manifest["structures"]:
        lib = gdstk.read_gds(item["gds"])
        top = lib.top_level()[0]
        ports = json.loads(Path(item["port_map"]).read_text(encoding="utf-8"))["ports"]
        polygons = top.get_polygons()
        for port in ports:
            signal_layer = (port["signal_layer"]["layer"], port["signal_layer"]["datatype"])
            signal_draw = draw_layer_for_pin[signal_layer]
            ground_draw = (35, 0)
            assert _point_covered_by_layer(polygons, port["signal_xy_um"], signal_draw), (item["name"], port["name"])
            assert _point_covered_by_layer(polygons, port["ground_xy_um"], ground_draw), (item["name"], port["ground"])


def test_ground_pads_are_tied_to_m5_frame(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "calibration"

    assert module.main(["--out-dir", str(out_dir)]) == 0
    manifest = json.loads((out_dir / "calibration_structures_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["structures"]:
        lib = gdstk.read_gds(item["gds"])
        top = lib.top_level()[0]
        ports = json.loads(Path(item["port_map"]).read_text(encoding="utf-8"))["ports"]
        m5_polygons = [p for p in top.get_polygons() if (int(p.layer), int(p.datatype)) == (35, 0)]
        bbox = _layer_bbox(m5_polygons)
        for port in ports:
            gx, gy = [float(v) for v in port["ground_xy_um"]]
            assert _has_axis_aligned_tie_to_frame(m5_polygons, (gx, gy), bbox), (item["name"], port["ground"])


def _point_covered_by_layer(polygons, xy, layer):
    x, y = [float(v) for v in xy]
    for polygon in polygons:
        if (int(polygon.layer), int(polygon.datatype)) != layer:
            continue
        xs = [float(px) for px, _ in polygon.points]
        ys = [float(py) for _, py in polygon.points]
        if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys):
            return True
    return False


def _layer_bbox(polygons):
    xs = []
    ys = []
    for polygon in polygons:
        xs.extend(float(px) for px, _ in polygon.points)
        ys.extend(float(py) for _, py in polygon.points)
    return (min(xs), min(ys), max(xs), max(ys))


def _has_axis_aligned_tie_to_frame(polygons, xy, bbox):
    gx, gy = xy
    x0, y0, x1, y1 = bbox
    for polygon in polygons:
        xs = [float(px) for px, _ in polygon.points]
        ys = [float(py) for _, py in polygon.points]
        px0, py0, px1, py1 = min(xs), min(ys), max(xs), max(ys)
        covers_ground = px0 <= gx <= px1 and py0 <= gy <= py1
        reaches_frame = px0 <= x0 or px1 >= x1 or py0 <= y0 or py1 >= y1
        if covers_ground and reaches_frame:
            return True
    return False
