"""Historical111 compatibility extraction; not fresh EMX or formal qualification.

The extraction function is the current exact56 function with only grid/shape
and historical text parameterized. Formulas, strict predicates, roundtrip
limits, polarity projection and SRF helpers remain unchanged.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import numpy as np

FREQUENCY_GRID_HZ=tuple(5_000_000_000+500_000_000*i for i in range(111))
SOURCE_PINS={
 'rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py':'5fbf0e3e22737874ea8b74fc758fb0f6451aca44a146fedb35a2ec95eeb0ac29',
 'rfic_transformer_inverse_design/analysis/extraction.py':'ff9ac389b04b55ae95ff0839d02d7a4a12be2f6bcdb26e6d871e0557ae516ce0',
 'rfic_transformer_inverse_design/network_analysis.py':'a6439fe88bdf9f64eb34182843c0a98f2ef109ad12a4a9bbb0ca7e6f68093bcf',
 'rfic_transformer_inverse_design/sim/touchstone.py':'9ee3490be889ea502d4b6e3ffcee5caf8e22d27d0c6f765ac27e9ff523462a3b',
 'rfic_transformer_inverse_design/sim/base.py':'f0a6a6a7418834f89cae13cac16a4df1302aac4ecb2ecb92dcc9ceab4c253c24',
 'rfic_transformer_inverse_design/campaigns/broadband56_full_campaign_authorization.py':'9d3f2c65cb5d573ed397085ab4b5499c65dea76665cb312c43f4ff88f052e948',
}
SOURCE_IDENTITIES=[]


def pin(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('exact nonsymlink path required')
    before=path.stat();raw=path.read_bytes();after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('source changed during read')
    return dict(path=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def configure_repository(repo):
    """Bind existing formula sources; do not modify their module globals."""
    root=Path(repo).absolute();sources=[]
    for relative,sha in SOURCE_PINS.items():
        p=pin(root/relative)
        if p['sha256']!=sha:raise ValueError('current formula source drift: '+relative)
        sources.append(p)
    sys.path.insert(0,str(root))
    qa=importlib.import_module('rfic_transformer_inverse_design.campaigns.broadband56_s4p_qa')
    if Path(qa.__file__).absolute()!=root/'rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py':
        raise ValueError('foreign preimported QA module')
    for name in ('ArtifactQaResult','Broadband56S4pQaError','load_touchstone','z_to_s',
                 'single_ended_to_differential_z','_reference_impedance_valid','_estimate_first_srf',
                 '_below_half_srf','_derived_ratio','_broad_envelope','_bool_text','_srf_record',
                 'S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE','PASSIVITY_SINGULAR_VALUE_TOLERANCE',
                 'RECIPROCITY_ABSOLUTE_TOLERANCE'):
        globals()[name]=getattr(qa,name)
    global SOURCE_IDENTITIES
    SOURCE_IDENTITIES=sources
    return sources


def extract111_under_current_mapping(path: Path) -> ArtifactQaResult:
    """Re-extract all111 historical rows under the current, conditional port convention."""

    try:
        touchstone = load_touchstone(path)
    except Exception as exc:  # noqa: BLE001 - preserve parser error.
        raise Broadband56S4pQaError(f"Touchstone parse failed: {exc}") from exc
    frequencies = np.asarray(touchstone.freqs_hz, dtype=np.float64)
    expected_frequencies = np.asarray(FREQUENCY_GRID_HZ, dtype=np.float64)
    s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
    checks = {
        "port_count_exact_four": int(touchstone.num_ports) == 4,
        "frequency_count_exact_111": int(touchstone.num_freqs) == len(FREQUENCY_GRID_HZ),
        "frequency_vector_exact": np.array_equal(frequencies, expected_frequencies),
        "frequency_strictly_increasing": bool(
            frequencies.size > 1 and np.all(np.diff(frequencies) > 0.0)
        ),
        "s_matrix_shape_exact": tuple(s_matrix.shape) == (111, 4, 4),
        "s_matrix_finite": bool(
            np.isfinite(s_matrix.real).all() and np.isfinite(s_matrix.imag).all()
        ),
        "reference_impedance_valid": _reference_impedance_valid(
            touchstone.reference_impedance_ohm, ports=4
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise Broadband56S4pQaError(
            "historical111 S4P exact frequency contract failed: " + ", ".join(failed)
        )

    try:
        z_matrix = np.asarray(touchstone.to_z_parameters(), dtype=np.complex128)
    except Exception as exc:  # noqa: BLE001 - preserve conversion failure.
        raise Broadband56S4pQaError(f"S-to-Z conversion failed: {exc}") from exc
    if tuple(z_matrix.shape) != (111, 4, 4) or not (
        np.isfinite(z_matrix.real).all() and np.isfinite(z_matrix.imag).all()
    ):
        raise Broadband56S4pQaError("S-to-Z conversion produced incomplete or non-finite Z")
    try:
        reconstructed_s = z_to_s(
            z_matrix, z0=touchstone.reference_impedance_ohm
        )
    except Exception as exc:  # noqa: BLE001
        raise Broadband56S4pQaError(f"Z-to-S roundtrip failed: {exc}") from exc
    roundtrip_error = float(np.max(np.abs(reconstructed_s - s_matrix)))
    if not math.isfinite(roundtrip_error) or roundtrip_error > S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE:
        raise Broadband56S4pQaError(
            "S-to-Z roundtrip exceeds tolerance: "
            f"actual={roundtrip_error:.17g}, allowed={S_TO_Z_ROUNDTRIP_ABSOLUTE_TOLERANCE:.17g}"
        )
    z_diff = single_ended_to_differential_z(z_matrix)
    if tuple(z_diff.shape) != (111, 2, 2) or not (
        np.isfinite(z_diff.real).all() and np.isfinite(z_diff.imag).all()
    ):
        raise Broadband56S4pQaError(
            "differential Z projection produced incomplete or non-finite data"
        )

    primary_srf = _estimate_first_srf(frequencies, z_diff[:, 0, 0].imag)
    secondary_srf = _estimate_first_srf(frequencies, z_diff[:, 1, 1].imag)
    rows: list[dict[str, Any]] = []
    passivity_failures = 0
    reciprocity_failures = 0
    descriptor_valid_rows = 0
    strict_valid_rows = 0

    for frequency_index, frequency_hz in enumerate(FREQUENCY_GRID_HZ):
        s_at_frequency = s_matrix[frequency_index]
        z_at_frequency = z_matrix[frequency_index]
        z_diff_at_frequency = z_diff[frequency_index]
        omega = 2.0 * math.pi * float(frequency_hz)
        z11 = complex(z_diff_at_frequency[0, 0])
        z22 = complex(z_diff_at_frequency[1, 1])
        z21 = complex(z_diff_at_frequency[1, 0])
        lp_h = float(z11.imag / omega)
        ls_h = float(z22.imag / omega)
        mutual_h = float(z21.imag / omega)
        product = abs(lp_h * ls_h)
        signed_k = mutual_h / math.sqrt(product) if product > 1.0e-30 else math.nan
        qp = _derived_ratio(z11.imag, z11.real)
        qs = _derived_ratio(z22.imag, z22.real)
        qmin = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
        ls_over_lp = _derived_ratio(ls_h, lp_h)
        features = {
            "lp_h": lp_h,
            "ls_h": ls_h,
            "lp_nh": lp_h * 1.0e9,
            "ls_nh": ls_h * 1.0e9,
            "qp": qp,
            "qs": qs,
            "qmin": qmin,
            "mutual_inductance_h": mutual_h,
            "signed_k": signed_k,
            "k_abs": abs(signed_k),
            "ls_over_lp": ls_over_lp,
            "xp_ohm": omega * lp_h,
            "xs_ohm": omega * ls_h,
        }
        finite_values = bool(
            np.isfinite(s_at_frequency.real).all()
            and np.isfinite(s_at_frequency.imag).all()
            and np.isfinite(z_at_frequency.real).all()
            and np.isfinite(z_at_frequency.imag).all()
            and all(math.isfinite(value) for value in features.values())
        )
        positive_primary_resistance = z11.real > 0.0
        positive_secondary_resistance = z22.real > 0.0
        positive_primary_reactance = z11.imag > 0.0
        positive_secondary_reactance = z22.imag > 0.0
        broadband_valid = bool(
            finite_values
            and positive_primary_resistance
            and positive_secondary_resistance
            and positive_primary_reactance
            and positive_secondary_reactance
        )
        below_half_srf = _below_half_srf(
            float(frequency_hz), primary_srf
        ) and _below_half_srf(float(frequency_hz), secondary_srf)
        strict_valid = bool(broadband_valid and below_half_srf)
        descriptor_valid_rows += int(broadband_valid)
        strict_valid_rows += int(strict_valid)

        max_singular_value = float(np.linalg.svd(s_at_frequency, compute_uv=False)[0])
        passivity_status = (
            "PASS"
            if max_singular_value <= 1.0 + PASSIVITY_SINGULAR_VALUE_TOLERANCE
            else "FAIL"
        )
        reciprocity_error = float(np.max(np.abs(s_at_frequency - s_at_frequency.T)))
        reciprocity_status = (
            "PASS"
            if reciprocity_error <= RECIPROCITY_ABSOLUTE_TOLERANCE
            else "FAIL"
        )
        passivity_failures += int(passivity_status == "FAIL")
        reciprocity_failures += int(reciprocity_status == "FAIL")

        broad_inside, broad_reasons = _broad_envelope(features)
        practical_inside = bool(
            0.10 <= features["k_abs"] <= 0.85
            and 0.50 <= features["ls_over_lp"] <= 2.0
        )
        matrix_values: dict[str, float] = {}
        for matrix_name, matrix in (
            ("s", s_at_frequency),
            ("z", z_at_frequency),
        ):
            for row_index in range(4):
                for col_index in range(4):
                    value = complex(matrix[row_index, col_index])
                    matrix_values[f"{matrix_name}{row_index + 1}{col_index + 1}_re"] = float(
                        value.real
                    )
                    matrix_values[f"{matrix_name}{row_index + 1}{col_index + 1}_im"] = float(
                        value.imag
                    )
        rows.append(
            {
                "frequency_hz": int(frequency_hz),
                **features,
                "finite_values": _bool_text(finite_values),
                "positive_primary_resistance": _bool_text(
                    positive_primary_resistance
                ),
                "positive_secondary_resistance": _bool_text(
                    positive_secondary_resistance
                ),
                "positive_primary_inductive_reactance": _bool_text(
                    positive_primary_reactance
                ),
                "positive_secondary_inductive_reactance": _bool_text(
                    positive_secondary_reactance
                ),
                "extraction_continuity_status": "PASS",
                "below_half_srf": _bool_text(below_half_srf),
                "broadband_descriptor_valid": _bool_text(broadband_valid),
                "strict_lumped_valid": _bool_text(strict_valid),
                "srf_status": (
                    f"PRIMARY={primary_srf.status};SECONDARY={secondary_srf.status}"
                ),
                "passivity_status": passivity_status,
                "reciprocity_status": reciprocity_status,
                "inside_broad_response_envelope": _bool_text(broad_inside),
                "inside_literature_practical_panel": _bool_text(
                    practical_inside
                ),
                "outside_envelope_reason": "" if broad_inside else ";".join(broad_reasons),
                **matrix_values,
            }
        )

    return ArtifactQaResult(
        rows=tuple(rows),
        summary={
            "port_count": 4,
            "frequency_points": 111,
            "frequency_start_hz": FREQUENCY_GRID_HZ[0],
            "frequency_stop_hz": FREQUENCY_GRID_HZ[-1],
            "frequency_step_hz": 500_000_000,
            "s_to_z_roundtrip_max_abs_error": roundtrip_error,
            "passivity_fail_frequency_count": passivity_failures,
            "reciprocity_fail_frequency_count": reciprocity_failures,
            "broadband_descriptor_valid_rows": descriptor_valid_rows,
            "strict_lumped_valid_rows": strict_valid_rows,
            "primary_srf": _srf_record(primary_srf),
            "secondary_srf": _srf_record(secondary_srf),
            "checks": checks,
        },
    )


def target15_summary(result):
    if len(result.rows)!=111 or tuple(row['frequency_hz'] for row in result.rows)!=FREQUENCY_GRID_HZ:
        raise ValueError('all111 original frequency rows must remain present')
    row=result.rows[20]
    if row['frequency_hz']!=15_000_000_000:raise ValueError('15GHz must be exact original zero-based index20')
    reasons=[name for name in ('finite_values','positive_primary_resistance','positive_secondary_resistance',
              'positive_primary_inductive_reactance','positive_secondary_inductive_reactance','below_half_srf')
             if row[name]!='true']
    return dict(frequency_hz=15_000_000_000,original_frequency_index_zero_based=20,
        current_definition_strict_lumped_valid=row['strict_lumped_valid']=='true',
        strict_failure_reasons=reasons,row=dict(row),
        nonfinite_numeric_fields=[name for name,value in row.items() if isinstance(value,float) and not math.isfinite(value)],
        label_interpretation='CONDITIONAL_ON_CURRENT_PORT_MAPPING_NOT_PHYSICAL_QUALIFICATION')


def json_safe(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [json_safe(v) for v in value]
    return value


def save_new(path,value):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(json_safe(value),f,indent=2,sort_keys=True,allow_nan=False);f.write('\n')


def run(args):
    configure_repository(args.repo)
    source=pin(args.s4p)
    if source['sha256']!=args.s4p_sha256:raise ValueError('historical S4P source identity changed')
    out=Path(args.out).absolute()
    if out.exists() or '..' in out.parts or any(p.is_symlink() for p in (out,*out.parents)):
        raise ValueError('new no-clobber output required')
    out.mkdir(parents=True,exist_ok=False)
    common=dict(schema='eucap15_historical111_reextraction.v1',utc=datetime.now(timezone.utc).isoformat(),
        source=source,extractor=pin(__file__),current_definition_sources=SOURCE_IDENTITIES,
        evidence_class='HISTORICAL_S4P_REEXTRACTION_NOT_FRESH',
        applied_external_ports=['P001','P002','P003','P004'],internal_port_permutation=[0,1,3,2],
        actual_identity_status='UNKNOWN',current_process_compatibility='UNKNOWN',
        actual_port_mapping_compatibility='UNKNOWN',actual_gds_calibre_status='UNKNOWN',
        formal_physical_qualification='UNKNOWN',production_accepted_added=0,
        fresh_emx_performed=False,simulator_calls=0,frequency_interpolation_or_resampling=False,
        srf_method='UNCHANGED_ACTUAL_ADJACENT_REACTANCE_ZERO_CROSSING_LINEAR_BRACKET_ESTIMATE',
        srf_estimation_is_not_spectrum_resampling=True,numpy_version=np.__version__,
        explanation='Numerical extraction does not prove process, ports, geometry identity, GDS/DRC or production eligibility.')
    try:
        result=extract111_under_current_mapping(args.s4p)
        target=target15_summary(result)
        if pin(args.s4p)!=source:raise ValueError('historical source changed during extraction')
        for p in SOURCE_IDENTITIES:
            if pin(p['path'])!=p:raise ValueError('formula source changed during extraction')
        csv_path=out/'historical_features_all111.csv'
        with csv_path.open('x',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=list(result.rows[0]));writer.writeheader();writer.writerows(result.rows)
        save_new(out/'TARGET15.json',target)
        receipt=dict(common,status='HISTORICAL_REEXTRACTION_COMPLETE_NOT_QUALIFIED',
            actual_frequency_vector_hz=list(FREQUENCY_GRID_HZ),retained_rows=111,
            target_frequency_index_zero_based=20,summary=result.summary,
            target15=pin(out/'TARGET15.json'),all111_csv=pin(csv_path),
            invalid_numeric_encoding='CSV preserves nan; JSON nonfinite values are null with field names reported, never zero-filled.')
        save_new(out/'RECEIPT.json',receipt)
        with (out/'SHA256SUMS').open('x',encoding='utf-8') as f:
            for name in ('historical_features_all111.csv','TARGET15.json','RECEIPT.json'):
                f.write(pin(out/name)['sha256']+'  '+name+'\n')
        return pin(out/'RECEIPT.json')
    except Exception as exc:
        save_new(out/'FAILURE.json',dict(common,status='FAIL_HISTORICAL_EXTRACTION_NOT_QUALIFIED',
                 error_type=type(exc).__name__,error=str(exc)))
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','s4p','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--s4p-sha256',required=True);args=parser.parse_args()
    try:print(json.dumps(dict(receipt=run(args))));return 0
    except Exception as exc:
        print(json.dumps(dict(status='HISTORICAL_REEXTRACTION_STOP',error_type=type(exc).__name__,error=str(exc))))
        return 2


if __name__=='__main__':raise SystemExit(main())
