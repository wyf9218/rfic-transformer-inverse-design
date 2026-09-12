"""Synthetic immutable handoff files; no production lease or native call."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent/'runtime'))
import controlled_execution as e
import fixed48_amendment as a


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.i=0
        self.budget=self.root/'budget';self.budget.mkdir()
        self.old=dict(out=str(self.budget/'old/owner'),code_root=str(self.root/'old_runtime'),
            repo=str(self.root/'physical'),budget_root=str(self.budget),original_manifest={'SYNTHETIC':'M'},
            resource_budget=dict(max_global_solvers=1,cpu_per_solver=2),
            max_native_concurrency=1,global_simulator_concurrency=1,configuration={'SYNTHETIC':'PHYSICAL'},source_pins=[])
        self.parent=self.save(dict(config=self.save(self.old)))
        self.origin=self.save(dict(config=self.save(self.old),note='SYNTHETIC_ORIGIN_NOT_PARENT'))
        self.plan=self.budget/'start_ledger/PLAN.json';self.plan.parent.mkdir()
        self.plan.write_text(json.dumps(dict(release=self.origin,candidates={'x':{'request_id':'x'}})))
        self.start=self.save(dict(pid=999,start_ticks=123,release=self.parent))
        self.quiet=self.save(dict(processes=dict(descendants=[],native=[]),plan=e.pin(self.plan),results=[],slots=[]))
        self.stop=self.save(dict(status='CONTROLLED_CONCURRENCY_HANDOFF_NO_CHILD_SIGNAL',pid=999,start_ticks=123,
            child_signals=0,healthy_solver_kills=0,plan_unchanged=True,result_pins_unchanged=True))
        auth=[self.save(dict(kind='SYNTHETIC_TEST_ONLY',index=i)) for i in range(2)]
        # Fixture authorities are visibly synthetic. The validator constants are
        # patched only here, never in a release or during remote preflight.
        self.addCleanup(patch.stopall)
        patch.object(a,'CONTINUOUS_AUTH',auth[0]['sha256']).start()
        patch.object(a,'FIXED48_AUTH',auth[1]['sha256']).start()
        self.config=deepcopy(self.old)
        self.config.update(out=str(self.budget/'recoveries/new/owner'),code_root=str(self.root/'new_runtime'),
            max_native_concurrency=48,global_simulator_concurrency=48,adopted_stages={})
        self.config['resource_budget']['max_global_solvers']=48
        self.config['candidate_roots']={'x':self.config['out']+'/x'}
        self.amend=dict(schema='eucap15_fixed48_concurrency_amendment.v1',parent_release=self.parent,
            budget_origin_release=self.origin,original_plan=e.pin(self.plan),parent_start=self.start,
            authorizations=auth,handoff=self.stop,quiescent=self.quiet,prior_results=[],prior_slots=[],source_replacements=[])
        self.config['concurrency_amendment']=self.amend
        self.release=dict(concurrency_amendment=self.amend)

    def save(self,value):
        self.i+=1;p=self.root/(str(self.i)+'.json');p.write_text(json.dumps(value));return e.pin(p)

    def run_validation(self):
        return a.validate(self.release,self.config,e.document,e.pin,self.origin)

    def test_origin_stays_original_not_immediate_parent(self):
        self.assertNotEqual(self.parent,self.origin)
        before=self.plan.read_bytes()
        self.assertEqual(self.run_validation(),self.origin)
        self.assertEqual(self.plan.read_bytes(),before)

    def test_wrong_origin_rejected(self):
        self.amend['budget_origin_release']=self.parent
        with self.assertRaisesRegex(ValueError,'ORIGIN'):self.run_validation()

    def test_scientific_change_rejected(self):
        self.config['configuration']={'SYNTHETIC':'CHANGED_PHYSICAL'}
        with self.assertRaisesRegex(ValueError,'OTHER_CONFIGURATION'):self.run_validation()

    def test_changed_candidate_root_rejected(self):
        self.config['candidate_roots']['x']='/wrong/place'
        with self.assertRaisesRegex(ValueError,'CANDIDATE_ROOT'):self.run_validation()

    def test_child_signal_or_incomplete_handoff_rejected(self):
        value=e.document(self.stop);value['healthy_solver_kills']=1
        self.amend['handoff']=self.save(value)
        with self.assertRaisesRegex(ValueError,'HANDOFF'):self.run_validation()

    def test_inner_capacity_not_rebound_rejected(self):
        self.config['resource_budget']['max_global_solvers']=1
        with self.assertRaisesRegex(ValueError,'INNER_FIXED48'):self.run_validation()

    def test_altered_old_evidence_rejected(self):
        Path(self.quiet['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'PIN_MISMATCH'):self.run_validation()


if __name__=='__main__':unittest.main(verbosity=2)
