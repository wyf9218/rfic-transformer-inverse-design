"""Exact frozen64 metadata adapter. No model, filesystem writes or native calls."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

MANIFEST_SHA = '007f0c42e597089029111cbb005e4679fc5dad55efa735a94fe7baaba916de46'
INTENT_SHA = 'ea8cb05f228215932a47ee6620267ca299818e8ea76299f9862f920b362c2abb'
PROPOSALS_SHA = '17de43afa74182674d6391c3b84bc8dca729d6c3f6d1b711e8c5728fab642fa3'
PREPARATION_SHA = 'fa2caeb03fb99b4e2fb038646310f91f8948c0fa44f90020e29d719122e7f926'
STUDY = 'eucap15_controlled_budget_20260910_v1'
MODEL = 'dev15-current6329-3x256-seed17-8106f2f8d027'
ARMS = ('COVERAGE_DIRECTED', 'GEOMETRY_DOE_CONTROL')
GEOMETRY_FIELDS = ('primary_outer_width_um', 'primary_outer_height_um',
    'secondary_outer_width_um', 'secondary_outer_height_um', 'line_width_um',
    'primary_terminal_y_span_um', 'secondary_terminal_y_span_um', 'offset_um',
    'primary_feed_extension_um', 'secondary_feed_extension_um')


class MetadataError(ValueError):
    pass


def require(value, message):
    if not value:
        raise MetadataError(message)


def parse(raw):
    def pairs(items):
        d = {}
        for key, value in items:
            require(key not in d, 'DUPLICATE_JSON_KEY')
            d[key] = value
        return d
    def nonfinite(value):
        raise MetadataError('NONFINITE_JSON: '+value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def read_pin(pin, path_map=None):
    require(set(pin) == {'path','sha256','bytes'}, 'EXACT_PIN_FIELDS_REQUIRED')
    path_map = path_map or {}
    p = Path(path_map.get(pin['path'], pin['path']))
    require(p.is_absolute() and '..' not in p.parts, 'ABSOLUTE_NO_TRAVERSAL_REQUIRED')
    require(all(not x.is_symlink() for x in (p,*p.parents)), 'SYMLINK_NOT_ALLOWED')
    raw = p.read_bytes()
    require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'],
            'SOURCE_PIN_MISMATCH: '+pin['path'])
    return raw


def validate_contract(intent, rows, preparation):
    require(intent['schema']=='eucap15_controlled_acquisition_intent.v1' and
            intent['study_id']==STUDY and intent['model_id']==MODEL, 'STUDY_MODEL_MISMATCH')
    require(preparation['status']=='PREPARED_NOT_NATIVE_RELEASE' and
            preparation['N_total_proposals']==64, 'PREPARATION_MISMATCH')
    b = intent['budget']
    require({k:b[k] for k in ('proposals_per_arm_max','solver_starts_per_arm_max',
        'total_solver_starts_max','native_wall_seconds_max','incremental_storage_bytes_max',
        'max_concurrent_native')} == dict(proposals_per_arm_max=32, solver_starts_per_arm_max=16,
        total_solver_starts_max=32, native_wall_seconds_max=21600,
        incremental_storage_bytes_max=2147483648, max_concurrent_native=2), 'BUDGET_MISMATCH')
    require(intent['comparisons']['equal_qualified_training']['primary_K']==4, 'COMPARISON_K_MISMATCH')
    q = intent['q_selection']
    require(q['values']==list(range(10,21)) and q['spans']==[2.5,2.5,20,.8], 'Q_CONTRACT_MISMATCH')
    require(len(rows)==64 and [r['global_order'] for r in rows]==list(range(1,65)), 'GLOBAL_ORDER_MISMATCH')
    require(len({r['request_id'] for r in rows})==len({r['candidate_id'] for r in rows})==64,
            'REQUEST_ID_DUPLICATE')
    require(set(r['arm'] for r in rows)==set(ARMS), 'ARM_MISMATCH')
    for arm, expected_pass in zip(ARMS,(22,28)):
        group=[r for r in rows if r['arm']==arm]
        require([r['arm_order'] for r in group]==list(range(1,33)), 'ARM_ORDER_MISMATCH')
        require(sum(r['analytic_pass'] is True for r in group)==expected_pass, 'ANALYTIC_ACCOUNTING_MISMATCH')
    require(Counter(r['source'] for r in rows)=={'SPARSE_TARGETED':25,'EXPLORATION':7,'GEOMETRY_DOE':32},
            'SOURCE_ACCOUNTING_MISMATCH')
    for row in rows:
        require(row['schema']=='eucap15_acquisition_candidate.v1' and
                row['protocol_schema']==intent['schema'] and row['recipe_sha256']==INTENT_SHA, 'PROPOSAL_BINDING_MISMATCH')
        require(row['request_id'].startswith(STUDY+'-'), 'FOREIGN_REQUEST')
        require(row['frequency_hz']==15000000000 and row['geometry_units']=='um' and
                tuple(row['geometry_fields'])==GEOMETRY_FIELDS and len(row['geometry'])==10 and
                all(type(v) in (int,float) and math.isfinite(v) for v in row['geometry']), 'GEOMETRY_FIELDS_MISMATCH')
        require(row['no_replacement'] is True and row['no_q_fallback'] is True and
                row['reservation_status']=='NOT_RESERVED', 'PREPARATION_IS_NOT_NATIVE_RESERVATION')
        require(row['native_status']=='NOT_SUBMITTED' and row['q_emx'] is None and
                row['actual_response'] is None and row['solver_start_order'] is None, 'FABRICATED_NATIVE_STATE')
        require(type(row['analytic_pass']) is bool and type(row['local_dispatch_eligible']) is bool and
                row['local_dispatch_eligible']==row['analytic_pass'] and row['duplicate_reasons']==[], 'FROZEN_HOLD_MISMATCH')
        if row['source']=='SPARSE_TARGETED':
            require(row['model_id']==MODEL and row['q_proxy'] in range(10,21) and
                    row['target'][2]==row['q_proxy'] and len(row['target'])==len(row['proxy'])==4, 'FROZEN_Q_MODEL_MISMATCH')
            require(row['candidate_id']==row['qscan_source_candidate']==
                    row['request_id']+'-q'+str(row['q_proxy']), 'Q_CANDIDATE_ID_MISMATCH')
        else:
            require(row['model_id'] is None and row['target'] is None and row['proxy'] is None and
                    row['q_proxy'] is None and row['request_id']==row['candidate_id'], 'INVENTED_DOE_TARGET_OR_MODEL')


@dataclass(frozen=True)
class FrozenBatch:
    manifest_pin: dict
    intent_pin: dict
    proposals_pin: dict
    preparation_pin: dict
    intent: dict
    rows: tuple
    verified_inputs: tuple

    def candidate(self, request_id):
        matches=[r for r in self.rows if r['request_id']==request_id]
        require(len(matches)==1, 'UNKNOWN_OR_DUPLICATE_REQUEST')
        # Deep copy prevents callers from modifying the frozen source object.
        original=json.loads(json.dumps(matches[0],allow_nan=False))
        return dict(original=original,
            physical_selection='FROZEN_CONTROLLED_ACQUISITION_SINGLE',
            controlled_manifest=self.manifest_pin, controlled_intent=self.intent_pin,
            original_request_denominator=64, original_proposal_denominator=64,
            candidate_id_sha256=hashlib.sha256(original['candidate_id'].encode()).hexdigest(),
            candidate_geometry_identity_sha256=original['canonical_geometry_sha256'],
            model_id=MODEL, model_used_for_proposal=original['source']=='SPARSE_TARGETED',
            candidate_model_id=original['model_id'], native_authorized=False,
            adapter_status='METADATA_ONLY_NOT_INSTALLED')


def load_batch(manifest_pin, path_map=None):
    require(manifest_pin['sha256']==MANIFEST_SHA, 'EXACT_CONTROLLED64_MANIFEST_REQUIRED')
    manifest=parse(read_pin(manifest_pin,path_map))
    require(manifest['schema']=='eucap15_controlled_acquisition_manifest.v1' and
            manifest['status']=='PREPARATION_ONLY_NOT_NATIVE_RELEASE', 'MANIFEST_SCHEMA_MISMATCH')
    ip=manifest['intent']
    require(ip['sha256']==INTENT_SHA, 'INTENT_SHA_MISMATCH')
    intent=parse(read_pin(ip,path_map))
    loaded={}; pins=[manifest_pin,ip]
    root=Path(manifest_pin['path']).parent
    for name,p in manifest['files'].items():
        require(Path(name).name==name and p['path']==str(root/name), 'FOREIGN_MANIFEST_FILE')
        # Bytes are checked; arrays, model weights and raw S4P are never opened.
        loaded[name]=read_pin(p,path_map); pins.append(p)
    pp=manifest['files']['SELECTED_CANDIDATES.jsonl']; prep=manifest['files']['PREPARATION_RECEIPT.json']
    require(pp['sha256']==PROPOSALS_SHA and prep['sha256']==PREPARATION_SHA, 'PREPARED_SET_SHA_MISMATCH')
    rows=[parse(line) for line in loaded['SELECTED_CANDIDATES.jsonl'].splitlines()]
    validate_contract(intent,rows,parse(loaded['PREPARATION_RECEIPT.json']))
    return FrozenBatch(manifest_pin,ip,pp,prep,intent,tuple(rows),tuple(pins))
