# M7 source increment

These are source snapshots, not a newly deployed native runtime or standalone replacement framework. Existing private artifact paths, old release IDs and library hashes stay in the local receipts; no private datasets or raw foundry files are copied into this publication.

- `resume_prepared.py`: retries the existing TRAIN002 prepared evidence only. Actual MARS invocation returned 75 (ledger busy); the existing ordinary metadata worker was installed at 06:55:22 UTC. It stops after successful publication because the bound historical owner is already dead. It does not repeat extraction, receiver preparation or EMX.
- `test_resume_prepared.py`: two new synthetic tests, first run only, plus compatibility against the five actual prepared JSON artifacts. Local pass is not formal admission.
- `incremental_history.py` and `test_incremental.py`: process-local prefix cache candidate, three new tests passed. Original publication code, lock, evidence reconstruction and append/readback are retained. All old prefix paths still receive metadata checks; only new record bodies are re-read/hashed. Not deployed and no throughput improvement measured.
- `INCREMENTAL_LEDGER_INTERFACE.md`: next supported metadata-process interface; no healthy process is hotpatched.
- `FIRST100_TRIAGE.jq`: exact offline classification of the actual P215 source-row 0..99 export. Current response heads and GDS bindings were observed, but qualification remains unclosed. The private per-row output retains original geometry identities and topcells.

No prior training, 128/64 experiment, full source join, Golden test, full-pool hash or paper package was repeated. See [snapshot](../SNAPSHOT_M7.json) for separate timestamps and denominators. Status labels distinguish installed ordinary retry, undeployed candidate, metadata evidence and physical output.
