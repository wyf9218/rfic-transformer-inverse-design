# Delegated Waiting-Stage Recovery

## Authority and Scope

The project owner's current standing authorization supersedes project-local
per-release personal SHA approval for scoped software repairs and controlled
recovery. A delegated receipt explicitly identifies Codex as release operator;
it must not claim that the owner personally approved a newly created SHA.
Existing personal approvals and failure evidence remain immutable.

The scientific contract, same campaign/queue/logical supervisor, fixed request
48, CPU thresholds 1.10/1.10, memory floor 20%, five independent fresh checks,
all remaining hard gates, and no-NN restriction remain unchanged.

## Implementation

- `broadband56_delegated_release.validate_release`: verify scope, source grant,
  actual package identities, and final-package test/preflight evidence.
- `broadband56_waiting_fence.interrupt`: pin the existing Python control chain
  with PIDFDs, fence the leaf and ancestors, prove quiescence, then terminate
  only the verified blocked controls and reacquire the original lock.
  Healthy native solvers are not signalled. A pre-termination failure resumes
  frozen controls. Failed and interrupted states are not stage completion.
- `broadband56_waiting_recovery`: preserve source receipts and raw artifacts,
  validate the exact zero-dispatch cohort, and create explicitly derived reuse
  receipts. Only byte-identical queue delegate path relocation is permitted.
- Existing checkpoint startup and production backend consume these bindings.
  Completed Golden evidence is reused; Golden is not rerun. Later normal
  batches retain the original sampler and full physical acceptance chain.

## Verified Before Deployment

Private final package `package_v5`, under the existing private Python with
NumPy 2.5.0:

- Affected regression: **481 passed**, 33.29 seconds, 17 named modules.
  Ordinary backend integration tests execute only their exact synthetic
  `mock_role.py` fixture, not Cadence, Calibre, or EMX.
  Test receipt SHA256:
  `c770afff689770a575d1301eb8e5067150c11c2f693368a57b2fdaa5bee8fac9`.
- Complete no-simulator recovery replay: **PASS**; actual source files with
  explicitly synthetic process-death/resource inputs. Lease serialization,
  first fixed48 policy consumption, ordered controller handoff validation,
  checkpoint resume, seven-role reuse, 119-candidate EMX input bindings and
  EMX argument parsing passed. Original evidence unchanged; no production
  lock/lease, startup intent, signal, or simulator action.
  Receipt SHA256:
  `ba8429c6a2dbe4b5dd65df1861d310f5b83139b7da4f1627036bc85300b484d1`.
- Formal checkpoint: 861 accepted geometries / 48,216 frequency rows.
  Pending prefix: 119 valid GDS/zero-blocking Calibre; 20 Cadence failures.
  The prefix is **not** newly accepted data.
- Runtime manifest SHA256:
  `0193f48806a9a7cc126a99f9a9838b0607c507280424afa816d99dfad2818b86`.
- Backend manifest SHA256:
  `06c9a80db6b45fc51ecd1d8e9af3a9ec5c49b30df6da66acee88bb28d67bc6e8`.

Earlier preparation and harness failures are retained, not re-labelled PASS.
These results prove software recovery preparation, not live resource admission,
deployment, fresh EMX execution, additional acceptance, or 48 native solvers.
Deployment and production require separate live receipts and process evidence.
Private manifests, PDK, process files, GDS, and credentials are not published.

## Live Deployment and First Acceptance

Verified on 2026-09-07 UTC, separately from the software tests above:

- Patch commit `970aec3c43aacd340c8d8f661d2f470ded54e05e` was deployed under
  the standing grant, with a truthful delegated release receipt. Generation
  32 retained the same campaign, queue, logical supervisor and original lock.
  The four fenced blocked Python controls exited; no healthy native solver
  was stopped. Old receipts and physical artifacts remained intact.
- Five distinct fresh resource checks passed. Requested/admitted/executor
  capacity was 48/48/48. At 04:03:14 UTC, process-identity verification
  observed **48 native EMX solvers**, not just futures or Python workers.
  Later tail concurrency decreased as the finite 119-candidate cohort drained.
  Live observation SHA256:
  `2d9c6e2d5f9ff7fe8c7245e6c340cf592d569689102036c4c77af826ca496a05`.
