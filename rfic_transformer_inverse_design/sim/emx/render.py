"""Rendering helpers for EMX-exported GDS layouts."""

from __future__ import annotations

import json
from pathlib import Path

import gdstk
import numpy as np


def layout_preview_extent(
    gds_path: Path,
    manifest_path: Path | None = None,
) -> tuple[float, float, float, float]:
    """Return the padded preview extent `(left, right, bottom, top)` in layout units."""
    _render_cells, _label_positions, _port_boxes, bounds, _layer_draw_order = _load_render_data(gds_path, manifest_path)
    return _padded_extent(bounds)


def render_emx_layout_preview(
    gds_path: Path,
    out_path: Path,
    manifest_path: Path | None = None,
) -> Path:
    """Render a GDS preview using manifest metadata without drawing terminal boxes."""
    gds_path = Path(gds_path)
    out_path = Path(out_path)
    manifest_path = Path(manifest_path) if manifest_path is not None else None

    render_cells, label_positions, port_boxes, bounds, layer_draw_order = _load_render_data(gds_path, manifest_path)
    plt, _polygon, _rectangle = _plotting_modules()
    fig, ax = plt.subplots(figsize=(6, 6), dpi=180)
    _draw_layout(
        ax,
        render_cells,
        label_positions,
        port_boxes,
        _polygon,
        _rectangle,
        layer_draw_order=layer_draw_order,
        show_labels=False,
        show_port_boxes=False,
    )
    left, right, bottom, top = _padded_extent(bounds)
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_aspect("equal", adjustable="box")
    ax.set_facecolor("#f0f0f0")
    fig.patch.set_facecolor("#f0f0f0")
    ax.axis("off")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return out_path


def render_emx_port_debug_panels(
    gds_path: Path,
    out_path: Path,
    manifest_path: Path | None = None,
    margin_um: float = 12.0,
) -> Path:
    """Render one zoomed panel per EMX port label without terminal-box overlays."""
    gds_path = Path(gds_path)
    out_path = Path(out_path)
    manifest_path = Path(manifest_path) if manifest_path is not None else None

    render_cells, label_positions, port_boxes, _bounds, layer_draw_order = _load_render_data(gds_path, manifest_path)
    if not port_boxes:
        return render_emx_layout_preview(gds_path, out_path, manifest_path=manifest_path)

    plt, _polygon, _rectangle = _plotting_modules()
    n = len(port_boxes)
    cols = min(3, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.5 * cols, 4.5 * rows), dpi=180)
    axes = np.atleast_1d(axes).ravel()

    for ax, box in zip(axes, port_boxes):
        _draw_layout(
            ax,
            render_cells,
            label_positions,
            port_boxes,
            _polygon,
            _rectangle,
            layer_draw_order=layer_draw_order,
            show_labels=True,
            show_port_boxes=False,
        )
        cx, cy = box["center"]
        half_w = box["size"][0] * 0.5 + margin_um
        half_h = box["size"][1] * 0.5 + margin_um
        ax.set_xlim(cx - half_w, cx + half_w)
        ax.set_ylim(cy - half_h, cy + half_h)
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")
        ax.set_title(box["label"], fontsize=10)

    for ax in axes[len(port_boxes):]:
        ax.axis("off")

    fig.patch.set_facecolor("#f0f0f0")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return out_path


