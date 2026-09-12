"""One bounded strict112 authority/DRC metadata join; no physics or dispatch.

Reuse the frozen materializer's pure validators only, not its full-profile CLI.
"""
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from datetime import datetime, timezone

import yaml

R = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
W = R / 'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
I = R / 'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1/strict112_four_20260912T142344723294Z'
OUT = Path(__file__).resolve().parent
PROFILE = R / 'rfic-transformer-inverse-design/configs/current_foundry_small_source_strict112_ffdf.production.v1.json'
M = R / 'rfic-transformer-inverse-design/scripts/materialize_current_foundry_small_source_ffdf.py'
PROFILE_SHA = 'e1148fbe7021e7e27df671f9ce6c57c919b72e4a4b20aa22b35a8fd547094099'
M_SHA = '0319cbdc0ff4744505795093ae432da981b72c0f2e6204be4f888e982005c077'
RECEIPT_SHA = '1db6e971c7ecd8c2063d26d3965974c96af61da5ccbcebf111009ba36a05b686'
PHYSICAL_COLUMNS = {'lp_nh_center','ls_nh_center','q_center','k_abs_center','qp_center','qs_center'}


def read_pinned(path, expected):
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) == (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == expected, (str(path), digest, expected)
    return raw, {'path': str(path), 'sha256': digest, 'bytes': len(raw)}


def pin(path):
    raw = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def put(name, obj):
    with (OUT/name).open('x', encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')


def main():
    assert all(p.name == 'ASSOCIATE.py' for p in OUT.iterdir()), 'no-clobber directory'
    raw, profile_pin = read_pinned(PROFILE, PROFILE_SHA)
    profile = json.loads(raw)
    raw, transport_pin = read_pinned(I/'RECEIPT.json', RECEIPT_SHA)
    received = json.loads(raw)
    _, module_pin = read_pinned(M,M_SHA)
    spec = importlib.util.spec_from_file_location('strict112_existing_pure_validators_m31',M)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    payloads, inputs = {}, {}
    for record in received['files']:
        assert record['status'] == 'MATCH'
        path = Path(record['local']['path'])
        assert path.parent == I
        payloads[record['role']], inputs[record['role']] = read_pinned(path,record['expected_sha256'])
        inputs[record['role']]['original_source_path'] = record['source']['path']
    assert set(inputs) == {'authority_csv','historical_generation_config','drc_index','drc_summary'}
    lineage = {r['role']: r for r in profile['lineage_artifacts']}
    authority_spec = profile['authority_csv']
    drc_spec = lineage['drc_index']
    def parse(role, conf):
        return mod._read_csv_snapshot(types.SimpleNamespace(payload=payloads[role],label=role),
            expected_rows=conf['row_count'],expected_columns=conf['column_count'],
            expected_header_sha256=conf.get('header_sha256'),required_columns=conf['required_columns'])
    authority_header, authority = parse('authority_csv',authority_spec)
    drc_header, drc = parse('drc_index',drc_spec)
    config = yaml.safe_load(payloads['historical_generation_config'])
    batch = json.loads(payloads['drc_summary'])
    mod._assert_json_contract(batch,lineage['drc_summary']['json_assertions'],label='actual_drc_batch_summary')
    assert batch['drc_index_sha256'] == inputs['drc_index']['sha256']
    assert batch['input_index_sha256'] == lineage['gds_index']['sha256']
    assert batch['checks'] and all(v is True for v in batch['checks'].values())

    aid_counts = collections.Counter(r['candidate_id_sha256'] for r in authority)
    did_counts = collections.Counter(r['candidate_id_sha256'] for r in drc)
    duplicate_a = {k:v for k,v in aid_counts.items() if v>1}
    duplicate_d = {k:v for k,v in did_counts.items() if v>1}
    # With duplicates, do not select any candidate; retain raw input references/counts.
    drc_by_id = mod._index_unique(drc,'candidate_id_sha256',label='strict DRC') if not duplicate_d else {}
    if not duplicate_a:
        mod._index_unique(authority,'candidate_id_sha256',label='strict authority')
    did_ordinal = {r['candidate_id_sha256']: j for j,r in enumerate(drc)}
    joins, selected = [], None
    pair_arm = collections.Counter()
    for ordinal, a in enumerate(authority):
        cid = a['candidate_id_sha256']
        d = drc_by_id.get(cid)
        errors = []
        if duplicate_a or duplicate_d:
            errors.append('DUPLICATE_CANDIDATE_IDS_NO_SELECTION')
        if d is None:
            errors.append('MISSING_UNIQUE_DRC_ROW')
        try:
            for key in ('candidate_id_sha256','candidate_geometry_identity_sha256','pair_id_sha256',
                'gds_sha256','drc_gds_sha256','gds_timestamp_normalized_sha256','drc_summary_sha256','touchstone_sha256'):
                mod._canonical_sha_field(a,key,label=f'authority[{ordinal}]')
            if not a['benchmark_arm'].strip():
                raise ValueError('benchmark_arm empty')
            if d is not None:
                for key in ('candidate_id_sha256','candidate_geometry_identity_sha256','gds_sha256',
                    'gds_timestamp_normalized_sha256','drc_summary_sha256'):
                    mod._canonical_sha_field(d,key,label=f'DRC[{did_ordinal[cid]}]')
                mod._require_equal_fields('authority/DRC',(a,d),(
                    'candidate_id_sha256','candidate_geometry_identity_sha256',
                    'gds_timestamp_normalized_sha256','gds_timestamp_normalization_algorithm',
                    'drc_summary_path','drc_summary_sha256'))
                if a['drc_gds_sha256'] != d['gds_sha256']:
                    raise ValueError('authority drc_gds_sha256 != DRC gds_sha256')
                matches = a['gds_raw_sha256_matches_drc'].strip().lower()
                if matches not in ('true','false') or ((matches=='true') != (a['gds_sha256']==a['drc_gds_sha256'])):
                    raise ValueError('raw-vs-DRC binding declaration conflict')
                mode = a['gds_binding_mode'].strip()
                if mode not in ('raw_sha256','timestamp_normalized_sha256') or (mode=='raw_sha256' and matches!='true'):
                    raise ValueError('invalid declared binding mode')
                if d['overall_status'] != 'PASS' or mod._integer(d['blocking_drc_violation_count'],label='blocking') != 0:
                    raise ValueError('DRC row not zero-blocking PASS')
                if d['error'].strip():
                    raise ValueError('DRC error field is nonempty')
            for field in ('gds_path','drc_summary_path','touchstone_path'):
                mod._normalised_row_path(a[field],label=field)
            s4p = Path(a['touchstone_path'])
            evaluation = s4p.parent.parent.name
            if not mod._evaluation_binds_path(evaluation,s4p) or not mod._evaluation_binds_artifact_path(evaluation,Path(a['gds_path'])):
                raise ValueError('evaluation GDS/S4P path binding mismatch')
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
        pair_arm[(a['pair_id_sha256'],a['benchmark_arm'])] += 1
        item = {
            'authority_ordinal_zero_based': ordinal,
            'drc_ordinal_zero_based': did_ordinal.get(cid),
            'candidate_id_sha256': cid,
            'candidate_geometry_identity_sha256': a['candidate_geometry_identity_sha256'],
            'pair_id_sha256': a['pair_id_sha256'], 'benchmark_arm': a['benchmark_arm'],
            'original_authority_metadata': {k:v for k,v in a.items() if k not in PHYSICAL_COLUMNS},
            'original_drc_record': d,
            'authority_row_ref': {'file': inputs['authority_csv'], 'ordinal_zero_based': ordinal},
            'status': 'INDEX_BINDING_PASS_NOT_PHYSICAL_QUALIFICATION' if not errors else 'INDEX_BINDING_FAIL',
            'failures': errors,
            'numeric_features_read_for_selection': False,
            'original_split': 'UNKNOWN', 'evaluation_reservation': 'PRESERVE_BENCHMARK_ARM_AND_PAIR_NO_TRAIN_PROMOTION',
            'actual_gds_drc_s4p_bytes_of_member_read': False,
            'current15ghz_qualification': 'UNKNOWN',
        }
        joins.append(item)
        if selected is None and not errors:
            selected = item
    missing_d = sorted(set(aid_counts)-set(did_counts))
    extra_d = sorted(set(did_counts)-set(aid_counts))
    counts = {
        'authority_rows': len(authority), 'drc_rows': len(drc),
        'authority_candidate_unique': len(aid_counts), 'drc_candidate_unique': len(did_counts),
        'authority_duplicate_candidate_keys': len(duplicate_a), 'drc_duplicate_candidate_keys': len(duplicate_d),
        'matched_candidate_ids': len(set(aid_counts)&set(did_counts)),
        'missing_drc_ids': len(missing_d), 'extra_drc_ids': len(extra_d),
        'index_binding_pass': sum(not j['failures'] for j in joins),
        'index_binding_fail': sum(bool(j['failures']) for j in joins),
        'same_ordinal_candidate_ids': sum(a['candidate_id_sha256']==d['candidate_id_sha256'] for a,d in zip(authority,drc)),
        'candidate_geometry_hash_unique': len({r['candidate_geometry_identity_sha256'] for r in authority}),
        'pair_id_unique': len({r['pair_id_sha256'] for r in authority}),
        'benchmark_arm_counts': dict(collections.Counter(r['benchmark_arm'] for r in authority)),
        'pair_arm_duplicate_combinations': sum(v>1 for v in pair_arm.values()),
        'gds_binding_mode_counts': dict(collections.Counter(r['gds_binding_mode'] for r in authority)),
        'raw_gds_equals_drc_gds_rows': sum(r['gds_sha256']==r['drc_gds_sha256'] for r in authority),
        'drc_blocking_sum': sum(mod._integer(r['blocking_drc_violation_count'],label='blocking') for r in drc),
        'documented_warning_sum': sum(mod._integer(r['documented_warning_count'],label='warnings') for r in drc),
        'new_current_qualified': None, 'formal_added': 0, 'members_physically_reaudited': 0,
    }
    config_record = {
        'source': inputs['historical_generation_config'],
        'target': config['target'], 'topology': config['topology'],
        'process_file_declared': config['emx']['emx_process_file'],
        'port_mode': config['emx']['port_mode'], 'pin_purpose': config['emx']['cadence_pin_purpose'],
        'differential_port_pairs': config['emx']['differential_port_pairs'],
        'power_line_8port': config['emx']['power_line_8port'],
        'foundry_layout': config['emx']['foundry_layout'],
        'solver_args': config['emx']['extra_args'], 'geometry_bounds': config['bounds'],
        'actual_generation_width_lower_um': {'primary':config['bounds']['primary']['trace_width_um'][0],
            'secondary':config['bounds']['secondary']['trace_width_um'][0]},
        'current_geometry_width_lower_required_um': 3.05,
        'selected_member_width_unknown_until_source_geometry_read': True,
        'historical_vs_current_contract': '111_POINTS_AND_3.0UM_GENERATION_BOUND_NOT_CURRENT_56_POINT_OR_3.05UM_BOUND_PROOF',
        'executed_process_bytes_and_current_port_compatibility': 'UNPROVEN_CONFIG_ONLY',
        'historical_q_target_mode_not_applied_to_current_research': True,
        'historical_drc_batch': batch,
        'drc_scope_not_full_chip': True,
        'drc_and_proc_source_bytes_not_reread_here': True,
    }
    put('ROW_BINDINGS.json',{'schema':'eucap15_strict112_actual_authority_drc_join.v1',
        'counts':counts,'authority_header':authority_header,'drc_header':drc_header,
        'preserved_order':'ORIGINAL_AUTHORITY_FILE_ORDER_WITH_ORIGINAL_DRC_ORDINALS',
        'missing_drc_ids':missing_d,'extra_drc_ids':extra_d,
        'duplicate_authority_keys':duplicate_a,'duplicate_drc_keys':duplicate_d,'rows':joins})
    put('SOURCE_CONTRACT_FINDINGS.json',config_record)
    put('SELECTED_MEMBER.json',{'selection_rule':'FIRST_INDEX_BINDING_PASS_IN_ORIGINAL_AUTHORITY_ORDER_NO_NUMERIC_RANKING',
        'selected':selected,'selected_count':int(selected is not None),'fresh_emx_requested':False})
    files = []
    if selected is not None:
        a = selected['original_authority_metadata']
        for role, path_key, sha_key in (
            ('selected_emx_input_gds','gds_path','gds_sha256'),
            ('selected_drc_summary','drc_summary_path','drc_summary_sha256'),
            ('selected_historical_s4p','touchstone_path','touchstone_sha256')):
            files.append({'role':role,'path':a[path_key],'expected_sha256':a[sha_key],
                'max_bytes':2097152,'authority':inputs['authority_csv'],
                'authority_ordinal_zero_based':selected['authority_ordinal_zero_based'],
                'candidate_id_sha256':selected['candidate_id_sha256']})
        files.append({'role':'source_csv_for_selected_10d_geometry',**profile['source_csv'],
            'expected_sha256':profile['source_csv']['sha256'],'max_bytes':2097152,
            'why_required':'Authority has geometry identity only, not the ten numeric fields; need exact source row bound by selected touchstone_path/evaluation. This is not the old full112 extraction.',
            'selected_row_filter':{'touchstone_path':a['touchstone_path']},
            'return_only_selected_row_after_pin_and_header_check':True,
            'preserve_all_source_fields_and_original_ordinal':True})
        gi = lineage['gds_index']
        files.append({'role':'gds_index_for_selected_actual_geometry_binding','path':gi['path'],
            'expected_sha256':gi['sha256'],'max_bytes':1048576,
            'selected_row_filter':{'candidate_id_sha256':selected['candidate_id_sha256']},
            'why_required':'Raw EMX GDS differs from DRC raw SHA; the declared timestamp-normalized identity needs the selected actual GDS index and geometry-audit path, not an inferred filename.',
            'return_only_selected_row_after_pin_and_header_check':True,
            'preserve_all_selected_fields_and_original_ordinal':True})
    request = {'schema':'eucap15_strict112_one_actual_member_artifacts_request.v1',
        'status':'PREPARED_FOR_EXISTING_NATIVE_OWNER_NOT_SENT_BY_THIS_AGENT',
        'selected_candidate_id':selected['candidate_id_sha256'] if selected else None,
        'max_files':len(files),'max_bytes_total':8388608,'files':files,
        'do_not_read_or_execute':['OTHER_111_MEMBERS_S4P_GDS','OLD1875_INDEX_OR_SHARD000','OLD93_MISSING_PATHS',
            'NEW_EMX_GDS_CALIBRE','FULL112_REEXTRACTION','TRAINING','FORMAL_LEDGER_APPEND'],
        'not_physical_dispatch':True,'preserve_pair_and_arm':True,'original_split':'UNKNOWN',
        'no_automatic_training_or_evaluation_reservation_change':True,
        'if_selected_gds_index_points_to_separate_drc_gds_or_geometry_audit':
            'Return the exact selected-row path/SHA for the next necessary binding. Do not guess paths or expand to all candidates.',
        'next_local_step':'Verify selected actual byte pins; current GDS grid/port/geometry compatibility and historical111 15GHz/SRF extraction only for this member, preserving old results and original evaluation reservation.',
        'production_continues_independently':True}
    put('MINIMUM_MEMBER_READ_REQUEST.json',request)
    for role, p in inputs.items():
        assert pin(Path(p['path']))['sha256']==p['sha256'],role
    receipt = {'schema':'eucap15_strict112_actual_index_m31_receipt.v1',
        'completed_utc':datetime.now(timezone.utc).isoformat(),'status':'COMPLETE_INDEX_JOIN_ONE_MEMBER_SELECTED_NOT_QUALIFIED',
        'inputs':inputs,'transport_receipt':transport_pin,'profile':profile_pin,
        'reused_source':module_pin,'reused_functions':['_read_csv_snapshot','_index_unique','_canonical_sha_field',
            '_require_equal_fields','_integer','_assert_json_contract','_normalised_row_path',
            '_evaluation_binds_path','_evaluation_binds_artifact_path'],
        'command':[sys.executable,'-B',*sys.argv],'cwd':str(Path.cwd()),'source':pin(Path(__file__).resolve()),
        'counts':counts,'selected_candidate_id':selected['candidate_id_sha256'] if selected else None,
        'source_split_not_assumed_train':True,'p215_overlap_unproven':True,
        'old_expected111_or_range_filter_not_run':True,'actual_member_bytes_not_yet_read':True,
        'native_calls':0,'new_formal_submissions':0,'full_profile_executed':False,
        'artifacts':{n:pin(OUT/n) for n in ('ROW_BINDINGS.json','SOURCE_CONTRACT_FINDINGS.json',
            'SELECTED_MEMBER.json','MINIMUM_MEMBER_READ_REQUEST.json')}}
    put('RECEIPT.json',receipt)
    with (OUT/'SHA256SUMS').open('x') as f:
        for n in ('ASSOCIATE.py','ROW_BINDINGS.json','SOURCE_CONTRACT_FINDINGS.json',
            'SELECTED_MEMBER.json','MINIMUM_MEMBER_READ_REQUEST.json','RECEIPT.json'):
            f.write(f"{pin(OUT/n)['sha256']}  {n}\n")
    print(json.dumps({'counts':counts,'selected':receipt['selected_candidate_id'],
        'request':pin(OUT/'MINIMUM_MEMBER_READ_REQUEST.json'),'receipt':pin(OUT/'RECEIPT.json'),
        'sha256sums':pin(OUT/'SHA256SUMS')}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        if not (OUT/'FAILURE.json').exists():
            put('FAILURE.json',{'status':'FAIL_LOCAL_INDEX_ASSOCIATION','type':type(exc).__name__,
                'error':str(exc),'native_calls':0,'formal_added':0})
        raise
