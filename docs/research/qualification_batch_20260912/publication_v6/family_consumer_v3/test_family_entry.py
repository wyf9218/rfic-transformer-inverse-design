"""New loader/profile/state fixtures only; no family4 or physical QA reruns."""
from contextlib import contextmanager
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import family_entry as f


def fixed_module():
    path=Path(__file__).parent.parent/'native_candidate_publication_fixed48_v1/profile_entry.py'
    spec=importlib.util.spec_from_file_location('synthetic_checked_profile_scope',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class FamilyConsumerTests(unittest.TestCase):
    def test_actual_callback_receives_release_scope_old_library_and_prepared_restore(self):
        fixed=fixed_module();history,legacy=object(),object()
        old=NS(history=history,legacy=legacy)
        callback=NS(history=history,legacy=legacy,raw=lambda p:b'{}',build_evidence=Mock(),
            RUNTIME_PINS={'old':'sha'},RELEASE_SHA='old',MANIFEST_SHA='old',INTENT_SHA='old',STUDY='old')
        prepared=NS(c=old);base=NS(EXPECTED_RUNTIME={})
        original_library=Mock(return_value=(Path('/SYNTHETIC/lib'),old,prepared,base))
        bridge=NS(library=original_library,save_once=Mock(),process_one=Mock(),RELEASE_SHA='old',CONFIG_SHA='old')
        original_save=bridge.save_once
        args=NS(library_dir=Path('/SYNTHETIC/lib'),
            state=Path('/SYNTHETIC/native_candidate_publication_fixed48_'+'a'*12))
        profile=dict(release={'sha256':'a'*64},config={'sha256':'b'*64},runtime={'new':'runtime'},
            manifest_sha256='c'*64,intent_sha256='d'*64,study='NEW_BATCH',prior_closed_results=[],
            family_publication={'actual_callback':{'sha256':f.CALLBACK_SHA}})
        with f.family_library(bridge,args,callback):
            self.assertIs(bridge.library(args.library_dir)[1],callback)
            self.assertIs(prepared.c,callback)
            with fixed.parameterized_bridge(bridge,args,profile):
                self.assertEqual(callback.STUDY,'NEW_BATCH')
                self.assertEqual(callback.RUNTIME_PINS,{'new':'runtime'})
                bridge.save_once(NS(),Path('/SYNTHETIC/RECEIVER.json'),{})
                payload=original_save.call_args.args[2]
                self.assertEqual(payload['runtime_profile']['profile']['family_publication']
                                 ['actual_callback']['sha256'],f.CALLBACK_SHA)
            self.assertEqual(callback.STUDY,'old')
        self.assertIs(bridge.library,original_library);self.assertIs(prepared.c,old)

    def test_family_provenance_new_state_and_source_substitution_fail_closed(self):
        rp=dict(path='/SYNTHETIC/RELEASE.json',sha256='a'*64,bytes=1)
        args=NS(state=Path('/SYNTHETIC/native_candidate_publication_family_v3_'+'a'*12),
                successor_entry=Path('/SYNTHETIC/successor.py'))
        callback=NS(__file__='/SYNTHETIC/callback.py')
        def pin(path):
            sha=f.SUCCESSOR_SHA if str(path)==str(args.successor_entry) else (
                f.CALLBACK_SHA if str(path)==callback.__file__ else 'f'*64)
            return dict(path=str(path),sha256=sha,bytes=1)
        fixed=NS(pin=pin);profile=dict(release=rp,authority_pins=[])
        out=f.decorate(fixed,args,profile,callback)
        self.assertEqual(out['family_publication']['actual_callback']['sha256'],f.CALLBACK_SHA)
        self.assertEqual(out['authority_pins'][-1]['sha256'],f.CALLBACK_SHA)
        self.assertEqual(profile['authority_pins'],[])
        check=Mock();bridge=NS(safe_path=lambda p:Path(p).absolute(),check_public=check)
        ctx=NS(state=Path('/SYNTHETIC/native_candidate_publication_fixed48_'+'a'*12),
               union=Path('/SYNTHETIC/qualified15_single_member_v1'),owner=Path('/SYNTHETIC/batch/owner'),
               shared_pins=[])
        f.rebind_state(bridge,ctx,args,out)
        self.assertEqual(ctx.state,args.state);self.assertEqual(check.call_count,1)
        with patch.object(fixed,'pin',return_value={'path':'/SYNTHETIC/other','sha256':'0'*64,'bytes':1}):
            with self.assertRaisesRegex(ValueError,'source identity'):f.decorate(fixed,args,profile,callback)
        args.state=Path('/SYNTHETIC/native_candidate_publication_fixed48_'+'a'*12)
        with self.assertRaisesRegex(ValueError,'family_v3'):f.decorate(fixed,args,profile,callback)

    def test_unfrozen_recovery_stops_before_runtime_or_profile_construction(self):
        fixed=NS(read=Mock(return_value={'code_root':'/SYNTHETIC/runtime'}),
            TRUSTED_AMENDMENT_SOURCE_SHA='a'*64,TRUSTED_FIXED48_RUNTIME={'old':'runtime'},
            construct_owner_profile=Mock())
        release=dict(config={'sha256':'b'*64},sources=[
            dict(path='/SYNTHETIC/runtime/fixed48_amendment.py',sha256='c'*64,bytes=1)])
        with patch.object(f,'RECOVERY_AMENDMENT_SHA',None),patch.object(f,'RECOVERY_RUNTIME',None):
            with self.assertRaisesRegex(ValueError,'NOT_FROZEN'):
                f.owner_validated_profile(fixed,NS(),NS(),{'sha256':'d'*64},release)
        fixed.construct_owner_profile.assert_not_called()


if __name__=='__main__':unittest.main()