- Seven completed prefix roles were explicitly reused, including 119 exact
  GDS/zero-blocking Calibre artifacts. Reuse itself added zero accepted rows.
  Reuse receipt SHA256:
  `52617e750bbf31ee1ad97aa02e19824cc84e53ad287a392be8f2aa6e835f9563`.
- Fresh EMX completed 119/119 with zero EMX failures. Full-band S4P QA passed
  119/119, producing 6,664 new frequency rows. EMX and QA receipt SHAs:
  `4c67468e90a2031da52f4c0d22fb9e8385f13b126d0398c77d3099638adb40ea`
  and `e71665a91357aa0e58ec34685d512561ef2c8993c3a590d8725f7e6fcafe809c`.
- At 04:08:24 UTC the formal progress receipt committed **861 -> 980**
  accepted unique geometries, totaling **54,880** frequency rows. The 20
  original Cadence failures remain failures. `PILOT_1000` is INCOMPLETE,
  decision `CONTINUE_SAMPLING`, remaining 20 accepted, not 20 guaranteed
  successful candidates. Progress receipt SHA256:
  `b9f1ac80b7a48c9a28c00ae19ac86f050e0296e9e31cd0547affe59ae4892f24`.

This is actual restored production, not completion of the 200K target.
No NN training, concurrency benchmark, or scientific-contract change occurred.
Stable end-to-end production throughput and completion ETA remain
`NOT_MEASURED`; this recovery cohort reused previously completed upstream work.

## Latest: Pilot Complete, Storage Blocked

At 04:19:17 UTC the same supervisor completed `PILOT_1000` with **1,000
accepted unique geometries / 56,000 frequency rows**, a net **139** new
accepted geometries since recovery. Stage receipt SHA256:
`b77ec0e462c23a28b3dc249710d3f5edc4dfe23fc90cc96a8d241ea74e060aa7`.
Checkpoint receipt SHA256:
`3f41102607d2df3b9e6e85d307bdce49d1df78162bf95d4e8f3febbeed72e2f8`.
Coverage remains partial at pilot scale. Complete frequency extraction does
not mean every row is strict-lumped-valid: the receipt reports 30,925
broadband-descriptor-valid rows and 10,796 strict-lumped-valid rows.

The controller then exited before Phase A: the existing consumers expected
`PILOT_1000_RESOURCE_SUMMARY.json`, but no producer had created it. The actual
error was `measured_pilot_bytes_per_geometry must be numeric`. The last root
status JSON still says queued; it is stale and must not be interpreted as a
live supervisor. A fresh process check at 04:36:38 UTC found **zero** project
supervisors/runners/native solvers.

`scripts/materialize_broadband56_pilot_storage.py` now produces that existing
input from the hash-bound terminal pilot, unique accepted denominator, full
attempt ledger, and actual retained physical artifact directories. It counts
failed attempts and intermediate files, counts unique inodes once, uses the
larger of logical/allocated bytes, and counts library links without following
them into external PDK directories. Two consecutive inventory passes must
match. Physical source paths themselves must remain non-symlink and hash-bound.
The first real replay rejected environment directory links; that failure is
preserved. The corrected final producer and affected regressions passed **44
tests** under the private Python. Commit:
`d9c9cbad015f0ef8910e2ffe56013cb705455e68`.

The resulting sidecar was published no-clobber into the current campaign root;
all three existing controller/launcher/backend readers consumed the exact
value successfully. No simulator-runtime/backend or scientific bytes changed.
Measurement SHA256:
`757a0da2e98350083f3a667d80808e87f9ef39271923642fa67f39f101b7f913`.
Publication/readback receipt SHA256:
`79b6f51150f817c2b2eb85ec0470490ee2646dfe524cb3ac29b95d2ec649e04c`.

The unchanged storage gate now has genuine numeric evidence:

- Retained pilot charge: 6,028,031,398 bytes / 1,000 accepted geometries.
- Required remaining capacity: **1,499,472,810,253 bytes**, using the existing
  `ceil(bytes_per_geometry * 199000 * 1.25)` formula.
