"""Supplement only two newly received artifacts; reuse prior extraction/GDS results."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
V = ROOT/'github_worktrees/eucap15-mlp-capacity-20260909'
W = ROOT/'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
Q = ROOT/'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1'
IN = Q/'strict112_two_20260912T144123255352Z'
OUT = Path(__file__).parent
sys.path.insert(0, str(V))
from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity as identity


def pin(path):
    path = Path(path).absolute()
    assert not any(p.is_symlink() for p in (path,*path.parents))
    before=path.stat(); raw=path.read_bytes(); after=path.stat()
    assert (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def load(path, sha=None):
    record=pin(path)
    if sha: assert record['sha256']==sha
    return json.loads(Path(path).read_text()),record


transport,transport_pin=load(IN/'RECEIPT.json','8863ebf0a9c571c53d3def5a55263695c058c4eb4c56c99ed32d352c8b41ba70')
inputs=[transport_pin]
files={r['role']:r for r in transport['files']}
assert set(files)=={'selected_actual_drc_gds','selected_geometry_audit'}
for record in files.values():
    assert record['status']=='MATCH' and pin(record['local']['path'])==record['local']
    assert record['local']['sha256']==record['source']['sha256']
    inputs.append(record['local'])
old_receipt,old_receipt_pin=load(OUT/'RECEIPT.json','51f6840950aa425592ac24f13f35aed9d86af0890ed3925ad141411e9fef90c0')
artifact_pins={Path(x['path']).name:x for x in old_receipt['artifacts']}
old_gds,old_gds_pin=load(OUT/'ACTUAL_GDS_BINDING.json',artifact_pins['ACTUAL_GDS_BINDING.json']['sha256'])
member,member_pin=load(OUT/'MEMBER_DISPOSITION.json',artifact_pins['MEMBER_DISPOSITION.json']['sha256'])
inputs.extend([old_receipt_pin,old_gds_pin,member_pin])
audit,audit_pin=load(files['selected_geometry_audit']['local']['path'])
assert audit_pin==files['selected_geometry_audit']['local']
drc_gds=Path(files['selected_actual_drc_gds']['local']['path'])
normalized=identity.gds_timestamp_normalized_sha256(drc_gds)
checks={
    'candidate_exact':audit['candidate_id_sha256']==transport['candidate_id']==member['candidate_id_sha256'],
    'original_geometry_hash_exact':audit['candidate_geometry_identity_sha256']==member['candidate_geometry_identity_sha256'],
    'actual_drc_gds_raw_sha_exact':audit['gds_sha256']==pin(drc_gds)['sha256'],
    'original_drc_gds_path_exact':audit['gds_path']==files['selected_actual_drc_gds']['source']['path'],
    'actual_drc_normalized_equals_audit':normalized==audit['gds_timestamp_normalized_sha256'],
    'actual_drc_normalized_equals_prior_actual_emx':normalized==old_gds['normalized_actual_sha256'],
    'normalization_algorithm_exact':audit['gds_timestamp_normalization_algorithm']==old_gds['normalized_algorithm'],
    'ten_geometry_field_set_exact':set(audit['geometry_um'])==set(member['source_geometry_fields']),
    'ten_source_geometry_values_exact':audit['geometry_um']==member['source_geometry_um'],
    'panel_index_equals_original_source_ordinal':audit['panel_index']==member['source_original_ordinal'],
}
assert all(checks.values()),checks
result=dict(schema='eucap15_strict112_actual_identity_supplement.v1',utc=datetime.now(timezone.utc).isoformat(),
    candidate_id_sha256=member['candidate_id_sha256'],status='NEW_TWO_ARTIFACT_IDENTITY_BINDING_CLOSED_NOT_ELIGIBILITY',
    inputs=inputs,checks=checks,actual_drc_gds_normalized_sha256=normalized,implementation=pin(__file__),
    normalized_helper=pin(identity.__file__),argv=sys.argv,
    source_geometry_encoding='Source CSV geom__ float strings decoded to finite floats equal original audit geometry_um JSON floats in all10 fields; original field values retained without grid quantization.',
    original_candidate_geometry_identity_sha256=audit['candidate_geometry_identity_sha256'],
    current_canonical9_geometry_sha256=member['source_geometry_canonical9_sha256'],
    geometry_hash_encoding_compatibility='ORIGINAL_HASH_AGREES_ACROSS_SOURCE_RECORDS_BUT_SERIALIZATION_NOT_PROVEN_EQUAL_TO_CURRENT9DP',
    raw_emx_and_drc_sha_differ=True,
    byte_normalization_interpretation='Only BGNLIB/BGNSTR timestamps are normalized; actual DRC GDS now matches prior actual EMX normalized identity. No GDS geometry or labels were repaired.',
    resolved=['ACTUAL_DRC_GDS_BYTES_TO_PRIOR_EMX_NORMALIZED_IDENTITY','ACTUAL_GEOMETRY_AUDIT_CANDIDATE_GEOMETRY_AND_DRC_GDS_BINDING','TEN_SOURCE_GEOMETRY_FIELD_VALUES_MATCH_ORIGINAL_AUDIT'],
    still_unresolved=['EXECUTED_PROCESS_BYTES_AND_CURRENT_COMPATIBILITY','FULL_BOUND_CURRENT_PHYSICAL_PORT_MAPPING','ORIGINAL_HASH_SERIALIZATION_DEFINITION','ORIGINAL_SPLIT_AND_CROSS_SOURCE_UNIQUENESS'],
    retained_negative=dict(conditional_mapping='CONDITIONAL_ON_CURRENT_PORT_MAPPING_NOT_PHYSICAL_QUALIFICATION',
        strict_valid=False,strict_failure_reasons=member['strict_failure_reasons'],current15_core_range=False,
        outside_range_fields=member['outside_range_fields'],q10_20_intersection=False,q_intersection_is_not_full100k_gate=True,
        gds_grid=old_gds['actual_grid']),
    original_geometry_stage_foundry_drc_executed=audit['foundry_drc_executed'],
    geometry_stage_false_does_not_erase_later_calibre_record=True,
    historical_geometry_audit_pass_does_not_override_current_label_grid_failure=True,
    benchmark_arm=member['benchmark_arm'],pair_id_sha256=member['pair_id_sha256'],original_split='UNKNOWN',
    new_s4p_extractions=0,old_emx_gds_grid_reexecuted=False,new_drc_gds_normalizations=1,
    source_artifacts_modified=False,fresh_emx=0,formal_added=0,
    next_action='No further evidence request for this rejected member; retain its benchmark reservation and all original negatives.')
for item in inputs: assert pin(item['path'])==item
with (OUT/'IDENTITY_SUPPLEMENT_M32.json').open('x',encoding='utf-8') as stream:
    json.dump(result,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write('\n')
with (OUT/'IDENTITY_SUPPLEMENT_M32_SHA256SUMS').open('x') as stream:
    for name in ('SUPPLEMENT_IDENTITY_M32.py','IDENTITY_SUPPLEMENT_M32.json'):
        stream.write(pin(OUT/name)['sha256']+'  '+name+'\n')
print(json.dumps(dict(checks=checks,artifact=pin(OUT/'IDENTITY_SUPPLEMENT_M32.json'))))
