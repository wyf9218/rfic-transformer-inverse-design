#!/usr/bin/env python3
"""Prepared-only v10 result-free transport/runtime transaction builder.

This module has no network, business-result, controller, outer-main, resume, or
signal capability.  Production entry is Linux-only, has no pathname-executed
main, and accepts only a trusted preflight that was itself admitted by a
separate exact root launch authorization.  The preflight must pread/hash/
compile this source from held FD198 while retaining held interpreter FD197.
Tests may call the core with temporary fixtures and an injected no-clobber
rename implementation.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import email.parser
import errno
import fcntl
import hashlib
import importlib.metadata
import io
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


AUTH_SCHEMA = "historical_200k_fixed10k_result_free_transport_authorization_v10"
AUTH_STATUS = (
    "TRUSTED_HELD_PREFLIGHT_DERIVED_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
)
EXECUTE_TEXT = (
    "I_HAVE_TRUSTED_HELD_V10_GO_AND_EXACT_RESULT_FREE_TRANSPORT_AUTHORIZATION"
)
AUTH_SHA_ARGV_MARKER = "<AUTHORIZATION_SHA256_SUPPLIED_OUT_OF_BAND>"
HELD_INTERPRETER_FD = 197
HELD_BUILDER_SOURCE_FD = 198
HELD_BUILDER_LAUNCH_SCHEMA = (
    "historical_200k_fixed10k_trusted_held_builder_launch_v1"
)
HELD_BUILDER_LAUNCH_STATUS = "AUTHORIZED_HELD_BYTES_IN_PREFLIGHT_PROCESS"
HELD_BUILDER_LAUNCH_METHOD = (
    "HELD_INTERPRETER_FD197_AND_PREAD_SHA_COMPILE_BUILDER_FD198_"
    "IN_TRUSTED_PREFLIGHT_PROCESS_V1"
)
HELD_SOURCE_READ_LIMIT_BYTES = 256 * 1024 * 1024
OUTER_LAUNCH_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_trusted_root_preflight_builder_launch_receipt_v2"
)
OUTER_LAUNCH_RECEIPT_STATUS = "PASS_TRUSTED_HELD_PREFLIGHT_BUILDER_LAUNCH_ONLY"
ROOT_LAUNCH_AUTHORIZATION_SCHEMA = (
    "historical_200k_fixed10k_root_held_preflight_launch_authorization_v2"
)
ROOT_LAUNCH_AUTHORIZATION_STATUS = (
    "AUTHORIZED_TRUSTED_HELD_PREFLIGHT_PACKAGE_AND_QA_ONLY"
)
PREFLIGHT_V2_PREPARED_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v2_"
    "bundle_manifest_v2"
)
PREFLIGHT_V2_PREPARED_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v2_"
    "prepared_receipt_v2"
)
PREFLIGHT_V2_PREPARED_STATUS = (
    "PASS_PREPARED_ONLY_NOT_AUTHORIZED_NOT_EXECUTED_AWAITING_INDEPENDENT_QA"
)
PREFLIGHT_V2_QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v2_"
    "independent_qa_receipt_v1"
)
PREFLIGHT_V2_QA_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v2_"
    "independent_qa_bundle_manifest_v1"
)
PREFLIGHT_V2_QA_STATUS = (
    "PASS_INDEPENDENT_QA_CODE_GO_REQUIRES_SEPARATE_EXACT_AUTHORIZATION"
)
PREFLIGHT_V2_QA_ACTION_VERDICT = (
    "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LINUX_MARS_XFS_NATIVE_"
    "COMPATIBILITY_PREFLIGHT_ONLY"
)
PREFLIGHT_V2_ZERO_FINDINGS = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
PREFLIGHT_V2_ALL_FALSE_AUTHORITY = {
    "mars_access_authorized": False,
    "mars_write_authorized": False,
    "preflight_execution_authorized": False,
    "transport_build_or_smoke_authorized": False,
    "production_root_or_journal_write_authorized": False,
    "result_access_authorized": False,
    "external_process_inspection_or_control_authorized": False,
    "signals_authorized": False,
    "controller_or_resume_authorized": False,
    "deployment_authorized": False,
}
PREFLIGHT_V2_PREPARED_PAYLOAD_NAMES = frozenset({
    "AUTHOR_COMPILE_V2_OUTPUT.json",
    "AUTHOR_PREFLIGHT_V2_SYNTHETIC_OUTPUT.json",
    "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V2.json",
    "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V2_CN.md",
    "UPSTREAM_EVIDENCE_BINDINGS_V2.json",
    "run_result_free_mars_native_preflight_v2.py",
    "test_result_free_mars_native_preflight_v2_synthetic.py",
})
PREFLIGHT_V2_PREPARED_CLOSURE_NAMES = frozenset({
    "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json", "SHA256SUMS",
})
PREFLIGHT_V2_PREPARED_TOP_NAMES = (
    PREFLIGHT_V2_PREPARED_PAYLOAD_NAMES | PREFLIGHT_V2_PREPARED_CLOSURE_NAMES
)
PREFLIGHT_V2_PREPARED_INDEX_NAMES = (
    PREFLIGHT_V2_PREPARED_TOP_NAMES - {"SHA256SUMS"}
)
PREFLIGHT_V2_QA_PAYLOAD_NAMES = frozenset({
    "COMMAND_LOG.txt", "INDEPENDENT_QA_OUTPUT.json",
    "INDEPENDENT_QA_REPORT_CN.md", "PACKAGE_CLOSURE_QA.json",
    "QA_HARNESS_OR_METHOD.md",
})
PREFLIGHT_V2_QA_CLOSURE_NAMES = frozenset({
    "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
})
PREFLIGHT_V2_QA_TOP_NAMES = (
    PREFLIGHT_V2_QA_PAYLOAD_NAMES | PREFLIGHT_V2_QA_CLOSURE_NAMES
)
PREFLIGHT_V2_QA_INDEX_NAMES = PREFLIGHT_V2_QA_TOP_NAMES - {"SHA256SUMS"}
PREFLIGHT_V2_MANIFEST_FILE_KEYS = frozenset({
    "relative_path", "role", "sha256", "size_bytes",
})
PREFLIGHT_V2_PREPARED_MANIFEST_KEYS = frozenset({
    "schema", "status", "created_utc", "payload_file_count", "files",
    "closure_files_not_in_payload_manifest", "authority",
})
PREFLIGHT_V2_PREPARED_RECEIPT_KEYS = frozenset({
    "schema", "status", "created_utc", "package_directory",
    "package_closure", "locked_tools", "author_validation", "scope",
    "authority", "next_legal_action",
})
PREFLIGHT_V2_QA_RECEIPT_KEYS = frozenset({
    "schema", "status", "created_utc", "qa_directory",
    "action_scoped_verdict", "audited_package", "qa_artifacts",
    "independent_validation", "finding_counts", "authority", "scope",
    "next_legal_action",
})
PREFLIGHT_V2_QA_MANIFEST_KEYS = frozenset({
    "schema", "status", "created_utc", "payload_file_count", "files",
    "closure_files_not_in_payload_manifest", "action_scoped_verdict",
    "finding_counts", "authority",
})
NATIVE_COMPATIBILITY_API_SCHEMA = (
    "historical_200k_fixed10k_v10_scoped_native_compatibility_api_v1"
)
NATIVE_COMPATIBILITY_API_STATUS = (
    "PASS_NATIVE_COMPATIBILITY_NOT_PRODUCTION_BUILD"
)
NATIVE_COMPATIBILITY_API_SCOPE = "NOT_PRODUCTION_BUILD"
NATIVE_COMPATIBILITY_DECISION_ID = (
    "historical-200k-fixed10k-post-stage06-runtime-v10"
)
NATIVE_COMPATIBILITY_TERMINAL_NAME = "NATIVE_COMPATIBILITY_TERMINAL.json"
NATIVE_COMPATIBILITY_TERMINAL_SCHEMA = (
    "historical_200k_fixed10k_v10_scoped_native_compatibility_terminal_v1"
)
V10_QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v10_qa_receipt_v1"
)
V10_QA_RECEIPT_STATUS = "PASS_INDEPENDENT_QA_RESULT_FREE_PACKAGE_ONLY"
V10_QA_ACTION_VERDICT = (
    "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
)
V9_NEGATIVE_QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v9_qa_receipt_v1"
)
V9_NEGATIVE_QA_RECEIPT_STATUS = (
    "NO_GO_INDEPENDENT_QA_RESULT_FREE_PACKAGE_ONLY"
)
V9_NEGATIVE_QA_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v9_qa_bundle_manifest_v1"
)
V9_NEGATIVE_QA_MANIFEST_STATUS = (
    "NO_GO_RELEASE_BLOCKED_BY_TERMINAL_NAMED_PARENT_CONTINUITY_GAP"
)
V9_NEGATIVE_QA_ACTION_VERDICT = (
    "NO_GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_MARS_NATIVE_COMPATIBILITY_PREFLIGHT"
)
V8_NEGATIVE_QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v8_qa_receipt_v1"
)
V8_NEGATIVE_QA_RECEIPT_STATUS = (
    "NO_GO_INDEPENDENT_QA_RESULT_FREE_PACKAGE_ONLY"
)
V8_NEGATIVE_QA_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v8_qa_bundle_manifest_v1"
)
V8_NEGATIVE_QA_MANIFEST_STATUS = (
    "NO_GO_RELEASE_BLOCKED_BY_REVERSE_CANONICAL_ANCESTRY_GUARD"
)
V8_NEGATIVE_QA_ACTION_VERDICT = (
    "NO_GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_MARS_NATIVE_COMPATIBILITY_PREFLIGHT"
)
V7_NEGATIVE_QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v7_qa_receipt_v1"
)
V7_NEGATIVE_QA_RECEIPT_STATUS = (
    "NO_GO_INDEPENDENT_QA_RESULT_FREE_PACKAGE_ONLY"
)
V7_NEGATIVE_QA_ACTION_VERDICT = (
    "NO_GO_FOR_RESULT_FREE_MARS_NATIVE_COMPATIBILITY_PREFLIGHT"
)
V10_SMOKE_BOOTSTRAP_SHA256 = (
    "a38e950b705e12cb07c30148a7e2fedf5b60e6c17c8d49a21984973cda1a34b4"
)
V10_SMOKE_BOOTSTRAP_SIZE_BYTES = 12667
HELD_BUILDER_LAUNCH_CONTRACT = {
    "schema": HELD_BUILDER_LAUNCH_SCHEMA,
    "status": HELD_BUILDER_LAUNCH_STATUS,
    "method": HELD_BUILDER_LAUNCH_METHOD,
    "interpreter_fd": HELD_INTERPRETER_FD,
    "builder_source_fd": HELD_BUILDER_SOURCE_FD,
    "interpreter_proc_path": f"/proc/self/fd/{HELD_INTERPRETER_FD}",
    "builder_source_proc_path": f"/proc/self/fd/{HELD_BUILDER_SOURCE_FD}",
    "interpreter_fd_inheritable": True,
    "builder_source_fd_inheritable": False,
}
EXPECTED_FINAL_ROOT = Path(
    "${MARS_RESEARCH_ROOT}/historical_200k_fixed10k_emx_v2_20260822/"
    "runtime_addendum_post_stage06_release_chain_v5_20260822T125710Z"
)
EXPECTED_PYTHON = Path(
    "${MARS_RESEARCH_ROOT}/"
    "rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python"
)
RUNTIME_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_post_stage06_runtime_dependency_identity_manifest_v10"
)
RUNTIME_MANIFEST_STATUS = "FROZEN_RESULT_FREE_RUNTIME_IDENTITY_V10"
BUILD_PASS_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_result_free_transport_build_pass_receipt_v10"
)
BUILD_PASS_RECEIPT_STATUS = (
    "PASS_V10_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_BUILT_NOT_SMOKED"
)
BEGIN_SCHEMA = "historical_200k_fixed10k_result_free_transport_begin_v10"
BEGIN_STATUS = "DURABLE_BEGIN_BEFORE_STAGING"
INTENT_SCHEMA = "historical_200k_fixed10k_result_free_transport_commit_intent_v10"
INTENT_STATUS = "DURABLE_COMMIT_INTENT_BEFORE_FIXED_ROOT_RENAME"
INTENT_RECOVERY_RULE = (
    "ONLY_EXACT_AUTH_DEV_INO_MANIFEST_FULL_TREE_AND_TRUST_BINDINGS_MAY_RESUME_OR_FINALIZE"
)
FILES_ONLY_DIGEST_ALGORITHM = (
    "sha256_sorted_relative_path_nul_sha256_nul_size_bytes_nul_mode_lf_v1"
)
STRUCTURAL_DIGEST_ALGORITHM = (
    "sha256_sorted_relative_path_nul_kind_nul_sha256_nul_size_bytes_nul_mode_lf_v1"
)
PRODUCTION_TERMINAL_PUBLICATION_METHOD = (
    "LINUX_XFS_O_TMPFILE_COMPLETE_FCHMOD0444_FSYNC_"
    "LINKAT_PROC_SELF_FD_AT_SYMLINK_FOLLOW_NOREPLACE_DIRFSYNC_V1"
)
SYNTHETIC_TERMINAL_PUBLICATION_METHOD = (
    "SYNTHETIC_COMPLETE_FSYNC_THEN_INJECTED_ATOMIC_NOREPLACE_RENAME_DIRFSYNC_V1"
)
TERMINAL_CANONICAL_VISIBILITY_RULE = (
    "CANONICAL_TERMINAL_ABSENT_UNTIL_COMPLETE_0444_FSYNCED_INODE_PUBLISH"
)
AT_FDCWD = -100
AT_SYMLINK_FOLLOW = 0x400
XFS_SUPER_MAGIC = 0x58465342
PROC_SUPER_MAGIC = 0x9FA0
PROC_SELF_FD_PATH = "/proc/self/fd"
TERMINAL_READ_LIMIT_BYTES = 16 * 1024 * 1024

V8_BINDING = {
    "directory_name": "post_stage06_release_chain_v8_prepared_20260822T142204Z",
    "receipt_sha256": "8eb40f37057b1257c34e5f5a69c5fe394cb525c47158e2367262ec95eea24246",
    "bundle_manifest_sha256": "47c94860d2eae020b6f09e6e8ec7f79497d9dc48aeb4ae4579407b0bd0333e1f",
    "sha256_index_sha256": "9fbef6b48567d8055af152f5bd60821e31ef3d44e2013754ca929efb81504a5a",
    "top_level_count": 41,
    "indexed_count": 40,
}
V8_AUDIT_BINDING = {
    "directory_name": "independent_post_stage06_release_chain_v8_audit_20260822T143334Z",
    "action_scoped_verdict": "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_SMOKE_ONLY",
    "report_sha256": "82b10ab30a237fc896c7ce354d7a838a95cfdf1a343979e69041cd475bf27166",
    "receipt_sha256": "efcb15c3342effac7aa4187933307de1f77e3367a9b2e2c0eefcb3956ee88b74",
    "output_sha256": "b6c5dcbe99ff14444a96c6b9d08d6bcb3771419fb161e63f819894e36c923def",
    "log_sha256": "eaa070fbe2f69b054820d50fc2f7f1423e5c6bad158ec5885414111b6e45068b",
    "harness_sha256": "642ae41c01f179485d26a85d6443bd26e49d4fc63fd8a48d6185592b0091121f",
    "sha256_index_sha256": "a630f9a77eedbbf0d64bb12f557964e24a164647db4fbfa4469c3e41e41bcc64",
}
V1_BINDING = {
    "directory_name": "transport_runtime_layout_builder_v1_prepared_20260822T143556Z",
    "builder_sha256": "0010a3c9fd1f01f6e99e8dec2d5af08ae76f083f8c70a8367ff7cf0c45019a55",
    "smoke_sha256": "eaa9975df3af2bc9d8b1e6f642dede4046ffc8500c8cba33faa799925aaf7d85",
    "test_sha256": "4e7145daa9c128c615e5571f521b6d432739f2926dc707d96add9fb19fdfc948",
    "bundle_manifest_sha256": "10a5b96badb31e3c018ee31ccf7296ff442312bdbf7dd538f361c6f3ba66f70e",
    "prepared_receipt_sha256": "2b9caf40282be0978fff51e361aff5ba02cfc33bfbd812968f7a25c73e627054",
    "sha256_index_sha256": "1fda6d4abf55a3cd5069b105ec9066edd93b5460ccde706b2c386737a87cff0b",
}
V1_AUDIT_BINDING = {
    "directory_name": "independent_transport_runtime_layout_builder_v1_audit_20260822T145038Z",
    "action_scoped_verdict": "NO-GO_FOR_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_BUILD_OR_SMOKE",
    "finding_counts": {"P0": 0, "P1": 6, "P2": 3},
    "report_sha256": "bdcc83fe120576ed03ca4a6d0ba637f87d22e09a1bc3548b106de97e63034808",
    "receipt_sha256": "f1fce63143f3732f4056afd02737919e50a7ea0950d8d5853930d710b07157c2",
    "output_sha256": "11625edc3f28103f34674c869298c263f4ef4885dcc7f16f2f54b92ba6fb572b",
    "log_sha256": "f565978a5421f85a694a85323b0660aab47588e89de76549902666219efe70b0",
    "harness_sha256": "d122a20a8a877f80d9ae7c18b8c5a63579f6af96c22fefe0a19460b655faed1c",
    "sha256_index_sha256": "973a641fdc9a903d124d4d4147b2616072b3c199518019e7f142869ee9d6ebb0",
}
V7_NEGATIVE_QA_BINDING = {
    "directory_name": (
        "independent_transport_runtime_layout_builder_v7_qa_20260822T185912Z"
    ),
    "action_scoped_verdict": V7_NEGATIVE_QA_ACTION_VERDICT,
    "finding_counts": {"P0": 1, "P1": 3, "P2": 1, "P3": 0},
    "bundle_manifest_sha256": (
        "c530a9815f7c3e8cc3b07c8cdb0ae8a4712ed9dccf8e75e278aa91e582c8591e"
    ),
    "log_sha256": (
        "c892d4d14032b435f7b0a95b3e0eab27d7a243c8d956697eeef863896d74432b"
    ),
    "output_sha256": (
        "7ba1cf084804c283aba69eae5f49500d6d625548a00828679e58640cfcee4702"
    ),
    "receipt_sha256": (
        "5ac3fe473772f5ed21f3506669457fbec151909218814433359d3ee3c261595a"
    ),
    "report_sha256": (
        "05012653344a2a61625084903b174f97e37435dc956ddcff8c0cb70f050101d1"
    ),
    "closure_sha256": (
        "f761be56ed5ab361d639c95c719db0d3af0d9a3d0d9ea9b18de816f6abd1e013"
    ),
    "harness_sha256": (
        "edf8db15b90cef36452f80d12b5afedf3db6a392b9126e5c555c4edf501e3d72"
    ),
    "sha256_index_sha256": (
        "d0331e3babffa91162caa8e8f885b361fdb335b9cdfe8f238cbe5f1d9abf85a4"
    ),
}
V7_NEGATIVE_TARGET_PACKAGE = {
    "directory_name": "transport_runtime_layout_builder_v7_prepared_20260822T185157Z",
    "builder_sha256": (
        "e36d8882adbcb65a17ede2c931e2c8e0e3056acbf27562cc0e8d36c4eb024a9a"
    ),
    "test_sha256": (
        "35dca6af8cda69a088127137c3c86373904fbd70c9595d89c3cf012143fd538c"
    ),
    "smoke_sha256": (
        "080034a09134c195bd627466eb66e147312a0e92a0f3d9f569819d7852f1eb26"
    ),
    "smoke_test_sha256": (
        "1207eb805791ab6724698b7d864685689feffe1de425c9e87aaaedaded976f6e"
    ),
    "bundle_manifest_sha256": (
        "96e052b779154080f99a50aa7e83ecec46b513e1a4a8f7253a586f71fe4a4bcf"
    ),
    "sha256_index_sha256": (
        "026d6a351babcdadb416a43db37edb47890c27c1b61e05c8825c15da10347e9f"
    ),
    "prepared_receipt_sha256": (
        "43a7b1344d6b9c8bb5f57ddb0691edd1359e93c278adc96ab739a3c2843f5379"
    ),
}
V9_NEGATIVE_QA_AUTHORITY = {
    "controller_or_resume_authorized": False,
    "deployment_authorized": False,
    "mars_access_authorized": False,
    "mars_write_authorized": False,
    "preflight_execution_authorized": False,
    "production_root_or_journal_write_authorized": False,
    "result_access_authorized": False,
    "signals_authorized": False,
    "transport_build_or_smoke_authorized": False,
}
V9_NEGATIVE_QA_SCOPE = {
    "candidate_modified": False,
    "external_processes_inspected_or_controlled": False,
    "linux_actual_executed": False,
    "local_candidate_test_children_only": True,
    "mars_accessed": False,
    "memory_modified": False,
    "network_accessed": False,
    "production_entry_executed": False,
    "results_or_emx_accessed": False,
    "signals_sent": False,
    "temporary_local_result_blind_fixtures_only": True,
}
V9_NEGATIVE_QA_FILE_BINDINGS = {
    "bundle_manifest": {
        "filename": "BUNDLE_MANIFEST.json",
        "sha256": "922e556401ec9a2520627e7a0795ff72ea9c1cee11446aad6bf406a00a15f452",
        "size_bytes": 2629,
        "role": "closure",
    },
    "command_log": {
        "filename": "COMMAND_LOG.txt",
        "sha256": "48486c39c7f4613b149012e2021b206edeb506b913c877231753bf18d5b9d176",
        "size_bytes": 4662,
        "role": "independent_command_and_result_log",
    },
    "attempt1_empty_stdout": {
        "filename": "HARNESS_ATTEMPT1_FAILED_EMPTY_STDOUT.txt",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "size_bytes": 0,
        "role": "preserved_failed_attempt1_empty_stdout",
    },
    "attempt1_failure": {
        "filename": "HARNESS_ATTEMPT1_FAILURE.log",
        "sha256": "51bbd02eeac5a9d849e09277bd4e31bce6a0ff2b34f203d59e00a3636cc485de",
        "size_bytes": 1017,
        "role": "preserved_failed_attempt1_qa_fixture_diagnostic",
    },
    "harness": {
        "filename": "INDEPENDENT_QA_HARNESS.py",
        "sha256": "47951b6ec7b2c6a5389391340bc131ebbbf87be6c90c34a55d5fd53cbcc734ba",
        "size_bytes": 42625,
        "role": "independent_result_blind_hostile_harness",
    },
    "output": {
        "filename": "INDEPENDENT_QA_OUTPUT.json",
        "sha256": "ab02284c8dcd72c5cd68cbee96ee7037a98f2044685c21b1501a8b2efe5b7dad",
        "size_bytes": 12635,
        "role": "independent_machine_readable_no_go_output",
    },
    "receipt": {
        "filename": "INDEPENDENT_QA_RECEIPT.json",
        "sha256": "2fbeee49ac220b0faec1994f5b4d2a846e7e745ca0941d23aa351fee21e9cc97",
        "size_bytes": 4446,
        "role": "closure",
    },
    "report": {
        "filename": "INDEPENDENT_QA_REPORT_CN.md",
        "sha256": "cfff7d0388c25092a9a055fab0c16951b0f41e3ef3447c8ded66d5c43f94e08b",
        "size_bytes": 8286,
        "role": "independent_human_readable_no_go_report",
    },
    "closure": {
        "filename": "PACKAGE_CLOSURE_QA.json",
        "sha256": "871d3fd73403f5513c1393d1e7206dda510ff0587428be1be680820f7bc65185",
        "size_bytes": 2712,
        "role": "independent_candidate_and_audit_closure_evidence",
    },
    "sha256_index": {
        "filename": "SHA256SUMS",
        "sha256": "5237b0d613170d357e1a2014318db4be078e8577bf6f33aa81f208a053386ebd",
        "size_bytes": 740,
        "role": "closure",
    },
}
V9_NEGATIVE_QA_BINDING = {
    "directory_name": (
        "independent_transport_runtime_layout_builder_v9_qa_20260822T203210Z"
    ),
    "action_scoped_verdict": V9_NEGATIVE_QA_ACTION_VERDICT,
    "finding_counts": {"P0": 0, "P1": 1, "P2": 0, "P3": 0},
    **{
        f"{stem}_sha256": item["sha256"]
        for stem, item in V9_NEGATIVE_QA_FILE_BINDINGS.items()
    },
}
V9_NEGATIVE_TARGET_PACKAGE = {
    "builder_sha256": "9880c59e427da628546ca8a70eb662370afd3f673db5154966de50cf94da9fb7",
    "bundle_manifest_sha256": "c7208af1e9af77a6754f84f662022552e9e9faa4d723237b47ef4ac8065de3a9",
    "directory": "transport_runtime_layout_builder_v9_prepared_20260822T202248Z",
    "prepared_receipt_sha256": "7088bbfbcd7952fe20ae0ac494d07a167a18d5eb7710f8c72bb852b95607b859",
    "sha256_index_sha256": "c70e0df2c0a3f2ec472850ed3394c40acdeacaf187f4b0444a89afb0cc6c0728",
    "smoke_sha256": "62a3fd058d8fd98b37ee97a60a7ea624e34207b746068a68e036bad3d4f880ed",
    "smoke_test_sha256": "1e13d41bf9a7949e2da9eff989ca3eab6d1100ec72266e1d7095bef9ff021bd8",
    "test_sha256": "8e445a19160845333f5698b349aee7a1cfeb35e771a267b6fc0f48249269eda5",
}
V8_NEGATIVE_QA_AUTHORITY = {
    "controller_or_resume_authorized": False,
    "deployment_authorized": False,
    "mars_access_authorized": False,
    "mars_write_authorized": False,
    "preflight_execution_authorized": False,
    "production_root_or_journal_write_authorized": False,
    "result_access_authorized": False,
    "signals_authorized": False,
    "transport_build_or_smoke_authorized": False,
}
V8_NEGATIVE_QA_SCOPE = {
    "candidate_modified": False,
    "external_processes_inspected_or_controlled": False,
    "linux_actual_executed": False,
    "mars_accessed": False,
    "network_accessed": False,
    "production_entry_executed": False,
    "results_or_emx_accessed": False,
    "signals_sent": False,
    "temporary_local_fixtures_only": True,
}
V8_NEGATIVE_QA_FILE_BINDINGS = {
    "bundle_manifest": {
        "filename": "BUNDLE_MANIFEST.json",
        "sha256": "dcc965a626aa24681efd1e3d8715a45b4f0cdc471393040a2ea0ee40c147689d",
        "size_bytes": 3570,
        "role": "closure",
    },
    "command_log": {
        "filename": "COMMAND_LOG.txt",
        "sha256": "2ed20737c8d33becc77169feaf07674b08bb2e341966ec915025db9858511c92",
        "size_bytes": 4355,
        "role": "independent_command_and_result_log",
    },
    "attempt1_empty_stdout": {
        "filename": "HARNESS_ATTEMPT1_FAILED_EMPTY_STDOUT.txt",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "size_bytes": 0,
        "role": "preserved_failed_attempt1_empty_stdout",
    },
    "attempt1_failure": {
        "filename": "HARNESS_ATTEMPT1_FAILURE.log",
        "sha256": "c1d59844d25133d7f82d7b2b234d8dc650f9ffd3df2d44333aefaf9ec669aa83",
        "size_bytes": 561,
        "role": "preserved_failed_attempt1_diagnostic",
    },
    "attempt2_empty_stdout": {
        "filename": "HARNESS_ATTEMPT2_FAILED_EMPTY_STDOUT.txt",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "size_bytes": 0,
        "role": "preserved_failed_attempt2_empty_stdout",
    },
    "attempt2_failure": {
        "filename": "HARNESS_ATTEMPT2_FAILURE.log",
        "sha256": "4dc5b036112a0ca15db56c0b51ebb2400eab3f807216cfa6ada922bb22e43d1c",
        "size_bytes": 673,
        "role": "preserved_failed_attempt2_diagnostic",
    },
    "attempt3_failure": {
        "filename": "HARNESS_ATTEMPT3_FAILURE.log",
        "sha256": "9755a50bce2f80e44f5f89005758e1d787870d10193c666cc54bd1e167bdf5b8",
        "size_bytes": 751,
        "role": "preserved_failed_attempt3_diagnostic",
    },
    "attempt3_output": {
        "filename": "HARNESS_ATTEMPT3_OUTPUT_WITH_FIXTURE_FAILURE.json",
        "sha256": "180661bfb27fb2963acbca291fc02965eca05cbe2cce2c2be557d6582de6f8d2",
        "size_bytes": 14170,
        "role": "preserved_failed_attempt3_full_output",
    },
    "harness": {
        "filename": "INDEPENDENT_QA_HARNESS.py",
        "sha256": "13c86db155a29e6b4eeaea2828dd13da6f583a4eb87ef63da809017f2172376e",
        "size_bytes": 56174,
        "role": "independent_result_blind_hostile_harness",
    },
    "output": {
        "filename": "INDEPENDENT_QA_OUTPUT.json",
        "sha256": "576ef34be32a1790537d0a5b1929cd7d5a8715af3edd0645aba13a64bd1d8d36",
        "size_bytes": 14045,
        "role": "independent_machine_readable_no_go_output",
    },
    "receipt": {
        "filename": "INDEPENDENT_QA_RECEIPT.json",
        "sha256": "e0379c3295fe98afeee2a003a071298dc297cf6f25113488d986250a2fa29444",
        "size_bytes": 4363,
        "role": "closure",
    },
    "report": {
        "filename": "INDEPENDENT_QA_REPORT_CN.md",
        "sha256": "c8d1f3ae01b8461ddc24b2f7ae7e50f864ddad06d14aa181b831cf726a9fd5cc",
        "size_bytes": 8389,
        "role": "independent_human_readable_no_go_report",
    },
    "closure": {
        "filename": "PACKAGE_CLOSURE_QA.json",
        "sha256": "cb4bc3867edf0f51fa46d666e8cc76f6a510c6638fc268d80a51f04b05636128",
        "size_bytes": 3786,
        "role": "independent_candidate_and_audit_closure_evidence",
    },
    "sha256_index": {
        "filename": "SHA256SUMS",
        "sha256": "071abb4150562d6dfb21d423a6bfe98d2b90e18fc078f537699d6f270e7c484b",
        "size_bytes": 1153,
        "role": "closure",
    },
}
V8_NEGATIVE_QA_BINDING = {
    "directory_name": (
        "independent_transport_runtime_layout_builder_v8_qa_20260822T200141Z"
    ),
    "action_scoped_verdict": V8_NEGATIVE_QA_ACTION_VERDICT,
    "finding_counts": {"P0": 1, "P1": 0, "P2": 0, "P3": 0},
    **{
        f"{stem}_sha256": item["sha256"]
        for stem, item in V8_NEGATIVE_QA_FILE_BINDINGS.items()
    },
}
V8_NEGATIVE_TARGET_PACKAGE = {
    "directory": "transport_runtime_layout_builder_v8_prepared_20260822T195209Z",
    "builder_sha256": "9faad10d74e1b37b832f3c31dc8994785da8a1e5d375cb56f91536fa8d0aef65",
    "test_sha256": "857cbc8ae5315b05fa8d8fd8d33d07107a09fd344611728192ae33aa3343d67f",
    "smoke_sha256": "55ca70d28f2dff9de8b0305535362eebf4bf91d5143af6c753e86eb3ddf0bdb5",
    "smoke_test_sha256": "ac678e9e36528eab2ad98e229f02fccb9e70e787b7bcb345a4d625cea67de168",
    "bundle_manifest_sha256": "484f294a9229fa5031983d1a8c41a0dcb453df013d43e529d79e8165e724922d",
    "sha256_index_sha256": "a18eff950d8c27576344189c9af8489c87f9f0467bfdbe33859dfe76c6980e76",
    "prepared_receipt_sha256": "883024e80527f181cea1eec008eec2d16de529c70ae44e0777c32c52b0169965",
}

COPY_DISTRIBUTIONS = (
    "numpy", "matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver",
    "packaging", "pillow", "pyparsing", "python-dateutil", "six",
)
MANIFEST_DISTRIBUTIONS = ("matplotlib", "numpy")
IMPORT_RELATIVE_PATHS = {
    "matplotlib": "matplotlib/__init__.py",
    "numpy": "numpy/__init__.py",
}
# Exact read-only authorized-environment inventory.  No glob/prefix matching.
EXTERNAL_RECORD_EXCLUSIONS = {
    "numpy": ("../../../bin/f2py", "../../../bin/numpy-config"),
    "matplotlib": (), "contourpy": (), "cycler": (),
    "fonttools": (
        "../../../bin/fonttools", "../../../bin/pyftmerge",
        "../../../bin/pyftsubset", "../../../bin/ttx",
        "../../../share/man/man1/ttx.1",
    ),
    "kiwisolver": (), "packaging": (), "pillow": (), "pyparsing": (),
    "python-dateutil": (), "six": (),
}
EXTERNAL_RECORD_EXCLUSION_ENTRIES = tuple(
    {"distribution": distribution, "relative_path": relative_path}
    for distribution in sorted(EXTERNAL_RECORD_EXCLUSIONS)
    for relative_path in sorted(EXTERNAL_RECORD_EXCLUSIONS[distribution])
)
if len(EXTERNAL_RECORD_EXCLUSION_ENTRIES) != 7:
    raise RuntimeError("the frozen external RECORD exclusion inventory is not exact7")
SUPPORT_FILES = (
    "build_full_band_s4p_qa_v3.py",
    "FULL_BAND_V3_PANEL_SCHEMA_ADDENDUM.json",
    "EMX_RESULT_INTERFACE_TEMPLATE_FROZEN.json",
)
ROOT_CHILDREN = frozenset({
    "bundle", "private_runtime_site_packages",
    "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json", *SUPPORT_FILES,
})
BUILD_RECEIPT_TOP_KEYS = frozenset({
    "schema", "status", "created_utc", "decision_id", "authorization",
    "journal", "publication", "runtime", "support_files", "bound_v8",
    "source_runtime", "external_record_exclusions", "package_binding",
    "trusted_launch", "scope",
})
BUILD_RECEIPT_NESTED_KEYS = {
    "authorization": frozenset({"path", "sha256", "logical_builder_argv"}),
    "journal": frozenset({
        "directory", "directory_device", "directory_inode", "parent_path",
        "parent_device", "parent_inode", "begin_path", "begin_sha256",
        "commit_intent_path", "commit_intent_sha256", "terminal_path",
        "lock_path", "lock_device", "lock_inode", "lock_method",
        "terminal_publication_method", "terminal_canonical_visibility_rule",
    }),
    "publication": frozenset({
        "method", "final_root_path", "final_root_device", "final_root_inode",
        "staging_device", "staging_inode", "final_inode_equals_staging",
        "files_only_full_root_digest", "structural_full_root_digest",
    }),
    "runtime": frozenset({
        "manifest_path", "manifest_sha256", "files_only_runtime_root_digest",
        "private_root_path", "private_root_device", "private_root_inode",
        "files_only_private_root_digest", "structural_private_tree_digest",
        "bundle_root_path", "bundle_root_device", "bundle_root_inode",
    }),
    "bound_v8": frozenset({
        "bundle_path", "prepared_receipt_sha256", "bundle_manifest_sha256",
        "sha256_index_sha256", "top_level_count", "indexed_count",
    }),
    "source_runtime": frozenset({
        "python_path", "python_sha256", "site_packages_path",
        "site_packages_device", "site_packages_inode", "source_inventory_digest",
    }),
    "package_binding": frozenset({
        "v10_builder_sha256", "v10_test_sha256", "v10_smoke_sha256",
        "v10_smoke_test_sha256", "v10_smoke_bootstrap_sha256",
        "v10_smoke_bootstrap_size_bytes",
        "v10_bundle_manifest_sha256", "v10_sha256_index_sha256",
        "v10_prepared_receipt_sha256", "v10_independent_audit_receipt_sha256",
        "v9_negative_qa_bundle_manifest_sha256",
        "v9_negative_qa_command_log_sha256",
        "v9_negative_qa_attempt1_empty_stdout_sha256",
        "v9_negative_qa_attempt1_failure_sha256",
        "v9_negative_qa_harness_sha256",
        "v9_negative_qa_output_sha256",
        "v9_negative_qa_receipt_sha256",
        "v9_negative_qa_report_sha256",
        "v9_negative_qa_closure_sha256",
        "v9_negative_qa_sha256_index_sha256",
        "v8_negative_qa_bundle_manifest_sha256",
        "v8_negative_qa_command_log_sha256",
        "v8_negative_qa_attempt1_empty_stdout_sha256",
        "v8_negative_qa_attempt1_failure_sha256",
        "v8_negative_qa_attempt2_empty_stdout_sha256",
        "v8_negative_qa_attempt2_failure_sha256",
        "v8_negative_qa_attempt3_failure_sha256",
        "v8_negative_qa_attempt3_output_sha256",
        "v8_negative_qa_harness_sha256",
        "v8_negative_qa_output_sha256",
        "v8_negative_qa_receipt_sha256",
        "v8_negative_qa_report_sha256",
        "v8_negative_qa_closure_sha256",
        "v8_negative_qa_sha256_index_sha256",
        "v7_negative_qa_bundle_manifest_sha256",
        "v7_negative_qa_log_sha256", "v7_negative_qa_output_sha256",
        "v7_negative_qa_receipt_sha256", "v7_negative_qa_report_sha256",
        "v7_negative_qa_closure_sha256", "v7_negative_qa_harness_sha256",
        "v7_negative_qa_sha256_index_sha256",
        "v1_audit_receipt_sha256", "runtime_inventory_sha256",
    }),
    "trusted_launch": frozenset({
        "schema", "status", "method", "interpreter_fd", "builder_source_fd",
        "interpreter_proc_path", "builder_source_proc_path",
        "interpreter_fd_inheritable", "builder_source_fd_inheritable",
        "interpreter_identity", "builder_source_identity",
        "interpreter_sha256", "builder_source_sha256",
        "builder_original_evidence_path", "outer_launch_receipt_path",
        "outer_launch_receipt_sha256", "outer_process_argv",
        "outer_process_argv_sha256", "root_launch_authorization_path",
        "root_launch_authorization_sha256",
        "preflight_package_manifest_path",
        "preflight_package_manifest_sha256", "preflight_package_index_path",
        "preflight_package_index_sha256",
        "preflight_independent_audit_receipt_path",
        "preflight_independent_audit_receipt_sha256",
        "preflight_independent_audit_index_path",
        "preflight_independent_audit_index_sha256",
    }),
    "scope": frozenset({
        "result_free_transport_runtime_layout_only", "result_accessed",
        "signals_sent", "processes_inspected", "controller_or_outer_main_executed",
        "deployment_or_resume_executed", "smoke_executed", "linux_integration",
    }),
}
SUPPORT_FILE_RECEIPT_KEYS = frozenset({
    "path", "device", "inode", "sha256", "size_bytes",
})
FAIL_TERMINAL_TOP_KEYS = frozenset({
    "schema", "status", "created_utc", "authorization_sha256",
    "decision_id", "phase", "error_type", "error_message",
    "staging_preservation", "fixed_root_path_present",
    "fixed_root_published", "fixed_root_exactly_validated",
    "terminal_publication_method", "terminal_canonical_visibility_rule",
    "result_accessed", "signals_sent",
})
JOURNAL_NAMES = {
    "begin": "BEGIN.json", "intent": "COMMIT_INTENT.json",
    "terminal": "TERMINAL.json", "staging": "STAGING", "lock": "LOCK",
}
LOCK_METHOD = "fcntl.flock(LOCK_EX|LOCK_NB)_HELD_THROUGH_TERMINAL"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
DECISION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{15,127}$")
V10_PACKAGE_BINDING_KEYS = frozenset({
    "directory", "builder_path", "builder_sha256", "test_path", "test_sha256",
    "smoke_path", "smoke_sha256", "smoke_test_path", "smoke_test_sha256",
    "bundle_manifest_path",
    "bundle_manifest_sha256", "sha256_index_path", "sha256_index_sha256",
    "prepared_receipt_path", "prepared_receipt_sha256",
})
V10_AUDIT_BINDING_KEYS = frozenset({
    "directory", "action_scoped_verdict", "report_path", "report_sha256",
    "receipt_path", "receipt_sha256", "output_path", "output_sha256",
    "log_path", "log_sha256", "harness_path", "harness_sha256",
    "sha256_index_path", "sha256_index_sha256",
})
V9_NEGATIVE_QA_BINDING_KEYS = frozenset({
    "directory", "action_scoped_verdict", "finding_counts",
    *(
        key
        for stem in V9_NEGATIVE_QA_FILE_BINDINGS
        for key in (f"{stem}_path", f"{stem}_sha256")
    ),
})
V8_NEGATIVE_QA_BINDING_KEYS = frozenset({
    "directory", "action_scoped_verdict", "finding_counts",
    *(
        key
        for stem in V8_NEGATIVE_QA_FILE_BINDINGS
        for key in (f"{stem}_path", f"{stem}_sha256")
    ),
})
V7_NEGATIVE_QA_BINDING_KEYS = frozenset({
    "directory", "action_scoped_verdict", "finding_counts",
    "bundle_manifest_path", "bundle_manifest_sha256",
    "log_path", "log_sha256", "output_path", "output_sha256",
    "receipt_path", "receipt_sha256", "report_path", "report_sha256",
    "closure_path", "closure_sha256", "harness_path", "harness_sha256",
    "sha256_index_path", "sha256_index_sha256",
})


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int
    mode: int
    nlink: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "Identity":
        return cls(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                   stat.S_IMODE(value.st_mode), value.st_nlink)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "Identity":
        expected = {"device", "inode", "size_bytes", "mtime_ns", "mode", "nlink"}
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or any(type(value[key]) is not int for key in ("device", "inode", "size_bytes", "mtime_ns", "nlink"))
            or type(value["mode"]) is not str
            or re.fullmatch(r"0[0-7]{3}", value["mode"]) is None
        ):
            raise BuildError("identity JSON schema mismatch")
        return cls(value["device"], value["inode"], value["size_bytes"],
                   value["mtime_ns"], int(value["mode"], 8), value["nlink"])

    def json(self) -> dict[str, Any]:
        return {
            "device": self.device, "inode": self.inode, "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns, "mode": f"{self.mode:04o}", "nlink": self.nlink,
        }


@dataclass(frozen=True)
class SourceFileSnapshot:
    relative_path: str
    distribution: str
    identity: Identity
    sha256: str


@dataclass
class DistributionSnapshot:
    canonical_name: str
    declared_name: str
    version: str
    record_relative_path: str
    metadata_relative_path: str
    import_relative_path: str | None
    raw_record_members: tuple[str, ...]
    safe_members: tuple[str, ...]
    excluded_members: tuple[str, ...]
    record_sha256: str
    closure_digest: str
    held_record_fd: int


@dataclass
class DiscoverySnapshot:
    root_path: Path
    root_fd: int
    root_identity: Identity
    files: dict[str, SourceFileSnapshot]
    distributions: dict[str, DistributionSnapshot]

    def close(self) -> None:
        for item in self.distributions.values():
            try:
                os.close(item.held_record_fd)
            except OSError:
                pass
        try:
            os.close(self.root_fd)
        except OSError:
            pass

    def inventory(self) -> dict[str, Any]:
        distributions: dict[str, Any] = {}
        for name in COPY_DISTRIBUTIONS:
            item = self.distributions[name]
            member_rows = [
                {
                    "relative_path": relative,
                    "sha256": self.files[relative].sha256,
                    "size_bytes": self.files[relative].identity.size_bytes,
                    "device": self.files[relative].identity.device,
                    "inode": self.files[relative].identity.inode,
                    "mtime_ns": self.files[relative].identity.mtime_ns,
                    "mode": f"{self.files[relative].identity.mode:04o}",
                    "nlink": self.files[relative].identity.nlink,
                }
                for relative in sorted(item.safe_members)
            ]
            distributions[name] = {
                "declared_name": item.declared_name,
                "version": item.version,
                "record_relative_path": item.record_relative_path,
                "record_sha256": item.record_sha256,
                "metadata_relative_path": item.metadata_relative_path,
                "metadata_sha256": self.files[item.metadata_relative_path].sha256,
                "safe_file_count": len(item.safe_members),
                "safe_closure_digest": item.closure_digest,
                "member_snapshot_digest": sha256_bytes(canonical_json_bytes(member_rows)),
                "excluded_out_of_site_record_members": list(item.excluded_members),
            }
        core = {
            "source_root_identity": self.root_identity.json(),
            "distribution_order": list(COPY_DISTRIBUTIONS),
            "distributions": distributions,
            "external_record_exclusion_evidence": external_record_exclusion_evidence(),
        }
        return {**core, "inventory_digest": sha256_bytes(canonical_json_bytes(core))}


@dataclass
class StagingHandles:
    journal_fd: int
    staging_fd: int
    bundle_fd: int
    private_fd: int
    staging_identity: Identity
    bundle_identity: Identity
    private_identity: Identity
    evidence: dict[str, Any]

    def close(self) -> None:
        for fd in (self.private_fd, self.bundle_fd, self.staging_fd, self.journal_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def exact_string(value: Any, label: str) -> str:
    if type(value) is not str:
        raise BuildError(f"{label} must be a JSON string")
    return value


def exact_integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise BuildError(f"{label} must be a nonnegative JSON integer")
    return value


def exact_boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise BuildError(f"{label} must be a JSON boolean")
    return value


def exact_sha256(value: Any, label: str) -> str:
    text = exact_string(value, label)
    if SHA_RE.fullmatch(text) is None:
        raise BuildError(f"{label} must be lowercase SHA-256")
    return text


def exact_string_list(value: Any, label: str) -> list[str]:
    if type(value) is not list or not value or not all(type(item) is str for item in value):
        raise BuildError(f"{label} must be a nonempty JSON list[str]")
    if any("\0" in item for item in value):
        raise BuildError(f"{label} contains NUL")
    return list(value)


def validate_trusted_launch_binding_shape(
    value: Any, package: Mapping[str, Any], source_python_sha256: str
) -> dict[str, Any]:
    expected = BUILD_RECEIPT_NESTED_KEYS["trusted_launch"]
    if type(value) is not dict or set(value) != expected:
        raise BuildError("trusted_launch exact schema mismatch")
    if (
        exact_string(value["schema"], "trusted_launch.schema")
        != HELD_BUILDER_LAUNCH_SCHEMA
        or exact_string(value["status"], "trusted_launch.status")
        != HELD_BUILDER_LAUNCH_STATUS
        or exact_string(value["method"], "trusted_launch.method")
        != HELD_BUILDER_LAUNCH_METHOD
    ):
        raise BuildError("trusted_launch schema/status/method mismatch")
    if exact_integer(value["interpreter_fd"], "trusted_launch.interpreter_fd") != HELD_INTERPRETER_FD:
        raise BuildError("trusted_launch interpreter FD mismatch")
    if exact_integer(value["builder_source_fd"], "trusted_launch.builder_source_fd") != HELD_BUILDER_SOURCE_FD:
        raise BuildError("trusted_launch builder source FD mismatch")
    if value["interpreter_proc_path"] != f"/proc/self/fd/{HELD_INTERPRETER_FD}":
        raise BuildError("trusted_launch interpreter proc path mismatch")
    if value["builder_source_proc_path"] != f"/proc/self/fd/{HELD_BUILDER_SOURCE_FD}":
        raise BuildError("trusted_launch builder source proc path mismatch")
    if (
        exact_boolean(
            value["interpreter_fd_inheritable"],
            "trusted_launch.interpreter_fd_inheritable",
        ) is not True
        or exact_boolean(
            value["builder_source_fd_inheritable"],
            "trusted_launch.builder_source_fd_inheritable",
        ) is not False
    ):
        raise BuildError("trusted_launch FD inheritance policy mismatch")
    interpreter_identity = Identity.from_json(value["interpreter_identity"])
    builder_identity = Identity.from_json(value["builder_source_identity"])
    if not (
        interpreter_identity.mode & 0o111
        and interpreter_identity.nlink == 1
        and builder_identity.mode == 0o444
        and builder_identity.nlink == 1
    ):
        raise BuildError("trusted_launch identity mode/nlink mismatch")
    if exact_sha256(value["interpreter_sha256"], "trusted_launch.interpreter_sha256") != source_python_sha256:
        raise BuildError("trusted_launch interpreter SHA mismatch")
    if exact_sha256(value["builder_source_sha256"], "trusted_launch.builder_source_sha256") != package["builder_sha256"]:
        raise BuildError("trusted_launch builder SHA mismatch")
    if value["builder_original_evidence_path"] != package["builder_path"]:
        raise BuildError("trusted_launch builder evidence path mismatch")
    launch_receipt_path = Path(exact_string(
        value["outer_launch_receipt_path"], "trusted_launch.outer_launch_receipt_path"
    ))
    if not launch_receipt_path.is_absolute():
        raise BuildError("trusted_launch outer receipt path must be absolute")
    exact_sha256(
        value["outer_launch_receipt_sha256"],
        "trusted_launch.outer_launch_receipt_sha256",
    )
    argv = exact_string_list(value["outer_process_argv"], "trusted_launch.outer_process_argv")
    argv_digest = sha256_bytes(canonical_json_bytes(argv))
    if exact_sha256(
        value["outer_process_argv_sha256"],
        "trusted_launch.outer_process_argv_sha256",
    ) != argv_digest:
        raise BuildError("trusted_launch outer process argv digest mismatch")
    for stem in (
        "root_launch_authorization",
        "preflight_package_manifest",
        "preflight_package_index",
        "preflight_independent_audit_receipt",
        "preflight_independent_audit_index",
    ):
        anchor_path = Path(exact_string(
            value[f"{stem}_path"], f"trusted_launch.{stem}_path"
        ))
        if not anchor_path.is_absolute():
            raise BuildError(f"trusted_launch.{stem}_path must be absolute")
        exact_sha256(
            value[f"{stem}_sha256"], f"trusted_launch.{stem}_sha256"
        )
    if Path(value["preflight_package_manifest_path"]).parent != Path(
        value["preflight_package_index_path"]
    ).parent:
        raise BuildError("preflight package manifest/index directory mismatch")
    if Path(value["preflight_independent_audit_receipt_path"]).parent != Path(
        value["preflight_independent_audit_index_path"]
    ).parent:
        raise BuildError("preflight audit receipt/index directory mismatch")
    return dict(value)


def validate_outer_launch_receipt_semantics(
    receipt: Any,
    *,
    auth: Mapping[str, Any],
) -> None:
    """Validate the complete-first root/preflight admission receipt.

    Its own digest lives only in ``trusted_launch``; the receipt deliberately
    contains no self-reference.  The two dynamic trust anchors below are
    independently signed artifacts consumed by the future root launcher.
    """

    keys = {
        "schema", "status", "created_utc", "decision_id",
        "root_launch_authorization_sha256",
        "root_launch_authorization_path",
        "preflight_package_manifest_sha256",
        "preflight_package_manifest_path", "preflight_package_index_path",
        "preflight_package_index_sha256",
        "preflight_independent_audit_receipt_sha256",
        "preflight_independent_audit_receipt_path",
        "preflight_independent_audit_index_path",
        "preflight_independent_audit_index_sha256",
        "interpreter_fd", "builder_source_fd", "interpreter_identity",
        "builder_source_identity", "interpreter_sha256",
        "builder_source_sha256", "builder_original_evidence_path",
        "outer_process_argv", "outer_process_argv_sha256", "scope",
        "authority",
    }
    if type(receipt) is not dict or set(receipt) != keys:
        raise BuildError("outer launch receipt exact key set mismatch")
    if (
        receipt["schema"] != OUTER_LAUNCH_RECEIPT_SCHEMA
        or receipt["status"] != OUTER_LAUNCH_RECEIPT_STATUS
        or receipt["decision_id"] != auth["decision_id"]
    ):
        raise BuildError("outer launch receipt schema/status/decision mismatch")
    created = exact_string(receipt["created_utc"], "outer_receipt.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError("outer launch receipt created_utc is not canonical UTC")
    for stem in (
        "root_launch_authorization", "preflight_package_manifest",
        "preflight_package_index", "preflight_independent_audit_receipt",
        "preflight_independent_audit_index",
    ):
        path = Path(exact_string(
            receipt[f"{stem}_path"], f"outer_receipt.{stem}_path"
        ))
        if not path.is_absolute():
            raise BuildError(f"outer_receipt.{stem}_path must be absolute")
        exact_sha256(receipt[f"{stem}_sha256"], f"outer_receipt.{stem}_sha256")
    launch = auth["trusted_launch"]
    comparisons = {
        "interpreter_fd": launch["interpreter_fd"],
        "builder_source_fd": launch["builder_source_fd"],
        "interpreter_identity": launch["interpreter_identity"],
        "builder_source_identity": launch["builder_source_identity"],
        "interpreter_sha256": launch["interpreter_sha256"],
        "builder_source_sha256": launch["builder_source_sha256"],
        "builder_original_evidence_path": launch[
            "builder_original_evidence_path"
        ],
        "outer_process_argv": launch["outer_process_argv"],
        "outer_process_argv_sha256": launch["outer_process_argv_sha256"],
        "root_launch_authorization_path": launch[
            "root_launch_authorization_path"
        ],
        "root_launch_authorization_sha256": launch[
            "root_launch_authorization_sha256"
        ],
        "preflight_package_manifest_path": launch[
            "preflight_package_manifest_path"
        ],
        "preflight_package_manifest_sha256": launch[
            "preflight_package_manifest_sha256"
        ],
        "preflight_package_index_path": launch[
            "preflight_package_index_path"
        ],
        "preflight_package_index_sha256": launch[
            "preflight_package_index_sha256"
        ],
        "preflight_independent_audit_receipt_path": launch[
            "preflight_independent_audit_receipt_path"
        ],
        "preflight_independent_audit_receipt_sha256": launch[
            "preflight_independent_audit_receipt_sha256"
        ],
        "preflight_independent_audit_index_path": launch[
            "preflight_independent_audit_index_path"
        ],
        "preflight_independent_audit_index_sha256": launch[
            "preflight_independent_audit_index_sha256"
        ],
    }
    if any(receipt[key] != value for key, value in comparisons.items()):
        raise BuildError("outer launch receipt held-byte binding mismatch")
    if receipt["scope"] != "TRUSTED_HELD_PREFLIGHT_BUILDER_LAUNCH_ONLY":
        raise BuildError("outer launch receipt scope mismatch")
    expected_authority = {
        "builder_launch_authorized": True,
        "transport_runtime_layout_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "controller_or_outer_main_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    if receipt["authority"] != expected_authority:
        raise BuildError("outer launch receipt authority mismatch")


def validate_root_launch_authorization_semantics(
    value: Any, *, auth: Mapping[str, Any]
) -> None:
    launch = auth["trusted_launch"]
    keys = {
        "schema", "status", "created_utc", "decision_id",
        "preflight_package_manifest_path",
        "preflight_package_manifest_sha256", "preflight_package_index_path",
        "preflight_package_index_sha256",
        "preflight_independent_audit_receipt_path",
        "preflight_independent_audit_receipt_sha256",
        "preflight_independent_audit_index_path",
        "preflight_independent_audit_index_sha256", "authority",
    }
    if type(value) is not dict or set(value) != keys:
        raise BuildError("root launch authorization exact key set mismatch")
    if (
        value["schema"] != ROOT_LAUNCH_AUTHORIZATION_SCHEMA
        or value["status"] != ROOT_LAUNCH_AUTHORIZATION_STATUS
        or value["decision_id"] != auth["decision_id"]
    ):
        raise BuildError("root launch authorization schema/status/decision mismatch")
    created = exact_string(value["created_utc"], "root_launch.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError("root launch authorization created_utc is invalid")
    for stem in (
        "preflight_package_manifest", "preflight_package_index",
        "preflight_independent_audit_receipt",
        "preflight_independent_audit_index",
    ):
        if (
            value[f"{stem}_path"] != launch[f"{stem}_path"]
            or value[f"{stem}_sha256"] != launch[f"{stem}_sha256"]
        ):
            raise BuildError(f"root launch authorization {stem} cross-binding mismatch")
    expected_authority = {
        "preflight_launch_authorized": True,
        "transport_runtime_layout_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    if value["authority"] != expected_authority:
        raise BuildError("root launch authorization authority mismatch")


def validate_v10_audit_receipt_semantics(
    receipt: Any,
    *,
    audit_binding: Mapping[str, Any],
    package_binding: Mapping[str, Any],
) -> None:
    """Require a formal zero-finding QA receipt, not a sibling GO string."""

    keys = {
        "schema", "status", "created_utc", "action_scoped_verdict",
        "finding_counts", "package", "audit_evidence", "authority",
    }
    if type(receipt) is not dict or set(receipt) != keys:
        raise BuildError("v10 independent QA receipt exact key set mismatch")
    if (
        receipt["schema"] != V10_QA_RECEIPT_SCHEMA
        or receipt["status"] != V10_QA_RECEIPT_STATUS
        or receipt["action_scoped_verdict"] != V10_QA_ACTION_VERDICT
        or audit_binding["action_scoped_verdict"] != V10_QA_ACTION_VERDICT
    ):
        raise BuildError("v10 independent QA receipt verdict mismatch")
    created = exact_string(receipt["created_utc"], "v10_qa_receipt.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError("v10 independent QA created_utc is not canonical UTC")
    if receipt["finding_counts"] != {"P0": 0, "P1": 0, "P2": 0, "P3": 0}:
        raise BuildError("v10 independent QA is not zero-finding")
    package_expected = {
        "directory": package_binding["directory"],
        "builder_sha256": package_binding["builder_sha256"],
        "test_sha256": package_binding["test_sha256"],
        "smoke_sha256": package_binding["smoke_sha256"],
        "smoke_test_sha256": package_binding["smoke_test_sha256"],
        "bundle_manifest_sha256": package_binding["bundle_manifest_sha256"],
        "sha256_index_sha256": package_binding["sha256_index_sha256"],
        "prepared_receipt_sha256": package_binding["prepared_receipt_sha256"],
    }
    if receipt["package"] != package_expected:
        raise BuildError("v10 independent QA package binding mismatch")
    evidence_expected = {
        key: audit_binding[f"{key}_sha256"]
        for key in ("report", "output", "log", "harness", "sha256_index")
    }
    if receipt["audit_evidence"] != evidence_expected:
        raise BuildError("v10 independent QA evidence binding mismatch")
    expected_authority = {
        "transport_runtime_layout_authorized": False,
        "mars_preflight_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "controller_or_outer_main_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    if receipt["authority"] != expected_authority:
        raise BuildError("v10 independent QA authority mismatch")


def validate_v9_negative_audit_receipt_semantics(
    receipt: Any, *, audit_binding: Mapping[str, Any]
) -> None:
    """Deep-bind the formal v9 P1/NO-GO that this version alone closes."""

    keys = {
        "action_scoped_verdict", "audit_artifacts", "audited_candidate",
        "authority", "created_utc", "finding_counts", "findings",
        "independent_validation", "next_legal_action",
        "preserved_failed_qa_attempts", "qa_directory", "schema", "scope",
        "status",
    }
    if type(receipt) is not dict or set(receipt) != keys:
        raise BuildError("v9 negative independent QA receipt key set mismatch")
    if (
        receipt["schema"] != V9_NEGATIVE_QA_RECEIPT_SCHEMA
        or receipt["status"] != V9_NEGATIVE_QA_RECEIPT_STATUS
        or receipt["action_scoped_verdict"]
        != V9_NEGATIVE_QA_ACTION_VERDICT
        or audit_binding["action_scoped_verdict"]
        != V9_NEGATIVE_QA_ACTION_VERDICT
        or receipt["finding_counts"]
        != V9_NEGATIVE_QA_BINDING["finding_counts"]
        or audit_binding["finding_counts"]
        != V9_NEGATIVE_QA_BINDING["finding_counts"]
        or receipt["qa_directory"]
        != V9_NEGATIVE_QA_BINDING["directory_name"]
    ):
        raise BuildError("v9 negative independent QA verdict/count mismatch")
    created = exact_string(receipt["created_utc"], "v9_negative_qa.created_utc")
    if created != "2026-08-22T20:38:18Z" or UTC_RE.fullmatch(created) is None:
        raise BuildError("v9 negative independent QA timestamp mismatch")
    if receipt["audited_candidate"] != V9_NEGATIVE_TARGET_PACKAGE:
        raise BuildError("v9 negative independent QA candidate binding mismatch")
    expected_artifacts = {
        "bundle_manifest_sha256": audit_binding["bundle_manifest_sha256"],
        "closure_sha256": audit_binding["closure_sha256"],
        "command_log_sha256": audit_binding["command_log_sha256"],
        "harness_sha256": audit_binding["harness_sha256"],
        "output_sha256": audit_binding["output_sha256"],
        "report_sha256": audit_binding["report_sha256"],
        "sha256_index_sha256": audit_binding["sha256_index_sha256"],
    }
    if receipt["audit_artifacts"] != expected_artifacts:
        raise BuildError("v9 negative independent QA artifact binding mismatch")
    expected_findings = [{
        "affected_paths": [
            "first_attempt_PASS", "recovery_PASS",
            "first_or_recovery_FAIL_terminal",
        ],
        "id": "P1-V9-001",
        "release_blocking": True,
        "summary": (
            "Synthetic/canonical named-parent replacement during terminal "
            "publication can return a false PASS because both held-path leases "
            "are not revalidated after terminal_publish_impl returns"
        ),
    }]
    if receipt["findings"] != expected_findings:
        raise BuildError("v9 negative independent QA complete finding set mismatch")
    expected_independent_validation = {
        "builder_synthetic": "PASS_94_OF_94_TWICE_BYTE_IDENTICAL",
        "candidate_closure": (
            "PASS_EXACT15_INDEX14_0555_0444_NLINK1_NO_CACHE"
        ),
        "fd197_fd198_readonly": (
            "PASS_BOOTSTRAP_AUTHENTICATED_SPAWN_POPEN_ZERO"
        ),
        "final_harness": (
            "PASS_EXPECTED_RELEASE_BLOCKER_35_TRUE_3_EXPECTED_FALSE_"
            "0_UNEXPECTED_TWICE_BYTE_IDENTICAL"
        ),
        "future_preflight": (
            "PASS_EXACT10_INDEX9_EXACT8_INDEX7_DYNAMIC_DEEP_HOSTILE"
        ),
        "linux_actual": "NOT_RUN_NON_LINUX_DARWIN",
        "native_api": "PASS_LOCAL_NOT_PRODUCTION_SCOPE_REAL_LINUX_NOT_RUN",
        "predecessor_evidence": (
            "PASS_V8_FORMAL_ALL14_V7_EXACT8_AND_FOUR_FALSE_SHA"
        ),
        "smoke_synthetic": "PASS_106_OF_106_TWICE_BYTE_IDENTICAL",
        "source_compile": "PASS_4_OF_4_TWICE_NO_CACHE",
        "transaction_no_clobber": (
            "PASS_COMPLETE_SECOND_PUBLISH_MIDWRITE_AND_BUILTIN_RACE"
        ),
    }
    if receipt["independent_validation"] != expected_independent_validation:
        raise BuildError("v9 negative independent QA validation summary mismatch")
    if receipt["preserved_failed_qa_attempts"] != {
        "attempt1_empty_stdout_sha256": (
            audit_binding["attempt1_empty_stdout_sha256"]
        ),
        "attempt1_failure_sha256": audit_binding["attempt1_failure_sha256"],
        "count": 1,
    }:
        raise BuildError("v9 negative independent QA failed-attempt closure mismatch")
    if receipt["authority"] != V9_NEGATIVE_QA_AUTHORITY:
        raise BuildError("v9 negative independent QA authority mismatch")
    if receipt["scope"] != V9_NEGATIVE_QA_SCOPE:
        raise BuildError("v9 negative independent QA scope mismatch")
    if receipt["next_legal_action"] != (
        "CREATE_NO_CLOBBER_V10_FIXING_POST_TERMINAL_PUBLISHER_BOTH_NAMED_"
        "PARENT_REVALIDATION_THEN_FRESH_LOCAL_RESULT_BLIND_INDEPENDENT_QA_ONLY"
    ):
        raise BuildError("v9 negative independent QA next action mismatch")


def validate_v9_negative_audit_manifest_semantics(
    manifest: Any, *, audit_binding: Mapping[str, Any]
) -> None:
    keys = {
        "action_scoped_verdict", "authority",
        "closure_files_not_in_payload_manifest", "created_utc", "files",
        "finding_counts", "payload_file_count", "schema", "status",
    }
    if type(manifest) is not dict or set(manifest) != keys:
        raise BuildError("v9 negative independent QA manifest key set mismatch")
    if (
        manifest["schema"] != V9_NEGATIVE_QA_MANIFEST_SCHEMA
        or manifest["status"] != V9_NEGATIVE_QA_MANIFEST_STATUS
        or manifest["action_scoped_verdict"]
        != V9_NEGATIVE_QA_ACTION_VERDICT
        or manifest["finding_counts"]
        != V9_NEGATIVE_QA_BINDING["finding_counts"]
        or manifest["authority"] != V9_NEGATIVE_QA_AUTHORITY
        or manifest["created_utc"] != "2026-08-22T20:38:18Z"
        or manifest["payload_file_count"] != 7
        or manifest["closure_files_not_in_payload_manifest"] != [
            "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json",
            "SHA256SUMS",
        ]
    ):
        raise BuildError("v9 negative independent QA manifest semantics mismatch")
    expected_records = []
    for stem, item in V9_NEGATIVE_QA_FILE_BINDINGS.items():
        if item["filename"] in {
            "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json",
            "SHA256SUMS",
        }:
            continue
        expected_records.append({
            "relative_path": item["filename"],
            "role": item["role"],
            "sha256": audit_binding[f"{stem}_sha256"],
            "size_bytes": item["size_bytes"],
        })
    expected_records.sort(key=lambda item: item["relative_path"])
    if manifest["files"] != expected_records:
        raise BuildError("v9 negative independent QA manifest payload mismatch")


def expected_v9_negative_audit_index(
    audit_binding: Mapping[str, Any]
) -> dict[str, str]:
    return {
        item["filename"]: audit_binding[f"{stem}_sha256"]
        for stem, item in V9_NEGATIVE_QA_FILE_BINDINGS.items()
        if item["filename"] not in {
            "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
        }
    }


def validate_v9_negative_audit_binding(
    value: Any, *, verify_bytes: bool
) -> None:
    validate_bound_files(
        value, V9_NEGATIVE_QA_BINDING_KEYS,
        "v9_negative_independent_audit", verify_bytes=verify_bytes,
    )
    if (
        Path(value["directory"]).name
        != V9_NEGATIVE_QA_BINDING["directory_name"]
        or value["action_scoped_verdict"]
        != V9_NEGATIVE_QA_ACTION_VERDICT
        or value["finding_counts"]
        != V9_NEGATIVE_QA_BINDING["finding_counts"]
        or any(
            value[f"{stem}_sha256"] != item["sha256"]
            or Path(value[f"{stem}_path"]).name != item["filename"]
            for stem, item in V9_NEGATIVE_QA_FILE_BINDINGS.items()
        )
    ):
        raise BuildError("v9 negative independent QA exact binding mismatch")
    directory_fd = open_directory_path(Path(value["directory"]))
    try:
        if (
            identity_fd(directory_fd).mode != 0o555
            or set(fresh_directory_names(directory_fd))
            != {
                item["filename"]
                for item in V9_NEGATIVE_QA_FILE_BINDINGS.values()
            }
        ):
            raise BuildError("v9 negative independent QA directory closure mismatch")
    finally:
        os.close(directory_fd)
    receipt_bytes, _ = read_frozen_regular_bytes_single_open(
        Path(value["receipt_path"]), value["receipt_sha256"],
        "v9 negative independent QA receipt",
    )
    receipt = strict_json_loads(receipt_bytes)
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise BuildError("v9 negative independent QA receipt is not canonical JSON")
    validate_v9_negative_audit_receipt_semantics(receipt, audit_binding=value)
    manifest_bytes, _ = read_frozen_regular_bytes_single_open(
        Path(value["bundle_manifest_path"]), value["bundle_manifest_sha256"],
        "v9 negative independent QA manifest",
    )
    manifest = strict_json_loads(manifest_bytes)
    if canonical_json_bytes(manifest) != manifest_bytes:
        raise BuildError("v9 negative independent QA manifest is not canonical JSON")
    validate_v9_negative_audit_manifest_semantics(manifest, audit_binding=value)
    index_bytes, _ = read_frozen_regular_bytes_single_open(
        Path(value["sha256_index_path"]), value["sha256_index_sha256"],
        "v9 negative independent QA SHA index",
    )
    if parse_sha_index(index_bytes) != expected_v9_negative_audit_index(value):
        raise BuildError("v9 negative independent QA SHA index mismatch")


def validate_v8_negative_audit_receipt_semantics(
    receipt: Any, *, audit_binding: Mapping[str, Any]
) -> None:
    """Deep-bind the sole formal v8 P0/NO-GO as negative predecessor."""

    keys = {
        "action_scoped_verdict", "audit_artifacts", "audited_candidate",
        "authority", "created_utc", "finding_counts", "findings",
        "independent_validation", "next_legal_action",
        "preserved_failed_qa_attempts", "qa_directory", "schema", "scope",
        "status",
    }
    if type(receipt) is not dict or set(receipt) != keys:
        raise BuildError("v8 negative independent QA receipt key set mismatch")
    if (
        receipt["schema"] != V8_NEGATIVE_QA_RECEIPT_SCHEMA
        or receipt["status"] != V8_NEGATIVE_QA_RECEIPT_STATUS
        or receipt["action_scoped_verdict"]
        != V8_NEGATIVE_QA_ACTION_VERDICT
        or audit_binding["action_scoped_verdict"]
        != V8_NEGATIVE_QA_ACTION_VERDICT
        or receipt["finding_counts"]
        != V8_NEGATIVE_QA_BINDING["finding_counts"]
        or audit_binding["finding_counts"]
        != V8_NEGATIVE_QA_BINDING["finding_counts"]
        or receipt["qa_directory"]
        != V8_NEGATIVE_QA_BINDING["directory_name"]
    ):
        raise BuildError("v8 negative independent QA verdict/count mismatch")
    created = exact_string(receipt["created_utc"], "v8_negative_qa.created_utc")
    if created != "2026-08-22T20:11:23Z" or UTC_RE.fullmatch(created) is None:
        raise BuildError("v8 negative independent QA timestamp mismatch")
    if receipt["audited_candidate"] != V8_NEGATIVE_TARGET_PACKAGE:
        raise BuildError("v8 negative independent QA candidate binding mismatch")
    expected_artifacts = {
        "bundle_manifest_sha256": audit_binding["bundle_manifest_sha256"],
        "closure_sha256": audit_binding["closure_sha256"],
        "command_log_sha256": audit_binding["command_log_sha256"],
        "harness_sha256": audit_binding["harness_sha256"],
        "output_sha256": audit_binding["output_sha256"],
        "report_sha256": audit_binding["report_sha256"],
        "sha256_index_sha256": audit_binding["sha256_index_sha256"],
    }
    if receipt["audit_artifacts"] != expected_artifacts:
        raise BuildError("v8 negative independent QA artifact binding mismatch")
    expected_findings = [{
        "id": "P0-V8-001",
        "release_blocking": True,
        "summary": (
            "_reject_synthetic_production_paths fails to reject a synthetic "
            "parent that is an inode ancestor of the canonical production parent"
        ),
    }]
    if receipt["findings"] != expected_findings:
        raise BuildError("v8 negative independent QA complete finding set mismatch")
    expected_independent_validation = {
        "builder_synthetic": "PASS_86_OF_86_TWICE_BYTE_IDENTICAL",
        "candidate_closure": "PASS_EXACT15_INDEX14_MODES_AND_SHA",
        "fd197_fd198_readonly": (
            "PASS_BOOTSTRAP_AUTHENTICATED_SPAWN_POPEN_ZERO"
        ),
        "final_harness": (
            "PASS_EXPECTED_RELEASE_BLOCKER_53_TRUE_OF_54_TWICE_BYTE_IDENTICAL"
        ),
        "future_preflight_v2": (
            "PASS_EXACT10_INDEX9_AND_EXACT8_INDEX7_DEEP_HOSTILE_MUTATIONS"
        ),
        "linux_actual": "NOT_RUN_NON_LINUX_DARWIN",
        "named_leases": "PASS_FILE_DIRECTORY_REPLACEMENT_AND_CLOSE",
        "native_api": "PASS_LOCAL_SCOPE_GUARDS_REAL_LINUX_NOT_RUN",
        "smoke_synthetic": "PASS_105_OF_105_TWICE_BYTE_IDENTICAL",
        "transaction_no_clobber": (
            "PASS_COMPLETE_MIDWRITE_SECOND_PUBLISH_AND_RACE"
        ),
        "v7_negative_evidence": (
            "PASS_ALL8_BINDINGS_AND_FOUR_HISTORICAL_FALSE_SHA_RECOMPUTED"
        ),
    }
    if receipt["independent_validation"] != expected_independent_validation:
        raise BuildError("v8 negative independent QA validation summary mismatch")
    if receipt["preserved_failed_qa_attempts"] != {
        "attempt1_failure_sha256": audit_binding["attempt1_failure_sha256"],
        "attempt2_failure_sha256": audit_binding["attempt2_failure_sha256"],
        "attempt3_failure_sha256": audit_binding["attempt3_failure_sha256"],
        "attempt3_output_sha256": audit_binding["attempt3_output_sha256"],
        "count": 3,
    }:
        raise BuildError("v8 negative independent QA failed-attempt closure mismatch")
    if receipt["authority"] != V8_NEGATIVE_QA_AUTHORITY:
        raise BuildError("v8 negative independent QA authority mismatch")
    if receipt["scope"] != V8_NEGATIVE_QA_SCOPE:
        raise BuildError("v8 negative independent QA scope mismatch")
    if receipt["next_legal_action"] != (
        "CREATE_NO_CLOBBER_V9_FIXING_BIDIRECTIONAL_SYNTHETIC_CANONICAL_"
        "INODE_ANCESTRY_AND_BOTH_NAMED_PARENT_CONTINUITY_THEN_FRESH_"
        "RESULT_BLIND_INDEPENDENT_QA_ONLY"
    ):
        raise BuildError("v8 negative independent QA next action mismatch")


def validate_v8_negative_audit_manifest_semantics(
    manifest: Any, *, audit_binding: Mapping[str, Any]
) -> None:
    keys = {
        "action_scoped_verdict", "authority",
        "closure_files_not_in_payload_manifest", "created_utc", "files",
        "finding_counts", "payload_file_count", "schema", "status",
    }
    if type(manifest) is not dict or set(manifest) != keys:
        raise BuildError("v8 negative independent QA manifest key set mismatch")
    if (
        manifest["schema"] != V8_NEGATIVE_QA_MANIFEST_SCHEMA
        or manifest["status"] != V8_NEGATIVE_QA_MANIFEST_STATUS
        or manifest["action_scoped_verdict"]
        != V8_NEGATIVE_QA_ACTION_VERDICT
        or manifest["finding_counts"]
        != V8_NEGATIVE_QA_BINDING["finding_counts"]
        or manifest["authority"] != V8_NEGATIVE_QA_AUTHORITY
        or manifest["created_utc"] != "2026-08-22T20:11:23Z"
        or manifest["payload_file_count"] != 11
        or manifest["closure_files_not_in_payload_manifest"] != [
            "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json",
            "SHA256SUMS",
        ]
    ):
        raise BuildError("v8 negative independent QA manifest semantics mismatch")
    expected_records = []
    for stem, item in V8_NEGATIVE_QA_FILE_BINDINGS.items():
        if item["filename"] in {
            "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json",
            "SHA256SUMS",
        }:
            continue
        expected_records.append({
            "relative_path": item["filename"],
            "role": item["role"],
            "sha256": audit_binding[f"{stem}_sha256"],
            "size_bytes": item["size_bytes"],
        })
    expected_records.sort(key=lambda item: item["relative_path"])
    if manifest["files"] != expected_records:
        raise BuildError("v8 negative independent QA manifest payload mismatch")


def expected_v8_negative_audit_index(
    audit_binding: Mapping[str, Any]
) -> dict[str, str]:
    return {
        item["filename"]: audit_binding[f"{stem}_sha256"]
        for stem, item in V8_NEGATIVE_QA_FILE_BINDINGS.items()
        if item["filename"] not in {
            "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
        }
    }


def validate_v8_negative_audit_binding(
    value: Any, *, verify_bytes: bool
) -> None:
    validate_bound_files(
        value, V8_NEGATIVE_QA_BINDING_KEYS,
        "v8_negative_independent_audit", verify_bytes=verify_bytes,
    )
    if (
        Path(value["directory"]).name
        != V8_NEGATIVE_QA_BINDING["directory_name"]
        or value["action_scoped_verdict"]
        != V8_NEGATIVE_QA_ACTION_VERDICT
        or value["finding_counts"]
        != V8_NEGATIVE_QA_BINDING["finding_counts"]
        or any(
            value[f"{stem}_sha256"] != item["sha256"]
            or Path(value[f"{stem}_path"]).name != item["filename"]
            for stem, item in V8_NEGATIVE_QA_FILE_BINDINGS.items()
        )
    ):
        raise BuildError("v8 negative independent QA exact binding mismatch")
    directory_fd = open_directory_path(Path(value["directory"]))
    try:
        if (
            identity_fd(directory_fd).mode != 0o555
            or set(fresh_directory_names(directory_fd))
            != {
                item["filename"]
                for item in V8_NEGATIVE_QA_FILE_BINDINGS.values()
            }
        ):
            raise BuildError("v8 negative independent QA directory closure mismatch")
    finally:
        os.close(directory_fd)
    receipt_bytes, _ = read_frozen_regular_bytes_single_open(
        Path(value["receipt_path"]), value["receipt_sha256"],
        "v8 negative independent QA receipt",
    )
    receipt = strict_json_loads(receipt_bytes)
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise BuildError("v8 negative independent QA receipt is not canonical JSON")
    validate_v8_negative_audit_receipt_semantics(
        receipt, audit_binding=value
    )
    manifest_bytes, _ = read_frozen_regular_bytes_single_open(
        Path(value["bundle_manifest_path"]), value["bundle_manifest_sha256"],
        "v8 negative independent QA manifest",
    )
    manifest = strict_json_loads(manifest_bytes)
    if canonical_json_bytes(manifest) != manifest_bytes:
        raise BuildError("v8 negative independent QA manifest is not canonical JSON")
    validate_v8_negative_audit_manifest_semantics(
        manifest, audit_binding=value
    )
    index_bytes, _ = read_frozen_regular_bytes_single_open(
        Path(value["sha256_index_path"]), value["sha256_index_sha256"],
        "v8 negative independent QA SHA index",
    )
    if parse_sha_index(index_bytes) != expected_v8_negative_audit_index(value):
        raise BuildError("v8 negative independent QA SHA index mismatch")


def validate_v7_negative_audit_receipt_semantics(
    receipt: Any, *, audit_binding: Mapping[str, Any]
) -> None:
    """Bind the complete formal v7 NO-GO receipt as mandatory negative evidence."""

    keys = {
        "schema", "status", "created_utc", "action_scoped_verdict",
        "finding_counts", "package", "audit_evidence", "authority",
    }
    if type(receipt) is not dict or set(receipt) != keys:
        raise BuildError("v7 negative independent QA receipt exact key set mismatch")
    if (
        receipt["schema"] != V7_NEGATIVE_QA_RECEIPT_SCHEMA
        or receipt["status"] != V7_NEGATIVE_QA_RECEIPT_STATUS
        or receipt["action_scoped_verdict"] != V7_NEGATIVE_QA_ACTION_VERDICT
        or audit_binding["action_scoped_verdict"]
        != V7_NEGATIVE_QA_ACTION_VERDICT
        or receipt["finding_counts"] != V7_NEGATIVE_QA_BINDING["finding_counts"]
        or audit_binding["finding_counts"]
        != V7_NEGATIVE_QA_BINDING["finding_counts"]
    ):
        raise BuildError("v7 negative independent QA verdict/count mismatch")
    created = exact_string(receipt["created_utc"], "v7_negative_qa.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError("v7 negative independent QA created_utc is invalid")
    package = receipt["package"]
    expected_package_keys = {
        "directory", "builder_sha256", "test_sha256", "smoke_sha256",
        "smoke_test_sha256", "bundle_manifest_sha256",
        "sha256_index_sha256", "prepared_receipt_sha256",
    }
    if type(package) is not dict or set(package) != expected_package_keys:
        raise BuildError("v7 negative independent QA package schema mismatch")
    package_directory = Path(exact_string(
        package["directory"], "v7_negative_qa.package.directory"
    ))
    if (
        not package_directory.is_absolute()
        or package_directory.name
        != V7_NEGATIVE_TARGET_PACKAGE["directory_name"]
        or any(
            package[key] != V7_NEGATIVE_TARGET_PACKAGE[key]
            for key in V7_NEGATIVE_TARGET_PACKAGE
            if key != "directory_name"
        )
    ):
        raise BuildError("v7 negative independent QA target package mismatch")
    evidence_expected = {
        key: audit_binding[f"{key}_sha256"]
        for key in ("report", "output", "log", "harness", "sha256_index")
    }
    if receipt["audit_evidence"] != evidence_expected:
        raise BuildError("v7 negative independent QA receipt evidence mismatch")
    expected_authority = {
        "transport_runtime_layout_authorized": False,
        "mars_preflight_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "controller_or_outer_main_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    if receipt["authority"] != expected_authority:
        raise BuildError("v7 negative independent QA authority mismatch")


def validate_v7_negative_audit_binding(
    value: Any, *, verify_bytes: bool
) -> None:
    validate_bound_files(
        value, V7_NEGATIVE_QA_BINDING_KEYS, "v7_negative_independent_audit",
        verify_bytes=verify_bytes,
    )
    if (
        Path(value["directory"]).name != V7_NEGATIVE_QA_BINDING["directory_name"]
        or value["action_scoped_verdict"]
        != V7_NEGATIVE_QA_BINDING["action_scoped_verdict"]
        or value["finding_counts"] != V7_NEGATIVE_QA_BINDING["finding_counts"]
        or any(
            value[f"{stem}_sha256"]
            != V7_NEGATIVE_QA_BINDING[f"{stem}_sha256"]
            for stem in (
                "bundle_manifest", "log", "output", "receipt", "report",
                "closure", "harness", "sha256_index",
            )
        )
    ):
        raise BuildError("v7 negative independent QA exact binding mismatch")
    receipt, _ = read_frozen_json_single_open(
        Path(value["receipt_path"]), value["receipt_sha256"],
        "v7 negative independent QA receipt",
    )
    validate_v7_negative_audit_receipt_semantics(
        receipt, audit_binding=value
    )
    if verify_bytes:
        index_bytes, _ = read_frozen_regular_bytes_single_open(
            Path(value["sha256_index_path"]), value["sha256_index_sha256"],
            "v7 negative independent QA SHA index",
        )
        index = parse_sha_index(index_bytes)
        expected_index = {
            "BUNDLE_MANIFEST.json": value["bundle_manifest_sha256"],
            "COMMAND_LOG.txt": value["log_sha256"],
            "INDEPENDENT_QA_OUTPUT.json": value["output_sha256"],
            "INDEPENDENT_QA_REPORT_CN.md": value["report_sha256"],
            "PACKAGE_CLOSURE_QA.json": value["closure_sha256"],
            "QA_HARNESS_OR_METHOD.md": value["harness_sha256"],
        }
        if index != expected_index:
            raise BuildError("v7 negative independent QA SHA index closure mismatch")


def external_record_exclusion_evidence() -> dict[str, Any]:
    entries = [dict(item) for item in EXTERNAL_RECORD_EXCLUSION_ENTRIES]
    return {
        "policy": "EXACT7_ALLOWLIST_EXCLUDED_FROM_PRIVATE_TREE",
        "count": 7,
        "entries": entries,
        "evidence_digest": sha256_bytes(canonical_json_bytes(entries)),
    }


def strict_json_loads(data: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise BuildError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise BuildError(f"non-finite JSON constant: {value}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"strict JSON parse failed: {exc}") from exc


def read_fd_bytes(fd: int, limit: int = 128 * 1024 * 1024) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(fd, min(1024 * 1024, limit - offset + 1), offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
        if offset > limit:
            raise BuildError("bounded read limit exceeded")
    return b"".join(chunks)


def sha256_fd(fd: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(fd, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest(), offset


def identity_fd(fd: int) -> Identity:
    return Identity.from_stat(os.fstat(fd))


def require_regular_fd(fd: int, label: str) -> Identity:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BuildError(f"{label} is not a single-link regular file")
    return Identity.from_stat(info)


def require_directory_fd(fd: int, label: str) -> Identity:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise BuildError(f"{label} is not a directory")
    return Identity.from_stat(info)


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise BuildError(f"unsafe relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BuildError(f"unsafe relative path: {value!r}")
    if path.as_posix() != value:
        raise BuildError(f"noncanonical relative path: {value!r}")
    return value


def safe_name(value: str) -> str:
    if safe_relative(value) != value or "/" in value:
        raise BuildError(f"not a safe basename: {value!r}")
    return value


def canonical_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def open_directory_path(path: Path) -> int:
    """Open an absolute directory without following *any* path component."""

    text = os.fspath(path)
    pure = PurePosixPath(text)
    if (
        not pure.is_absolute()
        or text != os.fspath(pure)
        or any(part in {"", ".", ".."} for part in pure.parts[1:])
    ):
        raise BuildError(f"directory path is not canonical absolute: {path}")
    current = os.open(
        "/",
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        for part in pure.parts[1:]:
            child = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=current,
            )
            os.close(current)
            current = child
        return current
    except FileNotFoundError:
        os.close(current)
        raise
    except OSError as exc:
        os.close(current)
        raise BuildError(
            f"directory path component refused by nofollow traversal: {path}: {exc}"
        ) from exc
    except BaseException:
        os.close(current)
        raise


def directory_fd_is_at_or_below(
    directory_fd: int, ancestor_identity: Identity
) -> bool:
    """Compare real directory ancestry by held inode, never lexical spelling."""

    current = os.dup(directory_fd)
    try:
        for _ in range(4096):
            current_identity = identity_fd(current)
            if (
                current_identity.device == ancestor_identity.device
                and current_identity.inode == ancestor_identity.inode
            ):
                return True
            parent = os.open(
                "..",
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=current,
            )
            parent_identity = identity_fd(parent)
            if (
                parent_identity.device == current_identity.device
                and parent_identity.inode == current_identity.inode
            ):
                os.close(parent)
                return False
            os.close(current)
            current = parent
        raise BuildError("directory ancestry exceeded the bounded depth")
    finally:
        os.close(current)


def reject_bidirectional_directory_overlap(
    synthetic_parent_fd: int,
    canonical_parent_fd: int,
    *,
    label: str,
) -> tuple[Identity, Identity]:
    """Reject equality or either real-inode ancestry direction.

    Both arguments must already have been opened component-by-component with
    ``O_NOFOLLOW``.  Comparing both held directory objects is essential: only
    checking whether the synthetic directory is below the canonical directory
    misses an over-broad synthetic parent that already contains the canonical
    production parent.
    """

    synthetic_identity = require_directory_fd(
        synthetic_parent_fd, f"{label} synthetic parent"
    )
    canonical_identity = require_directory_fd(
        canonical_parent_fd, f"{label} canonical parent"
    )
    synthetic_at_or_below_canonical = directory_fd_is_at_or_below(
        synthetic_parent_fd, canonical_identity
    )
    canonical_at_or_below_synthetic = directory_fd_is_at_or_below(
        canonical_parent_fd, synthetic_identity
    )
    if synthetic_at_or_below_canonical or canonical_at_or_below_synthetic:
        raise BuildError(
            f"{label} synthetic/canonical parents overlap by equal inode or "
            "bidirectional real ancestry"
        )
    return synthetic_identity, canonical_identity


@dataclass
class FrozenFileLease:
    path: Path
    sha256: str
    fd: int
    identity: Identity
    parent_fd: int
    parent_identity: Identity

    @classmethod
    def open(cls, path: Path, expected_sha256: str) -> "FrozenFileLease":
        parent_fd = open_directory_path(path.parent)
        try:
            fd = os.open(
                safe_name(path.name),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except BaseException:
            os.close(parent_fd)
            raise
        try:
            identity = require_regular_fd(fd, os.fspath(path))
            if identity.mode != 0o444:
                raise BuildError(f"leased trust file mode is not 0444: {path}")
            if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
                raise BuildError(f"leased trust file FD is not O_RDONLY: {path}")
            digest, size = sha256_fd(fd)
            if (
                digest != exact_sha256(expected_sha256, f"lease:{path}")
                or size != identity.size_bytes
            ):
                raise BuildError(f"leased trust file initial SHA/size mismatch: {path}")
            lease = cls(
                path, digest, fd, identity, parent_fd, identity_fd(parent_fd)
            )
            lease.revalidate()
            return lease
        except BaseException:
            os.close(fd)
            os.close(parent_fd)
            raise

    def revalidate(self) -> None:
        if fcntl.fcntl(self.fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            raise BuildError(f"leased trust FD lost O_RDONLY: {self.path}")
        before = identity_fd(self.fd)
        if before != self.identity:
            raise BuildError(f"leased trust inode changed: {self.path}")
        digest, size = sha256_fd(self.fd)
        if digest != self.sha256 or size != self.identity.size_bytes:
            raise BuildError(f"leased trust bytes changed: {self.path}")
        if identity_fd(self.parent_fd) != self.parent_identity:
            raise BuildError(f"leased trust parent inode changed: {self.path.parent}")
        path_parent_fd = open_directory_path(self.path.parent)
        try:
            if identity_fd(path_parent_fd) != self.parent_identity:
                raise BuildError(
                    f"leased trust parent path no longer names held inode: {self.path.parent}"
                )
            named_fd = os.open(
                safe_name(self.path.name),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self.parent_fd,
            )
            try:
                if identity_fd(named_fd) != self.identity:
                    raise BuildError(
                        f"leased trust named path no longer names held inode: {self.path}"
                    )
            finally:
                os.close(named_fd)
        finally:
            os.close(path_parent_fd)

    def read_bytes(self) -> bytes:
        self.revalidate()
        data = read_fd_bytes(self.fd)
        if sha256_bytes(data) != self.sha256 or identity_fd(self.fd) != self.identity:
            raise BuildError(f"leased trust bytes changed during read: {self.path}")
        return data

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


@dataclass
class FrozenDirectoryLease:
    path: Path
    fd: int
    identity: Identity

    @classmethod
    def open(cls, path: Path) -> "FrozenDirectoryLease":
        fd = open_directory_path(path)
        try:
            lease = cls(path, fd, require_directory_fd(fd, os.fspath(path)))
            lease.revalidate()
            return lease
        except BaseException:
            os.close(fd)
            raise

    def revalidate(self) -> None:
        held = identity_fd(self.fd)
        if (
            held.device != self.identity.device
            or held.inode != self.identity.inode
            or held.mode != self.identity.mode
        ):
            raise BuildError(f"leased directory inode changed: {self.path}")
        named_fd = open_directory_path(self.path)
        try:
            named = identity_fd(named_fd)
            if (
                named.device != self.identity.device
                or named.inode != self.identity.inode
                or named.mode != self.identity.mode
            ):
                raise BuildError(
                    f"leased directory path no longer names held inode: {self.path}"
                )
        finally:
            os.close(named_fd)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class ProductionTrustLease:
    """Hold every named authorization/package/audit anchor through terminal."""

    def __init__(
        self,
        leases: list[FrozenFileLease],
        directory_leases: list[FrozenDirectoryLease],
    ) -> None:
        self.leases = leases
        self.directory_leases = directory_leases
        self.files_by_path = {lease.path: lease for lease in leases}
        self.directories_by_path = {
            lease.path: lease for lease in directory_leases
        }

    @classmethod
    def open(
        cls,
        auth: Mapping[str, Any],
        authorization_path: Path,
        authorization_sha256: str,
        authorization_lease: FrozenFileLease | None = None,
    ) -> "ProductionTrustLease":
        bindings: dict[Path, str] = {
            authorization_path: authorization_sha256,
        }

        def add(path_value: Any, sha_value: Any) -> None:
            path = Path(exact_string(path_value, "trust lease path"))
            digest = exact_sha256(sha_value, f"trust lease SHA:{path}")
            prior = bindings.get(path)
            if prior is not None and prior != digest:
                raise BuildError(f"conflicting trust lease SHA for path: {path}")
            bindings[path] = digest

        launch = auth["trusted_launch"]
        add(
            launch["outer_launch_receipt_path"],
            launch["outer_launch_receipt_sha256"],
        )
        for stem in (
            "root_launch_authorization", "preflight_package_manifest",
            "preflight_package_index", "preflight_independent_audit_receipt",
            "preflight_independent_audit_index",
        ):
            add(launch[f"{stem}_path"], launch[f"{stem}_sha256"])
        for binding_name in (
            "v10_package", "v10_builder_independent_audit",
            "v9_builder_negative_independent_audit",
            "v8_builder_negative_independent_audit",
            "v7_builder_negative_independent_audit",
        ):
            binding = auth["bindings"][binding_name]
            for key, value in binding.items():
                if key.endswith("_path"):
                    stem = key[:-5]
                    add(value, binding[f"{stem}_sha256"])
        leases: list[FrozenFileLease] = []
        directory_leases: list[FrozenDirectoryLease] = []
        try:
            for path, digest in sorted(
                bindings.items(), key=lambda item: os.fspath(item[0])
            ):
                if authorization_lease is not None and path == authorization_path:
                    if (
                        authorization_lease.sha256 != digest
                        or authorization_lease.path != path
                    ):
                        raise BuildError("held authorization lease binding mismatch")
                    authorization_lease.revalidate()
                    leases.append(authorization_lease)
                else:
                    leases.append(FrozenFileLease.open(path, digest))
            lease_by_path = {lease.path: lease for lease in leases}
            for index_stem, member_parent in (
                (
                    "preflight_package_index",
                    Path(launch["preflight_package_manifest_path"]).parent,
                ),
                (
                    "preflight_independent_audit_index",
                    Path(launch[
                        "preflight_independent_audit_receipt_path"
                    ]).parent,
                ),
            ):
                index_path = Path(launch[f"{index_stem}_path"])
                index_lease = lease_by_path.get(index_path)
                if index_lease is None:
                    raise BuildError(f"missing initial held index lease: {index_path}")
                for name, digest in parse_sha_index(
                    index_lease.read_bytes()
                ).items():
                    member_path = member_parent / name
                    prior = lease_by_path.get(member_path)
                    if prior is not None:
                        if prior.sha256 != digest:
                            raise BuildError(
                                f"held closure member SHA conflict: {member_path}"
                            )
                        continue
                    member_lease = FrozenFileLease.open(member_path, digest)
                    leases.append(member_lease)
                    lease_by_path[member_path] = member_lease
            directory_paths = {
                Path(auth["source_bundle"]["path"]),
                Path(auth["source_site_packages"]),
                *(lease.path.parent for lease in leases),
                *(
                    Path(auth["bindings"][name]["directory"])
                    for name in (
                        "v10_package", "v10_builder_independent_audit",
                        "v9_builder_negative_independent_audit",
                        "v8_builder_negative_independent_audit",
                        "v7_builder_negative_independent_audit",
                    )
                ),
            }
            for path in sorted(directory_paths, key=os.fspath):
                directory_leases.append(FrozenDirectoryLease.open(path))
            result = cls(leases, directory_leases)
            result.revalidate(auth)
            return result
        except BaseException:
            for lease in leases:
                lease.close()
            for lease in directory_leases:
                lease.close()
            raise

    def _file(self, path_value: Any, sha_value: Any) -> FrozenFileLease:
        path = Path(exact_string(path_value, "leased trust lookup path"))
        lease = self.files_by_path.get(path)
        if lease is None or lease.sha256 != exact_sha256(
            sha_value, f"leased trust lookup SHA:{path}"
        ):
            raise BuildError(f"trust lease does not contain exact file: {path}")
        lease.revalidate()
        return lease

    def directory_identity(self, path_value: Any) -> Identity:
        path = Path(exact_string(path_value, "leased directory lookup path"))
        lease = self.directories_by_path.get(path)
        if lease is None:
            raise BuildError(f"trust lease does not contain directory: {path}")
        lease.revalidate()
        return lease.identity

    def dup_directory(self, path_value: Any) -> int:
        path = Path(exact_string(path_value, "leased directory dup path"))
        lease = self.directories_by_path.get(path)
        if lease is None:
            raise BuildError(f"trust lease does not contain directory: {path}")
        lease.revalidate()
        return os.dup(lease.fd)

    def _json(
        self, path_value: Any, sha_value: Any, label: str,
        *, require_canonical_bytes: bool = True,
    ) -> dict[str, Any]:
        lease = self._file(path_value, sha_value)
        data = lease.read_bytes()
        value = strict_json_loads(data)
        if type(value) is not dict:
            raise BuildError(f"{label} held bytes are not a strict JSON object")
        if require_canonical_bytes and canonical_json_bytes(value) != data:
            raise BuildError(f"{label} held bytes are not canonical JSON object")
        return value

    def _validate_binding_files(
        self, binding: Mapping[str, Any], keys: frozenset[str], label: str
    ) -> None:
        validate_bound_files(binding, keys, label, verify_bytes=False)
        for key in keys:
            if key.endswith("_path") and f"{key[:-5]}_sha256" in keys:
                self._file(binding[key], binding[f"{key[:-5]}_sha256"])

    def revalidate(self, auth: Mapping[str, Any]) -> None:
        for lease in self.directory_leases:
            lease.revalidate()
        for lease in self.leases:
            lease.revalidate()
        launch = auth["trusted_launch"]
        root_auth = self._json(
            launch["root_launch_authorization_path"],
            launch["root_launch_authorization_sha256"],
            "leased root launch authorization",
        )
        validate_root_launch_authorization_semantics(root_auth, auth=auth)
        package_directory = Path(
            launch["preflight_package_manifest_path"]
        ).parent
        audit_directory = Path(
            launch["preflight_independent_audit_receipt_path"]
        ).parent
        package_index = parse_sha_index(self._file(
            launch["preflight_package_index_path"],
            launch["preflight_package_index_sha256"],
        ).read_bytes())
        audit_index = parse_sha_index(self._file(
            launch["preflight_independent_audit_index_path"],
            launch["preflight_independent_audit_index_sha256"],
        ).read_bytes())
        if (
            set(package_index) != PREFLIGHT_V2_PREPARED_INDEX_NAMES
            or set(audit_index) != PREFLIGHT_V2_QA_INDEX_NAMES
        ):
            raise BuildError("leased preflight v2 exact index closure mismatch")
        package_dir_lease = self.directories_by_path.get(package_directory)
        audit_dir_lease = self.directories_by_path.get(audit_directory)
        if package_dir_lease is None or audit_dir_lease is None:
            raise BuildError("leased preflight v2 closure directory is absent")
        for lease, names, label in (
            (
                package_dir_lease, PREFLIGHT_V2_PREPARED_TOP_NAMES,
                "leased preflight v2 prepared package",
            ),
            (
                audit_dir_lease, PREFLIGHT_V2_QA_TOP_NAMES,
                "leased preflight v2 QA package",
            ),
        ):
            lease.revalidate()
            if lease.identity.mode != 0o555:
                raise BuildError(f"{label} directory mode is not exact 0555")
            if set(fresh_directory_names(lease.fd)) != names:
                raise BuildError(f"{label} top-level closure changed")
        package_members = {
            name: self._file(os.fspath(package_directory / name), digest).read_bytes()
            for name, digest in package_index.items()
        }
        audit_members = {
            name: self._file(os.fspath(audit_directory / name), digest).read_bytes()
            for name, digest in audit_index.items()
        }
        if package_index["BUNDLE_MANIFEST.json"] != launch[
            "preflight_package_manifest_sha256"
        ]:
            raise BuildError("leased preflight v2 manifest anchor mismatch")
        if audit_index["INDEPENDENT_QA_RECEIPT.json"] != launch[
            "preflight_independent_audit_receipt_sha256"
        ]:
            raise BuildError("leased preflight v2 QA receipt anchor mismatch")
        validate_preflight_v2_dynamic_closure_semantics(
            package_directory=package_directory,
            package_index_sha256=launch["preflight_package_index_sha256"],
            package_index=package_index,
            package_members=package_members,
            audit_directory=audit_directory,
            audit_index=audit_index,
            audit_members=audit_members,
        )
        package = auth["bindings"]["v10_package"]
        audit = auth["bindings"]["v10_builder_independent_audit"]
        self._validate_binding_files(
            package, V10_PACKAGE_BINDING_KEYS, "leased_v10_package"
        )
        self._validate_binding_files(
            audit, V10_AUDIT_BINDING_KEYS, "leased_v10_independent_audit"
        )
        audit_receipt = self._json(
            audit["receipt_path"], audit["receipt_sha256"],
            "leased v10 independent audit receipt",
            require_canonical_bytes=False,
        )
        validate_v10_audit_receipt_semantics(
            audit_receipt, audit_binding=audit, package_binding=package
        )
        v9_negative = auth["bindings"][
            "v9_builder_negative_independent_audit"
        ]
        self._validate_binding_files(
            v9_negative, V9_NEGATIVE_QA_BINDING_KEYS,
            "leased_v9_negative_independent_audit",
        )
        validate_v9_negative_audit_receipt_semantics(
            self._json(
                v9_negative["receipt_path"], v9_negative["receipt_sha256"],
                "leased v9 negative independent audit receipt",
            ),
            audit_binding=v9_negative,
        )
        validate_v9_negative_audit_manifest_semantics(
            self._json(
                v9_negative["bundle_manifest_path"],
                v9_negative["bundle_manifest_sha256"],
                "leased v9 negative independent audit manifest",
            ),
            audit_binding=v9_negative,
        )
        v9_negative_index = parse_sha_index(self._file(
            v9_negative["sha256_index_path"],
            v9_negative["sha256_index_sha256"],
        ).read_bytes())
        if v9_negative_index != expected_v9_negative_audit_index(v9_negative):
            raise BuildError(
                "leased v9 negative independent audit closure mismatch"
            )
        v9_negative_directory = Path(v9_negative["directory"])
        v9_negative_dir_lease = self.directories_by_path.get(
            v9_negative_directory
        )
        if (
            v9_negative_dir_lease is None
            or v9_negative_dir_lease.identity.mode != 0o555
            or set(fresh_directory_names(v9_negative_dir_lease.fd))
            != {
                item["filename"]
                for item in V9_NEGATIVE_QA_FILE_BINDINGS.values()
            }
        ):
            raise BuildError(
                "leased v9 negative independent audit directory mismatch"
            )
        v8_negative = auth["bindings"][
            "v8_builder_negative_independent_audit"
        ]
        self._validate_binding_files(
            v8_negative, V8_NEGATIVE_QA_BINDING_KEYS,
            "leased_v8_negative_independent_audit",
        )
        validate_v8_negative_audit_receipt_semantics(
            self._json(
                v8_negative["receipt_path"], v8_negative["receipt_sha256"],
                "leased v8 negative independent audit receipt",
            ),
            audit_binding=v8_negative,
        )
        validate_v8_negative_audit_manifest_semantics(
            self._json(
                v8_negative["bundle_manifest_path"],
                v8_negative["bundle_manifest_sha256"],
                "leased v8 negative independent audit manifest",
            ),
            audit_binding=v8_negative,
        )
        v8_negative_index = parse_sha_index(self._file(
            v8_negative["sha256_index_path"],
            v8_negative["sha256_index_sha256"],
        ).read_bytes())
        if v8_negative_index != expected_v8_negative_audit_index(v8_negative):
            raise BuildError(
                "leased v8 negative independent audit closure mismatch"
            )
        v8_negative_directory = Path(v8_negative["directory"])
        v8_negative_dir_lease = self.directories_by_path.get(
            v8_negative_directory
        )
        if (
            v8_negative_dir_lease is None
            or v8_negative_dir_lease.identity.mode != 0o555
            or set(fresh_directory_names(v8_negative_dir_lease.fd))
            != {
                item["filename"]
                for item in V8_NEGATIVE_QA_FILE_BINDINGS.values()
            }
        ):
            raise BuildError(
                "leased v8 negative independent audit directory mismatch"
            )
        v7_negative = auth["bindings"]["v7_builder_negative_independent_audit"]
        self._validate_binding_files(
            v7_negative, V7_NEGATIVE_QA_BINDING_KEYS,
            "leased_v7_negative_independent_audit",
        )
        validate_v7_negative_audit_receipt_semantics(
            self._json(
                v7_negative["receipt_path"], v7_negative["receipt_sha256"],
                "leased v7 negative independent audit receipt",
                require_canonical_bytes=False,
            ),
            audit_binding=v7_negative,
        )
        negative_index = parse_sha_index(self._file(
            v7_negative["sha256_index_path"],
            v7_negative["sha256_index_sha256"],
        ).read_bytes())
        expected_negative_index = {
            "BUNDLE_MANIFEST.json": v7_negative["bundle_manifest_sha256"],
            "COMMAND_LOG.txt": v7_negative["log_sha256"],
            "INDEPENDENT_QA_OUTPUT.json": v7_negative["output_sha256"],
            "INDEPENDENT_QA_REPORT_CN.md": v7_negative["report_sha256"],
            "PACKAGE_CLOSURE_QA.json": v7_negative["closure_sha256"],
            "QA_HARNESS_OR_METHOD.md": v7_negative["harness_sha256"],
        }
        if negative_index != expected_negative_index:
            raise BuildError("leased v7 negative independent audit closure mismatch")

    def close(self) -> None:
        for lease in reversed(self.leases):
            lease.close()
        self.leases.clear()
        for lease in reversed(self.directory_leases):
            lease.close()
        self.directory_leases.clear()
        self.files_by_path.clear()
        self.directories_by_path.clear()


def open_directory_at(root_fd: int, name: str) -> int:
    return os.open(safe_name(name), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                   getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)


def open_regular_at(root_fd: int, relative: str) -> int:
    parts = PurePosixPath(safe_relative(relative)).parts
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                            getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
            os.close(current)
            current = child
        result = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                         dir_fd=current)
        require_regular_fd(result, relative)
        return result
    finally:
        os.close(current)


def mkdir_open_at(root_fd: int, name: str, mode: int = 0o700) -> int:
    name = safe_name(name)
    os.mkdir(name, mode, dir_fd=root_fd)
    os.fsync(root_fd)
    return open_directory_at(root_fd, name)


def ensure_parent_at(root_fd: int, relative: str) -> tuple[int, str]:
    parts = PurePosixPath(safe_relative(relative)).parts
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try:
                child = open_directory_at(current, part)
            except FileNotFoundError:
                child = mkdir_open_at(current, part)
            os.close(current)
            current = child
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def write_bytes_at_exclusive(root_fd: int, name: str, data: bytes,
                             mode: int = 0o444,
                             before_write: Callable[[], None] | None = None) -> Identity:
    if before_write is not None:
        before_write()
    fd = os.open(safe_name(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=root_fd)
    created = require_regular_fd(fd, name)
    completed = False
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fchmod(fd, mode)
        os.fsync(fd)
        result = require_regular_fd(fd, name)
        completed = True
    finally:
        os.close(fd)
        if not completed:
            try:
                current = Identity.from_stat(os.stat(
                    safe_name(name), dir_fd=root_fd, follow_symlinks=False
                ))
                if current.device == created.device and current.inode == created.inode:
                    os.unlink(safe_name(name), dir_fd=root_fd)
                    os.fsync(root_fd)
            except (FileNotFoundError, OSError):
                pass
    os.fsync(root_fd)
    return result


def write_json_at_exclusive(root_fd: int, name: str, value: Mapping[str, Any],
                            before_write: Callable[[], None] | None = None) -> tuple[str, Identity]:
    data = canonical_json_bytes(value)
    return sha256_bytes(data), write_bytes_at_exclusive(root_fd, name, data, before_write=before_write)


def read_json_at(root_fd: int, name: str) -> tuple[dict[str, Any], str, Identity]:
    fd = os.open(safe_name(name), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
    try:
        identity = require_regular_fd(fd, name)
        if identity.mode != 0o444:
            raise BuildError(f"journal JSON mode is not exact 0444: {name}")
        data = read_fd_bytes(fd)
        if identity_fd(fd) != identity:
            raise BuildError(f"journal file changed during read: {name}")
    finally:
        os.close(fd)
    value = strict_json_loads(data)
    if not isinstance(value, dict):
        raise BuildError(f"journal JSON is not an object: {name}")
    if canonical_json_bytes(value) != data:
        raise BuildError(f"journal JSON bytes are not canonical: {name}")
    return value, sha256_bytes(data), identity


def read_canonical_terminal_at(
    journal_fd: int, name: str = "TERMINAL.json"
) -> tuple[dict[str, Any], str, Identity, bytes]:
    """Read a completely published canonical terminal through one nofollow FD.

    A terminal is valid only when it is a single-link 0444 regular file whose
    exact bytes are the canonical serialization of the strict JSON object.
    This deliberately rejects legacy/externally injected partial files rather
    than attempting to overwrite a canonical pathname.
    """
    name = safe_name(name)
    fd = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=journal_fd,
    )
    try:
        before = require_regular_fd(fd, name)
        if before.mode != 0o444 or before.nlink != 1:
            raise BuildError("canonical terminal must be mode0444 and nlink1")
        data = read_fd_bytes(fd, TERMINAL_READ_LIMIT_BYTES)
        after = identity_fd(fd)
        if before != after:
            raise BuildError("canonical terminal changed during held-FD read")
        path_after = Identity.from_stat(os.stat(
            name, dir_fd=journal_fd, follow_symlinks=False
        ))
        if path_after != before:
            raise BuildError("canonical terminal path differs from held FD")
    finally:
        os.close(fd)
    value = strict_json_loads(data)
    if not isinstance(value, dict):
        raise BuildError("canonical terminal JSON is not an object")
    if canonical_json_bytes(value) != data:
        raise BuildError("canonical terminal bytes are not exact canonical JSON")
    return value, sha256_bytes(data), before, data


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise BuildError("terminal write made no forward progress")
        offset += written


def _write_terminal_payload_before_visibility(
    fd: int,
    data: bytes,
    *,
    mid_write_hook: Callable[[], None] | None,
) -> None:
    split = max(1, len(data) // 2) if data else 0
    _write_all(fd, data[:split])
    if mid_write_hook is not None:
        mid_write_hook()
    _write_all(fd, data[split:])


def _linux_fstatfs_magic(fd: int) -> int:
    if not sys.platform.startswith("linux"):
        raise BuildError("terminal O_TMPFILE publication requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        fstatfs = libc.fstatfs
    except AttributeError as exc:
        raise BuildError("libc fstatfs is unavailable") from exc
    buffer = ctypes.create_string_buffer(512)
    fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    fstatfs.restype = ctypes.c_int
    ctypes.set_errno(0)
    if fstatfs(fd, ctypes.byref(buffer)) != 0:
        err = ctypes.get_errno()
        raise BuildError(f"fstatfs failed: errno={err} {os.strerror(err)}")
    return int(ctypes.c_long.from_buffer(buffer).value)


def _same_inode_bytes_identity(left: Identity, right: Identity) -> bool:
    return (
        left.device == right.device
        and left.inode == right.inode
        and left.size_bytes == right.size_bytes
        and left.mtime_ns == right.mtime_ns
        and left.mode == right.mode
    )


def _verified_proc_self_fd_path(
    source_fd: int, *, expected_nlink: int
) -> tuple[str, Identity]:
    """Bind a kernel-owned /proc/self/fd reference to the held source inode."""
    if not sys.platform.startswith("linux"):
        raise BuildError("/proc/self/fd terminal publication requires Linux")
    if type(source_fd) is not int or source_fd < 0:
        raise BuildError("source fd is invalid")
    held_before = Identity.from_stat(os.fstat(source_fd))
    if held_before.nlink != expected_nlink:
        raise BuildError("held terminal inode link count differs from expectation")
    proc_fd = os.open(
        PROC_SELF_FD_PATH,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    reopened_fd = -1
    try:
        if _linux_fstatfs_magic(proc_fd) != PROC_SUPER_MAGIC:
            raise BuildError("/proc/self/fd is not on procfs")
        member = str(source_fd)
        link_info = os.stat(member, dir_fd=proc_fd, follow_symlinks=False)
        if not stat.S_ISLNK(link_info.st_mode):
            raise BuildError("/proc/self/fd member is not the kernel fd symlink")
        referenced = Identity.from_stat(
            os.stat(member, dir_fd=proc_fd, follow_symlinks=True)
        )
        reopened_fd = os.open(
            member,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            dir_fd=proc_fd,
        )
        reopened = Identity.from_stat(os.fstat(reopened_fd))
        absolute_path = f"{PROC_SELF_FD_PATH}/{source_fd}"
        absolute = Identity.from_stat(os.stat(absolute_path, follow_symlinks=True))
        held_after = Identity.from_stat(os.fstat(source_fd))
        if not (
            held_before == held_after == referenced == reopened == absolute
            and referenced.nlink == expected_nlink
        ):
            raise BuildError("/proc/self/fd reference does not pin the held inode")
        return absolute_path, held_after
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise BuildError(f"verified /proc/self/fd reference unavailable: {exc}") from exc
    finally:
        if reopened_fd >= 0:
            os.close(reopened_fd)
        os.close(proc_fd)


def _linux_linkat_proc_self_fd_follow(
    source_fd: int, destination_dir_fd: int, destination_name: str
) -> None:
    """No-clobber link of a held O_TMPFILE for an unprivileged Linux user."""
    if not sys.platform.startswith("linux"):
        raise BuildError("linkat(/proc/self/fd, AT_SYMLINK_FOLLOW) requires Linux")
    destination_name = safe_name(destination_name)
    source_path, source_before = _verified_proc_self_fd_path(
        source_fd, expected_nlink=0
    )
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        linkat = libc.linkat
    except AttributeError as exc:
        raise BuildError("libc linkat is unavailable") from exc
    linkat.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = linkat(
        AT_FDCWD,
        ctypes.c_char_p(os.fsencode(source_path)),
        destination_dir_fd,
        ctypes.c_char_p(os.fsencode(destination_name)),
        AT_SYMLINK_FOLLOW,
    )
    if result != 0:
        err = ctypes.get_errno()
        raise BuildError(
            "linkat(/proc/self/fd/<held>, AT_SYMLINK_FOLLOW) no-clobber "
            "terminal publication failed: "
            f"errno={err} {os.strerror(err)}"
        )
    source_after = Identity.from_stat(os.fstat(source_fd))
    _, proc_after = _verified_proc_self_fd_path(source_fd, expected_nlink=1)
    if not (
        _same_inode_bytes_identity(source_before, source_after)
        and source_after == proc_after
        and source_after.nlink == 1
    ):
        raise BuildError("linked /proc/self/fd terminal inode identity changed")


def precheck_terminal_publication_linux_xfs(journal_fd: int) -> None:
    """Fail before ROOT construction when Linux/XFS O_TMPFILE is unavailable.

    A separately authorized MARS compatibility preflight must additionally
    exercise the real link step using ``probe_terminal_publication_linux_xfs``.
    Production has no named-temp or pathname-write fallback.
    """
    if _linux_fstatfs_magic(journal_fd) != XFS_SUPER_MAGIC:
        raise BuildError("production terminal publication requires XFS")
    flags = (
        os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_TMPFILE", 0)
    )
    if not getattr(os, "O_TMPFILE", 0):
        raise BuildError("Python/OS does not expose O_TMPFILE")
    # Resolve the exact symbol and kernel-owned procfs FD reference before any
    # staging or fixed-ROOT mutation.
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "linkat"):
        raise BuildError("libc linkat is unavailable")
    try:
        fd = os.open(".", flags, 0o600, dir_fd=journal_fd)
    except OSError as exc:
        raise BuildError(f"XFS O_TMPFILE precheck failed: {exc}") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 0
            or info.st_dev != os.fstat(journal_fd).st_dev
        ):
            raise BuildError("O_TMPFILE precheck inode identity mismatch")
        _verified_proc_self_fd_path(fd, expected_nlink=0)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_terminal_linux_otmpfile_noreplace(
    journal_fd: int,
    name: str,
    data: bytes,
    *,
    mid_write_hook: Callable[[], None] | None = None,
    after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Publish complete bytes via anonymous XFS inode + held /proc FD link.

    Before ``linkat`` the inode has no pathname and nlink==0.  Thus an
    interruption while writing/fchmod/fsync cannot leave a partial canonical
    TERMINAL.  ``linkat`` is no-clobber by definition and there is deliberately
    no pathname fallback.
    """
    name = safe_name(name)
    if file_exists_at(journal_fd, name):
        raise BuildError("canonical terminal already exists")
    precheck_terminal_publication_linux_xfs(journal_fd)
    flags = os.O_RDWR | os.O_TMPFILE | getattr(os, "O_CLOEXEC", 0)
    try:
        tmp_fd = os.open(".", flags, 0o600, dir_fd=journal_fd)
    except OSError as exc:
        raise BuildError(f"production O_TMPFILE open failed: {exc}") from exc
    try:
        initial = os.fstat(tmp_fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 0
            or initial.st_dev != os.fstat(journal_fd).st_dev
        ):
            raise BuildError("anonymous terminal inode identity mismatch")
        _write_terminal_payload_before_visibility(
            tmp_fd, data, mid_write_hook=mid_write_hook
        )
        if file_exists_at(journal_fd, name):
            raise BuildError("canonical terminal appeared before atomic link")
        os.fchmod(tmp_fd, 0o444)
        os.fsync(tmp_fd)
        prepared = Identity.from_stat(os.fstat(tmp_fd))
        prepared_sha, prepared_size = sha256_fd(tmp_fd)
        if (
            prepared.mode != 0o444
            or prepared.nlink != 0
            or prepared_size != len(data)
            or prepared_sha != sha256_bytes(data)
        ):
            raise BuildError("anonymous terminal is not exact complete frozen bytes")
        _linux_linkat_proc_self_fd_follow(tmp_fd, journal_fd, name)
        published = Identity.from_stat(os.fstat(tmp_fd))
        if (
            published.device != prepared.device
            or published.inode != prepared.inode
            or published.mode != 0o444
            or published.nlink != 1
        ):
            raise BuildError("linked terminal inode identity/mode/nlink mismatch")
        _, observed_sha, observed_identity, observed_bytes = (
            read_canonical_terminal_at(journal_fd, name)
        )
        if (
            observed_identity != published
            or observed_sha != prepared_sha
            or observed_bytes != data
        ):
            raise BuildError("linked canonical terminal bytes differ from held inode")
        if after_link_before_dir_fsync_hook is not None:
            after_link_before_dir_fsync_hook()
        os.fsync(journal_fd)
        _, final_sha, final_identity, final_bytes = read_canonical_terminal_at(
            journal_fd, name
        )
        if (
            final_identity != published
            or final_sha != prepared_sha
            or final_bytes != data
        ):
            raise BuildError("canonical terminal changed after directory fsync")
        return {
            "method": PRODUCTION_TERMINAL_PUBLICATION_METHOD,
            "canonical_visibility_rule": TERMINAL_CANONICAL_VISIBILITY_RULE,
            "sha256": prepared_sha,
            "identity": final_identity.json(),
        }
    finally:
        # Closing an unlinked O_TMPFILE destroys it.  Once linked, a fully
        # written/fsynced 0444 canonical inode intentionally remains even when
        # the post-link hook simulates interruption before directory fsync.
        os.close(tmp_fd)


