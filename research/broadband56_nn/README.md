# Broadband56 research models

An isolated research implementation of full four-port S-parameter prediction and
masked-spectrum inverse design. This directory does not launch or control data
generation, Cadence, Calibre, EMX, a production supervisor, or the GUI.

The initial research budget is **256 optimizer updates per forward model and 128
per inverse**, with effective batch 32 geometries, AdamW at `3e-4`, weight decay
`1e-4`, and gradient clipping at 1.0. These are short, recorded training budgets,
not evidence of convergence or physical qualification. Actual updates, early
stopping, deadlines, losses, and hardware belong in each private run receipt.

## Fixed comparison

| Package | Forward | Inverse |
|---|---|---|
| BB01 | F1: point-conditioned MLP | I1: masked-token MLP |
| BB02 | F2: geometry-conditioned FiLM residual model | I1 |
| BB03 | F3: branch/trunk frequency-basis model | I1 |
| BB04 | Exact same selected F2 checkpoint as BB02 | I2: 1D residual CNN |
| BB05 | Exact same selected F2 checkpoint as BB02 | I3: CNN + four Transformer layers |
| BB06 | Exact same selected F2 checkpoint as BB02 | I4: CNN + six larger Transformer layers |

The common evaluator `FREF` is a separately trained F1 with seed 29; it is never
used for inverse-gradient updates. The package training seed is 17. Shared data,
split, target generation, loss, and update budgets do not make the models equal
in parameter count or compute cost. No model is a predetermined winner.

R0 remains the separate historical 15 GHz NumPy model. Its validation-export
replay checks runtime agreement, not broadband performance or original trainer
byte identity. No old checkpoint is renamed into a BB package.

## Data and scientific boundaries

- Prepare only an explicitly pinned, formally accepted immutable snapshot.
  Never follow a growing campaign CSV. The actual geometry dimension, order,
  bounds, and units come from the frozen source contract.
- Each geometry supplies all 56 frequencies, 5–60 GHz in 1 GHz steps. Preserve
  all 32 row-major real/imaginary S channels; do not assume reciprocal reduction.
- Split by canonical geometry identity using frozen 60/20/20 hash thresholds.
  Finite-sample counts need not be exactly those percentages. Existing IDs keep
  their groups as data grows; normalization is fitted only on training rows.
- Keep valid S labels even where lumped parameters are invalid. The main
  PHYSICAL targets use the stored strict-lumped mask; the broader descriptor
  mask is retained separately. Invalid raw physical values are not clipped into
  credible labels. Mask before arithmetic, not after multiplying by NaN.
- SPECTRUM and PHYSICAL use the same specification interface and masking rules.
  PHYSICAL never receives an unrequested reference S spectrum. Clear hidden
  values before MLP, convolution, or attention, and reject empty requests.
- Freeze forward parameters while preserving gradients through predicted
  geometry. Physical supervision requires a snapshot/source-bound extractor
  parity receipt. Invalid predictions cannot erase requested constraints.
- Forward loss is shared-scale S MSE plus `0.1` adjacent-response **difference
  matching**, with physical auxiliary weight zero. Inverse loss is requested
  response loss plus `0.1` analytical feasibility; geometry-anchor weight zero.
- Analytical bounds and grid rounding are not a manufacturing certificate.
  Original-data holdout S parameters are not EMX validation of inverse-generated
  geometries. Without that separate physical chain, report
  `REAL_EMX_VALIDATION=NOT_RUN` and `PHYSICAL_WINNER=NOT_ESTABLISHED`.

## Install and prepare

Run from the repository root in an independent environment. Keep the environment
and outputs outside production and GUI directories. CPU and MPS are supported by
this local profile; visible hardware is not a promise of exclusive allocation.

```sh
python -m venv /path/to/private/research-env
/path/to/private/research-env/bin/python -m pip install -r research/broadband56_nn/requirements.txt
```

Examples below use `python` from that environment. Variables denote private
paths chosen by the operator; output directories must not already exist.

```sh
python -m research.broadband56_nn prepare-data \
  --source-manifest "$BB_SOURCE_MANIFEST" --out "$BB_DATA_ROOT"

python -m research.broadband56_nn.baseline \
  --run "$BB_HISTORICAL_R0_RUN" --out "$BB_R0_REPLAY_OUT"

python -m research.broadband56_nn.parity \
  --data "$BB_DATA_ROOT/dataset.npz" --contract "$BB_RUNTIME_CONTRACT" \
  --out "$BB_PHYSICAL_PARITY_RECEIPT"
```

`bb_source_manifest.v1` supplies `files` pins containing `path`, `sha256`, and
optional `size_bytes`: `accepted_geometries`, `long_features`,
`checkpoint_receipt`, `raw_products_receipt`, and `geometry_bounds`. Additional
file pins are checked too. It also declares the verified `port_contract`,
including port order and reference impedance; do not infer it from an unrelated
historical model. `prepare-data` writes `dataset.npz`, `normalizer.json`,
`splits.json`, provenance, a data manifest, and SHA-256 evidence.

## Train one bounded campaign or individual models

The campaign command executes one research training sequence and reuses one F2
for BB02/04/05/06. Do not launch duplicate individual runs alongside it.

```sh
python -m research.broadband56_nn.campaign \
  --data "$BB_DATA_ROOT" --out "$BB_RUNS_ROOT" \
  --contract "$BB_RUNTIME_CONTRACT" \
  --physical-parity-receipt "$BB_PHYSICAL_PARITY_RECEIPT" \
  --forward-steps 256 --inverse-steps 128 --device mps \
  --deadline-utc "$BB_TRAINING_DEADLINE_UTC"
```

