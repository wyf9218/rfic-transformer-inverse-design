#!/usr/bin/env python3
"""Freeze a completed validation-only F_ref before inverse-arm training."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

import evaluate_controlled_tandem_shared_fref_fixed_targets as evaluator
import evaluate_historical_tandem_fixed_targets as legacy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fref-weights", required=True)
    parser.add_argument("--fref-summary", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--expected-trainer-sha256", required=True)
    parser.add_argument("--output-json", required=True)
    return parser.parse_args()


def _stage_forward_model(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as archive:
        weight_keys = sorted(
            (key for key in archive.files if key.startswith("weight_")),
            key=lambda key: int(key.removeprefix("weight_")),
        )
        bias_keys = sorted(
            (key for key in archive.files if key.startswith("bias_")),
            key=lambda key: int(key.removeprefix("bias_")),
        )
        if not weight_keys or len(weight_keys) != len(bias_keys):
            raise ValueError("forward stage archive has incomplete arrays")
        return {
            "forward_weights": [np.asarray(archive[key], dtype=float) for key in weight_keys],
            "forward_biases": [np.asarray(archive[key], dtype=float) for key in bias_keys],
        }


def main() -> int:
    args = _parse_args()
    weights_path = Path(args.fref_weights).expanduser().resolve()
    summary_path = Path(args.fref_summary).expanduser().resolve()
    prereg_path = Path(args.preregistration_json).expanduser().resolve()
    clarification_path = Path(args.clarification_addendum_json).expanduser().resolve()
    output_path = Path(args.output_json).expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"no-clobber output exists: {output_path}")
    if legacy._sha256(prereg_path) != args.expected_preregistration_sha256:
        raise ValueError("preregistration SHA mismatch")
    if legacy._sha256(clarification_path) != args.expected_clarification_addendum_sha256:
        raise ValueError("clarification addendum SHA mismatch")

    summary = legacy._read_json(summary_path)
    model = legacy._load_weights(weights_path)
    arguments = summary.get("arguments") or {}
    split = summary.get("split_audit") or {}
    test_access = summary.get("test_access_contract") or {}
    normalization = summary.get("normalization_contract") or {}
    budget = summary.get("optimizer_budget_contract") or {}
    stage_resume = summary.get("stage_checkpoint_resume") or {}
    forward_record = (stage_resume.get("stage_records") or {}).get("forward_proxy") or {}
    stage_weights_path = Path(str(forward_record.get("weights_path") or "")).resolve()
    stage_metadata_path = Path(str(forward_record.get("metadata_path") or "")).resolve()
    stage_marker_path = (
        Path(str(stage_resume.get("checkpoint_root") or ""))
        / "forward_proxy_stage.complete.json"
    ).resolve()
    for path in (stage_weights_path, stage_metadata_path, stage_marker_path):
        if not path.is_file():
            raise FileNotFoundError(f"missing F_ref forward stage artifact: {path}")
    if legacy._sha256(stage_weights_path) != forward_record.get("weights_sha256"):
        raise ValueError("forward stage weights SHA does not match summary record")
    if legacy._sha256(stage_metadata_path) != forward_record.get("metadata_sha256"):
        raise ValueError("forward stage metadata SHA does not match summary record")
    marker = legacy._read_json(stage_marker_path)
    if marker != forward_record:
        raise ValueError("forward stage marker differs from the final summary record")

    final_forward_sha = evaluator._canonical_forward_component_sha256(model)
    stage_forward_sha = evaluator._canonical_forward_component_sha256(
        _stage_forward_model(stage_weights_path)
    )
    checks = {
        "execution_pass": summary.get("execution_status") == "PASS",
        "validation_only": summary.get("evaluation_mode") == "validation_only",
        "test_access_zero": int(test_access.get("test_access_event_count") or 0) == 0,
        "seed_exact": int(arguments.get("seed") or -1) == 2026082201,
        "large_train_rows_exact": int((split.get("row_counts") or {}).get("train") or 0)
        == 200000,
        "common_validation_rows_exact": int(
            (split.get("row_counts") or {}).get("validation") or 0
        )
        == 9096,
        "common_test_rows_exact": int((split.get("row_counts") or {}).get("test") or 0)
        == 9096,
        "common_holdout_exact": (
            (split.get("fixed_common_holdout_manifest") or {}).get("sha256")
            == "4cd2e1f584c2cf7c14ef64a89508bd30d92aec4eb4b1c377effe1d561b9b8ebe"
        ),
        "fixed_normalization_exact": normalization.get("sha256")
        == "9b29ac93f3eb0735964492497ec2032157c5ae290ce3ad2b97216a4bc4b34d47",
        "trainer_exact": (summary.get("model_comparison_contract") or {}).get(
            "trainer_implementation_sha256"
        )
        == args.expected_trainer_sha256,
        "forward_architecture_exact": legacy._layer_widths(model["forward_weights"])
        == [10, 128, 128, 4],
        "forward_updates_target_met": int(
            ((budget.get("realized") or {}).get("forward_optimizer_updates") or 0)
        )
        == 4800,
        "inverse_byproduct_updates_target_met": int(
            ((budget.get("realized") or {}).get("inverse_optimizer_updates") or 0)
        )
        == 4800,
        "early_stopping_disabled": budget.get("early_stopping_enabled") is False,
        "best_forward_checkpoint_on_fixed_cadence": int(
            (summary.get("best_optimizer_updates") or {}).get("forward_proxy") or 0
        )
        % 40
        == 0,
        "stage_and_final_forward_components_exact": stage_forward_sha == final_forward_sha,
    }
    if not all(checks.values()):
        raise ValueError(f"F_ref binding checks failed: {[key for key,value in checks.items() if not value]}")

    payload = {
        "schema": "controlled_shared_fref_binding_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS_FREF_FROZEN_RQ_I_ARMS_MAY_USE_THIS_FORWARD_ONLY",
        "parent_preregistration_sha256": args.expected_preregistration_sha256,
        "clarification_addendum_sha256": args.expected_clarification_addendum_sha256,
        "f_ref_seed": 2026082201,
        "f_ref_training_rows": 200000,
        "f_ref_weights": {
            "path": str(weights_path),
            "sha256": legacy._sha256(weights_path),
        },
        "f_ref_summary": {
            "path": str(summary_path),
            "sha256": legacy._sha256(summary_path),
        },
        "canonical_forward_component_sha256": final_forward_sha,
        "forward_stage_checkpoint": {
            "weights_path": str(stage_weights_path),
            "weights_sha256": legacy._sha256(stage_weights_path),
            "metadata_path": str(stage_metadata_path),
            "metadata_sha256": legacy._sha256(stage_metadata_path),
            "marker_path": str(stage_marker_path),
            "marker_sha256": legacy._sha256(stage_marker_path),
            "canonical_forward_component_sha256": stage_forward_sha,
        },
        "best_forward_optimizer_update": int(
            (summary.get("best_optimizer_updates") or {}).get("forward_proxy") or 0
        ),
        "checks": checks,
        "estimand_boundary": (
            "RQ-I is conditional on this single F_ref seed and forward-component SHA. The 100k inverse arm "
            "indirectly uses the extra large-pool forward information through this frozen scorer. F_ref random "
            "variation is not included in the five inverse-replicate interval."
        ),
        "unused_inverse_byproduct": {
            "present": True,
            "eligible_for_any_arm_or_claim": False,
            "reason": "Frozen trainer has no random-initialization forward-only CLI; inverse training cannot modify forward arrays.",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={payload['overall_status']}")
    print(f"canonical_forward_component_sha256={final_forward_sha}")
    print(f"output_json={output_path}")
    print(f"output_sha256={legacy._sha256(output_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
