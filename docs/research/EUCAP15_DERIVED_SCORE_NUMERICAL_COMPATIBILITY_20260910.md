# Derived-score numerical compatibility — 15 GHz selected evidence

The selected-evidence reader now accepts **one adjacent binary64 value only**
for the saved `normalized_response_score`, a derived square-root score.
All original labels, targets, residuals, scales, physical identity pins, Q
selection, validity predicates and hit decisions retain their exact checks.

The observed saved value was `0.15276803310865`
(`0x1.38de72509b6fep-3`); local recomputation was
`0.15276803310865003` (`0x1.38de72509b6ffp-3`).
The difference is `2.7755575615628914e-17`, exactly one adjacent float.
The producer and reader both use `sqrt(sum((error / scale)**2) / 4)`.
The cause of their differing final rounding is **not established**.
This is not a change to the scientific error definition or hit tolerances.

## Scope and safeguards

- Only finite, nonnegative plain floats are eligible; no bool, int or string coercion.
- Null must match null; zero must match positive zero exactly.
- Positive nonzero values may match exactly or be immediate `nextafter` neighbors.
- Two representable steps fail, including below powers of two where an
  `abs(delta) <= ulp(expected)` rule would wrongly accept two downward steps.
- A successful inspection includes saved/recomputed score and EXACT/ONE_ULP
  provenance in `derived_score_check`; the original receipt is never rewritten.
- Scores do not select a different Q, replace a failure or change a hit predicate.

## Verification and release boundary

One targeted run on Python 3.12.14: **56 passed in 0.55 seconds**.
Tests include complete synthetic development128 evidence chains, unchanged
source bytes, strict-invalid retention, adjacent and two-step boundaries,
wrong types, nonfinite values, and exact identity/label/flag rejection.
Existing full suites and native simulation were not rerun.

Command from the repository root in the existing research environment:

```sh
python -B -m pytest tests/test_eucap15_derived_score_ulp.py -q -p no:cacheprovider
```

The code/tests are `research/broadband56_nn/eucap15_selected_evidence.py` and
`tests/test_eucap15_derived_score_ulp.py`. The latter reuses the unchanged,
hash-pinned synthetic native-shaped fixture.

This software fix does **not** certify an EMX sample, pool admission or model
accuracy. The sole native owner must still complete the exact local evidence
closure; failed prior exports and all original physical artifacts stay intact.
