# 15 GHz Transformer Inverse Design: Research and Reproduction Handover

Prepared for Professor Liu and the next researcher · 12 October 2026

This document replaces the proposed in-person handover. It explains what has been completed, how to access and reproduce it asynchronously, and what remains unresolved. It does not authorize or automatically launch training, layout generation, or electromagnetic simulation. Existing results should be inspected before repeating any experiment.

For exact commands, start with [QUICKSTART](QUICKSTART.md). For relocated private assets, see [ASSET_MAP](ASSET_MAP.md). The GitHub README at the code repository is the current entry point; dated production instructions remain archival.

## 1. Repository and access model

The public code repository is [rfic-transformer-inverse-design](https://github.com/wyf9218/rfic-transformer-inverse-design). The real dataset, model weights, and private experiment evidence belong in the separate, access-controlled companion repository: [rfic-transformer-inverse-design-data](https://github.com/wyf9218/rfic-transformer-inverse-design-data). Professor Liu and nominated researchers need an explicit invitation to that repository. A public clone alone is not a complete research installation.

Use the published asset manifest to resolve dataset, model, split, result, and archive locations. Publication paths take precedence over historical workstation paths. The paths below identify existing code entries or archive members; an archive member is not a claim that it is present in the public repository. Preserve the manifest's original identifiers and recorded hashes when relocating files.

Credentials, SSH keys, authentication cookies, license files, and proprietary process-design-kit contents are not part of either research handover package. Licensed tools and process data must be provided through the institution's authorized environment. Access to a CSV does not automatically provide access to every raw S-parameter file referenced inside it.

## 2. Dataset identity and physical meaning

The original 100K dataset is the historical `physical_feature_inverse_training_table.csv`, not the later 6,329-row development set, the separate real10k reference, or a subsequently merged qualification ledger. Its recorded SHA-256 is:

`fe94d15b0d083330fe7a64d3e059d083a5aa32d73394c85fc4b881e9904e68a2`

Existing frozen records identify **100,000 rows and 100,000 unique geometries**. The historical split is **78,891 gradient-training / 9,740 validation / 11,369 test**. “100K-trained model” therefore describes the source dataset, not 100,000 rows used in gradient updates. Do not repartition it to make the old experiment exactly 80:10:10.

The training table contains 15 GHz labels in this order: **Lp (nH), Ls (nH), Q, |k|**. Q is defined as `min(Qp, Qs)`; display it as Q, because this label definition does not itself imply a minimum-Q optimization objective. The table's coupling label is an absolute value; it must not be described as a preserved signed-k column. Recorded raw-response sweeps use 5–60 GHz in 0.5 GHz increments, or 111 points. The single-frequency training table and the broadband raw responses are different artifacts.

The ten geometry fields, all in micrometres, are fixed in the following order:

| Index | Field | Interpretation |
|---|---|---|
| 1–2 | `primary_outer_width_um`, `primary_outer_height_um` | Primary nominal winding-path dimensions |
| 3–4 | `secondary_outer_width_um`, `secondary_outer_height_um` | Secondary nominal winding-path dimensions |
| 5 | `line_width_um` | One shared line-width variable |
| 6–7 | `primary_terminal_y_span_um`, `secondary_terminal_y_span_um` | Signal-feed centreline separations |
| 8 | `offset_um` | Signed horizontal secondary-to-primary displacement |
| 9–10 | `primary_feed_extension_um`, `secondary_feed_extension_um` | Nominal feed extensions |

Original data, flags, and historical splits remain unchanged. Later offline descriptive plots removed additional K, inductance-window, and half-SRF filters; this does not retroactively change an old model's training support or validation protocol. Missing values, numerical failures, and unverified physical compatibility must remain distinguishable.

## 3. Which model is being reproduced?

Three frequently discussed models are different research objects. Architecture counts include biases.

| Model identity | Forward F | Inverse I | Decoder and inverse objective |
|---|---|---|---|
| Original 100K, `current_foundry_qmin_response_only_seed20260713` | 10→256→256→128→4; 102,020 parameters | 4→512→512→256→10; 399,114 parameters | `hard_feasible_topology_v1`; minimum-Q objective, +2 training margin |
| `real10k_center15ghz_seed20260711` | 10→256→256→256→4; 135,428 parameters | 4→256→256→256→10; 135,434 parameters | Independent sigmoid bounds; symmetric four-feature objective |
| `legacyrecipe_D100k_seed20260711` | Same widths and parameter counts as real10k | Same widths and parameter counts as real10k | Recorded symmetric recipe reconstructed on the original 100K |

All use GELU hidden activations. The original 100K ran 3,600 updates per F/I stage, batch 4,096; real10k and the September 21 reconstruction ran 1,200 updates per stage, batch 1,024. The real10k split is 7,871/1,227/902. Its stored test-prediction file is empty: 902 reserved test rows must not be reported as a completed test evaluation.

Training first fits F to EM-derived labels, selects its checkpoint using validation, then trains I through a frozen F. Freezing F means its parameters are not updated; gradients with respect to its geometry input must still reach I. The NumPy implementation propagates those input gradients explicitly. EMX is not called during each gradient update. A 0.01 geometry-anchor term is present; the original 100K additionally uses a 0.02 topology term. These are not completely geometry-unsupervised models.

Normalizers and geometry bounds are fitted from each model's own training partition. Normalization arrays are embedded in the weight bundle. Keep weights, feature order, normalizer, decoder, split, and configuration together. The public `FrozenTandemMLP` loader defaults to the real10k contract; selecting an original100K folder without selecting its contract is unsafe. The historical toolkit has its own matching loader and bundle.

The hard decoder couples terminal and feed constraints to winding geometry. Independent sigmoid mapping only bounds each dimension separately. Neither is a substitute for grid-consistent layout, DRC, or physical validation. Changes to a decoder create a new method/version, not a harmless display adjustment.

## 4. Inference and the meaning of accuracy

The original100K application is one-shot: one inverse pass returns one candidate, with F used for diagnosis. Its historical inverse loss treats Q as a lower bound; the +2 margin belongs to training, not an extra inference input offset.

The later requested symmetric procedure fixes `(Lp, Ls, |k|)`, enumerates integer Q values 10 through 20, generates eleven candidates, and ranks them using the frozen forward prediction:

`score = sqrt(mean(((F(candidate) - target) / [2.5, 2.5, 20, 0.8])**2))`.

The inductance spans are in nH. Select the minimum score, breaking exact ties toward lower Q. This is neither maximum-Q selection nor a minimum-Q constraint. Afterwards, report each feature's absolute target-relative error; the joint error is their maximum, and a symmetric joint hit requires all four errors to be at most 10%. Ranking and reporting have different denominators. Never use EM results afterwards to replace the preselected candidate while calling it the same trial.

Three evidence classes must stay separate: **SELF_PROXY** uses the model's own F; **COMMON_PROXY** uses a declared shared F; **EM evidence** requires actual candidate-bound physical outputs. A common proxy remains a surrogate. A held-out EM-labelled geometry also differs from a newly synthesized geometry validated with fresh EMX.

## 5. Completed results and their limits

The data-size series uses nested samples within the original train, validation, and test partitions. D10k is 8,000/1,000/1,000; D50k is 40,000/5,000/5,000; D100k retains the historical split. The common forward evaluation contains 11,369 held-out EM-labelled rows. Seeds are **20260713, 17, and 29**. The following are existing three-seed means under the historical minimum-Q recipe, not the symmetric Q-scan experiment:

| Dataset size | Lp MAE (nH) | Ls MAE (nH) | Q MAE | Absolute-k MAE | COMMON_PROXY joint hit |
|---|---:|---:|---:|---:|---:|
| D10k | 0.10791 | 0.08102 | 0.49093 | 0.02611 | 5.97% |
| D50k | 0.08959 | 0.06235 | 0.32632 | 0.01503 | 10.09% |
| D100k | 0.08332 | 0.05753 | 0.28492 | 0.01274 | 11.94% |

Historical joint acceptance means Lp/Ls/|k| relative errors ≤10% and Q at least the requested value. The shared evaluator is the original100K seed20260713 F, so evaluation of that particular model is self-referential. These percentages are not physical success rates. Saved CSVs include sample standard deviations, not confidence intervals.

A separate same-100K width series compares three hidden layers of 128, 256, and 512 units for each F/I pair. Their mean Lp MAEs are 0.10027, 0.08742, and 0.07193 nH, while common-proxy inverse hits are 9.96%, 10.34%, and 10.17%. Better forward error therefore did not imply monotonically better inverse hit rate. Recorded wall times were not controlled for server load.

The symmetric Q-scan results on the same 8,000 application targets are 6,468/8,000 (80.85%) for real10k, 4,492/8,000 (56.15%) for externally scanned original100K weights, and 6,192/8,000 (77.40%) for the reconstructed symmetric100K. The latter reaches 4,220/10,000 (42.20%) on a wider target frame. All are SELF_PROXY. Differences involve architecture, decoder, recipe, evaluator, and/or target distribution; they are not isolated causal effects of dataset size or Q loss.

The reconstructed100K forward model has saved train/validation feature-balanced normalized RMSEs of 0.1013388/0.1364538 after 1,200 updates. On 11,369 held-out EM-labelled rows, MAPE is 3.84%, 3.21%, 5.06%, and 5.78%; mean maximum-four-feature relative error is 8.3352%, and joint ≤10% is 8,223/11,369. These are forward results, not fresh inverse-design EM accuracy.

The completed paired64 trial belongs to original100K one-shot/minimum-Q inference: 8 candidates hit in both proxy and EM, 1 only in proxy, 0 only in EM, and 55 in neither. All 64 reached terminal results. Do not attach these EM results to symmetric Q-scan candidates. Likewise, an overlay of 8,000 real10k inverse proxy points and 1,227 existing validation EM labels is an **unpaired distribution illustration**, not paired coverage validation.

## 6. Software packaging and entry-point inventory

Paths without an `archive:` prefix are relative to the public code repository. `archive:` identifies a member whose delivered location is resolved by the private companion manifest. This inventory describes existing implementations; it does not claim that every entry has been retested in the recipient's environment.

| Component | Purpose and entry | Required private dependencies | Verified status and limitation |
|---|---|---|---|
| Web GUI | `synthesis/q_sweep_gui.py` and `synthesis/q_sweep_ui.html` under `rfic_transformer_inverse_design/`; launcher `apps/mlp_q_sweep_gui/START_LOCAL.sh` | Matching model bundle; writable private output directory | Existing proxy/layout/physical modes. Start in proxy mode; historical software checks are not a new installation test. |
| CLI/toolkit | `rfic-transformer-q-sweep`; archive: `bin/transformer-synth` | Correct model contract and weights | Historical original100K single-inference smoke succeeded. Toolkit inference is not the same as GUI eleven-Q processing. |
| Loader and decoder | `synthesis/frozen_mlp.py`, `synthesis/model_adapter.py`; original archive `model/trainer_runtime.py` | Exact summary, NPZ, normalizer, decoder contract | Version-bound loading; default real10k contract must not silently substitute for original100K. |
| Training | archive: `dataset_size_811_20260921_v1/train_dataset_sizes.py`, `run_all.py`; `train_legacy_recipe.py` | Dataset, splits, frozen trainer and helper modules | Existing experiments complete. Historical paths/budgets require adaptation before any new run. |
| Plot/export | archive: `replot_repeat_csv.py`, `aggregate_repeats.py` | Small source CSVs and their manifest; no weights for CSV-only rendering | Portable CSV replot has been exercised. Full postprocessing may invoke inference and is a separate operation. |
| Layout/physical adapters | `synthesis/q_sweep_layout_backend.py`, `synthesis/q_sweep_physical_backend.py` | Licensed Cadence/Calibre/EMX environment, authorized process configuration | Evidence-binding interfaces exist. Availability of public adapter code does not establish native readiness or DRC approval. |
| Data inspection | archive: `inspect_dataset_readonly.py` | Original CSV; raw-response access separately | Reads a bounded number of rows using the standard library; no training or simulation. |
| Installation | `pyproject.toml`; `apps/mlp_q_sweep_gui/` launchers | Python environment; optional GUI/optimization dependencies | Package entry points are declared. Native-tool installation and research assets are not installed by pip. |

The GUI physical mode historically evaluates all eleven Q candidates and can rank actual EM results. That is a different experiment from preselecting one proxy candidate before EM. Do not merge their claimed performance.

## 7. Asynchronous reproduction sequence

1. **Obtain access and pin identities.** Clone the public release and private companion, record both commit IDs, and verify transferred assets against their supplied manifests. A missing asset is a transfer issue, not a reason to regenerate data. Never edit expected hashes to suppress a mismatch.
2. **Read one dataset row.** Run the supplied inspector with `--dataset <CSV> --rows 1`. Expect row identity, four physical labels, ten geometry values, and the raw-response reference. Explain the units and original split before proceeding.
3. **Replot one existing result without models.** Use the delivered `replot_repeat_csv.py`, supplied aggregate CSVs, original completion identity, and a new output directory. Expect SVG/PNG files plus source/render receipts. Open the previews; file creation alone does not verify presentation quality.
4. **Load one explicitly selected bundle.** Use the archived original100K toolkit's `--mode infer`, or the documented public loader with an explicit contract. Use a new private output directory. The historical example is `--lp-nh 0.833 --ls-nh 0.620 --q-min 10 --k-abs 0.487`. Expect `geometry_candidates.csv`, `inference_results.json`, and `run_manifest.json`; verify seed20260713 and physical status `NOT_RUN`. This example intentionally demonstrates the historical minimum-Q model.
5. **Inspect before extending.** Trace one figure to its CSV, target frame, model, evaluator, and failed-request denominator. Trace one physical case to candidate identity, actual GDS cell/hash, DRC evidence, EMX command, and S4P. Only then define any new experiment.

Exact runnable commands and relocated asset paths belong in the release's command guide and private manifest. Do not run archived training commands against completed output directories. Some `check` commands write receipts. Existing `resume` semantics mainly reuse completed stages; arbitrary-update Adam/RNG-state continuation is not guaranteed.

## 8. Physical structure, limitations, and next decisions

The documented case002 is an original100K model-generated example, not a proven training-table member. Its actual EMX-selected cell is `TRANSFORMER_001_4d59a6b8`. M10/AP carries the primary, left signal feeds, and right centre-tap connection; M9 carries the secondary, right signal feeds, and left centre-tap connection. M5 is the sample's slotted ground frame. Eight physical endpoints include four RF ports and four grounding endpoints; auxiliary vertical conductors do not short the signal feeds. Projected crossings do not necessarily contain vias.

Identical ten-parameter vectors can still represent different physical versions when grounding, ports, endpoints, or generation rules differ. Replacing an original GDS with a newly generated one invalidates the old GDS–S4P binding. Historical feed-length and post-grid endpoint failures remain useful development evidence, not proof that all current candidates are corrected.

Open issues include the original large candidate pool's incompletely recovered sampling provenance, unavailable exact real10k trainer bytes, incomplete physical validation of the symmetric recipe, target-support gaps, and deployment portability. The September reconstruction is a **recorded-recipe reconstruction**, not a bitwise replay. Fixed update budgets do not prove convergence. Do not describe the original100K source as pure LHS without source evidence.

The next researcher should first choose the paper's primary protocol: historical minimum-Q one-shot or symmetric Q-scan. Reuse completed experiments, then decide whether a new controlled comparison or paired physical validation is scientifically necessary. Do not silently restart old production controllers or presume historical resource permissions remain current. No new large experiment is required to accept this handover.

## 9. Written acceptance checklist

The recipient can return this checklist asynchronously with output paths and concise observations; no meeting is required.

- [ ] Both repository commit IDs and private access are recorded; required datasets and weights are actually downloadable.
- [ ] The original100K row, geometry order, units, split, and one raw-response reference can be located independently.
- [ ] A selected model's identity, normalizer, decoder, Q objective, and checkpoint are explained correctly.
- [ ] One CSV-only figure is reproduced in a fresh directory with its original sample count and evidence class.
- [ ] One explicitly selected model is loaded and produces a single candidate without invoking native simulation.
- [ ] One proxy result and one physical result are traced to their distinct source artifacts.
- [ ] Missing assets, platform errors, unresolved scientific questions, and the next responsible owner are recorded in writing.

Completing these checks establishes an operational transfer. It does not imply that every historical research objective has been completed or that proxy performance guarantees physical success.
