# Publication v3: source-only reproducibility

This folder provides byte-identical source for the actual historical307 append, new256 preparation/first append, read-only result receiver and eight synthetic qualification tests. See SOURCE_MAP.md for exact identities and executed scope. No tests were rerun for this copy.

The owner actually appended one new256 train member as649 and read it back; replay added0. That event does not deploy an automatic callback for future results or create a second simulator owner.

## Private bindings are required

Configure an existing supported research Python environment with NumPy, the recorded admission/geometry/atomic helpers, and this source folder. The receiver additionally requires its exact pinned native-runtime modules. Private INPUTS, path maps, closed source artifacts and the live qualified ledger are not present in this repository.

Generic preparation CLI (path variables must be assigned by the authorized operator; this is not a command already executed from the public checkout):

```sh
PYTHONPATH="$PUBLIC_CODE_DIR:$ADMISSION_DIR:$ATOMIC_GEOMETRY_DIR" \
"$RESEARCH_PYTHON" -B "$PUBLIC_CODE_DIR/prepare_callback.py" \
--inputs "$PRIVATE_INPUTS" --inputs-sha256 "$INPUTS_SHA256" \
--path-map "$PRIVATE_PATH_MAP" --out "$NEW_NO_CLOBBER_OUTPUT"
```

Preparation never commits. Only the existing native owner calls production256_publication.publish with the exact prepared evidence, current byte reader and original ledger WRITE.lock. It dynamically reads the mixed-schema head, checks all three geometry identity namespaces and performs one exclusive append/readback or zero-count replay. Do not call an old-schema-only ledger reader after historical rows are present.

Generic read-only receive CLI:

```sh
PYTHONPATH="$ADMISSION_DIR:$ATOMIC_GEOMETRY_DIR" \
"$RESEARCH_PYTHON" -B "$PUBLIC_CODE_DIR/consume_spec_v2.py" \
--spec "$PRIVATE_SPEC" --spec-sha256 "$SPEC_SHA256" \
--out "$NEW_RECEIVER_OUTPUT" --cache "$EXISTING_SUCCESS_CACHE"
```

The source spec must point at the exact supported private runtime and explicit transported bindings. A new delta selects unseen results; successful immutable cache receipts are reused without consuming closed source artifacts again. Do not reuse an existing output path.

These modules contain no SSH/native launch or simulation control entry. The historical next partition2001+ remains NOT_INSTALLED; existing1001–2000 receipts must not be relabeled for it. Synthetic fixture outputs are not physical evidence.
