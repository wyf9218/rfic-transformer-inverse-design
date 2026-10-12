# Handover verification — 12 October 2026

This page records publication checks, not new scientific experiments.

## Published research assets

- Repository: [rfic-transformer-inverse-design-data](https://github.com/wyf9218/rfic-transformer-inverse-design-data), **public**; no invitation required to read or clone.
- Visibility history: initially transferred privately, then made public under the owner's explicit 12 October 2026 instruction. Existing dated private-transfer records are retained as history, not current access requirements.
- Public visibility was verified through both authenticated and anonymous GitHub API access at **2026-10-12 19:01:29 UTC**; the observed repository flag changed from `private=true` to `private=false`.
- Pinned asset-publication commit: `5ccde8a44ff6749b81dab9d638d6fadc6d76def5` (before the later visibility and author-card documentation update).
- Manifest SHA-256: `10ce24d19ee3239c5cc139c87b0936bf63eaae64b8769e31b466a3dea3dd79f6`.
- 934 manifest entries, 586,922,682 bytes of curated research assets. The 37 final-weight file copies represent **20 distinct weight hashes**, not 37 independent experiments. Duplicate archival copies preserve their original context.
- Original 100K CSV compressed without changing its decompressed bytes; recorded row/unique-geometry census and splits preserved. The new extraction command reproduced the exact original SHA.
- All curated source files were checked against their destination copies during transfer. Core verification subsequently passed for 33 files. `.gitattributes` disables newline conversion to protect archival identities.
- Symmetric100K weights were retrieved through the existing authenticated MARS session, read-only, and matched `ddc3476184911007132728722ae5fb0a00d1183ee867af80cbc6ccaf9dfc805b`.
- No raw GDS/S4P corpus, licensed process deck, credential, or license file is included. Recipient permissions and current native-tool readiness are not established by upload success.

## Software and reproduction checks

| Check | Actual result | Scope |
|---|---|---|
| GUI/loader/Q-sweep focused suite | **89 passed, 1 skipped** | Synthetic evidence fixtures; skipped private parity test was not configured |
| Portable training-preparation tests | **10 passed** | Relocation/identity/no-clobber checks; no training |
| Asset helper tests | **3 passed** | Containment, symlink rejection and SHA calculation |
| Original100K, real10k, symmetric100K load | **PASS for all three** | One installation inference per bundle, finite 10-D/4-D outputs; not a scientific evaluation |
| Exact frozen training preparation/check | **PASS** | All frozen source pins, nested split and recipe parse; no optimizer updates |
| Wheel build, version0.1.1 | **PASS** | Wheel SHA `9d0144d88ad4b9184914eb11c023b71cbc5850b5a571b5fd1d3bf94731b0da56` |
| Fresh virtual-environment wheel installation | **PASS** | Separate Python3.12 environment; no dependency on dirty editable checkout |
| Installed wheel original100K load | **PASS** | Explicit deployed100k profile; one inference |
| Installed GUI HTTP/asset smoke | **PASS** | HTTP200, self-contained HTML and model context; zero jobs submitted, zero native tools |
| Whitespace and new-source privacy scan | **PASS** | Changed code sources only; curated data are in the separate companion, now public by owner instruction |
| Whole historical public test runner | **NOT PASS: 54 collection errors** | Archived tests require absent site modules/files and contaminate imports across historical trees; four skips, one deselection; no clean full-suite claim |

Earlier local GUI test attempts exposed cross-checkout editable-environment imports. The final focused run pinned the new worktree and passed. This does not erase those failed attempts. The initial private-transfer archive check reported original CSV CRLF/trailing whitespace; archived data bytes were deliberately not reformatted. The later visibility/documentation update did not rerun these software checks or change scientific results.

The native GUI producer does not yet emit the current `operating_point_evidence` and `physical_gate_evidence`. The operating-point gate is preserved. Physical GUI requests requiring that unsupported producer fail before dispatch with `NOT_NATIVE_ACCEPTED`; no fake evidence is generated. Package installation is **not** native physical acceptance.

## Environments

Focused checks and actual training preparation: Python3.12.14, NumPy2.5.2. Fresh wheel install: Python3.12.14, NumPy2.5.4, SciPy1.18.1, Matplotlib3.11.2, gdstk1.0.1 and PyYAML6.0.3. Historical training versions are preserved in archived run records; bitwise numerical equality across environments is not promised. The package contains only the already-public **synthetic** example process deck, not a foundry stackup.

The fresh wheel was built before this final documentation edit; its executable modules match this release. Install from the pinned source commit for the final README/documents.

## Explicitly not performed

No dataset generation, model retraining, full scientific reevaluation, GDS/DRC/EMX invocation, production control, or MARS process restart/stop was performed. A temporary local GUI child created solely for the HTTP smoke was stopped after the check. Existing research/server processes were left alone. No new evidence of 10,000 physical validation results is claimed.

The local archived CSV-only plotting entry had already been exercised before this release and was transferred unchanged; no repeat scientific aggregation was needed. A successor should inspect any newly rendered previews before using them in a paper.

## Remaining access/continuation items

No GitHub invitation is required for the public code or data/model companion. Native simulation still needs institution-approved accounts, raw-artifact storage permissions and licensed configurations. Completing the QA/DRC evidence producer and validating it on the matching physical contract is a specific software continuation task, not an installation step silently run by this guide. This release does not authorize unrestricted future publication of private artifacts or assign a new license to third-party material.
