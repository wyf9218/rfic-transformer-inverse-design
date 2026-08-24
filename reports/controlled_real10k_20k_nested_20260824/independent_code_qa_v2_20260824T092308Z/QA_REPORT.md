# Controlled real10K/20K independent code QA v2

Verdict: `NO_GO`  
CODE_GO: `NOT CREATED`  
Review: independent, result-blind, code/package only  
Findings: P0/P1/P2/P3 = `0/9/6/0`

## Scope boundary

No implementation was modified. No production or MARS target process was started, stopped, resumed, or signaled, and no MARS file was written. No research-data materialization, model training, scientific evaluation, or EMX was run. No production scientific data row, real weights file, or production numerical result was opened. QA did run local synthetic-fixture tests and a read-only MARS Python binding recomputation. Other MARS access was limited to package metadata, file modes, SHA-256 closure, path absence, and host identity.

The present CODE_GO authority would cover only `MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY`; materialization, training, common-test access, numerical release, fresh EMX, and process signals would remain false. Two P1 findings exist even in that narrow present scope, so no CODE_GO can be issued. Training-runner and evaluator findings are retained as future gate blockers, not as authority currently exercised.

## Outcome by gate

| Gate | P0 | P1 | P2 | Decision |
|---|---:|---:|---:|---|
| MARS native preflight/reviewed native test | 0 | 2 | 4 | NO-GO |
| Future six-arm training runner | 0 | 4 | 0 | NO-GO |
| Future one-time common-test evaluator | 0 | 3 | 2 | NO-GO |
| Total reviewed candidate | 0 | 9 | 6 | NO-GO |

## Blocking findings

1. `QA2-C001` (P1, current scope): inherited pytest environment can false-PASS the native test. `PYTEST_ADDOPTS=--collect-only` collected one test, executed zero test bodies, never opened deliberately nonexistent manifest/index paths, and returned 0. Preflight treats any zero return code as PASS (`preflight:705-748`).
2. `QA2-C002` (P1, current scope): GO/package/runtime bytes are hashed, parsed, and executed through separately reopened paths without held-FD/dev+ino continuity (`preflight:270-278,331-342,623-632,699-729,812-816,973-977`). Owner modes 0555/0444 are not immutable against the same UID.
3. `QA2-R001` (P1, future training): `_audit_material` accepts caller-authored self-consistent material without exact builder/splitter/source identities or the outer materialization COMPLETE receipt (`runner:570-607,762-785`). The author test fixture demonstrates acceptance with only `shared_contract`, one source identity, and an arbitrary all-true production check.
4. `QA2-R002` (P1, future training/F008): a hostile `subprocess.Popen` raising `SubprocessError` leaves INTENT/stdout/stderr but no `FAIL_RECEIPT.json` (`runner:2273-2325,2611-2621`).
5. `QA2-R003` (P1, future training/F007): the GO-bound package SHA-index byte identity is not rechecked per arm. Uppercasing digest text changed the index SHA while leaving its normalized semantics; `_verify_closure` still passed (`runner:295-314,1143-1149,1243-1255`).
6. `QA2-R004` (P1, future training): material QA requires a GO reporting zero P0/P1, but the runner's exact GO keyset omits `findings` and rejects it as extra (`builder:457-462`; `runner:663-673,1190-1203`).
7. `QA2-E001` (P1, future evaluator): the dynamically imported shared scientific contract and Python/NumPy runtime are not release/GO-bound (`evaluator:42-54,1448-1467,1691-1699`).
8. `QA2-E002` (P1, future evaluator): the irreversible claim has file fsync but no directory fsync; released trainer/CSV/holdout/targets/weights are hashed and then reopened by path without descriptor continuity (`evaluator:218-227,1730-1748,3016-3079`).
9. `QA2-E003` (P1, future evaluator): evaluator GO lacks an exact keyset, nonce, zero-P0/P1 counts, and QA-required SHA; any nonempty all-true check map is accepted (`evaluator:1666-1713`).

Six P2 hardening findings cover preflight attempt durability, extra package roles, caller-selected/nonnested candidate paths, substring-only singleton detection, evaluator symlink/nlink/mode handling, and nonfrozen final evaluator output closure. Full evidence and required fixes are in `QA_MATRIX.json`.

## F001–F008 closure

F001–F006: the original defects are closed. F007 and F008 are not fully closed:

- F007 fails because the exact GO-bound package SHA-index bytes can drift without per-arm rejection.
- F008 fails because catchable post-intent exception classes can leave no terminal FAIL receipt.

The new material-provenance and GO-schema contradictions are separate fresh P1 findings.

## Local verification

Exact warnings-as-errors suite:

```text
PYTHONWARNINGS=error python3 -m pytest -q \
  tests/test_build_controlled_real10k_20k_nested.py \
  tests/test_run_controlled_real10k_20k_materialization.py \
  tests/test_run_controlled_real10k_20k_paired.py \
  tests/test_build_controlled_real10k_20k_mars_package.py \
  tests/test_preflight_controlled_real10k_20k_mars.py \
  tests/test_evaluate_controlled_real10k_20k_common.py
105 passed in 5.04s
```

`py_compile`, `git diff --check`, and static forbidden-primitive gates passed. The passing author suite does not cover the hostile environment, descriptor continuity, material provenance, package-index byte drift, unhandled post-intent exception, or directory-durability cases above.

## MARS immutable-package check

- Package: `/volumes/research-localdata/ywang3652/controlled_real10k_20k_handoff_20260824T085115Z/package_v1`
- Host/UID/boot: `mars-0002.ece.wisc.edu` / `2259579` / `4801efc4-eba0-4b79-91b2-4b082f238271`
- Manifest SHA: `ef52a3ab99273d7bd1c0c0d5c38c0558dd99eed4637fb2912debdc792d34d374`
- Index SHA: `f82edc7e82dac1fdb24e41aaf878e8dcc7323b253da788eb6c37bbfda090667a`
- `sha256sum -c`: all 18 indexed members passed.
- Filesystem: exact 15-role package; root/directories 0555; files 0444, nlink=1; no extra role or file.
- Packaged code/preregistration/native-test bytes equal the local frozen bytes.
- Exact candidate path order: `materialization_gate_candidate_v1`, `materialization_output_v1`, `materialization_execution_receipt_v1`; all three absent.
- Future receipt directory `preflight_receipts/attempt_0001` absent.
- Packaged `_audit_package` and `_expected_go_bindings` were recomputed live on MARS. Canonical 4,596-byte binding JSON SHA: `f215d62e71e5a563a51b4ac42f336efb538ec554c3607483cd0b202ecb1d4bda`.
- Bound runtime identity exactly matches Python 3.12.13, NumPy 2.5.0, Python SHA `8c515a32...`, NumPy-core SHA `471db158...`, config SHA `eca5779c...`, and show-config SHA `1007c129...`.

The remote package transfer is exact. NO-GO is caused by reviewed implementation gates, not transport corruption.

## Decision

Because fresh P1 findings are nonzero, `CODE_GO.json` is intentionally absent. No current or future execution authority is granted. Corrected bytes require a new no-clobber package and a fresh independent result-blind QA.
