from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)


def _literal(relative: str, name: str) -> Any:
    for node in _tree(relative).body:
        targets: list[ast.expr]
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"frozenset", "list", "set", "tuple"}
            and len(value.args) == 1
        ):
            value = value.args[0]
        return ast.literal_eval(value)
    raise AssertionError(f"missing literal constant {name} in {relative}")


def _strict_json(path: Path) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            assert key not in result, f"duplicate JSON key {key!r} in {path}"
            result[key] = value
        return result

    def reject_constant(token: str) -> Any:
        raise AssertionError(f"non-finite JSON token {token} in {path}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs_hook,
        parse_constant=reject_constant,
    )
    assert type(value) is dict
    return value


def test_package_v5_public_interface_is_exactly_aligned() -> None:
    package = "scripts/build_controlled_real10k_20k_mars_package.py"
    preflight = "scripts/preflight_controlled_real10k_20k_mars.py"
    native = "tests/controlled_real10k_20k_mars_native_smoke.py"
    runtime_bundle = "scripts/build_controlled_real10k_20k_runtime_bundle.py"

    assert {
        _literal(package, "PACKAGE_VERSION"),
        _literal(preflight, "PACKAGE_VERSION"),
        _literal(native, "PACKAGE_VERSION"),
        _literal(runtime_bundle, "PACKAGE_VERSION"),
    } == {"v5"}
    assert {
        _literal(package, "QA_REQUIRED_SCHEMA"),
        _literal(preflight, "QA_REQUIRED_SCHEMA"),
        _literal(native, "PACKAGE_QA_SCHEMA"),
    } == {"controlled_real10k_20k_mars_package_independent_qa_required_v3"}
    assert {
        _literal(package, "PACKAGE_COMMIT_SCHEMA"),
        _literal(preflight, "PACKAGE_COMMIT_SCHEMA"),
        _literal(native, "PACKAGE_COMMIT_SCHEMA"),
    } == {"controlled_real10k_20k_mars_package_commit_v2"}
    assert {
        _literal(package, "BUILD_ATTEMPT_BODY_SCHEMA"),
        _literal(preflight, "PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA"),
        _literal(native, "BUILD_ATTEMPT_BODY_SCHEMA"),
    } == {"controlled_real10k_20k_mars_package_build_attempt_body_v3"}
    assert {
        _literal(package, "BUILD_ATTEMPT_COMMIT_SCHEMA"),
        _literal(preflight, "PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA"),
        _literal(native, "BUILD_ATTEMPT_COMMITTED_SCHEMA"),
    } == {"controlled_real10k_20k_mars_package_build_attempt_committed_v1"}

    assert _literal(package, "ROLE_DESTINATIONS") == _literal(
        preflight, "PACKAGE_ROLE_DESTINATIONS"
    ) == _literal(native, "ROLE_DESTINATIONS")
    assert len(_literal(package, "ROLE_DESTINATIONS")) == 21

    package_go_keys = tuple(_literal(package, "REQUIRED_GO_BINDING_KEYS"))
    assert package_go_keys == tuple(_literal(preflight, "PACKAGE_REQUIRED_GO_BINDING_KEYS"))
    assert len(package_go_keys) == 23
    assert "package_build_attempt_body" in package_go_keys
    assert "package_build_attempt_committed" in package_go_keys
    assert "package_build_attempt_receipt" not in package_go_keys
    assert _literal(native, "TEST_ID") == "descriptor_closed_package_consumer_graph_v5"


