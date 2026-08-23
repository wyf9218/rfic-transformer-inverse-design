#!/usr/bin/env python3
"""Generate HFSS/PyAEDT build and solve scripts from selected S8P handoff data.

This script does not run HFSS. It converts a PASS handoff packet into a more
actionable HFSS automation packet:

- `hfss_s8p_build_payload.json` with exact GDS polygon/label/port evidence.
- `build_hfss_s8p_from_payload.py` for AEDT geometry/port/setup creation.
- `solve_export_hfss_s8p.py` for optional solve and Touchstone export.

The generated scripts still need to be executed in a Windows/HFSS environment.
Their output is not accepted evidence until the exported `.s8p` passes the
existing physical-feature and EMX-vs-HFSS comparison gates.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.process import ProcConductor, ProcFileInfo, parse_proc_file  # noqa: E402


PORT_NAMES = tuple(f"P{index:03d}" for index in range(1, 9))
TABLE_RSH_FALLBACK_OHM_PER_SQ = {
    # The real TSMC65 proc represents these as width-dependent tables. Use the
    # typical values near the transformer line widths instead of silently
    # omitting conductor conductivity in HFSS.
    "metal5": 0.0775,
    "metal10": 0.0106,
}
POWER_LINE_ROLE_ORDER = (
    "left_power_top",
    "left_power_bottom",
    "primary_top",
    "primary_bottom",
    "secondary_top",
    "secondary_bottom",
    "right_power_top",
    "right_power_bottom",
)
POWER_LINE_EXPECTED_LABELS = {
    "left_power_top": "P002",
    "left_power_bottom": "P003",
    "primary_top": "P001",
    "primary_bottom": "P004",
    "secondary_top": "P006",
    "secondary_bottom": "P005",
    "right_power_top": "P007",
    "right_power_bottom": "P008",
}
POWER_LINE_TOLERANCE_UM = 1.0e-9
POWER_LINE_EXPECTED_GROUND_FRAME_POLICY = (
    "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"
)
DEFAULT_HFSS_CALIBRATION_PROFILE = "diagnosis_v71_terminal_dual_local_air_best_measured"
HFSS_CALIBRATION_PROFILES = {
    "diagnosis_v69_direct_local_reference_width_locked": {
        "name": "diagnosis_v69_direct_local_reference_width_locked",
        "intent": (
            "Apply the latest EMX-vs-HFSS diagnosis to the generated HFSS model: "
            "keep the historically closest direct lumped-port/local-reference setup, "
            "force every 8-port power-line port sheet to the synchronized physical "
            "line_width_um instead of any wider Cadence pin footprint, keep the M5 "
            "shield finite, and preserve the final 5-60 GHz / 1.0 GHz sweep contract."
        ),
        "diagnosis_basis": [
            "Across existing real HFSS exports, the best K/Kw agreement came from the direct local-reference family; terminal-reference variants did not reduce the Lp/Ls/Q mismatch.",
            "The dominant remaining error is HFSS Lp/Ls being too small, while K can already be close; this points to self-inductance/geometry/reference equivalence rather than a Touchstone format issue.",
            "Old payloads allowed P001/P004/P005/P006 port sheets to use a 4um Cadence pin footprint while the physical synchronized line width was about 2.84um, so the final HFSS model must lock port-sheet width to the drawn conductor width.",
        ],
        "final_acceptance_gate": (
            "This is a diagnosed HFSS rebuild profile only. It is accepted only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "1",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "physical_line_width",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
            "HFSS_AIR_BELOW_UM": "80",
            "HFSS_AIR_ABOVE_UM": "1200",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v70_direct_local_reference_emx_port_footprint": {
        "name": "diagnosis_v70_direct_local_reference_emx_port_footprint",
        "intent": (
            "Keep the diagnosed direct lumped-port/local-reference HFSS setup and keep "
            "the physical M9/M10 coil, bridge, and power-line conductors on the synchronized "
            "line_width_um, but drive each lumped-port excitation sheet with the EMX/Cadence "
            "pin footprint when available. This isolates whether the remaining EMX-vs-HFSS "
            "error is dominated by excitation-port equivalence rather than physical trace width."
        ),
        "diagnosis_basis": [
            "The v69 physical-width-locked rebuild satisfies the final line-width rule but makes the selected legacy EMX sample much worse than historical direct/local-reference exports.",
            "Historical best K/Kw agreement used the wider Cadence pin footprint for some excitation sheets, which means the HFSS port sheet should be treated as a solver excitation aperture, not as the physical metal width.",
            "This profile preserves physical conductor geometry while testing the EMX/Cadence port footprint as the excitation definition.",
        ],
        "final_acceptance_gate": (
            "Diagnostic only until a real HFSS-exported .s8p passes the EMX-vs-HFSS "
            "Lp/Ls/Q/abs(K) comparison gate. If this improves K without fixing Lp/Ls/Q, "
            "the next root cause remains stack/ground-return/self-inductance calibration."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "1",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
            "HFSS_AIR_BELOW_UM": "80",
            "HFSS_AIR_ABOVE_UM": "1200",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v71_terminal_dual_local_air_best_measured": {
        "name": "diagnosis_v71_terminal_dual_local_air_best_measured",
        "intent": (
            "Replay the best measured EMX-vs-HFSS calibration family inside the current "
            "traceable packet flow: Terminal solution, PyAEDT terminal-reference lumped "
            "ports, every port referenced to all local M5 conductors that geometrically "
            "contain the *_G label, finite M5 shield, EMX/Cadence excitation footprint, "
            "and a local-air dielectric window. This tests the diagnosed regression from "
            "the v70 one-reference direct-port model back toward the historically closest "
            "v48 local-air terminal-reference result."
        ),
        "diagnosis_basis": [
            "v70 kept the correct S8P file contract but made Lp/Ls/K much smaller than EMX, so the failure is not Touchstone format or K sign.",
            "The best historical real HFSS export for this same sample was v48_sidecar_stack_local_air: Terminal reference ports, local_ground_bbox, finite M5, connected-by-bbox M9/M10, and no local dielectric boxes.",
            "v48 referenced each port to both the rectangular M5 frame segment and the local M5 pad under the *_G label; v70 forced only the smallest local M5 object and used a direct integration-line port.",
            "If v71 returns near the v48 error level, the dominant root cause is HFSS port/ground-return equivalence plus local dielectric environment, not geometry extraction or S8P export.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v72_terminal_frame_reference_skip_pin_local_air": {
        "name": "diagnosis_v72_terminal_frame_reference_skip_pin_local_air",
        "intent": (
            "Apply the v71 best-measured Terminal/reference-port setup while removing "
            "pin-purpose GDS rectangles from the solved conductor geometry. EMX pin "
            "rectangles define port apertures and labels, but overlapping pin-purpose "
            "metal in HFSS can perturb the local terminal fields. This profile keeps "
            "the physical M9/M10/M5 drawing conductors, uses the EMX/Cadence port "
            "footprint for excitation sheets, references each terminal to the local M5 "
            "frame segment under its *_G label, and preserves the 5-60 GHz / 1.0 GHz "
            "S8P export contract."
        ),
        "diagnosis_basis": [
            "v71 restored the historical best L/Q behavior but still left Lp/Ls/K below EMX, so the next change should isolate geometry/port-local effects without changing port order or formulas.",
            "The selected GDS contains small pin-purpose rectangles overlapping the drawing metal at every terminal; those are not a new transformer design variable and can be treated as port aperture evidence rather than solved conductor metal.",
            "Skipping pin-purpose conductors keeps the actual drawn windings, bridges, vertical power lines, and rectangular M5 shield frame while preventing duplicate coincident metal at the port pads.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "1",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v73_terminal_lowfreq_port_accuracy": {
        "name": "diagnosis_v73_terminal_lowfreq_port_accuracy",
        "intent": (
            "Apply the strongest current HFSS-side repair without changing the EMX "
            "geometry, GDS polygons, port order, or ADS-equivalent extraction: keep "
            "the v71 Terminal/PyAEDT reference-port/local-air setup, explicitly write "
            "the adaptive setup properties into AEDT, increase lumped-port accuracy, "
            "and enable HFSS EnhancedLowFreqAccuracy for the lumped-port-only terminal "
            "model. This tests whether the residual Lp/Ls/Q/abs(K) mismatch is partly "
            "caused by loose adaptive/port accuracy rather than geometry."
        ),
        "diagnosis_basis": [
            "v71 is the best measured full 8-port profile so far, while v72 showed pin-purpose metal overlap is not the main root cause.",
            "AEDT setup introspection on the v72 project showed MaxDeltaS/MaximumPasses followed the screening runner overrides, so the next HFSS packet must explicitly record the setup properties after update.",
            "The model uses only lumped terminal ports; HFSS exposes EnhancedLowFreqAccuracy and PortAccuracy setup properties for this case, so they should be tested before more geometry changes.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v74a_terminal_global_m5_reference": {
        "name": "diagnosis_v74a_terminal_global_m5_reference",
        "intent": (
            "Use the v73 solved-accuracy setup but change only the Terminal-port "
            "reference conductor scope from local M5 bbox references to every M5 "
            "shield/ground conductor in the HFSS model. This isolates whether the "
            "remaining Lp/Ls/Q/abs(K) mismatch is caused by HFSS local return-path "
            "definition versus EMX single_ended_shield_grounded ports."
        ),
        "diagnosis_basis": [
            "v73 improved Lp/Ls relative to v72 but Q and abs(K) remained far outside the 5% gate, so increasing solver/port accuracy alone is not the root cause.",
            "Geometry quality checks show signal traces and port apertures cross the M5 ground-frame projection; therefore the HFSS terminal reference conductor list can strongly affect return current and extracted coupling.",
            "EMX was generated with shield-grounded Cadence pins, so the next controlled variant should bind each Terminal port to the full M5 shield/ground set while keeping geometry, port order, frequency grid, and ADS-equivalent extraction unchanged.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "global_m5",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "0",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v74b_terminal_grounded_m5_shield": {
        "name": "diagnosis_v74b_terminal_grounded_m5_shield",
        "intent": (
            "Use the v74A global-M5 Terminal-reference setup and additionally assign "
            "the M5 shield/ground objects as an ideal PerfectE grounded shield in HFSS. "
            "This tests whether the EMX single_ended_shield_grounded port mode is "
            "better matched by explicitly grounding M5, rather than leaving M5 as a "
            "finite floating conductor that only appears in terminal reference lists."
        ),
        "diagnosis_basis": [
            "v74A produced numerically the same Lp/Ls/Q/abs(K) as v73, so merely expanding the terminal reference-conductor list does not change the HFSS solution.",
            "The selected layout contract treats the area outside the white window as M5 ground, and EMX was configured with shield-grounded single-ended ports.",
            "The next isolated variable is therefore the electrical boundary condition on the M5 shield itself: finite floating conductor versus explicit grounded PerfectE shield.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "perfecte",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "global_m5",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "0",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v75a_terminal_physical_port_width": {
        "name": "diagnosis_v75a_terminal_physical_port_width",
        "intent": (
            "Keep the v73 high-accuracy Terminal/local-M5 reference setup but force "
            "every HFSS lumped-port sheet width to the synchronized physical conductor "
            "line_width_um instead of wider EMX/Cadence pin footprints. This isolates "
            "whether excitation aperture width/calibration-plane equivalence is the "
            "dominant source of the remaining Lp/Ls/Q/abs(K) mismatch."
        ),
        "diagnosis_basis": [
            "v74A and v74B showed that changing M5 reference scope or explicitly grounding M5 does not fix the EMX-HFSS mismatch.",
            "The selected payload uses physical line_width_um about 2.84 um, while main transformer ports P001/P004/P005/P006 use 4.0 um EMX pin-footprint sheets in v73.",
            "The next controlled variable is therefore port excitation aperture width: use the actual conductor width while keeping geometry, port order, frequency grid, and extraction unchanged.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "physical_line_width",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v76a_terminal_full_pdk_dielectric_stack": {
        "name": "diagnosis_v76a_terminal_full_pdk_dielectric_stack",
        "intent": (
            "Use the v73 high-accuracy Terminal/local-M5 reference setup but remove "
            "the artificial local-air dielectric z-window so HFSS builds the full "
            "PDK dielectric stack carried in the payload. This isolates whether the "
            "remaining EMX-vs-HFSS Lp/Ls/Q/abs(K) mismatch is caused by solving the "
            "same metal geometry in an air-only local window instead of the EMX/PDK "
            "dielectric environment."
        ),
        "diagnosis_basis": [
            "v74A, v74B, and v75A showed that changing terminal reference scope, explicit M5 grounding, or port-sheet aperture width does not close the 15 GHz Lp/Ls/Q/abs(K) gap.",
            "The payload already carries the parsed PDK dielectric stack, but v71-v75 profiles force HFSS_DIELECTRIC_Z_MIN_UM and HFSS_DIELECTRIC_Z_MAX_UM to 700/700, which creates no dielectric bodies.",
            "The next controlled HFSS repair is therefore to keep the same geometry, port order, extraction, and setup accuracy while letting the generated model create all dielectric layers from stack_min to stack_max.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "loss_tangent",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v76b_terminal_backend_dielectric_stack_no_substrate": {
        "name": "diagnosis_v76b_terminal_backend_dielectric_stack_no_substrate",
        "intent": (
            "Use the v73 high-accuracy Terminal/local-M5 reference setup and include "
            "the PDK backend dielectric layers above the synthetic substrate, while "
            "excluding the 0-700um substrate slab that made the v76A full-stack HFSS "
            "solve too expensive. This keeps a traceable PDK dielectric environment "
            "around M5/M9/M10 without turning the validation model into a huge "
            "substrate-volume mesh."
        ),
        "diagnosis_basis": [
            "v76A confirmed the payload can build a full PDK dielectric stack, but the 700um synthetic substrate makes the solve impractically slow for iterative EMX-HFSS calibration.",
            "v71-v75 created no dielectric bodies because HFSS_DIELECTRIC_Z_MIN_UM and HFSS_DIELECTRIC_Z_MAX_UM were both forced to 700/700.",
            "The controlled compromise is to set only the lower dielectric window to 700um and let the upper bound default to stack_max, keeping backend dielectrics while omitting the thick substrate slab.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "loss_tangent",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v77a_terminal_solve_inside_thick_metal": {
        "name": "diagnosis_v77a_terminal_solve_inside_thick_metal",
        "intent": (
            "Use the v73 high-accuracy Terminal/local-M5 reference setup and change "
            "only the conductor treatment: solve inside the finite-thickness M5/M9/M10 "
            "metal volumes. This isolates whether the remaining high-Q and low-L/K "
            "HFSS bias is caused by the surface-current approximation rather than "
            "port order, Touchstone format, or dielectric stack."
        ),
        "diagnosis_basis": [
            "v74A/v74B/v75A excluded terminal reference scope, explicit M5 grounding, and port sheet width as dominant root causes.",
            "v76A/v76B showed that adding full/backend dielectric stack is physically relevant but too expensive for the current automated validation loop.",
            "HFSS Q remains much higher than EMX while Lp/Ls/abs(K) are lower, so the next low-cost HFSS-side variable is finite-conductor volume current distribution with Solve Inside enabled.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v78a_terminal_midplane_port_calibration": {
        "name": "diagnosis_v78a_terminal_midplane_port_calibration",
        "intent": (
            "Use the v73 high-accuracy Terminal/local-M5 reference setup and change "
            "only the port calibration/integration-line z planes: place both signal "
            "and ground port endpoints at the center plane of their finite-thickness "
            "conductors instead of the payload bottom/top convention. This isolates "
            "whether the residual Lp/Ls/abs(K) mismatch is caused by HFSS port z-plane "
            "definition rather than metal geometry, port order, or Touchstone export."
        ),
        "diagnosis_basis": [
            "v75A showed port sheet width does not explain the mismatch.",
            "v77A showed finite-conductor Solve Inside improves Q and L slightly but worsens abs(K), so the next low-cost variable should target port calibration geometry rather than loss.",
            "The current payload puts signal ports at M9/M10 bottom and ground ports at M5 top; using conductor mid-planes is a standard finite-thickness calibration-plane check.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "mid",
            "HFSS_PORT_GROUND_Z_MODE": "mid",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v78b_terminal_signal_top_port_surface": {
        "name": "diagnosis_v78b_terminal_signal_top_port_surface",
        "intent": (
            "Use the v73 high-accuracy Terminal/local-M5 reference setup and move "
            "only the signal-side port endpoint from the M9/M10 bottom surface to "
            "the M9/M10 top surface, while keeping the ground endpoint on the M5 top "
            "surface. This is a surface-only follow-up to v78A, which tested mid-plane "
            "endpoints and failed HFSS solve."
        ),
        "diagnosis_basis": [
            "v78A mid-plane endpoints failed to solve, indicating HFSS lumped terminal ports cannot be defined with integration-line endpoints buried inside conductor volumes.",
            "The current payload-valid baseline uses signal bottom and ground top surfaces. The next controlled check is whether a signal top-surface endpoint is legal and materially changes L/K.",
            "If this also fails, port z-plane repair must be implemented as a geometric port-sheet/calibration-plane change, not as a simple endpoint z-mode switch.",
        ],
        "final_acceptance_gate": (
            "Diagnostic/repair candidate only. It becomes reportable only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "top",
            "HFSS_PORT_GROUND_Z_MODE": "top",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v79a_terminal_edge_contact_port_repair": {
        "name": "diagnosis_v79a_terminal_edge_contact_port_repair",
        "intent": (
            "Use the v78A/v78B failure diagnosis to repair the HFSS lumped-port "
            "geometry itself: keep the legal signal-bottom to M5-top z span, but move "
            "the port sheet and integration line from the conductor center to the "
            "outward signal-metal edge. Combine this with v77 finite-volume conductor "
            "solving because v77 substantially reduced the Q mismatch."
        ),
        "diagnosis_basis": [
            "v78A mid-plane endpoints failed because HFSS does not accept lumped-port endpoints buried inside conductor volumes.",
            "v78B signal-top endpoints built ports but failed to solve, consistent with a port sheet crossing the signal metal thickness.",
            "Therefore the repair should not move the endpoint into/top-through metal; it should keep the endpoint on the signal bottom surface and move the sheet laterally to the outward metal edge.",
            "v77 Solve Inside improved Q error from about 40% to about 19%, so finite conductor volume is retained for this repair candidate.",
        ],
        "final_acceptance_gate": (
            "Repair candidate only. It is accepted only after a real HFSS-exported "
            ".s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "edge_contact",
            "HFSS_PORT_EDGE_EPS_UM": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v80a_foundry_proc_solve_inside_repair": {
        "name": "diagnosis_v80a_foundry_proc_solve_inside_repair",
        "intent": (
            "Use the actual foundry `.proc` file at packet generation time and keep "
            "the v77 finite-volume conductor repair. v79A showed that changing port "
            "sheet center/edge placement did not move the 15 GHz Lp/Ls/Q/abs(K) "
            "errors, while the generated packet still referenced the synthetic "
            "default example proc file."
        ),
        "diagnosis_basis": [
            "The repository default proc explicitly states it is synthetic and not a foundry process file.",
            "v77/v79 errors are systematic: HFSS Lp, Ls, and K remain low relative to EMX even after port/reference repairs.",
            "A non-foundry stack is therefore a higher-probability root cause than port order, Touchstone format, K sign, or endpoint z toggles.",
            "This profile must be generated with --proc-file pointing at the real foundry RC_IRCX...typical.proc file.",
        ],
        "final_acceptance_gate": (
            "Repair candidate only. It is accepted only after a real HFSS-exported "
            ".s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "700",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v81a_foundry_backend_dielectric_compact": {
        "name": "diagnosis_v81a_foundry_backend_dielectric_compact",
        "intent": (
            "Use the actual foundry `.proc` file and add a compact backend dielectric "
            "window above the M5 shield, while excluding the 700um lossy substrate. "
            "This tests the remaining high-probability physical non-equivalence after "
            "v77/v79/v80 showed that port geometry and metal conductivity alone do "
            "not close the Lp/Ls/Q/abs(K) gap."
        ),
        "diagnosis_basis": [
            "v79A edge-contact ports produced nearly the same 15 GHz errors as v77, so port center/edge placement is not the dominant root cause.",
            "v80A real foundry metal conductivity worsened Q and did not materially improve L/K, so conductor resistance alone is not the dominant root cause.",
            "v73-v80 still force HFSS_DIELECTRIC_Z_MIN_UM=700 and HFSS_DIELECTRIC_Z_MAX_UM=700, creating no dielectric bodies in the model.",
            "The next controlled repair is to include only backend dielectrics near the transformer to avoid the very expensive full-substrate model.",
        ],
        "final_acceptance_gate": (
            "Repair candidate only. It is accepted only after a real HFSS-exported "
            ".s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v82a_foundry_effective_backend_dielectric_compact": {
        "name": "diagnosis_v82a_foundry_effective_backend_dielectric_compact",
        "intent": (
            "Use the actual foundry `.proc` file, keep the v77/v79 terminal "
            "reference and solve-inside conductor settings, and replace the v81 "
            "many-layer backend dielectric model with a small number of "
            "metal-gap-weighted effective dielectric slabs. This is the direct HFSS "
            "repair from the v81 out-of-memory diagnosis."
        ),
        "diagnosis_basis": [
            "v79A edge-contact ports produced almost the same errors as v77, so port center/edge placement is not the dominant root cause.",
            "v80A real foundry conductor conductivity did not close L/K and worsened Q, so metal conductivity alone is not the dominant root cause.",
            "v81A added the physically relevant backend dielectric window but HFSS failed during adaptive solve with out-of-memory, so the dielectric environment must be represented with fewer bodies before further comparison.",
            "The effective dielectric slabs are bounded by the M9/M10 metal gaps and use thickness-weighted epsilon from the actual foundry proc layers; this preserves the vertical capacitance environment without creating all thin dielectric boxes.",
        ],
        "final_acceptance_gate": (
            "Repair candidate only. It is accepted only after a real HFSS-exported "
            ".s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "metal_gap_weighted",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.005",
            "HFSS_SETUP_MAX_PASSES": "20",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v83a_foundry_effective_dielectric_exportable_delta03": {
        "name": "diagnosis_v83a_foundry_effective_dielectric_exportable_delta03",
        "intent": (
            "Use the same physical modeling repair as v82A, but stop HFSS before the "
            "out-of-memory adaptive pass observed in the real v82A run. The v82A "
            "profile reached Max Mag. Delta S=0.025559 at pass 10, then pass 11 "
            "required about 55 GB RAM on a 16 GB machine and produced no .s8p. "
            "This profile sets MaxDeltaS=0.03, one converged pass, and max 10 passes "
            "so a real diagnostic .s8p can be exported for EMX-vs-HFSS curve trend "
            "checking. It is not the final 5% acceptance setup."
        ),
        "diagnosis_basis": [
            "v82A created the effective backend dielectric model successfully and assigned all 8 terminal-reference ports.",
            "v82A failed only during adaptive pass 11: the HFSS profile reports Direct solver requires approximately 55 GB memory, out of memory.",
            "The same v82A run had already reached Max Mag. Delta S=0.025559 at pass 10, so a 0.03 diagnostic convergence gate should stop before the OOM pass and allow Touchstone export.",
            "This produces a traceable diagnostic .s8p to check whether effective backend dielectrics move Lp/Ls/abs(K) in the right direction before spending effort on a higher-memory final solve.",
        ],
        "final_acceptance_gate": (
            "Diagnostic export candidate only. It may be used for trend analysis, but "
            "a final reportable HFSS-vs-EMX validation still requires the stricter "
            "physical-feature error gate and a documented convergence setting."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "metal_gap_weighted",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.03",
            "HFSS_SETUP_MAX_PASSES": "10",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v84a_foundry_effective_dielectric_surface_metal_screen": {
        "name": "diagnosis_v84a_foundry_effective_dielectric_surface_metal_screen",
        "intent": (
            "Screen the v82/v83 effective-backend-dielectric repair without the 55 GB "
            "finite-volume metal solve burden. This keeps the same 8-port Terminal "
            "reference geometry, foundry-derived effective dielectric gaps, and EMX "
            "pin-footprint excitation sheets, but returns conductors to the lighter "
            "surface/finite-conductor treatment used by the earlier successful exports. "
            "It is intended for a narrow diagnostic frequency grid first; final reportable "
            "validation still requires the production 5-60 GHz / 1.0 GHz export."
        ),
        "diagnosis_basis": [
            "v79/v80 proved the 8-port S8P export path and terminal ordering are valid, but the physical metrics remain far from EMX.",
            "v82/v83 proved the effective backend dielectric model can be built and assigned to the same 8 ports, but finite-volume conductor solve plus full frequency sweep is too expensive on the current 16 GB Windows VM.",
            "Changing only the conductor solve burden while keeping the effective dielectric repair lets us quickly test whether dielectric modeling moves Lp/Ls/abs(K) in the right direction before spending higher-memory HFSS time.",
        ],
        "final_acceptance_gate": (
            "Diagnostic screening only. It is not final evidence unless rerun on the "
            "production 5-60 GHz grid and the exported .s8p passes the EMX-vs-HFSS "
            "physical-feature comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "metal_gap_weighted",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.08",
            "HFSS_SETUP_MAX_PASSES": "6",
            "HFSS_SETUP_MIN_PASSES": "2",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "30",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SETUP_PORT_ACCURACY": "2",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v96a_powerline_frame_reference_screen": {
        "name": "diagnosis_v96a_powerline_frame_reference_screen",
        "intent": (
            "Keep the v84/v92 foundry-effective Terminal-port setup, but split the "
            "M5 reference-conductor rule by port role. Main transformer ports still "
            "use all local M5 conductors containing each *_G label, while the four "
            "power-line/center-tap ports are referenced only to the larger M5 frame "
            "segment. This directly tests the latest diagnosis that the remaining "
            "EMX-vs-HFSS error is dominated by center-tap/power-line termination and "
            "M5 return-path equivalence rather than by Touchstone ordering."
        ),
        "diagnosis_basis": [
            "EMX and HFSS S8P headers both show P001...P008 ordering, so the current failure is not a simple header permutation.",
            "Port-pairing sensitivity showed that nonphysical use of power-line ports can make the 15 GHz marker look close while the full 5-60 GHz curve fails; therefore those ports must be diagnosed explicitly, not hidden in plotting.",
            "PyAEDT/HFSS Terminal lumped ports use explicit ReferenceConductors, so the reference conductor selected for P002/P003/P007/P008 is a first-order physical variable.",
            "Using the larger M5 frame for center-tap/power-line terminals tests whether the previous local-pad plus frame reference over-constrained the unused short-to-ground ports.",
        ],
        "final_acceptance_gate": (
            "Diagnostic screening only. It is not final evidence unless the real "
            "HFSS-exported .s8p passes Lp/Ls/Q/Kw on the physical P001-P004 vs "
            "P005-P006 extraction over the required 5-60 GHz grid."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "metal_gap_weighted",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_POWER_LINE_PORT_REFERENCE_MODE": "local_ground_bbox_largest",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.08",
            "HFSS_SETUP_MAX_PASSES": "6",
            "HFSS_SETUP_MIN_PASSES": "2",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "30",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SETUP_PORT_ACCURACY": "2",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v96b_connected_m5_ground_net_screen": {
        "name": "diagnosis_v96b_connected_m5_ground_net_screen",
        "intent": (
            "Keep the v84/v92 foundry-effective Terminal-port setup, but merge "
            "touching M5 shield/pin conductors into connected ground-net components "
            "before assigning Terminal port reference conductors. This is the follow-up "
            "to v96a: AEDT rejected a frame-only reference on P002 because the port "
            "sheet saw excess terminals, which means the M5 local pad and frame are "
            "not independent for that terminal aperture."
        ),
        "diagnosis_basis": [
            "v96a failed at HFSS build time with an AEDT excess-terminal error on P002 when only the larger M5 frame segment was selected as the reference conductor.",
            "That failure is evidence that the power-line port sheet intersects more than one M5 ground conductor, so selecting only one physical piece is not a valid Terminal port definition.",
            "EMX single_ended_shield_grounded is a ground-net condition, so a connected M5 ground net is a more physically defensible test than choosing a single frame or pad piece by area.",
            "This profile keeps the same 8-port S8P contract and only changes the M5 reference-net connectivity model.",
        ],
        "final_acceptance_gate": (
            "Diagnostic screening only. It is not final evidence unless the real "
            "HFSS-exported .s8p passes Lp/Ls/Q/Kw on the physical P001-P004 vs "
            "P005-P006 extraction over the required 5-60 GHz grid."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "metal_gap_weighted",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "1",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "label_center",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.08",
            "HFSS_SETUP_MAX_PASSES": "6",
            "HFSS_SETUP_MIN_PASSES": "2",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "30",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SETUP_PORT_ACCURACY": "2",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v97a_edge_connected_m5_backend_dielectric": {
        "name": "diagnosis_v97a_edge_connected_m5_backend_dielectric",
        "intent": (
            "Combine the physically defensible parts of the latest real HFSS runs: "
            "v79 edge-contact Terminal lumped ports and finite-volume conductor solve, "
            "v96 connected M5 ground-net reference conductors, and the v82/v92 "
            "foundry-derived backend effective dielectric environment. The root-cause "
            "test is whether adding the missing capacitive environment and ground-net "
            "equivalence moves the HFSS self-resonance from about 49 GHz toward the "
            "EMX pre-resonance cutoff near 24 GHz without breaking the 8-port .s8p contract."
        ),
        "diagnosis_basis": [
            "The current report-facing v79 export has a valid .s8p and a reasonable Q marker, but its first invalid/resonant point is about 49.5 GHz while the EMX sample becomes invalid near 24.0 GHz.",
            "v79 keeps Lp/Ls/Q closer than the dielectric-only variants, but K is about 25% low at 15 GHz, so the port/reference repair alone is incomplete.",
            "v90-v93 effective-backend-dielectric exports pull the HFSS resonance down to about 37-38 GHz and make K much closer, showing that missing capacitance/ground environment is a real contributor.",
            "v96b shows the connected M5 ground-net reference can make K agree within about 2% on the 15 GHz screen, although L/Q remain too small, so the next controlled profile should combine connected M5 with the v79 edge-contact/solve-inside path and backend dielectric model.",
        ],
        "final_acceptance_gate": (
            "Repair candidate only. It is accepted only after a real HFSS-exported "
            ".s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate and the "
            "5-60 GHz / 1.0 GHz Touchstone contract."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "metal_gap_weighted",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "1",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "edge_contact",
            "HFSS_PORT_EDGE_EPS_UM": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "703.416",
            "HFSS_SETUP_MAX_DELTA_S": "0.03",
            "HFSS_SETUP_MAX_PASSES": "10",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v98a_strict_proc_explicit_stack": {
        "name": "diagnosis_v98a_strict_proc_explicit_stack",
        "intent": (
            "Use the foundry process stack as the first-principles HFSS reference "
            "instead of the previous backend effective-dielectric approximation. "
            "This profile keeps the v97 edge-contact 8-port Terminal setup and "
            "connected M5 shield-ground reference, but creates the explicit PDK "
            "dielectric layers from the parsed .proc/PDF stack and removes only the "
            "actual metal volumes from those dielectric bodies."
        ),
        "diagnosis_basis": [
            "The v79 real export gave a valid .s8p but the HFSS first resonance remained near 49.5 GHz while EMX became invalid near 24 GHz, pointing to missing capacitance/ground environment rather than a Touchstone format issue.",
            "The v97 repair packet still used HFSS_DIELECTRIC_EFFECTIVE_MODE=metal_gap_weighted, so it was a controlled approximation, not a strict reproduction of the process cross-section.",
            "Earlier explicit-stack attempts removed whole metal z-slabs from the dielectric volume; that under-represents sidewall and in-plane dielectric loading around patterned M9/M10 conductors.",
            "For the reportable validation path, EMX and HFSS must use the same .proc-derived conductor heights, conductivities, dielectric epsilons, and 5-60 GHz / 1.0 GHz .s8p contract.",
        ],
        "final_acceptance_gate": (
            "Strict repair candidate only. It is accepted only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison "
            "gate over the 5-60 GHz / 1.0 GHz grid."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "conductivity",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "explicit_layers",
            "HFSS_DIELECTRIC_METAL_CAVITY_MODE": "subtract_actual_metals",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "1",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "edge_contact",
            "HFSS_PORT_EDGE_EPS_UM": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "0",
            "HFSS_DIELECTRIC_Z_MAX_UM": "718.643",
            "HFSS_SETUP_MAX_DELTA_S": "0.03",
            "HFSS_SETUP_MAX_PASSES": "10",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v98b_strict_proc_surface_conductor": {
        "name": "diagnosis_v98b_strict_proc_surface_conductor",
        "intent": (
            "Use the same explicit foundry process stack and actual-metal dielectric "
            "subtraction as v98a, but model high-conductivity M5/M9/M10 solids with "
            "SolveInside disabled. This is the practical HFSS repair path after v98a "
            "hit the AEDT warning that solving inside high-conductivity solids can "
            "require an impractically large mesh."
        ),
        "diagnosis_basis": [
            "v98a successfully built the true-process AEDT model, but the real solve stalled at mesh setup with repeated high-conductivity SolveInside warnings.",
            "The main physics correction relative to v97 is the explicit PDK dielectric stack plus subtract_actual_metals, not the expensive conductor-volume meshing itself.",
            "Disabling SolveInside keeps conductor material assignment and finite conductor boundaries while avoiding a volume mesh inside very conductive metals.",
            "This profile is accepted only if the exported .s8p passes the same 8-port, 5-60 GHz / 1.0 GHz, Lp/Ls/Q/abs(K) comparison gate.",
        ],
        "final_acceptance_gate": (
            "Strict-process practical repair candidate only. It is accepted only after "
            "a real HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) "
            "comparison gate over the 5-60 GHz / 1.0 GHz grid."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "conductivity",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "explicit_layers",
            "HFSS_DIELECTRIC_METAL_CAVITY_MODE": "subtract_actual_metals",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "1",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "edge_contact",
            "HFSS_PORT_EDGE_EPS_UM": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "0",
            "HFSS_DIELECTRIC_Z_MAX_UM": "718.643",
            "HFSS_SETUP_MAX_DELTA_S": "0.03",
            "HFSS_SETUP_MAX_PASSES": "10",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v98c_backend_proc_surface_conductor": {
        "name": "diagnosis_v98c_backend_proc_surface_conductor",
        "intent": (
            "Use the same v98 explicit-process dielectric model above the substrate "
            "surface while omitting the 700 um substrate volume that made the full "
            "0-718.643 um solve impractical in the local Windows VM. This keeps the "
            "foundry backend stack, actual-metal dielectric subtraction, 8-port "
            "terminal setup, and 5-60 GHz .s8p contract for a fast EMX/HFSS "
            "root-cause validation."
        ),
        "diagnosis_basis": [
            "v98a built correctly but stalled because high-conductivity SolveInside produced an impractically large mesh.",
            "v98b disabled SolveInside but still stalled before producing result files, pointing to the full 700 um substrate volume as the next dominant mesh cost.",
            "The M5 ground shield sits above the substrate; a backend-only explicit stack is a controlled approximation for isolating whether the v97 effective-dielectric model was the main source of HFSS/EMX mismatch.",
            "This profile is not the final full-substrate signoff unless its result is later confirmed by a full stack run; it is a practical calibration candidate.",
        ],
        "final_acceptance_gate": (
            "Backend-stack repair candidate only. It is accepted for reporting only "
            "as a diagnosed approximation unless a later full-substrate strict run "
            "confirms the same conclusion. Numeric acceptance still requires a real "
            ".s8p and the EMX-vs-HFSS Lp/Ls/Q/abs(K) gate over 5-60 GHz."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "conductivity",
            "HFSS_DIELECTRIC_EFFECTIVE_MODE": "explicit_layers",
            "HFSS_DIELECTRIC_METAL_CAVITY_MODE": "subtract_actual_metals",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "1",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "0",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_GEOMETRY_MODE": "edge_contact",
            "HFSS_PORT_EDGE_EPS_UM": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_PORT_SHEET_WIDTH_MODE": "emx_pin_footprint",
            "HFSS_AIR_MARGIN_UM": "250",
            "HFSS_RADIATION_MARGIN_UM": "350",
            "HFSS_AIR_BELOW_UM": "50",
            "HFSS_AIR_ABOVE_UM": "950",
            "HFSS_DIELECTRIC_XY_MARGIN_UM": "80",
            "HFSS_DIELECTRIC_Z_MIN_UM": "700",
            "HFSS_DIELECTRIC_Z_MAX_UM": "718.643",
            "HFSS_SETUP_MAX_DELTA_S": "0.03",
            "HFSS_SETUP_MAX_PASSES": "10",
            "HFSS_SETUP_MIN_PASSES": "4",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "1",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
            "HFSS_SETUP_PORT_ACCURACY": "3",
            "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY": "1",
            "HFSS_SWEEP_TYPE": "Interpolating",
        },
    },
    "diagnosis_v68_terminal_local_ground_reference": {
        "name": "diagnosis_v68_terminal_local_ground_reference",
        "intent": (
            "Apply the current EMX-vs-HFSS diagnosis to the generated HFSS model: "
            "use HFSS Terminal solution with PyAEDT terminal-reference ports, bind each "
            "port to the smallest local M5 ground conductor under its *_G label, keep "
            "the M5 shield finite, and keep the final 5-60 GHz / 1.0 GHz sweep."
        ),
        "diagnosis_basis": [
            "The existing exported HFSS .s8p is syntactically valid but Lp/Ls/Q/abs(K) are all much lower than EMX, so the failure is not a file-format issue.",
            "The old build used direct integration-line lumped ports and often found both the large M5 frame and a local M5 island for one ground label; that can over-broaden the HFSS reference path.",
            "EMX was generated with single_ended_shield_grounded pins, so the next HFSS attempt must make the local shield-ground reference explicit instead of relying on a broad/global M5 fallback.",
        ],
        "final_acceptance_gate": (
            "This is a diagnosed HFSS rebuild profile only. It is accepted only after a real "
            "HFSS-exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) comparison gate."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
            "HFSS_PORT_REFERENCE_EXPECTED_COUNT": "1",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
            "HFSS_AIR_BELOW_UM": "80",
            "HFSS_AIR_ABOVE_UM": "1200",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnosis_v67_lp_ls_default": {
        "name": "diagnosis_v67_lp_ls_default",
        "intent": (
            "Use the current EMX-vs-HFSS diagnosis as the generated-script default: "
            "keep M5 finite, reference each port to its local ground frame, enlarge the "
            "air/radiation box, use lossless dielectric first for Lp/Ls calibration, and "
            "tighten the adaptive setup."
        ),
        "diagnosis_basis": [
            "HFSS Lp/Ls are systematically low while K is close enough to rule out a pure port-order issue.",
            "All-M5/global-reference variants and ideal M5 grounding were not the best direction in the current sweep.",
            "The next useful HFSS change is a stack/reference/mesh calibration, not another display-only K sign change.",
        ],
        "final_acceptance_gate": (
            "This profile is a diagnosed HFSS build default only. The result is accepted "
            "only after a real exported .s8p passes the EMX-vs-HFSS Lp/Ls/Q/abs(K) gates."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
            "HFSS_AIR_BELOW_UM": "80",
            "HFSS_AIR_ABOVE_UM": "1200",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    "diagnostic_m5_perfecte_reference_check": {
        "name": "diagnostic_m5_perfecte_reference_check",
        "intent": (
            "A diagnostic-only stress test for the M5 reference assumption. It grounds M5 "
            "as an ideal PerfectE shield while keeping the same local-port treatment."
        ),
        "diagnosis_basis": [
            "Run only when isolating whether finite-M5 shield loss/reference is the dominant Lp/Ls bias.",
            "Do not use as the default final geometry unless it improves Lp/Ls without breaking K and Q.",
        ],
        "final_acceptance_gate": (
            "Diagnostic only. It must still pass exported .s8p physical-feature comparison before being reportable."
        ),
        "env_defaults": {
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "0",
            "HFSS_M5_SHIELD_BOUNDARY": "perfecte",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_BY_METAL": "0",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_SKIP_PIN_CONDUCTORS": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_PORT_MODE_RENORM_IMP": "0",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
            "HFSS_AIR_BELOW_UM": "80",
            "HFSS_AIR_ABOVE_UM": "1200",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SETUP_BASIS_ORDER": "1",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
}


@dataclass(frozen=True)
class Check:
    sample: str
    evaluation: str
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "sample": self.sample,
            "evaluation": self.evaluation,
            "status": self.status,
            "name": self.name,
            "detail": self.detail,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    handoff_summary_path = Path(args.handoff_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    proc_file = Path(args.proc_file).expanduser().resolve()
    handoff = _read_json(handoff_summary_path)
    proc_info = parse_proc_file(proc_file)
    grid = _frequency_grid(args)
    if args.allow_diagnostic_frequency_grid:
        grid_errors = _diagnostic_grid_errors(grid)
        grid_check_name = "frequency grid is explicit diagnostic grid"
    else:
        grid_errors = _production_grid_errors(grid)
        grid_check_name = "frequency grid is 5-60 GHz / 1.0 GHz / 56 points"
    formula_trace_path = _resolve_handoff_artifact(handoff, handoff_summary_path, "ads_formula_trace")

    global_checks = [
        _check("", "", "handoff summary exists", handoff_summary_path.is_file(), str(handoff_summary_path)),
        _check("", "", "handoff summary already passed", handoff.get("overall_status") == "PASS", str(handoff.get("overall_status"))),
        _check("", "", "handoff ADS/Python formula trace exists", formula_trace_path is not None and formula_trace_path.is_file(), "" if formula_trace_path is None else str(formula_trace_path)),
        _check("", "", "proc file exists", proc_file.is_file(), str(proc_file)),
        _check("", "", grid_check_name, not grid_errors, "; ".join(grid_errors) or json.dumps(grid)),
    ]

    sample_results = [
        _build_sample_scripts(sample, index, out_dir, proc_info, proc_file, grid, formula_trace_path, args)
        for index, sample in enumerate(handoff.get("sample_results") or [], start=1)
        if args.include_failed_handoff_samples or sample.get("overall_status") == "PASS"
    ]
    sample_check_objects = [check for result in sample_results for check in result.pop("_check_objects", [])]
    all_checks = global_checks + sample_check_objects
    fail_count = sum(1 for result in sample_results if result["overall_status"] == "FAIL")
    overall_status = "FAIL" if any(check.status == "FAIL" for check in all_checks) or fail_count else "PASS"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "HFSS_S8P_AEDT_SCRIPTS_READY_FOR_WINDOWS_REVIEW"
        if overall_status == "PASS"
        else "DO_NOT_RUN_GENERATED_HFSS_SCRIPTS_UNTIL_PACKET_PASSES",
        "handoff_summary": str(handoff_summary_path),
        "proc_file": str(proc_file),
        "out_dir": str(out_dir),
        "frequency_grid": grid,
        "frequency_grid_purpose": "diagnostic" if args.allow_diagnostic_frequency_grid else "production",
        "selected_count": len(sample_results),
        "pass_count": sum(1 for result in sample_results if result["overall_status"] == "PASS"),
        "fail_count": fail_count,
        "sample_results": sample_results,
        "checks": [check.as_dict() for check in all_checks],
        "limitations": [
            "This script generates HFSS/PyAEDT scripts only; it does not run HFSS or export a real `.s8p`.",
            "The generated build script uses exact GDS polygons and port/ground labels from the selected sample handoff.",
            "Final reportable validation still requires executing the generated scripts in HFSS, exporting `.s8p`, and passing EMX-vs-HFSS Lp/Ls/Q/K comparison gates.",
            "Diagnostic frequency grids are for fast root-cause screening only and cannot replace the final 5-60 GHz / 1.0 GHz / 56-point validation gate.",
        ],
    }

    summary_path = out_dir / "hfss_s8p_aedt_script_packet_summary.json"
    report_path = out_dir / "hfss_s8p_aedt_script_packet_report.md"
    checks_csv = out_dir / "hfss_s8p_aedt_script_packet_checks.csv"
    commands_path = out_dir / "run_generated_hfss_s8p_scripts.commands.ps1"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(checks_csv, all_checks)
    commands_path.write_text(_render_windows_commands(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"commands={commands_path}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-summary", required=True, help="selected_s8p_hfss_handoff_summary.json")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--proc-file",
        default=str(Path(__file__).resolve().parents[1] / "rfic_transformer_inverse_design" / "process" / "assets" / "proc" / "default_typical.proc"),
    )
    parser.add_argument("--setup-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument(
        "--allow-diagnostic-frequency-grid",
        action="store_true",
        help="Allow non-production grids such as 15.0-15.5 GHz two-point sweeps for HFSS root-cause screening. Final validation still requires the default production grid.",
    )
    parser.add_argument("--hfss-version", default="2025.1")
    parser.add_argument(
        "--hfss-solution-type",
        default="Terminal",
        choices=("Terminal", "Modal"),
        help="HFSS solution type. Terminal is the default because EMX ports are explicit Pxxx:Pxxx_G single-ended pins.",
    )
    parser.add_argument(
        "--hfss-calibration-profile",
        default=DEFAULT_HFSS_CALIBRATION_PROFILE,
        choices=tuple(sorted(HFSS_CALIBRATION_PROFILES)),
        help=(
            "Diagnosed HFSS build defaults embedded into the generated payload and scripts. "
            "Environment variables can still override individual values at run time."
        ),
    )
    parser.add_argument("--include-failed-handoff-samples", action="store_true")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _build_sample_scripts(
    sample: dict[str, Any],
    index: int,
    out_dir: Path,
    proc_info: ProcFileInfo,
    proc_file: Path,
    grid: dict[str, Any],
    formula_trace_path: Path | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if args.max_samples is not None and index > int(args.max_samples):
        return {
            "selection_rank": sample.get("selection_rank", str(index)),
            "evaluation": sample.get("evaluation", ""),
            "overall_status": "SKIPPED",
            "_check_objects": [],
        }
    evaluation = str(sample.get("evaluation") or f"sample_{index:02d}")
    sample_rank = str(sample.get("selection_rank") or index)
    sample_dir = Path(str(sample.get("handoff_sample_dir") or "")).expanduser()
    manifest_path = sample_dir / "sample_handoff_manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    copied = manifest.get("copied_artifacts", {}) if isinstance(manifest.get("copied_artifacts"), dict) else {}
    gds_path = _resolve_first_existing(
        copied.get("gds"),
        manifest.get("gds_path"),
        sample.get("gds_path"),
    )
    layout_json_path = _resolve_first_existing(
        copied.get("layout_json"),
        manifest.get("layout_json_path"),
        sample.get("layout_json_path"),
    )
    power_line_path = _resolve_first_existing(
        copied.get("power_line_8port_geometry_json"),
        manifest.get("power_line_8port_geometry_json_path"),
        sample.get("power_line_8port_geometry_json_path"),
    )
    emx_s8p_path = _resolve_first_existing(
        copied.get("emx_s8p"),
        manifest.get("touchstone_path"),
        sample.get("touchstone_path"),
    )

    checks = [
        _check(sample_rank, evaluation, "handoff sample status PASS", sample.get("overall_status") == "PASS", str(sample.get("overall_status"))),
        _check(sample_rank, evaluation, "sample handoff manifest exists", manifest_path.is_file(), str(manifest_path)),
        _check(sample_rank, evaluation, "GDS exists for HFSS geometry extraction", gds_path is not None and gds_path.is_file(), "" if gds_path is None else str(gds_path)),
        _check(sample_rank, evaluation, "layout JSON exists", layout_json_path is not None and layout_json_path.is_file(), "" if layout_json_path is None else str(layout_json_path)),
        _check(sample_rank, evaluation, "power-line geometry JSON exists", power_line_path is not None and power_line_path.is_file(), "" if power_line_path is None else str(power_line_path)),
        _check(sample_rank, evaluation, "EMX S8P exists", emx_s8p_path is not None and emx_s8p_path.is_file() and emx_s8p_path.suffix.lower() == ".s8p", "" if emx_s8p_path is None else str(emx_s8p_path)),
    ]
    gds_info: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    if gds_path is not None and gds_path.is_file():
        try:
            gds_info = _extract_gds(gds_path, proc_info)
            label_names = set(gds_info.get("labels", {}))
            polygon_count = len(gds_info.get("conductor_polygons", []))
            checks.extend(
                [
                    _check(sample_rank, evaluation, "GDS labels include P001-P008", all(name in label_names for name in PORT_NAMES), ",".join(sorted(label_names))),
                    _check(sample_rank, evaluation, "GDS labels include P001_G-P008_G", all(f"{name}_G" in label_names for name in PORT_NAMES), ",".join(sorted(label_names))),
                    _check(sample_rank, evaluation, "GDS conductor polygons extracted", polygon_count > 0, f"conductor_polygons={polygon_count}"),
                ]
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(_check(sample_rank, evaluation, "GDS extraction", False, f"{type(exc).__name__}: {exc}"))
    if gds_info:
        power_line = _read_json(power_line_path) if power_line_path is not None and power_line_path.is_file() else {}
        layout_manifest = _read_json(layout_json_path) if layout_json_path is not None and layout_json_path.is_file() else {}
        payload = _build_payload(
            sample=sample,
            manifest=manifest,
            gds_info=gds_info,
            proc_info=proc_info,
            proc_file=proc_file,
            power_line=power_line,
            layout_manifest=layout_manifest,
            emx_s8p_path=emx_s8p_path,
            layout_json_path=layout_json_path,
            power_line_path=power_line_path,
            formula_trace_path=formula_trace_path,
            grid=grid,
            hfss_version=str(args.hfss_version),
            hfss_solution_type=str(args.hfss_solution_type),
            hfss_calibration_profile=str(args.hfss_calibration_profile),
        )
        checks.extend(
            _payload_contract_checks(
                payload,
                sample_rank,
                evaluation,
                expected_frequency_points=int(grid["expected_points"]),
                diagnostic_frequency_grid=bool(args.allow_diagnostic_frequency_grid),
            )
        )

    status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    script_dir = out_dir / "samples" / f"{index:02d}_{_slug(evaluation)}"
    build_script = script_dir / "build_hfss_s8p_from_payload.py"
    solve_script = script_dir / "solve_export_hfss_s8p.py"
    payload_json = script_dir / "hfss_s8p_build_payload.json"
    sample_report = script_dir / "hfss_s8p_script_packet_README.md"
    if payload and status == "PASS":
        script_dir.mkdir(parents=True, exist_ok=True)
        payload_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        build_script.write_text(_render_build_script(), encoding="utf-8")
        solve_script.write_text(_render_solve_script(), encoding="utf-8")
        sample_report.write_text(_render_sample_readme(payload), encoding="utf-8")
        if gds_path is not None:
            shutil.copy2(gds_path, script_dir / "source_geometry.gds")
        if formula_trace_path is not None and formula_trace_path.is_file():
            shutil.copy2(formula_trace_path, script_dir / "hfss_ads_formula_trace.md")
    return {
        "selection_rank": sample_rank,
        "evaluation": evaluation,
        "overall_status": status,
        "script_dir": str(script_dir) if payload else "",
        "payload_json": str(payload_json) if payload else "",
        "build_script": str(build_script) if payload else "",
        "solve_script": str(solve_script) if payload else "",
        "sample_report": str(sample_report) if payload else "",
        "gds_path": "" if gds_path is None else str(gds_path),
        "emx_s8p_path": "" if emx_s8p_path is None else str(emx_s8p_path),
        "ads_formula_trace": "" if formula_trace_path is None else str(formula_trace_path),
        "port_count": len(payload.get("ports", [])) if payload else 0,
        "conductor_polygon_count": len(payload.get("conductor_polygons", [])) if payload else 0,
        "checks": [check.as_dict() for check in checks],
        "_check_objects": checks,
    }


def _extract_gds(gds_path: Path, proc_info: ProcFileInfo) -> dict[str, Any]:
    import gdstk

    lib = gdstk.read_gds(str(gds_path))
    if not lib.cells:
        raise ValueError(f"{gds_path} has no cells")
    conductor_polygons = []
    labels = {}
    all_bboxes = []
    for cell in lib.cells:
        for poly_index, poly in enumerate(cell.polygons):
            bbox = poly.bounding_box()
            if bbox is None:
                continue
            layer = int(poly.layer)
            datatype = int(poly.datatype)
            conductor = _conductor_for_gds_layer(proc_info, layer)
            if conductor is None:
                continue
            (min_x, min_y), (max_x, max_y) = bbox
            points = [[float(x), float(y)] for x, y in poly.points]
            record = {
                "cell": cell.name,
                "index": len(conductor_polygons),
                "source_polygon_index": int(poly_index),
                "layer": layer,
                "datatype": datatype,
                "metal": conductor.name,
                "role": _role_for_polygon(conductor.name, layer, datatype, proc_info),
                "point_count": len(points),
                "area_um2": _polygon_area(points),
                "bbox_um": [float(min_x), float(min_y), float(max_x), float(max_y)],
                "points_um": points,
            }
            conductor_polygons.append(record)
            all_bboxes.append(record["bbox_um"])
        for label in cell.labels:
            text = str(label.text)
            layer = int(label.layer)
            conductor = _conductor_for_gds_layer(proc_info, layer)
            labels[text] = {
                "text": text,
                "cell": cell.name,
                "layer": layer,
                "texttype": int(label.texttype),
                "origin_um": [float(label.origin[0]), float(label.origin[1])],
                "metal": "" if conductor is None else conductor.name,
                "proc_summary": proc_info.summary_for_gds_layer(layer),
            }
    return {
        "gds_path": str(gds_path),
        "unit": lib.unit,
        "precision": lib.precision,
        "top_cells": [cell.name for cell in lib.top_level()],
        "conductor_polygons": conductor_polygons,
        "labels": labels,
        "bbox_um": _combined_bbox(all_bboxes),
    }


def _build_payload(
    *,
    sample: dict[str, Any],
    manifest: dict[str, Any],
    gds_info: dict[str, Any],
    proc_info: ProcFileInfo,
    proc_file: Path,
    power_line: dict[str, Any],
    layout_manifest: dict[str, Any],
    emx_s8p_path: Path | None,
    layout_json_path: Path | None,
    power_line_path: Path | None,
    formula_trace_path: Path | None,
    grid: dict[str, Any],
    hfss_version: str,
    hfss_solution_type: str,
    hfss_calibration_profile: str,
) -> dict[str, Any]:
    stack = _stack_payload(proc_info, proc_file)
    _apply_sidecar_stack_overrides(stack, layout_manifest, power_line)
    calibration_profile = _hfss_calibration_profile_payload(hfss_calibration_profile)
    calibration_env_defaults = (
        calibration_profile.get("env_defaults") if isinstance(calibration_profile.get("env_defaults"), dict) else {}
    )
    port_sheet_width_mode = _normalize_port_sheet_width_mode(
        str(calibration_env_defaults.get("HFSS_PORT_SHEET_WIDTH_MODE", "physical_line_width"))
    )
    labels = gds_info["labels"]
    role_by_label = {str(label): role for role, label in (power_line.get("labels") or {}).items()}
    layout_ports = _layout_port_map(layout_manifest)
    ports = []
    for port_name in PORT_NAMES:
        signal = labels.get(port_name, {})
        ground = labels.get(f"{port_name}_G", {})
        signal_metal = signal.get("metal", "")
        ground_metal = ground.get("metal", "")
        role = role_by_label.get(port_name, "")
        sheet_axis = _port_sheet_axis(role)
        layout_port = layout_ports.get(port_name, {})
        ports.append(
            {
                "port_name": port_name,
                "role": role,
                "ground_name": f"{port_name}_G",
                "signal_label": signal,
                "ground_label": ground,
                "signal_metal": signal_metal,
                "ground_metal": ground_metal,
                "signal_z_um": _port_signal_z(stack, signal_metal),
                "ground_z_um": _port_ground_z(stack, ground_metal),
                "port_sheet_width_um": _port_sheet_width(
                    signal,
                    ground,
                    power_line,
                    layout_port,
                    sheet_axis,
                    port_sheet_width_mode,
                ),
                "port_sheet_width_source": _port_sheet_width_source(
                    power_line,
                    layout_port,
                    sheet_axis,
                    port_sheet_width_mode,
                ),
                "port_sheet_width_mode": port_sheet_width_mode,
                "port_sheet_axis": sheet_axis,
                "emx_internal_size_um": layout_port.get("internal_size_um"),
                "emx_signal_internal_size_um": layout_port.get("signal_internal_size_um"),
                "emx_ground_internal_size_um": layout_port.get("ground_internal_size_um"),
            }
        )
    payload = {
        "schema": "rfic_transformer_hfss_s8p_build_payload.v1",
        "sample_id": str(sample.get("evaluation", "")),
        "selection_rank": str(sample.get("selection_rank", "")),
        "source_handoff_manifest": manifest,
        "source_files": {
            "gds": gds_info.get("gds_path", ""),
            "layout_json": "" if layout_json_path is None else str(layout_json_path),
            "power_line_8port_geometry_json": "" if power_line_path is None else str(power_line_path),
            "emx_s8p": "" if emx_s8p_path is None else str(emx_s8p_path),
            "ads_formula_trace": "" if formula_trace_path is None else str(formula_trace_path),
        },
        "hfss": {
            "version": hfss_version,
            "solution_type": hfss_solution_type,
            "project_name": f"{_slug(str(sample.get('evaluation', 'sample')))}_s8p_hfss",
            "design_name": f"{_slug(str(sample.get('evaluation', 'sample')))}_s8p_3dmodel",
            "setup_name": "Setup_15GHz",
            "sweep_name": "Sweep_5_60_1p0",
            "expected_touchstone_suffix": ".s8p",
            "calibration_profile": calibration_profile,
            "calibration_env_defaults": dict(calibration_profile.get("env_defaults") or {}),
        },
        "frequency_grid": grid,
        "stack": stack,
        "bbox_um": gds_info.get("bbox_um", []),
        "conductor_polygons": gds_info["conductor_polygons"],
        "labels": labels,
        "ports": ports,
        "power_line_8port_geometry": power_line,
        "differential_port_pairs": manifest.get("port_pairs") or sample.get("port_pairs") or [],
        "acceptance_note": (
            "Generated script packet only. Do not report HFSS agreement until the exported `.s8p` "
            "passes the physical-feature and EMX-vs-HFSS comparison gates."
        ),
    }
    payload["contract_evidence"] = _payload_contract_evidence(payload)
    return payload


def _hfss_calibration_profile_payload(name: str) -> dict[str, Any]:
    profile_name = str(name or DEFAULT_HFSS_CALIBRATION_PROFILE).strip()
    if profile_name not in HFSS_CALIBRATION_PROFILES:
        raise ValueError(
            "Unsupported HFSS calibration profile="
            + repr(profile_name)
            + "; expected one of "
            + ", ".join(sorted(HFSS_CALIBRATION_PROFILES))
        )
    profile = HFSS_CALIBRATION_PROFILES[profile_name]
    return {
        "name": profile["name"],
        "intent": profile["intent"],
        "diagnosis_basis": list(profile.get("diagnosis_basis") or []),
        "final_acceptance_gate": profile.get("final_acceptance_gate", ""),
        "env_defaults": dict(profile.get("env_defaults") or {}),
    }


def _payload_contract_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    ports = list(payload.get("ports") or [])
    power_line = payload.get("power_line_8port_geometry") if isinstance(payload.get("power_line_8port_geometry"), dict) else {}
    labels = power_line.get("labels") if isinstance(power_line, dict) else {}
    primary = power_line.get("primary_power_line") if isinstance(power_line, dict) else {}
    secondary = power_line.get("secondary_power_line") if isinstance(power_line, dict) else {}
    physical_left, physical_right, primary_is_left = _physical_left_right_power_lines(power_line)
    vertical_length_um = _to_float(power_line.get("vertical_length_um")) if isinstance(power_line, dict) else None
    max_outer_height_um = _to_float(power_line.get("max_outer_height_um")) if isinstance(power_line, dict) else None
    if max_outer_height_um is None:
        max_outer_height_um = _to_float(power_line.get("max_outer_diameter_um")) if isinstance(power_line, dict) else None
    expected_vertical_length_um = _to_float(power_line.get("expected_vertical_length_um")) if isinstance(power_line, dict) else None
    ratio = _to_float(power_line.get("vertical_length_diameter_ratio")) if isinstance(power_line, dict) else None
    ground_frame_width_um = _to_float(power_line.get("ground_frame_width_um")) if isinstance(power_line, dict) else None
    shield_inner_bbox_um = _bbox_evidence(power_line.get("shield_inner_bbox_um")) if isinstance(power_line, dict) else None
    shield_outer_bbox_um = _bbox_evidence(power_line.get("shield_outer_bbox_um")) if isinstance(power_line, dict) else None
    ground_frame_edges_um = _ground_frame_edges_from_bboxes(shield_inner_bbox_um, shield_outer_bbox_um)
    hfss = payload.get("hfss") if isinstance(payload.get("hfss"), dict) else {}
    calibration_profile = hfss.get("calibration_profile") if isinstance(hfss.get("calibration_profile"), dict) else {}
    calibration_env_defaults = (
        hfss.get("calibration_env_defaults")
        if isinstance(hfss.get("calibration_env_defaults"), dict)
        else calibration_profile.get("env_defaults")
        if isinstance(calibration_profile.get("env_defaults"), dict)
        else {}
    )
    return {
        "expected_port_names": list(PORT_NAMES),
        "actual_port_names": [str(port.get("port_name", "")) for port in ports if isinstance(port, dict)],
        "actual_ground_names": [str(port.get("ground_name", "")) for port in ports if isinstance(port, dict)],
        "port_count": len(ports),
        "ports_have_signal_ground_labels": all(
            _has_label_origin(port.get("signal_label")) and _has_label_origin(port.get("ground_label"))
            for port in ports
            if isinstance(port, dict)
        )
        and len(ports) == len(PORT_NAMES),
        "ports_have_signal_ground_metal": all(
            bool(port.get("signal_metal")) and bool(port.get("ground_metal"))
            for port in ports
            if isinstance(port, dict)
        )
        and len(ports) == len(PORT_NAMES),
        "ports_have_z_evidence": all(
            _to_float(port.get("signal_z_um")) is not None and _to_float(port.get("ground_z_um")) is not None
            for port in ports
            if isinstance(port, dict)
        )
        and len(ports) == len(PORT_NAMES),
        "power_line_enabled": bool(power_line.get("enabled")) if isinstance(power_line, dict) else False,
        "power_line_role_labels": {role: str(labels.get(role, "")) for role in POWER_LINE_ROLE_ORDER}
        if isinstance(labels, dict)
        else {},
        "line_width_um": _power_line_shared_width(power_line),
        "bridge_width_um": _to_float(power_line.get("bridge_width_um")) if isinstance(power_line, dict) else None,
        "port_sheet_widths_um": {
            str(port.get("port_name", "")): _to_float(port.get("port_sheet_width_um"))
            for port in ports
            if isinstance(port, dict)
        },
        "port_sheet_width_sources": {
            str(port.get("port_name", "")): str(port.get("port_sheet_width_source", ""))
            for port in ports
            if isinstance(port, dict)
        },
        "port_sheet_width_modes": {
            str(port.get("port_name", "")): str(port.get("port_sheet_width_mode", ""))
            for port in ports
            if isinstance(port, dict)
        },
        "port_sheet_width_mode": _single_nonempty_value(
            [str(port.get("port_sheet_width_mode", "")) for port in ports if isinstance(port, dict)]
        ),
        "vertical_length_um": vertical_length_um,
        "max_outer_height_um": max_outer_height_um,
        "vertical_length_diameter_ratio": ratio,
        "expected_vertical_length_um": expected_vertical_length_um,
        "computed_vertical_length_um": None
        if max_outer_height_um is None
        else max_outer_height_um * 1.5,
        "ground_frame_width_um": ground_frame_width_um,
        "ground_frame_policy": str(power_line.get("ground_frame_policy", "")) if isinstance(power_line, dict) else "",
        "shield_inner_bbox_um": shield_inner_bbox_um,
        "shield_outer_bbox_um": shield_outer_bbox_um,
        "ground_frame_edges_um": ground_frame_edges_um,
        "primary_power_line_height_um": _to_float(primary.get("height_um")) if isinstance(primary, dict) else None,
        "secondary_power_line_height_um": _to_float(secondary.get("height_um")) if isinstance(secondary, dict) else None,
        "primary_power_line_center_x_um": _to_float(primary.get("center_x_um")) if isinstance(primary, dict) else None,
        "secondary_power_line_center_x_um": _to_float(secondary.get("center_x_um")) if isinstance(secondary, dict) else None,
        "primary_is_physical_left": primary_is_left,
        "physical_left_power_line": _power_line_endpoint_evidence(physical_left),
        "physical_right_power_line": _power_line_endpoint_evidence(physical_right),
        "primary_bridge": _bridge_evidence(power_line.get("primary_bridge") if isinstance(power_line, dict) else None),
        "secondary_bridge": _bridge_evidence(power_line.get("secondary_bridge") if isinstance(power_line, dict) else None),
        "source_files_present": {
            key: _path_exists(value)
            for key, value in (payload.get("source_files") or {}).items()
            if key in {"gds", "layout_json", "power_line_8port_geometry_json", "emx_s8p", "ads_formula_trace"}
        },
        "expected_touchstone_suffix": hfss.get("expected_touchstone_suffix"),
        "frequency_points": (payload.get("frequency_grid") or {}).get("points"),
        "hfss_calibration_profile_name": calibration_profile.get("name"),
        "hfss_calibration_env_defaults": dict(calibration_env_defaults),
    }


def _payload_contract_checks(
    payload: dict[str, Any],
    sample_rank: str,
    evaluation: str,
    *,
    expected_frequency_points: int = 111,
    diagnostic_frequency_grid: bool = False,
) -> list[Check]:
    evidence = _payload_contract_evidence(payload)
    checks: list[Check] = []
    actual_ports = evidence["actual_port_names"]
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload has eight HFSS ports",
            evidence["port_count"] == 8,
            f"ports={evidence['port_count']}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload port names are P001-P008",
            actual_ports == list(PORT_NAMES),
            f"expected={list(PORT_NAMES)}, actual={actual_ports}",
        )
    )
    expected_ground_names = [f"{name}_G" for name in PORT_NAMES]
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload ground names are P001_G-P008_G",
            evidence["actual_ground_names"] == expected_ground_names,
            f"expected={expected_ground_names}, actual={evidence['actual_ground_names']}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload ports carry signal/ground label coordinates",
            bool(evidence["ports_have_signal_ground_labels"]),
            evidence,
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload ports carry signal/ground metal mapping",
            bool(evidence["ports_have_signal_ground_metal"]),
            evidence,
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload ports carry stack z evidence",
            bool(evidence["ports_have_z_evidence"]),
            evidence,
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload source files include GDS layout power-line EMX and formula trace",
            all((evidence.get("source_files_present") or {}).get(key) for key in ("gds", "layout_json", "power_line_8port_geometry_json", "emx_s8p", "ads_formula_trace")),
            evidence.get("source_files_present"),
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload expects 8-port Touchstone export",
            evidence.get("expected_touchstone_suffix") == ".s8p",
            evidence.get("expected_touchstone_suffix"),
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload frequency grid has expected point count",
            int(evidence.get("frequency_points") or 0) == int(expected_frequency_points),
            evidence.get("frequency_points"),
        )
    )
    env_defaults = evidence.get("hfss_calibration_env_defaults") if isinstance(evidence.get("hfss_calibration_env_defaults"), dict) else {}
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload embeds diagnosed HFSS calibration profile",
            bool(evidence.get("hfss_calibration_profile_name")) and bool(env_defaults),
            f"profile={evidence.get('hfss_calibration_profile_name')}, env_default_count={len(env_defaults)}",
        )
    )
    reference_mode = str(env_defaults.get("HFSS_PORT_REFERENCE_MODE", "")).lower()
    profile_name = str(evidence.get("hfss_calibration_profile_name") or "")
    global_reference_modes = {"all", "all_m5", "global_m5"}
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload HFSS calibration port reference mode is explicitly diagnosed",
            reference_mode not in global_reference_modes or profile_name.startswith(("diagnosis_v74a_", "diagnosis_v74b_")),
            f"profile={profile_name}, mode={reference_mode}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload HFSS calibration keeps M5 shield finite by default",
            str(env_defaults.get("HFSS_M5_SHIELD_BOUNDARY", "")).lower() in {"finite", "finite_conductor", "none", "off"}
            or profile_name.startswith(("diagnosis_v74b_", "diagnostic_m5_perfecte_reference_check")),
            f"profile={profile_name}, mode={env_defaults.get('HFSS_M5_SHIELD_BOUNDARY')}",
        )
    )
    if not diagnostic_frequency_grid:
        checks.append(
            _check(
                sample_rank,
                evaluation,
                "payload frequency grid is final 5-60 GHz contract",
                int(evidence.get("frequency_points") or 0) == 111,
                evidence.get("frequency_points"),
            )
        )
    checks.extend(_payload_power_line_checks(payload, sample_rank, evaluation, evidence))
    return checks


def _payload_power_line_checks(
    payload: dict[str, Any],
    sample_rank: str,
    evaluation: str,
    evidence: dict[str, Any],
) -> list[Check]:
    checks: list[Check] = []
    power_line = payload.get("power_line_8port_geometry") if isinstance(payload.get("power_line_8port_geometry"), dict) else {}
    labels = power_line.get("labels") if isinstance(power_line, dict) else {}
    primary = power_line.get("primary_power_line") if isinstance(power_line, dict) else {}
    secondary = power_line.get("secondary_power_line") if isinstance(power_line, dict) else {}
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line 8-port geometry enabled",
            bool(evidence.get("power_line_enabled")),
            power_line.get("enabled") if isinstance(power_line, dict) else None,
        )
    )
    actual_role_labels = [str((labels or {}).get(role, "")) for role in POWER_LINE_ROLE_ORDER] if isinstance(labels, dict) else []
    expected_role_labels = [POWER_LINE_EXPECTED_LABELS[role] for role in POWER_LINE_ROLE_ORDER]
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line role labels match The best.s8p mapping",
            actual_role_labels == expected_role_labels,
            f"expected={expected_role_labels}, actual={actual_role_labels}",
        )
    )
    bridge_width = evidence.get("bridge_width_um")
    line_width = evidence.get("line_width_um")
    port_sheet_widths = evidence.get("port_sheet_widths_um") if isinstance(evidence.get("port_sheet_widths_um"), dict) else {}
    port_sheet_sources = (
        evidence.get("port_sheet_width_sources") if isinstance(evidence.get("port_sheet_width_sources"), dict) else {}
    )
    port_sheet_width_mode = _normalize_port_sheet_width_mode(str(evidence.get("port_sheet_width_mode", "")))
    port_sheet_widths_match_line = (
        line_width is not None
        and len(port_sheet_widths) == len(PORT_NAMES)
        and all(
            value is not None and abs(float(value) - float(line_width)) <= POWER_LINE_TOLERANCE_UM
            for value in port_sheet_widths.values()
        )
    )
    if port_sheet_width_mode == "emx_pin_footprint":
        checks.append(
            _check(
                sample_rank,
                evaluation,
                "payload port sheet widths use EMX pin footprint excitation mode",
                len(port_sheet_widths) == len(PORT_NAMES)
                and len(port_sheet_sources) == len(PORT_NAMES)
                and all(
                    str(source) in {
                        "layout_manifest_emx_pin_footprint",
                        "power_line_8port_geometry.line_width_um_or_bridge_width_um",
                        "signal_ground_distance_fallback",
                    }
                    for source in port_sheet_sources.values()
                ),
                f"mode={port_sheet_width_mode}, line_width_um={line_width}, "
                f"port_sheet_widths_um={port_sheet_widths}, sources={port_sheet_sources}",
            )
        )
    else:
        checks.append(
            _check(
                sample_rank,
                evaluation,
                "payload port sheet widths equal synchronized line width",
                port_sheet_widths_match_line,
                f"mode={port_sheet_width_mode}, line_width_um={line_width}, "
                f"port_sheet_widths_um={port_sheet_widths}",
            )
        )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line bridge width matches vertical power-line width",
            bridge_width is not None and _bridge_width_matches_power_lines(power_line, bridge_width),
            f"bridge_width_um={bridge_width}",
        )
    )
    vertical = evidence.get("vertical_length_um")
    max_height = evidence.get("max_outer_height_um")
    ratio = evidence.get("vertical_length_diameter_ratio")
    expected_vertical = evidence.get("expected_vertical_length_um")
    computed_vertical = evidence.get("computed_vertical_length_um")
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line max coil height is positive",
            max_height is not None and float(max_height) > 0.0,
            f"max_outer_height_um={max_height}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line vertical length ratio is 1.5",
            ratio is not None and abs(float(ratio) - 1.5) <= 1.0e-12,
            f"vertical_length_diameter_ratio={ratio}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line vertical length equals 1.5 max coil height",
            vertical is not None
            and computed_vertical is not None
            and abs(float(vertical) - float(computed_vertical)) <= POWER_LINE_TOLERANCE_UM,
            f"vertical_length_um={vertical}, computed={computed_vertical}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line stored expected vertical length matches",
            vertical is not None
            and expected_vertical is not None
            and abs(float(vertical) - float(expected_vertical)) <= POWER_LINE_TOLERANCE_UM,
            f"vertical_length_um={vertical}, expected_vertical_length_um={expected_vertical}",
        )
    )
    primary_height = evidence.get("primary_power_line_height_um")
    secondary_height = evidence.get("secondary_power_line_height_um")
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line left/right vertical heights match contract",
            vertical is not None
            and primary_height is not None
            and secondary_height is not None
            and abs(float(primary_height) - float(vertical)) <= POWER_LINE_TOLERANCE_UM
            and abs(float(secondary_height) - float(vertical)) <= POWER_LINE_TOLERANCE_UM
            and abs(float(primary_height) - float(secondary_height)) <= POWER_LINE_TOLERANCE_UM,
            f"vertical={vertical}, primary={primary_height}, secondary={secondary_height}",
        )
    )
    ground_frame_width = evidence.get("ground_frame_width_um")
    ground_frame_policy = evidence.get("ground_frame_policy")
    ground_frame_edges = evidence.get("ground_frame_edges_um") if isinstance(evidence.get("ground_frame_edges_um"), dict) else {}
    ground_frame_edges_match = (
        ground_frame_width is not None
        and bool(ground_frame_edges)
        and all(
            value is not None and abs(float(value) - float(ground_frame_width)) <= POWER_LINE_TOLERANCE_UM
            for value in ground_frame_edges.values()
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line ground frame policy is rectangular shield frame",
            ground_frame_policy == POWER_LINE_EXPECTED_GROUND_FRAME_POLICY,
            f"ground_frame_policy={ground_frame_policy}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line ground frame width is explicit and positive",
            ground_frame_width is not None and float(ground_frame_width) > 0.0,
            f"ground_frame_width_um={ground_frame_width}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line shield outer bbox expands inner window by ground frame width",
            ground_frame_edges_match,
            f"ground_frame_width_um={ground_frame_width}, edges={ground_frame_edges}",
        )
    )
    physical_left = evidence.get("physical_left_power_line") if isinstance(evidence.get("physical_left_power_line"), dict) else {}
    physical_right = evidence.get("physical_right_power_line") if isinstance(evidence.get("physical_right_power_line"), dict) else {}
    checks.append(
        _check(
            sample_rank,
            evaluation,
            "payload power-line physical left/right order is explicit",
            evidence.get("primary_is_physical_left") in {True, False},
            f"primary_x={evidence.get('primary_power_line_center_x_um')}, secondary_x={evidence.get('secondary_power_line_center_x_um')}, primary_is_left={evidence.get('primary_is_physical_left')}",
        )
    )
    endpoint_expectations = {
        "physical left top power port": (physical_left, "top_port_label", "P002"),
        "physical left bottom power port": (physical_left, "bottom_port_label", "P003"),
        "physical right top power port": (physical_right, "top_port_label", "P007"),
        "physical right bottom power port": (physical_right, "bottom_port_label", "P008"),
        "physical left top ground": (physical_left, "top_ground_label", "P002_G"),
        "physical left bottom ground": (physical_left, "bottom_ground_label", "P003_G"),
        "physical right top ground": (physical_right, "top_ground_label", "P007_G"),
        "physical right bottom ground": (physical_right, "bottom_ground_label", "P008_G"),
    }
    for label, (section, key, expected) in endpoint_expectations.items():
        actual = section.get(key) if isinstance(section, dict) else None
        checks.append(
            _check(
                sample_rank,
                evaluation,
                f"payload power-line {label}",
                actual == expected,
                f"expected={expected}, actual={actual}",
            )
        )
    for side in ("primary", "secondary"):
        checks.extend(_payload_bridge_checks(evidence.get(f"{side}_bridge"), side, sample_rank, evaluation))
    return checks


def _payload_bridge_checks(bridge: Any, side: str, sample_rank: str, evaluation: str) -> list[Check]:
    checks: list[Check] = []
    if not isinstance(bridge, dict):
        return [_check(sample_rank, evaluation, f"payload {side} bridge evidence exists", False, bridge)]
    width = bridge.get("width_um")
    max_abs_y = bridge.get("max_abs_y_um")
    delta_y = bridge.get("delta_y_um")
    edge_error = bridge.get("power_line_edge_alignment_error_um")
    extends_away = bridge.get("extends_away_from_coil_interior")
    length = bridge.get("length_um")
    checks.append(
        _check(
            sample_rank,
            evaluation,
            f"payload {side} bridge width matches vertical power-line width",
            width is not None and _bridge_width_matches_power_lines_from_bridge(bridge, width),
            f"width_um={width}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            f"payload {side} bridge centered at y=0",
            max_abs_y is not None and float(max_abs_y) <= POWER_LINE_TOLERANCE_UM,
            f"max_abs_y_um={max_abs_y}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            f"payload {side} bridge horizontal",
            delta_y is not None and abs(float(delta_y)) <= POWER_LINE_TOLERANCE_UM and bridge.get("is_horizontal") is True,
            f"delta_y_um={delta_y}, is_horizontal={bridge.get('is_horizontal')}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            f"payload {side} bridge touches power-line edge",
            edge_error is not None and float(edge_error) <= POWER_LINE_TOLERANCE_UM,
            f"edge_error_um={edge_error}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            f"payload {side} bridge stays outside coil interior",
            extends_away is True,
            f"extends_away_from_coil_interior={extends_away}",
        )
    )
    checks.append(
        _check(
            sample_rank,
            evaluation,
            f"payload {side} bridge has positive x length",
            length is not None and float(length) > 0.0,
            f"length_um={length}",
        )
    )
    return checks


def _bridge_width_matches_power_lines(power_line: Any, bridge_width: Any) -> bool:
    width = _to_float(bridge_width)
    if width is None or not isinstance(power_line, dict):
        return False
    expected_widths = []
    for key in ("primary_power_line", "secondary_power_line"):
        section = power_line.get(key)
        section_width = _to_float(section.get("width_um")) if isinstance(section, dict) else None
        if section_width is not None:
            expected_widths.append(section_width)
    return bool(expected_widths) and all(abs(width - expected) <= POWER_LINE_TOLERANCE_UM for expected in expected_widths)


def _bridge_width_matches_power_lines_from_bridge(bridge: Any, bridge_width: Any) -> bool:
    width = _to_float(bridge_width)
    if width is None or not isinstance(bridge, dict):
        return False
    left_edge = _to_float(bridge.get("power_line_left_edge_x_um"))
    right_edge = _to_float(bridge.get("power_line_right_edge_x_um"))
    if left_edge is None or right_edge is None:
        return False
    power_line_width = abs(right_edge - left_edge)
    return abs(width - power_line_width) <= POWER_LINE_TOLERANCE_UM


def _bbox_evidence(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    keys = ("min_x_um", "min_y_um", "max_x_um", "max_y_um")
    values: dict[str, float] = {}
    for key in keys:
        value = _to_float(raw.get(key))
        if value is None:
            return None
        values[key] = float(value)
    return values


def _ground_frame_edges_from_bboxes(
    inner: dict[str, float] | None,
    outer: dict[str, float] | None,
) -> dict[str, float] | None:
    if not inner or not outer:
        return None
    return {
        "left_um": float(inner["min_x_um"]) - float(outer["min_x_um"]),
        "right_um": float(outer["max_x_um"]) - float(inner["max_x_um"]),
        "bottom_um": float(inner["min_y_um"]) - float(outer["min_y_um"]),
        "top_um": float(outer["max_y_um"]) - float(inner["max_y_um"]),
    }


def _bridge_evidence(bridge: Any) -> dict[str, Any]:
    if not isinstance(bridge, dict):
        return {}
    y_values = []
    for section, key in (("coil_anchor", "y_um"), ("power_line_edge", "y_um")):
        item = bridge.get(section)
        y_values.append(_to_float(item.get(key)) if isinstance(item, dict) else None)
    y_values.extend([_to_float(bridge.get("center_y_um")), _to_float(bridge.get("power_line_center_y_um"))])
    present_y_values = [abs(float(value)) for value in y_values if value is not None]
    return {
        "width_um": _to_float(bridge.get("width_um")),
        "length_um": _to_float(bridge.get("length_um")),
        "delta_y_um": _to_float(bridge.get("delta_y_um")),
        "center_y_um": _to_float(bridge.get("center_y_um")),
        "power_line_center_y_um": _to_float(bridge.get("power_line_center_y_um")),
        "power_line_left_edge_x_um": _to_float(bridge.get("power_line_left_edge_x_um")),
        "power_line_right_edge_x_um": _to_float(bridge.get("power_line_right_edge_x_um")),
        "power_line_width_from_edges_um": (
            None
            if _to_float(bridge.get("power_line_left_edge_x_um")) is None
            or _to_float(bridge.get("power_line_right_edge_x_um")) is None
            else abs(
                float(_to_float(bridge.get("power_line_right_edge_x_um")))
                - float(_to_float(bridge.get("power_line_left_edge_x_um")))
            )
        ),
        "power_line_edge_alignment_error_um": _to_float(bridge.get("power_line_edge_alignment_error_um")),
        "extends_away_from_coil_interior": bridge.get("extends_away_from_coil_interior"),
        "is_horizontal": bridge.get("is_horizontal"),
        "y_values_um": y_values,
        "max_abs_y_um": max(present_y_values, default=None),
    }


def _physical_left_right_power_lines(power_line: Any) -> tuple[dict[str, Any], dict[str, Any], bool | None]:
    if not isinstance(power_line, dict):
        return {}, {}, None
    primary = power_line.get("primary_power_line") if isinstance(power_line.get("primary_power_line"), dict) else {}
    secondary = power_line.get("secondary_power_line") if isinstance(power_line.get("secondary_power_line"), dict) else {}
    primary_x = _to_float(primary.get("center_x_um")) if isinstance(primary, dict) else None
    secondary_x = _to_float(secondary.get("center_x_um")) if isinstance(secondary, dict) else None
    if primary_x is None or secondary_x is None or primary_x == secondary_x:
        return {}, {}, None
    if primary_x < secondary_x:
        return primary, secondary, True
    return secondary, primary, False


def _power_line_endpoint_evidence(power_line: Any) -> dict[str, Any]:
    if not isinstance(power_line, dict):
        return {}
    return {
        "center_x_um": _to_float(power_line.get("center_x_um")),
        "center_y_um": _to_float(power_line.get("center_y_um")),
        "height_um": _to_float(power_line.get("height_um")),
        "top_port_label": power_line.get("top_port_label"),
        "bottom_port_label": power_line.get("bottom_port_label"),
        "top_ground_label": power_line.get("top_ground_label"),
        "bottom_ground_label": power_line.get("bottom_ground_label"),
    }


def _has_label_origin(label: Any) -> bool:
    if not isinstance(label, dict):
        return False
    origin = label.get("origin_um")
    if not isinstance(origin, (list, tuple)) or len(origin) != 2:
        return False
    return all(_to_float(value) is not None for value in origin)


def _path_exists(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and Path(text).expanduser().is_file()


def _stack_payload(proc_info: ProcFileInfo, proc_file: Path) -> dict[str, Any]:
    conductors = {}
    for conductor in proc_info.conductors:
        conductors[conductor.name] = {
            "name": conductor.name,
            "thickness_um": float(conductor.thickness_um),
            "z_bottom_um": float(conductor.z_bottom_um),
            "z_top_um": float(conductor.z_top_um),
            "sheet_resistance_ohm_per_sq": _numeric_sheet_resistance(conductor),
            "sheet_resistance_expr": conductor.sheet_resistance_expr,
            "conductivity_s_per_m": _conductivity(conductor),
            "gds_layers": list(conductor.gds_layers),
            "gds_layer_datatypes": [list(pair) for pair in conductor.gds_layer_datatypes],
        }
    return {
        "source_proc_file": str(proc_file),
        "conductors": conductors,
        "dielectrics": [
            {
                "name": layer.name,
                "thickness_um": float(layer.thickness_um),
                "z_bottom_um": float(layer.z_bottom_um),
                "z_top_um": float(layer.z_top_um),
                "epsilon_r": float(layer.epsilon_r),
                "conductivity_s_per_m": float(getattr(layer, "conductivity_s_per_m", 0.0)),
            }
            for layer in proc_info.dielectrics
            if float(layer.z_top_um) > float(layer.z_bottom_um)
        ],
    }


def _apply_sidecar_stack_overrides(
    stack: dict[str, Any],
    layout_manifest: dict[str, Any],
    power_line: dict[str, Any],
) -> None:
    """Prefer EMX-sidecar process z evidence for conductors used in HFSS.

    The local fallback proc is only a parser/mapping fallback. EMX handoff
    sidecars record the actual process-layer z positions used when the GDS was
    generated on MARS; those z values must win for cross-simulator validation.
    """

    summaries: list[tuple[str, dict[str, Any]]] = []
    for source_name, source in (("layout_json", layout_manifest), ("power_line_8port_geometry", power_line)):
        if not isinstance(source, dict):
            continue
        summary = source.get("process_layer_summary")
        if isinstance(summary, dict):
            summaries.append((source_name, summary))
    conductors = stack.get("conductors") if isinstance(stack.get("conductors"), dict) else {}
    applied: dict[str, dict[str, Any]] = {}
    for source_name, summary in summaries:
        records = summary.get("records") if isinstance(summary.get("records"), dict) else {}
        for record in records.values():
            if not isinstance(record, dict):
                continue
            metal = str(record.get("conductor_name") or record.get("logical_name") or "").strip()
            if metal not in conductors:
                continue
            thickness = _to_float(record.get("conductor_thickness_um"))
            z_bottom = _to_float(record.get("conductor_z_bottom_um"))
            z_top = _to_float(record.get("conductor_z_top_um"))
            if thickness is None or z_bottom is None or z_top is None:
                continue
            if float(z_top) <= float(z_bottom) or float(thickness) <= 0:
                continue
            conductor = conductors[metal]
            before = {
                "thickness_um": conductor.get("thickness_um"),
                "z_bottom_um": conductor.get("z_bottom_um"),
                "z_top_um": conductor.get("z_top_um"),
            }
            conductor["thickness_um"] = float(thickness)
            conductor["z_bottom_um"] = float(z_bottom)
            conductor["z_top_um"] = float(z_top)
            if conductor.get("sheet_resistance_ohm_per_sq"):
                conductor["conductivity_s_per_m"] = 1.0 / (
                    float(conductor["sheet_resistance_ohm_per_sq"]) * float(thickness) * 1.0e-6
                )
            applied[metal] = {
                "source": source_name,
                "record": record.get("semantic_role") or record.get("proc_summary") or metal,
                "before": before,
                "after": {
                    "thickness_um": float(thickness),
                    "z_bottom_um": float(z_bottom),
                    "z_top_um": float(z_top),
                },
            }
    if applied:
        stack["sidecar_process_layer_overrides"] = applied


def _render_build_script() -> str:
    return r'''#!/usr/bin/env python3
"""Build an HFSS 8-port transformer model from `hfss_s8p_build_payload.json`.

