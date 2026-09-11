# Native64 guard delta: review archive, not an installed native runtime

Byte-preserved copies of the sole native owner's new isolated component delta.
Original source versions, failed evidence and all frozen64 candidates are retained.
The receipt records11 new synthetic tests (5 slot sequence,6 CPU counter), run
once. Old25/33 tests were not repeated. The test file intentionally refers to
original private evidence paths for before/after regression; those files and
production data are not assumed to exist in a GitHub checkout.

Fixes: persisted arm/global slots must be non-bool integers and globally unique
and contiguous; CPU counters/deltas must be valid and total delta positive.
Invalid CPU samples mean WAIT with null iowait, not a false resource PASS.
Resource thresholds, candidates, ordering and scientific scope did not change.

Native launcher integration and owner RESULT producer remain NOT_IMPLEMENTED;
deployment is NOT_INSTALLED, actual native starts0. Reservations are not solver
starts. Do not execute this archive as an autonomous scheduler or replace the
original native owner's controlled runtime with it.

Source receipt SHA256:
`d774791df2c35243f1a1fa1efd67510b402f6433feb143e04d5e3fdd9719e7fc`.
Source file hashes and exact original paths are in DELTA_RECEIPT.json.
