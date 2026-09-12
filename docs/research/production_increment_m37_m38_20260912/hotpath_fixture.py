"""New hot-path fixtures only; no simulator, remote host, or legacy suite."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import fixed48_runtime as f
from fixed48_capacity import TOOLS


class HotPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.now=datetime(2026,9,12,6,10,tzinfo=timezone.utc)
        timer=patch.object(f,'clock',side_effect=lambda:self.now.isoformat())
        timer.start();self.addCleanup(timer.stop)
        self.policy=dict(requested_emx=48,cpu_per_solver=2,normalized_load1_max=1.10,
            normalized_load5_max=1.10,minimum_available_memory_fraction=.20,system_cpu_reserve=4,
            cpu_reservation={t:2 for t in TOOLS},memory_reservation_bytes={t:8*1024**3 for t in TOOLS},
            tool_executor_capacity=dict(cadence=1,calibre=1,emx=48),candidate_pipeline_capacity=64,
            candidate_projected_peak_bytes=64*1024**2)
        self.owner=SimpleNamespace(config=dict(fixed48_policy=self.policy,budget_root=str(self.root)),
            out=self.root,release_pin={'SYNTHETIC_TEST_ONLY':'not_a_production_release'},
            event=Mock(),deadline=Mock(),verify_release=Mock())
        with patch.object(f.native_birth,'proc_info',return_value=dict(pid=1,start_ticks=1,uid=1)):
            self.manager=f.Manager(self.owner)
        for i in range(5):
            start=self.now-timedelta(seconds=(5-i)*61)
            self.publish(start,start+timedelta(seconds=60),i)
        self.candidate=self.root/'SYNTHETIC_ONLY';self.candidate.mkdir()

    def publish(self,start,end,number):
        state=dict(binding=self.manager.binding,started_utc=start.isoformat(),utc=end.isoformat(),
            checks={k:True for k in ('cpu','memory','swap','oom','iowait','sample','storage','isolation')},
            free_license_slots=dict(cadence=1,calibre=1,emx=48),idle_cpu_equivalents=140,
            available_memory_bytes=750*1024**3,total_memory_bytes=800*1024**3,
            free_disk_bytes=200*1024**3,normalized_load1=.8,normalized_load5=.8)
        path=self.root/('SYNTHETIC_RESOURCE_'+str(number)+'.json')
        f.slots.write_once(path,state)
        identity=f.e.pin(path)
        self.manager.publish_resource(state,identity)
        return identity

    def test_resource_publication_finishes_while_dispatch_lock_held(self):
        finished=threading.Event();errors=[]
        start=self.now;self.now+=timedelta(seconds=60)
        def publish():
            try:self.publish(start,self.now,5)
            except BaseException as error:errors.append(error)
            finally:finished.set()
        with self.manager.dispatch_lock:
            thread=threading.Thread(target=publish);thread.start()
            self.assertTrue(finished.wait(1),'Resource update waited for dispatch lock')
        thread.join(1)
        self.assertFalse(errors);self.assertEqual(self.manager.history.streak,6)

    def test_heavy_storage_check_does_not_block_new_resource_and_grant_uses_latest(self):
        entered=threading.Event();proceed=threading.Event();done=threading.Event();errors=[]
        original=self.manager.storage_snapshot
        def slow_storage():
            entered.set()
            if not proceed.wait(2):raise AssertionError('Fixture handoff timeout')
            return original()
        def acquire():
            try:self.manager.acquire(self.candidate,'emx')
            except BaseException as error:errors.append(error)
            finally:done.set()
        with patch.object(self.manager,'storage_snapshot',side_effect=slow_storage):
            thread=threading.Thread(target=acquire);thread.start()
            try:
                self.assertTrue(entered.wait(1))
                start=self.now;self.now+=timedelta(seconds=60)
                latest=self.publish(start,self.now,5)
                self.assertEqual(self.manager.history.streak,6)
            finally:proceed.set()
            self.assertTrue(done.wait(2));thread.join(1)
        self.assertFalse(errors)
        self.assertEqual(self.manager.permits[self.candidate.name]['resource'],latest)
        self.owner.verify_release.assert_called_once()
        self.assertEqual(self.manager.permits[self.candidate.name]['decision']['healthy_check_streak'],6)

    def test_no_tool_capacity_wait_does_not_verify_hashes_or_walk_storage(self):
        with self.manager.lock:
            self.manager.history.last['free_license_slots']={t:0 for t in TOOLS}
        with patch.object(self.manager,'storage_snapshot') as storage, \
                patch.object(f.e,'allocated_bytes') as allocated, \
                patch.object(self.manager.stop,'wait',return_value=True):
            self.assertEqual(self.manager.admit_candidate_count(),0)
            with self.assertRaisesRegex(RuntimeError,'OWNER_DRAINING'):
                self.manager.acquire(self.candidate,'emx')
        storage.assert_not_called();allocated.assert_not_called()
        self.owner.verify_release.assert_not_called()
        self.assertGreaterEqual(self.owner.deadline.call_count,2)
        self.assertEqual(self.manager.permits,{})

    def test_storage_walk_aging_resource_does_not_issue_permit(self):
        original=self.manager.storage_snapshot
        def age():
            result=original();self.now+=timedelta(seconds=91);return result
        with patch.object(self.manager,'storage_snapshot',side_effect=age), \
                patch.object(self.manager.stop,'wait',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'OWNER_DRAINING'):
                self.manager.acquire(self.candidate,'emx')
        self.owner.verify_release.assert_called_once()
        self.assertEqual(self.manager.permits,{})
        self.assertFalse(list(self.candidate.glob('*PERMIT*.json')))

    def test_candidate_admission_rechecks_resource_after_heavy_storage(self):
        original=self.manager.storage_snapshot
        def age():
            result=original();self.now+=timedelta(seconds=91);return result
        with patch.object(self.manager,'storage_snapshot',side_effect=age):
            self.assertEqual(self.manager.admit_candidate_count(),0)
        self.owner.verify_release.assert_called_once()

    def test_slow_permit_persistence_revokes_before_tool_start(self):
        original=f.pointer
        def slow_pointer(path,value):
            original(path,value)
            if path.name=='ACTIVE_TOOL_PERMIT.json' and 'sha256' in value:
                self.now+=timedelta(seconds=91)
        with patch.object(f,'pointer',side_effect=slow_pointer), \
                patch.object(self.manager.stop,'wait',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'OWNER_DRAINING'):
                self.manager.acquire(self.candidate,'emx')
        self.assertEqual(self.manager.permits,{})
        latest=f.e.document(f.e.pin(self.candidate/'ACTIVE_TOOL_PERMIT.json'))
        self.assertFalse(latest['active'])
        events=[call.args[0] for call in self.owner.event.call_args_list]
        self.assertNotIn('FIXED48_TOOL_PERMIT_GRANTED',events)
        self.assertIn('FIXED48_PERMIT_REVOKED_BEFORE_TOOL_START',events)


if __name__=='__main__':unittest.main(verbosity=2)

