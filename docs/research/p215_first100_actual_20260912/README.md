# Actual first100 P215 historical qualification diagnosis

This is an actual per-member audit of original P215 source rows0–99, all from `base_pool:accepted_pool`. It is **not** a model comparison, final10K validation, fresh production batch, or a representative estimate for all215,785 historical rows.

## Actual result

- Full bytes received and matched:100original S4P plus14shared multi-topcell GDS,5,929,917bytes; no replacement.
- All100 original GDS cells fail the existing current5nm required grid test;90also fail the existing horizontal/vertical/45-degree edge test. All declared topcells were found.
- Real111-point re-extraction succeeded for100/100:5–60GHz in0.5GHz increments, all11,100derived frequency rows retained, exact15GHz at zero-based index20.
- Under the unchanged current port mapping and formulas:100descriptor-valid,1strict,24inside the specified Lp/Ls/K range,1strict-and-in-range, and1also withinQ10–20.
- The other99 strict failures specifically fail the half-SRF condition. These are not the earlier128-request failure counts.
- The sole numerical strict/core record is source ordinal15. Its GDS also fails the current grid requirement: **current-qualified and newly submitted members from this partition are0**.

The updated reuse disposition is A0/B0/C100/D0: original geometry is retained for possible later current-chain revalidation; unchanged historical physical labels cannot be directly inherited. Original split/holdout provenance must be established before any sampling/training reuse. The prior B100 header-only triage remains preserved as an earlier evidence state.

## Scientific interpretation and figure source

[Actual per-row source table](ACTUAL_TARGET15_DIAGNOSTIC.csv) contains all100 original identities and the derived15GHz values, conditional strict flag and actual GDS failures. Its values are historical real-EM-response re-extractions, **not fresh EMX results or100new qualified samples**. The private full111 CSV and original S4P/GDS have exact pins in the snapshot. GitHub does not contain those private inputs.

Suggested author-verified table caption: “Qualification diagnosis of100consecutive historical P215 records. All records violate the current required manufacturing-grid criterion. Under the current differential mapping,99also fail the15GHz half-SRF criterion. Only one satisfies the numerical strict/range conditions, but no unchanged historical geometry is eligible for current-contract admission. Sequential source-order selection precludes extrapolation to the full archive.”

Do not add overlapping failure counts as disjoint outcomes. Do not interpret different grid sizes alone as physical incompatibility, infer causality from this audit, or use this subset for training/model selection. No diagram, plot, generated image or rewritten paper package was produced; authors may use the real source table with traditional plotting tools and verify the final figure.

## Reproduction boundary

The extraction core preserves the existing S/Z conversion, port projection, validity predicates, passivity/reciprocity checks and SRF helpers. [Exact core delta](code/CORE_DELTA.diff) changes only111grid/shapes and evidence labels. The existing adjacent-sample SRF zero-crossing estimate is retained; no S-parameter interpolation or fake56-point spectrum is created.

The GDS program calls the exact existing `_actual_grid_audit` implementation and original tolerance, on each recorded historical topcell. It checks necessary conditions only; it does not execute Calibre or claim full layout/process equivalence.

Run only with the existing pinned repository and verified private transport manifest. All output paths must be new. Use [the existing111 interface](code/HISTORICAL111_INTERFACE.md); `run_first100.py` reproduces this bounded partition. `analyze_existing_gds.py` consumes the private input manifest. Do not rerun these already completed inputs merely to inspect this publication.

Three new111 toy tests passed once; the original full QA/Golden/model training/128/64 experiments were not rerun. Actual production counts and the separately dated CPU-admission observation are reported in the project status, not inferred from this offline work.
