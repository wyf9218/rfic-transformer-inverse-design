"""New bounded synthetic metadata tests; no physical files, models or native tools."""
from copy import deepcopy
import pytest
from research.broadband56_nn import eucap15_acquisition_cost_reasons as m


def invalid_fixture():
    flags={k:"true" for k in m.BOOLEAN_FLAGS}
    flags.update(below_half_srf="false",strict_lumped_valid="false",frequency_hz=15000000000,
                 passivity_status="PASS",reciprocity_status="PASS",srf_status="SYNTHETIC")
    row=dict(state="EMX_INVALID",candidate_id="synthetic",below_half_srf=False,
             descriptor_valid=True,physics_qa_pass=True,strict_valid=False,feature=None)
    feature=dict(candidate_id="synthetic",frequency_ghz=15,original_frequency_row=flags,
                 physics_qa_pass=True,valid_for_strict_comparison=False,original_56_summary={})
    return row,feature


def test_single_srf_reason_is_from_original_flag():
    row,f=invalid_fixture()
    assert m.invalid_reasons(row,f)["reason_codes"]==["BELOW_HALF_SRF_FALSE"]


def test_overlapping_causes_and_exclusive_signature():
    row,f=invalid_fixture();row["descriptor_valid"]=False
    f["original_frequency_row"].update(broadband_descriptor_valid="false",positive_primary_resistance="false")
    detail=m.invalid_reasons(row,f)
    r=dict(source="SPARSE_TARGETED",state="EMX_INVALID",invalid_detail=detail,failed_checks=[])
    r.update({k:None for k in m.COSTS})
    g=m.aggregate([r])[0]
    assert g["invalid_n"]==1 and sum(g["exclusive_invalid_reason_combinations"].values())==1
    assert sum(g["overlapping_invalid_reason_counts"].values())==3


def test_non_srf_invalid_not_relabelled_srf():
    row,f=invalid_fixture();row.update(below_half_srf=True,physics_qa_pass=False)
    f["physics_qa_pass"]=False
    f["original_frequency_row"].update(below_half_srf="true",passivity_status="FAIL")
    assert m.invalid_reasons(row,f)["reason_codes"]==["PASSIVITY_STATUS_NOT_PASS","PHYSICS_QA_PASS_FALSE"]


@pytest.mark.parametrize("change",["malformed_flag","identity","frequency","saved_flag"])
def test_recorded_predicate_or_identity_drift_rejected(change):
    row,f=invalid_fixture()
    if change=="malformed_flag":f["original_frequency_row"]["finite_values"]="unknown"
    elif change=="identity":f["candidate_id"]="foreign"
    elif change=="frequency":f["original_frequency_row"]["frequency_hz"]=16000000000
    else:row["below_half_srf"]=True
    with pytest.raises(ValueError):m.invalid_reasons(row,f)


def test_two_recorded_native_clocks_not_confounded():
    assert m.native_footer("CPU time 12.25 sec\nWall-clock time 7.50 sec\n")==dict(
        emx_native_wallclock_seconds=7.5,emx_reported_cpu_seconds=12.25)
    assert m.native_footer("partial log\n")==dict(emx_native_wallclock_seconds=None,emx_reported_cpu_seconds=None)
    with pytest.raises(ValueError):m.native_footer("Wall-clock time 1 sec\nWall-clock time 2 sec\n")


def test_missing_cost_not_zero_or_complete_group_sum():
    rows=[]
    for value in (2.,None):
        r=dict(source="GEOMETRY_DOE",state="GDS_FAIL",failed_checks=["synthetic"],invalid_detail=None)
        r.update({k:None for k in m.COSTS});r["cadence_elapsed_seconds"]=value;rows.append(r)
    g=m.aggregate(rows)[1]["costs"]
    assert g["cadence_elapsed_seconds"]==dict(recorded_n=1,missing_n=1,observed_sum=2.,observed_mean=2.,full_original_group_sum=None)
    assert g["emx_native_wallclock_seconds"]["observed_sum"] is None


def test_wrapper_interval_has_timezone_and_cannot_be_negative():
    assert m.wrapper_interval("2026-01-01T00:00:00+00:00","2026-01-01T00:00:03+00:00")==3
    assert m.wrapper_interval(None,None) is None
    with pytest.raises(ValueError):m.wrapper_interval("2026-01-01T00:00:01+00:00","2026-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):m.wrapper_interval("2026-01-01T00:00:00","2026-01-01T00:00:03")
