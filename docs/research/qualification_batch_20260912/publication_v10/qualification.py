"""Read-only historical111 admission readiness, not a ledger/publisher.

Use saved actual111 outputs and original source-row identities. No extraction,
simulation, accepted-sequence allocation, split assignment or formal writes.
The current111 physical receipt adapter is intentionally fail-closed until an
actual owner-produced complete compatibility receipt has an observed schema.
"""
from __future__ import annotations
import hashlib
import importlib.util
import csv
import io
import json
import math
from pathlib import Path
import sys

QUALIFIER_SHA = '6e6ba71e7f35e5d78dbb0a194c08489389343c2e0c86b9584f793947e5fddf76'
GEOMETRY_SHA = 'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98'
EXTRACTOR_SHA = 'd53d4549477f34091bda2faf6e92b1844100eca84f2a87a260c44d4684db6410'
FREQUENCIES = [5_000_000_000 + i * 500_000_000 for i in range(111)]
EXTRACTION_REQUIRED = ['frequency_count_exact_111', 'frequency_vector_exact',
    'frequency_strictly_increasing', 'port_count_exact_four',
    'reference_impedance_valid', 's_matrix_finite', 's_matrix_shape_exact']
GDS_REQUIRED = ['geometry_range_pass', 'topology_pass', 'line_width_sync_pass',
    'angle_45_135_pass', 'ground_clearance_pass', 'foundry_layout_audit_pass',
    'manufacturing_grid_canonicalization_pass', 'foundry_slotted_ground_frame_pass',
    'foundry_power_line_contract_pass', 'foundry_via_stack_and_landing_pad_pass',
    'foundry_bridge_connection_pass']


def norm(p):
    return {'path': p['path'], 'sha256': p['sha256'],
            'bytes': p.get('bytes', p.get('size_bytes'))}


def same(a, b):
    return norm(a) == norm(b)


def _load(path, name, expected_sha):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('dependency symlink')
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        raise ValueError('dependency source drift: ' + str(path))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    prior = sys.modules.get(name)
    if prior is not None and Path(prior.__file__).absolute() != path:
        raise ValueError('foreign preimported dependency: ' + name)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self, q):
        self.q = q
        self.cache = {}
        self.pins = {}
        self.stats = {}
        self.csv_rows = {}

    def read(self, p):
        p = norm(p)
        prior = self.pins.get(p['path'])
        if prior is not None and prior != p:
            raise self.q.SourceIdentityError('conflicting shared pin: ' + p['path'])
        if p['path'] not in self.cache:
            try:
                self.cache[p['path']] = self.q.read(p)
            except Exception as exc:
                raise self.q.SourceIdentityError('shared source NO-GO: ' + str(exc)) from exc
            self.pins[p['path']] = p
        return self.cache[p['path']]

    def doc(self, p):
        try:
            return json.loads(self.read(p))
        except (ValueError, TypeError) as exc:
            raise self.q.SourceIdentityError('shared JSON NO-GO: ' + str(exc)) from exc

    def csv(self, p):
        data = self.read(p)  # Still checks conflicting declarations on reuse.
        if p['path'] not in self.csv_rows:
            self.csv_rows[p['path']] = list(csv.DictReader(io.StringIO(data.decode())))
        return self.csv_rows[p['path']]


def _index(rows, q, description):
    q.need(isinstance(rows, list), description + ': expected actual row array')
    result = {}
    for row in rows:
        key = row['source_row_index']
        q.need(type(key) is int and key >= 0 and key not in result,
               description + ': original source index must be unique nonnegative integer')
        result[key] = row
    return result


