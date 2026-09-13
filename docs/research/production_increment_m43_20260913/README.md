# M43: measured dispatch bottleneck fix, actual 48 not yet demonstrated

At 2026-09-13 23:23 UTC, batch19 had 23 fresh S4P, 5 analytic failures,
and 13 strict/in-range candidates. Actual native EMX concurrency at this
single observation was 0, not 48. Formal unique ledger lower bound was 8522.
Candidate eligibility and formal acceptance are not interchangeable.

Batch18 completed. Tail permit records showed 38–68 s waiting for the dispatch
lock and roughly 3.8–6 s holding it, with no recorded resource rejections.
Fresh whole-root directory traversal took about 3.8 s within that lock.

The new allocation walk uses fd-relative scandir, retaining a fresh full-root
walk, exact inode deduplication globally and within each candidate, and fail-closed
handling of symlinks, unreadable directories and directory replacement.
It removes repeated Path construction, lstat and relative-path parsing.
No allocation cache, capacity bypass, scientific-rule change, or new controller
was introduced. CPU remains 2 per EMX; configured EMX capacity remains 48.

One sequential Linux comparison on the same completed 256-candidate batch gave
3.85755 s for the deployed walk and 0.35794 s for the candidate. Total allocated
bytes (2613182464) and every candidate allocation were identical. OS cache and
measurement order were not randomized. This is not proof of live production
speedup or 48 simultaneous solvers. Four changed-path Linux tests passed.

The candidate is uploaded and its existing boundary installer has reserved the
next safe handoff. It is NOT_INSTALLED at the stated observation. Healthy batch19
is not restarted; the installer will continue the same state after its closure.
M42's maintenance pause is already installed and preserved in the candidate:
September14 22:00 America/Chicago stops new dispatch ahead of the 23:00 reboot.

The code here is an auditable runtime delta, not a standalone simulator launcher.
Tests require the existing authorized runtime via ALLOCATION_BASE_RUNTIME and
the deployed reference via ALLOCATION_REFERENCE_RUNTIME. Private deployment,
process, data and authentication paths are intentionally omitted. Original run
receipts remain private; their identities are in STATUS.json.
