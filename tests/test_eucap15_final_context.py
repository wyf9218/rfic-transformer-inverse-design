"""Synthetic frozen-file fixtures only; no FINAL targets, models or native I/O.

Counts are explicitly monkeypatched in tests. The fixture handwrites tiny
records, never calls prepare/run/_draw/_batch/load_pair or any predictor.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_final_context as m
from research.broadband56_nn import eucap15_final_frame as f
from research.broadband56_nn.frequency_qscan import q_targets
from tests.test_eucap15_final_routing import fixture as routing_fixture, refresh, FIELDS, MODEL


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')


class Fixture:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setattr(f, 'N_REQUESTS', 35)
        monkeypatch.setattr(f, 'N_AUDIT', 3)
        self.root = tmp_path / 'SYNTHETIC_NOT_A_FINAL_EXPERIMENT'
        self.root.mkdir()
        self.frame_path = self.root / 'FRAME.json'
        self.inference = self.root / 'inference'
        self.inference.mkdir()
        (self.inference / 'inference.lock').touch()
        self.complete_path = self.inference / 'INFERENCE_COMPLETE.json'
        self.pins = {}
        for name in f.PIN_NAMES:
            path = self.root / ('synthetic_' + name + '.json')
            write(path, dict(SYNTHETIC_OPAQUE_BYTES_NOT_WEIGHTS_OR_DATA=True, field_names=FIELDS))
            self.pins[name] = f.pin(path)
        source = self.root / 'synthetic_source.py'
        source.write_text('# synthetic source identity only\n')
        monkeypatch.setattr(f, '_source_pins', lambda: {'synthetic_source.py': f.pin(source)})
        self.model_path = self.root / 'SYNTHETIC_DECLARATION_NOT_REAL_FINAL.json'
        self.model = dict(schema='eucap15_final_model_freeze.v1', status='FINAL_FROZEN',
            model_role='FINAL', frequency_ghz=15, label_mode='STRICT_LUMPED',
            selection_basis='VALIDATION_ONLY', allow_extrapolation=False,
            model_id=MODEL, pins=self.pins, synthetic_unit_test_only=True)
        write(self.model_path, self.model)
        self.requests, self.records, self.routes = [], [], []
        for index in range(35):
            request = dict(request_id=f'SYNTHETIC-UNIFORM_TRIPLE-{index:06d}',
                request_order=index, target_source='FINAL_UNIFORM_TRIPLE', frequency_ghz=15,
                lp_nh=.7+index/1000, ls_nh=1.+index/1000, k_abs=.4+index/10000,
                full11_audit=index in (0, 1, 34),
                audit_order={0: 0, 1: 1, 34: 2}.get(index),
                preselected_emx=True, seed=11, synthetic_unit_test_only=True)
            _, rows = routing_fixture(request['full11_audit'])
            targets = q_targets([request[k] for k in ('lp_nh', 'ls_nh', 'k_abs')])
            for row, target in zip(rows, targets):
                row.update(request_id=request['request_id'],
                    candidate_id=f"{request['request_id']}-q{row['q_target']:02d}",
                    target_source='FINAL_UNIFORM_TRIPLE', dataset_scope='FINAL_FROZEN_DATASET',
                    target=target.tolist(), grid_proxy=target.tolist(), synthetic_unit_test_only=True)
                row['grid_proxy'][0] += abs(row['q_target']-14)/100
            refresh(rows)
            self.requests.append(request)
            self.records.append(rows)
            self.routes.append(m.route_request(request, rows, MODEL, FIELDS))
        self.audit_ids = [r['request_id'] for r in self.requests if r['full11_audit']]
        self.frame = dict(schema='eucap15_final_target_frame.v1',
            status='FROZEN_BEFORE_INFERENCE_AND_ANY_NATIVE',
            model_freeze=f.pin(self.model_path), model_id=MODEL, study_id='SYNTHETIC',
            seed=11, audit_seed=19, protocol=f._protocol(), geometry_fields=FIELDS.copy(),
            sources=f._source_pins(), N_requests=35, N_audit_requests=3,
            REAL_EMX_VALIDATION='NOT_RUN', dataset_labels_loaded=False,
            native_dispatch_installed=False, synthetic_unit_test_only=True)
        self.publish_frame()
        self.publish()
        def forbidden(*args, **kwargs):
            pytest.fail('Forbidden generation, model loading, inference or native operation')
        for name in ('prepare', 'run', '_draw', '_batch', 'load_pair', '_resources'):
            monkeypatch.setattr(f, name, forbidden)

    def publish_frame(self):
        path = self.root / 'requests.jsonl'
        path.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in self.requests))
        audit = self.root / 'audit_request_ids.json'
        write(audit, self.audit_ids)
        self.frame.update(requests=f.pin(path), audit_ids=f.pin(audit))
        write(self.frame_path, self.frame)

    def route(self, index):
        refresh(self.records[index])
        self.routes[index] = m.route_request(self.requests[index], self.records[index], MODEL, FIELDS)

    def preserve_references(self):
        # Only malicious synthetic fixture rebinding, not recomputation or repair.
        for rows, route in zip(self.records, self.routes):
            by_id = {r['candidate_id']: r for r in rows}
            for slot in route['unique_candidates']:
                if slot['candidate_id'] in by_id:
                    slot['original_record'] = deepcopy(by_id[slot['candidate_id']])

    def publish(self):
        frame_pin = f.pin(self.frame_path)
        shards, counts = [], []
        for start in (0, 32):
            directory = self.inference / f'batch_{start:06d}'
            directory.mkdir(exist_ok=True)
            batch = self.requests[start:start+32]
            records = [r for rows in self.records[start:start+32] for r in rows]
            routes = self.routes[start:start+32]
            ids = [r['request_id'] for r in batch]
            write(directory / 'INTENT.json', dict(frame=frame_pin, request_ids=ids, native_allowed=False))
            (directory / 'records.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in records))
            write(directory / 'ROUTES.json', routes)
            write(directory / 'INFERENCE_FAILURES.json', {'SYNTHETIC_NOT_INFERENCE': True})
            count = f._route_counts(routes)
            receipt = dict(schema='eucap15_final_inference_shard.v1', status='COMMITTED_PROXY_ONLY',
                frame=frame_pin, route_counts=count, request_ids=ids,
                artifacts={n: f.pin(directory/n) for n in
                    ('records.jsonl', 'ROUTES.json', 'INFERENCE_FAILURES.json', 'INTENT.json')},
                logical_proxy_candidates=11*len(batch), native_started=0, REAL_EMX_VALIDATION='NOT_RUN')
            write(directory / 'RECEIPT.json', receipt)
            shards.append(f.pin(directory / 'RECEIPT.json')); counts.append(count)
        totals = {key: sum(row[key] for row in counts) for key in f._route_counts([])}
        write(self.complete_path, dict(status='INFERENCE_COMPLETE_NOT_NATIVE_RELEASE',
            frame=frame_pin, shards=shards, committed_batches=2, new_batches=2,
            native_started=0, logical_counts=totals, additional_audit_budget=1000,
            additional_audit_slots_within_budget=totals['additional_audit_slots'] <= 1000,
            actual_geometry_deduplication='NOT_PERFORMED_NO_CROSS_REQUEST_REUSE_CLAIM',
            native_owner_action='Explicitly bind FINAL main/audit routes; existing pilot64 schemas are not compatible',
            REAL_EMX_VALIDATION='NOT_RUN', physical_error=None))


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    return Fixture(tmp_path, monkeypatch)


def fingerprints(root):
    return {str(p.relative_to(root)): (f.sha256(p), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_complete_two_shards_readonly_routes_sources_and_shared_candidate(frozen):
    before = fingerprints(frozen.root)
    result = m.load_context(frozen.frame_path,
        expected_frame=f.pin(frozen.frame_path), expected_completion=f.pin(frozen.complete_path))
    assert fingerprints(frozen.root) == before
    assert len(result['bundles']) == 35 and len(result['record_sources']) == 385
    assert len(result['slots']) == 65 and len(result['audit_request_ids']) == 3
    assert result['counts']['audit_slots'] == 33
    assert result['counts']['additional_audit_slots'] == 30
    assert result['context']['N_original_requests'] == 35
    assert result['context']['frame_sha256'] == f.sha256(frozen.frame_path)
    assert result['context']['model_freeze_sha256'] == f.sha256(frozen.model_path)
    route = result['routes'][0]
    slot = result['slots'][route['main']['candidate_id']]
    assert slot['memberships'] == ['MAIN', 'AUDIT'] and slot['q_proxy'] == 14
    unselected = frozen.records[2][0]['candidate_id']
    assert unselected in result['record_sources'] and unselected not in result['slots']
    last = frozen.records[34][-1]
    src = result['record_sources'][last['candidate_id']]
    assert src['record_index_zero_based'] == 32 and src['line_number_one_based'] == 33
    assert src['frozen_record_sha256'] == m.record_sha256(last)
    assert all(r['q_emx'] is None for r in result['routes'])
    assert result['native_dispatch_authorized'] is False
    assert result['checkpoint_deserialized'] is False and result['dataset_labels_loaded'] is False


def test_analytic_failed_preselection_is_retained_not_reselected(frozen):
    frozen.records[0][4]['analytic_grid'] = False
    frozen.route(0); frozen.publish()
    result = m.load_context(frozen.frame_path)
    assert result['routes'][0]['q_proxy'] == 14
    assert result['routes'][0]['main']['status'] == 'ANALYTIC_FAIL'
    assert len(result['routes'][0]['audit_slots']) == 11


def test_missing_non_audit_selection_keeps_original_denominator_and_all_proxy_sources(frozen):
    frozen.records[2][0]['grid_proxy'][0] = None
    frozen.route(2); frozen.publish()
    result = m.load_context(frozen.frame_path)
    assert result['routes'][2]['q_proxy'] is None
    assert result['routes'][2]['main']['status'] == 'NO_SELECTION_INCOMPLETE_PROXY_SCAN'
    assert result['counts']['main_requests'] == 35 and result['counts']['missing_main_selection'] == 1
    assert len(result['slots']) == 64 and len(result['record_sources']) == 385


def test_missing_audit_selection_rejects_unauthorized_eleventh_extra_slot(frozen):
    frozen.records[0][0]['grid_proxy'][0] = None
    frozen.route(0); frozen.publish()
    with pytest.raises(ValueError, match='Extra audit slot budget'):
        m.load_context(frozen.frame_path)


@pytest.mark.parametrize('change', ['main_status', 'membership', 'q_emx', 'native', 'integer_instead_of_bool'])
def test_route_shape_and_counts_alone_cannot_authorize_tampering(frozen, change):
    route = frozen.routes[0]
    if change == 'main_status': route['main']['status'] = 'GDS_FAIL'
    elif change == 'membership': route['unique_candidates'][4]['memberships'] = ['AUDIT']
    elif change == 'q_emx': route['q_emx'] = 14
    elif change == 'native': route['native_dispatch_authorized'] = True
    else: route['no_success_replacement'] = 1
    frozen.publish()
    with pytest.raises(ValueError, match='Stored ROUTES differs'):
        m.load_context(frozen.frame_path)


@pytest.mark.parametrize('change', ['model', 'scope', 'target', 'q_proxy', 'preselection', 'physical'])
def test_rebound_original_record_semantics_are_rechecked(frozen, change):
    row = frozen.records[0][4]
    if change == 'model': row['model_id'] = 'OTHER_SYNTHETIC_MODEL'
    elif change == 'scope': row['dataset_scope'] = 'DEVELOPMENT'
    elif change == 'target': row['target'][0] += 1e-12
    elif change == 'q_proxy': row['q_proxy'] = 15
    elif change == 'preselection': row['proxy_preselected'] = False
    else: row['actual_response'] = row['target'].copy()
    frozen.preserve_references(); frozen.publish()
    with pytest.raises(ValueError): m.load_context(frozen.frame_path)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'foreign', 'reverse'])
def test_completion_requires_exact_ordered_shard_list(frozen, change):
    value = f.read_json(frozen.complete_path)
    if change == 'missing': value['shards'].pop()
    elif change == 'duplicate': value['shards'][1] = value['shards'][0]
    elif change == 'foreign': value['shards'][1]['path'] = str(frozen.inference/'batch_000064/RECEIPT.json')
    else: value['shards'].reverse()
    write(frozen.complete_path, value)
    with pytest.raises(ValueError, match='committed shard'): m.load_context(frozen.frame_path)


@pytest.mark.parametrize('change', ['partial', 'extra_batch', 'extra_artifact'])
def test_uncommitted_or_out_of_range_artifacts_are_preserved_rejected(frozen, change):
    if change == 'partial':
        (frozen.inference/'batch_000032/RECEIPT.json').unlink()  # Synthetic fixture only.
    elif change == 'extra_batch':
        (frozen.inference/'batch_000064').mkdir()
    else: write(frozen.inference/'batch_000000/UNCOMMITTED.json', {'synthetic': True})
    before = fingerprints(frozen.root)
    with pytest.raises((ValueError, FileNotFoundError)): m.load_context(frozen.frame_path)
    assert fingerprints(frozen.root) == before


@pytest.mark.parametrize('artifact', ['records', 'model_pin', 'receipt'])
def test_changed_pinned_bytes_are_not_loaded_or_repaired(frozen, artifact):
    path = {'records': frozen.inference/'batch_000000/records.jsonl',
            'model_pin': Path(frozen.pins['forward']['path']),
            'receipt': frozen.inference/'batch_000000/RECEIPT.json'}[artifact]
    path.write_text(path.read_text() + ' ')
    before = fingerprints(frozen.root)
    with pytest.raises(ValueError): m.load_context(frozen.frame_path)
    assert fingerprints(frozen.root) == before


@pytest.mark.parametrize('field,value', [('status', 'INFERENCE_PARTIAL_RESUMABLE'),
    ('native_started', 1), ('native_started', False), ('physical_error', 0),
    ('additional_audit_slots_within_budget', False), ('new_batches', True)])
def test_completion_terminal_fields_not_just_file_presence(frozen, field, value):
    receipt = f.read_json(frozen.complete_path); receipt[field] = value
    write(frozen.complete_path, receipt)
    with pytest.raises(ValueError): m.load_context(frozen.frame_path)


@pytest.mark.parametrize('kind', ['frame', 'completion'])
def test_optional_expected_pin_binds_exact_bytes(frozen, kind):
    path = frozen.frame_path if kind == 'frame' else frozen.complete_path
    expected = f.pin(path); expected['sha256'] = '0'*64
    with pytest.raises(ValueError, match='Expected'):
        m.load_context(frozen.frame_path, **{'expected_'+kind: expected})


def test_duplicate_or_out_of_range_request_is_rejected(frozen):
    frozen.requests[1]['request_id'] = frozen.requests[0]['request_id']
    frozen.publish_frame(); frozen.publish()
    with pytest.raises(ValueError, match='Duplicate'): m.load_context(frozen.frame_path)


def test_geometry_field_order_is_bound_to_model_metadata(frozen):
    frozen.frame['geometry_fields'] = list(reversed(FIELDS))
    frozen.publish_frame(); frozen.publish()
    with pytest.raises(ValueError, match='geometry order'): m.load_context(frozen.frame_path)


def test_nonsymlink_files_only(frozen):
    path = frozen.inference/'batch_000000/records.jsonl'
    storage = frozen.root/'synthetic_records_storage'; path.rename(storage)
    path.symlink_to(storage)
    with pytest.raises(ValueError, match='[Ss]ymlink|nonsymlink'): m.load_context(frozen.frame_path)


def test_duplicate_json_key_is_rejected(frozen):
    raw = frozen.complete_path.read_text()
    frozen.complete_path.write_text(raw.replace('{', '{"native_started": 0,', 1))
    with pytest.raises(ValueError, match='Duplicate JSON key'): m.load_context(frozen.frame_path)


def test_read_time_change_of_already_hashed_opaque_input_is_detected(frozen, monkeypatch):
    original = m.route_request
    changed = []
    def mutate_after_source_hash(*args):
        result = original(*args)
        if not changed:
            changed.append(True)
            path = Path(frozen.pins['dataset']['path'])
            path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(m, 'route_request', mutate_after_source_hash)
    with pytest.raises(ValueError, match='Input changed during read'):
        m.load_context(frozen.frame_path)


def test_read_time_new_uncommitted_directory_is_detected(frozen, monkeypatch):
    original = m.route_request
    def mutate_inventory(*args):
        result = original(*args)
        (frozen.inference/'batch_999999').mkdir(exist_ok=True)
        return result
    monkeypatch.setattr(m, 'route_request', mutate_inventory)
    with pytest.raises(ValueError, match='inventory changed during read'):
        m.load_context(frozen.frame_path)


def test_public_counts_fixed_no_production_count_override():
    import inspect
    assert f.N_REQUESTS == 10000 and f.N_AUDIT == 100
    assert list(inspect.signature(m.load_context).parameters) == [
        'frame_path', 'expected_frame', 'expected_completion']
