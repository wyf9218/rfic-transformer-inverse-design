"""New last2 reader checks only; synthetic metadata, no native/model/old QA."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_development128_last2 as m


def rows():
    result = []
    states = ['ANALYTIC_FAIL']*35 + ['GDS_FAIL']*6 + ['EMX_INVALID']*37 + ['STRICT_VALID']*48 + ['PENDING']*2
    for i, state in enumerate(states):
        result.append(dict(request_id=f'request_{i:03}', candidate_id=f'candidate_{i:03}',
            candidate_geometry_identity_sha256='a'*64, q_proxy=10, q_emx=None,
            target=[1., 1., 10., .3], grid_proxy=[1., 1., 10., .3], state=state,
            actual=[1., 1., 10., .3] if state == 'STRICT_VALID' else None,
            strict_joint_hit=True if state == 'STRICT_VALID' else None,
            touchstone_sha='b'*64 if state == 'STRICT_VALID' else None,
            status_detail='SYNTHETIC', selected_support_status='SYNTHETIC',
            selected_outside_support_by_feature={}, original_source_record={'path': '/synthetic/record.json', 'sha256':'c'*64, 'bytes':0},
            full11_status='SYNTHETIC', native_result_pin=None if i >= 126 else
                {'path':f'/synthetic/native/request_{i:03}/RESULT.json', 'sha256':'d'*64, 'bytes':1}))
    return result


def snapshot(previous, new=(126, 127)):
    entries = [dict(request_id=r['request_id'], result=deepcopy(r['native_result_pin'])) for r in previous]
    for i in new:
        entries[i]['result'] = dict(path=f'/synthetic/native/request_{i:03}/RESULT.json', sha256='e'*64, bytes=1)
    return dict(requests=entries, batch={'unopened': True})


def encode(source):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=m.CSV_FIELDS)
    writer.writeheader()
    for row in source:
        writer.writerow({k:json.dumps(v, sort_keys=True) if isinstance(v, (dict,list,tuple)) else v for k,v in row.items()})
    return out.getvalue().encode()


def test_saved_csv_exact_roundtrip():
    previous = rows()
    assert m.parse_rows(encode(previous)) == previous


@pytest.mark.parametrize('field,value', [('q_proxy',True), ('q_proxy',10.0), ('q_proxy',21),
    ('q_emx',11), ('strict_joint_hit','true')])
def test_saved_csv_rejects_incompatible_values(field, value):
    previous = rows(); previous[0][field] = value
    with pytest.raises(ValueError):
        m.parse_rows(encode(previous))


def test_saved_csv_rejects_duplicate_id():
    previous = rows(); previous[1]['request_id'] = previous[0]['request_id']
    with pytest.raises(ValueError): m.parse_rows(encode(previous))


@pytest.mark.parametrize('new', [(126,), (127,), (126,127)])
def test_sparse_view_preserves_original_and_reads_only_new(new):
    previous=rows(); full=snapshot(previous,new); before=deepcopy(full)
    sparse, ids=m.sparse_new_view(previous, full)
    assert full == before and sparse['batch'] is None
    assert ids == [previous[i]['request_id'] for i in new]
    assert [e['request_id'] for e in sparse['requests'] if e['result']] == ids


def test_no_new_does_not_reconsume_baseline():
    previous=rows()
    with pytest.raises(ValueError,match='no new'): m.sparse_new_view(previous,snapshot(previous,()))


@pytest.mark.parametrize('mutation', ['drop','sha','path','bytes','order'])
def test_previously_accepted_result_cannot_change(mutation):
    previous=rows(); full=snapshot(previous)
    if mutation=='drop': full['requests'][40]['result']=None
    elif mutation=='order': full['requests'][40],full['requests'][41]=full['requests'][41],full['requests'][40]
    else: full['requests'][40]['result'][mutation]='f'*64 if mutation=='sha' else '/elsewhere/RESULT.json' if mutation=='path' else 2
    with pytest.raises(ValueError): m.sparse_new_view(previous,full)


def inspected_new(previous):
    checked=deepcopy(previous)
    for row in checked[:126]: row.update(native_result_pin=None,status_detail='NOT_REOPENED')
    for row in checked[126:]:
        row.update(state='GDS_FAIL',native_result_pin=dict(path='/new/RESULT.json',sha256='f'*64,bytes=1))
    return checked


def test_merge_reuses_exact126_rows():
    previous=rows(); checked=inspected_new(previous)
    merged=m.merge_rows(previous,checked,[r['request_id'] for r in previous[126:]])
    assert merged[:126] == previous[:126]
    assert merged[126:] == checked[126:]
    merged[0]['target'][0]=99
    assert previous[0]['target'][0] == 1


@pytest.mark.parametrize('field,value',[('q_proxy',11),('target',[1,1,10,.8]),
    ('candidate_id','replacement'),('candidate_geometry_identity_sha256','f'*64)])
def test_merge_rejects_changed_frozen_identity(field,value):
    previous=rows(); checked=inspected_new(previous); checked[127][field]=value
    with pytest.raises(ValueError): m.merge_rows(previous,checked,[r['request_id'] for r in previous[126:]])


def test_old_candidate_artifact_rejected_before_file_access(monkeypatch):
    previous=rows(); mirror=m.DeltaMirror([],previous)
    def forbidden(*args,**kwargs): raise AssertionError('must reject before underlying pin check')
    monkeypatch.setattr(m.existing.Mirror,'check',forbidden)
    with pytest.raises(ValueError,match='must not be re-read'):
        mirror.check(dict(path='/synthetic/native/request_040/emx/features.json',sha256='f'*64,bytes=1))


def test_existing_output_not_modified(tmp_path,monkeypatch):
    out=tmp_path/'frozen'; out.mkdir(); sentinel=out/'SENTINEL'; sentinel.write_text('untouched')
    monkeypatch.setattr(m,'load_baseline',lambda *a: pytest.fail('must not read baseline'))
    with pytest.raises(FileExistsError): m.run(Path('/missing'),Path('/missing'),out)
    assert list(out.iterdir()) == [sentinel] and sentinel.read_text()=='untouched'


def test_untrusted_baseline_pin_rejected_before_parse(tmp_path):
    fake=tmp_path/'receipt.json'; fake.write_text('{}')
    with pytest.raises(ValueError,match='independently accepted126'): m.load_baseline(fake,fake)
