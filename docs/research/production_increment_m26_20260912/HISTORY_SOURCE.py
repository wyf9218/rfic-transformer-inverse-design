"""One bounded local join of received shard000 metadata, not certification."""
import csv
import hashlib
import importlib.util
import io
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml
from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as geometry

ROOT = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
CODE = ROOT / 'github_worktrees/eucap15-mlp-capacity-20260909'
K = ROOT / 'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1/historical_shard000_files_20260912T125517138418Z'
OUT = Path(__file__).parent
EXPECTED_RECEIPT = '820827519bf6d5edd41dda17005c7c75f6918d4afe955501f37f80e331b6f935'
PROVENANCE = CODE / 'scripts/audit_mars56_s4p_candidate_queue_provenance.py'
spec = importlib.util.spec_from_file_location('saved_queue_provenance', PROVENANCE)
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


def pin(path, raw=None):
    raw = path.read_bytes() if raw is None else raw
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def write(name, value):
    with (OUT / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def number(row, field):
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError('Nonfinite ' + field)
    return value


def equal_number(left, right):
    return Decimal(left) == Decimal(right)


def main():
    assert not (OUT / 'RECEIPT.json').exists(), 'No-clobber output already exists'
    raw = (K / 'RECEIPT.json').read_bytes()
    assert pin(K / 'RECEIPT.json', raw)['sha256'] == EXPECTED_RECEIPT
    parent = json.loads(raw)
    inputs = {'parent_receipt': pin(K / 'RECEIPT.json', raw)}
    bodies = {}
    for item in parent['files']:
        path = Path(item['local_copy']['path'])
        assert path.parent == K and not path.is_symlink()
        raw = path.read_bytes()
        actual = pin(path, raw)
        assert actual == item['local_copy'], 'Received bytes changed: ' + path.name
        inputs[item['request']['name']] = dict(local=actual, remote=item['actual_pin'],
            historical_pin_match=item['historical_sha_match'], received_status=item['status'])
        bodies[item['request']['name']] = raw
    candidates = list(csv.DictReader(io.StringIO(bodies['candidate_source'].decode('utf-8-sig'))))
    dataset = list(csv.DictReader(io.StringIO(bodies['dataset_rows_source'].decode('utf-8-sig'))))
    manifest = json.loads(bodies['dataset_manifest'])
    ground = json.loads(bodies['ground_clearance_audit'])
    config = yaml.safe_load(bodies['config_source'])
    assert len(candidates) == len(dataset) == len(ground['records']) == 64, 'Not the bounded 64-row shard'
    by_candidate = {row['candidate_id']: (i, row) for i, row in enumerate(candidates)}
    by_ground = {row['cache_key']: row for row in ground['records']}
    assert len(by_candidate) == len(by_ground) == 64, 'Primary key duplicates'
    assert len({row['evaluation'] for row in dataset}) == 64, 'Evaluation duplicate'
    assert len({row['queue__candidate_id'] for row in dataset}) == 64, 'Candidate join repeats'
    fields10 = list(geometry.GEOMETRY_FIELDS)
    fields11 = list(manifest['field_order'])
    assert fields11 == fields10[:4] + ['primary_width_um', 'secondary_width_um'] + fields10[5:]
    all_geometry_fields = fields10 + ['primary_width_um', 'secondary_width_um']
    source_fields = list(candidates[0])
    dataset_fields = list(dataset[0])
    source_split_fields = [key for key in source_fields + dataset_fields if 'split' in key.lower()]
    outputs, immutable11, mapped_hashes, fingerprints = [], [], [], []
    for dataset_index, row in enumerate(dataset):
        candidate_id, evaluation = row['queue__candidate_id'], row['evaluation']
        candidate_index, candidate = by_candidate[candidate_id]
        audit = by_ground[evaluation]
        assert row['work_dir'] == audit['work_dir'] and row['touchstone_path'] == audit['touchstone_path']
        assert int(row['queue_row_index']) == candidate_index, 'Queue ordinal mismatch'
        mismatch = [field for field in all_geometry_fields
                    if not equal_number(candidate[field], row['geom__' + field])]
        alias_equal = all(equal_number(candidate[field], candidate['line_width_um'])
                          for field in ('primary_width_um', 'secondary_width_um'))
        dataset_alias_equal = all(equal_number(row['geom__' + field], row['geom__line_width_um'])
                                  for field in ('primary_width_um', 'secondary_width_um'))
        quantization = number(candidate, 'geometry_fingerprint_quantization_um')
        observed_fingerprint = candidate['geometry_fingerprint_sha256']
        recomputed = provenance._canonical_geometry_fingerprint(candidate, quantization)
        fingerprint_match = (candidate['geometry_fingerprint_schema'] == provenance.GEOMETRY_FINGERPRINT_SCHEMA
                             and recomputed == observed_fingerprint == row['queue__geometry_fingerprint_sha256']
                             and equal_number(candidate['geometry_fingerprint_quantization_um'],
                                              row['queue__geometry_fingerprint_quantization_um'])
                             and candidate['geometry_fingerprint_schema'] == row['queue__geometry_fingerprint_schema'])
        geometry11_strings = [str(Decimal(candidate[field]).normalize()) for field in fields11]
        immutable11.append(tuple(geometry11_strings))
        fingerprints.append(observed_fingerprint)
        mapping_allowed = alias_equal and dataset_alias_equal and not mismatch
        geometry10 = {field: number(candidate, field) for field in fields10} if mapping_allowed else None
        mapped = geometry.canonical_geometry_sha256(geometry10) if geometry10 is not None else None
        if mapped:
            mapped_hashes.append(mapped)
        actual = dict(Lp_nH=number(row, 'lp_nh_center'), Ls_nH=number(row, 'ls_nh_center'),
                      Qp=number(row, 'qp_center'), Qs=number(row, 'qs_center'),
                      K_signed=number(row, 'k_center'))
        actual['Qmin'] = min(actual['Qp'], actual['Qs'])
        actual['K_abs'] = abs(actual['K_signed'])
        response_in_range = .5 <= actual['Lp_nH'] <= 2 and .5 <= actual['Ls_nH'] <= 2 and .2 <= actual['K_abs'] <= .85
        q_intersection = response_in_range and 10 <= actual['Qmin'] <= 20
        center_fields = ('physical_feature_center_freq_hz', 'metrics__center_frequency_hz')
        declared_15ghz = all(number(row, field) == 15e9 for field in center_fields)
        declared_111 = (number(row, 'sparam_freq_points') == 111 and number(row, 'sparam_freq_start_hz') == 5e9
                        and number(row, 'sparam_freq_stop_hz') == 60e9 and number(row, 'sparam_freq_step_hz') == .5e9)
        gaps = ['ACTUAL_S4P_BYTES_SHA_AND_CANDIDATE_EXECUTION_BINDING_NOT_READ',
                'ACTUAL_GDS_BYTES_PORT_GEOMETRY_AND_GENERATOR_BINDING_MISSING',
                'CURRENT_CALIBRE_DRC_RESULT_AND_RULEDECK_BINDING_MISSING',
                'EXECUTED_PROCESS_BYTES_AND_CURRENT_COMPATIBILITY_BINDING_MISSING',
                'EXECUTED_PORT_MODE_AND_RETURN_GROUND_BINDING_MISSING',
                'CURRENT_EXTRACTION_STRICT_AND_HALF_SRF_EVIDENCE_MISSING',
                'CROSS_SOURCE_UNIQUE_INDEX_CHECK_NOT_DONE', 'ORIGINAL_SPLIT_NOT_DECLARED',
                'FORMAL_SUBMISSION_AND_READBACK_NOT_DONE']
        if not fingerprint_match:
            gaps.append('CANDIDATE_FINGERPRINT_BINDING_CONFLICT')
        if mismatch:
            gaps.append('CANDIDATE_DATASET_GEOMETRY_CONFLICT')
        if not alias_equal or not dataset_alias_equal:
            gaps.append('UNEQUAL_WIDTHS_CANNOT_MAP_TO_SHARED_WIDTH_10D')
        category = ('INCOMPATIBLE_SHARED_WIDTH_10D' if not alias_equal or not dataset_alias_equal else
                    'IDENTITY_BINDING_CONFLICT' if mismatch or not fingerprint_match else
                    'PENDING_SOURCE_AND_PHYSICAL_ARTIFACT_EVIDENCE')
        outputs.append(dict(dataset_row_index=dataset_index, candidate_row_index=candidate_index,
            evaluation=evaluation, candidate_id=candidate_id, source_candidate_id=candidate['source_candidate_id'],
            queue_row_index=int(row['queue_row_index']), selection_source=candidate['selection_source'],
            source_split='UNKNOWN', qualification_group=category, current_qualification='UNKNOWN',
            original_11d_fields=fields11, original_11d_geometry=geometry11_strings,
            width_values=dict(candidate_line=candidate['line_width_um'], candidate_primary=candidate['primary_width_um'],
                candidate_secondary=candidate['secondary_width_um'], dataset_line=row['geom__line_width_um'],
                dataset_primary=row['geom__primary_width_um'], dataset_secondary=row['geom__secondary_width_um']),
            candidate_widths_exactly_equal=alias_equal, dataset_widths_exactly_equal=dataset_alias_equal,
            geometry_join_mismatches=mismatch, shared_width_10d_mapping='EXPLICIT_LOSSLESS_VALUE_MAPPING_ONLY' if mapping_allowed else 'NOT_ALLOWED',
            mapped_10d_fields=fields10 if mapping_allowed else None, mapped_10d_geometry=geometry10,
            canonical9_geometry_sha256=mapped, canonical9_is_not_production_fingerprint=True,
            original_fingerprint=dict(schema=candidate['geometry_fingerprint_schema'], quantization_um=quantization,
                sha256=observed_fingerprint, recomputed_sha256=recomputed, exact_join_match=fingerprint_match),
            historical_reported_status=dict(ok=row['ok'], geometry_check_ok=row['geometry_check_ok'],
                geometry_check_backend=row['geometry_check_backend'], geometry_check_skipped=row['geometry_check__skipped']),
            historical_reported_em_response=actual,
            response_evidence_class='HISTORICAL_CSV_REPORTED_EM_METRICS_NOT_REEXTRACTED_NOT_CURRENT_CERTIFIED',
            reported_response_in_new_lpls_k_range=response_in_range, reported_response_q10_20_intersection=q_intersection,
            actual_response_is_not_proxy=True,
            prediction_columns={key: candidate[key] for key in source_fields if key.startswith(('pred_', 'raw_pred_', 'calibrated_pred_'))},
            target_columns={key: candidate[key] for key in source_fields if key.startswith('target_')},
            prediction_value_source=candidate['prediction_value_source'], prediction_or_target_used_for_qualification=False,
            frequency_metadata=dict(center15_declared=declared_15ghz, grid111_5to60_step0p5_declared=declared_111,
                nominal_15ghz_index_if_metadata_correct=20 if declared_111 else None, raw_frequency_vector_verified=False,
                current_56point_conversion='NOT_PERFORMED_NO_INTERPOLATION', half_srf='UNKNOWN'),
            actual_artifact_pointers=dict(work_dir=row['work_dir'], touchstone_path=row['touchstone_path'],
                s4p_sha256=None, gds_path=None, gds_sha256=None, calibre_result_path=None,
                declaration_only=True, local_or_remote_actual_artifact_readability='NOT_CHECKED'),
            ground_audit=audit, missing_evidence=gaps, formal_admitted_by_this_action=False))
    assert {row['candidate_id'] for row in outputs} == set(by_candidate), 'Orphan candidate'
    write('ROWS.json', dict(schema='eucap15_historical_shard000_bound_rows.v1', source_receipt=inputs['parent_receipt'],
                           rows=outputs))
    categories = Counter(row['qualification_group'] for row in outputs)
    summary = dict(schema='eucap15_historical_shard000_local_partition_receipt.v1',
        completed_utc=datetime.now(timezone.utc).isoformat(), status='LOCAL_METADATA_JOIN_COMPLETE_QUALIFICATION_PENDING',
        command=sys.argv, cwd=str(Path.cwd()), inputs=inputs,
        reused_functions={'historical_fingerprint': '_canonical_geometry_fingerprint',
                          'research_identity': 'canonical_geometry_sha256'},
        implementation=pin(Path(__file__)), reused_implementations=[pin(PROVENANCE), pin(Path(geometry.__file__))],
        rows_processed=64, candidate_rows=64, dataset_rows=64, ground_rows=64,
        primary_keys={'candidate_csv':'candidate_id','dataset_csv':'evaluation','ground_audit':'cache_key'},
        candidate_dataset_join={'left':'candidate_id','right':'queue__candidate_id','cardinality':'ONE_TO_ONE','matched':64,'orphans':0},
        candidate_dataset_geometry_conflicts=sum(bool(row['geometry_join_mismatches']) for row in outputs),
        exact_11d_unique_geometry=len(set(immutable11)), historical_fingerprint_unique=len(set(fingerprints)),
        candidate_shared_width_equal=sum(row['candidate_widths_exactly_equal'] for row in outputs),
        dataset_shared_width_equal=sum(row['dataset_widths_exactly_equal'] for row in outputs),
        research_canonical9_unique=len(set(mapped_hashes)), fingerprint_matched_rows=sum(row['original_fingerprint']['exact_join_match'] for row in outputs),
        qualification_group_counts=dict(categories), current_qualified_unique_count=None,
        current_qualified_count_status='UNKNOWN_NOT_ZERO', certified_by_this_action=0, formal_submissions=0,
        reported_response_range_count=sum(row['reported_response_in_new_lpls_k_range'] for row in outputs),
        reported_response_q10_20_intersection_count=sum(row['reported_response_q10_20_intersection'] for row in outputs),
        reported_response_range_counts_are_not_current_qualified_counts=True,
        historical_ok_rows=sum(row['historical_reported_status']['ok']=='True' for row in outputs),
        historical_ground_counts={key:ground[key] for key in ('candidate_count','pass_count','reject_count','missing_or_other_count')},
        source_split_fields=source_split_fields, source_split_unknown_rows=64 if not source_split_fields else None,
        data_contract={'declared_historical_config_emx':config['emx'],'declared_historical_manifest_port_mode':manifest['port_mode'],
            'declared_historical_manifest_port_pairs':manifest['differential_port_pairs'],
            'declared_historical_target_frequency':manifest['target_frequency'],
            'current_geometry_fields_from_source':fields10,'current_process_port_compatibility':'NOT_PROVEN_BY_PROCESS_NAME_OR_CONFIG_PATH',
            'historical_config_q_target_mode':config['target']['q_target_mode'],
            'historical_q_objective_not_current_q_scan_rule':True},
        missing_evidence_counts=dict(Counter(gap for row in outputs for gap in row['missing_evidence'])),
        minimum_next_binding={'per_sample':'Bind the listed evaluation/work_dir to actual S4P SHA, actual GDS/port geometry and Calibre artifacts, then current strict/SRF re-extraction.',
            'shared_source':'Verify executed process bytes, port/grounding and generator/extractor/ruledeck versions; configuration declarations alone do not close execution identity.',
            'counting':'Preserve UNKNOWN source split, cross-source deduplicate only after compatible source proof, and use sole formal writer with readback.'},
        exclusions={'training':0,'new_emx':0,'ssh':0,'tests':0,'old93_rows_reprocessed':0,'p215_full_rescan':0,
                    'interpolated_rows':0,'nontrain_sampling_or_model_use':False},
        artifacts=[pin(OUT/'ROWS.json')])
    write('RECEIPT.json', summary)
    with (OUT/'SHA256SUMS').open('x', encoding='utf-8') as stream:
        for name in ('SOURCE.py','ROWS.json','RECEIPT.json'):
            stream.write(pin(OUT/name)['sha256']+'  '+name+'\n')
    print(json.dumps({key:summary[key] for key in ('status','rows_processed','exact_11d_unique_geometry',
        'candidate_shared_width_equal','dataset_shared_width_equal','research_canonical9_unique','fingerprint_matched_rows',
        'qualification_group_counts','reported_response_range_count','reported_response_q10_20_intersection_count')}, sort_keys=True))


if __name__ == '__main__':
    main()
