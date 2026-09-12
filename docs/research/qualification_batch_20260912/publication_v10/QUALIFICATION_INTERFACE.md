# Historical111 read-only qualification readiness

This is a minimal reader around the existing pinned `read`, `identities`, `label_state` and `require_checks` helpers. It has no controller, extraction, GDS test, simulator, publisher or formal-write path. Do not pass its result to the existing56 publisher.

```python
ctx = qualification.load_context(
    contract_pin=current_yaml_pin,
    source_manifest_pin=input_manifest_pin,
    extraction_receipt_pin=actual111_receipt_pin,
    target_rows_pin=target15_rows_pin,
    per_source_pin=per_source_extraction_pin,
    all111_pin=saved_all111_csv_pin,
    readiness_pin=actual93_readiness_pin,
    geometry_helpers_path=existing_geometry_helpers_path,
    qualification_path=existing_history_qualification_v2_path,
)
row = qualification.assess_member(
    ctx, original_source_row_index,
    geometry=original_ten_geometry_values,
    geometry_fields=original_csv_geom_column_order_without_prefix,
    geometry_evidence_pin=original_first100_csv_pin,
    original_split=None,
    split_evidence_pin=None,
    actual_gds_receipt_pin=None,
    calibre_receipt_pin=None,
    compatibility_receipt_pin=None,
    known_identities=None,
)
```

All pins are `{path, sha256, bytes}` referring to exact existing bytes. `contract_pin` is the actual source-bound current YAML, not an invented JSON contract or historical campaign fingerprint. The original config's56-point declaration is not rewritten or claimed as a111-point response. The extraction receipt and per-member original111 grid carry the label-grid identity independently.

Small shared metadata is SHA-checked once and cached. The original100 CSV is parsed once per pin; each geometry uses the manifest's original `source_prefix_ordinal`, evaluation, S4P SHA, original geom-column order and production geometry fingerprint. No intermediate per-member JSON is needed. An earlier explicit JSON binding referencing the original CSV remains supported.

The17MB all111 output is **not read or hashed again**. Its already accepted output pin is matched against the original extraction receipt; only current nonsymlink path, size and stable lstat are checked. The result explicitly marks this as prior-full-SHA reuse, not current full-byte verification. No numerical labels or saved SRF are recomputed.

Unknown original split stays `None`; there is no train default or new split assignment. An explicitly supplied split must match its existing `bb_splits.v1` geometry mapping. Validation/test is preserved, and this route does not declare final independent-test eligibility. Q10..20 membership is a separate field, not an extra core-qualification condition.

## Current physical boundary

Missing complete actual-GDS receipt, actual zero-blocking Calibre/deck/GDS binding, or historical process/port/execution compatibility produces `missing_evidence`. Necessary grid/edge PASS is not the complete11-check GDS gate. Timestamp ordering and current file hashes do not by themselves become execution-time attestation.

The actual historical111 full-GDS/DRC/compatibility receipt schema has not yet been delivered by the native owner. Therefore this version deliberately cannot return a positive physical qualification even if arbitrary caller-written PASS files are supplied. It can inspect known GDS/Calibre common predicates but retains missing source-chain/deck/schema binding. Extend only against the actual emitted receipt; do not invent a successful receipt or reuse the old fresh56 identity. Current93 remain non-formal.

The existing `history_publication_adapter_v3/history_publication.py` has incompatible coupling: fixed old fingerprint and1..21135 accepted source identity; `original56`; and `referenced_frequency_rows == sequence * 56`. No record/ledger/publish code from that branch is called or modified. A future proper mixed111 publisher must preserve source namespaces and cumulative actual response-row counts without changing existing records.

Shared input/config/pin conflicts fail the source context. Member-specific absent/damaged evidence returns a member disposition so the next source member can continue. The known identity union is never mutated. No original source row is converted into a production accepted sequence.
