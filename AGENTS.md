# Codex Engineering Rules

## Project Goal

Build an auditable RFIC transformer inverse-design flow that maps physical targets to manufacturable geometry, exports real GDS, and validates selected layouts with fresh EMX evidence. The flow must preserve foundry, port, frequency, model, and dataset contracts and must distinguish numerical proxies from physical truth.

## Required Read Order

Before changing code, read `AGENTS.md`, `docs/CODEX_HANDOFF.md`, `docs/PROJECT_STATE.md`, `docs/CURRENT_TASK.md`, `docs/DECISIONS.md`, and the latest relevant receipt or manifest under `docs/research/`. Verify Git state and running processes before launching training, GDS, Calibre, EMX, HFSS, or campaign work.

## Frozen Contracts

- Do not silently change model inputs, geometry outputs, Q semantics or grid, score definition, split identity, seeds, training budget, port order, process layers, frequency grid, or artifact schemas. Any change requires a new versioned contract, no-clobber output, tests, and a recorded decision.
- Keep source-table rows, gradient-training rows, validation rows, test rows, accepted EMX rows, and unique geometries as separate denominators.
- Never overwrite historical evidence. A failed or interrupted run remains `FAIL`, `NO-GO`, or `UNVALIDATED_WIP`.

## Evidence Rules

- Treat frozen-forward output as proxy evidence only. Physical claims require hash-bound fresh real-EMX results; independent HFSS, foundry DRC, and silicon/measurement remain separate gates.
- Bind selected candidate ID, selected Q, exact 10-D geometry SHA-256, GDS SHA-256, S-parameter SHA-256, model identity, and input target in the same manifest.
- A PNG, schematic, preview, or approximate polygon is never a GDS artifact. Do not report DRC, EMX, HFSS, or physical accuracy without the corresponding real artifact and receipt.
- Do not hide negative results, extrapolate survivor-only results to rejected samples, or make causal improvement claims from uncontrolled comparisons.
- Mark unsupported claims `UNKNOWN`, stale claims `REVERIFY`, and unimplemented work `PLANNED`.

## Required Verification

- Run focused tests for changed modules and `python tools/run_public_tests.py` before commit; preserve and disclose pre-existing failures.
- For layout changes, verify real GDS structure, top cell, layer/datatype map, ports, manifest hashes, and foundry DRC where available.
- For physical selection, require every contracted candidate to receive fresh EMX output before ranking; fail closed on missing, mismatched, unsafe, or non-finite evidence.
- Run `git diff --check` and inspect the staged diff before commit.

## Code And Commit Discipline

- Follow existing Python patterns, type hints, deterministic seeds, explicit schemas, path containment, and no-clobber output directories.
- Keep public code free of credentials, private PDKs, model weights, foundry files, sensitive GDS, private server paths, and license data.
- Scope commits narrowly. Do not stage unrelated user changes. Record commands, artifact identities, limitations, and test results in the relevant receipt or state document.
