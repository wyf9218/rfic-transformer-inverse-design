# Historical-data qualification: evidence-backed methods addendum

Scope: prose for the existing five-page discussion manuscript, not a regenerated manuscript, new experiment, or FINAL result. Numerical and artifact evidence are separated; the September 8 first-work-package notes are historical and do not establish the current experiment-completion status.

## Proposed manuscript text

Historical samples are admitted by a provenance-and-artifact predicate, not by numerical membership in the target box alone. Each member must retain an identifiable parameter geometry, its executed layout and port configuration, the applicable process and DRC evidence, the actual electromagnetic response and extraction definition, and its source and split identity. A recalculated label at 15 GHz is reported as historical-response re-extraction, not as fresh electromagnetic simulation. The original frequency grid is retained: the inspected 111-point response contains an exact 15 GHz sample and is not interpolated or renamed as a 56-point result. Changes to layout or port construction invalidate automatic inheritance of the old physical labels until the required binding is established.

This distinction changed the disposition of one inspected historical member. Its recalculated response satisfies strict 15 GHz validity and the specified inductance–coupling bounds, but the original layout does not satisfy the current metadata/grid and conditional port gates. Specifically, all 268 polygon vertex sets passed the geometric grid/direction check, whereas eight labels were off the required grid and two conditional port measurements differed by 5 nm. Structural identity under a separate tolerance did not override these failures. The historical run also lacked an executed Calibre result and a process-file identity bound to execution. Therefore, this sample contributed no certified member; it remains a separately versioned parameter-geometry revalidation candidate. This one-member observation is neither an estimate of the source-wide failure rate nor evidence that the neural network is physically inaccurate.

Coverage is updated only from real EM response locations of formally admitted training members. Pending records and validation/test labels are excluded from the acquisition-feedback population. Ledger growth, growth of the received training subset, and the number of occupied response-space cells are distinct measurements. Accumulating samples without occupying new cells does not demonstrate uniform support or a controlled advantage over DOE.

## Exact sources and limits

- M27 HISTORY_DISPOSITION.json: ea378a5199d3d5f8451c1ea08c0bd9444966eb5832045faad2bc30b434f8cb3a; member d87ff4d971f5f26f only.
- M27 HISTORY_GDS_RECEIPT.json: 9f6d1be78ce4fd90c343e0f4b341dc8fd0d6dea9a8bbe8c10e1cbe5e792d68e3. The port check uses the saved historical frame/nominals; it is not full foundry signoff. The via check was not evaluated after upstream failure.
- M27 HISTORY_EXTRACTION_RECEIPT.json: 6a60363d164a683ab82381c86703ccf3a8edc57695d55af0e86fe269e8107a0e. Numerical validity is conditional on the stated extraction/port mapping; it does not prove historical/current process compatibility.
- M27 POST_RECOVERY_TRAIN_RECEIPT.json: d2845e10976098cc40b9274f606e7fcf685c153c81a052b848e9114927aad711. The received-source training union reached3944, with163/512 occupied cells; this is not the entire production pool.
- No weights, target requests, original artifacts, labels, split assignments or scientific tolerances were changed. No new solver, training run, or paper image was produced for this addendum.
