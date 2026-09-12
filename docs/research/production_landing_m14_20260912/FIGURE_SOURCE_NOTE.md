# Actual EM landing source — received 96-row increment

Descriptive source only; author produces and verifies final paper figures. No figure generated here.

Window: 2026-09-12 08:31:50.342478–09:05:27.301344 UTC. These are previously received real results, not newly executed EMX work in this projection.

- 96 terminal records: 77 completed EMX, 34 strict and within the 15 GHz target range.
- Of 34 eligible candidates, 31 have exact formal-record joins; 3 remain pending formal admission.
- Original formal splits: 18 train, 10 validation, 3 test. Pending splits: 2 train and 1 test. No split moved.
- Only 18 formal train geometries enter the descriptive landing view: 17 distinct cells, zero cells empty in the frozen 3801-train baseline, one row in a baseline cell containing fewer than five members; 18/18 have Qmin 10–20, none has |k| > 0.8.
- Baseline: 512 fixed bins, 159 occupied. This increment does not establish cumulative pool occupancy or superiority over another sampler. Target and predicted cells remain null where not recorded originally.

Sources: `ACTUAL_LANDINGS.json`, `SOURCE_BINDING.json`, `EXPECTED_COUNTS.json`, `RECEIPT.json`; exact SHA-256 values are in the manifest and SHA256SUMS. The referenced physical/formal evidence remains with the unique native owner.

Suggested caption: Actual EM response cells for the received increment, conditioned on formal TRAIN eligibility; validation, test, failed and pending-formal candidates remain visible in the accounting but do not change training coverage. Empty-cell and underfilled-cell comparisons use the unchanged frozen 3801-member training baseline and are not cumulative coverage claims.
