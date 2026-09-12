# M19 latest: installed repair and automatically launched batch000002

Latest cutoff11:07:31.564143 UTC is in DEPLOYED_SNAPSHOT.json; older SNAPSHOT.json(11:00:59) and PRE_REPAIR_SNAPSHOT.json(10:40:54) remain immutable snapshots.

The real11:05:38 installation succeeded at the oldbatchterminal/ownerexit boundary. Newowner3850660/start401507710, metadata3850664/start401507710, and detachedstandby3841226/start401506408 were live on releasebd8ab853... and runtime0ad043b6.... Oldowner/oldstandby/finiteinstaller had exited; healthy native signals0, oldbatch not replayed. Automatic transition and metadata attachment are proven, not just staged configuration.

At11:07:31 actualnative0, newbatchresults0: the existing five-independent-sample admission sequence was not complete. Firstresource sample11:06:50 PASS; no48-admitted or performance-benefit claim. Ordinary programs continue this wait and dispatch; no AI log polling is required.

Since11:00:59, only oldbatch's remaining28 RESULTs were read:28fresh,13strict/range unique,13new-result formal+1prior TRAIN057freshbackfill. Historyformal0; certifiedlowerbound6569. Oldsuccessor256/256terminal=238fresh+18analytic,core109/formal109/pending0. M19 total since10:27:01:133fresh/64core/64formal; do not add this total to its component windows.

Full result transport is now supported with optional --output NEW_PATH on the existing landing CLI: complete JSON is written once with O_EXCL and fsync; stdout gives onlypath/SHA/bytes/status. One new synthetic output test passed, no actualpost81/oldprojection rerun. The original truncatedpost81 artifact remains disclosed; its complete summary and18train sourceobjects are published, not a reconstructed fullprojection.

The following section documents earlier cuts and actual implementation history, not current process counts.

# M19: ongoing production and bounded permit-release repair

Latest source cutoff: 2026-09-12 11:00:59.782042 UTC. See SNAPSHOT.json; PRE_REPAIR_SNAPSHOT.json preserves10:40:54 separately.

- Window10:40:54–11:00:59:78 new fresh EMX results and3 analytical failures;40 strict/in-range candidates,39 newly formal,1 pending publication. Historical formal increment0. Certified unique lower bound6555.
- Actual native7×2CPU; requested48/executor48. Current successor228/256 terminal,210fresh,96core/95formal.28 without terminal are not28 running solvers.
- Original native/metadata/standby identities remain healthy. Automatic batch continuation has already executed; current256/12h/5GiB and all physical gates are unchanged.
- M19 since10:27:01 has105 fresh results,51 core candidates,50 new fresh formal records; this aggregate is not a second production window and must not be added to its components.

## Actual scheduler change

At the pre-cut seven EMX processes had finished but retained permits behind dispatch-lock work. The new per-candidate permit-release path avoids that heavy lock, persists deactivation before freeing reservation, fails closed on persistence error, and retains candidate storage claims. This is a demonstrated code defect, not proof of the sole throughput limiter.

The immutable package and exact source/tests are in native_permit_release. Remote24/24 targeted tests passed (12 new,12 affected existing); the original fixture failures remain disclosed in STATUS_20260912T110059Z.md. Scientific contracts and resource thresholds are unchanged.

At11:00:59 the finite installer PID3567815/start401468254 was waiting for BATCH_TERMINAL_AND_OWNER_EXIT. It preserves live work and existing budgets; no second native controller. Runtime is STAGED, NOT YET EFFECTIVE. Installer waits at most90minutes and remains attached to its finite SSH command; do not claim detached persistence or a measured performance improvement. Existing original production continues independently. No second installer should be launched.

## Incremental research intake

Existing landing CLI now accepts --observation PATH --observation-sha256 SHA with explicit source verification and expected counts. See OBS_READER_RECEIPT for the first successful new31 command and identities. Backfills require explicit --prior-received PATH SHA. No periodic local intake daemon is claimed installed by this CLI change.

Five new focused tests passed. A first attempt rejected a missing analytic-only flag before projection; its preserved failure receipt is included. The adapter records absent flags as null, never fabricated false/true. Raw values, split and identity are unchanged.

Prior41 first projection:10train/8cells/one empty frozen3801 cell[4,0,0]. New31:7train/7cells/zero empty or underfilled landings. Both have zero train|k|>0.8. These are reference-relative descriptive increments, not cumulative coverage or sampling superiority.

The next81 CLI ran once and exited0; its oversized stdout was truncated by transport. Original OBS remains complete. Only independently complete captured sections may be published; do not claim a complete frozen ACTUAL_LANDINGS or rerun physical work to repair reporting.

Do not rerun historical21135, first100 disposition, source93 readiness,15models,128or64. Full100K/FINAL/independent10000-request validation remains incomplete. No AI paper figures.
