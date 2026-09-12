"""Callable-only checks for the five received M35 members. No numerical extraction.

The caller must first bind an exact RETURN receipt to READ_MANIFEST.json and
pass each received file record under emx_gds, drc_summary, drc_gds, geometry_audit.
There is deliberately no CLI, scheduling, remote read, or import-time execution.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path('/Users/wyf/Documents/模拟变压器AI反向建模')
V=ROOT/'github_worktrees/eucap15-mlp-capacity-20260909'


def pin(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Nonsymlink path required')
    before=path.stat();raw=path.read_bytes();after=path.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('Artifact changed during read')
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def write_new(path,obj):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(obj,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write('\n')


def make_context(entrypoints):
    """Bind the already frozen helper pins once for the received five-member loop."""
    for name in ('gds','grid'):
        if pin(entrypoints[name]['source']['path'])!=entrypoints[name]['source']:
            raise ValueError('Frozen helper source drift: '+name)
    if str(V) not in sys.path:sys.path.insert(0,str(V))
    import gdstk
    from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity as identity
    from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as geometry
    from rfic_transformer_inverse_design.layout import foundry_audit,port_ground_metrics
    from rfic_transformer_inverse_design.core.adapter import TransformerOptimizationAdapter
    from rfic_transformer_inverse_design.core.bounds import InductorBounds,TransformerSearchSpace
    for module,name in ((identity,'gds'),(foundry_audit,'grid')):
        if Path(module.__file__).absolute()!=Path(entrypoints[name]['source']['path']):
            raise ValueError('Foreign preimported helper')
    return dict(gdstk=gdstk,identity=identity,geometry=geometry,foundry=foundry_audit,ports=port_ground_metrics,
        adapter=TransformerOptimizationAdapter,winding=InductorBounds,space=TransformerSearchSpace,
        source_pins=[pin(m.__file__) for m in (identity,geometry,foundry_audit,port_ground_metrics)])


def received_path(record):
    """Accept only actual owner source/local identity records, not metadata paths."""
    if record.get('status')!='MATCH':raise ValueError('Received member artifact is not MATCH')
    local=record['local'];source=record['source']
    if pin(local['path'])!=local:raise ValueError('Received local artifact pin mismatch')
    if local['sha256']!=source['sha256'] or local['bytes']!=source['bytes']:
        raise ValueError('Source and local bytes differ')
    return Path(local['path'])


def parameter_gate(context,values,pinned_geometry_config=None):
    """Reuse current pure parser/spec checks only with an explicit pinned config."""
    fields=context['geometry'].GEOMETRY_FIELDS
    if set(values)!=set(fields) or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values.values()):
        return dict(status='FAIL',reason='COMPLETE_FINITE_10D_GEOMETRY_REQUIRED')
    result=dict(geometry_fields=list(fields),geometry_um=values,
        current_canonical9_sha256=context['geometry'].canonical_geometry_sha256(values),
        shared_width_expansion='EXPLICIT_SINGLE_LINE_WIDTH_TO_EQUAL_PRIMARY_SECONDARY_VALUES',
        current_config_parameter_check='NOT_RUN_CURRENT_BOUND_GEOMETRY_CONFIG_NOT_SUPPLIED')
    if pinned_geometry_config is None:return result
    if pin(pinned_geometry_config['path'])!=pinned_geometry_config:
        raise ValueError('Geometry config pin mismatch')
    import yaml
    raw=yaml.safe_load(Path(pinned_geometry_config['path']).read_text())
    def winding(name):
        bounds,topology=raw['bounds'][name],raw['topology'][name]
        if topology['turns']!=1 or topology['center_tap'] is not True:raise ValueError('Unsupported topology')
        return context['winding'](**{k:tuple(bounds[k]) for k in ('outer_width_um','outer_height_um','trace_width_um','spacing_um','terminal_y_span_um','feed_extension_um')},turns=1,center_tap=True)
    adapter=context['adapter'](context['space'](primary=winding('primary'),secondary=winding('secondary'),
        offset_um=tuple(raw['bounds']['offset_um']),topology_mode='1t1t'))
    expanded={**values,'primary_width_um':values['line_width_um'],'secondary_width_um':values['line_width_um']}
    spec=adapter.from_vector([expanded[k] for k in adapter.field_order()]);flat=spec.flat_dict()
    if any(float(flat[k])!=float(expanded[k]) for k in adapter.field_order()):raise ValueError('Parser changed geometry')
    errors=adapter.search_space.validate(spec)+spec.validate()
    result.update(current_config_parameter_check='FAIL' if errors else 'PASS',errors=errors,
        config=pinned_geometry_config,physical_process_proof_from_config=False)
    return result


def verify_member(context,member,records,out,*,return_receipt,pinned_geometry_config=None,bound_port_inputs=None):
    """Check one newly received member; write an isolated result or retained FAIL."""
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    result=dict(schema='eucap15_five_actual_member_binding.v1',utc=datetime.now(timezone.utc).isoformat(),
        **{k:member[k] for k in ('authority_ordinal_zero_based','candidate_id_sha256','candidate_geometry_identity_sha256','benchmark_arm','pair_id_sha256','original_split')},
        return_receipt=return_receipt,screen_receipt_reused=member['screen_receipt'],numerical_extractions=0,
        formal_added=0,fresh_emx=0,source_artifacts_modified=False,implementation=pin(__file__),
        current_process_compatibility='UNKNOWN_SEPARATE_SHARED_EVIDENCE',current_port_compatibility='UNKNOWN',
        formal_uniqueness='NOT_CHECKED',benchmark_reservation_unchanged=True)
    try:
        if member['authority_ordinal_zero_based'] not in (1,2,11,13,15) or member['original_split']!='UNKNOWN':
            raise ValueError('Out-of-scope member or altered split')
        if pin(return_receipt['path'])!=return_receipt:raise ValueError('RETURN receipt pin changed')
        if set(records)!={'emx_gds','drc_summary','drc_gds','geometry_audit'}:raise ValueError('Four actual artifact records required')
        paths={key:received_path(record) for key,record in records.items()}
        drc=json.loads(paths['drc_summary'].read_text());audit=json.loads(paths['geometry_audit'].read_text())
        result['inputs']=[r['local'] for r in records.values()]
        checks={}
        for key in ('candidate_id_sha256','candidate_geometry_identity_sha256'):
            checks[key+'_exact']=drc.get(key)==audit.get(key)==member[key]
        checks['actual_emx_raw_sha_exact']=records['emx_gds']['local']['sha256']==member['expected_actual_emx_gds_sha256']
        checks['actual_drc_raw_sha_exact']=records['drc_gds']['local']['sha256']==drc.get('gds_sha256')==audit.get('gds_sha256')==member['expected_actual_drc_gds_sha256']
        checks['actual_drc_gds_path_bound']=records['drc_gds']['source']['path']==drc.get('gds_path')==audit.get('gds_path')
        checks['actual_geometry_audit_path_and_sha_bound']=records['geometry_audit']['source']['path']==drc.get('geometry_audit_path') and records['geometry_audit']['local']['sha256']==drc.get('geometry_audit_sha256')
        checks['normalization_algorithm_exact']=drc.get('gds_timestamp_normalization_algorithm')==audit.get('gds_timestamp_normalization_algorithm')==member['normalization_algorithm']
        normalized={key:context['identity'].gds_timestamp_normalized_sha256(paths[key]) for key in ('emx_gds','drc_gds')}
        checks['two_actual_normalized_gds_equal_declared']=normalized['emx_gds']==normalized['drc_gds']==drc.get('gds_timestamp_normalized_sha256')==audit.get('gds_timestamp_normalized_sha256')==member['expected_normalized_gds_sha256']
        result.update(binding_checks=checks,actual_normalized_sha256=normalized,
            original_gds_raw_sha_differ=records['emx_gds']['local']['sha256']!=records['drc_gds']['local']['sha256'])
        if not all(checks.values()):raise ValueError('Exact member binding failed: '+','.join(k for k,v in checks.items() if not v))
        actual=out/'transformer_layout_cadpins.gds'
        if Path(records['emx_gds']['source']['path']).name!=actual.name:raise ValueError('Original basename differs from Cadence contract')
        with actual.open('xb') as stream:stream.write(paths['emx_gds'].read_bytes())
        if pin(actual)['sha256']!=records['emx_gds']['local']['sha256']:raise ValueError('Derived evidence copy changed bytes')
        structural=context['identity'].gds_structural_identity(actual)
        library=context['gdstk'].read_gds(str(actual));tops=library.top_level()
        if len(tops)!=1 or tops[0].name!='TRANSFORMER':raise ValueError('Actual top cell mismatch')
        top=tops[0];polygons=top.get_polygons(apply_repetitions=True,include_paths=True,depth=None);labels=top.get_labels(apply_repetitions=True,depth=None)
        grid=context['foundry']._actual_grid_audit(polygons,labels=labels,grid_um=.005)
        parameters=parameter_gate(context,audit.get('geometry_um',{}),pinned_geometry_config)
        result.update(actual_emx_structural=structural,actual_emx_grid=grid,geometry_parameter_check=parameters,
            actual_labels=[dict(text=x.text,layer=x.layer,texttype=x.texttype,xy_um=[float(v) for v in x.origin]) for x in labels],
            actual_gds_copy=pin(actual),historical_geometry_audit_status=audit.get('overall_status'),
            historical_drc_summary=dict(status=drc.get('overall_status'),scope=drc.get('drc_scope'),
                blocking=drc.get('blocking_drc_violation_count'),documented_warnings=drc.get('documented_warning_rules'),
                source_rule_deck_sha256=drc.get('drc_source_rule_deck_sha256')),
            geometry_original_hash_equals_current9dp=parameters.get('current_canonical9_sha256')==member['candidate_geometry_identity_sha256'],
            source_geometry_tuple_binding='AUDIT_FIELDS_ONLY_ORIGINAL_SOURCE_CSV_NOT_RECEIVED',
            hash_namespace_relation='UNPROVEN_DO_NOT_SUBSTITUTE')
        if bound_port_inputs is None:
            result['actual_endpoint_ground_gate']='NOT_RUN_MISSING_ACTUAL_BOUND_POWER_LINE_AND_GROUND_FRAME'
        else:
            for ref in bound_port_inputs.values():
                if pin(ref['path'])!=ref:raise ValueError('Bound port evidence pin changed')
            frame=json.loads(Path(bound_port_inputs['foundry_audit']['path']).read_text())
            power=json.loads(Path(bound_port_inputs['power_line_audit']['path']).read_text())
            if frame.get('gds_sha256')!=pin(actual)['sha256']:raise ValueError('Port frame not bound to actual EMX GDS')
            result['actual_endpoint_ground_gate']=context['ports'].measure_port_ground_metrics(gds_path=actual,power_line_audit=power,foundry_audit=frame)
            result['bound_port_inputs']=bound_port_inputs
        failed=[name for name,value in [('actual_structural',structural['overall_status']),('actual_grid',grid['overall_status']),
            ('complete_finite_geometry',parameters.get('status')),('current_parameters',parameters.get('current_config_parameter_check'))] if value=='FAIL']
        if drc.get('overall_status')!='PASS' or drc.get('blocking_drc_violation_count')!=0:
            failed.append('historical_drc_summary_not_zero_blocking_pass')
        if isinstance(result['actual_endpoint_ground_gate'],dict) and result['actual_endpoint_ground_gate'].get('power_line_check')=='FAIL':
            failed.append('actual_endpoint_ground')
        result.update(status='BOUND_BUT_CURRENT_GATE_FAIL' if failed else 'ARTIFACT_BINDING_ONLY_NOT_PHYSICALLY_CERTIFIED',confirmed_failures=failed,
            helper_sources=context['source_pins'])
        for key,record in records.items():
            if pin(paths[key])!=record['local']:raise ValueError('Actual artifact changed after check')
    except Exception as exc:
        result.update(status='FAIL_ACTUAL_MEMBER_BINDING_OR_INPUT',error=f'{type(exc).__name__}: {exc}')
    write_new(out/'RESULT.json',result)
    return pin(out/'RESULT.json')
