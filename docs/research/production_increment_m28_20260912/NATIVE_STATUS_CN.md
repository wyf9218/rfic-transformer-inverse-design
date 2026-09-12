# M28: Production Increment and Bounded Dispatch Diagnosis

Live process/count cut13:32:23.194175 UTC, baseline13:23:11.275490. No new production deployment or process signal in M28.

- Observation `increment_20260912T133222626113Z/OBSERVATION.json`, SHA256 `021729e0280fab6d592b515fd71da7881f620b218be094487cb6faf280912558`,841813B.
- Formal accepted6820;15GHz rows6820; referenced broadband rows381920. Head006820 SHA `66564d2180dd01896027886f83994c9733562ca1fe2d9c64735ee9d91de9516f`,189179B.
- New56 terminal=53fresh(train31/validation12/test10)+3analytic. New20core(train10/validation6/test4) all20 formally committed; exact request-ID sets equal. Plus previous3pending now formally committed, so formal+23(train13/validation6/test4), not23 new core candidates. Pending0 at this cut.
- Previous pending DOE002, TRAIN000, TRAIN002 committed respectively6798/6799/6800 at13:23:16.284200/22.188022/28.443368. Header SHAs `4994ac902614bc06fa47ce0f2b1d328603469f0e115fd62b82f1540de2ea0a29`, `701e82db25e48e6557a3b253052fcc167ea5ceb9dfa82980cd62e6b346540caf`, `c9087fee83836417b68deefdb66c920fcb3f09ea0526075105a5cb52ede4a414`. Original pending snapshot remains unchanged.
- Batch4 cumulative67 terminal=62fresh+4analytic+1previous EMX-stage rejection,24formal. Requested/capacity48, actual native4 with4EMX permits and7Cadence permits. These are a later cut than the previous28native, not a change of policy.
- Same owner1052218/start402287917, metadata1052221/start402287917, continuation1041957/start402286569 remain alive. Runtime23ed and continuation1584 unchanged; no current continuation failure. No repeated old RESULT bodies, prefix QA or historical sample read.
- Resource snapshot SHA `2514a3d11aaea548dea73d0bff1b16f6e04e0e3a8d66f099a23851fde023a15e`,19522B,13:32:10.964455 PASS. Healthy8/required5, total samples16; normalized load1 .4767049154/load5 .4483668009, memory fraction .7575620214, iowait .0021876266%, free disk458000187392B, quotaUNKNOWN. Cadence3600/Calibre84/EMX292 available license evidence.
- Storage actual626774016B+projected2683109376B<5368709120B; pin `2c598cbd00ccb1128e1c929e03fa91c4d06e68f2280b10c798e4615b1df64238`,10859B. Resource replay allowed additional Cadence1/Calibre8/EMX28 under a shared budget; this cut was NOT CPU-insufficient like13:23.

## Why Four Native While Seventeen Awaited EMX

- One bounded existing OWNER_EVENTS prefix read selected13:31:00..13:36:00; no new resource probe, process sampling, result body read, or directory scan. `dispatch_window_20260912T133730334757Z/READBACK.json` SHA `9a18968e7deaaa1e612132ee476e92d967905a3cec90746dcc823938da18c643`,213025B. It stores497 selected events and19 exact permit readbacks. The event source is an append-only prefix observation, not an immutable whole-file SHA claim.
- All17 request IDs marked EMX_WAITING_ADMISSION at13:32:23 received ordinary EMX permits without intervention. First at13:32:26.142594, about3s after cut; ten by13:32:42.190474; all17 by13:34:31.298226, about128s after cut. No waiting ID left ungranted in this window.
- First ten permits used the SAME13:32:10 resource SHA2514a3d1, healthy8, explicit additional EMX26..29. The last seven used the subsequent13:33/13:34 samples, healthy9/10. Thus the earlier snapshot captured admission not yet completed, not a stuck queue or a need to wait for CPU load to drop.
- Deployed Manager.acquire uses a2s wait/recheck and serial dispatch_lock around release verification, storage snapshot, plan and durable permit publication; it rechecks capacity after I/O. Sample_loop uses60s sampling plus license/processing time. Cadence, Calibre and EMX shared this serial admission path; actual first Calibre permit after cut was13:32:25.155220.
- Finite subsequent grants prove dispatch resumed normally and no permanent lock/storage-claim block for those17. Existing events do NOT measure lock-hold/I/O durations or each rejected decision; the share of latency attributable to contention versus verification/storage I/O is UNKNOWN. Do not claim no performance overhead or a measured speed improvement. No tuning or new controller was introduced.
- Permit timestamps are not a new simultaneous-native observation; the13:37 retrospective read does not update13:32 live counts or formal count.

## Single Retained Failure

- DOE021 error is `FOREIGN_NATIVE_CHAIN` in the shared pre-native isolation gate, returncode1, before native EMX dispatch. Exact command/request/preflight/log evidence is in `failure_doe021_m28_v1/READBACK_CN.md` and four pinned raw files.
- Original peer payload is absent; which peer/rejection reason and whether this is a shared deterministic bug remain UNKNOWN. No evidence of a geometry-response failure and no basis to relax isolation or retry it. Minimal diagnostic capture at a supported future boundary is PLANNED only.

Remaining global blocker: none shown by this bounded cut/event continuation. Existing ordinary production proceeds, with resource-budgeted admission and preserved candidate failures. No additional AI polling, tests, deployment, NN training or obsolete campaign launch.
