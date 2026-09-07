# Running-Stage Isolation Repair (2026-09-07 UTC)

Status: PATCH_TESTED_NOT_DEPLOYED. This is not a production-resumption receipt.

## Live Evidence

Observation at 2026-09-07T02:44:13Z: generation 31, PID 3661745,
start_ticks 354956987, sole supervisor and exact live lease match.
Campaign, queue and logical owner remain unchanged.
Formal accepted: 861 unique geometries; frequency rows: 48216.
Current attempt: 139 Cadence inputs, 119 PASS and 20 FAIL;
119 GDS identity PASS and 119 zero-blocking Calibre PASS.
EMX dispatch log remains empty; no native EMX process or new S4P is proven.
Requested/admitted/executor/native EMX concurrency: 48/0/48/0.
CPU, memory, swap, I/O, storage and license checks pass; isolation alone fails.

## Minimal Repair

The idle-only classifier incorrectly counts the authorized stage launcher and
backend as two duplicate runners. A Python EMX batch is also classified as
Calibre because its arguments reference a Calibre input receipt.
The patch adds explicit running-stage scope to the existing sampler.
It validates lease/owner identity, history, backend/profile hashes, stage
arguments, parent edges, UID, boot identity and start ticks before recognizing
the authorized stage tree. Native counts use executable evidence.
Idle-start checks, conflicting-owner rejection, scientific contracts and all
resource thresholds are unchanged. Tests use explicitly synthetic fixtures.

## Verification

- Local supplemental regression: 301 PASS (Python 3.9; not the production runtime).
- Frozen private package: 424 PASS, 14 affected modules, approved private Python
  wrapper and actual interpreter separately verified; NumPy 2.5.0.
- Packaged real-input replay: old failure reproduced, patched isolation PASS;
  wrong owner/backend/lock/conflicting lease still rejected.
- No production resource state, lease, simulator or process was changed by tests.
- Runtime manifest SHA: 261e715b747e7d5dd415a23fc1ceaf06accafaa242ed90337a3824f7c8778977
- Backend manifest SHA: 27fa27fd1eb2eccfeb8da3377245b448bab2649ddf2e841cc3e582fd837a5c0d
- Test receipt SHA: 1adc59e0fe96bdbd75075e4e98107dd2550096b523e59faeceed77ea9be57980
- Real-input replay SHA: c02443e4c59a8dadc7e5ba7f489120f37b1fb214157527a4da8e535947ecf744

Preparation/replay artifacts are private, under the project account's
b56_running_isolation_dev_20260907T022716Z directory. No PDK, weights or GDS
are included in this commit.

## Deployment Blocker

The active bounded dispatcher has no external pause/drain request interface.
The existing supervisor stop handler waits for the current stage to return;
it does not stop that stage's blocked dispatch loop. The verified switch
boundary remains the complete committed batch, which has not been reached.
The failed-control-only predecessor route is inapplicable: generation 31 has
already executed Cadence and Calibre. Do not manufacture a terminal failure,
reuse its launch intent, change live hash-bound bytes, or treat zero native
EMX as a checkpoint. The existing 119 GDS/DRC artifacts must be preserved.

No new authorization candidate or production startup was issued for this patch.
Full successor-start preflight: NOT_RUN, no legal handoff boundary yet.
Next requirement is a valid controlled handoff/reuse route and exact new-byte
approval, not a repeat of the already consumed e94f approval.
Throughput and ETA: NOT_MEASURED. NN training and benchmarks remain disabled.
