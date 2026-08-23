from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
OBJ_DIR = ROOT / "outputs/hfss_aedt_diagnosis_profile_v70_emx_port_footprint_current/windows_return_v70_emx_port_footprint_interpolating/hfss_model_obj"
PORT_MANIFEST = ROOT / "outputs/hfss_aedt_diagnosis_profile_v70_emx_port_footprint_current/windows_return_v70_emx_port_footprint_interpolating/hfss_s8p_build_port_manifest.json"
OUT_DIR = ROOT / "reports/hfss_v70_emx_port_footprint_interpolating_validation_20260629/hfss_model_render"


COLORS = {
    "metal10": "#cf4c31",
    "metal9": "#256fba",
    "metal5": "#4f4f4f",
    "port_sheet": "#00b4dc",
    "dielectric": "#b4d2ff",
}


def parse_obj(path: Path) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    vertices: list[list[float]] = []
    faces: list[tuple[int, int, int]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = raw.strip().split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif parts[0] == "f" and len(parts) >= 4:
            idxs = []
            for token in parts[1:4]:
                value = int(token.split("/")[0])
                idxs.append(len(vertices) + value if value < 0 else value - 1)
            faces.append(tuple(idxs))
    return np.asarray(vertices, dtype=float), faces


def object_kind(path: Path) -> str:
    name = path.stem.lower()
    if name.startswith("metal10"):
        return "metal10"
    if name.startswith("metal9"):
        return "metal9"
    if name.startswith("metal5"):
        return "metal5"
    if name.startswith("port_sheet"):
        return "port_sheet"
    return "dielectric"


def load_meshes() -> list[dict]:
    meshes = []
    for path in sorted(OBJ_DIR.glob("*.obj")):
        kind = object_kind(path)
        verts, faces = parse_obj(path)
        if len(verts) == 0 or len(faces) == 0:
            continue
        meshes.append({
            "name": path.stem,
            "path": str(path),
            "kind": kind,
            "verts": verts,
            "faces": faces,
            "bbox": [
                float(verts[:, 0].min()), float(verts[:, 1].min()), float(verts[:, 2].min()),
                float(verts[:, 0].max()), float(verts[:, 1].max()), float(verts[:, 2].max()),
            ],
        })
    return meshes


def top_polys(mesh: dict) -> list[np.ndarray]:
    verts = mesh["verts"]
    faces = mesh["faces"]
    kind = mesh["kind"]
    polys = []
    zmax = float(verts[:, 2].max())
    for face in faces:
        tri = verts[list(face)]
        zspan = float(tri[:, 2].max() - tri[:, 2].min())
        if kind == "port_sheet" or (zspan < 1.0e-6 and float(tri[:, 2].mean()) >= zmax - 1.0e-6):
            polys.append(tri[:, :2])
    return polys


def all_3d_polys(mesh: dict) -> list[np.ndarray]:
    verts = mesh["verts"]
    return [verts[list(face)] for face in mesh["faces"]]


def port_centers() -> dict[str, tuple[float, float]]:
    data = json.loads(PORT_MANIFEST.read_text(encoding="utf-8"))
    centers = {}
    for port in data.get("ports", []):
        xy = port.get("signal_xyz_um") or []
        if len(xy) >= 2:
            centers[str(port.get("port_name"))] = (float(xy[0]), float(xy[1]))
    return centers


def axis_limits(meshes: list[dict], pad: float = 40.0) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    b = np.asarray([mesh["bbox"] for mesh in meshes], dtype=float)
    return (
        (float(b[:, 0].min() - pad), float(b[:, 3].max() + pad)),
        (float(b[:, 1].min() - pad), float(b[:, 4].max() + pad)),
        (float(b[:, 2].min() - 5.0), float(b[:, 5].max() + 5.0)),
    )


def draw_top(meshes: list[dict], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 8), dpi=220)
    for kind in ("metal5", "metal9", "metal10", "port_sheet"):
        polys = []
        for mesh in meshes:
            if mesh["kind"] == kind:
                polys.extend(top_polys(mesh))
        if not polys:
            continue
        alpha = 0.28 if kind == "port_sheet" else 0.72
        coll = PolyCollection(polys, facecolors=COLORS[kind], edgecolors="black", linewidths=0.35, alpha=alpha)
        ax.add_collection(coll)
    for name, (x, y) in port_centers().items():
        ax.scatter([x], [y], s=18, c="#00b4dc", edgecolors="black", linewidths=0.3, zorder=5)
        ax.text(x + 7, y + 7, name, fontsize=7, color="#0b4c5a", weight="bold")
    xlim, ylim, _ = axis_limits([m for m in meshes if m["kind"] != "dielectric"], pad=55)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.set_title("HFSS model render from AEDT OBJ export: top view, metals + lumped-port sheets")
    handles = [
        plt.Line2D([0], [0], color=COLORS["metal10"], lw=6, label="M10"),
        plt.Line2D([0], [0], color=COLORS["metal9"], lw=6, label="M9"),
        plt.Line2D([0], [0], color=COLORS["metal5"], lw=6, label="M5 shield/ground"),
        plt.Line2D([0], [0], color=COLORS["port_sheet"], lw=6, label="HFSS lumped-port sheets"),
    ]
    ax.legend(handles=handles, loc="upper right", framealpha=0.95)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def draw_3d(meshes: list[dict], out: Path, include_dielectric: bool = True) -> None:
    fig = plt.figure(figsize=(12, 8), dpi=220)
    ax = fig.add_subplot(111, projection="3d")
    for kind in ("dielectric", "metal5", "metal9", "metal10", "port_sheet"):
        if kind == "dielectric" and not include_dielectric:
            continue
        for mesh in meshes:
            if mesh["kind"] != kind:
                continue
            polys = all_3d_polys(mesh)
            if kind == "dielectric":
                alpha = 0.04
                lw = 0.05
            elif kind == "port_sheet":
                alpha = 0.38
                lw = 0.2
            else:
                alpha = 0.78
                lw = 0.12
            coll = Poly3DCollection(polys, facecolors=COLORS[kind], edgecolors="black", linewidths=lw, alpha=alpha)
            ax.add_collection3d(coll)
    limit_meshes = meshes if include_dielectric else [mesh for mesh in meshes if mesh["kind"] != "dielectric"]
    xlim, ylim, zlim = axis_limits(limit_meshes, pad=55)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_zlim(*zlim)
    ax.view_init(elev=28, azim=-52)
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.set_zlabel("z (um)")
    title = "HFSS model render from AEDT OBJ export: isometric view"
    if not include_dielectric:
        title += " (metals + port sheets only)"
    ax.set_title(title)
    ax.set_box_aspect((xlim[1] - xlim[0], ylim[1] - ylim[0], max(60.0, zlim[1] - zlim[0]) * 4.0))
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meshes = load_meshes()
    if not meshes:
        raise RuntimeError(f"No OBJ meshes found in {OBJ_DIR}")
    top_path = OUT_DIR / "26cb45d70af3cfd0_hfss_model_top_from_obj.png"
    iso_path = OUT_DIR / "26cb45d70af3cfd0_hfss_model_isometric_from_obj.png"
    iso_metals_path = OUT_DIR / "26cb45d70af3cfd0_hfss_model_isometric_metals_ports_from_obj.png"
    draw_top(meshes, top_path)
    draw_3d(meshes, iso_path, include_dielectric=True)
    draw_3d(meshes, iso_metals_path, include_dielectric=False)
    manifest = {
        "schema": "rfic_transformer_hfss_obj_render.v1",
        "source_obj_dir": str(OBJ_DIR),
        "source_port_manifest": str(PORT_MANIFEST),
        "mesh_count": len(meshes),
        "images": {
            "top": str(top_path),
            "isometric": str(iso_path),
            "isometric_metals_ports": str(iso_metals_path),
        },
        "status": "PASS" if top_path.is_file() and iso_path.is_file() and iso_metals_path.is_file() else "FAIL",
        "mesh_summary": [
            {"name": mesh["name"], "kind": mesh["kind"], "bbox": mesh["bbox"], "face_count": len(mesh["faces"])}
            for mesh in meshes
        ],
    }
    (OUT_DIR / "hfss_obj_render_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