def render_emx_layer_panels(
    gds_path: Path,
    out_path: Path,
    manifest_path: Path | None = None,
    max_cols: int = 3,
) -> Path:
    """Render one panel per geometric layer for the selected top cell."""
    gds_path = Path(gds_path)
    out_path = Path(out_path)
    manifest_path = Path(manifest_path) if manifest_path is not None else None

    render_cells, label_positions, _port_boxes, bounds, layer_draw_order = _load_render_data(gds_path, manifest_path)
    drawable_polygons = _collect_drawable_polygons(render_cells)
    if not drawable_polygons:
        return render_emx_layout_preview(gds_path, out_path, manifest_path=manifest_path)

    if layer_draw_order is not None:
        draw_rank = {int(layer): idx for idx, layer in enumerate(layer_draw_order)}
    else:
        draw_rank = {}
    layers = sorted({layer for layer, _datatype, _points in drawable_polygons}, key=lambda layer: (draw_rank.get(layer, layer), layer))

    plt, polygon_cls, _rectangle_cls = _plotting_modules()
    cols = min(max(1, int(max_cols)), len(layers))
    rows = int(np.ceil(len(layers) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.5 * cols, 4.5 * rows), dpi=180)
    axes = np.atleast_1d(axes).ravel()

    min_x, min_y, max_x, max_y = bounds
    if min_x == float("inf"):
        min_x = min_y = -1.0
        max_x = max_y = 1.0
    pad_x = max((max_x - min_x) * 0.08, 1.0)
    pad_y = max((max_y - min_y) * 0.08, 1.0)

    for ax, layer in zip(axes, layers):
        for polygon_layer, datatype, points in drawable_polygons:
            if polygon_layer != layer:
                continue
            style = _polygon_style(layer=polygon_layer, datatype=datatype)
            ax.add_patch(
                polygon_cls(
                    points,
                    closed=True,
                    facecolor=style["facecolor"],
                    edgecolor=style["edgecolor"],
                    linewidth=style["linewidth"],
                    alpha=style["alpha"],
                )
            )
        ax.set_xlim(min_x - pad_x, max_x + pad_x)
        ax.set_ylim(min_y - pad_y, max_y + pad_y)
        ax.set_aspect("equal", adjustable="box")
        ax.set_facecolor("#f0f0f0")
        ax.axis("off")
        ax.set_title(f"Layer {layer}", fontsize=10)
        for text, origin in label_positions.items():
            ax.text(origin[0], origin[1], text, color="red", fontsize=7, ha="center", va="center", clip_on=True)

    for ax in axes[len(layers):]:
        ax.axis("off")

    fig.patch.set_facecolor("#f0f0f0")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return out_path


def _add_port_box(ax, center: tuple[float, float], width_um: float, height_um: float, edge: str, rectangle_cls) -> None:
    x, y = center
    ax.add_patch(
        rectangle_cls(
            (x - width_um * 0.5, y - height_um * 0.5),
            width_um,
            height_um,
            facecolor=edge,
            alpha=0.18,
            edgecolor=edge,
            linewidth=1.8,
        )
    )


