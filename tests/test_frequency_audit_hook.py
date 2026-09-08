"""No real data/model or native process calls."""
import sys
import types
import json
from pathlib import Path

import pytest

from research.broadband56_nn.frequency_audit_hook import prepare_requested_audit


def test_absent_request_is_noop(tmp_path):
    assert prepare_requested_audit(tmp_path, {}, None) is None


def request(root):
    value = {'schema': 'frequency_pretest_audit_request.v1', 'study_id': 'synthetic',
             'dataset_scope': 'SYNTHETIC_ONLY', 'sampling_seed': 123,
             'emx_selection_seed': 456, 'random_count': 10000,
             'emx_per_panel': 100, 'batch_size': 256}
    (root / 'LARGE_EVALUATION_REQUEST.json').write_text(json.dumps(value))
    return value


def test_preparation_binds_exact_pair_not_neighbour(tmp_path, monkeypatch):
    req = request(tmp_path)
    calls = []
    module = types.ModuleType('research.broadband56_nn.frequency_large_eval')
    module.prepare = lambda config, out: calls.append((config, out)) or {'status': 'FROZEN'}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    pair = {'data_root': '/synthetic/data', 'frequency_ghz': 15, 'label_mode': 'STRICT_LUMPED',
            'roles': {r: {'best': {'path': '/synthetic/'+r+'.pt'}} for r in ('forward', 'inverse')}}
    result = prepare_requested_audit(tmp_path, pair, Path('/synthetic/profile.json'))
    assert result == {'status': 'FROZEN'} and len(calls) == 1
    config, out = calls[0]
    assert config['frequency_ghz'] == 15 and config['forward_checkpoint'] == '/synthetic/forward.pt'
    assert config['random_count'] == 10000 and config['sampling_seed'] == req['sampling_seed']
    assert out == tmp_path / 'large_scale_eval_v1' and 'reuse_evaluation_root' not in config


def test_cannot_relabel_posthoc_selection_as_prospective(tmp_path):
    request(tmp_path)
    path = tmp_path / 'posttrain/evaluation/test/EVALUATION_SUMMARY.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    with pytest.raises(ValueError, match='not frozen before test'):
        prepare_requested_audit(tmp_path, {}, None)


def test_extra_training_field_rejected(tmp_path):
    req = request(tmp_path)
    req['steps'] = 999999
    (tmp_path / 'LARGE_EVALUATION_REQUEST.json').write_text(json.dumps(req))
    with pytest.raises(ValueError, match='invalid pre-test'):
        prepare_requested_audit(tmp_path, {}, None)
