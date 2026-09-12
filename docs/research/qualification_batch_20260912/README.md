# Qualification and new-production increment, 12 September 2026

This is a dated snapshot, not a declaration that the 100K goal is complete.
The source counts and original hash/split references are in
[SNAPSHOT_0325.json](SNAPSHOT_0325.json). Private physical artifacts are not bundled.

At 03:09 UTC, the remaining original31 holdout members were actually committed:
3 validation and 8 test, without moving them to train. The separate ledger
read back 31 unique members: 20 train, 3 validation, 8 test. Eleven replay
checks added zero members; the original20 records were unchanged.

At 03:20 UTC, a native-owner ordinary program processed the first1000 original
old21135 records in ten blocks of100. The mutually exclusive audit outcomes
were304 qualified,220 out-of-range,476 physically invalid,0 duplicate,
0 missing-evidence and0 incompatible. Classification checks physical validity
before core range; it must not label all physically invalid records SRF-only.
In this specific1000,476 original rows have both strict and half-SRF flags false,
including214 also outside core range; all304 qualified rows have Qmin in10..20.
These are saved-label checks, not a fresh physics recomputation or a claim
that every failure in other cohorts was caused only by SRF.
Existing exact15 labels and individual artifact bindings were reused; no
new EMX or blanket historical QA was run for this audit. At this snapshot,
the304 are **audit-qualified, formal publication pending**, not304 new commits.
The remaining20135 records and other P215 sources are not covered by this batch.
Scoped local review SHA:5e3b0c4a724a6bd438a8d8d16c6c896bc927a188d2e64b81540d33a20db7f81f;
it does not claim an independent second read of remote GDS/S4P bytes.

The new independent production inputs contain192 geometryDOE and64 train-only
neighborhood proposals.238 pass local analysis;18 original failures remain.
Neither preparation nor software checks are a native release or EMX birth.
The input manifest is frozen separately from the closed controlled64/32 budget.
No target/proxy/q_proxy is invented for these geometry proposals.

See [proposal method](../EUCAP15_PRODUCTION256_METHOD_20260912.md),
[endpoint wiring](../EUCAP15_ENDPOINT_WIRING_20260912.md), and
[feedline development fix](../EUCAP15_FEEDLINE_FIX_20260912.md).
Fixes do not inherit old physical labels or relax DRC, GDS, SRF or range gates.

Only the native owner may register and run the separate256-start/12h/5GiB
budget, subject to stricter current admission. Actual current starts, costs
and formal publication must come from later birth/readback receipts, not this
input snapshot. Full100K certification remains unknown. The source records,
whole-server free space and solver-only time sums are not end-to-end costs.