Run this inside the Windows/HFSS Python environment. By default it builds and
saves the project only. Set HFSS_RUN_SOLVE=1 to run the solve/export script
after verifying geometry in AEDT.

Frequency-grid contract: Setup_15GHz with Sweep_5_60_1p0, 5-60 GHz, 1.0 GHz
step, 111 output points, exporting an 8-port `.s8p`.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path

from ansys.aedt.core import Hfss


SCRIPT_DIR = Path(__file__).resolve().parent
PAYLOAD_PATH = Path(os.environ.get("HFSS_S8P_PAYLOAD", SCRIPT_DIR / "hfss_s8p_build_payload.json"))
PAYLOAD = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
LOG_PATH = Path(os.environ.get("HFSS_BUILD_LOG", SCRIPT_DIR / "hfss_s8p_build.log"))
PORT_MANIFEST_PATH = Path(os.environ.get("HFSS_PORT_MANIFEST", SCRIPT_DIR / "hfss_s8p_build_port_manifest.json"))
CALIBRATION_PROFILE = PAYLOAD.get("hfss", {}).get("calibration_profile", {}) or {}
CALIBRATION_ENV_DEFAULTS = dict(
    PAYLOAD.get("hfss", {}).get("calibration_env_defaults")
    or CALIBRATION_PROFILE.get("env_defaults")
    or {}
)


