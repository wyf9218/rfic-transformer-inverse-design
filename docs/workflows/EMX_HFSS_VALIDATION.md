# EMX/HFSS Cross-Solver Validation

## Purpose

HFSS is an independent physics check, not a replacement label generator for
the production dataset. The comparison is meaningful only when geometry,
stackup, material properties, ground reference, port ordering, termination,
frequency grid, and de-embedding are matched.

## Sequence

1. Select a response-blind real EMX sample.
2. Freeze its geometry, process, port, and frequency manifests.
3. Rebuild the same structure in HFSS from the machine-readable payload.
4. Export the same Touchstone port count and reference impedance.
5. Run raw frequency/port/passivity/reciprocity gates.
6. Extract Lp, Ls, Q, and K with the same formulas.
7. Plot both full-band curves and a registered target-frequency marker.
8. Report signed and absolute differences without clipping resonances.

## Interpretation

Large disagreement is first treated as a contract mismatch: port reference,
ground, material stack, conductor thickness/loss, terminal geometry, or
de-embedding. Only after those are proven identical should mesh and numerical
solver differences be analyzed.

Agreement at one frequency does not prove broadband equivalence. A candidate
is suitable for reporting only when the complete trace, extraction equations,
source Touchstone files, and model screenshots are archived together.
