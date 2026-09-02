#!/usr/bin/env python3
"""Build the execution-free corrected foundry-layout approval candidate.

The builder performs a byte-bound, fail-closed correction of the approved
private 56-point configuration.  It can only add the five approved
``emx.foundry_layout`` leaves, verifies the existing authorization and failed
Golden evidence, and records the one paused controller.  It has no Cadence,
Calibre, EMX, queue, controller, or signal capability.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
    GEOMETRY_BOUNDS_UM,
    PORT_AND_GROUNDING_CONTRACT,
    expected_frequency_contract,
    expected_geometry_contract,
)


EXACT_PUBLIC_CODE_COMMIT = "1807d6a2ce248c38a20708a8a35dd7238bcb8b9d"
EXPECTED_CONTROLLER_PID = 765117
EXPECTED_SAFE_ANCHOR_ID = (
    "historical200k_seed20260711__fixed_frame_03624__g3f96565ab7ac4e8a"
)
EXPECTED_FULL_CAMPAIGN_CANDIDATE_SHA256 = (
    "e78c6fe6dfc2801a4bfa131e0a21398f2402bff99e1199d3c5ebd79b42344946"
)
EXPECTED_CALIBRE_RULE_DECK_SHA256 = (
    "8252a77efecf92d3b187d83f7047df45433ce662c53683996c175b2aa80653ef"
)
REQUESTED_AUTHORIZATION_SCOPE = (
    "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_"
    "THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
)
CANDIDATE_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_"
    "authorization_candidate.v1"
)
DIFF_SCHEMA = "rfic_transformer.broadband56_foundry_layout_configuration_diff.v1"
VERIFICATION_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_"
    "candidate_verification.v1"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CORRECTED_CONFIG_NAME = (
    "mars_s4p_grounded_powerline_broadband56_56pt_"
    "foundry_layout_corrected.yaml"
)
DIFF_NAME = "FOUNDRY_LAYOUT_CONFIGURATION_DIFF.json"
CANDIDATE_PREFIX = (
    "CORRECTED_FOUNDRY_LAYOUT_CONFIGURATION_AUTHORIZATION_CANDIDATE_"
)
VERIFICATION_NAME = "CORRECTED_FOUNDRY_LAYOUT_CANDIDATE_VERIFICATION_RECEIPT.json"

EXPECTED_FOUNDRY_LAYOUT = {
    "enabled": True,
    "manufacturing_grid_um": 0.005,
    "power_line_stitch_pad_depth_um": 6.0,
    "shield_strap_width_um": 10.0,
    "shield_strap_pitch_um": 20.0,
}
EXPECTED_CHANGED_PATHS = tuple(
    f"emx.foundry_layout.{name}" for name in EXPECTED_FOUNDRY_LAYOUT
)
CHANGE_REASONS = {
    "enabled": "Restore the DRC-proven foundry-layout generation path.",
    "manufacturing_grid_um": "Canonicalize generated geometry to the 0.005-um manufacturing grid.",
    "power_line_stitch_pad_depth_um": "Restore the DRC-proven power-line stitch landing depth.",
    "shield_strap_width_um": "Restore the DRC-proven slotted ground-frame strap width.",
    "shield_strap_pitch_um": "Restore the DRC-proven slotted ground-frame strap pitch.",
}
GENERATED_LAYOUT_AUDIT_REQUIREMENTS = (
    "manufacturing_grid_canonicalization_0p005_um",
    "slotted_foundry_ground_frame",
    "shield_strap_width_exact_10p0_um",
    "shield_strap_pitch_exact_20p0_um",
    "power_line_stitch_landing_depth_exact_6p0_um",
    "primary_power_line_bridge_continuity_after_grid_snap",
    "secondary_power_line_bridge_continuity_after_grid_snap",
    "foundry_via_stack_and_landing_pad_validity",
    "zero_off_grid_polygon_coordinates",
    "no_manual_gds_modification",
)
UNCHANGED_PHYSICAL_ITEMS = (
    "foundry_process",
    "process_corner",
    "proc_file",
    "pdk",
    "cadence_technology_library",
    "layer_map",
    "calibre_rule_deck",
    "calibre_blocking_rule_definitions",
    "m9_m10_winding_layers",
    "m4_cutout_and_keepout",
    "one_turn_one_turn_topology",
    "center_taps",
    "ten_geometry_variables",
    "geometry_variable_ordering",
    "geometry_lower_and_upper_bounds",
    "135_degree_winding_rules",
    "approved_feed_interface_exceptions",
    "port_mode",
    "signal_port_map",
    "auxiliary_ground_port_map",
    "s4p_reference_impedance",
    "physical_feature_extraction_equations",
    "frequency_grid",
)

EVIDENCE_ARGUMENTS = {
    "full_campaign_authorization": "full-campaign-authorization-receipt",
    "full_campaign_candidate": "full-campaign-candidate",
    "preparation_receipt": "preparation-receipt",
    "campaign_contract_frozen": "campaign-contract-frozen",
    "backend_identity_manifest": "backend-identity-manifest",
    "backend_identity_verification": "backend-identity-verification-receipt",
    "failed_golden_001": "failed-golden-001-receipt",
    "golden_001_preservation": "golden-001-preservation-receipt",
    "failed_safe_anchor_golden": "failed-safe-anchor-golden-receipt",
    "safe_anchor_source": "safe-anchor-source-receipt",
    "root_cause_addendum": "root-cause-addendum",
    "controller_pause_receipt": "controller-pause-receipt",
}


class CandidateBuildError(RuntimeError):
    """Raised when any requested evidence or immutable field is inconsistent."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        result = build_artifacts(args, out_dir=out_dir)
    except CandidateBuildError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print("decision=FOUNDRY_LAYOUT_APPROVAL_REQUIRED")
    for key in (
        "corrected_config_path",
        "corrected_config_sha256",
        "diff_path",
        "diff_sha256",
        "candidate_path",
        "candidate_sha256",
        "verification_receipt_path",
    ):
        print(f"{key}={result[key]}")
    print("simulator_action_taken=no")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--generated-utc")
    parser.add_argument("--baseline-config", required=True)
    parser.add_argument("--baseline-config-sha256", required=True)
    parser.add_argument("--runtime-repo", required=True)
    parser.add_argument("--authoritative-controller-pid", type=int, default=EXPECTED_CONTROLLER_PID)
    for flag in EVIDENCE_ARGUMENTS.values():
        parser.add_argument(f"--{flag}", required=True)
        parser.add_argument(f"--{flag}-sha256", required=True)
    args = parser.parse_args(argv)
    sha_values = {
        name: value for name, value in vars(args).items() if name.endswith("sha256")
    }
    for name, value in sha_values.items():
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            parser.error(f"--{name.replace('_', '-')} must be a lowercase SHA-256 digest")
    return args


