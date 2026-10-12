import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('handover_assets', Path(__file__).resolve().parents[1]/'tools/handover/assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def test_member_stays_within_root(tmp_path):
    assert assets.member(tmp_path,'models/model.json')==tmp_path/'models/model.json'
    for bad in ('../outside','/tmp/outside'):
        with pytest.raises(ValueError):assets.member(tmp_path,bad)


def test_member_rejects_symlink(tmp_path):
    (tmp_path/'link').symlink_to(tmp_path/'model.json')
    with pytest.raises(ValueError):assets.member(tmp_path,'link')


def test_digest_exact_bytes(tmp_path):
    p=tmp_path/'sample';p.write_bytes(b'abc')
    assert assets.digest(p)=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
