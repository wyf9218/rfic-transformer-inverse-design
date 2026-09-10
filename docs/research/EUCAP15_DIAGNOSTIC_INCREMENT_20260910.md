# 15 GHz development diagnostic increment — 10 September 2026

This is an incremental explanation of the frozen development128 experiment,
not a new model ranking, FINAL model, completed100K dataset, or final10K test.
The original targets, weights, Q preselection, tolerances and failed candidates
are unchanged. No model was retrained and no original EMX result was rerun.

## Physical frame and new diagnostic findings

The accepted source observation remains 14:30:19 UTC: 126 published terminals
out of128 original requests. These comprise48 strict-valid,37 half-SRF-invalid,
6 GDS failures and35 original analytical failures. Requests000125/q10 and
000126/q15 had no terminal at that observation. The native owner's one later
connection attempt failed; current completion of those two is unknown.
The existing40 joint hits mean40/128 observed coverage and40/48 conditional
strict-valid performance, not83.33% performance on the original frame.

### Analytical and actual-layout failures

The new41-case diagnostic preserves saved continuous decoded geometry, grid
geometry, native generator parameters where available, actual-layout audits,
and missing evidence separately.

| Subset | New diagnosis from existing artifacts | Limit |
| --- | --- | --- |
| 35 analytical failures | 21 primary-feed-extension deficits;14 secondary-feed-extension deficits | Saved continuous and grid analytical flags already fail; not an SRF failure or solely a grid-rounding effect |
| 6 GDS failures | 9 of48 audited ports have saved post-snap overlap/edge mismatches;maximum0.005um=5nm in each case | Observation from original audit, not a newly rerun GDS measurement |
| Top-level via flag in those6 | Upstream port-contract failure prevented that downstream check | Independent foundry via/landing audit passes6/6;do not report6 observed via defects |
| Structural identity | Native10D parameter rows equal frozen grid vectors;direct/Cadence structural matching true6/6 | Native parameters are not an independently recovered10D geometry from GDS |

Original pre-decoder neural logits were not saved and remain unavailable.
`continuous_geometry` is decoded physical geometry, not those logits.
No missing intermediate output has been synthesized or retrospectively claimed.

### K tail and joint misses

The top10 below are ranked only within the48 strict-valid requests by absolute
EMX-minus-target K error. K is dimensionless and uses the frozen absolute-value
definition. Original signed K is positive in these10 cases.

| Request suffix | Target K | Frozen proxy K | EMX K | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| 000006 | 0.774318 | 0.464337 | 0.457716 | 0.316603 |
| 000002 | 0.742276 | 0.498006 | 0.482632 | 0.259644 |
| 000088 | 0.745315 | 0.512966 | 0.504532 | 0.240784 |
| 000071 | 0.803510 | 0.647201 | 0.648025 | 0.155484 |
| 000029 | 0.700786 | 0.613387 | 0.616027 | 0.084759 |
| 000111 | 0.666909 | 0.589526 | 0.595163 | 0.071746 |
| 000008 | 0.670231 | 0.605918 | 0.600795 | 0.069436 |
| 000087 | 0.716053 | 0.643259 | 0.649658 | 0.066394 |
| 000084 | 0.523646 | 0.526754 | 0.543865 | 0.020219 |
| 000110 | 0.449842 | 0.427677 | 0.431482 | 0.018360 |

Displayed numbers are rounded; private case tables preserve original precision.
These10 account for83.878942% of the strict48 absolute K error sum and99.103667%
of its squared K error sum. In9/10, the larger absolute component is the
proxy-to-target gap, rather than the EMX-to-proxy gap. The signed identity is
`EMX-target=(proxy-target)+(EMX-proxy)`; absolute components can cancel and are
not causal attribution fractions.

The frozen train-only normalizer has K range
`[0.20000965983613986,0.5806891255192034]` over3801 gradient-training geometries.
Eight strict-valid requests exceed that upper marginal limit, and those exact
eight are the joint misses:6 K-only,1 Ls+K,1 all-four. This is descriptive
association, not proof that more data would fix them, proof of infeasibility,
or a density estimate. No validation/test label array was read for this check.

The existing Type7 P95 is0.21092888930289022: with48 values,
`h=(48-1)*0.95=44.65` in zero-based indexing. The45th and46th sorted errors are
0.15548436919090936(request000071) and0.24078363090164934(request000088),
weighted approximately0.35/0.65. P95 is neither a confidence interval nor a
maximum-error guarantee.

### Half-SRF invalidity

All37 previously labelled invalid cases retain passing finite, descriptor,
passivity and reciprocity flags. Existing56-point feature CSVs support the
saved reactance-zero brackets:34 fail the half-SRF rule for both windings,
3 for the secondary only. All37 secondary SRF estimates are below30GHz.
This is label-validity failure at15GHz, not evidence that37 solvers failed.
No S4P re-extraction, interpolation of a new spectrum or rule change was made.

## Acquisition provenance increment

