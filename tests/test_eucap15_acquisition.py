"""Small, explicitly synthetic fixtures; no model/native/data dependencies."""
import copy
import hashlib
import itertools

import numpy as np
import pytest

from research.broadband56_nn.eucap15_acquisition import (
    PROVENANCE_SCOPE, actual_landing, coverage_gain, geometry_lhs,
    sparse_targets, validate_coverage,
)


def grid(default_n=0):
    edges = [np.linspace(.5, 2., 9), np.linspace(.5, 2., 9), np.linspace(.2, .85, 9)]
    rows = []
    for i, j, k in itertools.product(range(8), repeat=3):
        rows.append(dict(grid_n=8, split='train', frequency_hz=15000000000,
            lp_bin=i, ls_bin=j, k_bin=k,
            lp_low_nh=float(edges[0][i]), lp_high_nh=float(edges[0][i+1]),
            ls_low_nh=float(edges[1][j]), ls_high_nh=float(edges[1][j+1]),
            k_low=float(edges[2][k]), k_high=float(edges[2][k+1]),
            N_strict_core_all_q=default_n, N_strict_core_q10_20=default_n,
            N_q_below10=0, N_q10_12=default_n, N_q12_14=0,
            N_q14_16=0, N_q16_18=0, N_q18_20=0, N_q_above20=0,
            empty_all_q=default_n == 0, unobserved_is_not_proven_unreachable=True))
    return rows


def set_count(row, n):
    row.update(N_strict_core_all_q=n, N_strict_core_q10_20=n,
               N_q10_12=n, empty_all_q=n == 0)


def actual(name, *, q=12., lp=.55, ls=.55, k=.22):
    return dict(geometry_hash=hashlib.sha256(name.encode()).hexdigest(), split='train',
                frequency_hz=15000000000, strict_lumped_valid=True,
                evidence_class='FRESH_REAL_EMX', actual=[lp, ls, q, k])


def gain(rows, new, baseline=()):
    return coverage_gain(rows, new, baseline_hashes=baseline, synthetic=True)


def test_coverage_csv_roundtrip_exact_and_readonly():
    rows = grid(1)
    strings = [{key: str(value) for key, value in row.items()} for row in rows]
    saved = copy.deepcopy(strings)
    assert validate_coverage(strings) == rows
    assert strings == saved
    assert validate_coverage(rows) == rows


@pytest.mark.parametrize('key,value', [
    ('split', 'test'), ('split', 'validation'), ('frequency_hz', 15000000001),
    ('grid_n', 4), ('lp_bin', 1), ('N_strict_core_all_q', -1),
    ('N_strict_core_all_q', True), ('N_strict_core_all_q', 0.5),
    ('N_strict_core_all_q', 1.0), ('N_strict_core_all_q', 'UNKNOWN'),
    ('N_strict_core_all_q', None), ('N_strict_core_q10_20', 1),
    ('N_q_below10', 1), ('N_q10_12', 1), ('N_q_above20', 1),
    ('lp_low_nh', .5000000000000001), ('k_high', float('nan')),
    ('empty_all_q', False), ('unobserved_is_not_proven_unreachable', False),
])
def test_coverage_rejects_wrong_scope_counts_or_bounds(key, value):
    rows = grid()
    rows[0][key] = value
    with pytest.raises(ValueError):
        validate_coverage(rows)


@pytest.mark.parametrize('kind', ['missing', 'extra', 'duplicate', 'reordered', 'missing_field'])
def test_coverage_requires_complete_unique_ordered_grid(kind):
    rows = grid()
    if kind == 'missing': rows.pop()
    elif kind == 'extra': rows.append(dict(rows[-1]))
    elif kind == 'duplicate': rows[-1] = dict(rows[0])
    elif kind == 'reordered': rows[0], rows[1] = rows[1], rows[0]
    else: del rows[0]['N_q_above20']
    with pytest.raises(ValueError): validate_coverage(rows)


def test_coverage_q_tails_and_exact_internal_edges():
    rows = grid()
    rows[0].update(N_strict_core_all_q=3, N_strict_core_q10_20=1,
                   N_q10_12=1, N_q_below10=1, N_q_above20=1, empty_all_q=False)
    assert validate_coverage(rows)[0]['N_strict_core_all_q'] == 3
    rows[2]['k_low'] = round(rows[2]['k_low'], 3)
    with pytest.raises(ValueError): validate_coverage(rows)


