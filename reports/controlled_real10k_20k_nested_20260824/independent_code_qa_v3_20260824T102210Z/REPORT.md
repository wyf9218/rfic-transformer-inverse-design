# Controlled real10K/20K independent code QA v3

## Verdict

**NO_GO.** Final counts are **P0=0, P1=6, P2=3, P3=0**. `CODE_GO.json` was not created because the exact eligibility rule requires P0=0 and P1=0.

This was an independent, result-blind code and package audit. Candidate code was not modified. No MARS write, process start/stop/signal, materialization, training, evaluation, EMX, numerical-metric interpretation, data-row interpretation, or weights interpretation occurred.

## Blocking findings

1. `QA3-MAT001` (P1): materialization rehashes frozen paths, then imports builder/shared/splitter by path. A same-path substitution fixture restored authorized bytes before the later rehash, while the already-loaded callable remained substituted.
2. `QA3-RUN001` (P1): the training runner copies `os.environ` and launches the trainer without an isolated startup or exact allowlist. A benign `PYTHONPATH/sitecustomize` fixture ran before the requested Python body; `-I -B` prevented it.
3. `QA3-EV001` (P1): evaluator release binds the output root as a string and locks a replaceable inode inside it; it does not hold/bind the root directory inode. A fixture produced two successful claims with one GO at the same lexical path.
4. `QA3-EV002` (P1): evaluator hashes and parses exact GO through separate path opens. A substitution between opens made the consumed reviewer differ from the SHA-authorized bytes.
5. `QA3-EV003` (P1): evaluator exact-GO dictionaries rely on Python value equality, so JSON `false` equals integer `0` and integer `1` equals `true`; malformed types were accepted and normalized.
6. `QA3-EV004` (P1): post-claim failure evidence remains mutable and unindexed. Deleting the 0644 claim/FATAL files from the 0755 root allowed the same GO to be replayed.

Nonblocking findings retained: `QA3-C101` (preflight setup can fail after mkdir but before the terminal-receipt try block), `QA3-C103` (candidate paths remain generically caller-selected), and `QA3-C104` (singleton scan is filename-substring based).

Exact evidence, line references, impacts, and required fixes are in `FINDINGS.json`.

## Required QA-v2 closure

- Closed: `C001`, `C002`, `R001`, `R002`, `R003`, `R004`, `E001`, `C102`, and `E101`.
- Partially closed but still blocked by new defects: `E002`, `E003`, and `E102`.
- Still P2: `C101`.

The author warnings-as-errors suite passed: `150 passed in 8.43s`. Passing author tests do not override the independently reproduced authorization/continuity defects.

## MARS read-only identity

- Host: `mars-0002.ece.wisc.edu`
- UID: `2259579`
- Live boot ID: `4801efc4-eba0-4b79-91b2-4b082f238271`
- Package: `/volumes/research-localdata/ywang3652/controlled_real10k_20k_handoff_20260824T100826Z/package_v2`
- Package index check: all 18 indexed members PASS
- Exact roles: 15; native roles exactly `[native_smoke_test]`
- Manifest SHA-256: `2b1acc29d9accbc9a612c1d4fa3e95fd7b1d9f73a2156e83e5b05f4b0c04c0b2`
- Receipt SHA-256: `7bcbe238a25f35e785af42714c9f1e1b4e108e5a1277ff70bcaad54e31078422`
- SHA index SHA-256: `7408446655081d717aabfdbb8dfdab786c37e4c35faa6fba7c69939876d5e7fc`
- QA-required SHA-256: `0012ba3c6ca59d3a0d0f5378a4eb89582062ef7cbb01380bf6c243bc9e57bea7`
- Build-attempt receipt SHA-256: `c4718a123df6967ca25d63d44a163d8dab5201766b1c74db5ebabffa67d05b4c`

Frozen runtime matched exactly: CPython 3.12.13, Python executable SHA `8c515a32b1a5d3d807e53359901a4d09ec06819b488736641aabd6a12eefba63`, NumPy 2.5.0, core SHA `471db15840ca5bb0b32158eddca6d3e42545611055d6a4a4dee6e0d8a315a442`, config-file SHA `eca5779c26589580219ef7f949eb28f5eaad7ad44febc8444ea17d53ff92a947`, and show-config SHA `1007c12980046a08a6661242d1d73fd28ac080e20908fc7e0ea65f6ec165688d`.

All three exact materialization candidate paths and `preflight_receipts/attempt_0001` were absent. No matching current-UID controlled process was found.

## Candidate identities

- shared contract: `ca6824e5d47fc037c856044ad74b0dec26844fed19d09bbfee42d44fbd3969c0`
- material builder: `e5c25b79e76343c77a88a7e6837b8547bd1a8ad8b8aaa277e61b3d487e9a8cfc`
- material gate: `9e7efe71111daeb2c954ffcd8b57a41c6b17384f2a8451dfb0964f83152f9746`
- paired runner: `33049b3f2fc28752d93f9f8b19665eb8d7ed6f5fdff48dcac621de1387fc1a91`
- package builder: `c4b9f496bda460e201958280a5a09c4aa2aaed36ffd265abd633ac8f5ef7b0b1`
- preflight: `bf77000ae2e1bb934050b26fa630fa405dc7e418a3da3102cf8a7bbe8a5c840a`
- evaluator: `1196e4c480ddfe4f355834f0ba27cc379032f4bddb4273c6a60de36c147af964`
- native smoke: `7f1cdd03fa0be778fbf419e1018ffb11bc6dbdde73596d6ea56e7083a4a7635a`

## Release boundary

No authority is released, including MARS native preflight. The only legal next step is a new no-clobber candidate that fixes all P1 defects, followed by fresh independent result-blind QA. Existing package, failed evidence, and candidate bytes must be preserved.
