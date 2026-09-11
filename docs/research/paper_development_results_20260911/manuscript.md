# Transformer Inverse Design at 15 GHz with Discrete Quality Factor Selection

## Abstract

We study 15 GHz transformer inverse design with discrete quality-factor selection. A tandem multilayer perceptron generates eleven candidates; its frozen forward model selects one before electromagnetic simulation. Five configurations with three seeds use a 6329-geometry development view partitioned into 3801 training, 1269 validation and 1259 test examples. The pre-frozen three-layer 256-unit seed-17 pair attains 1253/1259 held-out targets under SELF_PROXY evaluation, without new physical validation. In its separate terminal 128-request physical pilot, joint attainment is 40/128 (31.25%), or 40/48 (83.33%) conditional on strict validity. A separate controlled acquisition trial completes sixteen actual solver starts per arm on the frozen 3801-training-row baseline but adds no occupied response cells. Historical-reference and external-forward diagnostics remain separate. These development observations establish neither sampling superiority nor final-model physical accuracy.

## Index Terms

Integrated transformers, inverse design, neural networks, electromagnetic simulation, quality factor.

## Introduction

Transformer inverse design maps a requested electrical response to a manufacturable geometry. The inverse mapping can be nonunique. Agreement between forward and inverse networks helps screening but does not prove physical target attainment, particularly in sparse regions or after manufacturing-grid conversion.

Er et al. studied learned end-to-end synthesis of millimeter-wave passive matching networks with three-dimensional electromagnetic structures [1]. Chu, Mao and Wang studied transfer-learning-assisted transformer matching-network migration across technology nodes [2]. Their circuit objectives and metrics differ from this component-response task. We claim neither the first neural transformer inverse model, a new architecture, nor demonstrated improvement over these studies.

At 15 GHz, a user fixes both inductances and coupling magnitude. We select an integer quality-factor target from ten through twenty by matching all four responses symmetrically. Maximizing quality factor or treating it as a lower bound would solve a different problem.

We examine compatible historical coverage, controlled supplementation of sparse response regions, and physical realization of proxy-preselected designs. Historical qualification, full controlled supplementation and final-model validation remain incomplete. The following development results address these questions without substituting proxy consistency for electromagnetic evidence.

## Design Domain and Electromagnetic Evidence

### Electrical specification and validity

Let the electrical response be y = (Lp, Ls, Qmin, |k|), with Qmin = min(Qp, Qs). The original primary and secondary quality factors and signed coupling are retained in the data record. The requested inductance interval is 0.5 to 2.0 nH for each winding, and the coupling-magnitude interval is 0.2 to 0.85; all boundaries are inclusive. A design request fixes the three inductance-coupling coordinates. For each integer q from 10 through 20, its associated four-target vector is y(q) = (Lp, Ls, q, |k|).

The sampling window is not a proven rectangular feasible region. Marginal support or proximity in a two-dimensional projection does not establish joint attainability, and missing training examples do not prove physical impossibility. We therefore separate response-space occupancy from request attainment.

The native workflow exports Cadence geometry, audits the foundry geometry and port contract, applies Calibre, and simulates the same audited GDS with EMX. It retains four single-ended signal ports, grounded auxiliary connections, 50-ohm reference and original port order. Zero blocking violations are required within the macro/IP back-end rule scope, not full-chip signoff. Geometry, GDS and EM-output identities remain distinct.

Labels target 15 GHz, but evidence retains the original 5-60 GHz sweep at 1 GHz increments (56 points) for the extractor's below-half-self-resonance criterion. A 15 GHz-only calculation cannot establish resonance above 30 GHz. Production extraction and its validity scope are unchanged; a single-frequency calculation is not relabeled as an accepted sweep.

Strict eligibility requires finite labels and the executed strict-valid flag at 15 GHz, followed by joint inductance-coupling bounds. In the audited snapshot, strict validity equals descriptor validity plus the below-half-self-resonance condition. The separate 10 <= Qmin <= 20 intersection avoids discarding strict data outside that interval that may support forward diagnostics or a declared training guard band.

