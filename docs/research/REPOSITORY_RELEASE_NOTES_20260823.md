# Repository Release Notes — 2026-08-23

## Purpose

This public, sanitized GitHub snapshot was assembled as a durable handoff for future GPT-assisted research. It consolidates reusable code, tests, research contracts, verified numerical summaries, evidence hashes, failed-gate records, and explicit next actions without waiting for the still-running remote experiment. The repository owner authorized public visibility on 2026-08-23 so external GPT/research tools can read it without GitHub account access.

## Added for this handoff

- Chinese and English research handoffs;
- strict machine-readable state JSON;
- milestone and denominator accounting;
- historical 100k/200k architecture and fixed10k proxy metrics;
- 10,000→7,298 physical/fresh-EMX funnel status;
- formal NO-GO register with offline evidence hashes;
- useful-code map and full generated script catalog;
- controlled nested data-scale scripts and tests;
- selected physical-chain provenance snapshots;
- deterministic repository SHA-256 manifest generator and output.

## Controlled-experiment code synchronized from the active research tree

| file | source SHA-256 before any portability edit |
|---|---|
| `scripts/train_physical_feature_tandem_inverse.py` | `92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be` |
| `scripts/build_controlled_data_scaling_split.py` | `de66409c48e33eb3cb1821e0bd7c7a3611e627c3008f074e19b0a953b6798c8d` |
| `scripts/audit_controlled_subset_overlap.py` | `d259296a29a3be8179b0162a370ee25fe5e28bff70c00ab3fefc8535c5a0c946` |
| `scripts/bind_controlled_fref.py` | `0b10106391f971ec29451f5972aaf81e3cce9439a3d04bf8efe6da0da34f5f26` |
| `scripts/bind_controlled_fref_forward_stage.py` | `7ea86f7ac7d9d2f32baedd144558f37e75f927285e0f905804fd952aea72c926` |
| `scripts/run_controlled_paired_training.py` | `d344f7e099db8f4740fb1d7decab604723e8c99b866932db44fdaf2ab8737414` |
| `scripts/evaluate_controlled_tandem_shared_fref_fixed_targets.py` | `49f4137519d8891b7bbea0db39a7feff901836a20cfb8e2f20b60eb198fc27eb` |
| `scripts/evaluate_controlled_forward_common_holdout.py` | `cd0e7fb2d219744a9080d5dfc44f91dc0c609b050b608c595509cb0b3f547360` |
| `scripts/evaluate_and_analyze_controlled_results.py` | `eae0818aa41699b6010204c2f1a653f6ae058d8b6aa3f35a85ed831f24d5513f` |
| `scripts/analyze_controlled_paired_replicates.py` | `d513290c0bd1c5353bde911e15b5697f6a0e9d7408b88fb479e3ad3db3bd6b5a` |
| `scripts/evaluate_historical_tandem_fixed_targets.py` | `ffa428e2fc9dc1598bd85979fcae637dd5479d332e81dc773cc926b4b2254a36` |

The paired runner was made portable by replacing the private host/interpreter defaults with explicit arguments. Multi-head code was synchronized with the current shared tandem API and given an explicit fixed-response compatibility contract.

## Publication-scope edits

- Real school jump-host and license-host defaults were replaced by example domains.
- Three redundant embedded bootstrap payloads were omitted from this release copy because their compressed bodies retained obsolete site endpoints; the original offline source tree remains unchanged.
- Real data, weights, GDS, Touchstone, PDK, license, and run-output files remain excluded by `.gitignore`.

## Validation trace

1. System Python 3.9 run: collection failed because the repository requires Python ≥3.10. This is an environment failure, not a code-result claim.
2. Bundled Python 3.12 without test packages: stopped with `No module named pytest`.
3. A local ignored Python 3.12 virtual environment was created and `.[test]` installed.
4. First Python 3.12 collection identified two missing controlled-evaluation scripts; both were copied from the active tree.
5. Controlled data-scale/evaluator tests then passed `12/12`.
6. First full run reached `1045 passed, 52 skipped, 1 deselected, 1 failed`; the sole failure exposed an old multi-head/shared-tandem API mismatch.
7. The multi-head implementation/test were synchronized and its focused suite passed `9/9`.
8. A final full-suite result is recorded below before release.

## Final release validation

Python 3.12.13: **1050 passed, 52 skipped, 1 deselected, 0 failed in 71.30 seconds**.

The repository manifest is generated only after final documentation and code synchronization. Future changes must regenerate `docs/SCRIPT_CATALOG.md` and `CODE_SNAPSHOT_SHA256.txt` and rerun the public suite.
