"""Pure synthetic loaded-context fixtures; no files, real models or native work."""
from copy import deepcopy
import hashlib
import json

import pytest

from research.broadband56_nn.eucap15_final_binding import candidate_binding
from research.broadband56_nn.eucap15_final_routing import route_request
from research.broadband56_nn.eucap15_final_statistics import record_sha256
from research.broadband56_nn.frequency_large_eval import _geometry_hash
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    GEOMETRY_FIELDS, canonical_geometry_sha256,
)
from tests.test_eucap15_final_routing import fixture as routing_fixture, MODEL


def pin(name):
    raw = ('SYNTHETIC_ONLY:' + name).encode()
    return dict(path='/synthetic/' + name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def fixture(*, bad_geometry=False, optional_identity=False, parameter=True):
    bundles, routes, slots, records = [], [], {}, {}
    fields = list(GEOMETRY_FIELDS)
    shard = pin('batch_000000/RECEIPT.json')
    for order, audit in enumerate((True, False)):
        req, rows = routing_fixture(audit=audit)
        rid = f'SYNTHETIC-{order:06d}'
        req.update(request_id=rid, request_order=order)
        for row in rows:
            row.update(request_id=rid, candidate_id=f'{rid}-q{row["q_target"]:02d}',
                       dataset_scope='FINAL_FROZEN_DATASET', geometry_fields=fields.copy())
            row['grid_geometry'] = [float(i+1) + .123456789123 for i in range(10)]
            if bad_geometry and order == 0 and row['q_target'] == 14:
                row['grid_geometry'][3] = None
                row['analytic_grid'] = False
            if parameter:
                row['parameter_geometry_hash'] = _geometry_hash(row['grid_geometry'], fields)
            if optional_identity:
                row['candidate_geometry_identity_sha256'] = (
                    canonical_geometry_sha256(dict(zip(fields, row['grid_geometry'])))
                    if None not in row['grid_geometry'] else None)
        route = route_request(req, rows, MODEL, fields)
        bundles.append(dict(request=req, records=rows)); routes.append(route)
        slots.update({s['candidate_id']: s for s in route['unique_candidates']})
        for qi, row in enumerate(rows):
            index = order*11+qi
            records[row['candidate_id']] = dict(shard_receipt=deepcopy(shard),
                records=pin('batch_000000/records.jsonl'), routes=pin('batch_000000/ROUTES.json'),
                record_index_zero_based=index, line_number_one_based=index+1,
                frozen_record_sha256=record_sha256(row))
    sources = dict(frame=pin('FRAME.json'), model_freeze=pin('MODEL_FREEZE.json'),
        inference_complete=pin('INFERENCE_COMPLETE.json'), shards=[shard])
    return dict(schema='eucap15_final_context.v1', bundles=bundles, routes=routes,
        slots=slots, record_sources=records, source_pins=sources, audit_request_ids=['SYNTHETIC-000000'],
        native_dispatch_authorized=False, physical_evidence_verified=False,
        model_freeze=dict(schema='eucap15_final_model_freeze.v1', status='FINAL_FROZEN',
            model_role='FINAL', model_id=MODEL, frequency_ghz=15, label_mode='STRICT_LUMPED'),
        frame=dict(schema='eucap15_final_target_frame.v1', model_id=MODEL,
            model_freeze=deepcopy(sources['model_freeze']), geometry_fields=fields,
            N_requests=2, N_audit_requests=1,
            protocol=dict(scale=[2.5, 2.5, 20., .8],
                          absolute_tolerances=[.125, .125, 1., .04000000000000001])),
        context=dict(frame_sha256=sources['frame']['sha256'],
            model_freeze_sha256=sources['model_freeze']['sha256'], model_id=MODEL,
            geometry_fields=fields.copy(), N_original_requests=2))


def test_pure_exact_shared_binding_hash_namespaces_and_no_mutation(monkeypatch):
    ctx = fixture(optional_identity=True)
    before = deepcopy(ctx)
    def forbidden(*a, **kw):
        raise AssertionError('No file opening in pure binding')
    monkeypatch.setattr('builtins.open', forbidden)
    monkeypatch.setattr('pathlib.Path.open', forbidden)
    value = candidate_binding(ctx, 'SYNTHETIC-000000-q14')
    assert value['memberships'] == ['MAIN', 'AUDIT']
    assert value['record_index_zero_based'] == 4 and value['line_number_one_based'] == 5
    assert value['source_pins']['frame'] == ctx['source_pins']['frame']
    assert value['frozen_record_sha256'] == ctx['record_sources'][value['candidate_id']]['frozen_record_sha256']
    expected = hashlib.sha256(json.dumps([(k, f'{v:.9f}') for k, v in
        zip(GEOMETRY_FIELDS, value['grid_geometry'])], sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    assert value['candidate_geometry_identity_sha256'] == expected
    assert value['parameter_geometry_hash'] != expected != value['frozen_record_sha256']
    assert value['absolute_tolerances'][-1] == .04000000000000001
    assert value['actual_gds_sha256'] is None and value['q_emx'] is None
    assert value['native_dispatch_authorized'] is False and value['physical_evidence_verified'] is False
    assert value['original_request_denominator'] == 2 and value['original_audit_request_denominator'] == 1
    assert ctx == before
    value['source_pins']['frame']['path'] = '/mutated'
    value['original_record']['grid_geometry'][0] = 999
    assert ctx == before


def test_audit_only_and_main_only_do_not_replace_q():
    ctx = fixture()
    audit = candidate_binding(ctx, 'SYNTHETIC-000000-q10')
    main = candidate_binding(ctx, 'SYNTHETIC-000001-q14')
    assert audit['memberships'] == ['AUDIT'] and audit['q_target'] == 10 and audit['q_proxy'] == 14
    assert main['memberships'] == ['MAIN'] and main['line_number_one_based'] == 16
    with pytest.raises(ValueError, match='union'):
        candidate_binding(ctx, 'SYNTHETIC-000001-q10')


@pytest.mark.parametrize('parameter', [True, False])
def test_analytic_failed_null_geometry_is_preserved(parameter):
    ctx = fixture(bad_geometry=True, optional_identity=True, parameter=parameter)
    row = candidate_binding(ctx, 'SYNTHETIC-000000-q14')
    assert row['q_proxy'] == 14 and row['logical_status'] == 'ANALYTIC_FAIL'
    assert row['grid_geometry'][3] is None and row['candidate_geometry_identity_sha256'] is None
    assert row['parameter_geometry_hash'] is None
    assert ('parameter_geometry_hash' in row['original_record']) is parameter


def test_missing_parameter_and_optional_canonical_fields_not_added_to_original():
    row = candidate_binding(fixture(parameter=False), 'SYNTHETIC-000000-q14')
    assert row['parameter_geometry_hash'] is None and row['candidate_geometry_identity_sha256']
    assert 'parameter_geometry_hash' not in row['original_record']
    assert 'candidate_geometry_identity_sha256' not in row['original_record']


@pytest.mark.parametrize('change', ['context_schema', 'native', 'physical', 'model_role', 'model_id',
    'frame_sha', 'model_pin', 'denominator', 'audit_denominator', 'foreign', 'memberships',
    'q_proxy', 'record_sha', 'line_number', 'record_index', 'shard', 'relative_pin', 'bad_sha',
    'bool_bytes', 'negative_order', 'field_order', 'bool_geometry', 'optional_geometry_sha',
    'parameter_hash', 'analytic_drift'])
def test_candidate_context_drift_fails_closed(change):
    ctx = fixture(optional_identity=True)
    cid = 'SYNTHETIC-000000-q14'
    slot = ctx['slots'][cid]
    source = ctx['record_sources'][cid]
    if change == 'context_schema': ctx['schema'] = 'development'
    elif change == 'native': ctx['native_dispatch_authorized'] = True
    elif change == 'physical': ctx['physical_evidence_verified'] = True
    elif change == 'model_role': ctx['model_freeze']['model_role'] = 'REFERENCE'
    elif change == 'model_id': ctx['model_freeze']['model_id'] = 'other'
    elif change == 'frame_sha': ctx['context']['frame_sha256'] = '0'*64
    elif change == 'model_pin': ctx['frame']['model_freeze']['sha256'] = '0'*64
    elif change == 'denominator': ctx['context']['N_original_requests'] = 1
    elif change == 'audit_denominator': ctx['frame']['N_audit_requests'] = 2
    elif change == 'foreign': cid = 'SYNTHETIC-999999-q14'
    elif change == 'memberships': slot['memberships'] = ['AUDIT']
    elif change == 'q_proxy': slot['q_proxy'] = 13
    elif change == 'record_sha': source['frozen_record_sha256'] = '0'*64
    elif change == 'line_number': source['line_number_one_based'] += 1
    elif change == 'record_index': source['record_index_zero_based'] += 1
    elif change == 'shard': source['shard_receipt'] = pin('OTHER/RECEIPT.json')
    elif change == 'relative_pin': source['records']['path'] = 'relative.jsonl'
    elif change == 'bad_sha': source['routes']['sha256'] = 'not-a-sha'
    elif change == 'bool_bytes': source['routes']['bytes'] = True
    elif change == 'negative_order': slot['request_order'] = -1
    elif change == 'field_order': ctx['frame']['geometry_fields'].reverse()
    else:
        # Coherently rebind a synthetic record so its new canonical SHA cannot
        # hide parameter/canonical geometry or gate contradictions.
        record = slot['original_record']
        if change == 'bool_geometry': record['grid_geometry'][0] = True
        elif change == 'optional_geometry_sha': record['candidate_geometry_identity_sha256'] = '0'*64
        elif change == 'parameter_hash': record['parameter_geometry_hash'] = '0'*64
        else: record['analytic_grid'] = False
        ctx['bundles'][0]['records'][4] = deepcopy(record)
        source['frozen_record_sha256'] = record_sha256(record)
    with pytest.raises(ValueError):
        candidate_binding(ctx, cid)
