# Technical Decisions And Rejected Routes

Each decision records its scope and evidence. A later change requires a new decision rather than silently rewriting history.

## D001 - Physical Features Replace Zin As The Current Inverse Target

- **Decision:** Current inverse models consume `Lp`, `Ls`, Q, and `|K|`, not `Re(Zin), Im(Zin)`.
- **Reason:** The supervisor's design specifications are expressed as transformer physical features, and the training table can derive them directly from EM results.
- **Evidence:** `scripts/build_physical_feature_inverse_training_table.py`, `real10k_model_contract.json`, and commit `36e8a7667ad3c4c64aea8d5322312f728fcf5640`.
- **Rejected route:** Treating Zin as the current frozen-model contract. Historical Zin experiments remain historical evidence.
- **Re-evaluate when:** A versioned broadband/Zin model, dataset, test frame, and physical closure protocol are approved.

## D002 - The Frozen Real10k Model Is Hash-Bound

- **Decision:** The current model is `real10k_center15ghz_seed20260711`; summary, weights, and table must match their published SHA-256 identities.
- **Reason:** A model name alone cannot prevent accidental use of a different split, seed, architecture, or training table.
- **Evidence:** `rfic_transformer_inverse_design/synthesis/real10k_model_contract.json`; private artifacts independently matched the published hashes.
- **Rejected route:** Loading arbitrary NPZ/summary files based only on filenames.
- **Re-evaluate when:** A new model version has a new public contract and validation receipt.

## D003 - Current Application Uses Three User Inputs Plus An Internal Q Sweep

- **Decision:** User inputs are Lp, Ls, and |K|. Q is scanned at exact integers 10-20, yielding eleven candidates.
- **Reason:** This preserves one frozen 4-D model while searching the Q coordinate deterministically.
- **Evidence:** `q_sweep.py::Q_SWEEP_VALUES`, `PhysicalTarget3`, and `docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json`.
- **Rejected route:** Letting the GUI accept an arbitrary Q grid or silently generate fewer candidates.
- **Re-evaluate when:** The production requirement changes and a new versioned Q contract is validated.

## D004 - Current Selection Score Is Symmetric Four-Feature Range-Normalized RMSE

- **Decision:** Score all four errors after division by `[2.5,2.5,20,0.8]`; choose the minimum, with lower Q as exact tie-breaker.
- **Reason:** Features have different units and scales; the fixed declared spans make the score deterministic and auditable.
- **Evidence:** `q_sweep.py::_candidate_metrics` and release receipt.
- **Rejected route:** Selecting the visually nicest curve, selecting on proxy Q alone, or changing weights between runs.
- **Re-evaluate when:** A preregistered one-sided Qmin objective or task-specific weights are approved and tested.

## D005 - Exact-Q And Qmin Are Different Contracts

- **Decision:** The current Q-sweep score treats Q symmetrically as an exact target. The historical Qmin 12-18 experiment treats `min(Qp,Qs)` as a lower bound and remains separate.
- **Reason:** A value above a minimum can be acceptable, but it is not an exact match; combining the two definitions corrupts error interpretation.
- **Evidence:** `q_sweep.py`; historical private `qmin_sweep_real_emx_summary.json` with conclusion `SUPPORTED_AS_IMPORTANT_FACTOR_NOT_EXACT_Q_PROOF`.
- **Rejected route:** Using historical Qmin improvements as proof that the current exact-Q application passed physical validation.
- **Re-evaluate when:** The supervisor explicitly freezes one semantic and the score/tests are versioned accordingly.

## D006 - Proxy Results Are Candidate Diagnostics, Never Physical Labels

- **Decision:** `FROZEN_FORWARD_PROXY_DIAGNOSTIC` can rank or diagnose candidates only. Physical selection requires `FRESH_REAL_EMX` for all eleven.
- **Reason:** The inverse and forward models can share systematic error and cannot independently validate themselves.
- **Evidence:** `q_sweep.py::_run_physical_backend`, public model contract scientific boundary, and release receipt physical status.
- **Rejected route:** Plotting surrogate curves as if they came from EMX, ADS, HFSS, or measurement.
- **Re-evaluate when:** Never for evidence naming; even a calibrated proxy remains a proxy.

## D007 - Selected Candidate, Q, Geometry, GDS, And S4P Must Be Bound

- **Decision:** The physical response must contain the exact candidate set, candidate ID, Q, and geometry SHA. Selected GDS/S4P are copied and rehashed into deliverables.
- **Reason:** Prevents evaluating one geometry and delivering another.
- **Evidence:** `q_sweep.py::_write_backend_request`, `_run_physical_backend`, `_validated_backend_artifacts`, and `_copy_selected_physical_artifacts`.
- **Rejected route:** Selecting by filename, preview image, directory order, or unbound backend output.
- **Re-evaluate when:** Schema changes; binding itself remains mandatory.

## D008 - Real GDS Comes From A Structured Exporter

- **Decision:** The tracked general authority is `layout/export.py::export_transformer_layout`, which writes a `gdstk` GDS and manifest. A preview is never a GDS.
- **Reason:** Physical verification requires exact hierarchy, layers, datatypes, labels, and coordinates.
- **Evidence:** `layout/export.py` constructs `gdstk.Library` and calls `write_gds`; `core/types.py` declares manifest and process fields.
- **Rejected route:** Sending PNGs, ADS screenshots, hand-drawn diagrams, or approximate polygons as layout evidence.
- **Re-evaluate when:** A replacement exporter proves byte-level and process-level equivalence under a new receipt.

