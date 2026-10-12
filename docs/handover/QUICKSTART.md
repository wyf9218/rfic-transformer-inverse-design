# Self-service reproduction: start here

This guide is for Professor Liu and a successor using their own accounts. No meeting or access to the original chat is required for the local steps below. Native physical simulation is a separate institutional-access task. Do not run archived production launchers just to inspect the results.

## 1. Obtain both repositories

The owner must invite your GitHub account to the private repository. A 404 for the private link usually means you are signed out or not invited; it does not mean the data were deleted. Never request or send another person's password, authentication cookie or SSH private key.

```bash
git clone https://github.com/wyf9218/rfic-transformer-inverse-design.git
git clone https://github.com/wyf9218/rfic-transformer-inverse-design-data.git
cd rfic-transformer-inverse-design
git rev-parse HEAD
git -C ../rfic-transformer-inverse-design-data rev-parse HEAD
```

Record both commit IDs with any reproduced output. See [verification](VERIFICATION.md) for the tested release and scope. Do not upload the private repository or its outputs to a public issue.

## 2. Install the public package

Use Python 3.12 on macOS or Linux for the tested path. Python ≥3.10 is declared by the package. A discrete GPU is not needed for loading the supplied NumPy models. The browser GUI does not require the optional Qt desktop GUI dependencies.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
export RFIC_PRIVATE_REPO="$(cd ../rfic-transformer-inverse-design-data && pwd)"
export RFIC_HANDOVER_WORK="$(mktemp -d "${TMPDIR:-/tmp}/rfic-handover.XXXXXX")"
```

Keep this shell open so the two variables remain available. Choose permanent private storage instead of a temporary directory for new research results. Do not reuse a completed run's output directory.

## 3. Verify and access the real dataset

```bash
python tools/handover/assets.py verify --assets "$RFIC_PRIVATE_REPO"
python tools/handover/assets.py extract --assets "$RFIC_PRIVATE_REPO" \
  --out "$RFIC_HANDOVER_WORK/original100k.csv"
python "$RFIC_PRIVATE_REPO/tools/inspect_dataset_readonly.py" \
  --dataset "$RFIC_HANDOVER_WORK/original100k.csv" --rows 1
```

Expected: hash verification PASS, exact original CSV SHA `fe94d15b0d083330fe7a64d3e059d083a5aa32d73394c85fc4b881e9904e68a2`, four 15 GHz labels, ten geometry fields, and an original Touchstone reference. The bounded inspector reads one row, not a new census; the frozen source contains 100,000 rows. `verify --all` checks the full delivered archive when a full transfer check is needed. It is not necessary before every inference.

Labels are `[Lp_nH, Ls_nH, Q, K_abs]`; Q = min(Qp, Qs). Geometry is in µm. Raw S4P files are not embedded in the CSV or bundled here. Dataset access and raw-simulation access are different permissions.

## 4. Load a model and make one local inference

```bash
python tools/handover/model_smoke.py --assets "$RFIC_PRIVATE_REPO" \
  --model original100k --out "$RFIC_HANDOVER_WORK/original100k_smoke.json"
python tools/handover/model_smoke.py --assets "$RFIC_PRIVATE_REPO" \
  --model symmetric100k --out "$RFIC_HANDOVER_WORK/symmetric100k_smoke.json"
```

Expected: `PASS_MODEL_LOAD_AND_ONE_INFERENCE`; identities `current_foundry_qmin_response_only_seed20260713` and `legacyrecipe_D100k_seed20260711` respectively. Output includes ten geometry values, four forward-proxy values, bound model hashes and `fresh_EMX=NOT_RUN`. `--model real10k` loads the separate historical reference. This single target is an installation exercise, not a new accuracy experiment.

Never move weights alone into another model folder or swap normalizers. The original100K and symmetric100K have different architecture/decoder/loss histories. Read the [handover](HANDOVER_EN.md) before comparing them.

## 5. Open the packaged browser GUI

```bash
export RFIC_Q_SWEEP_MODEL_DIR="$RFIC_PRIVATE_REPO/models/original100k"
export RFIC_Q_SWEEP_OUTPUT_ROOT="$RFIC_HANDOVER_WORK/gui_jobs"
export RFIC_Q_SWEEP_MODEL_PROFILE=deployed100k
bash apps/mlp_q_sweep_gui/START_LOCAL.sh
```

Open `http://127.0.0.1:8765/`. The existing interface includes Chinese labels; the handover and commands are English. Supply Lp, Ls and |k|; proxy mode scans integer Q=10…20 and ranks symmetric four-feature error. This GUI ranking does **not** change the original model's historical minimum-Q training objective. Stop this local GUI with Ctrl-C when finished; this does not contact a MARS controller.

