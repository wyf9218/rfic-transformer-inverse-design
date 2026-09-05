# Concurrency Work: Partial, Not a Performance Result

## Observed Production State

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
