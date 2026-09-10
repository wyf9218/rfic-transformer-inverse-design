"""Pure reconciliation of saved original56 labels and fifteen-GHz claims.

This validates supplied values, not their native authenticity. The caller must
independently bind the frozen target/candidate, hashes, same-GDS DRC, solver and
feature closure before using the result as physical evidence. No files, models,
S-parameters, subprocesses or networks are read or executed here.
"""
from __future__ import annotations

import csv
import io
import math

from .eucap15_selected_evidence import (
    FREQUENCIES, PHYSICAL_FIELDS, FLAG_FIELDS, SelectedEvidenceError,
    _original_row_matches_csv, _numeric_text, _boolean_text, _fields, _same,
    _require, clean,
)


def _vector(value, name, *, positive=False):
    _require(isinstance(value, (list, tuple)) and len(value) == 4,
             name + " must contain exactly four numbers")
    _require(all(type(v) in (int, float) and math.isfinite(v)
                 and (not positive or v > 0) for v in value),
             name + " requires finite numbers (positive where specified), never bool")
    return list(value)


def validate_feature_values(feature, raw_csv: bytes, *, wanted, proxy, scale, tau):
    """Return actual/strict validity/hit/status from supplied label bytes only.

    Four-vectors use [Lp_nH, Ls_nH, min(Qp,Qs), abs(k)]. Targets, scale and
    absolute tolerances must be positive; finite proxy values may be negative.
    The caller supplies the exact frozen scale/tolerance floats. Invalid CSV
    observations remain null in JSON, never clipped or zero-filled. This API
    neither selects Q nor authenticates any native source or receipt.
    """
    try:
        wanted = _vector(wanted, "wanted", positive=True)
        proxy = _vector(proxy, "proxy")
        scale = _vector(scale, "scale", positive=True)
        tau = _vector(tau, "tau", positive=True)
        _require(isinstance(raw_csv, bytes), "raw_csv must be original bytes")
        _fields(feature, {"target": wanted, "proxy_self": proxy,
            "score_scale": scale, "absolute_hit_tolerances": tau}, "frozen value contract")
        reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8"), newline=""))
        rows = list(reader)
        names = reader.fieldnames
        _require(names and all(isinstance(n, str) and n for n in names)
                 and len(set(names)) == len(names) and len(rows) == 56,
                 "exact56 feature CSV/unique columns required")
        _require(all(set(row) == set(names) and all(isinstance(v, str) for v in row.values())
                     for row in rows), "feature CSV has missing or extra cells")
        _require([int(r["frequency_hz"]) for r in rows] == FREQUENCIES,
                 "feature CSV is not exact5..60GHz step1")
        _fields(feature["original_56_summary"], {"port_count": 4, "frequency_points": 56,
            "frequency_start_hz": FREQUENCIES[0], "frequency_stop_hz": FREQUENCIES[-1],
            "frequency_step_hz": 10**9}, "original56 summary")
        row = rows[10]
        _original_row_matches_csv(feature["original_frequency_row"], row)
        actual_raw = [_numeric_text(row[name], name) for name in PHYSICAL_FIELDS]
        qp, qs = (_numeric_text(row[name], name) for name in ("qp", "qs"))
        expected_qmin = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
        signed_k = _numeric_text(row["signed_k"], "signed_k")
        _require(_same(clean(actual_raw[2]), clean(expected_qmin)),
                 "stored qmin differs from finite qp/qs minimum contract")
        _require(_same(clean(actual_raw[3]), clean(abs(signed_k))),
                 "stored k_abs differs from absolute signed_k contract")
        actual = clean(actual_raw)
        descriptor = _boolean_text(row["broadband_descriptor_valid"], "descriptor")
        strict = _boolean_text(row["strict_lumped_valid"], "strict")
        _require(descriptor == all(_boolean_text(row[name], name) for name in FLAG_FIELDS),
                 "stored descriptor disagrees with its original predicates")
        _require(strict == (descriptor and _boolean_text(row["below_half_srf"], "below_half_srf")),
                 "stored strict flag disagrees with descriptor/SRF predicate")
        physics = row["passivity_status"] == row["reciprocity_status"] == "PASS"
        errors = [a-t for a, t in zip(actual_raw, wanted)]
        proxy_errors = [a-p for a, p in zip(actual_raw, proxy)]
        hits = [math.isfinite(e) and abs(e) <= t for e, t in zip(errors, tau)]
        finite = all(math.isfinite(a) for a in actual_raw)
        valid = bool(finite and descriptor and strict and physics)
        score = math.sqrt(sum((e/s)**2 for e, s in zip(errors, scale))/4) if finite else None
        _fields(feature, {"actual_fresh_emx": actual, "emx_minus_target": clean(errors),
            "emx_minus_proxy": clean(proxy_errors), "normalized_response_score": score,
            "within_tolerance": hits, "joint_response_hit": all(hits),
            "descriptor_valid": descriptor, "strict_lumped_valid": strict, "physics_qa_pass": physics,
            "valid_for_strict_comparison": valid, "strict_joint_hit": bool(all(hits) and valid),
            "target_relative_signed_percent": clean([100*e/t for e, t in zip(errors, wanted)]),
            "target_relative_absolute_percent": clean([100*abs(e)/t for e, t in zip(errors, wanted)])},
            "recomputed original15 feature")
        return {"actual": actual, "valid_for_strict_comparison": valid,
            "strict_joint_hit": bool(all(hits) and valid),
            "status": "STRICT_VALID" if valid else "EMX_INVALID"}
    except SelectedEvidenceError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, csv.Error) as error:
        raise SelectedEvidenceError("Invalid supplied feature values: " + str(error)) from error