def load_context(*, contract_pin, source_manifest_pin, extraction_receipt_pin,
                 target_rows_pin, per_source_pin, all111_pin, readiness_pin,
                 geometry_helpers_path, qualification_path):
    """Read each small frozen artifact once; never rehash the saved17MB CSV.

    contract_pin is the real current YAML configuration, treated as opaque
    exact bytes. Its old frequency setting is not relabelled as a111 contract.
    Large CSV provenance reuses the completed extraction receipt's output SHA;
    this call checks nonsymlink/size/stability only, explicitly not fresh hashing.
    """
    _load(geometry_helpers_path, 'geometry_helpers', GEOMETRY_SHA)
    q = _load(qualification_path, '_historical111_existing_qualifier', QUALIFIER_SHA)
    ctx = Context(q)
    ctx.contract_pin = norm(contract_pin)
    ctx.read(contract_pin)
    ctx.manifest = ctx.doc(source_manifest_pin)
    config = ctx.manifest['current_configuration']
    q.need(config['path'] == contract_pin['path'] and config['sha256'] == contract_pin['sha256'],
           'current configuration differs from source-bound actual configuration')
    receipt = ctx.doc(extraction_receipt_pin)
    q.need(receipt['schema'] == 'p215_explicit100_actual_historical111_conditional_reextraction.v1'
           and receipt['extractor']['sha256'] == EXTRACTOR_SHA
           and receipt['frequency_resampling'] is False and receipt['native_actions'] == 0
           and receipt['formally_added'] == 0 and receipt['target_index_zero_based'] == 20,
           'not the saved historical111 numerical receipt')
    outputs = receipt['outputs']
    for p in (target_rows_pin, per_source_pin, all111_pin):
        q.need(any(same(p, original) for original in outputs), 'output not bound by original extraction receipt')
    ctx.targets = _index(ctx.doc(target_rows_pin), q, 'TARGET15_ROWS')
    ctx.summaries = _index(ctx.doc(per_source_pin), q, 'PER_SOURCE_EXTRACTION')
    ctx.members = _index(ctx.manifest['rows'], q, 'INPUT_MANIFEST')
    readiness = ctx.doc(readiness_pin)
    q.need(readiness['schema'] == 'p215_training_source93_existing_provenance_readiness.v1'
           and same(readiness['source_labels'], target_rows_pin), 'readiness label identity mismatch')
    ctx.readiness = _index(readiness['members'], q, 'READINESS.members')
    q.need(set(ctx.targets) == set(ctx.summaries) == set(ctx.members), 'source/label member set mismatch')
    q.need(set(ctx.readiness) <= set(ctx.members), 'readiness member outside frozen source')
    q.need(receipt['extracted'] == len(ctx.targets)
           and receipt['retained_frequency_rows'] == 111 * len(ctx.targets), 'saved111 row accounting mismatch')
    large = Path(all111_pin['path'])
    q.need(not any(p.is_symlink() for p in (large, *large.parents)), 'all111 CSV symlink')
    before = large.stat()
    after = large.stat()
    stat_fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    q.need(before.st_size == all111_pin['bytes'] and stat_fields(before) == stat_fields(after),
           'saved all111 CSV changed or size differs')
    ctx.large_reference = dict(pin=norm(all111_pin), observation_stat=list(stat_fields(after)),
        verification='PRIOR_FULL_SHA_FROM_ACCEPTED_EXTRACTION_REUSED_CURRENT_LSTAT_ONLY',
        sha256_recomputed_this_call=False, frequency_rows_reparsed_this_call=False)
    ctx.source_manifest_pin = norm(source_manifest_pin)
    ctx.extraction_receipt_pin = norm(extraction_receipt_pin)
    return ctx


def _member_doc(ctx, pin):
    # Per-member damaged/missing evidence must not stop the next member. In
    # contrast shared source/readiness/config failures in load_context are fatal.
    return json.loads(ctx.q.read(pin))


