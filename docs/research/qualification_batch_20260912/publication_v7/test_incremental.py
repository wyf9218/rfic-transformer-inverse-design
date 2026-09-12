"""Three new synthetic cache/append/lock fixtures; no physical data audit."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path('/Users/wyf/Documents/模拟变压器AI反向建模')
W=ROOT/'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
sys.path.insert(0,str(ROOT/'reports/eucap15_native_owner_20260909T062500Z/qualified15_holdout_partition_v3'))
sys.path.insert(0,str(W/'history_publication_adapter_v3'))
import history_publication as h
import atomic_primitives as a
import incremental_history as inc

spec=importlib.util.spec_from_file_location('fixture_constructor_only',W/'history_publication_adapter_v1/test_history_publication.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.PublicationTests('test_foreign_block_rejected')
        self.f.setUp();self.addCleanup(self.f.tearDown)
        self.e=self.f.evidence()
        self.publisher=inc.IncrementalPublisher(self.f.root,first_sha=self.f.first_sha)

    def call(self):return self.publisher.publish(self.e,'SYNTHETIC_TEST_ONLY',reader=self.f.reader)

    def test_incremental_append_reads_only_new_bodies_and_preserves_replay(self):
        one=self.call();self.assertEqual((one['added'],one['qualification_sequence']),(1,2))
        self.assertEqual(self.publisher.summary()['record_body_reads'],1)
        self.assertEqual(self.call()['added'],0)
        self.assertEqual(self.publisher.summary()['record_body_reads'],2)
        original=Path.read_bytes
        def no_prefix_content(path):
            if path.parent==self.f.root/'records':raise AssertionError('Cached body was reread')
            return original(path)
        with patch.object(Path,'read_bytes',no_prefix_content):
            self.assertEqual(self.call()['added'],0)
        self.assertEqual(self.publisher.summary()['record_body_reads'],2)
        self.assertEqual(len(h.ledger(self.f.root,self.f.first_sha)),2)

    def test_cached_prefix_change_or_missing_rejected_even_with_restored_mtime(self):
        self.call();self.call()
        path=self.f.root/'records/000001.json';before=path.stat();body=path.read_bytes()
        path.write_bytes(body.replace(b'SYNTHETIC',b'synthetic'))
        os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'CACHED_PREFIX_CHANGED'):self.call()
        path.unlink()
        with self.assertRaisesRegex(ValueError,'PREFIX_TRUNCATED|sequence/path'):self.call()
        self.assertEqual(self.publisher.summary()['record_body_reads'],2)

    def test_external_append_seen_and_busy_lock_changes_nothing_or_duplicates(self):
        # Original publisher and new publisher share the actual permanent lease.
        with a.lease(self.f.root/'WRITE.lock'):
            with self.assertRaises(a.BusyStudy):self.call()
        self.assertEqual(self.publisher.summary()['record_body_reads'],0)
        self.f.old_record([205.,225.,235.,245.,6.,40.,45.,1.,150.,160.],2,self.f.first_sha)
        one=self.call();self.assertEqual(one['qualification_sequence'],3)
        prior=one['sha256']
        with a.lease(self.f.root/'WRITE.lock'):
            self.f.old_record([210.,230.,240.,250.,6.,40.,45.,1.,150.,160.],4,prior)
        again=self.call();self.assertEqual(again['added'],0)
        self.assertEqual(self.publisher.summary()['verified_prefix_count'],4)
        self.assertEqual(self.publisher.summary()['record_body_reads'],4)
        self.assertEqual(len(h.ledger(self.f.root,self.f.first_sha)),4)


if __name__=='__main__':unittest.main(verbosity=2)
