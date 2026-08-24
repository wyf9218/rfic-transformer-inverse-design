# Independent code QA v1 — NO-GO

Verdict: `NO_GO`  
MARS authorization: `NOT_AUTHORIZED`  
Review class: independent, result-blind, code-only

The first frozen builder/runner candidate passed its author tests but failed the independent release gate with P0/P1/P2/P3=`1/7/3/0`.

Blocking findings:

1. The runner could launch without an external exact-GO receipt.
2. Builder cell IDs used `0:1:2:3`; runner recomputed `(0, 1, 2, 3)`.
3. A one-seed or arbitrary-seed production invocation was accepted.
4. A LAUNCH-only attempt could allocate another attempt and duplicate a live child.
5. Arbitrary finite normalization arrays could pass.
6. The caller could self-select the trainer SHA; GELU was only asserted by a string.
7. Inputs were not rehashed immediately before every arm launch.
8. Prepare/preflight/lock/interrupt/ambiguous failures lacked complete durable receipts.

The authoritative receipt/report/matrix/SHA-index identities are recorded in `INDEPENDENT_CODE_QA_V1_NO_GO.json`. No training, model evaluation, fresh EMX, or performance-metric access occurred.

The only legal next step is to freeze corrected bytes and obtain a fresh independent result-blind code QA. This record does not authorize MARS.