def env_str(name, default):
    value = os.environ.get(str(name))
    if value is not None:
        return str(value).strip()
    return str(CALIBRATION_ENV_DEFAULTS.get(str(name), default)).strip()


def env_bool(name, default):
    return env_str(name, default).lower() in {"1", "true", "yes", "on"}


CONDUCTOR_SOLVE_INSIDE = env_bool("HFSS_CONDUCTOR_SOLVE_INSIDE", "0")
M5_SHIELD_BOUNDARY = env_str("HFSS_M5_SHIELD_BOUNDARY", "finite").lower()
DIELECTRIC_CONDUCTIVITY_MODE = env_str("HFSS_DIELECTRIC_CONDUCTIVITY_MODE", "loss_tangent").lower()
DIELECTRIC_EFFECTIVE_MODE = env_str("HFSS_DIELECTRIC_EFFECTIVE_MODE", "explicit_layers").lower()
DIELECTRIC_METAL_CAVITY_MODE = env_str("HFSS_DIELECTRIC_METAL_CAVITY_MODE", "z_interval_clear").lower()
UNITE_STRATEGY = env_str("HFSS_UNITE_STRATEGY", "connected_by_bbox").lower()
UNITE_BY_METAL = env_bool("HFSS_UNITE_BY_METAL", "0")
if UNITE_BY_METAL:
    UNITE_STRATEGY = "all_by_metal"