def test_materialization_authority_chain_is_exactly_aligned() -> None:
    materializer = "scripts/run_controlled_real10k_20k_materialization.py"
    runner = "scripts/run_controlled_real10k_20k_paired.py"
    evaluator = "scripts/evaluate_controlled_real10k_20k_common.py"
    preflight = "scripts/preflight_controlled_real10k_20k_mars.py"

    material_roles = tuple(_literal(materializer, "BOUND_ROLE_ORDER"))
    assert material_roles == tuple(_literal(runner, "MATERIALIZATION_BOUND_ROLE_ORDER"))
    assert material_roles == tuple(_literal(evaluator, "MATERIALIZATION_BOUND_ROLE_ORDER"))
    assert len(material_roles) == 21
    assert material_roles[7:9] == (
        "package_build_attempt_body",
        "package_build_attempt_committed",
    )

    assert {
        _literal(materializer, "MANIFEST_SCHEMA"),
        _literal(runner, "MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA"),
        _literal(evaluator, "MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA"),
    } == {"controlled_real10k_20k_materialization_gate_manifest_v2"}
    assert {
        _literal(materializer, "GO_SCHEMA"),
        _literal(runner, "MATERIALIZATION_GO_SCHEMA"),
        _literal(evaluator, "MATERIALIZATION_GO_SCHEMA"),
    } == {"controlled_real10k_20k_materialization_exact_go_v2"}
    assert {
        _literal(materializer, "COMPLETE_SCHEMA"),
        _literal(runner, "MATERIALIZATION_COMPLETE_SCHEMA"),
        _literal(evaluator, "MATERIALIZATION_COMPLETE_SCHEMA"),
    } == {"controlled_real10k_20k_materialization_complete_v3"}

    assert {
        _literal(preflight, "PREFLIGHT_SCHEMA"),
        _literal(materializer, "MARS_PREFLIGHT_BODY_SCHEMA"),
        _literal(evaluator, "MARS_PREFLIGHT_BODY_SCHEMA"),
    } == {"controlled_real10k_20k_mars_preflight_receipt_body_v3"}
    assert {
        _literal(preflight, "PREFLIGHT_COMMITTED_SCHEMA"),
        _literal(materializer, "MARS_PREFLIGHT_COMMITTED_SCHEMA"),
        _literal(evaluator, "MARS_PREFLIGHT_COMMITTED_SCHEMA"),
    } == {"controlled_real10k_20k_mars_preflight_committed_v3"}
    assert {
        _literal(preflight, "PREFLIGHT_LEASE_SCHEMA"),
        _literal(materializer, "MARS_PREFLIGHT_LEASE_SCHEMA"),
        _literal(evaluator, "MARS_PREFLIGHT_LEASE_SCHEMA"),
    } == {"controlled_real10k_20k_mars_preflight_one_use_lease_v3"}


def test_preregistered_scientific_contract_is_not_drifted() -> None:
    prereg = _strict_json(
        ROOT
        / "reports/controlled_real10k_20k_nested_20260824/CONTROLLED_EXPERIMENT_PREREGISTRATION_V1.json"
    )
    shared = "rfic_transformer_inverse_design/controlled_real10k_20k_contract.py"
    materializer = "scripts/run_controlled_real10k_20k_materialization.py"
    runner = "scripts/run_controlled_real10k_20k_paired.py"
    builder = "scripts/build_controlled_real10k_20k_nested.py"

    frozen_training = prereg["frozen_training_contract"]
    assert tuple(frozen_training["paired_seeds"]) == tuple(
        _literal(shared, "EXACT_PAIRED_SEEDS")
    ) == tuple(_literal(materializer, "PAIRED_SEEDS"))
    assert frozen_training["batch_size"] == 1024
    assert frozen_training["forward_optimizer_updates"] == 1200
    assert frozen_training["inverse_optimizer_updates"] == 1200
    assert frozen_training["early_stopping"] is False

    model = prereg["frozen_model_contract"]
    architecture = _literal(builder, "FROZEN_HISTORICAL_ARCHITECTURE")
    assert model["forward_layers"] == [10, *architecture["forward_hidden_widths"], 4]
    assert model["inverse_layers"] == [4, *architecture["inverse_hidden_widths"], 10]
    assert model["hidden_activation"] == "GELU"
    assert model["decoder"] == "independent_sigmoid"
    assert model["local_refinement_steps"] == 0

    arms = prereg["nested_materialization"]["arms"]
    expected = _literal(runner, "EXPECTED_COUNTS")
    assert expected == {
        "small": {
            "source_rows": arms["small"]["source_table_rows"],
            "gradient_train": arms["small"]["gradient_train_rows"],
            "validation": arms["small"]["validation_rows"],
            "test": arms["small"]["test_rows"],
        },
        "large": {
            "source_rows": arms["large"]["source_table_rows"],
            "gradient_train": arms["large"]["gradient_train_rows"],
            "validation": arms["large"]["validation_rows"],
            "test": arms["large"]["test_rows"],
        },
    }
