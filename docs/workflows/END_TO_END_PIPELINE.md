# End-to-End Pipeline

## 1. Configure

Start from a file in `configs/`. Supply all commercial-tool and foundry paths
through environment variables or a local untracked configuration. Never commit
licenses or PDK assets.

## 2. Generate Candidate Geometry

Candidate builders create independent ten-dimensional geometries, validate
angles, clearances, conductor continuity, terminal spans, feed support, and
ground-ring separation, then write immutable geometry identities.

## 3. Run EMX

The dataset runner creates Cadence-compatible layout, verifies port metadata,
runs EMX, and exports Touchstone. Parallel workers write independent shard
records so interrupted campaigns can resume without relabeling completed rows.

## 4. Audit Raw Results

Before acceptance, each result must satisfy:

- nonempty and parseable Touchstone;
- expected port count and 50-ohm reference;
- exact frequency start, stop, spacing, and point count;
- finite S-parameters and acceptable reciprocity/passivity diagnostics;
- traceable geometry and source hashes;
- no duplicate independent geometry.

## 5. Extract Physical Features

The common transformer contract extracts `Lp`, `Ls`, `Q=min(Qp,Qs)`, and
`|K|` at the registered target frequency. Broadband S-parameters remain the
source of truth and are retained for resonance and frequency-domain audits.

## 6. Balance And Checkpoint

Accepted rows are selected into exact cumulative 100k prefixes. Uniformity is
audited in one-dimensional marginals, all pairwise projections, and a fixed
four-dimensional grid. A model test is still recorded when strict uniformity
fails, but the checkpoint cannot be called formally balanced.

## 7. Train And Evaluate

Forward, direct inverse, and tandem models use pinned tables and isolated
physical cells. Test rows never update weights, select epochs, or set
acceptance thresholds.

## 8. Close The Loop

Inverse candidates are only proposals. They pass production geometry audit,
foundry DRC, fresh EMX, and sampled HFSS correlation before any accuracy claim.
