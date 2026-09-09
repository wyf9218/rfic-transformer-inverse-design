"""JSON-only, no-clobber tables for closed EuCAP development evaluations.

Input schema eucap15_development_tables_input.v1 contains a protocol pin and
arms=[{pair_receipt: pin, evaluation_receipt: pin}]. A pin has path/SHA256/bytes.
Only JSON metadata is opened. Checkpoint/NPZ/CSV pins are compared as inherited
identities, not reread or reevaluated. Missing arms remain explicitly partial.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def pin(path):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def bad_constant(value):
    raise ValueError("nonfinite JSON constant: " + value)


def finite_tree(value):
    if isinstance(value, float):
        require(math.isfinite(value), "nonfinite JSON number")
    elif isinstance(value, dict):
        for member in value.values():
            finite_tree(member)
    elif isinstance(value, list):
        for member in value:
            finite_tree(member)


def checked_json(item, seen):
    require(isinstance(item, dict) and set(item) == {"path", "sha256", "bytes"}, "exact JSON pin required")
    path = Path(item["path"])
    require(path.is_absolute() and path.suffix == ".json" and not path.is_symlink(), "JSON metadata only")
    raw = path.read_bytes()
    require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"], "metadata pin mismatch: " + str(path))
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    finite_tree(value)
    require(str(path) not in seen or seen[str(path)] == item, "same metadata path changed identity")
    seen[str(path)] = item
    return value


def dump(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def csv_table(path, rows):
    require(bool(rows), "empty table")
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def number(value, name, *, nullable=True):
    if value is None and nullable:
        return value
    require(type(value) in (int, float) and math.isfinite(value), "invalid numeric metric: " + name)
    return value


def integer(value, name):
    require(type(value) is int and value >= 0, "nonnegative integer required: " + name)
    return value


def selected_geometry_count(receipt):
    """Read only the count field defined by either exact supported schema."""
    fields = {
        "eucap15_development_data_receipt.v1": "selected_geometries",
        "eucap15_formal_development_view_receipt.v1": "selected_formal_geometries",
    }
    require(receipt.get("schema") in fields, "unsupported data receipt schema")
    field = fields[receipt["schema"]]
    require(field in receipt, "missing data receipt count: " + field)
    require(not (set(fields.values()) - {field}) & set(receipt), "ambiguous data receipt count fields")
    return integer(receipt[field], field)


def artifact(receipt, directory, name):
    matches = [p for p in receipt["artifacts"] if Path(p["path"]) == directory / name]
    require(len(matches) == 1, "missing/duplicate closed evaluation artifact: " + name)
    return matches[0]


def feature_rows(arm, population, features, protocol, target_count):
    require(set(features) == set(protocol["features"]), "feature scope differs")
    result = []
    for feature, unit in zip(protocol["features"], protocol["units"]):
        item = features[feature]
        n, valid, invalid = (integer(item[k], k) for k in ("fixed_denominator", "evaluable_denominator", "invalid_count"))
        require(n == target_count and valid + invalid == n and item["unit"] == unit, "feature denominator/unit differs")
        conditional = item["conditional_evaluable"]
        for key in ("mae", "rmse", "r2"):
            number(item[key], key)
        for key in ("mae", "rmse", "absolute_error_p95"):
            number(conditional[key], key)
        if invalid:
            require(item["mae"] is None and item["rmse"] is None, "invalid targets silently excluded from full-cohort error")
        if valid == 0:
            require(all(conditional[k] is None for k in ("mae", "rmse", "absolute_error_p95")), "zero-valid denominator fabricated")
        result.append({**arm, "population": population, "feature": feature, "unit": unit,
            "fixed_denominator": n, "evaluable_denominator": valid, "invalid_count": invalid,
            "mae_full_cohort": item["mae"], "rmse_full_cohort": item["rmse"], "r2": item["r2"],
            "r2_status": item["r2_status"], "conditional_mae": conditional["mae"],
            "conditional_rmse": conditional["rmse"], "conditional_absolute_error_p95": conditional["absolute_error_p95"],
            "mape_percent": None, "mape_status": "NOT_REPORTED_IN_CLOSED_JSON",
            "evidence": "VALIDATION_ORIGINAL_EM_LABELS" if population == "OWN_FORWARD" else "SELF_PROXY",
            "REAL_EMX_VALIDATION": "NOT_RUN"})
    return result


def inspect_arm(entry, protocol, seen):
    pair = checked_json(entry["pair_receipt"], seen)
    receipt = checked_json(entry["evaluation_receipt"], seen)
    require(pair.get("schema") == "frequency_pair_receipt.v1" and
            pair.get("status") == "TRAINED_BUDGET_OR_EARLY_STOP" and
            pair.get("experiment_class") == protocol["experiment_class"], "pair not closed development training")
    require(set(pair["roles"]) == {"forward", "inverse"}, "pair roles differ")
    require(receipt.get("schema") == "eucap15_development_evaluation_receipt.v1" and
            receipt.get("status") == "COMPLETE_VALIDATION_ONLY_DEVELOPMENT", "evaluation not complete")
    require(receipt["split"] == "validation" and receipt["real_emx_validation"] == "NOT_RUN" and
            receipt["test_evaluation"] == "DEFERRED_UNTIL_FIXED_STUDY_COMPLETE" and
            receipt["q_scan_run"] is False, "evaluation scope changed")
    directory = Path(entry["evaluation_receipt"]["path"]).parent
    require(artifact(receipt, directory, "EVALUATION_SUMMARY.json") == receipt["summary"], "summary source pin differs")
    summary = checked_json(receipt["summary"], seen)
    source_rows = checked_json(artifact(receipt, directory, "ARM_SOURCE_TABLE.json"), seen)
    eval_input = checked_json(artifact(receipt, directory, "EVALUATION_INPUT.json"), seen)
    identity = receipt["identity"]
    require(identity == summary["identity"] == eval_input["identity"], "evaluation input/output identity drift")
    require(eval_input["test_evaluation_authorized"] is False and eval_input["split"] == "validation", "test evaluation forbidden")
    require(summary["schema"] == "eucap15_development_summary.v1" and summary["split"] == "validation", "wrong summary")
    n = protocol["validation_rows"]
    require(summary["target_count"] == receipt["target_count"] == n, "not full protocol validation frame")
    for key, source in (("dataset", "dataset"), ("data_manifest", "source_data"), ("splits", "splits")):
        require(identity[key] == protocol[source], "dataset/manifest/split identity differs")
    require(canonical(summary["protocol"]) == identity["evaluation_protocol_sha256"], "evaluation protocol hash differs")
    require(summary["protocol"]["declared_spans"] == protocol["training_recipe"]["response_spans"], "declared spans changed")
    require(identity["frequency_ghz"] == pair["frequency_ghz"] == protocol["training_recipe"]["frequency_ghz"] and
            identity["label_mode"] == pair["label_mode"] == protocol["training_recipe"]["label_mode"], "frequency/label route differs")
    request = checked_json(pair["request"], seen)
    require(request["experiment_class"] == protocol["experiment_class"] and
            request["dataset_sha256"] == protocol["dataset"]["sha256"] and
            request["data_root"] == pair["data_root"] == str(Path(protocol["dataset"]["path"]).parent), "study data identity differs")
    shapes = [name for name, hidden in protocol["shapes"].items() if hidden == request["train"]["hidden_layers"]]
    require(len(shapes) == 1 and request["train"]["seed"] in protocol["seeds"], "unplanned shape/seed")
    arm = {"shape": shapes[0], "seed": request["train"]["seed"]}
    role_rows = {r["role"]: r for r in source_rows}
    require(len(role_rows) == len(source_rows) and set(role_rows) == set(identity["roles"]), "source table role scope differs")
    scoring = summary["inverse"]["scoring_forward"]
    require(scoring == receipt["inverse_scoring_forward_checkpoint"] == pair["inverse_forward"]["best"], "inverse scoring F differs")
    relationship = "OWN_FORWARD" if scoring == pair["roles"]["forward"]["best"] else "SHARED_FROZEN_FORWARD"
    require(summary["inverse"]["scoring_forward_relationship"] == relationship == pair["inverse_forward"]["source"], "own/common F mislabeled")
    if relationship == "SHARED_FROZEN_FORWARD":
        require(request.get("shared_forward_receipt") == pair["inverse_forward"]["receipt"] and
                identity["roles"]["inverse_forward_common_reference"]["best"] == scoring and
                identity["roles"]["inverse_forward_common_reference"]["receipt"] == pair["inverse_forward"]["receipt"], "shared F receipt drift")
    else:
        require(pair["inverse_forward"]["receipt"] == pair["roles"]["forward"]["receipt"], "own-F receipt differs")
    require(summary["own_forward"]["checkpoint"] == receipt["own_forward_checkpoint"] == pair["roles"]["forward"]["best"], "own F substituted by common F")
    training, training_identity = [], {}
    for role in ("forward", "inverse"):
        src, row = identity["roles"][role], role_rows[role]
        require(all(src[key] == pair["roles"][role][key] for key in ("receipt", "best", "last")), "pair/evaluation native pins differ")
        native = checked_json(src["receipt"], seen)
        config = checked_json(src["config.json"], seen)
        norm = checked_json(src["normalizer.json"], seen)
        contract = checked_json(src["contract.json"], seen)
        require(canonical(norm) == native["normalizer_sha"] == config["normalizer_sha"] == identity["normalizer_sha256"], "normalizer mismatch")
        require(canonical(contract) == config["contract_sha"] == identity["contract_sha256"] and src["contract.json"]["sha256"] == protocol["contract"]["sha256"], "contract mismatch")
        require(native["role"] == config["role"] == role and native["data_sha"] == config["data_sha"] == protocol["dataset"]["sha256"], "training role/data differs")
        require(native["stop_reason"] in ("VALIDATION_EARLY_STOP", "UPDATE_BUDGET_COMPLETE") and
                native["research_comparison_eligible"] is True and native["resume_probe"] is False and
                native["historical_weights_loaded"] is False and native["trainable_weights_changed"] is True and
                native["frozen_forward_unchanged"] is True and native["test_access"] is False, "native receipt not qualified")
        for key, value in protocol["training_recipe"].items():
            if key not in ("role", "seed", "hidden_layers", "steps"):
                require(config.get(key) == value, "training recipe drift: " + key)
        require(config["hidden_layers"] == protocol["shapes"][arm["shape"]] and config["seed"] == arm["seed"], "arm configuration drift")
        require(config["test_labels_optimized"] is False and config["resume_probe"] is False and
                config["research_comparison_eligible"] is True, "probe/test-derived configuration")
        require(config["runtime_source_sha256"]["bb00.py"] == summary["protocol"]["source_sha256"]["bb00.py"],
                "training/evaluation BB00 implementation differs")
        training_identity[role] = {"runtime_source_sha256": config["runtime_source_sha256"],
                                   "recipe": config["recipe"]}
        if role == "inverse":
            require(config["forward_checkpoint"] == scoring["path"], "inverse training F and scoring F differ")
        model = summary["own_forward" if role == "forward" else "inverse"]["model"]
        require(model["architecture"] == config["architecture"] == native["architecture"] and
                model["architecture"]["widths"][1:-1] == config["hidden_layers"] and
                model["seed"] == config["seed"], "saved model architecture identity differs")
        require(native["eligible_rows"] == summary["exposure"], "training/evaluation exposure differs")
        expected = {"seed": arm["seed"], "hidden_layers": json.dumps(config["hidden_layers"]),
            "source_snapshot_geometries": protocol["source_rows"], "gradient_eligible_geometries": protocol["gradient_train_rows"],
            "validation_eligible_geometries": n, "validation_split_geometries": n,
            "test_split_geometries_count_only": protocol["test_rows"], "data_sha256": protocol["dataset"]["sha256"],
            "normalizer_sha256": identity["normalizer_sha256"], "contract_sha256": identity["contract_sha256"],
            "validation_target_order_sha256": summary["target_id_order_sha256"], "best_step": model["step"],
            "last_step": native["completed_step"], "parameter_count": model["parameter_count"],
            "best_model_sha256": model["model_sha"], "last_model_sha256_native_receipt": native["model_sha"],
            "gradient_draws": native["gradient_draws"], "unique_gradient_geometries_seen": native["unique_gradient_geometries"]}
        require(all(row.get(key) == value for key, value in expected.items()), "ARM_SOURCE_TABLE inconsistent")
        for which in ("best", "last"):
            require(row[which + "_checkpoint"] == native[which + "_checkpoint"] == src[which]["path"] and
                    row[which + "_checkpoint_sha256"] == native[which + "_sha256"] == src[which]["sha256"], "checkpoint provenance differs")
        require(model["parameter_count"] == native["parameter_counts"]["total"] and
                0 < model["step"] <= native["completed_step"] <= protocol["training_recipe"]["schedule_total_steps"], "parameter count/budget mismatch")
        training.append({**arm, "role": role, "parameter_count": model["parameter_count"],
            "native_status": native["status"], "stop_reason": native["stop_reason"],
            "started_step": native["started_step"], "last_step": native["completed_step"],
            "updates_last_attempt": native["updates_this_run"], "best_step": model["step"],
            "elapsed_seconds": number(native["elapsed_seconds"], "elapsed", nullable=False),
            "elapsed_scope": "LAST_NATIVE_ATTEMPT_ONLY" if native["started_step"] else "COMPLETE_NATIVE_ROLE_RUN",
            "gradient_eligible_geometries": protocol["gradient_train_rows"],
            "gradient_draws": native["gradient_draws"], "unique_gradient_geometries_seen": native["unique_gradient_geometries"],
            "best_checkpoint": src["best"]["path"], "best_checkpoint_sha256": src["best"]["sha256"],
            "convergence": "NOT_ESTABLISHED", "REAL_EMX_VALIDATION": "NOT_RUN"})
    grid = summary["inverse"]["modes"]["grid"]
    require(grid["evidence"] == "SELF_PROXY" and grid["real_emx_validation"] == "NOT_RUN", "inverse evidence mislabeled")
    for key in ("joint_hit_count", "joint_hit_denominator", "target_failure_count", "evaluable_targets", "analytical_pass_count"):
        integer(grid[key], key)
    require(grid["joint_hit_denominator"] == n and grid["joint_hit_count"] + grid["target_failure_count"] == n and
            0 <= grid["joint_hit_count"] <= grid["evaluable_targets"] <= grid["analytical_pass_count"] <= n,
            "joint/invalid/geometry denominator drift")
    require(grid["joint_hit_rate"] == grid["joint_hit_count"] / n and
            grid["analytical_pass_rate"] == grid["analytical_pass_count"] / n, "saved rates do not match counts")
    inverse = {**arm, **{k: grid[k] for k in ("joint_hit_count", "joint_hit_denominator", "joint_hit_rate", "target_failure_count",
        "evaluable_targets", "analytical_pass_count", "analytical_pass_rate", "max_declared_span_error_p95", "p95_status",
        "conditional_evaluable_max_error_p95")}, "invalid_or_failed_targets": n - grid["evaluable_targets"],
        "scoring_forward_sha256": scoring["sha256"], "scoring_relationship": relationship,
        "evidence": "SELF_PROXY", "manufacturability": grid["manufacturability"], "REAL_EMX_VALIDATION": "NOT_RUN"}
    features = feature_rows(arm, "OWN_FORWARD", summary["own_forward"]["features"], summary["protocol"], n)
    features += feature_rows(arm, "INVERSE_GRID", grid["features"], summary["protocol"], n)
    return {"arm": arm, "pair": pair, "summary": summary, "identity": identity,
            "source_rows": role_rows, "training_identity": training_identity,
            "training": training, "inverse": inverse, "features": features}


def build(input_path, out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    seen = {}
    try:
        return _build(Path(input_path).resolve(), out, seen)
    except Exception as error:
        dump(out / "TABLES_FAILED.json", {"status": "FAIL_PRESERVED", "error": repr(error),
            "metadata_sources_read": list(seen.values()), "models_or_raw_arrays_read": False})
        raise


def _build(input_path, out, seen):
    inputs = checked_json(pin(input_path), seen)
    require(inputs.get("schema") == "eucap15_development_tables_input.v1", "wrong table input schema")
    protocol = checked_json(inputs["protocol"], seen)
    require(protocol["schema"] == "eucap15_current_snapshot_development_protocol.v1" and
            protocol["experiment_class"] == "DEVELOPMENT_CURRENT_SNAPSHOT" and
            len(protocol["seeds"]) == len(set(protocol["seeds"])) == 3, "wrong development protocol/three-seed plan")
    require(integer(protocol["validation_rows"], "validation_rows") > 0, "empty validation frame")
    data_receipt = checked_json(protocol["data_receipt"], seen)
    require(data_receipt["dataset"] == protocol["dataset"] and data_receipt["data_manifest"] == protocol["source_data"] and
            selected_geometry_count(data_receipt) == protocol["source_rows"] and
            data_receipt["split_counts"] == {"train": protocol["gradient_train_rows"], "validation": protocol["validation_rows"], "test": protocol["test_rows"]}, "protocol/data receipt denominator mismatch")
    actual = [inspect_arm(entry, protocol, seen) for entry in inputs["arms"]]
    indexed = {(x["arm"]["shape"], x["arm"]["seed"]): x for x in actual}
    require(len(indexed) == len(actual), "duplicate shape/seed evaluation")
    baseline_shapes = [name for name, widths in protocol["shapes"].items() if widths == protocol["training_recipe"]["hidden_layers"]]
    require(len(baseline_shapes) == 1, "ambiguous protocol baseline")
    baseline_key = (baseline_shapes[0], protocol["training_recipe"]["seed"])
    require(baseline_key in indexed, "closed baseline evaluation required to bind common inverse ruler")
    baseline = indexed[baseline_key]
    common = baseline["pair"]["roles"]["forward"]
    shared_identity = {key: baseline["identity"][key] for key in
        ("dataset", "data_manifest", "splits", "normalizer_sha256", "contract_sha256", "evaluation_protocol_sha256", "wrapper_source")}
    for item in actual:
        require(all(item["identity"][key] == value for key, value in shared_identity.items()), "cross-arm common scientific identity differs")
        require(item["training_identity"] == baseline["training_identity"], "cross-arm trainer/recipe identity differs")
        require(item["summary"]["target_id_order_sha256"] == baseline["summary"]["target_id_order_sha256"] and
                item["summary"]["protocol"] == baseline["summary"]["protocol"], "target order/evaluation protocol differs")
        require(item["pair"]["inverse_forward"]["best"] == common["best"] and
                item["pair"]["inverse_forward"]["receipt"] == common["receipt"], "inverse comparisons use different rulers")
        if "inverse_forward_common_reference" in item["source_rows"]:
            row = dict(item["source_rows"]["inverse_forward_common_reference"], role="forward")
            require(row == baseline["source_rows"]["forward"], "common F source row is not baseline reference")
    status, training, features, inverse = [], [], [], []
    for shape in protocol["shapes"]:
        for seed in protocol["seeds"]:
            item = indexed.get((shape, seed))
            status.append({"shape": shape, "seed": seed, "status": "CLOSED_VALIDATION_EVALUATION" if item else "NO_CLOSED_EVALUATION_PROVIDED",
                           "validation_targets": protocol["validation_rows"] if item else None, "training_convergence": "NOT_ESTABLISHED"})
            if item:
                training.extend(item["training"]); features.extend(item["features"]); inverse.append(item["inverse"])
    observations = []
    for row in training:
        for metric in ("parameter_count", "last_step", "best_step", "elapsed_seconds"):
            observations.append({"shape": row["shape"], "seed": row["seed"], "population": row["role"].upper() + "_TRAINING",
                "feature": "NOT_APPLICABLE", "metric": metric,
                "value": None if metric == "elapsed_seconds" and row["elapsed_scope"] == "LAST_NATIVE_ATTEMPT_ONLY" else row[metric]})
    for row in features:
        for metric in ("mae_full_cohort", "rmse_full_cohort", "conditional_mae", "conditional_rmse", "conditional_absolute_error_p95"):
            observations.append({"shape": row["shape"], "seed": row["seed"], "population": row["population"],
                                 "feature": row["feature"], "metric": metric, "value": row[metric]})
    for row in inverse:
        for metric in ("joint_hit_rate", "analytical_pass_rate", "max_declared_span_error_p95"):
            observations.append({"shape": row["shape"], "seed": row["seed"], "population": "INVERSE_GRID",
                                 "feature": "JOINT", "metric": metric, "value": row[metric]})
    metric_keys = list(dict.fromkeys((r["population"], r["feature"], r["metric"]) for r in observations))
    shape_summary = []
    for shape in protocol["shapes"]:
        completed = [seed for seed in protocol["seeds"] if (shape, seed) in indexed]
        for population, feature, metric in metric_keys:
            selected = [r for r in observations if (r["shape"], r["population"], r["feature"], r["metric"]) == (shape, population, feature, metric)]
            values = [r["value"] for r in selected]
            complete = len(completed) == 3 and len(values) == 3
            defined = complete and all(value is not None for value in values)
            shape_summary.append({"shape": shape, "population": population, "feature": feature, "metric": metric,
                "planned_seeds": json.dumps(protocol["seeds"]), "completed_seeds": json.dumps(completed),
                "status": "COMPLETE_THREE_SEEDS" if complete else "PARTIAL", "seed_n": len(values),
                "null_seed_n": sum(value is None for value in values), "mean_across_seeds": statistics.mean(values) if defined else None,
                "sample_sd_across_seeds": statistics.stdev(values) if defined else None,
                "metric_status": "DEFINED" if defined else "PARTIAL_OR_UNDEFINED_PRESERVED",
                "interpretation": "equal-seed descriptive mean/sampleSD; not pooled-target P95, CI, or winner"})
    tables = {"ARM_STATUS": status, "TRAINING_ARMS": training, "VALIDATION_FEATURE_METRICS": features,
              "INVERSE_GRID": inverse, "SHAPE_THREE_SEED_SUMMARY": shape_summary}
    for name, table in tables.items():
        csv_table(out / (name + ".csv"), table)
        dump(out / (name + ".json"), table)
    methods = {"scope": "DEVELOPMENT_CURRENT_SNAPSHOT_VALIDATION_ONLY", "validation_targets_per_arm": protocol["validation_rows"],
        "target_order_sha256": baseline["summary"]["target_id_order_sha256"], "common_inverse_forward": common,
        "evaluation_protocol": baseline["summary"]["protocol"],
        "caption": "Own-forward validation regression against existing EM labels; grid inverse residuals use one fixed baseline F and are SELF_PROXY, not fresh EMX. All original targets remain in joint/geometry denominators.",
        "null_policy": "Saved full-cohort nulls preserved; conditional errors have separate evaluable/invalid counts. No fill-zero and no undefined-seed exclusion.",
        "mape": "NOT_REPORTED_IN_CLOSED_JSON; no per-row recomputation performed",
        "p95": "Feature P95 is saved conditional absolute error. Joint P95 retains source failure-as-infinity status; null does not mean zero.",
        "seeds": "Three-seed summaries only when all three evaluations exist; mean and sample SD of arm statistics are descriptive, not pooled-error statistics or confidence intervals.",
        "timing": "Per-role native elapsed time; resumed roles explicitly last-attempt-only, not invented cumulative cost.",
        "comparison": "No raw training-loss ranking, winner, causal superiority, test selection, or tuning; common-F training randomness is not represented by inverse seed variation.",
        "raw_pin_policy": "Weights, NPZ, per-target CSV and history are NOT_READ; their saved pins are cross-compared. Only JSON metadata listed in SOURCE_MANIFEST is hash checked.",
        "model_calls": 0, "training_calls": 0, "test_evaluations": 0, "native_calls": 0, "figures": 0}
    dump(out / "METHODS_AND_CAPTIONS.json", methods)
    for source in list(seen.values()):
        checked_json(source, seen)
    dump(out / "SOURCE_MANIFEST.json", {"schema": "eucap15_development_table_sources.v1",
        "metadata_pins_verified": list(seen.values()), "input": pin(input_path), "implementation": pin(__file__)})
    receipt = {"schema": "eucap15_development_tables_receipt.v1", "status": "COMPLETE" if len(indexed) == len(protocol["shapes"]) * 3 else "PARTIAL",
        "completed_arms": len(indexed), "planned_arms": len(protocol["shapes"]) * 3,
        "interpretation": "table completeness only, not training convergence or physics accuracy",
        "sources": pin(out / "SOURCE_MANIFEST.json"), "artifacts": [pin(p) for p in sorted(out.iterdir()) if p.is_file()],
        "created_utc": datetime.now(timezone.utc).isoformat(), "new_model_or_test_calls": 0}
    dump(out / "TABLES_RECEIPT.json", receipt)
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as handle:
        for path in sorted(out.iterdir()):
            if path.name != "SHA256SUMS" and path.is_file():
                handle.write(pin(path)["sha256"] + "  " + path.name + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.out), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
