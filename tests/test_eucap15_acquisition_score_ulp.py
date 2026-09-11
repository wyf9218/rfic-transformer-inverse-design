"""Only acquisition verify_labels' new score branch; synthetic, not physical QA."""
from copy import deepcopy
import math
import pytest
from research.broadband56_nn import eucap15_acquisition_evidence as evidence
from tests.test_eucap15_acquisition_evidence import labels_fixture,csv_bytes


@pytest.mark.parametrize('strict,direction',[(True,-math.inf),(True,math.inf),(False,math.inf)])
def test_adjacent_derived_score_only_preserves_original_flags_and_residuals(strict,direction):
    proposal,feature,rows=labels_fixture(actual=[1.02,1.51,13.2,.51],half_srf=strict)
    expected=feature['normalized_response_score']; saved=math.nextafter(expected,direction)
    feature['normalized_response_score']=saved; before=deepcopy(feature)
    actual=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert feature==before
    assert actual['actual']==feature['actual_fresh_emx']
    assert actual['emx_minus_target']==feature['emx_minus_target']
    assert actual['strict_valid'] is strict and actual['strict_joint_hit'] is strict
    assert actual['derived_score_check']==dict(
        policy='DERIVED_SQRT_BINARY64_ADJACENT_ONE_STEP_ZERO_NULL_EXACT_V1',
        saved=saved,recomputed=expected,comparison='ONE_ULP')


@pytest.mark.parametrize('attack,match',[
    ('two_steps','normalized_response_score'),('residual','emx_minus_target'),
    ('flag','strict_lumped_valid'),('bool','normalized_response_score'),('missing','normalized_response_score')])
def test_other_fields_and_more_than_one_step_remain_exact(attack,match):
    proposal,feature,rows=labels_fixture(actual=[1.02,1.51,13.2,.51])
    expected=feature['normalized_response_score']
    feature['normalized_response_score']=math.nextafter(expected,math.inf)
    if attack=='two_steps':feature['normalized_response_score']=math.nextafter(feature['normalized_response_score'],math.inf)
    elif attack=='residual':feature['emx_minus_target'][0]=math.nextafter(feature['emx_minus_target'][0],math.inf)
    elif attack=='flag':feature['strict_lumped_valid']=1
    elif attack=='bool':feature['normalized_response_score']=True
    else:feature.pop('normalized_response_score')
    with pytest.raises(ValueError,match=match):evidence.verify_labels(feature,proposal,csv_bytes(rows))


def test_doe_null_is_exact_not_numeric_score_or_target_error():
    proposal,feature,rows=labels_fixture(source='GEOMETRY_DOE')
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['derived_score_check']['saved'] is result['derived_score_check']['recomputed'] is None
    assert result['target_errors_defined'] is False and result['emx_minus_target'] is result['strict_joint_hit'] is None
    feature['normalized_response_score']=0.0
    with pytest.raises(ValueError,match='normalized_response_score'):evidence.verify_labels(feature,proposal,csv_bytes(rows))


def test_zero_remains_exact_not_adjacent_subnormal():
    proposal,feature,rows=labels_fixture()
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['derived_score_check']['comparison']=='EXACT'
    feature['normalized_response_score']=math.nextafter(0.0,math.inf)
    with pytest.raises(ValueError,match='normalized_response_score'):evidence.verify_labels(feature,proposal,csv_bytes(rows))
