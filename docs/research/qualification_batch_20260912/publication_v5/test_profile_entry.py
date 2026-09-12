"""New profile isolation tests; no runtime, native result, or ledger is read."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

import profile_entry as p


def fixture():
    release=dict(path='/SYNTHETIC/new/RELEASE.json',sha256='f'*64,bytes=42)
    config=dict(path='/SYNTHETIC/new/CONFIG.json',sha256='e'*64,bytes=43)
    profile=dict(kind='SCHEDULING_ONLY_FIXED48',release=release,config=config,
        parent_release=dict(path='/SYNTHETIC/parent/RELEASE.json',sha256=p.OLD_RELEASE_SHA,bytes=44),
        runtime=dict.fromkeys(('controlled_metadata.py','controlled_result.py','controlled_execution.py',
            'native_birth.py','start_slots.py','native_resource_probe.py','geometry_helpers.py'),'a'*64),
        manifest_sha256='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb',
        intent_sha256='9de9dc38074df0dc1441dafeb9b8b6b1038e71ec40ffa653a1880854b6a51d42',
        study='eucap15_production_doe_neighborhood_20260912_v1',authority_pins=[],prior_closed_results=[])
    return release,dict(config=config),profile


class ProfileTests(unittest.TestCase):
    def test_unknown_release_fails_before_any_runtime_import(self):
        rp,release,_=fixture()
        with patch.object(p,'FIXED48_PROFILES',{}),self.assertRaisesRegex(ValueError,'NOT_FROZEN'):
            p.select_profile(rp,release)

    def test_registered_identity_rejects_config_runtime_and_dataset_mixing(self):
        rp,release,profile=fixture()
        with patch.object(p,'FIXED48_PROFILES',{rp['sha256']:profile}):
            self.assertEqual(p.select_profile(rp,release),profile)
            bad=copy.deepcopy(release);bad['config']['sha256']='b'*64
            with self.assertRaises(ValueError):p.select_profile(rp,bad)
        for change in ('runtime','manifest_sha256','parent_release'):
            bad=copy.deepcopy(profile)
            if change=='runtime':bad[change].pop('controlled_result.py')
            elif change=='parent_release':bad[change]['sha256']='0'*64
            else:bad[change]='0'*64
            with patch.object(p,'FIXED48_PROFILES',{rp['sha256']:bad}),self.assertRaises(ValueError):
                p.select_profile(rp,release)

    def modules(self):
        oldbuild=Mock(side_effect=lambda inputs,rid,reader:copy.deepcopy(inputs))
        c=NS(raw=lambda x:b'{}',build_evidence=oldbuild,history=NS(_normalized_pin=lambda x:x),
            RUNTIME_PINS={'old':'runtime'},RELEASE_SHA='old_release',MANIFEST_SHA='old_manifest',
            INTENT_SHA='old_intent',STUDY='old_study')
        reader=NS(EXPECTED_RUNTIME={'old':'runtime'})
        saved=[]
        def save(ctx,path,value):saved.append(copy.deepcopy(value));return {'synthetic_pin':True}
        bridge=NS(RELEASE_SHA='old_release',CONFIG_SHA='old_config',save_once=save,process_one=Mock(),
            library=lambda path:('/SYNTHETIC/lib',c,NS(),reader))
        return bridge,c,reader,oldbuild,saved

    def test_scoped_profile_restores_constants_and_records_explicit_provenance(self):
        _,_,profile=fixture();bridge,c,reader,oldbuild,saved=self.modules()
        args=NS(library_dir='/SYNTHETIC/lib',state='/SYNTHETIC/native_candidate_publication_fixed48_'+'f'*12)
        oldlib=bridge.library;oldsave=bridge.save_once
        with p.parameterized_bridge(bridge,args,profile):
            self.assertEqual(c.RELEASE_SHA,profile['release']['sha256'])
            self.assertEqual(reader.EXPECTED_RUNTIME,profile['runtime'])
            self.assertEqual(bridge.CONFIG_SHA,profile['config']['sha256'])
            bridge.save_once(None,Path('/SYNTHETIC/BINDING.json'),{})
            self.assertEqual(saved[0]['runtime_profile']['profile'],profile)
            bridge.save_once(None,Path('/SYNTHETIC/runs/delta.json'),{'schema':'eucap15_native_terminal_publication_delta.v1'})
            self.assertEqual(saved[1]['runtime_profile']['adapter'],p.pin(p.__file__))
        self.assertEqual(c.RELEASE_SHA,'old_release');self.assertEqual(reader.EXPECTED_RUNTIME,{'old':'runtime'})
        self.assertIs(c.build_evidence,oldbuild);self.assertIs(bridge.save_once,oldsave);self.assertIs(bridge.library,oldlib)

    def test_old_cache_and_other_profile_cannot_be_used_for_new_publication(self):
        _,_,profile=fixture();bridge,c,reader,_,_=self.modules()
        args=NS(library_dir='/SYNTHETIC/lib',state='/SYNTHETIC/native_candidate_publication_fixed48_'+'f'*12)
        with p.parameterized_bridge(bridge,args,profile):
            inputs=dict(consumer_receipt={'path':'/SYNTHETIC/cache'})
            with self.assertRaisesRegex(ValueError,'mixing'):
                c.build_evidence(inputs,'SYNTHETIC',reader=lambda x:b'{}')
            cached=dict(runtime_profile=p.profile_evidence(profile),execution_release=profile['release'],
                source_runtime=profile['runtime'])
            def read(identity):return json.dumps(cached).encode() if identity['path']=='/SYNTHETIC/cache' else b'fixture source'
            out=c.build_evidence(inputs,'SYNTHETIC',reader=read)
            self.assertEqual(out['native_runtime_profile'],p.profile_evidence(profile))
            bad=copy.deepcopy(out);bad['native_runtime_profile']['profile']['release']['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'foreign qualification'):
                c.build_evidence(bad,'SYNTHETIC',reader=read)

    def test_new_release_cannot_share_old_or_other_release_state(self):
        _,_,profile=fixture();bridge,*_=self.modules()
        for name in ('native_candidate_publication_v1','native_candidate_publication_fixed48_'+'e'*12):
            args=NS(library_dir='/SYNTHETIC/lib',state='/SYNTHETIC/'+name)
            with self.assertRaisesRegex(ValueError,'own immutable'):
                with p.parameterized_bridge(bridge,args,profile):pass

    def test_old_release_runs_pristine_bridge_without_profile_or_source_relabel(self):
        bridge=NS(bootstrap=Mock(return_value='OLD_CTX'),run=Mock(return_value=('oldpin','oldreceipt')))
        args=NS(legacy_bridge='OLD_ENTRY',release='OLD_RELEASE',request_id='OLD_REQUEST')
        rp=dict(path='OLD_RELEASE',sha256=p.OLD_RELEASE_SHA,bytes=1)
        with patch.object(p,'load_bridge',return_value=bridge),patch.object(p,'pin',return_value=rp),\
             patch.object(p,'read',return_value={}),patch.object(p,'select_profile',side_effect=AssertionError('old profile changed')):
            result=p.execute(args)
        self.assertEqual(result,('oldpin','oldreceipt'));bridge.bootstrap.assert_called_once_with(args)
        bridge.run.assert_called_once_with('OLD_CTX','OLD_REQUEST')

    def test_unfrozen_owner_validator_fails_before_library_or_runtime_import(self):
        rp,release,_=fixture();bridge=NS(library=Mock(side_effect=AssertionError('must not import')))
        with patch.object(p,'TRUSTED_FIXED48_RUNTIME',{}),self.assertRaisesRegex(ValueError,'VALIDATOR_NOT_FROZEN'):
            p.construct_owner_profile(bridge,NS(),rp,release)
        bridge.library.assert_not_called()

    def test_prior_closed_identity_is_retained_without_new_profile_reconsumption(self):
        _,_,profile=fixture();bridge,*_=self.modules();original=bridge.process_one
        prior=dict(path='/SYNTHETIC/old/RID/RESULT.json',sha256='b'*64,bytes=99)
        profile['prior_closed_results']=[prior]
        args=NS(library_dir='/SYNTHETIC/lib',state='/SYNTHETIC/native_candidate_publication_fixed48_'+'f'*12)
        ctx=NS(config={'candidate_roots':{'RID':'/SYNTHETIC/old/RID'}})
        with p.parameterized_bridge(bridge,args,profile):
            value=bridge.process_one(ctx,{'request_id':'RID'},[])
        self.assertEqual(value['status'],'PRIOR_RELEASE_CLOSED_RETAINED_NOT_RECONSUMED')
        self.assertEqual(value['original_result'],prior);original.assert_not_called()

    def test_profile_builder_delegates_only_to_existing_pinned_owner_validator(self):
        rp,release,profile=fixture();runtime=profile['runtime']
        amendment=dict(parent_release=profile['parent_release'],prior_results=[],authorizations=[])
        config=dict(code_root='/SYNTHETIC/runtime',concurrency_amendment=amendment,
            original_manifest={'path':'/SYNTHETIC/MANIFEST','sha256':profile['manifest_sha256'],'bytes':1},path_map={})
        release.update(concurrency_amendment=amendment,sources=[
            dict(path='/SYNTHETIC/runtime/'+name,sha256=sha,bytes=1)
            for name,sha in {**runtime,'fixed48_amendment.py':'c'*64}.items()])
        batch=NS(rows=[{}]*256,manifest_pin=config['original_manifest'],
            intent_pin={'sha256':profile['intent_sha256']})
        metadata=NS(load_batch=Mock(return_value=batch),
            read_pin=Mock(return_value=json.dumps({'study_id':profile['study']}).encode()))
        execution=NS(plan_release=Mock(return_value={'path':'/SYNTHETIC/ORIGIN','sha256':'d'*64,'bytes':1}))
        base=NS(EXPECTED_RUNTIME={},runtime=Mock(return_value=(metadata,None,execution,None)))
        bridge=NS(library=Mock(return_value=(None,None,None,base)))
        with patch.object(p,'TRUSTED_FIXED48_RUNTIME',runtime),patch.object(p,'TRUSTED_AMENDMENT_SOURCE_SHA','c'*64),\
             patch.object(p,'FIXED48_PROFILES',{}),patch.object(p,'read',return_value=config),\
             patch.object(p,'bytes_for',return_value=b'SYNTHETIC_ONLY'):
            got=p.construct_owner_profile(bridge,NS(library_dir='/SYNTHETIC/lib'),rp,release)
        execution.plan_release.assert_called_once_with(release,config)
        self.assertEqual(got['release'],rp);self.assertEqual(got['runtime'],runtime)
        self.assertEqual(got['owner_validator']['sha256'],'c'*64)
        self.assertEqual(base.EXPECTED_RUNTIME,{})


if __name__=='__main__':unittest.main(verbosity=2)
