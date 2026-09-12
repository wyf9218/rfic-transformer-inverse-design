"""One received strict112 member: actual GDS binding and existing111 result join."""
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
V = ROOT / 'github_worktrees/eucap15-mlp-capacity-20260909'
W = ROOT / 'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
Q = ROOT / 'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1'
IN = Q / 'strict112_member_20260912T143339435987Z'
OUT = Path(__file__).parent
sys.path.insert(0, str(V))
import gdstk
from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity as identity
from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as geometry
from rfic_transformer_inverse_design.layout import foundry_audit


def pin(path):
    path = Path(path).absolute()
    assert not any(p.is_symlink() for p in (path, *path.parents)), 'Nonsymlink input required'
    before = path.stat(); raw = path.read_bytes(); after = path.stat()
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def load(path, sha=None):
    item = pin(path)
    if sha: assert item['sha256'] == sha, str(path)
    return json.loads(Path(path).read_text()), item


def write(name, obj):
    with (OUT / name).open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, indent=2, ensure_ascii=False, allow_nan=False); stream.write('\n')


def projection(record):
    row = record['selected_row']
    assert len(row['header']) == len(row['values']) == len(row['fields'])
    assert dict(zip(row['header'], row['values'])) == row['fields']
    assert record['original_split'] == 'UNKNOWN'
    return row['fields']


