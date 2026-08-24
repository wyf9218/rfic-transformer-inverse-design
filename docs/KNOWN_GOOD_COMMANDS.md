# Known Good Commands

## Status Vocabulary

- `VERIFIED CURRENT`: executed successfully against the current working tree during this checkpoint.
- `VERIFIED RECEIPT`: successful in a named tracked receipt/CI contract, but not rerun in this checkpoint.
- `HISTORICALLY VERIFIED`: successful for an older or different contract; do not assume current equivalence.
- `REVERIFY`: plausible and code-backed, but current external environment or private artifacts were not exercised.
- `UNVERIFIED`: documented or planned but no successful evidence was found.

Run commands from the repository root unless stated otherwise. Replace placeholders with non-secret paths. Always use a new no-clobber output directory.

## Environment

CI install contract, `VERIFIED RECEIPT` in `.github/workflows/ci.yml`:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

Existing local environment activation, `VERIFIED CURRENT` because `.venv/bin/python` ran the focused tests:

```bash
source .venv/bin/activate
python --version
```

Python requirement is `>=3.10`; CI covers Python 3.10 and 3.13. Do not assume another environment contains private model/PDK dependencies.

## Tests

Focused current test, `VERIFIED CURRENT`:

```bash
.venv/bin/python -m pytest \
  tests/test_fixed_target_evaluation.py \
  tests/test_q_sweep_synthesis.py -q
```

Observed result before checkpoint: `10 passed` with warnings. This uses the current dirty working tree.

Public reproducible suite, `VERIFIED CURRENT` and required for every change:

```bash
.venv/bin/python tools/run_public_tests.py
```

Current-checkpoint result: `1086 passed, 52 skipped, 1 deselected in 78.90s`; exit code 0. The older release receipt `docs/research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json` used a broader comparison context and preserves 165 pre-existing failures; do not rewrite that historical receipt as clean.

Whitespace/staging checks:

```bash
git diff --check
git diff --stat
git diff --cached --check
git diff --cached --stat
```

## Three-Input Frozen MLP Q Sweep

Help command, no private model required:

```bash
.venv/bin/python -m rfic_transformer_inverse_design.synthesis.q_sweep_cli --help
```

Proxy diagnostic, `VERIFIED RECEIPT` for the release smoke target. The private model directory must contain summary/weights matching `real10k_model_contract.json`:

```bash
rfic-transformer-q-sweep \
  --model-dir /private/model \
  --out-dir /new/no_clobber/run \
  --design-id demo \
  --lp-nh 1.15 \
  --ls-nh 1.40 \
  --k-abs 0.76
```

Expected outputs under the new run directory:

- `proxy_candidates.csv`
- `proxy_candidates.json`
- `physical_backend_request.json`
- `selection.json`
- `run_manifest.json`
- `selected_geometry.json`
- a clearly labeled non-GDS preview PNG

Physical selection, `REVERIFY`. The current public release did not run the private backend:

```bash
rfic-transformer-q-sweep \
  --model-dir /private/model \
  --out-dir /new/no_clobber/physical_run \
  --design-id demo \
  --lp-nh 1.15 \
  --ls-nh 1.40 \
  --k-abs 0.76 \
  --mode physical \
  --physical-backend-command "bash /private/run_q_sweep_gds_emx_backend.sh"
```

The backend must return all eleven GDS/S4P candidates with `FRESH_REAL_EMX` and exact geometry hashes. No successful current command path for that private adapter is committed; keep this status `REVERIFY` until a new receipt exists.

## Web Application

Proxy web application, code and UI smoke `VERIFIED RECEIPT`; current private model path still required:

```bash
rfic-transformer-q-sweep-gui \
  --model-dir /private/model \
  --output-root /new/no_clobber/gui_runs \
  --mode proxy \
  --host 127.0.0.1 \
  --port 8765 \
  --open-browser
```

Physical web application, `REVERIFY`:

```bash
rfic-transformer-q-sweep-gui \
  --model-dir /private/model \
  --output-root /new/no_clobber/gui_physical_runs \
  --mode physical \
  --physical-backend-command "bash /private/run_q_sweep_gds_emx_backend.sh" \
  --host 127.0.0.1 \
  --port 8765 \
  --open-browser
```

The GDS download link is valid only for a successful physical job. Proxy jobs intentionally have no GDS download.

## General Layout And Simulation CLI

Show CLI contract, `VERIFIED RECEIPT` through package tests/entry-point definition:

```bash
rfic-transformer-inverse-design --help
```

Create-only layout command, `REVERIFY` for the current machine because it requires an audited config/process environment:

```bash
rfic-transformer-inverse-design create-only \
  --config /audited/config.yaml \
  --out-dir /new/no_clobber/create_only_run
```

General outputs are defined by the evaluator/exporter and can include a GDS, layout manifest, geometry/process evidence, and previews. Verify the exact top cell, layers, datatypes, ports, and hashes before use.

EM evaluation command, `REVERIFY`:

```bash
rfic-transformer-inverse-design eval \
  --config /audited/config.yaml \
  --out-dir /new/no_clobber/emx_eval_run
```

Do not run with template placeholders or an unaudited port/process config.

## Dataset Generation

General sample dataset command, `REVERIFY` for current EMX environment:

```bash
rfic-transformer-inverse-design sample-dataset \
  --config /audited/config.yaml \
  --out-dir /new/no_clobber/dataset_run \
  --count 100 \
  --batch-size 10 \
  --sampler lhs \
  --seed 20260824
```

Create-only sampling can be used for layout inspection but is not EM-labelled training data:

```bash
rfic-transformer-inverse-design sample-dataset \
  --config /audited/config.yaml \
  --out-dir /new/no_clobber/layout_only_dataset \
  --count 10 \
  --batch-size 10 \
  --sampler lhs \
  --seed 20260824 \
  --create-only
```

