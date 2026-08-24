# Codex Project Handoff

## Evidence Labels

- `VERIFIED`: supported by a tracked file, Git object, test, receipt, or checked local artifact.
- `REVERIFY`: previously supported but current runtime or external service state must be checked again.
- `UNKNOWN`: no sufficient evidence is available.
- `PLANNED`: design intent only; not implemented or not validated.

## Complete Project Goal

The project builds a traceable RFIC transformer inverse-design system for TSMC 65 nm-style M9/M10 layouts. A user supplies desired transformer physical features, the frozen inverse model proposes a bounded 10-D geometry, a deterministic Q sweep creates candidates, an authoritative exporter produces real GDS, and fresh EMX recomputes the physical response. The deliverable is a manufacturable, DRC-audited layout whose target, model, candidate, geometry, GDS, S-parameter file, and validation evidence are cryptographically bound. A surrogate prediction alone is not the deliverable.

Primary evidence: `rfic_transformer_inverse_design/synthesis/real10k_model_contract.json`, `rfic_transformer_inverse_design/synthesis/q_sweep.py`, `rfic_transformer_inverse_design/layout/export.py`, `docs/MARS56_GROUNDED_S4P_PORT_CONTRACT_20260702_CN.md`, and `docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json`.

## Current System Architecture

| Layer | Responsibility | Main files | State |
|---|---|---|---|
| Configuration and domain types | Geometry, bounds, topology, EMX, process, and artifact contracts | `rfic_transformer_inverse_design/core/types.py`, `rfic_transformer_inverse_design/core/defaults.py`, `rfic_transformer_inverse_design/api.py` | `VERIFIED` in code |
| Geometry and GDS | Validate geometry, build polygons/ports/ground structures, write actual GDS and manifest | `rfic_transformer_inverse_design/layout/export.py`, `rfic_transformer_inverse_design/layout/builders.py`, `rfic_transformer_inverse_design/layout/checks.py` | General exporter implemented; Q-sweep private-backend binding is `REVERIFY` |
| EM evaluation | Launch EMX or remote adapters, parse Touchstone, extract response | `rfic_transformer_inverse_design/sim/emx/`, `rfic_transformer_inverse_design/analysis/` | Public interfaces implemented; current private runtime is `REVERIFY` |
| Dataset | Build simulator rows and physical-feature-to-geometry training tables | `rfic_transformer_inverse_design/dataset.py`, `scripts/build_physical_feature_inverse_training_table.py` | Implemented; exact dataset identity is contract-bound |
| Frozen model | Load hash-bound summary/weights and run inverse plus forward proxy | `rfic_transformer_inverse_design/synthesis/frozen_mlp.py`, `rfic_transformer_inverse_design/synthesis/real10k_model_contract.json` | Implemented; weights intentionally private |
| Q sweep and selection | Generate 11 candidates, compute score, fail-closed physical binding, select one candidate | `rfic_transformer_inverse_design/synthesis/q_sweep.py` | Proxy implemented; current physical backend not run in public release |
| CLI and web UI | Accept targets, create jobs, show candidates, expose safe artifact downloads | `rfic_transformer_inverse_design/synthesis/q_sweep_cli.py`, `rfic_transformer_inverse_design/synthesis/q_sweep_gui.py` | Implemented; real GDS link appears only after physical success |
| Campaign and training orchestration | MARS queue generation, accepted-pool audits, checkpoint training | `scripts/run_accepted_1m_campaign_controller.sh`, `scripts/run_accepted_physical_feature_model_checkpoint.sh` | Historical code exists; current external execution state is `REVERIFY` |

## End-To-End Data Flow

1. **Input.** The current application accepts `Lp_nH`, `Ls_nH`, and `K_abs`; `design_id` is metadata. `PhysicalTarget3.validate()` enforces the frozen support in `q_sweep.py`.
2. **Candidate targets.** The code inserts each integer Q from 10 through 20, forming eleven vectors `[Lp, Ls, Q, |K|]` at 15 GHz.
3. **Inverse prediction.** One frozen inverse MLP maps each 4-D vector to the same ordered 10-D geometry contract.
4. **Proxy reconstruction.** The frozen forward surrogate maps each geometry back to `[Lp, Ls, Q, |K|]`. This is only `FROZEN_FORWARD_PROXY_DIAGNOSTIC` evidence.
5. **Proxy ranking.** The code calculates the fixed declared-range-normalized four-feature RMSE and breaks exact ties toward lower Q. Proxy selection is provisional.
6. **Physical request.** Physical mode writes all eleven candidate IDs, Q values, geometries, and geometry hashes to `physical_backend_request.json`.
7. **GDS and EMX.** A private backend must create a real GDS and `.s4p` for every exact candidate and return `FRESH_REAL_EMX` features at 15 GHz. The public repository intentionally excludes that backend and private assets.
8. **Fail-closed binding.** The application rejects missing candidates, Q mismatches, geometry-hash mismatches, unsafe paths, absent GDS/S4P files, non-finite features, and non-positive Q values.
9. **Physical ranking.** It recomputes the same four-feature score from fresh EMX using `Q=min(Qp,Qs)` and selects the minimum across all eleven candidates.
10. **Delivery.** Only the selected candidate's bound GDS/S4P are copied into `deliverables/`; the run manifest binds selected candidate, selected Q, geometry and hashes.
11. **Independent gates.** Foundry Calibre DRC and HFSS correlation are separate downstream gates. Fresh EMX does not imply either gate passed.

