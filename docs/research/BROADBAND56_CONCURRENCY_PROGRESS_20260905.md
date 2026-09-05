# Concurrency Work: Partial, Not a Performance Result

## Latest State (05:11 UTC)

The approved **1.10/1.10 single-worker PILOT_1000 startup policy is now
deployed**, replacing the previously documented deployment mismatch. The
20% memory floor and all other hard resource, license, isolation, scientific,
layout, DRC, frequency and physical-label contracts remain unchanged.
Exactly one source module changed from the preceding immutable package:
the operational swap/startup policy. All simulator/extraction role bytes
and EMX computational dependencies were verified unchanged.

The real resource-snapshot replay demonstrates that the old policy's CPU-only
WAIT becomes PASS when both loads are at most 1.10 and other hard gates pass.
The same 96 policy/adapter/controller tests passed locally and on the private
execution interpreter. Completed-stage and 100-geometry progress consumers,
the existing ordered handoff chain, interpreter identity and resume cursor
were also checked before the idle-only handoff. Two earlier no-simulator
preparation failures are retained: a nested Golden reuse reference and an
unrelocated finalizer replacement pin. Neither required a scientific change,
weaker validation or a new Golden simulation.

Generation 26 was stopped only after verifying its exact idle identity and
zero simulator/runner children. Generation 27 now holds the sole authoritative
lease for the same campaign, queue and logical supervisor. **100 accepted
geometries / 5,600 rows** were preserved and rebound without resimulation.

At 05:11:09 UTC the live process/lease/lock checks verified one supervisor,
zero duplicate supervisors, zero runners, and zero Cadence/Calibre/EMX jobs.
The latest startup resource gate was **WAIT**: normalized load1/load5 were
**1.132484/1.117864**, genuinely above 1.10. The process remains alive and
automatically samples again; this is now a capacity wait, not the obsolete
0.90/0.95 policy. The prelaunch auditor labels this compatibility check GOLDEN;
it does not mean the completed Golden is being simulated again or that the
validated production count is zero. New production has not yet started.

Policy source SHA-256:
`489c1abcf8ac183459ee39de24f61f1a75d7182888ef25684d5302845430c886`.
Runtime/backend manifest SHA-256:
`cec82a514ebbce093aa37f1acfe465b561a9b0902ff2de81554ce1edc0d61dca` /
`38083ebf3fa4b1a3d3d5d48757fd6ceb9c18c042f84b4ac45de72e60a9cd6764`.
Control preflight / private test receipt SHA-256:
`6be96adb4612b6ad563b3e5d881e4b14ca59b36cae540817e0320565f1652e0e` /
`2bf9e96351f944e5c39c10bd8cf123bc8089068153a34aa6f75cff10a17bfb52`.
Live process and policy status receipt SHA-256:
`0690603d566a75408f168be9f049eea673cc057e6d97709fdd3c4463b5e8e214`.

No fastest concurrency is established and no NN training has started.
The approved controlled concurrency comparison and full 200K campaign
remain incomplete; preserve the live supervisor while capacity is unavailable.

## Historical State (04:52 UTC)

The result-only recovery is now published: **100 accepted unique geometries,
100 original S4P artifacts and 5,600 frequency rows** are bound into the
resumable progress chain. The same campaign, queue and logical supervisor
remain in use. No recovered geometry was simulated again or counted twice.
The old root, failed attempts and original receipts remain unchanged.

The deployed postprocessing repair required an explicit compatibility binding
for the exact old/new finalizer SHA pair when reusing the completed Golden.
The Golden-reachable finalizer AST, simulator/extraction entrypoints and
scientific contract are unchanged. Unknown finalizer changes, absent bindings
and changes to another role still fail closed. This is reuse of original
execution evidence, not a new Golden or relaxed physical validation.

