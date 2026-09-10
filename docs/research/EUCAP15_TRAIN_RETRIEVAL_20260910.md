# 15 GHz train-only retrieval baseline — 2026-09-10

## Completed scope

One real development retrieval execution, not neural training or fresh EMX. The immutable6329 dataset supplies3801 train library geometries;1269 validation and1259 test identities remain separate. Only training numerical labels/geometry are used as the library. Original continuous geometry and saved EM labels are returned together without averaging, rounding or repair.

Protocol SHA: cbf8075993fa4c3d84722e8c9279bf33803b30274932afa1ef38377a32004b97.
Implementation SHA: d1c2278945d0632aa36aff47ca7684ce1086be5ee32573abdcd42b71924fc8f2.
Data identity SHA:4dc6e91a644db2aca0a580958ac8b31e1e26ed73497b935145202b07227ef580.
Evidence: PRIOR_TRAIN_EM_LABELS_NOT_FRESH; REAL_EMX_VALIDATION=NOT_RUN; FINAL=false.

## Method and panels

Select one train geometry minimizing RMS((stored_response-target)/[2.5,2.5,20,0.8]). Exact ties choose lexical geometry SHA; Q ties choose smaller integerQ. All3801 eligible train entries are retained, not filtered toQ10..20.

- Validation: the existing1269 frozen four-indicator targets; no Q scan. This is a development holdout already used to select neural checkpoints, not an untouched final test.
- FixedQ15: original128 request triples withQ15, an explicitly separate lookup control.
- RetrievalQscan: the same128 triples, integerQ10..20, selecting from stored train labels. q_retrieval is neither the original neural q_proxy nor a new-candidate q_emx. No original candidate or physical denominator is changed.

## Actual numerical outputs (independent numerical review recorded with delivery)

Independent numerical QA: GO, exact receipt `independent_qa_v1/INDEPENDENT_QA_RECEIPT.json`, SHA20fd850c98fd22e6c767035cc8e35b41beb207f6fcd9bf7661d19c2d5dd72fc6. It checked all3801 library members,2933 result records and12 aggregate rows. Exhaustive lookup verification covered all1408 Q queries and a predetermined65 validation queries; it did not exhaustively verify all1269 validation argmins. Maximum numeric difference5.551115123125783e-17. Separate claims review: SHARE_WITH_CAVEATS, SHAef74b8c9ab30be5bfce6e64df92c80c40b272f62784a129d2fb8e8b5b5f002ee.

Full-precision public aggregate: [METRICS.csv](eucap15_train_retrieval_20260910/METRICS.csv), SHA4bfa3514f9ed82e827f1e4adb0f2172bd6749793f3b7074f70402307c754dacd. Field values match the original12 rows; public LF versus source CRLF is the only format change. No private geometry or source paths appear in that table.

| Panel | N | Unique retrieved geometries | Legacy joint hits | Mean fixed-span RMS |
| --- | ---: | ---: | ---: | ---: |
| Validation fixed4 |1269|1041|1260/1269 (99.2908%)|0.0116980|
| Original128 fixedQ15 |128|72|24/128 (18.75%)|0.0900769|
| Original128 retrievalQscan |128|71|44/128 (34.375%)|0.0728185|

Joint tolerances are absolute[0.125nH,0.125nH,1,0.04000000000000001], not target-relative5%.

| Panel | Lp MAE (nH) | Ls MAE (nH) | Qmin MAE | abs(k) MAE |
| --- | ---: | ---: | ---: | ---: |
| Validation fixed4 |0.0242669|0.0245460|0.203852|0.00797799|
| Original128 fixedQ15 |0.197578|0.176997|1.339472|0.0771585|
| Original128 retrievalQscan |0.162059|0.157258|0.204631|0.0751924|

Stored-label retrieval only: these rates must not be combined with SELF_PROXY or fresh EMX denominators. Validation target distribution differs from the128 random-range requests; their rates are not directly comparable. Qscan includesQ15, so a non-increasing minimum score is guaranteed by the candidate set, not evidence that neural Q-scanning improves real physics. Joint box hits are a different objective from RMS and are not mathematically guaranteed to improve. Reusing the same geometry across queries does not establish a count of native solves or cache hits. No population confidence interval, family-independence certification or physical champion is claimed.

## Execution, tests and outputs

18 new synthetic API tests passed once before the actual run. No old pytest suites were rerun. Actual CLI exit0; full command0.47s, maxRSS114999296bytes (one local CPU thread requested). These timings are not a controlled neural/native benchmark. No model load, training update, random target generation, solver call or figure was added.

Private package(relative to workspace): reports/eucap15ghz_20260908T220300Z/train_retrieval_baseline_20260909_v1.
run_v1 contains RETRIEVAL_LIBRARY.json; VALIDATION_RESULTS.csv(1269); QSCAN_ALL1408.csv; FIXED_Q15_RESULTS.csv(128); QSCAN_SELECTED128.csv; METRICS.csv(12); SUMMARY.json; RECEIPT.json; SHA256SUMS.

- Summary SHA:e111372d00b087894776232e298c36de486b7389122665726f64fc06fc5f8166.
- Result receipt SHA:be76b0c2fe048b21765245a4c665db030224e68ea881004bfe6e411e1ec27e58.
- Result SHA index:9d9b60c748eca52c55d83b9acfd220e57e0f96a10ead100d1635c8a848291833.
- Synthetic test receipt SHA:7806f4f2fbbdd08c23adaf83a35078f6947aadc9baac5d8ad47cef5a6eb0a2bd.

The exact executed command/environment is in ACTUAL_EXECUTION_RECEIPT.json. The invocation shape below requires the private, checksum-pinned sources; a Git clone alone does not contain them:

```sh
python -B -m research.broadband56_nn.eucap15_train_retrieval --protocol /absolute/private/PROTOCOL.json --out /absolute/new/lookup_output
```

Use the existing sealed result for this exact experiment. Output paths are no-clobber; this example is not an instruction to rerun it. Source library, per-request geometry and private source paths are not published. No native dispatcher, automatic successor or production policy is installed or changed by this module.

## Remaining goal work

This completes an empirical library control, not the EuCAP goal. New128 native validation still awaits the sole owner's true successor boundary; latest native observation is9Sep23:37:20UTC, not live status. FINAL dataset/model freeze,10000 selected physical requests and100 full11Q audits are still incomplete. Author figure production remains manual/traditional from checked numeric sources.
