"""Full synthetic128 snapshot fixture; no real data/model/native operations."""
from copy import deepcopy
import csv
import io
import json
import struct
import importlib.util
import pytest
from pathlib import Path

from research.broadband56_nn import eucap15_development128_results as results
from research.broadband56_nn import eucap15_selected_evidence as evidence
from research.broadband56_nn import eucap15_development128_failed_evidence as failed
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import gds_timestamp_normalized_sha256
from tests.test_eucap15_development128_results import SnapshotFixture, _base
from tests.fixtures.eucap15_selected_evidence_fixture import SyntheticEvidence, pin, sha


class IncrementalFixture(SnapshotFixture):
    def __init__(self,root,monkeypatch):
        super().__init__(root)
        # The older metadata-only fixture intentionally lacked the field that
        # the real completed-evidence scope validator requires.
        self.freeze['tie_break']='EXACT_TIE_SMALLER_Q'
        self.freeze_pin=_base.save(self.root/'PILOT_FREEZE.json',self.freeze)
        self.manifest['freeze']=self.freeze_pin
        self.reseal()
        self.config['original_manifest']=self.manifest_pin
        self.config['path_map']={source['path']:str(self.native/'transport'/Path(source['path']).name)
            for source in [*self.qa['source_pins'],self.qa_pin]}
        self.reseal_config()
        for i,item in enumerate(self.items[:35]):
            self.snapshot['requests'][i]['result']=self.publish(item['request_id']+'/RESULT.json',self.analytic_failure(item))
        self.seal_snapshot()
        self.bind_constants(monkeypatch)
        monkeypatch.setattr(evidence,'DEVELOPMENT_MANIFEST_SHA',self.manifest_pin['sha256'])
        monkeypatch.setattr(evidence,'DEVELOPMENT_QA_SHA',self.qa_pin['sha256'])
        monkeypatch.setattr(evidence,'DEVELOPMENT_QA_BYTES',self.qa_pin['bytes'])
        self.chains={}
        self.install_synthetic_release(monkeypatch)

    def install_synthetic_release(self,monkeypatch):
        shared=self.root/'UNCREATED_SHARED_IDENTITY'
        self.owner_code=self.publish_raw(shared/'code/run_development_native.py',b'# synthetic owner, never executed\n')
        self.wrapper_code=self.publish_raw(shared/'code/research/broadband56_nn/frequency_research_emx.py',b'# synthetic wrapper, never executed\n')
        runtime_sources={name:self.publish_raw(shared/'runtime'/relative,('# synthetic '+name+'\n').encode())
            for name,relative in failed.FAILURE_SOURCE_PATH.items()}
        self.process_file=self.publish_raw(shared/'private/process.proc',b'SYNTHETIC PROCESS ONLY\n')
        self.emx_wrapper=self.publish_raw(shared/'private/emx-wrapper',b'SYNTHETIC NEVER EXECUTED\n')
        self.private_config=self.publish_raw(shared/'private/config.yaml',b'SYNTHETIC CONFIG ONLY\n')
        self.deck=self.publish_raw(shared/'private/deck.drc',b'SYNTHETIC DECK ONLY\n')
        self.runtime=dict(repo=str(shared/'runtime'),source_pins=list(runtime_sources.values()),
            process_file=self.process_file,emx_wrapper=self.emx_wrapper)
        self.config.update(configuration=self.private_config,python='/SYNTHETIC/NEVER_EXECUTE/python',
            code_root=str(shared/'code'),source_pins=[self.owner_code,self.wrapper_code],
            emx_runtime=self.runtime,dispatch_deadline_utc='2026-01-02T00:00:00Z',
            resource_budget={'max_native_concurrency':1,'cpu_per_native':2},
            global_lock_path=str(self.native/'SYNTHETIC_NEVER_LOCK'))
        self.config_pin=self.publish('CONFIG.json',self.config)
        self.release_pin=self.publish('RELEASE.json',dict(
            schema='eucap15_development128_delegated_prepared_release.v1',
            config=self.config_pin,sources=[self.owner_code,self.wrapper_code]))
        self.snapshot['release']=self.release_pin
        self.bind_constants(monkeypatch)
        # Only opaque source/deck identities are fixture constants; no reader
        # functions, arithmetic, identity gates or native calls are mocked.
        monkeypatch.setattr(failed,'RELEASE_SHA',self.release_pin['sha256'])
        monkeypatch.setattr(failed,'OWNER_SHA',self.owner_code['sha256'])
        monkeypatch.setattr(failed,'WRAPPER_SHA',self.wrapper_code['sha256'])
        monkeypatch.setattr(failed,'FAILURE_SOURCE_SHA',{k:p['sha256'] for k,p in runtime_sources.items()})
        monkeypatch.setattr(failed,'FOUNDRY_DECK_SHA256',self.deck['sha256'])

    def chain(self,index,kind='success',*,strict=True):
        item=self.items[index];rid=item['request_id'];cid=item['candidate_id']
        ctx=self.native_context(index);binding=ctx.development_binding
        records=[json.loads(line) for line in Path(item['source_records']['path']).read_text().splitlines()]
        chosen=records[item['record_line_number']-1]
        geometry=item['candidate_geometry_identity_sha256'];candidate_sha=sha(cid.encode())
        paths={};order=[]
        def blob(key,relative,raw):
            paths[key]=self.publish_raw(rid+'/'+relative,raw);return paths[key]
        def put(key,relative,obj):
            result=blob(key,relative,(json.dumps(obj,sort_keys=True,allow_nan=False)+'\n').encode())
            order.append(key);return result
        def rec(kind,dtype=0,payload=b''):
            return struct.pack('>HBB',len(payload)+4,kind,dtype)+payload
        raw_gds=rec(0,2,b'\x02\x58')+rec(1,2,b'\x00\x01'*12)+rec(5,2,b'\x00\x02'*12)+rec(7)+rec(4)
        gds=blob('gds','cadence_only/geometry.gds',raw_gds)
        ports=blob('ports','cadence_only/ports.json',b'{"synthetic":true}\n')
        normalized=gds_timestamp_normalized_sha256(Path(self.entries[gds['path']]['resolved']['path']))
        geometry_pin=put('geometry','gds_audit/GEOMETRY_AUDIT.json',dict(overall_status='PASS',
            candidate_id_sha256=candidate_sha,candidate_geometry_identity_sha256=geometry,
            gds_path=gds['path'],gds_sha256=gds['sha256'],gds_timestamp_normalized_sha256=normalized,
            checks={k:True for k in failed.GEOMETRY_CHECKS},original_artifacts=[gds,ports]))
        audited=[dict(candidate_id=r['candidate_id'],status='NOT_REQUESTED_MAIN_PILOT',
            audit_attempted=False,cadence_routed=False,calibre_eligible=False) for r in records]
        audited[item['q_proxy']-10]=dict(candidate_id=cid,status='PASS',candidate_id_sha256=candidate_sha,
            candidate_geometry_identity_sha256=geometry,gds=gds,port_manifest=ports,geometry_audit=geometry_pin)
        original_sources=dict(eleven_records=ctx.records_pin,qscan_freeze=ctx.freeze_pin,
            selected_manifest=ctx.manifest_pin,reference=ctx.reference_pin,private_config=self.private_config)
        audit=put('audit','gds_audit/REQUEST_GDS_AUDIT.json',dict(
            schema='eucap15_selected_request_gds_audit.v1',physical_selection='Q_PROXY_ONLY',
            selected_candidate_id=cid,N_selected=1,N_audit_attempted=1,original_request_denominator=128,
            development_binding=binding,source_pins=original_sources,records=audited))
        drc_report=blob('drc_report','calibre/drc.report',b'SYNTHETIC ZERO-BLOCKING REPORT\n')
        drc=put('drc','calibre/summary.json',dict(overall_status='PASS',blocking_drc_violation_count=0,
            drc_scope='foundry_macro_ip_back_end',candidate_id_sha256=candidate_sha,
            candidate_geometry_identity_sha256=geometry,gds_path=gds['path'],gds_sha256=gds['sha256'],
            geometry_audit_sha256=geometry_pin['sha256'],gds_timestamp_normalized_sha256=normalized,
            process_token='/TSMC65_05_12_26/',gds_top_cell='TRANSFORMER',
            drc_source_rule_deck_path=self.deck['path'],drc_source_rule_deck_sha256=self.deck['sha256'],
            drc_report_path=drc_report['path'],drc_report_sha256=drc_report['sha256'],
            checks={k:True for k in (*failed.GEOMETRY_CHECKS,'foundry_drc_pass',
                'no_blocking_drc_violations','calibre_result_accounting_complete')}))
        index_pin=self.publish_rows(rid+'/calibre/drc_index.csv',[dict(candidate_id_sha256=candidate_sha,
            overall_status='PASS',drc_summary_path=drc['path'],drc_summary_sha256=drc['sha256'])])
        paths['calibre_index']=index_pin
        common=dict(candidate_id=cid,model_id=self.manifest['model_id'],dataset_scope=self.manifest['dataset_scope'],
            frequency_ghz=15,q_requested=item['q_proxy'],q_proxy=item['q_proxy'])
        selection=dict(physical_selection='Q_PROXY_ONLY',selected_manifest=ctx.manifest_pin,
            reference=ctx.reference_pin,original_request_denominator=128,
            unselected_physical_status='NOT_REQUESTED_MAIN_PILOT',development_binding=binding)
        put('gds_request','GDS_REQUEST.json',dict(schema='eucap15_selected_gds_audit_request.v1',
            selected_manifest=ctx.original_manifest_pin,development_binding=binding,
            production_campaign_membership=False,source_pins=original_sources,
            request=dict(request_id=rid,q_proxy=item['q_proxy'],model_id=self.manifest['model_id'],
                dataset_scope=self.manifest['dataset_scope'],frequency_ghz=15,target_source=results.SOURCE),
            cadence=dict(root=str(self.native/rid/'cadence_only'),routes={cid:'parallel_shards/shard_000'})))
        request=put('request','EMX_REQUEST.json',dict(**common,schema='eucap15_selected_emx_request.v1',
            request_id=rid,target_source=results.SOURCE,development_binding=binding,
            production_campaign_membership=False,records=ctx.records_pin,qscan_freeze=ctx.freeze_pin,
            selected_manifest=ctx.original_manifest_pin,gds_audit=audit,calibre_index=index_pin,
            private_config=self.private_config,runtime=self.runtime,
            **{k:self.config[k] for k in ('dispatch_deadline_utc','resource_budget','global_lock_path')}))
        command=[self.emx_wrapper['path'],gds['path'],'--sweep','5000000000','60000000000',
            '--sweep-stepsize','1000000000','--parallel=2','--s-impedance=50','--cadence-pins=51',
            '--simultaneous-frequencies=0',*[f'--port={p}={p}:{p}_G' for p in ('P001','P002','P003','P004')]]
        statuses=[dict(candidate_id=r['candidate_id'],status=r['status']) for r in audited]
        proof=put('proof','emx_selected/PREFLIGHT.json',dict(**common,**selection,q_emx=None,
            schema=evidence.PROOF_SCHEMA,status='PASS',request_id=rid,candidate_id_sha256=candidate_sha,
            geometry_sha256=geometry,original_record=chosen,protocol=ctx.comparison_protocol,
            executed_hit_tolerances=ctx.executed_hit_tolerances,frequency_grid_hz=evidence.FREQUENCIES,
            port_order=['P001','P002','P003','P004'],port_permutation=[0,1,3,2],
            config_differential_port_pairs=[[0,1],[2,3]],reference_ohm=50,
            full11_physical_optimum='NOT_EVALUATED_SINGLE_PRESELECTED_CANDIDATE',production_membership=False,
            no_gds_generation=True,no_example_target_objective=True,output=str(self.native/rid/'emx_selected'),
            request=request,gds=gds,port_manifest=ports,calibre=drc,command=command,
            original_candidate_statuses=statuses,source_pins=[request,ctx.records_pin,ctx.freeze_pin,audit,
                index_pin,self.private_config,ctx.manifest_pin,ctx.reference_pin,gds,ports,geometry_pin,drc,drc_report,self.deck,
                self.wrapper_code,*self.runtime['source_pins'],self.process_file,self.emx_wrapper]))
        native_command=put('native_command','emx_selected/solve/emx/emx_command.json',command)
        stdout=blob('stdout','emx_selected/solve/emx/emx_stdout.log',b'SYNTHETIC NO NATIVE EXECUTION\n')
        stderr=blob('stderr','emx_selected/solve/emx/emx_stderr.log',b'SYNTHETIC NATIVE STDERR\n')
        if kind != 'solver':
            s4p=blob('s4p','emx_selected/solve/emx/synthetic.s4p',('! SYNTHETIC NOT EMX '+rid+'\n').encode())
            solver=put('solver','emx_selected/SOLVER_RECEIPT.json',dict(schema='frequency_research_fresh_solver.v1',
                status='PASS',candidate_id=cid,preflight=proof,source_gds_before=gds,source_gds_after=gds,
                real_emx=True,production_modified=False,q_emx=None,touchstone=s4p,
                artifacts=[s4p,native_command,stdout,stderr]))
        if kind == 'success':
            # Reuse the old fixture's exact56 saved-label schema, not its old
            # model/target/native identity. Nothing invokes an extractor.
            template=SyntheticEvidence(self.root/('label_schema_'+str(index)),actual=[1.,1.,10.,.3],below_half_srf=strict)
            csv_pin=self.publish_rows(rid+'/emx_selected/features/features_56.csv',template.rows)
            paths['csv']=csv_pin
            feature=template.read('feature')
            feature.update(**common,**selection,q_emx=None,target=item['selected_target'],
                proxy_self=item['selected_grid_proxy'],score_scale=self.freeze['score_scale'],
                absolute_hit_tolerances=self.freeze['absolute_tolerances'],preflight=proof,solver_receipt=solver,
                emx_minus_target=[0.]*4,emx_minus_proxy=[0.]*4,normalized_response_score=0.,
                within_tolerance=[True]*4,joint_response_hit=True,strict_joint_hit=strict,
                target_relative_signed_percent=[0.]*4,target_relative_absolute_percent=[0.]*4,
                original_candidate_statuses=statuses)
            feature_pin=put('feature','emx_selected/features/FEATURE_RECEIPT.json',feature)
            put('manifest','emx_selected/features/MANIFEST.json',dict(artifacts=[feature_pin,csv_pin],inputs_unchanged=True))
            result=dict(status='FRESH_EMX_EXTRACTED',request_id=rid,candidate_id=cid,q_proxy=item['q_proxy'],
                model_id=self.manifest['model_id'],dataset_scope=self.manifest['dataset_scope'],original_request_denominator=128,
                source_manifest=ctx.original_manifest_pin,original_records=item['source_records'],
                candidate_geometry_identity_sha256=geometry,development_binding=binding,
                production_accepted=False,feature=feature_pin,valid_for_strict_comparison=strict,
                strict_joint_hit=strict,absolute_percent_error=[0.]*4)
        else:
            error=('RuntimeError: EMX failed with exit code 7. See '+stderr['path']+' for details.' if kind=='solver'
                else 'Broadband56S4pQaError: S-to-Z conversion produced incomplete or non-finite Z')
            failure_name='SOLVER_FAILURE.json' if kind=='solver' else 'FEATURE_FAILURE.json'
            put('failure','emx_selected/'+failure_name,dict(status='FAIL_NO_AUTOMATIC_RETRY',error=error,
                ended_utc='2026-01-01T00:00:00Z'))
            log=blob('log','emx_0001.log',('SYNTHETIC PIPELINE\n'+error+'\n').encode())
            intent=dict(release=self.release_pin,command=[self.config['python'],'-B','-m',
                'research.broadband56_nn.frequency_research_emx','run','--request',request['path'],
                '--output',str(self.native/rid/'emx_selected'),'--inherited-global-lease-fd','<inherited-fd>'],
                output=str(self.native/rid/'emx_selected'),completion=str(self.native/rid/'emx_selected/features/FEATURE_RECEIPT.json'))
            put('intent','emx_INTENT.json',intent)
            put('process','emx_PROCESS.json',dict(intent=intent,returncode=1,completion=None,log=log,
                utc='2026-01-01T00:00:00Z'))
            result=dict(status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',request_id=rid,candidate_id=cid,
                q_proxy=item['q_proxy'],model_id=self.manifest['model_id'],dataset_scope=self.manifest['dataset_scope'],
                original_request_denominator=128,source_manifest=ctx.original_manifest_pin,
                original_records=item['source_records'],candidate_geometry_identity_sha256=geometry,
                error='PRIOR_STAGE_FAILED: emx')
        result_pin=put('result','RESULT.json',result)
        self.snapshot['requests'][index]['result']=result_pin
        self.chains[index]=dict(paths=paths,order=order,item=item,ctx=ctx,kind=kind)
        return self.chains[index]

    def complete_prior_stages(self,index):
        """Native-shaped closed metadata for exporter, never executed stages."""
        chain=self.chains[index];item=self.items[index];rid=item['request_id'];root=self.native/rid
        self.publish_rows(rid+'/cadence_candidates.csv',[dict(candidate_id=item['candidate_id'])])
        self.publish(rid+'/CALIBRE_REQUEST.json',dict(synthetic=True,
            development_binding=chain['ctx'].development_binding))
        for stage in results.STAGES:
            if stage=='emx' and chain['kind']!='success':continue
            output,completion,_=results.STAGES[stage]
            arg=('cadence_candidates.csv' if stage=='cadence' else 'GDS_REQUEST.json' if stage=='gds_audit'
                else 'CALIBRE_REQUEST.json' if stage=='calibre' else 'EMX_REQUEST.json')
            completion_path=str(root/completion)
            if completion_path not in self.entries:
                self.publish(rid+'/'+completion,dict(synthetic=True,status='PROCESS_COMPLETE'))
            log=self.publish_raw(rid+'/'+stage+'_synthetic.log',b'SYNTHETIC ONLY\n')
            intent=dict(release=self.release_pin,output=str(root/output),completion=completion_path,
                command=['SYNTHETIC_NEVER_EXECUTE',str(root/arg)])
            self.publish(rid+'/'+stage+'_INTENT.json',intent)
            self.publish(rid+'/'+stage+'_PROCESS.json',dict(intent=intent,returncode=0,
                completion=self.entries[completion_path]['original'],log=log,utc='2026-01-01T00:00:00Z'))

    def delta_request(self,previous,indices):
        # The exporter is a pinned private owner deliverable, not code bundled
        # in a fresh public checkout. Absence is SKIP, never a fabricated PASS.
        module_path=Path(__file__).resolve().parents[4]/'reports/eucap15_native_owner_20260909T062500Z/development128_incremental_code_20260910_v1/export_incremental.py'
        if not module_path.is_file():pytest.skip('private owner exporter source required for this integration')
        spec=importlib.util.spec_from_file_location('_synthetic128_actual_exporter',module_path)
        exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
        jobs=[]
        for i in indices:
            self.complete_prior_stages(i)
            item=self.items[i];root=self.native/item['request_id'];terminal=self.chains[i]['paths']['result']
            entries=[deepcopy(e) for p,e in self.entries.items() if Path(p).is_relative_to(root)]
            jobs.append(dict(request_id=item['request_id'],result=deepcopy(self.entries[terminal['path']]),
                artifacts=[e for e in entries if e['original']['path']!=terminal['path']]))
        native_roots=[self.native/item['request_id'] for item in self.items]
        shared=[deepcopy(e) for p,e in self.entries.items() if not any(Path(p).is_relative_to(root) for root in native_roots)]
        value=dict(schema=exporter.SCHEMA,previous_export=previous,manifest=self.manifest_pin,qa=self.qa_pin,
            shared_sources=shared,whitelist=jobs)
        return exporter,_base.save(self.root/'DELTA_REQUEST.json',value)

    def edit_chain(self,index,key,change,*,close_pins=True):
        """Rehash descendant JSON after a semantic mutation, not only stale-SHA attacks."""
        chain=self.chains[index];paths=chain['paths'];current=paths[key]
        value=self.read(current);change(value)
        paths[key]=self.publish(str(Path(current['path']).relative_to(self.native)),value)
        if close_pins and key=='drc':
            index_pin=paths['calibre_index']
            data=self.entries[index_pin['path']]['resolved']['path']
            rows=list(csv.DictReader(io.StringIO(Path(data).read_text())))
            rows[0]['drc_summary_sha256']=paths['drc']['sha256']
            paths['calibre_index']=self.publish_rows(str(Path(index_pin['path']).relative_to(self.native)),rows)
        def replace(value):
            if isinstance(value,dict):
                if {'path','sha256','bytes'} <= value.keys():
                    entry=self.entries.get(value['path'])
                    return deepcopy(entry['original'] if entry is not None else value)
                return {k:replace(v) for k,v in value.items()}
            if isinstance(value,list):return [replace(v) for v in value]
            return value
        if close_pins:
            later=chain['order'][chain['order'].index(key)+1:]
            for name in later:
                old=paths[name]
                paths[name]=self.publish(str(Path(old['path']).relative_to(self.native)),replace(self.read(old)))
        self.snapshot['requests'][index]['result']=paths['result']

    def publish_raw(self,relative,raw):
        original_path=str(self.native/relative)
        target=self.mirror_dir/(str(len(self.entries))+'_synthetic_artifact')
        if original_path in self.entries:
            target=Path(self.entries[original_path]['resolved']['path'])
        target.write_bytes(raw)
        resolved=pin(target);original=dict(resolved,path=original_path)
        self.entries[original_path]=dict(original=original,resolved=resolved)
        return original

    def publish_rows(self,relative,rows):
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
        return self.publish_raw(relative,stream.getvalue().encode())

    def native_context(self,index):
        mirror=results.Mirror(list(self.entries.values()))
        ctx=results.remote_context(self.context(),self.config,self.items[index],mirror)
        for original,entry in mirror.entries.items():
            self.entries[original]=deepcopy(entry)
        return ctx

    def write_export(self):
        """Real prepare_snapshot layout, not a hand-authored research snapshot."""
        self.seal_snapshot()
        out=self.root/'owner_export';out.mkdir()
        remote=self.native/'exports'/'SYNTHETIC_FIXED'
        def metadata(name,value):
            local=_base.save(out/name,value)
            return dict(local,path=str(remote/name))
        fixed_pin=metadata('FIXED_SNAPSHOT_MANIFEST.json',self.fixed)
        terminal_paths={entry['result']['path']:entry['request_id'] for entry in self.snapshot['requests'] if entry['result'] is not None}
        excluded={str(self.native/p) for p in ('FIXED_SNAPSHOT_MANIFEST.json','OWNER_EXPORT.json')}
        included=[deepcopy(e) for p,e in self.entries.items() if p not in excluded]
        terminal_entries=[dict(e,request_id=terminal_paths[e['original']['path']])
            for e in included if e['original']['path'] in terminal_paths]
        reused=[e for e in included if e['original']['path'] not in terminal_paths]
        index_pin=metadata('RESULT_SOURCE_INDEX.json',dict(
            schema='eucap15_development128_terminal_source_index.v1',originals_mirrored_without_change=True,
            results=terminal_entries,reused_local_roots=reused))
        map_pin=metadata('SOURCE_TO_LOCAL_PATH_MAP.json',{
            e['original']['path']:e['resolved']['path'] for e in included})
        owner=dict(self.owner,local_export_dir=str(out),remote_export_dir=str(remote),
            immutable_snapshot=fixed_pin,source_index=index_pin,path_map=map_pin)
        self.export_pin=_base.save(out/'EXPORT_RECEIPT.json',owner)
        self.export_root=out
        return self.export_pin

    def prepare(self,*,allow_unclosed_delta=False):
        self.write_export()
        destination=self.root/'PREPARED_SNAPSHOT.json'
        self.prepared_pin=results.prepare_snapshot(self.export_pin,destination,allow_unclosed_delta=allow_unclosed_delta)
        self.prepared=json.loads(destination.read_text())
        return self.prepared_pin
