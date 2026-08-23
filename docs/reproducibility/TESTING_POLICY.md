# Testing Policy

## Public CI

Run:

```bash
python tools/run_public_tests.py
```

This suite covers portable package logic, data/model contracts, fail-closed
audits, synthetic Touchstone fixtures, and reproducible command behavior.

## Site Integration

Files listed in `tests/site_integration_tests.txt` require artifacts that are
not distributed publicly: project-root orchestration scripts, real MARS/HFSS
return bundles, generated report trees, or foundry-tool handoff packages. They
remain available for the private research workspace and are not represented as
passing public CI.

The CMA-ES smoke node listed in `tests/public_ci_deselected_nodes.txt` is
stochastic across optional `cma` versions. Deterministic optimizer contract
tests remain in public CI.

To run every collected test in a fully provisioned private workspace:

```bash
python -m pytest -q
```

Public CI success therefore means the redistributable source is reproducible;
it does not claim access to EMX, HFSS, a foundry PDK, or real research data.