MARS four-port dataset wrapper, `HISTORICALLY VERIFIED` but current license/config/process state is `REVERIFY`:

```bash
CONFIG=/private/audited_mars_s4p.yaml \
OUT_DIR=/new/no_clobber/mars_s4p_run \
bash scripts/run_mars56_grounded_s4p_dataset.sh
```

Use the current contract: four RF ports, `.s4p`, 5-60 GHz, 0.5 GHz, 111 points, grounded shield mode. Do not substitute an old S8P template.

## Training-Table Construction

Code-backed command, `HISTORICALLY VERIFIED`; current exact use must pass explicit frozen columns:

```bash
.venv/bin/python scripts/build_physical_feature_inverse_training_table.py \
  /path/to/completed_dataset \
  --out-dir /new/no_clobber/inverse_table \
  --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
  --geometry-columns geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um \
  --check-touchstone-exists
```

Expected outputs:

- `physical_feature_inverse_training_table.csv`
- `physical_feature_inverse_training_manifest.json`
- `physical_feature_inverse_training_report.md`

Do not rely on the generic `k_center` default for the current frozen contract.

## Model Training And Evaluation

Exact real10k frozen-model training invocation, `HISTORICALLY VERIFIED` with return code 0 in the private `runner_command.json` whose model summary SHA is `90a81532...a3fa`. Paths are sanitized, but options match the recorded command:

```bash
.venv/bin/python scripts/train_physical_feature_tandem_inverse.py \
  --training-csv /path/to/physical_feature_inverse_training_table.csv \
  --out-dir /new/no_clobber/model_run \
  --input-columns input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center \
  --split-reference-columns input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center \
  --geometry-columns geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um \
  --min-training-rows 10000 \
  --validation-fraction 0.15 \
  --test-fraction 0.1 \
  --split-mode physical_cell_grouped \
  --physical-cell-bins 4 \
  --physical-cell-lower 0.5,0.5,5.0,0.0 \
  --physical-cell-upper 3.0,3.0,25.0,0.8 \
  --seed 20260711 \
  --split-seed 20260711 \
  --forward-depth 3 \
  --forward-width 256 \
  --inverse-depth 3 \
  --inverse-width 256 \
  --batch-size 1024 \
  --forward-max-optimizer-updates 1200 \
  --inverse-max-optimizer-updates 1200 \
  --validation-every-optimizer-updates 20 \
  --learning-rate 0.001 \
  --weight-decay 1e-6 \
  --response-loss-family mse \
  --response-loss-scaling declared_range \
  --response-weight-schedule warmup_ramp_adaptive_ema \
  --response-schedule-domain optimizer_update \
  --geometry-anchor-weight 0.01 \
  --local-refinement-steps 0 \
  --evaluation-mode validation_only \
  --max-prediction-rows 10000 \
  --stage-checkpoint-mode resume_exact
```

That historical command sealed the test set and produced a validation-only model; it is not a final physical-accuracy claim. It is also **not** sufficient for a controlled 10k/20k experiment because normalization arrays, geometry envelopes, exact nested split identities, forward weights, and the common evaluation frame must be frozen across arms. Status for the planned comparison: `UNVERIFIED`.

Fixed-target proxy evaluator, current dirty worktree only, `VERIFIED CURRENT` via focused tests but not committed:

```bash
.venv/bin/python scripts/evaluate_real10k_mlp_fixed10k.py --help
```

Do not call its output fresh EMX.

## GDS Inspection

Binary identity and type checks, generally verified shell tools:

```bash
test -s /path/to/layout.gds
shasum -a 256 /path/to/layout.gds
file /path/to/layout.gds
```

Structured GDS inspection, `REVERIFY` for each artifact:

```bash
.venv/bin/python -c 'import gdstk,sys; lib=gdstk.read_gds(sys.argv[1]); print([c.name for c in lib.cells])' /path/to/layout.gds
```

Then compare the top cell, layers/datatypes, ports, and manifest against the audited contract. File existence and a PNG preview are insufficient.

## EMX And Touchstone Checks

The exact raw EMX invocation is site/config dependent and intentionally not committed with private PDK paths. Current status: `REVERIFY`.

The public flow expects the wrapper/evaluator to produce `.s4p`; check identity and header before feature extraction:

```bash
test -s /path/to/result.s4p
shasum -a 256 /path/to/result.s4p
head -n 20 /path/to/result.s4p
```

The current port/frequency contract is in `docs/MARS56_GROUNDED_S4P_PORT_CONTRACT_20260702_CN.md`. Any command producing `.s8p` is a different historical contract.

## MARS And CHTC

MARS SSH connectivity was read-only checked on 2026-08-24, but no current exact-Q EMX process/backend run was proven. Before work:

```bash
ssh <approved-mars-alias> 'hostname; date; uptime; pgrep -afu "$USER" emx'
```

Status: `REVERIFY`. Never place passwords, Duo codes, tokens, private hosts, or license paths in this repository.

No current, audited CHTC submit command or live-job receipt was found in the public repository. CHTC submission status: `UNKNOWN`. Do not invent or reuse a historical submit file without a new audit.

## Output Locations

- Public tracked contracts/receipts: `docs/research/`.
- Public research snapshot: `research_snapshot/20260823/`.
- Local runtime default for general CLI: `tmp/rfic_transformer_inverse_design/runs/<command>/` when no `--out-dir` is supplied.
- Q-sweep: exactly the caller-supplied new `--out-dir`; physical deliverables under its `deliverables/` subdirectory.
- Private model, PDK, GDS, S4P, EMX logs, and training evidence: offline/private no-clobber stores, never the public Git repository.
