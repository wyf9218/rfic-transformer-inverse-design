# M21: continuous production, 69 new EMX results

Observation: **2026-09-12 11:34:31.323430 UTC**, baseline 11:15:44.864023. SNAPSHOT.json pins the sole native owner's original observation.

- Actual independent EMX: **16 × 2 CPU**; requested/executor capacity 48. Resource sampling passed 28 independent intervals. The fresh snapshot has 9.3963 idle CPU equivalents, or 5.3963 after system reserve, allowing two additional 2-CPU tools. This is a time-specific admission result, not a permanent capacity or stable-throughput claim.
- New 77 terminals: **69 fresh EMX + 8 analytical failures**; 41 strict-valid, **31 strict and in range**. All 31 are formally admitted: 17 train / 9 validation / 5 test.
- Formal increment **34** includes three prior pending backfills (2 train / 1 validation); these add no new solve. Historical formal increment **0**. Certified unique lower bound **6623**, not 100K completion.
- Current batch: 132 terminal (118 fresh / 14 analytical), 54 in-range strict / 54 formal, 124 without terminal. Those 124 are not all running. At the cut: 16 EMX, 2 EMX admission waits, 14 Calibre admission waits and 92 with no tool intent.
- Existing owner, metadata and standby identities remain alive. Automatic candidate processing and batch continuation are installed. The exact finite installer PID 516965/start401625264 is also alive, with no INSTALL_RECEIPT or INSTALL_FAILURE: **safe-boundary wait, not deployment of the staged 23ed runtime**. No second installer, healthy-task restart, old-budget reset or native signal.
- Existing 256-start / 12-hour / 5-GiB budget remains. Allocated 1209155584 bytes, projected reserve 2207510528 bytes, mounted free space 470720561152 bytes; quota unknown. No cleanup or full-cost extrapolation.

## Actual incremental statistics, not regenerated experiments

LANDING_RECEIPT.json preserves the one successful existing CLI invocation with --output. Its complete 1109282-byte output remains private, SHA dbfa4932f773958774bf7cf1c893a6415b33ba8fa97f71452b063510651cc166. TRAIN_SOURCE_ROWS.json separates 17 current training landings from 2 prior training backfills; old55 was not rerun.

The new 17 train geometries occupy 17 cells against frozen3801: zero baseline-empty landings, one underfilled landing (TRAIN025, cell [4,4,3], baseline count2). All17 have Q10–20; none have K>0.8. The two backfilled train landings add no empty/underfilled hit. Do not add these reference counts as cumulative pool coverage or claim sampler superiority.

Author caption source: actual EM-response coordinates of newly admitted training geometries, preserving method/seed/split and formal-record identities. No figures, old training, old physical cohort, full software suite or manuscript package was regenerated. The final independent 10000 requests remain pending final model freeze.