def probe_terminal_publication_linux_xfs(
    probe_dir_fd: int, probe_name: str
) -> dict[str, Any]:
    """Destructive-in-scratch capability probe for a separately signed preflight.

    The caller must provide an authorized empty scratch directory and unique
    no-clobber basename.  Success proves the same O_TMPFILE/linkat/fsync path,
    after which this helper removes only the exact probe inode and fsyncs the
    scratch directory.  The production builder never invokes this mutating
    probe implicitly.
    """
    payload = canonical_json_bytes({
        "schema": "result_free_terminal_publication_linux_xfs_preflight_v10",
        "status": "PASS_CAPABILITY_PROBE_ONLY_NO_BUILD_NO_SMOKE",
    })
    evidence = publish_terminal_linux_otmpfile_noreplace(
        probe_dir_fd, probe_name, payload
    )
    expected = Identity.from_json(evidence["identity"])
    current = Identity.from_stat(os.stat(
        safe_name(probe_name), dir_fd=probe_dir_fd, follow_symlinks=False
    ))
    if current != expected:
        raise BuildError("terminal publication probe identity changed")
    os.unlink(safe_name(probe_name), dir_fd=probe_dir_fd)
    os.fsync(probe_dir_fd)
    if file_exists_at(probe_dir_fd, probe_name):
        raise BuildError("terminal publication probe cleanup failed")
    return evidence