To inspect the symmetric model in a separate session, use its complete bundle:

```bash
export RFIC_Q_SWEEP_MODEL_DIR="$RFIC_PRIVATE_REPO/models/symmetric100k"
export RFIC_Q_SWEEP_MODEL_PROFILE=custom
bash apps/mlp_q_sweep_gui/START_LOCAL.sh
```

Use a new design/job ID and retain failures. Local proxy operation does not generate GDS or validate physical accuracy. The shipped physical adapter lacks the new required QA/DRC evidence producer and is **NOT_NATIVE_ACCEPTED**; it now rejects that request before native dispatch. The synthetic process deck in the wheel is not a foundry PDK. Do not connect a native backend until its exact geometry, ports, process, DRC, EMX and result-binding contract is validated in the licensed environment.

## 6. Replot an existing three-seed comparison without a model

```bash
python "$RFIC_PRIVATE_REPO/replot/replot_repeat_csv.py" \
  --cut "$RFIC_PRIVATE_REPO/research/repeats_20260921_v1/received/aggregate_v1/dataset/d14358c5cefdd731" \
  --complete-sha b928d11eb18b0aa890d0caf460e91a14f19c535daf9e02bc39ce0c3ea7654493 \
  --aggregator "$RFIC_PRIVATE_REPO/replot/aggregate_repeats.py" \
  --out "$RFIC_HANDOVER_WORK/dataset_replot"
```

Expected: two SVGs and two PNGs, `SOURCE.json` and `RENDER_COMPLETE.json`. It consumes saved means/sample SDs and performs no inference, training or simulation. Inspect the previews yourself. The uncertainty is sample SD over three seeds, **not** a confidence interval or 30,000 independent targets. The historical inverse score in this comparison is minimum-Q and COMMON_PROXY, not fresh EMX or the later symmetric scan.

For structure-width plots, change the cut to `architecture/b0e1ba535efa6985`, use completion SHA `2aaff00091c50b35d4c86bf5c280e494ae5867a93d96b18a8749fb326ab262f8`, and a new output directory.

## 7. Prepare a future training run; do not retrain to read existing results

First choose a scientific reason for a new experiment. The following two commands only relocate exact frozen inputs and parse/check the recipe. They neither train nor evaluate.

```bash
python tools/handover/prepare_training.py prepare \
  --assets "$RFIC_PRIVATE_REPO" --dataset "$RFIC_HANDOVER_WORK/original100k.csv" \
  --out "$RFIC_HANDOVER_WORK/new_training" --threads 2
python tools/handover/prepare_training.py check --out "$RFIC_HANDOVER_WORK/new_training"
```

Expected: `PREPARED_NOT_TRAINED`, then `PASS_OFFLINE_PREPARATION`; D10k 8000/1000/1000, D50k 40000/5000/5000, D100k 78891/9740/11369; seed20260713; common test11369. All partitions remain unchanged and each arm's normalizer is fitted only on its train rows.

**The following command really trains. It was not run during publication.** Provision at least the historical conservative envelope of 16 GiB RAM and 5 GiB free output space, check site-specific quotas, and select one arm explicitly. The portable entry is foreground CPU training, not a scheduler; default BLAS cap is two threads. It uses the historical minimum-Q/hard-feasible-decoder recipe, not the symmetric reconstruction.

```bash
python tools/handover/prepare_training.py run \
  --out "$RFIC_HANDOVER_WORK/new_training" --arm D10k
# Only after an interrupted run, with the SAME prepared identity:
python tools/handover/prepare_training.py resume \
  --out "$RFIC_HANDOVER_WORK/new_training" --arm D10k
```

Outputs are under `new_training/frozen/research/dataset_size_811_20260921_v1/runs/D10k/`: console log, entry receipts, history, selected weights and stage checkpoints. D50k/D100k use the same explicit entry. Resume reuses completed stages; it is **not** arbitrary-update Adam/RNG continuation. The run reports validation-only results; test evaluation remains a separate, frozen-choice step. Numerical identity across NumPy versions is not promised. Symmetric/width/repeat recipes are preserved in the private research archive but do not yet have this new portable launch wrapper; adapt their recorded location/budget fields explicitly before any future run.

## 8. Written acceptance and next work

Return only: both commit IDs, asset-verification result, successful model ID, GUI/CSV-replot outcome, and any missing file or permission. Use a private channel for data/logs. No live meeting is required by this guide.

The next researcher should select the paper's primary protocol, inspect existing data-support gaps, and decide whether a controlled experiment or new paired physical validation is needed. There is no completed 10,000-candidate fresh-EMX result to cite. Existing server tasks were neither inspected for liveness nor changed as part of this release; do not infer their current state from archived logs.
