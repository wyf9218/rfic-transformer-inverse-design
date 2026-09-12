"""Six new synthetic tests only; no old suite, simulator, or live probe."""
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

D=Path(__file__).resolve().parent;sys.path.insert(0,str(D/'runtime'))
import fixed48_runtime as f
import continue_batches as c
from run_development_native import BudgetEnded


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


hot=load('hot_setup',os.environ['HOTPATH_TEST_PATH'])
checkpoint=load('checkpoint_setup',os.environ['CHECKPOINT_TEST_PATH'])


class BudgetTests(unittest.TestCase):
    publish=hot.HotPathTests.publish
    def setUp(self):
        hot.HotPathTests.setUp(self)
        self.owner.ensure_plan=lambda resource:None

    def test_old_two_scans_and_new_one_scan_return_same_allocation(self):
        old=load('old_budget',os.environ['OLD_RELEASE_RUNTIME'])
        with patch.object(f.e,'allocated_bytes',return_value=12) as walk:
            old.Manager.require_ready(self.manager)
            before=self.manager.storage_snapshot()
            self.assertEqual(walk.call_count,2)
        with patch.object(f.e,'allocated_bytes',return_value=12) as walk:
            after=self.manager.require_ready()
            self.assertEqual(walk.call_count,1)
        self.assertEqual(after['total_allocated_bytes'],before['total_allocated_bytes'])
        self.assertEqual(self.owner.verify_release.call_count,2)

    def test_exact_and_exceeded_hard_limit_still_rejected_below_still_allowed(self):
        for size in (f.m.MAX_INCREMENTAL_BYTES-1,f.m.MAX_INCREMENTAL_BYTES,f.m.MAX_INCREMENTAL_BYTES+1):
            with self.subTest(size=size),patch.object(f.e,'allocated_bytes',return_value=size) as walk:
                if size<f.m.MAX_INCREMENTAL_BYTES:self.assertEqual(self.manager.require_ready()['total_allocated_bytes'],size)
                else:
                    with self.assertRaisesRegex(BudgetEnded,'STORAGE_CAP'):self.manager.require_ready()
                self.assertEqual(walk.call_count,1)
                self.assertEqual(self.manager.storage.claims,{})

    def test_all_three_callers_use_snapshot_without_repeated_precheck_walk(self):
        original=f.e.allocated_bytes
        def fresh(path):return 0
        with patch.object(f.e,'allocated_bytes',side_effect=fresh) as walk:
            self.assertEqual(self.manager.admit_candidate_count(),64)
            self.assertEqual(sum(str(v.args[0])==str(self.root) for v in walk.call_args_list),1)
        with patch.object(f.e,'allocated_bytes',side_effect=fresh) as walk:
            self.manager.acquire(self.candidate,'emx')
            self.assertEqual(sum(str(v.args[0])==str(self.root) for v in walk.call_args_list),1)
        self.manager.release(self.candidate.name)
        with patch.object(f.e,'allocated_bytes',side_effect=fresh) as walk:
            with self.manager.candidate({'request_id':self.candidate.name}):
                # Admission once, then the existing post-claim reservation publication.
                self.assertEqual(sum(str(v.args[0])==str(self.root) for v in walk.call_args_list),2)
                self.assertEqual(set(self.manager.storage.claims),{self.candidate.name})
        self.assertEqual(self.manager.storage.claims,{})

    def test_resource_aging_during_full_snapshot_still_prevents_grant(self):
        from datetime import timedelta
        def age(path):
            if str(path)==str(self.root):self.now+=timedelta(seconds=91)
            return 0
        with patch.object(f.e,'allocated_bytes',side_effect=age),patch.object(self.manager.stop,'wait',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'DRAINING'):self.manager.acquire(self.candidate,'emx')
        self.assertEqual(self.manager.permits,{})
        self.owner.verify_release.assert_called_once()

    def test_fault_before_io_still_blocks_without_scanning(self):
        self.manager.fault='SYNTHETIC_STORAGE_FAULT'
        with patch.object(f.e,'allocated_bytes') as walk:
            with self.assertRaisesRegex(RuntimeError,'SHARED_RESOURCE_FAULT'):self.manager.require_ready()
        walk.assert_not_called();self.owner.verify_release.assert_not_called()


class ResumeTests(unittest.TestCase):
    setUp=checkpoint.CheckpointTests.setUp
    save=checkpoint.CheckpointTests.save
    def test_existing_checkpoint_is_carried_through_second_handoff(self):
        previous=deepcopy(self.new);previous_pin=self.save(previous)
        directory=self.root/'second';directory.mkdir()
        conf=self.save(dict(out=str(directory)))
        manifest=self.save({'files':{'SELECTED_CANDIDATES.jsonl':{'synthetic_second':True}}})
        inputs=self.save(dict(manifest=manifest,path_map={}))
        second=self.save(dict(parent_release=self.current,config=conf,successor_registration=dict(inputs=inputs)))
        c.save(directory/'START_RECEIPT.json',dict(release=second,pid=999999999,start_ticks=1))
        start=c.e.pin(directory/'START_RECEIPT.json')
        event_path=directory/'EVENTS.jsonl'
        import json
        events=[dict(status='SUCCESSOR_OWNER_LAUNCHED',release=second,start=start),
            dict(status='WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT',release=second)]
        event_path.write_text(''.join(json.dumps(v)+'\n' for v in events))
        previous_start=self.save(dict(deployment=previous_pin,process=dict(pid=999999999,start_ticks=1)))
        h=deepcopy(c.e.document(self.handoff));h['previous_start']=previous_start
        state=dict(current_release=second,next_index=3,prior_batches=[*self.state['prior_batches'],
            dict(manifest=manifest,candidates={'synthetic_second':True})])
        value=deepcopy(self.cp);value.update(previous_deployment=previous_pin,previous_start=previous_start,
            handoff=self.save(h),events=c.e.pin(event_path),state=state,
            terminal=self.save(dict(release=second,N_original_requests=256,
                status='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE')))
        new=deepcopy(previous);new['continuation_checkpoint']=self.save(value)
        old=load('old_continuation',os.environ['OLD_CONTINUATION'])
        with self.assertRaisesRegex(ValueError,'RELEASE_CHAIN_CHANGED'):old.continuation_state(new)
        self.assertEqual(c.continuation_state(new),(second,3,state['prior_batches']))


if __name__=='__main__':unittest.main(verbosity=2)
