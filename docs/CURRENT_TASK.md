# Current Task

## Task Identity

**Context checkpoint for migration to a new Codex conversation.** This is the only active task represented by this file.

## User's Real Need

Preserve the current project facts, contracts, evidence boundaries, commands, failures, and next legal entry point in repository documentation so a new Codex conversation can continue without relying on the long chat history.

## Current Problem

The conversation is too long and responsive work has become slow. Important facts are spread across Git history, code, private/offline receipts, and contradictory historical decisions. Without a checkpoint, a new task could rerun expensive work, use the wrong port/Q contract, or present proxy output as physical validation.

## Boundary

This task may inspect repository files, Git history, non-secret local evidence, and read-only runtime state; create the six handoff documents; run non-destructive tests; and commit only those documents.

It must not implement features, refactor code, launch training, generate data, create GDS, run Calibre/EMX/HFSS, restart MARS campaigns, or alter private artifacts.

## Must Not Modify

- The five pre-existing dirty code/test paths listed in `docs/PROJECT_STATE.md`.
- Frozen model, Q grid, score, split, seed, port, frequency, process, dataset, and artifact contracts.
- Existing receipts, NO-GO evidence, generated data, model weights, GDS, S-parameters, or private foundry files.
- External MARS/CHTC jobs or services.

## Implementation Steps

1. Record root, branch, base HEAD, status, log, dirty purpose, entry points, commands, artifacts, and risks.
2. Record frozen-model I/O, Q selection, GDS/process/port contracts, evidence classes, and current UI/backend state.
3. Create `AGENTS.md` and five `docs/` handoff files with explicit `UNKNOWN`, `REVERIFY`, and `PLANNED` labels.
4. Verify every tracked-file reference exists and scan documentation for accidental secrets/private assets.
5. Run focused non-destructive tests and the public test runner without repairing unrelated code.
6. Run `git diff --check`, inspect the stat and staged diff, stage only the six documentation files, and commit with `docs: checkpoint project context for Codex handoff`.
7. Verify the post-commit status still contains the original five dirty paths.

## Acceptance Conditions

- All six requested documents exist and agree with the audited base HEAD.
- Important claims have a path, commit, test, command result, artifact identity, or explicit uncertainty label.
- Proxy, historical Qmin, fresh EMX, DRC, and HFSS evidence are not conflated.
- The current four-port `.s4p` contract and historical `.s8p` conflict are explicit.
- No private PDK, weights, sensitive GDS, credentials, tokens, licenses, or private server paths are staged.
- All document references to tracked repository files resolve.
- Test results, including failures, are recorded truthfully.
- Only the six handoff documents are committed with the exact requested message.

## Fail-Closed Conditions

- Stop the commit if a requested document is missing, a referenced tracked file does not exist, or a staged file is outside the six-document allowlist.
- Do not “fix” failing unrelated tests during this task; record them and leave code unchanged.
- Do not claim current EMX/CHTC/HFSS availability without a current receipt.
- Do not commit any secret/private artifact.
- Do not mark the task complete if Git cannot create the documentation-only commit.

## Test Requirements

- Focused: `.venv/bin/python -m pytest tests/test_fixed_target_evaluation.py tests/test_q_sweep_synthesis.py -q`
- Public: `.venv/bin/python tools/run_public_tests.py`
- Syntax/whitespace: `git diff --check` and `git diff --cached --check`
- Reference existence: every repository-relative code/document path named by the handoff must resolve.
- Staging allowlist: only `AGENTS.md` and the five requested `docs/*.md` files.

## Current Blocker

No blocker to documentation is known. The dirty `analysis/__init__.py` may cause current public-test regressions, but this task must record rather than repair it.

## Next Minimum Operation

After this checkpoint commit, start a new Codex conversation with the prompt supplied in the final response. The new conversation must read the six files and inspect current Git/runtime state before selecting any development task. The previously requested 10k-versus-20k controlled training comparison remains `PLANNED`, not active here.
