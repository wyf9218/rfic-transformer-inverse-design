"""Synthetic concurrency fixtures using the actual permit producer/consumer.

These are not resource samples, simulator results, or production permits.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D/'runtime'))
import fixed48_runtime as f

HOT=Path(os.environ['HOTPATH_TEST_PATH'])
spec=importlib.util.spec_from_file_location('hotpath_fixtures',HOT)
hot=importlib.util.module_from_spec(spec);spec.loader.exec_module(hot)


class ReleaseTests(hot.HotPathTests):
    def setUp(self):
        super().setUp()
        # The old hot-path fixture predates the deployed ensure_plan call.
        self.owner.ensure_plan=lambda resource:None

    def prepare_permit(self):
        self.manager.acquire(self.candidate,'emx')
        self.assertTrue(self.manager.storage.claim(self.candidate.name,0,0,64))
        return self.manager.permits[self.candidate.name]

    def invoke(self,action):
        done=threading.Event();errors=[]
        def run():
            try:action()
            except BaseException as error:errors.append(error)
            finally:done.set()
        thread=threading.Thread(target=run);thread.start()
        return thread,done,errors

    def test_old_release_reproduces_block_behind_dispatch(self):
        self.prepare_permit()
        source=Path(os.environ['OLD_RELEASE_RUNTIME'])
        spec=importlib.util.spec_from_file_location('old_release',source)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        with self.manager.dispatch_lock:
            thread,done,errors=self.invoke(lambda:old.Manager.release(self.manager,self.candidate.name))
            self.assertFalse(done.wait(.15))
            self.assertIn(self.candidate.name,self.manager.permits)
        thread.join(2)
        self.assertTrue(done.is_set());self.assertFalse(errors)

    def test_completed_release_does_not_wait_for_dispatch_and_preserves_storage(self):
        permit=self.prepare_permit()
        with self.manager.dispatch_lock:
            thread,done,errors=self.invoke(lambda:self.manager.release(self.candidate.name))
            self.assertTrue(done.wait(2),'release queued behind expensive dispatch checks')
        thread.join(2);self.assertFalse(errors)
        self.assertEqual(self.manager.permits,{})
        marker=json.loads((self.candidate/'ACTIVE_TOOL_PERMIT.json').read_text())
        self.assertFalse(marker['active'])
        self.assertEqual(f.e.document(marker['release'])['issued'],permit)
        self.assertEqual(set(self.manager.storage.claims),{self.candidate.name})

    def test_slow_release_io_keeps_count_but_not_state_lock(self):
        self.prepare_permit();entered=threading.Event();proceed=threading.Event()
        original=f.pointer
        def delayed(path,value):
            if value.get('active') is False:
                entered.set()
                if not proceed.wait(3):raise AssertionError('fixture timeout')
            return original(path,value)
        with patch.object(f,'pointer',side_effect=delayed):
            thread,done,errors=self.invoke(lambda:self.manager.release(self.candidate.name))
            try:
                self.assertTrue(entered.wait(1))
                # Resource accounting remains responsive and conservative during fsync.
                reader,read_done,read_errors=self.invoke(self.manager.resource_decision)
                self.assertTrue(read_done.wait(1));reader.join(1);self.assertFalse(read_errors)
                running,pending=self.manager.counts()
                self.assertEqual(running['emx']+pending['emx'],1)
            finally:proceed.set()
            thread.join(2);self.assertTrue(done.is_set());self.assertFalse(errors)

    def test_release_persistence_failure_retains_reservation(self):
        self.prepare_permit()
        old=(self.candidate/'ACTIVE_TOOL_PERMIT.json').read_bytes()
        with patch.object(f,'pointer',side_effect=OSError('synthetic fsync error')):
            with self.assertRaisesRegex(OSError,'synthetic'):
                self.manager.release(self.candidate.name)
        self.assertIn(self.candidate.name,self.manager.permits)
        self.assertEqual((self.candidate/'ACTIVE_TOOL_PERMIT.json').read_bytes(),old)
        self.assertIn(self.candidate.name,self.manager.storage.claims)
        self.assertIn('TOOL_PERMIT_RELEASE_PERSISTENCE',self.manager.fault)
        with self.assertRaisesRegex(RuntimeError,'SHARED_RESOURCE_FAULT'):
            self.manager.require_wait_ready()

    def test_concurrent_duplicate_release_is_idempotent(self):
        self.prepare_permit()
        calls=[self.invoke(lambda:self.manager.release(self.candidate.name)) for _ in range(8)]
        for thread,done,errors in calls:
            thread.join(2);self.assertTrue(done.is_set());self.assertFalse(errors)
        self.assertEqual(len(list(self.candidate.glob('PERMIT_RELEASED_*.json'))),1)
        self.assertEqual(self.manager.permits,{})

    def test_reacquire_and_release_share_candidate_guard(self):
        first=self.prepare_permit();self.manager.release(self.candidate.name)
        self.manager.acquire(self.candidate,'calibre')
        second=self.manager.permits[self.candidate.name]
        self.assertNotEqual(first,second);self.assertEqual(second['tool'],'calibre')
        latest=f.e.document(f.e.document(f.e.pin(self.candidate/'ACTIVE_TOOL_PERMIT.json')))
        self.assertEqual(latest,second)
        self.manager.release(self.candidate.name)
        self.assertEqual(len(list(self.candidate.glob('PERMIT_RELEASED_*.json'))),2)


if __name__=='__main__':unittest.main(verbosity=2)
