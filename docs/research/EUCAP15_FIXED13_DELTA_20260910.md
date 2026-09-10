# Fixed13 acquisition delta: saved physical evidence

Status: COMPLETE_FIXED13_ACCOUNTING; independent numerical QA GO.
This is a read-only consumer of13 previously published acquisition terminals,
not a new generation batch, a new128-model pilot, or a matched-budget comparison.

| Source | Fixed delta | Strict valid | SRF-invalid | GDS fail | Strict core |
| --- | ---: | ---: | ---: | ---: | ---: |
| EXPLORATION | 1 | 0 | 0 | 1 | 0 |
| GEOMETRY_DOE | 7 | 3 | 4 | 0 | 3 |
| SPARSE_TARGETED | 5 | 0 | 4 | 1 | 0 |

The sole native owner had already exported11 candidate/GDS/DRC/EMX/S4P/label
chains and2 bound GDS failures. The unchanged research validator reconciled
these with frozen proposals and their saved56-frequency CSVs in one execution:
1.06s, maximumRSS131104768bytes,330 source bindings. No solver, GDS construction,
DRC invocation, S-parameter re-extraction, model inference or training was run.

Independent arithmetic/identity review read only45 relevant source pins,
checking2371 conditions without repeating the330-source physical-chain acceptance.
The11 saved responses comprise3 strict/core-valid and8 strict-invalid records.
The latter all fail the SRF/2 condition despite descriptor and basic physics QA
passing. Their descriptive numbers remain available, but cannot contribute to
strict-lumped model accuracy or training admission.

Four SPARSE_TARGETED records have saved target/proxy residuals; all four are
DESCRIPTOR_ONLY_NOT_STRICT. Seven DOE records have no requested physical target,
so target/proxy errors are null. The two actual GDS failures retain null EM
labels and physical errors; their original geometry and selectedQ were not replaced.
Failure checks:ground_clearance_pass,foundry_power_line_contract_pass,
foundry_via_stack_and_landing_pad_pass.

## Denominators and limits

- This fixed terminal delta is13; the original frozen proposal frame remains256
  (128 per arm), with203 analytic/dispatch-eligible and53 held.
- These13 are not13 new solver invocations;11 are previously completed physical
  chains, and2 fail before EMX.
- The selection is an unbalanced completion delta, not an equal native-budget
  prefix or a chronological random sample. All3 strict cases being DOE is not
  evidence that DOE wins. Reuse the separate matched16 analysis for its stated scope.
- No current6329 dataset change, accepted commit, training admission, coverage
  gain, model comparison, FINAL test, q_emx or full11Q evaluation occurred.
- Native process liveness was not queried; this delivery does not establish
  original256 completion or frozen128 successor launch.

## Reproducible source package

The two adjacent Python files are byte-identical copies of the actual one-off
private-workspace driver and its16 synthetic tests. They reuse the repository's
existing evidence reader; this is not a portable installer or a new controller.
Private frozen exports/path maps/configuration are required and are not in Git.
The verified execution command is recorded in the private report's ACTUAL_COMMAND.txt.
Do not rerun the completed consumer just because its code is visible here.

- Driver SHA:10b57f01f84c0164d6ab1b481e4b8a1366ff3edd9ca2051210a868a3e3344934.
- Test SHA:7fb1cbd61835b24aa227da40cb1bb4697912c5474389675d6909a1db64b04e5a.
- Actual summary SHA:d3300ef7a91b95a2687e18c373f597c0fe51d04f3b697091bb7463b11a769a52.
- Actual receipt SHA:bba831b6e79fe4566550e7f14870f3b666e6295c5b7a199441ebc0f85b9e1bbd.
- Independent numeric QA SHA:0e6c730070eafa847026fd53748ea30200e22340a529d2da930fd7960b51d85d.
- Original owner export SHA:ca43bfabf6e37ac86339c9edef7089af3ef38a55238105aeff5858556aa43556.

Private REQUEST_RESULTS.json/CSV preserves exact identities, responses, flags,
residuals and failures; independent ROW_INTERPRETATION.json supplies the strict
versus descriptor-only label. Public STATE_COUNTS.csv is an exact grouped
count derived from those13 rows. No figure has been generated.
