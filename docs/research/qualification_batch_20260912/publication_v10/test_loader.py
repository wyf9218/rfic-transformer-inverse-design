"""New synthetic historical loader tests. Physical core is never executed."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path('/Users/wyf/Documents/模拟变压器AI反向建模')
sys.path.insert(0,str(ROOT/'github_worktrees/eucap15-mlp-capacity-20260909'))
sys.path.insert(0,str(ROOT/'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/samebatch_hotpath_v1/runtime'))
import historical_existing_gds as a


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.eval=self.root/'source/evaluations/actual_tail'
        self.eval.mkdir(parents=True)
        def put(relative,value):
            path=self.eval/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(value if isinstance(value,bytes) else json.dumps(value).encode())
            return a.core.pin(path)
        self.put=put
        fields=list(a.core.GEOMETRY_FIELDS);vector=[200,220,230,240,6,40,45,1,150,160]
        self.s4p=put('emx/emx.s4p',b'SYNTHETIC NOT A REAL TOUCHSTONE')
        artifacts={k:put(p,b'SYNTHETIC GDS' if k in ('direct','gds') else {}) for k,p in {
            'source':'layout/foundry_layout_source_audit.json','power':'layout/power_line_8port_geometry.json',
            'direct':'layout/transformer_layout.gds','gds':'streamout/transformer_layout_cadpins.gds'}.items()}
        proc='/SYNTHETIC/TSMC65_05_12_26/test.proc'
        ports=[dict(name=f'P{i:03d}',signal_labels=[f'P{i:03d}'],ground_labels=[f'P{i:03d}_G']) for i in range(1,5)]
        artifacts['port_manifest']=put('layout/transformer_layout.layout.json',dict(top_cell='TRANSFORMER',
            layout_path=artifacts['direct']['path'],cadence_pin_purpose=51,ports=ports,process_layer_summary=dict(process_file=proc)))
        self.summary=dict(ok=True,error=None,touchstone_path=self.s4p['path'],geometry=dict(zip(fields,vector)),
            geometry_check=dict(ok=True,metrics=dict(skipped=True)),artifacts=dict(cadence_gds=artifacts['gds']['path'],
                export_gds=artifacts['direct']['path'],export_manifest=artifacts['port_manifest']['path'],top_cell='TRANSFORMER'))
        artifacts['summary']=put('summary_cadence_roundtrip.json',self.summary)
        self.command=['/SYNTHETIC/emx',artifacts['gds']['path'],'TRANSFORMER',proc,'-s',self.s4p['path'],'--cadence-pins=51',
            *[f'--port=P{i:03d}=P{i:03d}:P{i:03d}_G' for i in range(1,5)],'--sweep','5000000000','60000000000','--sweep-stepsize','500000000']
        artifacts['emx_command']=put('emx/emx_command.json',self.command)
        evaluation='000000__shard_000__actual_tail'
        source_csv=self.root/'source.csv'
        row=dict(row_index='0',evaluation=evaluation,touchstone_path=self.s4p['path'],touchstone_sha256=self.s4p['sha256'],
            **{'geom__'+k:str(v) for k,v in zip(fields,vector)})
        with source_csv.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
        identity_manifest=put('identity_manifest.json',dict(schema='p215_new_training_source100_gds_current_bindings.v1',rows=[dict(
            source_prefix_ordinal=0,source_row_index=97567,evaluation=evaluation,gds_remote_path=artifacts['gds']['path'],
            gds_sha256=artifacts['gds']['sha256'],historical_s4p_sha256=self.s4p['sha256'],top_cell='TRANSFORMER',
            raw_geometry_identity_sha256='SYNTHETIC_RAW',production_geometry_fingerprint_sha256='SYNTHETIC_PRODUCTION')]))
        s=Path(self.s4p['path']).stat()
        receipt=put('prior_transport.json',dict(schema='p215_training_source100_private_s4p_transport.v1',records=[dict(
            expected=dict(ordinal=0,source_row_index=97567,evaluation=evaluation,path=self.s4p['path'],sha256=self.s4p['sha256']),
            actual=self.s4p,local=self.s4p,status='EXACT_HISTORICAL_SHA_VERIFIED',
            source_stat=[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink])]))
        runtime={k:put('runtime/'+k,b'SYNTHETIC '+k.encode()) for k in ('configuration','foundry_contract')}
        runtime['core_sources']={k:put('runtime/'+k,b'SYNTHETIC '+k.encode()) for k in a.core.CORE_MODULES}
        # Only synthetic authority constants for input parsing, never a backend.
        self.context_patches=[patch.object(a.core,'CONFIG_SHA256',runtime['configuration']['sha256']),
            patch.object(a.core,'FOUNDRY_CONTRACT_SHA256',runtime['foundry_contract']['sha256']),
            patch.object(a.core,'PROVEN_CORE_SHA256',{k:p['sha256'] for k,p in runtime['core_sources'].items()})]
        for p in self.context_patches:p.start();self.addCleanup(p.stop)
        self.request=dict(schema=a.SCHEMA,evaluation_root=str(self.eval),source_row=dict(source_prefix_ordinal=0,
            source_row_index=97567,evaluation=evaluation,source_csv=a.core.pin(source_csv),identity_manifest=identity_manifest),
            geometry_fields=fields,original_geometry=vector,artifacts=artifacts,historical_s4p=self.s4p,
            s4p_verification_receipt=receipt,runtime=runtime)
        self.path=self.root/'request.json';self.save()

    def save(self):self.path.write_text(json.dumps(self.request))

    def test_preserves_original_geometry_s4p_merged_identity_without_body_read(self):
        original=Path.read_bytes
        def forbid_response(path):
            if str(path)==self.s4p['path']:raise AssertionError('S4P body reread')
            return original(path)
        with patch.object(Path,'read_bytes',forbid_response),patch.object(a.core,'_load_backend') as backend:
            request=a.load_request(self.path)
        backend.assert_not_called()
        self.assertEqual(request.summary,self.summary)
        self.assertEqual(request.context['evaluation'],'000000__shard_000__actual_tail')
        self.assertTrue(request.summary['geometry_check']['metrics']['skipped'])
        self.assertEqual(request.context['historical_s4p']['body_reads'],0)
        fn=a.bound_auditor(request)
        self.assertIs(fn.__code__,a.core._audit_candidate.__code__)
        self.assertIs(fn.__globals__['_physical_checks'],a.core._physical_checks)
        self.assertIsNot(fn.__globals__['_candidate_inputs'],a.core._candidate_inputs)
        self.assertEqual(hashlib.sha256(Path(a.core.__file__).read_bytes()).hexdigest(),a.CORE_SHA)

    def test_wrong_original_geometry_command_topcell_and_s4p_bindings_rejected(self):
        originals=json.loads(json.dumps(self.request))
        for defect in ('geometry','gds','topcell','s4p'):
            with self.subTest(defect=defect):
                self.request=json.loads(json.dumps(originals))
                if defect=='geometry':self.request['original_geometry'][0]+=1
                else:
                    command=list(self.command)
                    command[{'gds':1,'topcell':2,'s4p':5}[defect]]='WRONG'
                    self.request['artifacts']['emx_command']=self.put('emx/emx_command.json',command)
                self.save()
                with self.assertRaises(a.core.AuditInputError):a.load_request(self.path)
        self.put('emx/emx_command.json',self.command)

    def test_missing_source_exact_failure_and_no_clobber_without_backend(self):
        Path(self.request['artifacts']['source']['path']).unlink()
        out=self.root/'result'
        with patch.object(a.core,'_load_backend') as backend:
            with self.assertRaisesRegex(FileNotFoundError,'MISSING_HISTORICAL_ARTIFACT: source'):
                a.audit(self.path,out)
        backend.assert_not_called()
        failure=json.loads((out/'HISTORICAL_GDS_FAILURE.json').read_text())
        self.assertFalse(failure['cadence_started']);self.assertFalse(failure['calibre_started']);self.assertFalse(failure['emx_started'])
        with self.assertRaises(FileExistsError):a.audit(self.path,out)


if __name__=='__main__':unittest.main(verbosity=2)
