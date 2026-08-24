#!/usr/bin/env python3
"""Descriptor-closed, result-blind package-v5 native smoke protocol.

This source is executed only through the sealed runtime bootstrap. Every
package byte inspected below arrives on an already-open descriptor. The smoke
does not read scientific rows/results, train, evaluate, invoke EMX, or signal.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import PurePosixPath
from typing import Any, Mapping

import rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap as runtime_bootstrap


PACKAGE_SCHEMA = "controlled_real10k_20k_mars_package_v2"
PACKAGE_VERSION = "v5"
PACKAGE_RECEIPT_SCHEMA = "controlled_real10k_20k_mars_package_receipt_v2"
PACKAGE_QA_SCHEMA = "controlled_real10k_20k_mars_package_independent_qa_required_v3"
PACKAGE_COMMIT_SCHEMA = "controlled_real10k_20k_mars_package_commit_v2"
BUILD_ATTEMPT_BODY_SCHEMA = "controlled_real10k_20k_mars_package_build_attempt_body_v3"
BUILD_ATTEMPT_COMMITTED_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
RUNTIME_CLOSURE_SCHEMA = "controlled_real10k_20k_runtime_closure_v1"
PROCESS_SINGLETON_SCHEMA = "controlled_real10k_20k_process_singleton_contract_v1"
REQUEST_SCHEMA = "controlled_real10k_20k_native_smoke_request_v3"
RESULT_SCHEMA = "controlled_real10k_20k_native_smoke_result_v3"
TEST_ID = "descriptor_closed_package_consumer_graph_v5"
CODE_GO_SCOPE = "MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY"
SHA_INDEX_NAME = "SHA256SUMS.txt"
SINGLETON_LOCK_NAME = "CONTROLLED_SINGLETON.lock"

ROLE_DESTINATIONS = {
    "package_builder_code": "builder/build_controlled_real10k_20k_mars_package.py",
    "runtime_bootstrap_code": "runtime/bootstrap/controlled_real10k_20k_runtime_bootstrap.py",
    "preflight_code": "runtime/scripts/preflight_controlled_real10k_20k_mars.py",
    "materialization_builder_code": "runtime/scripts/build_controlled_real10k_20k_nested.py",
    "materialization_gate_code": "runtime/scripts/run_controlled_real10k_20k_materialization.py",
    "runner_code": "runtime/scripts/run_controlled_real10k_20k_paired.py",
    "trainer_code": "runtime/scripts/train_physical_feature_tandem_inverse.py",
    "evaluator_code": "runtime/scripts/evaluate_controlled_real10k_20k_common.py",
    "runtime_package_init_code": "runtime/rfic_transformer_inverse_design/__init__.py",
    "shared_contract_code": "runtime/rfic_transformer_inverse_design/controlled_real10k_20k_contract.py",
    "splitter_code": "runtime/rfic_transformer_inverse_design/model_splitting.py",
    "runtime_dependency_closure_tree": "runtime/dependencies",
    "runtime_dependency_closure_json": "runtime/contracts/RUNTIME_CLOSURE.json",
    "process_singleton_contract_json": "runtime/contracts/PROCESS_SINGLETON_CONTRACT.json",
    "native_smoke_test": "runtime/tests/controlled_real10k_20k_mars_native_smoke.py",
    "preregistration_v1_json": "protocol/CONTROLLED_EXPERIMENT_PREREGISTRATION_V1.json",
    "preregistration_addendum_v1_1_json": "protocol/CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_1.json",
    "preregistration_addendum_v1_2_json": "protocol/CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_2.json",
    "authoritative_100k_csv": "inputs/authoritative_100k/physical_feature_inverse_training_table.csv",
    "historical_10k_csv": "inputs/historical_10k/multifrequency_physical_feature_training_table.csv",
    "historical_model_summary_json": "inputs/historical_model/physical_feature_tandem_inverse_summary.json",
}
REQUIRED_ROLES = frozenset(ROLE_DESTINATIONS)
PYTHON_CODE_ROLES = frozenset(
    {
        "package_builder_code", "runtime_bootstrap_code", "preflight_code",
        "materialization_builder_code", "materialization_gate_code", "runner_code",
        "trainer_code", "evaluator_code", "runtime_package_init_code",
        "shared_contract_code", "splitter_code", "native_smoke_test",
    }
)
PACKAGE_AUTHORITIES = {
    "native_linux_test_execution": False,
    "data_materialization": False,
    "training": False,
    "common_test_access": False,
    "numerical_metric_access": False,
    "fresh_emx": False,
    "process_signal": False,
}
REQUIRED_GO_BINDING_KEYS = (
    "candidate_output_dirs",
    "code_role_identity",
    "host_expected",
    "native_test_roles",
    "package_build_attempt_body",
    "package_build_attempt_committed",
    "package_commit",
    "package_independent_qa_required",
    "package_manifest",
    "package_receipt",
    "package_role_identity",
    "package_sha_index",
    "preflight_implementation",
    "preflight_one_use_lease",
    "preflight_receipt_root",
    "preflight_terminal_commit",
    "preregistration_role_identity",
    "process_singleton_contract",
    "process_singleton_lock",
    "runtime_dependency_closure",
    "runtime_entrypoints",
    "runtime_expected",
    "source_role_identity",
)
PACKAGE_ENTRYPOINTS = {
    "preflight": ROLE_DESTINATIONS["preflight_code"],
    "materialization": ROLE_DESTINATIONS["materialization_gate_code"],
    "runner": ROLE_DESTINATIONS["runner_code"],
    "trainer": ROLE_DESTINATIONS["trainer_code"],
    "evaluator": ROLE_DESTINATIONS["evaluator_code"],
    "native_smoke": ROLE_DESTINATIONS["native_smoke_test"],
}
SEALED_ENTRYPOINT_ROLE = {
    "materialization": "materialization_gate_code",
    "runner": "runner_code",
    "trainer": "trainer_code",
    "evaluator": "evaluator_code",
    "native_smoke": "native_smoke_test",
}
PACKAGE_IMPORT_GRAPH = {
    "materialization_builder_code": [
        "runtime_dependency_closure_tree", "shared_contract_code", "splitter_code"
    ],
    "materialization_gate_code": [
        "materialization_builder_code", "runtime_dependency_closure_tree",
        "shared_contract_code", "splitter_code",
    ],
    "runner_code": ["runtime_dependency_closure_tree", "shared_contract_code"],
    "trainer_code": ["runtime_dependency_closure_tree", "splitter_code"],
    "evaluator_code": [
        "runtime_dependency_closure_tree", "runtime_package_init_code",
        "shared_contract_code", "trainer_code",
    ],
    "splitter_code": ["runtime_dependency_closure_tree"],
}
FROZEN_CONSTANTS = {
    "FROZEN_AUTHORITATIVE_100K_SHA256": "68468eb2d3678aa0793157c1c647e975f60e8ec1673c259050ababe9fd1ff08a",
    "FROZEN_HISTORICAL_10K_SHA256": "3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8",
    "FROZEN_HISTORICAL_SUMMARY_SHA256": "90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa",
    "FROZEN_TRAINER_SHA256": "92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be",
    "FROZEN_PREREGISTRATION_V1_SHA256": "19aca7778f4974fd3e7eadaca8b291783e8e08e99a53a9dca70b070a4bf16417",
    "FROZEN_PREREGISTRATION_ADDENDUM_V1_1_SHA256": "9f1eb0e071ade0e5a42597b4242409282ed8d34cf159104f71df2d4d0d0a8633",
    "FROZEN_PREREGISTRATION_ADDENDUM_V1_2_SHA256": "fb7c7d0f9e206e3743cf795a544004e570842f26495903ad0eafdd5f909f37a9",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_json(payload: bytes, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, f"{label} duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> Any:
        raise RuntimeError(f"{label} forbidden constant: {token}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not exact UTF-8 JSON") from exc
    _require(type(value) is dict, f"{label} is not an object")
    return value


def _canonical_json_sha(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return _sha256(payload)


def _require_exact_json_equal(actual: Any, expected: Any, label: str) -> None:
    """Reject Python's JSON-hostile ``bool == int`` equality alias."""

    def compare(left: Any, right: Any, location: str) -> None:
        _require(type(left) is type(right), f"{label} type at {location}")
        if type(right) is dict:
            _require(set(left) == set(right), f"{label} keys at {location}")
            for key in sorted(right):
                compare(left[key], right[key], f"{location}.{key}")
        elif type(right) is list:
            _require(len(left) == len(right), f"{label} length at {location}")
            for index, (left_item, right_item) in enumerate(
                zip(left, right, strict=True)
            ):
                compare(left_item, right_item, f"{location}[{index}]")
        else:
            _require(left == right, f"{label} value at {location}")

    compare(actual, expected, "$")