def publish_terminal_via_injected_complete_rename(
    journal_fd: int,
    name: str,
    data: bytes,
    *,
    rename_impl: Callable[[int, str, int, str], None],
    mid_write_hook: Callable[[], None] | None = None,
    after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Nonproduction Darwin/synthetic equivalent with a named staging file.

    This exists only for local tests.  It never serves as a production fallback
    and is explicitly labelled NOT_RUN_NON_LINUX/SYNTHETIC in receipts.
    """
    name = safe_name(name)
    if file_exists_at(journal_fd, name):
        raise BuildError("canonical terminal already exists")
    temporary = safe_name(
        f".TERMINAL.v10.complete.{os.getpid()}.{os.urandom(12).hex()}"
    )
    fd = os.open(
        temporary,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=journal_fd,
    )
    created = require_regular_fd(fd, temporary)
    published = False
    try:
        _write_terminal_payload_before_visibility(
            fd, data, mid_write_hook=mid_write_hook
        )
        if file_exists_at(journal_fd, name):
            raise BuildError("canonical terminal appeared before synthetic rename")
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        prepared = identity_fd(fd)
        prepared_sha, prepared_size = sha256_fd(fd)
        if (
            prepared.mode != 0o444
            or prepared.nlink != 1
            or prepared_size != len(data)
            or prepared_sha != sha256_bytes(data)
        ):
            raise BuildError("synthetic terminal staging bytes are not complete")
        rename_impl(journal_fd, temporary, journal_fd, name)
        published = True
        _, observed_sha, observed_identity, observed_bytes = (
            read_canonical_terminal_at(journal_fd, name)
        )
        if (
            observed_identity.device != prepared.device
            or observed_identity.inode != prepared.inode
            or observed_sha != prepared_sha
            or observed_bytes != data
        ):
            raise BuildError("synthetic canonical terminal differs after rename")
        if after_link_before_dir_fsync_hook is not None:
            after_link_before_dir_fsync_hook()
        os.fsync(journal_fd)
        _, final_sha, final_identity, final_bytes = read_canonical_terminal_at(
            journal_fd, name
        )
        if (
            final_identity != observed_identity
            or final_sha != prepared_sha
            or final_bytes != data
        ):
            raise BuildError("synthetic terminal changed after directory fsync")
        return {
            "method": SYNTHETIC_TERMINAL_PUBLICATION_METHOD,
            "canonical_visibility_rule": TERMINAL_CANONICAL_VISIBILITY_RULE,
            "sha256": final_sha,
            "identity": final_identity.json(),
        }
    finally:
        os.close(fd)
        if not published:
            try:
                current = Identity.from_stat(os.stat(
                    temporary, dir_fd=journal_fd, follow_symlinks=False
                ))
                if current.device == created.device and current.inode == created.inode:
                    os.unlink(temporary, dir_fd=journal_fd)
                    os.fsync(journal_fd)
            except (FileNotFoundError, OSError):
                pass


def read_authorization_single_open(path: Path, expected_sha256: str,
                                   after_read_hook: Callable[[], None] | None = None
                                   ) -> tuple[dict[str, Any], Identity]:
    if not SHA_RE.fullmatch(expected_sha256):
        raise BuildError("authorization SHA is invalid")
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BuildError("authorization is not a nofollow single-link regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = require_regular_fd(fd, "authorization")
        if opened.mode != 0o444:
            raise BuildError("authorization mode must be exact 0444")
        if opened != Identity.from_stat(before):
            raise BuildError("authorization changed before open")
        data = read_fd_bytes(fd)
        if after_read_hook is not None:
            after_read_hook()
        after_fd = identity_fd(fd)
    finally:
        os.close(fd)
    after_path = path.lstat()
    if opened != after_fd or opened != Identity.from_stat(after_path):
        raise BuildError("authorization path changed during single-open hash/parse")
    if sha256_bytes(data) != expected_sha256:
        raise BuildError("authorization SHA mismatch")
    value = strict_json_loads(data)
    if not isinstance(value, dict):
        raise BuildError("authorization root is not an object")
    if canonical_json_bytes(value) != data:
        raise BuildError("authorization bytes are not canonical JSON")
    return value, opened


def read_frozen_regular_bytes_single_open(
    path: Path, expected_sha256: str, label: str
) -> tuple[bytes, Identity]:
    if not path.is_absolute():
        raise BuildError(f"{label} path must be absolute")
    fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = require_regular_fd(fd, label)
        if opened.mode != 0o444:
            raise BuildError(f"{label} mode must be exact 0444")
        data = read_fd_bytes(fd)
        if identity_fd(fd) != opened:
            raise BuildError(f"{label} changed during held read")
    finally:
        os.close(fd)
    if Identity.from_stat(path.lstat()) != opened:
        raise BuildError(f"{label} named path differs from held FD")
    if sha256_bytes(data) != exact_sha256(expected_sha256, f"{label}.sha256"):
        raise BuildError(f"{label} SHA mismatch")
    return data, opened


def read_frozen_json_single_open(
    path: Path, expected_sha256: str, label: str,
    *, require_canonical_bytes: bool = False,
) -> tuple[dict[str, Any], Identity]:
    data, identity = read_frozen_regular_bytes_single_open(
        path, expected_sha256, label
    )
    value = strict_json_loads(data)
    if type(value) is not dict:
        raise BuildError(f"{label} is not a strict JSON object")
    if require_canonical_bytes and canonical_json_bytes(value) != data:
        raise BuildError(f"{label} bytes are not canonical JSON")
    return value, identity


def validate_dynamic_preflight_anchor_files(auth: Mapping[str, Any]) -> None:
    launch = auth["trusted_launch"]
    root_auth, _ = read_authorization_single_open(
        Path(launch["root_launch_authorization_path"]),
        launch["root_launch_authorization_sha256"],
    )
    validate_root_launch_authorization_semantics(root_auth, auth=auth)
    package_manifest_path = Path(launch["preflight_package_manifest_path"])
    package_index_path = Path(launch["preflight_package_index_path"])
    audit_receipt_path = Path(
        launch["preflight_independent_audit_receipt_path"]
    )
    audit_index_path = Path(launch["preflight_independent_audit_index_path"])
    if package_manifest_path.name != "BUNDLE_MANIFEST.json":
        raise BuildError("preflight v2 package manifest canonical name mismatch")
    if audit_receipt_path.name != "INDEPENDENT_QA_RECEIPT.json":
        raise BuildError("preflight v2 QA receipt canonical name mismatch")
    package_directory = package_manifest_path.parent
    audit_directory = audit_receipt_path.parent
    if package_directory == audit_directory:
        raise BuildError("preflight v2 prepared and QA directories must be distinct")
    package_index, package_members = read_exact_frozen_index_closure(
        package_directory,
        index_path=package_index_path,
        index_sha256=launch["preflight_package_index_sha256"],
        expected_top_names=PREFLIGHT_V2_PREPARED_TOP_NAMES,
        expected_index_names=PREFLIGHT_V2_PREPARED_INDEX_NAMES,
        label="preflight v2 prepared package",
    )
    audit_index, audit_members = read_exact_frozen_index_closure(
        audit_directory,
        index_path=audit_index_path,
        index_sha256=launch["preflight_independent_audit_index_sha256"],
        expected_top_names=PREFLIGHT_V2_QA_TOP_NAMES,
        expected_index_names=PREFLIGHT_V2_QA_INDEX_NAMES,
        label="preflight v2 independent QA package",
    )
    if package_index["BUNDLE_MANIFEST.json"] != launch[
        "preflight_package_manifest_sha256"
    ]:
        raise BuildError("preflight v2 package manifest signed-anchor mismatch")
    if audit_index["INDEPENDENT_QA_RECEIPT.json"] != launch[
        "preflight_independent_audit_receipt_sha256"
    ]:
        raise BuildError("preflight v2 QA receipt signed-anchor mismatch")
    validate_preflight_v2_dynamic_closure_semantics(
        package_directory=package_directory,
        package_index_sha256=launch["preflight_package_index_sha256"],
        package_index=package_index,
        package_members=package_members,
        audit_directory=audit_directory,
        audit_index=audit_index,
        audit_members=audit_members,
    )


def parse_sha_index(data: bytes) -> dict[str, str]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BuildError("SHA index is not UTF-8") from exc
    result: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]):
            raise BuildError(f"invalid SHA index line {number}")
        name = safe_name(parts[1])
        if name == "SHA256SUMS" or name in result:
            raise BuildError(f"duplicate/recursive SHA member: {name}")
        result[name] = parts[0]
    return result


def strict_canonical_json_object(data: bytes, label: str) -> dict[str, Any]:
    value = strict_json_loads(data)
    if type(value) is not dict:
        raise BuildError(f"{label} is not a strict JSON object")
    if canonical_json_bytes(value) != data:
        raise BuildError(f"{label} bytes are not canonical JSON")
    return value


def validate_preflight_v2_manifest_file_records(
    records: Any,
    *,
    expected_names: frozenset[str],
    member_bytes: Mapping[str, bytes],
    index: Mapping[str, str],
    label: str,
) -> None:
    if type(records) is not list or len(records) != len(expected_names):
        raise BuildError(f"{label}.files exact count mismatch")
    observed_names: list[str] = []
    for position, record in enumerate(records):
        if type(record) is not dict or set(record) != PREFLIGHT_V2_MANIFEST_FILE_KEYS:
            raise BuildError(f"{label}.files[{position}] exact schema mismatch")
        name = safe_name(exact_string(
            record["relative_path"], f"{label}.files[{position}].relative_path"
        ))
        role = exact_string(record["role"], f"{label}.files[{position}].role")
        if not role or "\0" in role:
            raise BuildError(f"{label}.files[{position}].role is empty/unsafe")
        digest = exact_sha256(
            record["sha256"], f"{label}.files[{position}].sha256"
        )
        size = exact_integer(
            record["size_bytes"], f"{label}.files[{position}].size_bytes"
        )
        data = member_bytes.get(name)
        if (
            data is None
            or digest != index.get(name)
            or digest != sha256_bytes(data)
            or size != len(data)
        ):
            raise BuildError(f"{label}.files[{position}] byte/index binding mismatch")
        observed_names.append(name)
    if observed_names != sorted(expected_names):
        raise BuildError(f"{label}.files names/order mismatch")


def validate_preflight_v2_prepared_manifest_semantics(
    value: Any,
    *,
    member_bytes: Mapping[str, bytes],
    index: Mapping[str, str],
) -> None:
    label = "preflight v2 prepared manifest"
    if type(value) is not dict or set(value) != PREFLIGHT_V2_PREPARED_MANIFEST_KEYS:
        raise BuildError(f"{label} exact key set mismatch")
    if (
        value["schema"] != PREFLIGHT_V2_PREPARED_MANIFEST_SCHEMA
        or value["status"] != PREFLIGHT_V2_PREPARED_STATUS
    ):
        raise BuildError(f"{label} schema/status mismatch")
    created = exact_string(value["created_utc"], f"{label}.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError(f"{label} created_utc is invalid")
    if exact_integer(value["payload_file_count"], f"{label}.payload_file_count") != 7:
        raise BuildError(f"{label} payload count is not exact7")
    validate_preflight_v2_manifest_file_records(
        value["files"],
        expected_names=PREFLIGHT_V2_PREPARED_PAYLOAD_NAMES,
        member_bytes=member_bytes,
        index=index,
        label=label,
    )
    if value["closure_files_not_in_payload_manifest"] != sorted(
        PREFLIGHT_V2_PREPARED_CLOSURE_NAMES
    ):
        raise BuildError(f"{label} closure member list mismatch")
    if value["authority"] != PREFLIGHT_V2_ALL_FALSE_AUTHORITY:
        raise BuildError(f"{label} authority is not exact all-false")


def _validate_preflight_v2_locked_tool(
    item: Any,
    *,
    expected_name: str,
    expected_sha256: str,
    label: str,
) -> None:
    if type(item) is not dict or set(item) != {"path", "sha256", "line_count"}:
        raise BuildError(f"{label} exact schema mismatch")
    if (
        item["path"] != expected_name
        or exact_sha256(item["sha256"], f"{label}.sha256") != expected_sha256
        or exact_integer(item["line_count"], f"{label}.line_count") <= 0
    ):
        raise BuildError(f"{label} binding mismatch")


def _require_false_scope_fields(
    scope: Any, *, required_false: frozenset[str], label: str
) -> None:
    if type(scope) is not dict or not scope:
        raise BuildError(f"{label} must be a nonempty object")
    for name in required_false:
        if scope.get(name) is not False:
            raise BuildError(f"{label}.{name} must be exact false")


def validate_preflight_v2_prepared_receipt_semantics(
    value: Any,
    *,
    package_directory: str,
    member_bytes: Mapping[str, bytes],
    index: Mapping[str, str],
    index_sha256: str,
) -> None:
    label = "preflight v2 prepared receipt"
    if type(value) is not dict or set(value) != PREFLIGHT_V2_PREPARED_RECEIPT_KEYS:
        raise BuildError(f"{label} exact key set mismatch")
    if (
        value["schema"] != PREFLIGHT_V2_PREPARED_RECEIPT_SCHEMA
        or value["status"] != PREFLIGHT_V2_PREPARED_STATUS
    ):
        raise BuildError(f"{label} schema/status mismatch")
    created = exact_string(value["created_utc"], f"{label}.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError(f"{label} created_utc is invalid")
    if value["package_directory"] != package_directory:
        raise BuildError(f"{label} package directory mismatch")
    closure = value["package_closure"]
    expected_closure_keys = {
        "bundle_manifest_sha256", "payload_file_count",
        "sha_index_listed_count_expected", "top_level_file_count_expected",
    }
    if type(closure) is not dict or set(closure) != expected_closure_keys:
        raise BuildError(f"{label}.package_closure exact schema mismatch")
    if (
        closure["bundle_manifest_sha256"] != index["BUNDLE_MANIFEST.json"]
        or closure["payload_file_count"] != 7
        or closure["sha_index_listed_count_expected"] != 9
        or closure["top_level_file_count_expected"] != 10
    ):
        raise BuildError(f"{label}.package_closure value mismatch")
    tools = value["locked_tools"]
    if type(tools) is not dict or set(tools) != {"preflight", "synthetic_test"}:
        raise BuildError(f"{label}.locked_tools exact schema mismatch")
    _validate_preflight_v2_locked_tool(
        tools["preflight"],
        expected_name="run_result_free_mars_native_preflight_v2.py",
        expected_sha256=index["run_result_free_mars_native_preflight_v2.py"],
        label=f"{label}.locked_tools.preflight",
    )
    _validate_preflight_v2_locked_tool(
        tools["synthetic_test"],
        expected_name="test_result_free_mars_native_preflight_v2_synthetic.py",
        expected_sha256=index["test_result_free_mars_native_preflight_v2_synthetic.py"],
        label=f"{label}.locked_tools.synthetic_test",
    )
    validation = value["author_validation"]
    expected_validation_keys = {
        "darwin_actual", "linux_xfs_actual",
        "manifest_payload_hash_and_size_pass", "source_compile",
        "strict_json_parse_pass", "synthetic_test",
    }
    if type(validation) is not dict or set(validation) != expected_validation_keys:
        raise BuildError(f"{label}.author_validation exact schema mismatch")
    compile_evidence = validation["source_compile"]
    synthetic_evidence = validation["synthetic_test"]
    counter_keys = {"checked", "passed", "failed"}
    if (
        type(compile_evidence) is not dict
        or set(compile_evidence) != counter_keys | {"output_sha256"}
        or type(synthetic_evidence) is not dict
        or set(synthetic_evidence) != counter_keys | {"raw_output_sha256"}
    ):
        raise BuildError(f"{label}.author_validation evidence schema mismatch")
    for evidence, evidence_label in (
        (compile_evidence, "source_compile"),
        (synthetic_evidence, "synthetic_test"),
    ):
        checked = exact_integer(
            evidence["checked"], f"{label}.author_validation.{evidence_label}.checked"
        )
        passed = exact_integer(
            evidence["passed"], f"{label}.author_validation.{evidence_label}.passed"
        )
        failed = exact_integer(
            evidence["failed"], f"{label}.author_validation.{evidence_label}.failed"
        )
        if checked <= 0 or passed != checked or failed != 0:
            raise BuildError(f"{label}.author_validation.{evidence_label} is not all-pass")
    if compile_evidence["output_sha256"] != index[
        "AUTHOR_COMPILE_V2_OUTPUT.json"
    ]:
        raise BuildError(f"{label} compile output cross-binding mismatch")
    if synthetic_evidence["raw_output_sha256"] != index[
        "AUTHOR_PREFLIGHT_V2_SYNTHETIC_OUTPUT.json"
    ]:
        raise BuildError(f"{label} synthetic output cross-binding mismatch")
    if (
        validation["strict_json_parse_pass"] is not True
        or validation["manifest_payload_hash_and_size_pass"] is not True
        or type(validation["darwin_actual"]) is not str
        or type(validation["linux_xfs_actual"]) is not str
    ):
        raise BuildError(f"{label}.author_validation status mismatch")
    if value["authority"] != PREFLIGHT_V2_ALL_FALSE_AUTHORITY:
        raise BuildError(f"{label} authority is not exact all-false")
    _require_false_scope_fields(
        value["scope"],
        required_false=frozenset({
            "mars_accessed", "mars_written", "results_accessed",
            "external_processes_inspected_or_controlled",
            "real_preflight_or_smoke_subprocess_started", "signals_sent",
            "controller_or_outer_main_executed", "deployment_or_resume_executed",
            "production_root_or_journal_created_or_modified",
        }),
        label=f"{label}.scope",
    )
    if set(value["scope"]) != {
        "mars_accessed", "mars_written", "results_accessed",
        "external_processes_inspected_or_controlled",
        "real_preflight_or_smoke_subprocess_started", "signals_sent",
        "controller_or_outer_main_executed", "deployment_or_resume_executed",
        "production_root_or_journal_created_or_modified",
    }:
        raise BuildError(f"{label}.scope exact key set mismatch")
    if not exact_string(value["next_legal_action"], f"{label}.next_legal_action"):
        raise BuildError(f"{label}.next_legal_action is empty")
    # The separately signed index SHA is deliberately dynamic; checking its
    # syntax here makes explicit that it is not self-reported by the receipt.
    exact_sha256(index_sha256, f"{label}.separately_signed_index_sha256")


def validate_preflight_v2_qa_manifest_semantics(
    value: Any,
    *,
    member_bytes: Mapping[str, bytes],
    index: Mapping[str, str],
) -> None:
    label = "preflight v2 independent QA manifest"
    if type(value) is not dict or set(value) != PREFLIGHT_V2_QA_MANIFEST_KEYS:
        raise BuildError(f"{label} exact key set mismatch")
    if (
        value["schema"] != PREFLIGHT_V2_QA_MANIFEST_SCHEMA
        or value["status"] != PREFLIGHT_V2_QA_STATUS
        or value["action_scoped_verdict"] != PREFLIGHT_V2_QA_ACTION_VERDICT
        or value["finding_counts"] != PREFLIGHT_V2_ZERO_FINDINGS
    ):
        raise BuildError(f"{label} schema/status/verdict/findings mismatch")
    created = exact_string(value["created_utc"], f"{label}.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError(f"{label} created_utc is invalid")
    if exact_integer(value["payload_file_count"], f"{label}.payload_file_count") != 5:
        raise BuildError(f"{label} payload count is not exact5")
    validate_preflight_v2_manifest_file_records(
        value["files"],
        expected_names=PREFLIGHT_V2_QA_PAYLOAD_NAMES,
        member_bytes=member_bytes,
        index=index,
        label=label,
    )
    if value["closure_files_not_in_payload_manifest"] != sorted(
        PREFLIGHT_V2_QA_CLOSURE_NAMES
    ):
        raise BuildError(f"{label} closure member list mismatch")
    if value["authority"] != PREFLIGHT_V2_ALL_FALSE_AUTHORITY:
        raise BuildError(f"{label} authority is not exact all-false")


def validate_preflight_v2_qa_receipt_semantics(
    value: Any,
    *,
    qa_directory: str,
    package_directory: str,
    package_index: Mapping[str, str],
    package_index_sha256: str,
    qa_index: Mapping[str, str],
) -> None:
    label = "preflight v2 independent QA receipt"
    if type(value) is not dict or set(value) != PREFLIGHT_V2_QA_RECEIPT_KEYS:
        raise BuildError(f"{label} exact key set mismatch")
    if (
        value["schema"] != PREFLIGHT_V2_QA_RECEIPT_SCHEMA
        or value["status"] != PREFLIGHT_V2_QA_STATUS
        or value["action_scoped_verdict"] != PREFLIGHT_V2_QA_ACTION_VERDICT
        or value["finding_counts"] != PREFLIGHT_V2_ZERO_FINDINGS
    ):
        raise BuildError(f"{label} schema/status/verdict/findings mismatch")
    created = exact_string(value["created_utc"], f"{label}.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError(f"{label} created_utc is invalid")
    if value["qa_directory"] != qa_directory:
        raise BuildError(f"{label} qa_directory mismatch")
    audited = value["audited_package"]
    expected_audited_keys = {
        "bundle_manifest_sha256", "contract_sha256", "directory",
        "evidence_bindings_sha256", "prepared_receipt_sha256",
        "script_sha256", "sha256_index_sha256", "test_sha256",
    }
    if (
        type(audited) is not dict
        or set(audited) != expected_audited_keys
        or audited.get("directory") != package_directory
    ):
        raise BuildError(f"{label}.audited_package directory mismatch")
    required_package_bindings = {
        "script_sha256": package_index["run_result_free_mars_native_preflight_v2.py"],
        "test_sha256": package_index[
            "test_result_free_mars_native_preflight_v2_synthetic.py"
        ],
        "contract_sha256": package_index[
            "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V2.json"
        ],
        "evidence_bindings_sha256": package_index[
            "UPSTREAM_EVIDENCE_BINDINGS_V2.json"
        ],
        "prepared_receipt_sha256": package_index["PREPARED_RESULT_FREE_RECEIPT.json"],
        "bundle_manifest_sha256": package_index["BUNDLE_MANIFEST.json"],
        "sha256_index_sha256": package_index_sha256,
    }
    if any(audited.get(key) != digest for key, digest in required_package_bindings.items()):
        raise BuildError(f"{label}.audited_package closure cross-binding mismatch")
    artifacts = value["qa_artifacts"]
    artifact_names = {
        "report": "INDEPENDENT_QA_REPORT_CN.md",
        "output": "INDEPENDENT_QA_OUTPUT.json",
        "log": "COMMAND_LOG.txt",
        "harness": "QA_HARNESS_OR_METHOD.md",
        "closure": "PACKAGE_CLOSURE_QA.json",
        "manifest": "BUNDLE_MANIFEST.json",
    }
    if type(artifacts) is not dict or set(artifacts) != set(artifact_names):
        raise BuildError(f"{label}.qa_artifacts exact schema mismatch")
    for key, name in artifact_names.items():
        item = artifacts[key]
        if (
            type(item) is not dict
            or set(item) != {"path", "sha256"}
            or item["path"] != name
            or item["sha256"] != qa_index[name]
        ):
            raise BuildError(f"{label}.qa_artifacts.{key} binding mismatch")
    validation = value["independent_validation"]
    if type(validation) is not dict or not validation:
        raise BuildError(f"{label}.independent_validation is empty")
    if any(
        type(item) is not str or item.startswith(("FAIL", "NO_GO"))
        for item in validation.values()
    ):
        raise BuildError(f"{label}.independent_validation contains failure")
    if value["authority"] != PREFLIGHT_V2_ALL_FALSE_AUTHORITY:
        raise BuildError(f"{label} authority is not exact all-false")
    _require_false_scope_fields(
        value["scope"],
        required_false=frozenset({
            "mars_accessed", "results_accessed", "real_preflight_executed",
            "production_executed", "external_processes_inspected_or_controlled",
            "signals_sent", "candidate_modified", "memory_modified",
        }),
        label=f"{label}.scope",
    )
    if set(value["scope"]) != {
        "mars_accessed", "results_accessed", "real_preflight_executed",
        "production_executed", "external_processes_inspected_or_controlled",
        "signals_sent", "candidate_modified", "memory_modified",
    }:
        raise BuildError(f"{label}.scope exact key set mismatch")
    if not exact_string(value["next_legal_action"], f"{label}.next_legal_action"):
        raise BuildError(f"{label}.next_legal_action is empty")


def validate_preflight_v2_dynamic_closure_semantics(
    *,
    package_directory: Path,
    package_index_sha256: str,
    package_index: Mapping[str, str],
    package_members: Mapping[str, bytes],
    audit_directory: Path,
    audit_index: Mapping[str, str],
    audit_members: Mapping[str, bytes],
) -> None:
    if set(package_index) != PREFLIGHT_V2_PREPARED_INDEX_NAMES:
        raise BuildError("preflight v2 prepared SHA index exact9 closure mismatch")
    if set(package_members) != PREFLIGHT_V2_PREPARED_INDEX_NAMES:
        raise BuildError("preflight v2 prepared held-member exact9 closure mismatch")
    if set(audit_index) != PREFLIGHT_V2_QA_INDEX_NAMES:
        raise BuildError("preflight v2 QA SHA index exact7 closure mismatch")
    if set(audit_members) != PREFLIGHT_V2_QA_INDEX_NAMES:
        raise BuildError("preflight v2 QA held-member exact7 closure mismatch")
    for label, index, members in (
        ("prepared", package_index, package_members),
        ("QA", audit_index, audit_members),
    ):
        for name, digest in index.items():
            if sha256_bytes(members[name]) != digest:
                raise BuildError(f"preflight v2 {label} held member SHA mismatch: {name}")
    prepared_manifest = strict_canonical_json_object(
        package_members["BUNDLE_MANIFEST.json"], "preflight v2 prepared manifest"
    )
    validate_preflight_v2_prepared_manifest_semantics(
        prepared_manifest, member_bytes=package_members, index=package_index
    )
    prepared_receipt = strict_canonical_json_object(
        package_members["PREPARED_RESULT_FREE_RECEIPT.json"],
        "preflight v2 prepared receipt",
    )
    validate_preflight_v2_prepared_receipt_semantics(
        prepared_receipt,
        package_directory=package_directory.name,
        member_bytes=package_members,
        index=package_index,
        index_sha256=package_index_sha256,
    )
    qa_manifest = strict_canonical_json_object(
        audit_members["BUNDLE_MANIFEST.json"], "preflight v2 QA manifest"
    )
    validate_preflight_v2_qa_manifest_semantics(
        qa_manifest, member_bytes=audit_members, index=audit_index
    )
    qa_receipt = strict_canonical_json_object(
        audit_members["INDEPENDENT_QA_RECEIPT.json"], "preflight v2 QA receipt"
    )
    validate_preflight_v2_qa_receipt_semantics(
        qa_receipt,
        qa_directory=audit_directory.name,
        package_directory=package_directory.name,
        package_index=package_index,
        package_index_sha256=package_index_sha256,
        qa_index=audit_index,
    )


def read_exact_frozen_index_closure(
    directory: Path,
    *,
    index_path: Path,
    index_sha256: str,
    expected_top_names: frozenset[str],
    expected_index_names: frozenset[str],
    label: str,
) -> tuple[dict[str, str], dict[str, bytes]]:
    if index_path != directory / "SHA256SUMS":
        raise BuildError(f"{label} SHA index path/name mismatch")
    directory_fd = open_directory_path(directory)
    try:
        identity = require_directory_fd(directory_fd, label)
        if identity.mode != 0o555:
            raise BuildError(f"{label} directory mode is not exact 0555")
        if set(fresh_directory_names(directory_fd)) != expected_top_names:
            raise BuildError(f"{label} exact top-level closure mismatch")
    finally:
        os.close(directory_fd)
    index_bytes, _ = read_frozen_regular_bytes_single_open(
        index_path, index_sha256, f"{label} SHA index"
    )
    index = parse_sha_index(index_bytes)
    if set(index) != expected_index_names:
        raise BuildError(f"{label} SHA index member set mismatch")
    members: dict[str, bytes] = {}
    for name, digest in sorted(index.items()):
        data, _ = read_frozen_regular_bytes_single_open(
            directory / name, digest, f"{label} member:{name}"
        )
        members[name] = data
    return index, members


def fresh_directory_cursor(held_fd: int) -> int:
    fresh = os.open(
        ".",
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=held_fd,
    )
    if not same_device_inode(identity_fd(fresh).json(), identity_fd(held_fd)):
        os.close(fresh)
        raise BuildError("fresh directory cursor does not match held directory inode")
    return fresh


def fresh_directory_names(fd: int) -> list[str]:
    fresh = fresh_directory_cursor(fd)
    try:
        names = sorted(os.listdir(fresh))
    finally:
        os.close(fresh)
    if len(names) != len(set(names)):
        raise BuildError("duplicate directory entry")
    for name in names:
        safe_name(name)
    return names


def directory_names(fd: int) -> set[str]:
    names = set(fresh_directory_names(fd))
    for name in names:
        safe_name(name)
    return names


def classify_record_members(distribution: str, raw_members: Sequence[str]
                            ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    expected = tuple(EXTERNAL_RECORD_EXCLUSIONS[distribution])
    safe: list[str] = []
    excluded: list[str] = []
    for raw in raw_members:
        if raw in expected:
            excluded.append(raw)
        else:
            try:
                safe.append(safe_relative(raw))
            except BuildError as exc:
                raise BuildError(
                    f"unexpected unsafe RECORD member for {distribution}: {raw!r}"
                ) from exc
    if tuple(sorted(excluded)) != tuple(sorted(expected)):
        raise BuildError(
            f"external RECORD inventory mismatch for {distribution}: "
            f"observed={sorted(excluded)!r} expected={sorted(expected)!r}"
        )
    return tuple(safe), tuple(excluded)


def parse_record(data: bytes) -> tuple[str, ...]:
    try:
        values = tuple(row[0] for row in csv.reader(io.StringIO(data.decode("utf-8"))) if row)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BuildError(f"RECORD parse failed: {exc}") from exc
    if not values or any(not item for item in values) or len(set(values)) != len(values):
        raise BuildError("RECORD contains empty/duplicate members")
    return values


def closure_digest(name: str, safe_members: Sequence[str],
                   files: Mapping[str, SourceFileSnapshot],
                   excluded: Sequence[str]) -> str:
    rows = [
        f"COPY\0{path}\0{files[path].sha256}\0{files[path].identity.size_bytes}\n"
        for path in sorted(safe_members)
    ]
    rows += [f"EXCLUDE\0{path}\0OUT_OF_SITE_CONSOLE_OR_MANPAGE\n" for path in sorted(excluded)]
    return sha256_bytes("".join(rows).encode("utf-8"))


def discover_source(source_site_packages: Path) -> DiscoverySnapshot:
    root_fd = open_directory_path(source_site_packages)
    root_identity = require_directory_fd(root_fd, "source site-packages")
    distributions: dict[str, DistributionSnapshot] = {}
    try:
        found: dict[str, importlib.metadata.Distribution] = {}
        for item in importlib.metadata.distributions(path=[os.fspath(source_site_packages)]):
            declared = item.metadata.get("Name")
            if not declared:
                continue
            canonical = canonical_distribution_name(declared)
            if canonical not in COPY_DISTRIBUTIONS:
                continue
            if canonical in found:
                raise BuildError(f"duplicate selected distribution: {canonical}")
            found[canonical] = item
        missing = sorted(set(COPY_DISTRIBUTIONS) - set(found))
        if missing:
            raise BuildError(f"missing selected distributions: {missing}")

        files: dict[str, SourceFileSnapshot] = {}
        for name in COPY_DISTRIBUTIONS:
            distribution = found[name]
            if distribution.files is None:
                raise BuildError(f"distribution has no RECORD: {name}")
            raw = tuple(PurePosixPath(item).as_posix() for item in distribution.files)
            if len(set(raw)) != len(raw):
                raise BuildError(f"distribution has duplicate RECORD members: {name}")
            safe, excluded = classify_record_members(name, raw)
            records = [
                item for item in safe if PurePosixPath(item).name == "RECORD"
                and PurePosixPath(item).parent.name.endswith(".dist-info")
            ]
            if len(records) != 1:
                raise BuildError(f"RECORD is not unique: {name}")
            metadata = [
                item for item in safe if PurePosixPath(item).name == "METADATA"
                and PurePosixPath(item).parent == PurePosixPath(records[0]).parent
            ]
            if len(metadata) != 1:
                raise BuildError(f"METADATA is not unique: {name}")
            record_fd = open_regular_at(root_fd, records[0])
            record_identity = require_regular_fd(record_fd, f"{name}.RECORD")
            record_data = read_fd_bytes(record_fd)
            parsed = parse_record(record_data)
            if parsed != raw:
                os.close(record_fd)
                raise BuildError(f"held RECORD/order differs from importlib metadata: {name}")
            classify_record_members(name, parsed)

            for relative in safe:
                if relative in files:
                    os.close(record_fd)
                    raise BuildError(
                        f"file collision {relative}: {files[relative].distribution}/{name}"
                    )
                fd = open_regular_at(root_fd, relative)
                try:
                    identity = require_regular_fd(fd, relative)
                    digest, size = sha256_fd(fd)
                finally:
                    os.close(fd)
                if size != identity.size_bytes:
                    os.close(record_fd)
                    raise BuildError(f"source size drift during discovery: {relative}")
                files[relative] = SourceFileSnapshot(relative, name, identity, digest)
            if files[records[0]].identity != record_identity:
                os.close(record_fd)
                raise BuildError(f"held RECORD identity mismatch: {name}")
            metadata_fd = open_regular_at(root_fd, metadata[0])
            try:
                message = email.parser.BytesParser().parsebytes(read_fd_bytes(metadata_fd))
            finally:
                os.close(metadata_fd)
            declared_name = message.get("Name")
            version = message.get("Version")
            if (not declared_name or not version or
                    canonical_distribution_name(declared_name) != name or
                    distribution.version != version):
                os.close(record_fd)
                raise BuildError(f"held METADATA mismatch: {name}")
            import_relative = IMPORT_RELATIVE_PATHS.get(name)
            if import_relative is not None and import_relative not in safe:
                os.close(record_fd)
                raise BuildError(f"import origin absent: {name}")
            distributions[name] = DistributionSnapshot(
                name, declared_name, version, records[0], metadata[0], import_relative,
                raw, safe, excluded, sha256_bytes(record_data), "", record_fd,
            )
        for name, item in distributions.items():
            item.closure_digest = closure_digest(name, item.safe_members, files, item.excluded_members)
        snapshot = DiscoverySnapshot(source_site_packages, root_fd, root_identity, files, distributions)
        revalidate_source(snapshot)
        return snapshot
    except BaseException:
        for item in distributions.values():
            try:
                os.close(item.held_record_fd)
            except OSError:
                pass
        os.close(root_fd)
        raise


def revalidate_source(snapshot: DiscoverySnapshot) -> None:
    path_fd = open_directory_path(snapshot.root_path)
    try:
        if identity_fd(path_fd) != snapshot.root_identity:
            raise BuildError("source root identity changed")
    finally:
        os.close(path_fd)
    for relative, expected in snapshot.files.items():
        fd = open_regular_at(snapshot.root_fd, relative)
        try:
            identity = require_regular_fd(fd, relative)
            digest, size = sha256_fd(fd)
        finally:
            os.close(fd)
        if identity != expected.identity or digest != expected.sha256 or size != expected.identity.size_bytes:
            raise BuildError(f"source changed after discovery: {relative}")
    for name, item in snapshot.distributions.items():
        data = read_fd_bytes(item.held_record_fd)
        if (identity_fd(item.held_record_fd) != snapshot.files[item.record_relative_path].identity or
                sha256_bytes(data) != item.record_sha256 or parse_record(data) != item.raw_record_members):
            raise BuildError(f"held RECORD changed: {name}")


def copy_fd_to_relative(source_fd: int, destination_root_fd: int, relative: str) -> dict[str, Any]:
    parent_fd, name = ensure_parent_at(destination_root_fd, relative)
    destination_fd = -1
    try:
        destination_fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=parent_fd,
        )
        digest = hashlib.sha256()
        offset = 0
        while True:
            block = os.pread(source_fd, 1024 * 1024, offset)
            if not block:
                break
            digest.update(block)
            written = 0
            while written < len(block):
                written += os.write(destination_fd, block[written:])
            offset += len(block)
        os.fsync(destination_fd)
        identity = require_regular_fd(destination_fd, relative)
        os.fsync(parent_fd)
        return {"sha256": digest.hexdigest(), "size_bytes": offset, "identity": identity.json()}
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(parent_fd)


def copy_snapshot_member(snapshot: DiscoverySnapshot, item: SourceFileSnapshot,
                         destination_fd: int) -> None:
    source_fd = open_regular_at(snapshot.root_fd, item.relative_path)
    try:
        before = identity_fd(source_fd)
        before_sha, before_size = sha256_fd(source_fd)
        if before != item.identity or before_sha != item.sha256 or before_size != item.identity.size_bytes:
            raise BuildError(f"source changed before copy: {item.relative_path}")
        copied = copy_fd_to_relative(source_fd, destination_fd, item.relative_path)
        after = identity_fd(source_fd)
        after_sha, after_size = sha256_fd(source_fd)
    finally:
        os.close(source_fd)
    if after != item.identity or after_sha != item.sha256 or after_size != item.identity.size_bytes:
        raise BuildError(f"source changed during copy: {item.relative_path}")
    reopened = open_regular_at(snapshot.root_fd, item.relative_path)
    try:
        reopen_identity = identity_fd(reopened)
        reopen_sha, reopen_size = sha256_fd(reopened)
    finally:
        os.close(reopened)
    if (reopen_identity != item.identity or reopen_sha != item.sha256 or
            reopen_size != item.identity.size_bytes):
        raise BuildError(f"source path changed after copy: {item.relative_path}")
    if copied["sha256"] != item.sha256 or copied["size_bytes"] != item.identity.size_bytes:
        raise BuildError(f"copied bytes differ from discovery: {item.relative_path}")


def validate_copied_records(snapshot: DiscoverySnapshot, private_fd: int) -> None:
    union: set[str] = set()
    for name, item in snapshot.distributions.items():
        fd = open_regular_at(private_fd, item.record_relative_path)
        try:
            data = read_fd_bytes(fd)
        finally:
            os.close(fd)
        if sha256_bytes(data) != item.record_sha256:
            raise BuildError(f"copied RECORD digest mismatch: {name}")
        raw = parse_record(data)
        safe, excluded = classify_record_members(name, raw)
        if raw != item.raw_record_members or safe != item.safe_members or excluded != item.excluded_members:
            raise BuildError(f"copied RECORD closure mismatch: {name}")
        union.update(safe)
    if union != set(snapshot.files):
        raise BuildError("copied RECORD union differs from discovery closure")


def inventory_structural(fd: int, *, include_root: bool = True,
                         prefix: str = "") -> list[dict[str, Any]]:
    """Inventory root/directories/files for the explicit structural digest."""
    records: list[dict[str, Any]] = []
    root_info = os.fstat(fd)
    if not stat.S_ISDIR(root_info.st_mode):
        raise BuildError("structural inventory root is not a directory")
    if include_root:
        records.append({
            "relative_path": ".", "kind": "directory", "sha256": "",
            "size_bytes": 0, "mode": f"{stat.S_IMODE(root_info.st_mode):04o}",
        })

    def recurse(directory_fd: int, relative_prefix: str) -> None:
        names_before = fresh_directory_names(directory_fd)
        fresh = fresh_directory_cursor(directory_fd)
        try:
            for name in names_before:
                relative = f"{relative_prefix}/{name}" if relative_prefix else name
                child = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=fresh,
                )
                try:
                    info = os.fstat(child)
                    mode = f"{stat.S_IMODE(info.st_mode):04o}"
                    if stat.S_ISDIR(info.st_mode):
                        records.append({
                            "relative_path": relative, "kind": "directory",
                            "sha256": "", "size_bytes": 0, "mode": mode,
                        })
                        recurse(child, relative)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        digest, size = sha256_fd(child)
                        records.append({
                            "relative_path": relative, "kind": "regular",
                            "sha256": digest, "size_bytes": size, "mode": mode,
                        })
                    else:
                        raise BuildError(f"unsupported/symlink tree member: {relative}")
                finally:
                    os.close(child)
        finally:
            os.close(fresh)
        if names_before != fresh_directory_names(directory_fd):
            raise BuildError("directory entries changed during structural inventory")

    recurse(fd, prefix)
    records.sort(key=lambda item: item["relative_path"])
    if len(records) != len({item["relative_path"] for item in records}):
        raise BuildError("duplicate structural inventory relative path")
    return records


def files_only_records(
    structural_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": item["relative_path"],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
            "mode": item["mode"],
        }
        for item in structural_records
        if item["kind"] == "regular"
    ]


def inventory_files_only(fd: int) -> list[dict[str, Any]]:
    return files_only_records(inventory_structural(fd, include_root=True))


def files_only_digest(records: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        f"{item['relative_path']}\0{item['sha256']}\0{item['size_bytes']}\0{item['mode']}\n"
        for item in records
    ]
    return sha256_bytes("".join(sorted(rows)).encode("utf-8"))


def structural_digest(records: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        f"{item['relative_path']}\0{item['kind']}\0{item['sha256']}\0"
        f"{item['size_bytes']}\0{item['mode']}\n"
        for item in records
    ]
    return sha256_bytes("".join(sorted(rows)).encode("utf-8"))


def freeze_tree(fd: int) -> None:
    fresh = fresh_directory_cursor(fd)
    try:
        for name in fresh_directory_names(fd):
            child = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=fresh,
            )
            try:
                info = os.fstat(child)
                if stat.S_ISDIR(info.st_mode):
                    freeze_tree(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    os.fchmod(child, 0o444)
                    os.fsync(child)
                else:
                    raise BuildError(f"cannot freeze unsupported member: {name}")
            finally:
                os.close(child)
    finally:
        os.close(fresh)
    os.fchmod(fd, 0o555)
    os.fsync(fd)


def verify_frozen(fd: int) -> None:
    if identity_fd(fd).mode != 0o555:
        raise BuildError("frozen directory mode mismatch")
    fresh = fresh_directory_cursor(fd)
    try:
        for name in fresh_directory_names(fd):
            child = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=fresh,
            )
            try:
                info = os.fstat(child)
                if stat.S_ISDIR(info.st_mode):
                    verify_frozen(child)
                elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                      stat.S_IMODE(info.st_mode) != 0o444):
                    raise BuildError(f"frozen file mismatch: {name}")
            finally:
                os.close(child)
    finally:
        os.close(fresh)


def verify_bundle(fd: int, binding: Mapping[str, Any]) -> dict[str, Any]:
    index_fd = open_regular_at(fd, "SHA256SUMS")
    try:
        index_data = read_fd_bytes(index_fd)
    finally:
        os.close(index_fd)
    if sha256_bytes(index_data) != binding["sha256_index_sha256"]:
        raise BuildError("bundle SHA index digest mismatch")
    records = parse_sha_index(index_data)
    if len(records) != binding["indexed_count"]:
        raise BuildError("bundle SHA index count mismatch")
    if directory_names(fd) != set(records) | {"SHA256SUMS"}:
        raise BuildError("bundle exact top-level set mismatch")
    for name, expected in records.items():
        member = open_regular_at(fd, name)
        try:
            actual, _ = sha256_fd(member)
        finally:
            os.close(member)
        if actual != expected:
            raise BuildError(f"bundle member SHA mismatch: {name}")
    for key, filename in (
        ("receipt_sha256", "PREPARED_RESULT_FREE_RECEIPT.json"),
        ("bundle_manifest_sha256", "BUNDLE_MANIFEST.json"),
    ):
        if records.get(filename) != binding[key]:
            raise BuildError(f"bundle pinned member mismatch: {filename}")
    if len(records) + 1 != binding["top_level_count"]:
        raise BuildError("bundle top-level count mismatch")
    for filename in ("BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json"):
        member = open_regular_at(fd, filename)
        try:
            parsed = strict_json_loads(read_fd_bytes(member))
        finally:
            os.close(member)
        if not isinstance(parsed, dict):
            raise BuildError(f"bundle {filename} is not a strict JSON object")
    return {"top_level_count": len(records) + 1, "indexed_count": len(records),
            "sha256_index_sha256": binding["sha256_index_sha256"]}


def copy_authenticated_bundle(source_path: Path, staging_fd: int,
                              binding: Mapping[str, Any],
                              held_source_fd: int | None = None,
                              ) -> tuple[int, dict[str, Any]]:
    if source_path.name != binding["directory_name"]:
        raise BuildError("authenticated bundle directory name mismatch")
    source_fd = (
        open_directory_path(source_path)
        if held_source_fd is None else held_source_fd
    )
    try:
        if not path_matches_fd(source_path, source_fd):
            raise BuildError("authenticated source bundle path/held FD mismatch")
        before = verify_bundle(source_fd, binding)
        bundle_fd = mkdir_open_at(staging_fd, "bundle")
        try:
            for name in sorted(directory_names(source_fd)):
                member = open_regular_at(source_fd, name)
                try:
                    copy_fd_to_relative(member, bundle_fd, name)
                finally:
                    os.close(member)
            copied = verify_bundle(bundle_fd, binding)
            after = verify_bundle(source_fd, binding)
            if before != copied or before != after:
                raise BuildError("bundle changed during authenticated copy")
            return bundle_fd, copied
        except BaseException:
            os.close(bundle_fd)
            raise
    finally:
        os.close(source_fd)


def copy_support_from_staged_bundle(bundle_fd: int, staging_fd: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in SUPPORT_FILES:
        source = open_regular_at(bundle_fd, name)
        try:
            expected, size = sha256_fd(source)
            copied = copy_fd_to_relative(source, staging_fd, name)
        finally:
            os.close(source)
        if copied["sha256"] != expected or copied["size_bytes"] != size:
            raise BuildError(f"support copy mismatch: {name}")
        result[name] = {"sha256": expected, "size_bytes": size,
                        "source": "authenticated_staged_bundle_fd"}
    return result


def collect_support_file_evidence(root_fd: int, bundle_fd: int,
                                  final_root: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for name in SUPPORT_FILES:
        support_fd = open_regular_at(root_fd, name)
        bundle_member_fd = open_regular_at(bundle_fd, name)
        try:
            support_identity = identity_fd(support_fd)
            support_sha, support_size = sha256_fd(support_fd)
            bundle_sha, bundle_size = sha256_fd(bundle_member_fd)
        finally:
            os.close(bundle_member_fd)
            os.close(support_fd)
        if support_identity.mode != 0o444 or support_identity.nlink != 1:
            raise BuildError(f"support frozen identity mismatch: {name}")
        if support_sha != bundle_sha or support_size != bundle_size:
            raise BuildError(f"support/bundle held-FD bytes mismatch: {name}")
        evidence[name] = {
            "path": os.fspath(final_root / name),
            "device": support_identity.device,
            "inode": support_identity.inode,
            "sha256": support_sha,
            "size_bytes": support_size,
        }
    return evidence


def build_runtime_manifest(private_fd: int, snapshot: DiscoverySnapshot,
                           final_root: Path) -> dict[str, Any]:
    records = inventory_files_only(private_fd)
    listed = {item["relative_path"] for item in records}
    distributions: dict[str, Any] = {}
    for name in MANIFEST_DISTRIBUTIONS:
        item = snapshot.distributions[name]
        if item.import_relative_path not in listed or item.record_relative_path not in listed:
            raise BuildError(f"manifest member absent: {name}")
        distributions[name] = {
            "version": item.version,
            "import_relative_path": item.import_relative_path,
            "distribution_record_relative_path": item.record_relative_path,
        }
    return {
        "schema": RUNTIME_MANIFEST_SCHEMA,
        "status": RUNTIME_MANIFEST_STATUS,
        "site_packages_root": os.fspath(final_root / "private_runtime_site_packages"),
        "exact_file_set": True,
        "files": records,
        "distributions": distributions,
        "files_only_digest_algorithm": FILES_ONLY_DIGEST_ALGORITHM,
        "files_only_root_digest": files_only_digest(records),
    }


def build_staging(journal_fd: int, source_bundle: Path, snapshot: DiscoverySnapshot,
                  bundle_binding: Mapping[str, Any], final_root: Path,
                  source_bundle_held_fd: int | None = None,
                  ) -> StagingHandles:
    staging_fd = mkdir_open_at(journal_fd, JOURNAL_NAMES["staging"])
    bundle_fd = private_fd = -1
    try:
        bundle_fd, bundle_evidence = copy_authenticated_bundle(
            source_bundle, staging_fd, bundle_binding,
            held_source_fd=source_bundle_held_fd,
        )
        private_fd = mkdir_open_at(staging_fd, "private_runtime_site_packages")
        for relative in sorted(snapshot.files):
            copy_snapshot_member(snapshot, snapshot.files[relative], private_fd)
        revalidate_source(snapshot)
        validate_copied_records(snapshot, private_fd)
        freeze_tree(private_fd)
        manifest = build_runtime_manifest(private_fd, snapshot, final_root)
        manifest_data = canonical_json_bytes(manifest)
        write_bytes_at_exclusive(
            staging_fd, "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json", manifest_data
        )
        copy_support_from_staged_bundle(bundle_fd, staging_fd)
        if directory_names(staging_fd) != ROOT_CHILDREN:
            raise BuildError("staging ROOT exact child set mismatch")
        freeze_tree(staging_fd)
        verify_frozen(staging_fd)
        verify_bundle(bundle_fd, bundle_binding)
        private_structural_records = inventory_structural(private_fd, include_root=True)
        private_files_only_records = files_only_records(private_structural_records)
        full_structural_records = inventory_structural(staging_fd, include_root=True)
        full_files_only_records = files_only_records(full_structural_records)
        support_files = collect_support_file_evidence(
            staging_fd, bundle_fd, final_root
        )
        evidence = {
            "bundle": bundle_evidence,
            "support_files": support_files,
            "runtime_manifest_sha256": sha256_bytes(manifest_data),
            "files_only_digest_algorithm": FILES_ONLY_DIGEST_ALGORITHM,
            "structural_digest_algorithm": STRUCTURAL_DIGEST_ALGORITHM,
            "files_only_runtime_root_digest": manifest["files_only_root_digest"],
            "files_only_private_root_digest": files_only_digest(
                private_files_only_records
            ),
            "structural_private_tree_digest": structural_digest(
                private_structural_records
            ),
            "runtime_file_count": len(manifest["files"]),
            "files_only_full_root_digest": files_only_digest(
                full_files_only_records
            ),
            "structural_full_root_digest": structural_digest(
                full_structural_records
            ),
            "full_root_files_only_count": len(full_files_only_records),
            "full_root_structural_record_count": len(full_structural_records),
            "source_inventory": snapshot.inventory(),
            "runtime_manifest_distribution_keys": list(MANIFEST_DISTRIBUTIONS),
            "external_record_exclusions": external_record_exclusion_evidence(),
        }
        return StagingHandles(
            os.dup(journal_fd), staging_fd, bundle_fd, private_fd,
            identity_fd(staging_fd), identity_fd(bundle_fd), identity_fd(private_fd), evidence,
        )
    except BaseException:
        for fd in (private_fd, bundle_fd, staging_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise


def authorized_private_files(snapshot: DiscoverySnapshot) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": relative,
            "sha256": snapshot.files[relative].sha256,
            "size_bytes": snapshot.files[relative].identity.size_bytes,
            "mode": "0444",
        }
        for relative in sorted(snapshot.files)
    ]


def reconstruct_candidate_evidence(
    root_fd: int,
    snapshot: DiscoverySnapshot,
    bundle_binding: Mapping[str, Any],
    final_root: Path,
) -> tuple[Identity, Identity, dict[str, Any]]:
    """Independently reconstruct evidence; never consume intent build claims."""
    if directory_names(root_fd) != ROOT_CHILDREN:
        raise BuildError("recovery candidate exact ROOT child set mismatch")
    verify_frozen(root_fd)
    bundle_fd = open_directory_at(root_fd, "bundle")
    private_fd = open_directory_at(root_fd, "private_runtime_site_packages")
    try:
        bundle_identity = identity_fd(bundle_fd)
        private_identity = identity_fd(private_fd)
        bundle_evidence = verify_bundle(bundle_fd, bundle_binding)
        validate_copied_records(snapshot, private_fd)
        private_structural = inventory_structural(private_fd, include_root=True)
        private_files = files_only_records(private_structural)
        if private_files != authorized_private_files(snapshot):
            raise BuildError(
                "recovery private files differ from authorized source inventory"
            )
        expected_manifest = build_runtime_manifest(private_fd, snapshot, final_root)
        manifest_fd = open_regular_at(
            root_fd, "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json"
        )
        try:
            manifest_raw = read_fd_bytes(manifest_fd)
            manifest_identity = identity_fd(manifest_fd)
        finally:
            os.close(manifest_fd)
        if manifest_identity.mode != 0o444 or manifest_identity.nlink != 1:
            raise BuildError("recovery runtime manifest frozen identity mismatch")
        manifest = strict_json_loads(manifest_raw)
        if manifest != expected_manifest:
            raise BuildError(
                "recovery runtime manifest differs from independently rebuilt manifest"
            )
        support_files = collect_support_file_evidence(
            root_fd, bundle_fd, final_root
        )
        full_structural = inventory_structural(root_fd, include_root=True)
        full_files = files_only_records(full_structural)
        evidence = {
            "bundle": bundle_evidence,
            "support_files": support_files,
            "runtime_manifest_sha256": sha256_bytes(manifest_raw),
            "files_only_digest_algorithm": FILES_ONLY_DIGEST_ALGORITHM,
            "structural_digest_algorithm": STRUCTURAL_DIGEST_ALGORITHM,
            "files_only_runtime_root_digest": expected_manifest[
                "files_only_root_digest"
            ],
            "files_only_private_root_digest": files_only_digest(private_files),
            "structural_private_tree_digest": structural_digest(
                private_structural
            ),
            "runtime_file_count": len(private_files),
            "files_only_full_root_digest": files_only_digest(full_files),
            "structural_full_root_digest": structural_digest(full_structural),
            "full_root_files_only_count": len(full_files),
            "full_root_structural_record_count": len(full_structural),
            "source_inventory": snapshot.inventory(),
            "runtime_manifest_distribution_keys": list(MANIFEST_DISTRIBUTIONS),
            "external_record_exclusions": external_record_exclusion_evidence(),
        }
        if (
            evidence["files_only_private_root_digest"]
            != evidence["files_only_runtime_root_digest"]
        ):
            raise BuildError(
                "recovery private and manifest files-only digests differ"
            )
        return bundle_identity, private_identity, evidence
    finally:
        os.close(private_fd)
        os.close(bundle_fd)


def renameat2_noreplace(old_dir_fd: int, old_name: str,
                        new_dir_fd: int, new_name: str) -> None:
    if not sys.platform.startswith("linux"):
        raise BuildError("Linux renameat2(RENAME_NOREPLACE) required; no fallback")
    libc = ctypes.CDLL(None, use_errno=True)
    call = getattr(libc, "renameat2", None)
    if call is None:
        raise BuildError("libc renameat2 unavailable; no fallback")
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    if call(old_dir_fd, os.fsencode(safe_name(old_name)), new_dir_fd,
            os.fsencode(safe_name(new_name)), 1) != 0:
        error = ctypes.get_errno()
        if error in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise BuildError(f"renameat2 unsupported ({error}); no fallback")
        raise BuildError(f"renameat2 no-replace failed errno={error}")


def _canonical_absolute_api_path(value: Any, label: str) -> Path:
    text = exact_string(value, label)
    path = Path(text)
    pure = PurePosixPath(text)
    if (
        not pure.is_absolute()
        or text != os.fspath(pure)
        or any(part in {"", ".", ".."} for part in pure.parts[1:])
    ):
        raise BuildError(f"{label} is not a canonical absolute path")
    return path


def _borrowed_readonly_directory_identity(fd: Any, label: str) -> Identity:
    if type(fd) is not int or fd < 0:
        raise BuildError(f"{label} must be a nonnegative borrowed FD integer")
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError as exc:
        raise BuildError(f"{label} is not an open borrowed FD") from exc
    if flags & os.O_ACCMODE != os.O_RDONLY:
        raise BuildError(f"{label} must be opened O_RDONLY")
    return require_directory_fd(fd, label)


def _assert_absent_names(directory_fd: int, names: Sequence[str], label: str) -> None:
    for name in names:
        if file_exists_at(directory_fd, name):
            raise BuildError(f"{label} must remain absent: {name}")


def _same_directory_object(left: Identity, right: Identity) -> bool:
    return (
        left.device == right.device
        and left.inode == right.inode
        and left.mode == right.mode
    )


def _validate_native_compatibility_request(
    request: Any,
    *,
    production_parent_fd: int,
    compatibility_work_root_fd: int,
) -> dict[str, Any]:
    request_keys = {
        "schema", "scope", "decision_id", "authorization_sha256",
        "compatibility_root", "compatibility_journal",
        "canonical_production_final_root_forbidden",
        "canonical_production_journal_forbidden",
        "canonical_production_parent_forbidden", "publication_requirements",
        "authority",
    }
    if type(request) is not dict or set(request) != request_keys:
        raise BuildError("native compatibility request exact key set mismatch")
    if (
        request["schema"] != NATIVE_COMPATIBILITY_API_SCHEMA
        or request["scope"] != NATIVE_COMPATIBILITY_API_SCOPE
        or request["decision_id"] != NATIVE_COMPATIBILITY_DECISION_ID
    ):
        raise BuildError("native compatibility request schema/scope/decision mismatch")
    exact_sha256(
        request["authorization_sha256"],
        "native compatibility request.authorization_sha256",
    )
    expected_publication = {
        "real_linux_renameat2_noreplace": True,
        "real_linux_xfs_otmpfile_procfd_linkat": True,
        "pathname_fallback_allowed": False,
    }
    expected_authority = {
        "not_production_build": True,
        "production_root_write_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "controller_or_resume_authorized": False,
    }
    if request["publication_requirements"] != expected_publication:
        raise BuildError("native compatibility publication requirements mismatch")
    if request["authority"] != expected_authority:
        raise BuildError("native compatibility authority mismatch")
    compatibility_root = _canonical_absolute_api_path(
        request["compatibility_root"], "native compatibility root"
    )
    compatibility_journal = _canonical_absolute_api_path(
        request["compatibility_journal"], "native compatibility journal"
    )
    canonical_final = _canonical_absolute_api_path(
        request["canonical_production_final_root_forbidden"],
        "native compatibility canonical production final root",
    )
    canonical_journal = _canonical_absolute_api_path(
        request["canonical_production_journal_forbidden"],
        "native compatibility canonical production journal",
    )
    canonical_parent = _canonical_absolute_api_path(
        request["canonical_production_parent_forbidden"],
        "native compatibility canonical production parent",
    )
    expected_journal = canonical_parent / (
        f".result-free-transport-v10.{NATIVE_COMPATIBILITY_DECISION_ID}"
    )
    if (
        canonical_final != EXPECTED_FINAL_ROOT
        or canonical_parent != EXPECTED_FINAL_ROOT.parent
        or canonical_journal != expected_journal
    ):
        raise BuildError("native compatibility canonical production binding mismatch")
    if (
        compatibility_root == compatibility_journal
        or compatibility_root.parent != compatibility_journal.parent
        or compatibility_root in {canonical_final, canonical_journal, canonical_parent}
        or compatibility_journal in {canonical_final, canonical_journal, canonical_parent}
    ):
        raise BuildError("native compatibility targets are not distinct noncanonical siblings")
    root_name = safe_name(compatibility_root.name)
    journal_name = safe_name(compatibility_journal.name)
    if root_name == journal_name:
        raise BuildError("native compatibility root/journal names collide")
    production_identity = _borrowed_readonly_directory_identity(
        production_parent_fd, "native compatibility production parent FD"
    )
    work_identity = _borrowed_readonly_directory_identity(
        compatibility_work_root_fd, "native compatibility work-root FD"
    )
    named_production_fd = open_directory_path(canonical_parent)
    named_work_fd = open_directory_path(compatibility_root.parent)
    try:
        if identity_fd(named_production_fd) != production_identity:
            raise BuildError("production parent named path differs from borrowed FD")
        if identity_fd(named_work_fd) != work_identity:
            raise BuildError("compatibility work-root named path differs from borrowed FD")
    finally:
        os.close(named_work_fd)
        os.close(named_production_fd)
    if (
        directory_fd_is_at_or_below(
            compatibility_work_root_fd, production_identity
        )
        or directory_fd_is_at_or_below(production_parent_fd, work_identity)
    ):
        raise BuildError("compatibility work root aliases/contains production tree")
    return {
        "compatibility_root": compatibility_root,
        "compatibility_journal": compatibility_journal,
        "canonical_final": canonical_final,
        "canonical_journal": canonical_journal,
        "canonical_parent": canonical_parent,
        "root_name": root_name,
        "journal_name": journal_name,
        "production_identity": production_identity,
        "work_identity": work_identity,
    }


def _execute_native_compatibility_probe_core(
    *,
    request: dict[str, Any],
    production_parent_fd: int,
    compatibility_work_root_fd: int,
    rename_impl: Callable[[int, str, int, str], None],
    terminal_publish_impl: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Private fixtureable core; only the public wrapper attests real natives."""

    context = _validate_native_compatibility_request(
        request,
        production_parent_fd=production_parent_fd,
        compatibility_work_root_fd=compatibility_work_root_fd,
    )
    production_identity = context["production_identity"]
    work_identity = context["work_identity"]
    production_fd = os.dup(production_parent_fd)
    work_fd = os.dup(compatibility_work_root_fd)
    compatibility_root_fd = -1
    compatibility_journal_fd = -1
    staging_fd = -1
    staging_name = (
        ".v10-native-compatibility-staging-"
        + request["authorization_sha256"][:24]
    )
    try:
        if (
            identity_fd(production_fd) != production_identity
            or identity_fd(work_fd) != work_identity
        ):
            raise BuildError("borrowed native compatibility directory FD changed")
        _assert_absent_names(
            production_fd,
            [context["canonical_final"].name, context["canonical_journal"].name],
            "canonical production entry",
        )
        _assert_absent_names(
            work_fd,
            [context["root_name"], context["journal_name"], staging_name],
            "native compatibility work-root entry",
        )
        staging_fd = mkdir_open_at(work_fd, staging_name, mode=0o700)
        request_digest = sha256_bytes(canonical_json_bytes(request))
        probe_value = {
            "schema": NATIVE_COMPATIBILITY_TERMINAL_SCHEMA,
            "status": "COMPLETE_FROZEN_PROBE_BEFORE_RENAMEAT2_NOREPLACE",
            "scope": NATIVE_COMPATIBILITY_API_SCOPE,
            "decision_id": request["decision_id"],
            "authorization_sha256": request["authorization_sha256"],
            "request_sha256": request_digest,
            "result_accessed": False,
            "signals_sent": False,
            "external_processes_inspected": False,
            "controller_or_resume_executed": False,
        }
        probe_bytes = canonical_json_bytes(probe_value)
        write_bytes_at_exclusive(
            staging_fd, "COMPATIBILITY_PROBE.json", probe_bytes
        )
        os.fchmod(staging_fd, 0o555)
        os.fsync(staging_fd)
        os.fsync(work_fd)
        staging_identity = identity_fd(staging_fd)
        if staging_identity.mode != 0o555:
            raise BuildError("native compatibility staging directory is not 0555")
        rename_impl(work_fd, staging_name, work_fd, context["root_name"])
        os.fsync(work_fd)
        compatibility_root_fd = open_directory_at(work_fd, context["root_name"])
        published_root_identity = identity_fd(compatibility_root_fd)
        if published_root_identity != staging_identity:
            raise BuildError("native compatibility rename changed staging inode")
        if fresh_directory_names(compatibility_root_fd) != [
            "COMPATIBILITY_PROBE.json"
        ]:
            raise BuildError("native compatibility root exact contents mismatch")
        probe_fd = open_regular_at(
            compatibility_root_fd, "COMPATIBILITY_PROBE.json"
        )
        try:
            probe_identity = identity_fd(probe_fd)
            if (
                probe_identity.mode != 0o444
                or probe_identity.nlink != 1
                or read_fd_bytes(probe_fd) != probe_bytes
            ):
                raise BuildError("native compatibility probe bytes/mode mismatch")
        finally:
            os.close(probe_fd)
        compatibility_journal_fd = mkdir_open_at(
            work_fd, context["journal_name"], mode=0o700
        )
        terminal_value = {
            "schema": NATIVE_COMPATIBILITY_TERMINAL_SCHEMA,
            "status": NATIVE_COMPATIBILITY_API_STATUS,
            "scope": NATIVE_COMPATIBILITY_API_SCOPE,
            "decision_id": request["decision_id"],
            "authorization_sha256": request["authorization_sha256"],
            "request_sha256": request_digest,
            "compatibility_root": request["compatibility_root"],
            "compatibility_journal": request["compatibility_journal"],
            "result_accessed": False,
            "signals_sent": False,
            "external_processes_inspected": False,
            "controller_or_resume_executed": False,
        }
        terminal_bytes = canonical_json_bytes(terminal_value)
        terminal_publish_impl(
            compatibility_journal_fd,
            NATIVE_COMPATIBILITY_TERMINAL_NAME,
            terminal_bytes,
        )
        _, _, terminal_identity, observed_terminal = read_canonical_terminal_at(
            compatibility_journal_fd, NATIVE_COMPATIBILITY_TERMINAL_NAME
        )
        if observed_terminal != terminal_bytes:
            raise BuildError("native compatibility terminal bytes mismatch")
        os.fchmod(compatibility_journal_fd, 0o555)
        os.fsync(compatibility_journal_fd)
        os.fsync(work_fd)
        published_journal_identity = identity_fd(compatibility_journal_fd)
        if published_journal_identity.mode != 0o555:
            raise BuildError("native compatibility journal is not frozen 0555")
        if stat_directory_at(work_fd, context["root_name"]) != published_root_identity:
            raise BuildError("native compatibility root named inode changed")
        if stat_directory_at(
            work_fd, context["journal_name"]
        ) != published_journal_identity:
            raise BuildError("native compatibility journal named inode changed")
        _assert_absent_names(
            production_fd,
            [context["canonical_final"].name, context["canonical_journal"].name],
            "canonical production entry after native compatibility probe",
        )
        if (
            not _same_directory_object(
                identity_fd(production_parent_fd), production_identity
            )
            or not _same_directory_object(
                identity_fd(compatibility_work_root_fd), work_identity
            )
        ):
            raise BuildError("borrowed native compatibility FD changed after probe")
        named_production_fd = open_directory_path(context["canonical_parent"])
        named_work_fd = open_directory_path(
            context["compatibility_root"].parent
        )
        try:
            if (
                not _same_directory_object(
                    identity_fd(named_production_fd), production_identity
                )
                or not _same_directory_object(
                    identity_fd(named_work_fd), work_identity
                )
            ):
                raise BuildError("native compatibility named parent continuity failed")
        finally:
            os.close(named_work_fd)
            os.close(named_production_fd)
        return {
            "root_identity": published_root_identity.json(),
            "journal_identity": published_journal_identity.json(),
            "terminal_identity": terminal_identity.json(),
            "request_sha256": request_digest,
        }
    finally:
        for fd in (
            compatibility_journal_fd, compatibility_root_fd, staging_fd,
            work_fd, production_fd,
        ):
            if fd >= 0:
                os.close(fd)


_NATIVE_RENAMEAT2_NOREPLACE = renameat2_noreplace
_NATIVE_TERMINAL_PUBLISH = publish_terminal_linux_otmpfile_noreplace


def execute_scoped_noncanonical_native_compatibility_preflight_v1(
    *,
    request: dict,
    production_parent_fd: int,
    compatibility_work_root_fd: int,
    rename_impl,
    terminal_publish_impl,
) -> dict:
    """Exercise only noncanonical scratch paths with exact real Linux natives.

    Both directory FDs are borrowed: this function never closes them, retains
    them, transfers ownership, or starts background work.  It duplicates them
    synchronously and closes only its duplicates.
    """

    if (
        rename_impl is not _NATIVE_RENAMEAT2_NOREPLACE
        or terminal_publish_impl is not _NATIVE_TERMINAL_PUBLISH
        or renameat2_noreplace is not _NATIVE_RENAMEAT2_NOREPLACE
        or publish_terminal_linux_otmpfile_noreplace
        is not _NATIVE_TERMINAL_PUBLISH
    ):
        raise BuildError("native compatibility requires exact module primitive identities")
    if not sys.platform.startswith("linux"):
        raise BuildError("native compatibility API requires real Linux; no fallback")
    # This creates only an anonymous, immediately closed inode and therefore
    # fails before any named compatibility entry if XFS/O_TMPFILE/procfd-link
    # prerequisites are absent.
    precheck_terminal_publication_linux_xfs(compatibility_work_root_fd)
    _execute_native_compatibility_probe_core(
        request=request,
        production_parent_fd=production_parent_fd,
        compatibility_work_root_fd=compatibility_work_root_fd,
        rename_impl=rename_impl,
        terminal_publish_impl=terminal_publish_impl,
    )
    return {
        "schema": NATIVE_COMPATIBILITY_API_SCHEMA,
        "status": NATIVE_COMPATIBILITY_API_STATUS,
        "scope": NATIVE_COMPATIBILITY_API_SCOPE,
        "decision_id": request["decision_id"],
        "authorization_sha256": request["authorization_sha256"],
        "compatibility_root": request["compatibility_root"],
        "compatibility_journal": request["compatibility_journal"],
        "publication": {
            "renameat2_noreplace": True,
            "otmpfile_procfd_linkat": True,
            "pathname_fallback_used": False,
        },
        "production_guards": {
            "final_root_absent_before_after": True,
            "journal_absent_before_after": True,
            "parent_inode_held": True,
            "canonical_alias_rejected": True,
        },
        "result_accessed": False,
        "signals_sent": False,
        "external_processes_inspected": False,
        "controller_or_resume_executed": False,
    }


def stat_directory_at(root_fd: int, name: str) -> Identity:
    info = os.stat(safe_name(name), dir_fd=root_fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BuildError(f"not a nofollow directory entry: {name}")
    return Identity.from_stat(info)


def path_matches_fd(path: Path, fd: int) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return not stat.S_ISLNK(info.st_mode) and Identity.from_stat(info) == identity_fd(fd)


def same_device_inode(value: Mapping[str, Any], identity: Identity) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("device")) is int
        and type(value.get("inode")) is int
        and value["device"] == identity.device
        and value["inode"] == identity.inode
    )


