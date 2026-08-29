# Broadband56 Balanced-200k V2 Campaign

## Purpose

This branch prepares the sanitized, fail-closed control plane for campaign
`broadband56_real_emx_balanced200k_tsmc65_v2`.

The terminal execution target is exactly 200,000 canonical-geometry-unique,
zero-blocking-Calibre, fresh-real-EMX `.s4p` results on the exact 5-60 GHz,
1-GHz, 56-point grid.  The resulting 11,200,000 geometry-frequency records are
correlated records from 200,000 designs, not 11.2 million independent designs.

The public contract is
`configs/broadband56_real_emx_balanced200k_tsmc65_v2.json`.  It does not contain
private PDK, process, license, hostname, GDS, S4P, or runtime-path data.

## Frozen Overrides

- 200,000 accepted geometries and 200,000 complete `.s4p` artifacts.
- Exact frequencies: 5, 6, ..., 60 GHz.
- Eight acquisition anchors: 8, 15, 22, 29, 36, 43, 50, and 57 GHz.
- Primary vector: `[Xp, Xs, Qmin, |K|]`.
- Six fixed bins per dimension: 1,296 cells per anchor and 10,368 conditioned cells.
- Nine secondary physical features use six frozen bins plus explicit underflow
  and overflow categories; no valid real-EMX record is dropped for leaving the
  broad envelope.
- Phase A: 0-50k, exact 10-D optimized-LHS/Sobol space filling, seed 20260828.
- Phase B: 50k-150k in 5k accepted batches, fixed 60/20/20 repair/uncertainty/maximin mixture.
- Phase C: 150k-200k in 5k accepted batches, frozen 65/20/15 rare-repair/uncertainty/maximin mixture.

All process, layout, GDS, DRC, port, grounding, EMX, S/Z conversion, feature,
and provenance items are inherited from the approved broadband56 V1 contract.
Preparation fails unless the prior contract hash is supplied and the private
V1 and V2 production configurations are structurally identical after removing
only the four frequency-grid fields.

## Evidence Boundary

- A candidate queue is geometry-only and is never accepted data.
- A create-only layout run is not Calibre or EMX evidence.
- A temporary acquisition ensemble may rank unevaluated geometries but cannot
  write final labels.
- An accepted row requires analytical, topology, Cadence/GDS, zero-blocking
  Calibre, fresh EMX, parseable exact-grid S4P, finite S/Z, feature extraction,
  provenance, and fingerprint PASS evidence.
- A PNG or approximate polygon is never a GDS artifact.

## Current Public Components

- `rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py`:
  immutable constants, canonical 10-D identity, bin assignment, phase plan,
  and uniformity metrics.
- `scripts/prepare_broadband56_balanced200k_campaign.py`:
  no-clobber V1-hash/config preflight and V2 contract freeze. It runs no solver.
- `scripts/build_broadband56_phase_a_queue.py`:
  exact 10-D label-free Phase-A queue generation with shared line width and
  canonical duplicate rejection. The builder is fail-closed to `PHASE_A`,
  `base_space_filling`, and at most 50,000 requested rows. Each candidate
  records its sampler/seed and the PASS results of the local bounds, topology,
  and public top-metal analytical audits; those fields are queue provenance,
  not Cadence, Calibre, GDS, or EMX evidence.
- `scripts/build_broadband56_adaptive_candidate_pool.py`:
  no-clobber Phase-B/C geometry-only pool builder. It requires an exact staged
  5k round, hash-bound accepted ledger and frozen bounds, matching production
  config bounds, exact 56-point/4-port settings, at least 4x batch-size rows,
  canonical uniqueness, and disjointness from every accepted geometry. Prior
  attempted or reserved identities may be supplied as additional exclusions.
- `rfic_transformer_inverse_design/campaigns/broadband56_adaptive_selection.py`:
  exact Phase-B/C candidate-priority policy. It combines real-EMX fixed-cell
  deficit, ensemble uncertainty, normalized 10-D geometry novelty, frozen
  boundary coverage, and predicted feature validity, then applies
  deterministic block-greedy batch diversity. Predictions remain ranking
  metadata and never become labels.