UNITE_CONNECTED_M5 = env_bool("HFSS_UNITE_CONNECTED_M5", "0")
PORT_REFERENCE_MODE = env_str("HFSS_PORT_REFERENCE_MODE", "local_ground_bbox_smallest").lower()
POWER_LINE_PORT_REFERENCE_MODE = env_str("HFSS_POWER_LINE_PORT_REFERENCE_MODE", "").lower()
REQUIRE_LOCAL_GROUND_REFERENCE = env_bool("HFSS_REQUIRE_LOCAL_GROUND_REFERENCE", "1")
PORT_REFERENCE_EXPECTED_COUNT = int(env_str("HFSS_PORT_REFERENCE_EXPECTED_COUNT", "0") or "0")
USE_PYAEDT_REFERENCE_PORT = env_bool("HFSS_USE_PYAEDT_REFERENCE_PORT", "0")
SKIP_PIN_CONDUCTORS = env_bool("HFSS_SKIP_PIN_CONDUCTORS", "0")
PORT_GEOMETRY_MODE = env_str("HFSS_PORT_GEOMETRY_MODE", "label_center").lower()
PORT_EDGE_EPS_UM = float(env_str("HFSS_PORT_EDGE_EPS_UM", "0") or "0")
PORT_SIGNAL_Z_MODE = env_str("HFSS_PORT_SIGNAL_Z_MODE", "payload").lower()
PORT_GROUND_Z_MODE = env_str("HFSS_PORT_GROUND_Z_MODE", "payload").lower()
PORT_DEEMBED = env_bool("HFSS_PORT_DEEMBED", "0")
PORT_MODE_RENORM_IMP = env_bool("HFSS_PORT_MODE_RENORM_IMP", "0")
PORT_RENORM_IMPEDANCE = env_str("HFSS_PORT_RENORM_IMPEDANCE", "50ohm") or "50ohm"


