# RUN STATE

Updated: `2026-08-24T13:35:43Z`

- Status: `CODE_CANDIDATE_V5_FROZEN_AWAITING_FRESH_INDEPENDENT_QA`
- Phase: independent result-blind code-QA gate
- MARS connected: yes, read-only
- MARS training/common-test/metrics/fresh-EMX authority: no/no/no/no
- Controlled 10K/20K process count: `0`
- Last MARS load: `52.17 / 59.06 / 117.83`; launch gate is load1 `<=40`, currently FAIL
- Current high load is not this task; no remote process was modified

Frozen comparison:

- Newly trained controlled 10K vs newly trained controlled 20K; historical weights are descriptive only
- Forward `10→256→256→256→4`; inverse `4→256→256→256→10`; GELU; independent sigmoid
- One shared declared-domain normalization and common validation/test identities
- Seeds: `20260711, 20260712, 20260713`
- Small source/train/validation/test: `10000/7871/1227/902`
- Large source/train/validation/test: `20000/17871/1227/902`
- Each arm: batch `1024`; forward/inverse `1200/1200` updates; validation every `20`; no early stopping
- Fixed target SHA: `c9d7d8bc…d6407`; it was not regenerated

Latest authoritative independent gate:

- QA v4 remains `NO_GO`, P0/P1/P2/P3=`0/6/2/0`
- Receipt: `reports/controlled_real10k_20k_nested_20260824/independent_code_qa_v4_20260824T110825Z/RECEIPT.json`
- Receipt SHA: `a55003f0…4748`; no authority was released

Fresh code candidate:

- ID: `controlled_real10k_20k_code_candidate_v5_20260824T133543Z`
- Path: `reports/controlled_real10k_20k_nested_20260824/code_candidate_v5_20260824T133543Z`
- Manifest SHA: `58f58780…bc6d`
- QA-required SHA: `1dbdcc05…a2f7`
- QA4/root closure SHA: `312154d6…799d`
- Root receipt SHA: `24cfa07f…ee7f`
- SHA256SUMS SHA: `e03e74a9…034e`; `4/4` indexed artifacts PASS
- Complete warnings-as-errors regression: `331 passed / 0 failed / 2 MARS-native Linux skips`
- Source compile: `20/20 PASS`
- Frozen splitter/trainer baseline: `30 passed`, `87` known local NumPy warning fixtures
- Intermediate integration result `1 failed / 282 passed / 2 skipped / 45 errors` is preserved; stale fixture and runner pin were fixed without weakening production checks

Package/runtime interface:

- Package version `v5`; exact `21` roles and `23` GO bindings
- Package attempt requires distinct BODY+COMMITTED terminals
- Materialization direct chain has exact `21` roles; candidate/GO/complete schemas are v2/v2/v3
- Runner SHA bound by evaluator: `06d3658f…a6f31`

No-repeat record:

- No data or fixed10k regeneration, historical retraining, EMX rerun, materialization, training, evaluation, metric access, or signal occurred
- Referenced task `019eb52f-9739-73b2-8483-af70553603a8` remains timeout/unreadable; no status inferred

Next safe action: fresh independent result-blind QA of the exact candidate. Only exact GO may permit package-v5 preparation and a separately gated MARS-native preflight. Materialization, training, test release and metrics remain unauthorized.
