"""Read-only legacy 111/56-point re-extraction; never a fresh-EMX receipt.

Uses the deployed Touchstone, differential-port and SRF implementations.
Numerical strict status does not certify missing process/GDS/DRC provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as qa


class EvidenceError(ValueError):
    pass


def derive(touchstone):
    f = np.asarray(touchstone.freqs_hz, dtype=float)
    s = np.asarray(touchstone.s_matrix, dtype=complex)
    if touchstone.num_ports != 4:
        raise EvidenceError("PHYSICAL_CONFIGURATION_INCOMPATIBLE: expected four ports")
    grids = {56: np.arange(5, 61, dtype=float) * 1e9,
             111: np.arange(5, 60.5, .5, dtype=float) * 1e9}
    if len(f) not in grids or not np.array_equal(f, grids[len(f)]):
        raise EvidenceError("FREQUENCY_EVIDENCE_INSUFFICIENT: require actual complete 5..60 GHz 56/111 grid")
    if s.shape != (len(f), 4, 4) or not np.isfinite(s).all():
        raise EvidenceError("RESPONSE_INVALID: incomplete/nonfinite S matrix")
    if not qa._reference_impedance_valid(touchstone.reference_impedance_ohm, ports=4):
        raise EvidenceError("RESPONSE_INVALID: reference impedance")
    z = np.asarray(touchstone.to_z_parameters(), dtype=complex)
    if not np.isfinite(z).all():
        raise EvidenceError("RESPONSE_INVALID: nonfinite Z matrix")
    error = float(np.max(np.abs(qa.z_to_s(z, z0=touchstone.reference_impedance_ohm) - s)))
    if error > qa.S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE:
        raise EvidenceError("RESPONSE_INVALID: S/Z roundtrip tolerance")
    zd = qa.single_ended_to_differential_z(z)
    p_srf = qa._estimate_first_srf(f, zd[:, 0, 0].imag)
    s_srf = qa._estimate_first_srf(f, zd[:, 1, 1].imag)
    i = int(np.flatnonzero(f == 15e9)[0])  # exact measured point, no interpolation
    p, secondary, m = zd[i, 0, 0], zd[i, 1, 1], zd[i, 1, 0]
    omega = 2 * math.pi * 15e9
    lp, ls = float(p.imag / omega), float(secondary.imag / omega)
    product = abs(lp * ls)
    k = float(m.imag / omega / math.sqrt(product)) if product > 1e-30 else math.nan
    qp = qa._derived_ratio(p.imag, p.real)
    qs = qa._derived_ratio(secondary.imag, secondary.real)
    q = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
    values = dict(lp_nh=lp * 1e9, ls_nh=ls * 1e9, qp=qp, qs=qs,
                  q_min=q, signed_k=k, k_abs=abs(k))
    descriptor = all(math.isfinite(v) for v in values.values()) and min(
        p.real, secondary.real, p.imag, secondary.imag) > 0
    below_half = qa._below_half_srf(15e9, p_srf) and qa._below_half_srf(15e9, s_srf)
    strict = bool(descriptor and below_half)
    reasons = []
    if not descriptor:
        reasons.append("DESCRIPTOR_NONFINITE_OR_NONPOSITIVE_RX")
    if not below_half:
        reasons.append("TWO_SIDED_HALF_SRF_NOT_PASSED")
    if not (math.isfinite(k) and .2 <= abs(k) <= .85):
        reasons.append("K_OUTSIDE_0P2_0P85")
    return dict(
        frequency_count=len(f), frequency_hz=15e9,
        frequency_start_hz=float(f[0]), frequency_stop_hz=float(f[-1]),
        physical15={name: (value if math.isfinite(value) else None) for name, value in values.items()},
        primary_srf=qa._srf_record(p_srf), secondary_srf=qa._srf_record(s_srf),
        descriptor_valid=bool(descriptor), below_half_srf=bool(below_half),
        strict_valid=strict, numerical_range_eligible=bool(strict and .2 <= abs(k) <= .85),
        strict_reasons=reasons, s_z_roundtrip_abs_max=error,
        passivity_sigma_max=float(np.linalg.svd(s, compute_uv=False).max()),
        reciprocity_abs_max=float(np.max(np.abs(s - s.transpose(0, 2, 1)))),
        extraction_mode="HISTORICAL_ACTUAL_GRID_REEXTRACTION_NOT_FRESH_EMX",
    )


def one(row):
    result = dict(row)
    result.update(strict_valid=None, fully_qualified=False,
                  compatibility_status="SOURCE_EQUIVALENCE_AND_SAMPLE_PROOFS_PENDING")
    path = Path(row["s4p_path"])
    if not path.is_file():
        result.update(status="ARTIFACT_MISSING", reason="indexed S4P absent")
        return result
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result["observed_s4p_sha256"] = digest
    result["s4p_bytes"] = before.st_size
    if digest != row["s4p_sha256"]:
        result.update(status="BINDING_MISMATCH", reason="actual S4P SHA differs from frozen index")
        return result
    try:
        result.update(derive(qa.load_touchstone(path)))
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise EvidenceError("ACTIVE_OR_CHANGED_ARTIFACT: stat changed during read")
        result["status"] = "STRICT_PASS" if result["strict_valid"] else "STRICT_FAIL"
        current = result["physical15"]
        result["legacy_center_absolute_delta"] = {
            k: abs(current[k] - row[k]) if current[k] is not None else None
            for k in ("lp_nh", "ls_nh", "q_min", "k_abs")}
    except EvidenceError as exc:
        result.update(status=str(exc).split(":", 1)[0], reason=str(exc), strict_valid=None)
    except Exception as exc:
        result.update(status="EXTRACTION_ERROR", reason=f"{type(exc).__name__}: {exc}", strict_valid=None)
    # Only indexed evaluation root; no recursive walk or blanket certification.
    summary = path.parent.parent / "summary.json"
    result["summary_path"] = str(summary)
    result["proof_gaps"] = ["CURRENT_PROCESS_CONTENT_BINDING", "ACTUAL_GDS_PORT_EQUIVALENCE", "CALIBRE_TERMINAL"]
    if summary.is_file():
        try:
            raw = summary.read_bytes()
            obj = json.loads(raw)
            result["summary_sha256"] = hashlib.sha256(raw).hexdigest()
            result["summary_ok"] = obj.get("ok")
            result["summary_touchstone_matches"] = obj.get("touchstone_path") == str(path)
            gc = obj.get("geometry_check") or {}
            result["summary_geometry_check"] = {k: gc.get(k) for k in ("ok", "skipped", "backend")}
            result["summary_geometry_keys"] = sorted((obj.get("geometry") or {}).keys())
            result["declared_artifact_paths"] = {
                str(k): str(v) for k, v in (obj.get("artifacts") or {}).items()
                if isinstance(v, str) and any(w in str(k).lower() for w in ("gds", "layout", "drc", "calibre"))}
            if obj.get("touchstone_path") != str(path):
                result["proof_gaps"].append("SUMMARY_RESPONSE_PATH_BINDING")
        except Exception as exc:
            result["proof_gaps"].append(f"SUMMARY_UNREADABLE:{type(exc).__name__}")
    else:
        result["proof_gaps"].append("SUMMARY_MISSING")
    return result


def remote_run(rows):
    import datetime
    import inspect
    sources = {}
    for function in (qa.load_touchstone, qa.single_ended_to_differential_z,
                     qa._estimate_first_srf, qa.z_to_s):
        p = Path(inspect.getsourcefile(function))
        sources[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    print(json.dumps(dict(kind="runtime", time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          extractor_sources=sources, row_count=len(rows))), flush=True)
    for row in rows:
        print(json.dumps(dict(kind="result", result=one(row)), allow_nan=False), flush=True)
