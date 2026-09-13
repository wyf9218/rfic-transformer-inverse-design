"""Only changed fresh-walk behavior: exact totals, aliases, nesting, failures."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ['ALLOCATION_BASE_RUNTIME'])
sys.path[:0] = [str(HERE/'runtime'), str(BASE)]
import allocation_walk as new
import controlled_metadata as m

REFERENCE = Path(os.environ.get('ALLOCATION_REFERENCE_RUNTIME', str(BASE)))
spec = importlib.util.spec_from_file_location('unchanged_reference_manager', REFERENCE/'fixed48_runtime.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


class FreshAllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.a = self.root/'owner/a';self.a.mkdir(parents=True)
        self.b = self.root/'owner/b';self.b.mkdir()
        (self.a/'nested').mkdir()
        (self.a/'one').write_bytes(b'A'*13000)
        (self.a/'nested/two').write_bytes(b'B'*8193)
        (self.b/'other').write_bytes(b'C'*4097)
        self.claims = dict(a=self.a, b=self.b)

    def test_equal_totals_nested_claims_sparse_files_and_hardlinks(self):
        os.link(self.a/'one',self.b/'alias')
        os.link(self.a/'one',self.a/'alias')
        with (self.b/'sparse').open('wb') as f:
            f.seek(1024*1024);f.write(b'X')
        self.claims['nested'] = self.a/'nested'
        self.assertEqual(old.allocated_snapshot(self.root,self.claims),new.allocated_snapshot(self.root,self.claims))
        self.assertEqual(old.allocated_snapshot(self.root,{}),new.allocated_snapshot(self.root,{}))

    def test_fresh_each_call_sees_growth_and_no_state_cache(self):
        before = new.allocated_snapshot(self.root,self.claims)
        (self.b/'new').write_bytes(b'X'*32769)
        after = new.allocated_snapshot(self.root,self.claims)
        self.assertGreater(after[0],before[0])
        self.assertEqual(after,old.allocated_snapshot(self.root,self.claims))

    def test_symlink_duplicate_and_external_claims_rejected(self):
        for bad in (dict(a=self.a,alias=self.a),dict(a=self.root.parent),dict(a=self.root)):
            with self.assertRaises(m.MetadataError):new.allocated_snapshot(self.root,bad)
        (self.b/'link').symlink_to(self.a,target_is_directory=True)
        with self.assertRaises(m.MetadataError):new.allocated_snapshot(self.root,self.claims)

    def test_unreadable_directory_fails_and_directory_swap_not_followed(self):
        original = new.os.open
        def denied(path,*args,**kwargs):
            if path == 'a':raise PermissionError('synthetic denial')
            return original(path,*args,**kwargs)
        with patch.object(new.os,'open',side_effect=denied),self.assertRaises(PermissionError):
            new.allocated_snapshot(self.root,self.claims)
        def swapped(path,*args,**kwargs):
            if path == 'a':return original(self.b,*args,**{k:v for k,v in kwargs.items() if k!='dir_fd'})
            return original(path,*args,**kwargs)
        with patch.object(new.os,'open',side_effect=swapped),self.assertRaisesRegex(m.MetadataError,'DIRECTORY_CHANGED'):
            new.allocated_snapshot(self.root,self.claims)


if __name__ == '__main__':unittest.main(verbosity=2)
