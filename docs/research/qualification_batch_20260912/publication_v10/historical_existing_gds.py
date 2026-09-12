"""Audit existing historical GDS with the unchanged physical core; no native tools."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import stat
from types import FunctionType, SimpleNamespace

from research.broadband56_nn import frequency_research_gds_audit as core

CORE_SHA='6251d5976600a382abdc967140fcc424c603154525d5115c54d4b9146fd8da23'
SCHEMA='eucap15_historical_existing_gds_request.v1'
ARTIFACTS=('source','power','direct','gds','summary','port_manifest','emx_command')


def _path(path):
    p=Path(path)
    core.require(p.is_absolute() and '..' not in p.parts and not any(x.is_symlink() for x in (p,*p.parents)),
        'ABSOLUTE_NONSYMLINK_INPUT_REQUIRED: '+str(path))
    return p


def _pinned(expected,path_map,role):
    p=_path(path_map.get(expected['path'],expected['path']))
    if not p.is_file():raise FileNotFoundError('MISSING_HISTORICAL_ARTIFACT: '+role+': '+str(p))
    core.require(set(expected)=={'path','sha256','bytes'},'EXACT_ARTIFACT_PIN_REQUIRED: '+role)
    actual=core.pin(p)
    core.require((actual['sha256'],actual['bytes'])==(expected['sha256'],expected['bytes']),
        'HISTORICAL_ARTIFACT_CHANGED: '+role)
    return actual


def _option(command,flag,n=1):
    core.require(command.count(flag)==1,'EXACT_SINGLE_COMMAND_OPTION_REQUIRED: '+flag)
    at=command.index(flag);values=command[at+1:at+1+n]
    core.require(len(values)==n,'MISSING_COMMAND_OPTION_VALUE: '+flag)
    return values


def _s4p_reference(value,identity,source_row,path_map):
    receipt_pin=_pinned(value['s4p_verification_receipt'],path_map,'s4p_verification_receipt')
    receipt=core.read_json(receipt_pin['path'])
    core.require(receipt['schema']=='p215_training_source100_private_s4p_transport.v1','UNSUPPORTED_S4P_VERIFICATION_RECEIPT')
    matches=[r for r in receipt['records'] if r['expected']['source_row_index']==identity['source_row_index']
        and r['expected']['ordinal']==identity['source_prefix_ordinal'] and r['expected']['evaluation']==identity['evaluation']]
    core.require(len(matches)==1,'UNIQUE_HISTORICAL_RESPONSE_BINDING_REQUIRED')
    record=matches[0];pin=value['historical_s4p']
    core.require(record['status']=='EXACT_HISTORICAL_SHA_VERIFIED' and record['actual']==pin and
        record['expected']['path']==pin['path'] and record['expected']['sha256']==pin['sha256'] and
        source_row['touchstone_path']==pin['path'] and source_row['touchstone_sha256']==pin['sha256'],
        'HISTORICAL_RESPONSE_SOURCE_BINDING_CHANGED')
    # No S4P body/number/hash read: this is a prior receipt reference, not a new
    # claim about response bytes. On its original host, verify the saved stat.
    p=_path(path_map.get(pin['path'],pin['path']));s=p.lstat()
    core.require(stat.S_ISREG(s.st_mode) and s.st_size==pin['bytes'],'HISTORICAL_RESPONSE_MISSING_OR_SIZE_CHANGED')
    observed=[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink]
    same_original=str(p)==pin['path']
    if same_original:core.require(observed==record['source_stat'],'HISTORICAL_RESPONSE_SOURCE_STAT_CHANGED')
    else:core.require(str(p)==record['local']['path'] and
        (record['local']['sha256'],record['local']['bytes'])==(pin['sha256'],pin['bytes']),
        'UNBOUND_HISTORICAL_RESPONSE_COPY')
    return dict(pin=pin,verification_receipt=receipt_pin,readable_path=str(p),observed_stat=observed,
        verification='PRIOR_SHA_RECEIPT_PLUS_CURRENT_LSTAT_NOT_REHASHED',source_stat_revalidated=same_original,
        current_copy_sha_reverified=False,body_reads=0,numeric_reads=0)


def load_request(path):
    core.require(core.pin(core.__file__)['sha256']==CORE_SHA,'WRONG_PHYSICAL_CORE_SOURCE')
    request_pin=core.pin(_path(path));value=core.read_json(path)
    core.require(value['schema']==SCHEMA,'WRONG_HISTORICAL_GDS_REQUEST_SCHEMA')
    pm=value.get('path_map',{});identity=value['source_row']
    core.require(type(identity['source_row_index']) is int and identity['source_row_index']>=0 and
        type(identity['source_prefix_ordinal']) is int and identity['source_prefix_ordinal']>=0 and
        isinstance(identity['evaluation'],str) and identity['evaluation'],'EXACT_HISTORICAL_SOURCE_ROW_REQUIRED')
    core.require(set(value['artifacts'])==set(ARTIFACTS),'COMPLETE_EXISTING_GDS_ARTIFACT_SET_REQUIRED')
    evaluation_root=Path(value['evaluation_root'])
    core.require(evaluation_root.is_absolute() and '..' not in evaluation_root.parts,'EXPLICIT_ORIGINAL_EVALUATION_ROOT_REQUIRED')
    relative=dict(source='layout/foundry_layout_source_audit.json',power='layout/power_line_8port_geometry.json',
        direct='layout/transformer_layout.gds',gds='streamout/transformer_layout_cadpins.gds',
        port_manifest='layout/transformer_layout.layout.json',emx_command='emx/emx_command.json')
    core.require(all(Path(value['artifacts'][k]['path'])==evaluation_root/name for k,name in relative.items()) and
        Path(value['artifacts']['summary']['path']).parent==evaluation_root and
        Path(value['historical_s4p']['path'])==evaluation_root/'emx/emx.s4p',
        'ARTIFACTS_NOT_BOUND_TO_EXPLICIT_HISTORICAL_EVALUATION')
    # Source evidence is first, so known ENOENT is reported before attempting
    # backend loading or interpreting skipped historical geometry metrics.
    originals={k:_pinned(value['artifacts'][k],pm,k) for k in ARTIFACTS}
    csv_pin=_pinned(identity['source_csv'],pm,'source_csv')
    manifest_pin=_pinned(identity['identity_manifest'],pm,'identity_manifest')
    with Path(csv_pin['path']).open(newline='') as stream:
        matches=[r for r in csv.DictReader(stream) if int(r['row_index'])==identity['source_prefix_ordinal']]
    core.require(len(matches)==1 and matches[0]['evaluation']==identity['evaluation'],'SOURCE_CSV_ROW_BINDING_CHANGED')
    row=matches[0];manifest=core.read_json(manifest_pin['path'])
    core.require(manifest['schema']=='p215_new_training_source100_gds_current_bindings.v1','UNSUPPORTED_SOURCE_IDENTITY_MANIFEST')
    matching=[r for r in manifest['rows'] if r['source_prefix_ordinal']==identity['source_prefix_ordinal']]
    core.require(len(matching)==1,'UNIQUE_MANIFEST_SOURCE_ROW_REQUIRED');bound=matching[0]
    core.require(bound['source_row_index']==identity['source_row_index'] and bound['evaluation']==identity['evaluation']
        and bound['gds_remote_path']==value['artifacts']['gds']['path'] and
        bound['gds_sha256']==value['artifacts']['gds']['sha256'] and
        bound['historical_s4p_sha256']==value['historical_s4p']['sha256'] and bound['top_cell']=='TRANSFORMER',
        'SOURCE_MANIFEST_GDS_RESPONSE_IDENTITY_CHANGED')
    fields=value['geometry_fields'];vector=value['original_geometry']
    core.require(fields==list(core.GEOMETRY_FIELDS) and len(vector)==10 and
        all(type(x) in (int,float) and math.isfinite(x) for x in vector),'ORIGINAL_FINITE_ORDERED_10D_REQUIRED')
    core.require([float(row['geom__'+k]) for k in fields]==vector,'SOURCE_CSV_ORIGINAL_GEOMETRY_CHANGED')
    wanted=dict(zip(fields,vector));summary=core.read_json(originals['summary']['path'])
    core.require(summary['ok'] is True and summary['error'] is None and
        summary['touchstone_path']==value['historical_s4p']['path'],'SUCCESSFUL_HISTORICAL_SUMMARY_BINDING_REQUIRED')
    geometry_sha=core.canonical_geometry_sha256(wanted)
    core.require(geometry_sha==core.canonical_geometry_sha256(summary['geometry']),'HISTORICAL_SUMMARY_GEOMETRY_CHANGED')
    for key,role in (('cadence_gds','gds'),('export_gds','direct'),('export_manifest','port_manifest')):
        core.require(summary['artifacts'][key]==value['artifacts'][role]['path'],'SUMMARY_ARTIFACT_BINDING_CHANGED: '+role)
    command=core.read_json(originals['emx_command']['path'])
    core.require(isinstance(command,list) and len(command)>4 and all(isinstance(x,str) for x in command),
        'ORIGINAL_EMX_COMMAND_MUST_BE_ARGV_ARRAY')
    core.require(command[1]==value['artifacts']['gds']['path'] and command[2]==summary['artifacts']['top_cell']=='TRANSFORMER'
        and _option(command,'-s')[0]==summary['touchstone_path'],'COMMAND_GDS_TOPCELL_S4P_BINDING_CHANGED')
    port_manifest=core.read_json(originals['port_manifest']['path'])
    core.require(port_manifest['top_cell']=='TRANSFORMER' and port_manifest['layout_path']==value['artifacts']['direct']['path']
        and port_manifest['cadence_pin_purpose']==51 and command.count('--cadence-pins=51')==1,
        'PORT_MANIFEST_TOPCELL_LAYOUT_PIN_PURPOSE_CHANGED')
    ports=[{'name':f'P{i:03d}','signal_labels':[f'P{i:03d}'],'ground_labels':[f'P{i:03d}_G']} for i in range(1,5)]
    core.require([{k:r[k] for k in ('name','signal_labels','ground_labels')} for r in port_manifest['ports']]==ports and
        [x for x in command if x.startswith('--port=')]==[f'--port=P{i:03d}=P{i:03d}:P{i:03d}_G' for i in range(1,5)],
        'ORIGINAL_FOUR_DIFFERENTIAL_PORT_BINDING_CHANGED')
    core.require(command[3]==port_manifest['process_layer_summary']['process_file'] and '/TSMC65_05_12_26/' in command[3],
        'COMMAND_PORT_MANIFEST_PROCESS_CHANGED')
    response=_s4p_reference(value,identity,row,pm)
    paths={k:Path(p['path']) for k,p in originals.items()}
    request_id='HISTORICAL-SOURCE-'+str(identity['source_row_index'])+'-'+manifest_pin['sha256'][:16]
    context=dict(evidence_class='HISTORICAL_EXISTING_GDS_CURRENT_AUDIT',source_row_index=identity['source_row_index'],
        source_prefix_ordinal=identity['source_prefix_ordinal'],evaluation=identity['evaluation'],historical_s4p=response,
        original_geometry_identity=bound,historical_execution_gds_sha_proven=False,new_candidate_generated=False)
    candidate=dict(candidate_id=request_id,candidate_id_sha256=hashlib.sha256(request_id.encode()).hexdigest(),
        candidate_geometry_identity_sha256=geometry_sha)
    runtime=value['runtime']
    verified={k:_pinned(runtime[k],pm,k) for k in ('configuration','foundry_contract')}
    core.require(verified['configuration']['sha256']==core.CONFIG_SHA256 and
        verified['foundry_contract']['sha256']==core.FOUNDRY_CONTRACT_SHA256,'CURRENT_PHYSICAL_CONTRACT_CHANGED')
    core.require(set(runtime['core_sources'])==set(core.CORE_MODULES),'EXACT_EXISTING_BACKEND_SET_REQUIRED')
    verified['core_sources']={k:_pinned(p,pm,'core:'+k) for k,p in runtime['core_sources'].items()}
    core.require(all(p['sha256']==core.PROVEN_CORE_SHA256[k] for k,p in verified['core_sources'].items()),'PHYSICAL_BACKEND_CHANGED')
    sources=dict(source_csv=csv_pin,identity_manifest=manifest_pin,s4p_verification_receipt=response['verification_receipt'])
    return SimpleNamespace(value=value,request_pin=request_pin,paths=paths,original=originals,summary=summary,wanted=wanted,
        candidate=candidate,context=context,sources=sources,runtime=verified,command=command)


def bound_auditor(request):
    def historical_inputs(candidate,shard):
        core.require(candidate==request.candidate and shard is None,'HISTORICAL_AUDIT_CONTEXT_CHANGED')
        return request.paths,request.original,request.summary,request.wanted
    scope=dict(core._audit_candidate.__globals__);scope['_candidate_inputs']=historical_inputs
    return FunctionType(core._audit_candidate.__code__,scope,core._audit_candidate.__name__,
        core._audit_candidate.__defaults__,core._audit_candidate.__closure__)


def audit(request_path,out):
    out=_path(out);preliminary=core.read_json(request_path)
    request_file=_path(request_path)
    protected=[_path(preliminary.get('path_map',{}).get(p['path'],p['path'])) for p in preliminary['artifacts'].values()]
    core.require(out!=request_file and not request_file.is_relative_to(out) and
        not out.is_relative_to(Path(core.__file__).resolve().parents[2]) and
        not any(out==p or out.is_relative_to(p.parent) or p.is_relative_to(out) for p in protected),
        'OUTPUT_OVERLAPS_HISTORICAL_INPUTS')
    out.mkdir(parents=True,exist_ok=False)
    try:
        request=load_request(request_path);backend=core._load_backend(request.runtime)
        core.require(str(backend.cfg.emx.emx_process_file)==request.command[3],'CURRENT_CONFIG_ORIGINAL_PROCESS_PATH_MISMATCH')
        result,row=bound_auditor(request)(request.candidate,None,out,backend,request)
        core.require(core.pin(request_path)==request.request_pin,'HISTORICAL_REQUEST_CHANGED_DURING_AUDIT')
        for p in request.sources.values():core.verify_pin(p)
        for key in ('configuration','foundry_contract'):core.verify_pin(request.runtime[key])
        for p in request.runtime['core_sources'].values():core.verify_pin(p)
        core.require(core.pin(core.__file__)['sha256']==CORE_SHA,'PHYSICAL_CORE_CHANGED_DURING_AUDIT')
        response=request.context['historical_s4p'];s=Path(response['readable_path']).lstat()
        core.require([s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink]==response['observed_stat'],
            'HISTORICAL_RESPONSE_METADATA_CHANGED_DURING_AUDIT')
        receipt=dict(schema='eucap15_historical_existing_gds_audit.v1',status=result['status'],request=request.context,
            input_request=request.request_pin,physical_core=core.pin(core.__file__),result=result,
            N_existing_historical_geometries=1,new_geometry_count=0,cadence_started=False,calibre_started=False,emx_started=False,
            s4p_body_reads=0,s4p_numeric_reads=0,formal_accepted_added=0,
            REAL_EMX_VALIDATION='HISTORICAL_RESPONSE_REFERENCE_NO_NEW_SOLVE',
            qualification='CURRENT_GDS_AUDIT_ONLY_NOT_AUTOMATIC_HISTORICAL_OR_PRODUCTION_ACCEPTANCE')
        if result['status']=='PASS':
            path=out/'CALIBRE_INPUT.csv'
            with path.open('x',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=core.CALIBRE_FIELDS);writer.writeheader();writer.writerow(row)
            receipt['calibre_input']=core.pin(path)
        core.save_json(out/'HISTORICAL_GDS_RECEIPT.json',receipt);return receipt
    except Exception as error:
        core.save_json(out/'HISTORICAL_GDS_FAILURE.json',dict(schema='eucap15_historical_existing_gds_failure.v1',status='FAIL',
            error=type(error).__name__+': '+str(error),request_path=str(request_path),partial_evidence_preserved=True,
            cadence_started=False,calibre_started=False,emx_started=False,formal_accepted_added=0))
        raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--out')
    parser.add_argument('--validate-only',action='store_true');args=parser.parse_args()
    if args.validate_only:
        if args.out:parser.error('--validate-only does not accept --out')
        request=load_request(args.request)
        print(json.dumps(dict(status='HISTORICAL_INPUTS_BOUND_NOT_GDS_AUDITED',source=request.context,s4p_body_reads=0)))
    else:
        if not args.out:parser.error('--out required')
        print(json.dumps(audit(args.request,args.out)))


if __name__=='__main__':main()