### Historical reuse and development partitions

The current quantitative audit covers one frozen source snapshot with 10000 unique geometries. Its source geometry partitions contain 6015 training, 2002 validation and 1983 test members. After the original 15 GHz strict filter, the corresponding eligible counts are 3175, 1006 and 1046, totaling 5227. The term source snapshot is important: ten thousand source geometries do not mean ten thousand gradient-training examples. The reference model uses the original strict training population, not a retrospective restriction to the new domain.

The new joint domain gives 1804 training, 595 validation and 619 test examples (3018 total), of which 3016 satisfy the Q interval. The later 6329 development geometries comprise 6281 audited members plus 48 accepted additions. Original 3018 memberships are retained in the 3801/1269/1259 train/validation/test split. Exclusions are 348 historical-research members and 23 strict original-pilot outputs. Normalization is train-only. This frozen development view is neither all history, the full 6700-member owner ledger nor a FINAL dataset.

Canonical geometry identity is preserved, but parent-family independence is uncertified because parent mappings are missing. Final splits must also group related perturbations. Normalization and sampling decisions use training data only. Consulted historical tests cannot become untouched benchmarks by renaming; development feedback and final physical test outcomes remain separate, with no update to the final model from its own test results.

### Response coverage and supplementation

The original 1804 strict in-domain training examples occupy 147 of 512 fixed 8 by 8 by 8 inductance-coupling cells, leaving 365 empty. A 4 by 4 by 4 grid provides sensitivity analysis. Occupancy describes this empirical distribution, not the achievable fraction of all devices or universal reachability inside an occupied cell.

In the in-domain training population, Qmin neighborhoods [18.5,19.5) and [19.5,20.5) contain two and zero examples. These are neighborhood counts, not exact integer-Q counts. Upper-scan requests must expose this limited support rather than clip targets and claim satisfaction.

The first supplementation comparison freezes 128 directed proposals and 128 geometry Latin-hypercube controls. Cells with fewer than five original training observations receive weight max(5 - count, 0); 102 distinct cells are sampled without replacement, with uniform targets inside each. Twenty-six geometry proposals provide exploration. Seeds 2026090901 through 2026090904 fix selection, exploration, controls and order. Of 256 proposals, 203 are eligible and 53 remain unreplaced holds. The closed prefix contains 16 solver-started attempts per arm, retaining invalid outcomes. Directed proposals yield seven strictly valid responses, four in-domain, two train-split additions and one newly occupied cell; controls yield ten, eight, four and zero, respectively. Occupancy changes from 147 to 148 versus 147 to 147. Recorded native elapsed totals are 1046.78 versus 1077.95 s. Coverage uses actual labels and the original 1804 training baseline. This admission-conditional prefix is not equal proposal efficiency, global wall time, completed 128-per-arm performance or evidence of statistical superiority. A separate fixed thirteen-outcome acquisition increment contains eleven extracted responses and two GDS failures: three responses are strict-valid and eight fail the half-self-resonance condition. This unbalanced increment is not a matched-budget comparison, establishes no coverage gain or acquisition winner, and admits no rows into training.

## Tandem Reference and Discrete Selection

### Implemented model and optimization

The current reference consists of a forward network with dimensions 10 to 256 to 256 to 256 to 4 and an inverse network with dimensions 4 to 256 to 256 to 256 to 10. Each hidden layer uses the existing tanh-approximation Gaussian error linear unit. The two networks have 135428 and 135434 trainable parameters, respectively. Frequency selects the package and is not an extra neural input. In contract order, the geometry coordinates are primary outer width and height, secondary outer width and height, line width, primary and secondary terminal-y spans, offset, and primary and secondary feed extensions, all in micrometers.

The inverse decoder applies an independent sigmoid to each bounded logit and maps it into the observed standardized training-geometry envelope before conversion back to physical units. This coordinatewise envelope is not a guarantee of coupled geometric feasibility. The exported geometry is rounded to the original 0.005 micrometer grid. Rounding occurs for export and scoring; it is not a differentiable straight-through operation in the tandem training graph.

