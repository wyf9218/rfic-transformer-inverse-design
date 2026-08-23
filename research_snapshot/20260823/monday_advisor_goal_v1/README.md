# Monday Advisor Goal v1 — Public GPT Review Snapshot

This directory is the public, GPT-readable snapshot of the work completed for the 2026-08-24 advisor report as of `2026-08-23T05:13:58Z`.

## Read first

1. [`status/CURRENT_PROBLEMS_GPT_HANDOFF_CN.md`](status/CURRENT_PROBLEMS_GPT_HANDOFF_CN.md) — concise scientific problem statement, current evidence, invalid claims, and next legal sequence;
2. [`status/CURRENT_PROBLEMS_GPT_HANDOFF.json`](status/CURRENT_PROBLEMS_GPT_HANDOFF.json) — the same state in machine-readable form;
3. [`status/RUN_STATE.md`](status/RUN_STATE.md) — append-only execution trace;
4. [`status/INDEPENDENT_QA_REQUIRED.json`](status/INDEPENDENT_QA_REQUIRED.json) — exact frozen-candidate hashes and the independent-QA boundary.

## What is included

- `status/`: six exact, public-safe status and postfreeze-verification files;
- `public_code/report_interface_v8/`: all six completed Python sources for the v8 report-interface candidate;
- `public_code/mars_preflight_transport/`: all twelve completed Python sources for the result-free MARS preflight/transport candidate, preserving their relative subdirectories;
- `PUBLIC_CODE_SHA256.txt`: SHA-256 index of the 18 public review sources;
- `SNAPSHOT_SHA256.txt`: SHA-256 index of every published file in this directory except the index itself.

## Critical integrity boundary

The two local candidates are immutable exact-byte packages and remain `AWAITING_FRESH_INDEPENDENT_QA`; they are not GO. Their exact manifest, receipt, and SHA-index identities are recorded in `status/INDEPENDENT_QA_REQUIRED.json` and the two root postfreeze receipts.

The Python files under `public_code/` are sanitized review mirrors. Site hostname, username, MARS storage root, and local workspace paths were replaced with explicit placeholders such as `${MARS_HOST}`, `${MARS_USER}`, `${MARS_RESEARCH_ROOT}`, and `${LOCAL_WORKSPACE}`. Therefore:

- public-code hashes intentionally differ from the exact local frozen candidates;
- the public mirror is suitable for GPT/code review and task planning;
- it is not an executable deployment package and cannot be used to issue independent GO for the local frozen bytes;
- independent QA must use the exact local candidate paths and hashes in `INDEPENDENT_QA_REQUIRED.json`.

No model weights, real Touchstone data, PDK files, credentials, license endpoints, fresh-EMX numerical results, or process-control authorization are included.
