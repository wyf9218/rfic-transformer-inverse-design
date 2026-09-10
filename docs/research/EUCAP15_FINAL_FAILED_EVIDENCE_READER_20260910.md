# FINAL failed-candidate evidence reader

Scope: additive research-side code, not a deployed native producer, a new
physical campaign, or a FINAL model-selection receipt. Historical pilot64,
development128, and completed-feature readers remain unchanged.

## Entry point

`research.broadband56_nn.eucap15_final_failures.inspect_failure(ctx, candidate_id,
result_pin, mirror, expected_release=..., expected_private_config=...)`

- Obtain `ctx` once with the existing `eucap15_final_context.load_context`.
- Supply an explicitly frozen, byte-identical export through the existing
  `eucap15_development128_results.Mirror`. No remote path fallback is used.
- The caller independently chooses the exact reviewed owner-release and
  private-configuration pins. They are not inferred from a result being checked.
- Feed the returned `publication` into the existing FINAL statistics API.
  Reuse its original frame and MAIN/AUDIT membership; do not count an overlapping
  candidate twice or substitute a different Q after failure.
- A raised `SelectedEvidenceError` is **evidence NO_GO**, not a physical failure.
  Retain the unresolved slot and original denominator; never remove that row.

The reader performs no simulation, training, inference, S4P extraction, writes,
network access, process discovery, dispatch or retry. It hashes physical source
bytes but never creates numerical feature values. No FINAL targets or results
are delivered by this interface.

## Original evidence and classifications

| Publication | Required positive evidence | What it does not establish |
|---|---|---|
| GDS_FAIL | Exact original Cadence/GDS-audit process with positive failure exit, or completed actual-GDS audit with consistent failed checks | A wrapper failure alone does not establish a Cadence execution or intrinsically infeasible geometry |
| DRC_FAIL | Exact Calibre process with positive failure exit, or completed wrapper/index/same-GDS summary with actual rejection | A wrapper failure alone is not a foundry violation count |
| SOLVER_FAIL | Full bound preflight/GDS/DRC chain, exact saved native command and stdout/stderr, recognized positive native-exit exception, matching closed owner log | Native attempt counts, root cause, or geometric infeasibility |
| FEATURE_FAIL | Full same-GDS chain, successful own-S4P solver receipt, recognized original56 extractor exception and matching closed owner log | Strict validity or physical accuracy; actual labels remain null |

For ordinary pipeline errors, the original PROCESS must match its saved INTENT,
caller-selected release, candidate input, output and completion paths. A claimed
execution failure with a completion artifact requires separate reconciliation.
Signal exits, resource/lease waits, expired budgets, partial artifacts, unknown
exceptions, integrity drift and contradictory published successes are NO_GO.
An extracted SRF-invalid response is `EMX_INVALID` in the completed reader, not
`FEATURE_FAIL` here.

## Additive FINAL producer/export contract — not installed

The candidate path is `<campaign>/<request_id>/<candidate_id>/RESULT.json`.
`FINAL_BINDING.json` must exactly equal the existing `candidate_binding` output.
The result uses `eucap15_final_candidate_failure.v1`, original owner status
`CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION`, original error, binding pin,
model/request/candidate/Q/membership/denominator fields and canonical geometry
identity. `q_requested` is this candidate's `q_target`; `q_proxy` is never changed.
Native `q_emx` remains null.

The separately selected `<campaign>/RELEASE.json` uses
`eucap15_final_native_release.v1`, with `private_config`, exact frame/model-freeze/
inference-completion `source_pins`, and `failure_sources` for the unchanged
simulation and extractor implementations. For EMX classification those exact
pins must also occur at their real module paths below `EMX_REQUEST.runtime.repo`,
in `runtime.source_pins` and in `PREFLIGHT.source_pins`. An unrelated correct
source-code copy is not sufficient. Calibre stage argv binds the release's
`calibre_wrapper` using the existing lease-inheriting loader, unchanged.

`GDS_REQUEST.json` uses `eucap15_final_gds_audit_request.v1`, the same FINAL
identity fields, unchanged single-candidate Cadence route and frozen source
pins. `cadence_candidates.csv` preserves the exact original ten geometry fields.
The GDS audit uses the already delivered FINAL completed-reader schema. The
Calibre request keeps its existing schema and adds `final_binding`; its original
input/index/summary/report/deck and normalized/raw GDS identities stay distinct.

Legacy `SOLVER_FAILURE.json` and `FEATURE_FAILURE.json` are not rewritten or
promoted on their own: their original format lacks candidate identity. The
surrounding candidate-Q release, request, process and source closure supplies
the required binding. Unknown legacy failures remain unresolved, not guessed.

The Mirror is a finite published export. Absence from it does not prove absence
on MARS; the caller/sole native owner's exporter must ensure a complete terminal
closure. The reader rejects positive contradictions but cannot certify a live
filesystem or replace exporter QA. `native_attempts` remains null, even where a
specific nonzero EMX exit is established.

## Delivery boundary

Tests use handwritten synthetic identities, logs and minimal GDS fixtures. Test
counts are software checks, never EMX solve counts or scientific evidence.
This package does not install a FINAL owner/exporter or modify the existing
development256-to128 server successor. New physical statistics require real,
freshly exported owner artifacts. The full EuCAP goal remains ACTIVE / NOT_FINAL.
