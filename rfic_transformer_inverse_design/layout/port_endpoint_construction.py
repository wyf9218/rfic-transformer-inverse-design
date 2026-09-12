"""Opt-in development construction of shared, integer-grid terminal edges.

This acts on newly constructed polygons, before writing GDS.  It does not
modify an audit tolerance or repair an archived physical result.  It only
resolves a one-grid endpoint displacement; ambiguous topology fails closed.
"""
from __future__ import annotations

import math

POLICY = "shared_port_edges_20260912_v1"
GRID_UM = 0.005
OVERLAP_UNITS = 2000


def _units(value: float) -> int:
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError("finite physical coordinate required")
    return round(float(value) / GRID_UM)


def _on_grid(value: float) -> int:
    units = _units(value)
    if abs(float(value) - units * GRID_UM) > 1e-9:
        raise ValueError("construction input must already be on the 5 nm grid")
    return units


def _area2(points):
    return abs(sum(a[0]*b[1] - b[0]*a[1]
                   for a, b in zip(points, points[1:] + points[:1])))


def _directions(points):
    result = []
    for a, b in zip(points, points[1:] + points[:1]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        if (dx == 0 and dy == 0) or (dx and dy and abs(dx) != abs(dy)):
            raise ValueError("endpoint construction introduced a non-H/V/45 or zero edge")
        result.append((0 if dx == 0 else 1 if dx > 0 else -1,
                       0 if dy == 0 else 1 if dy > 0 else -1))
    return result


def construct_shared_port_edges(*, cell, ports, grid_um=GRID_UM, require_port_labels=False):
    """Constrain eight axial end faces to their actual snapped frame anchors.

    Each port declares its drawing pair, side, snapped ground inner edge,
    nominal terminal, cross-centre, and trace width.  A unique full-width H/V
    face must be within one grid unit.  Only its two axial coordinates may
    change.  All plans are validated before any cell mutation.  Coordinates,
    layers, labels, other polygons and non-terminal vertices are retained.
    """
    import gdstk

    if grid_um != GRID_UM or len(ports) != 8 or len({p['port_id'] for p in ports}) != 8:
        raise ValueError("exact eight-port, 5 nm development contract required")
    originals = list(cell.polygons)
    points = [[[ _on_grid(p[0]), _on_grid(p[1])] for p in poly.points] for poly in originals]
    planned = [[p.copy() for p in polygon] for polygon in points]
    records, claimed = [], set()
    for port in ports:
        side = port['side']
        if side not in ('left', 'right', 'top', 'bottom'):
            raise ValueError("unknown terminal side")
        axis = 0 if side in ('left', 'right') else 1
        cross_axis = 1-axis
        sign = -1 if side in ('left', 'bottom') else 1
        anchor = _on_grid(port['ground_inner_edge_um'])
        endpoint = anchor + sign*OVERLAP_UNITS
        nominal = _units(port['nominal_terminal_um'])
        if abs(endpoint-nominal) > 1:
            raise ValueError("nominal terminal and shared ground anchor differ by more than one grid")
        width = float(port['width_um'])
        if not math.isfinite(width) or width <= 0:
            raise ValueError("positive trace width required")
        cross = float(port['cross_center_um'])
        low, high = _units(cross-width/2), _units(cross+width/2)
        matches = []
        for pi, (poly, polygon) in enumerate(zip(originals, points)):
            if (poly.layer, poly.datatype) != tuple(port['drawing_pair']):
                continue
            for ei, a in enumerate(polygon):
                b = polygon[(ei+1) % len(polygon)]
                if a[axis] != b[axis] or abs(a[axis]-endpoint) > 1:
                    continue
                ends = sorted((a[cross_axis], b[cross_axis]))
                if abs(ends[0]-low) <= 1 and abs(ends[1]-high) <= 1:
                    # The two adjacent edges must be axial runs, not diagonal
                    # junctions; changing an end face cannot change topology.
                    prev = polygon[(ei-1) % len(polygon)]
                    after = polygon[(ei+2) % len(polygon)]
                    if prev[cross_axis] != a[cross_axis] or after[cross_axis] != b[cross_axis]:
                        continue
                    matches.append((pi, ei, a[axis]))
        if len(matches) != 1:
            raise ValueError(f"{port['port_id']}: expected one full-width terminal face, got {len(matches)}")
        pi, ei, before = matches[0]
        if (pi, ei) in claimed:
            raise ValueError("two ports claim the same terminal face")
        claimed.add((pi, ei))
        planned[pi][ei][axis] = endpoint
        planned[pi][(ei+1) % len(planned[pi])][axis] = endpoint
        records.append(dict(port_id=port['port_id'], polygon_index=pi, edge_index=ei,
                            axis=axis, ground_anchor_grid_units=anchor,
                            endpoint_before_grid_units=before, endpoint_after_grid_units=endpoint,
                            delta_grid_units=endpoint-before, overlap_grid_units=OVERLAP_UNITS))
    changed = []
    for pi, (before, after) in enumerate(zip(points, planned)):
        if before == after:
            continue
        if _directions(before) != _directions(after):
            raise ValueError("endpoint construction changed edge topology")
        before_area = _area2(before)
        if not before_area or abs(_area2(after)-before_area)/before_area > .005:
            raise ValueError("endpoint construction exceeds existing 0.5% area-change guard")
        changed.append(pi)
    replacements = [gdstk.Polygon([(x*GRID_UM, y*GRID_UM) for x,y in planned[pi]],
                                 layer=originals[pi].layer, datatype=originals[pi].datatype)
                    for pi in changed]
    label_checks = []
    if require_port_labels:
        for port, record in zip(ports, records):
            labels = [label for label in cell.labels if label.text == port['port_id']]
            if not labels:
                raise ValueError(f"{port['port_id']}: terminal label missing")
            pi, axis = record['polygon_index'], record['axis']
            polygon = gdstk.Polygon([(x*GRID_UM, y*GRID_UM) for x,y in planned[pi]])
            sign = -1 if port['side'] in ('left','bottom') else 1
            for label in labels:
                coordinate = _on_grid(label.origin[axis])
                clearance = sign*(record['endpoint_after_grid_units']-coordinate)
                if clearance < 1 or not gdstk.inside([label.origin], [polygon])[0]:
                    raise ValueError(f"{port['port_id']}: label not inside its planned terminal conductor")
                label_checks.append(dict(port_id=port['port_id'],
                    label_layer=label.layer,label_datatype=label.texttype,
                    endpoint_inset_grid_units=clearance,inside_planned_conductor=True))
    # Preserve polygon order as well as untouched polygon objects.
    if changed:
        by_index = dict(zip(changed, replacements))
        cell.remove(*originals)
        cell.add(*(by_index.get(pi, poly) for pi, poly in enumerate(originals)))
    return dict(schema=POLICY, evidence_class="LOCAL_POLYGON_CONSTRUCTION_NOT_PHYSICAL_VALIDATION",
                grid_um=GRID_UM, changed_polygon_count=len(changed),
                changed_port_count=sum(r['delta_grid_units'] != 0 for r in records), ports=records,
                label_containment_checks=label_checks,
                cadence_executed=False, calibre_executed=False, emx_executed=False)
