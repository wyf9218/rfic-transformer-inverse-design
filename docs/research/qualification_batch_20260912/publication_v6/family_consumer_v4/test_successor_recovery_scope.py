"""One new successor anchor-scope fixture; no old suites or native IO."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import family_entry as f


class SuccessorRecoveryScopeTest(unittest.TestCase):
    def test_frozen7204_scope_restores_and_unknown_fails_before_validator(self):
        old_amendment='aa6d854316335aa362b33c76d64e47a5784ba943c7eb3b03505090153f0406c5'
        old_successor_runtime={'original_successor':'preserved'}
        rp=dict(path='/SYNTHETIC/owner/RELEASE.json',sha256='a'*64,bytes=1)
        cp=dict(path='/SYNTHETIC/CONFIG.json',sha256='b'*64,bytes=1)
        config=dict(code_root='/SYNTHETIC/runtime',original_manifest={'sha256':'c'*64},
                    max_native_concurrency=48,resource_budget={'cpu_per_solver':2})
        source=dict(path='/SYNTHETIC/runtime/fixed48_amendment.py',sha256=f.RECOVERY_AMENDMENT_SHA,bytes=1)
        release=dict(config=cp,sources=[source],successor_registration={'schema':'SYNTHETIC'})
        profile=dict(config=cp,manifest_sha256='c'*64)
        bridge=NS(library=Mock(),run=Mock(return_value=('RECEIPT',{'status':'SYNTHETIC'})))
        fixed=NS(OLD_RELEASE_SHA='0'*64,TRUSTED_AMENDMENT_SOURCE_SHA=old_amendment,
                 TRUSTED_FIXED48_RUNTIME={'original_fixed48':'not_successor'},
                 load_bridge=Mock(return_value=bridge),pin=lambda p:rp,
                 read=lambda p:release if p==rp else config,
                 parameterized_bridge=lambda *a:nullcontext())
        expected=[f.RECOVERY_AMENDMENT_SHA,f.RECOVERY_RUNTIME]
        def verify_scope(*args):
            self.assertEqual(successor.AMENDMENT_SOURCE_SHA,expected[0])
            self.assertEqual(successor.TRUSTED_SUCCESSOR_RUNTIME,expected[1])
            return NS()
        successor=NS(load_fixed48=Mock(return_value=fixed),AMENDMENT_SOURCE_SHA=old_amendment,
            TRUSTED_SUCCESSOR_RUNTIME=old_successor_runtime,
            registered_successor=Mock(side_effect=verify_scope),
            successor_profile=Mock(side_effect=lambda *a:(verify_scope(),profile)[1]),
            successor_bootstrap=Mock(return_value=NS()))
        args=NS(successor_entry=Path('/SYNTHETIC/d5ca.py'),fixed48_entry=Path('/SYNTHETIC/1a86.py'),
                legacy_bridge=Path('/SYNTHETIC/7e.py'),family_callback=Path('/SYNTHETIC/97a.py'),
                library_dir=Path('/SYNTHETIC/lib'),release=Path(rp['path']),request_id=None,
                state=Path('/SYNTHETIC/native_candidate_publication_family_v3_'+'a'*12))
        with patch.object(f,'load_pinned',side_effect=lambda name,*a:successor if name=='unchanged_successor_v2' else NS()),\
             patch.object(f,'family_library',side_effect=lambda *a:nullcontext()),\
             patch.object(f,'decorate',side_effect=lambda fixed,args,p,cb:p),\
             patch.object(f,'rebind_state',side_effect=lambda bridge,ctx,*a:ctx):
            self.assertEqual(f.execute(args)[0],'RECEIPT')
            self.assertEqual(successor.AMENDMENT_SOURCE_SHA,old_amendment)
            self.assertIs(successor.TRUSTED_SUCCESSOR_RUNTIME,old_successor_runtime)
            # Original aa6d successor support must not inherit fixed48-only pins.
            source['sha256']=old_amendment;expected[:]=[old_amendment,old_successor_runtime]
            self.assertEqual(f.execute(args)[0],'RECEIPT')
            source['sha256']=f.RECOVERY_AMENDMENT_SHA
            successor.registered_successor.side_effect=ValueError('SYNTHETIC_OWNER_REJECTION')
            with self.assertRaisesRegex(ValueError,'SYNTHETIC_OWNER_REJECTION'):f.execute(args)
            self.assertEqual(successor.AMENDMENT_SOURCE_SHA,old_amendment)
            self.assertIs(successor.TRUSTED_SUCCESSOR_RUNTIME,old_successor_runtime)
            calls=successor.registered_successor.call_count
            source['sha256']='e'*64
            with self.assertRaisesRegex(ValueError,'NOT_FROZEN'):f.execute(args)
            self.assertEqual(successor.registered_successor.call_count,calls)


if __name__=='__main__':unittest.main()
