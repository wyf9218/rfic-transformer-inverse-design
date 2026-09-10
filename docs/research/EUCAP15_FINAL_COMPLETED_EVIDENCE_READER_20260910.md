# FINAL completed-feature evidence reader

This is a research-side, read-only interface. It is **not** a deployed native
producer, scheduler, FINAL model declaration, final target generation, or a
report of completed physical experiments. Historical pilot64/development128
schemas and outputs remain unchanged. No final 10,000-request experiment was
run to implement or test these modules.

## Reused implementation and call order

1. `eucap15_final_context.load_context(frame_path, expected_frame=...,
   expected_completion=...)` reads one already completed, pinned FINAL frame.
   It verifies all original requests and committed inference shards, recomputes
   MAIN/AUDIT routing, and retains all eleven original proxy records separately
   from the candidate union. The production counts remain 10,000 requests,
   100 audit requests, and 1,100 audit slots. It performs no inference.
2. `eucap15_final_binding.candidate_binding(ctx, candidate_id)` produces the
   logical candidate binding for an original union member. It retains q_target,
   frozen q_proxy, memberships, exact source pins, record line/hash, geometry
   field order, and the existing default-nine-decimal geometry identity. The
   parameter-vector hash, canonical geometry identity, record hash, and actual
   GDS hash are separate namespaces. No physical authorization is granted.
3. `eucap15_final_evidence.inspect_features(ctx, candidate_id, manifest_pin,
   mirror, expected_private_config=...)` consumes a **completed extraction**
   closure. Use the existing `eucap15_development128_results.Mirror` with
   explicit original/resolved equal-byte pins. The expected private-config pin
   must come from the separately frozen owner release, not from a result being
   checked. Every native stage refers to the same `FINAL_BINDING.json`.
4. Pass the returned `publication` to the existing
   `eucap15_final_statistics.summarize_frame` alongside the complete original
   bundles/context and the frozen scale/tolerances. Missing publications remain
   **evidence-pending in that snapshot**, not inferred native execution states.
   Failed-stage publications must first pass their own native evidence checks.

The intended native path is `request_id/candidate_id/emx_selected/features`.
MAIN and AUDIT memberships share one candidate directory/result, never two
solves. An audit-only candidate uses its original q_target even when it differs
from q_proxy. Native q_emx remains null; the existing audit statistics can
compute an EM optimum only after all original eleven candidates are strict-valid.

## Completed extraction checks

- Exact FINAL binding, frozen model/frame/shard/record identities and original
  target/proxy/geometry; no aliasing of pilot schemas to FINAL.
- The actual `row.geometry_audit` artifact, required existing geometry checks,
  original GDS/port artifacts, raw and timestamp-normalized GDS identities.
- Unique candidate-bound Calibre index row, exact summary path/SHA,
  geometry-audit SHA, required zero-blocking checks, process/top-cell/deck,
  normalized GDS, and original report/deck byte pins.
- Same GDS before/after the fresh solver, exact saved command, own nonempty
  S4P and feature manifest, retained 56 rows from 5 to 60 GHz.
- Existing 15-GHz label/strict/SRF/physics and numerical formulas, reused by
  `eucap15_feature_values`: Q=min(Qp,Qs), symmetric four-target residuals,
  frozen spans/tolerances. Invalid original values are retained, not repaired.

These are receipt-chain and saved-label consistency checks. The reader does
not rerun GDS geometry audits, Calibre, EMX or S4P extraction; independently
trusted native producer/runtime and prior FINAL model-selection QA are still
required. A synthetic test's PASS is never physical-validation evidence.

## Not delivered by this interface

- Native FINAL producer/deployment and sole-owner dispatch binding.
- A completed failed-stage consumer for GDS/DRC/solver/extraction failures.
  Generic EMX-pipeline errors must not be guessed into a specific failure class.
- Full-campaign native snapshot export/orchestration and a release-quality
  final statistics package.
- Any FINAL data/model freeze, new final requests, native result, accuracy
  claim, or author-made paper figure.

Public source/test identities and the actual new-test outcomes are recorded in
`eucap15_final_native_reader_20260910/RUN_STATE.json`. Private native paths,
model/data files, GDS/S4P, production configuration and paper drafts are not
part of this code publication. The already installed development256→128 server
successor is a different scope and was not modified here.