- `scripts/select_broadband56_adaptive_candidates.py`:
  no-clobber, hash-bound selector for one exact 5,000-candidate adaptive
  queue. It requires a candidate pool of at least 20,000, exact source quotas,
  canonical uniqueness against all accepted geometries, and internally
  consistent coverage-cell evidence. Missing or inconsistent evidence emits a
  FAIL receipt and no runnable candidate queue.
- `rfic_transformer_inverse_design/campaigns/broadband56_acquisition_ensemble.py`:
  deterministic geometry-identity splitting plus a minimum-five-member,
  independently seeded random-feature ridge forward ensemble. It predicts the
  seven frozen acquisition fields at all eight anchors, calibrates disagreement
  only on the calibration split, and evaluates once on a sealed validation
  split. Saved NPZ members contain no final labels.
- `scripts/train_broadband56_acquisition_ensemble.py`:
  no-clobber trainer that accepts only a hash-bound PASS checkpoint/round,
  exact accepted geometry ledger, frozen bounds, and fresh-real-EMX feature
  table. A failed validation receipt authorizes maximin fallback only.
- `scripts/predict_broadband56_acquisition_candidates.py`:
  verifies the staged round, exact ensemble receipt, member NPZ metadata,
  accepted-ledger disjointness, and candidate analytical/topology gates before
  writing 112 traceable prediction/uncertainty columns. The rows remain
  unevaluated candidate-priority metadata.
- `scripts/audit_broadband56_balanced200k_checkpoint.py`:
  streaming accepted/S4P/56-point/S-Z/feature/fingerprint audit plus required
  checkpoint receipt, coverage table, failure funnel, and SHA-256 index. It
  also writes `physical_coverage_by_frequency.csv`,
  `physical_coverage_marginals.csv`, and `physical_coverage_pairwise.csv` for
  all five frozen validity/panel populations and `ALL`/Phase A/B/C scopes.
- `scripts/finalize_broadband56_training_readiness.py`:
  terminal-only finalizer for `full_200k_training_weights.csv`,
  `maximal_balanced_subset.csv`, and `future_split_manifest.json` plus its
  geometry-level assignment table. It requires a SHA-bound `COMPLETE_200K`
  receipt with exactly 200,000 accepted geometries, 200,000 S4P artifacts,
  and 11,200,000 correlated rows. It recomputes actual eight-anchor cell
  memberships and checks them against the audited 10,368-cell table before
  creating any output. A failed identity, count, receipt, or hash check leaves
  no output directory.
- `scripts/finalize_broadband56_campaign_histories.py`:
  terminal history finalizer for `coverage_deficit_history.csv`,
  `acquisition_round_history.csv`, `acquisition_source_by_geometry.csv`, and
  the byte-exact `coverage_summary_200k.json` copy. It requires all 35 frozen
  audit endpoints: 100/1k/5k/20k/50k plus every Phase-B/C 5k endpoint through
  200k. It validates all 10,368 cell identities and deficit equations per
  endpoint, the exact 31-round accepted-sequence partition, and either the
  complete active mixture or the complete maximin fallback for each adaptive
  round. A mixed or approximate per-round source count is rejected.
- `scripts/render_broadband56_checkpoint_figures.py`:
  downstream-only renderer for all 14 required PNG and SVG figures at the
  exact 50k/100k/150k/200k checkpoints. Every manifest row binds the source
  CSV hash, denominator, frequency/anchor scope, validity definition, phase,
  production-config hash, contract fingerprint, and checkpoint receipt.
- `scripts/audit_broadband56_balanced200k_final_delivery.py`:
  final fail-closed completion auditor. It is the only public control-plane
  command that may emit a `COMPLETE_200K` delivery receipt. It requires the
  exact terminal checkpoint, all five named raw products, the 35-audit
  histories, training-readiness products, and four-by-fourteen PNG/SVG figure
  set to remain hash closed. It separately preserves the frozen coverage
  status and never turns proxy predictions into labels.

The secondary tables are explicitly record-weighted. They retain all exact
frequency rows but never replace the primary geometry-unique anchor metric.
Accepted geometry rows must carry a contiguous one-based acceptance sequence,
the phase implied by that sequence, and an acquisition source allowed by the
frozen phase mixture.

