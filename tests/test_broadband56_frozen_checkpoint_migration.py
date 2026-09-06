"""Synthetic migration fixtures; no production labels or simulator execution."""
import copy
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_handoff as cp
from tests.test_broadband56_checkpoint_handoff import resume_fixture
from tests.test_materialize_broadband56_v2_adaptive_checkpoint import MODULE as materializer
from tests.test_broadband56_scheduling import write


@pytest.mark.parametrize('accepted', [861, 999])
def test_first_consumer_reads_migrated_frozen_checkpoint_without_resampling(tmp_path, monkeypatch, accepted):
    root, boundary, migration_pin, backend, auth = resume_fixture(tmp_path, accepted)
    migration = cp.read(cp.bound(migration_pin))
    target = Path(migration_pin['path']).parent
    before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError('resuming a frozen checkpoint must not recompute or run tools')
    monkeypatch.setattr(materializer, '_run_bound_command', forbidden)
    checkpoint, source = materializer._checkpoint_from_prior_materializer(
        campaign_root=target, stage='PILOT_1000', checkpoint_count=100,
        backend_sha=backend['sha256'], authorization_sha=auth['sha256'])
    assert checkpoint.is_relative_to(target) and not checkpoint.is_symlink()
    materializer._validate_checkpoint(checkpoint_dir=checkpoint, expected_accepted=100)
    assert all(v['exists'] is True for v in cp.read(checkpoint/'CHECKPOINT_RECEIPT.json')['outputs'].values())
    assert source['materializer_receipt'] == migration['frozen_materializer_dependency']['rebound_materializer']
    assert cp.verified_resume_state(boundary, migration_pin)['current_accepted'] == accepted
    assert before == {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    original = cp.read(cp.bound(migration['frozen_materializer_dependency']['source_materializer']))
    rebound = cp.read(cp.bound(source['materializer_receipt']))
    for key in ('checkpoint_accepted','round_accepted_target','raw_selection_count'):
        assert rebound[key] == original[key]


@pytest.mark.parametrize('failure', ['source_checkpoint', 'source_materializer', 'copied_output',
    'rebound_science', 'rebound_sampling', 'backend', 'authorization', 'missing_binding', 'source_escape',
    'removed_marker'])
def test_startup_rejects_dependency_corruption_before_first_consumer(tmp_path, failure):
    _, boundary, migration_pin, backend, auth = resume_fixture(tmp_path, 861)
    migration = cp.read(cp.bound(migration_pin))
    dependency = migration['frozen_materializer_dependency']
    target = Path(migration_pin['path']).parent
    rebound_path = cp.bound(dependency['rebound_materializer'])
    rebound = cp.read(rebound_path)
    checkpoint_path = cp.bound(dependency['rebound_checkpoint'])
    checkpoint = cp.read(checkpoint_path)
    if failure == 'source_checkpoint':
        Path(dependency['source_checkpoint']['path']).write_text('{}')
    elif failure == 'source_materializer':
        Path(dependency['source_materializer']['path']).write_text('{}')
    elif failure == 'copied_output':
        Path(checkpoint['outputs']['coverage_cells']['path']).write_text('changed')
    elif failure == 'rebound_science':
        checkpoint['inputs']['contract'] = checkpoint['inputs']['geometry_bounds']
        write(checkpoint_path, checkpoint)
        dependency['rebound_checkpoint'] = cp.pin(checkpoint_path)
        rebound['checkpoint_receipt'] = dependency['rebound_checkpoint']
    elif failure == 'rebound_sampling':
        rebound['raw_selection_count'] -= 1
    elif failure == 'backend':
        dependency['target_backend'] = dependency['source_backend']
    elif failure == 'authorization':
        dependency['target_authorization'] = dependency['source_authorization']
    elif failure == 'missing_binding':
        migration.pop('frozen_materializer_dependency')
    elif failure == 'source_escape':
        dependency['source_terminal_receipt'] = dependency['source_materializer']
    elif failure == 'removed_marker':
        rebound.pop('operational_checkpoint_rebind')
    write(rebound_path, rebound)
    dependency['rebound_materializer'] = cp.pin(rebound_path)
    write(Path(migration_pin['path']), migration)
    with pytest.raises((ValueError, KeyError, FileNotFoundError)):
        cp.verified_resume_state(boundary, cp.pin(migration_pin['path']))
        materializer._checkpoint_from_prior_materializer(campaign_root=target, stage='PILOT_1000',
            checkpoint_count=100, backend_sha=backend['sha256'], authorization_sha=auth['sha256'])


def test_original_consumer_still_rejects_missing_prior_materializer(tmp_path):
    target = tmp_path/'empty'
    (target/'stages').mkdir(parents=True)
    with pytest.raises(materializer.AdaptiveCheckpointError, match='no prior materializer'):
        materializer._checkpoint_from_prior_materializer(campaign_root=target, stage='PILOT_1000',
            checkpoint_count=100, backend_sha='a'*64, authorization_sha='b'*64)


def test_rebind_accepts_only_declared_output_path_changes(tmp_path):
    _, boundary, migration_pin, backend, auth = resume_fixture(tmp_path, 861)
    migration = cp.read(cp.bound(migration_pin))
    dependency = copy.deepcopy(migration['frozen_materializer_dependency'])
    dependency['accepted_increment'] = 1
    with pytest.raises(ValueError, match='authority differs'):
        cp.validate_frozen_materializer_dependency(cp.read(cp.bound(boundary)), dependency,
            target_root=Path(migration_pin['path']).parent, target_backend=backend, target_authorization=auth)


@pytest.mark.parametrize('metadata', [{'exists': False}, {'extra': True}, {'sha256': '0'*64}])
def test_extended_output_record_keeps_strict_identity(tmp_path, metadata):
    path = tmp_path/'data.csv'
    path.write_text('synthetic test only')
    with pytest.raises(ValueError):
        cp.checkpoint_output_pin(dict(cp.pin(path), **metadata))
