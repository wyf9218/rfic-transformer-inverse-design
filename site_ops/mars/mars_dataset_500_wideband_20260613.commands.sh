#!/usr/bin/env bash
set -euo pipefail

# 1) Fill real MARS EMX/Cadence paths in the config before running with --check-emx-paths.
.venv/bin/python scripts/preflight_dataset_config.py '/home/researcher/Documents/模拟变压器AI反向建模/mars_dataset_500_wideband_20260613.yaml' --check-emx-paths --report runs/dataset500_wideband_grounded_20260613_config_preflight.md --summary runs/dataset500_wideband_grounded_20260613_config_preflight.json

# 2) Launch the wideband sample-dataset pilot only after preflight passes.
MPLCONFIGDIR=$PWD/.mplconfig \
.venv/bin/python -m rfic_transformer_inverse_design.interfaces.cli sample-dataset --config '/home/researcher/Documents/模拟变压器AI反向建模/mars_dataset_500_wideband_20260613.yaml' --count 500 --batch-size 10 --sampler lhs_optimized --seed 20260613 --z-load-ohm 50.0 --out-dir runs/dataset500_wideband_grounded_20260613 --fail-on-error

# 3) After the pilot finishes, prove file completeness and EMX command semantics.
.venv/bin/python scripts/audit_mars_run_progress.py runs/dataset500_wideband_grounded_20260613 --out-dir runs/dataset500_wideband_grounded_20260613/mars_run_progress_audit_20260613 --expected-count 500 --expected-frequency-start-ghz 5.0 --expected-frequency-stop-ghz 50.0 --expected-frequency-step-ghz 0.1 --expected-frequency-points 451 --max-touchstone-frequency-checks 500 --require-clearance-audit --require-emx-command --expected-port-mode single_ended_shield_grounded --expected-pin-purpose 51

# 4) Run all local acceptance gates before using the data.
.venv/bin/python scripts/run_dataset_quality_gates.py runs/dataset500_wideband_grounded_20260613 --out-dir runs/dataset500_wideband_grounded_20260613/dataset_quality_gates_20260613 --require-emx --expected-port-mode single_ended_shield_grounded --expected-pin-purpose 51 --require-clearance-audit --expected-frequency-start-ghz 5.0 --expected-frequency-stop-ghz 50.0 --expected-frequency-step-ghz 0.1 --expected-frequency-points 451 --max-touchstone-frequency-checks 500 --audit-sampling-distribution --sampling-require-uniform-closer-than-normal --sampling-min-uniform-vs-normal-fields-fraction 0.8 --sampling-min-histogram-entropy-frac 0.85 --sampling-max-min-norm 0.05 --sampling-min-max-norm 0.95 --sampling-space-filling-strata 20 --sampling-max-space-filling-empty-strata-frac 0 --sampling-max-space-filling-duplicate-frac 0 --touchstone-all --touchstone-target-frequency-ghz 15 --touchstone-positive-window-start-ghz 5.0 --touchstone-positive-window-stop-ghz 30 --touchstone-shape-window-start-ghz 5.0 --touchstone-shape-window-stop-ghz 30 --touchstone-max-shape-spike-ratio 4 --touchstone-max-shape-relative-step 0.25 --extract-response-features --audit-response-feature-coverage --response-require-cm --response-min-valid-count 500 --audit-zin-coverage --zin-min-valid-count 500 --audit-zin-sweep-coverage --zin-sweep-frequency-slices-ghz 5,10,15,20,25,30,35,40,45,50 --zin-sweep-min-valid-count 500 --zin-sweep-min-entropy-frac 0.70 --select-hfss-samples --hfss-sample-count 8