Private preparation also freezes `GEOMETRY_BOUNDS_FROZEN.json` from the exact
production search space. Checkpoint audit uses those bounds, never observed
sample minima/maxima, to produce `GEOMETRY_COVERAGE_SUMMARY.json`,
`geometry_coverage_marginals.csv`, and `geometry_coverage_pairwise.csv`.
These report ten one-dimensional occupancies, all 45 geometry pairs, boundary
occupancy, canonical duplicates, and normalized 10-D nearest-neighbor distance.
Geometry coverage is not physical-response coverage or simulator evidence.

The exact private Calibre/EMX batch adapter and authoritative V1 evidence are
not in this public repository. Their current MARS identities must be reverified
before a golden geometry is launched.

## Required Order

1. Verify the previous broadband56 contract and production-config SHA-256.
2. Run the preparation preflight into a new no-clobber directory.
3. Run the focused and public test suites.
4. Run one exact-contract golden geometry through private Cadence, Calibre, and EMX.
5. Audit the golden artifact and receipt.
6. Run 32 and then 1,000 geometries through the same fingerprinted backend.
7. Produce a measured resource estimate before Phase A.

Do not launch a 50k batch merely because the public contract tests pass.

The same fail-closed audit script has disjoint modes: `golden` accepts only
one geometry, `pilot` accepts only 32 or 1,000, `round` accepts only adaptive
5k endpoints that are not formal checkpoints, and `checkpoint` accepts only
the eleven frozen cumulative checkpoint counts. Only the 200,000 checkpoint
may emit `COMPLETE_200K`; golden, pilot, and round receipts cannot do so.

## Verified Commands

Focused contract tests:

```bash
python -m pytest tests/test_broadband56_balanced200k_contract.py -q
```

Full public regression suite (verified on 2026-08-28):

```bash
python tools/run_public_tests.py
```

Latest verified result after terminal-delivery-auditor integration:
`1465 passed, 54 skipped, 1 deselected, 1 warning`.  This is a software
regression result only; it is not Calibre, EMX, or physical campaign evidence.

Queue construction after a frozen private preparation receipt exists:

```bash
python scripts/build_broadband56_phase_a_queue.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --config /private/approved/broadband56_v2.yaml \
  --out-dir /private/no-clobber/queue_000001 \
  --count 32 \
  --sampler sobol \
  --seed 20260828
```

The private runner invocation remains `REVERIFY`; no public command may be
described as a successful Calibre/EMX production path until a fresh terminal
receipt is available.

After both fresh pilot audits pass, the measured resource estimate is produced
with:

```bash
python scripts/estimate_broadband56_balanced200k_resources.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --pilot-32-run-summary /private/no-clobber/pilot_32/parallel_candidate_queue_dataset_summary.json \
  --pilot-32-audit-dir /private/no-clobber/pilot_32/audit \
  --pilot-1000-run-summary /private/no-clobber/pilot_1000/parallel_candidate_queue_dataset_summary.json \
  --pilot-1000-audit-dir /private/no-clobber/pilot_1000/audit \
  --out-dir /private/no-clobber/resource_estimate
```

The estimator rejects create-only, proxy-only, resumed-shard, incomplete,
wrong-grid, wrong-port, fingerprint-mismatched, or failed checkpoint evidence.
Its CLI and fail-closed behavior are covered by synthetic software tests; a
real ETA remains `REVERIFY` until the contract-bound 32 and 1,000 pilots exist.

After Phase A reaches an audited 50,000 and after every subsequent 5k batch,
fit a checkpoint-bound candidate-priority ensemble without launching a solver:

```bash
python scripts/train_broadband56_acquisition_ensemble.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --checkpoint-audit-dir /private/no-clobber/latest_real_emx_audit \
  --out-dir /private/no-clobber/acquisition_ensemble
```

Only completed fresh-real-EMX rows may enter this command. The receipt binds
the exact accepted ledger, long feature table, bounds, split identities,
member seeds, model SHA-256 values, uncertainty calibration, and sealed
validation result. A PASS is candidate-ranking authorization only.

