"""Hash-bound execution profile for one broadband56 production stage.

The profile is a separate private artifact whose exact bytes are bound by the
private backend-identity manifest.  Keeping it separate preserves the already
approved production configuration byte-for-byte.  It may name arguments for
the required role scripts, but it cannot change their identity, order, stage
targets, shell policy, or result contract.  This module validates and expands
the profile; it never launches a process or writes an artifact.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .broadband56_balanced200k import CAMPAIGN_ID
from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT, STAGES
from .broadband56_full_campaign_authorization import PRODUCTION_BACKEND_ID


PROFILE_KEY = "broadband56_stage_execution_profile"
PROFILE_SCHEMA = "rfic_transformer.broadband56_v2_stage_execution_profile.v5"
COMMAND_PLAN_SCHEMA = "rfic_transformer.broadband56_v2_stage_execution_command_plan.v5"
PROFILE_EXECUTION_MODE = "HASH_BOUND_PYTHON_ROLE_COMMANDS"
ROLE_RECEIPT_REQUIRED_STATUS = "PASS"

ALLOWED_ARGUMENT_PLACEHOLDERS = (
    "{stage}",
    "{cumulative_target}",
    "{campaign_root}",
    "{backend_out_dir}",
    "{role_out_dir}",
    "{full_campaign_receipt}",
    "{backend_identity_manifest}",
    "{resource_snapshot}",
    "{max_concurrency}",
    "{prior_stage_receipt}",
    "{current_accepted}",
    "{remaining_accepted}",
    "{private_configuration}",
)

BASE_RESULT_PATH_FIELDS = (
    "raw_products_receipt",
    "checkpoint_receipt",
    "exact_gds_emx_receipt_index",
)

TERMINAL_RESULT_PATH_FIELDS = (
    "campaign_history_receipt",
    "training_readiness_receipt",
    "checkpoint_figure_receipt",
    "final_delivery_receipt",
)

SPACE_FILLING_ACQUISITION_ROLES = ("phase_a_queue_builder",)
CHECKPOINTED_SPACE_FILLING_ACQUISITION_ROLES = (
    "adaptive_checkpoint_materializer",
    "phase_a_queue_builder",
)
ADAPTIVE_ACQUISITION_ROLES = (
    "adaptive_checkpoint_materializer",
    "acquisition_ensemble_trainer",
    "adaptive_round_stager",
    "adaptive_candidate_pool_builder",
    "acquisition_predictor",
    "adaptive_candidate_selector",
)
PHYSICAL_PIPELINE_ROLES = (
    "cadence_streamout_runner",
    "candidate_gds_index_builder",
    "gds_physical_identity_auditor",
    "calibre_runner",
    "calibre_zero_blocking_receipt_builder",
    "exact_audited_gds_emx_runner",
    "full_band_s4p_qa_builder",
    "stage_attempt_product_builder",
    "stage_attempt_finalizer",
    "raw_products_finalizer",
    "checkpoint_auditor",
)
TERMINAL_DELIVERY_ROLES = (
    "campaign_histories_finalizer",
    "training_readiness_finalizer",
    "checkpoint_figure_renderer",
    "final_delivery_auditor",
)

_PLACEHOLDER_PATTERN = re.compile(r"\{[A-Za-z0-9_]+\}")


class StageExecutionProfileError(ValueError):
    """Raised when the private stage profile cannot be trusted."""


def expected_stage_role_order(stage: str) -> tuple[str, ...]:
    """Return the only permitted executable role order for a stage."""

    stage_name = str(stage).upper()
    stage_names = {item.name for item in STAGES}
    if stage_name not in stage_names:
        raise StageExecutionProfileError(f"unknown stage: {stage_name}")
    if stage_name in {"PHASE_B", "PHASE_C"}:
        acquisition = ADAPTIVE_ACQUISITION_ROLES
    elif stage_name == "GOLDEN":
        acquisition = SPACE_FILLING_ACQUISITION_ROLES
    else:
        acquisition = CHECKPOINTED_SPACE_FILLING_ACQUISITION_ROLES
    terminal = TERMINAL_DELIVERY_ROLES if stage_name == "PHASE_C" else ()
    return (*acquisition, *PHYSICAL_PIPELINE_ROLES, *terminal)


def expected_result_path_fields(stage: str) -> tuple[str, ...]:
    """Return result receipts that must exist before a stage can pass."""

    stage_name = str(stage).upper()
    expected_stage_role_order(stage_name)
    if stage_name == "PHASE_C":
        return (*BASE_RESULT_PATH_FIELDS, *TERMINAL_RESULT_PATH_FIELDS)
    return BASE_RESULT_PATH_FIELDS


def read_execution_profile(profile_path: Path) -> dict[str, Any]:
    """Parse one SHA-verified JSON or YAML execution-profile artifact."""

    path = Path(profile_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise StageExecutionProfileError(
            f"stage execution profile is missing or empty: {path}"
        )
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise StageExecutionProfileError(
            f"stage execution profile cannot be parsed: {path}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise StageExecutionProfileError("stage execution profile file is not an object")
    profile = payload.get(PROFILE_KEY, payload)
    if not isinstance(profile, Mapping):
        raise StageExecutionProfileError(
            f"stage execution profile lacks an object at {PROFILE_KEY}"
        )
    return dict(profile)


def profile_from_command_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one exact private command plan into the runtime profile."""

    required_fields = {
        "schema",
        "campaign_id",
        "contract_fingerprint_sha256",
        "backend_id",
        "shell_used",
        "stages",
    }
    if not isinstance(plan, Mapping) or set(plan) != required_fields:
        raise StageExecutionProfileError(
            "command plan fields do not exactly match the frozen contract"
        )
    if plan.get("schema") != COMMAND_PLAN_SCHEMA:
        raise StageExecutionProfileError("command plan schema mismatch")
    if plan.get("campaign_id") != CAMPAIGN_ID:
        raise StageExecutionProfileError("command plan campaign mismatch")
    if (
        plan.get("contract_fingerprint_sha256")
        != SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise StageExecutionProfileError("command plan fingerprint mismatch")
    if plan.get("backend_id") != PRODUCTION_BACKEND_ID:
        raise StageExecutionProfileError("command plan backend mismatch")
    if plan.get("shell_used") is not False:
        raise StageExecutionProfileError("command plan shell_used must be false")
    stages = plan.get("stages")
    if not isinstance(stages, Mapping):
        raise StageExecutionProfileError("command plan stages must be an object")
    return {
        "schema": PROFILE_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "execution_mode": PROFILE_EXECUTION_MODE,
        "shell_used": False,
        "stages": copy.deepcopy(dict(stages)),
    }


def validate_execution_profile(
    profile: Mapping[str, Any],
    *,
    backend_manifest: Mapping[str, Any],
) -> list[str]:
    """Return every static execution-profile violation."""

    errors: list[str] = []
    _equal(errors, "schema", profile.get("schema"), PROFILE_SCHEMA)
    _equal(errors, "campaign_id", profile.get("campaign_id"), CAMPAIGN_ID)
    _equal(
        errors,
        "contract_fingerprint_sha256",
        profile.get("contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    _equal(
        errors,
        "backend_id",
        profile.get("backend_id"),
        PRODUCTION_BACKEND_ID,
    )
    _equal(
        errors,
        "execution_mode",
        profile.get("execution_mode"),
        PROFILE_EXECUTION_MODE,
    )
    _equal(errors, "shell_used", profile.get("shell_used"), False)

    stages = profile.get("stages")
    expected_stage_names = {item.name for item in STAGES}
    if not isinstance(stages, Mapping):
        errors.append("stages must be an object")
        return errors
    if set(stages) != expected_stage_names:
        errors.append("stages keys do not exactly match the ordered campaign stages")

    identities = backend_manifest.get("script_identities")
    script_identities = identities if isinstance(identities, Mapping) else {}
    for stage in sorted(expected_stage_names):
        stage_profile = stages.get(stage)
        if not isinstance(stage_profile, Mapping):
            errors.append(f"stages.{stage} must be an object")
            continue
        allowed_fields = {"commands", "result_paths"}
        if "max_candidates_per_attempt" in stage_profile:
            allowed_fields.add("max_candidates_per_attempt")
            limit = stage_profile["max_candidates_per_attempt"]
            if stage == "GOLDEN" or type(limit) is not int or not 1 <= limit <= 32:
                errors.append(f"stages.{stage}.max_candidates_per_attempt must be 1..32 outside GOLDEN")
            raw_commands = stage_profile.get("commands")
            queues = [item for item in (raw_commands if isinstance(raw_commands, list) else [])
                      if isinstance(item, Mapping) and item.get("role") == "phase_a_queue_builder"]
            argv = queues[0].get("argv", []) if len(queues) == 1 else []
            option = "--attempt-candidate-limit"
            if (not isinstance(argv, list) or argv.count(option) != 1 or argv.index(option) + 1 >= len(argv)
                    or argv[argv.index(option) + 1] != str(limit)):
                errors.append(f"stages.{stage} queue argv does not bind the attempt limit")
            for frozen_option in ("--frozen-queue-receipt", "--frozen-queue-receipt-sha256"):
                if (not isinstance(argv, list) or argv.count(frozen_option) != 1
                        or argv.index(frozen_option) + 1 >= len(argv)
                        or not argv[argv.index(frozen_option) + 1]):
                    errors.append(f"stages.{stage} bounded queue must bind {frozen_option}")
        if "golden_terminal_mode" in stage_profile:
            from .broadband56_golden_stage import TERMINAL_MODE
            allowed_fields.add("golden_terminal_mode")
            if stage != "GOLDEN" or stage_profile["golden_terminal_mode"] != TERMINAL_MODE:
                errors.append(f"stages.{stage}.golden_terminal_mode is not authorized")
        if set(stage_profile) != allowed_fields:
            errors.append(f"stages.{stage} fields do not exactly match the profile contract")
        commands = stage_profile.get("commands")
        expected_roles = expected_stage_role_order(stage)
        if not isinstance(commands, list):
            errors.append(f"stages.{stage}.commands must be a list")
        else:
            actual_roles = [
                command.get("role") if isinstance(command, Mapping) else None
                for command in commands
            ]
            if actual_roles != list(expected_roles):
                errors.append(f"stages.{stage}.commands role order mismatch")
            for index, command in enumerate(commands):
                _validate_command(
                    errors,
                    command,
                    label=f"stages.{stage}.commands.{index}",
                    identities=script_identities,
                )
        result_paths = stage_profile.get("result_paths")
        expected_result_fields = set(expected_result_path_fields(stage))
        if not isinstance(result_paths, Mapping):
            errors.append(f"stages.{stage}.result_paths must be an object")
        elif set(result_paths) != expected_result_fields:
            errors.append(f"stages.{stage}.result_paths fields mismatch")
        else:
            for field, value in result_paths.items():
                if not _safe_relative_path(value):
                    errors.append(
                        f"stages.{stage}.result_paths.{field} must be a safe relative path"
                    )
    return errors


def expand_argument(value: str, substitutions: Mapping[str, str]) -> str:
    """Expand only the frozen placeholder vocabulary."""

    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise StageExecutionProfileError("command argument is empty or unsafe")
    result = value
    for placeholder, replacement in substitutions.items():
        result = result.replace(placeholder, replacement)
    unresolved = _PLACEHOLDER_PATTERN.findall(result)
    if unresolved:
        raise StageExecutionProfileError(
            f"command argument has unresolved placeholders: {sorted(set(unresolved))}"
        )
    return result


def resolve_under(root: Path, relative: str, *, label: str) -> Path:
    """Resolve a configured relative path without allowing root escape."""

    if not _safe_relative_path(relative):
        raise StageExecutionProfileError(f"{label} is not a safe relative path")
    resolved_root = Path(root).expanduser().resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise StageExecutionProfileError(f"{label} escapes the backend root") from exc
    return resolved


def _validate_command(
    errors: list[str],
    command: Any,
    *,
    label: str,
    identities: Mapping[str, Any],
) -> None:
    if not isinstance(command, Mapping):
        errors.append(f"{label} must be an object")
        return
    if set(command) != {"role", "argv", "receipt", "shell_used"}:
        errors.append(f"{label} fields do not exactly match the command contract")
    role = command.get("role")
    if not isinstance(role, str) or role not in identities:
        errors.append(f"{label}.role has no backend identity")
    argv = command.get("argv")
    if not isinstance(argv, list) or not all(
        isinstance(item, str) and item for item in argv
    ):
        errors.append(f"{label}.argv must be a string list")
    else:
        placeholders = {
            token for item in argv for token in _PLACEHOLDER_PATTERN.findall(item)
        }
        unknown = placeholders - set(ALLOWED_ARGUMENT_PLACEHOLDERS)
        if unknown:
            errors.append(f"{label}.argv has unknown placeholders: {sorted(unknown)}")
        if "{role_out_dir}" not in "\n".join(argv):
            errors.append(f"{label}.argv must bind role_out_dir")
        if any("\x00" in item or "\n" in item for item in argv):
            errors.append(f"{label}.argv contains unsafe control characters")
    receipt = command.get("receipt")
    if not _safe_relative_path(receipt):
        errors.append(f"{label}.receipt must be a safe relative path")
    _equal(errors, f"{label}.shell_used", command.get("shell_used"), False)


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and path != Path(".")


def _equal(errors: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label} mismatch")


__all__ = [
    "ALLOWED_ARGUMENT_PLACEHOLDERS",
    "BASE_RESULT_PATH_FIELDS",
    "COMMAND_PLAN_SCHEMA",
    "PROFILE_EXECUTION_MODE",
    "PROFILE_KEY",
    "PROFILE_SCHEMA",
    "ROLE_RECEIPT_REQUIRED_STATUS",
    "StageExecutionProfileError",
    "TERMINAL_RESULT_PATH_FIELDS",
    "expected_result_path_fields",
    "expected_stage_role_order",
    "expand_argument",
    "profile_from_command_plan",
    "read_execution_profile",
    "resolve_under",
    "validate_execution_profile",
]
