# Fixed48 Frozen Checkpoint Recovery

## Scope And Live Boundary

The approved lease-fingerprint deployment (`cf67887b8caccac7ec36511e0ca7d37091e122d1ac2d0b24946db4edaf7d3a81`)
ran once as generation 30. The lease repair, five independent resource checks,
and dispatch at requested/admitted/executor concurrency 48 passed. The first
materializer then failed: `no prior materializer checkpoint found at 100 accepted`.
The physical supervisor exited before Cadence, Calibre, or EMX started.
The remaining RUNNING status JSON is stale, not live process evidence.

The formal checkpoint remains **861 unique accepted geometries / 48,216 rows**.
The frozen sampling checkpoint of 100 is a different counter, not lost progress.
Generation 29/30 failures, consumed launch records, leases, and source data must
remain unchanged. The existing campaign, queue, and logical supervisor are retained.

## Minimal Repair

`broadband56_checkpoint_handoff.migrate_boundary` now selects the completed
materializer from the exact committed attempt trace, validates its source
backend/authorization, and migrates its frozen checkpoint into the new resume view.
Output data bytes and existing metadata remain unchanged. New receipts explicitly
bind original source receipts and the target backend/authorization; only declared
locations and operational identities are rebound, with accepted increment zero.

`verified_resume_state` validates the dependency before control handoff. Historical
receipts remain readable; a new backend declaring this migration cannot omit it.
The materializer, strict fixed-generation consumer, Golden verification, scientific
contract, DOE, DRC, ports, frequency grid, solver settings, and NN authorization are
unchanged. NN training remains unauthorized. No Golden rerun is needed or permitted
by this repair.

## Verification

- Local focused set: **111 passed**, 7.73 s.
- Final private Python / NumPy 2.5.0 runtime: **374 passed**, 16.59 s, 13 affected modules.
- Real-file replay: original first-role failure reproduced; actual `prepare_controls`,
  serialization/readback, first fixed48 policy consumption, complete control identity
  validation, resume validation, and unchanged materializer `main` all passed.
- First materializer decision: `REUSE_EXACT_FROZEN_SHARD_START_CHECKPOINT`.
- The replay uses explicit test authority/process/resource/lock inputs. It is not
  owner approval, a production lease, live capacity PASS, or production resumption.
- Child processes and signals prohibited; no production launch record consumed;
  source file hashes checked before/after, accepted increment zero.

Evidence SHA-256:

- Original materializer: `67670d2a0456bc493931c1a4e48a878b97f9eb4fdb8b56d30f9e387a100699d9`
- Original frozen checkpoint: `de78452510a6bce86d1a39adc1cae053c1928c8ac8fc30262df5985f84ebe028`
- Generation-30 terminal failure: `06314374200fce99fc07efc2bcc13782850479c8e6dfd54167fce7ec2d48fa86`
- Canonical existing recovery-interface receipt: `09c0e0eb82de20f2102fcf16ac5886577df22cca2a8c253255707012e06cda42`
- Final runtime manifest: `db5fcc040c3ecf97e2be8e8ed7e4e9236679a8377635c38707f4d45f20e2a432`
- Final backend manifest: `ef3b1966b66c81c8c105e95dfa21924064c6c53bb22d7952457beb68aeb5a56c`
- Final packaged regression: `309991317e4b437fe066154bbc7d3fefdccb3809ec1767c7f8272c0be7ae754b`
- Final actual startup/first-role replay: `bf0edb74391005769eb03a0da9d4d4a75b2f792ef4f0f3260c0c077b0eb07449`

## Rejected Preparation Routes

Preparation v1 omitted the auditor output record's `exists` metadata; strict
identity comparison rejected it. Preparation v2 changed the materializer code
identity and was rejected by Golden reuse validation. Both failures are retained.
Final package v3 handles real output metadata and keeps the original materializer
identity exactly. Neither failed preparation was approved or deployed.

## Next Legal Step

Publish one exact-SHA candidate binding this tested package and the existing
generation-30 terminal evidence. Changed bytes require that candidate's approval;
the already-consumed parent approval is not approval of this repair. After approval,
the existing recovery path may create the next physical generation, recheck live
ownership/resources, and resume PILOT_1000 from the latest valid checkpoint without
Golden or duplicated accepted work. Actual native EMX concurrency and new accepted
throughput are NOT_MEASURED until production is genuinely running.
