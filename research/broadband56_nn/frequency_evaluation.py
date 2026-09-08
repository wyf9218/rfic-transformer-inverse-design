"""Single-frequency held-out EM regression and inverse SELF_PROXY reporting.

No training, simulator or dataset generation. Test evaluation requires an exact
configuration freeze; all output directories are no-clobber. The four targets
are symmetric equalities, including Q_scalar=min(Qp,Qs).
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np

from .io import canonical_sha, read_json, save_json, sha256, utc_now

FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
UNITS = ("nH", "nH", "dimensionless", "dimensionless")
SPANS = (2.5, 2.5, 20.0, 0.8)
TOLERANCE = 0.05
LABEL_MODES = ("STRICT_LUMPED", "POINTWISE_DESCRIPTOR_EXPERIMENTAL")


def pin(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def verify_pin(value):
    actual = pin(value["path"])
    if any(actual[key] != value[key] for key in ("path", "sha256", "bytes") if key in value):
        raise ValueError("source identity changed: " + value["path"])
    return Path(actual["path"])


def protocol_identity():
    return {"schema": "frequency_evaluation_protocol.v1", "features": list(FEATURES),
        "units": list(UNITS), "declared_spans": list(SPANS), "tolerance_fraction": TOLERANCE,
        "tolerance_physical_units": (np.asarray(SPANS)*TOLERANCE).tolist(),
        "tolerance_definition": "absolute error <= 0.05 x fixed DECLARED_SPAN; NOT target-relative percentage or train-standard-deviation error",
        "target_semantics": "symmetric equality for all four features; Q_scalar=min(Qp,Qs)",
        "forward_evidence": "HELDOUT_EM_LABELS", "inverse_evidence": "SELF_PROXY",
        "joint_denominator": "every eligible original held-out target, including failed generation/inference/analytical geometry",
        "p95": "higher empirical quantile of per-target max(abs(error)/DECLARED_SPAN); failures rank as +infinity",
        "r2": "1-SSE/SST; null for fewer than two evaluable labels, missing predictions or near-constant truth",
        "source_sha256": {name: sha256(Path(__file__).with_name(name)) for name in
            ("frequency_evaluation.py", "bb00.py", "physics.py", "evaluation.py", "seven_evaluation.py")},
        "real_emx_validation": "NOT_RUN"}


def _frequency(frequency_ghz, label_mode):
    if type(frequency_ghz) is not int or not 5 <= frequency_ghz <= 60 or label_mode not in LABEL_MODES:
        raise ValueError("exact integer 5..60 GHz and explicit supported label mode required")


def _identity(data_root, forward_checkpoint, inverse_checkpoint, frequency_ghz, label_mode):
    _frequency(frequency_ghz, label_mode)
    data_root = Path(data_root)
    return {"data_manifest": pin(data_root/"data_manifest.json"),
        "dataset": pin(data_root/"dataset.npz"), "splits": pin(data_root/"splits.json"),
        "forward_checkpoint": pin(forward_checkpoint), "inverse_checkpoint": pin(inverse_checkpoint),
        "frequency_ghz": frequency_ghz, "label_mode": label_mode,
        "protocol_sha256": canonical_sha(protocol_identity())}


def freeze_frequency_evaluation(data_root, forward_checkpoint, inverse_checkpoint, out_path, *,
                                frequency_ghz=15, label_mode="STRICT_LUMPED", validation_summary=None):
    """Metadata-only freeze, without model loading or test-label evaluation."""
    identity = _identity(data_root, forward_checkpoint, inverse_checkpoint, frequency_ghz, label_mode)
    validation = None
    if validation_summary is not None:
        value = read_json(validation_summary)
        if value.get("split") != "validation" or value.get("identity") != identity:
            raise ValueError("validation evidence belongs to different frozen models/data/protocol")
        validation = pin(validation_summary)
    result = {"schema": "frequency_evaluation_freeze.v1", "status": "FROZEN", "created_utc": utc_now(),
        "identity": identity, "validation_summary": validation,
        "test_use": "final descriptive report only; never checkpoint/seed/loss/support selection"}
    save_json(out_path, result)
    return result


def _verify_test_freeze(path, identity):
    if path is None:
        raise ValueError("sealed test requires an exact pre-evaluation configuration freeze")
    freeze = read_json(path)
    if freeze.get("schema") != "frequency_evaluation_freeze.v1" or freeze.get("status") != "FROZEN" or freeze.get("identity") != identity:
        raise ValueError("test configuration freeze differs")
    if freeze.get("validation_summary"):
        verify_pin(freeze["validation_summary"])
    return pin(path)


def _arrays(targets, predicted):
    truth, prediction = np.asarray(targets, dtype=float), np.asarray(predicted, dtype=float)
    if truth.ndim != 2 or truth.shape[1] != 4 or not len(truth) or prediction.shape != truth.shape:
        raise ValueError("nonempty paired [N,4] responses required")
    if not np.isfinite(truth).all():
        raise ValueError("target frame must contain finite eligible labels before evaluation")
    return truth, prediction


def regression_metrics(targets, predicted, valid=None):
    """Physical-unit errors, with full and explicitly conditional denominators."""
    truth, prediction = _arrays(targets, predicted)
    mask = np.isfinite(prediction)
    if valid is not None:
        valid = np.asarray(valid)
        if valid.dtype != np.bool_ or valid.shape != truth.shape:
            raise ValueError("validity must be an exact [N,4] boolean mask")
        mask &= valid
    result = {}
    for j, name in enumerate(FEATURES):
        ok = mask[:, j]; n = int(ok.sum()); error = prediction[ok, j]-truth[ok, j]
        conditional = {"mae": float(np.abs(error).mean()) if n else None,
            "rmse": float(np.sqrt(np.mean(error**2))) if n else None,
            "absolute_error_p95": float(np.quantile(np.abs(error), .95, method="higher")) if n else None}
        y = truth[:, j]; sst = float(np.sum((y-y.mean())**2))
        constant = sst <= 1e-12 * max(float(np.sum(y*y)), 1.0)
        full = n == len(truth)
        r2 = None if not full or len(y) < 2 or constant else float(1-np.sum(error**2)/sst)
        result[name] = {"unit": UNITS[j], "fixed_denominator": len(truth), "evaluable_denominator": n,
            "invalid_count": len(truth)-n, "mae": conditional["mae"] if full else None,
            "rmse": conditional["rmse"] if full else None, "r2": r2,
            "r2_status": "DEFINED" if r2 is not None else "NOT_APPLICABLE_NEAR_CONSTANT_OR_INCOMPLETE",
            "conditional_evaluable": conditional}
    return result


def inverse_metrics(targets, predicted, feasible, valid=None):
    truth, prediction = _arrays(targets, predicted)
    feasible = np.asarray(feasible)
    if feasible.shape != (len(truth),) or feasible.dtype != np.bool_:
        raise ValueError("analytical feasibility must be an exact [N] boolean mask")
    mask = np.isfinite(prediction)
    if valid is not None:
        valid = np.asarray(valid)
        if valid.shape != mask.shape or valid.dtype != np.bool_:
            raise ValueError("prediction validity must be [N,4] boolean")
        mask &= valid
    mask &= feasible[:, None]
    errors = (prediction-truth)/np.asarray(SPANS)
    row_valid = mask.all(1)
    joint = row_valid & (np.abs(errors) <= TOLERANCE).all(1)
    severity = np.full(len(truth), np.inf)
    severity[row_valid] = np.abs(errors[row_valid]).max(1)
    # A failed target never becomes a silently dropped tail observation.
    p95 = np.sort(severity)[int(math.ceil(.95*(len(truth)-1)))]
    return {"evidence": "SELF_PROXY", "joint_hit_count": int(joint.sum()),
        "joint_hit_denominator": len(truth), "joint_hit_rate": float(joint.mean()),
        "target_failure_count": int((~joint).sum()), "evaluable_targets": int(row_valid.sum()),
        "analytical_pass_count": int(feasible.sum()), "analytical_pass_rate": float(feasible.mean()),
        "max_declared_span_error_p95": float(p95) if np.isfinite(p95) else None,
        "p95_status": "DEFINED" if np.isfinite(p95) else "UNDEFINED_DUE_TO_FAILURES",
        "conditional_evaluable_max_error_p95": float(np.quantile(severity[row_valid], .95, method="higher")) if row_valid.any() else None,
        "features": regression_metrics(truth, prediction, mask),
        "real_emx_validation": "NOT_RUN", "manufacturability": "NOT_PROVEN"}


def _predict(model, values, out_dim, device, micro_batch):
    import torch
    values = np.asarray(values)
    predicted = np.full((len(values), out_dim), np.nan)
    failures = []
    for start in range(0, len(values), micro_batch):
        ix = np.arange(start, min(start+micro_batch, len(values)))
        ix = ix[np.isfinite(values[ix]).all(1)]
        if not len(ix):
            continue
        try:
            with torch.no_grad():
                output = model(torch.as_tensor(values[ix], dtype=torch.float32, device=device)).cpu().numpy()
            if output.shape != (len(ix), out_dim):
                raise ValueError("model returned wrong response shape")
            predicted[ix] = output
        except (RuntimeError, ValueError, FloatingPointError) as error:
            failures.append({"local_indices": ix.tolist(), "error": type(error).__name__+": "+str(error)})
    return predicted, failures


def _csv(path, rows):
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def _number(value):
    return float(value) if np.isfinite(value) else None


def evaluate_frequency(data_root, forward_checkpoint, inverse_checkpoint, out_dir, *, frequency_ghz=15,
                       label_mode="STRICT_LUMPED", split="validation", device="cpu", micro_batch=32,
                       configuration_freeze=None):
    if split not in ("validation", "test") or type(micro_batch) is not int or micro_batch < 1:
        raise ValueError("explicit held-out split and positive micro batch required")
    identity = _identity(data_root, forward_checkpoint, inverse_checkpoint, frequency_ghz, label_mode)
    freeze_pin = _verify_test_freeze(configuration_freeze, identity) if split == "test" else None
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=False)
    save_json(out/"EVALUATION_INTENT.json", {"identity": identity, "split": split, "created_utc": utc_now(),
        "configuration_freeze": freeze_pin, "test_access_requested": split == "test"})
    try:
        from .training import Bundle
        from .bb00 import load_bb00, prepare_bb00
        from .seven_evaluation import _geometry_flags
        from .evaluation import _grid_from_contract, _grid_geometry
        bundle = Bundle(data_root)
        forward, fs = load_bb00(forward_checkpoint, device, identity["forward_checkpoint"]["sha256"])
        inverse, ins = load_bb00(inverse_checkpoint, device, identity["inverse_checkpoint"]["sha256"])
        contract = fs["contract"]
        norm, train, val, at, exposure = prepare_bb00(bundle, contract, SPANS, True,
            frequency_ghz=frequency_ghz, label_mode=label_mode)
        for state, role in ((fs, "forward"), (ins, "inverse")):
            if (state["role"] != role or state["data_sha"] != bundle.data_sha or
                state["normalizer_sha"] != canonical_sha(norm) or state["contract_sha"] != canonical_sha(contract) or
                state.get("research_comparison_eligible") is False or state.get("resume_probe") is True):
                raise ValueError("checkpoint role/data/frequency/normalizer/contract or comparison eligibility differs")
        if ins["forward_checkpoint_sha256"] != identity["forward_checkpoint"]["sha256"] or ins["forward_model_sha"] != fs["model_sha"]:
            raise ValueError("inverse is not bound to this exact frozen forward")
        forward.eval().requires_grad_(False); inverse.eval().requires_grad_(False)
        population = np.flatnonzero(bundle.arrays["split"] == (1 if split == "validation" else 2))
        if label_mode == "STRICT_LUMPED":
            eligible = bundle.arrays["y_valid"][:, at].all(1)
            if "strict_lumped_valid" in bundle.arrays:
                eligible &= bundle.arrays["strict_lumped_valid"][:, at]
        else:
            eligible = bundle.arrays["broadband_descriptor_valid"][:, at].copy()
        eligible &= np.isfinite(bundle.arrays["y"][:, at]).all(1)
        indices = population[eligible[population]]
        if not len(indices):
            raise ValueError("NO_ELIGIBLE_HELDOUT_LABELS")
        ids = bundle.arrays["geometry_ids"][indices].astype(str).tolist()
        if len(set(ids)) != len(ids):
            raise ValueError("held-out geometry IDs are not unique")
        targets = np.asarray(bundle.arrays["y"][indices, at], dtype=float)
        observed = np.asarray(bundle.arrays["geometry"][indices], dtype=float)
        fp, ferr = _predict(forward, observed, 4, device, micro_batch)
        geometry, ierr = _predict(inverse, targets, bundle.dim, device, micro_batch)
        grid_um = _grid_from_contract(contract)
        grid = np.full_like(geometry, np.nan); finite = np.isfinite(geometry).all(1)
        grid[finite] = _grid_geometry(geometry[finite], grid_um)
        modes = {}; mode_arrays = {}; errors = {"forward": ferr, "inverse": ierr}
        for name, generated in (("continuous", geometry), ("grid", grid)):
            feasible = _geometry_flags(generated, contract)
            response, failures = _predict(forward, generated, 4, device, micro_batch)
            valid = np.isfinite(response) & np.column_stack((response[:, 0]>0, response[:, 1]>0, response[:, 2]>0, response[:, 3]>=0))
            modes[name] = inverse_metrics(targets, response, feasible, valid)
            mode_arrays[name] = (generated, response, feasible, valid)
            errors[name+"_self_proxy"] = failures
        forward_rows=[]; inverse_rows=[]
        for i, target_id in enumerate(ids):
            common={"target_id": target_id, "source_index": int(indices[i]), "frequency_ghz": frequency_ghz,
                "label_mode": label_mode, "split": split, "fixed_target_denominator": len(ids)}
            row={**common, "evidence": "HELDOUT_EM_LABELS", "status": "PASS" if np.isfinite(fp[i]).all() else "PREDICTION_FAILED"}
            for j, feature in enumerate(FEATURES):
                row.update({"truth__"+feature:float(targets[i,j]), "prediction__"+feature:_number(fp[i,j]),
                    "error__"+feature:_number(fp[i,j]-targets[i,j])})
            forward_rows.append(row)
            for name,(g,p,feasible,valid) in mode_arrays.items():
                error=(p[i]-targets[i])/np.asarray(SPANS)
                hit=bool(feasible[i] and valid[i].all() and (np.abs(error)<=TOLERANCE).all())
                row={**common,"mode":name,"evidence":"SELF_PROXY","analytical_pass":bool(feasible[i]),
                    "joint_hit":hit,"status":"HIT" if hit else "FAILED_TARGET", "real_emx_validation":"NOT_RUN"}
                for j,feature in enumerate(FEATURES):
                    row.update({"target__"+feature:float(targets[i,j]),"prediction__"+feature:_number(p[i,j]),
                        "error__"+feature:_number(p[i,j]-targets[i,j]),"declared_span_error__"+feature:_number(error[j]),
                        "valid__"+feature:bool(valid[i,j])})
                row.update({"geometry__"+field:_number(g[i,j]) for j,field in enumerate(contract["field_names"])})
                inverse_rows.append(row)
        _csv(out/"forward_predictions.csv",forward_rows); _csv(out/"inverse_predictions.csv",inverse_rows)
        save_json(out/"EVALUATION_PROTOCOL.json",protocol_identity())
        save_json(out/"INFERENCE_FAILURES.json",errors)
        summary={"schema":"frequency_evaluation_summary.v1","status":"COMPLETE_DESCRIPTIVE_EVALUATION",
            "created_utc":utc_now(),"identity":identity,"split":split,"frequency_ghz":frequency_ghz,"label_mode":label_mode,
            "source_snapshot_geometries":len(bundle.arrays["geometry"]),"data_evidence":bundle.manifest.get("evidence","SOURCE_MANIFEST_BOUND"),"exposure":exposure,
            "source_split_geometries":len(population),"excluded_label_geometries":len(population)-len(ids),
            "target_count":len(ids),"target_id_order_sha256":canonical_sha(ids),
            "geometry_fields":contract["field_names"],"geometry_dimension":bundle.dim,"geometry_grid_um":grid_um,
            "configuration_freeze":freeze_pin,"forward":{"evidence":"HELDOUT_EM_LABELS","features":regression_metrics(targets,fp)},
            "inverse":modes,"grid_effect":{"joint_hit_rate_delta_grid_minus_continuous":modes["grid"]["joint_hit_rate"]-modes["continuous"]["joint_hit_rate"],
                "analytical_pass_count_delta":modes["grid"]["analytical_pass_count"]-modes["continuous"]["analytical_pass_count"],
                "max_coordinate_change_um":float(np.abs(grid[finite]-geometry[finite]).max()) if finite.any() else None},
            "model_metadata":{role:{"step":state["step"],"seed":state["train_config"]["seed"],"architecture":state["architecture"],
                "parameter_count":sum(p.numel() for p in model.parameters()),"model_sha":state["model_sha"]}
                for role,model,state in (("forward",forward,fs),("inverse",inverse,ins))},
            "artifacts":{name:pin(out/name) for name in ("forward_predictions.csv","inverse_predictions.csv","EVALUATION_PROTOCOL.json","INFERENCE_FAILURES.json")},
            "protocol":protocol_identity(),"real_emx_validation":"NOT_RUN","physical_accuracy":"NOT_ESTABLISHED",
            "comparison_scope":"single-frequency descriptive development evaluation; not causal model comparison or demonstrated convergence"}
        save_json(out/"EVALUATION_SUMMARY.json",summary)
        with (out/"SHA256SUMS.txt").open("x") as handle:
            for file in sorted(out.iterdir()):
                if file.is_file() and file.name!="SHA256SUMS.txt": handle.write(sha256(file)+"  "+file.name+"\n")
        return summary
    except Exception as error:
        save_json(out/"EVALUATION_FAILED.json",{"status":"FAIL","error":type(error).__name__+": "+str(error),
            "identity":identity,"split":split,"created_utc":utc_now(),"failure_preserved":True})
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("freeze","evaluate"))
    for name in ("data","forward","inverse","out"): parser.add_argument("--"+name,required=True)
    parser.add_argument("--frequency-ghz",type=int,default=15)
    parser.add_argument("--label-mode",choices=LABEL_MODES,default="STRICT_LUMPED")
    parser.add_argument("--split",choices=("validation","test"),default="validation")
    parser.add_argument("--device",default="cpu"); parser.add_argument("--micro-batch",type=int,default=32)
    parser.add_argument("--configuration-freeze"); parser.add_argument("--validation-summary")
    args=parser.parse_args(argv)
    kw={"frequency_ghz":args.frequency_ghz,"label_mode":args.label_mode}
    if args.action=="freeze":
        freeze_frequency_evaluation(args.data,args.forward,args.inverse,args.out,validation_summary=args.validation_summary,**kw)
    else:
        evaluate_frequency(args.data,args.forward,args.inverse,args.out,split=args.split,device=args.device,
            micro_batch=args.micro_batch,configuration_freeze=args.configuration_freeze,**kw)


if __name__=="__main__": main()
