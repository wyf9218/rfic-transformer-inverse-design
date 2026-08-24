# Project State

## Checkpoint Identity

| Field | Value |
|---|---|
| Audit date | 2026-08-24, America/Chicago |
| Repository root | `/Users/wyf/Documents/模拟变压器AI反向建模/github_release/rfic-transformer-inverse-design` |
| Remote | `https://github.com/wyf9218/rfic-transformer-inverse-design.git` |
| Branch | `codex/real10k-fixed10k-report` |
| Audited base HEAD | `36e8a7667ad3c4c64aea8d5322312f728fcf5640` |
| Checkpoint commit | The commit containing this file; obtain with `git rev-parse HEAD` |
| Working tree at audit | Dirty; five pre-existing code/test paths listed below |

The base HEAD is intentionally recorded rather than attempting to embed the self-referential SHA of the documentation commit.

## Git Status At Audit

```text
 M rfic_transformer_inverse_design/analysis/__init__.py
 M rfic_transformer_inverse_design/synthesis/frozen_mlp.py
?? rfic_transformer_inverse_design/analysis/fixed_target_evaluation.py
?? scripts/evaluate_real10k_mlp_fixed10k.py
?? tests/test_fixed_target_evaluation.py
```

These paths predate the context checkpoint and are not part of the documentation commit.

### Uncommitted Work And Purpose

| Path | Purpose inferred from the actual diff/file | State and risk |
|---|---|---|
| `rfic_transformer_inverse_design/analysis/__init__.py` | Export fixed-target evaluation helpers | **Risk:** it replaces the existing `__all__` list and drops prior public analysis exports; review before commit |
| `rfic_transformer_inverse_design/synthesis/frozen_mlp.py` | Suppress expected floating warnings and fail closed when a layer produces non-finite values | Focused tests pass; still uncommitted |
| `rfic_transformer_inverse_design/analysis/fixed_target_evaluation.py` | Deterministic fixed-10k proxy evaluation, support/OOD split, metrics, plots, tables, receipts | 1,020-line untracked implementation; proxy-only evidence |
| `scripts/evaluate_real10k_mlp_fixed10k.py` | CLI wiring for the frozen real10k model and fixed target frame | Untracked; depends on private model artifacts |
| `tests/test_fixed_target_evaluation.py` | Synthetic evaluation and fail-closed tests | Untracked |

Untracked file SHA-256 values at audit:

- evaluator: `096ff88a0f4ffc26a526e668664d8e3acfde2dfe2c9ad48a6ce636f18a7fe9e9`
- script: `42c558535ea06e758bee412eb41e53e5fc17571fbf0b503028f25f70608993f3`
- test: `52818592b92d60cc0561a00dcb36f3622a7590290340b94cb8268a3fc12698d0`

## Recent Ten Commits

| Commit | Date | Subject |
|---|---|---|
| `36e8a7667ad3c4c64aea8d5322312f728fcf5640` | 2026-08-24 | Add three-input MLP Q-sweep application |
| `f5a65b978ec709330246cbc5dea68a9362664acd` | 2026-08-23 | Publish strict contract blocker evidence |
| `e53cce67a4ea468d3265b85033e7dd3121959f7c` | 2026-08-23 | Record strict exact-contract blocker |
| `533b73daa3817e1e17ff33f97f9efe3ecfbf8000` | 2026-08-23 | Record completed exact-contract 200k diagnostic |
| `095274f35be8a1b6db8acbca76b93dd6738c57c6` | 2026-08-23 | Launch exact-contract 200k engineering run |
| `d2a94c8decd73948a24e37eae8e2dff971c8e4a0` | 2026-08-23 | Record MARS native preflight NO-GO |
| `fe42c6eefe4c88dd386a52720986c9123d29b9a0` | 2026-08-23 | Publish independent result-blind QA decision |
| `19a8b0f5d0e6d8685e07ed729ba8ad210c340fb8` | 2026-08-23 | Freeze Phase 1 QA candidate state |
| `6314f91a5de23a67bbc640560c57d4780cc4c47d` | 2026-08-23 | Record first GitHub sync receipt |
| `6ab7d73e248b02114355b03bfdf036af3e08ab24` | 2026-08-23 | Publish Monday advisor GPT review snapshot |

