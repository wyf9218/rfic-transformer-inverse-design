# Historical 111-point numerical re-extraction

Source `extract_historical111.py` is a bounded compatibility entry for the existing current-definition extractor. The only extraction-core changes are recorded in `CORE_DELTA.diff`. Three new synthetic tests passed; this code task did not extract real historical S4P.

## CLI

```sh
python -B extract_historical111.py \
  --repo /absolute/existing/repository \
  --s4p /absolute/completed/historical.s4p \
  --s4p-sha256 EXACT_EXISTING_SOURCE_SHA256 \
  --out /absolute/new/no-clobber-output
```

The existing repository must match all six embedded formula-source identities. Historical inputs and output paths must not traverse symbolic links. Source bytes are checked before and after extraction. Existing output directories are rejected.

For an already verified batch reader, call `configure_repository(repo)` once, then `extract111_under_current_mapping(s4p)` and `target15_summary(result)`. The batch caller must preserve its own per-source identity checks, output identity, original frequency rows and failure evidence. This is a numerical API, not a qualification API.

## Outputs and exact semantics

- `historical_features_all111.csv`: all 111 actual points from 5 through 60 GHz in 0.5 GHz steps; full original response-derived rows and validity fields.
- `TARGET15.json`: original zero-based index 20, its four physical labels and unchanged strict predicates/reasons. JSON nonfinite values are null with explicit field names, not filled with zero.
- `RECEIPT.json` and `SHA256SUMS`: source/code/formula identities and output hashes.
- On an extraction failure, a new `FAILURE.json` preserves the failure; no retry overwrites its directory.

There is no frequency interpolation, resampling, invented 56-point response or fresh solve. The reused SRF helper estimates a zero crossing between adjacent *actual* reactance samples using its existing linear bracket estimate. This is explicitly disclosed and differs from interpolation/resampling of response spectra. The original source module's 56-point grid is not changed.

The current external-port convention and internal permutation are applied conditionally, not certified as historical-port equivalence. Standalone identity, process, actual-port, GDS/Calibre and formal-qualification fields remain `UNKNOWN` because this extractor does not establish them. Any independently established incompatibility or `FAIL` must remain `FAIL` in the caller's combined evidence; it cannot be replaced by this numerical result or its `UNKNOWN` fields. In particular, historical GDS failures must not be interpreted as repaired by successful S4P re-extraction.

Strict label validity is not formal physical qualification. No native execution, new EMX, geometry mutation, production append or admission decision is implemented here.