def assess_member(ctx, source_row_index, *, geometry=None, geometry_fields=None,
                  geometry_evidence_pin=None, original_split=None,
                  split_evidence_pin=None, actual_gds_receipt_pin=None,
                  calibre_receipt_pin=None, compatibility_receipt_pin=None,
                  known_identities=None):
    """Return a read-only disposition; never mutate known IDs, data or ledger.

    geometry_evidence_pin preferably pins the original CSV directly. Its
    ordinal comes only from the bound source manifest, not caller reassignment.
    The earlier optional JSON binding shape is also supported:
    {source_row_index,evaluation,geometry,geometry_fields,
    original_csv_pin,source_prefix_ordinal}. It must not
    be passed off as an original production accepted record. Current actual93
    have no declared split; None is preserved. A non-null split needs its own
    exact bb_splits.v1 by-geometry record; no deterministic re-assignment occurs.
    """
    q = ctx.q
    result = dict(schema='eucap15_historical111_readonly_qualification.v1',
        source_row_index=source_row_index, source_old_accepted_sequence=None,
        production_accepted_sequence=None, qualification_sequence=None,
        source_contract_fingerprint=None, current_configuration=ctx.contract_pin,
        formal_added=0, formal_qualified=False, native_actions=0, labels_recomputed=False,
        original_response_frequency_count=111, preserved_split=original_split,
        split_status='UNKNOWN_NOT_ASSIGNED' if original_split is None else 'UNVERIFIED_SOURCE_SPLIT',
        final_independent_test_eligible=False, known_union_mutated=False,
        existing56_publisher_compatible=False, missing_evidence=[])
    missing = result['missing_evidence']
    stage = 'source_row_binding'
    try:
        q.need(type(source_row_index) is int and source_row_index in ctx.members, 'source row outside manifest')
        member, target, summary, ready = (mapping[source_row_index] for mapping in
            (ctx.members, ctx.targets, ctx.summaries, ctx.readiness))
        result.update(source_name=target['merge_source'], evaluation=member['evaluation'],
                      original_source_geometry_sha256=member['raw_geometry_identity_sha256'])
        q.need(all(row['evaluation'] == member['evaluation'] for row in (target, summary, ready)), 'evaluation/source row mismatch')
        q.need(target['merge_source'] == summary['merge_source'], 'historical source namespace mismatch')
        q.need(target['source'] == summary['source'] and
               target['source']['sha256'] == member['historical_s4p_sha256'] == ready['s4p']['sha256'] and
               ready['gds']['sha256'] == member['gds_sha256'], 'saved label/S4P/GDS binding mismatch')
        stage = 'original111_label_contract'
        q.require_checks(summary['summary']['checks'], EXTRACTION_REQUIRED, 'saved original111 extraction')
        s = summary['summary']
        q.need(s['frequency_points'] == 111 and s['frequency_start_hz'] == FREQUENCIES[0]
               and s['frequency_stop_hz'] == FREQUENCIES[-1] and s['frequency_step_hz'] == 500_000_000
               and s['port_count'] == 4, '111 source cannot be represented as56 or a changed grid')
        q.need(target['frequency_hz'] == target['row']['frequency_hz'] == FREQUENCIES[20]
               and target['original_frequency_index_zero_based'] == 20, '15GHz is not original111 index20')
        result['physical15'] = q.label_state(target['row'])
        result['q10_to20_supported'] = 10 <= result['physical15']['qmin'] <= 20
        result['original111'] = dict(source=ready['s4p'], all111=ctx.large_reference,
            frequency_hz=FREQUENCIES, primary_srf=s['primary_srf'], secondary_srf=s['secondary_srf'])
        stage = 'geometry_and_original_split'
        if geometry is None or geometry_fields is None or geometry_evidence_pin is None:
            missing.append('source_bound_exact_geometry_vector_and_field_order')
        else:
            if Path(geometry_evidence_pin['path']).suffix.lower() == '.csv':
                evidence = dict(source_row_index=source_row_index, evaluation=member['evaluation'],
                    geometry=geometry, geometry_fields=geometry_fields,
                    original_csv_pin=geometry_evidence_pin,
                    source_prefix_ordinal=member['source_prefix_ordinal'])
            else:
                evidence = _member_doc(ctx, geometry_evidence_pin)
            q.need(evidence['source_row_index'] == source_row_index and evidence['evaluation'] == member['evaluation']
                   and evidence['geometry'] == geometry and evidence['geometry_fields'] == geometry_fields,
                   'source geometry evidence differs')
            raw_rows = ctx.csv(evidence['original_csv_pin'])
            ordinal = evidence['source_prefix_ordinal']
            q.need(type(ordinal) is int and ordinal == member['source_prefix_ordinal']
                   and 0 <= ordinal < len(raw_rows), 'original CSV source ordinal differs')
            original = raw_rows[ordinal]
            expected_fields = [k[6:] for k in original if k.startswith('geom__')]
            q.need(geometry_fields == expected_fields and
                   geometry == [float(original['geom__' + k]) for k in expected_fields]
                   and original['evaluation'] == member['evaluation']
                   and original['touchstone_sha256'] == member['historical_s4p_sha256'],
                   'original CSV geometry/order/evaluation/S4P binding differs')
            q.need(len(geometry) == len(geometry_fields) == 10 and len(set(geometry_fields)) == 10
                   and all(type(x) in (int, float) and math.isfinite(x) for x in geometry), 'finite exact10 geometry required')
            ids = q.identities(geometry, geometry_fields)
            q.need(ids['production_1e6'] == member['production_geometry_fingerprint_sha256'], 'original production geometry binding differs')
            result.update(geometry=geometry, geometry_fields=geometry_fields, identities=ids,
                          geometry_evidence=norm(geometry_evidence_pin))
        declared = ready['original_split']
        if original_split is None:
            q.need(declared == 'UNKNOWN_NOT_DECLARED_IN_SUPPLIED_SOURCE', 'known original split cannot be discarded')
        else:
            q.need(original_split in ('train', 'validation', 'test'), 'invalid preserved split')
            if split_evidence_pin is None or 'identities' not in result:
                missing.append('exact_original_split_evidence')
            else:
                splits = _member_doc(ctx, split_evidence_pin)
                q.need(splits['schema'] == 'bb_splits.v1' and
                       splits['by_geometry_sha256'][result['identities']['canonical_9dp']] == original_split,
                       'requested split is not the evidenced original split')
                q.need(declared == original_split or declared == 'UNKNOWN_NOT_DECLARED_IN_SUPPLIED_SOURCE', 'conflicting original split evidence')
                result['split_status'] = 'PRESERVED_SOURCE_SPLIT_NOT_NEW_ASSIGNMENT'
        stage = 'actual_physical_evidence'
        # Only inspect existing exact metadata. No GDS analyzer/Calibre runs.
        if actual_gds_receipt_pin is None:
            missing.append('complete_actual_gds_required_checks_receipt')
        else:
            audit = _member_doc(ctx, actual_gds_receipt_pin)
            q.require_checks(audit.get('checks', {}), GDS_REQUIRED, 'complete actual GDS', false_status='physical_invalid')
            q.need(audit['overall_status'] == 'PASS' and audit['gds_sha256'] == member['gds_sha256'], 'actual GDS receipt failed/binding differs', 'physical_invalid')
            q.need(set(audit['effective_required_geometry_checks']) == set(GDS_REQUIRED), 'actual GDS required-key contract differs')
            if 'identities' in result:
                q.need(audit['candidate_geometry_identity_sha256'] == result['identities']['canonical_9dp'], 'actual GDS geometry binding differs')
        if calibre_receipt_pin is None:
            missing.append('actual_calibre_zero_blocking_receipt_deck_and_exact_gds_binding')
        else:
            drc = _member_doc(ctx, calibre_receipt_pin)
            q.need(drc['overall_status'] == 'PASS' and drc['calibre_executed'] is True
                   and drc['calibre_blocking_violations'] == 0 and drc['source_files_unchanged'] is True,
                   'actual Calibre zero-blocking gate failed', 'physical_invalid')
            q.need(drc['gds_sha256'] == member['gds_sha256'], 'Calibre current GDS binding differs')
            # An existing receipt header alone is not a verified deck/current
            # process/port/historical execution chain. Do not invent that schema.
            missing.append('observed_historical111_calibre_deck_and_source_chain_adapter')
        if compatibility_receipt_pin is None:
            missing.append('source_bound_current_process_ports_and_historical_execution_compatibility_receipt')
        else:
            _member_doc(ctx, compatibility_receipt_pin)
            missing.append('observed_historical111_compatibility_receipt_schema_and_artifact_bindings')
        # Until the actual owner schema exists, no caller-written PASS string
        # can make this bounded readiness adapter a formal eligibility bypass.
        if known_identities is not None and 'identities' in result:
            result['known_union_matches'] = {kind: known_identities[kind][value]
                for kind, value in result['identities'].items() if value in known_identities.get(kind, {})}
        result.update(status='missing_evidence', reason='; '.join(missing))
    except q.Disposition as exc:
        result.update(status=exc.status, reason=str(exc))
    except (KeyError, FileNotFoundError, PermissionError, StopIteration) as exc:
        result.update(status='missing_evidence', reason=type(exc).__name__ + ': ' + str(exc))
    except (ValueError, TypeError) as exc:
        result.update(status='incompatible', reason=type(exc).__name__ + ': ' + str(exc))
    result['evidence_stage'] = stage
    return result


if __name__ == '__main__':
    raise SystemExit('READ_ONLY_LIBRARY: call load_context and assess_member; no publisher or controller installed.')