def publish_staging(handles: StagingHandles, parent_fd: int, parent_path: Path,
                    final_name: str, rename_impl: Callable[[int, str, int, str], None],
                    before_rename_hook: Callable[[], None] | None = None) -> dict[str, Any]:
    verify_frozen(handles.staging_fd)
    if before_rename_hook is not None:
        before_rename_hook()
    if not path_matches_fd(parent_path, parent_fd):
        raise BuildError("authorized final parent path no longer matches held parent FD")
    if stat_directory_at(handles.journal_fd, JOURNAL_NAMES["staging"]) != handles.staging_identity:
        raise BuildError("staging directory entry no longer matches held staging FD")
    rename_impl(handles.journal_fd, JOURNAL_NAMES["staging"], parent_fd, final_name)
    os.fsync(parent_fd)
    os.fsync(handles.journal_fd)
    final_fd = open_directory_at(parent_fd, final_name)
    try:
        if identity_fd(final_fd) != handles.staging_identity:
            raise BuildError("published final inode differs from held staging inode")
        bundle_fd = open_directory_at(final_fd, "bundle")
        private_fd = open_directory_at(final_fd, "private_runtime_site_packages")
        try:
            if identity_fd(bundle_fd) != handles.bundle_identity:
                raise BuildError("published bundle inode differs from held bundle inode")
            if identity_fd(private_fd) != handles.private_identity:
                raise BuildError("published private inode differs from held private inode")
            verify_frozen(final_fd)
            private_structural = inventory_structural(private_fd, include_root=True)
            private_files = files_only_records(private_structural)
            if files_only_digest(private_files) != handles.evidence[
                "files_only_private_root_digest"
            ]:
                raise BuildError("published private files-only digest mismatch")
            if structural_digest(private_structural) != handles.evidence[
                "structural_private_tree_digest"
            ]:
                raise BuildError("published private structural digest mismatch")
            final_structural = inventory_structural(final_fd, include_root=True)
            final_files = files_only_records(final_structural)
            if files_only_digest(final_files) != handles.evidence[
                "files_only_full_root_digest"
            ]:
                raise BuildError("published final files-only digest mismatch")
            if structural_digest(final_structural) != handles.evidence[
                "structural_full_root_digest"
            ]:
                raise BuildError("published final structural digest mismatch")
            support_files = collect_support_file_evidence(
                final_fd, bundle_fd, parent_path / final_name
            )
            if support_files != handles.evidence["support_files"]:
                raise BuildError("published support identity/byte evidence mismatch")
            manifest_fd = open_regular_at(final_fd, "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json")
            try:
                manifest_sha, _ = sha256_fd(manifest_fd)
            finally:
                os.close(manifest_fd)
            if manifest_sha != handles.evidence["runtime_manifest_sha256"]:
                raise BuildError("published runtime manifest SHA mismatch")
        finally:
            os.close(private_fd)
            os.close(bundle_fd)
        return {
            "method": "renameat2(RENAME_NOREPLACE)_DIRFD_RELATIVE",
            "final_root_identity": identity_fd(final_fd).json(),
            "final_inode_equals_staging": True,
            "files_only_full_root_digest": handles.evidence[
                "files_only_full_root_digest"
            ],
            "structural_full_root_digest": handles.evidence[
                "structural_full_root_digest"
            ],
        }
    finally:
        os.close(final_fd)