Equivalent individual entry points, for a separately planned run:

```sh
python -m research.broadband56_nn train-forward \
  --data "$BB_DATA_ROOT" --contract "$BB_RUNTIME_CONTRACT" \
  --kind F2 --steps 256 --out "$BB_FORWARD_RUN" --device cpu

python -m research.broadband56_nn train-inverse \
  --data "$BB_DATA_ROOT" --contract "$BB_RUNTIME_CONTRACT" \
  --kind I3 --package-id BB05 --steps 128 --out "$BB_INVERSE_RUN" \
  --forward-checkpoint "$BB_F2_BEST_CHECKPOINT" \
  --physical-parity-receipt "$BB_PHYSICAL_PARITY_RECEIPT" --device cpu
```

Use the checkpoint explicitly selected by its training receipt, not a guessed
filename. Without a physical-parity receipt, an individual inverse run is
SPECTRUM-only and must be labeled accordingly.

## Resume versus new-data finetune

`resume` requires the same snapshot, normalizer, and geometry/port contract. It
restores model, optimizer, scheduler, and random/sampler states. `--steps` is the
number of additional updates, subject to the recorded epoch/deadline bounds.

```sh
python -m research.broadband56_nn resume \
  --data "$BB_DATA_ROOT" --contract "$BB_RUNTIME_CONTRACT" \
  --checkpoint "$BB_LAST_CHECKPOINT" --steps 32 \
  --out "$BB_NEW_RESUME_RUN" --device cpu
```

For new data, prepare a new snapshot with `--previous-splits` pointing to the old
`splits.json`. First finetune and validate the forward, then pair its selected
new checkpoint with inverse finetuning. Normalizer preservation is explicit;
the implementation loads previous weights but starts a **fresh optimizer**.
An implicit normalizer migration is not supported.

```sh
python -m research.broadband56_nn prepare-data \
  --source-manifest "$BB_NEW_SOURCE_MANIFEST" --out "$BB_NEW_DATA_ROOT" \
  --previous-splits "$BB_DATA_ROOT/splits.json"

python -m research.broadband56_nn finetune \
  --data "$BB_NEW_DATA_ROOT" --contract "$BB_RUNTIME_CONTRACT" \
  --checkpoint "$BB_OLD_FORWARD_CHECKPOINT" --preserve-normalizer \
  --steps 256 --out "$BB_NEW_FORWARD_RUN" --device cpu

python -m research.broadband56_nn finetune \
  --data "$BB_NEW_DATA_ROOT" --contract "$BB_RUNTIME_CONTRACT" \
  --checkpoint "$BB_OLD_INVERSE_CHECKPOINT" --preserve-normalizer \
  --forward-checkpoint "$BB_NEW_FORWARD_BEST_CHECKPOINT" \
  --physical-parity-receipt "$BB_NEW_PHYSICAL_PARITY_RECEIPT" \
  --steps 128 --out "$BB_NEW_INVERSE_RUN" --device cpu
```

Regenerate the extractor parity receipt for the new snapshot before PHYSICAL
finetuning. The old forward is rejected for the new inverse snapshot. Existing
train/validation/test assignments and scientific fingerprints cannot change.
Apply this sequence to each required forward and inverse; update the independent
FREF on the new training snapshot too. BB02/04/05/06 must still reference one
identical newly selected F2 checkpoint, not four separately finetuned copies.

## Evaluate and package

The evaluator compares the full common holdout, frozen specification panels,
own-forward versus FREF results, and continuous versus grid-rounded geometry.
It also exports candidate CSV files for later physical validation; that export
does not dispatch a simulator.

```sh
python -m research.broadband56_nn.evaluation \
  --data "$BB_DATA_ROOT" --runs-root "$BB_RUNS_ROOT" \
  --forward-reference "$BB_FREF_BEST_CHECKPOINT" \
  --split validation --out "$BB_EVALUATION_OUT" --device cpu

python -m research.broadband56_nn.delivery load-resume \
  --data "$BB_DATA_ROOT" --runs-root "$BB_RUNS_ROOT" \
  --out "$BB_LOAD_RESUME_OUT" --device cpu

python -m research.broadband56_nn.delivery package \
  --data "$BB_DATA_ROOT" --runs-root "$BB_RUNS_ROOT" \
  --resume-receipt "$BB_LOAD_RESUME_OUT/LOAD_RESUME_RECEIPT.json" \
  --out "$BB_PACKAGES_OUT"
```

Test scoring requires `--split test --configuration-freeze FILE`, with the exact
snapshot, normalizer, and ten selected checkpoint SHAs frozen before scoring.
Never use test scores to choose seeds, architectures, or hyperparameters.
Load/resume checks perform an actual additional update in a new process; they
do not overwrite the research runs. Package status must distinguish implemented,
smoke-trained, partially pretrained, and budget-complete/reload-verified results.
`PRETRAINED` does not mean converged, manufacturable, or physically qualified.

Public GitHub contains only compliant code, synthetic tests, and portable
documentation. Keep actual geometry/S data, model weights, receipts containing
private paths, PDK assets, and nonpublic manuscripts in private storage. Do not
publish a private package or bypass an existing publication hold.

```sh
python -m pytest -q tests/test_bb_data.py tests/test_bb_baseline.py \
  tests/test_bb_training.py tests/test_bb_finetune.py
```

These tests exercise synthetic software behavior, not scientific accuracy.