- Available data-volume capacity at 04:36:38: **732,776,697,856 bytes**.
- Deficit: **766,696,112,397 bytes**. Storage gate: **FAIL**.

No replacement supervisor was launched into this failed hard gate. Increasing
usable storage is an external requirement; no historical data were deleted,
no gate was weakened, and no system/application volume was repurposed. Future
controlled recovery must bind this measured sidecar to the new recovery root
before first resource consumption, retain the terminal 1,000 checkpoint, and
perform its full no-simulator recovery preflight. That new recovery/preflight
has **not** been performed; old startup intents must not be replayed. Standing
delegated authorization still applies, with no new owner-per-SHA approval.

## Post-Pilot Recovery Prepared, Not Launched

The preparation gap above is now addressed in a new isolated package. No
generation-32 runtime, receipt, lease, checkpoint, or physical artifact was
modified. Production remains stopped at 1,000 accepted / 56,000 rows.

- `broadband56_checkpoint_startup.validated_pilot_storage` binds the source
  summary to the exact completed pilot, including previous receipt migrations,
  its ledger/backend/authorization/producer identities, denominator, measured
  totals, and unchanged storage formula. Missing, changed, or conflicting
  inputs fail before new control directories are created.
- `restore_pilot_storage` copies the exact original bytes after the strict
  empty-envelope checkpoint migration and before any policy read. The new
  handoff records both source and restored identities. An old measured storage
  FAIL is not transformed into a live resource PASS.
- Real-file preflight also exposed a historical-handoff defect: the previous
  validator treated subsequent accepted receipts as unbound files in the old
  migration view. Historical links now permit only append-only extensions
  proven by the full committed-boundary validator. The original preserved
  count stays 861, while later receipts prove 1,000. The current startup link
  remains strict. Missing/changed originals, invalid QA/counts, and pending
  extensions remain rejected; previous failed-start evidence is preserved.
- The preparation descriptor was aligned to the existing backend's unchanged
  stage-launcher path. This correction did not change launcher bytes, the
  private configuration, stage profile, or any physical role implementation.

Final frozen runtime SHA256:
`6236517881bdee5b7eebdd0758ff635db3cd6310d07e66f6c5a9f4aed033f64a`.
Final backend SHA256:
`a188fa3ce67bb725d0d88499433dfa801fc6aca248865eebc8d557e669675d4d`.
Focused final-package regressions under the approved private Python / NumPy
2.5.0: **270 passed**, 16.69 seconds, 10 named modules; process/signal attempts
zero. Test receipt SHA256:
`d7e6a0d9290080a6c95783145fb8ee401962d7d820e5a44080590cdc8f38b795`.

Final no-simulator preflight SHA256:
`36c9475c3555c6f19b8d170a864c9dd65228da3a24512b2105f87d4a5098fc62`.
It exercises actual source files and package imports, full `prepare_controls`,
serialized storage/lease, first fixed48 and storage-policy consumption,
ordered handoff validation, resume identity, controller argv, and Phase-A
backend argument construction/parsing. All three actual storage readers return
6,028,031.398 bytes per geometry. Explicit test process/lock/gate inputs are
isolated: no production lease/lock, consumed intent, child process, signal,
Cadence, Calibre, EMX, queue generation, or controller main invocation.
The real old resource values evaluated with test bindings retain storage FAIL;
this is not a fresh capacity approval or a production deployment.

Earlier failed test/preflight outputs remain retained, including the dedicated
legacy exception-type assertion, executable-metadata pin adapter, historical
append-only validation, and stage-launcher path mismatch. They are not relabeled
as successful executions. Actual restart still requires a fresh storage and
remaining hard-gate check, the same queue/logical owner, and a new unconsumed
controlled startup record under the standing authorization.

## Final Independent Resource-Auditor Integration

The package above was not deployed. The expanded preflight identified two
remaining independent-process boundaries before any new supervisor launch:

- The controller omitted the measured pilot bytes from the resource auditor's
  CLI. `_write_resource_gate` now forwards the persisted value when present.
