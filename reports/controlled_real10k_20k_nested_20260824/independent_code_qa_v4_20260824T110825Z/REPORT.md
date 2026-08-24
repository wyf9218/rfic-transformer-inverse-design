# Controlled real10K/20K independent code QA v4

## Verdict

**NO_GO.** Final counts are **P0=0, P1=6, P2=2, P3=0**. `CODE_GO.json` was not created because exact release requires P0=0 and P1=0.

The package is metadata-complete and immutable, but not execution-eligible. MARS `package_v3` has 15 exact roles, 19 regular files, 18/18 indexed members passing, files 0444/nlink1, directories 0555, and no symlink or special entry. All future materialization/preflight paths and `CODE_GO.json` remain absent; matching controlled Python processes are zero.

## Blocking findings

1. `QA4-RT001`: preflight runtime probe uses `-I -B` without `-S`; venv `.pth/sitecustomize` can execute before NumPy and reviewed snapshot consumption.
2. `QA4-GO001`: preflight and training exact-GO validators accept JSON bool/int aliases.
3. `QA4-REC001`: the preflight receipt root is not held or externally leased; same-path replacement allows PASS publication and same-GO replay.
4. `QA4-REC002`: parent-fsync failure after PASS publication leaves a downstream-acceptable PASS despite main raising.
5. `QA4-RUN001`: the trainer's exact environment and `-I -B` do not block venv `.pth/sitecustomize`; QA3-RUN001 remains open.
6. `QA4-PKG001`: role-separated packaging does not form the importable `rfic_transformer_inverse_design` tree required by runner/trainer. The runner fails import; the trainer can resolve an external package path not held by package_v3. Native smoke compiles these roles but does not import their real controlled dependency graph.

Nonblocking interface findings are `QA4-IF001` (QA-required underdeclares the actual GO bindings) and `QA4-C104` (substring/TOCTOU singleton scan).

## QA-v3 closure and cross-binding

`QA3-MAT001` and evaluator `QA3-EV001..EV004` are closed by held-byte, held-root/lease, exact-type, and immutable failure-closure code plus hostile tests. `QA3-RUN001` is not closed because its test covers inherited `PYTHONPATH`, not venv site initialization.

Runner/evaluator static cross-binding passes: runner SHA, terminal schemas, paired seeds, columns, and isolated-child environment SHA `ae52267e...674f` agree exactly. That static agreement does not cure unbound startup execution or the broken package import graph.

## Test and result-blind boundary

- Warnings-as-errors six-component suite: `172 passed in 10.85s`.
- Targeted QA3 closure suite: `14 passed in 2.93s`.
- Independent hostile probes reproduced venv site execution, preflight/training GO type confusion, and role-layout import failure.
- No CSV row, weight array, model metric, or fresh-EMX result was interpreted.
- The primary reviewer made no MARS write. An auxiliary package red-team reviewer accidentally created `/tmp/SHOULD_NOT_WRITE_THIS`, immediately unlinked it, and confirmed it absent; this is recorded as operator scope deviation `QA4-OPS001` with no persistent state.
- No native preflight, materialization, training, evaluation, EMX, process start/stop, or signal occurred.

Exact findings and evidence are in `FINDINGS.json`; machine test evidence is in `TEST_MATRIX.json`.

## Release boundary

No authority is released. Preserve package_v3 and this NO_GO closure. The next legal action is a new no-clobber candidate followed by fresh independent result-blind QA.
