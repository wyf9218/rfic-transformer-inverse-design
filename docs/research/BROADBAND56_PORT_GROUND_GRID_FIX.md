# Port/Ground Grid Construction Fix

## Scope

The foundry-enabled eight-terminal exporter now constructs each auxiliary
bar's axial endpoints from snapped ground-reference coordinates plus/minus
the existing 2,000 manufacturing-grid-unit overlap. It no longer lets an
independently rounded full bar height determine the positive endpoint.
The generic polygon canonicalizer, metric producer, metric consumer,
foundry audit, DRC rules and physical contract are unchanged.

Evidence lineage: foundry repair `95d3ff91f86a3a47c68c57612f7a0b6f3a664d95`,
CPU policy `6b18b7daf42eb36b19c342c138cd546423357736`, actual-GDS metrics
`b6826a3be2af2a251aad83c268b66a4876786060`.
The existing Golden/initial-pilot CPU startup thresholds remain 1.10/1.10;
this geometry patch changes no resource gate.

## Private Real-Geometry Preflight

The historical GDS remains a rejected negative fixture: six verified
relationships, two top overlaps of 9.995 um, maximum error 0.005 um.
Its failure classification is
`REAL_GDS_FAILED_TWO_PORT_GROUND_OVERLAPS_SHORT_BY_ONE_GRID_UNIT`.

The same frozen geometry/configuration exported to a new directory gives
eight verified relationships, P005/P007 overlap 10.000 um and zero maximum
error. Exact polygon comparison shows only two bar polygons changed, each
positive endpoint by one grid unit; the other six relationship records,
all other polygons and all labels are unchanged. The actual-GDS foundry
audit, metric-schema validation, independent via/landing audit and no-op
Calibre-preflight consumer pass. No off-grid coordinates are present.

Forensic JSON SHA-256:
`f492cb51e566837bb98a0521e4de8b76afc0856732e071e8707a11f83ba64b76`.
Corrected direct-GDS SHA-256:
`2dc79d4a1c0732598af8ebf80d7825cd985ce54f2983f641bb3a8a24abbedaee`.
Raw GDS and private process/configuration files are not published.

This is direct-export geometry evidence, **not** Cadence execution,
Calibre DRC, fresh EMX, Golden PASS, or accepted campaign data. A new
exact-SHA authorization is required before launching the corrected runtime.
The failed historical stage must never be overwritten or retroactively
accepted. Neural-network training remains unauthorized.

## Regression Entry Points

- `tests/test_port_ground_grid_construction.py`: shared endpoint rule,
  signed/tie snapping, one-grid shortening and off-grid negative cases.
- `tests/test_port_ground_metrics.py`: actual polygon measurement,
  aggregate derivation, independent via gate and no-op consumer.
- `tests/test_run_broadband56_v2_calibre_batch.py`: downstream contract.
- `tests/test_transformer_layout.py`: existing exporter coverage.
- `tests/*broadband56*.py`: campaign integration coverage.

The first two geometry test modules forbid external process launches.