def _padded_extent(bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = bounds
    if min_x == float("inf"):
        min_x = min_y = -1.0
        max_x = max_y = 1.0

    pad_x = max((max_x - min_x) * 0.08, 1.0)
    pad_y = max((max_y - min_y) * 0.08, 1.0)
    return (min_x - pad_x, max_x + pad_x, min_y - pad_y, max_y + pad_y)


def _load_render_data(gds_path: Path, manifest_path: Path | None):
    lib = gdstk.read_gds(str(gds_path))
    render_cells = list(lib.cells)
    manifest = None
    layer_draw_order: tuple[int, ...] | None = None
    if manifest_path is not None and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        top_cell_name = manifest.get("top_cell")
        if top_cell_name is not None:
            cell_by_name = {cell.name: cell for cell in lib.cells}
            top_cell = cell_by_name.get(str(top_cell_name))
            if top_cell is not None:
                # Flatten the selected top cell so referenced geometry is
                # rendered in top-level coordinates instead of raw child-cell
                # local coordinates.
                flattened_top = top_cell.copy(f"{top_cell.name}__render_flat")
                flattened_top.flatten(apply_repetitions=True)
                render_cells = [flattened_top]

    label_positions: dict[str, tuple[float, float]] = {}
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for cell in render_cells:
        for polygon in cell.polygons:
            points = np.asarray(polygon.points)
            if points.size == 0:
                continue
            min_x = min(min_x, float(points[:, 0].min()))
            min_y = min(min_y, float(points[:, 1].min()))
            max_x = max(max_x, float(points[:, 0].max()))
            max_y = max(max_y, float(points[:, 1].max()))
        for label in cell.labels:
            origin = (float(label.origin[0]), float(label.origin[1]))
            # Keep the first occurrence so duplicate Cadence pin labels do not
            # jitter around depending on polygon emission order.
            label_positions.setdefault(label.text, origin)
            min_x = min(min_x, origin[0])
            min_y = min(min_y, origin[1])
            max_x = max(max_x, origin[0])
            max_y = max(max_y, origin[1])

    port_boxes: list[dict[str, object]] = []
    if manifest is not None:
        raw_layer_draw_order = manifest.get("layer_draw_order")
        if raw_layer_draw_order is not None:
            layer_draw_order = tuple(int(layer) for layer in raw_layer_draw_order)
        palette = [
            "#e74c3c",
            "#2980b9",
            "#27ae60",
            "#8e44ad",
            "#d35400",
            "#16a085",
            "#c0392b",
            "#2c3e50",
        ]
        for port in manifest.get("ports", []):
            port_name = str(port.get("name", "P001"))
            try:
                palette_idx = max(0, int(port_name[1:]) - 1) % len(palette)
            except Exception:
                palette_idx = 0
            port_color = palette[palette_idx]
            width_um, height_um = [float(v) for v in port.get("internal_size_um", [1.0, 1.0])]
            signal_size = port.get("signal_internal_size_um") or [width_um, height_um]
            ground_size = port.get("ground_internal_size_um") or [width_um, height_um]
            if bool(port.get("internal_signal_labels", True)):
                for label_name in port.get("signal_labels", []):
                    if label_name in label_positions:
                        port_boxes.append({"label": label_name, "center": label_positions[label_name], "size": (float(signal_size[0]), float(signal_size[1])), "edge": port_color})
            if bool(port.get("internal_ground_labels", True)):
                for label_name in port.get("ground_labels", []):
                    if label_name in label_positions:
                        port_boxes.append({"label": label_name, "center": label_positions[label_name], "size": (float(ground_size[0]), float(ground_size[1])), "edge": port_color})

    if min_x == float("inf"):
        min_x = min_y = -1.0
        max_x = max_y = 1.0
    return render_cells, label_positions, port_boxes, (min_x, min_y, max_x, max_y), layer_draw_order


def _collect_drawable_polygons(render_cells) -> list[tuple[int, int, np.ndarray]]:
    drawable_polygons: list[tuple[int, int, np.ndarray]] = []
    for cell in render_cells:
        for polygon in cell.polygons:
            points = np.asarray(polygon.points)
            if points.size == 0:
                continue
            layer = int(polygon.layer)
            datatype = int(getattr(polygon, "datatype", 0))
            drawable_polygons.append((layer, datatype, points))
    return drawable_polygons


def _polygon_style(*, layer: int, datatype: int) -> dict[str, object]:
    layer_colors = {
        35: "#5dade2",
        36: "#f4a261",
        38: "#4f8fba",
        39: "#2e86c1",
        55: "#8e44ad",
        74: "#f4a261",
        84: "#5d6d7e",
        85: "#8e44ad",
        139: "#5dade2",
    }
    if datatype == 7:
        return {"facecolor": "#ff4fb3", "edgecolor": "#7a003f", "linewidth": 0.8, "alpha": 0.9}
    if datatype == 10:
        return {"facecolor": "#fb7185", "edgecolor": "#9f1239", "linewidth": 0.9, "alpha": 0.95}
    if datatype == 11:
        return {"facecolor": "#a78bfa", "edgecolor": "#5b21b6", "linewidth": 0.9, "alpha": 0.95}
    if datatype == 12:
        return {"facecolor": "#67e8f9", "edgecolor": "#155e75", "linewidth": 0.9, "alpha": 0.95}
    if datatype == 13:
        return {"facecolor": "#facc15", "edgecolor": "#854d0e", "linewidth": 0.9, "alpha": 0.95}
    return {
        "facecolor": layer_colors.get(layer, "#7f8c8d"),
        "edgecolor": "#2c3e50",
        "linewidth": 0.4,
        "alpha": 0.75,
    }


def _draw_layout(
    ax,
    render_cells,
    label_positions,
    port_boxes,
    polygon_cls,
    rectangle_cls,
    layer_draw_order: tuple[int, ...] | None = None,
    *,
    show_labels: bool = True,
    show_port_boxes: bool = False,
) -> None:
    drawable_polygons = _collect_drawable_polygons(render_cells)

    if layer_draw_order is not None:
        draw_rank = {int(layer): idx for idx, layer in enumerate(layer_draw_order)}
    else:
        draw_rank = {}

    # Draw lower layers first and higher layers last so top metal appears on top.
    for layer, _datatype, points in sorted(
        drawable_polygons,
        key=lambda item: (draw_rank.get(item[0], item[0]), item[1]),
    ):
        datatype = _datatype
        style = _polygon_style(layer=layer, datatype=datatype)
        ax.add_patch(
            polygon_cls(
                points,
                closed=True,
                facecolor=style["facecolor"],
                edgecolor=style["edgecolor"],
                linewidth=style["linewidth"],
                alpha=style["alpha"],
            )
        )
    if show_labels:
        for text, origin in label_positions.items():
            ax.text(origin[0], origin[1], text, color="red", fontsize=7, ha="center", va="center", clip_on=True)
    if show_port_boxes:
        for box in port_boxes:
            _add_port_box(
                ax,
                box["center"],
                box["size"][0],
                box["size"][1],
                edge=box["edge"],
                rectangle_cls=rectangle_cls,
            )


def _plotting_modules():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon, Rectangle

    return plt, Polygon, Rectangle
