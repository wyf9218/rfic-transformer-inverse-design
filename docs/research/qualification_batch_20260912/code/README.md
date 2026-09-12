# Actual qualification code snapshots

These files expose the already-executed holdout11 and history1000 logic for
technical review. They are not an installed native tool, executable release,
permission grant, second controller or instruction to rerun the completed work.

- `admission.py` is byte-identical to the original holdout11 qualification and
  existing-ledger append implementation. It preserves3validation and8test.
- `history1000.py` is byte-identical to the embedded11468-byte runtime source,
  verified by AST literal/base64 extraction from the saved transport wrapper
  without executing it. It classifies old accepted rows1..1000 in100-row blocks,
  reuses saved labels and checks actual artifact bindings. It does **not** append
  qualified records to the accepted ledger or prove the whole100K union complete.
- `run_remote_public.py` replaces the original hardcoded private root with the
  required `EUCAP15_NATIVE_OWNER_ROOT` environment value and explanatory docstring.
  It is explicitly non-byte-identical, was not the executed runner and cannot
  satisfy its original immutable release pins. Do not substitute it into that
  release or treat this reference copy as permission to modify an existing run.

Use `SOURCE_MAP.json` for exact public/source SHA identities. The wrapper SHA is
not the same as its embedded history1000.py SHA. The wrapper also embeds private
inputs and is deliberately not published.

Dependencies are reused, not copied, from
`docs/research/qualification20_20260911/code/geometry_helpers.py` and
`atomic_primitives.py`. Their exact SHA values are listed in SOURCE_MAP.
Original imports expect these modules in the runtime import path.

Private INPUTS, source tables, release files, expected prior-ledger pins, proof
bindings, PDK/configuration, source GDS/DRC/S4P and actual ledger access are not
provided in Git. Required paths must truly exist in the native owner's approved
environment. This snapshot is not turnkey or independently deployment-ready.

No tests, old qualification audit, solver, training or ledger operation was run
to produce this public source copy. Existing results remain separate evidence.