def build_artifacts(args: argparse.Namespace, *, out_dir: Path) -> dict[str, str]:
    generated_utc = str(args.generated_utc or _utc_now())
    if not _is_aware_timestamp(generated_utc):
        raise CandidateBuildError("--generated-utc must be timezone-aware")
    if int(args.authoritative_controller_pid) != EXPECTED_CONTROLLER_PID:
        raise CandidateBuildError(
            f"authoritative controller PID must remain {EXPECTED_CONTROLLER_PID}"
        )

    baseline_path = Path(args.baseline_config).expanduser().resolve()
    baseline_record = _file_record(
        baseline_path,
        str(args.baseline_config_sha256),
        "approved private baseline configuration",
    )
    baseline_text = _utf8_text(baseline_path, "approved private baseline configuration")
    baseline = _yaml_mapping(baseline_text, "approved private baseline configuration")
    _validate_baseline_contract(baseline)

    corrected_text, corrected = build_corrected_configuration(baseline_text)
    changes = configuration_changes(baseline, corrected)
    _validate_only_approved_changes(changes)
    _validate_corrected_contract(corrected)

    evidence = _load_evidence(args)
    calibre_rule_deck = _validate_evidence(evidence, baseline_record=baseline_record)
    runtime_identity = _git_identity(Path(args.runtime_repo).expanduser().resolve())
    controller = _controller_snapshot(EXPECTED_CONTROLLER_PID)
    _validate_controller_snapshot(controller)

    out_dir.mkdir(parents=False, exist_ok=False)
    corrected_path = out_dir / CORRECTED_CONFIG_NAME
    corrected_path.write_text(corrected_text, encoding="utf-8")
    corrected_record = _file_record(
        corrected_path,
        _sha256(corrected_path),
        "corrected private configuration",
    )

    diff_payload = _build_diff_payload(
        generated_utc=generated_utc,
        baseline=baseline_record,
        corrected=corrected_record,
        changes=changes,
    )
    diff_path = out_dir / DIFF_NAME
    _write_json(diff_path, diff_payload)
    diff_record = _file_record(diff_path, _sha256(diff_path), "configuration diff")

    candidate = _build_candidate(
        generated_utc=generated_utc,
        baseline=baseline_record,
        corrected=corrected_record,
        diff=diff_record,
        evidence=evidence,
        calibre_rule_deck=calibre_rule_deck,
        runtime_identity=runtime_identity,
        controller=controller,
    )
    errors = validate_candidate(candidate)
    if errors:
        raise CandidateBuildError("candidate contract failed: " + "; ".join(errors))
    candidate_stamp = _filename_timestamp(generated_utc)
    candidate_path = out_dir / f"{CANDIDATE_PREFIX}{candidate_stamp}.json"
    _write_json(candidate_path, candidate)
    candidate_record = _file_record(candidate_path, _sha256(candidate_path), "candidate")

    verification = _build_verification_receipt(
        generated_utc=generated_utc,
        baseline=baseline_record,
        corrected=corrected_record,
        diff=diff_record,
        candidate=candidate_record,
        runtime_identity=runtime_identity,
        controller=controller,
    )
    verification_path = out_dir / VERIFICATION_NAME
    _write_json(verification_path, verification)
    verification_record = _file_record(
        verification_path,
        _sha256(verification_path),
        "candidate verification receipt",
    )
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(
            f"{record['sha256']}  {Path(record['path']).name}\n"
            for record in (
                corrected_record,
                diff_record,
                candidate_record,
                verification_record,
            )
        ),
        encoding="utf-8",
    )
    return {
        "corrected_config_path": str(corrected_path),
        "corrected_config_sha256": corrected_record["sha256"],
        "diff_path": str(diff_path),
        "diff_sha256": diff_record["sha256"],
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_record["sha256"],
        "verification_receipt_path": str(verification_path),
    }


