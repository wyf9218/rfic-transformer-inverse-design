# Executed qualification publishers and next-block candidate

Scope: source review snapshot, not a public native release or a new scheduler.
The two publication modules below are byte-identical to the modules embedded in
the actual MARS execution wrappers. The next-block module was not installed.
No tests, source-table computation, qualification, solver or ledger write was rerun
to prepare this source snapshot.

## Source map

All source paths below are relative to the private workspace root, not this repository.

| Public file | Private source | Original = delivered SHA-256 | Evidence |
|---|---|---|---|
| history_publication.py | reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/history_publication_adapter_v1/history_publication.py | 2fabb94c0171183d41cbb0e441c72e9d629c709077c0d327521685a48ce9d2c5 | Executed; 14,035 bytes |
| controlled6_publication.py | reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/controlled6_publication_adapter_v1/controlled6_publication.py | b74bba0ac7aa656e0f198ce5a91a643ace1b986384496e82bd897e2a4cbec56a | Executed; 13,820 bytes |
| history_qualification_v2.py | reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/history_queue_delta_v2/history_qualification_v2.py | 6e6ba71e7f35e5d78dbb0a194c08489389343c2e0c86b9584f793947e5fddf76 | 13,075 bytes; NOT_INSTALLED |

No source redaction or path refactor was required for these three modules.
They contain schema/field names and evidence hashes, not credentials, raw sample
geometries, PDK content, private input dictionaries or private mount paths.
The geometry list comprehension in history_qualification_v2 constructs geometry
from caller-supplied fields; it is not an embedded sample.

## Actual execution binding, not just matching architecture or filenames

The private wrapper roots are under:
reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/

- controlled6_publish_20260912T035503198120Z/EXECUTED_SOURCE.py:
  SHA 4104b467c8d5326fdac3224da9b119e55862a2d39fa5408d8225791f1deaeee4.
  AST literal FILES[controlled6_publication.py] was strict-base64 decoded and
  compared byte-for-byte with the source copy, without executing the wrapper.
  READBACK_RECEIPT.json SHA b301f4f33495cee4539588bd8df2ef4abbdc8bea01d797e01bfacf07d20e827a
  records the original six appended: union 31 to 37.
- history304_publish_20260912T035535150737Z/EXECUTED_SOURCE.py:
  SHA 7edccca6f534caab9fb072f8f006959de875dd469df43152c272dcb025fb6543.
  The same AST/base64/byte-identity check was made for history_publication.py.
  READBACK_RECEIPT.json SHA 69f0c6cea4c4c8e405a7cc8031ccdc1da1553dcaa3ff00a4f7ae52d36cf8b1b2
  records 304 appended: union 37 to 341.
- These counts refer to the bounded certified union, not full-history certification.
  Wrappers and full receipts are deliberately excluded: they contain private bindings.

## Reused dependencies, not replacement implementations

- ../code/admission.py:
  75276646b91dc1783e53f79253cde018d557c484d3c4b1171b1a554f82ab0ab3.
- docs/research/qualification20_20260911/code/geometry_helpers.py:
  b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98.
- docs/research/qualification20_20260911/code/atomic_primitives.py:
  f397cd6390a146c41b2563f10f3430590fc632c2e23fc059468a80ebe36da4ca.

NumPy and the existing isolated research environment are required.
controlled6_publication additionally requires its exact four original native64
runtime modules listed in RUNTIME_PINS. The older public native64_result_bridge
controlled_result.py, native_birth.py and start_slots.py have different hashes;
they are not drop-in substitutes. The private runtime is not published here.
No public deployment, authentication setup, scheduler installation or standalone
ability to read private data is implied by this snapshot.

## Read-only qualified-union inspection

Only on an authorized host with the existing union and required private environment.
Set RESEARCH_PYTHON to that environment's Python executable and
EUCAP15_QUALIFIED_ROOT to the actual original qualified15_single_member_v1 directory.
From the repository root, the following is a read-only example, not a newly run command:

```sh
PYTHONPATH="$PWD/docs/research/qualification_batch_20260912/publication_v2:$PWD/docs/research/qualification_batch_20260912/code:$PWD/docs/research/qualification20_20260911/code" \
"$RESEARCH_PYTHON" -B -c 'import os; from pathlib import Path; import history_publication as h; rows=h.ledger(Path(os.environ["EUCAP15_QUALIFIED_ROOT"])); print(len(rows),rows[-1]["sha256"])'
```

Use the mixed history_publication.ledger reader after the historical append.
Do not use the old admission.ledger reader on historical schema records.
Do not rerun the completed six/304 batch wrappers to manufacture another increment.
Their publishers retain exact replay/current-union checks and original WRITE.lock.

## Next source partition: NOT_INSTALLED

history_qualification_v2 is a library candidate for the owner's existing next-block
loop. It is not a launcher, cursor, publisher, complete pipeline or automatic trigger.
The owner must supply the actual next source partition, existing identity index,
source/label/artifact bindings and pinned qualification_required_checks_v2 contract.
The required gate-key source contract is private and is not invented in this snapshot.

Integration calls, not a verified installed resume command:

```python
import history_qualification_v2 as q
q.validate_gate_contract(contract)
shared_check, shared_pins = q.make_shared_verifier()
row = q.classify_member(accepted_row, source_index_row, label15_row,
                        contract, current_known_identities, shared_check)
# Original owner persists per-member outcome/cursor, rechecks shared pins at closure,
# and performs separately supported publication only for qualified records.
```

SourceIdentityError remains a source-level NO-GO; do not convert it to a per-row PASS.
The frozen initial history_publication.py intentionally accepts only its closed
first-1000 audit/source identity; this candidate does not extend that release.
Next-block publication and actual persistent resume wiring therefore remain separate.
