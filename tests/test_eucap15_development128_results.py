"""Synthetic published metadata only; NOT a physical-chain/EMX validation.

Reuse the initial-reader fixture without running its tests. Inert weights,
dataset and split paths intentionally do not exist. No network or simulator.
"""
from collections import Counter
from copy import deepcopy
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_development128_results as results

_spec = importlib.util.spec_from_file_location(
    '_development128_synthetic_base',
    Path(__file__).with_name('test_eucap15_development128_reader.py'))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
initial = results.initial


class SnapshotFixture(_base.Fixture):
    """A complete original128 plus a captured35 synthetic failure snapshot."""
    def __init__(self, root):
        super().__init__(root)
        self.pair = dict(roles={role: {'best': item} for role, item in self.model['best_weights'].items()},
                         inverse_forward={'best': self.model['best_weights']['forward']})
        self.pair_pin = _base.save(self.root/'SYNTHETIC_PAIR.json', self.pair)
        self.specification = dict(q_values=list(range(10, 21)), q_scalar='min(Qp,Qs)',
                                  score_scale=list(initial.SCORE_SPANS), frequency_ghz=15,
                                  dataset_scope=initial.SCOPE)
        self.spec_pin = _base.save(self.root/'SYNTHETIC_SPEC.json', self.specification)
        self.model['pair'] = self.pair_pin
        self.model_pin = _base.save(self.root/'MODEL_IDENTITY.json', self.model)
        self.freeze.update(model_identity=self.model_pin, specification=self.spec_pin)
        self.freeze_pin = _base.save(self.root/'PILOT_FREEZE.json', self.freeze)
        self.manifest.update(model_identity=self.model_pin, freeze=self.freeze_pin)
        self.sources.extend([self.pair_pin, self.spec_pin])
        self.reseal()
        self.native = self.root/'UNCREATED_NATIVE_IDENTITY'
        self.mirror_dir = self.root/'mirror'; self.mirror_dir.mkdir()
        self.entries = {}
        self.config = dict(schema='eucap15_development128_successor_owner.v1',
                           model_id=initial.MODEL_ID, original_manifest=self.manifest_pin,
                           out=str(self.native), path_map={})
        for source in [*self.qa['source_pins'], self.qa_pin]:
            self.config['path_map'][source['path']] = str(self.native/'transport'/Path(source['path']).name)
        self.reseal_config()
        owner_export = self.publish('OWNER_EXPORT.json', {'scope': 'SYNTHETIC_FROZEN_EXPORT_NOT_NATIVE'})
        self.snapshot = dict(schema=results.SCHEMA, dataset_scope=initial.SCOPE, model_id=initial.MODEL_ID,
                             N_original_requests=128, mode='FROZEN_PUBLISHED_TERMINALS_NOT_LIVE_STATUS',
                             snapshot_utc='2026-01-01T00:00:00Z', release=self.release_pin,
                             owner_export=owner_export, batch=None, requests=[])
        for item in self.items:
            terminal = None
            if not item['selected_analytic_pass']:
                terminal = self.publish(item['request_id']+'/RESULT.json', self.analytic_failure(item))
            self.snapshot['requests'].append(dict(request_id=item['request_id'], result=terminal))
        self.seal_snapshot()

    def publish(self, relative, value):
        original_path = str(self.native/relative)
        target = self.mirror_dir/(str(len(self.entries))+'_metadata.json')
        if original_path in self.entries:
            target = Path(self.entries[original_path]['resolved']['path'])
        resolved = _base.save(target, value)
        original = dict(resolved, path=original_path)
        self.entries[original_path] = dict(original=original, resolved=resolved)
        return original

    def read(self, original):
        return json.loads(Path(self.entries[original['path']]['resolved']['path']).read_text())

    def reseal_config(self):
        self.config_pin = self.publish('CONFIG.json', self.config)
        self.release_pin = self.publish('RELEASE.json',
            dict(schema='eucap15_development128_delegated_prepared_release.v1', config=self.config_pin))
        if hasattr(self, 'snapshot'):
            self.snapshot['release'] = self.release_pin

    def seal_snapshot(self):
        fixed_rows=[]
        for index,(item,entry) in enumerate(zip(self.items,self.snapshot['requests'])):
            fixed_rows.append(dict(request_id=item['request_id'],request_order=index,q_proxy=item['q_proxy'],
                candidate_id=item['candidate_id'],candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
                terminal=entry['result'],observation='NO_TERMINAL_AT_CAPTURE' if entry['result'] is None else 'TERMINAL_AT_CAPTURE'))
        count=sum(row['terminal'] is not None for row in fixed_rows)
        self.fixed=dict(schema='eucap15_development128_fixed_terminal_snapshot.v1',
            original_request_denominator=128,selected_manifest_original=self.manifest_pin,
            capture_completed_utc=self.snapshot['snapshot_utc'],rows=fixed_rows,
            terminal_count_at_capture=count,no_terminal_count_at_capture=128-count)
        self.fixed_pin=self.publish('FIXED_SNAPSHOT_MANIFEST.json',self.fixed)
        self.owner=dict(schema='eucap15_development128_terminal_export.v1',original_denominator=128,
            release=deepcopy(self.entries[self.release_pin['path']]),
            config=deepcopy(self.entries[self.config_pin['path']]),immutable_snapshot=self.fixed_pin,
            results_exported=count,no_terminal_at_snapshot=128-count)
        self.snapshot['owner_export']=self.publish('OWNER_EXPORT.json',self.owner)
        self.snapshot['sources'] = deepcopy(list(self.entries.values()))
        self.snapshot_pin = _base.save(self.root/'SNAPSHOT.json', self.snapshot)
        return self.snapshot_pin

    def bind_constants(self, monkeypatch):
        monkeypatch.setattr(results, 'MANIFEST_SHA', self.manifest_pin['sha256'])
        monkeypatch.setattr(results, 'QA_SHA', self.qa_pin['sha256'])
        monkeypatch.setattr(results, 'RELEASE_SHA', self.release_pin['sha256'])

    def context(self):
        return initial.load_context(self.manifest_pin, self.qa_pin)

    def consume(self, monkeypatch):
        self.bind_constants(monkeypatch)
        self.seal_snapshot()
        return results.consume(self.context(), self.snapshot, results.Mirror(self.snapshot['sources']))

    def analytic_failure(self, item):
        return dict(status='ANALYTIC_FAIL_ORIGINAL_NO_REPLACEMENT', request_id=item['request_id'],
                    candidate_id=item['candidate_id'], q_proxy=item['q_proxy'],
                    model_id=initial.MODEL_ID, dataset_scope=initial.SCOPE,
                    original_request_denominator=128, source_manifest=self.manifest_pin,
                    original_records=item['source_records'],
                    candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
                    native_started=False, reasons=['ORIGINAL_SELECTED_ANALYTIC_FAIL'])

    def change_result(self, index, key, value):
        item = self.items[index]
        old = self.snapshot['requests'][index]['result']
        terminal = self.read(old) if old is not None else self.analytic_failure(item)
        terminal[key] = value
        new = self.publish(item['request_id']+'/RESULT.json', terminal)
        self.snapshot['requests'][index]['result'] = new
        self.seal_snapshot()

    def pipeline_failure(self, name='cadence', error=None):
        """Saved synthetic process metadata; never executes its command."""
        index=35; item=self.items[index]; root=self.native/item['request_id']
        mirror=results.Mirror(list(self.entries.values()))
        ctx=results.remote_context(self.context(), self.config, item, mirror)
        gd=dict(schema='eucap15_selected_gds_audit_request.v1', selected_manifest=ctx.original_manifest_pin,
                development_binding=ctx.development_binding, production_campaign_membership=False,
                request=dict(request_id=item['request_id'],q_proxy=item['q_proxy'],model_id=initial.MODEL_ID,
                             dataset_scope=initial.SCOPE,frequency_ghz=15,target_source=results.SOURCE),
                source_pins=dict(eleven_records=ctx.records_pin,qscan_freeze=ctx.freeze_pin,
                                 selected_manifest=ctx.manifest_pin,reference=ctx.reference_pin),
                cadence=dict(root=str(root/'cadence_only'),routes={item['candidate_id']:'parallel_shards/shard_000'}))
        self.publish(item['request_id']+'/GDS_REQUEST.json',gd)
        output, completion, _ = results.STAGES[name]
        argument = ('cadence_candidates.csv' if name=='cadence' else 'GDS_REQUEST.json' if name=='gds_audit'
                    else 'CALIBRE_REQUEST.json' if name=='calibre' else 'EMX_REQUEST.json')
        log = self.publish(item['request_id']+'/'+name+'_synthetic.log', {'synthetic_process_failed': True})
        process=dict(intent=dict(release=self.release_pin,output=str(root/output),completion=str(root/completion),
                                 command=['SYNTHETIC_DO_NOT_EXECUTE',str(root/argument)]),
                     returncode=1,log=log,completion=None)
        self.publish(item['request_id']+'/'+name+'_PROCESS.json',process)
        if name in ('calibre','emx'):
            self.publish(item['request_id']+'/'+argument,dict(development_binding=ctx.development_binding))
        terminal=self.analytic_failure(item)
        terminal.update(status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',
                        error=error or 'STAGE_EXECUTION_FAILED: '+name+'; SYNTHETIC_FAILURE')
        self.snapshot['requests'][index]['result']=self.publish(item['request_id']+'/RESULT.json',terminal)
        self.seal_snapshot()
        return process