def build_corrected_configuration(baseline_text: str) -> tuple[str, dict[str, Any]]:
    baseline = _yaml_mapping(baseline_text, "baseline configuration")
    emx = _mapping(baseline.get("emx"), "baseline emx")
    if "foundry_layout" in emx:
        raise CandidateBuildError("baseline already contains emx.foundry_layout")
    marker = "\nbounds:\n"
    if baseline_text.count(marker) != 1:
        raise CandidateBuildError("baseline must contain exactly one top-level bounds marker")
    block = (
        "\n  foundry_layout:\n"
        "    enabled: true\n"
        "    manufacturing_grid_um: 0.005\n"
        "    power_line_stitch_pad_depth_um: 6.0\n"
        "    shield_strap_width_um: 10.0\n"
        "    shield_strap_pitch_um: 20.0\n"
    )
    corrected_text = baseline_text.replace(marker, block + "bounds:\n", 1)
    corrected = _yaml_mapping(corrected_text, "corrected configuration")
    return corrected_text, corrected


def configuration_changes(
    old: Mapping[str, Any], new: Mapping[str, Any]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _collect_changes(old, new, path="", changes=changes)
    return sorted(changes, key=lambda item: item["path"])


def _collect_changes(
    old: Any,
    new: Any,
    *,
    path: str,
    changes: list[dict[str, Any]],
) -> None:
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else str(key)
            if key not in old:
                if isinstance(new[key], Mapping):
                    _collect_changes({}, new[key], path=child, changes=changes)
                else:
                    changes.append(
                        {"path": child, "old_state": "MISSING", "old_value": None, "new_value": new[key]}
                    )
            elif key not in new:
                if isinstance(old[key], Mapping):
                    _collect_changes(old[key], {}, path=child, changes=changes)
                else:
                    changes.append(
                        {"path": child, "old_state": "PRESENT", "old_value": old[key], "new_state": "MISSING", "new_value": None}
                    )
            else:
                _collect_changes(old[key], new[key], path=child, changes=changes)
        return
    if old != new:
        changes.append(
            {"path": path, "old_state": "PRESENT", "old_value": old, "new_value": new}
        )


def _validate_only_approved_changes(changes: list[dict[str, Any]]) -> None:
    observed = tuple(item["path"] for item in changes)
    if set(observed) != set(EXPECTED_CHANGED_PATHS) or len(observed) != len(
        EXPECTED_CHANGED_PATHS
    ):
        raise CandidateBuildError(
            "unauthorized configuration change set: " + json.dumps(observed)
        )
    for item in changes:
        leaf = item["path"].rsplit(".", 1)[-1]
        if item.get("old_state") != "MISSING":
            raise CandidateBuildError(f"approved field was not missing: {item['path']}")
        if item.get("new_value") != EXPECTED_FOUNDRY_LAYOUT[leaf]:
            raise CandidateBuildError(f"incorrect corrected value: {item['path']}")


def _validate_baseline_contract(config: Mapping[str, Any]) -> None:
    target = _mapping(config.get("target"), "target")
    frequency = (
        int(round(float(target.get("frequency_start_hz", -1)))),
        int(round(float(target.get("frequency_stop_hz", -1)))),
        int(round(float(target.get("frequency_step_hz", -1)))),
        int(target.get("band_points", -1)),
    )
    expected_frequency = (
        FREQUENCY_GRID_HZ[0],
        FREQUENCY_GRID_HZ[-1],
        FREQUENCY_GRID_HZ[1] - FREQUENCY_GRID_HZ[0],
        len(FREQUENCY_GRID_HZ),
    )
    if frequency != expected_frequency:
        raise CandidateBuildError(f"baseline frequency contract mismatch: {frequency}")

    topology = _mapping(config.get("topology"), "topology")
    if target.get("topology_mode") != "1t1t":
        raise CandidateBuildError("baseline topology_mode must be 1t1t")
    for winding in ("primary", "secondary"):
        value = _mapping(topology.get(winding), f"topology.{winding}")
        if value.get("turns") != 1 or value.get("center_tap") is not True:
            raise CandidateBuildError(f"baseline {winding} topology contract mismatch")

    emx = _mapping(config.get("emx"), "emx")
    power = _mapping(emx.get("power_line_8port"), "emx.power_line_8port")
    if (
        emx.get("port_mode") != PORT_AND_GROUNDING_CONTRACT["port_mode"]
        or emx.get("cadence_pin_purpose") != PORT_AND_GROUNDING_CONTRACT["cadence_pin_purpose"]
        or power.get("touchstone_mode") != PORT_AND_GROUNDING_CONTRACT["touchstone_mode"]
        or power.get("port_ground_reference") != PORT_AND_GROUNDING_CONTRACT["port_ground_reference"]
        or power.get("port_map") != PORT_AND_GROUNDING_CONTRACT["port_order"]
        or emx.get("ground_unused_s8p_ports") is not False
    ):
        raise CandidateBuildError("baseline port and grounding contract mismatch")

    bounds = _mapping(config.get("bounds"), "bounds")
    observed_bounds = {
        "primary_outer_width_um": _nested(bounds, "primary", "outer_width_um"),
        "primary_outer_height_um": _nested(bounds, "primary", "outer_height_um"),
        "secondary_outer_width_um": _nested(bounds, "secondary", "outer_width_um"),
        "secondary_outer_height_um": _nested(bounds, "secondary", "outer_height_um"),
        "line_width_um": _nested(bounds, "primary", "trace_width_um"),
        "primary_terminal_y_span_um": _nested(bounds, "primary", "terminal_y_span_um"),
        "secondary_terminal_y_span_um": _nested(bounds, "secondary", "terminal_y_span_um"),
        "offset_um": bounds.get("offset_um"),
        "primary_feed_extension_um": _nested(bounds, "primary", "feed_extension_um"),
        "secondary_feed_extension_um": _nested(bounds, "secondary", "feed_extension_um"),
    }
    expected_bounds = {name: list(value) for name, value in GEOMETRY_BOUNDS_UM.items()}
    if observed_bounds != expected_bounds:
        raise CandidateBuildError("baseline geometry bounds mismatch")
    if _nested(bounds, "secondary", "trace_width_um") != expected_bounds["line_width_um"]:
        raise CandidateBuildError("secondary line-width bound is not synchronized")


def _validate_corrected_contract(config: Mapping[str, Any]) -> None:
    _validate_baseline_contract(config)
    emx = _mapping(config.get("emx"), "emx")
    foundry = _mapping(emx.get("foundry_layout"), "emx.foundry_layout")
    if dict(foundry) != EXPECTED_FOUNDRY_LAYOUT:
        raise CandidateBuildError("corrected foundry-layout block is not exact")


def _load_evidence(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for name, flag in EVIDENCE_ARGUMENTS.items():
        attribute = flag.replace("-", "_")
        path = Path(getattr(args, attribute)).expanduser().resolve()
        expected_sha = str(getattr(args, attribute + "_sha256"))
        record = _file_record(path, expected_sha, name)
        record["payload"] = _json_mapping(path, name)
        evidence[name] = record
    return evidence


def _validate_evidence(
    evidence: Mapping[str, dict[str, Any]],
    *,
    baseline_record: Mapping[str, Any],
) -> dict[str, Any]:
    full = evidence["full_campaign_authorization"]["payload"]
    if not (
        full.get("overall_status") == "PASS"
        and full.get("decision") == FULL_CAMPAIGN_PASS_DECISION
        and full.get("authorization_scope") == FULL_CAMPAIGN_APPROVAL_SCOPE
        and full.get("campaign_id") == CAMPAIGN_ID
        and full.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT
        and _nested(full, "approved_candidate", "sha256")
        == EXPECTED_FULL_CAMPAIGN_CANDIDATE_SHA256
    ):
        raise CandidateBuildError("full-campaign authorization receipt is not exact PASS")
    if _nested(full, "private_identity_bindings", "private_configuration", "sha256") != baseline_record["sha256"]:
        raise CandidateBuildError("full authorization does not bind the baseline configuration")

    full_candidate = evidence["full_campaign_candidate"]
    if full_candidate["sha256"] != EXPECTED_FULL_CAMPAIGN_CANDIDATE_SHA256:
        raise CandidateBuildError("full-campaign candidate SHA mismatch")
    candidate_payload = full_candidate["payload"]
    if candidate_payload.get("campaign_id") != CAMPAIGN_ID:
        raise CandidateBuildError("full-campaign candidate campaign mismatch")

    preparation = evidence["preparation_receipt"]["payload"]
    if not (
        preparation.get("overall_status") == "PASS"
        and preparation.get("decision") == "PREPARED_FOR_GOLDEN_GATE"
        and preparation.get("campaign_id") == CAMPAIGN_ID
    ):
        raise CandidateBuildError("preparation receipt is not exact PASS")

    manifest = evidence["backend_identity_manifest"]["payload"]
    if not (
        manifest.get("campaign_id") == CAMPAIGN_ID
        and manifest.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise CandidateBuildError("backend identity manifest mismatch")
    private_config = _nested(manifest, "runtime_identities", "private_configuration")
    if not isinstance(private_config, Mapping) or private_config.get("sha256") != baseline_record["sha256"]:
        raise CandidateBuildError("backend manifest does not bind baseline configuration")
    deck = _nested(manifest, "runtime_identities", "calibre_rule_deck")
    if not isinstance(deck, Mapping):
        raise CandidateBuildError("backend manifest lacks Calibre rule-deck identity")
    if deck.get("sha256") != EXPECTED_CALIBRE_RULE_DECK_SHA256:
        raise CandidateBuildError("Calibre rule-deck identity changed")
    deck_record = _file_record(
        Path(str(deck.get("path"))).expanduser().resolve(),
        str(deck.get("sha256")),
        "Calibre rule deck",
    )

    backend_verification = evidence["backend_identity_verification"]["payload"]
    if not (
        backend_verification.get("overall_status") == "PASS"
        and backend_verification.get("decision") == "USE_HASH_BOUND_PRODUCTION_BACKEND"
    ):
        raise CandidateBuildError("backend identity verification is not PASS")

    frozen = evidence["campaign_contract_frozen"]["payload"]
    if not (
        frozen.get("campaign_id") == CAMPAIGN_ID
        and frozen.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise CandidateBuildError("frozen campaign contract mismatch")

    golden_001 = evidence["golden_001_preservation"]["payload"]
    if not (
        golden_001.get("overall_status") == "PASS"
        and golden_001.get("mark") == "GOLDEN_ATTEMPT_001_DRC_FAIL_NOT_ACCEPTED"
        and golden_001.get("blocking_drc_violation_count") == 337
    ):
        raise CandidateBuildError("Golden 001 failure preservation evidence mismatch")
    if _nested(golden_001, "key_evidence", "stage_progress_receipt", "sha256") != evidence["failed_golden_001"]["sha256"]:
        raise CandidateBuildError("Golden 001 failed receipt binding mismatch")

    safe_anchor = evidence["failed_safe_anchor_golden"]["payload"]
    if not (
        safe_anchor.get("overall_status") == "FAIL"
        and safe_anchor.get("decision") == "BLOCKED_SYSTEMIC_DRC_CONTRACT_MISMATCH"
        and _nested(safe_anchor, "gates", "calibre_blocking_violations") == 331
        and _nested(safe_anchor, "gates", "emx") == "NOT_RUN"
        and _nested(safe_anchor, "candidate", "historical_safe_anchor_id")
        == EXPECTED_SAFE_ANCHOR_ID
    ):
        raise CandidateBuildError("failed safe-anchor Golden evidence mismatch")
    safe_source = evidence["safe_anchor_source"]["payload"]
    if not (
        safe_source.get("overall_status") == "PASS"
        and safe_source.get("historical_candidate_id") == EXPECTED_SAFE_ANCHOR_ID
    ):
        raise CandidateBuildError("safe-anchor source identity mismatch")

    root_cause = evidence["root_cause_addendum"]["payload"]
    if not (
        root_cause.get("overall_status") == "FAIL"
        and root_cause.get("decision") == "BLOCKED_SYSTEMIC_DRC_CONTRACT_MISMATCH"
        and root_cause.get("simulator_action_taken_for_addendum") is False
    ):
        raise CandidateBuildError("root-cause addendum mismatch")

    pause = evidence["controller_pause_receipt"]["payload"]
    if not (
        pause.get("overall_status") == "PASS"
        and pause.get("controller_pid") == EXPECTED_CONTROLLER_PID
        and pause.get("controller_alive") is True
        and pause.get("controller_killed") is False
        and pause.get("controller_restarted") is False
    ):
        raise CandidateBuildError("controller pause receipt mismatch")
    return deck_record


def _git_identity(repository: Path) -> dict[str, Any]:
    if not repository.is_dir():
        raise CandidateBuildError(f"runtime repository is missing: {repository}")
    try:
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        tree = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidateBuildError(f"cannot verify runtime git identity: {exc}") from exc
    if head != EXACT_PUBLIC_CODE_COMMIT:
        raise CandidateBuildError(f"runtime commit mismatch: {head}")
    if status:
        raise CandidateBuildError("runtime repository is not clean")
    return {
        "path": str(repository),
        "head_commit": head,
        "tree_sha1": tree,
        "working_tree_clean": True,
    }


def _controller_snapshot(pid: int) -> dict[str, Any]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise CandidateBuildError("controller process audit requires Linux /proc")
    uid = os.getuid()
    campaign_root_token = f"/{CAMPAIGN_ID}_attempt_identity_compat_20260901T184022Z"
    controllers: list[dict[str, Any]] = []
    simulators: list[int] = []
    simulator_executables = {"emx", "calibre", "virtuoso", "innovus"}
    simulator_role_scripts = {
        "run_broadband56_v2_cadence_streamout_batch.py",
        "run_broadband56_v2_calibre_batch.py",
        "run_broadband56_v2_calibre_zero_blocking_batch.py",
        "run_broadband56_v2_exact_gds_emx_batch.py",
        "run_tsmc65_calibre_macro_drc.py",
    }
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            args = [
                item.decode(errors="replace")
                for item in (entry / "cmdline").read_bytes().split(b"\0")
                if item
            ]
            if not args:
                continue
            names = {Path(value).name for value in args}
            current_pid = int(entry.name)
            if (
                "run_broadband56_v2_authorized_queue_controller.py" in names
                and any(campaign_root_token in value for value in args)
            ):
                status_text = (entry / "status").read_text(encoding="utf-8")
                state_line = next(
                    line for line in status_text.splitlines() if line.startswith("State:")
                )
                controllers.append(
                    {
                        "pid": current_pid,
                        "state": state_line.split(":", 1)[1].strip(),
                        "command_sha256": hashlib.sha256(
                            b"\0".join(value.encode() for value in args)
                        ).hexdigest(),
                    }
                )
            executable = Path(args[0]).name.lower()
            if executable in simulator_executables or names & simulator_role_scripts:
                simulators.append(current_pid)
        except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration):
            continue
    controllers.sort(key=lambda item: item["pid"])
    return {
        "audited_utc": _utc_now(),
        "controller_count": len(controllers),
        "controllers": controllers,
        "authoritative_controller_pid": pid,
        "project_active_simulator_count": len(simulators),
        "project_active_simulator_pids": sorted(simulators),
        "process_scan_method": "same_uid_linux_proc_exact_executable_or_script_name_v1",
    }


def _validate_controller_snapshot(snapshot: Mapping[str, Any]) -> None:
    controllers = snapshot.get("controllers")
    if not isinstance(controllers, list) or len(controllers) != 1:
        raise CandidateBuildError("one-controller invariant failed")
    controller = controllers[0]
    if (
        snapshot.get("controller_count") != 1
        or snapshot.get("authoritative_controller_pid") != EXPECTED_CONTROLLER_PID
        or controller.get("pid") != EXPECTED_CONTROLLER_PID
        or not str(controller.get("state", "")).startswith("T")
    ):
        raise CandidateBuildError("authoritative controller is not the one paused PID")
    if snapshot.get("project_active_simulator_count") != 0:
        raise CandidateBuildError("a project simulator process is active")


def _build_diff_payload(
    *,
    generated_utc: str,
    baseline: Mapping[str, Any],
    corrected: Mapping[str, Any],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    reported_changes = []
    for item in changes:
        leaf = item["path"].rsplit(".", 1)[-1]
        reported_changes.append(
            {
                **item,
                "reason": CHANGE_REASONS[leaf],
                "changes_geometry_bytes": True,
                "changes_drc_rules": False,
                "changes_scientific_frequency_contract": False,
            }
        )
    return {
        "schema": DIFF_SCHEMA,
        "generated_utc": generated_utc,
        "campaign_id": CAMPAIGN_ID,
        "source_configuration": dict(baseline),
        "corrected_configuration": dict(corrected),
        "comparison_method": "recursive_parsed_yaml_leaf_comparison_v1",
        "approved_changed_paths": list(EXPECTED_CHANGED_PATHS),
        "changed_field_count": len(reported_changes),
        "changed_fields": reported_changes,
        "unchanged_physical_contract_items": list(UNCHANGED_PHYSICAL_ITEMS),
        "summary": {
            "overall_status": "PASS",
            "DRC_RULE_CHANGE": "no",
            "FREQUENCY_CONTRACT_CHANGE": "no",
            "GEOMETRY_BOUNDS_CHANGE": "no",
            "GEOMETRY_GENERATION_CORRECTION": "yes",
            "unapproved_field_change_count": 0,
        },
    }


def _build_candidate(
    *,
    generated_utc: str,
    baseline: Mapping[str, Any],
    corrected: Mapping[str, Any],
    diff: Mapping[str, Any],
    evidence: Mapping[str, dict[str, Any]],
    calibre_rule_deck: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
    controller: Mapping[str, Any],
) -> dict[str, Any]:
    public_evidence = {
        name: {key: value for key, value in record.items() if key != "payload"}
        for name, record in evidence.items()
    }
    return {
        "schema": CANDIDATE_SCHEMA,
        "generated_utc": generated_utc,
        "campaign_id": CAMPAIGN_ID,
        "scientific_contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "approval_status": "PENDING_EXPLICIT_PROJECT_OWNER_SHA256_APPROVAL",
        "requested_authorization_scope": REQUESTED_AUTHORIZATION_SCOPE,
        "candidate_file_authorizes_execution": False,
        "automatic_simulator_execution_authorized": False,
        "execution_effect": "NONE_REQUEST_ONLY",
        "simulator_action_taken": False,
        "queue_or_controller_action_taken": False,
        "corrected_public_runtime": dict(runtime_identity),
        "exact_required_public_code_commit": EXACT_PUBLIC_CODE_COMMIT,
        "previous_private_configuration": dict(baseline),
        "corrected_private_configuration": dict(corrected),
        "configuration_diff": dict(diff),
        "corrected_foundry_layout_contract": dict(EXPECTED_FOUNDRY_LAYOUT),
        "frequency_contract": expected_frequency_contract(),
        "geometry_contract": expected_geometry_contract(),
        "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT,
        "calibre_rule_deck_identity": dict(calibre_rule_deck),
        "unchanged_contract": {
            "overall_status": "PASS",
            "machine_comparison_method": "recursive_parsed_yaml_exactly_five_allowed_leaf_additions",
            "items": list(UNCHANGED_PHYSICAL_ITEMS),
            "drc_rule_change": False,
            "geometry_bounds_change": False,
            "frequency_contract_change": False,
            "geometry_generation_correction": True,
        },
        "generated_layout_audit_contract": {
            "execution_status_in_this_candidate_task": "NOT_RUN_PENDING_CORRECTED_GDS",
            "required_on_rescue_golden": True,
            "all_must_pass_before_calibre": True,
            "requirements": [
                {"name": name, "required": True, "current_result": "NOT_RUN"}
                for name in GENERATED_LAYOUT_AUDIT_REQUIREMENTS
            ],
        },
        "rescue_golden_contract": {
            "geometry": "REGENERATE_EXACT_VERIFIED_SAFE_ANCHOR_PARAMETERS",
            "safe_anchor_id": EXPECTED_SAFE_ANCHOR_ID,
            "ordered_gates": [
                "analytical",
                "topology",
                "cadence",
                "generated_layout_audit",
                "calibre",
                "fresh_real_emx",
                "exact_56_point_four_port_s4p_qa",
            ],
            "calibre_blocking_violation_count_required": 0,
            "emx_allowed_only_after_zero_blocking_calibre": True,
            "fresh_real_emx_required": True,
            "exact_frequency_points_required": len(FREQUENCY_GRID_HZ),
            "systematic_drc_failure_action": "STOP_AND_REPORT_RULE_BY_RULE_EVIDENCE",
            "brute_force_additional_geometries_forbidden": True,
        },
        "post_golden_pass_stage_chain": [
            {"stage": "PILOT_32", "cumulative_accepted": 32},
            {"stage": "PILOT_1000", "cumulative_accepted": 1_000},
            {"stage": "PHASE_A", "cumulative_accepted": 50_000},
            {"stage": "PHASE_B", "cumulative_accepted": 150_000},
            {"stage": "PHASE_C", "cumulative_accepted": 200_000},
        ],
        "existing_full_campaign_authorization": public_evidence[
            "full_campaign_authorization"
        ],
        "private_evidence": public_evidence,
        "controller_invariant": {
            **dict(controller),
            "required_state": "WAITING_FOR_CORRECTED_FOUNDRY_LAYOUT_APPROVAL",
            "controller_signalled_in_this_task": False,
            "controller_killed_in_this_task": False,
            "controller_restarted_in_this_task": False,
            "duplicate_controller_created": False,
            "duplicate_queue_created": False,
            "duplicate_supervisor_created": False,
        },
        "authorization_boundary": (
            "This exact candidate is execution-free. Only explicit project-owner approval "
            "of its exact SHA-256 may authorize restoration of the corrected foundry-layout "
            "contract and one rescue Golden; the pre-existing FULL_CAMPAIGN receipt governs "
            "the already approved post-Golden stage chain."
        ),
    }


def validate_candidate(candidate: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def require(name: str, condition: bool) -> None:
        if not condition:
            errors.append(name)

    require("schema", candidate.get("schema") == CANDIDATE_SCHEMA)
    require("campaign", candidate.get("campaign_id") == CAMPAIGN_ID)
    require(
        "fingerprint",
        candidate.get("scientific_contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    require(
        "scope",
        candidate.get("requested_authorization_scope")
        == REQUESTED_AUTHORIZATION_SCOPE,
    )
    require("execution_free", candidate.get("candidate_file_authorizes_execution") is False)
    require("simulator_free", candidate.get("simulator_action_taken") is False)
    require(
        "code_commit",
        candidate.get("exact_required_public_code_commit") == EXACT_PUBLIC_CODE_COMMIT,
    )
    require(
        "runtime_commit",
        _nested(candidate, "corrected_public_runtime", "head_commit")
        == EXACT_PUBLIC_CODE_COMMIT,
    )
    require(
        "foundry_layout",
        candidate.get("corrected_foundry_layout_contract") == EXPECTED_FOUNDRY_LAYOUT,
    )
    require("frequency", candidate.get("frequency_contract") == expected_frequency_contract())
    require("geometry", candidate.get("geometry_contract") == expected_geometry_contract())
    require("ports", candidate.get("port_and_grounding_contract") == PORT_AND_GROUNDING_CONTRACT)
    require(
        "drc_deck",
        _nested(candidate, "calibre_rule_deck_identity", "sha256")
        == EXPECTED_CALIBRE_RULE_DECK_SHA256,
    )
    require(
        "unchanged_contract",
        _nested(candidate, "unchanged_contract", "overall_status") == "PASS"
        and _nested(candidate, "unchanged_contract", "drc_rule_change") is False
        and _nested(candidate, "unchanged_contract", "geometry_bounds_change") is False
        and _nested(candidate, "unchanged_contract", "frequency_contract_change") is False,
    )
    requirements = _nested(candidate, "generated_layout_audit_contract", "requirements")
    require(
        "generated_layout_audits",
        isinstance(requirements, list)
        and [item.get("name") for item in requirements]
        == list(GENERATED_LAYOUT_AUDIT_REQUIREMENTS)
        and all(item.get("required") is True and item.get("current_result") == "NOT_RUN" for item in requirements),
    )
    require(
        "controller",
        _nested(candidate, "controller_invariant", "controller_count") == 1
        and _nested(candidate, "controller_invariant", "authoritative_controller_pid")
        == EXPECTED_CONTROLLER_PID
        and _nested(candidate, "controller_invariant", "project_active_simulator_count")
        == 0,
    )
    return errors


def _build_verification_receipt(
    *,
    generated_utc: str,
    baseline: Mapping[str, Any],
    corrected: Mapping[str, Any],
    diff: Mapping[str, Any],
    candidate: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
    controller: Mapping[str, Any],
) -> dict[str, Any]:
    check_names = (
        "configuration_parses",
        "exact_foundry_layout_values",
        "manufacturing_grid_contract_0p005_um",
        "foundry_ground_frame_audit_required_on_rescue_golden",
        "primary_and_secondary_bridge_connectivity_audits_required",
        "via_stack_and_landing_pad_audit_required",
        "calibre_rule_deck_unchanged",
        "geometry_bounds_unchanged",
        "port_map_unchanged",
        "frequency_grid_exact_56_unchanged",
        "no_clobber_output_created",
        "exact_sha_bindings_verified",
        "runtime_exact_commit_and_clean",
        "one_controller_invariant",
        "zero_project_simulator_processes",
        "candidate_is_execution_free",
    )
    checks = [{"name": name, "pass": True} for name in check_names]
    return {
        "schema": VERIFICATION_SCHEMA,
        "generated_utc": generated_utc,
        "campaign_id": CAMPAIGN_ID,
        "overall_status": "PASS",
        "decision": "FOUNDRY_LAYOUT_APPROVAL_REQUIRED",
        "checks": checks,
        "check_count": len(checks),
        "pass_count": len(checks),
        "fail_count": 0,
        "layout_level_validation_status": "NOT_RUN_PENDING_CORRECTED_GDS",
        "simulator_action_taken": False,
        "baseline_configuration": dict(baseline),
        "corrected_configuration": dict(corrected),
        "configuration_diff": dict(diff),
        "authorization_candidate": dict(candidate),
        "runtime_identity": dict(runtime_identity),
        "controller_snapshot": dict(controller),
        "negative_tests_required_in_public_test_suite": [
            "wrong_sha_rejection",
            "unauthorized_field_change_rejection",
            "no_clobber_rejection",
        ],
    }


def _file_record(path: Path, expected_sha256: str, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CandidateBuildError(f"{label} is missing: {path}")
    actual = _sha256(path)
    if actual != expected_sha256:
        raise CandidateBuildError(
            f"{label} SHA-256 mismatch: expected={expected_sha256} actual={actual}"
        )
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": actual}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utf8_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CandidateBuildError(f"cannot read {label}: {exc}") from exc


def _json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateBuildError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CandidateBuildError(f"{label} is not a JSON object")
    return value


def _yaml_mapping(text: str, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CandidateBuildError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CandidateBuildError(f"{label} is not a YAML mapping")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateBuildError(f"{label} must be a mapping")
    return value


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_aware_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _filename_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
