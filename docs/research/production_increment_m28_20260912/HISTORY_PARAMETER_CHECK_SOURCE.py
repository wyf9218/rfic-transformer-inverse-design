"""M28 bounded companion: current pure parameter checks of 63 mapped members.

No source join, S-parameter extraction, GDS read, simulator, or ledger operation.
Outputs are deliberately not a dispatch/accepted/training manifest.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

import yaml
from rfic_transformer_inverse_design.core.adapter import TransformerOptimizationAdapter
from rfic_transformer_inverse_design.core.bounds import InductorBounds, TransformerSearchSpace

ROOT = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
REPO = ROOT / 'github_worktrees/eucap15-mlp-capacity-20260909'
W = ROOT / 'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
OUT = Path(__file__).resolve().parent
CONFIG = ROOT / 'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1/historical_shard000_files_20260912T125517138418Z/mars_current_foundry_s4p_5_60_0p5_drc_margin_v2.yaml'
INPUTS = {
    'joined_rows': (W / 'history_shard000_m26_v1/ROWS.json', 'cf3ec4a52cf46f13f798106f41331ac8f439886c07e4aff43199bb2b575f0490'),
    'join_receipt': (W / 'history_shard000_m26_v1/RECEIPT.json', '65d4f7c823550965917fb54f7119b69470a74ee02261625483c788a6287918ec'),
    'source_config': (CONFIG, '996ebad95e407b5f959b3807e1a2b90fad5599c54a7b6745fcd24ce82c4708e0'),
    'first_final_disposition': (W / 'history_first_member_m27_v1/QUALIFICATION_DISPOSITION.json', 'ea378a5199d3d5f8451c1ea08c0bd9444966eb5832045faad2bc30b434f8cb3a'),
}
CATEGORIES = {
    'EVIDENCE_SUFFICIENT_FOR_PHYSICAL_REUSE_REEXTRACTION': 'Actual artifacts plus compatible physical identity established; no member is presumed to meet this.',
    'RAW_RESPONSE_READABLE_PHYSICAL_EVIDENCE_GAP_OR_NO_GO': 'Actual historical response readable; identity/physical gaps or a confirmed gate failure prevent direct label inheritance.',
    'TRUSTED_PARAMETER_GEOMETRY_ONLY_CURRENT_REVALIDATION': 'Identity-bound parameters pass the scoped current pure check; actual response/artifact compatibility is not established.',
    'UNRESOLVED_OR_PARAMETER_GATE_FAIL_ISOLATED': 'Identity or parameter checking did not close; retain the original failure and do not dispatch.',
}


def pin(path):
    body = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}


def emit(name, data):
    with (OUT / name).open('x', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write('\n')


def main():
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    # SOURCE.py is created once using apply_patch; all result files must be new.
    if any(path.name != 'SOURCE.py' for path in OUT.iterdir()):
        raise FileExistsError('Output is no-clobber and must contain only SOURCE.py before its single run')
    pins = {key: pin(path) for key, (path, expected) in INPUTS.items()}
    for key, (_, expected) in INPUTS.items():
        if pins[key]['sha256'] != expected:
            raise ValueError(f'Input identity mismatch: {key}')
    joined = json.loads(INPUTS['joined_rows'][0].read_text())
    prior = json.loads(INPUTS['join_receipt'][0].read_text())
    first = json.loads(INPUTS['first_final_disposition'][0].read_text())
    raw = yaml.safe_load(CONFIG.read_text())
    assert prior['rows_processed'] == 64 and len(joined['rows']) == 64
    assert first['evaluation'] == 'd87ff4d971f5f26f'
    assert first['decision'] == 'NO_GO_DIRECT_ADMISSION_OF_ORIGINAL_GDS_UNDER_CURRENT_GATE'
    assert raw['target']['topology_mode'] == '1t1t'

    # Only declared geometric bounds/topology are passed to the pure adapter.
    # Intentionally do not call load_run_config: it may try reading a proc file.
    # VDD/shield/grid/ports/ruledecks are outside this scoped parameter check.
    def winding(name):
        bounds, topo = raw['bounds'][name], raw['topology'][name]
        assert topo['turns'] == 1 and topo['center_tap'] is True
        return InductorBounds(**{key: tuple(bounds[key]) for key in (
            'outer_width_um', 'outer_height_um', 'trace_width_um', 'spacing_um',
            'terminal_y_span_um', 'feed_extension_um')}, turns=1, center_tap=True)

    adapter = TransformerOptimizationAdapter(TransformerSearchSpace(
        primary=winding('primary'), secondary=winding('secondary'),
        offset_um=tuple(raw['bounds']['offset_um']), topology_mode='1t1t'))
    output_rows, queue, checks = [], [], []
    for row in joined['rows']:
        first_member = row['evaluation'] == first['evaluation']
        assert row['source_split'] == 'UNKNOWN'
        base = {key: row[key] for key in (
            'dataset_row_index', 'candidate_row_index', 'evaluation', 'candidate_id',
            'source_candidate_id', 'queue_row_index', 'selection_source', 'source_split',
            'mapped_10d_fields', 'mapped_10d_geometry', 'canonical9_geometry_sha256',
            'original_fingerprint')}
        base['source_binding'] = {'joined_rows': pins['joined_rows'],
            'candidate_source': prior['inputs']['candidate_source'],
            'dataset_source': prior['inputs']['dataset_rows_source']}
        if first_member:
            check = {'status': 'REUSED_M27_NOT_RERUN', 'reference': pins['first_final_disposition'],
                'geometry_reuse': first['geometry_reuse'],
                'current_pure_parameter_check_this_action': False}
            category = 'RAW_RESPONSE_READABLE_PHYSICAL_EVIDENCE_GAP_OR_NO_GO'
            gaps = list(first['uncertainties_remaining'])
            blockers = first['confirmed_current_gate_failures']
            artifact_status = 'FIRST_MEMBER_ACTUAL_ARTIFACTS_ALREADY_READ_IN_M27'
            queue_eligible = True
        else:
            errors, bounds_errors, geometry_errors = [], [], []
            values = row['mapped_10d_geometry']
            try:
                if not (row['candidate_widths_exactly_equal'] and row['dataset_widths_exactly_equal']):
                    raise ValueError('Prior explicit width equality is not established')
                if row['geometry_join_mismatches']:
                    raise ValueError('Prior geometry binding contains conflicts')
                if set(values) != set(row['mapped_10d_fields']) or not all(math.isfinite(float(v)) for v in values.values()):
                    raise ValueError('Mapped geometry fields or finite values invalid')
                expanded = {**values, 'primary_width_um': values['line_width_um'],
                    'secondary_width_um': values['line_width_um']}
                spec = adapter.from_vector([expanded[field] for field in adapter.field_order()])
                flat = spec.flat_dict()
                if any(float(flat[field]) != float(expanded[field]) for field in adapter.field_order()):
                    raise ValueError('Parser changed an explicit geometric parameter')
                bounds_errors = adapter.search_space.validate(spec)
                geometry_errors = spec.validate()
                errors = bounds_errors + geometry_errors
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
            check = {'status': 'FAIL' if errors else 'PASS', 'parser_errors_and_geometry_errors': errors,
                'bounds_errors': bounds_errors, 'geometry_errors': geometry_errors,
                'current_pure_parameter_check_this_action': True,
                'field_order_11d': list(adapter.field_order()),
                'mapping': 'EXPLICIT_SHARED_WIDTH_TO_EQUAL_PRIMARY_AND_SECONDARY_WIDTH_NO_VALUE_CHANGE',
                'scope': 'CURRENT_PURE_ADAPTER_AND_SPEC_VALIDATORS_WITH_PINNED_SOURCE_GEOMETRIC_BOUNDS',
                'actual_gds_grid_port_drc_process_validation': 'NOT_PERFORMED'}
            checks.append({'evaluation': row['evaluation'], 'candidate_id': row['candidate_id'], **check})
            category = ('UNRESOLVED_OR_PARAMETER_GATE_FAIL_ISOLATED' if errors else
                'TRUSTED_PARAMETER_GEOMETRY_ONLY_CURRENT_REVALIDATION')
            gaps, blockers = list(row['missing_evidence']), {}
            artifact_status = 'HISTORICAL_POINTERS_ONLY_ACTUAL_READABILITY_NOT_CHECKED'
            queue_eligible = not errors
        base.update({'reuse_category': category, 'parameter_check': check,
            'missing_evidence': gaps, 'confirmed_gate_failures_this_member_only': blockers,
            'actual_artifact_status': artifact_status,
            'historical_artifact_pointers_not_proof_of_readability': row['actual_artifact_pointers'],
            'old_physical_labels_inheritance': 'BLOCKED',
            'current_physical_qualification': ('NO_GO_ORIGINAL_GDS' if first_member else 'UNKNOWN'),
            'formal_submitted': False, 'assigned_new_training_split': None})
        output_rows.append(base)
        if queue_eligible:
            queue.append({**{key: base[key] for key in (
                'dataset_row_index', 'candidate_row_index', 'evaluation', 'candidate_id',
                'source_candidate_id', 'queue_row_index', 'selection_source', 'source_split',
                'mapped_10d_fields', 'mapped_10d_geometry', 'canonical9_geometry_sha256',
                'original_fingerprint', 'source_binding')},
                'geometry_units': 'um', 'source_kind': 'HISTORICAL_TRUSTED_PARAMETER_REVALIDATION',
                'status': 'NOT_DISPATCHED_NOT_RESOURCE_ADMITTED',
                'source_geometry_changed': False, 'old_physical_labels_inheritance': 'BLOCKED',
                'prior_current_gds_gate': 'NO_GO' if first_member else 'NOT_CHECKED',
                'eligibility_basis': check,
                'requires_before_submission': [
                    'CURRENT_OWNER_RESOURCE_AND_BUDGET_ADMISSION',
                    'CURRENT_VERSION_GDS_DRC_PORT_PROC_AND_GEOMETRY_BINDING',
                    'SWEEPT_S4P_AND_CURRENT_15GHZ_STRICT_SRF_RANGE_EXTRACTION',
                    'CROSS_SOURCE_UNIQUE_CHECK_AND_FORMAL_SUBMISSION_READBACK'],
                'old_targets_predictions_and_labels_not_included_or_used': True})

    assert len(checks) == 63 and len(output_rows) == 64
    assert len({row['evaluation'] for row in output_rows}) == 64
    assert len({row['canonical9_geometry_sha256'] for row in queue}) == len(queue)
    counts = collections.Counter(row['reuse_category'] for row in output_rows)
    categories = {key: counts[key] for key in CATEGORIES}
    assert sum(categories.values()) == 64
    emit('PARAMETER_CHECKS_63.json', {'schema': 'eucap15_scoped_parameter_checks.v1', 'rows': checks})
    emit('ROWS.json', {'schema': 'eucap15_historical_reuse_classification_m28.v1',
        'category_definitions': CATEGORIES, 'rows': output_rows})
    emit('GEOMETRY_REVALIDATION_CANDIDATES.json', {
        'schema': 'eucap15_historical_parameter_revalidation_candidates.v1',
        'status': 'UNASSIGNED_UNBUDGETED_NOT_DISPATCHED_NOT_TRAINING',
        'scope': 'THIS_SHARD_ONLY_NO_PHYSICAL_LABEL_INHERITANCE',
        'ordering': 'ORIGINAL_DATASET_ORDER_NO_TARGET_OR_PROXY_RANKING',
        'targets': 'NONE_NO_TARGETS_SYNTHESIZED', 'source_split': 'UNKNOWN_PRESERVED',
        'original_111point_or_other_physical_labels_used': False,
        'request_does_not_grant_native_execution': True, 'rows': queue})
    implementation = [pin(REPO / 'rfic_transformer_inverse_design/core' / name)
        for name in ('adapter.py', 'bounds.py', 'topology.py', 'types.py')]
    # Ensure inputs stayed fixed throughout this bounded local action.
    for key, (path, expected) in INPUTS.items():
        assert pin(path)['sha256'] == expected, key
    receipt = {
        'schema': 'eucap15_historical_reuse_m28_receipt.v1', 'status': 'COMPLETE_SCOPED_PARAMETER_CLASSIFICATION',
        'started_utc': started, 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': time.monotonic() - tick, 'inputs': pins,
        'source': pin(Path(__file__).resolve()), 'reused_implementation': implementation,
        'argv': [sys.executable, '-B', *sys.argv], 'cwd': str(Path.cwd()),
        'pythonpath': str(REPO), 'category_counts': categories,
        'new_parameter_checks': len(checks), 'new_parameter_pass': sum(r['status'] == 'PASS' for r in checks),
        'new_parameter_fail': sum(r['status'] == 'FAIL' for r in checks),
        'first_member_disposition_reused_not_recomputed': 1,
        'trusted_geometry_candidate_queue_count': len(queue), 'candidate_queue_dispatched': 0,
        'source_split_unknown': 64, 'formal_submissions_this_action': 0,
        'current_qualified_unique_count': None,
        'current_qualified_unique_count_status': 'NOT_CERTIFIED_NOT_ZERO_ESTIMATE',
        'common_blocker': 'Source-level executed proc/current physical compatibility and current candidate-bound Calibre/DRC evidence are absent; old physical labels cannot be inherited.',
        'no_common_missing_evidence_read_requests_repeated': True,
        'parameter_scope_excludes': ['ACTUAL_GRID', 'POWER_LINE_ENDPOINT_AND_GROUND_OVERLAP', 'ACTUAL_GDS',
            'PORTS', 'VIA', 'FOUNDRY_DRC', 'EXECUTED_PROCESS_BYTES', 'CURRENT_PHYSICAL_LABELS'],
        'old_q_target_mode_max_not_applied': True,
        'no_old_join_repeated': True, 'no_s4p_extraction': True, 'no_gds_reads': True,
        'no_ssh_native_training_ledger_operations': True,
        'artifacts': {name: pin(OUT / name) for name in (
            'PARAMETER_CHECKS_63.json', 'ROWS.json', 'GEOMETRY_REVALIDATION_CANDIDATES.json')},
    }
    emit('RECEIPT.json', receipt)
    with (OUT / 'SHA256SUMS').open('x', encoding='utf-8') as handle:
        for name in ('SOURCE.py', 'PARAMETER_CHECKS_63.json', 'ROWS.json',
                'GEOMETRY_REVALIDATION_CANDIDATES.json', 'RECEIPT.json'):
            handle.write(f"{pin(OUT / name)['sha256']}  {name}\n")
    print(json.dumps({key: receipt[key] for key in ('category_counts', 'new_parameter_checks',
        'new_parameter_pass', 'new_parameter_fail', 'trusted_geometry_candidate_queue_count')}))
    print(json.dumps({'receipt': pin(OUT / 'RECEIPT.json'), 'sha256sums': pin(OUT / 'SHA256SUMS')}))


if __name__ == '__main__':
    main()
