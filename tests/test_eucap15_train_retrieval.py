"""Small synthetic API checks only: no real data, weights, models or native calls."""
from copy import deepcopy
import math

import numpy as np
import pytest

from research.broadband56_nn import eucap15_train_retrieval as retrieval


SPANS = (2.5, 2.5, 20.0, 0.8)


def brute_force(values, identities, targets):
    chosen, scores = [], []
    for target in targets:
        candidates = []
        for index, row in enumerate(values):
            score = math.sqrt(sum(((float(x)-float(y))/s)**2
                                  for x, y, s in zip(row, target, SPANS))/4)
            candidates.append((score, identities[index], index))
        score, _, index = min(candidates)
        chosen.append(index); scores.append(score)
    return chosen, scores


def synthetic_rows():
    fields = ['g'+str(i) for i in range(10)]
    contract = dict(field_names=fields, lower=[0.0]*10, upper=[10.0]*10)
    roles = ['train', 'validation', 'train', 'test']
    rows = []
    for index, role in enumerate(roles):
        row = dict(geometry_sha256=f'{index+1:064x}', assigned_development_split=role,
                   view_row=str(index), lp_nh='1.1', ls_nh='1.2', qmin='11', k_abs='0.3',
                   qp='11', qs='12', frequency_hz='15000000000', strict_lumped_valid='true',
                   below_half_srf='true', label_source_path='SYNTHETIC_NOT_READ.csv',
                   label_source_sha256='a'*64, label_source_row_1based_including_header=str(index+2))
        row.update({'geom__'+field: str(1+index/10) for field in fields})
        rows.append(row)
    splits = dict(by_geometry_sha256={r['geometry_sha256']: r['assigned_development_split'] for r in rows},
                  ids={role: [r['geometry_sha256'] for r in rows if r['assigned_development_split']==role]
                       for role in ('train', 'validation', 'test')})
    return rows, splits, contract


def test_nearest_matches_independent_scalar_brute_force_and_original_indices():
    values = [[.7, 1.8, 8., .22], [1.4, .9, 15., .65], [1.1, 1.3, 11., .4]]
    identities = ['c'*64, 'a'*64, 'b'*64]
    targets = [[.9, 1.7, 10., .3], [1.3, .8, 19., .7], [1.1, 1.3, 11., .4]]
    expected_indices, expected_scores = brute_force(values, identities, targets)
    indices, scores = retrieval.nearest(values, identities, targets)
    np.testing.assert_array_equal(indices, expected_indices)
    np.testing.assert_allclose(scores, expected_scores, rtol=1e-14, atol=1e-15)
    np.testing.assert_array_equal(retrieval.SPANS, SPANS)


def test_exact_ties_select_lowest_geometry_hash_not_first_input():
    target = [1.0, 1.0, 10.0, .3]
    # Exact equal distances, different stored geometries; never re-rank by input position.
    values = [[1., 1., 12., .3], [1., 1., 8., .3], [1., 1., 15., .3]]
    identities = ['f'*64, '1'*64, '0'*64]
    for order in ([0, 1, 2], [1, 2, 0], [2, 0, 1]):
        source = [values[j] for j in order]; names = [identities[j] for j in order]
        indices, scores = retrieval.nearest(source, names, [target])
        assert names[int(indices[0])] == '1'*64
        assert scores[0] == pytest.approx(.05, abs=1e-15)


def test_block_sizes_are_identical_across_multiple_blocks():
    values = [[.6, .8, 7., .2], [1., 1.1, 12., .4], [1.8, 1.7, 20., .8]]
    targets = [[.5+i/50, .7+i/60, 8.+i/5, .2+i/120] for i in range(67)]
    baseline = retrieval.nearest(values, ['c', 'a', 'b'], targets, block_size=1)
    for size in (7, 32):
        result = retrieval.nearest(values, ['c', 'a', 'b'], targets, block_size=size)
        np.testing.assert_array_equal(result[0], baseline[0])
        np.testing.assert_array_equal(result[1], baseline[1])