def receipt_package_binding(auth: Mapping[str, Any]) -> dict[str, Any]:
    package = auth["bindings"]["v10_package"]
    audit = auth["bindings"]["v10_builder_independent_audit"]
    v9_negative = auth["bindings"]["v9_builder_negative_independent_audit"]
    v8_negative = auth["bindings"]["v8_builder_negative_independent_audit"]
    v7_negative = auth["bindings"]["v7_builder_negative_independent_audit"]
    return {
        "v10_builder_sha256": package["builder_sha256"],
        "v10_test_sha256": package["test_sha256"],
        "v10_smoke_sha256": package["smoke_sha256"],
        "v10_smoke_test_sha256": package["smoke_test_sha256"],
        "v10_smoke_bootstrap_sha256": V10_SMOKE_BOOTSTRAP_SHA256,
        "v10_smoke_bootstrap_size_bytes": V10_SMOKE_BOOTSTRAP_SIZE_BYTES,
        "v10_bundle_manifest_sha256": package["bundle_manifest_sha256"],
        "v10_sha256_index_sha256": package["sha256_index_sha256"],
        "v10_prepared_receipt_sha256": package["prepared_receipt_sha256"],
        "v10_independent_audit_receipt_sha256": audit["receipt_sha256"],
        **{
            f"v9_negative_qa_{stem}_sha256": v9_negative[f"{stem}_sha256"]
            for stem in V9_NEGATIVE_QA_FILE_BINDINGS
        },
        **{
            f"v8_negative_qa_{stem}_sha256": v8_negative[f"{stem}_sha256"]
            for stem in V8_NEGATIVE_QA_FILE_BINDINGS
        },
        **{
            f"v7_negative_qa_{stem}_sha256": v7_negative[f"{stem}_sha256"]
            for stem in (
                "bundle_manifest", "log", "output", "receipt", "report",
                "closure", "harness", "sha256_index",
            )
        },
        "v1_audit_receipt_sha256": V1_AUDIT_BINDING["receipt_sha256"],
        "runtime_inventory_sha256": auth["source_inventory"]["inventory_digest"],
    }


