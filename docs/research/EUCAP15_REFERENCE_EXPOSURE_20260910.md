# Exact single-reference gradient-exposure audit — 2026-09-10

Status: COMPLETE_SCOPED_METADATA_DERIVATION; DEVELOPMENT, NOT FINAL.

## Scientific increment

The frozen formal10K reference `f15-strict_lumped-f5f2ced054ef` has 3,175 eligible
gradient geometries in its original source snapshot. Its previously verified
forward/inverse best/last ledgers all contain those 3,175 geometries.
This audit joins the existing hash-indexed eligibility table to the already
audited 6,700-member pool and current 6,329 development view, without reopening
weights, NPZs or S4P, or running simulations. The membership CSV contains label
columns that are mechanically parsed as strings; their values are not used or
numerically evaluated in this metadata join.

| Reference-ledger relation | Current train | Current validation | Current test | Excluded from current view |
| --- | ---: | ---: | ---: | ---: |
| Seen in recorded reference gradient training | 1804 | 0 | 0 | 0 |
| Old non-gradient holdout membership | 0 | 595 | 619 | 0 |
| Outside this reference source snapshot | 1997 | 674 | 640 | 371 |

The overlap of this exact reference's recorded gradient rows with the current
validation/test hashes is **zero**. This does not establish family independence
or independence from other historical models, inherited weights, prior validation
selection, or development feedback. It does not authorize using old weights in
a new main comparison. All historical exposure and FINAL eligibility remain
unverified; existing source ledgers are not modified.

## Evidence and execution

Proof is not based on architecture or aggregate counts alone:
the pinned trainer stores `sorted(set)` seen indices; the prior actual checkpoint
audit checked seen is a subset of the exact eligible training index set; all four
counts equal the eligible count. Equality of sets then allows a per-hash join.
The new row-level overlay and exact source-path/SHA bindings remain private.

One real metadata invocation completed; zero checkpoint loads, zero new updates,
zero EMX, and zero test-label values used. Eight new pure-join synthetic tests passed
once. These are software checks, not eight physical experiments. An initial
static-review finding on short-circuit boolean validation was corrected before
the real run. A reporting-only correction distinguishes mechanically parsed CSV
label fields from zero label values used. Original output is preserved; the
correction does not rerun or change the join. No old regression suite was rerun.

- Effective summary SHA-256: `ad9d5a2a982a90974059d3483fb7a8f0473900c4dbd10f4e79aaf4d69654a40a`
- Preserved original summary SHA-256: `66f6c367342d8be7f9e540bcfe905afa8496fe20f1490ed6c2988ccf908d63e5`
- Result manifest SHA-256: `1830f857e77a2daf5e98ce9eb29d76c13d111838e63938f82c8677997bd3289b`
- Result SHA256SUMS SHA-256: `e205e33335fd62c91c4de3b7355c7931713d0dc673428216a275e24c9b1b35cd`
- Exact input configuration SHA-256: `06983fc3690e484ecca0122579ad1ee71e311d34a1fb1c67fab2e4fb542dd735`

This CLI requires the private, hash-pinned audit inputs; they are not supplied
by a public clone. The actual executed command is saved in private
`reference_exposure_20260910_v1/EXECUTION_v1.json`. Invocation form:

```sh
python -B -m research.broadband56_nn.eucap15_reference_exposure \
  --input /private/path/INPUTS_v2.json \
  --input-sha256 06983fc3690e484ecca0122579ad1ee71e311d34a1fb1c67fab2e4fb542dd735 \
  --out /new/private/no-clobber-directory
```

Do not rerun the completed audit for status checks. Read its existing receipt.
The sole native owner and installed original256→development128 successor are
unchanged. No native live-state refresh or new physics is implied by this audit.

## Bounded historical-source difference received

The sole data owner additionally closed the known `first15 v1` omission:
nine previously strict-valid results have actual |k| in
[0.13170441257930257, 0.16875369683896452], all below the new-domain 0.2 minimum.
Their exact geometry hashes and 12-decimal geometry vectors do not overlap the
known6,700 pool, but their **new-domain additions are zero**. Two original
analytic failures remain recorded. These are old results newly classified for
domain eligibility, not new solves or a new physical accuracy test.

Owner receipt SHA-256:
`c05bcc15c26b7573550eda0ff89dc4f343df07158a597f82bd84feda64e8d86d`.
The five-file owner checksum index was verified by research; generator source
and nine-row difference were read. Compatibility is limited to compared frozen
process/config/port/geometry/extractor pins, not a fresh raw-GDS/S4P signoff.
July pools still lack current-contract or exact readable member-index evidence;
their counts remain unknown, not zero or additive. A geometry-order field copied
from the current contract is not treated as reverified old-table schema.
