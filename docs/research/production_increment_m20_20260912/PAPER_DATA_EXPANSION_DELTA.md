# Incremental paper text: operational data expansion

Status: author-facing text insertion only, not a regenerated five-page document or FINAL result. The existing five-page discussion DOCX (SHA 4fecf1a3009e3925cc55219ea01812ca0371be7dd1f29c4d479033b8dd544242) remains unchanged. Suggested placement: end of the data qualification/coverage subsection, with dated counts retained until the final data freeze.

## Proposed English paragraph

By 12 September 2026, 11:15:44 UTC, the operational qualification ledger contained a certified lower bound of 6,589 distinct, strictly valid geometries within the specified 15 GHz inductance–coupling domain. This ledger is not the training population of the reported 6,329-geometry development study, and the two counts must not be added. In the first observed portion of a subsequent 256-candidate production batch, 55 terminal outcomes comprised 49 electromagnetic extractions and six analytical rejections before dispatch. Of the extracted responses, 34 met strict validity and 23 also satisfied the joint inductance–coupling bounds; 20 had completed formal admission and three remained pending. The 13 newly admitted training members occupied 11 cells of the unchanged 512-cell, 3,801-training-example reference grid. One landed in a previously underfilled cell, none in a previously empty cell, and none had coupling magnitude above 0.8. These interim observations demonstrate incremental data delivery, not uniform response-space support, superiority of a sampling strategy, or stable hourly throughput. The development-model weights and held-out results were not updated using this increment; final data/model freezing and independent 10,000-request physical validation remain incomplete.

## Sources and limits

- SNAPSHOT.json: exact new-release/native-owner observation and formal ledger head 6589.
- NEW55_LANDING_RECEIPT.json and NEW55_TRAIN_SOURCE.json: one completed incremental calculation, real labels, split, sources, pins and reference-grid cells.
- The three pending candidates are not called formally admitted. Six analytical rejections retain their denominator; undefined physical values are not imputed.
- The 8 GiB resource reservation is configuration, not experimental device memory cost. No throughput or physical-accuracy comparison with the five-configuration, three-seed development study is inferred.
- No scientific image, fresh solver, model update, old QA rerun, or five-page reformat was performed for this text.