Generation 25 passed startup resource gates but exited because the restored
status omitted `check_index`. No new simulator ran. Its generic startup
failure report says zero accepted, conflicting with the actual validated
100-row progress chain; that generic field is not the dataset authority.
The previous physical process was proven dead, the missing control cursor
was derived from retained stage directories, and generation 26 resumed the
same root with unchanged runtime/backend bytes. Live process identity and
lease checks verified one supervisor and zero simulator jobs.

**Production has not yet restarted.** At 04:52:18 UTC the next resource check
reported normalized load1/load5 of 1.094345/1.058716, available-memory fraction
0.439401, and WAIT. The earlier 04:50 check passed memory, swap, I/O, storage,
license and isolation gates; only CPU gates failed. Inspection of the deployed
policy shows that PILOT_1000 still uses 0.90/0.95, despite the owner's approved
single-worker 1.10/1.10 window. The public source contains the correction,
but it is not in this immutable runtime. This deployment mismatch must not
be described as genuine failure of the approved 1.10 threshold. Preserve the
live supervisor until a checked, idle-only operational transition is ready.

The local adjacent regression set passed 100 tests in 4.25 seconds. The exact
private-interpreter Golden/finalizer subset passed 67 tests with no simulator
launch. These are software tests, not full-suite or concurrency measurements.

Published-progress receipt SHA-256:
`394417c6e935ebfd0c4dbe194964974a3c0913e4cf2d33f6091c5c31ad8d66ae`.
Stage/progress consumer preflight receipt SHA-256:
`a54582d37acf3e4c4db765d7528c49e5aaabe8bea9d212613d94e7bdf5e7d706`.
Private software-test receipt SHA-256:
`a1677b54e6a8ef166435b66973bd307e9bec69595c00867027997185ee1081ba`.
Generation-26 handoff / lease SHA-256:
`cdafd6350847048f75114c0218a26ddaee0dd9ca56455f5026df0a2d5fe9595b` /
`a7e8013785d62d854bebd3a350fe7d02adb19b1695857076bd98a094055d1d5a`.

The native-solver benchmark correction is still preparation-only, and no
complete concurrency level or fastest level is established. No NN training
has started. The 200,000-geometry goal is not complete.

## Historical State (04:25 UTC)

Production reached **97 published accepted geometries / 5,432 rows**. Three
more geometries completed fresh EMX and exact 56-point QA. At the resulting
100-geometry boundary, cumulative finalization failed and the physical
supervisor exited. The older `PILOT_1000_RUNNING` snapshot is stale: it is
not evidence of a live supervisor or active production.

The failed concatenation mixed the preceding stage's projected public-output
indexes with full attempt tables. The preceding execution trace already binds
the full cumulative inputs. The fix follows that trace and finalizer receipt,
checks their identities and source/output agreement, and preserves the full
tables. Unexplained CSV header differences still fail closed; no fields or
physical values are invented, and no prior artifact is overwritten.

A private result-only recovery has now verified **100 unique geometries,
100 S4P artifacts and 5,600 frequency rows**, including recomputing the
physical values from those existing S4Ps and passing the checkpoint audit.
It ran zero subprocesses and zero simulations. Coverage remains partial.
These results are in a separate no-clobber directory: the three recovered
geometries are **not yet published to the live queue**. Backend/control rebind,
progress restoration and supervisor recovery remain pending. No production
restart, new authorization request, or NN training occurred in this recovery.

The finalizer regression set passed 14 tests locally and with the private
interpreter; the related local finalizer/progress/raw-product/materializer
set passed 47 tests. This is not a full-suite or throughput claim.

Result recovery receipt SHA-256:
`88d9c085a3dbaa52db3ac6f89b64fd08544dd0d1a1fcff729c0152c3ae3e91fb`.
Recovered 100-geometry checkpoint receipt SHA-256:
`0f9ffb547ba6945dc1a52b849fda69530311a9e38cb03dccea826bae48f00c86`.
Private software test receipt SHA-256:
`a80dc7945cc0f4f98b73f7988c70319d7a7235d490796d00265118494220f200`.

