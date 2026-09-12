"""Read-only first-member GDS diagnostics plus already-complete extraction join."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import gdstk
from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as geometry
from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity as identity
from rfic_transformer_inverse_design.layout import foundry_audit, port_ground_metrics

ROOT = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
W = ROOT / 'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
Q = ROOT / 'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1'
H = Q / 'historical_first_member_20260912T130430551686Z'
J = Q / 'historical_member_evidence_20260912T130927826798Z'
K = Q / 'historical_member_evidence_20260912T131003142608Z'
OUT = Path(__file__).parent
EVAL = 'd87ff4d971f5f26f'


def pin(path):
    assert not path.is_symlink()
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def checked_json(path, sha):
    assert pin(path)['sha256'] == sha
    return json.loads(path.read_text())


def write(name, value):
    with (OUT/name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def main():
    assert not (OUT/'RECEIPT.json').exists(), 'No-clobber member output'
    receipts = [(H, '1028bad626fe45dbf1bd9ed259c5a7b7b261685cb6226f81db04c4dc1c679816'),
                (J, 'fcf5c0aa664b1f0f4f2776fef48e1f494e9bf11f57c3fc49ec54467d3a716c0f'),
                (K, 'fe5c310b5735b505cf4dc8c79bc929047702dbe490c35664c9305a73def23f3a')]
    inputs = []
    documents = {}
    for folder, sha in receipts:
        receipt = checked_json(folder/'RECEIPT.json', sha)
        inputs.append(pin(folder/'RECEIPT.json'))
        for record in ([receipt['s4p']] if folder == H else receipt['files']):
            local = record['local_copy']
            path = Path(local['path'])
            assert path.is_relative_to(folder) and pin(path) == local
            inputs.append(local)
            if path.suffix == '.json':
                documents[str(path.relative_to(folder))] = json.loads(path.read_text())
    prior_path = W/'history_shard000_m26_v1/ROWS.json'
    prior = checked_json(prior_path, 'cf3ec4a52cf46f13f798106f41331ac8f439886c07e4aff43199bb2b575f0490')['rows'][0]
    assert prior['evaluation'] == EVAL
    inputs.append(pin(prior_path))
    target_path = OUT/'extraction111_v1/TARGET15.json'
    extraction_path = OUT/'extraction111_v1/RECEIPT.json'
    extraction = checked_json(extraction_path, '6a60363d164a683ab82381c86703ccf3a8edc57695d55af0e86fe269e8107a0e')
    assert pin(target_path) == extraction['target15']
    target = json.loads(target_path.read_text())
    inputs.extend([pin(extraction_path), pin(target_path)])
    summary = documents['summary_cadence_roundtrip.json']
    direct_summary = documents['summary.json']
    command = documents['emx/emx_command.json']
    layout = documents['layout/transformer_layout.layout.json']
    source_audit = documents['layout/foundry_layout_audit.json']
    source_geometry = documents['layout/geometry.json']
    assert summary['cache_key'] == direct_summary['cache_key'] == EVAL
    assert summary['command'] == command and summary['touchstone_path'] == prior['actual_artifact_pointers']['touchstone_path']
    assert command[1] == summary['cadence']['streamout_gds'] and command[2] == 'TRANSFORMER'
    assert command[command.index('-s')+1] == summary['touchstone_path']
    assert command[3] == layout['process_layer_summary']['process_file']
    expected_ports = ['--port=P%03d=P%03d:P%03d_G' % (i,i,i) for i in range(1,5)]
    assert [arg for arg in command if arg.startswith('--port=')] == expected_ports
    wanted = prior['mapped_10d_geometry']
    assert all(summary['geometry'][field] == direct_summary['geometry'][field] == wanted[field] for field in geometry.GEOMETRY_FIELDS)
    for winding in ('primary','secondary'):
        source_values = source_geometry[winding]['geometry']
        for short in ('outer_width_um','outer_height_um','terminal_y_span_um','feed_extension_um'):
            assert source_values[short] == wanted[winding+'_'+short]
        assert source_values['trace_width_um'] == wanted['line_width_um']
    assert source_geometry['offset_um'] == wanted['offset_um']
    assert geometry.canonical_geometry_sha256(wanted) == prior['canonical9_geometry_sha256']
    gds = K/'streamout/transformer_layout_cadpins.gds'
    gds_pin = pin(gds)
    structural = identity.gds_structural_identity(gds)
    library = gdstk.read_gds(str(gds))
    tops = library.top_level()
    assert len(tops) == 1 and tops[0].name == 'TRANSFORMER'
    top = tops[0]
    polygons = top.get_polygons(apply_repetitions=True, include_paths=True, depth=None)
    labels = top.get_labels(apply_repetitions=True, depth=None)
    grid = foundry_audit._actual_grid_audit(polygons, labels=labels, grid_um=0.005)
    # Only an explicitly conditional geometric reference is supplied. This is
    # not a forged full actual-foundry audit or any form of Calibre result.
    geometric_reference = dict(gds_sha256=gds_pin['sha256'], ground_frame=source_audit['ground_frame'])
    power = summary['geometry_check']['power_line_8port_geometry_audit']
    measured = port_ground_metrics.measure_port_ground_metrics(
        gds_path=gds, power_line_audit=power, foundry_audit=geometric_reference)
    gds_result = dict(schema='eucap15_historical_actual_gds_scoped_readonly.v1', gds=gds_pin,
        structural_identity=structural, actual_grid=grid,
        actual_labels=[dict(text=label.text, layer=label.layer, texttype=label.texttype,
                            xy_um=[float(v) for v in label.origin]) for label in labels],
        port_ground_measurement=measured,
        port_measurement_condition='USES_RECORDED_HISTORICAL_FRAME_AND_POWER_NOMINALS_NOT_FULL_CURRENT_FOUNDRY_QUALIFICATION',
        geometric_reference=geometric_reference,
        full_foundry_contract_audit='NOT_RUN_EXECUTED_PROCESS_BYTES_UNAVAILABLE',
        calibre_drc='NOT_RUN_IN_ORIGINAL_RECORDED_SOURCE',
        historical_foundry_drc_executed=source_audit['foundry_drc_executed'],
        direct_layout_gds_comparison='NOT_RUN_DIRECT_LAYOUT_GDS_NOT_RECEIVED',
        native_calls=0, source_artifacts_modified=False)
    write('ACTUAL_GDS_DIAGNOSTIC.json', gds_result)
    current = target['row']
    old = prior['historical_reported_em_response']
    pairs = [('Lp_nH','lp_nh'),('Ls_nH','ls_nh'),('Qmin','qmin'),('K_abs','k_abs'),('K_signed','signed_k')]
    differences = {old_key:dict(historical_csv=old[old_key], current_reextract=current[new_key],
                    signed_difference=current[new_key]-old[old_key]) for old_key,new_key in pairs}
    comparison = dict(schema='eucap15_historical_first_member_current_extraction_comparison.v1',
        evaluation=EVAL, evidence_class='HISTORICAL_S4P_REEXTRACTION_NOT_FRESH',
        source_s4p=extraction['source'], source_csv_row_pin=pin(prior_path), source_csv_row_index=prior['dataset_row_index'],
        current_definition=target, current_srf=extraction['summary'], differences=differences,
        signed_k_note='Recorded legacy signed K is negative; applied current secondary orientation [0,1,3,2] produces positive K. Do not use abs(K) agreement to claim physical port compatibility.',
        current_range_inside=.5 <= current['lp_nh'] <= 2 and .5 <= current['ls_nh'] <= 2 and .2 <= current['k_abs'] <= .85,
        q10_20_inside=10 <= current['qmin'] <= 20,
        numerical_agreement_proves_execution_identity=False, original_split='UNKNOWN', uses_for_training_or_sampling=False)
    write('CURRENT_VS_HISTORICAL.json', comparison)
    receipt = dict(schema='eucap15_historical_first_member_closure.v1', completed_utc=datetime.now(timezone.utc).isoformat(),
        evaluation=EVAL, candidate_id=prior['candidate_id'], scope='ONE_MEMBER_ONLY_NOT_THE_64_ROW_PARTITION',
        status='CURRENT_NUMERIC_STRICT_IN_RANGE_PHYSICAL_CERTIFICATION_PENDING',
        reuse_category='RAW_RESPONSE_READABLE_REEXTRACTED_WITH_MISSING_PHYSICAL_IDENTITY_OR_CERTIFICATION',
        source_split='UNKNOWN', inputs=inputs, command=sys.argv, cwd=str(Path.cwd()),
        current_definition_sources=extraction['current_definition_sources'],
        implementation=pin(Path(__file__)), reused_gds_sources=[pin(Path(module.__file__)) for module in (identity,foundry_audit,port_ground_metrics)],
        retained_frequency_points=111, exact_15ghz_index=20,
        descriptor_15ghz=current['broadband_descriptor_valid']=='true', strict_15ghz=current['strict_lumped_valid']=='true',
        below_half_srf_15ghz=current['below_half_srf']=='true', current_range_inside=comparison['current_range_inside'],
        q10_20_inside=comparison['q10_20_inside'], gds_structural_status=structural['overall_status'],
        gds_grid_status=grid['overall_status'], conditional_port_ground_status=measured['power_line_check'],
        current_formal_qualified='UNKNOWN', certified_added=0, formal_submissions=0,
        missing_evidence=['EXECUTED_PROCESS_CONTENT_SHA_AND_CURRENT_PROCESS_COMPATIBILITY',
            'CURRENT_CALIBRE_DRC_RUN_ON_THE_EXACT_GDS_AND_RULEDECK_BINDING',
            'DIRECT_LAYOUT_GDS_TO_CADENCE_IDENTITY_OR_EQUIVALENT_GENERATOR_BINDING',
            'COMPLETE_CURRENT_FOUNDRY_GEOMETRY_GATE_NOT_THE_CONDITIONAL_PORT_METRICS_ALONE',
            'CROSS_SOURCE_UNIQUE_INDEX_AND_FORMAL_SUBMISSION_READBACK','ORIGINAL_SPLIT_IDENTITY'],
        command_path_and_label_binding='MATCHED_EXISTING_SUMMARY_COMMAND_LAYOUT_AND_ACTUAL_PORT_LABELS_SUBJECT_TO_GDS_CHECKS',
        process_name_or_metric_agreement_not_identity=True,
        next_legal_entry='Reuse these exact S4P/current111 labels, then have sole native owner close historical process/generator identity and exact-GDS current DRC. If incompatible, retain trusted geometry for current-flow revalidation; never inherit the old physical labels automatically.',
        artifacts=[pin(OUT/'ACTUAL_GDS_DIAGNOSTIC.json'),pin(OUT/'CURRENT_VS_HISTORICAL.json')],
        actions=dict(new_emx=0,ssh=0,training=0,tests=0,old64_join_reexecuted=False,model_or_source_mutation=False))
    write('RECEIPT.json', receipt)
    for original in inputs:
        assert pin(Path(original['path'])) == original, 'Source changed during diagnostic'
    with (OUT/'SHA256SUMS').open('x') as stream:
        for name in ('ANALYZE_MEMBER.py','ACTUAL_GDS_DIAGNOSTIC.json','CURRENT_VS_HISTORICAL.json','RECEIPT.json'):
            stream.write(pin(OUT/name)['sha256']+'  '+name+'\n')
    print(json.dumps({key:receipt[key] for key in ('status','descriptor_15ghz','strict_15ghz','below_half_srf_15ghz',
        'current_range_inside','gds_structural_status','gds_grid_status','conditional_port_ground_status','current_formal_qualified')},sort_keys=True))


if __name__ == '__main__':
    main()
