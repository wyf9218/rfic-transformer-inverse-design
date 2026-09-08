# Frequency-indexed large-scale evaluation: static chart contract

This contract precedes renderer implementation. Its source is the user's large-scale
evaluation supplement, not an anticipated numerical result. Every chart reads saved
CSV/JSON only; it never loads a model, selects a checkpoint, generates a target,
or launches a physical solve.

Scope: **FOUR_TARGET_ONE_SHOT**, with four prescribed targets including Q_scalar.
This is not the subsequent three-target Q-scan experiment. Its C=10,000 four-target
requests must never be renamed as Q-scan requests or merged into Q-scan statistics.

## Common rules

- Surface: standalone reproducible Matplotlib SVG, PDF and 300-dpi PNG, with chart-ready
  CSV/JSON, captions, exact source SHA-256 pins and a renderer identity.
- Grain: panel × frequency × label policy × model × evaluator × geometry stage.
  A/B/C/D and raw/grid remain separate. A uses EXISTING_EMX_FORWARD; B/C use
  SELF_PROXY unless separately identified. Pending D is not an EMX result.
- A prediction's physical-valid flag is only finite/sign sanity under the frozen
  numerical policy. It does not establish strict labels for a generated candidate.
- Blue and gold roots plus neutrals; markers, line style, hatching, labels and facets
  supply non-color distinctions. No branding for third-party academic research.
- Missing or untrained frequency cells stay grey/blank, not zero. A lone evaluated
  frequency uses a marker, not an interpolated curve. Strict/descriptor are separated.
- Physical MAE/P50/P90/P95 use separate feature axes. Normalized errors are errors
  divided by frozen training scales, not percentage errors relative to each target.
- Fixed requested denominators are visible. A fixed-denominator empirical attainment
  curve may end below one; failures are not imputed as large errors. Finite sample
  counts accompany available-case errors. Single LHS designs receive no binomial CI.
- Captions bind model/data/target identities, label policy, panel, evaluator, frequency,
  sample count, seed, normalization, tolerance and non-completion states.
- The renderer reports EXPORTED_PENDING_VISUAL_QA. Actual final PNG/PDF inspection is
  required before asserting visual acceptance. No claim of physical superiority.

## Chart map

| Chart | Question and defensible takeaway | Family / sufficient data / fallback |
|---|---|---|
| Full 56-point counts and separate model states | Where do labels, eligible holdout observations and evaluated requests exist? | Full-axis line for 56 real label counts; evaluated N as markers; separate categorical state strip. Missing labels/models are explicit. |
| Four-feature × 56-point normalized MAE | Which actually evaluated cells have what relative-to-training-scale error? | Panel/source/stage-separated heatmap. Missing grey; no interpolation or fake zero. |
| Physical-unit errors by frequency | What are MAE, P50/P90/P95 absolute errors in each unit? | Four faceted mark/line axes; connect only adjacent actually measured frequencies. One frequency is a marker. |
| Joint response hits, geometry and physical audit | How many original requested targets passed each distinct criterion? | Separate axes for ALL_FOUR_HIT, analytic geometry and genuine fresh-EMX progress. Pending coverage shown, never called failure or zero accuracy. |
| 15-GHz fixed-denominator distributions | What fraction of all requests reaches each observed error threshold? | r_max step attainment with r_max=1; four per-feature absolute-error attainment curves. Each curve divides by N_requested and annotates finite N. |
| Truth/target versus prediction | What is the full individual relationship? | Four per-feature hexbins when N≥50, scatter below 50, no density below eight. Identity reference only; inverse titles explicitly SELF_PROXY or GENERATED_FRESH_EMX. |
| Target-space coverage and reliability | Where were requests made, and where did this method miss? | Lp–Ls and Q–|k| 2D cells with count, mean r_max and hit fraction. Fixed frozen bin edges. Empty blank; cells with N<10 cross-hatched; no smoothing/support-domain claim. |

Target-space bins must be fixed before final scoring, using the declared train-only
window (out-of-window observations are separately counted). No error-based trimming,
adaptive bins chosen for flattering results, or omission of unsuccessful requests.

If genuine D predictions become available, source-specific prediction plots and
proxy-versus-fresh differences must use exact candidate/target joins. Until then a
pending audit panel and preselected counts are the only D figure content.
