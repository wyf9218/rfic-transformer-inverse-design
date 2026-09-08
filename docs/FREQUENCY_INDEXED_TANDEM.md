# Frequency-indexed Tandem MLP

Machine-readable delivery status: [FREQUENCY_INDEXED_STATUS_20260908.json](research/FREQUENCY_INDEXED_STATUS_20260908.json).

This is the current research priority. The historical BB00–BB06 sources and results remain separate; complex broadband comparisons are not a prerequisite.

## What the model does

Choose an exact integer frequency and label mode. Supply `[Lp_nH, Ls_nH, Q_scalar=min(Qp,Qs), K_abs]`. Load that frequency's inverse model, generate one geometry, and diagnose it with the same frozen forward model. Frequency is **not** an MLP input. A single geometry is not required to satisfy all 56 frequencies.

- Forward: `d_g → 256 → 256 → 256 → 4`.
- Inverse: `4 → 256 → 256 → 256 → d_g`.
- Hidden activation: tanh-approximate GELU. Geometry mapping: independent sigmoid to the valid training geometry envelope.
- `d_g` and field order come from the verified data contract. The current dataset has 10 geometry fields.
- BB00's response-led recipe is reused: four symmetric targets, an explicitly recorded 0.01 geometry anchor, AdamW and adaptive response weighting. This is not a byte-exact reproduction of the historical optimizer.

## Real milestone: 5K development, not formal 10K

The first real new-data run uses an existing verified 5,000-geometry snapshot (280,000 frequency rows). At 15GHz the strict-valid finite subset is 2,617 geometries: 1,575 train, 533 validation, 509 test. Both networks were initialized from scratch and each performed 9,844 optimizer updates, reaching the declared 200-equivalent-epoch budget. Validation selected forward update 9,100 and inverse update 8,800.

Best/last saving, separate-process loading, and one diagnostic continuation update per network passed. Optimizer, scheduler, sampler and RNG state continued; the inverse changed while its frozen forward remained unchanged. Diagnostic descendants are not evaluation models. The training status remains `PARTIAL` / convergence not established, not “all frequencies trained.”

Held-out forward errors against the original EM labels (509 geometries):

| Target | MAE | RMSE | R² |
| --- | ---: | ---: | ---: |
| Lp (nH) | 0.010305 | 0.013978 | 0.998449 |
| Ls (nH) | 0.010040 | 0.013198 | 0.998114 |
| Q_scalar | 0.187211 | 0.255598 | 0.974528 |
| \|k\| | 0.010652 | 0.014813 | 0.970739 |

Inverse SELF_PROXY joint hits: 507/509 (99.607%). A hit requires analytical geometry feasibility and all four absolute residuals within `[0.125 nH, 0.125 nH, 1.0, 0.04]`, i.e. 5% of declared spans `[2.5 nH, 2.5 nH, 20, 0.8]`. This is **not per-target relative 5% error or physical design accuracy**. Both failures remain in the denominator. Analytical feasibility passed for 509/509, but no manufacturing/DRC acceptance is implied. The 0.005µm export grid did not change the joint-hit count.

`REAL_EMX_VALIDATION=NOT_RUN`: the reference geometry's EM labels are valid forward-test truth, but are not EM truth for the newly generated inverse geometry. The target frame is the empirical held-out manifold, not arbitrary independent combinations of the four targets. One seed and a hash split do not establish OOD performance, convergence, or a causal architecture comparison.

## Labels and support

All frequencies retain the same 60/20/20 geometry-hash assignment; each frequency fits its normalizer only on its valid train subset. Raw labels and masks are not clipped, imputed or changed. `STRICT_LUMPED` and `POINTWISE_DESCRIPTOR_EXPERIMENTAL` are separate experiments.

The verified extractor uses `2*f < observed SRF`, or `2*f <= censored lower bound`. A scan censored above60GHz cannot establish strict validity at31–60GHz. This does not invalidate high-frequency S-parameters. In this 5K snapshot, strict counts at5/10/15/20/25/30GHz are5000/4931/2617/244/8/0. Only two train geometries remain at25GHz: this is not adequate evidence for a reliable supported model. Configured frequency slots are not trained support.

## Commands

Run in the independent research environment. Private data and weights are intentionally absent from GitHub; supply verified local paths in the private configuration.

```bash
python -m research.broadband56_nn.frequency_tandem inspect-data --config CONFIG.json
python -m research.broadband56_nn.frequency_tandem train --config CONFIG.json --out NEW_RUN
python -m research.broadband56_nn.frequency_tandem train --config CONFIG.json --out NEW_RESUME_RUN --resume LAST.pt
python -m research.broadband56_nn.frequency_study run --request STUDY.json
python -m research.broadband56_nn.frequency_study resume --request STUDY.json
python -m research.broadband56_nn.frequency_profile --data VERIFIED_DATA --out NEW_PROFILE
python -m research.broadband56_nn.frequency_tandem infer --forward FORWARD.pt --inverse INVERSE.pt --frequency-ghz 15 --label-mode STRICT_LUMPED --targets '[1.2,1.2,14,0.3]'
```

Ordinary continuation requires remaining frozen update/epoch budget and matching state. An expired or exhausted budget is not silently reset. `--resume-probe` with a one-step diagnostic configuration is acceptance evidence, not a way to extend ranked training. Unsupported routes and out-of-training-envelope targets are rejected unless research extrapolation is explicitly requested. No nearest-frequency fallback, target clipping, geometry interpolation or implicit physical-validity claim is allowed.

`frequency_study` reuses the existing research device lock and read-only committed-receipt transport. A formal10K request freezes exactly accepted_sequence1..10000, preserves previous geometry splits and all56 points. Completed training is a no-op on repeat invocation; interrupted work requires the resume entry. Scheduler installation status is recorded in the actual private run state; source code existence alone does not prove installation or automatic training.