@pytest.fixture(autouse=True)
def no_feature_inspection(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Physical feature inspection is outside these synthetic consumer tests')
    monkeypatch.setattr(results, 'inspect_features', forbidden)


def test_synthetic_cli_all128_35_original_fail_93_pending_and_zero_valid_nulls(tmp_path, monkeypatch, capsys):
    f=SnapshotFixture(tmp_path/'fixture');f.bind_constants(monkeypatch)
    before={str(p):p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
    out=(tmp_path/'output').resolve()
    results.main(['--manifest',f.manifest_pin['path'],'--qa-receipt',f.qa_pin['path'],
                  '--snapshot',f.snapshot_pin['path'],'--snapshot-sha256',f.snapshot_pin['sha256'],'--out',str(out)])
    summary=json.loads((out/'SUMMARY.json').read_text())
    assert summary['N_original_requests']==128 and summary['N_owner_terminal_receipts_consumed']==35
    assert summary['N_analytic_fail']==35 and summary['N_pending_requests']==93
    assert summary['N_strict_valid']==summary['N_joint_hit']==0
    assert summary['completed_joint_hit_fraction_original'] is None
    assert summary['q_emx'] is None and summary['complete11_status']=='NOT_EVALUATED'
    assert summary['REAL_EMX_VALIDATION']=='NO_COMPLETED_PHYSICAL_EVIDENCE_CONSUMED'
    assert summary['mode']=='FROZEN_PUBLISHED_TERMINALS_NOT_LIVE_STATUS'
    assert summary['model_loads']==summary['model_inferences']==summary['native_calls']==0
    assert summary['FINAL'] is False and not summary['source_data_arrays_read']
    with (out/'REQUEST_RESULTS.csv').open(newline='') as stream: rows=list(csv.DictReader(stream))
    assert len(rows)==128 and Counter(r['state'] for r in rows)=={'ANALYTIC_FAIL':35,'PENDING':93}
    assert all(r['actual']=='' and r['q_emx']=='' for r in rows)
    with (out/'PHYSICAL_METRICS.csv').open(newline='') as stream: metrics=list(csv.DictReader(stream))
    assert len(metrics)==8 and all(r['mae']=='' and r['n']=='0' for r in metrics)
    assert (out/'ERROR_ECDF.csv').read_text().splitlines()==[','.join(initial.ECDF_FIELDS)]
    assert before=={str(p):p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
    assert not f.native.exists()
    assert all(not Path(p['path']).exists() for p in f.model['best_weights'].values())
    assert json.loads(capsys.readouterr().out)['N_owner_terminals']==35


@pytest.mark.parametrize('key,value',[
    ('request_id','OTHER'),('candidate_id','OTHER-q10'),('q_proxy',11),('q_proxy',True),
    ('model_id','OLD_FORMAL_REFERENCE'),('dataset_scope','FINAL'),('original_request_denominator',64),
    ('candidate_geometry_identity_sha256','f'*64),('source_manifest',{'path':'/wrong','sha256':'0'*64,'bytes':0}),
    ('original_records',{'path':'/wrong','sha256':'0'*64,'bytes':0}),('native_started',True),('reasons',['OTHER'])])
def test_published_analytic_failure_exact_identity_rejected(tmp_path,monkeypatch,key,value):
    f=SnapshotFixture(tmp_path/'fixture');f.change_result(0,key,value)
    with pytest.raises(ValueError):f.consume(monkeypatch)


def test_original_analytic_pass_cannot_be_relabelled_original_failure(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture');f.change_result(35,'status','ANALYTIC_FAIL_ORIGINAL_NO_REPLACEMENT')
    with pytest.raises(ValueError,match='analytic-pass relabeled'):f.consume(monkeypatch)


@pytest.mark.parametrize('change',['missing','duplicate','reorder','extra_field','wrong_path'])
def test_snapshot_fixed_denominator_order_and_result_path(tmp_path,monkeypatch,change):
    f=SnapshotFixture(tmp_path/'fixture')
    if change=='missing':f.snapshot['requests'].pop()
    elif change=='duplicate':f.snapshot['requests'][1]=deepcopy(f.snapshot['requests'][0])
    elif change=='reorder':f.snapshot['requests'][0],f.snapshot['requests'][1]=f.snapshot['requests'][1],f.snapshot['requests'][0]
    elif change=='extra_field':f.snapshot['requests'][0]['state']='PASS'
    else:f.snapshot['requests'][0]['result']=f.snapshot['requests'][1]['result']
    with pytest.raises(ValueError):f.consume(monkeypatch)


@pytest.mark.parametrize('status',['UNRECOGNIZED_DONE','RESOURCE_WAIT',None])
def test_unknown_claimed_terminal_preserved_not_silently_pending(tmp_path,monkeypatch,status):
    f=SnapshotFixture(tmp_path/'fixture');f.change_result(35,'status',status)
    f.bind_constants(monkeypatch);out=(tmp_path/'failed').resolve()
    with pytest.raises(ValueError,match='unknown terminal'):
        results.build(f.manifest_pin,f.qa_pin,f.snapshot_pin,out)
    assert json.loads((out/'FAILURE_RECEIPT.json').read_text())['status']=='NO_GO_PRESERVED'
    assert (out/'INTENT.json').exists() and not (out/'SUMMARY.json').exists()
    assert f.read(f.snapshot['requests'][35]['result'])['status']==status


@pytest.mark.parametrize('key,value',[('model_id','WRONG'),('original_manifest',{'path':'/wrong','sha256':'0'*64,'bytes':0}),('schema','wrong')])
def test_native_config_identity_and_schema_binding(tmp_path,monkeypatch,key,value):
    f=SnapshotFixture(tmp_path/'fixture');f.config[key]=value;f.reseal_config()
    with pytest.raises(ValueError,match='owner config'):f.consume(monkeypatch)


def test_exact_release_sha_is_not_optional(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture');monkeypatch.setattr(results,'RELEASE_SHA','0'*64)
    with pytest.raises(ValueError,match='release differs'):
        results.consume(f.context(),f.snapshot,results.Mirror(f.snapshot['sources']))


@pytest.mark.parametrize('which',['manifest','qa'])
def test_build_exact_source_roots_checked_before_output(tmp_path,monkeypatch,which):
    f=SnapshotFixture(tmp_path/'fixture');f.bind_constants(monkeypatch)
    monkeypatch.setattr(results,'MANIFEST_SHA' if which=='manifest' else 'QA_SHA','0'*64)
    out=(tmp_path/'not_created').resolve()
    with pytest.raises(ValueError,match='exact development128 identity'):
        results.build(f.manifest_pin,f.qa_pin,f.snapshot_pin,out)
    assert not out.exists()


def test_snapshot_sha_and_no_clobber_failure_evidence(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture');f.bind_constants(monkeypatch)
    source=Path(f.snapshot_pin['path']);source.write_text(source.read_text()+' ')
    out=(tmp_path/'failed').resolve()
    with pytest.raises(ValueError,match='snapshot changed'):
        results.build(f.manifest_pin,f.qa_pin,f.snapshot_pin,out)
    before={p.name:p.read_bytes() for p in out.iterdir()}
    assert 'FAILURE_RECEIPT.json' in before and 'SUMMARY.json' not in before
    with pytest.raises(FileExistsError):results.build(f.manifest_pin,f.qa_pin,initial.pin(source),out)
    assert before=={p.name:p.read_bytes() for p in out.iterdir()}


def test_completed_output_cannot_be_overwritten(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture');f.bind_constants(monkeypatch);out=(tmp_path/'done').resolve()
    results.build(f.manifest_pin,f.qa_pin,f.snapshot_pin,out)
    before={p.name:p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError):results.build(f.manifest_pin,f.qa_pin,f.snapshot_pin,out)
    assert before=={p.name:p.read_bytes() for p in out.iterdir()}


@pytest.mark.parametrize('change',['declared_sha','declared_size','bytes_changed','missing','conflict','symlink'])
def test_mirror_exact_sha_size_mapping_and_symlink_fail_closed(tmp_path,change):
    root=tmp_path.resolve();source=root/'source.json';_base.save(source,{'synthetic':True})
    resolved=initial.pin(source);original=dict(resolved,path=str(root/'not_existing_original.json'))
    entry=dict(original=original,resolved=resolved)
    if change=='declared_sha':entry['resolved']=dict(resolved,sha256='0'*64)
    elif change=='declared_size':entry['resolved']=dict(resolved,bytes=resolved['bytes']+1)
    elif change=='symlink':
        link=root/'link.json';link.symlink_to(source);entry['resolved']=dict(resolved,path=str(link))
    if change in ('declared_sha','declared_size','symlink'):
        with pytest.raises(ValueError):results.Mirror([entry])
        return
    mirror=results.Mirror([entry])
    if change=='bytes_changed':
        source.write_text('{}')
        with pytest.raises(ValueError,match='changed'):mirror.document(original)
    elif change=='missing':
        with pytest.raises(ValueError,match='unpublished'):mirror.document(dict(original,path=str(root/'unknown.json')))
    else:
        other=root/'same_bytes.json';other.write_bytes(source.read_bytes())
        with pytest.raises(ValueError,match='conflicting'):mirror.add(original,initial.pin(other))


def test_used_mirror_mutation_detected_on_final_recheck(tmp_path):
    p=tmp_path.resolve()/'receipt.json';_base.save(p,{'synthetic':1});resolved=initial.pin(p)
    original=dict(resolved,path=str(p.parent/'remote.json'))
    mirror=results.Mirror([dict(original=original,resolved=resolved)])
    assert mirror.document(original)=={'synthetic':1}
    p.write_text('{"synthetic":2}')
    with pytest.raises(ValueError,match='changed'):mirror.recheck()


@pytest.mark.parametrize('change',['missing_map','pair_forward','spec_q','unbound_pair'])
def test_remote_view_model_pair_spec_and_exact_qa_binding(tmp_path,monkeypatch,change):
    f=SnapshotFixture(tmp_path/'fixture');ctx=f.context();config=deepcopy(f.config)
    mirror=results.Mirror(f.snapshot['sources'])
    if change=='missing_map':del config['path_map'][f.pair_pin['path']]
    elif change=='pair_forward':
        changed=deepcopy(f.pair);changed['inverse_forward']['best']=f.model['best_weights']['inverse']
        changed_pin=_base.save(f.root/'OTHER_PAIR.json',changed)
        ctx.identity['pair']=changed_pin;ctx.qa['source_pins'].append(changed_pin)
        config['path_map'][changed_pin['path']]=str(f.native/'transport/OTHER_PAIR.json')
    elif change=='spec_q':
        changed=dict(f.specification,q_scalar='Q_LOWER_BOUND')
        changed_pin=_base.save(f.root/'OTHER_SPEC.json',changed)
        ctx.freeze['specification']=changed_pin;ctx.qa['source_pins'].append(changed_pin)
        config['path_map'][changed_pin['path']]=str(f.native/'transport/OTHER_SPEC.json')
    else:ctx.qa['source_pins']=[p for p in ctx.qa['source_pins'] if p!=f.pair_pin]
    with pytest.raises(ValueError):results.remote_context(ctx,config,f.items[35],mirror)


@pytest.mark.parametrize('stage,expected',[('cadence','GDS_FAIL'),('gds_audit','GDS_FAIL'),('calibre','DRC_FAIL')])
def test_closed_synthetic_pipeline_failure_kept_without_claiming_solver_start(tmp_path,monkeypatch,stage,expected):
    f=SnapshotFixture(tmp_path/'fixture');f.pipeline_failure(stage)
    rows,closures=f.consume(monkeypatch)
    assert len(rows)==128 and rows[35]['state']==expected and rows[35]['actual'] is None
    assert rows[35]['q_proxy']==10 and rows[35]['q_emx'] is None
    assert len(closures)==1 and closures[0]['failure']['interpretation']=='PIPELINE_STAGE_FAILURE_NOT_PROOF_OF_NATIVE_SOLVER_START'


def test_ambiguous_emx_pipeline_failure_never_claimed_as_solver_failure(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture');f.pipeline_failure('emx')
    with pytest.raises(ValueError,match='REQUIRES_SPECIFIC_SOLVER_OR_EXTRACTION'):
        f.consume(monkeypatch)


def test_false_batch_complete_with_pending_is_rejected(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture')
    f.snapshot['batch']=f.publish('BATCH_RECEIPT.json',dict(status='ALL_ORIGINAL_128_ACCOUNTED',
        release=f.release_pin,N_original_requests=128,N_proxy_records=1408,results=[]))
    with pytest.raises(ValueError,match='batch differs'):f.consume(monkeypatch)


def test_duplicate_json_key_cannot_redefine_terminal(tmp_path,monkeypatch):
    f=SnapshotFixture(tmp_path/'fixture');target=f.snapshot['requests'][0]['result']
    resolved=Path(f.entries[target['path']]['resolved']['path'])
    resolved.write_text('{"status":"ANALYTIC_FAIL_ORIGINAL_NO_REPLACEMENT","status":"OTHER"}')
    rp=initial.pin(resolved);op=dict(rp,path=target['path'])
    f.entries[target['path']]=dict(original=op,resolved=rp)
    f.snapshot['requests'][0]['result']=op
    with pytest.raises(ValueError,match='duplicate JSON'):f.consume(monkeypatch)


@pytest.mark.parametrize('change',['hidden_terminal','capture_time','geometry','order','q','observation','count','release','config','manifest'])
def test_owner_fixed_publication_cannot_be_omitted_or_relabelled(tmp_path,change):
    f=SnapshotFixture(tmp_path/'fixture')
    if change=='hidden_terminal':f.snapshot['requests'][0]['result']=None
    elif change=='capture_time':f.snapshot['snapshot_utc']='2026-01-02T00:00:00Z'
    elif change=='geometry':f.fixed['rows'][0]['candidate_geometry_identity_sha256']='f'*64
    elif change=='order':f.fixed['rows'][0]['request_order']=2
    elif change=='q':f.fixed['rows'][0]['q_proxy']=11
    elif change=='observation':f.fixed['rows'][0]['observation']='NO_TERMINAL_AT_CAPTURE'
    elif change=='count':f.fixed['terminal_count_at_capture']=34
    elif change=='release':f.owner['release']['original']['sha256']='0'*64
    elif change=='config':f.owner['config']['original']['sha256']='0'*64
    else:f.fixed['selected_manifest_original']=dict(f.manifest_pin,sha256='0'*64)
    f.owner['immutable_snapshot']=f.publish('FIXED_SNAPSHOT_MANIFEST.json',f.fixed)
    f.snapshot['owner_export']=f.publish('OWNER_EXPORT.json',f.owner)
    f.snapshot['sources']=deepcopy(list(f.entries.values()))
    with pytest.raises(ValueError):
        results.validate_export(f.context(),f.snapshot,results.Mirror(f.snapshot['sources']))


@pytest.mark.parametrize('change',['none','candidate','index','input_request'])
def test_drc_rejection_wrapper_candidate_binding(tmp_path,monkeypatch,change):
    """Synthetic completed DRC rejection; no native process or feature inspection."""
    import hashlib
    f=SnapshotFixture(tmp_path/'fixture')
    process=f.pipeline_failure('calibre',error='CALIBRE_ZERO_BLOCKING_NOT_PASS')
    item=f.items[35];relative=item['request_id']
    input_pin=f.entries[str(f.native/relative/'CALIBRE_REQUEST.json')]['original']
    index_pin=f.publish(relative+'/calibre/drc_index.csv',{})
    index_path=Path(f.entries[index_pin['path']]['resolved']['path'])
    candidate_sha=hashlib.sha256(item['candidate_id'].encode()).hexdigest()
    if change=='candidate':candidate_sha='f'*64
    with index_path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['candidate_id_sha256','overall_status'])
        writer.writeheader();writer.writerow(dict(candidate_id_sha256=candidate_sha,overall_status='FAIL'))
    resolved=initial.pin(index_path);index_pin=dict(resolved,path=index_pin['path'])
    f.entries[index_pin['path']]=dict(original=index_pin,resolved=resolved)
    summary=f.publish(relative+'/calibre/SUMMARY.json',dict(scope='SYNTHETIC_REJECTION_ONLY'))
    wrapper=dict(status='PROCESS_COMPLETE',input_request=input_pin,index=index_pin,summary=summary,
                 N_candidates=1,N_pass=0,production_modified=False,solver_started=False)
    if change=='index':wrapper['index']=summary
    if change=='input_request':wrapper['input_request']=summary
    completion_name=results.STAGES['calibre'][1]
    process.update(returncode=0,completion=f.publish(relative+'/'+completion_name,wrapper))
    f.publish(relative+'/calibre_PROCESS.json',process)
    if change=='none':
        rows,closures=f.consume(monkeypatch)
        assert len(rows)==128 and Counter(r['state'] for r in rows)=={'ANALYTIC_FAIL':35,'PENDING':92,'DRC_FAIL':1}
        assert rows[35]['actual'] is None and rows[35]['q_proxy']==10 and rows[35]['q_emx'] is None
        assert len(closures)==1 and closures[0]['failure']['stage']=='calibre'
        assert not f.native.exists()
    else:
        message='another candidate' if change=='candidate' else 'DRC wrapper rejection'
        with pytest.raises(ValueError,match=message):f.consume(monkeypatch)