Then stage the next adaptive round without launching a solver:

```bash
python scripts/stage_broadband56_adaptive_round.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --audit-dir /private/no-clobber/latest_real_emx_audit \
  --ensemble-receipt /private/no-clobber/acquisition_ensemble/ENSEMBLE_RECEIPT.json \
  --out-dir /private/no-clobber/next_adaptive_round
```

The stager verifies the preceding real-EMX receipt and all 10,368 fixed cell
rows, and hash-binds the accepted-geometry ledger plus frozen geometry bounds.
A valid five-or-more-member, geometry-split, sealed-validation,
uncertainty-calibrated ensemble activates the exact Phase-B or Phase-C source
quotas. A missing or invalid ensemble is recorded and activates the frozen
5,000-sample maximin fallback instead. Neither mode creates labels or accepted
samples; those remain dependent on fresh Cadence, Calibre, and EMX evidence.

After staging, construct a large geometry-only pool from the same frozen
bounds. The command performs no Cadence, Calibre, GDS, or EMX work:

```bash
python scripts/build_broadband56_adaptive_candidate_pool.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --config /private/approved/broadband56_v2.yaml \
  --round-dir /private/no-clobber/next_adaptive_round \
  --out-dir /private/no-clobber/unevaluated_candidate_pool \
  --count 20000 \
  --sampler sobol \
  --seed 20310828
```

The example seed is the Phase-A base seed plus the 50,000 accepted round start;
each later round must use and record its own deterministic seed. A pool must
contain at least 20,000 rows for the frozen 5,000-candidate selection batch.
Its local analytical/top-metal PASS fields are queue provenance only.

For an ensemble-authorized round, attach calibrated candidate-priority
predictions:

```bash
python scripts/predict_broadband56_acquisition_candidates.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --round-dir /private/no-clobber/next_adaptive_round \
  --ensemble-receipt /private/no-clobber/acquisition_ensemble/ENSEMBLE_RECEIPT.json \
  --candidate-csv /private/no-clobber/unevaluated_candidate_pool/broadband56_adaptive_candidate_pool.csv \
  --out-dir /private/no-clobber/candidate_predictions
```

The predictor rejects a model whose internal seed, training count, hidden
width, or ridge metadata differs from the receipt. It does not create EMX
labels or realized coverage. In maximin-fallback mode this prediction step is
not used.

Finally, select the exact unlabeled 5,000-candidate queue without launching a
solver. In ensemble mode, `--candidate-csv` is the predictor output; in
fallback mode it is the contract-matching geometry-only pool:

```bash
python scripts/select_broadband56_adaptive_candidates.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --round-dir /private/no-clobber/next_adaptive_round \
  --candidate-csv /private/no-clobber/candidate_pool_for_selection.csv \
  --out-dir /private/no-clobber/selected_candidate_queue
```

The command is locally software-tested only. Private MARS candidate generation
and Cadence/Calibre/EMX execution remain `REVERIFY` and must not be inferred
from a PASS selection receipt.

Only after the exact terminal checkpoint exists, create the frozen
training-readiness products without training a model:

```bash
python scripts/finalize_broadband56_training_readiness.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --checkpoint-dir /private/no-clobber/checkpoint_200000 \
  --accepted-geometries /private/no-clobber/accepted_geometry_200k.csv \
  --long-features /private/no-clobber/broadband_features_11p2m_long.csv \
  --out-dir /private/no-clobber/training_readiness_200000
```

The full-dataset weight is the mean inverse occupancy of each geometry's
actual broadband-valid anchor cells, globally normalized to mean one and
clipped to `[0.25, 4.0]`; a geometry outside every primary cell receives a
neutral pre-normalization weight rather than fabricated coverage evidence.
Samples are never duplicated. The balanced-subset algorithm first assigns
each geometry to its least-populated actual conditioned cell, then performs
strict equal-quota capacity-aware water filling over those deterministic
owner strata. Its maximality claim is limited to that frozen partition and is
not a claimed global optimum over the multi-anchor hypergraph. The future
80/10/10 split uses salted canonical-geometry SHA-256 ordering with exact
largest-remainder counts, so all 56 rows from one geometry remain together.
This command is locally verified with synthetic terminal evidence; running it
on real 200k evidence remains `PLANNED` until `COMPLETE_200K` exists.