The following observations are history. In particular, the reported live
two-worker production and benchmark-hook state below must not be treated
as the current execution state.

## Historical State (04:04 UTC)

The production batch completed its full acceptance path: **93 canonical
unique geometries / 5,208 frequency rows** are now accepted. Its 61 new
EMX results are no longer merely pending receipts. The target remains
200,000 geometries / 11,200,000 rows. No neural-network training has started.

A controlled idle-boundary handoff installed the exclusive benchmark hook
in one physical successor of the same logical supervisor. The existing
campaign root, queue, backend, simulator code, full authorization and accepted
artifacts remain unchanged. The old physical process was proven dead before
the successor started; no running simulator was killed. At 04:04 UTC the
successor was live and had entered its benchmark resource sampling phase.
No controlled throughput trial had completed yet.

The control-only changes preserve the next iteration index across same-root
resumes and reject pre-existing probe logs before executing a probe. Failed
iteration evidence cannot be overwritten. An exclusive callback validates
the completed stage/progress chain and lease, runs only at an idle pilot
boundary, and obtains a fresh resource sample before returning to production.

Larger test waves require measured solver memory headroom while retaining
the 20% available-memory floor. Sampled process high-water memory is not a
proven absolute memory bound. The five-distinct-healthy-check ramp requirement
remains separate from the owner's single-worker 1.10 startup window.

Verification: 181 local targeted regression tests passed. The private
benchmark/control fixture set passed all 88 tests on the approved execution
interpreter with child-process execution disabled. These are software tests,
not throughput measurements; they do not establish a production optimum.

Real same-root component preflight receipt SHA-256:
`8ce3b71660d62770f7cd290d977f114dd021cc01e2ae6924eab4567f19bbc5dd`.
Private fixture receipt SHA-256:
`744b7041e5762d9157989b8a4c6ee6ebed7a60e97e9805ae536cc1d26be99880`.
Control integration manifest SHA-256:
`57b7c86582ffa7c57f5b16d44b3d0af5d65b2bdfdb04a858a128369a68ae4b3d`.

All seven screening levels, paired confirmation and end-to-end production
confirmation remain incomplete. No fastest concurrency or 200K completion
date is established. The earlier observations below are preserved history,
not current runtime status. This is not a claim that the full test suite passes.

### First Real Trial: Incomplete (04:05 UTC)

One of 96 planned single-worker jobs completed fresh EMX and exact 56-point
S4P/feature QA. Replenishment stopped because the monitoring binding pointed
to the vendor's shell launcher instead of the native solver ELF. The live
isolation check therefore rejected the native solver, and the trial recorded
zero observed solvers. It correctly drained the running job without killing
it. The remaining 95 jobs were not submitted. This is an instrumentation
failure and **not a valid throughput result or fastest-concurrency result**.

The same supervisor returned to production with acceptance unchanged at
93 / 5,208. Two actual native EMX solver processes were subsequently observed
in the next production attempt; no duplicate supervisor existed. The one
benchmark result does not count toward the 200K production total.

The native binary identity has been found and verified against live processes.
A prepared local correction rejects non-ELF solver bindings before callback
installation; 90 benchmark/control fixture tests pass locally. This correction
is **not yet installed in the running supervisor**. Do not overwrite the
incomplete screen or interrupt the healthy production attempt to apply it.

Benchmark QA receipt SHA-256:
`8d802976182b50d75cf359f089f87073abdd5ad5ecb9b6f3f414efee3d97f433`.
Live handoff/result evidence SHA-256:
`3998505ba8bce5a9d13c94d92641237646a2ced9e19f352dbe27d8a4d2c1992d`.

## Historical Observation (02:42 UTC)

