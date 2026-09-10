"""Descriptive validation tradeoffs from already closed five-shape tables.

Post-hoc equal-seed aggregation, not a preregistered cross-shape decision rule.
Never changes checkpoints, the shared inverse F, the frozen128 pilot, or FINAL.
"""
from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path
import statistics
import sys

from .eucap15_formal_development_view import checked, document, pin, require, write_csv
from .data import _write_json
from .io import utc_now


FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
SHAPES = ("2x256", "3x128", "3x256", "3x512", "5x256")
SEEDS = (17, 29, 43)
SPANS = (2.5, 2.5, 20., .8)


def reduce_tables(features, training, inverse):
    """Use full-cohort RMSE only; undefined seeds must never disappear."""
    fm = {(r["shape"], r["seed"], r["population"], r["feature"]): r for r in features}
    tm = {(r["shape"], r["seed"], r["role"]): r for r in training}
    im = {(r["shape"], r["seed"]): r for r in inverse}
    require(len(fm) == len(features) == 120 and len(tm) == len(training) == 30 and
            len(im) == len(inverse) == 15, "missing/duplicate table rows")
    per_seed, shapes = [], []
    for shape in SHAPES:
        grouped = {}
        for pop, role in (("OWN_FORWARD", "forward"), ("INVERSE_GRID", "inverse")):
            records = []
            for seed in SEEDS:
                rows = [fm[shape, seed, pop, f] for f in FEATURES]
                require(all(r["fixed_denominator"] == 1269 for r in rows), "original denominator changed")
                require(all(r["REAL_EMX_VALIDATION"] == "NOT_RUN" for r in rows), "not a native ranking")
                require(all(r["invalid_count"] == 1269-r["evaluable_denominator"] for r in rows), "invalid count mismatch")
                require(len({r["evaluable_denominator"] for r in rows}) == 1, "feature supports differ")
                expected = "VALIDATION_ORIGINAL_EM_LABELS" if pop == "OWN_FORWARD" else "SELF_PROXY"
                require(all(r["evidence"] == expected for r in rows), "evidence class differs")
                complete = all(r["evaluable_denominator"] == 1269 and r["rmse_full_cohort"] is not None for r in rows)
                if complete:
                    values = [r["rmse_full_cohort"] for r in rows]
                    require(all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in values), "bad full RMSE")
                    mse = math.fsum((x/span)**2 for x, span in zip(values, SPANS))/4
                    score = math.sqrt(mse)
                else:
                    require(all(r["rmse_full_cohort"] is None for r in rows), "incomplete support reported as full RMSE")
                    mse = score = None
                tr = tm[shape, seed, role]
                require(tr["started_step"] == 0 and tr["elapsed_scope"] == "COMPLETE_NATIVE_ROLE_RUN", "partial timing cannot be pooled")
                require(tr["gradient_eligible_geometries"] == 3801 and tr["REAL_EMX_VALIDATION"] == "NOT_RUN", "training scope differs")
                row = dict(shape=shape, seed=seed, role=role, unique_validation_targets=1269,
                    fixed_span_mse=mse, fixed_span_rmse=score, metric_status="DEFINED" if complete else "UNDEFINED_FULL_COHORT_HAS_FAILURES",
                    invalid_count=rows[0]["invalid_count"], evidence=expected,
                    parameter_count=tr["parameter_count"], elapsed_seconds=tr["elapsed_seconds"],
                    updates=tr["updates_last_attempt"], stop_reason=tr["stop_reason"],
                    checkpoint_path=tr["best_checkpoint"], checkpoint_sha256=tr["best_checkpoint_sha256"])
                per_seed.append(row); records.append(row)
            grouped[role] = records
        fr, ir = grouped["forward"], grouped["inverse"]
        require(all(r["fixed_span_mse"] is not None for r in fr), "forward ranking needs all three complete seeds")
        require(len({r["parameter_count"] for r in fr}) == len({r["parameter_count"] for r in ir}) == 1, "parameters vary within shape")
        scores = [r["fixed_span_rmse"] for r in fr]
        igrid = [im[shape, seed] for seed in SEEDS]
        require(all(r["joint_hit_denominator"] == 1269 and r["evidence"] == "SELF_PROXY" for r in igrid), "inverse scope mismatch")
        require(len({r["scoring_forward_sha256"] for r in inverse}) == 1, "different inverse rulers")
        for r in igrid:
            require(r["joint_hit_count"]+r["target_failure_count"] == 1269 and
                    r["analytical_pass_count"]+r["invalid_or_failed_targets"] == 1269, "inverse counts do not reconcile")
        all_i = all(r["fixed_span_mse"] is not None for r in ir)
        shapes.append(dict(shape=shape, seed_n=3, unique_validation_targets=1269,
            seed_target_evaluations=3807, unique_validation_geometry_hashes=1269,
            forward_fixed_span_rmse_equal_seed_rms=math.sqrt(statistics.mean(r["fixed_span_mse"] for r in fr)),
            forward_seed_rmse_mean=statistics.mean(scores), forward_seed_rmse_sd=statistics.stdev(scores),
            forward_seed_rmse_median=statistics.median(scores), forward_seed_rmse_min=min(scores), forward_seed_rmse_max=max(scores),
            forward_parameters=fr[0]["parameter_count"], inverse_parameters=ir[0]["parameter_count"],
            forward_training_seconds_mean=statistics.mean(r["elapsed_seconds"] for r in fr),
            inverse_training_seconds_mean=statistics.mean(r["elapsed_seconds"] for r in ir),
            forward_updates_total=sum(r["updates"] for r in fr), inverse_updates_total=sum(r["updates"] for r in ir),
            forward_budget_stopped_seeds=sum(r["stop_reason"] == "UPDATE_BUDGET_COMPLETE" for r in fr),
            inverse_budget_stopped_seeds=sum(r["stop_reason"] == "UPDATE_BUDGET_COMPLETE" for r in ir),
            inverse_full_cohort_rmse=(math.sqrt(statistics.mean(r["fixed_span_mse"] for r in ir)) if all_i else None),
            inverse_full_cohort_metric_status="DEFINED" if all_i else "UNDEFINED_AT_LEAST_ONE_SEED_HAS_FAILURES",
            inverse_joint_hits_seed_target_evaluations=sum(r["joint_hit_count"] for r in igrid),
            inverse_joint_hit_rate_equal_seed=statistics.mean(r["joint_hit_count"]/1269 for r in igrid),
            inverse_analytical_failures_seed_target_evaluations=sum(r["invalid_or_failed_targets"] for r in igrid),
            inverse_evidence="SELF_PROXY_COMMON_F_NOT_PHYSICS"))
    ordered = sorted(shapes, key=lambda r: r["forward_fixed_span_rmse_equal_seed_rms"])
    for row in shapes:
        row["descriptive_forward_rank"] = 1 + sum(
            r["forward_fixed_span_rmse_equal_seed_rms"] < row["forward_fixed_span_rmse_equal_seed_rms"] for r in shapes)
        row["forward_error_parameter_nondominated"] = not any(
            r["forward_parameters"] <= row["forward_parameters"] and
            r["forward_fixed_span_rmse_equal_seed_rms"] <= row["forward_fixed_span_rmse_equal_seed_rms"] and
            (r["forward_parameters"] < row["forward_parameters"] or
             r["forward_fixed_span_rmse_equal_seed_rms"] < row["forward_fixed_span_rmse_equal_seed_rms"]) for r in shapes)
    minimum = ordered[0]["forward_fixed_span_rmse_equal_seed_rms"]
    return per_seed, shapes, [r["shape"] for r in ordered if r["forward_fixed_span_rmse_equal_seed_rms"] == minimum]


