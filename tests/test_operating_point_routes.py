"""Changed routes only. Synthetic fixtures are not scientific EM results."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
from research.broadband56_nn import operating_point15 as research
from rfic_transformer_inverse_design.analysis import operating_point15 as common
from research.broadband56_nn.frequency_profile import frequency_mask
from research.broadband56_nn.frequency_evaluation import _frequency
from rfic_transformer_inverse_design.synthesis.q_sweep import _operating_point_backend_gate

def fixture():
    return dict(candidate_id='SOFTWARE_TEST', frequency_hz=15e9,
        physical15=dict(lp_nh=3.,ls_nh=.3,qmin=5.,k_abs=.4),descriptor_valid=True,
        strict_valid=False,below_half_srf=False,s_z_roundtrip_abs_max=1e-12,
        passivity_sigma_max=.99,reciprocity_abs_max=0.)

def test_one_policy_implementation_and_no_old_y_mask_leak():
    assert research.classify is common.classify
    b=SimpleNamespace(manifest={'label_policy':common.LABEL_POLICY},arrays=dict(frequency_hz=np.array([15e9]),
        y=np.ones((2,1,4)),y_valid=np.zeros((2,1,4),bool),strict_lumped_valid=np.zeros((2,1),bool),
        operating_point_valid=np.array([[True],[False]])))
    assert frequency_mask(b,15,'OPERATING_POINT_15GHZ').tolist()==[True,False]

def test_gui_uses_numerical_and_bound_physical_evidence_not_srf():
    row=dict(candidate_id='SOFTWARE_TEST',geometry_sha256='g',operating_point_evidence=fixture(),
        physical_gate_evidence=dict(geometry_sha256='g',gds_sha256='d',s4p_sha256='s',current_structure_compatible=True,
            drc_zero_blocking=True,original_GDS_DRC_EMX_binding=True))
    artifacts=dict(gds_sha256='d',s4p_sha256='s')
    assert _operating_point_backend_gate(row,np.array([3.,.3,5.,.4]),artifacts)['operating_point_valid']
    row['operating_point_evidence'].pop('below_half_srf');row['operating_point_evidence'].pop('strict_valid')
    assert _operating_point_backend_gate(row,np.array([3.,.3,5.,.4]),artifacts)['operating_point_valid']
    row['physical_gate_evidence']['current_structure_compatible']=False
    with pytest.raises(ValueError,match='BINDING'):_operating_point_backend_gate(row,np.array([3.,.3,5.,.4]),artifacts)

def test_gui_negative_nonfinite_missing_qa_still_rejected():
    for value in (-1.,float('nan')):
        row=dict(candidate_id='SOFTWARE_TEST',geometry_sha256='g',operating_point_evidence=fixture())
        row['operating_point_evidence']['physical15']['lp_nh']=value
        with pytest.raises(ValueError):_operating_point_backend_gate(row,np.array([value,.3,5.,.4]),{})
    with pytest.raises(ValueError,match='MISSING_OPERATING_POINT'):_operating_point_backend_gate({'candidate_id':'x'},np.ones(4),{})

def test_new_qscan_and_evaluation_route_without_generating_requests(tmp_path):
    from research.broadband56_nn.frequency_qscan import prepare
    _frequency(15,'OPERATING_POINT_15GHZ')
    with pytest.raises(ValueError):_frequency(14,'OPERATING_POINT_15GHZ')
    config=dict(schema='frequency_qscan_request.v1',allow_extrapolation=False,random_count=10000,holdout_count=100,
        batch_requests=8,frequency_ghz=15,label_mode='OPERATING_POINT_15GHZ',label_policy=common.LABEL_POLICY)
    with patch('research.broadband56_nn.frequency_qscan._load_context',side_effect=RuntimeError('REACHED_MODEL_BINDING')):
        with pytest.raises(RuntimeError,match='REACHED_MODEL_BINDING'):prepare(config,tmp_path/'not_created')
    assert not (tmp_path/'not_created').exists()
    config['label_mode']='STRICT_LUMPED'
    with pytest.raises(ValueError,match='operating_point'):prepare(config,tmp_path/'legacy_not_created')

def test_new_inverse_statistics_no_srf_with_original_denominator():
    from research.broadband56_nn.frequency_physical_statistics import operating_point_rows
    f=fixture();f.update(frequency_ghz=15,q_proxy=12,actual_fresh_emx=[3.,.3,5.,.4],target=[3.,.3,5.,.4],
        proxy_self=[3.,.3,5.,.4],absolute_hit_tolerances=[.1,.1,1.,.1])
    x=operating_point_rows([dict(request_id='a',candidate_id='SOFTWARE_TEST',q_proxy=12,status='SOLVED',feature=f),
        dict(request_id='b',q_proxy=13,status='FAILED',feature=None)],analysis_kind='PREDECLARED_OPERATING_POINT')
    assert x['operating_point_valid_count']==1 and x['full_denominator_joint_hit_fraction']==.5
