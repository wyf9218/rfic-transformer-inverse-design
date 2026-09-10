"""Synthetic scheduler/recovery tests; no at jobs, native tools or MARS reads."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import successor_once as s


def ready(**changes):
    value = dict(old_owner_alive=False, old_related_count=0, old_pid_reused=False, new_owner_count=0,
                 native_count=0, predecessor_complete=True, successor_complete=False, deadline_expired=False)
    value.update(changes)
    return value


class DecisionTests(unittest.TestCase):
    def test_live_owner_with_zero_native_waits(self):
        self.assertEqual(s.decision(ready(old_owner_alive=True)), 'WAIT_PREDECESSOR_ALIVE')

    def test_dead_owner_missing_terminal_waits(self):
        self.assertEqual(s.decision(ready(predecessor_complete=False)), 'WAIT_PREDECESSOR_FULL_ACCOUNTING')

    def test_related_orphan_waits(self):
        self.assertEqual(s.decision(ready(old_related_count=1)), 'WAIT_PREDECESSOR_ALIVE')

    def test_native_survivor_waits(self):
        self.assertEqual(s.decision(ready(native_count=1)), 'WAIT_SURVIVING_NATIVE')

    def test_pid_reuse_does_not_mean_completion(self):
        self.assertEqual(s.decision(ready(old_pid_reused=True)), 'BLOCKED_PREDECESSOR_PID_REUSED')

    def test_exact_completed_boundary_requires_fresh_gates(self):
        self.assertEqual(s.decision(ready()), 'CHECK_FRESH_RESOURCE_AND_ORIGINAL_GATES')

    def test_existing_successor_never_submitted_twice(self):
        self.assertEqual(s.decision(ready(new_owner_count=1), True), 'DONE_EXISTING_SUCCESSOR_NO_DUPLICATE')

    def test_duplicate_successor_blocks(self):
        self.assertEqual(s.decision(ready(new_owner_count=2)), 'BLOCKED_DUPLICATE_SUCCESSOR')

    def test_preexec_crash_cannot_blindly_retry(self):
        self.assertEqual(s.decision(ready(), True), 'BLOCKED_PREEXEC_INTENT_WITHOUT_LIVE_OWNER_REVIEW_REQUIRED')

    def test_completed_successor_no_resubmission(self):
        self.assertEqual(s.decision(ready(successor_complete=True), True), 'DONE_SUCCESSOR_ALREADY_COMPLETE')

    def test_deadline_does_not_authorize_start(self):
        self.assertEqual(s.decision(ready(deadline_expired=True)), 'BLOCKED_IMMUTABLE_DEADLINE_EXPIRED')

    def test_unknown_identity_blocks(self):
        self.assertEqual(s.decision(ready(identity_error=True)), 'BLOCKED_IDENTITY')

    def test_preflight_process_never_counts_as_started_owner(self):
        config=dict(successor_script=dict(path='/frozen/owner.py'), successor_release=dict(path='/frozen/release.json'),
                    python=dict(path='/private/python'), native_python_path='/venv/python')
        base=['/venv/python','-B','/frozen/owner.py','--release','/frozen/release.json']
        self.assertEqual(s.successor_mode(base,config),'OWNER')
        self.assertEqual(s.successor_mode(base+['--preflight-only'],config),'PREFLIGHT')
        self.assertEqual(s.decision(ready(successor_inspection_count=1)), 'WAIT_SUCCESSOR_PREFLIGHT_NOT_OWNER')
        with self.assertRaisesRegex(s.Blocked,'UNRECOGNIZED'):
            s.successor_mode(base+['--unknown-mode'],config)


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root/'tickets').mkdir()
        (self.root/'events').mkdir()
        self.a = s.Adapter.__new__(s.Adapter)
        self.a.root = self.root
        self.a.manifest_sha = 'synthetic-sha'
        self.a.manifest_path = self.root/'manifest.json'
        self.a.c = dict(python=dict(path='/synthetic/python'), adapter_script='/synthetic/adapter.py',
            adapter_id='synthetic-only', interval_minutes=5, home='/synthetic', username='synthetic',
            successor_script=dict(path='/synthetic/owner.py'), successor_release=dict(path='/synthetic/release.json'))
        self.a.env = {'PATH':'/usr/bin:/bin'}
        self.a.at_jobs = Mock(return_value={})

    def test_submit_receipt_is_durable_and_reinstall_idempotent(self):
        with patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='job 123 at Thu Sep 10 00:00:00 2026')) as run:
            first = self.a.schedule('ticket1')
            self.assertEqual(first['job_id'], '123')
            self.a.at_jobs.return_value = {'123':'# eucap15-once-ticket:ticket1\n'}
            self.a.schedule('ticket1')
            self.assertEqual(run.call_count, 1)
        self.assertTrue((self.root/'tickets/ticket1/SCHEDULE_RECEIPT.json').exists())

    def test_crash_after_at_submission_reconciles_without_resubmit(self):
        p = self.root/'tickets/ticket1'
        p.mkdir()
        s.write_new(p/'TICKET.json',dict(manifest_sha256='synthetic-sha'))
        s.write_new(p/'SUBMIT_INTENT.json',dict(synthetic=True))
        self.a.at_jobs.return_value = {'123':'# eucap15-once-ticket:ticket1\n'}
        with patch.object(s.subprocess,'run',side_effect=AssertionError('no submit')):
            self.assertTrue(self.a.schedule('ticket1')['reconciled'])

    def test_ambiguous_at_submission_blocks(self):
        p = self.root/'tickets/ticket1'
        p.mkdir()
        s.write_new(p/'TICKET.json',dict(manifest_sha256='synthetic-sha'))
        s.write_new(p/'SUBMIT_INTENT.json',dict(synthetic=True))
        with self.assertRaisesRegex(s.Blocked,'UNCERTAIN'):
            self.a.schedule('ticket1')

    def test_duplicate_at_ticket_rejected(self):
        with self.assertRaisesRegex(s.Blocked,'DUPLICATE_SCHEDULER'):
            s.matching_jobs({'1':'token','2':'token'},'token')

    def test_durable_write_no_clobber(self):
        s.write_new(self.root/'record.json',dict(first=True))
        with self.assertRaises(FileExistsError):
            s.write_new(self.root/'record.json',dict(second=True))

    def test_recovery_follows_consumed_ticket_once(self):
        p=self.root/'tickets/first';p.mkdir()
        s.write_new(p/'CONSUMED.json',dict(next_ticket='second'))
        self.assertEqual(s.unconsumed_ticket(self.root,'first'),'second')

    def test_complete256_validation_rejects_incomplete_and_modified_rows(self):
        release=dict(path='synthetic',sha256='synthetic',bytes=0)
        terminal=self.root/'BATCH_RECEIPT.json'
        rows=[dict(request_id='TEST-%03d'%i) for i in range(256)]
        s.write_new(terminal,dict(status='ALL_FROZEN_256_ACCOUNTED',release=release,N_original_requests=256,results=rows[:-1]))
        with self.assertRaisesRegex(s.Blocked,'INCOMPLETE'):
            s.terminal_valid(terminal,release,256)

    def ticket(self):
        p=self.root/'tickets/first';p.mkdir()
        s.write_new(p/'TICKET.json',dict(manifest_sha256='synthetic-sha'))
        self.a.schedule=Mock(return_value=dict(job_id='synthetic-next'))
        return p

    def test_wait_rearms_before_snapshot_and_does_not_admit(self):
        p=self.ticket()
        self.a.snapshot=Mock(return_value=ready(old_owner_alive=True))
        self.a.original_admission=Mock(side_effect=AssertionError('no native admission during wait'))
        result=self.a.run_ticket('first')
        self.assertEqual(result['status'],'WAIT_PREDECESSOR_ALIVE')
        self.assertTrue((p/'CONSUMED.json').exists())
        self.a.schedule.assert_called_once()

    def test_duplicate_consumed_ticket_no_more_work(self):
        p=self.ticket();s.write_new(p/'CONSUMED.json',dict(next_ticket='later'))
        result=self.a.run_ticket('first')
        self.assertEqual(result['status'],'DUPLICATE_TICKET_IGNORED')
        self.a.schedule.assert_not_called()

    def test_stopped_adapter_never_signals_or_dispatches(self):
        self.ticket()
        s.state_write(self.root,dict(status='DONE_SCHEDULING_STOPPED_NO_CHILD_SIGNAL'))
        self.assertEqual(self.a.run_ticket('first')['status'],'DONE_SCHEDULING_STOPPED_NO_CHILD_SIGNAL')
        self.a.schedule.assert_not_called()

    def test_resource_wait_keeps_no_exec_intent(self):
        self.ticket();self.a.snapshot=Mock(return_value=ready())
        self.a.original_admission=Mock(return_value=(None,dict(status='WAIT'),None))
        self.assertEqual(self.a.run_ticket('first')['status'],'WAIT_FRESH_RESOURCE')
        self.assertFalse((self.root/'NATIVE_EXEC_INTENT.json').exists())

    def test_ready_exec_records_intent_before_original_command(self):
        self.ticket();self.a.snapshot=Mock(return_value=ready())
        owner=SimpleNamespace(env={},verify_release=Mock(),predecessor_closed=Mock())
        self.a.original_admission=Mock(return_value=(owner,dict(status='PASS'),None))
        class Replaced(BaseException): pass
        def exec_fake(path,argv,env):
            self.assertTrue((self.root/'NATIVE_EXEC_INTENT.json').exists())
            self.assertEqual(argv,['/synthetic/python','-B','/synthetic/owner.py','--release','/synthetic/release.json'])
            raise Replaced()
        with patch.object(s,'current_identity',return_value=dict(pid=123,start_ticks='SYNTHETIC')), patch.object(s.os,'dup2'), patch.object(s.os,'execve',side_effect=exec_fake):
            with self.assertRaises(Replaced): self.a.run_ticket('first')
        owner.verify_release.assert_called_once()
        owner.predecessor_closed.assert_called_once()

    def test_own_invocation_is_observed_without_resubmit(self):
        self.ticket()
        s.write_new(self.root/'NATIVE_EXEC_INTENT.json',dict(pid=123,start_ticks='SYNTHETIC'))
        self.a.snapshot=Mock(return_value=ready(new_owner_count=1,
            processes=[dict(new_owner=True,pid=123,start_ticks='SYNTHETIC')]))
        value=self.a.run_ticket('first')
        self.assertEqual(value['status'],'DONE_SUCCESSOR_LAUNCH_OBSERVED')
        self.assertTrue(value['successor_started_by_adapter'])

    def test_exact_complete256_reloads_committed_records(self):
        release=dict(path='synthetic',sha256='synthetic',bytes=0)
        rows=[dict(request_id='TEST-%03d'%i) for i in range(256)]
        for row in rows:
            p=self.root/row['request_id'];p.mkdir();s.write_new(p/'RESULT.json',row)
        path=self.root/'BATCH_RECEIPT.json'
        s.write_new(path,dict(status='ALL_FROZEN_256_ACCOUNTED',release=release,N_original_requests=256,results=rows))
        self.assertTrue(s.terminal_valid(path,release,256,[r['request_id'] for r in rows]))
        (self.root/'TEST-010/RESULT.json').write_text('{}')
        with self.assertRaisesRegex(s.Blocked,'TERMINAL_RESULT_MISMATCH'):
            s.terminal_valid(path,release,256)


if __name__=='__main__':
    unittest.main(verbosity=2)
