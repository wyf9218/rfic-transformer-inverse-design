# Run this in the Windows/HFSS Python environment.
# It builds the strict-local-ground v50 HFSS model, solves it, and exports .s8p.

$ErrorActionPreference = "Stop"

$SampleDir = "\\Mac\Home\Documents\模拟变压器AI反向建模\reports\s8p_shared_line_width_mars_evidence_20260622\hfss_aedt_candidate_26cb45d70af3cfd0_pdkproc_v50_strict_local_pxxxg_ref_s8p_contract\samples\01_26cb45d70af3cfd0"
$Python = "C:\Program Files\ANSYS Inc\v251\AnsysEM\commonfiles\CPython\3_10\winx64\Release\python\python.exe"

Push-Location $SampleDir

$env:HFSS_UNITE_STRATEGY = "connected_by_bbox"
$env:HFSS_PORT_REFERENCE_MODE = "local_ground_bbox_smallest"
$env:HFSS_REQUIRE_LOCAL_GROUND_REFERENCE = "1"
$env:HFSS_RUN_SOLVE = "0"

Remove-Item ".\hfss_s8p_build_port_manifest.json" -Force -ErrorAction SilentlyContinue
Remove-Item ".\hfss_solve_export_results\26cb45d70af3cfd0_hfss_export.s8p" -Force -ErrorAction SilentlyContinue

& $Python .\build_hfss_s8p_from_payload.py
if ($LASTEXITCODE -ne 0) {
    throw "HFSS build script failed with exit code $LASTEXITCODE"
}

if (!(Test-Path ".\hfss_s8p_build_port_manifest.json")) {
    throw "HFSS build did not create hfss_s8p_build_port_manifest.json"
}

& $Python .\solve_export_hfss_s8p.py
if ($LASTEXITCODE -ne 0) {
    throw "HFSS solve/export script failed with exit code $LASTEXITCODE"
}

if (!(Test-Path ".\hfss_solve_export_results\26cb45d70af3cfd0_hfss_export.s8p")) {
    throw "HFSS solve/export did not create the expected .s8p"
}

Pop-Location
