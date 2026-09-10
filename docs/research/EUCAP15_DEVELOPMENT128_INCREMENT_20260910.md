# 15 GHz development128 — first physical-result increment

Status: GO_SCOPED_DESCRIPTIVE_DEVELOPMENT128; not FINAL.

The frozen128-request development frame contains48 strict-valid physical
responses and40 confirmed joint hits. Failures and2 unpublished terminals remain
in the128 denominator; small survivor errors do not establish a high whole-frame
success rate or achievement of the final100K-data/10000-request goal.

One research intake used the owner's closed export and an externally prepared,
closing-receipt-bound snapshot. It did not rerun inference, training or EMX.
The model is `f15-development6329-3x256-seed17-9d69d7ebfc66`:
6329 source geometries,3801 gradient-training,1269 validation and1259 test
rows (test count only). These results are not those of the older reference64
or the acquisition120; none of these frames is the final independent10000.

## Fixed-frame accounting

| Original selected requests | Count |
| --- | ---: |
| Strict-valid fresh-response candidates | 48 |
| Extracted but strict-comparison invalid | 37 |
| Actual GDS audit failures | 6 |
| Original analytical failures, retained | 35 |
| No terminal in the frozen observation | 2 |
| Original denominator | 128 |

126 terminal receipts are accounted for. Of these,91 are new terminal results:
85 extracted responses and6 GDS failures; the35 earlier analytical failures are
reused unchanged. Each request keeps its original single `q_proxy`; the
11Q proxy search does not represent11 physical solves. No candidate was replaced.
Pending is a snapshot observation, not a statement about current process liveness.
All37 invalid extractions were individually checked against their original flags:
`below_half_srf=false`, while finite values, descriptor validity, passivity and
reciprocity passed. This is an audited reason count, not an assumed SRF label.
The source observation was September10,2026 at14:30:19UTC; the local closed-bound
publication time14:53:51UTC does not refresh the remote observation.

40 requests satisfy all four absolute tolerances with strict-valid physics:
`[0.125 nH,0.125 nH,1.0,0.04000000000000001]` in `[Lp,Ls,Qmin,|k|]` order.
Thus confirmed joint hits/original requests =40/128=31.25% at this snapshot.
This is observed coverage while2 remain pending, not a completed success rate.
The conditional48-strict subset has40/48=83.33% joint hits; this must not replace
the original-denominator result. Strict-valid coverage is48/128=37.5%.
No confidence interval was estimated; percentile errors are not intervals.
Independent QA recomputed all8 metric rows and384 ECDF points, checked original
targets/Q/identities and original feature flags, and found source bytes unchanged.
It did not rerun the consumer, model or native simulation.

## Errors within the48 strict-valid responses only

| Target metric | MAE | RMSE | P95 absolute error | Mean target-relative absolute error |
| --- | ---: | ---: | ---: | ---: |
| Lp [nH] | 0.016222 | 0.032827 | 0.052511 | 1.8638% |
| Ls [nH] | 0.019200 | 0.036200 | 0.092371 | 2.1623% |
| Qmin [dimensionless] | 0.190752 | 0.318808 | 0.736408 | 1.4507% |
| K_abs [dimensionless] | 0.032374 | 0.075632 | 0.210929 | 5.1932% |

Percentage means are `mean(100*abs(actual-target)/target)`, not error divided
by a fixed span and not the engineering hit tolerance. Q remains a symmetric
target, not a lower bound. K error has a substantial observed upper tail even
among strict-valid survivors; mean errors alone must not hide it.

`EMX minus frozen grid proxy` is supplied separately as surrogate diagnostic
error. It is not interchangeable with `EMX minus target`, which measures inverse
design realization. No causal improvement over the old64 or acquisition42 is
claimed: their targets, models or selection mechanisms differ.

## Author-made figure sources and captions

These are data/figure specifications, not generated paper figures.
The author should create and inspect final figures in conventional plotting tools.

