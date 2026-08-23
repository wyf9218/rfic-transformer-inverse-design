#!/usr/bin/env python3
"""Result-blind static compatibility check against the frozen v8 producer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import adapt_complete_emx_interface_v8 as adapter


WORKSPACE = Path(__file__).resolve().parents[3]
V8 = (
    WORKSPACE
    / "reports/historical_200k_fixed10k_mars_physical_20260822"
    / "post_stage06_release_chain_v8_prepared_20260822T142204Z"
)
EXPECTED_SHA256 = {
    "build_complete_emx_interface_v5.py": "7836b51e51acc9d1d96900fed79687f8565e0a58abbecb06ee6f9e0c4129ff08",
    "release_chain_common_v5.py": "74beda9e5461cdcdb1c7b03f45d813993561f1efb55a194ad75d17801776f607",
    "RELEASE_CHAIN_V5_CONTRACT.json": "42387f9f3372d019eed9d964a4bc9832a52c2c00cd645c02edca6309914c9dd6",
    "EMX_RESULT_INTERFACE_TEMPLATE_FROZEN.json": "e481b7b1281c56dc05d99ff144f66f9fc3e3bc7f654717d4dafd98b519130b49",
    "SHA256SUMS": "9fbef6b48567d8055af152f5bd60821e31ef3d44e2013754ca929efb81504a5a",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_json(name: str) -> dict[str, Any]:
    value = adapter.strict_json_bytes((V8 / name).read_bytes(), f"frozen v8 {name}")
    require(isinstance(value, dict), f"frozen v8 {name} must be an object")
    return value


def main() -> int:
    observed_sha = {name: sha((V8 / name).read_bytes()) for name in EXPECTED_SHA256}
    require(observed_sha == EXPECTED_SHA256, "frozen v8 producer bytes changed")
    contract = read_json("RELEASE_CHAIN_V5_CONTRACT.json")
    template = read_json("EMX_RESULT_INTERFACE_TEMPLATE_FROZEN.json")
    common_source = (V8 / "release_chain_common_v5.py").read_text(encoding="utf-8")
    reference_raw = Path(adapter.__file__).with_name(
        adapter.FROZEN_RELEASE_CONTRACT_REFERENCE_NAME
    ).read_bytes()
    require(
        sha(reference_raw) == EXPECTED_SHA256["RELEASE_CHAIN_V5_CONTRACT.json"],
        "v8 frozen-v8 contract reference SHA drift",
    )
    require(
        adapter.strict_json_bytes(reference_raw, "v8 contract reference") == contract,
        "v8 contract reference payload differs from frozen-v8",
    )
    require(
        set(contract) == adapter.RELEASE_CONTRACT_ROOT_KEYS and len(contract) == 24,
        "frozen-v8 release contract is not exact 24-key shape",
    )
    adapter.validate_release_contract_payload(contract)

    require(contract.get("expected_count") == 7298, "v8 expected_count drift")
    require(
        contract.get("expected_panel_counts")
        == {"extension_k_gt_0p8": 1306, "legacy_k_le_0p8": 5992},
        "v8 panel counts drift",
    )
    watcher = contract["active_watcher"]
    require(
        watcher["process_identity_algorithm"] == adapter.WATCHER_PROCESS_IDENTITY_ALGORITHM,
        "watcher process-identity algorithm drift",
    )
    require(
        watcher["maximum_launch_receipt_after_proc_start_seconds"] == 120
        and adapter.MAXIMUM_LAUNCH_RECEIPT_AFTER_PROC_START_NS == 120_000_000_000,
        "inclusive [0,120s] process receipt window drift",
    )
    require(
        set(contract["semantic_validation_contract"]["authorization_process_identity_fields"])
        == adapter.PROCESS_IDENTITY_KEYS,
        "process identity field contract drift",
    )
    require(
        contract["semantic_validation_contract"]["watcher_launch_receipt_delta_ns_inclusive_range"]
        == [0, 120_000_000_000],
        "process delta boundary contract drift",
    )
    require(
        adapter.WATCHER_LAUNCH_RECEIPT_KEYS
        == {"schema", "status_at_receipt", "started_utc", "host", "pid",
            "stage06_supervisor_pid", "watcher_script", "physical_statistics_script_sha256",
            "v2_statistics_script_sha256", "preregistration_sha256", "expected_physical_count",
            "v2_is_primary_for_final_report", "v1_preserved_as_superseded_physical_evidence",
            "no_clobber"},
        "watcher launch receipt field contract drift",
    )
    go_policy = contract["independent_go"]
    require(go_policy["required_schema"] == adapter.INDEPENDENT_GO_SCHEMA, "independent GO schema drift")
    require(go_policy["required_status"] == adapter.INDEPENDENT_GO_STATUS, "independent GO status drift")
    require(go_policy["receipt_present_in_prepared_bundle"] is False, "prepared package unexpectedly contains GO")
    require(contract["canonical_control"]["stage07_terminal_schema"] == adapter.STAGE07_TERMINAL_SCHEMA, "Stage07 schema drift")
    require(contract["canonical_control"]["stage08_terminal_schema"] == adapter.STAGE08_TERMINAL_SCHEMA, "Stage08 schema drift")
    require(contract["canonical_control"]["controller_terminal_schema"] == adapter.CONTROLLER_TERMINAL_SCHEMA, "controller schema drift")
    require(contract["canonical_control"]["authorization_schema"] == adapter.AUTHORIZATION_SCHEMA, "authorization schema drift")
    require(contract["canonical_control"]["resume_outcome_schema"] == adapter.OUTCOME_SCHEMA, "outcome schema drift")
    require(contract["stage07"]["manifest_schema"] == adapter.STAGE07_MANIFEST_SCHEMA, "Stage07 manifest schema drift")
    require(contract["stage08"]["manifest_schema"] == adapter.STAGE08_MANIFEST_SCHEMA, "Stage08 manifest schema drift")
    require(contract["three_chain"]["terminal_schema"] == adapter.THREE_CHAIN_TERMINAL_SCHEMA, "three-chain terminal schema drift")
    require(contract["three_chain"]["manifest_schema"] == adapter.THREE_CHAIN_MANIFEST_SCHEMA, "three-chain manifest schema drift")
    require(contract["full_band_v3"]["terminal_schema"] == adapter.FULL_BAND_TERMINAL_SCHEMA, "full-band terminal schema drift")
    require(contract["full_band_v3"]["manifest_schema"] == adapter.FULL_BAND_MANIFEST_SCHEMA, "full-band manifest schema drift")
    require(contract["full_band_v3"]["internal_terminal_schema"] == adapter.FULL_BAND_INTERNAL_SCHEMA, "full-band internal schema drift")

    require(set(contract["stage07"]["manifest_exact_artifacts"]) == adapter.MANIFEST_ARTIFACT_KEYS["stage07_manifest"], "Stage07 artifact keys drift")
    require(set(contract["stage08"]["manifest_exact_artifacts"]) == adapter.MANIFEST_ARTIFACT_KEYS["stage08_manifest"], "Stage08 artifact keys drift")
    require(set(contract["three_chain"]["manifest_exact_artifacts"]) == adapter.MANIFEST_ARTIFACT_KEYS["three_chain_manifest"], "three-chain artifact keys drift")
    require(set(contract["full_band_v3"]["manifest_exact_outputs"]) == adapter.MANIFEST_ARTIFACT_KEYS["full_band_v3_manifest"], "full-band output keys drift")

    attempts = contract["preflight_attempts"]
    require(attempts["resume_stage07_08"]["terminal_schema"] == adapter.PREFLIGHT_TERMINAL_SCHEMA, "resume preflight schema drift")
    require(attempts["three_chain"]["terminal_schema"] == adapter.THREE_CHAIN_PREFLIGHT_SCHEMA, "three preflight schema drift")
    require(attempts["full_band_v3"]["terminal_schema"] == adapter.FULL_BAND_PREFLIGHT_SCHEMA, "full preflight schema drift")
    allowed_controllers = set(contract["execution_units"]["allowed_controllers"])
    expected_launcher_keys = {f"launcher_preflight__{name}" for name in allowed_controllers}
    require(expected_launcher_keys == adapter.LAUNCHER_PREFLIGHT_KEYS, "launcher-preflight release keys drift")
    for key in contract["execution_units"]["controller_to_launcher_preflight_key"].values():
        require(attempts[key]["terminal_schema"] == adapter.LAUNCHER_PREFLIGHT_SCHEMA, f"launcher schema drift: {key}")

    require(
        adapter.LAUNCHER_EVIDENCE_KEYS
        == {"bundle_manifest", "common", "contract", "controller", "independent_review_go", "runtime_dependency_manifest"},
        "launcher evidence key set drift",
    )
    require(
        adapter.RESUME_PREFLIGHT_EVIDENCE_KEYS
        == {"bundle_manifest", "contract", "identity_gate", "launcher_authentication_terminal", "review_go",
            "runtime_dependency_manifest", "runtime_interpreter", "sources", "watcher_launch_receipt",
            "watcher_process_identity", "watcher_script"},
        "resume preflight evidence key set drift",
    )
    require(
        adapter.THREE_PREFLIGHT_EVIDENCE_KEYS
        == {"generator", "launcher_authentication_terminal", "runtime_dependency_manifest", "stage08_terminal"},
        "three-chain preflight evidence key set drift",
    )
    require(
        adapter.FULL_PREFLIGHT_EVIDENCE_KEYS
        == {"runtime_dependency_manifest", "launcher_authentication_terminal", "source_bindings", "stage08_terminal"},
        "full-band preflight evidence key set drift",
    )
    require(
        adapter.FULL_SOURCE_BINDING_KEYS
        == {"panel_schema_addendum", "superseded_base_generator", "unchanged_method_preregistration",
            "unchanged_stage06_config", "v3_generator"},
        "full-band source-binding role set drift",
    )
    require(
        adapter.FULL_MANIFEST_INPUT_KEYS
        == {"identity_receipt", "identity_summary", "identity_rows", "stage06_launch_receipt",
            "stage06_terminal_receipt", "stage06_dataset_rows", "stage06_config",
            "stage08_terminal_receipt", "method_preregistration", "auditor_script"},
        "production full-band manifest input role set drift",
    )
    require(
        adapter.FULL_MANIFEST_RUNTIME_KEYS
        == {"rfic_transformer_inverse_design/analysis/extraction.py",
            "rfic_transformer_inverse_design/sim/touchstone.py",
            "rfic_transformer_inverse_design/network_analysis.py",
            "rfic_transformer_inverse_design/dataset.py"},
        "production full-band manifest runtime role set drift",
    )

    checks = contract["terminal_check_contracts"]
    check_bindings = {
        "stage07": "stage07",
        "stage08": "stage08",
        "stage07_08_controller": "controller",
        "resume_stage07_08": "preflight",
        "three_chain_terminal": "three_chain",
        "full_band_v3_terminal": "full_band_v3",
        "three_chain": "three_chain_preflight",
        "full_band_v3": "full_band_v3_preflight",
        "launcher_preflight": "launcher_preflight",
    }
    for contract_key, adapter_key in check_bindings.items():
        require(set(checks[contract_key]) == set(adapter.TERMINAL_SPECS[adapter_key][2]), f"terminal check keys drift: {contract_key}")

    completion = template["completion_contract"]
    expected_fields = {
        "comparison_feature_metric_fields": list(adapter.FEATURE_METRIC_FIELDS),
        "joint_metric_fields": list(adapter.JOINT_METRIC_FIELDS),
        "q_metric_fields": list(adapter.Q_METRIC_FIELDS),
        "fixed_bin_metric_fields": list(adapter.FIXED_BIN_METRIC_FIELDS),
        "stage_count_fields": list(adapter.STAGE_COUNT_FIELDS),
        "paired_feature_row_fields": list(adapter.PAIRED_FEATURE_ROW_FIELDS),
    }
    for key, expected in expected_fields.items():
        require(completion.get(key) == expected, f"template completion fields drift: {key}")

    final = contract["final_interface"]
    require(final["interface_schema"] == adapter.INPUT_SCHEMA, "producer interface schema drift")
    require(final["required_controller_terminal"] is True, "controller terminal is no longer required")
    require(final["required_nested_manifests_and_internal_terminals"] is True, "nested manifest/internal closure is no longer required")
    require(final["full_band_flags_change_15ghz_primary"] is False, "full-band primary-change flag drift")
    require(final["full_band_flags_filter_candidates"] is False, "full-band filter flag drift")
    require(final["superseded_v2_terminal_accepted"] is False, "superseded terminal flag drift")

    require(len(adapter.PRODUCER_TERMINAL_ROLES) == 17, "producer source role count drift")
    require(len(adapter.RELEASE_RECORD_BINDINGS) == 13, "release static-record count drift")
    require(len(adapter.LAUNCHER_PREFLIGHT_KEYS) == 4, "launcher record count drift")
    require(len(adapter.PRODUCER_ROOT_KEYS) == 15, "producer root key count drift")
    require(len(adapter.PORTABLE_ROOT_KEYS) == 17, "portable root key count drift")
    require(len(adapter.COMPATIBILITY_ADAPTER_KEYS) == 16, "compatibility mapping key count drift")
    require(len(adapter.PRODUCER_RUN_KEYS) == 22, "producer run exact key count drift")
    require(len(adapter.PORTABLE_RUN_KEYS) == 24, "portable run exact key count drift")
    require(len(adapter.PRODUCER_IDENTITY_KEYS) == 8, "producer identity exact key count drift")
    require(len(adapter.PORTABLE_IDENTITY_KEYS) == 9, "portable identity exact key count drift")
    require(len(adapter.PRODUCER_STAGE06_KEYS) == 7, "producer Stage06 exact key count drift")
    require(len(adapter.PORTABLE_STAGE06_KEYS) == 8, "portable Stage06 exact key count drift")
    require(len(adapter.PRODUCER_METRIC_CONTRACT_KEYS) == 15, "producer metric exact key count drift")
    require(len(adapter.PORTABLE_METRIC_CONTRACT_KEYS) == 18, "portable metric exact key count drift")
    require(len(adapter.NESTED_ARTIFACT_ROLES) == 41, "nested artifact role count drift")
    require(len(adapter.STAGE_COUNT_ROWS) == 6, "funnel row count drift")
    require([row["stage_order"] for row in adapter.STAGE_COUNT_ROWS] == list(range(6)), "funnel order drift")
    require([row["completed"] for row in adapter.STAGE_COUNT_ROWS] == [10000, 7926, 7373, 7298, 7298, 7298], "funnel counts drift")
    require(
        adapter.SURVIVOR_SCOPE
        == {
            "original_target_count": 10000,
            "analytical_pass_count": 7926,
            "cadence_pass_count": 7373,
            "calibre_gds_pass_count": 7298,
            "fresh_emx_numeric_count": 7298,
            "legacy_survivor_count": 5992,
            "extension_survivor_count": 1306,
            "statistics_conditioning": "survivor_conditional_not_original_10000_unconditional_accuracy",
        },
        "structured survivor scope drift",
    )

    producer_source = (V8 / "build_complete_emx_interface_v5.py").read_text(encoding="utf-8")
    for exact_source_fragment in (
        '_source_file(f"canonical_{role}", record)',
        'f"launcher_preflight__{name}"',
        'if key.startswith("launcher_preflight__")',
        'expected_top_level_keys=("schema", "generated_utc", "inputs", "runtime_sources", "outputs")',
        '"full_band_v3_internal_terminal": internal_snapshot.public_record()',
    ):
        require(exact_source_fragment in producer_source, f"frozen producer source construction drift: {exact_source_fragment}")
    for exact_common_fragment in (
        '"path": self.path', '"sha256": self.sha256', '"size_bytes": self.size_bytes',
    ):
        require(exact_common_fragment in common_source, f"frozen public record shape drift: {exact_common_fragment}")

    adapter_source = Path(adapter.__file__).read_text(encoding="utf-8")
    for required_v8_fragment in (
        "def derive_source_science_tables(", "def validate_stage_funnel(",
        "def validate_process_identity(", "def validate_independent_go_payload(",
        "def validate_watcher_launch_receipt_payload(",
        "def validate_runtime_manifest_payload(", "def _open_root_component_chain(",
        'getattr(os, "O_NOFOLLOW", 0)', 'getattr(os, "O_DIRECTORY", 0)',
        "def _compare_recomputed_rows(",
        'payload.get("comparison_feature_metrics")', 'payload.get("joint_metrics")',
        'payload.get("q_metrics")',
        "def frozen_release_contract_reference(", "def _validate_contract_node(",
        "def _read_bound_public_bytes(", "len(raw) != size_bytes",
        "def validate_exact_count_types(", "type(value) is not int",
        "PRODUCER_RUN_KEYS", "PORTABLE_RUN_KEYS",
        "def require_exact_typed_value(", "def reject_authority_like_keys(",
        "class HeldRootLease", "class HeldRegularSnapshot", "class HeldOutputLease",
        "def _write_exclusive_at(", "NESTED_ARTIFACT_ROLES",
        "def bind_nested_artifact(", "SURVIVOR_SCOPE",
        "def validate_producer_survivor_statement(",
        "PRODUCER_SURVIVOR_CONDITIONING_STATEMENT",
        "nested_artifact_identities",
        "distinct nested artifact roles must bind distinct path/SHA identities",
        "self._held_root_files",
        "def _verify_held_file_versions(",
    ):
        require(required_v8_fragment in adapter_source, f"v8 implementation gate absent: {required_v8_fragment}")

    consumer_source = Path(adapter.__file__).with_name(
        "consume_portable_emx_interface_v8.py"
    ).read_text(encoding="utf-8")
    for required_consumer_fragment in (
        "expected_size_bytes", "public size_bytes differs from held-FD bytes",
        "adapter.PORTABLE_RUN_KEYS", "adapter.validate_stage_funnel(",
        "source_raw_by_role", "public_record[\"size_bytes\"]",
        "def _load_portable_interface_held(", "held_snapshots",
        "adapter.require_exact_typed_value(",
    ):
        require(
            required_consumer_fragment in consumer_source,
            f"v8 consumer implementation gate absent: {required_consumer_fragment}",
        )

    output = {
        "schema": "report_interface_v8_frozen_v8_static_compatibility_v1",
        "status": "PASS_RESULT_BLIND_STATIC_SOURCE_CONTRACT",
        "frozen_v8_root": str(V8.relative_to(WORKSPACE)),
        "frozen_v8_sha256": observed_sha,
        "gates": {
            "terminal_and_authorization_schemas": "PASS",
            "manifest_schemas_and_exact_artifact_keys": "PASS",
            "terminal_check_key_sets": "PASS",
            "launcher_controller_filename_keys": "PASS_4_OF_4",
            "completion_contract_ordered_fields": "PASS_6_OF_6",
            "complete_interface_nested_release_requirements": "PASS",
            "producer_role_construction_source_fragments": "PASS_5_OF_5",
            "public_record_path_sha_size_shape": "PASS_3_OF_3",
            "preflight_and_launcher_exact_evidence_sets": "PASS_4_OF_4",
            "full_manifest_exact_input_and_runtime_roles": "PASS_10_PLUS_4",
            "frozen_authorization_process_go_runtime_semantics": "PASS",
            "portable_and_mapping_exact_keysets": "PASS_15_17_16",
            "science_recomputation_and_funnel_implementation": "PASS",
            "componentwise_nofollow_root_traversal": "PASS",
            "frozen_contract_exact_24_key_deep_typed_policy": "PASS",
            "public_size_bytes_held_fd_length_binding": "PASS_ADAPTER_AND_CONSUMER",
            "all_count_fields_exact_builtin_int": "PASS",
            "nested_producer_and_portable_exact_typed_maps": "PASS",
            "same_held_bytes_sha_size_parse_and_named_continuity": "PASS",
            "held_output_dirfd_exclusive_publication_and_swap_rejection": "PASS",
            "nested_manifest_artifact_held_bytes_closure": "PASS_41_OF_41",
            "structured_survivor_scope_and_unconditional_claim_rejection": "PASS",
        },
        "expected_producer_source_role_count": len(adapter.PRODUCER_TERMINAL_ROLES),
        "expected_launcher_preflight_count": len(adapter.LAUNCHER_PREFLIGHT_KEYS),
        "boundaries": {
            "static_prepared_source_only": True,
            "actual_emx_metrics_or_results_read": False,
            "mars_login_performed": False,
            "stage07_or_stage08_executed": False,
            "independent_go_claimed": False,
        },
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
