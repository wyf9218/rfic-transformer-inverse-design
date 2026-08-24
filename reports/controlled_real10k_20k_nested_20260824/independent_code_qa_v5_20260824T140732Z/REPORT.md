# Controlled real10K/20K independent code QA v5

## Verdict

**NO_GO.** Final counted findings are **P0=0, P1=2, P2=1, P3=0**. Exact release eligibility requires P0=0 and P1=0, so no `CODE_GO.json`, execution authority, or downstream authority artifact was created.

This was a fresh, independent, result-blind review of exact candidate `controlled_real10k_20k_code_candidate_v5_20260824T133543Z`. The frozen candidate index SHA-256 is `e03e74a9fd9578a2ac3b653fa7ea65a62cc0820057401d0534a3becf442d034e`; the candidate manifest SHA-256 is `58f5878044884f443aed91b5aeeed66f79c38c5ff4745a7078efd7bb6452bc6d`. HEAD and `origin/main` were both `0b23ca5a3bf026c65aa86dc4fca07ac61dafe34d`. The manifest's `base_head=36e8a7667ad3c4c64aea8d5322312f728fcf5640` is the candidate commit's starting parent baseline, not the frozen candidate commit and not a finding.

## Blocking P1: evaluator exact21 is not a direct semantic package re-audit

`QA5-EV001` is release-blocking. The evaluator defines the expected 21 materialization roles at `scripts/evaluate_controlled_real10k_20k_common.py:310-332` and checks that the candidate and paired reduced binding maps have those keys at `4013-4115`. It also recomputes the live path SHA for each record. That is an exact-key and live-file self-consistency check, but it is not the required direct package/protocol/source provenance check.

At `4511-4589`, the evaluator accepts the package proof and package manifest when their `role_identity` objects agree, but it does not require the package's `required_roles`, `role_destinations`, or `role_identity` to be the frozen exact 21-role graph. At `4590-4634`, it cross-binds only the process-singleton contract and the two runtime-closure identities. It does not repeat the ten-role package-to-materialization mapping that the materializer correctly applies at `scripts/run_controlled_real10k_20k_materialization.py:210-220,1661-1665`. It also does not independently validate the candidate's full static materialization contract, its canonical SHA, frozen source/protocol identities, or result/row-access and host declarations.

The defect was reproduced locally without scientific data. The repository's own synthetic fixture constructs an 11-role package (`tests/test_evaluate_controlled_real10k_20k_common.py:1996-2048`), supplies dummy regular files for otherwise unbound candidate roles (`2241-2256`), and sets `materialization_contract`, `host_constraints_asserted`, and `result_or_row_access` to empty objects (`2491-2511`). The production `_audit_singleton_transmission_closure` nevertheless accepted it; the existing test at `2905-2923` expects acceptance. The observed accepted state was candidate roles 21, package required roles 11, package role identities 11, and ten scientific/code/protocol candidate roles not semantically package-bound.

The evaluator must require the frozen package 21-role graph and destinations, compare all applicable candidate bindings through the materializer's complete mapping, and independently reapply frozen source/protocol/static-contract/access constraints before one-time test release.

## Blocking P1: one-sided Q direction conflicts with common-forward exact-label fidelity

`QA5-SCI001` is separately release-blocking. The preregistration freezes exact-Q training semantics and defines the common 902-row primary estimand as forward prediction error against stored real-EMX geometry/response labels (`CONTROLLED_EXPERIMENT_PREREGISTRATION_V1.json:70,117-120`). For that task, both Q underprediction and overprediction are forward-label errors.

Production evaluation nevertheless calls the shared inverse-target response helper for `common_forward_primary` (`scripts/evaluate_controlled_real10k_20k_common.py:6375-6450,7482-7487`). That helper defines Q shortfall as `max(true_Q-predicted_Q,0)`, calls `predicted_Q>=true_Q` a target-met event, and sets the Q contribution of `fixed_span_engineering_joint_error` to the one-sided shortfall. The paired layer marks target-met-rate increases as `positive_favors_large` (`6578-6590`), and the bootstrap reproduces the same scalar direction (`6817-6864,7017-7022`).

