# M36 historical qualification source-contract finding

Observed UTC: 2026-09-12T15:35:23Z. Read-only source inspection; no new extraction, member audit, tests, native actions or formal writes.
Semantic-layer evidence distinctions applied; current 15 GHz contract supersedes obsolete metric guidance in that historical layer.
Scope: identify the existing historical111 adapter gap, not introduce new physical requirements or certify any member.

## Exact entries and existing provenance

1. `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/historical111_qualification_v1/qualification.py`
   Source SHA256: `4a88a302973030606962cb3c97c8568253ba2d19f21537a067f0418ce3ceaa26`.
   Existing `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/historical111_qualification_v1/TEST_RECEIPT.json`, SHA256 `0bf56f49e81c8f7773867d020ff81d928aa66771f819c26da536f7c2fece7e49`: software checks only; explicitly no positive physical qualification path enabled.
   Existing `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/historical111_qualification_v1/actual93_v1/RECEIPT.json`, SHA256 `3cab54310c9e4a97151573b2060b5559dcd2edd63b12a037c324a8e0862b78a9`: exact same source pin; old 93 binding consumption, not re-executed here.
2. `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/history_queue_delta_v2/history_qualification_v2.py`
   Source SHA256: `6e6ba71e7f35e5d78dbb0a194c08489389343c2e0c86b9584f793947e5fddf76`.
   Existing `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/history_queue_delta_v2/test_run_v1/TEST_RECEIPT.json`, SHA256 `3c4f98cea680772b81fa48495619c730db6afaebb5cc1aeecf35949126cd8e47`: six software fixture tests; not a historical111 admission receipt.
3. `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/history_publication_adapter_v3/history_publication.py`
   Source SHA256: `885f794ada737faad7deed9c4cef3bc56ac92d7e7a631fd98c5381eb3b5a2811`.
   Existing `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/history_publication_adapter_v3/TEST_RECEIPT.json`, SHA256 `e0d4ee052d954070822e0eb75aa55700687195db2c9c07043d4c5bea127fc60e`: new cursor fixtures; this inspection does not establish current native deployment identity.

## Actual implementation boundary

- Historical111 `qualification.py:269–290` unconditionally appends adapter/schema missing-evidence even when DRC/compatibility receipts are supplied, then returns `missing_evidence`. This is an unimplemented positive adapter path, NOT proof of physical failure and NOT a request for another human approval.
- Historical56 `history_qualification_v2.py:145–186` requires the original candidate-bound execution receipt, exact config, GDS/DRC/source pins and command; line 158 requires exactly 56 points. Its `.proc` pins use the source-cache verifier at lines 52–67 and 180–184. Shared source evidence can be read once and reused with identical pins; sample-to-artifact binding remains per member.
- Publisher v3 line 287 fixes the 56-point grid, line 339 writes `original56`, and lines 363/416 count 56 referenced rows per member. Its cursor/source sequence binding is also source-specific. It is not a drop-in historical111 publisher: preserve all 111 original points and historical provenance; do not relabel, interpolate or fabricate old accepted sequences.
- Publisher v3 lines 302–310/338/421 explicitly preserve `split=None`, `UNKNOWN_NOT_ASSIGNED`, and no model/training membership update. UNKNOWN excludes automatic train consumption but is not, by itself, a new physical-ledger rejection. Keep the strict112 benchmark arm/pair reservation independently.
- Legacy holdout admission lines 86 and 221 are restricted to an existing validation/test partition; that scope-specific gate must not become a global UNKNOWN-split ban.

## Source-level process, port and archive evidence

- Profile `/Users/wyf/Documents/模拟变压器AI反向建模/rfic-transformer-inverse-design/configs/current_foundry_small_source_strict112_ffdf.production.v1.json`, SHA256 `e1148fbe7021e7e27df671f9ce6c57c919b72e4a4b20aa22b35a8fd547094099`, pins source/authority/lineage artifacts but has no historical-execution `.proc` byte-SHA field.
- Helper `/Users/wyf/Documents/模拟变压器AI反向建模/rfic-transformer-inverse-design/rfic_transformer_inverse_design/analysis/current_foundry_real_emx_evidence.py`, SHA256 `79ceae57d7516dca5ac9cc1a42d0ae7762cbaf48020537cbe2fcb864309046a1`: lines 149–177 distinguish S4P-header evidence from cryptographic execution attestation and check a process-path token plus port arguments, not `.proc` bytes.
- Materializer `/Users/wyf/Documents/模拟变压器AI反向建模/rfic-transformer-inverse-design/scripts/materialize_current_foundry_small_source_ffdf.py`, SHA256 `0319cbdc0ff4744505795093ae432da981b72c0f2e6204be4f888e982005c077`: lines 1084–1103/1175–1301 bind declared source lineage/DRC evidence; lines 678–749 check recorded ports. Old expected111/old ranges are not current numerical qualification.
- Therefore absent historical `.proc` SHA alone is not a hard rejection established by that old profile. Actual current-compatible process/port provenance is still required; a matching path or today's file SHA does not prove the historical executed bytes or ports. Current authorization is not physical evidence.
- Received source DRC summary `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1/strict112_four_20260912T142344723294Z/drc_summary.json`, SHA256 `0b92ffda4e8da617e3df6d9c042954957dcfa95b166a1a3946abdf1e7596b187`, records an immutable DRC archive/deck chain. It can support shared DRC-source verification; it is not EMX process-byte attestation. No new PDK read is requested here.

## Geometry identity crosswalk and current member state

- Helper `/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15_native_owner_20260909T062500Z/qualified15_holdout_partition_v3/geometry_helpers.py`, SHA256 `b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98`: exact 10-field order at line 4; canonical 9-decimal ordered field/value pairs at lines 14–30; raw `.17g` pipe text at 53–56; production 1e-6 ROUND_HALF_UP/schema at 58–76.
- Recompute each namespace only from the same actually bound, finite 10-field geometry. Preserve original geometry hash as its own source alias; do not infer its serialization, require equality across namespaces, silently round the actual geometry or reverse a hash. Nominal 5 nm alias is a conservative duplicate check, not proof of the actual GDS grid.
- Existing M35 five actual member bindings remain unchanged: ordinals 1/2/11 passed actual GDS structural/grid checks, 13/15 failed actual pin-grid checks. This task does not recheck those five or their numerical labels. The three survivors still need the actual bound endpoint/current-config/geometry evidence being received by the parent, plus current-compatible source/port and cross-source unique admission binding.
- Legal next step: implement only the observed historical111 compatibility/DRC receipt adapter and suitable historical ledger record path after actual evidence is available, reusing existing identity, duplicate and writer controls; preserve UNKNOWN split and original benchmark context. Do not turn the hardcoded placeholder into PASS, weaken physical gates or apply the 56-point publisher unchanged.

Execution provenance: bounded source reads and receipt metadata inspection only; `rg -n` verified affected lines, `shasum -a 256` pinned four small existing receipts. No whole-directory hashing, old QA, source-file modification or formal ledger update.
