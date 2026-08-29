# Broadband56 reconstructed V1 baseline R2 approval request

## Why a new approval is required

The first reconstructed candidate remains immutable and its preparation-only
approval remains preserved. During the authorized MARS preparation preflight,
the historical general-preflight receipt and the current historical production
configuration independently produced the same SHA-256:

`c4ffcc8a424770e4b3c0c64d16c575271d29867d2b47dc3a29b7f642d2fd3655`

The first candidate recorded `c4ffcca8...` instead. Hexadecimal characters at
zero-based positions 6 and 7 were transposed. The preflight therefore failed
closed, no 56-point private configuration was created, and no simulator or
campaign process was launched.

## Corrected candidate identity

Path:

`docs/research/BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_R2_20260829.json`

Exact SHA-256:

`037889d41f41bbedbcd38188c44a5c8c1ccffa62943c334a72f0980f38bf864f`

R2 is still a new reconstruction, not the missing historical V1 contract. It
retains the exact 5-60 GHz inclusive, 1 GHz, 56-point grid and the complete
unchanged-physical-contract list. A machine comparison confirms that the
frequency grid and physical-contract list are byte-for-byte equivalent to the
first candidate; only the proven source-config identity and correction
provenance differ.

R2 remains `PENDING_EXPLICIT_SHA256_APPROVAL`,
`automatic_command_authorized=false`, and
`production_use_authorized=false`. The first candidate's approval does not
transfer to R2.

## Exact approval scope

An unambiguous approval response is:

> I, Yufeng Wang, project owner and project leader, explicitly approve
> `docs/research/BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_R2_20260829.json`
> with exact SHA-256
> `037889d41f41bbedbcd38188c44a5c8c1ccffa62943c334a72f0980f38bf864f`
> for `APPROVE_V2_PREPARATION_PREFLIGHT_ONLY`. This is a corrected new
> reconstruction, not the missing historical V1 contract. This approval permits
> only a new no-clobber approval receipt, private 56-point configuration
> preparation, and V2 preparation preflight. It does not authorize golden,
> Cadence, Calibre, EMX, queue, supervisor, Phase A/B/C, or campaign execution.

After, and only after, that exact approval is received, record it into a new
no-clobber directory with:

```bash
python scripts/record_broadband56_reconstructed_baseline_approval.py \
  --candidate-contract docs/research/BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_R2_20260829.json \
  --candidate-sha256 037889d41f41bbedbcd38188c44a5c8c1ccffa62943c334a72f0980f38bf864f \
  --approved-by '<explicit approver identity>' \
  --approved-utc '<timezone-aware approval timestamp>' \
  --approval-reference '<reference to the explicit approval instruction>' \
  --out-dir '<new no-clobber approval receipt directory>'
```

The recorder is record-only. A future PASS approval receipt would still
authorize preparation preflight only.
