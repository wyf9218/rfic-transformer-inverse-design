# Historical existing-GDS adapter — no native launcher

Status: input-loader implementation tested; physical core not run, not deployed. The unique native owner owns any deployment and subsequent Calibre queue. No active source was changed.

```text
python -B historical_existing_gds.py --request ABS_REQUEST.json --validate-only
python -B historical_existing_gds.py --request ABS_REQUEST.json --out ABS_NEW_OUTPUT
```

Use the existing approved Python and PYTHONPATH containing the source-bound samebatch runtime plus its matching repository backend. Private inputs are explicitly pinned/transferred, not assumed present in Git. The module entry is `audit(request_path,out)`. `out` is no-clobber and outside input artifact directories/source runtime. Failures retain `HISTORICAL_GDS_FAILURE.json`; no retry into that directory.

Request fields:

- `schema`: `eucap15_historical_existing_gds_request.v1`.
- `source_row`: `{source_prefix_ordinal,source_row_index,evaluation,source_csv,identity_manifest}`. CSV is the bounded `new_training_first100.csv` pin; manifest is the existing `p215_new_training_source100_gds_current_bindings.v1` pin. No full-P215 re-read.
- `evaluation_root`: explicit **original** evaluation directory. Do not derive it from the merged evaluation value. Example: source row97567 has evaluation `000000__shard_000__04db4f3dd1a9a370`, but actual directory tail `04db4f3dd1a9a370`.
- `geometry_fields`: exact existing10D order; `original_geometry`: original ungridded ten finite values. CSV and summary geometry are checked; no newly decoded/repaired candidate is introduced.
- `artifacts`: exact keys `source,power,direct,gds,summary,port_manifest,emx_command`, each original `{path,sha256,bytes}`. Relative paths under explicit evaluation are the original layout/source audit, power audit, direct GDS, streamout GDS, original summary, layout manifest and EMX command. Summary may retain its historical S4P.
- `historical_s4p`: original `{path,sha256,bytes}`; `s4p_verification_receipt`: exact existing `p215_training_source100_private_s4p_transport.v1` receipt pin.
- `runtime`: original `configuration`, `foundry_contract`, `core_sources` pins accepted by the unchanged GDS core.
- Optional `path_map`: original path to exact local/MARS readable copy. Content pins remain original. S4P maps only to the original or the copy recorded in its prior transport receipt.

Original EMX command must remain a JSON argv array. The loader binds argv GDS, topcell, process, four port definitions, pin purpose and `-s` output to the original summary/manifest/source row. It does not launch the command or reinterpret 111 frequency points as56. S4P is never reread or rehashed here: the prior exact verification and current lstat are explicitly distinguished, including a copied file's current SHA not being reverified.

The same `_audit_candidate` code object and `_physical_checks` function are used via private globals injection of only the input loader. Foundry `_audit_actual_gds`, port measurements and structural identity remain in the original backend. `CALIBRE_INPUT.csv` keeps the existing field set and appears only after actual GDS audit PASS. This output is not automatic source qualification, formal accepted, fresh EMX or new geometry.

For source97567, native receipt confirms original `layout/foundry_layout_source_audit.json` missing. Supply no fabricated SHA or PASS; a first actual call must retain `MISSING_HISTORICAL_ARTIFACT: source`. `geometry_check.metrics.skipped=true` is preserved and does not independently reject the row.

Missing-source boundary in unchanged foundry core18345730:

- `_validate_source_audit` line854 requires the source audit schema, enabled/PASS, `PRE_CADENCE_LAYOUT_CONSTRUCTION`,0.005um and grid/frame/bridge schemas. A new current-GDS audit cannot be backdated as this evidence.
- `_audit_actual_gds` line449 reads ground-frame requested inner bbox/frame width; line528 reads primary/secondary bridge inputs. `_actual_bridge_record` line731 needs layer/datatype and bridge/bar probe x/y.
- `_physical_checks` line311 requires original grid-canonicalization PASS and `max_relative_area_change<=0.005`. A single current GDS does not prove a pre/post construction area change.

Future separately identified current-GDS verification may reconstruct only genuinely derivable frame/probe inputs and compare actual polygons. It cannot inherit or fabricate historical construction PASS, and remains outside this adapter's current implementation.
