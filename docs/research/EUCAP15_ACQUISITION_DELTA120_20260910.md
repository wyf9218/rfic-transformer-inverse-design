# 15 GHz acquisition: new 120 closed records

Status: independently checked descriptive development acquisition increment. This is not new6329 inverse accuracy or FINAL10000.

## Frozen scope and retained denominator

This consumes the already exported, previously unprocessed terminal records
from the original256 acquisition proposals. Selection is all new closed
executed terminals in original global order, not strict validity or target hit.
Previous matched32 and fixed13 contribute only exact exclusion identities;
their physical data are not reprocessed.

At the owner's 2026-09-10T05:29:13.367425+00:00 observation:
147 extracted +18 execution failures +53 original holds +38 without terminal
=256. That snapshot does not establish current process liveness.
The new120 are104 full physical chains plus16 actual GDS-audit rejections.
There are no replacement candidates or post-EMX Q substitutions.

## Actual new-delta accounting

| Source | Original new-delta count | Strict valid | EMX invalid for strict comparison | GDS rejected | Strict and inside new core domain |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sparse targeted | 42 | 11 | 24 | 7 | 10 |
| Geometry DOE | 66 | 25 | 32 | 9 | 17 |
| Exploration | 12 | 7 | 5 | 0 | 4 |
| Total | 120 | 43 | 61 | 16 | 31 |

Core means strict-valid EM results with Lp/Ls each0.5–2.0nH and |k|0.2–0.85.
The31 are response-domain eligible observations, not31 newly deduplicated
training members or31 new occupied bins. No training admission, deduplication
against the current6329, or coverage-gain calculation is performed here.
EMX-invalid rows retain their extracted descriptor values but are excluded from
strict numerical accuracy summaries; GDS failures have null physical values.

## Targeted residuals: explicit conditional population

Only sparse-targeted proposals have response targets. DOE and exploration have
no target/proxy/Q objective and therefore no invented target residual.
The numeric table uses the11 strict-valid targeted survivors; it does not
represent all42 targeted proposals or a deployed model's accuracy.

| Physical feature | MAE | RMSE | Mean absolute target-relative error (%) |
| --- | ---: | ---: | ---: |
| Lp (nH) | 0.0191215965 | 0.0244740127 | 2.63884167 |
| Ls (nH) | 0.0225934031 | 0.0320978657 | 3.55216584 |
| Qmin | 0.3199670171 | 0.3966356083 | 2.34114959 |
| Absolute k | 0.0170748281 | 0.0267751371 | 3.45285864 |

Per feature e=EMX-target; MAE=mean(|e|), RMSE=sqrt(mean(e²)),
MAPE=mean(100|e|/target), not an EMX-denominator or fixed-span percentage.
The strict joint-hit count is10, retaining42 as the original targeted-delta
denominator:10/42=23.8095%. Absolute tolerances are
[0.125nH,0.125nH,1,0.04], not uniform5% relative error.
This is a selected completion subset of an acquisition procedure, not IID
random model-validation requests. Different source counts/completion order
prevent an equal-budget or causal sparse-versus-DOE superiority claim.

The scoring contract stays symmetric with spans[2.5nH,2.5nH,20,0.8].
Q=min(Qp,Qs); targeted Q remains the pre-frozen integer10..20.
No EMX-best-Q or full11Q audit is computed.

## Evidence and repair

The original local consumption attempt stopped before accepting any row:
Original failure proposal differs. Its FAIL receipt is retained unchanged.
The actual native original_record has35 unchanged proposal fields plus5 exact
derived aliases: grid_geometry,grid_proxy,analytic_grid,candidate_id_sha256,
candidate_geometry_identity_sha256. The adapter now accepts the plain frozen
proposal or this exact complete derived form; changed, partial or unknown
fields still fail. No source data, candidate or solver output was changed.

The corrected consumer completed120 in3.72s (one execution), with0 new native
actions,0 model loads/updates,0 old physical rows reprocessed and2914 consumed
source bindings. Existing parser verifies the unchanged GDS, required geometry
and Calibre gates, physical configuration, solver receipts/S4P hashes, original
56-point5–60GHz sweep, and saved15GHz strict/descriptor/SRF predicates.
It does not rerun an electromagnetic solve or re-extract S-parameters.

New helper regression:10 targeted cases PASS, previous34 deselected.
Driver software verification covers20 distinct synthetic cases across
12+8+4+1 executions, not one final-source full20 run.
Neither synthetic test count is a physical sample count.

Independent saved-value QA passed for all120 rows and104 original56 CSVs:
35,538 field assertions and324 distinct input files rehashed at completion.
The checker uses standard-library arithmetic without importing the consumer,
chain parser, metrics reducer, models or native tools. It inherits the original
chain-authenticity audit and does not rerun GDS/DRC/EMX. Derived numeric values
are checked at1e-12 absolute/relative tolerance; original parsed CSV values
are compared exactly. The immutable original SUMMARY retains its pre-QA
status; the separately pinned QA receipt and RUN_STATE record completed review.

- Input config SHA-256:860e8e255af8e9740d8b076d3a4d07230edc4a0ff484c6b430588bde3fc31260
- Actual acceptance receipt SHA-256:0687ca2f9c3589b4a28d912dff64d17df80822da8ea4fc1791e7d51b4507afcd
- Summary SHA-256:6dd9da265a628cb58ab126be75170c1e063c96547ec75512cee7c445d9e03cdb
- Preserved first FAIL SHA-256:ee630508c87b018ed6b5bb9990d8d44b8c822305894e990e00e3f5619055a9b5
- Independent numeric QA SHA-256:a33c07d01f0cffad9ef65fd5ce42440ecf11c179f87a889c42c2ca0534f1a50a

## Author figure source and invocation

Use the exact aggregate JSON in eucap15_acquisition_delta120_20260910/SUMMARY.json
for source-count/error tables. Private REQUEST_RESULTS.csv/JSON retain all120
identities, errors, nulls and source references. Suggested author panels:
source-level stacked status counts with original denominators, plus targeted
residual distribution/CDF explicitly limited to11 strict survivors and the
10/42 all-targeted joint-hit rate. No image is generated by this package.

Caption: “Previously unprocessed terminal increment from a frozen256-proposal
15GHz development acquisition batch, observed2026-09-10 05:29 UTC.
The120 records include43 strict-valid EM results,61 strict-invalid extracted
results and16 GDS rejections. Error statistics are conditional on11 strict
targeted survivors; failures remain in the42 targeted-delta denominator.
No equal-budget gain or new-model physical accuracy is inferred.”

CLI: python -B -m research.broadband56_nn.eucap15_acquisition_delta
--inputs ABSOLUTE_PRIVATE_CONFIG --inputs-sha256 EXACT_CONFIG_SHA
--out NEW_ABSOLUTE_OUTPUT_DIRECTORY.
A public clone does not include private input maps, datasets, weights, PDK,
GDS or S4P. Inspect existing results instead of rerunning this completed delta.

The new6329-model128 physical batch remains separately unreceived (35 original
analytic failures and93 without RESULT at the saved observation).
FINAL dataset/model,10000requests and100 preselected complete11Q audits remain
unfinished. The existing sole-owner native scheduling mechanism is unchanged.
