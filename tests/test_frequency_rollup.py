import pytest
from research.broadband56_nn.frequency_rollup import checked, metrics, pin, FEATURES


def test_portable_pin_requires_base_and_checks_hash(tmp_path):
    p = tmp_path/'MODEL_INDEX.json'; p.write_text('{}')
    reference = dict(path=p.name, sha256=pin(p)['sha256'])
    assert checked(reference, tmp_path)['path'] == str(p)
    with pytest.raises(ValueError, match='owning package'):
        checked(reference)
    with pytest.raises(ValueError, match='mismatch'):
        checked(dict(reference, sha256='0'*64), tmp_path)
    with pytest.raises(ValueError, match='escapes'):
        checked(dict(reference, path='../MODEL_INDEX.json'), tmp_path)


def test_saved_prediction_aggregation_no_model():
    row = {'joint_hit':'True', 'analytical_pass':'True'}
    for feature in FEATURES:
        row.update({f'target__{feature}':'2', f'prediction__{feature}':'2.01', f'error__{feature}':'.01', f'valid__{feature}':'True'})
    result, hits = metrics([row], 15, 'B_FOUR_TARGET_INVERSE', 'grid', {'sha256':'a'*64})
    assert hits == 1 and all(m['MAE'] == pytest.approx(.01) for m in result)
    assert all(m['N_requested'] == 1 and m['evidence'] == 'SELF_PROXY' for m in result)
    row['joint_hit'] = 'False'
    with pytest.raises(ValueError, match='joint-hit'):
        metrics([row], 15, 'B_FOUR_TARGET_INVERSE', 'grid', {'sha256':'a'*64})
    row['analytical_pass'] = 'False'
    result, hits = metrics([row], 15, 'B_FOUR_TARGET_INVERSE', 'grid', {'sha256':'a'*64})
    assert hits == 0 and result[0]['N_finite'] == 1