@pytest.mark.parametrize('change', ['nan_library', 'infinite_target', 'duplicate_id', 'wrong_dimensions', 'empty_library'])
def test_nearest_rejects_invalid_library_or_queries(change):
    values = [[1., 1., 10., .3], [1.1, 1.2, 11., .4]]
    ids = ['a', 'b']; targets = [[1., 1., 10., .3]]
    if change == 'nan_library': values[0][0] = float('nan')
    elif change == 'infinite_target': targets[0][2] = float('inf')
    elif change == 'duplicate_id': ids[1] = ids[0]
    elif change == 'wrong_dimensions': targets = [[1., 1., 10.]]
    else: values = []; ids = []
    with pytest.raises(ValueError): retrieval.nearest(values, ids, targets)


@pytest.mark.parametrize('block', [0, 33, True])
def test_nearest_rejects_unbounded_or_noninteger_block(block):
    with pytest.raises(ValueError, match='bounded block_size'):
        retrieval.nearest([[1., 1., 10., .3]], ['a'], [[1., 1., 11., .3]], block_size=block)


def test_q_choices_exact_ties_choose_lower_q_column():
    scores = np.ones((3, 11))
    scores[1, [4, 7]] = .1
    scores[2, 10] = 0
    choices = retrieval.q_choices(scores)
    np.testing.assert_array_equal(choices, [0, 4, 10])  # Q10, Q14, Q20


@pytest.mark.parametrize('scores', [np.ones((2, 10)), [[0.0]*10+[float('nan')]]])
def test_q_choices_reject_missing_or_nonfinite_candidate(scores):
    with pytest.raises(ValueError, match='complete eleven finite'):
        retrieval.q_choices(scores)


def test_train_only_library_does_not_convert_bad_nontrain_labels_or_geometry():
    rows, splits, contract = synthetic_rows()
    for row in rows:
        if row['assigned_development_split'] != 'train':
            for key in ('lp_nh', 'ls_nh', 'qmin', 'k_abs', 'qp', 'qs',
                        'frequency_hz', 'strict_lumped_valid', 'below_half_srf'):
                row[key] = 'INTENTIONALLY_NOT_NUMERIC_OR_PHYSICAL'
            for field in contract['field_names']: row['geom__'+field] = 'DO_NOT_CONVERT'
    before = deepcopy((rows, splits, contract))
    library, metadata, counts = retrieval.train_library(iter(rows), splits, contract)
    assert len(library) == 2 and dict(counts) == {'train': 2, 'validation': 1, 'test': 1}
    assert list(metadata) == [r['geometry_sha256'] for r in rows]
    assert [r['geometry_sha256'] for r in library] == splits['ids']['train']
    assert library[0]['values'] == [1.1, 1.2, 11.0, .3]
    assert library[0]['geometry_um'] == [1.0]*10
    assert library[0]['label_source_path'] == 'SYNTHETIC_NOT_READ.csv'
    assert library[0]['label_source_row'] == '2'
    assert (rows, splits, contract) == before


def test_train_rows_reject_invalid_numeric_strict_frequency_q_and_bounds():
    changes = [('lp_nh', 'bad'), ('k_abs', 'nan'), ('ls_nh', '2.1'), ('k_abs', '.1'),
               ('frequency_hz', '14000000000'), ('strict_lumped_valid', 'false'),
               ('below_half_srf', 'false'), ('qmin', '12'), ('geom__g0', '11')]
    for key, value in changes:
        rows, splits, contract = synthetic_rows(); rows[0][key] = value
        with pytest.raises(ValueError): retrieval.train_library(rows, splits, contract)


def test_split_overlap_rejected_before_any_physical_conversion():
    rows, splits, contract = synthetic_rows()
    splits['ids']['validation'].append(splits['ids']['train'][0])
    rows[0]['lp_nh'] = 'BAD_BUT_SPLIT_GATE_MUST_FAIL_FIRST'
    with pytest.raises(ValueError, match='overlapping/duplicate split'):
        retrieval.train_library(rows, splits, contract)


def test_source_split_duplicate_missing_and_role_mismatch_fail_closed():
    for change in ('duplicate_source', 'missing_source', 'wrong_role', 'duplicate_split'):
        rows, splits, contract = synthetic_rows()
        if change == 'duplicate_source': rows.append(deepcopy(rows[0]))
        elif change == 'missing_source': rows.pop()
        elif change == 'wrong_role': splits['by_geometry_sha256'][rows[0]['geometry_sha256']] = 'test'
        else: splits['ids']['train'].append(splits['ids']['train'][0])
        with pytest.raises(ValueError): retrieval.train_library(rows, splits, contract)
