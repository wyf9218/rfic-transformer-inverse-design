"""Only missing redundant wrapper-argv observation; synthetic, never native.

Imports a fixture class, not an old test collection. Re-pinned in-memory
metadata exercises the real _closed_birth function without physics or models.
"""
from copy import deepcopy
import socket
import subprocess

import pytest

from research.broadband56_nn import eucap15_controlled_evidence as evidence
from tests.test_eucap15_controlled_birth_state import BirthCase


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Synthetic metadata branch forbids native/network/model/physics')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(evidence, 'inspect_feature_chain', forbidden)
    monkeypatch.setattr(evidence.frame, 'load_context', forbidden)


def missing_wrapper_case():
    f = BirthCase()
    f.birth['process']['ppid'] = 122
    f.birth['ancestor']['argv'] = []
    f.birth['ancestor']['state'] = 'R'
    wrapper = deepcopy(f.birth['ancestor'])
    wrapper.update(argv=['/bin/bash', *f.command], state='S')
    intermediate = dict(pid=122, ppid=123, start_ticks=456, uid=789,
                        state='S', argv=['Singularity runtime parent'])
    f.birth['ancestry'] = [deepcopy(f.birth['process']), intermediate, wrapper]
    return f


def test_empty_direct_wrapper_uses_complete_same_birth_without_filling_observation():
    f = missing_wrapper_case()
    before = deepcopy(f.birth)
    row = f.inspect()
    assert row['direct_wrapper_argv_observation'] == 'MISSING'
    assert row['wrapper_identity_source'] == 'COMPLETE_SAME_BIRTH_ANCESTRY'
    assert row['native_birth_identity_verified'] is True
    assert row['native_count_in_this_result'] == 1
    assert row['solver_start_verified'] is False
    assert row['solver_start_order'] is row['solver_started_utc'] is None
    assert f.birth == before and f.birth['ancestor']['argv'] == []


@pytest.mark.parametrize('location', ['process', 'chain_native', 'chain_intermediate', 'chain_wrapper'])
def test_empty_required_native_or_ancestry_argv_is_not_missing_direct_observation(location):
    f = missing_wrapper_case()
    p = f.birth['process'] if location == 'process' else f.birth['ancestry'][
        {'chain_native': 0, 'chain_intermediate': 1, 'chain_wrapper': 2}[location]]
    p['argv'] = []
    with pytest.raises(ValueError, match='Malformed process identity'):
        f.inspect()


@pytest.mark.parametrize('field', ['pid', 'start_ticks', 'uid', 'ppid'])
def test_missing_direct_argv_never_hides_stable_wrapper_identity_drift(field):
    f = missing_wrapper_case()
    f.birth['ancestor'][field] += 1
    with pytest.raises(ValueError, match='Native ancestry identity conflict'):
        f.inspect()


def test_chain_wrapper_command_must_be_exact_bash_plus_bound_command():
    f = missing_wrapper_case()
    f.birth['ancestry'][2]['argv'][0] = '/bin/sh'
    with pytest.raises(ValueError, match='lacks exact same-birth command evidence'):
        f.inspect()


def test_nonempty_direct_wrapper_argv_conflict_is_not_missing():
    f = missing_wrapper_case()
    f.birth['ancestor']['argv'] = ['/bin/bash', *f.command, 'foreign']
    with pytest.raises(ValueError, match='Native ancestry identity conflict'):
        f.inspect()


@pytest.mark.parametrize('location', ['ancestor', 'chain_wrapper'])
def test_missing_direct_argv_requires_both_wrapper_observations_live(location):
    f = missing_wrapper_case()
    p = f.birth['ancestor'] if location == 'ancestor' else f.birth['ancestry'][2]
    p['state'] = 'Z'
    with pytest.raises(ValueError, match='non-live process state'):
        f.inspect()


def test_absent_argv_key_is_not_explicit_empty_observation():
    f = missing_wrapper_case()
    del f.birth['ancestor']['argv']
    with pytest.raises(ValueError, match='Missing process identity or state'):
        f.inspect()


def test_same_wrapper_identity_cannot_replace_missing_parent_chain():
    f = missing_wrapper_case()
    f.birth['ancestry'][1]['ppid'] = 999
    with pytest.raises(ValueError, match='not a wrapper descendant'):
        f.inspect()