def log(message):
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(str(message) + "\n")


def q(value):
    return '"' + str(value) + '"'


def mode_renorm_imp_props():
    if not PORT_MODE_RENORM_IMP:
        return []
    return ["RenormImp:=", PORT_RENORM_IMPEDANCE]


def resolved_conductor_z(metal, mode, fallback):
    mode = str(mode or "payload").strip().lower()
    if mode in {"payload", "as_payload", "label", "default"}:
        return float(fallback)
    conductor = PAYLOAD.get("stack", {}).get("conductors", {}).get(str(metal or ""))
    if not conductor:
        return float(fallback)
    z_bottom = float(conductor["z_bottom_um"])
    z_top = float(conductor["z_top_um"])
    if mode in {"bottom", "z_bottom", "metal_bottom"}:
        return z_bottom
    if mode in {"top", "z_top", "metal_top"}:
        return z_top
    if mode in {"mid", "middle", "center", "centre", "metal_mid"}:
        return 0.5 * (z_bottom + z_top)
    raise ValueError("Unsupported HFSS port z mode=" + repr(mode))


def resolved_port_signal_z(port):
    return resolved_conductor_z(port.get("signal_metal", ""), PORT_SIGNAL_Z_MODE, port["signal_z_um"])


def resolved_port_ground_z(port):
    return resolved_conductor_z(port.get("ground_metal", ""), PORT_GROUND_Z_MODE, port["ground_z_um"])


def add_material(o_project, name, eps="1", conductivity=None, dielectric_loss_tangent=None):
    manager = o_project.GetDefinitionManager()
    props = [
        "NAME:" + name,
        "CoordinateSystemType:=", "Cartesian",
        "BulkOrSurfaceType:=", 1,
        ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
        "permittivity:=", str(eps),
    ]
    if conductivity is not None:
        props += ["conductivity:=", str(conductivity)]
    if dielectric_loss_tangent is not None:
        props += ["dielectric_loss_tangent:=", str(dielectric_loss_tangent)]
    try:
        manager.AddMaterial(props)
    except Exception:
        try:
            manager.EditMaterial(name, props)
        except Exception:
            log("material add/edit failed for " + name + ": " + traceback.format_exc())


def dielectric_material_loss(layer):
    sigma = float(layer.get("conductivity_s_per_m", 0.0) or 0.0)
    if sigma <= 0:
        return {"conductivity": 0.0, "dielectric_loss_tangent": None, "mode": "zero"}
    if DIELECTRIC_CONDUCTIVITY_MODE in {"conductivity", "direct"}:
        return {"conductivity": sigma, "dielectric_loss_tangent": None, "mode": "conductivity"}
    if DIELECTRIC_CONDUCTIVITY_MODE in {"ignore", "lossless", "zero"}:
        return {"conductivity": 0.0, "dielectric_loss_tangent": None, "mode": "ignore"}
    if DIELECTRIC_CONDUCTIVITY_MODE not in {"loss_tangent", "tan_delta", "tand"}:
        raise ValueError("Unsupported HFSS_DIELECTRIC_CONDUCTIVITY_MODE=" + repr(DIELECTRIC_CONDUCTIVITY_MODE))
    freq_ghz = float(PAYLOAD.get("frequency_grid", {}).get("setup_frequency_ghz", 15.0))
    eps_r = float(layer["epsilon_r"])
    eps0 = 8.8541878128e-12
    omega = 2.0 * math.pi * freq_ghz * 1.0e9
    tan_delta = sigma / (omega * eps0 * eps_r)
    return {"conductivity": 0.0, "dielectric_loss_tangent": tan_delta, "mode": "loss_tangent"}


def interval_overlap(a0, a1, b0, b1):
    lo = max(float(a0), float(b0))
    hi = min(float(a1), float(b1))
    return max(0.0, hi - lo)


def weighted_dielectric_value(layers, z0, z1, key, default_value):
    weighted = 0.0
    total = 0.0
    for layer in layers:
        thickness = interval_overlap(z0, z1, layer.get("z_bottom_um", 0.0), layer.get("z_top_um", 0.0))
        if thickness <= 0:
            continue
        weighted += thickness * float(layer.get(key, default_value) or default_value)
        total += thickness
    if total <= 0:
        return float(default_value)
    return weighted / total


def effective_dielectric_layers(stack, conductors, z_min, z_max):
    mode = DIELECTRIC_EFFECTIVE_MODE
    source_layers = list(stack.get("dielectrics", []))
    if mode in {"", "explicit", "explicit_layers", "off", "none"}:
        return source_layers
    if mode not in {"metal_gap_weighted", "weighted_metal_gaps", "effective", "weighted"}:
        raise ValueError("Unsupported HFSS_DIELECTRIC_EFFECTIVE_MODE=" + repr(DIELECTRIC_EFFECTIVE_MODE))

    breakpoints = [float(z_min), float(z_max)]
    for metal in ("metal9", "metal10"):
        conductor = conductors.get(metal)
        if not conductor:
            continue
        for key in ("z_bottom_um", "z_top_um"):
            z_value = float(conductor[key])
            if float(z_min) < z_value < float(z_max):
                breakpoints.append(z_value)
    breakpoints = sorted(set(round(value, 9) for value in breakpoints))

    effective_layers = []
    for idx, (seg0, seg1) in enumerate(zip(breakpoints, breakpoints[1:])):
        if seg1 <= seg0:
            continue
        covered = sum(
            interval_overlap(seg0, seg1, layer.get("z_bottom_um", 0.0), layer.get("z_top_um", 0.0))
            for layer in source_layers
        )
        if covered <= 1.0e-9:
            continue
        eps_eff = weighted_dielectric_value(source_layers, seg0, seg1, "epsilon_r", 4.2)
        sigma_eff = weighted_dielectric_value(source_layers, seg0, seg1, "conductivity_s_per_m", 0.0)
        effective_layers.append(
            {
                "name": "effective_gap_{:02d}_{:09.0f}nm_{:09.0f}nm".format(idx, seg0 * 1000.0, seg1 * 1000.0),
                "z_bottom_um": seg0,
                "z_top_um": seg1,
                "epsilon_r": eps_eff,
                "conductivity_s_per_m": sigma_eff,
                "source": "HFSS_DIELECTRIC_EFFECTIVE_MODE=" + mode,
            }
        )
    log(
        "effective_dielectric_layers mode={} count={} layers={}".format(
            mode,
            len(effective_layers),
            json.dumps(effective_layers, sort_keys=True),
        )
    )
    return effective_layers


def attr(name, material, color, transparency, solve_inside=True):
    return [
        "NAME:Attributes",
        "Name:=", name,
        "Flags:=", "",
        "Color:=", color,
        "Transparency:=", float(transparency),
        "PartCoordinateSystem:=", "Global",
        "UDMId:=", "",
        "MaterialValue:=", q(material),
        "SurfaceMaterialValue:=", q(""),
        "SolveInside:=", bool(solve_inside),
        "ShellElement:=", False,
        "ShellElementThickness:=", "0um",
        "IsMaterialEditable:=", True,
        "UseMaterialAppearance:=", False,
        "IsLightweight:=", False,
    ]


def create_box(o_editor, name, x0, y0, z0, sx, sy, sz, material, color, transparency=0.8, solve_inside=True):
    if sx <= 0 or sy <= 0 or sz <= 0:
        return None
    o_editor.CreateBox(
        [
            "NAME:BoxParameters",
            "XPosition:=", f"{x0:.9f}um",
            "YPosition:=", f"{y0:.9f}um",
            "ZPosition:=", f"{z0:.9f}um",
            "XSize:=", f"{sx:.9f}um",
            "YSize:=", f"{sy:.9f}um",
            "ZSize:=", f"{sz:.9f}um",
        ],
        attr(name, material, color, transparency, solve_inside),
    )
    return name


def subtract_tools_from_blank(o_editor, blank_name, tool_names):
    tool_names = [str(name) for name in tool_names if name]
    if not blank_name or not tool_names:
        return
    try:
        o_editor.Subtract(
            ["NAME:Selections", "Blank Parts:=", str(blank_name), "Tool Parts:=", ",".join(tool_names)],
            ["NAME:SubtractParameters", "KeepOriginals:=", True],
        )
        log("subtracted actual metal tools from " + str(blank_name) + ": " + ",".join(tool_names))
    except Exception:
        log("dielectric metal subtraction failed for " + str(blank_name) + ": " + traceback.format_exc())


def create_rectangular_frame_boxes(o_editor, name, outer_bbox, inner_bbox, z0, thickness, material, color, transparency=0.18, solve_inside=True):
    outer_x0, outer_y0, outer_x1, outer_y1 = [float(v) for v in outer_bbox]
    inner_x0, inner_y0, inner_x1, inner_y1 = [float(v) for v in inner_bbox]
    names = [
        create_box(o_editor, name + "_left", outer_x0, outer_y0, z0, inner_x0 - outer_x0, outer_y1 - outer_y0, thickness, material, color, transparency, solve_inside),
        create_box(o_editor, name + "_right", inner_x1, outer_y0, z0, outer_x1 - inner_x1, outer_y1 - outer_y0, thickness, material, color, transparency, solve_inside),
        create_box(o_editor, name + "_top", inner_x0, inner_y1, z0, inner_x1 - inner_x0, outer_y1 - inner_y1, thickness, material, color, transparency, solve_inside),
        create_box(o_editor, name + "_bottom", inner_x0, outer_y0, z0, inner_x1 - inner_x0, inner_y0 - outer_y0, thickness, material, color, transparency, solve_inside),
    ]
    return [item for item in names if item]


def rectangular_frame_records(name, outer_bbox, inner_bbox):
    outer_x0, outer_y0, outer_x1, outer_y1 = [float(v) for v in outer_bbox]
    inner_x0, inner_y0, inner_x1, inner_y1 = [float(v) for v in inner_bbox]
    records = [
        {"name": name + "_left", "bbox_um": [outer_x0, outer_y0, inner_x0, outer_y1]},
        {"name": name + "_right", "bbox_um": [inner_x1, outer_y0, outer_x1, outer_y1]},
        {"name": name + "_top", "bbox_um": [inner_x0, inner_y1, inner_x1, outer_y1]},
        {"name": name + "_bottom", "bbox_um": [inner_x0, outer_y0, inner_x1, inner_y0]},
    ]
    return [item for item in records if item["bbox_um"][2] > item["bbox_um"][0] and item["bbox_um"][3] > item["bbox_um"][1]]


def non_overlapping_z_segments(z0, z1, blocked_intervals, eps=1.0e-9):
    segments = [(float(z0), float(z1))]
    for block0, block1 in sorted((float(a), float(b)) for a, b in blocked_intervals):
        next_segments = []
        for seg0, seg1 in segments:
            if block1 <= seg0 + eps or block0 >= seg1 - eps:
                next_segments.append((seg0, seg1))
                continue
            if block0 > seg0 + eps:
                next_segments.append((seg0, min(block0, seg1)))
            if block1 < seg1 - eps:
                next_segments.append((max(block1, seg0), seg1))
        segments = next_segments
    return [(seg0, seg1) for seg0, seg1 in segments if seg1 - seg0 > eps]


def clipped_interval(z0, z1, clip0, clip1, eps=1.0e-9):
    seg0 = max(float(z0), float(clip0))
    seg1 = min(float(z1), float(clip1))
    if seg1 - seg0 <= eps:
        return None
    return seg0, seg1


def looks_like_rectangular_frame(poly):
    if poly.get("metal") != "metal5":
        return False
    points = [(round(float(x), 6), round(float(y), 6)) for x, y in poly.get("points_um", [])]
    return len(points) >= 8 and len(set(points)) < len(points)


def set_solve_inside(o_editor, name, solve_inside):
    try:
        o_editor.ChangeProperty(
            [
                "NAME:AllTabs",
                [
                    "NAME:Geometry3DAttributeTab",
                    ["NAME:PropServers", name],
                    ["NAME:ChangedProps", ["NAME:Solve Inside", "Value:=", bool(solve_inside)]],
                ],
            ]
        )
    except Exception:
        log("Solve Inside property update failed for " + name + ": " + traceback.format_exc())


def object_name(value):
    name = getattr(value, "name", None)
    if name:
        return str(name)
    text = str(value)
    if not text or text.lower() in {"none", "true", "false"}:
        return None
    return text