def build_pass_receipt(auth: Mapping[str, Any], intent: Mapping[str, Any],
                       intent_sha256: str, publish: Mapping[str, Any]) -> dict[str, Any]:
    core = intent["core"]
    evidence = core["build"]
    source_identity = auth["source_inventory"]["source_root_identity"]
    final_identity = publish["final_root_identity"]
    staging_identity = core["staging_identity"]
    bundle_identity = core["bundle_identity"]
    private_identity = core["private_identity"]
    journal_identity = core["journal_identity"]
    lock_identity = core["lock_identity"]
    parent_identity = core["parent_identity"]
    journal = auth["journal"]
    source_bundle = auth["source_bundle"]
    return {
        "schema": BUILD_PASS_RECEIPT_SCHEMA,
        "status": BUILD_PASS_RECEIPT_STATUS,
        "created_utc": core["receipt_created_utc"],
        "decision_id": auth["decision_id"],
        "authorization": {
            "path": core["authorization_path"],
            "sha256": core["authorization_sha256"],
            "logical_builder_argv": list(core["logical_builder_argv"]),
        },
        "journal": {
            "directory": journal["directory"],
            "directory_device": journal_identity["device"],
            "directory_inode": journal_identity["inode"],
            "parent_path": journal["parent_path"],
            "parent_device": parent_identity["device"],
            "parent_inode": parent_identity["inode"],
            "begin_path": journal["begin"],
            "begin_sha256": core["begin_sha256"],
            "commit_intent_path": journal["intent"],
            "commit_intent_sha256": intent_sha256,
            "terminal_path": journal["terminal"],
            "lock_path": journal["lock"],
            "lock_device": lock_identity["device"],
            "lock_inode": lock_identity["inode"],
            "lock_method": LOCK_METHOD,
            "terminal_publication_method": core[
                "terminal_publication_method"
            ],
            "terminal_canonical_visibility_rule": core[
                "terminal_canonical_visibility_rule"
            ],
        },
        "publication": {
            "method": "renameat2(RENAME_NOREPLACE)_DIRFD_RELATIVE",
            "final_root_path": auth["final_root"],
            "final_root_device": final_identity["device"],
            "final_root_inode": final_identity["inode"],
            "staging_device": staging_identity["device"],
            "staging_inode": staging_identity["inode"],
            "final_inode_equals_staging": publish["final_inode_equals_staging"],
            "files_only_full_root_digest": publish[
                "files_only_full_root_digest"
            ],
            "structural_full_root_digest": publish[
                "structural_full_root_digest"
            ],
        },
        "runtime": {
            "manifest_path": os.fspath(Path(auth["final_root"]) / "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json"),
            "manifest_sha256": evidence["runtime_manifest_sha256"],
            "files_only_runtime_root_digest": evidence[
                "files_only_runtime_root_digest"
            ],
            "private_root_path": os.fspath(Path(auth["final_root"]) / "private_runtime_site_packages"),
            "private_root_device": private_identity["device"],
            "private_root_inode": private_identity["inode"],
            "files_only_private_root_digest": evidence[
                "files_only_private_root_digest"
            ],
            "structural_private_tree_digest": evidence[
                "structural_private_tree_digest"
            ],
            "bundle_root_path": os.fspath(Path(auth["final_root"]) / "bundle"),
            "bundle_root_device": bundle_identity["device"],
            "bundle_root_inode": bundle_identity["inode"],
        },
        "support_files": evidence["support_files"],
        "bound_v8": {
            "bundle_path": source_bundle["path"],
            "prepared_receipt_sha256": source_bundle["receipt_sha256"],
            "bundle_manifest_sha256": source_bundle["bundle_manifest_sha256"],
            "sha256_index_sha256": source_bundle["sha256_index_sha256"],
            "top_level_count": source_bundle["top_level_count"],
            "indexed_count": source_bundle["indexed_count"],
        },
        "source_runtime": {
            "python_path": auth["source_python"],
            "python_sha256": auth["source_python_sha256"],
            "site_packages_path": auth["source_site_packages"],
            "site_packages_device": source_identity["device"],
            "site_packages_inode": source_identity["inode"],
            "source_inventory_digest": auth["source_inventory"]["inventory_digest"],
        },
        "external_record_exclusions": evidence["external_record_exclusions"],
        "package_binding": receipt_package_binding(auth),
        "trusted_launch": dict(core["trusted_launch"]),
        "scope": {
            "result_free_transport_runtime_layout_only": True,
            "result_accessed": False,
            "signals_sent": False,
            "processes_inspected": False,
            "controller_or_outer_main_executed": False,
            "deployment_or_resume_executed": False,
            "smoke_executed": False,
            "linux_integration": core["linux_integration"],
        },
    }


