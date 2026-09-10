"""Pure candidate binding from an already verified FINAL logical context.

The caller must obtain ctx with eucap15_final_context.load_context. This module
does not read or authenticate pinned files, choose a model/Q, or authorize a
native job. The returned dictionary can be frozen once and referenced by every
native stage. Parameter-vector, canonical geometry and actual GDS identities
remain separate; no GDS identity is available from this function.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from pathlib import PurePosixPath
import re

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    GEOMETRY_FIELDS, canonical_geometry_sha256,
)
from .frequency_large_eval import _geometry_hash
from .eucap15_final_statistics import record_sha256


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _same(left, right):
    return record_sha256(left) == record_sha256(right)


def _pin(value):
    _require(isinstance(value, Mapping) and {'path', 'sha256', 'bytes'} <= value.keys(),
             'Complete frozen source pin required')
    path = value['path']
    _require(isinstance(path, str) and PurePosixPath(path).is_absolute()
             and str(PurePosixPath(path)) == path and '..' not in PurePosixPath(path).parts,
             'Exact absolute source pin path required')
    _require(isinstance(value['sha256'], str)
             and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None
             and type(value['bytes']) is int and value['bytes'] > 0,
             'Frozen source SHA256/byte count required')
    return deepcopy(dict(value))


def candidate_binding(ctx, candidate_id):
    """Bind one MAIN/AUDIT union member without I/O or physical claims.

    Input is the dictionary returned by load_context, not a substitute manifest
    or an arbitrary candidate. Its complete frame/source verification is not
    repeated. This function reconciles the selected member's in-memory source
    links and recomputes geometry identity with the existing default9-decimal
    campaign helper. ANALYTIC_FAIL retains its original geometry/nulls; absence
    of finite geometry yields null identity, never a repaired candidate.
    """
    try:
        _require(isinstance(ctx, dict) and ctx.get('schema') == 'eucap15_final_context.v1',
                 'Verified FINAL context required')
        _require(ctx.get('native_dispatch_authorized') is False
                 and ctx.get('physical_evidence_verified') is False,
                 'Logical context must not claim native authorization or physical QA')
        _require(isinstance(candidate_id, str) and candidate_id in ctx['slots'],
                 'Candidate is not a member of the frozen MAIN/AUDIT union')
        frame, model, context = ctx['frame'], ctx['model_freeze'], ctx['context']
        _require(frame.get('schema') == 'eucap15_final_target_frame.v1'
                 and model.get('schema') == 'eucap15_final_model_freeze.v1'
                 and model.get('status') == 'FINAL_FROZEN' and model.get('model_role') == 'FINAL'
                 and model.get('frequency_ghz') == 15 and model.get('label_mode') == 'STRICT_LUMPED',
                 'FINAL frame/model scope differs')
        source_pins = {name: _pin(ctx['source_pins'][name])
                       for name in ('frame', 'model_freeze', 'inference_complete')}
        _require(_same(source_pins['model_freeze'], frame['model_freeze'])
                 and context['frame_sha256'] == source_pins['frame']['sha256']
                 and context['model_freeze_sha256'] == source_pins['model_freeze']['sha256']
                 and context['model_id'] == model['model_id'] == frame['model_id'],
                 'Frozen frame/model source identity differs')
        n = context['N_original_requests']
        _require(type(n) is int and n > 0 and n == frame['N_requests']
                 and len(ctx['bundles']) == len(ctx['routes']) == n,
                 'Complete original frame denominator required')
        _require(type(frame['N_audit_requests']) is int and frame['N_audit_requests'] > 0
                 and frame['N_audit_requests'] == len(ctx['audit_request_ids']),
                 'Frozen original audit denominator differs')
        slot = ctx['slots'][candidate_id]
        order, q = slot['request_order'], slot['q_target']
        _require(type(order) is int and 0 <= order < n and type(q) is int and 10 <= q <= 20,
                 'Original request/Q order required')
        bundle, route = ctx['bundles'][order], ctx['routes'][order]
        request, record = bundle['request'], slot['original_record']
        rid = request['request_id']
        _require(request['request_order'] == order and route['request_order'] == order
                 and route['request_id'] == rid == slot['request_id']
                 and candidate_id == slot['candidate_id'] == record['candidate_id'] == f'{rid}-q{q:02d}'
                 and record['request_id'] == rid and record['q_target'] == q,
                 'Candidate/request/Q identity differs')
        matches = [item for item in route['unique_candidates'] if item['candidate_id'] == candidate_id]
        _require(len(matches) == 1 and _same(slot, matches[0])
                 and len(bundle['records']) == 11 and _same(record, bundle['records'][q-10]),
                 'Union slot is not the original routed record')
        q_proxy = route['q_proxy']
        expected_memberships = (['MAIN'] if q == q_proxy else []) + (['AUDIT'] if request['full11_audit'] else [])
        _require(expected_memberships and slot['memberships'] == expected_memberships
                 and _same(record['q_proxy'], q_proxy) and _same(slot['q_proxy'], q_proxy)
                 and record['model_id'] == context['model_id'] == slot['model_id']
                 and record['frequency_ghz'] == 15 and record['dataset_scope'] == 'FINAL_FROZEN_DATASET',
                 'Frozen membership/preselection/model scope differs')
        original_source = ctx['record_sources'][candidate_id]
        digest = record_sha256(record)
        expected_index = (order % 32) * 11 + q - 10
        _require(type(original_source['record_index_zero_based']) is int
                 and type(original_source['line_number_one_based']) is int
                 and original_source['record_index_zero_based'] == expected_index
                 and original_source['line_number_one_based'] == expected_index + 1
                 and original_source['frozen_record_sha256'] == digest,
                 'Original shard line/canonical record SHA differs')
        source_pins.update({name: _pin(original_source[name])
                            for name in ('shard_receipt', 'records', 'routes')})
        _require(_same(source_pins['shard_receipt'], ctx['source_pins']['shards'][order // 32]),
                 'Candidate shard differs from completed inference shard')
        fields = list(frame['geometry_fields'])
        _require(fields == list(GEOMETRY_FIELDS) == context['geometry_fields'] == record['geometry_fields'],
                 'Original geometry order differs from authoritative ten-field contract')
        geometry = record['grid_geometry']
        _require(isinstance(geometry, list) and len(geometry) == 10
                 and all(v is None or type(v) in (int, float) for v in geometry),
                 'Original ten-value geometry/null array required')
        finite = all(v is not None and math.isfinite(v) for v in geometry)
        analytic = record['analytic_grid']
        _require(type(analytic) is bool and slot['analytic_grid'] is analytic
                 and slot['status'] == ('PENDING' if analytic else 'ANALYTIC_FAIL')
                 and (finite or not analytic), 'Original analytic gate/geometry differs')
        geometry_sha = canonical_geometry_sha256(dict(zip(fields, geometry))) if finite else None
        parameter_hash = record.get('parameter_geometry_hash')
        if parameter_hash is not None:
            _require(isinstance(parameter_hash, str) and parameter_hash == _geometry_hash(geometry, fields),
                     'Original parameter-vector hash differs (not canonical geometry/GDS hash)')
        if 'candidate_geometry_identity_sha256' in record:
            _require(_same(record['candidate_geometry_identity_sha256'], geometry_sha),
                     'Original optional canonical geometry identity differs')
        _require(record['parameter_identity_not_actual_gds'] is True,
                 'Parameter geometry is not actual GDS evidence')
        protocol = frame['protocol']
        return deepcopy(dict(schema='eucap15_final_candidate_binding.v1',
            scope='FROZEN_LOGICAL_CANDIDATE_NOT_NATIVE_AUTHORIZATION_OR_GDS_PROOF',
            source_pins=source_pins, record_index_zero_based=expected_index,
            line_number_one_based=expected_index+1, frozen_record_sha256=digest,
            model_id=context['model_id'], model_role='FINAL', dataset_scope='FINAL_FROZEN_DATASET',
            frequency_ghz=15, request_id=rid, request_order=order, candidate_id=candidate_id,
            q_target=q, q_proxy=q_proxy, memberships=expected_memberships,
            original_request_denominator=n, original_audit_request_denominator=frame['N_audit_requests'],
            original_record=record, geometry_fields=fields, grid_geometry=geometry,
            candidate_geometry_identity_sha256=geometry_sha, parameter_geometry_hash=parameter_hash,
            parameter_identity_not_actual_gds=True, actual_gds_sha256=None,
            geometry_identity_method='canonical_geometry_sha256_DEFAULT_DECIMAL_PLACES_9',
            target=record['target'], grid_proxy=record['grid_proxy'],
            score_scale=protocol['scale'], absolute_tolerances=protocol['absolute_tolerances'],
            analytic_grid=analytic, logical_status=slot['status'], q_emx=None,
            no_failure_replacement=True, native_dispatch_authorized=False, physical_evidence_verified=False))
    except ValueError:
        raise
    except (KeyError, TypeError, IndexError, OverflowError) as error:
        raise ValueError('Invalid FINAL candidate binding context: ' + str(error)) from error
