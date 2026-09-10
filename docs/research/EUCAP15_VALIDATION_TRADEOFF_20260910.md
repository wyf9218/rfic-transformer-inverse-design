# 15 GHz MLP capacity: descriptive validation tradeoffs

Status: DEVELOPMENT_CURRENT_SNAPSHOT; not FINAL or new inverse EMX accuracy.

## What this increment adds

Five structures and three seeds already completed 15 tandem pairs / 30 role runs,
with 538,099 primary updates. This package adds a fixed-span joint readout from
their saved validation aggregates; it does not retrain or reevaluate models.

All arms use source/train/validation/sealed-test counts 6329/3801/1269/1259,
seeds 17/29/43 and the frozen training/early-stop policy. Each shape has its own
trained forward; all inverses use the same frozen baseline forward as feedback
and evaluation ruler. This is not a comparison of five complete tandem systems.
Twenty-four roles stopped by validation patience and six by the update budget;
sufficient convergence is not established. Test has not been evaluated.

## Metric and compact result

For feature RMSEs r and fixed spans s = [2.5 nH, 2.5 nH, 20, 0.8],
each seed score is sqrt(mean over four features of (r/s)^2).
The shape score is sqrt(mean over three seeds of seed_score^2).
Lower is better. This is dimensionless fixed-span error, not target-relative
percentage error, and 1 minus this score is not accuracy.

| Hidden layers | Forward parameters | Equal-seed RMS | Sample SD of seed scores |
| --- | ---: | ---: | ---: |
| 2x256 | 69636 | 0.0090856882 | 0.0000720394 |
| 3x128 | 34948 | 0.0079710045 | 0.0000557014 |
| 3x256 | 135428 | 0.0077540077 | 0.0001518745 |
| 3x512 | 532996 | 0.0078944407 | 0.0001264874 |
| 5x256 | 267012 | 0.0082458964 | 0.0002216354 |

In this readout, 3x256 has the lowest observed forward score. The parameter/error
nondominated alternatives are 3x128 and 3x256. This is an empirical description,
not significance, universal depth/width superiority, or an automatic selection.
The equal-seed aggregation was operationalized after the tables existed, not
preregistered as a cross-shape winner rule. No model or frozen128 binding changed.

Each seed reuses the same 1269 validation geometries. 3807 seed-target evaluations
are not 3807 independent targets. Three-seed SD is not a confidence interval.
Timing in the full aggregate is measured role-training cost on the recorded
environment, not inference latency or a hardware-independent speed claim.

## Inverse failures and physical boundary

The inverse analytical failure totals are 44/49/45/37/16 of 3807 repeated-seed
evaluations for the table's five shapes respectively. All five shape-level
full-cohort inverse RMSEs remain null because at least one seed has failures.
Conditional surviving-row error is not substituted for the missing full-cohort
metric. Joint-hit counts retain the original denominator and remain SELF_PROXY
under one common frozen forward, not fresh EMX accuracy.

The original reference64 physical result is separately 17/64 joint hits with
58 S4P outputs (23 strict valid, 35 SRF-invalid), four GDS failures and two
analytic failures. It does not validate the new6329 inverse. Development128
closed physical output has not been received by this package. FINAL10000 and
the preselected100 full11Q audit have not run. No new native-state claim is made.

## Data-source qualification added in parallel

The data owner matched all48 Stage238 members to the exact frozen source queue.
All were recorded as base_space_filling / sobol_normalized_10d, seed20260828.
The queue has no parent/prototype/family fields. These remain UNKNOWN;
Sobol, unique geometry hashes, and a common seed do not prove family independence.
No members were added or changed. July exact index/table references were located,
but current-process/port/extractor compatibility and all-history counts remain
unverified. Do not add legacy source counts or infer nearest-neighbor parents.

## Author table / figure source and reproducibility

The [full aggregate JSON](eucap15_validation_tradeoff_20260910/VALIDATION_TRADEOFF_5ROW.json)
includes full precision, parameter counts, role costs, seed variation, retained
failure counts and null markers. The
[public state](eucap15_validation_tradeoff_20260910/RUN_STATE.json) records scope.

Suggested author figure: five structures on the x-axis, fixed-span forward score
on the y-axis; show the three individual seed observations from the private
PER_SEED_SCORES.csv. Label SD separately if used, and do not call it a confidence
interval. A second parameter-versus-score panel may show the observed tradeoff.
Caption: "15 GHz development validation on 1269 common held-out geometry hashes;
three seeds per structure. Fixed-span joint RMSE, with post-hoc equal-seed
aggregation. No test or fresh inverse EMX results are used; convergence and
parent-family independence are not established." No image is generated here.

The arithmetic QA independently used 60-digit Decimal on the original60 forward
feature RMSEs: 769 checks, 187 numeric comparisons; score difference at most
1.7869e-18. It did not call the reducer, models, raw predictions or native tools.

- Summary SHA-256: 6253c3a55aa626b523dff072d759b3e70cc85ac61626dd664452b01425eb7412
- Independent QA SHA-256: 8918f2889a134a2f06dd4c78b16dc6f5201c9ba70f11ba66dd78860807794879
- Owner lineage receipt SHA-256: 2c010aa6dbb437a043df94c4b96de1421c47a8b0165c696f36f85f239f33f885

The CLI is research/broadband56_nn/eucap15_validation_tradeoff.py, using existing
private TABLES_RECEIPT.json and DEVELOPMENT_PROTOCOL.json plus their exact SHAs.
The executed command is preserved privately in EXECUTION_v1.json.
Public clones do not contain private training data, weights, PDK or physical
artifacts. Read completed outputs for status; do not rerun this completed job.
