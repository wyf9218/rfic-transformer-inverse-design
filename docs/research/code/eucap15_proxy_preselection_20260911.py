"""Frozen128 saved-proxy tolerance and signed K_abs decomposition; stdlib only.

No inference/native calls, Q reranking, physics validation, or source mutation.
Unknown values stay null. The signed terms are arithmetic, not causal shares.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import traceback

SOURCE_SHA = "d27ce63a60902f00f1f1f53eef2972168d2ef3d349485f6c81c896120f47c75d"
FREEZE_SHA = "b279a1c2785b85777c7cc1ca311e500ac7fa54d017cec19f10afb820ecef3c68"
FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
TAU = (.125, .125, 1., .04000000000000001)
SPANS = (2.5, 2.5, 20., .8)
STATES = ("ANALYTIC_FAIL", "GDS_FAIL", "EMX_INVALID", "STRICT_VALID", "PENDING")


def require(test, message):
    if not test:
        raise ValueError(message)


def pin(path):
    p = Path(path)
    require(p.is_absolute() and p.is_file() and not p.is_symlink(), "Exact regular input required")
    raw = p.read_bytes()
    return {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}, raw


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def vector(text, name):
    value = decode(text) if text.strip() else None
    if value is None:
        return [None] * 4, [name + ":MISSING_VECTOR"]
    require(isinstance(value, list) and len(value) == 4, name + ": expected four-vector")
    clean, missing = [], []
    for feature, item in zip(FEATURES, value):
        if item is None or (type(item) in (float, int) and not math.isfinite(item)):
            clean.append(None)
            missing.append(name + ":" + feature + ":MISSING_OR_NONFINITE")
        else:
            require(type(item) in (float, int), name + ": nonnumeric/bool value")
            clean.append(float(item))
    return clean, missing


def classify_proxy(target, proxy):
    residual = [None if t is None or p is None else p - t for t, p in zip(target, proxy)]
    within = [None if e is None else abs(e) <= tau for e, tau in zip(residual, TAU)]
    # Any missing component makes the full-vector conclusion UNKNOWN. Known
    # feature failures are separately retained, never counted as a full pass.
    joint = None if None in within else all(within)
    return residual, within, joint


def signed_k(target, proxy, actual):
    t, p, e = target[3], proxy[3], actual[3]
    a = p - t if p is not None and t is not None else None
    b = e - p if e is not None and p is not None else None
    c = e - t if e is not None and t is not None else None
    total = math.fsum((a, b)) if a is not None and b is not None else None
    discrepancy = total - c if total is not None and c is not None else None
    bound = 4 * math.ulp(max(abs(t), abs(p), abs(e))) if None not in (t, p, e) else None
    return dict(K_target=t, K_grid_proxy=p, K_emx=e,
        proxy_minus_target=a, emx_minus_proxy=b, emx_minus_target=c,
        signed_sum=total, closure_residual=discrepancy,
        numerical_bound_4ulp_max_input=bound,
        signed_identity_numeric_pass=None if discrepancy is None else abs(discrepancy) <= bound)


def group_summary(rows):
    n = len(rows)
    known = sum(r["proxy_joint_within_tolerance"] is not None for r in rows)
    passed = sum(r["proxy_joint_within_tolerance"] is True for r in rows)
    failed = sum(r["proxy_joint_within_tolerance"] is False for r in rows)
    return dict(N_original_group=n, N_proxy_joint_known=known, N_proxy_joint_within=passed,
        N_proxy_joint_exceeds=failed, N_proxy_joint_unknown=n-known,
        joint_within_fraction_original_denominator=passed/n if n else None,
        joint_exceeds_fraction_original_denominator=failed/n if n else None,
        features={feature: dict(
            N_known=sum(r["proxy_within_by_feature"][j] is not None for r in rows),
            N_within=sum(r["proxy_within_by_feature"][j] is True for r in rows),
            N_exceeds=sum(r["proxy_within_by_feature"][j] is False for r in rows),
            N_unknown=sum(r["proxy_within_by_feature"][j] is None for r in rows))
            for j, feature in enumerate(FEATURES)},
        known_exceedance_combinations=dict(sorted(Counter(
            "+".join(r["proxy_exceeded_features"]) or "NONE" for r in rows
            if r["proxy_joint_within_tolerance"] is not None).items())))


def arithmetic_check(original, rows, decomposition, summary):
    """Independent exact-rational subtraction oracle; no call to classifiers."""
    oracle = []
    finite_feature_checks = k_subtraction_checks = closure_checks = 0
    for raw, row, parts in zip(original, rows, decomposition):
        flags = []
        for j in range(4):
            t, p = raw["target"][j], raw["proxy"][j]
            expected = None if t is None or p is None else float(Fraction(p) - Fraction(t))
            require(row["proxy_minus_target"][j] == expected, "Proxy residual arithmetic differs")
            flag = None if expected is None else abs(expected) <= TAU[j]
            require(row["proxy_within_by_feature"][j] is flag, "Proxy tolerance decision differs")
            flags.append(flag)
            finite_feature_checks += expected is not None
        joint = None if any(f is None for f in flags) else not any(f is False for f in flags)
        require(row["proxy_joint_within_tolerance"] is joint, "Joint decision differs")
        oracle.append((raw["state"], flags, joint))
        t, p, e = raw["target"][3], raw["proxy"][3], raw["actual"][3]
        for key, left, right in (("proxy_minus_target", p, t), ("emx_minus_proxy", e, p), ("emx_minus_target", e, t)):
            expected = None if left is None or right is None else float(Fraction(left) - Fraction(right))
            require(parts[key] == expected, "Signed K subtraction differs: " + key)
            k_subtraction_checks += expected is not None
        if None not in (t, p, e):
            require((Fraction(p)-Fraction(t)) + (Fraction(e)-Fraction(p)) == Fraction(e)-Fraction(t), "Exact signed identity failed")
            require(parts["signed_identity_numeric_pass"] is True, "Binary64 signed identity exceeds rounding bound")
            closure_checks += 1
        else:
            require(parts["signed_identity_numeric_pass"] is None, "Missing actual became successful closure")
    for state in ("ALL128", *STATES):
        selected = oracle if state == "ALL128" else [r for r in oracle if r[0] == state]
        actual = summary["groups"][state]
        require(actual["N_original_group"] == len(selected), "Group denominator differs")
        for name, truth in (("N_proxy_joint_within", True), ("N_proxy_joint_exceeds", False), ("N_proxy_joint_unknown", None)):
            require(actual[name] == sum(r[2] is truth for r in selected), "Group joint count differs")
        for j, feature in enumerate(FEATURES):
            for name, truth in (("N_within", True), ("N_exceeds", False), ("N_unknown", None)):
                require(actual["features"][feature][name] == sum(r[1][j] is truth for r in selected), "Feature subtotal differs")
    # Small new boundaries only; no historical tests or real-data rerun.
    zero = [0.] * 4
    require(classify_proxy(zero, list(TAU))[2] is True, "Inclusive exact boundary")
    beyond = list(TAU); beyond[3] = math.nextafter(TAU[3], math.inf)
    require(classify_proxy(zero, beyond)[2] is False, "One value beyond fixed boundary")
    require(classify_proxy(zero, [0., 0., None, 0.])[2] is None, "Missing stays UNKNOWN")
    require(vector('[0,0,NaN,0]', 'synthetic')[0][2] is None, "Nonfinite stays UNKNOWN")
    require(signed_k([0,0,0,.5], [0,0,0,.6], [None]*4)["emx_minus_target"] is None, "No missing EMX imputation")
    return dict(status="PASS_NEW_ARITHMETIC_ONLY", original_rows_checked=len(rows),
        finite_proxy_feature_subtractions=finite_feature_checks,
        finite_signed_K_subtractions=k_subtraction_checks, signed_K_closures=closure_checks,
        summary_groups_checked=6, focused_synthetic_boundaries=5,
        method="Fraction.from_float exact-rational differences independently rounded to binary64; direct raw-row tally; exact symbolic cancellation plus4ULP numeric bound",
        independence="Different arithmetic path in same author script, not independent native/physical-chain QA; root separately reviews outputs")


def save_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def save_csv(path, rows):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def run(source, freeze_path, output):
    source_pin, raw = pin(source)
    freeze_pin, freeze_bytes = pin(freeze_path)
    require(source_pin["sha256"] == SOURCE_SHA and freeze_pin["sha256"] == FREEZE_SHA, "Frozen input SHA mismatch")
    freeze = decode(freeze_bytes)
    require(freeze["N_original_requests"] == 128 and freeze["N_proxy_slots"] == 1408, "Original frame changed")
    require(freeze["absolute_tolerances"] == list(TAU) and freeze["score_scale"] == list(SPANS), "Tolerance/scale changed")
    out = Path(output)
    require(out.is_absolute() and not out.exists() and not out.is_symlink(), "No-clobber output required")
    out.mkdir(parents=False)
    source_code_pin, _ = pin(Path(__file__).resolve())
    save_json(out/"RUN_INTENT.json", dict(created_utc=datetime.now(timezone.utc).isoformat(),
        source=source_pin, freeze=freeze_pin, implementation=source_code_pin,
        command=sys.argv, executable=sys.executable, scope="FROZEN128_PRESELECTION_DIAGNOSTIC_ONLY"))
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        require(len(reader.fieldnames) == len(set(reader.fieldnames)), "Duplicate CSV header")
        original_csv = list(reader)
        require(len(original_csv) == 128 and len({r['request_id'] for r in original_csv}) == 128, "128 unique original requests required")
        rows, decomposition, original = [], [], []
        for index, row in enumerate(original_csv):
            require(None not in row and row["state"] in STATES, "CSV row/state invalid")
            target, target_missing = vector(row["target"], "target")
            proxy, proxy_missing = vector(row["grid_proxy"], "grid_proxy")
            actual, actual_missing = vector(row["actual"], "actual")
            q = int(row["q_proxy"]) if row["q_proxy"] else None
            require(q is None or 10 <= q <= 20, "Q outside frozen scan")
            require(q is None or target[2] == q, "Own selected Q target differs")
            errors, flags, joint = classify_proxy(target, proxy)
            base = dict(source_row_0based=index, request_id=row["request_id"], candidate_id=row["candidate_id"],
                candidate_geometry_identity_sha256=row["candidate_geometry_identity_sha256"],
                q_proxy=q, q_emx=row["q_emx"] or None, state=row["state"],
                status_detail=row["status_detail"], selected_support_status=row["selected_support_status"])
            rows.append(dict(**base, target=target, grid_proxy=proxy, actual=actual,
                proxy_minus_target=errors,
                proxy_error_div_fixed_span=[e/s if e is not None else None for e, s in zip(errors, SPANS)],
                proxy_within_by_feature=flags, proxy_exceeded_features=[f for f,v in zip(FEATURES,flags) if v is False],
                proxy_joint_within_tolerance=joint,
                proxy_preselection_status="UNKNOWN" if joint is None else "WITHIN_ALL_FOUR" if joint else "EXCEEDS_AT_LEAST_ONE",
                frozen_strict_joint_hit=row["strict_joint_hit"] or None,
                missing=target_missing+proxy_missing+actual_missing,
                original_source_record=decode(row["original_source_record"]) if row["original_source_record"] else None))
            parts = signed_k(target, proxy, actual)
            decomposition.append(dict(**base, **parts,
                interpretation="STRICT_VALID_RESPONSE" if row["state"] == "STRICT_VALID" else "DESCRIPTOR_ONLY_NOT_STRICT" if row["state"] == "EMX_INVALID" else "NO_EMX_RESPONSE",
                K_semantics="K_abs; signed differences, not signed physical coupling",
                causal_contribution="NOT_IDENTIFIED_NO_ABSOLUTE_COMPONENT_RATIOS",
                touchstone_sha=row["touchstone_sha"] or None))
            original.append(dict(target=target, proxy=proxy, actual=actual, state=row["state"]))
        groups = {"ALL128": group_summary(rows)}
        groups.update({state: group_summary([r for r in rows if r['state'] == state]) for state in STATES})
        summary = dict(schema="eucap15_proxy_preselection_diagnosis.v1", N_original_requests=128,
            scope="SAVED_SELECTED_Q_BEFORE_EMX_PROXY_DIAGNOSTIC", feature_order=list(FEATURES),
            absolute_tolerances=list(TAU), score_spans=list(SPANS), groups=groups,
            K_decomposition_by_state={state: dict(N_original_group=sum(r['state']==state for r in decomposition),
                N_complete_signed_identity=sum(r['state']==state and r['signed_identity_numeric_pass'] is True for r in decomposition),
                N_unknown_signed_identity=sum(r['state']==state and r['signed_identity_numeric_pass'] is None for r in decomposition),
                maximum_abs_closure_residual=max((abs(r['closure_residual']) for r in decomposition if r['state']==state and r['closure_residual'] is not None), default=None)) for state in STATES},
            limitations=["Score minimum is not an all-feature tolerance guarantee; no alternative Q or fallback was selected.",
                "Proxy and actual vectors are saved source values; no new inference, EMX or whole-chain verification.",
                "K denotes abs(k); the decomposition is signed arithmetic error and not causal attribution.",
                "EMX_INVALID is descriptor-only and remains separate from STRICT_VALID; original statuses are untouched.",
                "Missing/nonfinite comparison components are UNKNOWN, not zero or a successful match.",
                "Same frozen128 snapshot only; no latest terminal refresh, previous K-tail/P95 recomputation, training admission or FINAL claim."])
        check = arithmetic_check(original, rows, decomposition, summary)
        require(pin(source)[0] == source_pin and pin(freeze_path)[0] == freeze_pin and pin(Path(__file__).resolve())[0] == source_code_pin, "Input/source changed")
        for name, values in (("PRESELECTION", rows), ("K_SIGNED_DECOMPOSITION", decomposition)):
            save_json(out/(name+".json"), values); save_csv(out/(name+".csv"), values)
        save_json(out/"SUMMARY.json", summary)
        save_json(out/"ARITHMETIC_CHECK.json", check)
        outputs = [pin(p)[0] for p in sorted(out.iterdir()) if p.is_file()]
        receipt = dict(schema="eucap15_proxy_preselection_receipt.v1", status="COMPLETE_SCOPED_OFFLINE_DIAGNOSTIC",
            completed_utc=datetime.now(timezone.utc).isoformat(), inputs=[source_pin,freeze_pin],
            implementation=source_code_pin, outputs=outputs, input_sha_before_after_unchanged=True,
            source_request_rows=128, new_training_updates=0, new_model_inference=0, new_native_runs=0,
            new_arithmetic_checks=check, original_failure_flags_and_Q_unchanged=True,
            physical_chain_qa="REUSED_NOT_RERUN", limitations=summary["limitations"])
        save_json(out/"RECEIPT.json",receipt)
        with (out/"SHA256SUMS").open("x") as stream:
            for p in sorted(out.iterdir()):
                if p.is_file() and p.name != "SHA256SUMS": stream.write(pin(p)[0]["sha256"]+"  "+p.name+"\n")
        print(json.dumps(dict(status=receipt['status'],groups={k:{x:v[x] for x in ('N_original_group','N_proxy_joint_within','N_proxy_joint_exceeds','N_proxy_joint_unknown')} for k,v in groups.items()},receipt=pin(out/"RECEIPT.json")[0]),ensure_ascii=False),flush=True)
    except Exception as error:
        save_json(out/"FAILURE_RECEIPT.json",dict(status="FAIL_PRESERVED",error=repr(error),traceback=traceback.format_exc(),source=source_pin,freeze=freeze_pin))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True); parser.add_argument("--freeze",required=True); parser.add_argument("--out",required=True)
    args=parser.parse_args()
    run(args.source,args.freeze,args.out)