Train-only normalizers standardize geometry and responses. Forward residual weights derive from training-label scales divided by declared response spans and are normalized to unit mean. The objective is proportional to fixed-span response MSE, not unweighted physical-unit error. Normalized loss is not an electrical accuracy percentage.

Following the tandem principle of Liu et al. [3], we train the forward first and freeze its validation-selected weights for inverse training while retaining gradients to generated geometry. The inverse objective combines response consistency with geometry-normalized reconstruction. This auxiliary term regularizes a nonunique mapping; recovering the original training geometry is not the criterion for inverse-design success.

The audited reference recipe uses a scheduled response weight and a geometry-term coefficient of 0.01. The response weight has a warm-up and ramp before the existing moving-average adaptation; the adaptation is associated with validation events, not every optimizer step. Validation checkpoint selection uses its documented response and geometry criterion rather than the instantaneous weighted training loss. These details are retained for reference replay. The current development study retains this recipe. Its inverse checkpoint criterion is the square root of response loss plus 0.01 times the square root of standardized geometry-anchor loss, without the scheduled response multiplier.

The reference uses AdamW (learning rate 0.0003, weight decay 0.0001), constant learning rate and unit gradient-norm clipping. Batch 32 accumulates microbatches of eight, sampling eligible train geometries uniformly with replacement. Seed 17 runs on a two-thread CPU. The nominal 12000-update budget per network is subject to saved time, exposure and early-stop limits; validation occurs at update one and every 100 updates. Optimizer, random-generator and scheduling states support continuation.

The formal reference selects forward/inverse updates 11800/11900 and retains both last checkpoints at 12000 with PARTIAL status, not a convergence claim. Development-5000 and formal-10000 references remain separately identified and their physical scores unmerged. The audited nonzero geometry gradient through the frozen forward confirms implementation behavior only, not physical correctness.

### Selection before electromagnetic evaluation

For each requested triple, the inverse network generates eleven continuous geometries. The original export mapping produces the manufacturing-grid candidates, and the frozen forward predicts the response of those exported candidates. The main score is defined by E(q) = sqrt(mean over j of ((Fj(ggrid(q)) - yj(q))/sj) squared), where s = (2.5 nH, 2.5 nH, 20, 0.8). The declared spans are retained historical scales; they are not the widths of the new target box and are not fitted to the evaluation requests.

The chosen qproxy minimizes the finite score, with exact ties resolved toward smaller Q. All eleven predictions, continuous and grid geometries, scores and orderings are retained. Because Q changes between candidates, this is the best symmetric four-target match among prescribed alternatives, not Q maximization or optimization of one unchanged four-tuple.

Selection is frozen before native evaluation. Analytical, export, DRC or solver failure remains a failed request; another Q cannot replace it. The ten unselected candidates are proxy-only and not requested in the main physical trial. All-request statistics therefore include selection failures.

The score used for selection and the criterion for simultaneous target attainment are different quantities. For compatibility, the legacy absolute tolerance vector is (0.125 nH, 0.125 nH, 1, 0.04). This is not a requirement that each response be within five percent of its own target. The implementation records the exact floating-point thresholds used at boundary comparisons. Physical-unit errors and target-relative errors will be reported alongside tolerance-dependent hit rates; no accuracy score is formed by subtracting a normalized error from one.

## Evaluation Design and Controlled Comparisons

### Forward and inverse evidence

Forward evaluation compares predictions with saved EM labels excluded from fitting and checkpoint selection. The 1259-case holdout differs from the 23 excluded historical-pilot strict survivors, whose Q selection prevents representative random-test interpretation. SELF_PROXY feeds inverse geometries into their scoring forward. In each physical pilot, EM-minus-target measures realization and EM-minus-frozen-forward measures surrogate discrepancy. These quantities, holdout, pilot populations and model identities are not pooled; final independent validation remains future work.

Feature summaries include signed bias, MAE, RMSE, median and upper absolute-error percentiles, and maximum error. Inverse percentage errors divide by the nonzero target; external-forward percentage errors divide by the absolute prior EM label. Numerical summaries display their defined-label count alongside original-request denominators, valid fractions and stage outcomes. Missing or invalid labels are neither zero error nor arbitrary hundred-percent error.

