import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import qualification as a

R = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
W = R/'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
Q = W/'history_queue_delta_v2/history_qualification_v2.py'
G = R/'reports/eucap15_native_owner_20260909T062500Z/qualified15_holdout_partition_v3/geometry_helpers.py'


class Historical111Readiness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.p = Path(self.temp.name).resolve()
        self.contract = self.write('current.yaml', b'target:\n  band_points: 56\n')
        helpers = a._load(G, 'geometry_helpers', a.GEOMETRY_SHA)
        self.q = a._load(Q, '_historical111_existing_qualifier', a.QUALIFIER_SHA)
        self.geometry = [200., 210., 190., 180., 12., 40., 45., 8., 30., 35.]
        self.fields = list(helpers.GEOMETRY_FIELDS)
        self.ids = self.q.identities(self.geometry, self.fields)
        self.s4p = self.write('old.s4p', b'FIXTURE_ALREADY_EXTRACTED_HISTORICAL_RESPONSE')
        self.gds = self.write('old.gds', b'FIXTURE_GDS_NOT_EXECUTED')
        self.row = dict(source_row_index=97567, source_prefix_ordinal=0, evaluation='fixture-original',
            gds_sha256=self.gds['sha256'], raw_geometry_identity_sha256='a'*64,
            production_geometry_fingerprint_sha256=self.ids['production_1e6'],
            historical_s4p_sha256=self.s4p['sha256'])
        self.manifest = dict(rows=[self.row], current_configuration=self.contract)
        label = dict(lp_nh=1.,ls_nh=1.,qp=30.,qs=32.,qmin=30.,signed_k=.4,k_abs=.4,
                     strict_lumped_valid='true',below_half_srf='true',broadband_descriptor_valid='true',frequency_hz=15_000_000_000)
        common = dict(source_row_index=97567,evaluation='fixture-original',merge_source='fixture_history',source=self.s4p)
        self.target = dict(common,row=label,frequency_hz=15_000_000_000,original_frequency_index_zero_based=20)
        self.summary = dict(common,summary=dict(checks={k:True for k in a.EXTRACTION_REQUIRED},
            frequency_points=111,frequency_start_hz=5_000_000_000,frequency_stop_hz=60_000_000_000,
            frequency_step_hz=500_000_000,port_count=4,primary_srf={'status':'FIXTURE'},secondary_srf={'status':'FIXTURE'}))
        self.large = self.write('all111.csv', b'FIXTURE_SAVED111_OUTPUT_NOT_REPARSED')
        header = ['evaluation','touchstone_sha256']+['geom__'+k for k in self.fields]
        row = ['fixture-original',self.s4p['sha256']]+[str(v) for v in self.geometry]
        csvpin = self.write('source100.csv', (','.join(header)+'\n'+','.join(row)+'\n').encode())
        self.csvpin = csvpin
        self.geometry_pin = self.write('geometry_binding.json',dict(source_row_index=97567,
            evaluation='fixture-original',geometry=self.geometry,geometry_fields=self.fields,
            source_prefix_ordinal=0,original_csv_pin=csvpin))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        data = value if isinstance(value,bytes) else json.dumps(value,sort_keys=True).encode()
        p = self.p/name
        p.write_bytes(data)
        return dict(path=str(p),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))

    def context(self):
        manifest=self.write('manifest.json',self.manifest)
        targets=self.write('targets.json',[self.target])
        summaries=self.write('summaries.json',[self.summary])
        receipt=self.write('receipt.json',dict(schema='p215_explicit100_actual_historical111_conditional_reextraction.v1',
            extractor={'sha256':a.EXTRACTOR_SHA},frequency_resampling=False,native_actions=0,formally_added=0,
            target_index_zero_based=20,extracted=1,retained_frequency_rows=111,outputs=[targets,summaries,self.large]))
        ready=self.write('readiness.json',dict(schema='p215_training_source93_existing_provenance_readiness.v1',source_labels=targets,
            members=[dict(source_row_index=97567,evaluation='fixture-original',s4p=self.s4p,gds=self.gds,
                original_split='UNKNOWN_NOT_DECLARED_IN_SUPPLIED_SOURCE')]))
        return a.load_context(contract_pin=self.contract,source_manifest_pin=manifest,extraction_receipt_pin=receipt,
            target_rows_pin=targets,per_source_pin=summaries,all111_pin=self.large,readiness_pin=ready,
            geometry_helpers_path=G,qualification_path=Q)

    def assess(self, ctx, **kwargs):
        return a.assess_member(ctx,97567,geometry=self.geometry,geometry_fields=self.fields,
                              geometry_evidence_pin=self.geometry_pin,**kwargs)

    def test_111_cannot_be_relabelled56_and_large_output_is_not_rehashed(self):
        original=Path.read_bytes
        def guarded(p):
            if str(p)==self.large['path']:raise AssertionError('saved large CSV must not be read or rehashed')
            return original(p)
        with patch.object(Path,'read_bytes',guarded):
            ctx=self.context(); result=self.assess(ctx)
        self.assertEqual(result['original_response_frequency_count'],111)
        self.assertFalse(result['original111']['all111']['sha256_recomputed_this_call'])
        self.summary['summary']['frequency_points']=56
        result=self.assess(self.context())
        self.assertEqual(result['status'],'incompatible')
        self.assertIn('111 source cannot',result['reason'])

    def test_missing_actual_drc_is_missing_evidence_not_formal_pass(self):
        known={k:{} for k in self.ids};before=copy.deepcopy(known)
        result=self.assess(self.context(),known_identities=known)
        self.assertEqual(result['status'],'missing_evidence')
        self.assertIn('actual_calibre_zero_blocking_receipt_deck_and_exact_gds_binding',result['missing_evidence'])
        self.assertIn('complete_actual_gds_required_checks_receipt',result['missing_evidence'])
        self.assertFalse(result['q10_to20_supported'])
        self.assertEqual(result['formal_added'],0)
        self.assertFalse(result['formal_qualified'])
        self.assertIsNone(result['production_accepted_sequence'])
        self.assertIsNone(result['preserved_split'])
        self.assertEqual(known,before)

    def test_original_validation_preserved_unknown_not_assigned_wrong_train_rejected(self):
        splits=self.write('split.json',dict(schema='bb_splits.v1',seed=17,
            by_geometry_sha256={self.ids['canonical_9dp']:'validation'}))
        ctx=self.context()
        result=self.assess(ctx,original_split='validation',split_evidence_pin=splits)
        self.assertEqual(result['preserved_split'],'validation')
        self.assertEqual(result['split_status'],'PRESERVED_SOURCE_SPLIT_NOT_NEW_ASSIGNMENT')
        self.assertFalse(result['final_independent_test_eligible'])
        self.assertEqual(result['status'],'missing_evidence')
        wrong=self.assess(ctx,original_split='train',split_evidence_pin=splits)
        self.assertEqual(wrong['status'],'incompatible')
        self.assertIn('not the evidenced original split',wrong['reason'])
        self.assertEqual(json.loads(self.q.read(splits))['by_geometry_sha256'][self.ids['canonical_9dp']],'validation')
        self.assertEqual(len(ctx.csv_rows),1)

    def test_direct_original_csv_uses_manifest_ordinal_and_cached_parse(self):
        ctx=self.context()
        direct=a.assess_member(ctx,97567,geometry=self.geometry,geometry_fields=self.fields,
                               geometry_evidence_pin=self.csvpin)
        self.assertEqual(direct['status'],'missing_evidence')
        self.assertEqual(direct['identities'],self.ids)
        self.assertEqual(direct['geometry_evidence'],self.csvpin)
        self.assertIsNone(direct['preserved_split'])
        first_rows=ctx.csv_rows[self.csvpin['path']]
        wrong=a.assess_member(ctx,97567,geometry=self.geometry,geometry_fields=list(reversed(self.fields)),
                              geometry_evidence_pin=self.csvpin)
        self.assertEqual(wrong['status'],'incompatible')
        self.assertIn('original CSV geometry/order',wrong['reason'])
        self.assertIs(ctx.csv_rows[self.csvpin['path']],first_rows)


if __name__=='__main__':unittest.main()