def test_sparse_matches_prescribed_rng_sequence_and_does_not_mutate():
    rows = grid(5)
    for index, count in enumerate((0, 1, 4)): set_count(rows[index], count)
    saved = copy.deepcopy(rows)
    result = sparse_targets(rows, 3, 113)
    rng = np.random.Generator(np.random.PCG64(113))
    expected_indices = rng.choice(512, size=3, replace=False, p=np.array([.5, .4, .1] + [0.] * 509))
    for order, (item, index) in enumerate(zip(result, expected_indices)):
        row = rows[index]
        assert item['cell'] == [0, 0, int(index)]
        assert item['order'] == order
        assert item['selection_weight'] == [0.5, 0.4, 0.1][index]
        assert item['deficit'] == 5 - row['N_strict_core_all_q']
        expected_target = rng.uniform([row['lp_low_nh'], row['ls_low_nh'], row['k_low']],
                                      [row['lp_high_nh'], row['ls_high_nh'], row['k_high']])
        assert item['target'] == expected_target.tolist()
        assert actual_landing(item['target']) == tuple(item['cell'])
    assert result == sparse_targets(rows, 3, 113)
    assert rows == saved


def test_sparse_uses_all_q_not_intersection_and_keeps_empty_cells():
    rows = grid(5)
    rows[0].update(N_strict_core_q10_20=0, N_q10_12=0, N_q_below10=5)
    set_count(rows[1], 0)
    set_count(rows[2], 4)
    result = sparse_targets(rows, 2, 0)
    assert {tuple(x['cell']) for x in result} == {(0, 0, 1), (0, 0, 2)}
    assert {x['N'] for x in result} == {0, 4}
    with pytest.raises(ValueError): sparse_targets(rows, 3, 0)
    with pytest.raises(ValueError): sparse_targets(grid(5), 1, 0)


@pytest.mark.parametrize('n,seed', [(0, 1), (-1, 1), (1.1, 1), (True, 1), (1, -1), (1, 2.2)])
def test_sampling_rejects_invalid_n_seed(n, seed):
    with pytest.raises(ValueError): sparse_targets(grid(), n, seed)
    with pytest.raises(ValueError): geometry_lhs(np.zeros(10), np.ones(10), n, seed)


def test_geometry_lhs_exact_formula_and_every_stratum():
    low = np.arange(10, dtype=float)
    high = low + np.arange(1, 11)
    result = geometry_lhs(low, high, 17, 97)
    rng = np.random.Generator(np.random.PCG64(97))
    expected = np.column_stack([(rng.permutation(17) + rng.random(17))/17 for _ in range(10)])
    np.testing.assert_array_equal(result, low + (high-low)*expected)
    assert result.shape == (17, 10)
    assert (result >= low).all() and (result < high).all()
    for column in np.floor((result-low)/(high-low)*17).astype(int).T:
        assert sorted(column) == list(range(17))
    np.testing.assert_array_equal(result, geometry_lhs(low, high, 17, 97))


@pytest.mark.parametrize('lower,upper', [([0]*3, [1]*3), ([0]*10, [0]*10),
    ([0]*10, [-1]*10), ([0]*10, [float('inf')]*10), ([float('nan')]*10, [1]*10)])
def test_geometry_lhs_invalid_bounds(lower, upper):
    with pytest.raises(ValueError): geometry_lhs(lower, upper, 2, 0)


def test_landing_boundaries_no_epsilon_no_clipping():
    assert actual_landing([.5, .5, .2]) == (0, 0, 0)
    assert actual_landing([2., 2., .85]) == (7, 7, 7)
    assert actual_landing([.6875, .6875, .28125]) == (1, 1, 1)
    assert actual_landing([np.nextafter(.6875, 0), .6875, .28125]) == (0, 1, 1)
    assert actual_landing([np.nextafter(2., 3), 1., .5]) is None
    assert actual_landing([np.nextafter(.5, 0), 1., .5]) is None
    assert actual_landing([1., 1., float('nan')]) is None
    assert actual_landing([1., float('inf'), .5]) is None
    assert actual_landing([2., 2., .85], n=4) == (3, 3, 3)
    with pytest.raises(ValueError): actual_landing([1, 1, 12, .5])