Rejection means target satisfaction was not demonstrated; resource waiting is pending, not device infeasibility. Partial-campaign attainment is not a completed success probability. P95 absolute error describes the observed error distribution, not a 95% confidence bound or maximum guarantee. Multiple Q candidates are not independent requests.

### Final random requests and the full scan subset

After data and model freezing, the final campaign will pre-generate 10000 independent uniform inductance-coupling triples with a recorded seed. Eleven Q evaluations per request yield 110000 proxy candidates. Only the preselected candidate enters the main native workflow: at most 10000 new solves before failures and exact reuse, not 110000 physical solves. Incremental execution preserves the original request list.

Before physical outcomes, 100 requests will be randomly selected for complete eleven-Q audits, adding at most 1000 solves because their selected candidates already belong to the main trial. A physical within-library optimum requires all eleven original candidates to be strict-valid; otherwise failures and missing candidates remain visible and no full-library optimum is declared. The audit never replaces qproxy in main statistics.

Exact reuse requires identical geometry and physical configuration with validated evidence. Requests, unique geometries, solves, cache hits and fresh-result fractions are counted separately. Repeated geometries remain distinct request outcomes, not independent physical realizations. An independent-EM known-reachable panel is reported separately; replacing its observed Q target with an integer scan does not preserve the original four-tuple's reachability guarantee.

### Network depth and width

The completed development study uses two layers of 256 units; three layers of 128, 256 or 512 units; and five layers of 256 units, each with seeds 17, 29 and 43. All models start from scratch on the same 3801 training geometries, with common labels, train-only normalization, activation and evaluation. The per-network cap is 23757 updates, with validation every 100 and patience of 20 validation events. With replacement sampling, this is a 200 draw-equivalent epoch budget, not 200 shuffled passes. Actual stopping steps differ. The retained PARTIAL receipts do not establish sufficient convergence, and the earlier 3018 experiment is not a same-update-budget data-size control.

Each configuration trains its own forward regressor. All inverse networks train through the same frozen validation-best forward from the new 6329-view three-layer 256-unit seed-17 baseline, while preserving gradients with respect to generated geometry. Thus inverse seed dispersion is conditional on one shared surrogate, not complete-system forward-and-inverse uncertainty. The same 1269 validation geometries select checkpoints and provide the architecture diagnostics. The 1259 test geometries were subsequently evaluated once for the pre-frozen three-layer 256-unit seed-17 pair, not to rank all configurations. That evaluation made no new model selection or training update. Neither the holdout results nor the development128 physical outcomes are used to reselect the reported pair; the separate historical-reference weights remain unchanged.

The Q-selection comparison will distinguish fixed Q15, the preselected qproxy procedure and the complete-EM subset. Comparisons use the same request triples and preserve their own target-Q definitions. The full-EM subset provides a within-library diagnostic under a larger physical budget; it is not an equally priced deployable baseline. A training-only nearest-neighbor retrieval baseline can provide an additional reference without treating lookup on a test library as independent prediction. These comparisons remain incomplete and their anticipated benefit is not reported as a result.

## Development Results and Physical Status

The five configurations completed 15 forward-inverse pairs and 538099 main optimizer updates. Best and last checkpoints were saved and validation-best checkpoints reloaded. Summed role-training time was 1653.45 s on a two-thread CPU, excluding evaluation, orchestration and earlier experiments; it is not wall time or time to convergence. The baseline pair was reused. Isolated load/resume checks on the other fourteen pairs added 28 diagnostic updates, excluded from the main count; they do not establish bitwise trajectory equivalence.

Table I reports three-seed mean forward validation MAEs and parameter counts. Every seed uses the same 1269 validation geometries. The machine-readable source retains per-seed values and sample standard deviations; neither the means nor those deviations are confidence intervals. The comparison is descriptive under the stated budget, not a physical ranking or proof of an optimal architecture.

**Table I. Forward parameters and validation MAE**

