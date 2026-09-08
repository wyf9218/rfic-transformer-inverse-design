"""Frozen, sharded single-frequency A/B/C evaluation and D preselection.

This module never trains, launches EMX, modifies source labels, or creates a
controller. ``prepare`` reads metadata/labels but makes zero model calls;
``run`` reuses committed shards and optionally adapts existing A/B predictions.
All inverse predictions are SELF_PROXY, not new physical measurements.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import time

import numpy as np

from .io import canonical_sha, load_checkpoint, read_json, save_json, sha256, utc_now
from .frequency_evaluation import FEATURES, UNITS, pin, verify_pin
from .frequency_profile import LABEL_MODES, frequency_mask

PANELS = {"A": "FORWARD_HELDOUT_EMX", "B": "INVERSE_HELDOUT_FEASIBLE_TARGET",
          "C": "INVERSE_RANDOM_BOX_STRESS", "D": "INVERSE_GENERATED_FRESH_EMX"}
TARGET_COLUMNS = ("target_lp_nh", "target_ls_nh", "target_q_scalar", "target_k_abs")
BANDS = ((5, 11), (12, 18), (19, 25), (26, 32), (33, 39), (40, 46), (47, 53), (54, 60))


def _publish_bytes(path, producer, *, allow_identical=False):
    """Publish a complete artifact once; identical committed copies may resume."""
    path=Path(path)
    fd,temporary=tempfile.mkstemp(prefix=".uncommitted-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as stream:
            producer(stream);stream.flush();os.fsync(stream.fileno())
        if path.exists():
            if not allow_identical or sha256(path)!=sha256(temporary): raise FileExistsError("existing artifact differs or no-clobber: "+str(path))
        else: os.link(temporary,path)
    finally:
        os.unlink(temporary)


def _csv(path, rows, fields=None, *, allow_identical=False):
    rows = list(rows)
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _publish_bytes(path,write,allow_identical=allow_identical)


def _read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _jsonl(path, rows, *, allow_identical=False):
    def write(stream):
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False)+"\n")
    _publish_bytes(path,write,allow_identical=allow_identical)


def _resumable_json(path, value):
    _publish_bytes(path,lambda stream:stream.write(json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+"\n"),allow_identical=True)


def _float(value):
    if value is None or value == "": return None
    value = float(value)
    return value if np.isfinite(value) else None


def _bool(value):
    if value in (True, "True", "true", 1, "1"): return True
    if value in (False, "False", "false", 0, "0"): return False
    raise ValueError("exact boolean field required")


def evaluation_window(train_values, fallback_spans):
    """Freeze train-only linear P01/P99; preserve constant sampling dimensions."""
    values = np.asarray(train_values, dtype=np.float64)
    fallback = np.asarray(fallback_spans, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 4 or not len(values) or not np.isfinite(values).all():
        raise ValueError("nonempty finite eligible train [N,4] required")
    if fallback.shape != (4,) or not np.isfinite(fallback).all() or np.any(fallback <= 0):
        raise ValueError("frozen training response spans must be four positive values")
    low, high = np.quantile(values, [.01, .99], axis=0, method="linear")
    span = high-low
    degenerate = span == 0
    scale = np.where(degenerate, fallback, span)
    return {"source": "ELIGIBLE_TRAIN_ONLY", "n_train": len(values), "quantile_method": "linear",
        "p01": low.tolist(), "p99": high.tolist(), "actual_p99_minus_p01": span.tolist(),
        "degenerate_dimensions": degenerate.tolist(), "evaluation_scale": scale.tolist(),
        "scale_source_per_feature": ["FROZEN_TRAINING_RESPONSE_SPAN_FALLBACK_CONSTANT_DIMENSION" if x else
                                     "ELIGIBLE_TRAIN_P99_MINUS_P01" for x in degenerate],
        "fallback_spans": fallback.tolist(), "tolerance_abs": (.05*scale).tolist(),
        "tolerance_definition": "TRAIN_SCALE_5_PERCENT", "relation": ["EQ"]*4,
        "not_target_relative_percentage": True,
        "sampling_caveat": "marginal Cartesian exploration box, joint feasibility UNKNOWN",
        "train_min": values.min(0).tolist(), "train_max": values.max(0).tolist()}


def randomized_lhs(window, count, seed):
    if type(count) is not int or count < 1 or type(seed) is not int:
        raise ValueError("positive request count and integer seed required")
    # Each column has exactly one jittered point in every marginal stratum.
    # A separate independent column permutation retains LHS, not IID claims.
    rng = np.random.default_rng(seed)
    unit = np.column_stack([(rng.permutation(count)+rng.random(count))/count for _ in range(4)])
    low, high = np.asarray(window["p01"]), np.asarray(window["p99"])
    return low+(high-low)*unit


def _load_context(config):
    from .training import Bundle
    from .bb00 import prepare_bb00
    f, mode = config["frequency_ghz"], config["label_mode"]
    if type(f) is not int or not 5 <= f <= 60 or mode not in LABEL_MODES:
        raise ValueError("exact integer frequency and explicit label mode required")
    bundle = Bundle(config["data_root"])
    # Deserialization only: do not construct models or predict in prepare.
    states = [load_checkpoint(config[key]) for key in ("forward_checkpoint", "inverse_checkpoint")]
    fs, ins = states
    norm, train, _, at, exposure = prepare_bb00(bundle, fs["contract"],
        fs["normalizer"]["response_spans"], True, frequency_ghz=f, label_mode=mode)
    for state, role in zip(states, ("forward", "inverse")):
        if (state.get("schema") != "bb00_training_state.v1" or state.get("role") != role or
            state["data_sha"] != bundle.data_sha or state["normalizer_sha"] != canonical_sha(norm) or
            canonical_sha(state["normalizer"]) != canonical_sha(norm) or
            state["contract_sha"] != canonical_sha(fs["contract"]) or
            state.get("research_comparison_eligible") is False or state.get("resume_probe") is True):
            raise ValueError("model/data/normalizer/route/eligibility mismatch")
    if (ins["forward_checkpoint_sha256"] != sha256(config["forward_checkpoint"]) or
            ins["forward_model_sha"] != fs["model_sha"]):
        raise ValueError("inverse not bound to the exact frozen forward")
    eligible = frequency_mask(bundle, f, mode)
    test = np.flatnonzero((bundle.arrays["split"] == 2) & eligible)
    if not len(test): raise ValueError("NO_ELIGIBLE_TEST_LABELS")
    ids = bundle.arrays["geometry_ids"].astype(str)
    hashes = bundle.arrays["geometry_sha256"].astype(str)
    if len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
        raise ValueError("duplicate source geometry ID/hash")
    if set(hashes[train]) & set(hashes[test]): raise ValueError("train/test geometry overlap")
    return bundle, fs, ins, norm, train, test, at, exposure


def _reuse_identity(root, identity):
    root = Path(root).resolve(strict=True)
    summary = read_json(root/"EVALUATION_SUMMARY.json")
    if summary.get("status") != "COMPLETE_DESCRIPTIVE_EVALUATION" or summary.get("split") != "test":
        raise ValueError("reuse requires completed existing test evaluation")
    for key in ("dataset", "data_manifest", "splits", "forward_checkpoint", "inverse_checkpoint"):
        if summary["identity"][key]["sha256"] != identity[key]["sha256"]:
            raise ValueError("reuse model/data/split identity mismatch: "+key)
    for key in ("frequency_ghz", "label_mode"):
        if summary[key] != identity[key]: raise ValueError("reuse route mismatch")
    sources = {"summary": pin(root/"EVALUATION_SUMMARY.json")}
    for name in ("forward_predictions.csv", "inverse_predictions.csv"):
        original = summary["artifacts"][name]
        verify_pin(original)
        sources[name] = pin(root/name)
        if sources[name]["sha256"] != original["sha256"]: raise ValueError("reuse file identity mismatch")
    return {"root": str(root), "created_utc": summary["created_utc"], "sources": sources,
            "previous_scoring_protocol": summary.get("protocol"), "timing_available": False}


def make_targets(config, bundle, test, at, window):
    f, mode, study = config["frequency_ghz"], config["label_mode"], config["study_id"]
    seed = config["sampling_seed"]
    targets = []
    for panel in ("A", "B", "C"):
        values = (randomized_lhs(window, config["random_count"], seed) if panel == "C"
                  else np.asarray(bundle.arrays["y"][test, at], dtype=float))
        for i, values_row in enumerate(values):
            ref = str(bundle.arrays["geometry_ids"][test[i]]) if panel != "C" else ""
            refhash = str(bundle.arrays["geometry_sha256"][test[i]]) if panel != "C" else ""
            target_id = f"{study}:{panel}:f{f}:{i:06d}"
            outside = bool(((values_row < window["train_min"]) | (values_row > window["train_max"])).any())
            row = {"study_id": study, "panel": panel, "target_id": target_id, "frequency_ghz": f,
                "label_mode": mode, "reference_geometry_id": ref, "reference_geometry_hash": refhash,
                "source_index": int(test[i]) if panel != "C" else "", "source_split": "test" if panel != "C" else "NOT_APPLICABLE",
                "sampling_method": "randomized_LHS" if panel == "C" else "ALL_ELIGIBLE_TEST_GEOMETRIES",
                "sampling_seed": seed if panel == "C" else "", "random_replicate_id": 0 if panel == "C" else "",
                "sampling_window_id": canonical_sha(window) if panel == "C" else "EMPIRICAL_HELDOUT_TUPLES",
                "support_tag": "OUTSIDE_TRAIN_MARGINAL_RANGE" if outside else "INSIDE_TRAIN_MARGINAL_RANGE_JOINT_NOT_PROVEN",
                "target_feasibility": "UNKNOWN" if panel == "C" else "KNOWN_REFERENCE_EMX_GEOMETRY",
                "descriptor_valid": bool(bundle.arrays["broadband_descriptor_valid"][test[i], at]) if panel != "C" else "NOT_APPLICABLE",
                "strict_valid": bool(bundle.arrays["strict_lumped_valid"][test[i], at]) if panel != "C" else "NOT_APPLICABLE",
                "emx_preselected": False, "selection_group": "", "selection_probability_if_applicable": ""}
            row.update(dict(zip(TARGET_COLUMNS, map(float, values_row))))
            for j, name in enumerate(FEATURES):
                row["relation__"+name] = "EQ"; row["tolerance__"+name] = window["tolerance_abs"][j]
            targets.append(row)
    if len({row["target_id"] for row in targets}) != len(targets): raise ValueError("duplicate target ID")
    rng = np.random.default_rng(config["emx_selection_seed"])
    selected = []
    for panel in ("B", "C"):
        population = [row for row in targets if row["panel"] == panel]
        count = min(config["emx_per_panel"], len(population))
        for index in sorted(rng.choice(len(population), count, replace=False).tolist()):
            row = population[index]
            row.update(emx_preselected=True, selection_group=panel, selection_probability_if_applicable=count/len(population))
            selected.append({"target_id": row["target_id"], "selection_group": panel,
                "population_N": len(population), "selected_N": count, "inclusion_probability": count/len(population)})
    return targets, selected


def prepare(config, out):
    """Freeze targets and audit IDs before new scores; zero network predictions."""
    config = read_json(config) if not isinstance(config, dict) else dict(config)
    if config.get("schema") != "frequency_large_eval_request.v1": raise ValueError("request schema mismatch")
    f = config["frequency_ghz"]
    for key, default in (("batch_size", 256), ("random_count", 10000), ("emx_per_panel", 100),
                         ("sampling_seed", 2026090815+f), ("emx_selection_seed", 2026090900+f), ("device", "cpu")):
        config.setdefault(key, default)
    if config["device"] != "cpu": raise ValueError("first profile admits CPU only; no silent hardware change")
    if type(config["batch_size"]) is not int or config["batch_size"] < 1: raise ValueError("positive batch size required")
    if type(config["emx_per_panel"]) is not int or not 0 <= config["emx_per_panel"] <= 100: raise ValueError("first audit budget 0..100 per panel")
    if not config.get("study_id") or not config.get("dataset_scope"): raise ValueError("study and actual dataset scope required")
    out = Path(out).resolve()
    if out.exists(): raise FileExistsError(out)
    bundle, fs, ins, norm, train, test, at, exposure = _load_context(config)
    source_count = len(bundle.arrays["geometry"])
    if "FORMAL_10K" in config["dataset_scope"] and source_count != 10000: raise ValueError("formal 10K scope requires exactly10000 source geometries")
    identity = {name: pin(Path(config["data_root"])/name) for name in ("dataset.npz", "data_manifest.json", "splits.json")}
    identity = {"dataset": identity["dataset.npz"], "data_manifest": identity["data_manifest.json"], "splits": identity["splits.json"],
        "forward_checkpoint": pin(config["forward_checkpoint"]), "inverse_checkpoint": pin(config["inverse_checkpoint"]),
        "frequency_ghz": f, "label_mode": config["label_mode"], "normalizer_sha256": canonical_sha(norm),
        "contract_sha256": canonical_sha(fs["contract"])}
    for key in ("profile_json", "model_index"):
        if config.get(key): identity[key] = pin(config[key])
    window = evaluation_window(bundle.arrays["y"][train, at], norm["response_spans"])
    targets, selected = make_targets(config, bundle, test, at, window)
    previous = _reuse_identity(config["reuse_evaluation_root"], identity) if config.get("reuse_evaluation_root") else None
    out.mkdir(parents=True, exist_ok=False)
    save_json(out/"REQUEST.json", config)
    _csv(out/"target_manifest.csv", targets)
    save_json(out/"emx_preselected.json", {"schema": "frequency_fresh_emx_preselection.v1", "created_utc": utc_now(),
        "sampling": "uniform random without replacement; no success-based replacement", "seed": config["emx_selection_seed"],
        "selected": selected, "selection_timing": "BEFORE_NEW_SCORING_EXISTING_B_PREVIOUSLY_SCORED" if previous else "BEFORE_ANY_SCORING_IN_THIS_STUDY",
        "historical_scoring_provenance": previous, "REAL_EMX_VALIDATION": "NOT_RUN"})
    save_json(out/"normalizer.json", norm); save_json(out/"geometry_contract.json", fs["contract"])
    protocol = {"schema": "frequency_large_evaluation_protocol.v1", "feature_order": list(FEATURES), "units": list(UNITS),
        "panels": PANELS, "window": window, "quantile_method": "linear", "joint_hit": "all four finite and physically valid predictions AND all abs(error)<=tolerance; geometry pass reported separately",
        "fixed_denominator": "N_requested including failed and pending requests; available errors use explicit per-feature finite count",
        "confidence_intervals": "fixed finite design only; C single randomized LHS is not IID, no Wilson/bootstrap",
        "geometry_mapping": "source-verified grid export only; grid_only_not_layout_audited; no repair, clipping or GDS claim",
        "inverse_mode": "one-shot", "target_feasibility_C": "UNKNOWN", "independent_proxy": "NOT_AVAILABLE",
        "timing": "CPU batch wall time divided by batch requests; NOT individual latency; reused historical timing unavailable",
        "REAL_EMX_VALIDATION": "NOT_RUN"}
    result = {"schema": "frequency_large_eval_freeze.v1", "status": "FROZEN_BEFORE_NEW_SCORING", "created_utc": utc_now(),
        "config": config, "identity": identity, "protocol": protocol, "protocol_sha256": canonical_sha(protocol),
        "train_window": window, "target_space_bin_edges": {name: np.linspace(window["p01"][j], window["p99"][j], 9).tolist()
            for j, name in enumerate(FEATURES)}, "source_unique_geometries": source_count,
        "frequency_exposure": exposure, "training_seed": ins["train_config"]["seed"],
        "model_id": f"f{f}-{config['label_mode']}-{identity['inverse_checkpoint']['sha256'][:12]}",
        "model_steps": {"forward": fs["step"], "inverse": ins["step"]},
        "existing_AB_reuse": previous, "test_use": "final scoring only; no threshold, window, checkpoint or training selection",
        "artifacts": {name: pin(out/name) for name in ("REQUEST.json", "target_manifest.csv", "emx_preselected.json", "normalizer.json", "geometry_contract.json")},
        "implementation": {name: pin(Path(__file__).with_name(name)) for name in
            ("frequency_large_eval.py", "bb00.py", "frequency_tandem.py", "frequency_evaluation.py", "frequency_profile.py", "evaluation.py", "seven_evaluation.py", "physics.py", "io.py")},
        "prepare_model_prediction_calls": 0}
    save_json(out/"CONFIGURATION_FREEZE.json", result)
    return result


def summarize_records(records, requested=None):
    """Full-denominator statistics; nonfinite errors are absent, never imputed."""
    records = list(records)
    keys = ("panel", "evaluation_source", "geometry_stage", "frequency_ghz", "label_mode", "train_seed", "model_id")
    groups = {}
    seen = set()
    for row in records:
        key = tuple(row[k] for k in keys)
        unique = key+(row["target_id"], row["feature"])
        if unique in seen: raise ValueError("duplicate prediction key")
        seen.add(unique)
        if row["feature"] not in FEATURES: raise ValueError("unknown physical feature")
        j = FEATURES.index(row["feature"])
        if row["unit"] != UNITS[j]: raise ValueError("physical unit mismatch")
        groups.setdefault(key, []).append(row)
    result = []
    for key, rows in groups.items():
        by_target = {}
        for row in rows: by_target.setdefault(row["target_id"], {})[row["feature"]] = row
        if any(set(values) != set(FEATURES) for values in by_target.values()):
            raise ValueError("each completed request must retain all four feature rows")
        n_requested = (requested or {}).get(key[0], len(by_target))
        if n_requested < len(by_target): raise ValueError("more completed than requested")
        features = {}
        for j, feature in enumerate(FEATURES):
            available = [r for r in rows if r["feature"] == feature and _bool(r["prediction_finite"])
                         and _bool(r["source_label_valid"]) and _float(r["error_signed"]) is not None]
            errors = np.asarray([float(r["error_signed"]) for r in available])
            normalized = np.asarray([float(r["error_normalized"]) for r in available])
            n = len(errors)
            def mean(values): return float(np.mean(values)) if len(values) else None
            quant = np.quantile(np.abs(errors), [.5,.9,.95], method="linear").tolist() if n else [None]*3
            nq = np.quantile(normalized, [.5,.9,.95], method="linear").tolist() if n else [None]*3
            features[feature] = {"unit": UNITS[j], "N_requested":n_requested, "N_finite":n,
                "finite_fraction": n/n_requested if n_requested else None,
                "Bias_available":mean(errors), "MAE_available":mean(np.abs(errors)),
                "RMSE_available":float(np.sqrt(np.mean(errors**2))) if n else None,
                "MAE_full_population_status":"DEFINED" if n==n_requested else "NOT_DEFINED_INCOMPLETE_OR_NONFINITE",
                "P50_abs":quant[0],"P90_abs":quant[1],"P95_abs":quant[2],
                "MAE_normalized":mean(normalized), "P50_normalized":nq[0],"P90_normalized":nq[1],"P95_normalized":nq[2],
                "within_tolerance_count":sum(_bool(r["within_tolerance"]) for r in available),
                "within_tolerance_rate_fixed_denominator":sum(_bool(r["within_tolerance"]) for r in available)/n_requested if n_requested else None,
                "quantile_method":"linear"}
        finite = sum(all(_bool(r["prediction_finite"]) and _bool(r["source_label_valid"]) for r in values.values()) for values in by_target.values())
        hit = sum(all(_bool(r["within_tolerance"]) and _bool(r["prediction_finite"]) and _bool(r["source_label_valid"]) and
                      _bool(r["prediction_physical_valid"]) for r in values.values()) for values in by_target.values())
        geopass = sum(next(iter(values.values()))["geometry_status"] == "ANALYTIC_PASS" for values in by_target.values())
        reference_ids = {r["reference_geometry_id"] for r in rows if r["reference_geometry_id"]}
        candidate_ids = {r["candidate_id"] for r in rows if r["candidate_id"]}
        result.append({**dict(zip(keys,key)), "N_requested":n_requested,"N_completed":len(by_target),
            "N_finite":finite,"N_pending":n_requested-len(by_target), "N_failed":len(by_target)-finite,
            "N_target_misses_among_completed":len(by_target)-hit,
            "N_unique_targets":len(by_target), "N_unique_reference_geometries":len(reference_ids),
            "N_unique_candidates":len(candidate_ids), "ALL_FOUR_HIT_count":hit,
            "ALL_FOUR_HIT_rate":hit/n_requested if n_requested else None,
            "geometry_pass_count":geopass if key[2]!="reference" else None,
            "geometry_pass_rate":geopass/n_requested if n_requested and key[2]!="reference" else None,
            "geometry_metric":"analytical only; not layout/DRC or manufacturability",
            "features":features,"REAL_EMX_VALIDATION":"NOT_RUN", "N_emx_solved":0,
            "confidence_interval_method":"SINGLE_RANDOMIZED_LHS_NO_IID_CI" if key[0]=="C" else "FIXED_FRAME_DESCRIPTIVE_NO_SAMPLING_CI",
            "statistical_unit":"one target request at one frequency; seed variability NOT_MEASURED"})
    return result


def band_statistics(records):
    """Recompute micro quantiles from individual records; never average P95s."""
    rows = list(records); result = []
    for low, high in BANDS:
        grouped = {}
        for row in rows:
            if not low <= int(row["frequency_ghz"]) <= high or not _bool(row["prediction_finite"]) or not _bool(row["source_label_valid"]): continue
            key = tuple(row[k] for k in ("panel","evaluation_source","geometry_stage","label_mode","feature","train_seed","model_id"))
            grouped.setdefault(key, []).append(row)
        for key, group in grouped.items():
            values = np.asarray([float(r["error_abs"]) for r in group])
            normalized = np.asarray([float(r["error_normalized"]) for r in group])
            frequencies = sorted({int(r["frequency_ghz"]) for r in group})
            per_frequency = [np.mean([float(r["error_abs"]) for r in group if int(r["frequency_ghz"]) == f]) for f in frequencies]
            result.append({**dict(zip(("panel","evaluation_source","geometry_stage","label_mode","feature","train_seed","model_id"),key)),
                "band_ghz":[low,high],"evaluated_frequencies":frequencies,"N_available_records":len(group),
                "micro_MAE":float(values.mean()),"macro_MAE":float(np.mean(per_frequency)),
                "micro_P95_abs":float(np.quantile(values,.95,method="linear")),
                "micro_MAE_normalized":float(normalized.mean()),
                "micro_P95_normalized":float(np.quantile(normalized,.95,method="linear")),
                "quantile_method":"linear; recomputed from individual finite records",
                "uncertainty":"NOT_ESTIMATED; cross-frequency repeated geometry not independent"})
    return result


def _geometry_hash(geometry, fields):
    geometry=np.asarray(geometry,dtype=float)
    if not np.isfinite(geometry).all(): return None
    return canonical_sha({"fields":fields,"units":"um","values":np.round(geometry,12).tolist(),
                          "identity_scope":"parameter_vector_only_not_actual_GDS"})


def _geometry_status(geometry, contract):
    from .seven_evaluation import _geometry_flags
    geometry=np.asarray(geometry,dtype=float)
    finite=np.isfinite(geometry).all(1)
    bounded=finite & (geometry>=np.asarray(contract["lower"])).all(1) & (geometry<=np.asarray(contract["upper"])).all(1)
    analytic=np.zeros(len(geometry),dtype=bool)
    if finite.any(): analytic[finite]=_geometry_flags(geometry[finite],contract)
    return bounded,analytic


def _candidate(row, raw, grid, raw_flags, grid_flags, freeze):
    fields=freeze["geometry_fields"]
    rawhash, gridhash = _geometry_hash(raw,fields), _geometry_hash(grid,fields)
    finite = rawhash is not None and gridhash is not None
    candidate_id = f"f{freeze['identity']['frequency_ghz']}-g-{gridhash}" if gridhash else row["target_id"]+":NONFINITE"
    return {"candidate_id":candidate_id,"target_ids":[row["target_id"]],"panel":row["panel"],
        "generated_geometry_raw":[_float(v) for v in raw],"generated_geometry_grid":[_float(v) for v in grid],
        "actual_gds_geometry":None,"geometry_field_order":fields,"units":"um",
        "raw_geometry_hash":rawhash,"canonical_geometry_hash":gridhash,
        "canonical_mapping":"grid_only_not_layout_audited","within_bounds":{"raw":bool(raw_flags[0]),"grid":bool(grid_flags[0])},
        "analytic_check":{"raw":"PASS" if raw_flags[1] else "FAIL","grid":"PASS" if grid_flags[1] else "FAIL"},
        "layout_audit":"NOT_RUN","calibre_blocking":None,"emx_status":"NOT_RUN",
        "gds_sha":None,"s4p_sha":None,"duplicate_group":gridhash,"support_distance_if_available":None,
        "grid_perturbation_max_um":float(np.max(np.abs(grid-raw))) if finite else None,
        "grid_perturbation":[_float(v) for v in grid-raw],"emx_preselected":_bool(row["emx_preselected"])}


def prediction_rows(targets, prediction, freeze, stage, geometry_flags=None, candidates=None,
                    inference_ms=None, forward_ms=None, reused=False):
    """Unit-bearing long table; negative finite predictions remain in errors."""
    prediction=np.asarray(prediction,dtype=float)
    if prediction.shape != (len(targets),4): raise ValueError("paired prediction shape required")
    rows=[]; window=freeze["train_window"]
    source="EXISTING_EMX_FORWARD" if stage=="reference" else "SELF_PROXY"
    for i,target in enumerate(targets):
        physical=np.isfinite(prediction[i]) & (prediction[i] >= np.asarray([0,0,0,0]))
        physical[:3] &= prediction[i,:3]>0
        for j,feature in enumerate(FEATURES):
            truth=float(target[TARGET_COLUMNS[j]]); pred=_float(prediction[i,j]); error=None if pred is None else pred-truth
            abs_error=None if error is None else abs(error); scale=window["evaluation_scale"][j]; tau=window["tolerance_abs"][j]
            finite=pred is not None
            row={"study_id":freeze["config"]["study_id"],"dataset_id":freeze["identity"]["dataset"]["sha256"],
                "panel":target["panel"],"target_id":target["target_id"],"reference_geometry_id":target["reference_geometry_id"],
                "candidate_id":candidates[i]["candidate_id"] if candidates else "",
                "frequency_ghz":int(target["frequency_ghz"]),"label_mode":target["label_mode"],
                "train_seed":freeze["training_seed"],"model_id":freeze["model_id"],
                "model_sha256":freeze["identity"]["inverse_checkpoint" if stage!="reference" else "forward_checkpoint"]["sha256"],
                "normalizer_id":freeze["identity"]["normalizer_sha256"],
                "evaluator_id":"FROZEN_TANDEM_FORWARD","evaluator_sha256":freeze["identity"]["forward_checkpoint"]["sha256"],
                "evaluation_source":source,"geometry_stage":stage,"feature":feature,"unit":UNITS[j],
                "reference_or_target":truth,"predicted":pred,"error_signed":error,"error_abs":abs_error,
                "evaluation_scale":scale,"error_normalized":None if error is None else abs_error/scale,
                "tolerance_abs":tau,"error_over_tolerance":None if error is None else abs_error/tau,
                "within_tolerance":bool(finite and abs_error<=tau),"source_label_valid":True,
                "source_label_valid_basis":"FINITE_DECLARED_RANDOM_TARGET_NOT_EMX_LABEL" if target["panel"]=="C" else "ORIGINAL_ELIGIBLE_EMX_MASKS",
                "prediction_finite":finite,"prediction_physical_valid":bool(physical[j]),
                "model_support_status":target["support_tag"],
                "inference_status":"COMPLETED" if np.isfinite(prediction[i]).all() else "NONFINITE_OR_INFERENCE_FAILED",
                "geometry_status":("REFERENCE_NOT_REAUDITED" if stage=="reference" else
                    "ANALYTIC_PASS" if geometry_flags[i] else "ANALYTIC_FAIL"),
                "physical_label_status":"EXISTING_REFERENCE_EMX" if stage=="reference" else "GENERATED_GEOMETRY_EMX_NOT_RUN",
                "failure_code":"" if finite else "NONFINITE_OR_BATCH_INFERENCE_FAILED",
                "timing_inference_ms":inference_ms,"timing_forward_ms":forward_ms,
                "timing_basis":"HISTORICAL_NOT_AVAILABLE" if reused else "CPU_BATCH_WALL_AMORTIZED_NOT_SINGLE_REQUEST_LATENCY",
                "prediction_origin":"REUSED_HASH_PINNED_PREVIOUS_OUTPUT" if reused else "NEW_ONE_SHOT_INFERENCE",
                "independent_proxy_status":"NOT_AVAILABLE"}
            rows.append(row)
    return rows


def _load_reuse(inputs, targets):
    if inputs is None: return None
    for value in inputs["sources"].values(): verify_pin(value)
    forward=_read_csv(inputs["sources"]["forward_predictions.csv"]["path"])
    inverse=_read_csv(inputs["sources"]["inverse_predictions.csv"]["path"])
    lookup={"A":{},"B":{}}
    for panel, rows in (("A",forward),("B",inverse)):
        for row in rows:
            key=(row["target_id"], row.get("mode","reference"))
            if key in lookup[panel]: raise ValueError("duplicate reusable prediction ID/mode")
            lookup[panel][key]=row
    for panel in ("A","B"):
        selected=[r for r in targets if r["panel"]==panel]
        expected={(r["reference_geometry_id"],mode) for r in selected for mode in (("reference",) if panel=="A" else ("continuous","grid"))}
        if set(lookup[panel])!=expected: raise ValueError("reused A/B population is not exact full frozen test frame")
        for target in selected:
            for mode in (("reference",) if panel=="A" else ("continuous","grid")):
                old=lookup[panel][(target["reference_geometry_id"],mode)]
                prefix="truth__" if panel=="A" else "target__"
                if any(float(old[prefix+feature])!=float(target[TARGET_COLUMNS[j]]) for j,feature in enumerate(FEATURES)):
                    raise ValueError("reused target tuple changed")
    return lookup


def _batch(targets, freeze, bundle, forward, inverse, reuse):
    from .frequency_evaluation import _predict
    from .evaluation import _grid_from_contract, _grid_geometry
    panel=targets[0]["panel"]; device=freeze["config"]["device"]; n=len(targets)
    contract=freeze["geometry_contract"]; fields=contract["field_names"]
    target=np.asarray([[float(row[c]) for c in TARGET_COLUMNS] for row in targets])
    errors={}; timing={}; reused=reuse is not None and panel in ("A","B")
    if panel=="A":
        if reused:
            prediction=np.asarray([[_float(reuse["A"][(r["reference_geometry_id"],"reference")]["prediction__"+f]) for f in FEATURES] for r in targets],dtype=float)
            ms=None
        else:
            values=bundle.arrays["geometry"][[int(row["source_index"]) for row in targets]]
            started=time.perf_counter();prediction,errors["forward"]=_predict(forward,values,4,device,n)
            ms=(time.perf_counter()-started)*1000/n
        return prediction_rows(targets,prediction,freeze,"reference",forward_ms=ms,reused=reused),[],errors,{"forward_batch_ms":None if ms is None else ms*n}
    if reused:
        raw=np.asarray([[_float(reuse["B"][(r["reference_geometry_id"],"continuous")]["geometry__"+field]) for field in fields] for r in targets],dtype=float)
        grid=np.asarray([[_float(reuse["B"][(r["reference_geometry_id"],"grid")]["geometry__"+field]) for field in fields] for r in targets],dtype=float)
        inverse_ms=None
        finite=np.isfinite(raw).all(1)
        if finite.any() and not np.allclose(grid[finite],_grid_geometry(raw[finite],_grid_from_contract(contract)),rtol=0,atol=1e-12):
            raise ValueError("reused grid differs from frozen canonical grid-only mapping")
    else:
        started=time.perf_counter();raw,errors["inverse"]=_predict(inverse,target,bundle.dim,device,n)
        inverse_ms=(time.perf_counter()-started)*1000/n
        grid=np.full_like(raw,np.nan);finite=np.isfinite(raw).all(1)
        grid[finite]=_grid_geometry(raw[finite],_grid_from_contract(contract))
    rb,ra=_geometry_status(raw,contract);gb,ga=_geometry_status(grid,contract)
    candidates=[_candidate(row,raw[i],grid[i],(rb[i],ra[i]),(gb[i],ga[i]),freeze) for i,row in enumerate(targets)]
    rows=[];timing["inverse_batch_ms"]=None if inverse_ms is None else inverse_ms*n
    for stage,geometry,flags in (("raw",raw,ra),("grid",grid,ga)):
        if reused:
            mode="continuous" if stage=="raw" else "grid"
            prediction=np.asarray([[_float(reuse["B"][(r["reference_geometry_id"],mode)]["prediction__"+f]) for f in FEATURES] for r in targets],dtype=float)
            ms=None
        else:
            started=time.perf_counter();prediction,errors[stage+"_forward"]=_predict(forward,geometry,4,device,n)
            ms=(time.perf_counter()-started)*1000/n
        timing[stage+"_forward_batch_ms"]=None if ms is None else ms*n
        rows.extend(prediction_rows(targets,prediction,freeze,stage,flags,candidates,inverse_ms,ms,reused))
    return rows,candidates,errors,timing


def _frequency_status(freeze):
    profile=read_json(verify_pin(freeze["identity"]["profile_json"])) if "profile_json" in freeze["identity"] else None
    if profile:
        if (profile["source_identity"]["dataset"]["sha256"]!=freeze["identity"]["dataset"]["sha256"] or
                profile["source_identity"]["splits"]["sha256"]!=freeze["identity"]["splits"]["sha256"] or
                profile["source_identity"]["data_manifest_sha256"]!=freeze["identity"]["data_manifest"]["sha256"]):
            raise ValueError("frequency profile belongs to different dataset/split")
    by_frequency={r["frequency_ghz"]:r for r in profile["rows"]} if profile else {}
    result=[]
    for f in range(5,61):
        source=by_frequency.get(f,{})
        for mode in LABEL_MODES:
            label=source.get("label_modes",{}).get(mode,{})
            selected=f==freeze["identity"]["frequency_ghz"] and mode==freeze["identity"]["label_mode"]
            result.append({"frequency_ghz":f,"label_mode":mode,
                "model_status":"READY_FOR_EVALUATION_TRAINED_PARTIAL" if selected else "NOT_TRAINED_OR_NOT_BOUND_TO_THIS_EVALUATION",
                "data_status":label.get("status","NOT_AVAILABLE"),
                "n_accepted_geometries":source.get("total_unique_geometries",freeze["source_unique_geometries"]),
                "n_descriptor_valid":source.get("descriptor_valid_count"),"n_strict_valid":source.get("strict_valid_count"),
                **{"n_"+part+"_eligible":label.get("splits",{}).get(name,{}).get("eligible") for part,name in (("train","train"),("val","validation"),("test","test"))}})
    return result


def _verify_freeze(out):
    freeze=read_json(out/"CONFIGURATION_FREEZE.json")
    if freeze.get("schema")!="frequency_large_eval_freeze.v1" or canonical_sha(freeze["protocol"])!=freeze["protocol_sha256"]:
        raise ValueError("frozen protocol identity mismatch")
    for value in freeze["artifacts"].values(): verify_pin(value)
    for value in freeze["identity"].values():
        if isinstance(value,dict) and "path" in value: verify_pin(value)
    for name,value in freeze["implementation"].items():
        # A prospective prepare may precede final runner implementation. It is
        # provenance, not an approval chain. Core mappings/data/models stay pinned;
        # execution records the actual runner bytes and unchanged frozen protocol.
        if name!="frequency_large_eval.py": verify_pin(value)
    freeze["geometry_contract"]=read_json(out/"geometry_contract.json")
    freeze["geometry_fields"]=freeze["geometry_contract"]["field_names"]
    return freeze


def _existing_shard(root, target_ids, protocol_sha):
    receipts=sorted(root.glob("attempt_*/SHARD_RECEIPT.json"))
    if len(receipts)>1: raise ValueError("multiple completed attempts for one shard")
    if not receipts: return None
    receipt=read_json(receipts[0])
    if receipt["target_ids"]!=target_ids or receipt["protocol_sha256"]!=protocol_sha or receipt["status"]!="COMPLETE":
        raise ValueError("shard target/protocol identity mismatch")
    for value in receipt["artifacts"].values(): verify_pin(value)
    return receipts[0]


def run(out, *, reuse_evaluation_root=None, max_new_batches=None, panels=None):
    """Resume only uncommitted inference batches; no simulator or trainer calls."""
    if max_new_batches is not None and (type(max_new_batches) is not int or max_new_batches<1):
        raise ValueError("positive new-batch limit required")
    if panels is not None and (not panels or not set(panels).issubset({"A","B","C"})):
        raise ValueError("panels must be selected from A/B/C")
    out=Path(out).resolve(strict=True)
    with (out/"evaluation.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        freeze=_verify_freeze(out)
        if (out/"prediction_manifest.json").exists():
            manifest=read_json(out/"prediction_manifest.json")
            for value in manifest["artifacts"].values(): verify_pin(value)
            _write_sha_index(out)
            return read_json(out/"summary.json")
        targets=_read_csv(out/"target_manifest.csv")
        if len({r["target_id"] for r in targets})!=len(targets): raise ValueError("duplicate frozen target IDs")
        config=freeze["config"]; requested={p:sum(r["panel"]==p for r in targets) for p in ("A","B","C")}
        if (out/"EXECUTION_INPUTS.json").exists():
            inputs=read_json(out/"EXECUTION_INPUTS.json")
            if reuse_evaluation_root and str(Path(reuse_evaluation_root).resolve()) != (inputs.get("reuse_AB") or {}).get("root"):
                raise ValueError("cannot change reusable source after execution start")
        else:
            reuse=(_reuse_identity(reuse_evaluation_root,freeze["identity"]) if reuse_evaluation_root else freeze["existing_AB_reuse"])
            inputs={"schema":"frequency_large_execution_inputs.v1","created_utc":utc_now(),"reuse_AB":reuse,
                "freeze":pin(out/"CONFIGURATION_FREEZE.json"),"execution_source":pin(__file__),
                "prepare_source":freeze["implementation"]["frequency_large_eval.py"],
                "protocol_sha256":freeze["protocol_sha256"],"device":"cpu","precision":"float32 inference; float64 statistics",
                "batch_size":config["batch_size"],"single_sample_latency":"NOT_MEASURED"}
            save_json(out/"EXECUTION_INPUTS.json",inputs)
        if inputs["freeze"]["sha256"]!=sha256(out/"CONFIGURATION_FREEZE.json"): raise ValueError("execution freeze changed")
        reuse=_load_reuse(inputs.get("reuse_AB"),targets)
        receipts=[];pending=[]
        for panel in ("A","B","C"):
            selected=[row for row in targets if row["panel"]==panel]
            for start in range(0,len(selected),config["batch_size"]):
                batch=selected[start:start+config["batch_size"]]
                root=out/("panel_"+panel)/"shards"/f"batch_{start:06d}"
                receipt=_existing_shard(root,[r["target_id"] for r in batch],freeze["protocol_sha256"])
                if receipt: receipts.append(receipt)
                else: pending.append((root,batch))
        selected_pending=[item for item in pending if panels is None or item[1][0]["panel"] in panels]
        if selected_pending:
            import torch
            from .frequency_tandem import load_frequency_pair
            from .training import Bundle
            torch.set_num_threads(2)
            started=time.perf_counter()
            forward,inverse,_,_=load_frequency_pair(config["forward_checkpoint"],config["inverse_checkpoint"],
                frequency_ghz=config["frequency_ghz"],label_mode=config["label_mode"],device="cpu")
            bundle=Bundle(config["data_root"])
            model_load_seconds=time.perf_counter()-started
            for number,(root,batch) in enumerate(selected_pending):
                if max_new_batches is not None and number>=max_new_batches:
                    return {"status":"PARTIAL_SHARDS_SAVED","completed_shards":len(receipts),"remaining_shards":len(pending)-number,
                            "REAL_EMX_VALIDATION":"NOT_RUN"}
                root.mkdir(parents=True,exist_ok=True)
                attempts=[p for p in root.glob("attempt_*") if p.is_dir()]
                attempt=root/f"attempt_{len(attempts)+1:04d}"; attempt.mkdir(exist_ok=False)
                save_json(attempt/"INTENT.json",{"created_utc":utc_now(),"target_ids":[r["target_id"] for r in batch],
                    "protocol_sha256":freeze["protocol_sha256"],"execution_source":pin(__file__),
                    "previous_uncommitted_attempts":[str(p) for p in attempts],"model_load_seconds_this_process":model_load_seconds})
                rows,candidates,errors,timing=_batch(batch,freeze,bundle,forward,inverse,reuse)
                _csv(attempt/"prediction_records.csv",rows)
                _jsonl(attempt/"candidate_records.jsonl",candidates)
                save_json(attempt/"INFERENCE_FAILURES.json",errors)
                save_json(attempt/"SHARD_RECEIPT.json",{"schema":"frequency_large_shard.v1","status":"COMPLETE",
                    "created_utc":utc_now(),"target_ids":[r["target_id"] for r in batch],"panel":batch[0]["panel"],
                    "protocol_sha256":freeze["protocol_sha256"],"N_requested":len(batch),"timing":timing,
                    "artifacts":{name:pin(attempt/name) for name in ("INTENT.json","prediction_records.csv","candidate_records.jsonl","INFERENCE_FAILURES.json")}})
                receipts.append(attempt/"SHARD_RECEIPT.json")
        if len(selected_pending)<len(pending):
            return {"status":"PARTIAL_SHARDS_SAVED","completed_shards":len(receipts),
                    "remaining_shards":len(pending)-len(selected_pending),"REAL_EMX_VALIDATION":"NOT_RUN"}
        return _finalize(out,freeze,targets,receipts,requested,inputs)


def _finalize(out,freeze,targets,receipts,requested,inputs):
    rows=[];candidates=[]
    for path in sorted(receipts):
        receipt=read_json(path)
        rows.extend(_read_csv(receipt["artifacts"]["prediction_records.csv"]["path"]))
        with Path(receipt["artifacts"]["candidate_records.jsonl"]["path"]).open() as stream:
            candidates.extend(json.loads(line) for line in stream if line.strip())
    groups=summarize_records(rows,requested)
    mapping={}
    for candidate in candidates:
        for target_id in candidate["target_ids"]:
            if target_id in mapping: raise ValueError("multiple candidates for a one-shot target")
            mapping[target_id]=candidate
    selected=read_json(out/"emx_preselected.json")["selected"]
    pending=[]
    for choice in selected:
        candidate=mapping[choice["target_id"]]
        analytic_failed=candidate["analytic_check"]["grid"]!="PASS"
        pending.append({**choice,"panel":"D","evaluation_source":"GENERATED_FRESH_EMX", "candidate_id":candidate["candidate_id"],
            "generated_geometry_raw":candidate["generated_geometry_raw"],"generated_geometry_grid":candidate["generated_geometry_grid"],
            "canonical_geometry_hash":candidate["canonical_geometry_hash"],"geometry_field_order":candidate["geometry_field_order"],
            "analytic_check":candidate["analytic_check"],"actual_gds_geometry":None,"gds_sha":None,"s4p_sha":None,
            "layout_audit":"NOT_RUN","calibre_status":"NOT_RUN","emx_status":"NOT_RUN",
            "status":"FAIL_ANALYTIC_PRECHECK" if analytic_failed else "PENDING_RESOURCE_ADMISSION",
            "failure_code":"ANALYTIC_GEOMETRY_FAIL" if analytic_failed else None,"selection_preserved_no_replacement":True})
    for panel in ("B","C"):
        n=sum(r["selection_group"]==panel for r in selected)
        failed=sum(r["selection_group"]==panel and r["status"]=="FAIL_ANALYTIC_PRECHECK" for r in pending)
        groups.append({"panel":"D","selection_group":panel,"evaluation_source":"GENERATED_FRESH_EMX","geometry_stage":"actual_gds",
            "frequency_ghz":freeze["identity"]["frequency_ghz"],"label_mode":freeze["identity"]["label_mode"],
            "N_requested":n,"N_completed":failed,"N_finite":0,"N_pending":n-failed,"N_failed":failed,"N_emx_selected":n,"N_emx_solved":0,
            "ALL_FOUR_HIT_count":0,"ALL_FOUR_HIT_rate":None,"confirmed_success_lower_bound":0 if n else None,
            "geometry_pass_count":None,"geometry_pass_rate":None,"features":{},"REAL_EMX_VALIDATION":"NOT_RUN",
            "confidence_interval_method":"NOT_AVAILABLE_PENDING_PHYSICAL_RESULTS",
            "failure_not_inferred_from_pending":True})
    # Complete artifacts are hard-link published. An interrupted finalization
    # reuses byte-identical outputs; differing evidence is never overwritten.
    names=("prediction_records.csv","candidate_records.jsonl","pending_candidate_manifest.jsonl","frequency_status.csv","summary.json")
    _csv(out/"prediction_records.csv",rows,allow_identical=True)
    _jsonl(out/"candidate_records.jsonl",candidates,allow_identical=True)
    _jsonl(out/"pending_candidate_manifest.jsonl",pending,allow_identical=True)
    status=_frequency_status(freeze);_csv(out/"frequency_status.csv",status,allow_identical=True)
    created=read_json(out/"summary.json")["created_utc"] if (out/"summary.json").exists() else utc_now()
    summary={"schema":"frequency_large_eval_summary.v1","status":"ABC_COMPLETE_D_PENDING","created_utc":created,
        "study_id":freeze["config"]["study_id"],"dataset_scope":freeze["config"]["dataset_scope"],
        "identity":freeze["identity"],"freeze":pin(out/"CONFIGURATION_FREEZE.json"),"groups":groups,
        "band_statistics":band_statistics(rows),"frequency_status":status,"N_by_panel":requested,
        "N_D_selected_by_source":{p:sum(r["selection_group"]==p for r in selected) for p in ("B","C")},
        "new_geometry_REAL_EMX_VALIDATION":"NOT_RUN","independent_proxy":"NOT_AVAILABLE",
        "model_training_state":"TRAINED_PARTIAL_CONVERGENCE_NOT_ESTABLISHED",
        "candidate_records_grain":"one inverse request; equal grid parameter hashes share candidate_id, not evidence of actual GDS/EMX identity",
        "interpretation":"A=existing reference EMX labels; B/C=self-proxy; D has no physical results; no champion or feasibility proof",
        "execution_inputs":pin(out/"EXECUTION_INPUTS.json"),"shards":[pin(path) for path in sorted(receipts)],
        "artifacts":{name:pin(out/name) for name in names if name!="summary.json"}}
    _resumable_json(out/"summary.json",summary)
    manifest={"schema":"frequency_large_prediction_manifest.v1","created_utc":utc_now(),"status":"ABC_COMPLETE_D_NOT_RUN",
        "frozen_inputs":pin(out/"CONFIGURATION_FREEZE.json"),"execution_inputs":pin(out/"EXECUTION_INPUTS.json"),
        "target_manifest":pin(out/"target_manifest.csv"),"protocol_sha256":freeze["protocol_sha256"],
        "artifacts":{name:pin(out/name) for name in names},"shards":[pin(path) for path in sorted(receipts)]}
    _resumable_json(out/"prediction_manifest.json",manifest)
    _write_sha_index(out)
    return summary


def _write_sha_index(out):
    lines=[]
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name not in ("SHA256SUMS.txt","evaluation.lock") and not path.name.startswith(".uncommitted-"):
            lines.append(sha256(path)+"  "+str(path.relative_to(out))+"\n")
    _publish_bytes(out/"SHA256SUMS.txt",lambda stream:stream.writelines(lines),allow_identical=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest="action",required=True)
    p=subs.add_parser("prepare");p.add_argument("--config",required=True);p.add_argument("--out",required=True)
    r=subs.add_parser("run");r.add_argument("--out",required=True);r.add_argument("--reuse-evaluation-root")
    r.add_argument("--max-new-batches",type=int)
    r.add_argument("--panels",nargs="+",choices=("A","B","C"))
    args=parser.parse_args(argv)
    result=prepare(args.config,args.out) if args.action=="prepare" else run(args.out,reuse_evaluation_root=args.reuse_evaluation_root,max_new_batches=args.max_new_batches,panels=args.panels)
    print(json.dumps({"status":result["status"],"out":str(Path(args.out).resolve()),"N_by_panel":result.get("N_by_panel")},sort_keys=True))


if __name__=="__main__":main()