def validate_build_pass_receipt_schema(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != BUILD_RECEIPT_TOP_KEYS:
        raise BuildError("build PASS receipt top-level schema mismatch")
    if value["schema"] != BUILD_PASS_RECEIPT_SCHEMA or value["status"] != BUILD_PASS_RECEIPT_STATUS:
        raise BuildError("build PASS receipt schema/status mismatch")
    for key in ("created_utc", "decision_id"):
        exact_string(value[key], f"build_receipt.{key}")
    if UTC_RE.fullmatch(value["created_utc"]) is None:
        raise BuildError("build PASS receipt created_utc is not canonical UTC")
    for label, keys in BUILD_RECEIPT_NESTED_KEYS.items():
        item = value[label]
        if not isinstance(item, dict) or set(item) != keys:
            raise BuildError(f"build PASS receipt {label} schema mismatch")
    if not isinstance(value["support_files"], dict) or set(value["support_files"]) != set(SUPPORT_FILES):
        raise BuildError("build PASS receipt support_files exact set mismatch")
    for name in SUPPORT_FILES:
        item = value["support_files"][name]
        if not isinstance(item, dict) or set(item) != SUPPORT_FILE_RECEIPT_KEYS:
            raise BuildError(f"build PASS receipt support schema mismatch: {name}")
        if exact_string(item["path"], f"build_receipt.support_files.{name}.path") != os.fspath(
            Path(value["publication"]["final_root_path"]) / name
        ):
            raise BuildError(f"build PASS receipt support path mismatch: {name}")
        exact_sha256(item["sha256"], f"build_receipt.support_files.{name}.sha256")
        for key in ("device", "inode", "size_bytes"):
            exact_integer(item[key], f"build_receipt.support_files.{name}.{key}")
    if value["external_record_exclusions"] != external_record_exclusion_evidence():
        raise BuildError("build PASS receipt exact7 evidence mismatch")
    for container, keys in (
        (value["authorization"], ("sha256",)),
        (value["journal"], ("begin_sha256", "commit_intent_sha256")),
        (value["publication"], (
            "files_only_full_root_digest", "structural_full_root_digest",
        )),
        (value["runtime"], (
            "manifest_sha256", "files_only_runtime_root_digest",
            "files_only_private_root_digest", "structural_private_tree_digest",
        )),
        (value["bound_v8"], ("prepared_receipt_sha256", "bundle_manifest_sha256", "sha256_index_sha256")),
        (value["source_runtime"], ("python_sha256", "source_inventory_digest")),
        (value["package_binding"], tuple(
            key for key in value["package_binding"]
            if key != "v10_smoke_bootstrap_size_bytes"
        )),
    ):
        for key in keys:
            exact_sha256(container[key], f"build_receipt.{key}")
    if (
        value["package_binding"]["v10_smoke_bootstrap_sha256"]
        != V10_SMOKE_BOOTSTRAP_SHA256
        or exact_integer(
            value["package_binding"]["v10_smoke_bootstrap_size_bytes"],
            "build_receipt.package_binding.v10_smoke_bootstrap_size_bytes",
        ) != V10_SMOKE_BOOTSTRAP_SIZE_BYTES
    ):
        raise BuildError("build PASS receipt held smoke bootstrap mismatch")
    for container, keys in (
        (value["journal"], (
            "directory_device", "directory_inode", "parent_device", "parent_inode",
            "lock_device", "lock_inode",
        )),
        (value["publication"], ("final_root_device", "final_root_inode", "staging_device", "staging_inode")),
        (value["runtime"], (
            "private_root_device", "private_root_inode",
            "bundle_root_device", "bundle_root_inode",
        )),
        (value["bound_v8"], ("top_level_count", "indexed_count")),
        (value["source_runtime"], ("site_packages_device", "site_packages_inode")),
    ):
        for key in keys:
            exact_integer(container[key], f"build_receipt.{key}")
    expected_scope = {
        "result_free_transport_runtime_layout_only": True,
        "result_accessed": False,
        "signals_sent": False,
        "processes_inspected": False,
        "controller_or_outer_main_executed": False,
        "deployment_or_resume_executed": False,
        "smoke_executed": False,
    }
    for key, expected in expected_scope.items():
        if exact_boolean(value["scope"][key], f"build_receipt.scope.{key}") is not expected:
            raise BuildError("build PASS receipt scope mismatch")
    validate_trusted_launch_binding_shape(
        value["trusted_launch"],
        {
            "builder_path": value["trusted_launch"]["builder_original_evidence_path"],
            "builder_sha256": value["package_binding"]["v10_builder_sha256"],
        },
        value["source_runtime"]["python_sha256"],
    )
    if value["scope"]["linux_integration"] not in {
        "PASS_LINUX_RENAMEAT2_NOREPLACE", "NOT_RUN_NON_LINUX",
        "SYNTHETIC_RENAME_INJECTED_NOT_PRODUCTION",
    }:
        raise BuildError("build PASS receipt Linux integration status mismatch")
    if value["publication"]["method"] != "renameat2(RENAME_NOREPLACE)_DIRFD_RELATIVE":
        raise BuildError("build PASS receipt publication method mismatch")
    if value["journal"]["lock_method"] != LOCK_METHOD:
        raise BuildError("build PASS receipt lock method mismatch")
    expected_terminal_method = (
        PRODUCTION_TERMINAL_PUBLICATION_METHOD
        if value["scope"]["linux_integration"]
        == "PASS_LINUX_RENAMEAT2_NOREPLACE"
        else SYNTHETIC_TERMINAL_PUBLICATION_METHOD
    )
    if (
        value["journal"]["terminal_publication_method"]
        != expected_terminal_method
        or value["journal"]["terminal_canonical_visibility_rule"]
        != TERMINAL_CANONICAL_VISIBILITY_RULE
    ):
        raise BuildError("build PASS receipt terminal publication contract mismatch")
    journal_directory = Path(exact_string(
        value["journal"]["directory"], "build_receipt.journal.directory"
    ))
    if Path(exact_string(
        value["journal"]["lock_path"], "build_receipt.journal.lock_path"
    )) != journal_directory / JOURNAL_NAMES["lock"]:
        raise BuildError("build PASS receipt lock path mismatch")
    if exact_boolean(value["publication"]["final_inode_equals_staging"],
                     "build_receipt.final_inode_equals_staging") is not True:
        raise BuildError("build PASS receipt final/staging identity mismatch")
    if (value["publication"]["final_root_device"], value["publication"]["final_root_inode"]) != (
        value["publication"]["staging_device"], value["publication"]["staging_inode"]
    ):
        raise BuildError("build PASS receipt final inode is not staging inode")
    if value["runtime"]["files_only_private_root_digest"] != value["runtime"]["files_only_runtime_root_digest"]:
        raise BuildError("build PASS receipt private/runtime files-only digest mismatch")
    final_root = Path(exact_string(
        value["publication"]["final_root_path"], "build_receipt.publication.final_root_path"
    ))
    if exact_string(value["runtime"]["private_root_path"], "build_receipt.runtime.private_root_path") != os.fspath(
        final_root / "private_runtime_site_packages"
    ):
        raise BuildError("build PASS receipt private root path mismatch")
    if exact_string(value["runtime"]["bundle_root_path"], "build_receipt.runtime.bundle_root_path") != os.fspath(
        final_root / "bundle"
    ):
        raise BuildError("build PASS receipt bundle root path mismatch")
    source_bundle_path = Path(exact_string(
        value["bound_v8"]["bundle_path"], "build_receipt.bound_v8.bundle_path"
    ))
    if (
        not source_bundle_path.is_absolute()
        or source_bundle_path.name != V8_BINDING["directory_name"]
        or value["bound_v8"]["prepared_receipt_sha256"]
        != V8_BINDING["receipt_sha256"]
        or value["bound_v8"]["bundle_manifest_sha256"]
        != V8_BINDING["bundle_manifest_sha256"]
        or value["bound_v8"]["sha256_index_sha256"]
        != V8_BINDING["sha256_index_sha256"]
        or value["bound_v8"]["top_level_count"]
        != V8_BINDING["top_level_count"]
        or value["bound_v8"]["indexed_count"]
        != V8_BINDING["indexed_count"]
    ):
        raise BuildError("build PASS receipt frozen v8 binding mismatch")
    if any("terminal_sha256" in key or "receipt_sha256" in key
           for key in value["journal"]):
        raise BuildError("build PASS receipt contains a self-reference field")


def make_intent(auth_sha: str, auth: Mapping[str, Any], handles: StagingHandles,
                *, authorization_path: Path, begin_sha256: str,
                parent_identity: Identity, journal_identity: Identity,
                lock_identity: Identity,
                linux_integration: str,
                terminal_publication_method: str,
                observed_builder_argv: Sequence[str]) -> dict[str, Any]:
    core = {
        "authorization_sha256": auth_sha,
        "authorization_path": os.fspath(authorization_path),
        "decision_id": auth["decision_id"],
        "final_root": auth["final_root"],
        "journal_directory": auth["journal"]["directory"],
        "receipt_created_utc": utc_now(),
        "begin_sha256": begin_sha256,
        "parent_identity": parent_identity.json(),
        "journal_identity": journal_identity.json(),
        "lock_identity": lock_identity.json(),
        "logical_builder_argv": list(observed_builder_argv),
        "trusted_launch": dict(auth["trusted_launch"]),
        "trust_bindings": {
            name: auth["bindings"][name]
            for name in (
                "v10_package", "v10_builder_independent_audit",
                "v9_builder_negative_independent_audit",
                "v8_builder_negative_independent_audit",
                "v7_builder_negative_independent_audit",
            )
        },
        "staging_identity": handles.staging_identity.json(),
        "bundle_identity": handles.bundle_identity.json(),
        "private_identity": handles.private_identity.json(),
        "linux_integration": linux_integration,
        "terminal_publication_method": terminal_publication_method,
        "terminal_canonical_visibility_rule": TERMINAL_CANONICAL_VISIBILITY_RULE,
        "build": handles.evidence,
    }
    core_digest = sha256_bytes(canonical_json_bytes(core))
    return {
        "schema": INTENT_SCHEMA,
        "status": INTENT_STATUS,
        "core": core,
        "core_digest": core_digest,
        "recovery_rule": INTENT_RECOVERY_RULE,
    }


def fail_terminal(auth_sha: str, decision_id: str, phase: str, error: BaseException,
                  staging_identity: Identity | None,
                  observed_staging_identity: Identity | None = None,
                  *, fixed_root_path_present: bool = False,
                  fixed_root_inode_matches_intent: bool = False,
                  fixed_root_exactly_validated: bool = False,
                  terminal_publication_method: str) -> dict[str, Any]:
    return {
        "schema": "historical_200k_fixed10k_result_free_transport_terminal_v10",
        "status": "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT",
        "created_utc": utc_now(),
        "authorization_sha256": auth_sha,
        "decision_id": decision_id,
        "phase": phase,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "staging_preservation": {
            "policy": "PRESERVE_EXACT_INODE_FOR_FAILURE_EVIDENCE_NO_RECURSIVE_DELETE",
            "expected_identity": staging_identity.json() if staging_identity else None,
            "observed_entry_identity": (
                observed_staging_identity.json() if observed_staging_identity else None
            ),
            "entry_matches_expected": (
                staging_identity == observed_staging_identity
                if staging_identity is not None and observed_staging_identity is not None
                else None
            ),
        },
        "fixed_root_path_present": fixed_root_path_present,
        "fixed_root_published": fixed_root_inode_matches_intent,
        "fixed_root_exactly_validated": fixed_root_exactly_validated,
        "terminal_publication_method": terminal_publication_method,
        "terminal_canonical_visibility_rule": TERMINAL_CANONICAL_VISIBILITY_RULE,
        "result_accessed": False,
        "signals_sent": False,
    }


def validate_fail_terminal_schema(
    value: Any,
    *,
    authorization_sha256: str,
    decision_id: str,
    terminal_publication_method: str,
) -> None:
    if not isinstance(value, dict) or set(value) != FAIL_TERMINAL_TOP_KEYS:
        raise BuildError("FAIL terminal exact schema mismatch")
    if (
        value["schema"]
        != "historical_200k_fixed10k_result_free_transport_terminal_v10"
        or value["status"] != "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
        or value["authorization_sha256"] != authorization_sha256
        or value["decision_id"] != decision_id
        or value["terminal_publication_method"]
        != terminal_publication_method
        or value["terminal_canonical_visibility_rule"]
        != TERMINAL_CANONICAL_VISIBILITY_RULE
        or value["result_accessed"] is not False
        or value["signals_sent"] is not False
    ):
        raise BuildError("FAIL terminal binding/status mismatch")
    if UTC_RE.fullmatch(exact_string(
        value["created_utc"], "fail_terminal.created_utc"
    )) is None:
        raise BuildError("FAIL terminal created_utc is invalid")
    for key in ("phase", "error_type", "error_message"):
        exact_string(value[key], f"fail_terminal.{key}")
    for key in (
        "fixed_root_path_present", "fixed_root_published",
        "fixed_root_exactly_validated",
    ):
        exact_boolean(value[key], f"fail_terminal.{key}")
    preservation = value["staging_preservation"]
    if not isinstance(preservation, dict) or set(preservation) != {
        "policy", "expected_identity", "observed_entry_identity",
        "entry_matches_expected",
    }:
        raise BuildError("FAIL terminal staging_preservation schema mismatch")
    if preservation["policy"] != (
        "PRESERVE_EXACT_INODE_FOR_FAILURE_EVIDENCE_NO_RECURSIVE_DELETE"
    ):
        raise BuildError("FAIL terminal staging policy mismatch")
    for key in ("expected_identity", "observed_entry_identity"):
        item = preservation[key]
        if item is not None:
            Identity.from_json(item)
    if preservation["entry_matches_expected"] is not None:
        exact_boolean(
            preservation["entry_matches_expected"],
            "fail_terminal.entry_matches_expected",
        )


def file_exists_at(fd: int, name: str) -> bool:
    try:
        os.stat(safe_name(name), dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def acquire_transaction_lock(
    journal_fd: int,
    *,
    after_open_before_flock_hook: Callable[[int, Identity], None] | None = None,
) -> tuple[int | None, Identity, bool]:
    """Open-or-create the one journal lock inode and try to become its owner.

    O_EXCL is used only to record truthful creation provenance, never to choose
    the owner.  A contender that observes an existing path opens that same
    inode without O_CREAT, and flock alone selects the transaction owner.  A
    creator that loses flock truthfully reports that it created LOCK.
    """
    common_flags = (
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    lock_created_by_this_attempt = False
    while True:
        try:
            lock_fd = os.open(
                JOURNAL_NAMES["lock"],
                common_flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=journal_fd,
            )
            lock_created_by_this_attempt = True
            break
        except FileExistsError:
            try:
                lock_fd = os.open(
                    JOURNAL_NAMES["lock"], common_flags, dir_fd=journal_fd
                )
                break
            except FileNotFoundError:
                # A hostile/racing unlink is never expected, but retrying the
                # provenance decision keeps the mutation claim exact.
                continue
    try:
        identity = identity_fd(lock_fd)
        if (
            identity.mode != 0o600
            or identity.nlink != 1
            or not stat.S_ISREG(os.fstat(lock_fd).st_mode)
        ):
            raise BuildError("journal LOCK exact regular identity mismatch")
        current = os.stat(
            JOURNAL_NAMES["lock"], dir_fd=journal_fd, follow_symlinks=False
        )
        if Identity.from_stat(current) != identity:
            raise BuildError("journal LOCK path differs from opened lock FD")
        if after_open_before_flock_hook is not None:
            after_open_before_flock_hook(lock_fd, identity)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return None, identity, lock_created_by_this_attempt
        validate_held_transaction_lock(journal_fd, lock_fd, identity)
        os.fsync(lock_fd)
        os.fsync(journal_fd)
        return lock_fd, identity, lock_created_by_this_attempt
    except BaseException:
        try:
            os.close(lock_fd)
        except OSError:
            pass
        raise


def validate_held_transaction_lock(
    journal_fd: int, lock_fd: int, expected: Identity
) -> None:
    if identity_fd(lock_fd) != expected:
        raise BuildError("held journal LOCK identity changed")
    current = os.stat(
        JOURNAL_NAMES["lock"], dir_fd=journal_fd, follow_symlinks=False
    )
    if Identity.from_stat(current) != expected:
        raise BuildError("journal LOCK path no longer names held lock inode")


def verify_final_against_intent(
    parent_fd: int,
    final_name: str,
    intent: Mapping[str, Any],
    auth: Mapping[str, Any],
    snapshot: DiscoverySnapshot,
) -> dict[str, Any]:
    core = intent["core"]
    expected_root = Identity.from_json(core["staging_identity"])
    final_fd = open_directory_at(parent_fd, final_name)
    try:
        if identity_fd(final_fd) != expected_root:
            raise BuildError("recovery final inode differs from commit intent")
        bundle_identity, private_identity, rebuilt = reconstruct_candidate_evidence(
            final_fd, snapshot, auth["source_bundle"], Path(auth["final_root"])
        )
        if bundle_identity != Identity.from_json(core["bundle_identity"]):
            raise BuildError("recovery bundle inode differs from commit intent")
        if private_identity != Identity.from_json(core["private_identity"]):
            raise BuildError("recovery private inode differs from commit intent")
        if rebuilt != core["build"]:
            raise BuildError(
                "recovery rebuilt authorization evidence differs from commit intent"
            )
        return {
            "method": "renameat2(RENAME_NOREPLACE)_DIRFD_RELATIVE",
            "final_root_identity": expected_root.json(),
            "final_inode_equals_staging": True,
            "files_only_full_root_digest": rebuilt[
                "files_only_full_root_digest"
            ],
            "structural_full_root_digest": rebuilt[
                "structural_full_root_digest"
            ],
        }
    finally:
        os.close(final_fd)


def inspect_fixed_root_publication(
    parent_fd: int,
    final_name: str,
    intent: Mapping[str, Any] | None,
    auth: Mapping[str, Any],
    snapshot: DiscoverySnapshot | None,
) -> tuple[bool, bool, bool]:
    try:
        info = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False, False, False
    path_present = True
    inode_matches = False
    exactly_validated = False
    if stat.S_ISDIR(info.st_mode) and isinstance(intent, dict):
        core = intent.get("core")
        if isinstance(core, dict):
            try:
                expected = Identity.from_json(core.get("staging_identity", {}))
            except BuildError:
                expected = None
            if expected is not None:
                inode_matches = (
                    info.st_dev == expected.device and info.st_ino == expected.inode
                )
        if inode_matches and snapshot is not None:
            try:
                verify_final_against_intent(
                    parent_fd, final_name, intent, auth, snapshot
                )
            except BaseException:
                exactly_validated = False
            else:
                exactly_validated = True
    return path_present, inode_matches, exactly_validated


def validate_begin_record(
    begin: Any,
    *,
    auth: Mapping[str, Any],
    auth_sha: str,
    authorization_path: Path,
    parent_fd: int,
    lock_identity: Identity,
    terminal_publication_method: str,
) -> None:
    keys = {
        "schema", "status", "created_utc", "authorization_path",
        "authorization_sha256", "decision_id", "final_root",
        "parent_identity", "lock_identity", "lock_method",
        "terminal_publication_method", "terminal_canonical_visibility_rule",
        "v10_package", "v10_builder_audit", "v9_negative_audit",
        "v8_negative_audit",
        "v7_negative_audit", "v1_audit",
        "result_accessed", "signals_sent",
    }
    if type(begin) is not dict or set(begin) != keys:
        raise BuildError("BEGIN exact key set mismatch")
    if begin["schema"] != BEGIN_SCHEMA or begin["status"] != BEGIN_STATUS:
        raise BuildError("BEGIN schema/status mismatch")
    created = exact_string(begin["created_utc"], "BEGIN.created_utc")
    if UTC_RE.fullmatch(created) is None:
        raise BuildError("BEGIN created_utc is not canonical UTC")
    if (
        begin["authorization_path"] != os.fspath(authorization_path)
        or begin["authorization_sha256"] != auth_sha
        or begin["decision_id"] != auth["decision_id"]
        or begin["final_root"] != auth["final_root"]
        or begin["lock_method"] != LOCK_METHOD
        or begin["terminal_publication_method"] != terminal_publication_method
        or begin["terminal_canonical_visibility_rule"]
        != TERMINAL_CANONICAL_VISIBILITY_RULE
        or begin["v10_package"] != auth["bindings"]["v10_package"]
        or begin["v10_builder_audit"]
        != auth["bindings"]["v10_builder_independent_audit"]
        or begin["v9_negative_audit"]
        != auth["bindings"]["v9_builder_negative_independent_audit"]
        or begin["v8_negative_audit"]
        != auth["bindings"]["v8_builder_negative_independent_audit"]
        or begin["v7_negative_audit"]
        != auth["bindings"]["v7_builder_negative_independent_audit"]
        or begin["v1_audit"]
        != auth["bindings"]["v1_builder_independent_audit"]
        or exact_boolean(begin["result_accessed"], "BEGIN.result_accessed")
        is not False
        or exact_boolean(begin["signals_sent"], "BEGIN.signals_sent") is not False
        or Identity.from_json(begin["lock_identity"]) != lock_identity
        or not same_device_inode(begin["parent_identity"], identity_fd(parent_fd))
    ):
        raise BuildError("BEGIN exact authorization/constant binding mismatch")


def validate_intent_record(
    intent: Any,
    *,
    auth: Mapping[str, Any],
    auth_sha: str,
    authorization_path: Path,
    begin_sha: str,
    parent_fd: int,
    journal_fd: int,
    lock_identity: Identity,
    logical_builder_argv: Sequence[str],
    linux_integration: str,
    terminal_publication_method: str,
) -> dict[str, Any]:
    top_keys = {"schema", "status", "core", "core_digest", "recovery_rule"}
    core_keys = {
        "authorization_sha256", "authorization_path", "decision_id",
        "final_root", "journal_directory", "receipt_created_utc",
        "begin_sha256", "parent_identity", "journal_identity",
        "lock_identity", "logical_builder_argv", "trusted_launch",
        "trust_bindings",
        "staging_identity", "bundle_identity", "private_identity",
        "linux_integration", "terminal_publication_method",
        "terminal_canonical_visibility_rule", "build",
    }
    if type(intent) is not dict or set(intent) != top_keys:
        raise BuildError("COMMIT_INTENT exact top-level key set mismatch")
    core = intent.get("core")
    if type(core) is not dict or set(core) != core_keys:
        raise BuildError("COMMIT_INTENT exact core key set mismatch")
    receipt_created = exact_string(
        core["receipt_created_utc"], "COMMIT_INTENT.core.receipt_created_utc"
    )
    if UTC_RE.fullmatch(receipt_created) is None:
        raise BuildError("COMMIT_INTENT receipt_created_utc is not canonical UTC")
    if (
        intent["schema"] != INTENT_SCHEMA
        or intent["status"] != INTENT_STATUS
        or intent["recovery_rule"] != INTENT_RECOVERY_RULE
        or core["authorization_sha256"] != auth_sha
        or core["authorization_path"] != os.fspath(authorization_path)
        or core["decision_id"] != auth["decision_id"]
        or core["final_root"] != auth["final_root"]
        or core["journal_directory"] != auth["journal"]["directory"]
        or core["begin_sha256"] != begin_sha
        or core["logical_builder_argv"] != list(logical_builder_argv)
        or core["trusted_launch"] != auth["trusted_launch"]
        or core["trust_bindings"] != {
            name: auth["bindings"][name]
            for name in (
                "v10_package", "v10_builder_independent_audit",
                "v9_builder_negative_independent_audit",
                "v8_builder_negative_independent_audit",
                "v7_builder_negative_independent_audit",
            )
        }
        or core["linux_integration"] != linux_integration
        or core["terminal_publication_method"] != terminal_publication_method
        or core["terminal_canonical_visibility_rule"]
        != TERMINAL_CANONICAL_VISIBILITY_RULE
        or not same_device_inode(core["parent_identity"], identity_fd(parent_fd))
        or not same_device_inode(core["journal_identity"], identity_fd(journal_fd))
        or Identity.from_json(core["lock_identity"]) != lock_identity
        or sha256_bytes(canonical_json_bytes(core)) != intent["core_digest"]
    ):
        raise BuildError("COMMIT_INTENT exact authorization/constant binding mismatch")
    exact_sha256(intent["core_digest"], "COMMIT_INTENT.core_digest")
    Identity.from_json(core["staging_identity"])
    Identity.from_json(core["bundle_identity"])
    Identity.from_json(core["private_identity"])
    if type(core["build"]) is not dict:
        raise BuildError("COMMIT_INTENT build evidence must be an object")
    return core


def recover_existing(
    auth: Mapping[str, Any],
    auth_sha: str,
    journal_fd: int,
    parent_fd: int,
    parent_path: Path,
    final_name: str,
    rename_impl: Callable[[int, str, int, str], None],
    lock_fd: int,
    lock_identity: Identity,
    authorization_path: Path,
    observed_argv: Sequence[str],
    linux_integration: str,
    terminal_publish_impl: Callable[..., dict[str, Any]],
    terminal_publication_method: str,
    enforce_fixed: bool,
    trust_lease: ProductionTrustLease | None,
    synthetic_path_lease: "SyntheticProductionPathLease | None" = None,
    terminal_mid_write_hook: Callable[[], None] | None = None,
    terminal_after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
    fail_terminal_mid_write_hook: Callable[[], None] | None = None,
    fail_terminal_after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Recover only after rebuilding every scientific/layout fact from auth."""
    snapshot: DiscoverySnapshot | None = None
    source_bundle_fd = -1
    expected_staging: Identity | None = None
    intent_for_failure: Mapping[str, Any] | None = None
    exact_pass_evidence_rebuilt = False
    existing_terminal_is_fail = False
    existing_fail_conflicts_with_valid_pass = False

    def revalidate_synthetic_separation() -> None:
        if synthetic_path_lease is not None:
            synthetic_path_lease.revalidate()

    def revalidate_terminal_continuity() -> None:
        """Last named-path/held-inode trust check through terminal durability."""

        revalidate_synthetic_separation()
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)

    try:
        revalidate_synthetic_separation()
        validate_held_transaction_lock(journal_fd, lock_fd, lock_identity)
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)
        begin, begin_sha, _ = read_json_at(journal_fd, JOURNAL_NAMES["begin"])
        validate_begin_record(
            begin,
            auth=auth,
            auth_sha=auth_sha,
            authorization_path=authorization_path,
            parent_fd=parent_fd,
            lock_identity=lock_identity,
            terminal_publication_method=terminal_publication_method,
        )
        if not file_exists_at(journal_fd, JOURNAL_NAMES["intent"]):
            if file_exists_at(journal_fd, JOURNAL_NAMES["terminal"]):
                terminal, _, _, _ = read_canonical_terminal_at(
                    journal_fd, JOURNAL_NAMES["terminal"]
                )
                if terminal.get("status") == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT":
                    validate_fail_terminal_schema(
                        terminal,
                        authorization_sha256=auth_sha,
                        decision_id=auth["decision_id"],
                        terminal_publication_method=terminal_publication_method,
                    )
                    revalidate_synthetic_separation()
                    os.fsync(journal_fd)
                    os.fsync(parent_fd)
                    revalidate_terminal_continuity()
                    raise BuildError("existing transaction is terminal FAIL before intent")
                raise BuildError("terminal exists without durable COMMIT_INTENT")
            raise BuildError(
                "interrupted before durable COMMIT_INTENT; staging preserved"
            )

        intent, intent_sha, _ = read_json_at(
            journal_fd, JOURNAL_NAMES["intent"]
        )
        intent_for_failure = intent
        core = validate_intent_record(
            intent,
            auth=auth,
            auth_sha=auth_sha,
            authorization_path=authorization_path,
            begin_sha=begin_sha,
            parent_fd=parent_fd,
            journal_fd=journal_fd,
            lock_identity=lock_identity,
            logical_builder_argv=observed_argv,
            linux_integration=linux_integration,
            terminal_publication_method=terminal_publication_method,
        )
        expected_staging = Identity.from_json(core["staging_identity"])
        Identity.from_json(core["bundle_identity"])
        Identity.from_json(core["private_identity"])

        existing_terminal: dict[str, Any] | None = None
        existing_terminal_sha: str | None = None
        existing_terminal_bytes: bytes | None = None
        if file_exists_at(journal_fd, JOURNAL_NAMES["terminal"]):
            (
                existing_terminal,
                existing_terminal_sha,
                _,
                existing_terminal_bytes,
            ) = read_canonical_terminal_at(
                journal_fd, JOURNAL_NAMES["terminal"]
            )
            if existing_terminal.get("status") == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT":
                validate_fail_terminal_schema(
                    existing_terminal,
                    authorization_sha256=auth_sha,
                    decision_id=auth["decision_id"],
                    terminal_publication_method=terminal_publication_method,
                )
                existing_terminal_is_fail = True
            elif existing_terminal.get("status") != BUILD_PASS_RECEIPT_STATUS:
                raise BuildError("existing terminal schema/status is invalid")

        snapshot = discover_source(Path(auth["source_site_packages"]))
        if enforce_fixed and snapshot.root_identity != trust_lease.directory_identity(
            auth["source_site_packages"]
        ):
            raise BuildError("recovery source site-packages differs from held lease")
        if snapshot.inventory() != auth["source_inventory"]:
            raise BuildError("recovery source inventory differs from authorization")
        revalidate_source(snapshot)
        source_bundle_path = Path(auth["source_bundle"]["path"])
        source_bundle_fd = (
            trust_lease.dup_directory(source_bundle_path)
            if enforce_fixed else open_directory_path(source_bundle_path)
        )
        source_bundle_identity = identity_fd(source_bundle_fd)
        source_bundle_before = verify_bundle(
            source_bundle_fd, auth["source_bundle"]
        )

        final_exists = file_exists_at(parent_fd, final_name)
        staging_exists = file_exists_at(journal_fd, JOURNAL_NAMES["staging"])
        if final_exists and staging_exists:
            raise BuildError("both fixed ROOT and staging exist during recovery")
        if final_exists:
            publish = verify_final_against_intent(
                parent_fd, final_name, intent, auth, snapshot
            )
            if existing_terminal_is_fail:
                existing_fail_conflicts_with_valid_pass = True
                raise BuildError(
                    "existing FAIL terminal conflicts with independently rebuilt "
                    "valid final PASS evidence"
                )
        elif staging_exists:
            staging_fd = open_directory_at(journal_fd, JOURNAL_NAMES["staging"])
            try:
                if identity_fd(staging_fd) != expected_staging:
                    raise BuildError("recovery staging inode differs from intent")
                bundle_identity, private_identity, rebuilt = (
                    reconstruct_candidate_evidence(
                        staging_fd,
                        snapshot,
                        auth["source_bundle"],
                        Path(auth["final_root"]),
                    )
                )
                if bundle_identity != Identity.from_json(core["bundle_identity"]):
                    raise BuildError("recovery staging bundle inode mismatch")
                if private_identity != Identity.from_json(core["private_identity"]):
                    raise BuildError("recovery staging private inode mismatch")
                if rebuilt != core["build"]:
                    raise BuildError(
                        "recovery independently rebuilt evidence differs from intent"
                    )
                if existing_terminal_is_fail:
                    existing_fail_conflicts_with_valid_pass = True
                    raise BuildError(
                        "existing FAIL terminal conflicts with independently rebuilt "
                        "valid staging PASS evidence"
                    )
                bundle_fd = open_directory_at(staging_fd, "bundle")
                private_fd = open_directory_at(
                    staging_fd, "private_runtime_site_packages"
                )
                handles = StagingHandles(
                    os.dup(journal_fd), os.dup(staging_fd), bundle_fd, private_fd,
                    expected_staging, bundle_identity, private_identity, rebuilt,
                )
            finally:
                os.close(staging_fd)
            try:
                if enforce_fixed:
                    revalidate_production_trust(auth, trust_lease)
                revalidate_synthetic_separation()
                publish = publish_staging(
                    handles, parent_fd, parent_path, final_name, rename_impl
                )
            finally:
                handles.close()
        else:
            raise BuildError(
                "commit intent exists but both staging and fixed ROOT are absent"
            )

        if (
            identity_fd(source_bundle_fd) != source_bundle_identity
            or not path_matches_fd(source_bundle_path, source_bundle_fd)
            or verify_bundle(source_bundle_fd, auth["source_bundle"])
            != source_bundle_before
        ):
            raise BuildError("authorized source V8 bundle changed during recovery")
        revalidate_source(snapshot)

        receipt = build_pass_receipt(auth, intent, intent_sha, publish)
        validate_build_pass_receipt_schema(receipt)
        validate_held_transaction_lock(journal_fd, lock_fd, lock_identity)
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)
        receipt_bytes = canonical_json_bytes(receipt)
        exact_pass_evidence_rebuilt = True
        if existing_terminal is not None:
            if (
                existing_terminal != receipt
                or existing_terminal_bytes != receipt_bytes
            ):
                raise BuildError(
                    "existing PASS terminal bytes differ from rebuilt final evidence"
                )
            revalidate_synthetic_separation()
            os.fsync(journal_fd)
            os.fsync(parent_fd)
            revalidate_terminal_continuity()
            return {
                "status": "ALREADY_TERMINAL_PASS",
                "intent_sha256": intent_sha,
                "terminal_sha256": existing_terminal_sha,
                "receipt": existing_terminal,
            }
        revalidate_synthetic_separation()
        terminal_evidence = terminal_publish_impl(
            journal_fd,
            JOURNAL_NAMES["terminal"],
            receipt_bytes,
            mid_write_hook=terminal_mid_write_hook,
            after_link_before_dir_fsync_hook=(
                terminal_after_link_before_dir_fsync_hook
            ),
        )
        if terminal_evidence.get("method") != terminal_publication_method:
            raise BuildError("terminal publisher method evidence mismatch")
        os.fsync(journal_fd)
        os.fsync(parent_fd)
        revalidate_terminal_continuity()
        return {
            "status": "RECOVERED_PASS_FROM_DURABLE_COMMIT_INTENT",
            "intent_sha256": intent_sha,
            "terminal_sha256": sha256_bytes(receipt_bytes),
            "receipt": receipt,
        }
    except BaseException as exc:
        if (
            existing_terminal_is_fail
            and not existing_fail_conflicts_with_valid_pass
            and file_exists_at(journal_fd, JOURNAL_NAMES["terminal"])
        ):
            # A complete strict FAIL that survived a crash after link but
            # before directory fsync becomes durable on the next recovery.
            # A FAIL conflicting with independently valid PASS evidence is
            # deliberately excluded and remains rejected as forged/conflicting.
            revalidate_synthetic_separation()
            os.fsync(journal_fd)
            os.fsync(parent_fd)
            revalidate_terminal_continuity()
        if (
            not exact_pass_evidence_rebuilt
            and not file_exists_at(journal_fd, JOURNAL_NAMES["terminal"])
        ):
            observed = None
            if file_exists_at(journal_fd, JOURNAL_NAMES["staging"]):
                observed = stat_directory_at(
                    journal_fd, JOURNAL_NAMES["staging"]
                )
                if expected_staging is None:
                    expected_staging = observed
            root_present, root_inode_match, root_exact = inspect_fixed_root_publication(
                parent_fd,
                final_name,
                intent_for_failure,
                auth,
                snapshot,
            )
            fail_value = fail_terminal(
                auth_sha,
                auth["decision_id"],
                "RECOVERY_INDEPENDENT_REVALIDATION",
                exc,
                expected_staging,
                observed,
                fixed_root_path_present=root_present,
                fixed_root_inode_matches_intent=root_inode_match,
                fixed_root_exactly_validated=root_exact,
                terminal_publication_method=terminal_publication_method,
            )
            validate_fail_terminal_schema(
                fail_value,
                authorization_sha256=auth_sha,
                decision_id=auth["decision_id"],
                terminal_publication_method=terminal_publication_method,
            )
            if enforce_fixed:
                revalidate_production_trust(auth, trust_lease)
            revalidate_synthetic_separation()
            terminal_evidence = terminal_publish_impl(
                journal_fd,
                JOURNAL_NAMES["terminal"],
                canonical_json_bytes(fail_value),
                mid_write_hook=fail_terminal_mid_write_hook,
                after_link_before_dir_fsync_hook=(
                    fail_terminal_after_link_before_dir_fsync_hook
                ),
            )
            if terminal_evidence.get("method") != terminal_publication_method:
                raise BuildError("FAIL terminal publisher method evidence mismatch")
            os.fsync(journal_fd)
            os.fsync(parent_fd)
            revalidate_terminal_continuity()
        raise
    finally:
        if source_bundle_fd >= 0:
            os.close(source_bundle_fd)
        if snapshot is not None:
            snapshot.close()


def validate_bound_files(value: Any, expected_keys: frozenset[str], label: str,
                         *, verify_bytes: bool) -> None:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise BuildError(f"{label} binding schema mismatch")
    directory = Path(exact_string(value["directory"], f"{label}.directory"))
    if not directory.is_absolute():
        raise BuildError(f"{label}.directory must be absolute")
    pairs = [
        key[:-5] for key in expected_keys
        if key.endswith("_path") and f"{key[:-5]}_sha256" in expected_keys
    ]
    for stem in pairs:
        path = Path(exact_string(value[f"{stem}_path"], f"{label}.{stem}_path"))
        expected_sha = exact_sha256(value[f"{stem}_sha256"], f"{label}.{stem}_sha256")
        if not path.is_absolute() or path.parent != directory:
            raise BuildError(f"{label}.{stem}_path is outside the exact directory")
        if verify_bytes:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = require_regular_fd(fd, f"{label}.{stem}")
                if opened.mode != 0o444:
                    raise BuildError(f"{label}.{stem} mode is not exact 0444")
                actual_sha, _ = sha256_fd(fd)
            finally:
                os.close(fd)
            if actual_sha != expected_sha or Identity.from_stat(path.lstat()) != opened:
                raise BuildError(f"{label}.{stem} byte/path binding mismatch")


def validate_source_inventory_shape(value: Any) -> None:
    expected = {
        "source_root_identity", "distribution_order", "distributions",
        "external_record_exclusion_evidence", "inventory_digest",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise BuildError("source_inventory schema mismatch")
    Identity.from_json(value["source_root_identity"])
    if value["distribution_order"] != list(COPY_DISTRIBUTIONS):
        raise BuildError("source_inventory distribution order mismatch")
    distributions = value["distributions"]
    if not isinstance(distributions, dict) or set(distributions) != set(COPY_DISTRIBUTIONS):
        raise BuildError("source_inventory distribution set mismatch")
    item_keys = {
        "declared_name", "version", "record_relative_path", "record_sha256",
        "metadata_relative_path", "metadata_sha256", "safe_file_count",
        "safe_closure_digest", "member_snapshot_digest",
        "excluded_out_of_site_record_members",
    }
    for name in COPY_DISTRIBUTIONS:
        item = distributions[name]
        if not isinstance(item, dict) or set(item) != item_keys:
            raise BuildError(f"source_inventory.{name} schema mismatch")
        for key in ("declared_name", "version"):
            if not exact_string(item[key], f"source_inventory.{name}.{key}"):
                raise BuildError(f"source_inventory.{name}.{key} is empty")
        safe_relative(exact_string(item["record_relative_path"], f"source_inventory.{name}.record_relative_path"))
        safe_relative(exact_string(item["metadata_relative_path"], f"source_inventory.{name}.metadata_relative_path"))
        for key in ("record_sha256", "metadata_sha256", "safe_closure_digest", "member_snapshot_digest"):
            exact_sha256(item[key], f"source_inventory.{name}.{key}")
        if exact_integer(item["safe_file_count"], f"source_inventory.{name}.safe_file_count") <= 0:
            raise BuildError(f"source_inventory.{name}.safe_file_count must be positive")
        exclusions = item["excluded_out_of_site_record_members"]
        if type(exclusions) is not list or exclusions != list(EXTERNAL_RECORD_EXCLUSIONS[name]):
            raise BuildError(f"source_inventory.{name} exclusion inventory mismatch")
    if value["external_record_exclusion_evidence"] != external_record_exclusion_evidence():
        raise BuildError("source_inventory exact7 exclusion evidence mismatch")
    exact_sha256(value["inventory_digest"], "source_inventory.inventory_digest")
    core = {key: value[key] for key in expected if key != "inventory_digest"}
    if sha256_bytes(canonical_json_bytes(core)) != value["inventory_digest"]:
        raise BuildError("source_inventory digest mismatch")


def validate_authorization_payload(auth: Mapping[str, Any], authorization_path: Path,
                                   authorization_sha256: str, observed_argv: Sequence[str],
                                   *, enforce_fixed: bool) -> None:
    if not authorization_path.is_absolute():
        raise BuildError("authorization path must be absolute")
    exact_sha256(authorization_sha256, "trusted authorization SHA")
    required = {
        "schema", "status", "decision_id", "final_root", "source_python",
        "source_python_sha256", "source_bundle", "source_site_packages",
        "source_inventory", "bindings", "logical_builder_argv",
        "trusted_launch", "journal", "scope", "authority",
    }
    if not isinstance(auth, dict) or set(auth) != required:
        raise BuildError("authorization exact schema/status mismatch")
    if (exact_string(auth["schema"], "authorization.schema") != AUTH_SCHEMA or
            exact_string(auth["status"], "authorization.status") != AUTH_STATUS):
        raise BuildError("authorization exact schema/status mismatch")
    decision_id = exact_string(auth["decision_id"], "authorization.decision_id")
    if not DECISION_RE.fullmatch(decision_id):
        raise BuildError("decision_id is invalid")
    final_root = Path(exact_string(auth["final_root"], "authorization.final_root"))
    if not final_root.is_absolute() or (enforce_fixed and final_root != EXPECTED_FINAL_ROOT):
        raise BuildError("final ROOT mismatch")
    source_python = exact_string(auth["source_python"], "authorization.source_python")
    if not Path(source_python).is_absolute() or (enforce_fixed and source_python != os.fspath(EXPECTED_PYTHON)):
        raise BuildError("source Python path mismatch")
    exact_sha256(auth["source_python_sha256"], "authorization.source_python_sha256")
    source_site_packages = Path(exact_string(
        auth["source_site_packages"], "authorization.source_site_packages"
    ))
    if not source_site_packages.is_absolute():
        raise BuildError("source site-packages path must be absolute")
    validate_source_inventory_shape(auth["source_inventory"])
    source_bundle = auth["source_bundle"]
    if not isinstance(source_bundle, dict) or set(source_bundle) != {"path", *V8_BINDING.keys()}:
        raise BuildError("source bundle binding schema mismatch")
    bundle_path = Path(exact_string(source_bundle["path"], "authorization.source_bundle.path"))
    if not bundle_path.is_absolute() or bundle_path.name != source_bundle["directory_name"]:
        raise BuildError("source bundle path/name mismatch")
    for key in ("directory_name", "receipt_sha256", "bundle_manifest_sha256", "sha256_index_sha256"):
        exact_string(source_bundle[key], f"authorization.source_bundle.{key}")
    for key in ("receipt_sha256", "bundle_manifest_sha256", "sha256_index_sha256"):
        exact_sha256(source_bundle[key], f"authorization.source_bundle.{key}")
    for key in ("top_level_count", "indexed_count"):
        exact_integer(source_bundle[key], f"authorization.source_bundle.{key}")
    if enforce_fixed and {key: source_bundle[key] for key in V8_BINDING} != V8_BINDING:
        raise BuildError("source bundle differs from frozen v8 binding")
    bindings = auth["bindings"]
    if not isinstance(bindings, dict) or set(bindings) != {
        "v8_scientific_independent_audit", "v1_builder",
        "v1_builder_independent_audit", "v10_package",
        "v10_builder_independent_audit",
        "v9_builder_negative_independent_audit",
        "v8_builder_negative_independent_audit",
        "v7_builder_negative_independent_audit",
    }:
        raise BuildError("authorization binding closure schema mismatch")
    if enforce_fixed:
        if bindings["v8_scientific_independent_audit"] != V8_AUDIT_BINDING:
            raise BuildError("v8 audit binding mismatch")
        if bindings["v1_builder"] != V1_BINDING or bindings["v1_builder_independent_audit"] != V1_AUDIT_BINDING:
            raise BuildError("v1 negative evidence binding mismatch")
    validate_bound_files(
        bindings["v10_package"], V10_PACKAGE_BINDING_KEYS, "v10_package",
        verify_bytes=enforce_fixed,
    )
    package_names = {
        "builder_path": "build_result_free_transport_runtime_v10.py",
        "test_path": "test_transport_runtime_layout_builder_v10_synthetic.py",
        "smoke_path": "result_free_runtime_smoke_v10.py",
        "smoke_test_path": "test_result_free_runtime_smoke_v10_synthetic.py",
        "bundle_manifest_path": "BUNDLE_MANIFEST.json",
        "sha256_index_path": "SHA256SUMS",
        "prepared_receipt_path": "PREPARED_RESULT_FREE_RECEIPT.json",
    }
    if any(Path(bindings["v10_package"][key]).name != name
           for key, name in package_names.items()):
        raise BuildError("v10 package bound filenames are not exact")
    validate_bound_files(
        bindings["v10_builder_independent_audit"], V10_AUDIT_BINDING_KEYS,
        "v10_builder_independent_audit", verify_bytes=enforce_fixed,
    )
    if bindings["v10_builder_independent_audit"].get("action_scoped_verdict") != V10_QA_ACTION_VERDICT:
        raise BuildError("v10 independent audit scoped GO absent")
    audit_receipt, _ = read_frozen_json_single_open(
        Path(bindings["v10_builder_independent_audit"]["receipt_path"]),
        bindings["v10_builder_independent_audit"]["receipt_sha256"],
        "v10 builder independent audit receipt",
    )
    validate_v10_audit_receipt_semantics(
        audit_receipt,
        audit_binding=bindings["v10_builder_independent_audit"],
        package_binding=bindings["v10_package"],
    )
    validate_v9_negative_audit_binding(
        bindings["v9_builder_negative_independent_audit"],
        verify_bytes=enforce_fixed,
    )
    validate_v8_negative_audit_binding(
        bindings["v8_builder_negative_independent_audit"],
        verify_bytes=enforce_fixed,
    )
    validate_v7_negative_audit_binding(
        bindings["v7_builder_negative_independent_audit"],
        verify_bytes=enforce_fixed,
    )
    validate_trusted_launch_binding_shape(
        auth["trusted_launch"], bindings["v10_package"],
        auth["source_python_sha256"],
    )
    outer_receipt, _ = read_authorization_single_open(
        Path(auth["trusted_launch"]["outer_launch_receipt_path"]),
        auth["trusted_launch"]["outer_launch_receipt_sha256"],
    )
    validate_outer_launch_receipt_semantics(outer_receipt, auth=auth)
    validate_dynamic_preflight_anchor_files(auth)
    journal = auth["journal"]
    if not isinstance(journal, dict) or set(journal) != {
        "directory", "begin", "intent", "terminal", "lock", "parent_path",
        "parent_device", "parent_inode",
    }:
        raise BuildError("journal schema mismatch")
    journal_dir = Path(exact_string(journal["directory"], "authorization.journal.directory"))
    if not journal_dir.is_absolute() or journal_dir.parent != final_root.parent:
        raise BuildError("journal must be an exact sibling of fixed ROOT")
    if journal_dir.name != f".result-free-transport-v10.{decision_id}":
        raise BuildError("journal directory name is not decision-bound")
    parent_path = Path(exact_string(journal["parent_path"], "authorization.journal.parent_path"))
    if parent_path != final_root.parent:
        raise BuildError("journal parent path mismatch")
    for key in ("parent_device", "parent_inode"):
        if exact_integer(journal[key], f"authorization.journal.{key}") < 0:
            raise BuildError(f"journal {key} is negative")
    for key in ("begin", "intent", "terminal", "lock"):
        if Path(exact_string(journal[key], f"authorization.journal.{key}")) != journal_dir / JOURNAL_NAMES[key]:
            raise BuildError(f"journal {key} path mismatch")
    if exact_string(auth["scope"], "authorization.scope") != "RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY":
        raise BuildError("authorization scope mismatch")
    expected_authority = {
        "transport_runtime_layout_authorized": True,
        "result_access_authorized": False,
        "signals_authorized": False,
        "controller_or_outer_main_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    authority = auth["authority"]
    if (not isinstance(authority, dict) or set(authority) != set(expected_authority) or
            any(exact_boolean(authority[key], f"authorization.authority.{key}") is not value
                for key, value in expected_authority.items())):
        raise BuildError("authorization authority mismatch")
    builder_argv = exact_string_list(
        auth["logical_builder_argv"], "logical_builder_argv"
    )
    expected_argv_template = [
        source_python, "-I", "-B", "-S",
        bindings["v10_package"]["builder_path"],
        "--source-bundle", source_bundle["path"],
        "--authorization", os.fspath(authorization_path),
        "--trusted-authorization-sha256", AUTH_SHA_ARGV_MARKER,
        "--execute", EXECUTE_TEXT,
    ]
    expected_observed_argv = [
        authorization_sha256 if item == AUTH_SHA_ARGV_MARKER else item
        for item in expected_argv_template
    ]
    if builder_argv != expected_argv_template or list(observed_argv) != expected_observed_argv:
        raise BuildError("logical builder argv differs from authorization")
    if enforce_fixed:
        if (
            globals().get("__held_source_fd__") != HELD_BUILDER_SOURCE_FD
            or globals().get("__held_source_sha256__")
            != bindings["v10_package"]["builder_sha256"]
            or globals().get("__held_original_evidence_path__")
            != bindings["v10_package"]["builder_path"]
            or globals().get("__held_outer_launch_receipt_sha256__")
            != auth["trusted_launch"]["outer_launch_receipt_sha256"]
            or globals().get("__file__")
            != f"/proc/self/fd/{HELD_BUILDER_SOURCE_FD}"
        ):
            raise BuildError("builder was not loaded through the trusted held-byte entry")


def linux_cmdline() -> list[str]:
    if not sys.platform.startswith("linux") or not Path("/proc/self/cmdline").is_file():
        raise BuildError("production builder requires Linux /proc command identity")
    return [item.decode("utf-8") for item in Path("/proc/self/cmdline").read_bytes().split(b"\0") if item]


def validate_live_held_builder_launch(
    auth: Mapping[str, Any], held_context: Mapping[str, Any]
) -> None:
    expected = validate_trusted_launch_binding_shape(
        auth["trusted_launch"], auth["bindings"]["v10_package"],
        auth["source_python_sha256"],
    )
    if type(held_context) is not dict or held_context != expected:
        raise BuildError("live held builder context differs from authorization")
    for fd, identity_key, sha_key, inheritable_key, label in (
        (
            HELD_INTERPRETER_FD, "interpreter_identity", "interpreter_sha256",
            "interpreter_fd_inheritable", "held interpreter",
        ),
        (
            HELD_BUILDER_SOURCE_FD, "builder_source_identity",
            "builder_source_sha256", "builder_source_fd_inheritable",
            "held builder source",
        ),
    ):
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            raise BuildError(f"{label} FD is not read-only")
        if os.get_inheritable(fd) is not expected[inheritable_key]:
            raise BuildError(f"{label} FD inheritance state mismatch")
        before = require_regular_fd(fd, label)
        if not 0 < before.size_bytes <= HELD_SOURCE_READ_LIMIT_BYTES:
            raise BuildError(f"{label} size is outside the bounded policy")
        if before != Identity.from_json(expected[identity_key]):
            raise BuildError(f"{label} identity differs from authorization")
        digest, size = sha256_fd(fd)
        after = identity_fd(fd)
        if before != after or digest != expected[sha_key] or size != before.size_bytes:
            raise BuildError(f"{label} changed during held hash")
    executable_fd = os.open("/proc/self/exe", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        if identity_fd(executable_fd) != identity_fd(HELD_INTERPRETER_FD):
            raise BuildError("running executable inode differs from held interpreter FD")
        executable_sha, _ = sha256_fd(executable_fd)
        if executable_sha != expected["interpreter_sha256"]:
            raise BuildError("running executable SHA differs from held interpreter")
    finally:
        os.close(executable_fd)
    interpreter_realpath = Path(os.path.realpath(auth["source_python"]))
    interpreter_named_fd = os.open(
        interpreter_realpath,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if identity_fd(interpreter_named_fd) != identity_fd(HELD_INTERPRETER_FD):
            raise BuildError(
                "source Python resolved path no longer names held interpreter inode"
            )
        interpreter_named_sha, _ = sha256_fd(interpreter_named_fd)
        if interpreter_named_sha != expected["interpreter_sha256"]:
            raise BuildError("source Python resolved path SHA mismatch")
    finally:
        os.close(interpreter_named_fd)
    if linux_cmdline() != expected["outer_process_argv"]:
        raise BuildError("actual outer /proc cmdline differs from held launch binding")
    builder_path = Path(expected["builder_original_evidence_path"])
    before = builder_path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BuildError("builder evidence path is not a single-link regular file")
    named_fd = os.open(
        builder_path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if identity_fd(named_fd) != identity_fd(HELD_BUILDER_SOURCE_FD):
            raise BuildError("builder evidence path no longer names held source inode")
        named_sha, _ = sha256_fd(named_fd)
        if named_sha != expected["builder_source_sha256"]:
            raise BuildError("builder evidence path SHA mismatch")
    finally:
        os.close(named_fd)
    package = auth["bindings"]["v10_package"]
    audit = auth["bindings"]["v10_builder_independent_audit"]
    validate_bound_files(
        package, V10_PACKAGE_BINDING_KEYS, "live_v10_package", verify_bytes=True
    )
    validate_bound_files(
        audit, V10_AUDIT_BINDING_KEYS, "live_v10_independent_audit",
        verify_bytes=True,
    )
    audit_receipt, _ = read_frozen_json_single_open(
        Path(audit["receipt_path"]), audit["receipt_sha256"]
        , "live v10 independent audit receipt"
    )
    validate_v10_audit_receipt_semantics(
        audit_receipt, audit_binding=audit, package_binding=package
    )
    validate_v9_negative_audit_binding(
        auth["bindings"]["v9_builder_negative_independent_audit"],
        verify_bytes=True,
    )
    validate_v8_negative_audit_binding(
        auth["bindings"]["v8_builder_negative_independent_audit"],
        verify_bytes=True,
    )
    validate_v7_negative_audit_binding(
        auth["bindings"]["v7_builder_negative_independent_audit"],
        verify_bytes=True,
    )
    launch_receipt, launch_receipt_identity = read_authorization_single_open(
        Path(expected["outer_launch_receipt_path"]),
        expected["outer_launch_receipt_sha256"],
    )
    validate_outer_launch_receipt_semantics(launch_receipt, auth=auth)
    if (
        globals().get("__held_source_fd__") != HELD_BUILDER_SOURCE_FD
        or globals().get("__held_source_sha256__")
        != expected["builder_source_sha256"]
        or globals().get("__held_original_evidence_path__")
        != expected["builder_original_evidence_path"]
        or globals().get("__held_outer_launch_receipt_sha256__")
        != expected["outer_launch_receipt_sha256"]
        or globals().get("__file__")
        != f"/proc/self/fd/{HELD_BUILDER_SOURCE_FD}"
    ):
        raise BuildError("trusted held builder loader provenance globals mismatch")


def revalidate_production_trust(
    auth: Mapping[str, Any], trust_lease: ProductionTrustLease | None
) -> None:
    if trust_lease is None:
        raise BuildError("production trust lease is absent")
    trust_lease.revalidate(auth)
    validate_live_held_builder_launch(auth, auth["trusted_launch"])


def _execute_transaction_core(
    auth: Mapping[str, Any], authorization_path: Path, authorization_sha256: str,
    *, rename_impl: Callable[[int, str, int, str], None] = renameat2_noreplace,
    enforce_fixed: bool = True,
    observed_argv: Sequence[str] | None = None,
    after_discovery_hook: Callable[[DiscoverySnapshot], None] | None = None,
    lock_opened_before_flock_hook: Callable[[int, Identity], None] | None = None,
    before_rename_hook: Callable[[], None] | None = None,
    terminal_publish_impl: Callable[..., dict[str, Any]] | None = None,
    terminal_publication_method: str | None = None,
    terminal_mid_write_hook: Callable[[], None] | None = None,
    terminal_after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
    fail_terminal_mid_write_hook: Callable[[], None] | None = None,
    fail_terminal_after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
    linux_integration: str | None = None,
    preopened_parent_fd: int | None = None,
    trust_lease: ProductionTrustLease | None = None,
    synthetic_path_lease: "SyntheticProductionPathLease | None" = None,
) -> dict[str, Any]:
    argv = list(observed_argv) if observed_argv is not None else linux_cmdline()
    validate_authorization_payload(
        auth, authorization_path, authorization_sha256, argv, enforce_fixed=enforce_fixed
    )
    if enforce_fixed:
        revalidate_production_trust(auth, trust_lease)
        if synthetic_path_lease is not None:
            raise BuildError("production core may not accept a synthetic path lease")
    elif preopened_parent_fd is not None and synthetic_path_lease is None:
        raise BuildError("synthetic preopened parent lacks a separation lease")
    final_root = Path(auth["final_root"])
    parent_path = final_root.parent
    if preopened_parent_fd is None:
        parent_fd = open_directory_path(parent_path)
    else:
        parent_fd = os.dup(preopened_parent_fd)
        if not path_matches_fd(parent_path, parent_fd):
            os.close(parent_fd)
            raise BuildError("preopened transaction parent path/FD mismatch")
    parent_identity = identity_fd(parent_fd)
    expected_parent = auth["journal"]
    if (parent_identity.device != expected_parent["parent_device"] or
            parent_identity.inode != expected_parent["parent_inode"]):
        os.close(parent_fd)
        raise BuildError("authorized journal/final parent dev+ino mismatch")
    if synthetic_path_lease is not None:
        synthetic_path_lease.revalidate()
        if not _same_directory_object(
            parent_identity, synthetic_path_lease.synthetic_parent_identity
        ):
            os.close(parent_fd)
            raise BuildError(
                "transaction parent differs from synthetic separation lease"
            )
    if linux_integration is None:
        linux_integration = (
            "PASS_LINUX_RENAMEAT2_NOREPLACE"
            if sys.platform.startswith("linux") and rename_impl is renameat2_noreplace
            else "NOT_RUN_NON_LINUX" if not sys.platform.startswith("linux")
            else "SYNTHETIC_RENAME_INJECTED_NOT_PRODUCTION"
        )
    if linux_integration not in {
        "PASS_LINUX_RENAMEAT2_NOREPLACE", "NOT_RUN_NON_LINUX",
        "SYNTHETIC_RENAME_INJECTED_NOT_PRODUCTION",
    }:
        os.close(parent_fd)
        raise BuildError("linux integration status is invalid")
    if enforce_fixed and linux_integration != "PASS_LINUX_RENAMEAT2_NOREPLACE":
        os.close(parent_fd)
        raise BuildError("production execution requires real Linux renameat2 integration")
    if terminal_publish_impl is None:
        terminal_publish_impl = publish_terminal_linux_otmpfile_noreplace
    inferred_terminal_method = (
        PRODUCTION_TERMINAL_PUBLICATION_METHOD
        if terminal_publish_impl is publish_terminal_linux_otmpfile_noreplace
        else SYNTHETIC_TERMINAL_PUBLICATION_METHOD
    )
    if terminal_publication_method is None:
        terminal_publication_method = inferred_terminal_method
    if terminal_publication_method != inferred_terminal_method:
        os.close(parent_fd)
        raise BuildError("terminal publisher implementation/method mismatch")
    if (
        enforce_fixed
        and (
            terminal_publish_impl is not publish_terminal_linux_otmpfile_noreplace
            or terminal_publication_method
            != PRODUCTION_TERMINAL_PUBLICATION_METHOD
        )
    ):
        os.close(parent_fd)
        raise BuildError("production has no non-O_TMPFILE terminal fallback")
    journal_fd: int | None = None
    lock_fd: int | None = None
    lock_identity: Identity | None = None
    snapshot: DiscoverySnapshot | None = None
    handles: StagingHandles | None = None
    recovery_owns_terminal_outcome = False
    phase = "OPEN_PARENT"

    def revalidate_synthetic_separation() -> None:
        if synthetic_path_lease is not None:
            synthetic_path_lease.revalidate()

    def revalidate_terminal_continuity() -> None:
        """Last named-path/held-inode trust check through terminal durability."""

        revalidate_synthetic_separation()
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)

    try:
        journal_path = Path(auth["journal"]["directory"])
        journal_directory_created_by_this_attempt = False
        try:
            journal_fd = open_directory_at(parent_fd, journal_path.name)
        except FileNotFoundError:
            try:
                revalidate_synthetic_separation()
                journal_fd = mkdir_open_at(parent_fd, journal_path.name)
                journal_directory_created_by_this_attempt = True
            except FileExistsError:
                # Another contender won the mkdir race.  Both contenders now
                # open the same journal and arbitrate solely on its LOCK inode.
                journal_fd = open_directory_at(parent_fd, journal_path.name)
        revalidate_synthetic_separation()

        def revalidating_lock_hook(
            lock_candidate_fd: int, lock_candidate_identity: Identity
        ) -> None:
            if lock_opened_before_flock_hook is not None:
                lock_opened_before_flock_hook(
                    lock_candidate_fd, lock_candidate_identity
                )
            revalidate_synthetic_separation()

        lock_attempt = acquire_transaction_lock(
            journal_fd,
            after_open_before_flock_hook=revalidating_lock_hook,
        )
        lock_fd, attempted_lock_identity, lock_created_by_this_attempt = (
            lock_attempt
        )
        if lock_fd is None:
            return {
                "status": "IN_PROGRESS_NO_TRANSACTION_PAYLOAD_MUTATION",
                "decision_id": auth["decision_id"],
                "lock_method": LOCK_METHOD,
                "observed_lock_identity": attempted_lock_identity.json(),
                "journal_directory_created_by_this_attempt": (
                    journal_directory_created_by_this_attempt
                ),
                "lock_created_by_this_attempt": lock_created_by_this_attempt,
                "journal_mutated_by_this_attempt": (
                    journal_directory_created_by_this_attempt
                    or lock_created_by_this_attempt
                ),
                "transaction_payload_written_by_this_attempt": False,
                "result_accessed": False,
                "signals_sent": False,
            }
        lock_identity = attempted_lock_identity
        if enforce_fixed:
            # Anonymous-only early check: no pathname is created.  The
            # separately authorized MARS/XFS preflight must exercise the full
            # probe helper, while the eventual terminal publication repeats
            # all checks and has no fallback.
            precheck_terminal_publication_linux_xfs(journal_fd)
        journal_names = set(fresh_directory_names(journal_fd))
        if JOURNAL_NAMES["begin"] not in journal_names:
            if journal_names != {JOURNAL_NAMES["lock"]}:
                raise BuildError(
                    "journal without BEGIN must contain exactly LOCK"
                )
            # The lock owner, not the mkdir creator, owns initialization.  An
            # empty pre-existing journal or a crash leaving LOCK alone is thus
            # recoverable.  Parent fsync makes a raced/pre-existing mkdir
            # durable before BEGIN becomes the transaction anchor.
            os.fsync(parent_fd)
            begin = {
                "schema": BEGIN_SCHEMA,
                "status": BEGIN_STATUS,
                "created_utc": utc_now(),
                "authorization_path": os.fspath(authorization_path),
                "authorization_sha256": authorization_sha256,
                "decision_id": auth["decision_id"],
                "final_root": auth["final_root"],
                "parent_identity": identity_fd(parent_fd).json(),
                "lock_identity": lock_identity.json(),
                "lock_method": LOCK_METHOD,
                "terminal_publication_method": terminal_publication_method,
                "terminal_canonical_visibility_rule": (
                    TERMINAL_CANONICAL_VISIBILITY_RULE
                ),
                "v10_package": auth["bindings"]["v10_package"],
                "v10_builder_audit": auth["bindings"][
                    "v10_builder_independent_audit"
                ],
                "v9_negative_audit": auth["bindings"][
                    "v9_builder_negative_independent_audit"
                ],
                "v8_negative_audit": auth["bindings"][
                    "v8_builder_negative_independent_audit"
                ],
                "v7_negative_audit": auth["bindings"][
                    "v7_builder_negative_independent_audit"
                ],
                "v1_audit": auth["bindings"]["v1_builder_independent_audit"],
                "result_accessed": False,
                "signals_sent": False,
            }
            validate_begin_record(
                begin,
                auth=auth,
                auth_sha=authorization_sha256,
                authorization_path=authorization_path,
                parent_fd=parent_fd,
                lock_identity=lock_identity,
                terminal_publication_method=terminal_publication_method,
            )
            revalidate_synthetic_separation()
            begin_sha, _ = write_json_at_exclusive(
                journal_fd, JOURNAL_NAMES["begin"], begin
            )
        else:
            # recover_existing has its own complete independent evidence rebuild
            # and terminal decision.  Its exceptions must never fall through to
            # this outer first-attempt handler, which lacks the recovery-held
            # snapshot and could otherwise convert a recoverable PASS-terminal
            # publication interruption into a permanent FAIL.
            recovery_owns_terminal_outcome = True
            revalidate_synthetic_separation()
            return recover_existing(
                auth, authorization_sha256, journal_fd, parent_fd, parent_path,
                final_root.name, rename_impl, lock_fd, lock_identity,
                authorization_path, argv, linux_integration,
                terminal_publish_impl, terminal_publication_method,
                enforce_fixed,
                trust_lease,
                synthetic_path_lease,
                terminal_mid_write_hook,
                terminal_after_link_before_dir_fsync_hook,
                fail_terminal_mid_write_hook,
                fail_terminal_after_link_before_dir_fsync_hook,
            )

        phase = "CHECK_NO_CLOBBER_TARGET"
        if file_exists_at(parent_fd, final_root.name):
            raise BuildError("fixed ROOT already exists")
        phase = "DISCOVER_SOURCE"
        snapshot = discover_source(Path(auth["source_site_packages"]))
        if enforce_fixed and snapshot.root_identity != trust_lease.directory_identity(
            auth["source_site_packages"]
        ):
            raise BuildError("source site-packages differs from held trust lease")
        if after_discovery_hook is not None:
            after_discovery_hook(snapshot)
        if snapshot.inventory() != auth["source_inventory"]:
            raise BuildError("source inventory differs from authorization")
        phase = "BUILD_STAGING"
        revalidate_synthetic_separation()
        handles = build_staging(
            journal_fd, Path(auth["source_bundle"]["path"]), snapshot,
            auth["source_bundle"], final_root,
            source_bundle_held_fd=(
                trust_lease.dup_directory(auth["source_bundle"]["path"])
                if enforce_fixed else None
            ),
        )
        phase = "WRITE_COMMIT_INTENT"
        validate_held_transaction_lock(journal_fd, lock_fd, lock_identity)
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)
        intent = make_intent(
            authorization_sha256, auth, handles,
            authorization_path=authorization_path,
            begin_sha256=begin_sha,
            parent_identity=parent_identity,
            journal_identity=identity_fd(journal_fd),
            lock_identity=lock_identity,
            linux_integration=linux_integration,
            terminal_publication_method=terminal_publication_method,
            observed_builder_argv=argv,
        )
        validate_intent_record(
            intent,
            auth=auth,
            auth_sha=authorization_sha256,
            authorization_path=authorization_path,
            begin_sha=begin_sha,
            parent_fd=parent_fd,
            journal_fd=journal_fd,
            lock_identity=lock_identity,
            logical_builder_argv=argv,
            linux_integration=linux_integration,
            terminal_publication_method=terminal_publication_method,
        )
        revalidate_synthetic_separation()
        intent_sha, _ = write_json_at_exclusive(
            journal_fd, JOURNAL_NAMES["intent"], intent
        )
        phase = "PUBLISH_STAGING"
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)

        def revalidating_before_rename() -> None:
            if before_rename_hook is not None:
                before_rename_hook()
            revalidate_synthetic_separation()

        publish = publish_staging(
            handles, parent_fd, parent_path, final_root.name, rename_impl,
            before_rename_hook=revalidating_before_rename,
        )
        phase = "WRITE_PASS_TERMINAL"
        receipt = build_pass_receipt(auth, intent, intent_sha, publish)
        validate_build_pass_receipt_schema(receipt)
        validate_held_transaction_lock(journal_fd, lock_fd, lock_identity)
        if enforce_fixed:
            revalidate_production_trust(auth, trust_lease)
        revalidate_synthetic_separation()
        terminal_bytes = canonical_json_bytes(receipt)
        terminal_evidence = terminal_publish_impl(
            journal_fd,
            JOURNAL_NAMES["terminal"],
            terminal_bytes,
            mid_write_hook=terminal_mid_write_hook,
            after_link_before_dir_fsync_hook=(
                terminal_after_link_before_dir_fsync_hook
            ),
        )
        if terminal_evidence.get("method") != terminal_publication_method:
            raise BuildError("PASS terminal publisher method evidence mismatch")
        os.fsync(journal_fd)
        os.fsync(parent_fd)
        revalidate_terminal_continuity()
        return {
            "status": "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY",
            "intent_sha256": intent_sha,
            "terminal_sha256": sha256_bytes(terminal_bytes),
            "build": handles.evidence,
            "publish": publish,
            "receipt": receipt,
            "scope": {"result_accessed": False, "signals_sent": False,
                      "controller_or_outer_main_executed": False},
        }
    except BaseException as exc:
        lock_still_held = False
        if journal_fd is not None and lock_fd is not None and lock_identity is not None:
            try:
                validate_held_transaction_lock(journal_fd, lock_fd, lock_identity)
            except BaseException:
                lock_still_held = False
            else:
                lock_still_held = True
        if (
            journal_fd is not None
            and lock_still_held
            and not recovery_owns_terminal_outcome
            and not file_exists_at(journal_fd, JOURNAL_NAMES["terminal"])
        ):
            # Only an exact final tree bound by durable COMMIT_INTENT is a
            # recoverable post-publication interruption.  Mere path existence
            # can instead be the no-clobber collision that caused this failure.
            published_exact_for_recovery = False
            failure_intent: Mapping[str, Any] | None = None
            if (file_exists_at(journal_fd, JOURNAL_NAMES["intent"])
                    and file_exists_at(parent_fd, final_root.name)
                    and snapshot is not None):
                try:
                    recovery_intent, _, _ = read_json_at(
                        journal_fd, JOURNAL_NAMES["intent"]
                    )
                    failure_intent = recovery_intent
                    verify_final_against_intent(
                        parent_fd, final_root.name, recovery_intent, auth, snapshot
                    )
                except BaseException:
                    pass
                else:
                    published_exact_for_recovery = True
            if not published_exact_for_recovery:
                expected_staging_identity = handles.staging_identity if handles is not None else None
                observed_staging_identity = None
                if file_exists_at(journal_fd, JOURNAL_NAMES["staging"]):
                    observed_staging_identity = stat_directory_at(
                        journal_fd, JOURNAL_NAMES["staging"]
                    )
                    if expected_staging_identity is None:
                        expected_staging_identity = observed_staging_identity
                if failure_intent is None and file_exists_at(
                    journal_fd, JOURNAL_NAMES["intent"]
                ):
                    try:
                        failure_intent, _, _ = read_json_at(
                            journal_fd, JOURNAL_NAMES["intent"]
                        )
                    except BaseException:
                        failure_intent = None
                root_present, root_inode_match, root_exact = inspect_fixed_root_publication(
                    parent_fd,
                    final_root.name,
                    failure_intent,
                    auth,
                    snapshot,
                )
                try:
                    fail_value = fail_terminal(
                        authorization_sha256, auth["decision_id"], phase, exc,
                        expected_staging_identity, observed_staging_identity,
                        fixed_root_path_present=root_present,
                        fixed_root_inode_matches_intent=root_inode_match,
                        fixed_root_exactly_validated=root_exact,
                        terminal_publication_method=terminal_publication_method,
                    )
                    validate_fail_terminal_schema(
                        fail_value,
                        authorization_sha256=authorization_sha256,
                        decision_id=auth["decision_id"],
                        terminal_publication_method=terminal_publication_method,
                    )
                    if enforce_fixed:
                        revalidate_production_trust(auth, trust_lease)
                    revalidate_synthetic_separation()
                    terminal_evidence = terminal_publish_impl(
                        journal_fd,
                        JOURNAL_NAMES["terminal"],
                        canonical_json_bytes(fail_value),
                        mid_write_hook=fail_terminal_mid_write_hook,
                        after_link_before_dir_fsync_hook=(
                            fail_terminal_after_link_before_dir_fsync_hook
                        ),
                    )
                    if terminal_evidence.get("method") != terminal_publication_method:
                        raise BuildError(
                            "FAIL terminal publisher method evidence mismatch"
                        )
                    os.fsync(journal_fd)
                    os.fsync(parent_fd)
                    revalidate_terminal_continuity()
                except BaseException:
                    pass
        raise
    finally:
        if handles is not None:
            handles.close()
        if snapshot is not None:
            snapshot.close()
        if journal_fd is not None:
            try:
                os.close(journal_fd)
            except OSError:
                pass
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        os.close(parent_fd)


@dataclass
class SyntheticProductionPathLease:
    """Hold both sides of the synthetic/canonical separation invariant."""

    synthetic_parent_path: Path
    canonical_parent_path: Path
    synthetic_parent_fd: int
    synthetic_parent_identity: Identity
    canonical_parent_fd: int
    canonical_parent_identity: Identity | None

    @classmethod
    def open(cls, auth: Mapping[str, Any]) -> "SyntheticProductionPathLease":
        try:
            final_root = Path(exact_string(
                auth["final_root"], "synthetic.final_root"
            ))
            journal = auth["journal"]
            journal_directory = Path(exact_string(
                journal["directory"], "synthetic.journal.directory"
            ))
            journal_parent = Path(exact_string(
                journal["parent_path"], "synthetic.journal.parent_path"
            ))
        except (KeyError, TypeError) as exc:
            raise BuildError(
                "synthetic authorization lacks exact write paths"
            ) from exc
        if (
            not final_root.is_absolute()
            or not journal_directory.is_absolute()
            or not journal_parent.is_absolute()
        ):
            raise BuildError("synthetic write paths must be canonical absolute paths")
        normalized = [
            Path(os.path.abspath(os.fspath(item)))
            for item in (final_root, journal_directory, journal_parent)
        ]
        if normalized != [final_root, journal_directory, journal_parent]:
            raise BuildError("synthetic write paths are not canonical absolute paths")
        if (
            final_root.parent != journal_parent
            or journal_directory.parent != journal_parent
        ):
            raise BuildError(
                "synthetic final/journal must be direct children of one parent"
            )

        canonical_final = EXPECTED_FINAL_ROOT
        canonical_parent = canonical_final.parent
        synthetic_targets = (final_root, journal_directory, journal_parent)
        if any(
            target == canonical_final
            or target == canonical_parent
            or target.is_relative_to(canonical_parent)
            or canonical_parent.is_relative_to(target)
            for target in synthetic_targets
        ):
            raise BuildError(
                "author-test synthetic entry and canonical production tree "
                "overlap in either lexical containment direction"
            )

        synthetic_parent_fd = open_directory_path(journal_parent)
        canonical_parent_fd = -1
        try:
            synthetic_identity = require_directory_fd(
                synthetic_parent_fd, "synthetic transaction parent"
            )
            try:
                canonical_parent_fd = open_directory_path(canonical_parent)
            except FileNotFoundError:
                canonical_identity = None
            else:
                synthetic_identity, canonical_identity = (
                    reject_bidirectional_directory_overlap(
                        synthetic_parent_fd,
                        canonical_parent_fd,
                        label="synthetic production-path admission",
                    )
                )
            lease = cls(
                synthetic_parent_path=journal_parent,
                canonical_parent_path=canonical_parent,
                synthetic_parent_fd=synthetic_parent_fd,
                synthetic_parent_identity=synthetic_identity,
                canonical_parent_fd=canonical_parent_fd,
                canonical_parent_identity=canonical_identity,
            )
            lease.revalidate()
            return lease
        except BaseException:
            if canonical_parent_fd >= 0:
                os.close(canonical_parent_fd)
            os.close(synthetic_parent_fd)
            raise

    def revalidate(self) -> None:
        if self.synthetic_parent_fd < 0:
            raise BuildError("synthetic production-path lease is closed")
        if not _same_directory_object(
            identity_fd(self.synthetic_parent_fd), self.synthetic_parent_identity
        ):
            raise BuildError("held synthetic parent inode changed")
        named_synthetic_fd = open_directory_path(self.synthetic_parent_path)
        try:
            if not _same_directory_object(
                identity_fd(named_synthetic_fd), self.synthetic_parent_identity
            ):
                raise BuildError(
                    "synthetic parent named path no longer names held inode"
                )
        finally:
            os.close(named_synthetic_fd)

        try:
            named_canonical_fd = open_directory_path(self.canonical_parent_path)
        except FileNotFoundError:
            if self.canonical_parent_fd >= 0:
                raise BuildError(
                    "canonical production parent disappeared after admission"
                )
            return
        try:
            if self.canonical_parent_fd < 0:
                raise BuildError(
                    "canonical production parent appeared after admission"
                )
            if self.canonical_parent_identity is None:
                raise BuildError("held canonical production identity is absent")
            if (
                not _same_directory_object(
                    identity_fd(self.canonical_parent_fd),
                    self.canonical_parent_identity,
                )
                or not _same_directory_object(
                    identity_fd(named_canonical_fd),
                    self.canonical_parent_identity,
                )
            ):
                raise BuildError(
                    "canonical production parent named/held inode continuity failed"
                )
            observed_synthetic, observed_canonical = (
                reject_bidirectional_directory_overlap(
                    self.synthetic_parent_fd,
                    self.canonical_parent_fd,
                    label="synthetic production-path revalidation",
                )
            )
            if (
                not _same_directory_object(
                    observed_synthetic, self.synthetic_parent_identity
                )
                or not _same_directory_object(
                    observed_canonical, self.canonical_parent_identity
                )
            ):
                raise BuildError(
                    "synthetic/canonical parent identity changed during revalidation"
                )
        finally:
            os.close(named_canonical_fd)

    def close(self) -> None:
        if self.canonical_parent_fd >= 0:
            os.close(self.canonical_parent_fd)
            self.canonical_parent_fd = -1
        if self.synthetic_parent_fd >= 0:
            os.close(self.synthetic_parent_fd)
            self.synthetic_parent_fd = -1


def _reject_synthetic_production_paths(
    auth: Mapping[str, Any],
) -> SyntheticProductionPathLease:
    """Acquire a held bidirectional synthetic/canonical separation lease."""

    return SyntheticProductionPathLease.open(auth)


def execute_synthetic_author_test(
    auth: Mapping[str, Any], authorization_path: Path, authorization_sha256: str,
    *, rename_impl: Callable[[int, str, int, str], None],
    observed_argv: Sequence[str],
    after_path_separation_hook: Callable[[], None] | None = None,
    after_discovery_hook: Callable[[DiscoverySnapshot], None] | None = None,
    lock_opened_before_flock_hook: Callable[[int, Identity], None] | None = None,
    before_rename_hook: Callable[[], None] | None = None,
    terminal_publish_impl: Callable[..., dict[str, Any]],
    terminal_publication_method: str,
    terminal_mid_write_hook: Callable[[], None] | None = None,
    terminal_after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
    fail_terminal_mid_write_hook: Callable[[], None] | None = None,
    fail_terminal_after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
    linux_integration: str = "NOT_RUN_NON_LINUX",
) -> dict[str, Any]:
    """Explicit author-test surface; never an authority for the production tree."""

    path_lease = _reject_synthetic_production_paths(auth)
    try:
        if after_path_separation_hook is not None:
            after_path_separation_hook()
        path_lease.revalidate()
        result = _execute_transaction_core(
            auth,
            authorization_path,
            authorization_sha256,
            rename_impl=rename_impl,
            enforce_fixed=False,
            observed_argv=observed_argv,
            after_discovery_hook=after_discovery_hook,
            lock_opened_before_flock_hook=lock_opened_before_flock_hook,
            before_rename_hook=before_rename_hook,
            terminal_publish_impl=terminal_publish_impl,
            terminal_publication_method=terminal_publication_method,
            terminal_mid_write_hook=terminal_mid_write_hook,
            terminal_after_link_before_dir_fsync_hook=(
                terminal_after_link_before_dir_fsync_hook
            ),
            fail_terminal_mid_write_hook=fail_terminal_mid_write_hook,
            fail_terminal_after_link_before_dir_fsync_hook=(
                fail_terminal_after_link_before_dir_fsync_hook
            ),
            linux_integration=linux_integration,
            preopened_parent_fd=path_lease.synthetic_parent_fd,
            synthetic_path_lease=path_lease,
        )
        path_lease.revalidate()
        return result
    finally:
        path_lease.close()


def execute_authorized(
    auth: Mapping[str, Any], authorization_path: Path, authorization_sha256: str,
    *, held_context: Mapping[str, Any], logical_process_argv: Sequence[str],
    authorization_lease: FrozenFileLease,
) -> dict[str, Any]:
    """Only production surface: live held validation is unconditional and first."""

    trust_lease: ProductionTrustLease | None = None
    try:
        authorization_lease.revalidate()
        validate_live_held_builder_launch(auth, held_context)
        trust_lease = ProductionTrustLease.open(
            auth, authorization_path, authorization_sha256,
            authorization_lease=authorization_lease,
        )
        revalidate_production_trust(auth, trust_lease)
        result = _execute_transaction_core(
            auth,
            authorization_path,
            authorization_sha256,
            rename_impl=renameat2_noreplace,
            enforce_fixed=True,
            observed_argv=logical_process_argv,
            terminal_publish_impl=publish_terminal_linux_otmpfile_noreplace,
            terminal_publication_method=PRODUCTION_TERMINAL_PUBLICATION_METHOD,
            linux_integration="PASS_LINUX_RENAMEAT2_NOREPLACE",
            trust_lease=trust_lease,
        )
        revalidate_production_trust(auth, trust_lease)
        return result
    finally:
        if trust_lease is not None:
            trust_lease.close()
        else:
            authorization_lease.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--trusted-authorization-sha256", required=True)
    parser.add_argument("--execute", choices=[EXECUTE_TEXT], required=True)
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        parser.error("bare -- separator forbidden")
    for option in ("--source-bundle", "--authorization", "--trusted-authorization-sha256", "--execute"):
        matches = [item for item in raw if item == option or item.startswith(option + "=")]
        if len(matches) != 1:
            parser.error(f"exactly one {option} required")
    return parser.parse_args(raw)


def main() -> int:
    raise BuildError(
        "direct pathname execution is forbidden; use trusted_held_main after "
        "pread/hash/compile from held FD198 in the trusted preflight process"
    )


def trusted_held_main(
    held_context: Mapping[str, Any], logical_process_argv: Sequence[str]
) -> int:
    logical = list(logical_process_argv)
    if len(logical) < 6:
        raise BuildError("logical held builder argv is too short")
    args = parse_args(logical[5:])
    if not args.authorization.is_absolute() or not args.source_bundle.is_absolute():
        raise BuildError("production paths must be absolute")
    authorization_lease = FrozenFileLease.open(
        args.authorization, args.trusted_authorization_sha256
    )
    try:
        auth_data = authorization_lease.read_bytes()
        auth = strict_json_loads(auth_data)
        if type(auth) is not dict or canonical_json_bytes(auth) != auth_data:
            raise BuildError("held authorization is not canonical JSON object")
        if args.source_bundle != Path(auth.get("source_bundle", {}).get("path", "")):
            raise BuildError("source bundle path differs from authorization")
        if not (sys.flags.isolated == 1 and sys.flags.no_site == 1 and
                sys.flags.dont_write_bytecode == 1):
            raise BuildError("production builder requires exact -I -B -S")
        if not sys.platform.startswith("linux"):
            raise BuildError("production builder requires Linux")
        if os.readlink("/proc/self/exe") != os.path.realpath(auth.get("source_python", "")):
            raise BuildError("running interpreter realpath differs from authorization")
        executable_fd = os.open("/proc/self/exe", os.O_RDONLY)
        try:
            executable_sha, _ = sha256_fd(executable_fd)
        finally:
            os.close(executable_fd)
        if executable_sha != auth.get("source_python_sha256"):
            raise BuildError("running interpreter SHA differs from authorization")
        execute_authorized(
            auth, args.authorization, args.trusted_authorization_sha256,
            held_context=held_context, logical_process_argv=logical,
            authorization_lease=authorization_lease,
        )
        authorization_lease = None  # ownership transferred and closed
    finally:
        if authorization_lease is not None:
            authorization_lease.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"FAIL_CLOSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