At 2026-09-05T02:42:53Z, the existing sole supervisor was running PILOT_1000
at configured concurrency 2. Two actual EMX solver executables were observed.
The completed production checkpoint remained 32 accepted unique geometries
and 1,792 geometry-frequency rows. Four additional fresh-EMX PASS receipts
were present in the active batch, pending subsequent QA and acceptance.
Neither an EMX receipt nor a submitted candidate is an accepted geometry.

The observer did not launch, stop, restart or duplicate the supervisor.
The live campaign still uses its existing immutable runtime; the source
changes described below have not been deployed to that process.

## Source Changes and Verification

- The owner-approved 20% memory floor is reflected in the resource policy.
- The single-worker pilot startup window includes normalized load1/load5
  at most 1.10. Other resource and physical gates are unchanged.
- A bounded trial engine accepts explicit authority, admission and telemetry
  callbacks. It preserves failed jobs, drains existing children on a gate
  failure, and does not count benchmark repetitions as production samples.
- Golden operational reuse validates the original complete execution chain,
  identical simulation entrypoint/configuration/dependency bytes, unchanged
  results, and a bound target authorization. It does not rewrite historical
  execution records as a new simulation.

Local targeted tests: 156 passed. The private-runtime subset: 144 passed.
These are software/fixture tests, not simulator throughput measurements.
A private no-simulator consumer preflight read the 32-geometry checkpoint
and selected the next frozen boundary of 100, with 68 raw candidates.
The subsequent idle-only deployment check did not pass because the original
supervisor had already entered production. No deployment was performed.

## What Is Not Complete

The frozen comparison covers concurrency 1, 2, 4, 8, 16, 32 and 48 on the
same 96-job workload. No controlled benchmark trial has completed, and the
benchmark execution hook is not installed in the live supervisor.

There is no measured fastest concurrency yet. The currently observed
2-worker production batch is not a controlled comparison with historical
1-worker batches or the historical 48-worker campaign. An EMX-only winner
would still require end-to-end pipeline confirmation.

Preserve the active child processes. Recheck live progress before any
operational transition; a preparation fixture is not permission to stop
an active simulator or to reuse an obsolete 32-row progress snapshot.

## Subsequent Verification (03:19 UTC)

The same physical supervisor remained alive and the active two-worker batch
had 47 fresh-EMX PASS receipts awaiting batch QA/acceptance. The official
checkpoint was still 32 geometries / 1,792 rows. Normalized load1/load5 had
risen to approximately 1.093/1.076; these readings do not satisfy the
five-consecutive-healthy-check ramp requirement. No running job was stopped.

The trial engine now stops replenishment and drains healthy jobs if actual
solver telemetry is invalid or exceeds the requested concurrency. Excess
solver activity cannot qualify as evidence that a requested level was tested
safely. Six additional fixture cases cover this boundary.

Local targeted regression tests: 162 passed. Private benchmark orchestration,
admission and output-verification fixtures: 62 passed locally and 62 passed
with the private execution interpreter. The latter is the same test set,
not 124 independent cases. All are software tests, not throughput trials.

A read-only adapter preflight rechecked 32 audited source geometries and three
existing real S4P artifacts using the unchanged exact-frequency parser,
S-to-Z conversion and physical-feature extraction. Source, authorization,
configuration, command and output identities were verified. The helper was
correctly rejected as a non-authoritative execution caller. A separate live
isolation check correctly rejected running a benchmark alongside production.

Private preflight receipt SHA-256:
`2e83825ec7165ad536fac39eab4d58c63561832f86dedecedf289ae8f5ea741f`.
Private fixture-test receipt SHA-256:
`8e235af7355b2c6bf39a223a6413d75afb551c5b3fcd37dc1635cf33c05fe5f6`.

The benchmark session implementation is still preparation-only, not installed
in the live supervisor. Idle-boundary startup integration, measured per-job
memory headroom before larger waves, real screening trials, paired repeats
and end-to-end confirmation remain incomplete. No fastest level or completion
time for the 200,000-geometry campaign has been established.
