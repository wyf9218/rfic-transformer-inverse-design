# Fixed48 checkpoint lease fingerprint repair

Status: software verified, NOT production resumed. New exact-SHA approval is
required before deployment; the approved parent does not authorize changed bytes.

## Minimal change

- `broadband56_checkpoint_startup.prepare_controls` obtains the fingerprint from
  the validated candidate/authority chain, rejects a conflicting existing value,
  explicitly writes it into the successor lease, then reads the pinned file back
  and invokes the real `broadband56_scheduling.fixed_generation_policy` consumer.
- `validated_lease_fingerprint` verifies owner, queue, source and target backend,
  authority, and overlay before completing a missing legacy field.
- `broadband56_checkpoint_handoff.validate_failed_control_predecessor` verifies
  the failed control-only successor and its original checkpoint/migration. This
  preserves monotonically increasing physical generations without treating a
  failed startup as new production or reverting to an older lease generation.
- The strict scheduling consumer, fixed48 policy, simulator code, production
  profile, scientific contract, DRC, port and frequency contracts are unchanged.

## Evidence

At 2026-09-06T20:50:01Z a fresh read-only MARS observation found no project
supervisor/runner/native solver. All 14 frozen failure/checkpoint evidence pins
still matched. The committed checkpoint remains 861 accepted geometries and
48,216 frequency rows in PILOT_1000. Stale WAITING JSON is not proof of a process.

The private packaged replay used the approved private Python and numpy 2.5.0,
real old leases and the real 861 checkpoint. Producer and strict consumer were
not mocked. The old generation-28 lease was copied byte-identically, made
read-only, and passed without adding the missing field before the producer.

- Unmodified producer reproduced the exact owner/backend binding exception.
- Repaired producer serialized the correct fingerprint and passed first policy
  consumption and full controller control/argument validation.
- The real failed generation-29 predecessor validated and replayed a generation-30
  continuation at 861 rows of geometry, not a Golden restart or a 100/1000 gate.
- Conflicting fingerprint, backend, owner, and an uncompleted missing field remain
  rejected. Historical source/failure evidence stayed byte-identical.
- Test authority, process-role inputs and PASS resource-gate inputs were explicitly
  fixtures. The real campaign lock was opened read-only; its acquisition call was
  replaced by a test-only no-op. No production lease, launch intent, supervisor,
  simulator or project-owner approval was created or consumed by this replay.

Public-safe receipt SHA-256 identities:

| Evidence | SHA-256 |
| --- | --- |
| Approved parent candidate | `0a4c476c279a28d4c5d6fe56c4260f846db82a29fc0d3c1cd3083afb659e60bc` |
| Preserved generation-29 terminal failure | `f683c3fdb139653dda920f8dd1ccc953a9a059aeff1a430bc18ef430575572d2` |
| Packaged real-file replay | `6b864e440310db59214a70a8cafd4a075b3d79105b00f71ecffe7278930e1d0c` |
| MARS affected regression receipt | `a156b6cc2017c72c028e9d97b879773ee4dc64845ea0999dc52235acb8e6e70b` |

The first isolated packaging preflight failed because the capacity producer pin
still named the previous runtime directory. The subsequent isolated package
corrected that path binding without changing producer bytes. The failed package
and FAIL receipt remain preserved; neither package has been deployed.

## Tests

Local focused regression: 208 passed. Final MARS package with the deployed legacy
executor and approved private Python: 348 passed, zero errors/failures/skips,
zero child-process or signal attempts. Exact affected test modules:

```text
test_broadband56_checkpoint_startup.py
test_broadband56_checkpoint_handoff.py
test_broadband56_fixed48_scheduling.py
test_broadband56_scheduling.py
test_broadband56_swap_override_control_scripts.py
test_broadband56_stage_resource_history.py
test_broadband56_fixed48_backend_context.py
test_broadband56_dispatch.py
test_run_broadband56_v2_authorized_queue_controller.py
test_broadband56_capacity_snapshot_adapter.py
test_broadband56_swap_override_policy.py
```

Private evidence paths and the one final candidate are recorded in the workspace
engineering memory, not published here. The next legal action is approval of that
new exact candidate, followed by fresh live isolation/resource/license checks
and the existing append-only launch mechanism. Requested concurrency and executor
capacity remain 48; live admission and native EMX concurrency are not established
by these software tests. NN training remains unauthorized.