| Shape | Forward params | Lp MAE (nH) | Ls MAE (nH) | Qmin MAE | \|k\| MAE |
| --- | --- | --- | --- | --- | --- |
| 2×256 | 69636 | 0.00760 | 0.00766 | 0.1689 | 0.00759 |
| 3×128 | 34948 | 0.00747 | 0.00734 | 0.1488 | 0.00632 |
| 3×256 | 135428 | 0.00689 | 0.00692 | 0.1479 | 0.00601 |
| 3×512 | 532996 | 0.00763 | 0.00718 | 0.1492 | 0.00622 |
| 5×256 | 267012 | 0.00779 | 0.00768 | 0.1515 | 0.00666 |

Three-seed means; each inverse has six more parameters. Qmin and |k| are dimensionless. No confidence intervals.

Grid-converted inverse outputs have mean SELF_PROXY joint-hit rates of 98.77%, 98.69%, 98.71%, 99.00% and 99.58%, in configuration order. Each rate retains all 1269 validation targets and analytical failures under the stated absolute tolerances. Targets retain their observed Qmin, not an integer-Q scan. These rates are conditional on the shared surrogate and are not five independent complete forward-inverse physical rankings. The physical pilot below evaluates only the frozen three-layer 256-unit seed-17 development pair.

The completed development holdout evaluates that pair at forward update 14300 and inverse update 11300 on all 1259 reserved test geometries, following the pre-frozen selection and evaluation protocol. Forward MAEs against their saved EM labels are 0.007493 nH, 0.007142 nH, 0.148353 and 0.006409 in Lp, Ls, Qmin and |k| order. Continuous and grid-converted inverse outputs each attain 1253/1259 targets (99.52%) under SELF_PROXY evaluation. The original denominator retains five analytically invalid geometries; analytical passes total 1254/1259, and undefined full-cohort inverse errors remain null. These one-shot targets retain their observed Qmin rather than scanning integer Q. No new EMX was run on these generated holdout geometries: REAL_EMX_VALIDATION=NOT_RUN and manufacturability is not proven. The result is a development holdout diagnostic, not a final benchmark or physical success rate.

Separately, the same frozen development pair completed the original 128 selected-candidate physical frame with no pending requests. The final accounting is 35 analytical failures, seven GDS failures, 38 strict-invalid extracted responses and 48 strict-valid responses; eight of the strict-valid responses miss at least one tolerance. Joint attainment is 40/128 (31.25%) over all original requests and 40/48 (83.33%) conditional on strict validity. The latter excludes 80 failed or strict-invalid requests and must not be called overall model accuracy. All original targets, pre-EMX qproxy choices and failure identities were retained without replacement. No full eleven-Q physical optimum was computed. The 86 bound Touchstone outcomes are not an independently certified native-attempt count. These fixed-frame physical results are separate from the 1259 empirical holdout targets and cannot turn their SELF_PROXY rate into an EMX accuracy estimate.

The separate pilot used the unchanged historical formal-10000-source reference, not either new development model. Its 64 PCG64-seed-202609081501 requests, 704 proxy rows and selected Q values were preserved. All requests are terminal: 58 EMX extractions, four GDS failures and two analytical failures. Of the extracted responses, 23 are strict-valid and 35 fail the half-self-resonance condition. Joint attainment is 17/64 (26.56%), or 17/23 (73.91%) conditional on strict validity. EM-minus-target MAEs are 0.023456 nH, 0.068636 nH, 0.169219 and 0.049941. Failures remain in the original denominator; no selection was replaced. Complete target-relative error tables are retained for author-made figures.

Separately, the predesignated 6329-view three-layer 256-unit seed-17 forward at update 14300 was loaded on the 23 strict pilot geometries excluded from that dataset. Against their prior EM labels, MAEs in Lp, Ls, Qmin and |k| order are 0.010147 nH, 0.006463 nH, 0.185744 and 0.011359; corresponding mean absolute percentage errors are 1.1001%, 0.6153%, 1.4426% and 3.1447%. Percentage denominators are the absolute EM labels, not design targets. Historical Q selection, strict-survivor selection and uncertified family independence limit this 23-case diagnostic. It is not a random final test, model-selection evidence or physical validation of the new inverse; the other 41 original requests have no forward diagnostic.

