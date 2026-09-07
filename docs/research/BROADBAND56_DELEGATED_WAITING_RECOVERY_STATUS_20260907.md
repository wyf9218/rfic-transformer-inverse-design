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
