# Development128: native-return reader

Scope: DEVELOPMENT_CURRENT_SNAPSHOT, not FINAL. This extends the frozen
initial ledger; it does not regenerate targets, perform inference, retrain,
dispatch native jobs, change q_proxy, or replace failed candidates.

## Evidence and status

- Model: `f15-development6329-3x256-seed17-9d69d7ebfc66`.
- Source/train/validation/test: 6329/3801/1269/1259 geometries.
- Original frame: 128 requests, 1408 proxy slots, 128 frozen selections.
- Original analytic outcomes: 93 pass, 35 fail. These are not EMX counts.
- Native adapter and MARS metadata preflight: implemented by the sole owner.
- Prepared native release SHA: `2c24abe9d81e951cc77286499b8976a8c7dfee6d0b6e2a8b051becf8065911bc`.
- At the owner's 2026-09-09 22:59:47 UTC fixed capture, 35 original analytic
  failure terminals were published; 93 had no terminal at capture.
- Source export SHA: `f6b31c9dfca54bc3e997591fd9a33f9ec28b3545cfaf5396f9378a8d282f1341`.
- New128 real GDS/DRC/EMX results are absent from that capture. No physical
  error or completed success rate can be inferred from it.
- Automatic follow-up after the existing256 owner: NOT_INSTALLED. Prepared
  release files are not a submitted or running successor queue.
- Research-side actual read completed once at 2026-09-09 23:13:48 UTC: all35
  published failure identities matched, original128 retained, 93 pending.
  Seven output hashes passed. No model/solver/data-array access occurred.
- Actual result receipt SHA: `ef106a890bcdea8ce2f0548999471afe1bccc7c00aefc3d8a8e00d5c999e8d61`.
- Final consumer SHA: `d3aa73f431f6839882b39fc5677344028958afaa4a6131473461a6c2a44fa4c0`.
- Validation: 149 affected evidence tests; 57 consumer tests before a narrow
  DRC-binding fix; four new DRC tests after that fix (the57 were not repeated).
  Independent static review cleared the final consumer for this scoped read.
  Synthetic success-chain tests are not real native validation.

## Reader contract

`research.broadband56_nn.eucap15_development128_results` accepts only a pinned
publication, never a live output-directory scan. `prepare_snapshot` indexes
the existing owner export; it does not recalculate the candidate QA.
The reader reconciles all128 identities with the owner's fixed snapshot,
preserving exact original and resolved file paths and SHA/byte sizes.

Successful extraction requires the original selected geometry's complete
GDS/Calibre/solver/S4P/exact56-row chain. Strict validity and target hit are
separate. The unchanged statistical helper uses symmetric absolute tolerances
[0.125 nH, 0.125 nH, 1, 0.04000000000000001], not target-relative5%.

Original analytic failures retain null physical values. GDS/DRC execution
failures require matching terminal process and stage evidence. Unknown or
incomplete claimed terminals stop with a retained NO_GO receipt; they are not
silently changed into pending. Ambiguous EMX pipeline failures are not called
solver failures: the command includes both solve and extraction.

Missing published terminals mean pending **in this snapshot**, not current
process liveness. No solve count is inferred from wrapper exit codes or hashes.
Outputs are no-clobber JSON/CSV plus source closure and SHA256SUMS; no figures.

## Runtime boundary

Private metadata and physical mirrors are not distributed through GitHub.
Use the existing research Python environment and an exact private snapshot.
CLI: `python -B -m research.broadband56_nn.eucap15_development128_results --help`.
The required inputs are `--manifest`, `--qa-receipt`, `--snapshot`,
`--snapshot-sha256`, and a **new** `--out` directory.
The dated private work-package receipt records the one actual command and
artifact hashes. Do not rerun a completed read merely to reproduce a report.