### Separate controlled 64-proposal acquisition trial

A separate 64-proposal trial froze 25 sparse-targeted selections plus seven exploration proposals against 32 geometry-LHS controls. Decisions used the frozen 3801-training-row, 159-occupied-cell baseline, without feedback from the prior twenty research train additions. Each arm completed sixteen native solver starts and extractions, reaching its cap before the six-hour deadline. Directed/DOE counts are 2/10 strict-valid, 1/5 strict-and-in-domain and 1/4 unique train-eligible candidates; the remaining in-domain DOE case retains its test split and is excluded from coverage feedback. The directed arm's sole train-eligible candidate is EXPLORATION-005, not a sparse-targeted success. Both arms remain at 159 occupied cells, with no cell crossing the five-example threshold. The primary equal-start comparison at m = 16 is complete, but the primary first-four-qualified-train comparison at K = 4 is NOT_REACHED; a smaller K is not substituted. All 64 proposals are terminal: 32 extracted, fourteen analytical failures, five unclassified pre-native failures and thirteen budget-not-dispatched outcomes, not pending work. No GDS or solver cause is assigned to the five unclassified cases. Eligibility does not confer formal admission or change the frozen data or training. Recorded native solver wall-clock totals are 1103.26 s for directed starts and 1262.35 s for DOE. Complete per-arm pipeline and storage costs remain unavailable; missing values are null, not zero, and equal starts do not imply equal total cost. This single policy/seed comparison does not establish general sampling superiority.

## Limitations and Conclusion

The model study uses the frozen 6329 development geometries. Acquisition baselines 1804/147 and 3801/159 (training rows/occupied cells) belong to different trials and cannot establish a controlled data-size effect. Parent-family independence and convergence remain uncertified; occupancy depends on binning and does not prove attainability. Shared-forward inverse dispersion excludes surrogate-retraining uncertainty. The once-evaluated 1259 holdout cannot support further test-driven tuning or FINAL status. Historical64, development128 and SELF_PROXY holdout rates have different models or target populations and procedures, precluding controlled-improvement claims. The historical pilot has 35 half-self-resonance failures; the development128 has 38 strict-invalid outcomes without an assumed common cause.

Conclusions remain specific to the recorded topology, process and simulation settings. There is no measured-silicon, process-corner, mesh-convergence, broadband-synthesis or system-level matching validation. Retaining 56 sweep points for validity does not demonstrate broadband inverse design. Evidence must distinguish model, coverage, export, solver and specification failures.

Development training, one reserved holdout, both physical pilots and the two distinct acquisition comparisons are now recorded. The development pair meets 40/128 original physical requests (31.25%); the conditional 40/48 and SELF_PROXY 1253/1259 are not overall physical accuracy. The new acquisition trial adds no occupied cells and does not reach its primary first-four-qualified-train comparison. These findings establish no final-model advantage or five complete forward-inverse physical rankings. The 100000-qualified-geometry objective, certified final partitions and independent 10000-request physical campaign with its prechosen full-Q subset remain incomplete.

## References

S. Er et al., "Deep learning assisted end-to-end synthesis of mm-wave passive networks with 3D EM structures: A study on a transformer-based matching network," in 2021 IEEE MTT-S International Microwave Symposium, 2021, pp. 66-69, doi: 10.1109/IMS19712.2021.9575030.

C. Chu, Y. Mao, and H. Wang, "Transfer learning assisted fast design migration over technology nodes: A study on transformer matching network," in 2024 IEEE/MTT-S International Microwave Symposium, 2024, pp. 188-191, doi: 10.1109/IMS40175.2024.10600344.

D. Liu, Y. Tan, E. Khoram, and Z. Yu, "Training deep neural networks for the inverse design of nanophotonic structures," ACS Photonics, vol. 5, no. 4, pp. 1365-1369, 2018, doi: 10.1021/acsphotonics.7b01377.
