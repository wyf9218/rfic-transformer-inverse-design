"""One-arm, validation-only EuCAP development evaluation of a closed F/I pair.

Reuse frequency_evaluation's exact metrics, prediction, geometry and export-grid
rules. Never invoke posttrain (which also evaluates test), train, Q-scan, native
physics, or plotting. This does not confer final-study or physical validation.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from .frequency_evaluation import (FEATURES, _csv, _number, _predict, evaluate_frequency,
                                   pin, protocol_identity, regression_metrics)
from .io import canonical_sha, read_json, save_json, sha256, utc_now

SCHEMA = "eucap15_development_evaluation_request.v1"


def _checked(expected):
    if not isinstance(expected, dict) or set(expected) != {"path", "sha256", "bytes"}:
        raise ValueError("exact path/SHA256/bytes pin required")
    if pin(expected["path"]) != expected:
        raise ValueError("source pin changed: " + str(expected["path"]))
    return Path(expected["path"])


def _role(receipt_pin, role, data_sha):
    path = _checked(receipt_pin)
    receipt = read_json(path)
    expected = {"schema": "bb00_training_receipt.v1", "role": role, "kind": "BB00",
        "frequency_ghz": 15, "label_mode": "STRICT_LUMPED", "data_sha": data_sha,
        "validation_selected_checkpoint": True, "resume_probe": False,
        "research_comparison_eligible": True, "historical_weights_loaded": False}
    if any(receipt.get(k) != v or (isinstance(v, bool) and type(receipt.get(k)) is not bool)
           for k, v in expected.items()):
        raise ValueError("closed eligible 15GHz training receipt differs: " + role)
    if receipt.get("status") not in ("PARTIAL", "SMOKE_TRAINED") or receipt["completed_step"] < 1:
        raise ValueError("no completed resumable optimizer update: " + role)
    sources = {"receipt": receipt_pin}
    for key in ("best", "last"):
        item = pin(receipt[key + "_checkpoint"])
        if item["sha256"] != receipt[key + "_sha256"]:
            raise ValueError("native training checkpoint SHA changed: " + role + "/" + key)
        sources[key] = item
    for name in ("config.json", "normalizer.json", "contract.json", "history.json"):
        sources[name] = pin(path.parent / name)
    config = read_json(sources["config.json"]["path"])
    norm = read_json(sources["normalizer.json"]["path"])
    contract = read_json(sources["contract.json"]["path"])
    if (canonical_sha(norm) != receipt["normalizer_sha"] or
            canonical_sha(contract) != config["contract_sha"] or
            config["normalizer_sha"] != receipt["normalizer_sha"] or
            config["data_sha"] != data_sha or config["role"] != role or
            config["frequency_ghz"] != 15 or config["label_mode"] != "STRICT_LUMPED" or
            config["resume_probe"] is not False or config["research_comparison_eligible"] is not True or
            config["test_labels_optimized"] is not False):
        raise ValueError("native role/data/normalizer/contract/config binding differs: " + role)
    if list(config["response_spans"]) != [2.5, 2.5, 20.0, 0.8]:
        raise ValueError("EuCAP fixed symmetric response spans changed")
    return {"sources": sources, "receipt": receipt, "config": config,
            "normalizer": norm, "contract": contract}


def _own_forward(data_root, info, delegated, delegated_dir, output, device, micro_batch):
    """Score own F once on the delegate's exact validation frame, not on test."""
    from .bb00 import load_bb00
    with (delegated_dir / "forward_predictions.csv").open(newline="") as handle:
        original = list(csv.DictReader(handle))
    ids = [row["target_id"] for row in original]
    indices = np.asarray([int(row["source_index"]) for row in original], dtype=np.int64)
    if (len(ids) != delegated["target_count"] or len(set(ids)) != len(ids) or
            canonical_sha(ids) != delegated["target_id_order_sha256"] or
            any(row["split"] != "validation" for row in original)):
        raise ValueError("own/common forward validation target order differs")
    # Only geometry/IDs/split are read here. Original validation EM targets are
    # reused from the already evaluated source table; test labels are not scored.
    with np.load(Path(data_root) / "dataset.npz", allow_pickle=False) as data:
        if (np.any(indices < 0) or np.any(indices >= len(data["split"])) or
                not np.all(data["split"][indices] == 1) or
                data["geometry_ids"][indices].astype(str).tolist() != ids):
            raise ValueError("own-forward source rows are not the identical validation frame")
        geometry = np.asarray(data["geometry"][indices], dtype=float)
    target = np.asarray([[float(row["truth__" + name]) for name in FEATURES] for row in original])
    model, state = load_bb00(info["sources"]["best"]["path"], device,
                             info["sources"]["best"]["sha256"])
    if (state["role"] != "forward" or state["data_sha"] != delegated["identity"]["dataset"]["sha256"] or
            state["normalizer_sha"] != canonical_sha(info["normalizer"]) or
            state["contract_sha"] != canonical_sha(info["contract"]) or
            state["model_sha"] != state["best_model_sha"] or state.get("resume_probe") or
            state.get("research_comparison_eligible") is False):
        raise ValueError("own forward checkpoint is not the exact eligible validation-best model")
    model.eval().requires_grad_(False)
    prediction, failures = _predict(model, geometry, 4, device, micro_batch)
    rows = []
    for i, old in enumerate(original):
        row = dict(old, evidence="VALIDATION_ORIGINAL_EM_LABELS_OWN_FORWARD",
                   status="PASS" if np.isfinite(prediction[i]).all() else "PREDICTION_FAILED")
        for j, name in enumerate(FEATURES):
            row["prediction__" + name] = _number(prediction[i, j])
            row["error__" + name] = _number(prediction[i, j] - target[i, j])
        rows.append(row)
    _csv(output / "own_forward_predictions.csv", rows)
    save_json(output / "OWN_FORWARD_FAILURES.json", failures)
    metadata = {"step": state["step"], "seed": state["train_config"]["seed"],
        "architecture": state["architecture"], "parameter_count": sum(p.numel() for p in model.parameters()),
        "model_sha": state["model_sha"]}
    return {"evidence": "VALIDATION_ORIGINAL_EM_LABELS", "features": regression_metrics(target, prediction)}, metadata