A result-blind scalar counterexample demonstrates the conflict: for stored Q=10, small prediction 9 has exact error 1, while large prediction 100 has exact error 90. Yet the small one-sided shortfall is 1 and target-met is false; the large shortfall is 0, target-met is true, and the engineering-joint Q component is zero. Thus the formally paired/bootstrapped directional metrics favor the much worse forward predictor. These one-sided semantics are legitimate for an inverse target specification evaluated through a predicted response, but not as common-forward stored-label accuracy.

Before result access, common-forward accuracy must use symmetric exact-Q errors and signed-bias diagnostics. Q target-met, one-sided shortfall, and one-sided engineering-joint metrics must be restricted to inverse target-spec proxy/fixed frames, or explicitly labeled secondary and excluded from common-forward fidelity/direction claims. The paired and bootstrap scalar keysets and tests must be updated accordingly.

## P2: evaluator exact21 path checks are TOCTOU-prone

`QA5-EV002` is a nonblocking-but-required hardening finding. `_sha256` reopens a lexical path (`scripts/evaluate_controlled_real10k_20k_common.py:412-417`), `_binding` performs separate path checks/hash/stat operations (`1357-1374`), and `_json` later reopens the path again (`557-565`). In the exact21 record audit, `_audit_materialization_candidate_record` takes `lstat`, performs a path SHA read, then constructs the accepted identity from the earlier metadata without a final descriptor/path continuity check (`2887-2933`).

A deterministic local interleaving used two same-length, mode-0444, nlink-1 regular files and `os.replace` immediately after the production path hash read. The audit returned success with the old SHA and old inode while the live path already held the replacement SHA and a new inode. All five proof predicates were true: accepted, returned pre-replace SHA, live replacement SHA, returned pre-replace inode, and changed live inode.

The evaluator should single-open each authority and exact21 member with `O_NOFOLLOW`, fstat before/after, hash and parse held bytes, retain descriptors through consumption, and add rename-interleaving hostile tests.

## Scientific contract review

Static review found the following implementation contracts aligned with preregistration v1 and addenda v1.1/v1.2:

- exact ordered 10K prefix/subset of 20K, with the 10K append restricted to historical train cells;
- identical common validation/test identity lists, complete-cell isolation, and a sealed 902-row common test denominator;
- one shared declared-domain midpoint/half-range normalization contract;
- exact forward `10-256-256-256-4` and inverse `4-256-256-256-10` networks, GELU, and independent-sigmoid decoder;
- paired seeds `20260711/12/13`, equal 1200 forward and 1200 inverse optimizer updates, validation every 20 updates, and disabled early stopping;
- validation-only checkpoint selection, zero training-time test access, and complete small/large pairs for all three seeds;
- fixed10k reuse without regeneration, and no fresh-EMX generation;
- common stored historical real-EMX forward-label error kept distinct from own-forward proxy and geometry-label secondary evidence;
- three-seed `large-small` paired deltas, sample SD with df=2 t intervals, and the four-frame PCG64 physical-cell cluster bootstrap with common resampled row multisets and retained failed denominators; their mechanics pass, while `QA5-SCI001` remains open for the common-forward Q scalar semantics.

## Test and integrity evidence

The complete frozen warnings-as-errors suite was run independently with bytecode and pytest cache publication disabled. Result: **331 passed, 2 skipped in 17.32 seconds**, exit 0. Both skips are explicitly Linux-only:

- exact memfd/ELF launch is MARS-native Linux QA;
- SystemExit attestation needs Linux sealed descriptors.

All four candidate index members and all 22 implementation/test/frozen-dependency identities declared by the candidate manifest matched. Candidate index and manifest hashes were unchanged after WAE and hostile synthetic checks. Before QA publication, tracked diff and index were clean, and the untracked-file count was zero.

No MARS/network access, CSV row access, weight access, test/performance/fresh-EMX value access, materialization, training, evaluation, fixed10k regeneration, EMX generation, or process signal occurred.

## Release boundary

No authority is released. Preserve this NO_GO closure. The next legal action is a new no-clobber candidate that closes `QA5-EV001`, `QA5-SCI001`, and `QA5-EV002`, followed by another fresh independent result-blind QA.
