# Monday Advisor Goal v1 — Public GPT Review Snapshot

This directory is the public, GPT-readable snapshot of the work completed for the 2026-08-24 advisor report as of `2026-08-23T20:24:51Z`.

## Read first

1. [`status/CURRENT_PROBLEMS_GPT_HANDOFF_CN.md`](status/CURRENT_PROBLEMS_GPT_HANDOFF_CN.md) — concise scientific problem statement, current evidence, invalid claims, and next legal sequence;
2. [`status/CURRENT_PROBLEMS_GPT_HANDOFF.json`](status/CURRENT_PROBLEMS_GPT_HANDOFF.json) — the same state in machine-readable form;
3. [`status/RUN_STATE.md`](status/RUN_STATE.md) — append-only execution trace;
4. [`status/RUN_STATE.json`](status/RUN_STATE.json) — machine-readable milestone status and exact artifact identities;
5. [`status/INDEPENDENT_QA_REQUIRED.json`](status/INDEPENDENT_QA_REQUIRED.json) — exact frozen-candidate hashes and the independent-QA boundary;
6. [`status/GITHUB_SYNC_RECEIPT_BATCH1.json`](status/GITHUB_SYNC_RECEIPT_BATCH1.json) — verified commit/push identity for the first incremental sync.
7. [`status/independent_audit_v3/INDEPENDENT_AUDIT_RECEIPT.json`](status/independent_audit_v3/INDEPENDENT_AUDIT_RECEIPT.json) — fresh result-blind independent-QA decision bound to the exact local frozen bytes.

## What is included

- `status/`: public-safe status, postfreeze-verification, GitHub-sync, and independent-audit files;
- `public_code/report_interface_v8/`: all six completed Python sources for the v8 report-interface candidate;
- `public_code/mars_preflight_transport/`: all twelve completed Python sources for the result-free MARS preflight/transport candidate, preserving their relative subdirectories;
- `PUBLIC_CODE_SHA256.txt`: SHA-256 index of the 18 public review sources;
- `SNAPSHOT_SHA256.txt`: SHA-256 index of every published file in this directory except the index itself.

## Critical integrity boundary

The two local candidates are immutable exact-byte packages. The fresh independent result-blind audit issued `GO_EXACT_FROZEN_BYTES_WITH_SCOPE`; the decision, exact identities, evidence denominators, limitations, and SHA index are published under `status/independent_audit_v3/`.

The Python files under `public_code/` are sanitized review mirrors. Site hostname, username, MARS storage root, and local workspace paths were replaced with explicit placeholders such as `${MARS_HOST}`, `${MARS_USER}`, `${MARS_RESEARCH_ROOT}`, and `${LOCAL_WORKSPACE}`. Therefore:

- public-code hashes intentionally differ from the exact local frozen candidates;
- the public mirror is suitable for GPT/code review and task planning;
- it is not an executable deployment package and cannot be used to issue independent GO for the local frozen bytes;
- the issued GO binds only the exact local paths and hashes in `INDEPENDENT_QA_REQUIRED.json` and becomes invalid on any byte or closure drift.

The scoped GO does not authorize MARS access, fresh-EMX numerical-result access, process inspection, signal/resume, transport, native preflight, Stage07/08, EMX rerun, or controlled-arm launch. No model weights, real Touchstone data, PDK files, credentials, license endpoints, fresh-EMX numerical results, or process-control authorization are included.
