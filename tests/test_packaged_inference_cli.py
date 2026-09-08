"""Small stdlib boundary tests; these do not prove real model inference."""
import importlib.util
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'tools/verify_broadband56_package_inference.py'
SPEC = importlib.util.spec_from_file_location('portable_inference_cli_under_test', SOURCE)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def test_relative_reference_is_required(tmp_path):
    path = tmp_path / 'payload'
    path.write_text('unchanged')
    assert HELPER.inside(tmp_path, tmp_path, 'payload') == path
    with pytest.raises(ValueError, match='relative'):
        HELPER.inside(tmp_path, tmp_path, str(path))


def test_reference_escape_rejected(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    (tmp_path / 'outside').write_text('x')
    with pytest.raises(ValueError, match='escapes'):
        HELPER.inside(package, package, '../outside')


@pytest.mark.parametrize('placement', ['same', 'inside', 'parent'])
def test_nested_output_rejected_before_mutation(tmp_path, placement):
    package = tmp_path / 'package'
    package.mkdir()
    output = {'same': package, 'inside': package / 'out', 'parent': tmp_path}[placement]
    with pytest.raises(ValueError, match='non-nested'):
        HELPER.main(['--package', str(package), '--out', str(output),
                     '--expected-package-receipt-sha256', '0' * 64])
    assert list(package.iterdir()) == []


def test_existing_output_is_not_reused(tmp_path):
    package, output = tmp_path / 'package', tmp_path / 'used'
    package.mkdir()
    output.mkdir()
    sentinel = output / 'retained_failure.json'
    sentinel.write_text('{"status":"FAIL"}')
    with pytest.raises(FileExistsError):
        HELPER.main(['--package', str(package), '--out', str(output),
                     '--expected-package-receipt-sha256', '0' * 64])
    assert sentinel.read_text() == '{"status":"FAIL"}'
    assert list(output.iterdir()) == [sentinel]


def test_wrong_root_identity_stops_before_deserialization(tmp_path):
    (tmp_path / 'PACKAGE_RECEIPT.json').write_text('not valid JSON')
    with pytest.raises(ValueError, match='Wrong package receipt SHA'):
        HELPER.package_tree(tmp_path, '0' * 64)


def test_only_exact_denied_optional_platform_probe_is_classified():
    args = [Path('/proc/self/maps'), False, 'darwin', 'torch', '_load_global_deps']
    assert HELPER.expected_blocked_platform_probe(*args)
    for index, wrong in enumerate([Path('/outside/checkpoint.pt'), True, 'linux', 'other', 'forward']):
        candidate = list(args)
        candidate[index] = wrong
        assert not HELPER.expected_blocked_platform_probe(*candidate)
