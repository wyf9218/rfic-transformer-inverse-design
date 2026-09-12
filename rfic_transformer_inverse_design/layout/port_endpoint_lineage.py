"""Opt-in draft: bind a missed face to its own pre/post-grid edge lineage.

Not deployed. Does not widen the existing endpoint matcher or change polygons.
Requires a contemporaneous pre/post canonicalization trace; archived failures
without that trace are not silently repairable. Physical validation is absent.
"""
from copy import deepcopy
import math

POLICY = 'lineage_cross_binding_20260912_v2'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def bind_cross_centers(*, cell, ports, before, after, endpoint):
    """Return new port metadata only; delegate all construction to original v1.

    Existing v1 matches remain unchanged. For a missed face, demand a unique
    exact nominal face before canonicalization and the same edge afterward,
    unchanged vertex count, axial adjacency, exact intended snapped width,
    original one-grid axial limit, and labels inside that exact conductor.
    """
    import gdstk

    actual = [dict(layer=p.layer, datatype=p.datatype, points=p.points.tolist()) for p in cell.polygons]
    _require(actual == after, 'cell does not equal captured post-canonicalization polygons')
    _require(len(before) == len(after), 'polygon count changed; lineage unavailable')
    result, records = deepcopy(ports), []
    for port, newport in zip(ports, result):
        axis = 0 if port['side'] in ('left', 'right') else 1
        cross = 1 - axis
        sign = -1 if port['side'] in ('left', 'bottom') else 1
        endpoint_unit = endpoint._on_grid(port['ground_inner_edge_um']) + sign * endpoint.OVERLAP_UNITS
        nominal = float(port['nominal_terminal_um'])
        width = float(port['width_um'])
        center = float(port['cross_center_um'])
        _require(math.isfinite(width) and width > 0, 'invalid width')
        low, high = endpoint._units(center-width/2), endpoint._units(center+width/2)
        old_matches = []
        for pi, polygon in enumerate(after):
            if (polygon['layer'], polygon['datatype']) != tuple(port['drawing_pair']):
                continue
            pts = [[endpoint._on_grid(x), endpoint._on_grid(y)] for x, y in polygon['points']]
            for ei, a in enumerate(pts):
                b, prev, follow = pts[(ei+1)%len(pts)], pts[(ei-1)%len(pts)], pts[(ei+2)%len(pts)]
                ends = sorted((a[cross], b[cross]))
                if (a[axis] == b[axis] and abs(a[axis]-endpoint_unit) <= 1 and
                    abs(ends[0]-low) <= 1 and abs(ends[1]-high) <= 1 and
                    prev[cross] == a[cross] and follow[cross] == b[cross]):
                    old_matches.append((pi, ei))
        if len(old_matches) == 1:
            # V2: a match-window hit is not proof of full intended width.
            # Validate it too; do not reserve this guard only for fallback.
            pi, ei = old_matches[0]
            points = after[pi]['points']
            measured_width = abs(endpoint._on_grid(points[ei][cross]) -
                                 endpoint._on_grid(points[(ei+1)%len(points)][cross]))
            _require(measured_width == endpoint._units(width), 'canonicalized face not exact intended width')
            continue
        _require(not old_matches, 'ambiguous existing faces; refusing lineage fallback')
        raw_matches = []
        for pi, polygon in enumerate(before):
            if (polygon['layer'], polygon['datatype']) != tuple(port['drawing_pair']):
                continue
            pts = polygon['points']
            for ei, a in enumerate(pts):
                b, prev, follow = pts[(ei+1)%len(pts)], pts[(ei-1)%len(pts)], pts[(ei+2)%len(pts)]
                ends = sorted((a[cross], b[cross]))
                if (abs(a[axis]-nominal) < 1e-8 and abs(b[axis]-nominal) < 1e-8 and
                    abs(ends[0]-(center-width/2)) < 1e-8 and
                    abs(ends[1]-(center+width/2)) < 1e-8 and
                    abs(prev[cross]-a[cross]) < 1e-8 and abs(follow[cross]-b[cross]) < 1e-8):
                    raw_matches.append((pi, ei))
        _require(len(raw_matches) == 1, f"{port['port_id']}: unique exact pre-grid lineage unavailable")
        pi, ei = raw_matches[0]
        _require(len(before[pi]['points']) == len(after[pi]['points']), 'selected polygon vertex lineage changed')
        _require((before[pi]['layer'], before[pi]['datatype']) == (after[pi]['layer'], after[pi]['datatype']), 'layer lineage changed')
        pts = [[endpoint._on_grid(x), endpoint._on_grid(y)] for x, y in after[pi]['points']]
        a, b, prev, follow = pts[ei], pts[(ei+1)%len(pts)], pts[(ei-1)%len(pts)], pts[(ei+2)%len(pts)]
        _require(a[axis] == b[axis] and abs(a[axis]-endpoint_unit) <= 1, 'original axial endpoint guard failed')
        _require(prev[cross] == a[cross] and follow[cross] == b[cross], 'axial adjacency lineage failed')
        _require(abs(a[cross]-b[cross]) == endpoint._units(width), 'canonicalized face not exact intended width')
        _require((a[cross]-b[cross]) * (before[pi]['points'][ei][cross]-before[pi]['points'][(ei+1)%len(pts)][cross]) > 0, 'edge orientation lineage failed')
        labels = [x for x in cell.labels if x.text == port['port_id']]
        _require(bool(labels), 'port label unavailable for lineage binding')
        _require(all(gdstk.inside([x.origin], [cell.polygons[pi]])[0] for x in labels), 'label outside lineage conductor')
        actual_center = (a[cross]+b[cross]) * endpoint.GRID_UM/2
        newport['cross_center_um'] = actual_center
        records.append(dict(port_id=port['port_id'], polygon_index=pi, edge_index=ei,
            nominal_cross_center_um=center, canonical_face_center_um=actual_center,
            metadata_shift_um=actual_center-center, face_width_um=abs(a[cross]-b[cross])*endpoint.GRID_UM,
            polygon_mutation=False, original_match_tolerance_unchanged=True,
            source='SAME_RAW_FACE_TO_SAME_CANONICALIZED_EDGE_NOT_NEAREST_FACE'))
    return result, dict(policy=POLICY, rebound_ports=records, polygon_mutation=False,
                        cadence_executed=False, calibre_executed=False, emx_executed=False)


