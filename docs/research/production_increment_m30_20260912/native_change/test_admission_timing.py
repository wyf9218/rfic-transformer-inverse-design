"""Focused synthetic tests; no native tool, probe, or production lease."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


HERE=Path(__file__).resolve().parent
BASE=Path(os.environ.get('TIMING_BASE_RUNTIME',str(HERE.parent/'runtime')))
sys.path[:0]=[str(HERE/'runtime'),str(BASE)]
import fixed48_runtime as f
import run_fixed48_native as owner_module


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


old=load('parent_runtime',os.environ.get('TIMING_PARENT_SOURCE',str(BASE/'fixed48_runtime.py')))
workspace=next((p for p in HERE.parents if (p/'AGENTS.md').is_file()),None)
hot_path=os.environ.get('HOTPATH_TEST_PATH')
if hot_path is None:
    hot_path=str(workspace/'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/fixed48_dispatch_hotpath_v1/test_hotpath.py')
hot=load('timing_fixture_only',hot_path)


class TimingTests(unittest.TestCase):
    def exercise(self,new,scenario):
        case=hot.HotPathTests('test_no_tool_capacity_wait_does_not_verify_hashes_or_walk_storage')
        case.setUp()
        try:
            case.owner.ensure_plan=Mock()
            trace=[]
            manager=case.manager
            with ExitStack() as stack:
                for name in ('require_wait_ready','resource_decision','require_ready','storage_snapshot','release'):
                    original=getattr(manager,name)
                    def record(*args,_name=name,_original=original,**kwargs):
                        trace.append(_name)
                        return _original(*args,**kwargs)
                    stack.enter_context(patch.object(manager,name,side_effect=record))
                if scenario=='no_capacity':
                    manager.history.last['free_license_slots']={t:0 for t in f.TOOLS}
                if scenario=='stale':
                    original_walk=manager.storage_snapshot
                    def aging_walk():
                        value=original_walk()
                        case.now+=__import__('datetime').timedelta(seconds=91)
                        return value
                    stack.enter_context(patch.object(manager,'storage_snapshot',side_effect=aging_walk))
                if scenario=='revoked':
                    original_pointer=f.pointer
                    def aging_pointer(path,value):
                        original_pointer(path,value)
                        if path.name=='ACTIVE_TOOL_PERMIT.json' and 'sha256' in value:
                            case.now+=__import__('datetime').timedelta(seconds=91)
                    stack.enter_context(patch.object(f,'pointer',side_effect=aging_pointer))
                    # The old function's module must see the same persistence hook.
                    stack.enter_context(patch.object(old,'pointer',side_effect=aging_pointer))
                stack.enter_context(patch.object(old,'clock',side_effect=lambda:case.now.isoformat()))
                waits={'side_effect':[False,False,True]} if scenario=='no_capacity' else {'return_value':True}
                stack.enter_context(patch.object(manager.stop,'wait',**waits))
                error=None
                try:
                    (manager.acquire if new else lambda *a:old.Manager.acquire(manager,*a))(case.candidate,'emx')
                except RuntimeError as exc:
                    error=(type(exc).__name__,str(exc))
            events=[(c.args[0],{k:v for k,v in c.kwargs.items() if k not in ('permit','admission_timing')})
                    for c in case.owner.event.call_args_list]
            summaries=[c.kwargs['admission_timing'] for c in case.owner.event.call_args_list
                       if 'admission_timing' in c.kwargs]
            failure=dict(manager.admission_terminal_timings)
            terminal=[]
            if new and error:
                owner=object.__new__(owner_module.Owner);owner.manager=manager
                with patch.object(owner_module.SerialOwner,'event') as emit:
                    owner.event('NEW_RESULT_CAPTURED',request=case.candidate.name)
                    owner.event('NEW_RESULT_CAPTURED',request=case.candidate.name)
                    terminal=[v.kwargs for v in emit.call_args_list]
            return dict(trace=trace,error=error,events=events,summaries=summaries,
                failure=failure,terminal=terminal,permits=len(manager.permits),
                verify_calls=case.owner.verify_release.call_count,
                ensure_plan_calls=case.owner.ensure_plan.call_count,
                root_names=sorted(p.name.split('_2026')[0] for p in case.candidate.iterdir()))
        finally:case.doCleanups()

    def parity(self,scenario):
        before=self.exercise(False,scenario);after=self.exercise(True,scenario)
        for key in ('trace','error','events','permits','verify_calls','ensure_plan_calls','root_names'):
            self.assertEqual(before[key],after[key],key)
        return after

    def test_success_same_gate_calls_and_one_summary(self):
        result=self.parity('success')
        self.assertEqual(len(result['summaries']),1)
        self.assertEqual(result['summaries'][0]['attempts'],1)
        self.assertEqual(result['summaries'][0]['rejection_counts'],{})
        self.assertEqual(result['failure'],{})

    def test_no_capacity_same_exception_no_poll_events_terminal_summary_once(self):
        result=self.parity('no_capacity')
        self.assertEqual(result['events'],[])
        self.assertEqual(result['verify_calls'],0)
        timing=next(iter(result['failure'].values()))
        self.assertEqual(timing['attempts'],3)
        self.assertIn('resource_outer: PARTIAL_OR_FULL_CAPACITY_AVAILABLE',timing['rejection_counts'])
        self.assertEqual(timing['rejection_counts']['resource_outer: PARTIAL_OR_FULL_CAPACITY_AVAILABLE'],3)
        self.assertIn('admission_terminal_timings',result['terminal'][0])
        self.assertNotIn('admission_terminal_timings',result['terminal'][1])

    def test_stale_same_rejection_and_no_grant(self):
        result=self.parity('stale')
        self.assertEqual(result['permits'],0)
        self.assertEqual(result['summaries'],[])
        timing=next(iter(result['failure'].values()))
        self.assertIn('resource_final: STALE_OR_INSUFFICIENT_INDEPENDENT_CHECKS',timing['rejection_counts'])

    def test_revoke_same_event_and_failure_summary(self):
        result=self.parity('revoked')
        self.assertEqual(result['permits'],0)
        self.assertEqual(result['summaries'],[])
        self.assertEqual(result['events'][0][0],'FIXED48_PERMIT_REVOKED_BEFORE_TOOL_START')
        timing=next(iter(result['failure'].values()))
        self.assertEqual(timing['rejection_counts']['FIXED48_PERMIT_REVOKED_BEFORE_TOOL_START'],1)

    def test_monotonic_wait_and_hold_sum_include_active_interval(self):
        now=[0.0]
        class Lock:
            def __enter__(self):now[0]+=2
            def __exit__(self,*args):now[0]+=1
        with patch.object(f.time,'monotonic',side_effect=lambda:now[0]):
            timing=f.AdmissionTiming();timing.attempts+=1
            with timing.dispatch(Lock()):
                now[0]+=5
                summary=timing.summary()
                self.assertEqual(summary['total_wait_seconds'],7)
                self.assertEqual(summary['dispatch_lock_wait_seconds'],2)
                self.assertEqual(summary['dispatch_lock_hold_seconds_through_summary'],5)
            self.assertIsNone(timing.holding_since)
            self.assertEqual(timing.summary()['dispatch_lock_hold_seconds_through_summary'],5)

    def test_owner_failure_event_drains_pending_timings_once(self):
        import threading
        owner=object.__new__(owner_module.Owner)
        owner.manager=__import__('types').SimpleNamespace(lock=threading.RLock(),
            admission_terminal_timings={'SYNTHETIC_ONLY':{'attempts':2}})
        with patch.object(owner_module.SerialOwner,'event') as emit:
            owner.event('FIXED48_OWNER_EXIT_NO_CHILD_SIGNAL',error='RuntimeError: synthetic')
            owner.event('FIXED48_OWNER_EXIT_NO_CHILD_SIGNAL',error='RuntimeError: synthetic')
        self.assertIn('admission_terminal_timings',emit.call_args_list[0].kwargs)
        self.assertNotIn('admission_terminal_timings',emit.call_args_list[1].kwargs)

    def test_event_write_error_preserves_exception_and_unpublished_summary(self):
        import threading
        owner=object.__new__(owner_module.Owner)
        value={'attempts':3}
        owner.manager=__import__('types').SimpleNamespace(lock=threading.RLock(),
            admission_terminal_timings={'SYNTHETIC_ONLY':value})
        error=OSError('SYNTHETIC_EVENT_WRITE_FAILURE')
        with patch.object(owner_module.SerialOwner,'event',side_effect=error):
            with self.assertRaises(OSError) as caught:
                owner.event('NEW_RESULT_CAPTURED',request='SYNTHETIC_ONLY')
        self.assertIs(caught.exception,error)
        self.assertIs(owner.manager.admission_terminal_timings['SYNTHETIC_ONLY'],value)

    def test_boundary_copies_late_registry_no_overwrite_and_rejects_conflict(self):
        import tempfile
        with patch.object(sys,'argv',['synthetic_installer',str(HERE)]):
            installer=load('timing_installer_copy_helper',HERE/'install_at_boundary.py')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();oldroot=root/'old';newroot=root/'new'
            for base in (oldroot,newroot):(base/'batch_registry').mkdir(parents=True)
            first=oldroot/'batch_registry/first.json';first.write_bytes(b'{"SYNTHETIC":1}\n')
            installer.sync_boundary_registry(oldroot,newroot)
            target=newroot/'batch_registry/first.json';before=target.read_bytes()
            second=oldroot/'batch_registry/late.json';second.write_bytes(b'{"SYNTHETIC":2}\n')
            rows=installer.sync_boundary_registry(oldroot,newroot)
            self.assertEqual(len(rows),2)
            self.assertEqual(target.read_bytes(),before)
            self.assertEqual((newroot/'batch_registry/late.json').read_bytes(),second.read_bytes())
            target.write_bytes(b'{"SYNTHETIC":"conflict"}\n')
            with self.assertRaisesRegex(ValueError,'STAGED_REGISTRY_CONFLICT'):
                installer.sync_boundary_registry(oldroot,newroot)
            self.assertEqual(first.read_bytes(),before)


if __name__=='__main__':unittest.main(verbosity=2)
