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
  canonical duplicate rejection.
- `scripts/audit_broadband56_balanced200k_checkpoint.py`:
  streaming accepted/S4P/56-point/S-Z/feature/fingerprint audit plus required
  checkpoint receipt, coverage table, failure funnel, and SHA-256 index. It
  also writes `physical_coverage_by_frequency.csv`,
  `physical_coverage_marginals.csv`, and `physical_coverage_pairwise.csv` for
  all five frozen validity/panel populations and `ALL`/Phase A/B/C scopes.

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

Verified result: `1426 passed, 54 skipped, 1 deselected`.  This is a software
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
stage the next adaptive round without launching a solver:

```bash
python scripts/stage_broadband56_adaptive_round.py \
  --contract /private/no-clobber/campaign_contract_frozen.json \
  --audit-dir /private/no-clobber/latest_real_emx_audit \
  --ensemble-receipt /private/no-clobber/acquisition_ensemble/ENSEMBLE_RECEIPT.json \
  --out-dir /private/no-clobber/next_adaptive_round
```

The stager verifies the preceding real-EMX receipt and all 10,368 fixed cell
rows. A valid five-or-more-member, geometry-split, sealed-validation,
uncertainty-calibrated ensemble activates the exact Phase-B or Phase-C source
quotas. A missing or invalid ensemble is recorded and activates the frozen
5,000-sample maximin fallback instead. Neither mode creates labels or accepted
samples; those remain dependent on fresh Cadence, Calibre, and EMX evidence.