# Promoted from lineage_cross_binding_v1.py, with the new matched-branch
# exact-width guard documented above; the archived helper is unchanged:
# SHA256 01c0ae7c2eac2fca55baf0de58824c034625d84dcaceb9a8bb8772c6d49aa2b1.
# No global exporter patching: the caller explicitly supplies same-call lineage.
POLICY_V2 = 'shared_port_edges_lineage_20260912_v2'


def snapshot_polygons(cell):
    return [dict(layer=p.layer, datatype=p.datatype, points=p.points.tolist())
            for p in cell.polygons]


def construct_shared_port_edges_with_lineage(*, cell, ports, before,
                                             grid_um=0.005,
                                             require_port_labels=True):
    """Explicit opt-in pre-GDS construction; delegates all old v1 guards.

    The pre-grid snapshot must be captured in this same export invocation.
    This never modifies old candidate identities or reinterprets an archived
    failed result. Its output is construction evidence, not actual-GDS QA.
    """
    from . import port_endpoint_construction as endpoint

    _require(grid_um == endpoint.GRID_UM, 'original 5 nm grid required')
    _require(require_port_labels is True, 'lineage entry requires existing label guard')
    after = snapshot_polygons(cell)
    rebound, binding = bind_cross_centers(cell=cell, ports=ports, before=before,
                                        after=after, endpoint=endpoint)
    construction = endpoint.construct_shared_port_edges(
        cell=cell, ports=rebound, grid_um=grid_um,
        require_port_labels=require_port_labels)
    return dict(construction, schema=POLICY_V2,
                original_endpoint_policy=endpoint.POLICY,
                lineage_binding=binding, resolved_ports=rebound,
                evidence_class='PRE_CADENCE_SAME_EDGE_LINEAGE_CONSTRUCTION_NOT_ACTUAL_GDS_QA')