def evaluate_development(data_root, forward_receipt_pin, inverse_receipt_pin, out, *,
                         device="cpu", micro_batch=32, inverse_forward_receipt_pin=None,
                         expected_data_manifest=None):
    """Evaluate validation once; receipt arguments are exact native receipt pins.

    All actual model loading/inference is delegated to evaluate_frequency. Last
    checkpoints are byte-bound to their native terminal receipt, not reloaded or
    evaluated here. Best checkpoints are loaded/digest-verified by the delegate.
    """
    if device != "cpu" or type(micro_batch) is not int or not 1 <= micro_batch <= 352:
        raise ValueError("bounded CPU development evaluation required")
    data_root = Path(data_root).resolve()
    if expected_data_manifest is None:
        expected_data_manifest = pin(data_root / "data_manifest.json")
    manifest_path = _checked(expected_data_manifest)
    if manifest_path != data_root / "data_manifest.json":
        raise ValueError("prepared data manifest is not at the exact requested root")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "bb_data_manifest.v1" or manifest.get("status") != "PASS":
        raise ValueError("closed prepared data manifest required")
    dataset = pin(data_root / "dataset.npz")
    if dataset["sha256"] != manifest["artifacts"]["dataset.npz"]["sha256"]:
        raise ValueError("prepared dataset SHA changed")
    roles = {"forward": _role(forward_receipt_pin, "forward", dataset["sha256"]),
             "inverse": _role(inverse_receipt_pin, "inverse", dataset["sha256"])}
    fs, ins = roles["forward"], roles["inverse"]
    common = fs if inverse_forward_receipt_pin is None else _role(inverse_forward_receipt_pin, "forward", dataset["sha256"])
    if any(info["normalizer"] != ins["normalizer"] or info["contract"] != ins["contract"] for info in (fs, common)):
        raise ValueError("F/I train normalizer or geometry contract differs")
    if pin(ins["config"]["forward_checkpoint"]) != common["sources"]["best"]:
        raise ValueError("inverse config is not bound to the supplied validation-best forward")
    if inverse_forward_receipt_pin is not None:
        roles["inverse_forward_common_reference"] = common
    protocol = protocol_identity()
    identity = {"data_manifest": expected_data_manifest, "dataset": dataset,
        "splits": pin(data_root / "splits.json"),
        "roles": {role: info["sources"] for role, info in roles.items()},
        "normalizer_sha256": canonical_sha(fs["normalizer"]),
        "contract_sha256": canonical_sha(fs["contract"]),
        "evaluation_protocol_sha256": canonical_sha(protocol),
        "wrapper_source": pin(__file__), "frequency_ghz": 15, "label_mode": "STRICT_LUMPED"}
    output = Path(out).resolve()
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "EVALUATION_INPUT.json", {"schema": SCHEMA, "identity": identity,
        "split": "validation", "device": device, "micro_batch": micro_batch,
        "test_evaluation_authorized": False, "created_utc": utc_now()})
    try:
        import torch
        torch.set_num_threads(2)
        delegated_dir = output / "inverse_scoring_forward_validation"
        summary = evaluate_frequency(data_root, common["sources"]["best"]["path"],
            ins["sources"]["best"]["path"], delegated_dir, frequency_ghz=15,
            label_mode="STRICT_LUMPED", split="validation", device=device, micro_batch=micro_batch)
        if (summary["split"] != "validation" or summary["configuration_freeze"] is not None or
                summary["identity"]["dataset"] != dataset or
                summary["identity"]["protocol_sha256"] != identity["evaluation_protocol_sha256"]):
            raise ValueError("delegated evaluation drifted from the validation-only identity")
        for role, info in (("forward", common), ("inverse", ins)):
            if summary["identity"][role + "_checkpoint"] != info["sources"]["best"]:
                raise ValueError("delegated common-F/inverse checkpoint identity differs")
        own_metrics, own_metadata = summary["forward"], summary["model_metadata"]["forward"]
        own_csv = delegated_dir / "forward_predictions.csv"
        if fs["sources"]["best"] != common["sources"]["best"]:
            own_metrics, own_metadata = _own_forward(data_root, fs, summary, delegated_dir, output, device, micro_batch)
            own_csv = output / "own_forward_predictions.csv"
        model_metadata = {"forward": own_metadata, "inverse": summary["model_metadata"]["inverse"],
                          "inverse_forward_common_reference": summary["model_metadata"]["forward"]}
        source_rows = []
        for role, info in roles.items():
            metadata = model_metadata[role]
            if (metadata["architecture"] != info["config"]["architecture"] or
                    metadata["seed"] != info["config"]["seed"] or
                    summary["exposure"] != info["receipt"]["eligible_rows"]):
                raise ValueError("model/normalizer/exposure differs from the bound native training receipt")
            source_rows.append({"role": role, "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
                "hidden_layers": json.dumps(metadata["architecture"]["widths"][1:-1]),
                "seed": metadata["seed"], "source_snapshot_geometries": summary["source_snapshot_geometries"],
                "gradient_eligible_geometries": summary["exposure"]["train"]["eligible_geometries"],
                "unique_gradient_geometries_seen": info["receipt"]["unique_gradient_geometries"],
                "gradient_draws": info["receipt"]["gradient_draws"],
                "validation_eligible_geometries": summary["target_count"],
                "validation_split_geometries": summary["source_split_geometries"],
                "test_split_geometries_count_only": summary["exposure"]["test"]["split_geometries"],
                "test_metrics": "DEFERRED_UNTIL_FIXED_STUDY_COMPLETE",
                "validation_target_order_sha256": summary["target_id_order_sha256"],
                "data_sha256": dataset["sha256"], "normalizer_sha256": identity["normalizer_sha256"],
                "contract_sha256": identity["contract_sha256"],
                "best_step": metadata["step"], "last_step": info["receipt"]["completed_step"],
                "parameter_count": metadata["parameter_count"], "best_model_sha256": metadata["model_sha"],
                "last_model_sha256_native_receipt": info["receipt"]["model_sha"],
                "best_checkpoint": info["sources"]["best"]["path"],
                "best_checkpoint_sha256": info["sources"]["best"]["sha256"],
                "last_checkpoint": info["sources"]["last"]["path"],
                "last_checkpoint_sha256": info["sources"]["last"]["sha256"],
                "native_training_status": info["receipt"]["status"],
                "convergence": "NOT_ESTABLISHED", "REAL_EMX_VALIDATION": "NOT_RUN"})
        with (output / "ARM_SOURCE_TABLE.csv").open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(source_rows[0]), lineterminator="\n")
            writer.writeheader(); writer.writerows(source_rows)
        save_json(output / "ARM_SOURCE_TABLE.json", source_rows)
        development_summary = {"schema": "eucap15_development_summary.v1", "split": "validation",
            "identity": identity, "target_count": summary["target_count"],
            "target_id_order_sha256": summary["target_id_order_sha256"], "exposure": summary["exposure"],
            "own_forward": {**own_metrics, "model": own_metadata, "rows": pin(own_csv),
                "checkpoint": fs["sources"]["best"]},
            "inverse": {"model": model_metadata["inverse"], "modes": summary["inverse"],
                "rows": pin(delegated_dir / "inverse_predictions.csv"), "evidence": "SELF_PROXY",
                "scoring_forward": common["sources"]["best"],
                "scoring_forward_relationship": "OWN_FORWARD" if fs["sources"]["best"] == common["sources"]["best"] else "SHARED_FROZEN_FORWARD"},
            "grid_effect": summary["grid_effect"], "protocol": protocol,
            "comparison_scope": "own-F regression and I conditioned on named frozen F; validation development only, not causal winner",
            "test_evaluation": "DEFERRED_UNTIL_FIXED_STUDY_COMPLETE", "real_emx_validation": "NOT_RUN"}
        save_json(output / "EVALUATION_SUMMARY.json", development_summary)
        for source in [expected_data_manifest, dataset, identity["splits"], identity["wrapper_source"],
                       *(p for info in roles.values() for p in info["sources"].values())]:
            _checked(source)
        if canonical_sha(protocol_identity()) != identity["evaluation_protocol_sha256"]:
            raise ValueError("evaluation implementation changed during inference")
        artifacts = [pin(p) for p in sorted(output.rglob("*")) if p.is_file()]
        receipt = {"schema": "eucap15_development_evaluation_receipt.v1",
            "status": "COMPLETE_VALIDATION_ONLY_DEVELOPMENT", "identity": identity,
            "summary": pin(output / "EVALUATION_SUMMARY.json"), "artifacts": artifacts,
            "split": "validation", "target_count": summary["target_count"],
            "device": "cpu", "cpu_threads": 2,
            "model_selection": "native validation-best only; validation is development evidence, not unbiased final test",
            "last_checkpoint_verification": "exact native receipt/file SHA; not reloaded or evaluated",
            "forward_evidence": "VALIDATION_ORIGINAL_EM_LABELS", "inverse_evidence": "SELF_PROXY",
            "own_forward_checkpoint": fs["sources"]["best"], "inverse_scoring_forward_checkpoint": common["sources"]["best"],
            "native_delegate_forward_table": "diagnostic for named inverse-scoring F; never substituted for own-F regression",
            "geometry_modes": ["continuous", "grid"], "grid_repair": False,
            "test_access": "no test predictions/loss/ranking; original split/validity counts only",
            "test_evaluation": "DEFERRED_UNTIL_FIXED_STUDY_COMPLETE", "q_scan_run": False,
            "real_emx_validation": "NOT_RUN", "paper_figures_created": False,
            "convergence_claim": "NOT_ESTABLISHED", "created_utc": utc_now()}
        save_json(output / "DEVELOPMENT_EVALUATION_RECEIPT.json", receipt)
        with (output / "SHA256SUMS").open("x") as handle:
            for path in sorted(output.rglob("*")):
                if path.is_file() and path != output / "SHA256SUMS":
                    handle.write(sha256(path) + "  " + str(path.relative_to(output)) + "\n")
        return receipt
    except Exception as error:
        save_json(output / "DEVELOPMENT_EVALUATION_FAILED.json", {"status": "FAILED_NO_AUTOMATIC_RETRY",
            "identity": identity, "error": type(error).__name__ + ": " + str(error), "created_utc": utc_now()})
        raise


def evaluate_request(config_path, out):
    request = read_json(config_path)
    if (request.get("schema") != SCHEMA or request.get("split") != "validation" or
            request.get("test_evaluation_authorized") is not False):
        raise ValueError("explicit validation-only request required; no test route exists here")
    return evaluate_development(request["data_root"], request["forward_receipt"], request["inverse_receipt"], out,
        expected_data_manifest=request.get("data_manifest"), device=request.get("device", "cpu"),
        micro_batch=request.get("micro_batch", 32), inverse_forward_receipt_pin=request.get("inverse_forward_receipt"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate_request(args.config, args.out), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