- The auditor still called the legacy concurrency policy, which requires a
  benchmark-derived pilot limit for Phase A. A hash-bound fixed48 overlay now
  uses the existing `broadband56_scheduling.concurrency_for_snapshot`, just as
  the controller and executor do. Legacy non-fixed behavior is unchanged.
  No benchmark result or health counter is invented. Full concurrency evidence
  is included in the resource receipt; storage and all other hard gates remain.

The new final immutable runtime SHA256 is
`6748f85de892b098f8c158ae8dc3cb9c14bea65885f4db39d4118df45a3604a8`;
backend SHA256 is
`7a19dacd412717fbe8d00eb8ab07b4ec103bddb3d77864859772b22e72ec95b1`.
Final-package tests under the approved private Python: **283 passed**, 15.87s,
10 affected modules. Receipt SHA256:
`2784660dcbb616f9c653d7b93445186a868883459a67b3837623a819691a9a07`.
Local two-module incremental tests: **42 passed**. `git diff --check` passed.

Full no-simulator preflight SHA256:
`0437ace22429c34f67a0b30428a8b8b48a25828c4472e67819f8105956691d6a`.
It adds actual CLI parsing, exact environment identity validation, the actual
resource auditor and shared fixed48 policy, then the full recovery/control and
backend argument chain. Explicit historical-resource/test-owner inputs produce
**WAIT**, storage FAIL, admitted 0, requested/executor 48, health streak 0,
required streak 5. These are isolated test results, not current live admission.
No process, signal, production lease/lock, consumed launch record or simulator
was created. Source evidence stayed unchanged. Earlier missing test-environment
and legacy-concurrency failures remain in their no-clobber directories.

At 05:06:57 UTC, the last live read was still 1,000 accepted / 56,000 rows,
zero production processes, and 731,528,798,208 free bytes against the unchanged
1,499,472,810,253-byte storage requirement. This package verification does not
prove a supervisor restart or any new accepted sample. Recovery may restore
the unique capacity-checking controller, but no simulator may launch while
storage or any other hard gate fails.

## Live Recovery: Capacity Checking, No New Simulators

At 05:14:28 UTC the final package was actually dispatched through the existing
checkpoint launcher under standing delegated authority. The new physical
generation is 33, PID 2745934 / start ticks 356199713, with the same campaign,
queue, logical owner and authoritative lock. At 05:16:03 the process was live,
matched its immutable lease and was the only project supervisor. No previous
healthy solver was stopped. The committed 1,000 / 56,000 checkpoint and exact
pilot-storage input bytes were preserved; accepted increment is zero.

The first fresh independent resource audit completed normally at 05:15:38.
All control identities, CPU, memory, swap, I/O, licenses and isolation passed.
Only storage failed: 730,899,976,192 bytes available versus the unchanged
1,499,472,810,253 required. Requested/admitted/executor/native concurrency is
**48 / 0 / 48 / 0**. The controller is alive and checking capacity; Phase A
simulation and new sample generation have **not** resumed. Do not restart it
merely because a gate says WAIT. No benchmark or NN training was started.

Evidence SHA256 identities:
- Actual delegated candidate: `a7abe4d8cd22d9a4f89ef1627f0577ec53b6647e490f4689b8c7acce892de6a7`.
- Delegated release: `3fd4097e71a58bdd6d6dd80253b296002d78e1a176bc5b80abf6249cce191ac2`.
- Single-use dispatch: `1f61b697ff042e33832abbb7dba8199df2ab82ad54a020fc9d433debf2c603d4`.
- Lease33: `79efdfbd859eacc8cd238570e6ff9815d3a913d0de4eaae0c2f55803c5176011`.
- Checkpoint migration: `f56e28418b0766172c2af94b7245980a7d922dd19761bea6e9a99612423dbbba`.
- Fresh resource audit: `7065b6313ca38a26d593a48d1a761872704408ba33016a82d641aef5087108b2`.
- Live process/lease observation: `73983b8eb3f7558093cd9390ccaee6e96e38c9de67512473e95d22411e2c78f0`.

Private original receipts remain on MARS and in the local engineering evidence
mirror. None is published here. The old unlaunched release and all failed
preflights remain preserved, not relabeled as successful production.
