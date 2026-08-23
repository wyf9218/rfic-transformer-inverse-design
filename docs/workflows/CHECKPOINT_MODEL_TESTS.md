# Checkpoint Model Tests

## Cadence

The formal checkpoints are cumulative prefixes at 100k, 200k, ..., 1M. The
same trainer implementation and model contract are reused so learning-curve
changes can be attributed to data rather than changing code.

## Required Artifacts

- pinned training CSV and SHA-256;
- record and source-manifest hashes;
- strict physical-feature uniformity summary and plots;
- direct/Ridge baseline;
- direct neural-network architecture search;
- frozen-forward tandem result;
- complete held-out prediction table;
- physical-cell row-weighted, equal-cell, p95, and worst-cell errors;
- predicted-geometry feasibility audit;
- checkpoint manifest and completion marker.

## Split Contract

The default formal split holds out complete cells from a fixed four-dimensional
`Lp/Ls/Q/|K|` grid. Geometry identities cannot cross train, validation, and test
sets. The first checkpoint also pins a common external panel reused by later
checkpoints.

## Model Selection

Validation cells may choose epochs and predefined hyperparameters. Test cells
are evaluated after all weights are frozen. A model-success claim additionally
requires multiple seeds and real-EM closure.

## Optional Research Arms

- balanced MSE for imbalanced regression;
- Q versus Qp/Qs input ablation;
- frequency-sequence surrogate;
- multi-head tandem best-of-K candidates;
- conformal calibration on exchangeable held-out cells;
- local inverse refinement.

These arms never replace the fixed baseline merely because one point estimate
is lower. Comparisons require the same data SHA, split fingerprint, frozen
forward model, optimizer budget, and paired held-out rows.
