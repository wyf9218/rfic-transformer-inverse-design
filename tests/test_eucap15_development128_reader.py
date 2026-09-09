"""Synthetic metadata only. No original pilot/model/dataset/native access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_development128_reader as reader


def save(path,value):
    path.write_text(json.dumps(value,sort_keys=True,allow_nan=False))
    return reader.pin(path)


def inert_pin(root,name,sha='1'*64):
    return {'path':str(root/name),'sha256':sha,'bytes':1}


class Fixture:
    def __init__(self,root):
        self.root=root.resolve();self.root.mkdir()
        self.sources=[]
        norm=inert_pin(self.root,'UNREAD_NORMALIZER.json')
        geom=inert_pin(self.root,'UNREAD_CONTRACT.json')
        self.model=dict(schema='eucap15_current_development_model_identity.v1',
            experiment_class=reader.SCOPE,model_role=reader.MODEL_ROLE,model_id=reader.MODEL_ID,
            source_rows=6329,gradient_train_rows=3801,validation_rows=1269,test_rows_count_only=1259,
            FINAL=False,REAL_EMX_VALIDATION='NOT_RUN',
            selection_basis='PREDECLARED_FIRST_COMPLETED_3X256_SEED17_NOT_ABLATION_WINNER',
            best_weights={role:inert_pin(self.root,'NEVER_OPEN_'+role+'.pt',sha) for role,sha in reader.WEIGHT_SHAS.items()},
            source_dataset_inherited_pin=inert_pin(self.root,'dataset.npz',reader.DATA_SHA),
            source_split_inherited_pin=inert_pin(self.root,'splits.json',reader.SPLIT_SHA),
            normalizer=norm,geometry_contract=geom)
        self.model_pin=save(self.root/'MODEL_IDENTITY.json',self.model)
        self.freeze=dict(schema='eucap15_current_development_pilot_freeze.v1',
            status='FROZEN_BEFORE_ANY_PREDICTION_AND_NATIVE',model_id=reader.MODEL_ID,
            model_identity=self.model_pin,frequency_ghz=15,N_original_requests=128,N_proxy_slots=1408,
            config={'dataset_scope':reader.SCOPE,'allow_extrapolation':True},
            score_scale=list(reader.SCORE_SPANS),absolute_tolerances=reader.TAU,
            selection='ALL_ELEVEN_FINITE_SCORES_THEN_MINIMUM_NO_ANALYTICAL_RERANK',
            optimizer_updates=0,REAL_EMX_VALIDATION='NOT_RUN',
            artifacts={'normalizer.json':norm,'geometry_contract.json':geom})
        self.freeze_pin=save(self.root/'PILOT_FREEZE.json',self.freeze)
        self.items=[]
        for i in range(128):
            rid=f'SYNTHETIC_ONLY_{i:03d}';eligible=i>=35
            records=[]
            for q in range(10,21):
                records.append(dict(request_id=rid,model_id=reader.MODEL_ID,dataset_scope=reader.SCOPE,
                    frequency_ghz=15,target_source='DEVELOPMENT_CURRENT_SNAPSHOT_UNIFORM_TRIPLE',
                    q_proxy=10,q_target=q,proxy_preselected=q==10,candidate_id=f'{rid}-q{q:02d}',
                    candidate_geometry_identity_sha256=hashlib.sha256(str(i).encode()).hexdigest(),
                    target=[1.,1.,q,.3],grid_proxy=[1.,1.,float(q),.3],analytic_grid=eligible,
                    evidence_source='SELF_PROXY',emx_status='NOT_RUN',actual_response=None))
            record_file=self.root/f'{rid}.jsonl'
            record_file.write_text('\n'.join(json.dumps(r,sort_keys=True) for r in records)+'\n')
            record_pin=reader.pin(record_file);self.sources.append(record_pin)
            chosen=records[0]
            self.items.append(dict(request_id=rid,request_order=i,N_original_requests=128,N_original_proxy_candidates=11,
                native_started=False,no_failure_replacement=True,production_campaign_membership=False,q_emx=None,
                physical_stages={'gds':'NOT_RUN','calibre':'NOT_RUN','emx':'NOT_RUN'},q_proxy=10,record_line_number=1,
                candidate_id=chosen['candidate_id'],candidate_geometry_identity_sha256=chosen['candidate_geometry_identity_sha256'],
                selected_target=chosen['target'],selected_grid_proxy=chosen['grid_proxy'],selected_analytic_pass=eligible,
                source_records=record_pin,source_record_canonical_sha256=reader.canonical(chosen),
                selected_support_status='IN_TRAIN_MARGINAL_SUPPORT_JOINT_UNKNOWN',selected_outside_support_by_feature=[False]*4,
                state='PENDING_OWNER_ACCEPTANCE_NOT_DISPATCHED' if eligible else 'NOT_SUBMITTED_SELECTED_ANALYTIC_FAIL'))
        self.manifest=dict(schema='eucap15_current_development_selected_handoff.v1',
            status='NOT_DISPATCHED_REQUIRES_OWNER_DEVELOPMENT_SCOPE_ADAPTER',dataset_scope=reader.SCOPE,
            model_role=reader.MODEL_ROLE,model_id=reader.MODEL_ID,frequency_ghz=15,label_mode='STRICT_LUMPED',
            N_original_requests=128,N_proxy_slots=1408,N_q_proxy_selected=128,N_selected_analytic_pass=93,
            N_native_started=0,FINAL=False,REAL_EMX_VALIDATION='NOT_RUN',no_failure_replacement=True,
            production_campaign_membership=False,model_identity=self.model_pin,freeze=self.freeze_pin,requests=self.items)
        self.reseal()

    def reseal(self):
        self.manifest_pin=save(self.root/'MANIFEST.json',self.manifest)
        self.qa=dict(schema='eucap15_development128_independent_candidate_qa.v1',status='GO',
            scope='CANDIDATE_LIST_METADATA_ARITHMETIC_ONLY',FINAL=False,REAL_EMX_VALIDATION='NOT_RUN',
            native_admission='NOT_APPROVED_BY_THIS_QA',physical_correctness='NOT_VALIDATED',
            source_pins=[self.manifest_pin,self.model_pin,self.freeze_pin,*self.sources],synthetic_fixture=True)
        self.qa_pin=save(self.root/'SYNTHETIC_QA.json',self.qa)


def test_complete128_cli_initial_accounting_and_no_model_reads(tmp_path,capsys):
    f=Fixture(tmp_path/'fixture')
    before={p.name:p.read_bytes() for p in f.root.iterdir()}
    out=(tmp_path/'output').resolve()
    reader.main(['--manifest',f.manifest_pin['path'],'--manifest-sha256',f.manifest_pin['sha256'],
        '--qa-receipt',f.qa_pin['path'],'--qa-sha256',f.qa_pin['sha256'],'--out',str(out)])
    summary=json.loads((out/'SUMMARY.json').read_text())
    assert summary['N_original_requests']==128
    assert summary['N_analytic_fail']==35 and summary['N_pending_requests']==93
    assert summary['N_strict_valid']==summary['N_joint_hit']==0
    assert summary['completed_joint_hit_fraction_original'] is None
    assert summary['REAL_EMX_VALIDATION']=='NOT_RUN' and summary['FINAL'] is False
    assert summary['mode']=='FROZEN_INITIAL_LEDGER_NOT_CURRENT_LIVE_STATUS'
    assert summary['native_result_consumer']=='NOT_INSTALLED_AWAIT_OWNER_SCHEMA'
    assert summary['model_loads']==summary['native_calls']==0
    assert (out/'ERROR_ECDF.csv').read_text().count('\n')==1
    assert (out/'REQUEST_RESULTS.csv').read_text().count('\n')==129
    assert (out/'PHYSICAL_METRICS.csv').read_text().count('\n')==9
    assert before=={p.name:p.read_bytes() for p in f.root.iterdir()}
    assert 'INITIAL_LEDGER_WRITTEN' in capsys.readouterr().out


@pytest.mark.parametrize('change',['scope','model','q'])
def test_scope_model_or_q_misbinding_rejected(tmp_path,change):
    f=Fixture(tmp_path/'fixture')
    if change=='scope':f.manifest['dataset_scope']='FORMAL_10K'
    elif change=='model':f.manifest['model_id']='wrong-model'
    else:f.manifest['requests'][0]['q_proxy']=11
    f.reseal()
    with pytest.raises(ValueError):reader.load_context(f.manifest_pin,f.qa_pin)


@pytest.mark.parametrize('change',['wrong_GO','unbound_manifest'])
def test_wrong_go_or_missing_exact_qa_binding_rejected(tmp_path,change):
    f=Fixture(tmp_path/'fixture')
    if change=='wrong_GO':f.qa['status']='NO_GO'
    else:f.qa['source_pins']=f.qa['source_pins'][1:]
    f.qa_pin=save(f.root/'SYNTHETIC_QA.json',f.qa)
    with pytest.raises(ValueError):reader.load_context(f.manifest_pin,f.qa_pin)


def test_changed_pinned_bytes_preserve_failure_output(tmp_path):
    f=Fixture(tmp_path/'fixture');out=(tmp_path/'failed_output').resolve()
    record=Path(f.sources[0]['path']);record.write_text(record.read_text()+' ')
    with pytest.raises(ValueError):reader.build(f.manifest_pin,f.qa_pin,out)
    assert json.loads((out/'FAILURE_RECEIPT.json').read_text())['status']=='NO_GO_PRESERVED'
    assert not (out/'SUMMARY.json').exists()


def test_any_native_publication_input_is_rejected_without_opening(tmp_path):
    f=Fixture(tmp_path/'fixture');out=(tmp_path/'output').resolve()
    for unknown in ('NONEXISTENT_NATIVE_INDEX.json',{}, {'entries':[{'status':'PASS'}]}):
        with pytest.raises(ValueError,match='CONSUMER_NOT_INSTALLED'):
            reader.build(f.manifest_pin,f.qa_pin,out,publication_index=unknown)
        assert not out.exists()


def test_no_clobber_preserves_existing_result(tmp_path):
    f=Fixture(tmp_path/'fixture');out=(tmp_path/'output').resolve()
    reader.build(f.manifest_pin,f.qa_pin,out)
    before={p.name:p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError):reader.build(f.manifest_pin,f.qa_pin,out)
    assert before=={p.name:p.read_bytes() for p in out.iterdir()}
