# Frozen 256 → development128 native successor

This is the byte-identical adapter and synthetic test source deployed by the
sole native owner. It is not another simulation controller or a general job
queue. Production, frozen candidates, q_proxy, original gates and native
concurrency are unchanged. Private deployment manifests and runtime/PDK inputs
are deliberately not published.

## Verified deployment, not completed physical handoff

At 2026-09-10 01:57:43 UTC, the MARS system atd service had consumed the initial
ticket. The adapter recorded WAIT_PREDECESSOR_ALIVE and one successor check
was queued for 02:02 UTC. The server checks every five minutes without AI
wakeups. No user-systemd service, linger change, or second native owner was
installed. This is a timestamped capture, not a continuously updated job list.

The fixed release remains
`2c24abe9d81e951cc77286499b8976a8c7dfee6d0b6e2a8b051becf8065911bc`.
It contains 128 original requests: 93 analytical passes and 35 retained
analytical failures. None may be replaced or assigned another Q.

Dispatch requires the original 256-item terminal with matching frozen IDs/Q,
the predecessor PID/start identity to have exited, no related/native survivors,
and fresh original resource, license and lease gates. Native concurrency stays
one; the original deadline is 2026-09-10 18:00 UTC. Ambiguous submission or
pre-exec intent without an observed owner blocks instead of blindly restarting.
Stopping future scheduling does not signal healthy solvers.

## Evidence and source identity

- Installation receipt SHA: `09f04b4af68ac0f8947a946f23e553f850d2f1076c9af755ecb30cbcac6cd2ea`.
- Deployment manifest SHA: `9d8eaa346771604965ca9ed8adfc13933bdd269fbc262d86492f73d7518ac243`.
- Adapter SHA: `85ff89e1dbb1d81aa1233cf52f50c05c22820f44e803be976365976643bf952d`.
- Original synthetic tests SHA: `ca0d91ee3eff2b321f59b8cfdbbe87223630abeff84b6f92531be7a32f773c6c`.
- Owner-reported final synthetic run: 27 distinct tests PASS. Earlier 24/26
  development runs are not additional distinct cases. Research did not rerun
  those 27 cases for publication.
- Independent static review confirmed that exact owner argv is distinguished
  from --preflight-only; the latter waits and never proves native launch.
- The original preflight regression covers helper/decision behavior, not the
  complete process-snapshot-to-ticket path. See the separate, narrowly scoped
  integration test file and its independently recorded result.
- The two new fake-proc integration cases passed in one local run (0.008 s),
  without repeating the original 27 tests. They exercise unchanged process
  parsing, snapshot construction and ticket handling; at/exec/signals are
  prohibited. They are still synthetic, not a completed physical handoff.
- The first real scheduler check is evidence of installed waiting only.
  Full native handoff, new128 physical errors, and FINAL accuracy are not
  established by any of these tests.

## Maintenance boundary

Do not install this public copy, submit a second ticket, or manually launch
new128 while the original server registration exists. Live maintenance belongs
to the sole native owner and must use the existing private binding and exact
deployment paths. The code will not initialize a new deployment from this
repository alone.

The original mock-only tests can be invoked from this directory with
`python -B -m unittest -v test_successor_once`; the narrow process integration
tests use `python -B -m unittest -v test_successor_once_process_integration`.
These are maintenance commands, not native-launch instructions and not a
request to rerun already completed checks.

[Current interface routing](../../docs/research/eucap15_interface_routing_20260910/RUN_STATE.json)
separates this development successor from still-unimplemented FINAL native
evidence ingestion and dispatch.
