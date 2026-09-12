"""Synthetic scheduling inputs only; never live admission or simulator proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from capacity import InflightReservations, ResourceHistory, TOOLS


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.t=datetime(2026,9,12,tzinfo=timezone.utc)
        self.binding={'release':'SYNTHETIC_TEST_ONLY','owner':{'pid':123,'start_ticks':321}}
        self.h=ResourceHistory(self.binding)
        self.p=dict(requested_emx=48,cpu_per_solver=2,normalized_load1_max=1.10,
            normalized_load5_max=1.10,minimum_available_memory_fraction=.20,system_cpu_reserve=4,
            cpu_reservation=dict(cadence=2,calibre=2,emx=2),
            memory_reservation_bytes={t:8*1024**3 for t in TOOLS},
            tool_executor_capacity=dict(cadence=1,calibre=1,emx=48))
        self.zero={t:0 for t in TOOLS}

    def sample(self,i,**overrides):
        s=dict(binding=self.binding,started_utc=(self.t+timedelta(seconds=61*i)).isoformat(),
            utc=(self.t+timedelta(seconds=61*i+60)).isoformat(),
            checks={k:True for k in ('cpu','memory','swap','oom','iowait','sample','storage','isolation')},
            free_license_slots=dict(cadence=1,calibre=2,emx=64),idle_cpu_equivalents=110,
            available_memory_bytes=700*1024**3,total_memory_bytes=800*1024**3,free_disk_bytes=1000*1024**3,
            normalized_load1=.8,normalized_load5=.9)
        s.update(overrides)
        return s

    def add(self,i,**overrides):
        s=self.sample(i,**overrides)
        self.h.observe(s,dict(path='/SYNTHETIC_TEST_ONLY/'+str(i),sha256=f'{i:064x}',bytes=999))
        return s

    def limits(self,s,pending=None):
        return self.h.limits(s['utc'],self.zero,pending or self.zero,self.p)

    def test_five_independent_checks_full48_and_separate_tools(self):
        for i in range(4):
            s=self.add(i)
            self.assertEqual(self.limits(s)['additional'],self.zero)
        s=self.add(4)
        self.assertEqual(self.limits(s)['additional'],dict(cadence=1,calibre=1,emx=48))

    def test_partial_capacity_does_not_require_all48(self):
        for i in range(5):s=self.add(i,free_license_slots=dict(cadence=0,calibre=2,emx=7))
        self.assertEqual(self.limits(s)['additional'],dict(cadence=0,calibre=1,emx=7))

    def test_pending_cross_tool_cpu_reservations_share_budget(self):
        for i in range(5):s=self.add(i,idle_cpu_equivalents=12)
        r=self.limits(s,dict(cadence=1,calibre=1,emx=1))
        self.assertEqual(r['additional']['emx'],1)

    def test_duplicate_overlapping_and_owner_changed_rejected(self):
        s=self.add(0)
        with self.assertRaisesRegex(ValueError,'REPEATED'):
            self.h.observe(s,dict(path='/SYNTHETIC_OTHER',sha256='0'*64,bytes=999))
        with self.assertRaisesRegex(ValueError,'OVERLAPPING'):
            self.h.observe(s,dict(path='/SYNTHETIC_OTHER',sha256='1'*64,bytes=999))
        changed=self.sample(1,binding=dict(release='WRONG'))
        with self.assertRaisesRegex(ValueError,'BINDING'):
            self.h.observe(changed,dict(path='/SYNTHETIC_OTHER',sha256='2'*64,bytes=999))

    def test_stale_sample_waits_and_hard_failure_resets_streak(self):
        for i in range(5):s=self.add(i)
        at=(datetime.fromisoformat(s['utc'])+timedelta(seconds=91)).isoformat()
        self.assertEqual(self.h.limits(at,self.zero,self.zero,self.p)['additional'],self.zero)
        checks=deepcopy(s['checks']);checks['swap']=False
        self.add(5,checks=checks)
        self.assertEqual(self.h.streak,0)

    def test_missing_license_capacity_is_not_pass(self):
        with self.assertRaisesRegex(ValueError,'LICENSE'):
            self.add(0,free_license_slots=dict(cadence=1,calibre=1,emx=True))

    def test_inflight_accounting_is_exclusive_and_does_not_count_bytes_twice(self):
        r=InflightReservations(100)
        self.assertTrue(r.claim('a',10,10,50))
        self.assertTrue(r.claim('b',0,10,50))
        self.assertFalse(r.claim('c',0,10,1))
        r.refresh(dict(a=30,b=20))
        self.assertEqual(sum(v['remaining'] for v in r.claims.values()),50)
        self.assertFalse(r.claim('c',0,50,1))
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):
            r.claim('a',30,50,50)
        r.release('a')
        self.assertTrue(r.claim('c',0,50,20))


if __name__=='__main__':unittest.main(verbosity=2)
