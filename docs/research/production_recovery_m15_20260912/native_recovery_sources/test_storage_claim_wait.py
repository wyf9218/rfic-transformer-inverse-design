"""Synthetic claim races through the real Manager and reservation consumer."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D/'runtime'))
import fixed48_runtime as current
from fixed48_capacity import InflightReservations
from controlled_metadata import MetadataError

class Stop:
    def __init__(self,manager,action):self.manager=manager;self.action=action;self.calls=0
    def wait(self,seconds):
        self.calls+=1
        assert seconds==2 and not self.manager.in_lock
        return self.action(self.calls)

def manager(module,action):
    value=module.Manager.__new__(module.Manager)
    value.owner=SimpleNamespace(out=Path('/unused-synthetic-output'))
    value.policy={'candidate_projected_peak_bytes':64}
    value.storage=InflightReservations(100);value.in_lock=False;value.ready_calls=0
    value.storage.claim('active',0,0,64)
    @contextmanager
    def locked():
        assert not value.in_lock;value.in_lock=True
        try:yield
        finally:value.in_lock=False
    value.dispatch_lock=locked()
    # A fresh context manager is required for every iteration.
    class Lock:
        def __enter__(self):assert not value.in_lock;value.in_lock=True
        def __exit__(self,*args):value.in_lock=False
    value.dispatch_lock=Lock()
    def ready():value.ready_calls+=1
    value.require_ready=ready
    value.storage_snapshot=lambda:{'total_allocated_bytes':0}
    value.stop=Stop(value,action)
    return value

class Checks(unittest.TestCase):
    def test_original_reproduces_storage_claim_exception(self):
        source=Path(os.environ.get('OLD_MANAGER_PATH',D.parent/'samebatch_hotpath_v1/runtime/fixed48_runtime.py'))
        spec=importlib.util.spec_from_file_location('old_manager',source)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        value=manager(old,lambda n:False)
        with patch.object(old.e,'allocated_bytes',return_value=0):
            with self.assertRaisesRegex(MetadataError,'PROJECTED_INFLIGHT_STORAGE_UNAVAILABLE'):
                with value.candidate({'request_id':'new'}):self.fail('must not enter')
        self.assertEqual(set(value.storage.claims),{'active'})

    def test_wait_releases_lock_and_preserves_claim_until_capacity_frees(self):
        def release(n):
            self.assertEqual(set(value.storage.claims),{'active'})
            if n==2:value.storage.release('active')
            return False
        value=manager(current,release)
        with patch.object(current.e,'allocated_bytes',return_value=0):
            with value.candidate({'request_id':'new'}):
                self.assertEqual(set(value.storage.claims),{'new'})
                self.assertEqual(value.stop.calls,2)
                self.assertEqual(value.ready_calls,3)
        self.assertEqual(value.storage.claims,{})

    def test_stop_during_wait_creates_no_claim(self):
        value=manager(current,lambda n:True)
        with patch.object(current.e,'allocated_bytes',return_value=0):
            with self.assertRaisesRegex(RuntimeError,'OWNER_DRAINING'):
                with value.candidate({'request_id':'new'}):self.fail('must not enter')
        self.assertEqual(set(value.storage.claims),{'active'})

    def test_deadline_or_sampler_failure_is_not_swallowed(self):
        for message in ('DEADLINE','SAMPLER_FAULT','ACTUAL_DISK_CAP'):
            value=manager(current,lambda n:False)
            def ready():
                value.ready_calls+=1
                if value.ready_calls>1:raise RuntimeError(message)
            value.require_ready=ready
            with patch.object(current.e,'allocated_bytes',return_value=0):
                with self.assertRaisesRegex(RuntimeError,message):
                    with value.candidate({'request_id':'new'}):self.fail('must not enter')
            self.assertEqual(set(value.storage.claims),{'active'})

    def test_duplicate_claim_still_rejected(self):
        value=manager(current,lambda n:False)
        with patch.object(current.e,'allocated_bytes',return_value=0):
            with self.assertRaisesRegex(ValueError,'DUPLICATE_INFLIGHT_CLAIM'):
                with value.candidate({'request_id':'active'}):self.fail('must not enter')
        self.assertEqual(value.stop.calls,0)

    def test_claim_released_after_candidate_error(self):
        value=manager(current,lambda n:False);value.storage.release('active')
        with patch.object(current.e,'allocated_bytes',return_value=0):
            with self.assertRaisesRegex(RuntimeError,'CANDIDATE_TEST_ERROR'):
                with value.candidate({'request_id':'new'}):raise RuntimeError('CANDIDATE_TEST_ERROR')
        self.assertEqual(value.storage.claims,{})

if __name__=='__main__':unittest.main()
