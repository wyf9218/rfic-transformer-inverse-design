# Terminal-face lineage v2 — DEVELOPMENT_NOT_DEPLOYED

Scope: only the six newly frozen failures in `cadence_failure_delta_v1/CASES.json`; no old128/64, model training, historical QA or native reruns.

## Actual finding

| New case | Face | Nominal → canonical cross-center (µm) | Local construction |
|---|---|---|---|
| DOE027 | P002 | -35.6800 → -35.6725 | PASS |
| DOE043 | P002 | -14.5800 → -14.5725 | PASS |
| DOE080 | P002 | -15.0400 → -15.0325 | PASS |
| DOE082 | P004 | -43.7775 → -43.7850 | PASS |
| TRAIN012 | P004 | -31.1025 → -31.1100 | PASS |
| TRAIN020 | P004 | -12.3025 → -12.3100 | PASS |

The exact current-source canonicalizer accumulates rounded edge lengths and closes each polygon. The selected face center moves ±7.5 nm, while its nominal metadata previously did not. The six faces retain their intended width and axial coordinate; this is not evidence of a general environment/license failure or inadequate feedline extension.

The opt-in v2 binds the uniquely identified pre-grid face to the same post-grid edge. It changes only cross-center metadata, not the six cases' polygons, endpoints or labels. Original 5 nm grid, axial limit, 10 µm overlap, full intended width and original label/geometry checks remain. A new exact-width guard also covers the existing-match branch: one-grid width alteration cannot pass merely by falling within the original match window.

## Actual checked boundaries

- Exact old/current source six-case capture: `capture_run_v2/REPLAY.json`, SHA-256 `7088341440eb8afe73e84150448f3761aa710296917e8b7843b9d8b1a9ccf52b`.
- Six saved new captures plus three negative guards: `entry_check_v2/CHECK.json`, SHA-256 `4c805c37c26f485110d233bc639cf65ee38641578deec54b485334913b0e1824`. All pass. The initial width-guard failure is retained in `entry_check_v1/FAILURE.json`; the earlier capture mapping error is retained separately.
- The six-case check source pin is `5df35c288c531d1418d83eba4a087f89eb8673d510f3eca4956e363a05dbb3bd`. Afterwards only the binding-policy string was relabeled from the archived draft v1 to `lineage_cross_binding_20260912_v2`; no behavioral code changed for that relabel. The final module pin is `7d0e0a67aed3364cc4cbcdf2ea2bc6121863eb4184d48cc50f0ec8abd9536061`.
- Actual newly patched exporter, one representative new DOE027: `export_wiring_check_v1/RECEIPT.json`, SHA-256 `10ebf30ac51b471e50804c0756b62559e3cd15867181fa67f3dda5ead8fcfb6d`. Same-invocation before/after binding, resolved-center writeback, eight original label checks, CLI acceptance and evaluator constructor verified. Stopped before downstream bridge audit/GDS write. This single added integration check did not rerun the six-case/negative-guard suites.

## Deployable research source and switch

In the independent research worktree, these five files are actually wired (not merely a proposed patch):

1. `rfic_transformer_inverse_design/layout/port_endpoint_lineage.py` — `7d0e0a67aed3364cc4cbcdf2ea2bc6121863eb4184d48cc50f0ec8abd9536061`
2. `rfic_transformer_inverse_design/layout/export.py` — `7030d03fe08806ff1a8b5e3a34dddc8471c135659fe7d9c783613e1058f0e8e7`
3. `rfic_transformer_inverse_design/execution/evaluator.py` — `3bf7f1d11f74b85e007f5bf964bec8f19379d708ab5bc850b90c122b08ac30a9`
4. `scripts/run_candidate_queue_dataset.py` — `3c908101249cbdb7e7644352e696ab102f75c5269fce5fc8121c7dbb6e8b884c`
5. `scripts/run_candidate_queue_dataset_parallel.py` — `0b0341ecec11d6049e9a3de95899ed3cb576e30b4783b9a467de66bf837f3020`

Explicit switch: `--port-endpoint-policy shared_port_edges_lineage_20260912_v2`. API: `export_transformer_layout(..., port_endpoint_policy="shared_port_edges_lineage_20260912_v2")`. Evaluator passes the same policy; its existing nonlegacy cache key separates versions. Legacy default and v1 remain selectable and unchanged.

## Remaining boundary

GDS writes, Cadence, Calibre and EMX executions are all **0**. The six original native outcomes remain FAIL. The existing source mirror/active production and all frozen candidates are unchanged. Construction-derived overlap/centers are explicitly not actual-GDS measurements. Only the unique native owner may bind this new version at an authorized safe boundary; actual exported GDS, unchanged DRC/port gates and fresh EMX remain unvalidated. New-version physical labels cannot inherit old-version validation.
