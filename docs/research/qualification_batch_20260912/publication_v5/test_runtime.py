"""Synthetic admission and thread delegates, never native execution evidence."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fixed48_runtime as f
from fixed48_capacity import TOOLS
from rfic_transformer_inverse_design.campaigns.broadband56_dispatch import bounded_completed


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve()
        self.policy=dict(requested_emx=48,cpu_per_solver=2,normalized_load1_max=1.10,
            normalized_load5_max=1.10,minimum_available_memory_fraction=.20,system_cpu_reserve=4,
            cpu_reservation={t:2 for t in TOOLS},memory_reservation_bytes={t:8*1024**3 for t in TOOLS},
            tool_executor_capacity=dict(cadence=1,calibre=1,emx=48),candidate_pipeline_capacity=64,
            candidate_projected_peak_bytes=64*1024**2)
        self.owner=SimpleNamespace(config=dict(fixed48_policy=self.policy,budget_root=str(root)),out=root,
            release_pin={'TEST_ONLY':'not_a_production_release'},event=lambda *a,**kw:None,
            deadline=lambda:None,verify_release=lambda:None)
        self.manager=f.Manager(self.owner)
        end=datetime.now(timezone.utc)
        for i in range(5):
            began=end-timedelta(seconds=(5-i)*61)
            sample=dict(binding=self.manager.binding,started_utc=began.isoformat(),
                utc=(began+timedelta(seconds=60)).isoformat(),
                checks={k:True for k in ('cpu','memory','swap','oom','iowait','sample','storage','isolation')},
                free_license_slots=dict(cadence=1,calibre=1,emx=48),idle_cpu_equivalents=140,
                available_memory_bytes=750*1024**3,total_memory_bytes=800*1024**3,
                free_disk_bytes=200*1024**3,normalized_load1=.8,normalized_load5=.8)
            p=root/(f'TEST_ONLY_SAMPLE_{i}.json');f.slots.write_once(p,sample)
            self.manager.last_pin=f.e.pin(p);self.manager.history.observe(sample,self.manager.last_pin)

    def test_pipeline_has_budgeted_preprocessing_headroom_beyond48(self):
        self.assertEqual(self.manager.admit_candidate_count(),64)
        self.assertEqual(self.policy['tool_executor_capacity']['emx'],48)
        for i in range(48):
            self.manager.storage.claim(str(i),0,0,64*1024**2)
        self.assertEqual(self.manager.admit_candidate_count(),64)

    def test_tool_permit_lifecycle_and_pending_shared_reservations(self):
        root=self.owner.out/'synthetic-candidate';root.mkdir()
        with self.manager.candidate({'request_id':root.name}):
            self.manager.acquire(root,'emx')
            active=f.e.document(f.e.document(f.e.pin(root/'ACTIVE_TOOL_PERMIT.json')))
            self.assertEqual(active['tool'],'emx')
            self.assertTrue(active['active'])
            reflected,pending=self.manager.counts()
            self.assertEqual(pending['emx'],1);self.assertEqual(reflected['emx'],0)
            self.manager.release(root.name)
            self.assertFalse(f.e.document(f.e.pin(root/'ACTIVE_TOOL_PERMIT.json'))['active'])
        self.assertEqual(self.manager.storage.claims,{})

    def test_shared_fault_stops_new_admission(self):
        self.manager.fault='SYNTHETIC_INTERFACE_FAILURE'
        with self.assertRaisesRegex(RuntimeError,'SHARED_RESOURCE_FAULT'):
            self.manager.admit_candidate_count()

    def test_final_emx_permit_rejects_wrong_candidate(self):
        root=self.owner.out/'synthetic-candidate';root.mkdir()
        with self.manager.candidate({'request_id':root.name}):
            self.manager.acquire(root,'emx')
            config={'out':str(self.owner.out)}
            with patch.object(f,'own_peers',return_value=[]):
                value=f.verify_emx_permit(config,self.owner.release_pin,{'request_id':root.name})
                self.assertEqual(value['request_id'],root.name)
                with self.assertRaisesRegex(ValueError,'EXACT_ACTIVE_EMX_PERMIT'):
                    f.verify_emx_permit(config,{'TEST_ONLY':'wrong_release'},{'request_id':root.name})

    def test_actual_bounded_executor_refills_and_accounts_exact_indexes(self):
        path=self.owner.out/'synthetic_dispatch';seen=[];lock=threading.Lock()
        def invoke(i):
            time.sleep(.001)
            with lock:seen.append(i)
            return i
        with closing(bounded_completed(65,invoke,max_workers=64,admission=lambda:64,
                receipt_dir=path,poll_seconds=.001)) as completed:
            returned={i:fut.result() for i,fut in completed}
        self.assertEqual(sorted(seen),list(range(65)))
        self.assertEqual(returned,{i:i for i in range(65)})
        receipt=json.loads((path/'DISPATCH_RECEIPT.json').read_bytes())
        self.assertFalse(receipt['native_concurrency_proven'])
        self.assertEqual(receipt['accepted_increment'],0)

    def test_actual_bounded_gate_fault_drains_without_more_submissions(self):
        path=self.owner.out/'synthetic_gate_fault';finished=[];calls=0
        def admit():
            nonlocal calls
            calls+=1
            if calls>1:raise ValueError('SYNTHETIC_HARD_GATE')
            return 2
        def invoke(i):time.sleep(.01);finished.append(i)
        with self.assertRaisesRegex(Exception,'SYNTHETIC_HARD_GATE'):
            with closing(bounded_completed(10,invoke,max_workers=64,admission=admit,
                    receipt_dir=path,poll_seconds=.001)) as completed:
                for _,future in completed:future.result()
        self.assertEqual(sorted(finished),[0,1])
        r=json.loads((path/'DISPATCH_RECEIPT.json').read_bytes())
        self.assertEqual(r['not_dispatched_indexes'],list(range(2,10)))


if __name__=='__main__':unittest.main(verbosity=2)
