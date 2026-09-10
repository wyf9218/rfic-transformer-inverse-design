"""Read a completed, frozen FINAL logical frame without inference or dispatch.

The original reader validates the declared model/data/source pins; checkpoints
and datasets are only hashed, never deserialized here. This is not independent
FINAL model-selection or physical-chain QA. All original proxy rows are retained
separately from the MAIN/AUDIT union. No file, lock, target or job is created.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import stat

from . import eucap15_final_frame as frozen
from .eucap15_final_routing import route_request
from .eucap15_final_statistics import record_sha256


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _same(left, right):
    # Unlike dict equality, canonical JSON distinguishes bool from int and
    # preserves signed-zero/number representations in the stored route.
    return record_sha256(left) == record_sha256(right)


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Nonfinite JSON constant: ' + value)


def _decode(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


class _Read:
    """Invocation-local stability guard, not a cache, lock or polling service."""

    def __init__(self):
        self.tokens = {}

    def track(self, path):
        path = Path(path).absolute()
        frozen._no_symlink(path)
        value = path.stat()
        _require(stat.S_ISREG(value.st_mode), 'Regular input required: ' + str(path))
        token = (value.st_dev, value.st_ino, value.st_size,
                 value.st_mtime_ns, value.st_ctime_ns)
        if path in self.tokens:
            _require(token == self.tokens[path], 'Input changed during read: ' + str(path))
        else:
            self.tokens[path] = token
        return path

    def pin(self, path):
        path = self.track(path)
        result = frozen.pin(path)
        self.track(path)
        return result

    def raw(self, value):
        _require(isinstance(value, Mapping) and {'path', 'sha256', 'bytes'} <= value.keys(),
                 'Complete source pin required')
        _require(isinstance(value['path'], str) and Path(value['path']).is_absolute(),
                 'Exact absolute source path required')
        _require(type(value['bytes']) is int and value['bytes'] >= 0,
                 'Integer source byte count required')
        path = self.track(value['path'])
        raw = path.read_bytes()
        self.track(path)
        _require(len(raw) == value['bytes'] and hashlib.sha256(raw).hexdigest() == value['sha256'],
                 'Source pin differs: ' + str(path))
        return raw

    def json(self, value):
        return _decode(self.raw(value))

    def jsonl(self, value):
        return [_decode(line) for line in self.raw(value).splitlines()]

    def finish(self):
        for path in tuple(self.tokens):
            self.track(path)


def _inventory(path):
    frozen._no_symlink(path)
    _require(path.is_dir(), 'Committed inference directory missing')
    return tuple(sorted(p.name for p in path.iterdir()))


def load_context(frame_path, *, expected_frame=None, expected_completion=None):
    """Return verified ``bundles``, statistics ``context``, routes and union slots.

    ``record_sources`` covers all eleven original records per request, including
    unselected rows. ``slots`` contains only unique MAIN/AUDIT members and does
    not deduplicate equal geometry across requests. Optional expected pins bind
    a caller's publication to these exact paths/bytes, not a latest-directory
    guess. Public counts come only from the existing 10000/100 frame contract;
    there is no count override, draw, resume or native entry point.

    This is one finite read invocation. Existing read_frame/_committed checks
    are reused, then pinned artifact bytes are read for the returned values.
    Metadata is checked again before return to reject changes during the read.
    """
    reader = _Read()
    frame_path = Path(frame_path).absolute()
    frame_pin = reader.pin(frame_path)
    if expected_frame is not None:
        _require(_same(frame_pin, expected_frame), 'Expected frame pin differs')
    initial_frame = reader.json(frame_pin)
    initial_model = reader.json(initial_frame['model_freeze'])
    # Track binary inputs before the original reader hashes them, without
    # loading a checkpoint, dataset arrays, validation labels or split values.
    for group in (initial_frame['sources'], initial_model['pins']):
        for source in group.values():
            reader.track(source['path'])
    for name in ('requests', 'audit_ids'):
        reader.track(initial_frame[name]['path'])
    checked_pin, frame, model, requests = frozen.read_frame(frame_path)
    _require(_same(checked_pin, frame_pin) and _same(frame, initial_frame)
             and _same(model, initial_model), 'Frame/model changed during original reader')
    _require(frame.get('REAL_EMX_VALIDATION') == 'NOT_RUN'
             and frame.get('dataset_labels_loaded') is False
             and frame.get('native_dispatch_installed') is False,
             'Frame must remain preparation-only, not physical evidence')
    _require(_same(requests, reader.jsonl(frame['requests'])), 'Request bytes changed during read')
    audit_ids = reader.json(frame['audit_ids'])
    fields = frame['geometry_fields']
    _require(isinstance(fields, list) and len(fields) == 10 and len(set(fields)) == 10
             and all(isinstance(v, str) and v for v in fields), 'Ten unique geometry fields required')
    for name in ('normalizer', 'geometry_contract'):
        _require(reader.json(model['pins'][name]).get('field_names') == fields,
                 'Frame geometry order differs from ' + name)

    root = frame_path.parent / 'inference'
    complete_path = root / 'INFERENCE_COMPLETE.json'
    initial_inventory = _inventory(root)
    complete_pin = reader.pin(complete_path)
    if expected_completion is not None:
        _require(_same(complete_pin, expected_completion), 'Expected completion pin differs')
    completion = reader.json(complete_pin)
    starts = list(range(0, frozen.N_REQUESTS, 32))
    paths = [root / f'batch_{start:06d}' / 'RECEIPT.json' for start in starts]
    _require(isinstance(completion.get('shards'), list)
             and [p.get('path') for p in completion['shards']] == [str(p) for p in paths],
             'Missing, duplicate, unordered or foreign committed shard')
    expected_dirs = {p.parent.name for p in paths}
    _require(set(initial_inventory) == expected_dirs | {'INFERENCE_COMPLETE.json', 'inference.lock'},
             'Unexpected or uncommitted inference entry; do not infer completion from directories')

    bundles, routes, slots, sources, shard_pins, counts = [], [], {}, {}, [], []
    shard_inventories = {}
    for start, path, expected_pin in zip(starts, paths, completion['shards']):
        directory = path.parent
        shard_inventories[directory] = _inventory(directory)
        _require(set(shard_inventories[directory]) ==
                 {'RECEIPT.json', 'records.jsonl', 'ROUTES.json', 'INFERENCE_FAILURES.json', 'INTENT.json'},
                 'Uncommitted/extra shard artifact is not authorized')
        actual_pin = reader.pin(path)
        _require(_same(actual_pin, expected_pin), 'Committed receipt pin differs')
        receipt = reader.json(actual_pin)
        for item in receipt.get('artifacts', {}).values():
            reader.track(item['path'])
        batch = requests[start:start+32]
        reused_pin, reused_counts = frozen._committed(path, directory, batch, frame_pin)
        _require(_same(reused_pin, actual_pin), 'Committed receipt changed during read')
        records = reader.jsonl(receipt['artifacts']['records.jsonl'])
        saved_routes = reader.json(receipt['artifacts']['ROUTES.json'])
        reader.json(receipt['artifacts']['INFERENCE_FAILURES.json'])
        reader.json(receipt['artifacts']['INTENT.json'])
        computed = []
        for offset, request in enumerate(batch):
            rows = records[offset*11:(offset+1)*11]
            _require(all(row.get('dataset_scope') == 'FINAL_FROZEN_DATASET' for row in rows),
                     'Candidate dataset scope is not FINAL_FROZEN_DATASET')
            route = route_request(request, rows, model['model_id'], fields)
            _require(_same(route, saved_routes[offset]), 'Stored ROUTES differs from full routing recomputation')
            _require(not route['budget']['requires_extra_audit_budget'],
                     'Extra audit slot budget is not authorized')
            for slot in route['unique_candidates']:
                cid = slot['candidate_id']
                _require(cid not in slots, 'Duplicate logical candidate in MAIN/AUDIT union')
                slots[cid] = slot
            for q_index, record in enumerate(rows):
                cid = record['candidate_id']
                _require(cid not in sources, 'Duplicate original proxy candidate')
                sources[cid] = dict(shard_receipt=actual_pin,
                    records=receipt['artifacts']['records.jsonl'],
                    routes=receipt['artifacts']['ROUTES.json'],
                    record_index_zero_based=offset*11+q_index,
                    line_number_one_based=offset*11+q_index+1,
                    frozen_record_sha256=record_sha256(record))
            bundles.append(dict(request=request, records=rows))
            computed.append(route)
        local_counts = frozen._route_counts(computed)
        _require(_same(local_counts, reused_counts), 'Recomputed shard counts differ')
        routes.extend(computed); counts.append(local_counts); shard_pins.append(actual_pin)
    totals = {key: sum(row[key] for row in counts) for key in frozen._route_counts([])}
    protocol = frame['protocol']
    _require(totals['main_requests'] == frozen.N_REQUESTS
             and totals['audit_requests'] == frozen.N_AUDIT
             and totals['audit_slots'] == 11*frozen.N_AUDIT
             and len(sources) == 11*frozen.N_REQUESTS
             and len(slots) == totals['unique_candidate_slots'], 'Full original denominators differ')
    _require(totals['additional_audit_slots'] <= protocol['additional_audit_budget']
             and len(slots) <= protocol['maximum_main_plus_audit_candidate_slots'],
             'Campaign main/audit candidate budget exceeded')
    new_batches = completion.get('new_batches')
    _require(type(new_batches) is int and 0 <= new_batches <= len(paths), 'Invalid committed new_batches')
    expected = dict(status='INFERENCE_COMPLETE_NOT_NATIVE_RELEASE', frame=frame_pin,
        shards=shard_pins, committed_batches=len(paths), new_batches=new_batches,
        native_started=0, logical_counts=totals, additional_audit_budget=1000,
        additional_audit_slots_within_budget=True,
        actual_geometry_deduplication='NOT_PERFORMED_NO_CROSS_REQUEST_REUSE_CLAIM',
        native_owner_action='Explicitly bind FINAL main/audit routes; existing pilot64 schemas are not compatible',
        REAL_EMX_VALIDATION='NOT_RUN', physical_error=None)
    _require(_same(completion, expected), 'INFERENCE_COMPLETE identity/status/counts differ')
    _require(initial_inventory == _inventory(root), 'Inference inventory changed during read')
    for path, inventory in shard_inventories.items():
        _require(inventory == _inventory(path), 'Shard inventory changed during read')
    reader.finish()
    context = dict(frame_sha256=frame_pin['sha256'],
        model_freeze_sha256=frame['model_freeze']['sha256'], model_id=model['model_id'],
        geometry_fields=fields.copy(), N_original_requests=len(requests))
    return dict(schema='eucap15_final_context.v1',
        scope='VERIFIED_FROZEN_LOGICAL_FRAME_NOT_NATIVE_OR_FINAL_MODEL_SELECTION_QA',
        context=context, bundles=bundles, routes=routes, slots=slots, record_sources=sources,
        counts=totals, frame=frame, model_freeze=model, audit_request_ids=audit_ids,
        source_pins=dict(frame=frame_pin, model_freeze=frame['model_freeze'],
            inference_complete=complete_pin, requests=frame['requests'], audit_ids=frame['audit_ids'],
            shards=shard_pins, model_inputs=model['pins'], frozen_implementation=frame['sources']),
        reader_implementation=frozen.pin(__file__),
        read_stability='PINNED_BYTES_AND_UNCHANGED_FILE_METADATA_DURING_READ',
        dataset_labels_loaded=False, checkpoint_deserialized=False,
        native_dispatch_authorized=False, physical_evidence_verified=False)
