"""Only the reported two guard branches; all state and resource inputs synthetic."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import controlled_metadata as m
import start_slots as slots
import native_resource_probe as resource

OLD=Path(__file__).resolve().parent.parent/'controlled64_adapter_20260911_v1'


def old_module(name):
    spec=importlib.util.spec_from_file_location('frozen_'+name,OLD/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class SlotSequenceDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()/'synthetic'
        pin=dict(path='/synthetic-only',sha256='0'*64,bytes=1)
        rows=[dict(request_id=f'SYNTHETIC-{i}',candidate_id=f'SYNTHETIC-{i}',arm=arm,
            arm_order=j+1,global_order=i+1,canonical_geometry_sha256=f'{i:064x}',q_proxy=None,
            local_dispatch_eligible=True) for ai,arm in enumerate(m.ARMS) for j in range(32) for i in [ai*32+j]]
        self.plan=slots.plan(pin,'2026-09-11T00:00:00Z','2026-09-11T06:00:00Z',rows,evidence_class='SYNTHETIC_TEST_ONLY')
        self.book=slots.SlotBook(self.root,self.plan);self.book.create()
        for row in rows[:2]:self.book.reserve(row['candidate_id'],pin,pin,['synthetic-no-execution'],
            '2026-09-11T00:01:00Z',0)
        self.paths=sorted((self.root/'slots').glob('*.json'))

    def tearDown(self):self.temp.cleanup()

    def mutate(self,key,value,remove=False):
        p=self.paths[0];r=json.loads(p.read_text())
        if remove:r.pop(key,None)
        else:r[key]=value
        p.write_text(json.dumps(r))

    def test_old_gap_reproduced_fixed_rejected(self):
        self.mutate('global_slot',99)
        old=old_module('start_slots').SlotBook(self.root,self.plan)
        self.assertEqual(len(old.records()),2)
        with self.assertRaisesRegex(m.MetadataError,'GLOBAL_SLOT_SEQUENCE'):self.book.records()

    def test_old_duplicate_reproduced_fixed_rejected(self):
        other=json.loads(self.paths[1].read_text())['global_slot']
        self.mutate('global_slot',other)
        self.assertEqual(len(old_module('start_slots').SlotBook(self.root,self.plan).records()),2)
        with self.assertRaisesRegex(m.MetadataError,'GLOBAL_SLOT_SEQUENCE'):self.book.records()

    def test_global_and_arm_slot_require_nonbool_integer(self):
        original=self.paths[0].read_bytes()
        for key in ('global_slot','arm_slot'):
            for value in (True,1.0,'1',None):
                with self.subTest(key=key,value=value):
                    self.paths[0].write_bytes(original);self.mutate(key,value)
                    with self.assertRaisesRegex(m.MetadataError,'SLOT_SEQUENCE_INTEGER'):self.book.records()

    def test_missing_global_slot_rejected(self):
        self.mutate('global_slot',None,remove=True)
        with self.assertRaisesRegex(m.MetadataError,'SLOT_SEQUENCE_INTEGER'):self.book.records()

    def test_valid_serialized_sequence_read_only(self):
        before={p:p.read_bytes() for p in self.paths}
        result=self.book.records()
        self.assertEqual(sorted(r['global_slot'] for r in result),[1,2])
        self.assertEqual(before,{p:p.read_bytes() for p in self.paths})


class CpuCounterDeltaTests(unittest.TestCase):
    def setUp(self):
        self.config=dict(normalized_load1_max=1.1,normalized_load5_max=1.1,
            minimum_available_memory_fraction=.2,
            resource_budget=dict(min_memory_available_bytes=8*1024**3,min_disk_free_bytes=20*1024**3))
        self.before=dict(cpu=[100]*8,vm=dict(pswpin=0,pswpout=0,oom_kill=0),
            memory=dict(MemAvailable=16*1024**3,MemTotal=32*1024**3))
        self.after=copy.deepcopy(self.before);self.after['cpu']=[200,100,150,900,101,100,100,100]

    def evaluate(self,module=resource):
        return module.evaluate(self.config,self.before,self.after,60,
            dict(cadence=True,calibre=True,emx=True),100*1024**3,[1.,1.,1.],64,[])

    def invalid(self):
        r=self.evaluate();self.assertEqual(r['status'],'WAIT')
        self.assertFalse(r['checks']['sample']);self.assertFalse(r['checks']['iowait'])
        self.assertFalse(r['cpu_sample_valid']);self.assertIsNone(r['iowait_percent'])

    def test_negative_iowait_old_false_pass_reproduced(self):
        self.after['cpu'][4]=99
        old=self.evaluate(old_module('native_resource_probe'))
        self.assertEqual(old['status'],'PASS');self.assertLess(old['iowait_percent'],0)
        self.invalid()

    def test_other_negative_delta_with_positive_total_rejected(self):
        self.after['cpu'][0]=99
        self.assertEqual(self.evaluate(old_module('native_resource_probe'))['status'],'PASS')
        self.invalid()

    def test_total_zero_old_false_pass_reproduced(self):
        self.after['cpu']=list(self.before['cpu'])
        self.assertEqual(self.evaluate(old_module('native_resource_probe'))['status'],'PASS')
        self.invalid()

    def test_cpu_counter_reset_rejected(self):
        self.after['cpu']=[0]*8;self.invalid()

    def test_counter_dimension_or_type_rejected(self):
        good=list(self.after['cpu'])
        for values in (good[:7],good+[0],[True]+good[1:],[200.0]+good[1:]):
            with self.subTest(values=values):self.after['cpu']=values;self.invalid()

    def test_healthy_counter_exact_original_iowait_and_gates(self):
        old=self.evaluate(old_module('native_resource_probe'));new=self.evaluate()
        self.assertEqual(new['status'],'PASS')
        self.assertEqual(new['checks'],old['checks'])
        self.assertEqual(new['iowait_percent'],old['iowait_percent'])


if __name__=='__main__':unittest.main(verbosity=2)
