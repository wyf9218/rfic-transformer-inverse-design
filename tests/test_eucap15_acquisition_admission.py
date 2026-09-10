"""Small synthetic admission boundaries; no real data or native/model calls."""
import copy

import pytest

from research.broadband56_nn import eucap15_acquisition_admission as a


def one(cid='new', geometry=None):
    g = geometry or [200., 210., 220., 230., 5., 40., 45., 10., 150., 155.]
    p = dict(candidate_id=cid, request_id=cid, arm='GEOMETRY_DOE_CONTROL', arm_order=1,
             global_order=1, source='GEOMETRY_DOE', q_proxy=None, geometry_fields=list(a.FIELDS),
             geometry_units='um', geometry=g, canonical_geometry_sha256=a.canonical(g),
             target=None, proxy=None, sparse_cell=None)
    row = {k:p[k] for k in ('candidate_id','request_id','arm','arm_order','global_order','source','q_proxy')}
    row.update(geometry_sha256=p['canonical_geometry_sha256'], target=None, frozen_proxy=None,
               actual=[1.,1.,15.,.5], strict_valid=True,core_eligible=True,state='STRICT_VALID',
               descriptor_valid=True,physics_qa_pass=True,below_half_srf=True)
    return row, p


def classify(rows, proposals, current=None, known=None, reserved=None, previous=None, grid=None):
    return a.classify_rows(rows,proposals,current or {},known or {},reserved or {},previous or {},grid or {})


def test_unique_member_keeps_fixed_split_and_doe_null():
    r,p=one(); v=classify([r],{p['candidate_id']:p})[0]
    assert v['admitted_research_increment'] is True
    assert v['fixed_split']==a.SPLITS[a.data._split_for_hash(r['geometry_sha256'],17)]
    assert v['target_cell'] is v['predicted_cell'] is None
    assert v['actual_cell'] is not None


@pytest.mark.parametrize('pool',['current','known','reserved','previous'])
def test_whole_pool_or_reserved_overlap_not_new(pool):
    r,p=one(); h=r['geometry_sha256']
    value={'split':a.SPLITS[a.data._split_for_hash(h,17)]} if pool=='current' else {'source_group':'FORMAL_BASE_20973'} if pool=='known' else ['frozen']
    v=classify([r],{p['candidate_id']:p},**{pool:{h:value}})[0]
    assert not v['admitted_research_increment'] and v['conflicts']


def test_same_nominal_grid_not_falsely_novel():
    r,p=one(); g=a.nominal_grid_hash(p['geometry'])
    v=classify([r],{p['candidate_id']:p},grid={'current6329':{g:['different_raw_canonical']}})[0]
    assert not v['admitted_research_increment']
    assert 'GRID_EQUIVALENCE_REVIEW' in v['conflicts']


def test_duplicate_group_rejected_symmetrically():
    r,p=one(); s,q=one('second'); q['global_order']=s['global_order']=2
    v=classify([r,s],{'new':p,'second':q})
    assert all(not x['admitted_research_increment'] for x in v)
    assert all('DUPLICATE_WITHIN_NEW120_ALL_MEMBERS_EXCLUDED' in x['conflicts'] for x in v)


def test_baseline_never_converts_holdout_labels():
    row={'geometry_sha256':'a','frequency_hz':str(a.FREQUENCY),'strict_lumped_valid':'true',
         'core_eligible':'true','lp_nh':'1','ls_nh':'1','qmin':'15','k_abs':'.5'}
    held={**row,'geometry_sha256':'b',**{k:'DO_NOT_CONVERT_TEST_LABEL' for k in a.FEATURES}}
    before,values=a.baseline_coverage([row,held],{'a':'train','b':'test'})
    assert len(values)==1 and sum(r['N_strict_core_all_q'] for r in before)==1


def test_non_strict_finite_descriptors_retained_not_admitted():
    r,p=one(); r.update(strict_valid=False,core_eligible=False,state='EMX_INVALID',below_half_srf=False)
    v=classify([r],{'new':p})[0]
    assert v['original']['actual']==[1.,1.,15.,.5]
    assert not v['admitted_research_increment'] and not v['coverage_feedback']
    assert v['actual_cell_interpretation']=='DESCRIPTOR_ONLY_NOT_COVERAGE'


def test_target_cell_drift_rejected():
    r,p=one(); p.update(source='SPARSE_TARGETED',q_proxy=15,target=[1.,1.,15.,.5],proxy=[1.,1.,15.,.5],sparse_cell=[0,0,0])
    r.update(source=p['source'],q_proxy=15,target=p['target'],frozen_proxy=p['proxy'])
    with pytest.raises(ValueError,match='sparse target cell'):
        classify([r],{'new':p})
