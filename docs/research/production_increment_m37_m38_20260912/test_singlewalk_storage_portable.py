"""Only the new allocation path and its affected admission gates are tested."""
from datetime import timedelta
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
BASE=Path(os.environ.get('SINGLEWALK_BASE_RUNTIME',str(HERE.parent/'runtime')))
sys.path[:0]=[str(HERE/'runtime'),str(BASE)]
import fixed48_runtime as f
from run_development_native import BudgetEnded

spec=importlib.util.spec_from_file_location('existing_fixture',
    HERE/'hotpath_fixture.py')
hot=importlib.util.module_from_spec(spec);spec.loader.exec_module(hot)


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.a=self.root/'owner'/'a';self.b=self.root/'owner'/'b'
        self.a.mkdir(parents=True);self.b.mkdir()
        (self.a/'nested').mkdir()
        (self.a/'x').write_bytes(b'a'*12288)
        (self.a/'nested'/'y').write_bytes(b'b'*4096)
        (self.b/'z').write_bytes(b'c'*8192)
        (self.root/'shared.log').write_bytes(b'd'*4096)
        os.link(self.a/'x',self.a/'same_inode')
        os.link(self.a/'x',self.b/'cross_candidate_hardlink')
        self.claims={'a':self.a,'b':self.b,'missing':self.root/'owner'/'missing'}

    def test_real_files_hardlinks_missing_claim_equal_old_counts_one_walk(self):
        total=f.e.allocated_bytes(self.root)
        each={rid:f.e.allocated_bytes(p) for rid,p in self.claims.items()}
        original=f.os.walk
        with patch.object(f.os,'walk',wraps=original) as walk:
            observed=f.allocated_snapshot(self.root,self.claims)
        self.assertEqual(observed,(total,each))
        self.assertEqual(walk.call_count,1)
        # Cross-candidate hard links count once globally but in both subtrees.
        self.assertGreater(each['a'],0);self.assertGreater(each['b'],0)
        self.assertEqual(each['missing'],0)

    def test_growth_is_observed_without_cache(self):
        before=f.allocated_snapshot(self.root,self.claims)
        with (self.a/'x').open('ab') as stream:stream.write(b'e'*65536)
        after=f.allocated_snapshot(self.root,self.claims)
        self.assertGreater(after[0],before[0])
        self.assertGreater(after[1]['a'],before[1]['a'])
        self.assertEqual(after[0],f.e.allocated_bytes(self.root))

    def test_symlink_rejected_without_following_target(self):
        (self.a/'link').symlink_to(self.b,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'SYMLINK'):
            f.allocated_snapshot(self.root,self.claims)

    def test_outside_and_root_claim_rejected(self):
        for claim in (self.root.parent,self.root):
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'CLAIM_OUTSIDE'):
                f.allocated_snapshot(self.root,{'bad':claim})

    def test_walk_permission_error_not_silently_counted_as_zero(self):
        def bad_walk(*args,**kwargs):
            kwargs['onerror'](PermissionError('SYNTHETIC_UNREADABLE_SUBTREE'))
            return iter(())
        with patch.object(f.os,'walk',side_effect=bad_walk):
            with self.assertRaisesRegex(PermissionError,'SYNTHETIC_UNREADABLE'):
                f.allocated_snapshot(self.root,self.claims)


class AdmissionTests(unittest.TestCase):
    setUp=hot.HotPathTests.setUp
    publish=hot.HotPathTests.publish

    def test_snapshot_keeps_fresh_claim_reservations_and_binding(self):
        peak=self.policy['candidate_projected_peak_bytes']
        self.manager.storage.claim(self.candidate.name,0,0,peak)
        total=f.e.allocated_bytes(self.root)
        allocated=f.e.allocated_bytes(self.candidate)
        with patch.object(f.e,'allocated_bytes',side_effect=AssertionError('repeated walk')):
            record=self.manager.storage_snapshot()
        self.assertEqual(record['total_allocated_bytes'],total)
        self.assertEqual(record['allocation_by_candidate'],{self.candidate.name:allocated})
        self.assertEqual(record['remaining_projected_bytes'],max(0,peak-allocated))
        self.assertEqual(record['binding'],self.manager.binding)
        self.assertEqual(record['allocation_scan']['traversals'],1)
        self.assertEqual(f.e.document(self.manager.storage_pin),record)

    def test_exact_ceiling_still_blocks_and_below_still_allowed(self):
        for total in (f.m.MAX_INCREMENTAL_BYTES-1,f.m.MAX_INCREMENTAL_BYTES,f.m.MAX_INCREMENTAL_BYTES+1):
            with self.subTest(total=total),patch.object(f,'allocated_snapshot',return_value=(total,{})):
                if total<f.m.MAX_INCREMENTAL_BYTES:
                    self.assertEqual(self.manager.require_ready()['total_allocated_bytes'],total)
                else:
                    with self.assertRaisesRegex(BudgetEnded,'STORAGE_CAP'):
                        self.manager.require_ready()
        self.assertEqual(self.owner.verify_release.call_count,3)

    def test_changed_walk_aging_resource_still_prevents_native_permit(self):
        original=f.allocated_snapshot
        def age(*args,**kwargs):
            value=original(*args,**kwargs);self.now+=timedelta(seconds=91);return value
        self.owner.ensure_plan=Mock()
        with patch.object(f,'allocated_snapshot',side_effect=age),patch.object(self.manager.stop,'wait',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'DRAINING'):
                self.manager.acquire(self.candidate,'emx')
        self.assertEqual(self.manager.permits,{})
        self.owner.ensure_plan.assert_called_once()
        self.assertFalse(list(self.candidate.glob('*_PERMIT_*.json')))

    def test_grant_uses_new_snapshot_and_same_existing_checks(self):
        self.owner.ensure_plan=Mock()
        self.manager.acquire(self.candidate,'emx')
        permit=self.manager.permits[self.candidate.name]
        self.assertEqual(permit['storage'],self.manager.storage_pin)
        self.assertEqual(permit['decision']['healthy_check_streak'],5)
        self.owner.verify_release.assert_called_once()
        record=f.e.document(permit['storage'])
        self.assertEqual(record['allocation_scan']['method'],'SINGLE_FRESH_ROOT_WALK')
        self.manager.release(self.candidate.name)
        self.assertEqual(self.manager.permits,{})


if __name__=='__main__':unittest.main(verbosity=2)


