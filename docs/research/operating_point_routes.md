# 15 GHz operating-point routes

The canonical classifier is `rfic_transformer_inverse_design.analysis.operating_point15`.
The research import is a compatibility shim to the same implementation, not a second policy.
`operating_point_15ghz_v1` retains exact-frequency, finite positive R/X, numerical QA,
K_abs 0.2–0.85, and separately bound GDS/DRC/EMX provenance. There is no half-SRF
or unknown-SRF rejection. Historical strict masks and frozen experiments remain intact.

New 15 GHz frequency training and Q-scan configurations use `OPERATING_POINT_15GHZ`.
The shared eligibility mask does not intersect historical `y_valid`, which can
contain the retired strict mask. Normalizers and model identities remain paired.
New data construction still enforces exposure-aware 80/10/10 split readiness;
an input package existing does not establish training completion or independent test eligibility.

The installed-package GUI backend now requires original `operating_point_evidence`
and geometry/GDS/S4P-bound `physical_gate_evidence`. Missing evidence is rejected,
not silently treated as physical validity. Its full eleven-Q diagnostic is explicitly
separate from the final pre-EMX q_proxy-selected experiment.
Source integration does not imply an already running GUI process has loaded the new code.

Changed-route verification: five synthetic route checks plus the affected backend
binding test passed (6 tests). Synthetic fixtures are software checks, not EM results.
No previous model, frozen target set, simulation, or full historical suite was rerun.