## Main Entry Points

| Interface | Entry point | Purpose |
|---|---|---|
| General CLI | `rfic_transformer_inverse_design/interfaces/cli.py::main` | create-only GDS, EM evaluation, optimization, dataset sampling, lumped comparison |
| General GUI | `rfic_transformer_inverse_design/interfaces/gui_qt.py::main` | interactive general workflow |
| Three-input Q-sweep CLI | `rfic_transformer_inverse_design/synthesis/q_sweep_cli.py::main` | Lp/Ls/|K| to 11 Q candidates and proxy/physical selection |
| Three-input Q-sweep GUI | `rfic_transformer_inverse_design/synthesis/q_sweep_gui.py::main` | local web form, jobs, candidate table, safe downloads |
| GDS exporter | `rfic_transformer_inverse_design/layout/export.py::export_transformer_layout` | real `gdstk` GDS plus manifest/previews |
| Dataset builder | `scripts/build_physical_feature_inverse_training_table.py::main` | simulator rows to auditable inverse-training table |
| Tandem trainer | `scripts/train_physical_feature_tandem_inverse.py` | forward surrogate plus inverse MLP training |
| Public test runner | `tools/run_public_tests.py::main` | reproducible suite excluding private site tests and one declared stochastic node |

Console-script mappings are in `pyproject.toml`.

## Currently Runnable Functions

- `VERIFIED`: importable Q-sweep proxy logic, deterministic 11-candidate selection, physical response validation, no-clobber outputs, and GUI artifact containment.
- `VERIFIED`: general GDS exporter code writes binary GDS through `gdstk` and records manifests.
- `VERIFIED`: focused fixed-target/Q-sweep tests passed in the current dirty working tree before this checkpoint; see Test Status.
- `VERIFIED`: CI contract installs `.[test]` on Python 3.10 and 3.13 and invokes `python tools/run_public_tests.py`.
- `REVERIFY`: commands needing private weights can run only when a directory matching the three published SHA-256 identities is available.
- `REVERIFY`: general EMX/create-only commands require a valid process/config environment and have not yet been rerun for this checkpoint.

## Currently Not Proven Runnable

- Current exact-Q physical backend on MARS: `REVERIFY`.
- End-to-end GUI `下载 GDS` using eleven fresh EMX candidates: `REVERIFY`; the public release did not run the backend.
- CHTC submission and current queue state: `UNKNOWN`.
- Current EMX license availability and throughput: `REVERIFY`.
- Current HFSS automation/correlation: `REVERIFY`; it is not a consequence of an EMX pass.
- Formal fixed10k public report release: `NO-GO` because of unresolved evidence-interface findings.
- Strict causal 100k/200k scaling experiment: `BLOCKED` by normalization/envelope/weight refitting under the current trainer.

## Recent Completed Work

1. Added the hash-bound three-input MLP Q-sweep CLI/GUI and deterministic candidate binding in base HEAD `36e8a7667ad3c4c64aea8d5322312f728fcf5640`.
2. Published strict contract blockers and negative evidence in commits `f5a65b9`, `e53cce6`, and `533b73d`.
3. Generated a private fixed-10k proxy audit in a no-clobber offline directory. It is not committed and not fresh EMX.
4. Preserved historical Qmin 12-18 real-EMX evidence separately; it is a different model/semantic contract and is not current-app validation.
5. Completed a 200,000-source-row engineering training diagnostic: 161,446 gradient rows, 19,135 validation rows, and 19,419 sealed test rows. The tracked status marks it non-causal, proxy-only, and blocked from strict literal comparison.

## Test Status

### Current working-tree focused test

```bash
.venv/bin/python -m pytest \
  tests/test_fixed_target_evaluation.py \
  tests/test_q_sweep_synthesis.py -q
```