Evidence: `q_sweep.py::run_q_sweep`, `_run_physical_backend`, `_candidate_metrics`, `_validated_backend_artifacts`, and `_copy_selected_physical_artifacts`.

## Frozen Model Contract

### Identity

| Item | Value | Evidence |
|---|---|---|
| Model ID | `real10k_center15ghz_seed20260711` | `real10k_model_contract.json` |
| Model seed | `20260711` | same |
| Target frequency | 15 GHz | same |
| Source rows | 10,000 real-EMX-labelled unique geometries | contract plus private summary SHA below |
| Gradient-training / validation / sealed test rows | 7,871 / 1,227 / 902 | private summary `physical_feature_tandem_inverse_summary.json`, SHA-256 `90a81532...a3fa` |
| Split | grouped 4-D physical cells; no geometry identity overlap | same private summary |
| Inverse | `4 -> 256 -> 256 -> 256 -> 10` | public contract |
| Forward | `10 -> 256 -> 256 -> 256 -> 4` | public contract |
| Activation / projection | GELU / sigmoid to observed training envelope | public contract |
| Summary SHA-256 | `90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa` | public contract and checked private artifact |
| Weights SHA-256 | `ffea66dfdd0bb252e402e1ade70f5e8768511e26f2978b1d73b1817a0221e42a` | public contract and checked private artifact |
| Training-table SHA-256 | `3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8` | public contract and private summary |

The model weights and private summary are intentionally absent from the public repository. Their identities may be checked in the private offline evidence store; they must not be committed.

### Exact Inputs And Outputs

Inputs, in order:

1. `input__lp_nh_center`
2. `input__ls_nh_center`
3. `input__q_center`, trained as `min(Qp, Qs)`
4. `input__k_abs_center`

The GUI exposes only Lp, Ls, and |K|; Q is generated internally. Declared support is `[0.5,3.0] nH`, `[0.5,3.0] nH`, `[5,25]`, and `[0,0.8]` respectively.

Outputs, in order and in micrometres:

1. `geom__primary_outer_width_um`
2. `geom__primary_outer_height_um`
3. `geom__secondary_outer_width_um`
4. `geom__secondary_outer_height_um`
5. `geom__line_width_um`
6. `geom__primary_terminal_y_span_um`
7. `geom__secondary_terminal_y_span_um`
8. `geom__offset_um`
9. `geom__primary_feed_extension_um`
10. `geom__secondary_feed_extension_um`

The single `line_width` is shared by the modeled conductors. A generated geometry is not physical evidence until it passes layout/process validation and fresh EMX.

### Q Sweep And Score

- Q grid: exact integers 10, 11, ..., 20; eleven candidates.
- Feature order: `Lp`, `Ls`, `Q_scalar`, `K_abs`.
- Declared spans: `[2.5 nH, 2.5 nH, 20, 0.8]`.
- Score: `sqrt(mean(((abs(observed-target))/span)^2))`.
- Selection: minimum score; exact tie selects lower Q.
- Current score is symmetric exact-target matching. It is **not** a one-sided `Q >= Qmin` objective. The underlying training label nevertheless used `min(Qp,Qs)`, which is a semantic risk requiring explicit review before changing the contract.

Evidence: constants and `_candidate_metrics()` in `q_sweep.py`.

## Geometry-To-GDS And Process Contract

`layout/export.py::export_transformer_layout` is the tracked general authoritative exporter. It builds a `gdstk.Library(unit=1e-6, precision=1e-9)` and calls `write_gds`; previews are separate artifacts. Process draw/pin layer/datatype pairs are resolved from the process file and recorded in the manifest.

Public defaults in `TransformerEmxConfig` include:

