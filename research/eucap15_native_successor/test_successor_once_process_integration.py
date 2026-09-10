"""Two synthetic /proc-to-ticket regressions; no real /proc, at or native I/O.

Adapter construction is a private fixture; process_snapshot, Adapter.snapshot,
decision and run_ticket are the unchanged deployed implementations. Only Path's
exact /proc root is redirected, process UID/PID are synthetic, and scheduling
is a private durable stand-in. Admission, subprocesses and exec are prohibited.
"""
from contextlib import ExitStack
import hashlib
from pathlib import Path as RealPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import successor_once as s


SOURCE_SHA = '85ff89e1dbb1d81aa1233cf52f50c05c22820f44e803be976365976643bf952d'
FAKE_UID = 700001
SELF_PID = 700002
OWNER_PID = 700003
OWNER_START = '123456789'


class FakeProcEntry:
    def __init__(self, directory, uid, touched):
        self.directory = directory
        self.name = directory.name
        self.uid = uid
        self.touched = touched

    def stat(self):
        self.touched.append(('uid', self.name))
        return SimpleNamespace(st_uid=self.uid)

    def __truediv__(self, child):
        if child not in ('stat', 'cmdline'):
            raise AssertionError('Unexpected simulated proc member: ' + child)
        self.touched.append((child, self.name))
        return self.directory / child


class FakeProcRoot:
    def __init__(self, root, touched):
        self.root, self.touched = root, touched

    def glob(self, pattern):
        if pattern != '[0-9]*':
            raise AssertionError('Unexpected proc enumeration: ' + pattern)
        self.touched.append(('glob', pattern))
        # Numeric directories have real private stat/cmdline fixture files.
        for directory in sorted(self.root.glob(pattern)):
            uid = FAKE_UID + 1 if directory.name == '700004' else FAKE_UID
            yield FakeProcEntry(directory, uid, self.touched)


class ExactPathFactory:
    def __init__(self, root, touched):
        self.proc = FakeProcRoot(root, touched)

    def __call__(self, *parts):
        value = RealPath(*parts)
        if value == RealPath('/proc'):
            return self.proc
        if value.is_absolute() and value.parts[:2] == ('/', 'proc'):
            raise AssertionError('Unapproved proc path; never read host: ' + str(value))
        return value


class ProcessTicketIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(hashlib.sha256(RealPath(s.__file__).read_bytes()).hexdigest(), SOURCE_SHA)
        self.temp = tempfile.TemporaryDirectory(prefix='eucap15-proc-integration-')
        self.addCleanup(self.temp.cleanup)
        self.root = RealPath(self.temp.name).resolve()
        self.proc = self.root / 'fake_proc'; self.proc.mkdir()
        self.state = self.root / 'state'; self.state.mkdir()
        (self.state / 'events').mkdir(); (self.state / 'tickets').mkdir()
        self.touched = []
        self.factory = ExactPathFactory(self.proc, self.touched)
        # Unknown absolute proc routes must fail even when they resemble a file.
        with self.assertRaisesRegex(AssertionError, 'Unapproved proc path'):
            self.factory('/proc/self/stat')
        with self.assertRaisesRegex(AssertionError, 'Unapproved proc path'):
            self.factory('/proc/99999/cmdline')
        self.a = s.Adapter.__new__(s.Adapter)
        self.a.root = self.state
        self.a.manifest_sha = 'synthetic-manifest-sha'
        self.a.manifest_path = self.root / 'manifest.json'
        start = self.root / 'PREDECESSOR_START.json'
        s.write_new(start, dict(pid=700099, start_ticks='42'))
        self.a.c = dict(adapter_id='synthetic-process-integration', interval_minutes=5,
            predecessor_pid=700099, predecessor_start_ticks='42', predecessor_start=dict(path=str(start)),
            predecessor_script=dict(path=str(self.root/'old_owner.py')),
            predecessor_root=str(self.root/'old_run'), predecessor_terminal=str(self.root/'old_run/BATCH_RECEIPT.json'),
            predecessor_release=dict(path=str(self.root/'old_release.json')),
            successor_script=dict(path=str(self.root/'new_owner.py')),
            successor_release=dict(path=str(self.root/'new_release.json')),
            successor_terminal=str(self.root/'new_run/BATCH_RECEIPT.json'),
            successor_request_ids=[], python=dict(path=str(self.root/'private_python')),
            native_python_path=str(self.root/'native_python'), deadline_utc='2099-01-01T00:00:00+00:00')
        ticket = self.state/'tickets/first'; ticket.mkdir()
        s.write_new(ticket/'TICKET.json', dict(manifest_sha256=self.a.manifest_sha))
        self.ticket = ticket
        self.a.schedule = Mock(side_effect=self.fake_schedule)
        self.a.original_admission = Mock(side_effect=AssertionError('Admission forbidden in these scenarios'))
        self.a.at_jobs = Mock(side_effect=AssertionError('Real at enumeration forbidden'))
        self.assertIs(self.a.snapshot.__func__, s.Adapter.snapshot)
        self.assertIs(self.a.run_ticket.__func__, s.Adapter.run_ticket)
        # These unreadable decoys demonstrate that own PID / other UID are skipped
        # before their malformed command or missing stat can enter classification.
        for pid in (SELF_PID, 700004):
            folder = self.proc/str(pid); folder.mkdir()
            (folder/'cmdline').write_bytes(b'not-a-process\0--unknown\0')
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(s, 'Path', self.factory))
        stack.enter_context(patch.object(s.os, 'getuid', return_value=FAKE_UID))
        stack.enter_context(patch.object(s.os, 'getpid', return_value=SELF_PID))
        self.exec_mock = stack.enter_context(patch.object(s.os, 'execve', side_effect=AssertionError('exec forbidden')))
        self.run_mock = stack.enter_context(patch.object(s.subprocess, 'run', side_effect=AssertionError('subprocess forbidden')))
        self.popen_mock = stack.enter_context(patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Popen forbidden')))
        self.kill_mock = stack.enter_context(patch.object(s.os, 'kill', side_effect=AssertionError('signal forbidden')))
        stack.enter_context(patch.object(s.os, 'dup2', side_effect=AssertionError('stdio redirection forbidden')))
        stack.enter_context(patch.object(s, 'current_identity', side_effect=AssertionError('pre-exec identity forbidden')))

    def fake_schedule(self, ticket, minutes):
        self.assertEqual(minutes, 5)
        folder = self.state/'tickets'/ticket; folder.mkdir()
        s.write_new(folder/'TICKET.json', dict(ticket=ticket, manifest_sha256=self.a.manifest_sha))
        value = dict(job_id='SYNTHETIC_NOT_AT', ticket=ticket, token='synthetic-ticket:'+ticket)
        s.write_new(folder/'SCHEDULE_RECEIPT.json', value)
        return value

    def process(self, *, preflight):
        c = self.a.c
        args = [c['native_python_path'], '-B', c['successor_script']['path'], '--release', c['successor_release']['path']]
        if preflight:
            args.append('--preflight-only')
        folder = self.proc/str(OWNER_PID); folder.mkdir()
        (folder/'cmdline').write_bytes(b'\0'.join(x.encode() for x in args)+b'\0')
        after_comm = ['S', '1'] + ['0']*17 + [OWNER_START]
        (folder/'stat').write_text(str(OWNER_PID)+' (synthetic python) '+' '.join(after_comm)+'\n')
        return args

    def assert_safe_pipeline(self, value, args):
        snapshot = value['snapshot']
        self.assertEqual(snapshot['native_count'], 0)
        self.assertEqual(snapshot['processes'][0]['argv'], args)
        self.assertEqual(snapshot['processes'][0]['pid'], OWNER_PID)
        self.assertEqual(snapshot['processes'][0]['start_ticks'], OWNER_START)
        self.assertEqual(len(snapshot['processes']), 1)
        self.assertEqual(self.touched.count(('glob', '[0-9]*')), 1)
        self.assertIn(('cmdline', str(OWNER_PID)), self.touched)
        self.assertIn(('stat', str(OWNER_PID)), self.touched)
        self.assertNotIn(('cmdline', str(SELF_PID)), self.touched)
        self.assertNotIn(('cmdline', '700004'), self.touched)
        self.a.schedule.assert_called_once()
        next_ticket = s.read(self.ticket/'NEXT_TICKET.json')['ticket']
        self.assertEqual(s.read(self.ticket/'CONSUMED.json')['next_ticket'], next_ticket)
        next_dir = self.state/'tickets'/next_ticket
        self.assertTrue((next_dir/'TICKET.json').is_file())
        self.assertTrue((next_dir/'SCHEDULE_RECEIPT.json').is_file())
        self.assertFalse((next_dir/'CONSUMED.json').exists())
        self.assertEqual(value['next_check']['ticket'], next_ticket)
        self.assertFalse((self.state/'NATIVE_EXEC_INTENT.json').exists())
        self.assertFalse(value['invocation_intent_present'])
        self.assertFalse(value['successor_started_by_adapter'])
        self.assertEqual(s.read(self.state/'STATE.json')['status'], value['status'])
        self.a.original_admission.assert_not_called(); self.a.at_jobs.assert_not_called()
        self.exec_mock.assert_not_called(); self.run_mock.assert_not_called()
        self.popen_mock.assert_not_called(); self.kill_mock.assert_not_called()

    def test_exact_preflight_proc_snapshot_waits_and_retains_next_ticket(self):
        args = self.process(preflight=True)
        value = self.a.run_ticket('first')
        self.assertEqual(value['status'], 'WAIT_SUCCESSOR_PREFLIGHT_NOT_OWNER')
        self.assertFalse(value['status'].startswith('DONE_'))
        self.assertEqual(value['snapshot']['successor_inspection_count'], 1)
        self.assertEqual(value['snapshot']['new_owner_count'], 0)
        self.assert_safe_pipeline(value, args)

    def test_exact_real_owner_proc_snapshot_finishes_without_duplicate_exec(self):
        args = self.process(preflight=False)
        value = self.a.run_ticket('first')
        self.assertEqual(value['status'], 'DONE_EXISTING_SUCCESSOR_NO_DUPLICATE')
        self.assertEqual(value['snapshot']['new_owner_count'], 1)
        self.assertEqual(value['snapshot']['successor_inspection_count'], 0)
        self.assert_safe_pipeline(value, args)


if __name__ == '__main__':
    unittest.main(verbosity=2)