1. **Request outcome bar.** Use the five mutually exclusive counts above, total128,
   with an optional separate annotation40joint/128. Do not stack joint hits on
   top of48strict because they overlap. Caption: "Fixed128 selected-candidate
   development frame; failure and pending requests retained."
2. **Four target-versus-realization scatter panels.** Read `target` and `actual`
   arrays from `REQUEST_RESULTS.csv`; use all48 `STRICT_VALID` rows. Keep
   37 `EMX_INVALID` outside the strict panels (or separately marked as diagnostic
   equivalent descriptors); absent responses remain absent, not zeros. Use equal
   target/actual axis units, identity lines and the declared absolute tolerances.
   Caption must include "48strict-valid of128original requests; survivor-conditional".
3. **Absolute-error CDF panels.** Use `ERROR_ECDF.csv`, filter
   `comparison=emx_minus_target`, one series per feature. It supplies rank/n with
   n=48, originalN=128 and ties retained. Label Q and|k|dimensionless. P95 is an
   observed quantile, not a95%confidence bound or worst-case guarantee.
4. **Target versus proxy residual table/panels.** Use `PHYSICAL_METRICS.csv`'s
   separate comparison field. Do not conflate small forward-surrogate residuals
   with targets being achieved. Both panels condition on the same48strict rows.

## P215 historical-source increment

An actual once-per-table pinned row join resolved two historical sources:
97567 `base_pool:accepted_pool` and118218 `training_csv:new_training_table`,
total215785 rows. Historical bounds select97037+118218=215255, excluding530.
The118218 are already inside215785;raw120K cannot be added again.

These are source counts, not current15GHz eligibility or the four reuse-category
counts. Current process/port/GDS/DRC compatibility, response15GHz/SRF evidence,
current range and eligible cross-source dedup remain unclosed. Current100K
qualified count stays unknown, not215255 or215785.

## Exact provenance

- Public aggregate data: [summary](data/eucap15_development128_increment_20260910/SUMMARY.json)
  and [metric table](data/eucap15_development128_increment_20260910/PHYSICAL_METRICS.csv).
  [Source map](data/eucap15_development128_increment_20260910/SOURCE_MAP.json) records
  exact summary bytes and CSV CRLF-to-LF-only publication; all8 parsed rows match.
- Independent QA receipt SHA256: `14ec1067a5bb6aa807b89856f281364523e05282c6381ab757f012088fd1dd03`.
- Statistics summary SHA256: `c38fdac76de70088095d2486c790dc1320f55087ecfb6625ee424f480c3b3144`.
- REQUEST_RESULTS.csv SHA256: `d27ce63a60902f00f1f1f53eef2972168d2ef3d349485f6c81c896120f47c75d`.
- PHYSICAL_METRICS.csv SHA256: `d0762affb5ca0b2625ec9ecd41924384fd01d723036859e705734d4f765122ea`.
- ERROR_ECDF.csv SHA256: `97efdaae963827fa75abfeff267a330ec98bc245d42939a19edd9e4378d84ac2`.
- Research intake receipt SHA256: `bdc061331571924ad8ca49bec02eef4b2a8b4b81542e8f73cc9e400d120716bc`.
- Owner closing receipt SHA256: `664ce59fc5ef865dabdeaa8ef63509ab3a231b0eb7189b23b60d42c04f132da8`.
- Externally closed-bound reader snapshot SHA256: `fcfa89d3afc172f21d08f4e46630ac3c6dd453ab7466468d09b58578307aab19`.
- P215 source-count result SHA256: `38d901806f95e72c1bbc8b18afa32a76bc9071ff68d18a47871acf96f9788407`.
- Native observation is a frozen publication,not live status;pending identities
  and all original evidence/failed intermediate exports remain preserved.
- The one-adjacent-float rule only reconciles a derived scalar score and is
  recorded separately;it does not relax raw labels,Q,validity or hit decisions.

Private full sources are indexed in the existing research output directory
`reports/eucap15ghz_20260908T220300Z/development128_statistics_20260910T145600Z`.
No raw geometry,PDK,private models or full source closure is embedded here.