def test_gain_counts_duplicate_existing_outside_all_q_and_crossing():
    rows = grid()
    set_count(rows[0], 4)
    a = actual('new_a', q=9)
    outside = actual('new_outside', lp=2.01)
    b = actual('new_b', lp=.8, ls=.8, k=.35, q=20)
    old = actual('old')
    batch = [a, dict(a), outside, b, old]
    saved_rows, saved_batch = copy.deepcopy(rows), copy.deepcopy(batch)
    result = gain(rows, batch, baseline=[old['geometry_hash']])
    assert result['counts'] == dict(input_rows=5, unique_geometry=4, duplicate_rows=1,
        existing_geometry=1, new_geometry=3, new_in_domain=2, new_out_of_domain=1)
    assert result['newly_occupied_cells'] == 1
    assert result['occupancy_fraction_gain'] == 1/512
    assert result['sparse_crossed_5'] == 1
    assert result['after_coverage'][0]['N_q_below10'] == 1
    assert sum(x['added'] for x in result['per_cell']) == 2
    assert sum(x['added_q10_20'] for x in result['per_cell']) == 1
    assert len(result['retained_unique_rows']) == 4
    assert result['retained_unique_rows'][1]['actual_cell'] is None
    assert result['provenance_scope'] == PROVENANCE_SCOPE
    assert result['evidence_scope'] == 'SYNTHETIC_ONLY'
    assert rows == saved_rows and batch == saved_batch


def test_gain_q_strata_actual_edges_are_counted_once():
    q_values = [9., 10., 12., 14., 16., 18., 20., 21.]
    result = gain(grid(), [actual(str(q), q=q) for q in q_values])
    row = result['after_coverage'][0]
    assert row['N_strict_core_all_q'] == 8
    assert row['N_strict_core_q10_20'] == 6
    assert [row[k] for k in ('N_q_below10', 'N_q10_12', 'N_q12_14', 'N_q14_16',
                             'N_q16_18', 'N_q18_20', 'N_q_above20')] == [1, 1, 1, 1, 1, 2, 1]


def test_gain_unknown_is_null_not_zero_and_empty_is_explicit():
    result = gain(grid(), None)
    assert result['status'] == 'UNKNOWN_NO_CLOSED_PUBLICATIONS_PROVIDED'
    for key in ('counts', 'per_cell', 'after_coverage', 'newly_occupied_cells',
                'occupancy_fraction_gain', 'sparse_crossed_5'):
        assert result[key] is None
    assert gain(grid(), [])['newly_occupied_cells'] == 0


def test_gain_requires_baseline_and_empty_only_explicit_synthetic():
    with pytest.raises(TypeError): coverage_gain(grid(), [])
    with pytest.raises(ValueError): coverage_gain(grid(), [], baseline_hashes=[])
    with pytest.raises(ValueError): coverage_gain(grid(), [], baseline_hashes=None, synthetic=True)
    with pytest.raises(ValueError): coverage_gain(grid(), [], baseline_hashes='a'*64, synthetic=True)
    with pytest.raises(ValueError): gain(grid(), [], baseline=['wrong'])
    result = coverage_gain(grid(), [], baseline_hashes=[actual('old')['geometry_hash']])
    assert result['evidence_scope'] == 'CALLER_DECLARED_REAL_EM_NOT_INDEPENDENTLY_VALIDATED'


@pytest.mark.parametrize('key,value', [('split', 'test'), ('split', 'validation'),
    ('frequency_hz', 14000000000), ('strict_lumped_valid', False), ('strict_lumped_valid', 1),
    ('evidence_class', 'SELF_PROXY'), ('evidence_class', 'UNKNOWN'),
    ('geometry_hash', 'abcd'), ('actual', [1, 1, float('nan'), .5]),
    ('actual', [1, 1, 12]), ('actual', [1, 1, 12, True])])
def test_gain_invalid_source_rows_fail_closed(key, value):
    row = actual('a')
    row[key] = value
    with pytest.raises(ValueError): gain(grid(), [row])


@pytest.mark.parametrize('already_existing', [False, True])
def test_conflicting_repeats_fail_even_if_baseline(already_existing):
    a, b = actual('a'), actual('a', q=13.)
    with pytest.raises(ValueError):
        gain(grid(), [a, b], baseline=[a['geometry_hash']] if already_existing else [])


def test_previously_added_round_hash_is_not_added_again():
    a, b = actual('a'), actual('b')
    first = gain(grid(), [a])
    second = gain(first['after_coverage'], [a, b], baseline=[a['geometry_hash']])
    assert second['counts']['existing_geometry'] == 1
    assert second['after_coverage'][0]['N_strict_core_all_q'] == 2


def test_global_numpy_rng_not_consumed():
    np.random.seed(32)
    expected = np.random.random(4)
    np.random.seed(32)
    sparse_targets(grid(), 2, 9)
    geometry_lhs(np.zeros(10), np.ones(10), 2, 9)
    np.testing.assert_array_equal(np.random.random(4), expected)
