"""Only synthetic factory registration tests; no real sampler/data/native calls."""
import argparse
import copy
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import successor_factory as F


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='successor_factory_synthetic_')
        self.root=Path(self.tmp.name).resolve();self.registry=self.root/'registry'
        self.calls=0
        dummy=lambda h:dict(path=str(self.root/'not_read'),sha256=h)
        self.request=dict(schema='eucap15_successor_factory_request.v1',batch_id='SYNTHETIC_BATCH_A',
            seeds=dict(doe=1101,neighbor=1102,split=17),recipe=dummy(F.RECIPE_SHA),
            inputs={k:dummy(h) for k,h in F.BASE_SHA.items()},
            sources={k:dummy(h) for k,h in F.SOURCE_SHA.items()},
            known_pool=dict(manifest=dummy('1'*64),file=dummy('2'*64)),
            prior_batches=[dict(manifest=dummy('3'*64),candidates=dummy('4'*64))],
            research_root=str(self.root))
        self.preparer=patch.object(F,'prepare_intent',side_effect=self.fake_prepare);self.preparer.start()
        self.sampler=patch.object(F,'invoke_sampler',side_effect=self.fake_sampler);self.sampler.start()

    def tearDown(self):
        self.sampler.stop();self.preparer.stop();self.tmp.cleanup()

    def request_pin(self,r=None):
        p=self.root/f'request_{len(list(self.root.glob("request_*.json")))}.json'
        F.atomic_json(p,r or self.request,immutable=True);return F.pin(p)

    def fake_prepare(self,r,request_pin):
        return dict(schema='SYNTHETIC_NOT_A_REAL_SAMPLER_INTENT',study_id=r['batch_id'],
            seeds=r['seeds'],suggested_new_budget=dict(maximum_actual_EMX_starts=256,
            maximum_wall_seconds=43200,maximum_incremental_bytes=5368709120,
            requested_parallel_jobs=48,requested_cpu_per_job=2))

    def fake_sampler(self,ip,out,research_root):
        self.calls+=1;out.mkdir();intent=json.loads(Path(ip['path']).read_bytes())
        for name in F.OUTPUT_FILES:
            if name=='PREPARATION_RECEIPT.json':
                value=dict(status='FROZEN_256_PROPOSALS_NOT_NATIVE_RELEASE',study_id=intent['study_id'],
                    source_train_count=3801,counts=dict(GEOMETRY_DOE=dict(proposals=192),
                    TRAIN_NEIGHBORHOOD=dict(proposals=64)),actual_emx_starts=0,model_calls=0,training_updates=0,
                    synthetic_fixture_only=True)
            else:value=dict(synthetic_fixture_only=True,name=name,no_real_geometry=True)
            F.atomic_json(out/name,value,immutable=True)
        F.atomic_json(out/'MANIFEST.json',dict(schema='eucap15_production_input_manifest.v1',
            status='PREPARATION_ONLY_NOT_NATIVE_RELEASE',study_id=intent['study_id'],intent=ip,
            files={n:F.pin(out/n) for n in F.OUTPUT_FILES}),immutable=True)
        return dict(command=['SYNTHETIC_NO_SUBPROCESS'],returncode=0,stdout='',stderr='')

    def test_complete_replay_never_samples_twice(self):
        p=self.request_pin();a=F.generate_once(p,self.registry);b=F.generate_once(p,self.registry)
        self.assertEqual(a['manifest'],b['manifest']);self.assertEqual(self.calls,1)
        self.assertTrue(a['sampled_now']);self.assertFalse(b['sampled_now'])

    def test_same_id_different_input_conflicts(self):
        F.generate_once(self.request_pin(),self.registry)
        r=copy.deepcopy(self.request);r['seeds']['doe']=2201
        with self.assertRaisesRegex(ValueError,'BATCH_INPUT_CONFLICT'):F.generate_once(self.request_pin(r),self.registry)
        self.assertEqual(self.calls,1)

    def test_partial_registration_never_samples(self):
        (self.registry/'batches'/self.request['batch_id']).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'PARTIAL_REGISTRATION'):F.generate_once(self.request_pin(),self.registry)
        self.assertEqual(self.calls,0)

    def test_failed_sampler_retains_failure_and_never_resamples(self):
        p=self.request_pin()
        with patch.object(F,'invoke_sampler',return_value=dict(returncode=9,command=['SYNTHETIC_FAIL'],stdout='',stderr='fixture')) as mocked:
            with self.assertRaisesRegex(ValueError,'SAMPLER_FAILED'):F.generate_once(p,self.registry)
            with self.assertRaisesRegex(ValueError,'PARTIAL_OUTPUT'):F.generate_once(p,self.registry)
            self.assertEqual(mocked.call_count,1)
        self.assertTrue((self.registry/'batches'/self.request['batch_id']/'FAILURE.json').is_file())

    def test_complete_manifest_recovers_missing_wrapper_without_sampling(self):
        # The sampler fully froze its own manifest; wrapper completion was interrupted.
        p=self.request_pin();r=self.request;batch=self.registry/'batches'/r['batch_id'];batch.mkdir(parents=True)
        ipath=batch/'INTENT.json';F.atomic_json(ipath,self.fake_prepare(r,p),immutable=True);ip=F.pin(ipath)
        F.atomic_json(batch/'REGISTERED.json',dict(batch_id=r['batch_id'],request_sha256=p['sha256'],intent=ip),immutable=True)
        self.fake_sampler(ip,batch/'run_v1',r['research_root']);self.calls=0
        got=F.generate_once(p,self.registry)
        self.assertFalse(got['sampled_now']);self.assertEqual(self.calls,0)
        self.assertTrue(json.loads((batch/'COMPLETED.json').read_bytes())['recovered_complete_manifest_without_resampling'])

    def test_used_seed_rejects_different_batch_and_cross_role(self):
        F.generate_once(self.request_pin(),self.registry)
        r=copy.deepcopy(self.request);r['batch_id']='SYNTHETIC_BATCH_B';r['seeds']=dict(doe=2201,neighbor=1101,split=17)
        with self.assertRaisesRegex(ValueError,'SEED_ALREADY_REGISTERED'):F.generate_once(self.request_pin(r),self.registry)
        self.assertEqual(self.calls,1)

    def test_distinct_batch_and_seed_pair_get_distinct_output_identity(self):
        a=F.generate_once(self.request_pin(),self.registry)
        r=copy.deepcopy(self.request);r['batch_id']='SYNTHETIC_BATCH_B';r['seeds']=dict(doe=2201,neighbor=2202,split=17)
        b=F.generate_once(self.request_pin(r),self.registry)
        self.assertNotEqual(a['manifest']['path'],b['manifest']['path']);self.assertNotEqual(a['manifest']['sha256'],b['manifest']['sha256'])
        self.assertEqual(self.calls,2)

    def test_changed_complete_artifact_is_not_silently_accepted(self):
        p=self.request_pin();a=F.generate_once(p,self.registry)
        # Corruption injected only into disposable synthetic output.
        artifact=Path(a['manifest']['path']).parent/'RESOURCE_CHECK.json'
        artifact.write_text('{"synthetic_corruption":true}\n')
        with self.assertRaisesRegex(ValueError,'source pin changed'):F.generate_once(p,self.registry)
        self.assertEqual(self.calls,1)

    def test_registration_directory_identity_is_checked(self):
        p=self.request_pin();batch=self.registry/'batches'/self.request['batch_id'];batch.mkdir(parents=True)
        F.atomic_json(batch/'REGISTERED.json',dict(batch_id='SYNTHETIC_OTHER',request_sha256=p['sha256']),immutable=True)
        with self.assertRaisesRegex(ValueError,'registration identity'):F.generate_once(p,self.registry)
        self.assertEqual(self.calls,0)

    def test_seed_and_split_contract_rejected_before_sampling(self):
        for values in (dict(doe=True,neighbor=4,split=17),dict(doe=-1,neighbor=4,split=17),
            dict(doe=4,neighbor=4,split=17),dict(doe=4,neighbor=5,split=18),dict(doe=2**32,neighbor=4,split=17)):
            with self.subTest(values=values):
                r=copy.deepcopy(self.request);r['seeds']=values
                with self.assertRaises(ValueError):F.generate_once(self.request_pin(r),self.registry)
        self.assertEqual(self.calls,0)

    def test_frozen_source_identity_cannot_be_changed(self):
        for group,key in (('inputs','current_source_rows'),('sources','physics')):
            r=copy.deepcopy(self.request);r[group][key]['sha256']='f'*64
            with self.assertRaisesRegex(ValueError,'identity changed'):F.generate_once(self.request_pin(r),self.registry)
        self.assertEqual(self.calls,0)

    def test_unsafe_batch_id_rejected(self):
        r=copy.deepcopy(self.request);r['batch_id']='../ANOTHER_BATCH'
        with self.assertRaisesRegex(ValueError,'unsafe batch identity'):F.generate_once(self.request_pin(r),self.registry)
        self.assertEqual(self.calls,0)

    def test_readonly_check_lists_multiple_missing_pins_without_registration(self):
        got=F.check_bindings(self.request_pin())
        self.assertEqual(got['status'],'MISSING_OR_CHANGED_BINDINGS')
        self.assertGreater(len(got['missing']),2);self.assertEqual(self.calls,0)
        self.assertFalse(self.registry.exists());self.assertFalse(got['installed'])


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.absolute();out.mkdir(exist_ok=False)
    started=time.monotonic();stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FactoryTests))
    with (out/'TEST_LOG.txt').open('x') as f:f.write(stream.getvalue())
    value=dict(status='PASS' if result.wasSuccessful() else 'FAIL',tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),completed_utc=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=time.monotonic()-started,scope='SYNTHETIC_FACTORY_REGISTRATION_ONLY',
        real_candidate_generation_calls=0,emx_starts=0,old_QA_runs=0,source=F.pin(__file__),factory=F.pin(F.__file__),log=F.pin(out/'TEST_LOG.txt'))
    F.atomic_json(out/'TEST_RECEIPT.json',value,immutable=True)
    print(json.dumps(value));raise SystemExit(0 if result.wasSuccessful() else 1)