## D009 - Current EMX Output Contract Is Four-Port S4P

- **Decision:** Exported RF ports are P001-P004; P005-P008 are ground-only reference labels. The result is `.s4p` with primary P001-P002 and secondary P003-P004.
- **Reason:** Current manifest exports four RF signal ports with local M5 references; auxiliary vertical labels are not Touchstone ports.
- **Evidence:** `docs/MARS56_GROUNDED_S4P_PORT_CONTRACT_20260702_CN.md`.
- **Rejected route:** Treating all eight physical labels as eight Touchstone ports in the current flow.
- **Re-evaluate when:** An explicitly versioned `.s8p` topology, port order, extraction equations, and EMX/HFSS cross-check are approved.

The generic Python default `single_ended_floating` is not the production grounded choice. The current template must explicitly set `single_ended_shield_grounded`; omitting that override is a configuration failure.

## D010 - Foundry DRC, Fresh EMX, And HFSS Are Separate Gates

- **Decision:** None of these gates implies another passed.
- **Reason:** They test different properties: manufacturability, one solver/process response, and cross-solver correlation.
- **Evidence:** `q_sweep.py` scientific boundary, release receipt, and `AGENTS.md`.
- **Rejected route:** Calling a DRC-clean layout electrically correct or an EMX result HFSS-validated.
- **Re-evaluate when:** Never; only the exact gate evidence can support its claim.

## D011 - Preserve Denominators And Negative Evidence

- **Decision:** Keep source rows, gradient rows, validation/test rows, accepted EMX rows, unique geometries, and survivors distinct. Preserve failures and rejected samples.
- **Reason:** Denominator substitution inflates performance and destroys reproducibility.
- **Evidence:** `docs/research/KNOWN_NO_GO_20260823.md` and private frozen-model split summary.
- **Rejected route:** Calling 7,298 survivors “10,000 EMX tests,” calling a source table the training set, or deleting NO-GO receipts.
- **Re-evaluate when:** Never; only terminology can be refined.

## D012 - Historical 100k/200k Results Are Not A Dataset-Size Causal Experiment

- **Decision:** Report them as descriptive engineering comparisons only.
- **Reason:** Architecture, decoder, normalization, split, seed, optimizer budget, or inference path were not all held fixed.
- **Evidence:** exact-contract status/blocker receipts in `docs/research/`; commits `533b73d`, `e53cce6`, and `f5a65b9`.
- **Rejected route:** Claiming the 200k result improved because it used more data.
- **Re-evaluate when:** A nested 10k/20k or 100k/200k experiment freezes all non-data variables and has complete receipts.

## D013 - Current Trainer Cannot Yet Prove Strict Exact-Contract Scaling

- **Decision:** Do not launch a “corrective” causal scaling run with the current trainer while it refits normalization, geometry envelopes, and forward weights.
- **Reason:** Those refits violate the strict control-variable contract.
- **Evidence:** `docs/research/DEPLOYED100K_EXACT_CONTRACT_ON_200K_STRICT_BLOCKER_20260824.json` and commit `f5a65b978ec709330246cbc5dea68a9362664acd`.
- **Rejected route:** Renaming an own-forward-proxy diagnostic as a strict controlled comparison.
- **Re-evaluate when:** The trainer supports frozen reference arrays/envelopes/forward surrogate or the scientific contract is explicitly changed.

## D014 - Physical Mode Fails Closed

- **Decision:** Missing backend, incomplete candidate set, bad schema/source, hash mismatch, unsafe path, missing artifacts, non-finite response, or non-positive Q terminates the run.
- **Reason:** Partial success is unsafe for physical handoff.
- **Evidence:** `q_sweep.py::execute_q_sweep`, `_run_physical_backend`, and tests in `tests/test_q_sweep_synthesis.py`.
- **Rejected route:** Falling back silently to proxy output while labeling the run physical.
- **Re-evaluate when:** Never; new failure cases should be added, not weakened.

## D015 - The Download-GDS UI Is Conditional

- **Decision:** The frontend exposes a GDS link only when physical mode returns a path-contained real GDS artifact.
- **Reason:** Proxy mode has no GDS and must not offer a misleading download.
- **Evidence:** `q_sweep_gui.py::_collect_artifacts` and the HTML rendering loop.
- **Rejected route:** Generating a fake `.gds` link from the PNG preview.
- **Re-evaluate when:** The backend contract changes, while preserving real-artifact gating.

## D016 - Fixed10k Public Release Remains NO-GO

- **Decision:** Do not publish its final accuracy table/charts as approved evidence.
- **Reason:** Independent review found post-PASS identity mutability, role aliasing, and survivor-extrapolation weaknesses.
- **Evidence:** `docs/research/KNOWN_NO_GO_20260823.md`.
- **Rejected route:** Hiding the NO-GO column or regenerating into the same output directory until a pass appears.
- **Re-evaluate when:** A no-clobber successor binds the complete artifact graph atomically and passes adversarial validation.
