"""Exactly six newly failed cases: in-memory construction and old matcher trace.

No GDS write, Cadence, Calibre, EMX, physical QA, train or old-case replay.
"""
import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

CASES_SHA='dcf9f8ff184a939d7b052d6f3f6f627f8ce74e22462c4b2ca0d2a0014781df60'
EVIDENCE_SHA='020db65bb9cb82dd6511a8d08fe6710c4c596782020f3cbc121e5cc8e6f14b1c'
EXPORT_SHA='055467d5ff21bae8823551891c2f8f6df8c0e621c8e831a8c054f06b14aead23'
ENDPOINT_SHA='df712c44abb2113d6cced55653d8da169ecae74a7061b8dec756a4f73f09c151'
LINEAGE_SHA='01c0ae7c2eac2fca55baf0de58824c034625d84dcaceb9a8bb8772c6d49aa2b1'


def pin(p):
    p=Path(p).absolute(); raw=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def require(ok,text):
    if not ok:raise ValueError(text)


def save(path,obj):
    with Path(path).open('x') as f:json.dump(obj,f,indent=2,allow_nan=False)


class Captured(Exception):pass


def main():
    p=argparse.ArgumentParser()
    for name in ('cases','evidence','repo','aliases','lineage','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--source-config',type=Path,required=True)
    p.add_argument('--source-config-sha256',required=True)
    a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=False)
    subprocess.Popen=lambda *x,**kw: (_ for _ in ()).throw(RuntimeError('NATIVE_FORBIDDEN'))
    require(pin(a.cases)['sha256']==CASES_SHA and pin(a.evidence)['sha256']==EVIDENCE_SHA,'new six input drift')
    cases=json.loads(a.cases.read_bytes())['rows'];meta=json.loads(a.evidence.read_bytes())
    require(len(cases)==6 and {r['request_id'] for r in cases}=={r['request_id'] for r in meta['results']},'six exact identities required')
    files={f['pin']['path']:f for f in meta['files']};checked=[]
    def read(p):
        f=files[p['path']];actual=pin(f['local_path'])
        require(actual['sha256']==p['sha256'] and actual['bytes']==p['bytes'],'failure evidence changed')
        checked.append(dict(original=p,resolved=actual));return Path(f['local_path']).read_bytes()
    aliases=json.loads(a.aliases.read_bytes())
    sys.path.insert(0,str(a.repo.absolute()))
    import gdstk
    from rfic_transformer_inverse_design.api import load_run_config,TransformerOptimizationAdapter
    from rfic_transformer_inverse_design.layout import export,port_endpoint_construction as endpoint
    from scripts.run_candidate_queue_dataset import _apply_overrides
    require(pin(export.__file__)['sha256']==EXPORT_SHA and pin(endpoint.__file__)['sha256']==ENDPOINT_SHA,'reconstruction source mismatch')
    require(pin(a.lineage)['sha256']==LINEAGE_SHA,'existing lineage helper drift')
    spec=importlib.util.spec_from_file_location('existing_lineage',a.lineage)
    lineage=importlib.util.module_from_spec(spec);spec.loader.exec_module(lineage)
    cfg_source=None
    if a.source_config:
        require(a.source_config_sha256 and pin(a.source_config)['sha256']==a.source_config_sha256,'current native config pin differs')
        cfg_source=json.loads(a.source_config.read_bytes())
    expected_inputs={}
    def collect_pins(v):
        if isinstance(v,dict):
            if all(k in v for k in ('path','sha256','bytes')):
                expected_inputs.setdefault(v['path'],[]).append(v)
            for value in v.values():collect_pins(value)
        elif isinstance(v,list):
            for value in v:collect_pins(value)
    collect_pins(cfg_source)
    physical_inputs=[]
    results=[];loaded=[]
    original_canonicalizer=export._canonicalize_cell_to_foundry_grid
    def polygons(cell):return [dict(layer=p.layer,datatype=p.datatype,points=p.points.tolist()) for p in cell.polygons]
    def clone(capture):
        c=gdstk.Cell('REPLAY_ONLY')
        for p in capture['after']:c.add(gdstk.Polygon(p['points'],layer=p['layer'],datatype=p['datatype']))
        for p in capture['labels']:c.add(gdstk.Label(p['text'],p['origin'],layer=p['layer'],texttype=p['texttype']))
        return c
    for case in cases:
        rid=case['request_id'];record=next(r for r in meta['results'] if r['request_id']==rid)
        require(record['source_result']==case['result_pin'],'original RESULT binding mismatch')
        process=json.loads(read(record['process']));require(process['returncode']==2,'not the recorded failed export')
        entry=next(p for p in record['directly_referenced_files'] if p['path'].endswith('/candidate_queue_dataset_summary.json'))
        summary=json.loads(read(entry));args=summary['arguments']
        errors=[]
        def visit(v):
            if isinstance(v,dict):
                if isinstance(v.get('error'),str) and 'full-width terminal face' in v['error']:errors.append(v['error'])
                for x in v.values():visit(x)
            elif isinstance(v,list):
                for x in v:visit(x)
        visit(summary);require(len(set(errors))==1,'exact original endpoint failure unavailable')
        require(args['port_endpoint_policy']==endpoint.POLICY,'actual policy differs')
        config_path=args['config'];cfg=load_run_config(Path(aliases[config_path]))
        overrides=SimpleNamespace(**{k:args[k] for k in ('force_wideband_5_60_1p0','force_wideband_5_60_0p5','force_wideband_5_50_0p1','force_port_mode','force_cadence_pin_purpose')})
        cfg=_apply_overrides(cfg,overrides)
        original_process=str(cfg.emx.emx_process_file)
        for native_path in (config_path,original_process):
            actual=pin(aliases[native_path])
            matching=[p for p in expected_inputs.get(native_path,[]) if p['sha256']==actual['sha256'] and p['bytes']==actual['bytes']]
            require(bool(matching),'actual configuration/process not current-release pinned: '+native_path)
            entry=dict(original=matching[0],local=actual)
            if entry not in physical_inputs:physical_inputs.append(entry)
        cfg=replace(cfg,emx=replace(cfg.emx,emx_process_file=Path(aliases[original_process])))
        adapter=TransformerOptimizationAdapter(cfg.bounds)
        source_geometry=dict(zip(case['geometry_fields'],case['geometry']))
        # Match existing _geometry_from_rows' shared-line-width mapping; do not
        # invoke that routine's already completed analytical/DRC prechecks.
        vector=[source_geometry['line_width_um'] if k in ('primary_width_um','secondary_width_um')
                else source_geometry[k] for k in adapter.field_order()]
        geometry=adapter.from_vector(vector).with_shared_line_width(source_geometry['line_width_um'])
        require([geometry.flat_dict()[k] for k in case['geometry_fields']]==case['geometry'],'original geometry changed')
        capture={}
        def traced(*,cell,grid_um):
            capture['before']=polygons(cell)
            audit=original_canonicalizer(cell=cell,grid_um=grid_um)
            capture['after']=polygons(cell);capture['grid_audit']=audit;return audit
        def endpoint_capture(**kw):
            capture['ports']=kw['ports']
            capture['labels']=[dict(text=x.text,origin=list(x.origin),layer=x.layer,texttype=x.texttype) for x in kw['cell'].labels]
            raise Captured('BEFORE_ENDPOINT_MUTATION_GDS_OR_NATIVE')
        with patch.object(export,'_canonicalize_cell_to_foundry_grid',traced),patch.object(endpoint,'construct_shared_port_edges',endpoint_capture):
            try:export.export_transformer_layout(geometry,cfg,a.out/rid,validate_geometry=False,port_endpoint_policy=endpoint.POLICY)
            except Captured:pass
        require('ports' in capture,'capture did not reach actual endpoint call')
        original_error=None
        try:endpoint.construct_shared_port_edges(cell=clone(capture),ports=capture['ports'],require_port_labels=True)
        except ValueError as exc:original_error=str(exc)
        require('layout export failed: '+str(original_error)==errors[0],'different failure reproduced')
        capture.update(request_id=rid,evidence_class='NEW_LOCAL_RECONSTRUCTION_NOT_HISTORICAL_SAVED_POLYGONS')
        save(a.out/(rid+'-CAPTURE.json'),capture)
        trial=dict(request_id=rid,geometry_sha256=case['geometry_sha256'],original_error=original_error,capture=pin(a.out/(rid+'-CAPTURE.json')))
        try:
            cell=clone(capture);before=[p.points.tobytes() for p in cell.polygons]
            ports,binding=lineage.bind_cross_centers(cell=cell,ports=capture['ports'],before=capture['before'],after=capture['after'],endpoint=endpoint)
            require(before==[p.points.tobytes() for p in cell.polygons],'lineage changed polygons')
            trial['existing_lineage_binding']=binding
            trial['existing_endpoint_with_lineage']=endpoint.construct_shared_port_edges(cell=cell,ports=ports,require_port_labels=True)
            trial['lineage_status']='LOCAL_CONSTRUCTION_PASS_NOT_PHYSICAL'
        except ValueError as exc:
            trial.update(lineage_status='REJECTED',lineage_error=str(exc))
        results.append(trial)
        print(json.dumps({k:v for k,v in trial.items() if k not in ('existing_endpoint_with_lineage','existing_lineage_binding')}),flush=True)
    if cfg_source:
        expected={p['path']:p for p in cfg_source['source_pins']}
        for name,module in tuple(sys.modules.items()):
            path=getattr(module,'__file__',None)
            if not path or not name.startswith('rfic_transformer_inverse_design'):continue
            rel=Path(path).absolute().relative_to(a.repo.absolute()); original=str(Path(cfg_source['repo'])/rel)
            needpin=expected[original];actual=pin(path)
            require(actual['sha256']==needpin['sha256'] and actual['bytes']==needpin['bytes'],'actual native source mismatch: '+str(rel))
            loaded.append(dict(original=needpin,local=actual))
    require(not list(a.out.rglob('*.gds')),'GDS unexpectedly written')
    report=dict(schema='eucap15_terminal_face_new_six_local_trace.v2',results=results,
        source_pins=[pin(__file__),pin(export.__file__),pin(endpoint.__file__),pin(a.lineage)],
        inputs=[pin(a.cases),pin(a.evidence),pin(a.aliases)],failure_metadata_read=checked,
        native_configuration_pin=pin(a.source_config) if a.source_config else None,
        current_native_source_closure=loaded,
        physical_input_pins=physical_inputs,
        reconstruction_source_status='EXACT_NATIVE_CONFIG_SOURCE_PINS_VERIFIED' if cfg_source else 'LOCAL_MATCHES_PRIOR_SOURCE_CURRENT_NATIVE_CLOSURE_NOT_YET_VERIFIED',
        cadence_runs=0,calibre_runs=0,emx_runs=0,gds_written=0,old_tests_rerun=0,
        old_failures_modified=False,deployment='NOT_INSTALLED',physical_validation='NOT_RUN')
    save(a.out/'REPLAY.json',report)
    print(json.dumps(dict(receipt=pin(a.out/'REPLAY.json'))))


if __name__=='__main__':main()
