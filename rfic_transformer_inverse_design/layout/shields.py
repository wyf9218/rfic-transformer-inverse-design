"""Shield geometry helper functions for transformer layout export."""

from __future__ import annotations

def _polygon_bbox(polygons: tuple[object, ...] | list[object]) -> tuple[float, float, float, float]:
    boxes = [poly.bounding_box() for poly in polygons if poly.bounding_box() is not None]
    if not boxes:
        raise ValueError("Cannot compute shield bbox because no conductor polygons were exported")
    min_x = min(float(box[0][0]) for box in boxes)
    min_y = min(float(box[0][1]) for box in boxes)
    max_x = max(float(box[1][0]) for box in boxes)
    max_y = max(float(box[1][1]) for box in boxes)
    return (min_x, min_y, max_x, max_y)


def _shield_inner_bbox(
    *,
    conductor_bbox: tuple[float, float, float, float],
    left_feed_x_um: float | None = None,
    right_feed_x_um: float | None = None,
    largest_coil_height_um: float | None = None,
    margin_um: float | None = None,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = conductor_bbox
    center_y = 0.5 * (min_y + max_y)
    inner_height_um = (
        (max_y - min_y)
        if largest_coil_height_um is None
        else float(largest_coil_height_um) + 2.0 * max(0.0, float(0.0 if margin_um is None else margin_um))
    )
    half_height_um = 0.5 * inner_height_um
    return (
        float(min_x if left_feed_x_um is None else left_feed_x_um),
        float(center_y - half_height_um),
        float(max_x if right_feed_x_um is None else right_feed_x_um),
        float(center_y + half_height_um),
    )


def _rectangular_ring(
    *,
    inner_bbox: tuple[float, float, float, float],
    width_um: float,
    layer: int,
    datatype: int,
) -> list[object]:
    import gdstk

    inner_min_x, inner_min_y, inner_max_x, inner_max_y = inner_bbox
    outer = gdstk.rectangle(
        (inner_min_x - width_um, inner_min_y - width_um),
        (inner_max_x + width_um, inner_max_y + width_um),
        layer=layer,
        datatype=datatype,
    )
    inner = gdstk.rectangle(
        (inner_min_x, inner_min_y),
        (inner_max_x, inner_max_y),
        layer=layer,
        datatype=datatype,
    )
    ring = gdstk.boolean([outer], [inner], "not", layer=layer, datatype=datatype)
    return list(ring if ring is not None else [outer])


def _shield_label_point(
    *,
    side: str,
    port_y_um: float,
    inner_bbox: tuple[float, float, float, float],
    width_um: float,
) -> tuple[float, float]:
    inner_min_x, _inner_min_y, inner_max_x, _inner_max_y = inner_bbox
    if side == "left_inner":
        return (inner_min_x, port_y_um)
    if side == "left_outer":
        return (inner_min_x - width_um, port_y_um)
    if side == "right_inner":
        return (inner_max_x, port_y_um)
    if side == "right_outer":
        return (inner_max_x + width_um, port_y_um)
    raise ValueError(f"Unsupported shield label side: {side}")


def _shield_label_point_below_signal(
    *,
    side: str,
    signal_point: tuple[float, float],
    inner_bbox: tuple[float, float, float, float],
    width_um: float,
    offset_um: float,
    inset_um: float = 0.0,
) -> tuple[float, float]:
    signal_x_um, signal_y_um = signal_point
    inner_min_x, inner_min_y, inner_max_x, inner_max_y = inner_bbox
    label_x_um = float(signal_x_um)
    label_y_um = float(signal_y_um) - float(offset_um)
    inward_um = max(0.0, float(inset_um))
    feed_anchor_dx_um = max(inward_um, 0.25 * float(width_um))
    if side == "left_outer":
        edge_x_um, _ = _shield_label_point(
            side=side,
            port_y_um=float(signal_y_um),
            inner_bbox=inner_bbox,
            width_um=width_um,
        )
        label_x_um = float(edge_x_um) + inward_um
        label_y_um = float(signal_y_um) - float(offset_um)
    elif side == "right_outer":
        edge_x_um, _ = _shield_label_point(
            side=side,
            port_y_um=float(signal_y_um),
            inner_bbox=inner_bbox,
            width_um=width_um,
        )
        label_x_um = float(edge_x_um) - inward_um
        label_y_um = float(signal_y_um) - float(offset_um)
    elif side == "left_near_feed":
        label_x_um = float(signal_x_um) - feed_anchor_dx_um
    elif side == "right_near_feed":
        label_x_um = float(signal_x_um) + feed_anchor_dx_um
    elif side == "top_outer":
        label_y_um = float(inner_max_y + float(width_um) - inward_um)
    elif side == "bottom_outer":
        label_y_um = float(inner_min_y - float(width_um) + inward_um)
    return (label_x_um, label_y_um)