After all Phase-A checkpoints and every adaptive 5k endpoint have terminal
PASS receipts, finalize the required campaign histories. Repeat `--audit-dir`
exactly once for each of the 35 frozen accepted-count audits:

```bash
python scripts/finalize_broadband56_campaign_histories.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --accepted-geometries /private/no-clobber/accepted_geometry_200k.csv \
  --audit-dir /private/no-clobber/audit_000100 \
  --audit-dir /private/no-clobber/audit_001000 \
  --audit-dir /private/no-clobber/audit_005000 \
  --audit-dir /private/no-clobber/audit_020000 \
  --audit-dir /private/no-clobber/audit_050000 \
  --audit-dir /private/no-clobber/every_remaining_5k_endpoint_through_200000 \
  --out-dir /private/no-clobber/campaign_histories_200000
```

The abbreviated path in this documentation is not a shell wildcard: the real
invocation must enumerate every exact audit directory. The script discovers
counts from SHA-bound status files, rejects duplicates and omissions, and
creates no history output until all 35 inputs pass. It performs no MARS,
Cadence, Calibre, EMX, proxy inference, or model-training work.

After the PASS history finalizer and the exact 50k/100k/150k/200k formal
checkpoint audits exist, render the frozen 14-figure set at each checkpoint:

```bash
python scripts/render_broadband56_checkpoint_figures.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --history-dir /private/no-clobber/campaign_histories_200000 \
  --audit-dir /private/no-clobber/audit_050000 \
  --audit-dir /private/no-clobber/audit_100000 \
  --audit-dir /private/no-clobber/audit_150000 \
  --audit-dir /private/no-clobber/audit_200000 \
  --out-dir /private/no-clobber/checkpoint_figures_200000
```

The renderer validates every source receipt and SHA index before creating its
staging directory. It emits PNG and SVG copies plus one manifest per
checkpoint and a top-level receipt/index. Every logical figure binds its
source CSV SHA-256, denominator, exact frequency or anchor scope, validity
definition, campaign phase, production-config SHA-256, and campaign-contract
fingerprint. Record-weighted frequency plots remain explicitly correlated
within geometry; geometry-unique anchor occupancy remains the primary metric.
The renderer performs no remote action, simulation, proxy inference, or model
training. The implementation is locally software-tested with synthetic,
hash-closed evidence; real campaign figures remain `PLANNED` until all four
required formal checkpoint audits exist.

After the terminal checkpoint, history, training-readiness, and figure
finalizers all pass, bind the complete private delivery without copying its
large artifacts:

```bash
python scripts/audit_broadband56_balanced200k_final_delivery.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --raw-dir /private/no-clobber/final_products \
  --checkpoint-dir /private/no-clobber/audit_200000 \
  --history-dir /private/no-clobber/campaign_histories_200000 \
  --training-readiness-dir /private/no-clobber/training_readiness_200000 \
  --figure-dir /private/no-clobber/checkpoint_figures_200000 \
  --out-dir /private/no-clobber/final_delivery_audit_200000
```

The raw directory names are frozen as `accepted_geometry_200k.csv`,
`broadband_features_11p2m_long.csv`,
`sparameter_artifact_index_200k.csv`, `geometry_provenance_200k.csv`, and
`failure_funnel.csv`. The provenance table must bind each accepted geometry to
the exact production config, candidate source, GDS, Calibre report, EMX log,
and S4P by existing path and SHA-256; the S4P path/hash must also agree with the
terminal artifact index. Shared files are hash-cached during validation, while
per-geometry artifacts remain individually checked. `COMPLETE_200K` proves
execution accounting only; `COVERAGE_PASS`, `COVERAGE_PARTIAL`,
`COVERAGE_PHYSICALLY_LIMITED`, or `COVERAGE_AUDIT_FAIL` remains a separate
field. Running this command performs no remote action, simulation, inference,
or model training. Real execution remains `PLANNED` until all source evidence
exists.
