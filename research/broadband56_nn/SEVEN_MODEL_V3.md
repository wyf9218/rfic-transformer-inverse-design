# BB00–BB06: one new 10K study

This is an incremental extension of the existing six broadband systems. It does
not replace their architecture, old runs, production controller, or GUI.
Private run receipts, not this source guide, establish actual training status.

## Two BB00 branches, one group

`baseline_package build` preserves the original historical NumPy weights,
normalizer, configuration evidence, implementation, and existing replay receipt.
`baseline_package inspect` loads that package without repeating the historical
validation evaluation. A missing historical optimizer state or mismatched
historical trainer SHA remains unproven; loading weights is not exact optimizer
continuation.

`bb00.py` implements the verified 256 × 3 tanh-approximation GELU tandem MLP:
geometry → four physical values and four physical values → geometry. Its inverse
uses the historical independent sigmoid training-envelope mapping, not the
broadband systems' decoder. Normalization for new training uses only new-train
geometries with valid 15 GHz labels. The old normalizer does not filter new data.
An incompatible geometry interface requires explicit `IO_ADAPTED` opt-in.

The new baseline initializes both roles randomly. The verified historical
response/geometry-anchor recipe is retained, while AdamW, learning rate, batch,
and short update budgets are explicitly a **new-data baseline configuration**,
not proof of exact historical optimization. Both best-validation and last states
include optimizer, scheduler, sampler/RNG, response schedule, and source bindings.
Diagnostic one-update resume branches cannot enter the primary comparison.

## One frozen dataset, separate panels

- Admission requires at least 10,000 formally committed unique geometries.
  Use authoritative accepted sequence 1 through 10,000, even after an overshoot.
  A status count alone never authorizes training. A full checkpoint or a pinned
  checkpoint plus validated contiguous committed increments is required.
- Preserve all 56 records, 32 real S channels, Z values and original validity
  flags. Accepted S data does not imply 56 valid lumped-parameter labels.
  Do not read partial CSVs, modify production, or create seven data copies.
- Geometry grouping uses the existing deterministic seed-17 hash split.
  Report actual train/validation/test counts, eligible 15 GHz counts and actual
  training exposure; 10K is the total snapshot, not the gradient-training count.
- Every ranked forward and inverse is trained from scratch on the new train
  subset. The historical replay and earlier 5K pretrained packages are separate
  references, never warm starts for the primary ranking.
- The seven-group common request is only `[Lp, Ls, Qmin, |K|]` at 15 GHz.
  BB01–BB06 receive no hidden reference spectrum, geometry, other frequencies,
  or reference-only validity pattern. All use one-shot inference.
- A separate train-only FREF does not participate in inverse gradients. Report
  own-forward diagnostics separately from shared-FREF residuals. Keep forward
  and inverse tables separate, and never rank unlike raw training losses.
- Broadband panels include BB01–BB06 only. BB00 is `NOT_SUPPORTED`, not zero.
  The preserved v2 evaluator did not record own-forward/grid predictions;
  corresponding export cells are `NOT_RECORDED`, not fabricated measurements.
- Freeze target identities before validation and checkpoint/configuration
  identities before sealed-test scoring. Missing/invalid outputs stay in the
  fixed requested denominator. Undefined full-panel RMSE remains undefined.
- Different supervision and compute make this a descriptive method/system
  comparison, not a pure architectural causal ablation. Without fresh physical
  validation of generated geometry: `REAL_EMX_VALIDATION=NOT_RUN` and no physical
  accuracy champion.

## Supported commands

Run in the independent research environment from this worktree. The variables
below denote explicitly configured private paths, not repository-bundled data.

```sh
python -m research.broadband56_nn.baseline_package build \
  --replay-receipt "$BB_LEGACY_REPLAY" --out "$BB_REFERENCE_PACKAGE" \
  --expected-replay-sha256 "$BB_LEGACY_REPLAY_SHA"
python -m research.broadband56_nn.baseline_package inspect \
  --package "$BB_REFERENCE_PACKAGE" --expected-manifest-sha256 "$BB_REFERENCE_MANIFEST_SHA"

python -m research.broadband56_nn.seven_suite create-request \
  --out "$BB_STUDY_REQUEST" --campaign-id "$BB_CAMPAIGN_ID" \
  --control-root "$BB_STUDY_CONTROL_ROOT" \
  --contract "$BB_RUNTIME_CONTRACT" --legacy-replay "$BB_LEGACY_REPLAY" \
  --spec "$BB_EXECUTION_SPEC" --device mps \
  --forward-steps 256 --inverse-steps 128 --wall-budget-seconds 1800

python -m research.broadband56_nn.seven_suite check-and-run-once \
  --request "$BB_STUDY_REQUEST" --root "$BB_STUDY_CONTROL_ROOT" \
  --access-config "$BB_READ_ONLY_ACCESS_CONFIG"
```

`--source-manifest` may replace `--access-config` when the exact required private
files already exist locally and pass their pins. The read-only transport uses an
existing authorized SSH session; it never stores credentials. Below threshold
it reads committed JSON receipts, not large production CSVs. At eligibility it
transfers immutable artifacts, verifies complete hashes and then freezes data.

The same `--request`, `--root`, and optional access arguments support:

| Command | Effect |
|---|---|
| `prepare-10k` | Freeze and materialize eligible data; no training |
| `train-seven` | Complete the twelve shared/BB00 training components serially |
| `resume-seven` | Continue a started study; never create a fresh trial |
| `evaluate-seven` | Evaluate completed stages only; no training launch |
| `package-seven` | Reuse/complete evaluations and private packaging of completed stages |
| `check-and-run-once` | Perform the one legal next step, or return waiting/already-running/complete |

Do not change the request, study root or software after freezing. Campaign + 10K
+ suite version is the stable identity; checkpoint growth does not create a new
study. Durable advisory locks protect the study and device, including training
and proof subprocesses. Completed stages are adopted only with exact terminal
receipts. Partial attempts and failure evidence are retained; no-checkpoint
interruptions require an explicit recovery decision, never silent retraining.

The local profile admits CPU/MPS only, two CPU threads, one training process,
micro-batch 8 and effective batch 32. It checks memory/disk/hardware before work
and each training stage. Resource shortage yields `WAITING_RESOURCE`; production
continues independently. A frozen absolute wall-budget deadline is not reset by
resuming; exhaustion remains `PARTIAL` and requires an explicit budget decision.

## Deployment and evidence

**Automatic trigger: `NOT_INSTALLED`.** This is a foreground one-shot program,
not a daemon and not a claim it survives terminal/app exit. No AI heartbeat is
installed or resumed. An operator can use the same verified entry from an
approved ordinary scheduler later without changing production.

Preparation tests use synthetic data and bounded real optimizer updates. They
prove software behavior, not scientific model accuracy or completed new 10K
training. Report separately: code ready; historical weights loaded/replayed;
synthetic smoke trained; formal new 10K training status for each group.

Actual completed studies contain a source/split manifest, twelve stage receipts,
logs and best/last checkpoints; common inverse and forward 15 GHz tables; a
separate broadband table; new-process load/resume proofs; private packages with
fixed evidence references; and manifests/SHA256SUMS. The original study remains
required for its resume command. Private data, weights, PDK assets and personal
paths are not public-release content. An existing publication hold still applies.
