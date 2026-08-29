# Broadband56 reconstructed V1 baseline approval request

## Current decision

The approved historical non-V2 broadband56 V1 contract was not found in the
bounded MARS searches, local attachments, the current Git worktree, any Git
ref, or Git object history. The available historical production evidence is a
5-60 GHz, 0.5 GHz, 111-point contract and cannot substitute for the required
5-60 GHz, 1 GHz, 56-point baseline.

The adjacent JSON is therefore a **new reconstructed baseline candidate**, not
a recovered historical artifact. It is intentionally marked
`PENDING_EXPLICIT_SHA256_APPROVAL`, `automatic_command_authorized=false`, and
`production_use_authorized=false`.

## Evidence boundary

- The V2 public override and sanitized private-config template are present and
  SHA-bound.
- Read-only MARS verification showed that the historical EMX wrapper, process
  file, Cadence `cds.lib`, and Cadence layer map still match their historical
  preflight hashes.
- No original V1 56-point contract, approved V1 SHA-256, or private V2
  production configuration was found.
- No MARS file was written and no Cadence, Calibre, EMX, runner, controller, or
  supervisor process was launched while preparing this request.

## Approval required

The candidate JSON may be used as the non-historical replacement baseline only
after the user or project leader approves its **exact SHA-256**. Approval of a
different SHA does not transfer. Until then, preparation, golden, 32, 1,000,
and Phase A/B/C remain forbidden.

The preparation command also enforces an independent approval receipt through
`--previous-contract-approval-receipt`. For a reconstructed baseline it
requires the exact schema, PASS decision, non-placeholder approver and
timezone-aware approval time, explicit instruction reference, matching
campaign ID and candidate SHA, and preparation-only scope. Both automatic
execution and golden/simulator authorization must remain false. The adjacent
receipt template intentionally has `overall_status=FAIL`; it cannot authorize
preparation and must never be edited in place.

An unambiguous approval response is:

> I approve the exact SHA-256 of
> `docs/research/BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_20260829.json`
> as the new reconstructed non-historical V1 baseline for V2 preparation. This
> approval permits preparation preflight only; golden and later execution
> remain subject to their own gates.

Supplying the original V1 contract file and its approved SHA-256 remains the
preferred alternative and supersedes this reconstruction path before use.

After, and only after, the explicit approval above is received, record it into
a new no-clobber directory with:

```bash
python scripts/record_broadband56_reconstructed_baseline_approval.py \
  --candidate-contract docs/research/BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_20260829.json \
  --candidate-sha256 1abb85dd7e6aad709eb404dbcc174a8a66e325baf76f26c0eeb64a6498ea7a12 \
  --approved-by '<explicit approver identity>' \
  --approved-utc '<timezone-aware approval timestamp>' \
  --approval-reference '<reference to the explicit approval instruction>' \
  --out-dir '<new no-clobber approval receipt directory>'
```

The recorder has no production or remote execution path. A PASS output still
authorizes only the subsequent preparation preflight.