Result before checkpoint: `10 passed`; warnings were emitted. This includes the five uncommitted paths, so it is not evidence for base HEAD alone.

### Committed release evidence

`docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json` records:

- focused tests: 7 passed, 0 failed;
- desktop and 390x844 mobile web smoke: pass;
- legacy runtime regression: pass;
- wheel build: pass;
- physical backend: not run;
- full-suite branch: 1220 passed, 165 failed, 48 skipped;
- full-suite baseline: 1213 passed, 165 failed, 48 skipped.

The unchanged 165 failures are preserved as pre-existing failures; the repository release is not represented as a clean full suite.

### Current working-tree public suite

```bash
.venv/bin/python tools/run_public_tests.py
```

Result at this checkpoint: `1086 passed, 52 skipped, 1 deselected in 78.90s`; exit code 0. This result includes the five pre-existing uncommitted code/test paths and the documentation changes, so it must not be attributed to base HEAD alone.

## Main Artifacts

Tracked public evidence:

- `rfic_transformer_inverse_design/synthesis/real10k_model_contract.json`
- `docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json`
- `docs/research/CURRENT_STATUS.md`
- `docs/research/KNOWN_NO_GO_20260823.md`
- `docs/research/DEPLOYED100K_EXACT_CONTRACT_ON_200K_STATUS_20260824.json`
- `docs/research/DEPLOYED100K_EXACT_CONTRACT_ON_200K_STRICT_BLOCKER_20260824.json`
- `docs/MARS56_GROUNDED_S4P_PORT_CONTRACT_20260702_CN.md`
- `research_snapshot/20260823/` and its manifests/receipts

Private/offline artifacts, intentionally not committed:

- frozen model summary, weights, and training table identified by the public contract hashes;
- fixed-10k target frame and proxy audit, latest summary SHA-256 `5376b49966008808c745f4e36b9985e07dabd70b245bca2377767184a4f0dbbc`;
- historical Qmin real-EMX GDS/S4P artifacts;
- foundry PDK/process/Calibre files, sensitive GDS, credentials, and license data.

No GDS, S4P/S8P, NPZ weights, PPTX, HTML report, or wheel is currently tracked in this public repository.

### Evidence Conflicts And Precedence

- `docs/research/CURRENT_STATUS.md` is a 2026-08-23 snapshot and says a strict nested experiment was still running. It is not a 2026-08-24 process receipt. The newer 200k status proves a separate engineering diagnostic completed; it does not prove the older nested experiment completed.
- Old configs/conversation history contain eight-port `.s8p` paths. The latest explicit current contract is the grounded four-RF-port `.s4p` document and grounded template.
- The generic Python EMX config defaults to `single_ended_floating`; the current grounded campaign template explicitly overrides it with `single_ended_shield_grounded`.
- The deployed/presented historical 100k model used seed 20260713, while the final historical registry selected seed 20260714. `docs/research/REFERENCE_100K_SELECTION_UNPROVEN.json` therefore forbids treating them as one reference identity.

## External Runtime Snapshot

A read-only MARS check on 2026-08-24 confirmed that an existing SSH control connection could read the host. It observed old watcher/GUI processes, high system load, and no matching active `emx` process for the current task. This proves access at that instant only; it does not prove current license, backend, or campaign readiness. Status: `REVERIFY` before any new external run.

## Current Blockers And Risks

- Review the dirty `analysis/__init__.py` API regression before committing that code.
- Obtain and validate the exact private physical-backend command and verify it uses the authoritative GDS/process/port contract.
- Decide whether the production Q requirement is exact symmetric matching or one-sided Qmin; do not silently mix them.
- Repair formal release immutability and role-identity failures before publishing fixed10k accuracy charts.
- Do not infer dataset-size causality from historical 100k/200k runs.
- Do not report 7,298 survivors as 10,000 fresh-EMX tests or extrapolate to rejected targets.

## Uncommitted Work After This Documentation Commit

The five code/test paths listed in Git Status remain intentionally uncommitted. No training, GDS, DRC, EMX, HFSS, or application feature change is included in the context checkpoint.
