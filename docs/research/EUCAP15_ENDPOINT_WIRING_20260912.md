# Shared endpoint policy: explicit existing-runner integration

This supplements the preceding local endpoint-construction note. No new
controller is introduced, no old trial is resumed, and no native runtime is
modified by this research-side implementation.

The actual native CONFIG names `scripts/run_candidate_queue_dataset_parallel.py`.
Its existing `_run_shard` starts the same-directory
`run_candidate_queue_dataset.py`, whose `main` creates
`TransformerEmxEvaluator`; `_evaluate_export` then calls the layout exporter.
All four stages now explicitly forward one optional argument:

```text
--port-endpoint-policy shared_port_edges_20260912_v1
```

The sole native owner must copy the exact five source files listed in the
private wiring receipt into its **new isolated runtime**, retain the existing
Cadence command and budgets, and append that flag. Both scripts and package
imports must resolve to that isolated repo. Without the flag the old default
is `legacy`; no environment variable silently changes existing runs. The
nonlegacy policy is included in the evaluator cache key, preventing reuse of
old geometry/config cache entries for a changed physical construction.

## New verification and label boundary

Two process-free integration cases exercised parallel argument parsing,
single-shard `main`, the real evaluator and its exporter call, capturing the
actual keyword at the final function boundary. Nine further new cases cover
cache isolation, label containment and rejected arguments. The earlier12
geometry tests and six-case construction replay were not repeated.
Two initial test-harness failures are retained: an exception sentinel was
captured by the evaluator's normal failure handler. A test-only BaseException
sentinel fixed the stopping mechanism; this did not change production code.

Labels are not required to coincide with end faces. The existing construction
insets horizontal signal labels; auxiliary ground-pin centres are inset
0.25 um. The new opt-in exporter nevertheless requires every original port
label to remain inside its own planned terminal conductor and at least one
grid step behind its end face, before committing any polygon changes. It
does not relocate labels or relax a gate. A new read-only check of the saved
six-case replay points confirms all48 port groups satisfy this condition.

Updated overlap metadata is explicitly
`PRE_CADENCE_CONSTRUCTION_DERIVED_NOT_ACTUAL_GDS_MEASUREMENT`.
Actual streamout geometry, DRC, SRF and fresh EMX remain separately required.
Physical labels from an old construction cannot be inherited by the new one.

Wiring receipt SHA:
`5b82bc173a27a49dd6cd0ae776c4af289ab63ae0a228fd39daaac2423d642fb9`.
Private path: `reports/eucap15ghz_20260908T220300Z/endpoint_fix_20260912_v1/wiring_v1/RECEIPT.json`.
Status: **LOCAL_CLI_PATH_VERIFIED; NATIVE_DEPLOYMENT/VALIDATION_NOT_EXECUTED**.
