# Model Catalog

## Fixed Baselines

| Model | Mapping | Role |
|---|---|---|
| Ridge | physical features to geometry | linear reference and data sanity check |
| Direct MLP | physical features to geometry | deterministic supervised baseline |
| Forward MLP | geometry to physical features | frozen response surrogate |
| Tandem inverse | physical features to geometry through frozen forward | response-consistent inverse baseline |

## Tandem Objective

The inverse model predicts geometry, the frozen forward network reconstructs
`[Lp, Ls, Q, |K|]`, and the response error supplies the main gradient. Small
geometry-anchor and label-free topology terms may stabilize training but must be
ablation-tested.

## Multi-Head Best-of-K

The research baseline conditions a shared inverse network on a one-hot head
identity and generates multiple geometries for each target. Its loss combines:

- best candidate response consistency;
- a small all-candidate response term;
- optional paired-geometry anchoring;
- smooth candidate diversity repulsion;
- differentiable topology feasibility.

Multiple proxy-consistent candidates are not multiple valid transformers.
Selected candidates still require DRC and fresh EM simulation.

## Evaluation Metrics

- declared-range-normalized response RMSE and MAE;
- per-feature physical MAE for Lp, Ls, Q, and K;
- equal-cell and p95 held-out-cell error;
- geometry envelope and topology violations;
- head utilization and candidate diversity;
- parameter count, training time, and inference cost;
- new-EM target hit rate after DRC.

## Promotion Boundary

No model is promoted from a single seed, a random-row split, proxy consistency
alone, or a small interface smoke test. Formal review uses fixed data, fixed
OOD cells, multiple seeds, paired uncertainty analysis, and real-EM closure.