def bboxes_touch_or_overlap(a, b, tol=1.0e-6):
    return not (
        float(a[2]) < float(b[0]) - tol
        or float(b[2]) < float(a[0]) - tol
        or float(a[3]) < float(b[1]) - tol
        or float(b[3]) < float(a[1]) - tol
    )


def connected_components_by_bbox(records):
    remaining = list(records)
    components = []
    while remaining:
        seed = remaining.pop(0)
        component = [seed]
        changed = True
        while changed:
            changed = False
            next_remaining = []
            for candidate in remaining:
                if any(bboxes_touch_or_overlap(candidate["bbox_um"], item["bbox_um"]) for item in component):
                    component.append(candidate)
                    changed = True
                else:
                    next_remaining.append(candidate)
            remaining = next_remaining
        components.append(component)
    return components


def maybe_unite_component(hfss, metal, component):
    names = [record["name"] for record in component if record.get("name")]
    if len(names) < 2:
        return names
    try:
        united = hfss.modeler.unite(names, purge=False, keep_originals=False)
        united_name = object_name(united) or names[0]
        log("united connected " + metal + " component -> " + str(united) + " from " + ",".join(names))
        return [united_name]
    except Exception:
        log("connected unite failed for " + metal + ": " + traceback.format_exc())
        return names


def bbox_union(records):
    bboxes = [record.get("bbox_um", [0, 0, 0, 0]) for record in records]
    return [
        min(float(bbox[0]) for bbox in bboxes),
        min(float(bbox[1]) for bbox in bboxes),
        max(float(bbox[2]) for bbox in bboxes),
        max(float(bbox[3]) for bbox in bboxes),
    ]


def maybe_unite_component_records(hfss, metal, component):
    united_names = maybe_unite_component(hfss, metal, component)
    if len(united_names) == 1:
        return [{"name": united_names[0], "bbox_um": bbox_union(component)}]
    by_name = {record.get("name"): record for record in component}
    return [
        {"name": name, "bbox_um": by_name.get(name, {"bbox_um": bbox_union(component)})["bbox_um"]}
        for name in united_names
    ]


def point_in_bbox(point, bbox, tol=1.0e-6):
    x, y = float(point[0]), float(point[1])
    return (
        float(bbox[0]) - tol <= x <= float(bbox[2]) + tol
        and float(bbox[1]) - tol <= y <= float(bbox[3]) + tol
    )


def bbox_distance_to_point(point, bbox):
    x, y = float(point[0]), float(point[1])
    dx = max(float(bbox[0]) - x, 0.0, x - float(bbox[2]))
    dy = max(float(bbox[1]) - y, 0.0, y - float(bbox[3]))
    return math.hypot(dx, dy)


def bbox_area(bbox):
    return max(float(bbox[2]) - float(bbox[0]), 0.0) * max(float(bbox[3]) - float(bbox[1]), 0.0)


def is_power_line_port_role(role):
    text = str(role or "").strip().lower()
    return text in {"left_power_top", "left_power_bottom", "right_power_top", "right_power_bottom"}


def port_reference_mode_for_role(role):
    if is_power_line_port_role(role) and POWER_LINE_PORT_REFERENCE_MODE:
        return POWER_LINE_PORT_REFERENCE_MODE
    return PORT_REFERENCE_MODE


def port_reference_conductors(port, m5_records, all_m5_names):
    reference_mode = port_reference_mode_for_role(port.get("role", ""))
    if reference_mode in {"", "all", "all_m5", "global_m5"}:
        return list(all_m5_names), "all_m5"
    supported_modes = {
        "local",
        "local_ground",
        "local_ground_bbox",
        "local_ground_single",
        "local_ground_bbox_smallest",
        "local_ground_bbox_largest",
    }
    if reference_mode not in supported_modes:
        raise ValueError("Unsupported HFSS port reference mode=" + repr(reference_mode))
    ground = port["ground_label"]["origin_um"]
    containing_records = [
        record
        for record in m5_records
        if record.get("name") and point_in_bbox(ground, record.get("bbox_um", [0, 0, 0, 0]))
    ]
    if reference_mode in {"local_ground_single", "local_ground_bbox_smallest"} and containing_records:
        selected = sorted(containing_records, key=lambda record: bbox_area(record.get("bbox_um", [0, 0, 0, 0])))[0]
        return [selected["name"]], "local_ground_bbox_smallest"
    if reference_mode == "local_ground_bbox_largest" and containing_records:
        selected = sorted(containing_records, key=lambda record: bbox_area(record.get("bbox_um", [0, 0, 0, 0])), reverse=True)[0]
        return [selected["name"]], "local_ground_bbox_largest"
    containing = [record["name"] for record in containing_records]
    if containing:
        return containing, "local_ground_bbox"
    if REQUIRE_LOCAL_GROUND_REFERENCE:
        raise ValueError(
            "No explicit local M5 ground reference found for "
            + str(port.get("port_name", ""))
            + " / "
            + str(port.get("ground_name", ""))
            + "; refusing nearest/global M5 fallback for final EMX-HFSS validation."
        )
    nearest = sorted(
        [record for record in m5_records if record.get("name")],
        key=lambda record: bbox_distance_to_point(ground, record.get("bbox_um", [0, 0, 0, 0])),
    )
    if nearest:
        return [nearest[0]["name"]], "local_ground_bbox_nearest_fallback"
    return list(all_m5_names), "local_ground_bbox_fallback_all_m5"


def validate_port_reference_conductors(port, reference_conductors):
    if PORT_REFERENCE_EXPECTED_COUNT <= 0:
        return
    actual = len([item for item in reference_conductors if item])
    if actual != PORT_REFERENCE_EXPECTED_COUNT:
        raise ValueError(
            "HFSS port "
            + str(port.get("port_name", ""))
            + " expected "
            + str(PORT_REFERENCE_EXPECTED_COUNT)
            + " local M5 reference conductor(s), got "
            + str(actual)
            + ": "
            + ",".join(reference_conductors)
        )


def assign_m5_shield_boundary(o_design, object_names):
    mode = M5_SHIELD_BOUNDARY
    if mode in {"", "finite", "finite_conductor", "none", "off"}:
        log("m5_shield_boundary=finite_conductor_no_explicit_ground")
        return None
    if mode not in {"perfecte", "perfect_e", "ground", "ideal_ground"}:
        raise ValueError("Unsupported HFSS_M5_SHIELD_BOUNDARY=" + repr(mode))
    names = [name for name in (object_name(item) for item in object_names) if name]
    if not names:
        raise ValueError("HFSS_M5_SHIELD_BOUNDARY requested but no metal5 objects are available")
    boundary_name = "M5_Grounded_Shield"
    o_design.GetModule("BoundarySetup").AssignPerfectE(
        [
            "NAME:" + boundary_name,
            "Objects:=", names,
            "InfGroundPlane:=", False,
        ]
    )
    log("m5_shield_boundary=perfecte objects=" + ",".join(names))
    return {"mode": "perfecte", "boundary_name": boundary_name, "objects": names}


def create_poly_sheet(modeler, name, points, z, material, color, transparency, solve_inside=True):
    points = [(float(x), float(y)) for x, y in points]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    points_3d = [[f"{x:.9f}um", f"{y:.9f}um", f"{float(z):.9f}um"] for x, y in points]
    obj = modeler.create_polyline(
        points_3d,
        cover_surface=True,
        close_surface=True,
        name=name,
        material=material,
        xsection_type="None",
    )
    if obj:
        try:
            obj.color = color
            obj.transparency = transparency
        except Exception:
            log("polyline style failed for " + name + ": " + traceback.format_exc())
    return name


def thicken(o_editor, name, thickness_um):
    o_editor.ThickenSheet(
        ["NAME:Selections", "Selections:=", name, "NewPartsModelFlag:=", "Model"],
        ["NAME:SheetThickenParameters", "Thickness:=", f"{float(thickness_um):.9f}um", "BothSides:=", False, "ReplaceOriginal:=", True],
    )


def clamp(value, lower, upper):
    return max(float(lower), min(float(upper), float(value)))


def role_outward_vector(role):
    text = str(role or "").strip().lower()
    if text in {"primary_top", "primary_bottom"}:
        return -1.0, 0.0
    if text in {"secondary_top", "secondary_bottom"}:
        return 1.0, 0.0
    if text.endswith("_top"):
        return 0.0, 1.0
    if text.endswith("_bottom"):
        return 0.0, -1.0
    return 0.0, 0.0


def point_in_xy_bbox(point, bbox, tol=1.0e-6):
    x, y = float(point[0]), float(point[1])
    return (
        float(bbox[0]) - tol <= x <= float(bbox[2]) + tol
        and float(bbox[1]) - tol <= y <= float(bbox[3]) + tol
    )


def distance_to_xy_bbox(point, bbox):
    x, y = float(point[0]), float(point[1])
    dx = max(float(bbox[0]) - x, 0.0, x - float(bbox[2]))
    dy = max(float(bbox[1]) - y, 0.0, y - float(bbox[3]))
    return math.hypot(dx, dy)


def payload_signal_bbox_for_port(port):
    signal = port["signal_label"]["origin_um"]
    metal = str(port.get("signal_metal", ""))
    candidates = [
        poly
        for poly in PAYLOAD.get("conductor_polygons", [])
        if str(poly.get("metal", "")) == metal
        and poly.get("bbox_um")
        and point_in_xy_bbox(signal, poly.get("bbox_um"))
    ]
    if not candidates:
        candidates = [
            poly
            for poly in PAYLOAD.get("conductor_polygons", [])
            if str(poly.get("metal", "")) == metal and poly.get("bbox_um")
        ]
        candidates = sorted(candidates, key=lambda poly: distance_to_xy_bbox(signal, poly.get("bbox_um")))
    if not candidates:
        return None
    return list(sorted(candidates, key=lambda poly: bbox_area(poly.get("bbox_um", [0, 0, 0, 0])))[0]["bbox_um"])


def edge_contact_signal_xy(port):
    signal = port["signal_label"]["origin_um"]
    sx, sy = float(signal[0]), float(signal[1])
    vx, vy = role_outward_vector(port.get("role", ""))
    bbox = payload_signal_bbox_for_port(port)
    if bbox is None or (abs(vx) <= 1.0e-12 and abs(vy) <= 1.0e-12):
        return sx, sy, {"mode": "label_center_fallback", "reason": "missing_bbox_or_role_vector"}
    x0, y0, x1, y1 = [float(value) for value in bbox]
    if abs(vx) >= abs(vy):
        x = x0 - PORT_EDGE_EPS_UM if vx < 0 else x1 + PORT_EDGE_EPS_UM
        y = clamp(sy, y0, y1)
        tangent = [0.0, 1.0]
    else:
        x = clamp(sx, x0, x1)
        y = y0 - PORT_EDGE_EPS_UM if vy < 0 else y1 + PORT_EDGE_EPS_UM
        tangent = [1.0, 0.0]
    return x, y, {
        "mode": "edge_contact",
        "role_outward_vector": [vx, vy],
        "signal_bbox_um": bbox,
        "tangent_xy": tangent,
        "edge_eps_um": PORT_EDGE_EPS_UM,
    }


def computed_port_geometry(port):
    cached = port.get("_hfss_port_geometry")
    if cached:
        return cached
    signal = port["signal_label"]["origin_um"]
    ground = port["ground_label"]["origin_um"]
    sx, sy = float(signal[0]), float(signal[1])
    gx, gy = float(ground[0]), float(ground[1])
    z_signal = resolved_port_signal_z(port)
    z_ground = resolved_port_ground_z(port)
    width = float(port["port_sheet_width_um"])
    geometry_mode = str(PORT_GEOMETRY_MODE or "label_center").strip().lower()
    metadata = {"mode": geometry_mode}
    if geometry_mode in {"edge", "edge_contact", "side_edge", "signal_edge"}:
        sx, sy, edge_meta = edge_contact_signal_xy(port)
        gx, gy = sx, sy
        metadata.update(edge_meta)
        tangent = edge_meta.get("tangent_xy", [0.0, 1.0])
        ux, uy = float(tangent[0]), float(tangent[1])
    else:
        dx, dy = gx - sx, gy - sy
        length = math.hypot(dx, dy)
        if length <= 1.0e-9:
            axis = str(port.get("port_sheet_axis", "y")).strip().lower()
            if axis == "x":
                ux, uy = 1.0, 0.0
            else:
                ux, uy = 0.0, 1.0
        else:
            ux, uy = -dy / length, dx / length
        metadata["mode"] = "label_center"
    half = 0.5 * width
    points = [
        (sx + ux * half, sy + uy * half, z_signal),
        (sx - ux * half, sy - uy * half, z_signal),
        (gx - ux * half, gy - uy * half, z_ground),
        (gx + ux * half, gy + uy * half, z_ground),
    ]
    result = {
        "signal_xyz_um": [sx, sy, z_signal],
        "ground_xyz_um": [gx, gy, z_ground],
        "points": points,
        "metadata": metadata,
    }
    port["_hfss_port_geometry"] = result
    return result


def create_port_sheet(modeler, port):
    geometry = computed_port_geometry(port)
    points = geometry["points"]
    name = "port_sheet_" + port["port_name"]
    obj = modeler.create_polyline(
        [[f"{x:.9f}um", f"{y:.9f}um", f"{z:.9f}um"] for x, y, z in points],
        cover_surface=True,
        close_surface=True,
        name=name,
        material="vacuum",
        xsection_type="None",
    )
    if obj:
        try:
            obj.color = "(0 180 220)"
            obj.transparency = 0.45
        except Exception:
            log("port sheet style failed for " + name + ": " + traceback.format_exc())
    return name


def assign_lumped_port(o_design, sheet_name, port):
    geometry = computed_port_geometry(port)
    sx, sy, z_signal = geometry["signal_xyz_um"]
    gx, gy, z_ground = geometry["ground_xyz_um"]
    module = o_design.GetModule("BoundarySetup")
    module.AssignLumpedPort(
        [
            "NAME:" + port["port_name"],
            "Objects:=", [sheet_name],
            "RenormalizeAllTerminals:=", True,
            "DoDeembed:=", PORT_DEEMBED,
            [
                "NAME:Modes",
                [
                    "NAME:Mode1",
                    "ModeNum:=", 1,
                    "UseIntLine:=", True,
                    ["NAME:IntLine", "Start:=", [f"{sx:.9f}um", f"{sy:.9f}um", f"{z_signal:.9f}um"], "End:=", [f"{gx:.9f}um", f"{gy:.9f}um", f"{z_ground:.9f}um"]],
                    "AlignmentGroup:=", 0,
                    "CharImp:=", "Zpi",
                    *mode_renorm_imp_props(),
                ],
            ],
            "ShowReporterFilter:=", False,
            "ReporterFilter:=", [True],
            "Impedance:=", "50ohm",
        ]
    )


def assign_port(hfss, o_design, sheet_name, port, reference_conductors):
    geometry = computed_port_geometry(port)
    sx, sy, z_signal = geometry["signal_xyz_um"]
    gx, gy, z_ground = geometry["ground_xyz_um"]
    solution_type = str(PAYLOAD.get("hfss", {}).get("solution_type", "")).lower()
    if USE_PYAEDT_REFERENCE_PORT and "terminal" in solution_type and reference_conductors:
        boundary = hfss.lumped_port(
            assignment=sheet_name,
            reference=reference_conductors,
            create_port_sheet=False,
            integration_line=[
                [f"{sx:.9f}um", f"{sy:.9f}um", f"{z_signal:.9f}um"],
                [f"{gx:.9f}um", f"{gy:.9f}um", f"{z_ground:.9f}um"],
            ],
            impedance=50,
            name=port["port_name"],
            renormalize=True,
            deembed=PORT_DEEMBED,
            terminals_rename=True,
            auto_identify=False,
        )
        log("assigned terminal reference port " + port["port_name"] + " boundary=" + str(boundary) + " refs=" + ",".join(reference_conductors))
        return "terminal_reference"
    assign_lumped_port(o_design, sheet_name, port)
    log("assigned direct integration-line lumped port " + port["port_name"] + " refs_checked=" + ",".join(reference_conductors))
    return "direct_lumped_integration_line"


def port_manifest_record(port, sheet_name):
    geometry = computed_port_geometry(port)
    sx, sy, z_signal = geometry["signal_xyz_um"]
    gx, gy, z_ground = geometry["ground_xyz_um"]
    return {
        "port_name": port["port_name"],
        "role": port.get("role", ""),
        "ground_name": port.get("ground_name", ""),
        "sheet_name": sheet_name,
        "signal_metal": port.get("signal_metal", ""),
        "ground_metal": port.get("ground_metal", ""),
        "signal_xyz_um": [sx, sy, z_signal],
        "ground_xyz_um": [gx, gy, z_ground],
        "payload_signal_z_um": float(port["signal_z_um"]),
        "payload_ground_z_um": float(port["ground_z_um"]),
        "resolved_signal_z_mode": PORT_SIGNAL_Z_MODE,
        "resolved_ground_z_mode": PORT_GROUND_Z_MODE,
        "port_deembed": PORT_DEEMBED,
        "include_mode_renorm_imp": PORT_MODE_RENORM_IMP,
        "renorm_impedance": PORT_RENORM_IMPEDANCE if PORT_MODE_RENORM_IMP else "",
        "integration_line": {
            "start_xyz_um": [sx, sy, z_signal],
            "end_xyz_um": [gx, gy, z_ground],
        },
        "port_geometry_mode": PORT_GEOMETRY_MODE,
        "port_geometry_metadata": geometry.get("metadata", {}),
        "port_sheet_width_um": float(port["port_sheet_width_um"]),
        "port_sheet_axis": port.get("port_sheet_axis", ""),
    }


def effective_hfss_env_manifest():
    names = [
        "HFSS_CONDUCTOR_SOLVE_INSIDE",
        "HFSS_M5_SHIELD_BOUNDARY",
        "HFSS_DIELECTRIC_CONDUCTIVITY_MODE",
        "HFSS_DIELECTRIC_EFFECTIVE_MODE",
        "HFSS_UNITE_STRATEGY",
        "HFSS_UNITE_BY_METAL",
        "HFSS_UNITE_CONNECTED_M5",
        "HFSS_PORT_REFERENCE_MODE",
        "HFSS_POWER_LINE_PORT_REFERENCE_MODE",
        "HFSS_PORT_REFERENCE_EXPECTED_COUNT",
        "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE",
        "HFSS_USE_PYAEDT_REFERENCE_PORT",
        "HFSS_SKIP_PIN_CONDUCTORS",
        "HFSS_PORT_GEOMETRY_MODE",
        "HFSS_PORT_EDGE_EPS_UM",
        "HFSS_PORT_SIGNAL_Z_MODE",
        "HFSS_PORT_GROUND_Z_MODE",
        "HFSS_PORT_DEEMBED",
        "HFSS_PORT_MODE_RENORM_IMP",
        "HFSS_PORT_RENORM_IMPEDANCE",
        "HFSS_AIR_MARGIN_UM",
        "HFSS_RADIATION_MARGIN_UM",
        "HFSS_AIR_BELOW_UM",
        "HFSS_AIR_ABOVE_UM",
        "HFSS_DIELECTRIC_XY_MARGIN_UM",
        "HFSS_DIELECTRIC_Z_MIN_UM",
        "HFSS_DIELECTRIC_Z_MAX_UM",
        "HFSS_DIELECTRIC_METAL_CAVITY_MODE",
        "HFSS_SETUP_MAX_DELTA_S",
        "HFSS_SETUP_MAX_PASSES",
        "HFSS_SETUP_MIN_PASSES",
        "HFSS_SETUP_MIN_CONVERGED_PASSES",
        "HFSS_SETUP_PERCENT_REFINEMENT",
        "HFSS_SETUP_BASIS_ORDER",
        "HFSS_SETUP_PORT_ACCURACY",
        "HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY",
        "HFSS_SWEEP_TYPE",
    ]
    fallback_defaults = {
        "HFSS_DIELECTRIC_Z_MIN_UM": "stack_min",
        "HFSS_DIELECTRIC_Z_MAX_UM": "stack_max",
    }
    return {
        name: {
            "value": env_str(name, fallback_defaults.get(name, "")),
            "source": "environment" if name in os.environ else "payload_calibration_default",
        }
        for name in names
    }


