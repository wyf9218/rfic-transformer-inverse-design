# Controlled 64 proposal trial recorded costs

This increment projects previously saved cost metadata. It adds no solver starts, physical results, model fitting or qualification admissions.

| Native solver only | Directed | Geometry DOE |
| --- | ---: | ---: |
| Actual starts and extracted responses | 16 | 16 |
| Sum of solver wall time in seconds | 1103.26 | 1262.35 |
| Sum of solver reported CPU time in seconds | 2220.28 | 2540.88 |

These are sums of native solver footers, not end-to-end experiment duration. CPU time is the reported field, not an estimate obtained by multiplying wall time by threads. Wrapper and Cadence timings overlap other costs and must not be added without an explicit model.

All 64 original proposals are retained in REQUEST_COSTS. The 37 observed Cadence records include five failures before a native solver start; their elapsed subtotal is 295.369427 s. Restricting Cadence to the 32 started candidates gives 248.282952 s and excludes those failures. Neither subtotal is complete pipeline cost.

GDS-audit and Calibre elapsed times, CPU user/system decomposition and per-candidate allocated storage were not recorded completely. JSON null and CSV empty cells mean unavailable, not zero. The CSV contains saved raw cost values, not recalculated physical errors.

The completed equal-start comparison remains descriptive: directed/DOE train-eligible counts are 1/4, both add zero occupied cells to the frozen 159-cell baseline. The directed train-eligible case is from exploration. The predeclared first-four-qualified-train comparison is NOT_REACHED. No general sampling advantage or full-budget cost advantage is established.

SUMMARY contains exact private evidence paths relative to the workspace and SHA-256 values. REQUEST_COSTS retains original IDs, order, state and source-row identity, but omits raw geometry, private tool paths and raw process logs. The immutable earlier postbudget summary is not overwritten by this supplement.
