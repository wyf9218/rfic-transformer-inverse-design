# Current Evidence Status

Snapshot date: 2026-08-23. This repository records a sanitized research state; it does not bundle private data or simulator artifacts.

## Complete evidence blocks

- Historical deployed-100k and historical-200k model structures, row counts, parameter counts, and decoder differences were verified.
- A deterministic, unique 10,000-target finite-coverage frame was frozen: 8,000 legacy targets at `|K|≤0.8` and 2,000 extension targets above 0.8.
- Both historical models were evaluated on that identical frame. Historical 200k had 17.57% lower all-target joint proxy RMSE, but Q-target attainment was 7.99 percentage points lower.
- The physical funnel is closed through fresh EMX: `10,000 → 7,926 → 7,373 → 7,298 → 7,298`.
- Stage06 produced 7,298/7,298 fresh S4P survivor artifacts with no failed shards.

## Running

- The strict nested 100k/200k paired experiment has 7/10 terminal arms and 3/5 complete seed pairs at the last read-only observation.
- It holds architecture, decoder, source, split, budget, seed contract, forward reference, and fixed-target inference constant.
- No historical comparison may substitute for this experiment's eventual causal data-scale result.

## Blocked or incomplete

- The formal fresh-EMX statistics and figures are blocked by a report-interface NO-GO (`P0/P1/P2/P3=0/3/0/0`).
- The RQ-I fixed10k release is NO-GO (`0/2/0/0`).
- The Monday HTML/PPTX is not finalized; an earlier partial is visual NO-GO.
- The current-contract `|K|<1` 200k/300k/400k/500k learning curve is not complete.
- Full-domain uniformity, final inverse-design closure across the original 10,000 targets, and EMX/HFSS agreement remain unproven.

## Denominator and interpretation rules

- “100k/200k” must be expanded into source-table, gradient-training, validation, and test rows.
- The fixed10k frame is deterministic coverage, not iid random sampling and not 10,000 EMX labels.
- The 7,298 EMX results are survivor-conditioned. The 2,702 analytical/Cadence/Calibre losses are MNAR and must remain in funnel reporting.
- Historical 100k/200k is an uncontrolled descriptive comparison; only the paired nested experiment can support a data-scale causal claim.
- Historical held-out-manifold accuracy and fixed10k one-shot error are different tasks and must not share one accuracy label.

Read [ENGINEERING_HANDOFF_20260823_CN.md](ENGINEERING_HANDOFF_20260823_CN.md) for the complete state and [KNOWN_NO_GO_20260823.md](KNOWN_NO_GO_20260823.md) before using any figure or claim.
