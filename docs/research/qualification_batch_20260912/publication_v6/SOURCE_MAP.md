# Incremental production-path sources

These are byte-identical copies of the changed local engineering sources, not new physical results. The operational observation cutoff is specified separately in the milestone snapshot; executor capacity is not measured native concurrency.

- `hotpath_candidate/fixed48_runtime.py`: removes repeated heavy release/storage reads from the shared resource-publication lock. It remains a candidate source; the native owner separately integrates its mandatory `ensure_plan` check.
- `family_split_v2/production256_publication.py`: preserves each neighborhood child's frozen canonical-hash split while verifying train-parent identity and retaining the non-independent-family restriction. It does not relabel validation/test as train. Physical qualification, source provenance, deduplication and atomic formal submission are unchanged.
- `family_split_v2/backfill_one.py`: narrowly scoped, idempotent metadata backfill for the existing TRAIN_NEIGHBORHOOD-002 receiver. No new simulation or extraction.
- `successor_v2/successor_entry.py`: binds the deployed registered-successor validator. It reads real owner/input/budget identities rather than accepting a new identity asserted by the caller.
- `family_consumer_v3/family_entry.py`: ordinary serial metadata receiver using the unchanged original library plus the explicitly pinned family callback. It writes a new release-scoped `native_candidate_publication_family_v3_<release SHA prefix>` state, never overwriting old HOLDs. Same-batch recovery source anchors are from the actual staged owner package.

- `family_consumer_v4/family_entry.py`: final ordinary entry additionally scopes the already-frozen7204 amendment anchor into the unchanged successor reader. One new narrow fixture checks restoration, old/new compatibility and rejection of unknown sources. The v3 source remains a pre-deployment checkpoint, not a second worker.

The private deployment libraries, dataset, release and actual artifact paths are supplied by the sole native owner; these copies alone cannot recreate or claim a physical run. Required earlier library sources are preserved under the preceding publication versions. Entry scripts provide `--help`.

Only changed-path tests were added: six hotpath tests, four family-rule fixtures, and three ordinary-consumer loader/profile/state fixtures. Existing physical QA, model trainings and prior experiments were not rerun. Source-anchor completion had one static CLI check and did not rerun the fixtures.

No native simulator was controlled by this publication step. Historical reuse, fresh strict/range qualification and actual formal append counts remain separately timestamped. The final independent10K validation still requires the final frozen model.
