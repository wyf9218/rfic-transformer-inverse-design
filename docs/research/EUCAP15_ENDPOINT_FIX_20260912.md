# Opt-in shared terminal-edge construction — development only

The current native exporter, observed on 12 September at 03:06 UTC, has SHA
`dfc23ee740e0cfc65ec4499c626d5ba2b9ccbac185e65417eea0306804f4e51a`.
It equals the research baseline before this patch and already contains commit
2829aef's vertical-bar integer-anchor change. That earlier implementation is
reused, not duplicated. Its independent polygon-run rounding still permits a
terminal edge to differ from the snapped ground-frame reference by one grid.
The original six failed GDS cases retain their original status and identities.
Their historical audit records bind the runtime path but do not pin this
exporter file, so the current observation is not backfilled as proof of the
exporter's exact bytes at those earlier executions.

## New construction version

```python
export_transformer_layout(
    geometry, run_config, new_output_directory,
    port_endpoint_policy="shared_port_edges_20260912_v1",
)
```

The default remains `legacy`; existing evaluator calls do **not** automatically
enable this policy. The sole native owner must bind the new two source files
and explicitly pass the keyword in its isolated new-version entry point.
No shared native source, old128, old64, candidate, target, Q or result is changed.

After normal polygon canonicalization, the new constructor reuses the actual
snapped frame edges, using exactly 2000 integer units for the existing 10 um
overlap on the unchanged 0.005 um grid. It identifies exactly one full-width
terminal face on the declared metal layer. Only the two axial endpoint
coordinates can move, by at most one grid unit. Other vertices, polygons,
layers and labels are unchanged. Ambiguous/missing faces, off-grid inputs,
larger displacements, changed edge directions or excessive area change fail
before any cell mutation. The existing 0.5% construction area guard remains.

This is a new geometry-construction intervention, not an audit-tolerance fix,
not a neural training update, and not a retrospective change to failed results.
All existing GDS identity, foundry, DRC, SRF and physical acceptance gates remain
authoritative. New-version construction evidence is saved in the source audit.

## Executed local evidence

- Twelve new, focused software regressions passed in 0.94 seconds; external
  subprocesses were forbidden. The historical test suites were not rerun.
- Six exact SHA-bound original Cadence GDS files were read into memory and
  locally replayed under the new construction rule. Exactly nine previously
  mismatched faces changed: five horizontal signal ends and four auxiliary
  ends in one case. Seven polygons changed in total. Each change is one grid;
  a second application makes no changes. The replay writes JSON vertices, not GDS.
- The original GDS and supporting sources remained byte-identical. No Cadence,
  Calibre, EMX, training, gate resubmission or production admission occurred.
- The first replay attempt selected the direct-export GDS instead of the
  recorded Cadence streamout, failed its SHA guard before construction, and is
  retained. The next attempt used the exact streamout paths and passed.

Private evidence: `reports/eucap15ghz_20260908T220300Z/endpoint_fix_20260912_v1/`.
Successful local replay receipt SHA:
`7cd15af4320548b514ed3585035e384e0ed859c9376563230fff75647a408840`.

**Status: LOCAL_POLYGON_REPLAY_PASS; NATIVE_INTEGRATION_NOT_EXECUTED;
CADENCE/DRC/FRESH_EMX_VALIDATION=NOT_RUN.**
The local replay is not a manufacturing-pass claim or proof of physical
accuracy improvement. Deployment and a new candidate's full native chain are
still required; old failures must not be replaced or reclassified.
