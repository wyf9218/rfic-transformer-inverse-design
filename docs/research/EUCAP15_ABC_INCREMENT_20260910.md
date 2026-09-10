# 15 GHz: acquisition admission and B/C handoff

This increment preserves the 100K data target and final independent 10K validation goal. It does not complete either target.

## A. Actual research admission

31 previously validated strict/core geometries were written to a separate research increment: 20 train, 3 validation and 8 test. The original120 records and sampling origins remain intact. No canonical or nominal0.005um grid collision was found within the batch or against all6329 members, known6700, previous45 observations or2112 reserved all-Q geometries of reference64 and development128. These are parameter identities, not a GDS/family-independence proof or an exhaustive P215 audit.

The original6329 snapshot, production accepted table, frozen splits, model weights and normalizers were not modified. The unchanged models still have3801 gradient-training members.3821 below is the eligible research train pool after admission, not newly trained exposure. New validation/test records remain separate and are not the final10K request experiment.

| Quantity | Before | After | Increment |
|---|---:|---:|---:|
| Train-eligible geometry count |3801|3821|20|
| Occupied response cells /512 |159|163|4|
| Cells with fewer than5 samples /512 |402|402|0|
| Total deficit to5 per cell |1921|1913|-8|
| Maximum actual train K_abs |0.5806891255|0.6726857936|Observed extension|
| Actual train K_abs greater than0.8 |0|0|0|
| Maximum actual train Qmin |18.70441442|18.70441442|0|

| Sampling origin | Closed records | New eligible | Assigned train/val/test | New train cells | Deficit filled |
|---|---:|---:|---|---:|---:|
| Sparse-targeted |42|10|7/0/3|4|7|
| Geometry DOE |66|17|11/3/3|0|1|
| Exploration |12|4|2/0/2|0|0|

Coverage is computed from actual strict EM responses of admitted train members only. Same eight equal bins per Lp/Ls/K axis, domain[0.5,2]nH/[0.5,2]nH/[0.2,0.85], internal edges assigned upward and final upper edge included. Sparse means fewer than5; deficit is sum(max(5-N,0)). These post-hoc3801 baseline results do not replace the frozen1804 decision baseline or original model/recipe. Source-specific cells can overlap; do not generally add their increments.

The private all120 ledger retains original target cell, separately calculated frozen-proxy cell and actual EM cell. DOE/exploration have no response target or proxy, so those cells/errors stay null. Invalid descriptor landings are not admitted strict coverage.

## Recorded costs and invalid reasons

| Origin | Cadence elapsed sum(s) /records | EMX native wall sum(s) /solvers | EMX CPU time sum(s) |
|---|---|---|---:|
| Sparse-targeted |265.046524 /42|2119.58 /35|4263.68|
| Geometry DOE |461.130746 /66|4127.36 /57|8342.99|
| Exploration |88.492616 /12|892.27 /12|1799.05|
| All new120 |814.669885 /120|7139.21 /104|14405.72|

All120 costs include recorded failed-candidate stages. GDS-audit/Calibre elapsed and complete end-to-end cost remain unknown because explicit timings are absent; unrun/missing values are not imputed as0. Native wall sums, reported CPU time and overlapping wrapper intervals are distinct; do not add them together or call cumulative solver elapsed campaign parallel wall time. Unequal closed subsets42/66/12 do not establish same-budget superiority.

The61 invalid extracted responses were checked individually against saved original predicates: all61 have below_half_srf=false (24/32/5 by source), without additional failed recorded predicates in this batch. This is a checked result, not an assumption that all EMX-invalid data are SRF failures. The16 GDS failures remain separate. The complete metadata source closure is the private READ_SOURCE_PINS.json; row source_pins lists first-read increments, not a standalone complete row closure.

## B. Native execution status

The sole native owner's bounded live check failed because the SSH control session disconnected. At12:56:50UTC current256/128 process and completion counts were UNKNOWN, not failed/zero. Opening the saved Terminal login is not successful authentication. No new128 terminal was received in this work package; current strict/hit counts are unavailable. The original targets, weights,128q_proxy and35 analytic failures are unchanged.

Static deployed-code review shows hold RESULTs count in all256 accounting; absent RESULTs are pending. It does not prove a hold-related successor bug or current runtime health. No competing controller, repeat submission or signal was issued. One scoped live check and incremental receipt can proceed after authentication is restored.

## C. Historical source reuse

P215 retains215785 historical rows, not certified independent current-eligible geometries. Offline evidence identifies10 historical inventory entries, including118218 active-v13 rows already realized in P215; raw120000 must not be added again. Other historical totals are not established P215 contributions. Current four-way geometry counts remain null, not0.

The missing bounded read is exact merge_source values joined by source_row_index to the pinned audit index. Source-specific process/GDS/DRC identity, actual frequency/port/SRF evidence and current-core dedup must then support classification.111vs56 grids are separate: both configured grids include15GHz, but no actual S4P or SRF/port revalidation occurred here. No interpolation, renamed fresh56 output or abs(k)-based compatibility shortcut was used. The100K qualified count is not certified.

## Figure sources and captions for the author

COVERAGE_BEFORE.csv and COVERAGE_AFTER.csv are exact aggregate copies of the research output,512cells each; SUMMARY.json contains source contributions and K/Q distributions. All hashes are recorded in the private delivery/publication receipt. Original target/geometry/physics files are not public.

Coverage caption: "Retrospective real-response coverage of the frozen3801 training members and20 newly admitted training-assigned geometries. Occupied cells increased159 to163; missing membership to5-per-cell decreased1921 to1913. Validation/test labels do not drive coverage. This unmatched completion increment is not a controlled same-budget comparison or proof of reachability."

Support caption: "Actual train-pool K-bin and Q-stratum counts before and after the research increment. MaxK increases to0.672686; K>0.8 remains unobserved and maxQ remains18.7044. Counts are observations, not guaranteed inverse-model support."

The15 completed pairs are not retrained. Existing validation supports a descriptive3x256 development choice; common-F inverse results are not five independent F/I physical rankings. Reference64, acquisition120/targeted42 and development128 remain separate. Final figures are made and checked by the author with traditional tools; no new paper or generated figure was produced.