| Role | Layer | Default datatype/purpose | Evidence |
|---|---:|---:|---|
| AP / M10 primary | 74 | draw datatype 0 unless process resolution overrides | `core/types.py` |
| M9 secondary | 39 | same rule | `core/types.py` |
| M5 shield/local ground | 35 | same rule | `core/types.py` |
| Cadence pin purpose | process layer | 51 | `core/types.py` |
| Labels | 135 | datatype 0 | `core/types.py` |

The exact private Q-sweep backend's top-cell name, resolved layer/datatype map, and use of this exporter are `REVERIFY`; the public release validates GDS existence and hash binding but does not inspect private GDS contents. A PNG structure preview and the approximate octagons drawn by `q_sweep.py` are explicitly non-GDS diagnostics and must never be relabeled as GDS or DRC evidence.

### Current Four-Port Contract

The latest explicit port contract is four RF signal ports and a `.s4p` result:

- `P001`: M10 primary top; local ground `P001_G`.
- `P002`: M10 primary bottom; local ground `P002_G`.
- `P003`: M9 secondary top; local ground `P003_G`.
- `P004`: M9 secondary bottom; local ground `P004_G`.
- `P005-P008`: vertical M5 ground-only reference labels, not Touchstone ports.
- Differential pairs: primary `P001-P002`, secondary `P003-P004`.
- Grid: 5-60 GHz, 0.5 GHz spacing, 111 points.

Evidence: `docs/MARS56_GROUNDED_S4P_PORT_CONTRACT_20260702_CN.md`.

The grounded campaign template `configs/mars_s4p_grounded_powerline_physical_feature_500_template.yaml` explicitly sets `single_ended_shield_grounded`, frequency endpoints/step, and Cadence pin purpose 51. The generic `TransformerEmxConfig` default is still `single_ended_floating`. Therefore production runs must use and audit the grounded config; relying on the Python default is a known contract hazard.

Older configuration files and conversation decisions mention eight exported ports and `.s8p`. Those are historical compatibility paths, not the current explicit contract. Any return to `.s8p` requires a new decision and complete revalidation.

## Dataset Fields And Denominators

`scripts/build_physical_feature_inverse_training_table.py` reads `dataset_rows.csv` and emits `physical_feature_inverse_training_table.csv`, a manifest, and a report. Each accepted row contains source identity, Touchstone path, `input__*` physical features, and `geom__*` output fields. It can fail closed on missing labels, geometry, or Touchstone evidence.

The builder's generic default still names `q_center,k_center`; the frozen model explicitly requires `q_center,k_abs_center`. Therefore any current model build must pass or verify the exact explicit columns rather than relying on defaults. This naming mismatch is a recorded risk, not permission to rename historical tables.

Always report source rows, actual gradient rows, validation rows, test rows, accepted real-EMX rows, and unique 10-D geometries separately.

## Frontend And Backend Responsibilities

Frontend responsibilities in `q_sweep_gui.py`:

- collect Lp, Ls, |K|, and design ID;
- enforce user-facing validation and one active job;
- poll job status, show all eleven candidates and evidence source;
- serve only path-contained artifacts;
- display `下载 GDS` and `下载 S4P` only when the physical result actually contains those files.

Public Python backend responsibilities:

- load hash-bound private model artifacts;
- create deterministic candidates and hashes;
- rank proxy results;
- serialize physical requests;
- validate the complete physical response and select/copy artifacts.

Private backend responsibilities (`REVERIFY`):

- convert each exact 10-D candidate through the authoritative foundry-aware exporter;
- run GDS checks/DRC as separately declared gates;
- run fresh EMX for all eleven candidates;
- return schema-valid, hash-bound GDS, S4P, Lp, Ls, Qp, Qs, and |K|.

The public web application is implemented and smoke-tested. End-to-end physical download remains incomplete in the public release because the private backend was not run; see `MLP_Q_SWEEP_GUI_RELEASE_20260824.json` status `PASS_WITH_PHYSICAL_BACKEND_NOT_RUN`.

## Proxy Mode Versus Physical Mode

| Property | Proxy mode | Physical mode |
|---|---|---|
| Evidence source | `FROZEN_FORWARD_PROXY_DIAGNOSTIC` | `FRESH_REAL_EMX` |
| Candidate count | 11 | exactly 11 required |
| GDS/S4P required | No | Yes, for every candidate |
| Selection meaning | provisional model diagnostic | fresh-EMX selection under current score |
| DRC/HFSS proof | No | Still no; separate gates |
| Missing backend behavior | not applicable | fail closed |

## Completed Work

