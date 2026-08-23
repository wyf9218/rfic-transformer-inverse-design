#!/usr/bin/env python3
"""Run the declared v8 synthetic hostile matrix and emit result-blind JSON."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

import test_report_interface_compatibility_v8 as tests


FORMAL_V4_EXECUTABLE_GATE_IDS = {
    "D01_RESUME_EVIDENCE_EMPTY", "D02_THREE_PREFLIGHT_EVIDENCE_EMPTY",
    "D03_FULL_PREFLIGHT_EVIDENCE_EMPTY", "D04_LAUNCHER_EVIDENCE_EMPTY",
    "D05_LAUNCHER_EVIDENCE_EMPTY", "D06_LAUNCHER_EVIDENCE_EMPTY",
    "D07_LAUNCHER_EVIDENCE_EMPTY", "X01_IDENTITY_GATE_CROSS_MISMATCH",
    "X02_STAGE07_MANIFEST_MISMATCH", "X03_STAGE08_MANIFEST_MISMATCH",
    "X04_THREE_MANIFEST_MISMATCH", "X05_FULL_MANIFEST_MISMATCH",
    "X06_FULL_INTERNAL_MISMATCH", "X07_FULL_MANIFEST_EXTRA_INPUT_SOURCE",
    "A01_AUTH_LAUNCHER_MISMATCH", "A02_PROCESS_ALGORITHM",
    "A03_PROCESS_DELTA", "A04_PROCESS_ARGV", "R01_FEATURE_SUMMARY_TAMPER",
    "R02_FEATURE_TYPE_TAMPER", "R03_EXACT_JOINT_TAMPER",
    "R04_Q_SUMMARY_TAMPER", "R05_PANEL_K_CLASSIFICATION",
    "R06_STAGE_STATUS_LABEL", "R07_END_TO_END_AGGREGATE_AND_PANEL_TAMPER",
    "P11_MIRROR_ROOT_ANCESTOR_SYMLINK", "P13_CALLER_REHASH_TOP_LEVEL_EXTRA",
    "P14_CALLER_REHASH_MAPPING_EXTRA",
}
V5_EXTENSION_GATE_IDS = {f"H{index:02d}_" for index in range(54, 82)}
FORMAL_V5_FAILED_GATE_IDS = {
    "F01_FUNNEL_BOOL_FLOAT_DIRECT", "F02_FUNNEL_BOOL_FLOAT_E2E",
    "F03_CONTRACT_MISSING_EXECUTION_UNITS", "F04_CONTRACT_EXTRA_AUTHORITY_DIRECT",
    "F05_CONTRACT_EXTRA_AUTHORITY_E2E", "F06_RUN_EXTRA_AUTHORITY_E2E",
    "F07_GLOBAL_FALSE_SIZE_DIRECT", "F08_GLOBAL_FALSE_SIZE_E2E",
}
PREVIOUS_V6_AUTHOR_GATE_IDS = {
    gate
    for gate in (
        "V7A01_CONTRACT_NESTED_EXTRA", "V7A02_CONTRACT_AUTHORITY_TYPE_CONFUSION",
        "V7A03_RUN_KEYSET_MISSING", "V7S01_LAUNCHER_COMMON_FALSE_SIZE",
        "V7S02_LAUNCHER_CONTROLLER_FALSE_SIZE",
        "V7S03_PORTABLE_RELEASE_FALSE_SIZE_CONSUMER", "V7C01_RUN_BOOL_COUNT",
        "V7C02_RUN_INTEGRAL_FLOAT_COUNT", "V7C03_IDENTITY_BOOL_COUNT",
        "V7C04_TABLE_INTEGRAL_FLOAT_COUNT", "V7C05_CONTRACT_INTEGRAL_FLOAT_COUNT",
        "V7C06_SOURCE_ROW_INTEGRAL_FLOAT_COUNT", "V7C07_PORTABLE_RUN_FLOAT_CONSUMER",
        "V7M01_COMBINED_DIRECT", "V7M02_COMBINED_E2E",
    )
}
FORMAL_V6_FAILED_GATE_IDS = {
    "H04_NESTED_AUTHORITY_INJECTION",
    "B02_PORTABLE_BOOL_FIELD_AS_INT",
    "B04_PORTABLE_CONTINUOUS_AS_BOOL",
    "H04_MAPPING_FALSE_AS_INT",
    "F03_RELEASE_FALSE_AS_INT",
    "G03_CONSUMER_HASH_PARSE_SAME_BYTES",
    "G01_OUTPUT_ANCESTOR_SYMLINK",
    "G03_OUTPUT_ROOT_SWAP_TOCTOU",
    "G02_HARDLINK_SOURCE",
    "D04_GDS_COUNT_CROSS_BIND",
    "H06_SURVIVOR_CONDITIONAL_NARRATIVE",
    "D06_NESTED_ARTIFACT_BYTES_CLOSURE",
}
V7_ADDITIONAL_GATE_IDS = {
    "V7B01_ADAPTER_SOURCE_NAMED_HELD_CONTINUITY",
    "V7F01_FORMAL_V6_NEGATIVE_EVIDENCE_BINDING",
    "V7X01_COMBINED_NESTED_TYPE_SURVIVOR_CONSUMER",
    "V7X02_COMBINED_PHANTOM_HARDLINK_OUTPUT_SCOPE_ADAPTER",
}
FORMAL_V7_FAILED_GATE_IDS = {
    "V8F01_OUTPUT_FILE_VERSION_CONTINUITY",
    "V8F02_NESTED_ROLE_IDENTITY_ONE_TO_ONE",
    "V8F03_SURVIVOR_STATEMENT_EXACT_BINDING",
}


def failure_rows(result: unittest.TestResult) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for kind, values in (("failure", result.failures), ("error", result.errors)):
        for test, detail in values:
            rows.append({"kind": kind, "test": test.id(), "detail": detail})
    return rows


def main() -> int:
    groups = tests.HOSTILE_GATE_GROUPS
    gate_ids = [gate for method in groups.values() for gate in method]
    if len(gate_ids) != 151 or len(set(gate_ids)) != 151:
        raise RuntimeError("hostile gate IDs must be 151 unique entries")
    legacy_ids = gate_ids[:53]
    expected_legacy = [f"H{index:02d}_" for index in range(1, 54)]
    if any(not gate.startswith(prefix) for gate, prefix in zip(legacy_ids, expected_legacy)):
        raise RuntimeError("legacy hostile gate IDs must preserve ordered H01..H53")
    if not FORMAL_V4_EXECUTABLE_GATE_IDS.issubset(gate_ids):
        missing = sorted(FORMAL_V4_EXECUTABLE_GATE_IDS - set(gate_ids))
        raise RuntimeError(f"formal-v4 executable gate IDs are missing: {missing}")
    observed_extensions = {
        gate[:4] for gate in gate_ids if gate.startswith("H") and gate[:4] in V5_EXTENSION_GATE_IDS
    }
    if observed_extensions != V5_EXTENSION_GATE_IDS:
        raise RuntimeError("v5 extension hostile gate IDs must cover H54..H81")
    if not FORMAL_V5_FAILED_GATE_IDS.issubset(gate_ids):
        raise RuntimeError("formal-v5 failed-gate closure is incomplete")
    if not PREVIOUS_V6_AUTHOR_GATE_IDS.issubset(gate_ids):
        raise RuntimeError("previous v6 author hostile/combination gate closure is incomplete")
    if not FORMAL_V6_FAILED_GATE_IDS.issubset(gate_ids):
        raise RuntimeError("formal-v6 failed-gate closure is incomplete")
    if not V7_ADDITIONAL_GATE_IDS.issubset(gate_ids):
        raise RuntimeError("v7 held-source/binding/combination gate closure is incomplete")
    if not FORMAL_V7_FAILED_GATE_IDS.issubset(gate_ids):
        raise RuntimeError("formal-v7 failed-gate closure is incomplete")
    available = {
        name
        for name in dir(tests.CompatibilityV8Tests)
        if name.startswith("test_") and callable(getattr(tests.CompatibilityV8Tests, name))
    }
    missing_methods = sorted(set(groups) - available)
    if missing_methods:
        raise RuntimeError(f"hostile matrix references absent test methods: {missing_methods}")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(tests.CompatibilityV8Tests)
    result = unittest.TestResult()
    suite.run(result)
    failures = failure_rows(result)
    readme_title = Path(__file__).with_name("README_CN.md").read_text(encoding="utf-8").splitlines()[0]
    documentation_pass = readme_title == "# 周一报告 complete-EMX 接口兼容层 v8 AWAITING_FRESH_INDEPENDENT_QA（结果盲）"
    if not documentation_pass:
        failures.append({
            "kind": "documentation_gate",
            "test": "P10_PREPARED_README_NOT_WIP",
            "detail": f"unexpected README title: {readme_title!r}",
        })
    passed = result.wasSuccessful() and documentation_pass
    output: dict[str, Any] = {
        "schema": "report_interface_compatibility_v8_author_hostile_matrix_v1",
        "status": "PASS_AUTHOR_SYNTHETIC_NOT_INDEPENDENT_GO" if passed else "FAIL_NO_GO",
        "scope": "local_synthetic_result_blind_only",
        "unittest_method_count": result.testsRun,
        "unittest_failure_count": len(result.failures),
        "unittest_error_count": len(result.errors),
        "unittest_skipped_count": len(result.skipped),
        "hostile_gate_count": len(gate_ids),
        "hostile_gate_pass_count": len(gate_ids) if passed else 0,
        "hostile_gate_fail_count": 0 if passed else len(gate_ids),
        "hostile_gate_groups": groups,
        "formal_v4_failed_gate_closure": {
            "formal_failed_total": 29,
            "executable_gate_count": len(FORMAL_V4_EXECUTABLE_GATE_IDS),
            "executable_gate_pass_count": len(FORMAL_V4_EXECUTABLE_GATE_IDS) if passed else 0,
            "documentation_gate": "P10_PREPARED_README_NOT_WIP",
            "documentation_gate_status": (
                "PASS_PREPARED_NOT_WIP" if documentation_pass else "FAIL_NO_GO"
            ),
        },
        "v5_extension_gate_count": len(V5_EXTENSION_GATE_IDS),
        "v5_extension_gate_pass_count": len(V5_EXTENSION_GATE_IDS) if passed else 0,
        "formal_v5_failed_gate_closure": {
            "failed_gate_count": len(FORMAL_V5_FAILED_GATE_IDS),
            "failed_gate_pass_count": len(FORMAL_V5_FAILED_GATE_IDS) if passed else 0,
            "failed_gate_ids": sorted(FORMAL_V5_FAILED_GATE_IDS),
        },
        "previous_v6_author_gate_count": len(PREVIOUS_V6_AUTHOR_GATE_IDS),
        "previous_v6_author_gate_pass_count": (
            len(PREVIOUS_V6_AUTHOR_GATE_IDS) if passed else 0
        ),
        "formal_v6_failed_gate_closure": {
            "failed_gate_count": len(FORMAL_V6_FAILED_GATE_IDS),
            "failed_gate_pass_count": len(FORMAL_V6_FAILED_GATE_IDS) if passed else 0,
            "failed_gate_ids": sorted(FORMAL_V6_FAILED_GATE_IDS),
        },
        "v7_additional_gate_count": len(V7_ADDITIONAL_GATE_IDS),
        "v7_additional_gate_pass_count": len(V7_ADDITIONAL_GATE_IDS) if passed else 0,
        "formal_v7_failed_gate_closure": {
            "failed_gate_count": len(FORMAL_V7_FAILED_GATE_IDS),
            "failed_gate_pass_count": len(FORMAL_V7_FAILED_GATE_IDS) if passed else 0,
            "failed_gate_ids": sorted(FORMAL_V7_FAILED_GATE_IDS),
        },
        "failures": failures,
        "boundaries": {
            "author_harness_is_independent_review": False,
            "mars_login_performed": False,
            "actual_emx_metrics_or_results_read": False,
            "production_chain_executed": False,
            "watcher_signal_sent": False,
            "stage07_or_stage08_executed": False,
            "actual_complete_interface_adapted": False,
            "report_publication_authorized": False,
        },
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
