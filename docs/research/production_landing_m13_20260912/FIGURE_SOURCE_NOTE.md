# Actual response landing increment — 12 September 2026

These are additional figure-source data, not an author-produced figure.
Source physical observation:2026-09-12 08:31:50 UTC; window starts08:21:28 UTC.
The unchanged grid has8 bins per axis (512 cells), Lp/Ls0.5–2.0nH and|k|0.2–0.85.
The fixed reference is the previously published3801 TRAIN rows, with159 occupied cells.

Of24 new terminal records,20 contain real EMX responses and7 are strict/in-range.
The original split gives5 new formally admitted train geometries and2 test geometries.
Only the5 train geometries enter the comparison with the training reference.
They occupy5 distinct cells, all already containing at least5 reference train geometries.
Therefore no previously empty reference cell is observed in this five-row increment,
and no increment lands in a reference cell with fewer than5 examples.
No newtrain sample has|k|>0.8; max|k|=0.3635922675727834,maxQmin=16.94949878181.
All5 newtrain samples haveQmin in[10,20]. These are actual EM responses, not proxies.

This is a descriptive window-specific landing diagnostic, NOT cumulative production
coverage, a new training snapshot, a same-budget source comparison, or proof of
algorithm advantage. Original validation/test assignments and all failure records
remain untouched. Previous rounds and historical admission are not merged into this
fixed-reference calculation. The original acquisition decision baseline is not
rewritten to3801. Target/predicted cells are unavailable in this received cut and
remainnull; no target errors are invented for DOE or neighborhood geometry proposals.

Suggested author figure:unchanged 8x8 slices byKbin, source-coded actual TRAIN markers,
with the background frozen3801 count. Keep the data observation date andn=5 visible.
Do not display test/validation as contributions to training coverage. Empty cells
meanunobserved, not physically impossible. No plot was generated here.

Reproduction (repository root, existing research environment):
python -m research.broadband56_nn.eucap15_received_landing_increment --baseline docs/research/eucap15_abc_increment_20260910/COVERAGE_BEFORE.csv --received docs/research/qualification_batch_20260912/publication_v12/RECEIVED_NEW24.json

The command is read-only and printsJSON. The saved result was produced once at
2026-09-12T09:02:43.885831+00:00. SHA-pinned inputs and originalRESULT/formalrecord
references are inACTUAL_LANDINGS.json. Running it later changes onlygenerated_utc
and local source-path spelling; scientific rows and fixed-grid outputs are deterministic.
