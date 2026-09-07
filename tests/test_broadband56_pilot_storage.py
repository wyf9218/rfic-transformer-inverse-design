"""Synthetic filesystem tests; these are not production resource observations."""
import importlib.util
from pathlib import Path

import pytest


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ROOT = Path(__file__).resolve().parents[1]
storage = module(ROOT/'scripts/materialize_broadband56_pilot_storage.py', 'pilot_storage_test')
controller = module(ROOT/'scripts/run_broadband56_v2_authorized_queue_controller.py', 'pilot_controller_test')


def test_existing_consumer_missing_summary_reproduces_failure(tmp_path):
    value = controller._pilot_bytes_per_geometry(tmp_path)
    assert value is None
    with pytest.raises(ValueError, match='measured_pilot_bytes_per_geometry must be numeric'):
        storage.required_storage_bytes(stage='PHASE_A', current_accepted=1000,
                                       measured_pilot_bytes_per_geometry=value)


def test_actual_inventory_serialization_existing_consumer_and_policy(tmp_path):
    root = tmp_path/'artifacts'
    root.mkdir()
    (root/'raw').write_bytes(b'x'*5000)
    measured = storage.inventory({root})
    assert measured == storage.inventory({root})
    per_geometry = measured['total_charged_bytes']/1000
    assert per_geometry > 0
    storage.write(tmp_path/'PILOT_1000_RESOURCE_SUMMARY.json', dict(bytes_per_geometry=per_geometry))
    value = controller._pilot_bytes_per_geometry(tmp_path)
    assert value == per_geometry
    assert storage.required_storage_bytes(stage='PHASE_A', current_accepted=1000,
        measured_pilot_bytes_per_geometry=value) >= measured['total_charged_bytes'] * 199


def test_hardlinks_count_storage_once(tmp_path):
    root = tmp_path/'a'
    root.mkdir()
    original = root/'raw'
    original.write_bytes(b'physical fixture')
    (root/'link').hardlink_to(original)
    result = storage.inventory({root})
    assert result['roots'][0]['regular_files'] == 1


@pytest.mark.parametrize('directory', [False, True])
def test_environment_links_count_inode_without_following_target(tmp_path, directory):
    root = tmp_path/'root'
    root.mkdir()
    target = tmp_path/'target'
    if directory:
        target.mkdir()
        (target/'external').write_bytes(b'x'*1000000)
    else:
        target.write_bytes(b'x'*1000000)
    (root/'link').symlink_to(target)
    result = storage.inventory({root})
    assert result['roots'][0]['symlinks_not_followed'] == 1
    assert result['roots'][0]['regular_files'] == 0
    assert result['total_charged_bytes'] < 1000000
    with pytest.raises(ValueError, match='non-symlink'):
        storage.pin(root/'link')


def test_missing_root_fails(tmp_path):
    with pytest.raises(ValueError, match='missing'):
        storage.inventory({tmp_path/'missing'})


def test_file_drift_and_no_clobber(tmp_path):
    path = tmp_path/'data.json'
    storage.write(path, dict(fixture=True))
    record = storage.pin(path)
    with pytest.raises(FileExistsError):
        storage.write(path, dict(fixture=False))
    assert storage.pin(path) == record
    path.write_text('changed')
    with pytest.raises(ValueError, match='identity'):
        storage.bound(record)


def test_stage_scope(tmp_path):
    path = tmp_path/(storage.CAMPAIGN_ID+'_fixture')/'stages/000001_pilot_1000/backend/raw'
    assert storage.stage_root(path) == path.parents[1]
    with pytest.raises(ValueError, match='outside'):
        storage.stage_root(tmp_path/'other_campaign/stages/000001/backend/raw')


def test_wrong_pilot_sha_rejected_before_validation(tmp_path):
    path = tmp_path/'STAGE_RECEIPT.json'
    storage.write(path, {})
    with pytest.raises(ValueError, match='SHA'):
        storage.prepare(path, '0'*64)
