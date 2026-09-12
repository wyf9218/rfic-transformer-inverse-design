"""One Linux integration test with real synthetic parent/child processes only."""
import ast
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, sys.argv.pop(1))
import native_birth
import quiescent_handoff as q
import waiter_reservation as r


class WaitingBoundary(unittest.TestCase):
    def test_late_registry_real_copy_conflict_and_before_retirement_order(self):
        runtime = Path(native_birth.__file__).parent
        spec = importlib.util.spec_from_file_location('bounded_prior_installer', runtime.parent/'install_at_boundary.py')
        installer = importlib.util.module_from_spec(spec)
        argv = sys.argv
        try:
            sys.argv = [str(spec.origin), str(runtime.parent)]
            spec.loader.exec_module(installer)
        finally:
            sys.argv = argv
        with tempfile.TemporaryDirectory(prefix='synthetic_registry_') as directory:
            old, new = Path(directory)/'old', Path(directory)/'new'
            (old/'batch_registry').mkdir(parents=True)
            (new/'batch_registry').mkdir(parents=True)
            source = old/'batch_registry/late.json'
            original = b'{"synthetic":true,"late_registration":6}\n'
            source.write_bytes(original)
            copied = installer.sync_boundary_registry(old, new)
            self.assertEqual(len(copied), 1)
            target = new/'batch_registry/late.json'
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(installer.sync_boundary_registry(old, new)[0]['copy'], copied[0]['copy'])
            target.write_bytes(b'{"conflict":true}\n')
            with self.assertRaisesRegex(Exception, 'STAGED_REGISTRY_CONFLICT'):
                installer.sync_boundary_registry(old, new)
            self.assertEqual(source.read_bytes(), original)
        tree = ast.parse((Path(__file__).parent/'installer_continuation.py').read_text())
        positions = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                positions.setdefault(node.func.attr, []).append(node.lineno)
        for name in ('sync_boundary_registry', 'registered_profile', 'require_parent_terminal'):
            self.assertLess(min(positions[name]), min(positions['terminate_frozen']))

    def test_reservation_preserves_child_and_then_closes_only_parent(self):
        with tempfile.TemporaryDirectory(prefix='synthetic_waiter_') as directory:
            root = Path(directory)
            child_code = "from pathlib import Path;import sys,time; p=Path(sys.argv[1]);\nwhile not (p/'GO').exists():time.sleep(.01)\n(p/'CHILD_DONE').write_text('synthetic-complete')"
            parent_code = "import subprocess,sys,json,time;from pathlib import Path;import native_birth; p=Path(sys.argv[1]); c=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(p)]); (p/'CHILD.json').write_text(json.dumps(native_birth.proc_info(c.pid)));\nwhile c.poll() is None:time.sleep(.1)\n(p/'NEXT_BATCH').write_text('must-not-dispatch');time.sleep(60)"
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(native_birth.__file__).parent)
            parent = subprocess.Popen([sys.executable, '-c', parent_code, str(root), child_code], env=env)
            fd = None
            child = None
            try:
                for _ in range(300):
                    if (root/'CHILD.json').exists():
                        break
                    time.sleep(.01)
                identity = native_birth.proc_info(parent.pid)
                child = json.loads((root/'CHILD.json').read_bytes())
                def live_children():
                    v = native_birth.proc_info(child['pid'])
                    return [] if v['state'] == 'Z' else [v]
                def snapshot():
                    return dict(process=native_birth.proc_info(parent.pid), token='synthetic-release-boundary',
                        waiting=not (root/'NEXT_BATCH').exists(), fds_safe=True, children_recognized=True,
                        owner_alive=bool(live_children()), wchan=(Path('/proc')/str(parent.pid)/'wchan').read_text())
                self.assertIsNone(q.freeze(identity, lambda:dict(descendants=live_children(), native=[])))
                bad = snapshot()
                bad['children_recognized'] = False
                with self.assertRaisesRegex(RuntimeError, 'UNRECOGNIZED_DIRECT_CHILD'):
                    r.validate(bad, identity, bad['token'], before=True)
                fd, evidence = r.reserve(identity, snapshot)
                self.assertEqual(evidence['child_signals'], 0)
                self.assertNotIn(native_birth.proc_info(child['pid'])['state'], ('T', 't'))
                (root/'GO').write_text('synthetic-only')
                for _ in range(300):
                    if (root/'CHILD_DONE').exists() and not live_children():
                        break
                    time.sleep(.01)
                self.assertEqual((root/'CHILD_DONE').read_text(), 'synthetic-complete')
                self.assertFalse(live_children())
                self.assertFalse((root/'NEXT_BATCH').exists())
                q.terminate_frozen(identity, fd)
                fd = None
                parent.wait(timeout=5)
                self.assertFalse((root/'NEXT_BATCH').exists())
            finally:
                if fd is not None:
                    q.resume(fd)
                if parent.poll() is None:
                    parent.terminate()
                    parent.wait(timeout=5)
                if child is not None:
                    try:
                        actual = native_birth.proc_info(child['pid'])
                        if actual['start_ticks'] == child['start_ticks'] and actual['state'] != 'Z':
                            cfd = os.pidfd_open(child['pid'])
                            signal.pidfd_send_signal(cfd, signal.SIGTERM)
                            os.close(cfd)
                    except (FileNotFoundError, ProcessLookupError):
                        pass


if __name__ == '__main__':
    unittest.main()