def run(receipt_pin, protocol_pin, out):
    out = Path(out).absolute()
    require(".." not in out.parts and not any(p.is_symlink() for p in (out, *out.parents)), "unsafe output")
    receipt, protocol = document(checked(receipt_pin)), document(checked(protocol_pin))
    require(receipt["status"] == "COMPLETE" and receipt["completed_arms"] == receipt["planned_arms"] == 15, "tables not complete")
    require(tuple(protocol["shapes"]) == SHAPES and tuple(protocol["seeds"]) == SEEDS and
            tuple(protocol["training_recipe"]["response_spans"]) == SPANS, "frozen design changed")
    needed = ("VALIDATION_FEATURE_METRICS.json", "TRAINING_ARMS.json", "INVERSE_GRID.json", "METHODS_AND_CAPTIONS.json")
    source_pins = {"receipt": receipt_pin, "protocol": protocol_pin}
    tables = {}
    for name in needed:
        matches = [p for p in receipt["artifacts"] if Path(p["path"]).name == name]
        require(len(matches) == 1, "missing/duplicate table artifact")
        source_pins[name] = matches[0]
        tables[name] = document(checked(matches[0]))
    methods = tables["METHODS_AND_CAPTIONS.json"]
    require(tuple(methods["evaluation_protocol"]["declared_spans"]) == SPANS and
            methods["validation_targets_per_arm"] == 1269, "evaluation contract differs")
    require({r["scoring_forward_sha256"] for r in tables["INVERSE_GRID.json"]} ==
            {methods["common_inverse_forward"]["best"]["sha256"]}, "inverse table/methods ruler differs")
    out.mkdir(parents=True, exist_ok=False)
    try:
        arm, shape, minimum = reduce_tables(tables[needed[0]], tables[needed[1]], tables[needed[2]])
        require(sum(r["updates"] for r in arm) == 538099, "training cost total differs")
        for item in source_pins.values():
            checked(item)
        write_csv(out/"PER_SEED_SCORES.csv", arm, list(arm[0]))
        write_csv(out/"VALIDATION_TRADEOFF_5ROW.csv", shape, list(shape[0]))
        _write_json(out/"VALIDATION_TRADEOFF_5ROW.json", shape)
        summary = dict(schema="eucap15_validation_tradeoff.v1", status="COMPLETE_DESCRIPTIVE_VALIDATION_ONLY", created_utc=utc_now(),
            empirical_lowest_forward_score_shapes=minimum, automatic_model_selection="NOT_PERFORMED",
            final_model_selection="NOT_PERFORMED", cross_shape_pre_registered_rule=False,
            metric="per_seed=sqrt(mean_4((full_cohort_feature_RMSE/span)^2)); equal_seed_rms=sqrt(mean_3(per_seed^2))",
            metric_source="Frozen equal-four-feature fixed-span residual definition; equal-seed aggregation operationalized after seeing completed tables.",
            no_posthoc_preregistration_claim=True, frozen_spans=list(SPANS),
            full_cohort_inverse_rank="NOT_COMPUTABLE_WHERE_SEED_FULL_COHORT_RMSE_UNDEFINED_NO_CONDITIONAL_SUBSTITUTION",
            inference="Descriptive empirical rank only, under this dataset/split/training+early-stop policy; not statistical significance, causal universal superiority or physical validation.",
            seed_uncertainty="n=3 sample SD; not CI, independent datasets or3807 independent targets. All seeds reuse1269 targets.",
            shared_inverse_forward_sha256=methods["common_inverse_forward"]["best"]["sha256"],
            same_family_independence="NOT_CERTIFIED", convergence="NOT_ESTABLISHED",
            native_validation="NOT_RUN", changed_pilot128=False, changed_checkpoints=False,
            no_new_models_or_predictions=True, new_training_updates=0, test_evaluation_calls=0,
            source_pins=source_pins, script=pin(__file__), command=sys.argv, python=sys.executable,
            forward_rank_table=shape)
        _write_json(out/"SUMMARY.json", summary)
        artifacts = [pin(out/name) for name in ("PER_SEED_SCORES.csv", "VALIDATION_TRADEOFF_5ROW.csv", "VALIDATION_TRADEOFF_5ROW.json", "SUMMARY.json")]
        _write_json(out/"MANIFEST.json", {"schema":"eucap15_validation_tradeoff_manifest.v1", "artifacts":artifacts})
        artifacts.append(pin(out/"MANIFEST.json"))
        with (out/"SHA256SUMS").open("x") as stream:
            stream.writelines(p["sha256"]+"  "+Path(p["path"]).name+"\n" for p in artifacts)
        return summary
    except Exception as exc:
        _write_json(out/"FAILURE.json", {"status":"FAIL_PRESERVED", "error":repr(exc), "sources":source_pins})
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("receipt", "receipt-sha256", "protocol", "protocol-sha256", "out"):
        p.add_argument("--"+name, required=True)
    a=p.parse_args()
    rp, pp = pin(a.receipt), pin(a.protocol)
    require(rp["sha256"] == a.receipt_sha256 and pp["sha256"] == a.protocol_sha256, "frozen input differs")
    result=run(rp, pp, a.out)
    print(result["status"], result["empirical_lowest_forward_score_shapes"])


if __name__ == "__main__":
    main()