A new120-row metadata join links the already accepted proposals, admission
ledger and cost records. The original decision used1804 train members and seeds
2026090901/02/03/04; it is not renamed to the retrospective3801-member baseline.
The existing20 newtrain members still yield159→163 occupied cells out of512,
and0 cells cross the sparse threshold5. Those figures are descriptive.

The42 directed records did not originally save a predicted-bin field; their
later bin derived from the frozen proxy is kept in a separate column. Missing
original bins are not backfilled as if they were part of the initial decision.
DOE and exploration have no invented target-error field. Reservation counts,
full end-to-end time and remote storage remain unknown wherever not recorded.

An initial metadata join stopped on the original-versus-derived-bin mismatch;
its failure is preserved. The corrected join has its own output directory.
A subsequent code-only no-clobber guard repair prevents a mistaken restart
from adding a failure marker to an already completed directory; two new
temporary-directory guard checks passed without rerunning the scientific join.

## New bounded comparison: candidates prepared, physics not submitted

One real CPU preparation loaded the unchanged current6329 forward/inverse
checkpoints and froze64 proposals:25 sparse-cell requests scanned over11 Q
values,7 geometric exploration proposals, and32 ordinary geometry-LHS controls.
The two studies use different model-ID aliases; equality here refers to the
same pair receipt `9d69d7ebfc660d6e567cbed773f7e981fc82fd410e2ed46bf079b1439394d1fe`,
forward SHA `23034fcd736608f7e81277d4dc555bcd6ebc89cfb36f5bdc7fde72e3bdeb1b9d`
and inverse SHA `8106f2f8d0274fc7985def30dbf6c515b94cb70daa3e72b362f5655767c07ab9`,
not a model name or architecture-only inference.
There are275 logical Q candidates and25 preselected Q values, not275 EMX solves.
Coverage-directed analytical PASS/FAIL counts are22/10;DOE counts are28/4.
No known-geometry duplicate was found against the frozen exclusion lists, which
are not an all-history independence certificate. All64 slots remain in the
ledger, with no Q fallback or failure replacement.

This new round freezes the original3801-train/159-cell baseline for both
decision and evaluation. The previous20 newtrain geometries are exclusions,
not an unreported model retraining or within-round feedback update. The bin
contract remains8×8×8 overLp/Ls0.5–2nH and|K|0.2–0.85, sparse threshold5;
weights are `max(5-N,0)` and exploration is7/32. Seeds are2026091011–14,
with geometry split seed17. All actual gain/cost result fields remain null.

Predeclared upper limits are32 proposals and16 actual solver starts per arm,
32 starts total,6 hours and2GiB incremental native storage. Native concurrency
must be admitted by the sole owner and cannot exceed2. These are proposed
hard stops, not an already installed scheduler or measured resource approval.
Equal-start comparisons retain failures;equal-qualified-train comparisons use
the predeclared firstK=4 members in frozen arm order, report their costs, and
remain NOT_REACHED if either arm cannot attainK within the caps.

Preparation ran once in1.325502 seconds and produced about3.08MB before final
receipt/index overhead. Local available disk at preparation was450991337472B;
this is not a MARS capacity measurement. The native-owner connection failed
and the new frame is not supported by the existing hard-bound owner runners without an
owner-side adapter. Thus `NATIVE_STATUS=NOT_SUBMITTED`,
`AUTOMATIC_TRIGGER=NOT_INSTALLED_FOR_NEW_FRAME`, and both equal-budget and
equal-qualified-count physical comparisons are NOT_RUN. No causal coverage
advantage is claimed.

## Evidence and author handoff

Private source artifacts are under the existing study root
`reports/eucap15ghz_20260908T220300Z/`:

| Artifact | SHA-256 |
| --- | --- |
| `development128_failure_diagnosis_20260910_v1/RECEIPT.json` | `ede6c3dc85c789b4dfa978d6e2b9cefefed9ca57be3f200000e54d814fe0e3fb` |
| `development128_k_tail_diagnosis_20260910_v1/run_v1/DIAGNOSIS.json` | `54b21e77f965088dc51202c31c70bd8d4bf121ee8e6b4b0fbc0d7ac8a6e3c57b` |
| `acquisition_round_trace_20260910_v1/run_v2/RECEIPT.json` | `7932bb1345e6551eca37294197dad1a383cc3ea189eab16a1b5a74d0208ddd83` |
| `acquisition_controlled_budget_20260910_v1/EXECUTION_RECEIPT.json` | `a2e0d4f7cd9205367092ef9b641f6c752cd5cd11b8f88126956a748e4a802d3b` |
| `acquisition_controlled_budget_20260910_v1/run_v1/PREPARATION_RECEIPT.json` | `fa2caeb03fb99b4e2fb038646310f91f8948c0fa44f90020e29d719122e7f926` |

Each private package includes case-level tables, exact artifact bindings and
checksums. Private geometries, foundry artifacts and weights are not included
in this public note. No paper figure was generated: authors can use the real
tables to prepare and verify tail-error, failure-funnel and coverage figures.
Keep the original128 denominator visible, separate strict-only errors, and
label model-to-EMX residual decomposition as descriptive.