- `VERIFIED`: frozen real10k model contract and hash identities are published without private weights.
- `VERIFIED`: three-input exact-Q CLI and web application are implemented at commit `36e8a7667ad3c4c64aea8d5322312f728fcf5640`.
- `VERIFIED`: eleven-candidate proxy generation, deterministic score/tie rule, no-clobber output, hash binding, safe downloads, and fail-closed physical schema are covered by focused release tests.
- `VERIFIED`: public release receipt reports 7/7 new focused tests passing, desktop/mobile smoke pass, wheel build pass, and unchanged pre-existing full-suite failure count.
- `VERIFIED HISTORICAL`: a separate Qmin 12-18 experiment produced 14 unique GDS, 14 DRC passes, 14 S4P files, and fresh EMX. It used a different frozen model and one-sided Qmin semantics, so it cannot validate the current exact-Q application.
- `VERIFIED PROXY ONLY`: the private fixed-target frame contains 10,000 deterministic targets, of which 8,000 are in support and 2,000 are high-K OOD. Its latest metrics are surrogate diagnostics, not fresh EMX.
- `VERIFIED ENGINEERING ONLY`: a historical 200,000-row accepted source table completed training with 161,446 gradient rows, 19,135 validation rows, and 19,419 sealed test rows. Its own-forward-proxy evaluation completed, but literal normalization/envelope identities differ and no fresh EMX was run; see `docs/research/DEPLOYED100K_EXACT_CONTRACT_ON_200K_STATUS_20260824.json`.

## Incomplete Or Blocked Work

- `REVERIFY`: current private Q-sweep physical backend command, current EMX license availability, and end-to-end eleven-candidate physical run.
- `REVERIFY`: exact private GDS top cell, resolved M9/M10/M5 datatypes, port labels, and exporter identity for the current Q-sweep backend.
- `UNKNOWN`: current CHTC job/submission state; no current public receipt proves it.
- `PLANNED`: controlled nested 10k-versus-20k training comparison under one frozen architecture, normalization, split, seeds, and optimizer-update budget.
- `PLANNED`: fresh-EMX evaluation of the frozen 10,000 target frame. The current 10,000-target report is proxy-only.
- `NO-GO`: current formal fixed10k release and report interfaces have unresolved immutability/identity/extrapolation findings in `docs/research/KNOWN_NO_GO_20260823.md`.
- `BLOCKED`: strict exact-contract 100k/200k causal comparison cannot proceed with the current trainer because it refits normalization/envelopes/weights; see the exact-contract blocker receipts under `docs/research/`.

## Latest Valid Test And Result Evidence

- Release receipt `docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json`: 7 focused tests passed; UI desktop/mobile smoke passed; wheel built; physical backend not run.
- The same receipt preserves the full-suite comparison: baseline and branch both had 165 failures, with branch passes increasing 1213 to 1220. This is not a clean full suite.
- Historical isolated repository receipt `docs/research/REPOSITORY_VALIDATION_20260823.json`: 1050 passed, 52 skipped, 1 deselected, 0 failed at that earlier snapshot.
- Current working-tree focused tests passed 10/10. The public runner passed 1086 tests with 52 skipped and 1 deselected. Details and scope are recorded in `docs/PROJECT_STATE.md`; the five pre-existing uncommitted code/test changes are excluded from this documentation commit.

## Most Important Technical Risks

1. Proxy performance can be excellent while fresh EMX closure is poor; never use the proxy as a physical label.
2. Current exact-Q selection semantics differ from the historical one-sided Qmin requirement. Changing this requires an explicit contract and controlled validation.
3. The private physical backend is not shipped, so public GUI success does not prove GDS generation or EMX execution.
4. The current dirty `analysis/__init__.py` replaces the prior `__all__` list and may break public imports; it is uncommitted and must be reviewed separately.
5. Old `.s8p`/eight-port artifacts can be confused with the current `.s4p` contract.
6. Historical 100k/200k comparisons are not causal because model, normalization, decoder, split, or training budget differed.
7. Survivor-conditioned fresh-EMX metrics cannot be extrapolated to rejected or missing targets.
8. `docs/research/CURRENT_STATUS.md` is a dated 2026-08-23 snapshot. Its live-process section is not current runtime evidence on 2026-08-24 and must be rechecked rather than copied forward.

## Mandatory Read Order For The Next Codex

1. `AGENTS.md`
2. `docs/CODEX_HANDOFF.md`
3. `docs/PROJECT_STATE.md`
4. `docs/CURRENT_TASK.md`
5. `docs/DECISIONS.md`
6. `docs/KNOWN_GOOD_COMMANDS.md`
7. `rfic_transformer_inverse_design/synthesis/real10k_model_contract.json`
8. `docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json`
9. `docs/research/KNOWN_NO_GO_20260823.md`
10. The latest task-specific receipt, manifest, log, and SHA-256 index

After reading, run `git status`, verify current processes and artifact hashes, and do not resume training or EMX merely because an old conversation requested it.
