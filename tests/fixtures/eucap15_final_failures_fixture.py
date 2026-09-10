"""Private synthetic failure artifacts; no models, GDS or solvers are used."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path

from research.broadband56_nn import eucap15_final_context as context_reader
from research.broadband56_nn import eucap15_final_failures as failures
from research.broadband56_nn.eucap15_final_binding import candidate_binding
from research.broadband56_nn.eucap15_development128_results import Mirror
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import GEOMETRY_FIELDS
from tests import test_eucap15_final_context as context_fixture
from tests import test_eucap15_final_routing as routing_fixture
from tests.fixtures.eucap15_selected_evidence_fixture import SyntheticEvidence, pin, sha
from tests.fixtures.eucap15_final_evidence_fixture import FinalEvidence


class FailureEvidence(SyntheticEvidence):
    """A test-only tiny context plus explicit published failure proof."""
    def __init__(self,tmp_path,monkeypatch,*,order=0,q=14,stage='cadence',analytic=True):
        base=FinalEvidence(tmp_path,monkeypatch,order=order,q=q,analytic=analytic)
        self.__dict__.update(base.__dict__)
        self.hidden={k:self.paths.pop(k) for k in ('feature','manifest','csv')}
        self.json_order=[k for k in self.json_order if k not in self.hidden]
        self.order=[k for k in self.order if k not in self.hidden]
        fields=list(GEOMETRY_FIELDS)
        b=self.binding
        monkeypatch.setattr(failures,'FOUNDRY_DECK_SHA256',self.p('deck')['sha256'])
        self.runtime=tmp_path/'SYNTHETIC_RUNTIME_NEVER_EXECUTED'
        for name in ('simulation','extractor'):
            self.blob(name,self.runtime/failures.FAILURE_SOURCE_PATH[name],f'# SYNTHETIC {name}; NEVER EXECUTED\n'.encode())
        self.blob('calibre_wrapper',self.runtime/'synthetic_calibre_wrapper.py',b'# SYNTHETIC WRAPPER NEVER EXECUTED\n')
        monkeypatch.setattr(failures,'FAILURE_SOURCE_SHA',
            {name:self.p(name)['sha256'] for name in ('simulation','extractor')})
        self.put('release',self.native.parent.parent/'RELEASE.json',dict(
            schema='eucap15_final_native_release.v1',private_config=self.p('config'),
            calibre_wrapper=self.p('calibre_wrapper'),
            source_pins={name:b['source_pins'][name] for name in ('frame','model_freeze','inference_complete')},
            failure_sources={name:self.p(name) for name in ('simulation','extractor')}))
        self.expected_release=self.p('release')
        self.common=dict(final_binding=self.p('binding'),request_id=self.request_id,candidate_id=self.candidate_id,
            model_id=b['model_id'],dataset_scope='FINAL_FROZEN_DATASET',frequency_ghz=15,
            q_requested=q,q_proxy=b['q_proxy'],q_emx=None,memberships=b['memberships'],
            physical_selection='FROZEN_MAIN_AUDIT_UNION',
            original_request_denominator=b['original_request_denominator'])
        self.put('gds_request',self.native/'GDS_REQUEST.json',dict(
            **{k:v for k,v in self.common.items() if k!='q_emx'},
            schema='eucap15_final_gds_audit_request.v1',
            production_campaign_membership=False,
            source_pins={**b['source_pins'],'final_binding':self.p('binding'),'private_config':self.p('config')},
            cadence=dict(root=str(self.native/'cadence_only'),routes={self.candidate_id:'parallel_shards/shard_000'})))
        row=dict(candidate_id=self.candidate_id,candidate_id_sha256=self.candidate_sha,
                 **dict(zip(fields,b['grid_geometry'])))
        stream=io.StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=list(row),lineterminator='\n')
        writer.writeheader(); writer.writerow(row)
        self.blob('candidates',self.native/'cadence_candidates.csv',stream.getvalue().encode())
        self.paths['calibre_input']=self.native/'gds_audit'/'CALIBRE_INPUT.csv'
        self.calibre_rows=[dict(candidate_id_sha256=self.candidate_sha,
            candidate_geometry_identity_sha256=self.geometry_sha,
            gds_path=self.p('gds')['path'],gds_sha256=self.p('gds')['sha256'],
            geometry_audit_path=self.p('geometry')['path'],
            gds_timestamp_normalized_sha256=self.read('geometry')['gds_timestamp_normalized_sha256'],
            top_cell='TRANSFORMER')]
        self.write_rows('calibre_input',self.calibre_rows)
        audit=self.read('audit'); audit['calibre_input']=self.p('calibre_input')
        self.put('audit',self.paths['audit'],audit)
        self.put('calibre_request',self.native/'CALIBRE_REQUEST.json',dict(
            schema='frequency_research_calibre_request.v1',final_binding=self.p('binding'),
            input_index=self.p('calibre_input'),out=str(self.native/'calibre'),
            script=self.p('simulation'),gds_hash_source=self.p('extractor'),
            runtime_sources=[self.p('simulation'),self.p('extractor')]))
        self.index_rows[0]['overall_status']='PASS'
        self.write_index()
        # This order re-closes pins for semantic attacks, without repairing the
        # deliberately changed claim. Flat hash fields are kept unless an
        # earlier artifact legitimately changed in this synthetic mutation.
        request=self.read('request'); request['runtime']=dict(repo=str(self.runtime),source_pins=[self.p('simulation'),self.p('extractor')])
        self.put('request',self.paths['request'],request)
        proof=self.read('proof'); proof['source_pins'].extend([self.p('simulation'),self.p('extractor')])
        self.put('proof',self.paths['proof'],proof)
        self.order=['binding','config','simulation','extractor','calibre_wrapper','release','gds','ports','geometry',
            'deck','report','drc','index','calibre_input','audit','calibre_request',
            'gds_request','candidates','request','s4p','log','command','proof','solver']
        self.repin_after('index')
        self.stage={'gds_reject':'gds_audit','drc_reject':'calibre','solver':'emx','feature':'emx'}.get(stage,stage)
        self.mode=stage
        self.excluded={'audit'} if stage in ('cadence','gds_audit') else set()
        if stage not in ('solver','feature'):
            self.excluded.update(('proof','solver'))
        if stage=='gds_reject':
            def reject_geometry(x):
                x['overall_status']='FAIL'; x['checks'][failures.GEOMETRY_CHECKS[0]]=False
            self.edit('geometry',reject_geometry)
            def reject_audit(x):
                x['N_audit_pass']=0
                x['records'][0].update(status='FAIL',failed_checks=[failures.GEOMETRY_CHECKS[0]])
            self.edit('audit',reject_audit)
        elif stage=='drc_reject':
            def reject_drc(x):
                x['overall_status']='FAIL'; x['blocking_drc_violation_count']=1
                x['checks']['foundry_drc_pass']=False
                x['checks']['no_blocking_drc_violations']=False
            self.edit('drc',reject_drc)
            self.index_rows[0]['overall_status']='FAIL'; self.write_index(); self.repin_after('index')
            self.put('wrapper',self.native/'calibre'/'RESEARCH_WRAPPER_RECEIPT.json',dict(
                status='PROCESS_COMPLETE',input_request=self.p('calibre_request'),index=self.p('index'),
                summary=self.p('drc'),N_candidates=1,N_pass=0,production_modified=False,solver_started=False))
            self.order.append('wrapper')
        if stage=='solver':
            self.hidden['solver']=self.paths.pop('solver')
            self.json_order.remove('solver'); self.order.remove('solver')
            for name in ('stdout','stderr'):
                self.blob('native_'+name,self.native/'emx_selected'/'solve'/'emx'/f'emx_{name}.log',
                          f'SYNTHETIC {name}: NO SOLVER EXECUTED\n'.encode())
            self.put('native_command',self.native/'emx_selected'/'solve'/'emx'/'emx_command.json',self.command)
            tail='RuntimeError: EMX failed with exit code 7. See '+self.p('native_stderr')['path']+' for details.'
        elif stage=='feature':
            tail='Broadband56S4pQaError: S-to-Z conversion produced incomplete or non-finite Z'
        else:
            tail='RuntimeError: SYNTHETIC_'+self.stage.upper()+'_FAILURE'
        if stage in ('solver','feature'):
            filename='SOLVER_FAILURE.json' if stage=='solver' else 'FEATURE_FAILURE.json'
            self.put('subfailure',self.native/'emx_selected'/filename,dict(
                status='FAIL_NO_AUTOMATIC_RETRY',error=tail,ended_utc='2000-01-01T00:00:02Z'))
            self.order.extend(k for k in ('native_stdout','native_stderr','native_command','subfailure') if k in self.paths)
        output,completion,_=failures.STAGES[self.stage]
        argument=self.native/('cadence_candidates.csv' if self.stage=='cadence' else
            'GDS_REQUEST.json' if self.stage=='gds_audit' else 'CALIBRE_REQUEST.json' if self.stage=='calibre' else 'EMX_REQUEST.json')
        if self.stage=='cadence':
            command=['SYNTHETIC_PYTHON_NEVER_EXECUTED','synthetic_cadence.py','--candidate-csv',str(argument),'--out-dir',str(self.native/output)]
        elif self.stage=='calibre':
            command=['SYNTHETIC_PYTHON_NEVER_EXECUTED','-B','-c',failures.CALIBRE_LOADER,
                     self.p('calibre_wrapper')['path'],str(argument),'<inherited-fd>']
        else:
            module='research.broadband56_nn.frequency_research_'+('gds_audit' if self.stage=='gds_audit' else self.stage)
            command=['SYNTHETIC_PYTHON_NEVER_EXECUTED','-m',module]
            if self.stage=='emx': command.append('run')
            command.extend(['--request',str(argument)])
            if self.stage!='calibre': command.extend(['--out' if self.stage=='gds_audit' else '--output',str(self.native/output)])
        self.put('intent',self.native/(self.stage+'_INTENT.json'),dict(
            release=self.expected_release,output=str(self.native/output),completion=str(self.native/completion),command=command))
        self.blob('process_log',self.native/(self.stage+'_synthetic.log'),('SYNTHETIC CLOSED WRAPPER\n'+tail+'\n').encode())
        terminal=self.p('audit') if stage=='gds_reject' else self.p('wrapper') if stage=='drc_reject' else None
        self.put('process',self.native/(self.stage+'_PROCESS.json'),dict(
            intent=self.p_document('intent'),returncode=0 if terminal else 1,
            log=self.p('process_log'),completion=terminal))
        error=('ACTUAL_GDS_AUDIT_REJECTED' if stage=='gds_reject' else
            'CALIBRE_ZERO_BLOCKING_NOT_PASS' if stage=='drc_reject' else
            'STAGE_EXECUTION_FAILED: '+self.stage+'; '+self.p('process_log')['path'])
        self.put('result',self.native/'RESULT.json',dict(**self.common,
            schema='eucap15_final_candidate_failure.v1',status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',
            candidate_geometry_identity_sha256=self.geometry_sha,error=error))
        self.order.extend(['intent','process_log','process','result'])

    def p_document(self,key):
        return self.read(key)

    def write_rows(self,key,rows):
        stream=io.StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)
        self.blob(key,self.paths[key],stream.getvalue().encode())

    def write_index(self):
        self.write_rows('index',self.index_rows)

    def repin_after(self,key):
        def replace(value):
            if isinstance(value,dict):
                if {'path','sha256','bytes'}<=value.keys(): return pin(value['path'])
                return {k:replace(v) for k,v in value.items()}
            if isinstance(value,list): return [replace(v) for v in value]
            return value
        for name in self.order[self.order.index(key)+1:]:
            if name=='index':
                for row in self.index_rows:
                    row['drc_summary_sha256']=pin(row['drc_summary_path'])['sha256']
                self.write_index()
            elif name in self.json_order:
                value=replace(self.read(name))
                if name=='drc' and key in ('geometry','gds'):
                    value['geometry_audit_sha256']=self.p('geometry')['sha256']
                if name=='process' and key in self.order[:self.order.index('intent')+1]:
                    value['intent']=self.read('intent')
                self.put(name,self.paths[name],value)

    def mirror(self):
        sources={p['path']:p for p in self.binding['source_pins'].values()}
        sources.update({str(p):pin(p) for k,p in self.paths.items() if k not in self.excluded})
        entries=[]
        for i,original in enumerate(sources.values()):
            dest=self.root/'MIRROR_ONLY'/f'{i:03d}'/Path(original['path']).name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(Path(original['path']).read_bytes())
            entries.append(dict(original=original,resolved=pin(dest)))
        return Mirror(entries)

    def inspect(self,mirror=None):
        return failures.inspect_failure(self.ctx,self.candidate_id,self.p('result'),
            mirror or self.mirror(),expected_release=self.expected_release,
            expected_private_config=self.expected_config)