def main():
    assert not (OUT / 'RECEIPT.json').exists(), 'No-clobber verification'
    transport, transport_pin = load(IN / 'RECEIPT.json', 'fc0c5a8c3d6241260453b585d6b05700c2adae4bc07beb066d14b58799cef1c6')
    inputs = [transport_pin]
    documents = {}
    records = {}
    for item in transport['files']:
        assert item['status'] == 'MATCH' and item['selection_error'] is None
        path = Path(item['local']['path'])
        assert path.is_relative_to(IN) and pin(path) == item['local']
        records[item['role']] = item
        inputs.append(item['local'])
        if path.suffix == '.json': documents[item['role']] = json.loads(path.read_text())
    selected_doc, selected_pin = load(W / 'history_strict112_actual_index_m31_v1/SELECTED_MEMBER.json', 'b7f393ba2ab6f72757b32ea92c3febac75c9f38559ef808d88a9eaa74ea4fd56')
    selected = selected_doc['selected']; authority = selected['original_authority_metadata']
    inputs.append(selected_pin)
    candidate = selected['candidate_id_sha256']
    assert candidate == transport['selected_candidate']
    data_doc = documents['source_csv_for_selected_10d_geometry']
    gds_doc = documents['gds_index_for_selected_actual_geometry_binding']
    data, index = projection(data_doc), projection(gds_doc)
    drc = documents['selected_drc_summary']
    assert data_doc['source'] == records['source_csv_for_selected_10d_geometry']['source']
    assert gds_doc['source'] == records['gds_index_for_selected_actual_geometry_binding']['source']
    assert data['evaluation'] == 'bc3355cc893399aa'
    assert data['touchstone_path'] == authority['touchstone_path'] == records['selected_historical_s4p']['source']['path']
    assert data_doc['selection_filter'] == {'touchstone_path': data['touchstone_path']}
    assert gds_doc['selection_filter'] == {'candidate_id_sha256': candidate}
    for field in ('candidate_id_sha256', 'candidate_geometry_identity_sha256'):
        assert index[field] == drc[field] == authority[field]
    for field in ('gds_path', 'gds_sha256', 'gds_timestamp_normalized_sha256', 'gds_timestamp_normalization_algorithm', 'geometry_audit_path', 'geometry_audit_sha256'):
        assert index[field] == drc[field], field
    assert drc['gds_sha256'] == authority['drc_gds_sha256']
    assert records['selected_emx_input_gds']['source']['path'] == authority['gds_path']
    assert records['selected_emx_input_gds']['source']['sha256'] == authority['gds_sha256']
    assert records['selected_drc_summary']['source']['sha256'] == authority['drc_summary_sha256']
    mapped = {field: float(data['geom__' + field]) for field in geometry.GEOMETRY_FIELDS}
    assert all(math.isfinite(v) for v in mapped.values())
    assert float(data['geom__primary_width_um']) == float(data['geom__secondary_width_um']) == mapped['line_width_um']
    canonical9 = geometry.canonical_geometry_sha256(mapped)
    # Restore only the original basename in a byte-identical derived evidence copy:
    # the existing structural audit uses that basename to apply Cadence pin checks.
    original_gds = Path(records['selected_emx_input_gds']['local']['path'])
    (OUT / 'actual_gds').mkdir(exist_ok=False)
    gds = OUT / 'actual_gds' / Path(authority['gds_path']).name
    with gds.open('xb') as stream: stream.write(original_gds.read_bytes())
    assert pin(gds)['sha256'] == pin(original_gds)['sha256']
    normalized = identity.gds_timestamp_normalized_sha256(gds)
    structural = identity.gds_structural_identity(gds)
    lib = gdstk.read_gds(str(gds)); tops = lib.top_level()
    assert len(tops) == 1 and tops[0].name == 'TRANSFORMER'
    top = tops[0]
    polygons = top.get_polygons(apply_repetitions=True, include_paths=True, depth=None)
    labels = top.get_labels(apply_repetitions=True, depth=None)
    grid = foundry_audit._actual_grid_audit(polygons, labels=labels, grid_um=0.005)
    gds_results = dict(actual_emx_gds=pin(original_gds), original_basename_restored_copy=pin(gds),
        raw_emx_gds_equals_recorded_drc_gds=pin(gds)['sha256'] == drc['gds_sha256'],
        normalized_actual_sha256=normalized, normalized_algorithm=index['gds_timestamp_normalization_algorithm'],
        actual_emx_normalized_equals_index_and_drc=normalized == index['gds_timestamp_normalized_sha256'] == authority['gds_timestamp_normalized_sha256'],
        actual_drc_gds_byte_verification='NOT_RECEIVED', structural_identity=structural, actual_grid=grid,
        actual_labels=[dict(text=x.text, layer=x.layer, texttype=x.texttype, xy_um=[float(v) for v in x.origin]) for x in labels],
        historical_calibre=dict(status=drc['overall_status'], generated_utc=drc['generated_utc'],
            scope=drc['drc_scope'], blocking=drc['blocking_drc_violation_count'], documented_warnings=drc['documented_warning_rules'],
            source_deck_sha256=drc['drc_source_rule_deck_sha256']),
        actual_current_port_ground_audit='NOT_RUN_MISSING_BOUND_FULL_POWER_FRAME_EVIDENCE',
        complete_current_process_and_gds_compatibility='UNKNOWN', simulator_calls=0)
    write('ACTUAL_GDS_BINDING.json', gds_results)
    extracted, extraction_pin = load(OUT / 'extraction111_v1/RECEIPT.json', '2bd99acd22e08b698be20b8ab37543111d9208d7cefc3fe4583267966f439066')
    target, target_pin = load(OUT / 'extraction111_v1/TARGET15.json')
    assert target_pin == extracted['target15']
    assert extracted['source'] == records['selected_historical_s4p']['local']
    inputs.extend([extraction_pin, target_pin])
    row = target['row']
    core = .5 <= row['lp_nh'] <= 2 and .5 <= row['ls_nh'] <= 2 and .2 <= row['k_abs'] <= .85
    differences = {name:dict(historical_csv=float(data[old]), current_reextraction=row[new], signed_difference=row[new]-float(data[old]))
        for name,old,new in [('Lp_nH','lp_nh_center','lp_nh'),('Ls_nH','ls_nh_center','ls_nh'),('Qp','qp_center','qp'),('Qs','qs_center','qs'),('K_signed','k_center','signed_k')]}
    result = dict(candidate_id_sha256=candidate, candidate_geometry_identity_sha256=index['candidate_geometry_identity_sha256'],
        benchmark_arm=selected['benchmark_arm'], pair_id_sha256=selected['pair_id_sha256'],
        authority_original_ordinal=selected['authority_ordinal_zero_based'],
        source_original_ordinal=data_doc['selected_row']['original_ordinal_zero_based'],
        gds_index_original_ordinal=gds_doc['selected_row']['original_ordinal_zero_based'], original_split='UNKNOWN',
        source_geometry_fields=list(geometry.GEOMETRY_FIELDS), source_geometry_um=mapped,
        source_geometry_canonical9_sha256=canonical9,
        canonical9_equals_historical_candidate_geometry_hash=canonical9 == index['candidate_geometry_identity_sha256'],
        hash_namespaces_not_assumed_equal=True, actual_geometry_audit='NOT_RECEIVED',
        exact_15ghz_original_index=20, retained_frequencies=111, interpolation=False,
        descriptor_valid=row['broadband_descriptor_valid']=='true', strict_valid=target['current_definition_strict_lumped_valid'],
        strict_failure_reasons=target['strict_failure_reasons'], current15_core_range=core,
        outside_range_fields=[name for name,lo,hi in [('lp_nh',.5,2),('ls_nh',.5,2),('k_abs',.2,.85)] if not lo <= row[name] <= hi],
        q10_20_intersection=10 <= row['qmin'] <= 20, target15=target, srf=extracted['summary'], historical_comparison=differences,
        old_objective_not_reused=True, signed_k_orientation_note='Current [0,1,3,2] secondary orientation changes signed K; absolute agreement does not prove executed port identity.',
        status='INELIGIBLE_CURRENT15_STRICT_AND_RANGE', formal_submissions=0, current100k_added=0,
        use_for_training_or_sampling=False, preserve_original_evaluation_reservation=True)
    assert not result['strict_valid'] and not core
    write('MEMBER_DISPOSITION.json', result)
    receipt = dict(schema='eucap15_strict112_single_actual_member.v1', completed_utc=datetime.now(timezone.utc).isoformat(),
        status=result['status'], candidate_id_sha256=candidate, scope='ONLY_ONE_MEMBER_NOT_OTHER111',
        inputs=inputs, implementation=pin(__file__), reused_sources=[pin(x.__file__) for x in (identity, geometry, foundry_audit)],
        argv=sys.argv, cwd=str(Path.cwd()), benchmark_arm=result['benchmark_arm'], pair_id_sha256=result['pair_id_sha256'],
        extraction_invocations_new=1, existing_extraction_reexecuted=False, gds_actual_read=True, formal_submissions=0,
        geometry_namespace_binding='MATCH' if canonical9==index['candidate_geometry_identity_sha256'] else 'DIFFERENT_NAMESPACE_NOT_SUBSTITUTED',
        current15_eligible=False, current100k_added=0, fresh_emx=0, ssh=0, training=0, tests=0,
        next_legal_action='Preserve the physical negative and benchmark reservation. Any received exact DRC GDS/geometry audit may close identity only, never reverse the half-SRF/range failure or add this member to100K.',
        unresolved_identity=['EXECUTED_PROCESS_CONTENT_SHA_AND_CURRENT_COMPATIBILITY','FULL_BOUND_CURRENT_PORT_MAPPING_AND_POWER_FRAME','ACTUAL_DRC_GDS_AND_GEOMETRY_AUDIT_BYTES','ORIGINAL_SPLIT_AND_CROSS_SOURCE_UNIQUENESS'],
        artifacts=[pin(OUT/name) for name in ('ACTUAL_GDS_BINDING.json','MEMBER_DISPOSITION.json')])
    for item in inputs: assert pin(item['path']) == item, 'Input changed'
    write('RECEIPT.json', receipt)
    with (OUT / 'SHA256SUMS').open('x') as stream:
        for name in ('VERIFY_MEMBER.py','ACTUAL_GDS_BINDING.json','MEMBER_DISPOSITION.json','RECEIPT.json','actual_gds/transformer_layout_cadpins.gds'):
            stream.write(pin(OUT/name)['sha256']+'  '+name+'\n')
    print(json.dumps(dict(status=result['status'], normalized_match=gds_results['actual_emx_normalized_equals_index_and_drc'],
        structural=structural['overall_status'], grid=grid, canonical9=canonical9,
        namespace_match=result['canonical9_equals_historical_candidate_geometry_hash'], receipt=pin(OUT/'RECEIPT.json'))))


if __name__ == '__main__': main()
