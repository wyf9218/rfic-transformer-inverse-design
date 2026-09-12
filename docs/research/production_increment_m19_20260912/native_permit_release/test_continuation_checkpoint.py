"""Synthetic immutable continuation receipts, never production process evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D/'runtime'))
import continue_batches as c


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.serial=0
        self.base=self.save({'synthetic_base':True})
        self.prior=[]
        self.old=dict(initial_release=self.base,factory_template={'prior_batches':self.prior},
            target_qualified=100000,authorizations=['SYNTHETIC_ONLY'],python=sys.executable)
        self.previous=self.save(self.old)
        self.old_start=self.save(dict(deployment=self.previous,process=dict(pid=999999999,start_ticks=1)))
        self.handoff=self.save(dict(previous_start=self.old_start,child_signals=0,
            all_threads_stopped=True,descendants=[],native=[],
            status='TERMINAL_BOUNDARY_STANDBY_REPLACED_NO_CHILD_SIGNAL'))
        config=self.save(dict(out=str(self.root)))
        manifest=self.save({'files':{'SELECTED_CANDIDATES.jsonl':{'synthetic_candidate_pin':True}}})
        inputs=self.save(dict(manifest=manifest,path_map={}))
        self.current=self.save(dict(parent_release=self.base,config=config,
            successor_registration=dict(inputs=inputs)))
        self.start=self.save(dict(release=self.current,pid=999999999,start_ticks=1),name='START_RECEIPT.json')
        self.events=[dict(status='SUCCESSOR_OWNER_LAUNCHED',release=self.current,start=self.start),
            dict(status='WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT',release=self.current)]
        ep=self.root/'EVENTS.jsonl';ep.write_text(''.join(json.dumps(v)+'\n' for v in self.events))
        self.terminal=dict(release=self.current,N_original_requests=256,
            status='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE')
        self.state=dict(current_release=self.current,next_index=2,prior_batches=[
            dict(manifest=manifest,candidates={'synthetic_candidate_pin':True})])
        self.cp=dict(schema='eucap15_terminal_boundary_continuation_checkpoint.v1',
            previous_deployment=self.previous,previous_start=self.old_start,handoff=self.handoff,
            events=c.e.pin(ep),state=self.state,terminal=self.save(self.terminal))
        self.new=deepcopy(self.old);self.new['continuation_checkpoint']=self.save(self.cp)

    def save(self,value,name=None):
        self.serial+=1;path=self.root/(name or (str(self.serial)+'.json'))
        c.save(path,value);return c.e.pin(path)

    def test_replay_preserves_current_index_prior_and_skips_first_batch(self):
        current,index,prior=c.continuation_state(self.new)
        self.assertEqual(current,self.current);self.assertEqual(index,2)
        self.assertEqual(prior,self.state['prior_batches'])

    def test_tampered_index_is_rejected(self):
        self.cp['state']['next_index']=1
        self.new['continuation_checkpoint']=self.save(self.cp)
        with self.assertRaisesRegex(ValueError,'STATE_CHANGED'):c.continuation_state(self.new)

    def test_changed_scope_is_rejected(self):
        self.new['target_qualified']=200000
        with self.assertRaisesRegex(ValueError,'SCOPE_CHANGED'):c.continuation_state(self.new)

    def test_live_previous_standby_is_rejected(self):
        with patch.object(c.reg,'alive',return_value=True):
            with self.assertRaisesRegex(ValueError,'STILL_LIVE'):c.continuation_state(self.new)

    def test_unclosed_batch_is_rejected(self):
        self.terminal['N_original_requests']=255;self.cp['terminal']=self.save(self.terminal)
        self.new['continuation_checkpoint']=self.save(self.cp)
        with self.assertRaisesRegex(ValueError,'NOT_CLOSED'):c.continuation_state(self.new)

    def test_partially_advanced_continuation_cannot_handoff(self):
        self.events.append(dict(status='PRIOR_BATCH_CLOSED_AND_OWNER_DEAD',release=self.current))
        with self.assertRaisesRegex(ValueError,'NOT_AT_WAITING'):c.replay_waiting_state(self.old,self.events)


if __name__=='__main__':unittest.main(verbosity=2)
