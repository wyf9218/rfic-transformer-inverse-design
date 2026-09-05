# Current Evidence Status

## Current Authorized Data Campaign

Latest published observation: `2026-09-05T00:38:32Z`, not a live dashboard.
The active objective is exactly **200,000 unique accepted fresh real-EMX
geometries**, each with a four-port S4P at 5 through 60 GHz inclusive, 1 GHz
spacing and 56 frequency points. The full target is 11,200,000
geometry-frequency rows. **NN training is not authorized for this campaign.**

- Independently verified production progress: **31 accepted geometries,
  31 S4P files and 1,736 feature rows**. Golden validation samples are excluded.
- Failure accounting: 40 terminal candidates = 31 accepted + 9 Cadence-stage
  rejections. Rejected candidates were not relabeled or hidden.
- An independent read-only audit verified 255 artifact identities, the four
  migrated progress receipts, all 31 exact-frequency S4P files and 161,448
  stored feature-field comparisons. It did not run a simulator or replace data.
- One generation-20 supervisor was verified alive. It was waiting for the
  approved hard resource gates before a new Rescue Golden; available memory
  was 31.7%, below the unchanged 40% threshold. Zero simulators were active.
- Pilot 32 remains incomplete. The existing authorized chain continues after
  a genuine Golden PASS; neither a new campaign nor NN training is required.

The [public progress snapshot](BROADBAND56_PUBLIC_PROGRESS_20260905T003832Z.json)
contains exact aggregate counts, evidence SHA-256 identities and limitations.
The private runtime remains at source commit
`74bda20fb82c05fd63b4a4a625f1822a7b702423`; publishing this status does not modify it.
No raw geometry, GDS, S4P, private paths, PDK or credentials are published.

### Public Repository Test Limitation

The public test runner currently reports **1,983 passed, 1 failed, 54 skipped,
1 deselected, and 4 passed subtests**. The failure is
`tests/test_port_ground_metrics.py::test_metric_contract_binds_the_actual_sources`:
the public geometry-metric metadata retains an old SHA for the Cadence batch
script. Both the metadata and the script are byte-identical to source HEAD
`74bda20fb82c05fd63b4a4a625f1822a7b702423`, so this mismatch predates this
documentation-only publication. It remains unresolved and is not reported as a
green full-suite result. The snapshot records the exact expected/actual hashes
and private JUnit hash. The running private package has not been modified.

## Historical Snapshot: 2026-08-23

The following sections are preserved historical evidence, **not current
process status or authorization to train**. Their last live observation was
`2026-08-23T04:10:07Z`; the old raw-load threshold and model-training priorities
do not control the currently authorized Broadband56 campaign.

### Historical Primary Goals

- Monday report: explain the historical-200k architecture and exact row denominators; compare it with deployed-100k without causal overclaiming; complete fixed10k statistics/charts; release the five-pair controlled 100k/200k effect; release survivor-conditioned fresh-EMX three-chain errors; and finalize an advisor-ready HTML/PPTX plus question-and-answer material.
- Post-Monday mainline: build a current-contract, strict-`|K|<1`, controlled 200k/300k/400k/500k learning curve.
- Long term: map `[Lp,Ls,Qmin,|K|]` to manufacturable 10-D geometry and close proxy, layout, EMX, and sampled HFSS/measurement evidence.

### Historical Complete Evidence Blocks

- Historical deployed-100k and historical-200k model structures, row counts, parameter counts, and decoder differences were verified.
- A deterministic, unique 10,000-target finite-coverage frame was frozen: 8,000 legacy targets at `|K|≤0.8` and 2,000 extension targets above 0.8.
- Both historical models were evaluated on that identical frame. Historical 200k had 17.57% lower all-target joint proxy RMSE, but Q-target attainment was 7.99 percentage points lower.
- The physical funnel is closed through fresh EMX: `10,000 → 7,926 → 7,373 → 7,298 → 7,298`.
- Stage06 produced 7,298/7,298 fresh S4P survivor artifacts with no failed shards.

### Historical Running Snapshot

- The strict nested 100k/200k paired experiment has 7/10 terminal arms and 3/5 complete seed pairs at `2026-08-23T04:10:07Z`.
- The existing supervisor was alive with zero active children. Observed load1 was `231.03`, above the frozen prelaunch threshold `40`; rep4-large remained staged with no launch or terminal receipt. Do not launch it manually.
- It holds architecture, decoder, source, split, budget, seed contract, forward reference, and fixed-target inference constant.
- No historical comparison may substitute for this experiment's eventual causal data-scale result.

### Historical Blocked Or Incomplete Claims

- The formal fresh-EMX statistics and figures are blocked by a report-interface NO-GO (`P0/P1/P2/P3=0/3/0/0`).
- The RQ-I fixed10k release is NO-GO (`0/2/0/0`).
- The Monday HTML/PPTX is not finalized; an earlier partial is visual NO-GO.
- The current-contract `|K|<1` 200k/300k/400k/500k learning curve is not complete.
- Full-domain uniformity, final inverse-design closure across the original 10,000 targets, and EMX/HFSS agreement remain unproven.

### Denominator And Interpretation Rules

- “100k/200k” must be expanded into source-table, gradient-training, validation, and test rows.
- The fixed10k frame is deterministic coverage, not iid random sampling and not 10,000 EMX labels.
- The 7,298 EMX results are survivor-conditioned. The 2,702 analytical/Cadence/Calibre losses are MNAR and must remain in funnel reporting.
- Historical 100k/200k is an uncontrolled descriptive comparison; only the paired nested experiment can support a data-scale causal claim.
- Historical held-out-manifold accuracy and fixed10k one-shot error are different tasks and must not share one accuracy label.

Read [ENGINEERING_HANDOFF_20260823_CN.md](ENGINEERING_HANDOFF_20260823_CN.md) for the historical state and [KNOWN_NO_GO_20260823.md](KNOWN_NO_GO_20260823.md) before using any historical figure or claim.
