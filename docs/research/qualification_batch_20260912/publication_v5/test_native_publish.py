"""New orchestration fixtures only; no real result/physics/ledger is consumed."""
from contextlib import contextmanager
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

import native_publish as n

WORKSPACE=Path(__file__).resolve().parents[4]
ATOMIC=WORKSPACE/'reports/eucap15_native_owner_20260909T062500Z/qualified15_holdout_partition_v3/atomic_primitives.py'
spec=importlib.util.spec_from_file_location('fixture_original_atomic',ATOMIC)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


class Reads:
    def __init__(self):self.checked={}
    def read(self,p):return n.read_exact(p)
    def load(self,p):return json.loads(self.read(p))


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='eucap_native_bridge_fixture_')
        self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name).resolve()
        self.ctx=NS(owner=root/'owner',state=root/'native_candidate_publication_fixture',
            union=root/'qualified15_single_member_v1',atomic_json=a.atomic_json,lease=a.lease,busy=a.BusyStudy,
            contract_pin={'path':'SYNTHETIC_CONTRACT','sha256':'fixture','bytes':0},runtime=[],
            mapping={},lib=root/'library',shared_pins=[],batch=NS(rows=[]),
            release_pin={'path':'SYNTHETIC_RELEASE','sha256':'fixture','bytes':0},
            release={'config':{'path':'SYNTHETIC_CONFIG','sha256':'fixture','bytes':0}})
        self.ctx.owner.mkdir();self.ctx.state.mkdir();self.ctx.union.mkdir();self.ctx.lib.mkdir()
        self.current=[]
        self.ctx.c=NS(SCHEMA='eucap15_production256_qualified_increment.v1',QUALIFICATION='fixture_qualification',
            INPUT_SCHEMA='fixture_inputs',legacy=NS(FP='fixture_fp'),
            history=NS(ledger=Mock(side_effect=lambda p:self.current)),
            build_evidence=Mock(return_value={'schema':'SYNTHETIC_EVIDENCE'}),publish=Mock())
        self.ctx.c.publish.return_value=dict(status='COMMITTED',added=1,path='fixture_record',sha256='fixture_sha')
        self.row=dict(request_id='SYNTHETIC-DOE-008',geometry=[1,2],geometry_fields=['a','b'],
            canonical_geometry_sha256='fixture_geometry',assigned_development_split='train')
        self.ctx.batch.rows=[self.row]

    def terminal(self,row=None,status='FRESH_EMX_EXTRACTED'):
        row=row or self.row
        value=dict(request_id=row['request_id'],status=status,original_proposal=row,
            core15_eligible=True,valid_for_strict_comparison=True,actual_native_starts=None)
        path=self.ctx.owner/row['request_id']/'RESULT.json';a.atomic_json(path,value,immutable=True)
        return n.pin(path),value

    def committed(self,rp,row=None):
        row=row or self.row
        m=dict(original_result=rp,original_proposal=copy.deepcopy(row),geometry=row['geometry'],
            geometry_fields=row['geometry_fields'],geometry_sha256=row['canonical_geometry_sha256'],
            split=row['assigned_development_split'])
        return dict(path='fixture_record',sha256='fixture_sha',value=dict(schema=self.ctx.c.SCHEMA,
            evidence={'qualification':self.ctx.c.QUALIFICATION,'member':m},
            record={'request_id':row['request_id'],'scientific_contract_fingerprint':'fixture_fp','split':m['split']}))

    def patches(self,received=None):
        @contextmanager
        def all_patches():
            with patch.object(n,'check_public'),patch.object(n,'reads_for',return_value=Reads()),\
                 patch.object(n,'receipt_for',return_value=({'path':'synthetic_receipt','sha256':'fixture','bytes':0},
                    {'verified_result':received})):
                yield
        return all_patches()

    def test_original_record_skip_requires_exact_result_geometry_split_and_contract(self):
        rp,_=self.terminal();old=self.committed(rp)
        self.assertEqual(n.existing_submission(self.ctx,[old],self.row,rp)['added'],0)
        for group,key,value in [('member','original_result',dict(rp,sha256='changed')),
                                ('member','split','test'),('member','geometry',[9,9]),
                                ('record','scientific_contract_fingerprint','other')]:
            changed=copy.deepcopy(old)
            container=changed['value']['evidence']['member'] if group=='member' else changed['value']['record']
            container[key]=value
            with self.assertRaises(ValueError):n.existing_submission(self.ctx,[changed],self.row,rp)

    def test_crash_after_append_before_outcome_skips_without_reconsuming(self):
        rp,_=self.terminal();self.current=[self.committed(rp)]
        with patch.object(n,'reads_for',side_effect=AssertionError('must not reconsume')):
            first=n.process_one(self.ctx,self.row,self.current)
            second=n.process_one(self.ctx,self.row,self.current)
        self.assertEqual(first['status'],'ALREADY_FORMALLY_COMMITTED_EXACT_SOURCE')
        self.assertEqual(second['status'],'REUSED_TERMINAL_CHECKPOINT')
        self.ctx.c.publish.assert_not_called()
        with self.assertRaises(n.PublicEvidenceError):n.process_one(self.ctx,self.row,[])

    def test_missing_result_is_not_pending_or_permanent_terminal(self):
        result=n.process_one(self.ctx,self.row,[])
        self.assertEqual(result['status'],'NO_CLOSED_RESULT_OBSERVED')
        self.assertFalse((self.ctx.state/'requests'/self.row['request_id']).exists())

    def test_ledger_lock_busy_preserves_evidence_and_retries_same_result(self):
        rp,source=self.terminal();self.ctx.c.publish.side_effect=a.BusyStudy('WRITE.lock')
        with self.patches(source):first=n.process_one(self.ctx,self.row,[])
        self.assertEqual(first['status'],'RETRYABLE_LEDGER_BUSY')
        self.assertFalse((self.ctx.state/'requests'/self.row['request_id']/'OUTCOME.json').exists())
        self.ctx.c.publish.side_effect=None
        with self.patches(source):second=n.process_one(self.ctx,self.row,[])
        self.assertEqual(second['added'],1)
        self.assertEqual(self.ctx.c.publish.call_count,2)

    def test_shared_publish_corruption_stops_not_candidate_hold(self):
        _,source=self.terminal();self.ctx.c.publish.side_effect=ValueError('mixed ledger prefix changed')
        with self.patches(source),self.assertRaises(n.PublicEvidenceError):n.process_one(self.ctx,self.row,[])
        self.assertFalse((self.ctx.state/'requests'/self.row['request_id']/'OUTCOME.json').exists())

    def test_candidate_missing_artifact_hold_is_recoverable_without_false_commit(self):
        _,source=self.terminal();self.ctx.c.build_evidence.side_effect=FileNotFoundError('own artifact')
        with self.patches(source):first=n.process_one(self.ctx,self.row,[])
        work=self.ctx.state/'requests'/self.row['request_id']
        self.assertEqual(first['status'],'CANDIDATE_EVIDENCE_HOLD_NO_QUALIFICATION')
        self.assertEqual(len(list(work.glob('HOLD_*.json'))),1);self.assertFalse((work/'OUTCOME.json').exists())
        self.ctx.c.publish.assert_not_called();self.ctx.c.build_evidence.side_effect=None
        with self.patches(source):second=n.process_one(self.ctx,self.row,[])
        self.assertEqual(second['added'],1)

    def test_failure_null_native_count_retained_and_next_request_continues(self):
        _,source=self.terminal(status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION')
        source.update(error='RESOURCE_WAIT_REQUIRED_NO_DISPATCH',stage_evidence=[])
        # The fixture terminal was saved before these fields; use a returned
        # original constructor value through a read-only fixture reader.
        reads=NS(load=lambda p:source,read=n.read_exact)
        self.ctx.result=NS(read_pin=Mock(),load=Mock(),failed_candidate=Mock(return_value=source))
        with patch.object(n,'reads_for',return_value=reads):out=n.process_one(self.ctx,self.row,[])
        self.assertIsNone(out['actual_native_starts']);self.assertEqual(out['added'],0)
        self.ctx.c.publish.assert_not_called()
        nextrow=dict(self.row,request_id='SYNTHETIC-DOE-009');_,fresh=self.terminal(nextrow)
        with self.patches(fresh):second=n.process_one(self.ctx,nextrow,[])
        self.assertEqual(second['added'],1)

    def test_public_fault_stops_before_next_and_union_busy_is_retriable(self):
        other=dict(self.row,request_id='SYNTHETIC-DOE-009');self.ctx.batch.rows.append(other)
        with patch.object(n,'LIBRARY',{}),patch.object(n,'check_public'),\
             patch.object(n,'process_one',side_effect=n.PublicEvidenceError('shared release changed')) as call:
            _,out=n.run(self.ctx)
        self.assertEqual(call.call_count,1);self.assertEqual(out['status'],'STOPPED_SHARED_AUTHORITY_OR_CHECKPOINT_FAULT')
        self.ctx.c.history.ledger.side_effect=a.BusyStudy('WRITE.lock')
        with patch.object(n,'LIBRARY',{}),patch.object(n,'check_public'),patch.object(n,'process_one') as call:
            _,out=n.run(self.ctx)
        self.assertEqual(out['status'],'RETRYABLE_LEDGER_BUSY');call.assert_not_called()

    def test_individual_append_busy_requests_program_retry_not_success_exit(self):
        with patch.object(n,'LIBRARY',{}),patch.object(n,'check_public'),\
             patch.object(n,'process_one',return_value=dict(status='RETRYABLE_LEDGER_BUSY',added=0)):
            _,out=n.run(self.ctx)
        self.assertEqual(out['status'],'RETRYABLE_LEDGER_BUSY')
        self.assertEqual(out['formally_added'],0)
        self.assertEqual(out['fault']['error_type'],'BusyStudy')


if __name__=='__main__':unittest.main(verbosity=2)