def main():
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    if PORT_MANIFEST_PATH.exists():
        PORT_MANIFEST_PATH.unlink()
    log("HFSS S8P build started")
    project_path = os.environ.get("HFSS_SAVE_PATH", str(SCRIPT_DIR / (PAYLOAD["hfss"]["project_name"] + ".aedt")))
    hfss = Hfss(
        project=project_path,
        design=PAYLOAD["hfss"]["design_name"],
        solution_type=PAYLOAD["hfss"]["solution_type"],
        version=PAYLOAD["hfss"]["version"],
        non_graphical=True,
        new_desktop=True,
        close_on_exit=True,
        remove_lock=True,
    )
    hfss.modeler.model_units = "um"
    o_project = hfss.oproject
    o_design = hfss.odesign
    o_editor = hfss.modeler.oeditor

    stack = PAYLOAD["stack"]
    conductors = stack["conductors"]
    log("calibration_profile=" + json.dumps(CALIBRATION_PROFILE, sort_keys=True))
    log("calibration_env_defaults=" + json.dumps(CALIBRATION_ENV_DEFAULTS, sort_keys=True))
    log("conductor_solve_inside=" + repr(CONDUCTOR_SOLVE_INSIDE))
    log("m5_shield_boundary_requested=" + repr(M5_SHIELD_BOUNDARY))
    log("dielectric_conductivity_mode=" + repr(DIELECTRIC_CONDUCTIVITY_MODE))
    log("dielectric_effective_mode=" + repr(DIELECTRIC_EFFECTIVE_MODE))
    log("dielectric_metal_cavity_mode=" + repr(DIELECTRIC_METAL_CAVITY_MODE))
    log("unite_strategy=" + repr(UNITE_STRATEGY))
    log("unite_connected_m5=" + repr(UNITE_CONNECTED_M5))
    log("port_reference_mode=" + repr(PORT_REFERENCE_MODE))
    log("power_line_port_reference_mode=" + repr(POWER_LINE_PORT_REFERENCE_MODE))
    log("port_reference_expected_count=" + repr(PORT_REFERENCE_EXPECTED_COUNT))
    log("require_local_ground_reference=" + repr(REQUIRE_LOCAL_GROUND_REFERENCE))
    log("skip_pin_conductors=" + repr(SKIP_PIN_CONDUCTORS))
    log("port_geometry_mode=" + repr(PORT_GEOMETRY_MODE))
    log("port_edge_eps_um=" + repr(PORT_EDGE_EPS_UM))
    log("port_signal_z_mode=" + repr(PORT_SIGNAL_Z_MODE))
    log("port_ground_z_mode=" + repr(PORT_GROUND_Z_MODE))
    log("port_deembed=" + repr(PORT_DEEMBED))
    log("port_mode_renorm_imp=" + repr(PORT_MODE_RENORM_IMP))
    log("port_renorm_impedance=" + repr(PORT_RENORM_IMPEDANCE))
    for metal, conductor in conductors.items():
        sigma = conductor.get("conductivity_s_per_m")
        if sigma:
            add_material(o_project, "proc_" + metal + "_equiv", eps="1", conductivity=sigma)
    add_material(o_project, "proc_diel_eps4p2", eps="4.2", conductivity=0.0)
    add_material(o_project, "air", eps="1", conductivity=0.0)

    bbox = PAYLOAD["bbox_um"]
    margin = float(env_str("HFSS_AIR_MARGIN_UM", "250"))
    x0, y0, x1, y1 = bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin
    dielectric_xy_margin_raw = env_str("HFSS_DIELECTRIC_XY_MARGIN_UM", "")
    dielectric_xy_margin = margin if not dielectric_xy_margin_raw else float(dielectric_xy_margin_raw)
    diel_x0 = bbox[0] - dielectric_xy_margin
    diel_y0 = bbox[1] - dielectric_xy_margin
    diel_x1 = bbox[2] + dielectric_xy_margin
    diel_y1 = bbox[3] + dielectric_xy_margin
    log("dielectric_xy_margin_um=" + repr(dielectric_xy_margin))
    conductor_intervals = [
        (float(conductor["z_bottom_um"]), float(conductor["z_top_um"]))
        for conductor in conductors.values()
        if float(conductor["z_top_um"]) > float(conductor["z_bottom_um"])
    ]
    full_dielectric_z_min = min(float(layer.get("z_bottom_um", 0.0)) for layer in stack.get("dielectrics", [{"z_bottom_um": 0.0}]))
    full_dielectric_z_max = max(float(layer.get("z_top_um", 0.0)) for layer in stack.get("dielectrics", [{"z_top_um": 0.0}]))
    dielectric_z_min = float(env_str("HFSS_DIELECTRIC_Z_MIN_UM", str(full_dielectric_z_min)))
    dielectric_z_max = float(env_str("HFSS_DIELECTRIC_Z_MAX_UM", str(full_dielectric_z_max)))
    log("dielectric_z_window_um=" + repr([dielectric_z_min, dielectric_z_max]))
    dielectric_layers_to_create = effective_dielectric_layers(stack, conductors, dielectric_z_min, dielectric_z_max)

    metal_colors = {"metal5": "(80 80 80)", "metal9": "(37 111 186)", "metal10": "(207 76 49)"}
    conductor_objects_by_metal = {metal: [] for metal in conductors}
    conductor_records_by_metal = {metal: [] for metal in conductors}
    for poly in PAYLOAD["conductor_polygons"]:
        metal = poly["metal"]
        if SKIP_PIN_CONDUCTORS and str(poly.get("role", "")).endswith("_pin"):
            log(
                "skipped pin-purpose conductor polygon "
                + str(poly.get("index"))
                + " role="
                + str(poly.get("role", ""))
            )
            continue
        conductor = conductors[metal]
        name = "{}_poly_{:03d}".format(metal, int(poly["index"]))
        if looks_like_rectangular_frame(poly):
            power_geom = PAYLOAD.get("power_line_8port_geometry", {})
            inner = power_geom.get("shield_inner_bbox_um")
            outer = power_geom.get("shield_outer_bbox_um")
            if not inner or not outer:
                raise ValueError("rectangular M5 frame detected but shield bbox evidence is missing")
            frame_names = create_rectangular_frame_boxes(
                o_editor,
                name,
                [outer["min_x_um"], outer["min_y_um"], outer["max_x_um"], outer["max_y_um"]],
                [inner["min_x_um"], inner["min_y_um"], inner["max_x_um"], inner["max_y_um"]],
                float(conductor["z_bottom_um"]),
                float(conductor["thickness_um"]),
                "proc_" + metal + "_equiv",
                metal_colors.get(metal, "(160 160 160)"),
                0.18,
                CONDUCTOR_SOLVE_INSIDE,
            )
            conductor_objects_by_metal[metal].extend(frame_names)
            conductor_records_by_metal[metal].extend(
                [record for record in rectangular_frame_records(name, [outer["min_x_um"], outer["min_y_um"], outer["max_x_um"], outer["max_y_um"]], [inner["min_x_um"], inner["min_y_um"], inner["max_x_um"], inner["max_y_um"]]) if record["name"] in frame_names]
            )
            log("created rectangular M5 frame boxes from shield bbox for " + name)
            continue
        create_poly_sheet(hfss.modeler, name, poly["points_um"], float(conductor["z_bottom_um"]), "proc_" + metal + "_equiv", metal_colors.get(metal, "(160 160 160)"), 0.18, CONDUCTOR_SOLVE_INSIDE)
        thicken(o_editor, name, float(conductor["thickness_um"]))
        set_solve_inside(o_editor, name, CONDUCTOR_SOLVE_INSIDE)
        conductor_objects_by_metal[metal].append(name)
        conductor_records_by_metal[metal].append({"name": name, "bbox_um": poly["bbox_um"]})

    if UNITE_STRATEGY == "all_by_metal":
        for metal, object_names in conductor_objects_by_metal.items():
            if len(object_names) < 2:
                continue
            try:
                united = hfss.modeler.unite(object_names, purge=False, keep_originals=False)
                united_name = object_name(united) or object_names[0]
                conductor_objects_by_metal[metal] = [united_name]
                log("united " + metal + " objects -> " + str(united) + " from " + ",".join(object_names))
            except Exception:
                log("unite failed for " + metal + ": " + traceback.format_exc())
    elif UNITE_STRATEGY in {"connected", "connected_by_bbox", "bbox_connected"}:
        for metal, records in conductor_records_by_metal.items():
            if metal == "metal5" and not UNITE_CONNECTED_M5:
                log("connected unite skipped for metal5 reference frame")
                continue
            component_records = []
            for component in connected_components_by_bbox(records):
                component_records.extend(maybe_unite_component_records(hfss, metal, component))
            if component_records:
                conductor_records_by_metal[metal] = component_records
                conductor_objects_by_metal[metal] = [record["name"] for record in component_records]
        log("completed connected_by_bbox unite strategy")
    elif UNITE_STRATEGY in {"none", "off", "no_unite"}:
        log("skipped unite; keeping individual conductor polygons for terminal-reference audit")
    else:
        raise ValueError("Unsupported HFSS_UNITE_STRATEGY=" + repr(UNITE_STRATEGY))

    dielectric_cavity_mode = DIELECTRIC_METAL_CAVITY_MODE
    if dielectric_cavity_mode not in {"z_interval_clear", "subtract_actual_metals"}:
        raise ValueError("Unsupported HFSS_DIELECTRIC_METAL_CAVITY_MODE=" + repr(DIELECTRIC_METAL_CAVITY_MODE))
    for idx, layer in enumerate(dielectric_layers_to_create):
        material = "proc_diel_{:02d}".format(idx)
        dielectric_loss = dielectric_material_loss(layer)
        if float(layer.get("conductivity_s_per_m", 0.0) or 0.0) > 0:
            log("dielectric_loss_model layer={} sigma={} mode={} tan_delta={}".format(
                str(layer.get("name")),
                layer.get("conductivity_s_per_m"),
                dielectric_loss["mode"],
                dielectric_loss["dielectric_loss_tangent"],
            ))
        add_material(
            o_project,
            material,
            eps=str(layer["epsilon_r"]),
            conductivity=dielectric_loss["conductivity"],
            dielectric_loss_tangent=dielectric_loss["dielectric_loss_tangent"],
        )
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in str(layer["name"]))
        clipped = clipped_interval(layer["z_bottom_um"], layer["z_top_um"], dielectric_z_min, dielectric_z_max)
        if clipped is None:
            continue
        if dielectric_cavity_mode == "subtract_actual_metals":
            dielectric_segments = [clipped]
        else:
            dielectric_segments = non_overlapping_z_segments(clipped[0], clipped[1], conductor_intervals)
        for seg_idx, (seg0, seg1) in enumerate(dielectric_segments):
            dielectric_name = create_box(
                o_editor,
                "diel_{:02d}_{:02d}_{}".format(idx, seg_idx, safe_name),
                diel_x0,
                diel_y0,
                seg0,
                diel_x1 - diel_x0,
                diel_y1 - diel_y0,
                seg1 - seg0,
                material,
                "(180 210 255)",
                0.88,
            )
            if dielectric_cavity_mode == "subtract_actual_metals":
                metal_tools = []
                for metal, object_names in conductor_objects_by_metal.items():
                    conductor = conductors.get(metal)
                    if not conductor:
                        continue
                    if interval_overlap(seg0, seg1, conductor["z_bottom_um"], conductor["z_top_um"]) <= 0:
                        continue
                    metal_tools.extend(object_name(item) for item in object_names)
                subtract_tools_from_blank(o_editor, dielectric_name, [item for item in metal_tools if item])

    all_m5_reference_conductors = [
        name for name in (object_name(item) for item in conductor_objects_by_metal.get("metal5", [])) if name
    ]
    m5_boundary_record = assign_m5_shield_boundary(o_design, all_m5_reference_conductors)

    port_manifest_records = []
    for port in PAYLOAD["ports"]:
        sheet_name = create_port_sheet(hfss.modeler, port)
        port_refs, port_reference_mode_used = port_reference_conductors(
            port,
            conductor_records_by_metal.get("metal5", []),
            all_m5_reference_conductors,
        )
        validate_port_reference_conductors(port, port_refs)
        assignment_mode = assign_port(hfss, o_design, sheet_name, port, port_refs)
        record = port_manifest_record(port, sheet_name)
        record["assignment_mode"] = assignment_mode
        record["reference_conductors"] = port_refs
        record["port_reference_mode"] = port_reference_mode_used
        port_manifest_records.append(record)
        log("assigned port " + port["port_name"] + " role=" + str(port.get("role", "")))
    PORT_MANIFEST_PATH.write_text(
        json.dumps(
            {
                "schema": "rfic_transformer_hfss_s8p_build_port_manifest.v1",
                "payload": str(PAYLOAD_PATH),
                "port_count": len(port_manifest_records),
                "expected_port_order": [f"P{idx:03d}" for idx in range(1, 9)],
                "actual_port_order": [record["port_name"] for record in port_manifest_records],
                "calibration_profile": CALIBRATION_PROFILE,
                "calibration_env_defaults": CALIBRATION_ENV_DEFAULTS,
                "effective_hfss_env": effective_hfss_env_manifest(),
                "m5_shield_boundary": m5_boundary_record
                or {"mode": "finite_conductor", "boundary_name": None, "objects": conductor_objects_by_metal.get("metal5", [])},
                "unite_by_metal": UNITE_BY_METAL,
                "unite_strategy": UNITE_STRATEGY,
                "unite_connected_m5": UNITE_CONNECTED_M5,
                "port_reference_mode": PORT_REFERENCE_MODE,
                "power_line_port_reference_mode": POWER_LINE_PORT_REFERENCE_MODE,
                "port_reference_expected_count": PORT_REFERENCE_EXPECTED_COUNT,
                "require_local_ground_reference": REQUIRE_LOCAL_GROUND_REFERENCE,
                "skip_pin_conductors": SKIP_PIN_CONDUCTORS,
                "port_geometry_mode": PORT_GEOMETRY_MODE,
                "port_edge_eps_um": PORT_EDGE_EPS_UM,
                "port_signal_z_mode": PORT_SIGNAL_Z_MODE,
                "port_ground_z_mode": PORT_GROUND_Z_MODE,
                "dielectric_metal_cavity_mode": DIELECTRIC_METAL_CAVITY_MODE,
                "port_deembed": PORT_DEEMBED,
                "port_mode_renorm_imp": PORT_MODE_RENORM_IMP,
                "port_renorm_impedance": PORT_RENORM_IMPEDANCE if PORT_MODE_RENORM_IMP else "",
                "ports": port_manifest_records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log("wrote port manifest " + str(PORT_MANIFEST_PATH))

    air_margin = float(env_str("HFSS_RADIATION_MARGIN_UM", "350"))
    z_min = dielectric_z_min
    z_max = max(dielectric_z_max, max(float(c["z_top_um"]) for c in conductors.values()))
    air_below = float(env_str("HFSS_AIR_BELOW_UM", "50"))
    air_above = float(env_str("HFSS_AIR_ABOVE_UM", "950"))
    log("airbox_vertical_padding_um=" + repr([air_below, air_above]))
    create_box(o_editor, "airbox_radiation", x0 - air_margin, y0 - air_margin, z_min - air_below, x1 - x0 + 2 * air_margin, y1 - y0 + 2 * air_margin, z_max - z_min + air_below + air_above, "air", "(230 240 255)", 0.95)
    try:
        o_design.GetModule("BoundarySetup").AssignRadiation(["NAME:Rad_AirBox", "Objects:=", ["airbox_radiation"]])
    except Exception:
        log("AssignRadiation failed: " + traceback.format_exc())

    grid = PAYLOAD["frequency_grid"]
    setup = hfss.create_setup(
        name=PAYLOAD["hfss"]["setup_name"],
        setup_type="HFSSDriven",
        Frequency=f"{float(grid['setup_frequency_ghz']):g}GHz",
    )
    setup_overrides = {
        "Frequency": f"{float(grid['setup_frequency_ghz']):g}GHz",
        "MaxDeltaS": float(env_str("HFSS_SETUP_MAX_DELTA_S", "0.02")),
        "MaximumPasses": int(env_str("HFSS_SETUP_MAX_PASSES", "8")),
        "MinimumPasses": int(env_str("HFSS_SETUP_MIN_PASSES", "2")),
        "MinimumConvergedPasses": int(env_str("HFSS_SETUP_MIN_CONVERGED_PASSES", "1")),
        "PercentRefinement": int(env_str("HFSS_SETUP_PERCENT_REFINEMENT", "30")),
        "BasisOrder": int(env_str("HFSS_SETUP_BASIS_ORDER", "1")),
        "PortAccuracy": int(env_str("HFSS_SETUP_PORT_ACCURACY", "2")),
        "EnhancedLowFreqAccuracy": env_bool("HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY", "0"),
    }
    for key, value in setup_overrides.items():
        setup.props[key] = value
    try:
        setup.update()
    except Exception:
        log("setup.update failed after applying " + json.dumps(setup_overrides, sort_keys=True) + ": " + traceback.format_exc())
        raise
    setup_verified = {key: setup.props.get(key) for key in setup_overrides}
    log("setup_overrides_requested=" + json.dumps(setup_overrides, sort_keys=True))
    log("setup_props_after_update=" + json.dumps(setup_verified, sort_keys=True))
    hfss.create_linear_step_sweep(
        setup=PAYLOAD["hfss"]["setup_name"],
        unit="GHz",
        start_frequency=float(grid["start_ghz"]),
        stop_frequency=float(grid["stop_ghz"]),
        step_size=float(grid["step_ghz"]),
        name=PAYLOAD["hfss"]["sweep_name"],
        save_fields=False,
        save_rad_fields=False,
        sweep_type=env_str("HFSS_SWEEP_TYPE", "Discrete"),
    )

    hfss.save_project(project_path)
    log("saved project " + project_path)
    hfss.release_desktop(close_projects=True, close_desktop=True)
    log("released desktop")
    if os.environ.get("HFSS_RUN_SOLVE", "0").strip().lower() in {"1", "true", "yes", "on"}:
        subprocess.check_call([sys.executable, str(SCRIPT_DIR / "solve_export_hfss_s8p.py")])


try:
    main()
except Exception:
    log("FATAL: " + traceback.format_exc())
    raise
'''


def _render_solve_script() -> str:
    return r'''#!/usr/bin/env python3
"""Solve/export the generated HFSS S8P project.

Run only after manually reviewing the geometry created by
`build_hfss_s8p_from_payload.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import traceback
from pathlib import Path

from ansys.aedt.core import Hfss


SCRIPT_DIR = Path(__file__).resolve().parent
PAYLOAD = json.loads((SCRIPT_DIR / "hfss_s8p_build_payload.json").read_text(encoding="utf-8"))
RESULTS_DIR = Path(os.environ.get("HFSS_SOLVE_RESULTS_DIR", SCRIPT_DIR / "hfss_solve_export_results"))
LOG_PATH = Path(os.environ.get("HFSS_SOLVE_LOG", SCRIPT_DIR / "hfss_s8p_solve_export.log"))
EXPORT_MANIFEST_PATH = Path(os.environ.get("HFSS_EXPORT_MANIFEST", SCRIPT_DIR / "hfss_s8p_export_manifest.json"))


def log(message):
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(str(message) + "\n")


def slug(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "sample"


def strip_comment(line):
    return line.split("!", 1)[0].strip()


def touchstone_option(path):
    option = {
        "frequency_unit": "ghz",
        "parameter_kind": "s",
        "format": "ma",
        "reference_ohm": 50.0,
    }
    for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
        line = strip_comment(raw)
        if not line.startswith("#"):
            continue
        tokens = line[1:].strip().lower().split()
        if tokens:
            option["frequency_unit"] = tokens[0]
        if len(tokens) >= 2:
            option["parameter_kind"] = tokens[1]
        if len(tokens) >= 3:
            option["format"] = tokens[2]
        if "r" in tokens:
            idx = tokens.index("r")
            if idx + 1 < len(tokens):
                try:
                    option["reference_ohm"] = float(tokens[idx + 1])
                except ValueError:
                    option["reference_ohm"] = None
        return option
    return option


def frequency_scale(unit):
    return {
        "hz": 1.0,
        "khz": 1.0e3,
        "mhz": 1.0e6,
        "ghz": 1.0e9,
    }.get(str(unit).lower(), 1.0e9)


def read_touchstone_frequencies_hz(path, port_count):
    option = touchstone_option(path)
    values = []
    for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
        line = strip_comment(raw)
        if not line or line.startswith("#") or line.startswith("["):
            continue
        for token in line.replace("D", "E").replace("d", "e").split():
            try:
                values.append(float(token))
            except ValueError:
                pass
    block_len = 1 + 2 * port_count * port_count
    freqs = []
    idx = 0
    scale = frequency_scale(option["frequency_unit"])
    while idx + block_len <= len(values):
        freqs.append(values[idx] * scale)
        idx += block_len
    return freqs, option, len(values) - idx


def inspect_s8p(path):
    path = Path(path)
    result = {
        "path": str(path),
        "exists": path.is_file(),
        "suffix": path.suffix.lower(),
        "port_count": None,
        "option": {},
        "frequency_point_count": 0,
        "start_ghz": None,
        "stop_ghz": None,
        "step_ghz": None,
        "trailing_numeric_token_count": None,
        "status": "FAIL",
        "reasons": [],
    }
    if not path.is_file():
        result["reasons"].append("file_not_found")
        return result
    match = re.search(r"\.s(\d+)p$", path.name.lower())
    result["port_count"] = int(match.group(1)) if match else None
    if result["suffix"] != ".s8p":
        result["reasons"].append("suffix_not_s8p")
    if result["port_count"] != 8:
        result["reasons"].append("port_count_not_8")
    freqs, option, trailing = read_touchstone_frequencies_hz(path, 8)
    result["option"] = option
    result["frequency_point_count"] = len(freqs)
    result["trailing_numeric_token_count"] = trailing
    if option.get("parameter_kind") != "s":
        result["reasons"].append("parameter_not_s")
    ref = option.get("reference_ohm")
    if ref is None or abs(float(ref) - 50.0) > 1.0e-9:
        result["reasons"].append("reference_not_50_ohm")
    grid = PAYLOAD["frequency_grid"]
    expected_points = int(grid["points"])
    expected_start = float(grid["start_ghz"]) * 1.0e9
    expected_stop = float(grid["stop_ghz"]) * 1.0e9
    expected_step = float(grid["step_ghz"]) * 1.0e9
    tol = float(os.environ.get("HFSS_EXPORT_FREQ_TOL_HZ", "100000"))
    if freqs:
        result["start_ghz"] = freqs[0] / 1.0e9
        result["stop_ghz"] = freqs[-1] / 1.0e9
        result["step_ghz"] = None if len(freqs) < 2 else (freqs[1] - freqs[0]) / 1.0e9
    if len(freqs) != expected_points:
        result["reasons"].append("frequency_point_count_mismatch")
    if not freqs or abs(freqs[0] - expected_start) > tol:
        result["reasons"].append("frequency_start_mismatch")
    if not freqs or abs(freqs[-1] - expected_stop) > tol:
        result["reasons"].append("frequency_stop_mismatch")
    if len(freqs) >= 2 and any(abs((freqs[i + 1] - freqs[i]) - expected_step) > tol for i in range(len(freqs) - 1)):
        result["reasons"].append("frequency_step_mismatch")
    if trailing:
        result["reasons"].append("incomplete_touchstone_numeric_block")
    result["status"] = "PASS" if not result["reasons"] else "FAIL"
    return result


def exported_s8p_candidates():
    return sorted(RESULTS_DIR.rglob("*.s8p"), key=lambda path: (-path.stat().st_size, str(path)))


def validate_exported_s8p(exported):
    candidates = exported_s8p_candidates()
    inspections = [inspect_s8p(path) for path in candidates]
    passing = [item for item in inspections if item["status"] == "PASS"]
    manifest = {
        "schema": "rfic_transformer_hfss_s8p_export_manifest.v1",
        "expected_touchstone_suffix": ".s8p",
        "expected_port_count": 8,
        "expected_reference_ohm": 50.0,
        "expected_frequency_grid": PAYLOAD["frequency_grid"],
        "pyAEDT_export_results_return": repr(exported),
        "result_dir": str(RESULTS_DIR),
        "candidate_count": len(candidates),
        "candidates": inspections,
        "selected_s8p": "",
        "status": "FAIL",
    }
    if passing:
        selected = Path(passing[0]["path"])
        stable = RESULTS_DIR / (slug(PAYLOAD.get("sample_id") or PAYLOAD["hfss"]["project_name"]) + "_hfss_export.s8p")
        if selected.resolve() != stable.resolve():
            shutil.copy2(selected, stable)
            selected = stable
            passing[0] = inspect_s8p(selected)
        manifest["selected_s8p"] = str(selected)
        manifest["status"] = "PASS"
    EXPORT_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log("export_manifest=" + str(EXPORT_MANIFEST_PATH))
    log("export_manifest_status=" + manifest["status"])
    if manifest["status"] != "PASS":
        raise RuntimeError("HFSS export did not produce a valid .s8p matching EMX contract: " + json.dumps(manifest, indent=2))
    return manifest


def main():
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    if EXPORT_MANIFEST_PATH.exists():
        EXPORT_MANIFEST_PATH.unlink()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    source_project = Path(os.environ.get("HFSS_SAVE_PATH", SCRIPT_DIR / (PAYLOAD["hfss"]["project_name"] + ".aedt")))
    solve_project = Path(os.environ.get("HFSS_SOLVE_PROJECT", SCRIPT_DIR / (PAYLOAD["hfss"]["project_name"] + "_solve.aedt")))
    if not source_project.exists():
        raise FileNotFoundError(source_project)
    if solve_project.exists():
        solve_project.unlink()
    if solve_project.with_name(solve_project.name + "results").is_dir():
        shutil.rmtree(solve_project.with_name(solve_project.name + "results"), ignore_errors=True)
    shutil.copy2(source_project, solve_project)
    hfss = Hfss(
        project=str(solve_project),
        design=PAYLOAD["hfss"]["design_name"],
        solution_type=PAYLOAD["hfss"]["solution_type"],
        version=PAYLOAD["hfss"]["version"],
        non_graphical=True,
        new_desktop=True,
        close_on_exit=True,
        remove_lock=True,
    )
    try:
        log("boundaries=" + repr(list(hfss.odesign.GetModule("BoundarySetup").GetBoundaries())))
        log("excitations=" + repr(list(hfss.odesign.GetModule("BoundarySetup").GetExcitations())))
    except Exception:
        log("boundary query failed: " + traceback.format_exc())
    ok = hfss.analyze_setup(PAYLOAD["hfss"]["setup_name"], cores=int(os.environ.get("HFSS_SOLVE_CORES", "4")), tasks=1, blocking=True)
    log("analyze_setup_return=" + repr(ok))
    exported = hfss.export_results(export_folder=str(RESULTS_DIR), touchstone_format="RealImag")
    log("export_results=" + repr(exported))
    export_manifest = validate_exported_s8p(exported)
    log("validated_hfss_s8p=" + export_manifest["selected_s8p"])
    hfss.save_project(str(solve_project))
    hfss.release_desktop(close_projects=True, close_desktop=True)
    log("expected_touchstone_suffix=.s8p")


try:
    main()
except Exception:
    log("FATAL: " + traceback.format_exc())
    raise
'''


def _render_sample_readme(payload: dict[str, Any]) -> str:
    calibration_profile = ((payload.get("hfss") or {}).get("calibration_profile") or {})
    calibration_env_defaults = (payload.get("hfss") or {}).get("calibration_env_defaults") or calibration_profile.get("env_defaults") or {}
    lines = [
        "# HFSS S8P AEDT Script Packet",
        "",
        f"- Sample: `{payload['sample_id']}`",
        f"- EMX S8P: `{payload['source_files']['emx_s8p']}`",
        f"- GDS source: `{payload['source_files']['gds']}`",
        f"- Build payload: `hfss_s8p_build_payload.json`",
        f"- Build script: `build_hfss_s8p_from_payload.py`",
        f"- Build-time port manifest: `hfss_s8p_build_port_manifest.json` after the build script runs",
        f"- Solve/export script: `solve_export_hfss_s8p.py`",
        f"- ADS/Python formula trace: `hfss_ads_formula_trace.md` from `{payload['source_files'].get('ads_formula_trace', '')}`",
        f"- Frequency grid: {payload['frequency_grid']['start_ghz']} to {payload['frequency_grid']['stop_ghz']} GHz, step {payload['frequency_grid']['step_ghz']} GHz, {payload['frequency_grid']['points']} points",
        f"- HFSS calibration profile: `{calibration_profile.get('name', '')}`",
        "",
        "## HFSS Calibration Profile",
        "",
        f"- Intent: {calibration_profile.get('intent', '')}",
        f"- Acceptance gate: {calibration_profile.get('final_acceptance_gate', '')}",
        "",
        "| Env | Payload default |",
        "| --- | --- |",
    ]
    for key in sorted(calibration_env_defaults):
        lines.append(f"| `{key}` | `{calibration_env_defaults[key]}` |")
    lines.extend(
        [
            "",
            "The generated build script reads these payload defaults first, then allows explicit environment variables to override them.",
            "",
            "## Procedure",
            "",
            "1. Copy this folder to the Windows/HFSS environment.",
            "2. Run `python build_hfss_s8p_from_payload.py` to create the AEDT project.",
            "3. Check `hfss_s8p_build_port_manifest.json`, then open the project and visually verify the 8 ports, vertical power lines, same-width centered bridges, shield ground, and stack.",
            "4. Run `python solve_export_hfss_s8p.py` or set `HFSS_RUN_SOLVE=1` before running the build script.",
            "5. Use the exported `.s8p` with the existing EMX-vs-HFSS physical-feature comparison scripts.",
            "",
            "## Ports",
            "",
            "| Port | Role | Signal metal | Ground metal | Signal xy | Ground xy |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for port in payload["ports"]:
        lines.append(
            f"| {port['port_name']} | {port.get('role', '')} | {port.get('signal_metal', '')} | "
            f"{port.get('ground_metal', '')} | {port.get('signal_label', {}).get('origin_um', '')} | "
            f"{port.get('ground_label', {}).get('origin_um', '')} |"
        )
    lines.extend(["", "This packet is not final validation evidence until the generated HFSS `.s8p` exists and passes the comparison gates."])
    return "\n".join(lines) + "\n"


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS S8P AEDT Script Packet",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Handoff summary: `{summary['handoff_summary']}`",
        f"- Proc file: `{summary['proc_file']}`",
        f"- Selected/pass/fail: {summary['selected_count']} / {summary['pass_count']} / {summary['fail_count']}",
        "",
        "| Rank | Evaluation | Status | Payload | Build script | Solve script | Ports | Polygons |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for result in summary["sample_results"]:
        if result.get("overall_status") == "SKIPPED":
            continue
        lines.append(
            f"| {_cell(result.get('selection_rank', ''))} | {_cell(result.get('evaluation', ''))} | {_cell(result.get('overall_status', ''))} | "
            f"`{_cell(result.get('payload_json', ''))}` | `{_cell(result.get('build_script', ''))}` | `{_cell(result.get('solve_script', ''))}` | "
            f"{result.get('port_count', 0)} | {result.get('conductor_polygon_count', 0)} |"
        )
    lines.extend(["", "## Checks", "", "| Status | Sample | Evaluation | Check | Detail |", "| --- | --- | --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['status'])} | {_cell(check.get('sample', ''))} | {_cell(check.get('evaluation', ''))} | {_cell(check['name'])} | {_cell(check['detail'])} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _render_windows_commands(summary: dict[str, Any]) -> str:
    lines = [
        "# Generated helper commands for Windows/HFSS.",
        "# Review geometry before enabling solve/export.",
        "# The UNC path form is for Windows VMs that share the macOS home folder as \\\\Mac\\Home.",
        "",
    ]
    for result in summary["sample_results"]:
        if result.get("overall_status") != "PASS":
            continue
        script_dir = result["script_dir"]
        windows_script_dir = _windows_unc_path(script_dir)
        script_dir_escaped = script_dir.replace("'", "''")
        windows_script_dir_escaped = windows_script_dir.replace("'", "''")
        lines.extend(
            [
                f"# macOS path: {script_dir}",
                f"Push-Location '{windows_script_dir_escaped}'",
                "python .\\build_hfss_s8p_from_payload.py",
                "# After visual review:",
                "# python .\\solve_export_hfss_s8p.py",
                "Pop-Location",
                "# Fallback if the UNC share is unavailable:",
                f"# Push-Location '{script_dir_escaped}'",
                "",
            ]
        )
    return "\n".join(lines)


def _windows_unc_path(path: str) -> str:
    text = str(path)
    prefix = "/home/researcher/"
    if text.startswith(prefix):
        return "\\\\Mac\\Home\\" + text[len(prefix):].replace("/", "\\")
    return text.replace("/", "\\")


def _frequency_grid(args: argparse.Namespace) -> dict[str, Any]:
    start = float(args.frequency_start_ghz)
    stop = float(args.frequency_stop_ghz)
    step = float(args.frequency_step_ghz)
    points = int(round((stop - start) / step)) + 1 if step > 0 else 0
    return {
        "setup_frequency_ghz": float(args.setup_frequency_ghz),
        "start_ghz": start,
        "stop_ghz": stop,
        "step_ghz": step,
        "points": points,
        "expected_points": int(args.expected_frequency_points),
    }


def _production_grid_errors(grid: dict[str, Any]) -> list[str]:
    errors = []
    if abs(float(grid["start_ghz"]) - 5.0) > 1.0e-12:
        errors.append(f"start_ghz={grid['start_ghz']}")
    if abs(float(grid["stop_ghz"]) - 60.0) > 1.0e-12:
        errors.append(f"stop_ghz={grid['stop_ghz']}")
    if abs(float(grid["step_ghz"]) - 0.5) > 1.0e-12:
        errors.append(f"step_ghz={grid['step_ghz']}")
    if int(grid["points"]) != 111 or int(grid["expected_points"]) != 111:
        errors.append(f"points={grid['points']}, expected={grid['expected_points']}")
    return errors


def _diagnostic_grid_errors(grid: dict[str, Any]) -> list[str]:
    errors = []
    start = float(grid["start_ghz"])
    stop = float(grid["stop_ghz"])
    step = float(grid["step_ghz"])
    points = int(grid["points"])
    expected = int(grid["expected_points"])
    if start <= 0.0:
        errors.append(f"start_ghz={start}")
    if stop < start:
        errors.append(f"stop_ghz={stop} before start_ghz={start}")
    if step <= 0.0:
        errors.append(f"step_ghz={step}")
    if points < 2:
        errors.append(f"points={points}")
    if points != expected:
        errors.append(f"points={points}, expected={expected}")
    return errors


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} top-level JSON is {type(data).__name__}")
    return data


def _resolve_handoff_artifact(handoff: dict[str, Any], handoff_summary_path: Path, key: str) -> Path | None:
    artifacts = handoff.get("artifacts") if isinstance(handoff, dict) else {}
    raw = artifacts.get(key) if isinstance(artifacts, dict) else None
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    return path.resolve() if path.is_absolute() else (handoff_summary_path.parent / path).resolve()


def _resolve_first_existing(*items: Any) -> Path | None:
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if path.is_file():
            return path.resolve()
    return None


def _conductor_for_gds_layer(proc_info: ProcFileInfo, layer: int) -> ProcConductor | None:
    conductors = proc_info.conductors_for_gds_layer(int(layer))
    if not conductors:
        return None
    return conductors[0]


def _role_for_polygon(conductor_name: str, layer: int, datatype: int, proc_info: ProcFileInfo) -> str:
    pairs = proc_info.gds_pairs_for_layer(int(layer))
    pair_role = next((pair.role for pair in pairs if int(pair.layer) == int(layer) and int(pair.datatype) == int(datatype)), "single")
    return f"{conductor_name}_{pair_role}"


def _numeric_sheet_resistance(conductor: ProcConductor) -> float | None:
    expr = str(conductor.sheet_resistance_expr).strip()
    try:
        value = float(expr)
    except ValueError:
        leading_number = re.match(r"^\s*([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)\s*(?:\*|$)", expr)
        if leading_number:
            value = float(leading_number.group(1))
        elif "rsh_table" in expr.lower():
            value = TABLE_RSH_FALLBACK_OHM_PER_SQ.get(conductor.name.lower())
            if value is None:
                return None
        else:
            return None
    return value if math.isfinite(value) else None


def _conductivity(conductor: ProcConductor) -> float | None:
    sheet = _numeric_sheet_resistance(conductor)
    if sheet is None or sheet <= 0 or conductor.thickness_um <= 0:
        return None
    return 1.0 / (sheet * float(conductor.thickness_um) * 1.0e-6)


def _port_signal_z(stack: dict[str, Any], metal: str) -> float | None:
    conductor = stack.get("conductors", {}).get(metal)
    return None if not conductor else float(conductor["z_bottom_um"])


def _port_ground_z(stack: dict[str, Any], metal: str) -> float | None:
    conductor = stack.get("conductors", {}).get(metal)
    return None if not conductor else float(conductor["z_top_um"])


def _layout_port_map(layout_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ports = layout_manifest.get("ports") if isinstance(layout_manifest, dict) else []
    if not isinstance(ports, list):
        return {}
    return {
        str(port.get("name")): port
        for port in ports
        if isinstance(port, dict) and port.get("name")
    }


def _port_sheet_width_from_layout_port(layout_port: dict[str, Any], axis: str) -> float | None:
    if not isinstance(layout_port, dict):
        return None
    axis_index = 0 if str(axis).strip().lower() == "x" else 1
    candidates: list[float] = []
    for key in ("signal_internal_size_um", "ground_internal_size_um", "internal_size_um"):
        values = layout_port.get(key)
        if not isinstance(values, (list, tuple)) or len(values) <= axis_index:
            continue
        value = _to_float(values[axis_index])
        if value is not None and math.isfinite(value) and value > 0.0:
            candidates.append(float(value))
    if not candidates:
        return None
    return max(candidates)


def _port_sheet_width_source(
    power_line: dict[str, Any] | None,
    layout_port: dict[str, Any],
    axis: str,
    mode: str = "physical_line_width",
) -> str:
    width_mode = _normalize_port_sheet_width_mode(mode)
    if width_mode == "emx_pin_footprint" and _port_sheet_width_from_layout_port(layout_port, axis) is not None:
        return "layout_manifest_emx_pin_footprint"
    if _power_line_shared_width(power_line) is not None:
        return "power_line_8port_geometry.line_width_um_or_bridge_width_um"
    if _port_sheet_width_from_layout_port(layout_port, axis) is not None:
        return "layout_manifest_emx_pin_footprint"
    if isinstance(power_line, dict):
        for key in ("line_width_um", "bridge_width_um"):
            width = _to_float(power_line.get(key))
            if width is not None and math.isfinite(width) and width > 0.0:
                return f"power_line_8port_geometry.{key}"
    return "signal_ground_distance_fallback"


def _port_sheet_width(
    signal: dict[str, Any],
    ground: dict[str, Any],
    power_line: dict[str, Any] | None = None,
    layout_port: dict[str, Any] | None = None,
    axis: str = "y",
    mode: str = "physical_line_width",
) -> float:
    width_mode = _normalize_port_sheet_width_mode(mode)
    footprint_width = _port_sheet_width_from_layout_port(layout_port or {}, axis)
    if width_mode == "emx_pin_footprint" and footprint_width is not None:
        return float(footprint_width)
    shared_width = _power_line_shared_width(power_line)
    if shared_width is not None:
        return float(shared_width)
    if footprint_width is not None:
        return float(footprint_width)
    if isinstance(power_line, dict):
        for key in ("line_width_um", "bridge_width_um"):
            width = _to_float(power_line.get(key))
            if width is not None and math.isfinite(width) and width > 0.0:
                return float(width)
    sxy = signal.get("origin_um") if isinstance(signal, dict) else None
    gxy = ground.get("origin_um") if isinstance(ground, dict) else None
    if not sxy or not gxy:
        return 3.0
    dx = float(gxy[0]) - float(sxy[0])
    dy = float(gxy[1]) - float(sxy[1])
    distance = math.hypot(dx, dy)
    return max(1.0, min(10.0, 0.12 * distance))


def _normalize_port_sheet_width_mode(mode: str) -> str:
    text = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "physical_line_width",
        "physical": "physical_line_width",
        "line_width": "physical_line_width",
        "physical_line": "physical_line_width",
        "physical_line_width": "physical_line_width",
        "emx": "emx_pin_footprint",
        "emx_pin": "emx_pin_footprint",
        "emx_pin_footprint": "emx_pin_footprint",
        "layout": "emx_pin_footprint",
        "layout_footprint": "emx_pin_footprint",
        "pin_footprint": "emx_pin_footprint",
    }
    return aliases.get(text, "physical_line_width")


def _power_line_shared_width(power_line: dict[str, Any] | None) -> float | None:
    if not isinstance(power_line, dict) or not power_line.get("enabled"):
        return None
    for key in ("line_width_um", "bridge_width_um"):
        width = _to_float(power_line.get(key))
        if width is not None and math.isfinite(width) and width > 0.0:
            return float(width)
    return None


def _single_nonempty_value(values: list[str]) -> str:
    seen = {str(value).strip() for value in values if str(value).strip()}
    if len(seen) == 1:
        return next(iter(seen))
    if not seen:
        return ""
    return "mixed:" + ",".join(sorted(seen))


def _port_sheet_axis(role: str) -> str:
    text = str(role or "").strip().lower()
    if text in {"left_power_top", "left_power_bottom", "right_power_top", "right_power_bottom"}:
        return "x"
    return "y"


def _polygon_area(points: list[list[float]]) -> float:
    area = 0.0
    if len(points) < 3:
        return area
    for left, right in zip(points, points[1:] + points[:1]):
        area += float(left[0]) * float(right[1]) - float(right[0]) * float(left[1])
    return abs(area) * 0.5


def _combined_bbox(bboxes: list[list[float]]) -> list[float]:
    if not bboxes:
        return []
    return [
        min(float(item[0]) for item in bboxes),
        min(float(item[1]) for item in bboxes),
        max(float(item[2]) for item in bboxes),
        max(float(item[3]) for item in bboxes),
    ]


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _write_checks_csv(path: Path, checks: list[Check]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "evaluation", "status", "name", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.as_dict())


def _check(sample: str, evaluation: str, name: str, passed: bool, detail: Any) -> Check:
    return Check(str(sample), str(evaluation), "PASS" if passed else "FAIL", name, str(detail))


def _slug(value: str) -> str:
    chars = []
    for char in str(value):
        chars.append(char if char.isalnum() or char in {"-", "_"} else "_")
    return ("".join(chars).strip("_") or "sample")[:80]


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