def _safe_relative(raw: Any, label: str) -> PurePosixPath:
    _require(type(raw) is str and raw and "\x00" not in raw and "\\" not in raw, label)
    path = PurePosixPath(raw)
    _require(not path.is_absolute(), f"{label} is absolute")
    _require(all(part not in {"", ".", ".."} for part in path.parts), f"{label} traverses")
    return path


def _read_fd(descriptor: int, label: str) -> bytes:
    _require(type(descriptor) is int and descriptor >= 0, f"{label} descriptor")
    before = os.fstat(descriptor)
    _require(stat.S_ISREG(before.st_mode), f"{label} is not a regular file")
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    after = os.fstat(descriptor)
    _require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mode)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mode),
        f"{label} changed during read",
    )
    payload = b"".join(chunks)
    _require(len(payload) == after.st_size, f"{label} size mismatch")
    return payload


def _descriptor_number(path: Any, label: str) -> int:
    _require(
        type(path) is str
        and (path.startswith("/proc/self/fd/") or path.startswith("/dev/fd/")),
        f"{label} is not a descriptor path",
    )
    try:
        return int(path.rsplit("/", 1)[1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"{label} descriptor path is malformed") from exc


def _read_snapshot(record: Any, label: str, *, role_file: bool = False) -> bytes:
    keys = {"descriptor_path", "display_path", "sha256"}
    if role_file:
        keys.add("kind")
    _require(type(record) is dict and set(record) == keys, f"{label} record keyset")
    if role_file:
        _require(record["kind"] == "file", f"{label} role kind")
    _require(type(record["display_path"]) is str and record["display_path"], f"{label} display")
    _require(_is_sha256(record["sha256"]), f"{label} SHA")
    payload = _read_fd(_descriptor_number(record["descriptor_path"], label), label)
    _require(_sha256(payload) == record["sha256"], f"{label} snapshot SHA")
    return payload


def _namespace(payload: bytes, display_path: str, name: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "__name__": name,
        "__file__": display_path,
        "__package__": "controlled_entrypoints",
        "__cached__": None,
    }
    exec(compile(payload.decode("utf-8"), display_path, "exec"), namespace)
    return namespace


def _artifact_maps(
    manifest: Mapping[str, Any], role_requests: Mapping[str, Any]
) -> tuple[dict[str, bytes], dict[str, str], set[str], dict[str, bytes]]:
    artifacts = manifest["artifacts"]
    _require(type(artifacts) is list and len(artifacts) == len(REQUIRED_ROLES), "artifact count")
    _require([item.get("role") for item in artifacts] == sorted(REQUIRED_ROLES), "artifact order")
    _require(type(role_requests) is dict and set(role_requests) == REQUIRED_ROLES, "request role set")
    snapshots: dict[str, bytes] = {}
    consumed: dict[str, str] = {}
    physical_paths: set[str] = set()
    tree_payloads: dict[str, bytes] = {}
    role_identity: dict[str, dict[str, str]] = {}
    for artifact in artifacts:
        _require(type(artifact) is dict, "artifact object")
        role = artifact["role"]
        _require(artifact.get("path") == ROLE_DESTINATIONS[role], f"role path: {role}")
        requested = role_requests[role]
        if role == "runtime_dependency_closure_tree":
            tree_keys = {
                "role", "kind", "path", "sha256", "inventory_sha256", "file_count",
                "directory_count", "size_bytes", "mode_octal", "source_path_at_build", "members",
            }
            _require(set(artifact) == tree_keys and artifact["kind"] == "tree", "tree artifact")
            _require(
                type(requested) is dict
                and set(requested)
                == {"kind", "display_path", "sha256", "inventory_sha256", "members"}
                and requested["kind"] == "tree",
                "tree request",
            )
            _require(requested["display_path"].endswith(artifact["path"]), "tree display")
            _require(
                requested["sha256"] == artifact["sha256"]
                and requested["inventory_sha256"] == artifact["inventory_sha256"],
                "tree request binding",
            )
            members = artifact["members"]
            requested_members = requested["members"]
            _require(type(members) is list and members, "tree members")
            _require(
                type(requested_members) is list and len(requested_members) == len(members),
                "tree requested members",
            )
            _require([item["path"] for item in members] == sorted(item["path"] for item in members), "tree order")
            normalized: list[dict[str, Any]] = []
            directories: set[str] = set()
            total_size = 0
            prefix = PurePosixPath(artifact["path"])
            for declared, member_request in zip(members, requested_members, strict=True):
                _require(
                    type(declared) is dict
                    and set(declared) == {"path", "sha256", "size_bytes", "mode_octal", "nlink"},
                    "tree member schema",
                )
                _require(
                    type(declared["size_bytes"]) is int
                    and declared["size_bytes"] >= 0
                    and type(declared["nlink"]) is int,
                    "tree member numeric types",
                )
                _require(
                    type(member_request) is dict
                    and set(member_request)
                    == {"path", "descriptor_path", "display_path", "sha256", "size_bytes"},
                    "tree member request schema",
                )
                path = _safe_relative(declared["path"], "tree member path")
                _require(prefix in path.parents, "tree member prefix")
                _require(member_request["path"] == path.as_posix(), "tree requested path")
                _require(member_request["display_path"].endswith(path.as_posix()), "tree member display")
                _require(member_request["sha256"] == declared["sha256"], "tree requested SHA")
                payload = _read_fd(
                    _descriptor_number(member_request["descriptor_path"], "tree member"),
                    "tree member",
                )
                _require(
                    type(member_request["size_bytes"]) is int
                    and _sha256(payload) == declared["sha256"]
                    and len(payload) == declared["size_bytes"] == member_request["size_bytes"]
                    and declared["mode_octal"] == "0444"
                    and declared["nlink"] == 1,
                    "tree member identity",
                )
                relative = path.relative_to(prefix).as_posix()
                normalized.append({**declared, "path": relative})
                tree_payloads[relative] = payload
                physical_paths.add(path.as_posix())
                total_size += len(payload)
                parent = PurePosixPath(relative).parent
                while parent.parts:
                    directories.add(parent.as_posix())
                    parent = parent.parent
            _require(
                type(artifact["file_count"]) is int
                and type(artifact["directory_count"]) is int
                and type(artifact["size_bytes"]) is int
                and artifact["sha256"] == _canonical_json_sha(normalized)
                and artifact["file_count"] == len(members)
                and artifact["directory_count"] == len(directories)
                and artifact["size_bytes"] == total_size
                and artifact["mode_octal"] == "0555",
                "tree aggregate",
            )
            consumed[role] = artifact["sha256"]
        else:
            file_keys = {
                "role", "kind", "path", "sha256", "size_bytes", "mode_octal",
                "nlink", "source_path_at_build",
            }
            _require(set(artifact) == file_keys and artifact["kind"] == "file", f"file artifact: {role}")
            payload = _read_snapshot(requested, f"role {role}", role_file=True)
            _require(requested["display_path"].endswith(artifact["path"]), f"role display: {role}")
            _require(
                type(artifact["size_bytes"]) is int
                and type(artifact["nlink"]) is int
                and requested["sha256"] == artifact["sha256"]
                and len(payload) == artifact["size_bytes"]
                and artifact["mode_octal"] == "0444"
                and artifact["nlink"] == 1,
                f"role identity: {role}",
            )
            snapshots[role] = payload
            consumed[role] = artifact["sha256"]
            physical_paths.add(artifact["path"])
        role_identity[role] = {
            "kind": artifact["kind"],
            "path": artifact["path"],
            "sha256": artifact["sha256"],
        }
    _require_exact_json_equal(manifest["role_identity"], role_identity, "manifest role identity")
    return snapshots, consumed, physical_paths, tree_payloads


def _validate_package_receipts(
    request: Mapping[str, Any], manifest_sha: str, role_identity: Mapping[str, Any]
) -> tuple[bytes, bytes, bytes, bytes, bytes, bytes]:
    receipt_bytes = _read_snapshot(request["receipt"], "package receipt")
    qa_bytes = _read_snapshot(request["independent_qa_required"], "package QA")
    index_bytes = _read_snapshot(request["sha_index"], "SHA index")
    commit_bytes = _read_snapshot(request["package_commit"], "package commit")
    attempt_body_bytes = _read_snapshot(
        request["package_build_attempt_body"], "build-attempt body"
    )
    attempt_committed_bytes = _read_snapshot(
        request["package_build_attempt_committed"], "build-attempt committed marker"
    )
    receipt = _strict_json(receipt_bytes, "package receipt")
    _require(
        set(receipt)
        == {
            "schema", "status", "package_version", "manifest", "independent_qa_required",
            "role_identity", "authorities", "execution_authorized", "result_accessed",
            "numerical_metrics_accessed",
        },
        "package receipt keyset",
    )
    _require(
        receipt["schema"] == PACKAGE_RECEIPT_SCHEMA
        and receipt["status"] == "PASS_PREPARED_AWAITING_INDEPENDENT_QA"
        and receipt["package_version"] == PACKAGE_VERSION
        and receipt["manifest"] == {"path": "MANIFEST.json", "sha256": manifest_sha}
        and receipt["independent_qa_required"]
        == {"path": "INDEPENDENT_QA_REQUIRED.json", "sha256": _sha256(qa_bytes)}
        and receipt["execution_authorized"] is False
        and receipt["result_accessed"] is False
        and receipt["numerical_metrics_accessed"] is False,
        "package receipt scalar binding",
    )
    _require_exact_json_equal(receipt["role_identity"], role_identity, "receipt roles")
    _require_exact_json_equal(receipt["authorities"], PACKAGE_AUTHORITIES, "receipt authorities")
    qa = _strict_json(qa_bytes, "package QA")
    _require(
        set(qa)
        == {
            "schema", "verdict", "package_manifest", "required_go_receipt",
            "required_native_test_roles", "required_role_identity", "authorities",
            "execution_authorized",
        },
        "package QA keyset",
    )
    _require(
        qa["schema"] == PACKAGE_QA_SCHEMA
        and qa["verdict"] == "NO_GO_PENDING_EXTERNAL_CODE_QA",
        "package QA schema/verdict",
    )
    _require(
        qa["package_manifest"] == {"path": "MANIFEST.json", "sha256": manifest_sha},
        "QA manifest",
    )
    _require_exact_json_equal(
        qa["required_go_receipt"],
        {
            "issuer": "independent_qa",
            "verdict": "GO",
            "exact_binding_keyset_required": True,
            "required_binding_keys": list(REQUIRED_GO_BINDING_KEYS),
            "maximum_age_seconds": 21600,
            "future_clock_skew_seconds": 0,
            "one_use": True,
        },
        "package QA GO interface",
    )
    _require_exact_json_equal(qa["required_role_identity"], role_identity, "QA roles")
    _require(qa["required_native_test_roles"] == ["native_smoke_test"], "QA smoke role")
    _require(qa["execution_authorized"] is False, "QA execution authority")
    _require_exact_json_equal(qa["authorities"], PACKAGE_AUTHORITIES, "QA authorities")
    commit = _strict_json(commit_bytes, "package commit")
    _require(
        set(commit)
        == {
            "schema", "status", "package_version", "manifest", "receipt",
            "independent_qa_required", "sha256sums", "required_external_pass_attempt",
            "creation_order_contract", "authorities", "execution_authorized",
        },
        "package commit keyset",
    )
    _require(
        commit["schema"] == PACKAGE_COMMIT_SCHEMA
        and commit["status"] == "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT"
        and commit["package_version"] == PACKAGE_VERSION
        and commit["manifest"] == {"path": "MANIFEST.json", "sha256": manifest_sha}
        and commit["receipt"] == {"path": "RECEIPT.json", "sha256": _sha256(receipt_bytes)}
        and commit["independent_qa_required"]
        == {"path": "INDEPENDENT_QA_REQUIRED.json", "sha256": _sha256(qa_bytes)}
        and commit["sha256sums"] == {"path": SHA_INDEX_NAME, "sha256": _sha256(index_bytes)}
        and commit["execution_authorized"] is False,
        "package commit binding",
    )
    _require_exact_json_equal(
        commit["required_external_pass_attempt"],
        {
            "body": {
                "path": request["package_build_attempt_body"]["display_path"],
                "schema": BUILD_ATTEMPT_BODY_SCHEMA,
                "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            },
            "committed": {
                "path": request["package_build_attempt_committed"]["display_path"],
                "schema": BUILD_ATTEMPT_COMMITTED_SCHEMA,
                "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
            },
        },
        "package commit external attempt",
    )
    _require_exact_json_equal(
        commit["creation_order_contract"],
        {"this_member_created_last": True, "post_commit_package_file_creation_permitted": False},
        "package commit creation order",
    )
    _require_exact_json_equal(commit["authorities"], PACKAGE_AUTHORITIES, "commit authorities")
    attempt = _strict_json(attempt_body_bytes, "build-attempt body")
    _require(
        set(attempt)
        == {
            "schema", "status", "started_utc", "completed_utc", "invocation",
            "observed_identity", "package", "partial_output_preserved", "authorities",
            "execution_authorized",
        },
        "build-attempt body keyset",
    )
    package = attempt["package"]
    _require(
        type(package) is dict
        and set(package)
        == {
            "path", "manifest_sha256", "receipt_sha256",
            "independent_qa_required_sha256", "sha256sums_sha256",
            "package_commit_sha256", "file_count",
        }
        and attempt["schema"] == BUILD_ATTEMPT_BODY_SCHEMA
        and attempt["status"] == "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
        and package["manifest_sha256"] == manifest_sha
        and package["receipt_sha256"] == _sha256(receipt_bytes)
        and package["independent_qa_required_sha256"] == _sha256(qa_bytes)
        and package["sha256sums_sha256"] == _sha256(index_bytes)
        and package["package_commit_sha256"] == _sha256(commit_bytes)
        and type(package["file_count"]) is int
        and package["file_count"] > 0
        and attempt["partial_output_preserved"] is False
        and attempt["execution_authorized"] is False,
        "build-attempt body binding",
    )
    _require_exact_json_equal(attempt["authorities"], PACKAGE_AUTHORITIES, "attempt authorities")
    observed = attempt["observed_identity"]
    _require(
        type(observed) is dict
        and set(observed)
        == {
            "package_spec_sha256", "builder_sha256", "package_output_device",
            "package_output_inode",
        }
        and _is_sha256(observed["package_spec_sha256"])
        and _is_sha256(observed["builder_sha256"])
        and type(observed["package_output_device"]) is int
        and type(observed["package_output_inode"]) is int,
        "build-attempt observed package identity",
    )

    attempt_committed = _strict_json(
        attempt_committed_bytes, "build-attempt committed marker"
    )
    _require(
        set(attempt_committed)
        == {
            "schema", "status", "committed_utc", "body", "package_commit",
            "package_root", "attempt_root", "attempt_parent", "publication",
            "authorities", "execution_authorized",
        }
        and attempt_committed["schema"] == BUILD_ATTEMPT_COMMITTED_SCHEMA
        and attempt_committed["status"]
        == "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED"
        and attempt_committed["execution_authorized"] is False,
        "build-attempt committed keyset/status",
    )
    _require_exact_json_equal(
        attempt_committed["body"],
        {
            "path": request["package_build_attempt_body"]["display_path"],
            "sha256": _sha256(attempt_body_bytes),
            "schema": BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        },
        "build-attempt committed body binding",
    )
    _require_exact_json_equal(
        attempt_committed["package_commit"],
        {
            "path": request["package_commit"]["display_path"],
            "sha256": _sha256(commit_bytes),
            "schema": PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        },
        "build-attempt committed package binding",
    )

    def require_directory_identity(value: Any, label: str) -> None:
        _require(
            type(value) is dict
            and set(value) == {"path", "st_dev", "st_ino", "mode_octal"}
            and type(value["path"]) is str
            and type(value["st_dev"]) is int
            and type(value["st_ino"]) is int
            and type(value["mode_octal"]) is str
            and len(value["mode_octal"]) == 4
            and all(character in "01234567" for character in value["mode_octal"]),
            label,
        )

    package_root = attempt_committed["package_root"]
    attempt_root = attempt_committed["attempt_root"]
    attempt_parent = attempt_committed["attempt_parent"]
    require_directory_identity(package_root, "build-attempt package-root identity")
    require_directory_identity(attempt_root, "build-attempt root identity")
    require_directory_identity(attempt_parent, "build-attempt parent identity")
    body_path = PurePosixPath(request["package_build_attempt_body"]["display_path"])
    _require(
        package_root
        == {
            "path": package["path"],
            "st_dev": observed["package_output_device"],
            "st_ino": observed["package_output_inode"],
            "mode_octal": "0555",
        }
        and attempt_root["path"] == body_path.parent.as_posix()
        and attempt_root["mode_octal"] == "0555"
        and attempt_parent["path"] == body_path.parent.parent.as_posix(),
        "build-attempt directory closure binding",
    )
    _require_exact_json_equal(
        attempt_committed["publication"],
        {
            "body_file_fsync": True,
            "attempt_root_fsync": True,
            "attempt_parent_fsync": True,
            "attempt_root_frozen": True,
            "continuity_verified": True,
            "terminal_inode_reserved_create_once_before_freeze": True,
            "terminal_bytes_published_after_durability": True,
            "post_commit_attempt_file_creation_permitted": False,
        },
        "build-attempt durability publication",
    )
    _require_exact_json_equal(
        attempt_committed["authorities"], PACKAGE_AUTHORITIES, "committed authorities"
    )
    return (
        receipt_bytes,
        qa_bytes,
        index_bytes,
        commit_bytes,
        attempt_body_bytes,
        attempt_committed_bytes,
    )


def _validate_runtime_contract(
    manifest: Mapping[str, Any],
    snapshots: Mapping[str, bytes],
    tree_payloads: Mapping[str, bytes],
) -> dict[str, Any]:
    runtime = manifest["runtime"]
    _require(
        type(runtime) is dict
        and set(runtime)
        == {"entrypoints", "import_graph", "dependency_closure", "process_singleton_contract"},
        "manifest runtime keyset",
    )
    _require_exact_json_equal(runtime["entrypoints"], PACKAGE_ENTRYPOINTS, "package entrypoint map")
    _require_exact_json_equal(runtime["import_graph"], PACKAGE_IMPORT_GRAPH, "package import graph")
    closure = _strict_json(snapshots["runtime_dependency_closure_json"], "runtime closure")
    _require(closure["schema"] == RUNTIME_CLOSURE_SCHEMA, "runtime closure schema")
    _require(set(closure["entrypoints"]) == set(SEALED_ENTRYPOINT_ROLE), "sealed entrypoint set")
    for entrypoint, role in SEALED_ENTRYPOINT_ROLE.items():
        record = closure["entrypoints"][entrypoint]
        role_members = [item for item in closure["members"] if item["role"] == role]
        _require(
            set(record) == {"member", "sha256", "display_path", "role"}
            and len(role_members) == 1
            and record["role"] == role
            and record["sha256"] == role_members[0]["sha256"],
            f"sealed entrypoint binding: {entrypoint}",
        )
    expected_tree_paths = {closure["pure_archive"]["path"]} | {
        item["path"] for item in closure["native_extensions"]
    } | {item["path"] for item in closure["native_libraries"]}
    _require(set(tree_payloads) == expected_tree_paths, "runtime tree/closure file set")
    for record in [
        closure["pure_archive"],
        *closure["native_extensions"],
        *closure["native_libraries"],
    ]:
        payload = tree_payloads[record["path"]]
        _require(
            _sha256(payload) == record["sha256"] and len(payload) == record["size_bytes"],
            "runtime tree identity",
        )
    dependency = runtime["dependency_closure"]
    _require(
        dependency["schema"] == closure["schema"]
        and dependency["inventory_path"]
        == ROLE_DESTINATIONS["runtime_dependency_closure_json"]
        and dependency["inventory_sha256"]
        == _sha256(snapshots["runtime_dependency_closure_json"])
        and dependency["tree_path"] == ROLE_DESTINATIONS["runtime_dependency_closure_tree"],
        "manifest runtime closure declaration",
    )
    _require_exact_json_equal(
        dependency["pure_archive"], closure["pure_archive"], "runtime pure archive"
    )
    _require_exact_json_equal(dependency["python"], closure["python"], "runtime Python")
    _require_exact_json_equal(dependency["numpy"], closure["numpy"], "runtime NumPy")
    singleton = _strict_json(
        snapshots["process_singleton_contract_json"], "singleton contract"
    )
    _require(singleton["schema"] == PROCESS_SINGLETON_SCHEMA, "singleton schema")
    _require(
        singleton["lock"]["relative_path"] == SINGLETON_LOCK_NAME
        and singleton["lock"]["sha256"] == _sha256(b"")
        and singleton["lock"]["mechanism"] == "fcntl.flock"
        and singleton["lock"]["operation"] == "LOCK_EX|LOCK_NB"
        and singleton["lifetime"]["full_lifetime_required"] is True,
        "singleton lock/lifetime",
    )
    singleton_declaration = runtime["process_singleton_contract"]
    _require(
        singleton_declaration["schema"] == PROCESS_SINGLETON_SCHEMA
        and singleton_declaration["path"]
        == ROLE_DESTINATIONS["process_singleton_contract_json"]
        and singleton_declaration["sha256"]
        == _sha256(snapshots["process_singleton_contract_json"])
        and singleton_declaration["lock_path"] == SINGLETON_LOCK_NAME
        and singleton_declaration["lock_sha256"] == _sha256(b""),
        "manifest singleton declaration",
    )
    _require_exact_json_equal(
        singleton_declaration["protected_entrypoints"],
        singleton["protected_entrypoints"],
        "singleton protected entrypoints",
    )
    return closure


def run(request: dict[str, Any]) -> dict[str, Any]:
    request_keys = {
        "schema", "manifest", "receipt", "independent_qa_required", "sha_index",
        "package_commit", "package_build_attempt_body",
        "package_build_attempt_committed", "process_singleton_lock",
        "indexed_files", "roles", "runtime_manifest_sha256",
    }
    _require(set(request) == request_keys, "native smoke request keyset")
    _require(request["schema"] == REQUEST_SCHEMA, "native smoke request schema")
    _require(_is_sha256(request["runtime_manifest_sha256"]), "runtime manifest SHA")
    active_runtime = runtime_bootstrap.require_active_runtime(
        "native_smoke", request["runtime_manifest_sha256"]
    )
    _require(
        os.environ.get("CONTROLLED_REAL10K_20K_PREFLIGHT_ONLY") == "1",
        "preflight-only boundary",
    )
    _require(sys.flags.isolated == 1, "native smoke lacks -I")
    _require(sys.flags.no_site == 1, "native smoke lacks -S")
    _require(sys.dont_write_bytecode is True, "native smoke lacks -B")
    _require(sys.flags.optimize == 0, "native smoke was optimized")

    manifest_bytes = _read_snapshot(request["manifest"], "manifest")
    manifest = _strict_json(manifest_bytes, "manifest")
    manifest_sha = _sha256(manifest_bytes)
    _require(
        set(manifest)
        == {
            "schema", "package_version", "build_spec", "required_roles",
            "role_destinations", "role_identity", "artifacts", "runtime", "authorities",
            "execution_authorized", "result_accessed", "numerical_metrics_accessed",
        },
        "manifest keyset",
    )
    _require(
        manifest["schema"] == PACKAGE_SCHEMA
        and manifest["package_version"] == PACKAGE_VERSION
        and manifest["required_roles"] == sorted(REQUIRED_ROLES)
        and manifest["role_destinations"] == ROLE_DESTINATIONS
        and manifest["execution_authorized"] is False
        and manifest["result_accessed"] is False
        and manifest["numerical_metrics_accessed"] is False,
        "manifest package contract",
    )
    _require_exact_json_equal(
        manifest["authorities"], PACKAGE_AUTHORITIES, "manifest authorities"
    )
    snapshots, consumed, physical_paths, tree_payloads = _artifact_maps(
        manifest, request["roles"]
    )
    _require(
        request["runtime_manifest_sha256"]
        == consumed["runtime_dependency_closure_json"],
        "active runtime is not package-bound",
    )
    receipt_bytes, qa_bytes, index_bytes, _, _, _ = _validate_package_receipts(
        request, manifest_sha, manifest["role_identity"]
    )
    lock_bytes = _read_snapshot(request["process_singleton_lock"], "singleton lock")
    _require(
        lock_bytes == b""
        and request["process_singleton_lock"]["sha256"] == _sha256(b""),
        "singleton lock identity",
    )

    indexed_requests = request["indexed_files"]
    _require(type(indexed_requests) is dict, "indexed descriptor map")
    expected_indexed = physical_paths | {
        SINGLETON_LOCK_NAME,
        "MANIFEST.json",
        "RECEIPT.json",
        "INDEPENDENT_QA_REQUIRED.json",
    }
    _require(set(indexed_requests) == expected_indexed, "indexed descriptor set")
    indexed: dict[str, str] = {}
    previous = ""
    for line in index_bytes.decode("utf-8").splitlines():
        _require(line.count("  ") == 1, "SHA-index line format")
        digest, raw_path = line.split("  ", 1)
        path = _safe_relative(raw_path, "SHA-index path").as_posix()
        _require(path > previous and path not in indexed, "SHA-index order/duplicate")
        previous = path
        payload = _read_snapshot(indexed_requests[path], f"indexed {path}")
        _require(
            _is_sha256(digest) and _sha256(payload) == digest,
            f"SHA-index identity: {path}",
        )
        indexed[path] = digest
    _require(set(indexed) == expected_indexed, "SHA-index closure")
    _require(
        indexed["MANIFEST.json"] == manifest_sha
        and indexed["RECEIPT.json"] == _sha256(receipt_bytes)
        and indexed["INDEPENDENT_QA_REQUIRED.json"] == _sha256(qa_bytes),
        "SHA-index metadata bindings",
    )
    closure = _validate_runtime_contract(manifest, snapshots, tree_payloads)

    compiled_count = 0
    for role in sorted(PYTHON_CODE_ROLES):
        compile(
            snapshots[role].decode("utf-8"),
            request["roles"][role]["display_path"],
            "exec",
        )
        compiled_count += 1
    preflight = _namespace(
        snapshots["preflight_code"],
        request["roles"]["preflight_code"]["display_path"],
        "controlled_entrypoints.native_smoke_preflight",
    )
    _require(preflight["CODE_GO_SCOPE"] == CODE_GO_SCOPE, "preflight GO scope")
    _require(preflight["PACKAGE_REQUIRED_ROLES"] == REQUIRED_ROLES, "preflight role set")
    for name, expected in FROZEN_CONSTANTS.items():
        _require(preflight[name] == expected, f"frozen constant: {name}")

    consumers: dict[str, dict[str, Any]] = {}
    for entrypoint in ("materialization", "runner", "trainer", "evaluator"):
        role = SEALED_ENTRYPOINT_ROLE[entrypoint]
        source, origin = runtime_bootstrap.active_member_source(role, consumed[role])
        consumers[entrypoint] = _namespace(
            source,
            request["roles"][role]["display_path"],
            f"controlled_entrypoints.native_smoke_{entrypoint}",
        )
        _require(
            _sha256(source) == consumed[role] and origin.startswith("descriptor-zip:"),
            f"sealed consumer: {entrypoint}",
        )
    _require(callable(consumers["materialization"].get("main")), "materialization consumer")
    _require(callable(consumers["runner"].get("_run_contract")), "runner consumer")
    _require(callable(consumers["trainer"].get("_parse_args")), "trainer consumer")
    _require(callable(consumers["evaluator"].get("main")), "evaluator consumer")
    _require(
        consumers["runner"].get("runtime_bootstrap") is runtime_bootstrap,
        "runner runtime binding",
    )
    for module_name in (
        "numpy",
        "numpy._core._multiarray_umath",
        "rfic_transformer_inverse_design.controlled_real10k_20k_contract",
        "rfic_transformer_inverse_design.model_splitting",
    ):
        module = sys.modules.get(module_name)
        origin = str(getattr(module, "__file__", "")) if module is not None else ""
        _require(
            module is not None
            and origin.startswith(("descriptor-zip:/proc/self/fd/", "/proc/self/fd/")),
            f"descriptor-bound import: {module_name}",
        )

    return {
        "schema": RESULT_SCHEMA,
        "status": "PASS",
        "test_id": TEST_ID,
        "manifest_sha256": manifest_sha,
        "sha_index_sha256": _sha256(index_bytes),
        "package_commit_sha256": request["package_commit"]["sha256"],
        "package_build_attempt_body_sha256": request[
            "package_build_attempt_body"
        ]["sha256"],
        "package_build_attempt_committed_sha256": request[
            "package_build_attempt_committed"
        ]["sha256"],
        "runtime_manifest_sha256": request["runtime_manifest_sha256"],
        "role_count": len(consumed),
        "compiled_python_role_count": compiled_count,
        "consumed_role_sha256": consumed,
        "checks": {
            "isolated_python_I_B_S": True,
            "exact_package_v5_role_destinations": True,
            "descriptor_snapshots_only": True,
            "package_commit_and_external_attempt_body_committed_bound": True,
            "package_sha_closure": True,
            "runtime_dependency_closure_bound": True,
            "process_singleton_contract_bound": True,
            "sealed_materialization_runner_trainer_evaluator_imported": True,
            "numpy_shared_splitter_descriptor_bound": True,
            "active_descriptor_runtime_exact": True,
            "result_blind": True,
            "no_training_metrics_emx_or_signal": True,
        },
        "runtime": active_runtime,
    }


def main() -> int:
    request = _strict_json(sys.stdin.buffer.read(), "native smoke request")
    result = run(request)
    sys.stdout.write(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
