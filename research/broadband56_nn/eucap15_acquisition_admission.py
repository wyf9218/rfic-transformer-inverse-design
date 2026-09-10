"""Admit a pinned acquisition delta to a separate RESEARCH increment ledger.

No native calls, model loads, training, production acceptance or snapshot edits.
Only baseline train labels and newly train-assigned admitted rows feed coverage.
Canonical parameter-geometry dedup is not a family-independence certification.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import traceback

import numpy as np

from . import data, evaluation, eucap15_acquisition as coverage
from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as campaign

FIELDS = tuple(campaign.GEOMETRY_FIELDS)
SPLITS = ('train', 'validation', 'test')
FEATURES = ('lp_nh', 'ls_nh', 'qmin', 'k_abs')
SOURCES = ('SPARSE_TARGETED', 'GEOMETRY_DOE', 'EXPLORATION')
FREQUENCY = 15_000_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonstandard JSON constant: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def safe(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == str(path.absolute()) and '..' not in path.parts,
            'Canonical absolute path required')
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink path rejected')
    return path


def pin(path):
    path = safe(path)
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


class Inputs:
    def __init__(self):
        self.sources = {}

    def read(self, expected):
        require(set(expected) == {'path', 'sha256', 'bytes'}, 'Exact full input pin required')
        path = safe(expected['path'])
        raw = path.read_bytes()
        actual = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        require(actual == expected, 'Input pin changed: ' + str(path))
        require(str(path) not in self.sources or self.sources[str(path)] == actual, 'Input changed during run')
        self.sources[str(path)] = actual
        return raw

    def json(self, expected):
        return document(self.read(expected))

    def csv(self, expected):
        reader = csv.DictReader(io.StringIO(self.read(expected).decode('utf-8'), newline=''))
        require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), 'CSV header duplicated')
        rows = list(reader)
        require(all(None not in row and None not in row.values() for row in rows), 'Ragged CSV')
        return rows

    def stable(self):
        for expected in self.sources.values():
            require(pin(expected['path']) == expected, 'Source changed at end of admission')


def member_pin(manifest, root, name):
    p = manifest['artifacts'][name]
    require(p['path'] == name, 'Unexpected data artifact path')
    return dict(path=str(root/name), sha256=p['sha256'], bytes=p['size_bytes'])


def boolean(value):
    require(value in ('True', 'False', 'true', 'false'), 'Unexpected source flag')
    return value.lower() == 'true'


def finite4(values):
    require(isinstance(values, (list, tuple)) and len(values) == 4 and
            all(type(v) in (int, float) and math.isfinite(v) for v in values), 'Four finite labels required')
    return list(values)


def canonical(values):
    require(isinstance(values, (list, tuple)) and len(values) == 10 and
            all(type(v) in (int, float) and math.isfinite(v) for v in values), 'Ten finite geometry fields required')
    return campaign.canonical_geometry_sha256(dict(zip(FIELDS, values)))


def nominal_grid_hash(values):
    return canonical(evaluation._grid_geometry(values, .005).tolist())


def metadata_index(rows, *, split_map=None):
    """Never converts physical-response columns, including validation/test labels."""
    result = {}
    for row in rows:
        geometry = [float(row['geom__'+field]) for field in FIELDS]
        h = canonical(geometry)
        require(row['geometry_sha256'] == h and h not in result, 'Canonical member identity duplicate/drift')
        record = {k: row[k] for k in ('geometry_sha256', 'geometry_id', 'source_group', 'candidate_id')}
        if split_map is not None:
            require(h in split_map and row['assigned_development_split'] == split_map[h], 'Member split drift')
            require(split_map[h] == SPLITS[data._split_for_hash(h, 17)], 'Frozen split algorithm drift')
            record['split'] = split_map[h]
        result[h] = dict(record, geometry=geometry, nominal_grid_sha256=nominal_grid_hash(geometry))
    return result


def empty_coverage():
    edges = [np.linspace(low, high, 9) for low, high in zip(coverage.LOWER, coverage.UPPER)]
    result = []
    for i in range(8):
        for j in range(8):
            for k in range(8):
                cell = (i, j, k)
                row = dict(grid_n=8, split='train', frequency_hz=FREQUENCY,
                           lp_bin=i, ls_bin=j, k_bin=k, empty_all_q=True,
                           unobserved_is_not_proven_unreachable=True)
                for axis, pair in enumerate(coverage.BOUND_FIELDS):
                    row[pair[0]], row[pair[1]] = (float(edges[axis][cell[axis]+offset]) for offset in (0, 1))
                row.update({name: 0 for name in coverage.COUNTS})
                result.append(row)
    return result


def q_index(q):
    return 0 if q < 10 else 6 if q > 20 else min(int((q-10)//2), 4)+1


def baseline_coverage(rows, split_map):
    before, labels = empty_coverage(), []
    for row in rows:
        h = row['geometry_sha256']
        require(h in split_map, 'Unknown baseline member')
        if split_map[h] != 'train':
            continue  # Do not convert validation/test response strings.
        actual = finite4([float(row[field]) for field in FEATURES])
        require(int(row['frequency_hz']) == FREQUENCY and boolean(row['strict_lumped_valid']) and
                boolean(row['core_eligible']), 'Baseline train eligibility differs')
        cell = coverage.actual_landing([actual[j] for j in (0, 1, 3)])
        require(cell is not None, 'Baseline train outside frozen core')
        r = before[cell[0]*64 + cell[1]*8 + cell[2]]
        r['N_strict_core_all_q'] += 1
        r[coverage.Q_COUNTS[q_index(actual[2])]] += 1
        r['N_strict_core_q10_20'] += int(10 <= actual[2] <= 20)
        r['empty_all_q'] = False
        labels.append(dict(geometry_hash=h, actual=actual))
    return coverage.validate_coverage(before), labels


def reserved_index(doc, inputs):
    require(doc['schema'] == 'eucap15_reserved_geometry_metadata.v1' and
            doc['status'] == 'FROZEN_METADATA_ONLY_NO_LABELS' and
            doc['geometry_identity'] == 'CAMPAIGN_ORDERED_FIELDS_9DP' and
            doc['geometry_fields'] == list(FIELDS), 'Reserved geometry contract differs')
    sets = {s['name']: s for s in doc['sets']}
    require(len(sets) == len(doc['sets']) and len(sets) >= 2, 'Missing reserved evaluation sets')
    for entry in doc['sets']:
        require(entry['source_pins'], 'Reserved set lacks source provenance')
        for p in entry['source_pins']:
            inputs.read(p)  # Hash only; never parse evaluation labels/model tensors.
    index, grid_index, counts, unavailable = defaultdict(list), defaultdict(list), Counter(), Counter()
    for row in doc['rows']:
        name = row['set_name']
        require(name in sets and row['source_pins'], 'Reserved row source missing')
        for p in row['source_pins']:
            require(p in sets[name]['source_pins'], 'Unbound reserved row source')
        counts[name] += 1
        h = row['geometry_sha256']
        if h is None:
            require(row['geometry'] is None, 'Unhashable geometry must remain null')
            unavailable[name] += 1
            continue
        require(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h), 'Malformed reserved hash')
        if row['geometry'] is not None:
            require(canonical(row['geometry']) == h, 'Reserved canonical geometry drift')
        record = {k: row[k] for k in ('set_name', 'request_id', 'candidate_id', 'source_pins')}
        index[h].append(record)
        if row['geometry'] is not None:
            grid_index[nominal_grid_hash(row['geometry'])].append(dict(record, geometry_sha256=h))
    require(dict(counts) == {name: row['count'] for name, row in sets.items()}, 'Reserved original denominator drift')
    return dict(index), dict(counts), dict(unavailable), dict(grid_index)


def classify_rows(rows, proposals, current, known, reserved, previous, grid_indexes):
    """All outcomes survive; duplicate eligible groups are symmetrically excluded."""
    hashes = Counter(r['geometry_sha256'] for r in rows)
    grid_hashes = Counter(nominal_grid_hash(proposals[r['candidate_id']]['geometry']) for r in rows)
    result = []
    for row in rows:
        p = proposals[row['candidate_id']]
        require(all(row[k] == p[k] for k in ('request_id', 'candidate_id', 'arm', 'arm_order', 'global_order', 'source', 'q_proxy')),
                'Frozen proposal identity drift')
        require(p['geometry_fields'] == list(FIELDS) and p['geometry_units'] == 'um', 'Proposal geometry order/units drift')
        h = canonical(p['geometry'])
        grid_h = nominal_grid_hash(p['geometry'])
        require(h == p['canonical_geometry_sha256'] == row['geometry_sha256'], 'Physical/proposal canonical identity drift')
        require(row['target'] == p['target'] and row['frozen_proxy'] == p['proxy'], 'Original target/proxy changed')
        split = SPLITS[data._split_for_hash(h, 17)]
        potential = row['strict_valid'] is True and row['core_eligible'] is True
        if potential:
            actual = finite4(row['actual'])
            require(row['state'] == 'STRICT_VALID' and row['descriptor_valid'] is True and
                    row['physics_qa_pass'] is True and row['below_half_srf'] is True and
                    coverage.actual_landing([actual[j] for j in (0, 1, 3)]) is not None,
                    'Potential member lacks approved strict/core evidence')
        conflicts = []
        if hashes[h] > 1:
            conflicts.append('DUPLICATE_WITHIN_NEW120_ALL_MEMBERS_EXCLUDED')
        if h in current:
            require(split == current[h]['split'], 'Existing full-pool split conflict')
            conflicts.append('EXISTING_CURRENT6329_' + current[h]['split'].upper())
        if h in known:
            conflicts.append('EXISTING_KNOWN6700_' + known[h]['source_group'])
        if h in reserved:
            conflicts.append('RESERVED_EVALUATION_GEOMETRY')
        if h in previous:
            conflicts.append('PREVIOUS45_OBSERVED_GEOMETRY')
        grid_matches = {name: mapping[grid_h] for name, mapping in grid_indexes.items() if grid_h in mapping}
        if grid_matches or grid_hashes[grid_h] > 1:
            conflicts.append('GRID_EQUIVALENCE_REVIEW')
        admitted = potential and not conflicts
        target_cell = proxy_cell = None
        if p['source'] == 'SPARSE_TARGETED':
            target = finite4(p['target']); proxy = finite4(p['proxy'])
            target_cell = coverage.actual_landing([target[j] for j in (0, 1, 3)])
            require(target_cell is not None and list(target_cell) == p['sparse_cell'], 'Original sparse target cell drift')
            proxy_cell = coverage.actual_landing([proxy[j] for j in (0, 1, 3)])
        else:
            require(p['source'] in SOURCES and p['target'] is p['proxy'] is p['sparse_cell'] is None,
                    'DOE/exploration invented target/proxy')
        actual_cell = None
        if row['actual'] is not None:
            actual_cell = coverage.actual_landing([row['actual'][j] for j in (0, 1, 3)])
        result.append(dict(original=row, proposal_geometry=p['geometry'], geometry_fields=list(FIELDS),
            geometry_sha256=h, nominal_grid_sha256=grid_h,
            request_id=row['request_id'], candidate_id=row['candidate_id'], source=row['source'],
            arm=row['arm'], global_order=row['global_order'], fixed_split=split,
            potential_strict_core=potential, admitted_research_increment=admitted,
            disposition='ADMITTED_RESEARCH_INCREMENT' if admitted else 'EXCLUDED_CONFLICT' if potential else 'NOT_STRICT_CORE',
            conflicts=conflicts, current_pool_match=current.get(h), known_pool_match=known.get(h),
            previous45_matches=previous.get(h, []), nominal_grid_matches=grid_matches,
            within_delta_nominal_grid_count=grid_hashes[grid_h],
            reserved_matches=reserved.get(h, []), target_cell=list(target_cell) if target_cell is not None else None,
            predicted_cell=list(proxy_cell) if proxy_cell is not None else None,
            actual_cell=list(actual_cell) if actual_cell is not None else None,
            actual_cell_interpretation='STRICT_ELIGIBLE_LANDING' if potential else 'DESCRIPTOR_ONLY_NOT_COVERAGE' if row['actual'] is not None else 'NO_PHYSICAL_LABELS',
            coverage_feedback=admitted and split == 'train', parent_family='UNKNOWN_NOT_CERTIFIED',
            production_accepted=False, merged_into_current6329=False, training_performed=False))
    return result


def distribution(labels, before, high_k):
    return dict(n=len(labels), high_k_threshold=high_k, high_k_rule='K_abs > threshold',
        high_k_count=sum(row['actual'][3] > high_k for row in labels),
        max_k=max((row['actual'][3] for row in labels), default=None),
        max_q=max((row['actual'][2] for row in labels), default=None),
        k_bin_counts=[sum(r['N_strict_core_all_q'] for r in before if r['k_bin'] == k) for k in range(8)],
        q_strata={key: sum(r[key] for r in before) for key in coverage.Q_COUNTS},
        q10_20=sum(r['N_strict_core_q10_20'] for r in before))


def feature_refs(row, closure):
    """Carry previously approved full56 provenance; never re-extract/parse S4P."""
    feature = row['feature']
    require(feature is not None and row['s4p'] is not None, 'Admitted member missing physical source references')
    root = Path(feature['path']).parent
    result = {}
    for name, source in (('feature_receipt', feature), ('s4p', row['s4p'])):
        require(source['path'] in closure and closure[source['path']]['original'] == source, 'Missing approved physical pin')
        result[name] = closure[source['path']]
    for name in ('features_56.csv', 'MANIFEST.json'):
        key = str(root/name)
        require(key in closure, 'Missing original56 reference')
        result[name] = closure[key]
    return result


def write_json(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def write_csv(path, rows):
    require(rows, 'Nonempty coverage grid required')
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)


def run(config_pin):
    inputs = Inputs(); cfg = inputs.json(config_pin)
    require(cfg['schema'] == 'eucap15_acquisition_admission_config.v1' and
            cfg['authorization'] == 'USER_RESEARCH_INCREMENT_ADMISSION_NOT_PRODUCTION' and
            cfg['seed'] == 17 and cfg['high_k_threshold'] == .8, 'Admission authorization/protocol differs')
    for p in cfg['implementation']:
        inputs.read(p)
    own = {pin(Path(module.__file__).absolute())['path']: pin(Path(module.__file__).absolute())
           for module in (sys.modules[__name__], data, coverage, campaign, evaluation)}
    require(all(p in cfg['implementation'] for p in own.values()), 'Missing exact runtime source binding')
    output = safe(cfg['output'])
    require(not any(output.is_relative_to(Path(p['path']).parent) for p in cfg['inputs'].values()),
            'Output must be outside every frozen input package')
    output.mkdir(parents=False, exist_ok=False)
    try:
        return _run(cfg, inputs, output, config_pin)
    except BaseException as error:
        write_json(output/'FAILURE_RECEIPT.json', dict(status='FAIL_PRESERVED', error=repr(error),
            traceback=traceback.format_exc(), configuration=config_pin, native_calls=0, model_calls=0, training_calls=0))
        raise
    finally:
        with (output/'SHA256SUMS').open('x') as f:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name != 'SHA256SUMS':
                    f.write(pin(p)['sha256']+'  '+p.name+'\n')


def _run(cfg, inputs, out, config_pin):
    source = cfg['inputs']
    receipt, qa = inputs.json(source['delta_receipt']), inputs.json(source['delta_qa'])
    require(qa['status'] == 'GO_SCOPED_SAVED_VALUES_AND_IDENTITIES' and
            qa['inputs']['run_receipt'] == source['delta_receipt'] and qa['all_read_source_sha_unchanged'] is True,
            'Missing exact independent numerical approval')
    artifacts = {Path(p['path']).name: p for p in receipt['artifacts']}
    rows = inputs.json(artifacts['REQUEST_RESULTS.json'])
    delta_summary = inputs.json(artifacts['SUMMARY.json'])
    closure_rows = inputs.json(artifacts['SOURCE_CLOSURE.json'])
    closure = {r['original']['path']: r for r in closure_rows}
    require(len(closure) == len(closure_rows), 'Duplicate closed source key')
    require(len(rows) == 120 and len({r['request_id'] for r in rows}) == 120 and
            len({r['candidate_id'] for r in rows}) == 120, 'Original120 denominator drift')
    proposals_list = [document(line) for line in inputs.read(source['proposals']).splitlines()]
    require(len(proposals_list) == 256, 'Original256 proposal denominator')
    proposals = {r['candidate_id']: r for r in proposals_list}
    require(len(proposals) == 256 and [r['global_order'] for r in proposals_list] == list(range(1, 257)), 'Original proposal identity/order')
    recipe = inputs.json(source['recipe'])
    for p in proposals_list:
        require(p['recipe_sha256'] == source['recipe']['sha256'], 'Proposal recipe identity drift')
    for p in (source['recipe'], source['proposals']):
        require(any(r['original']['sha256'] == p['sha256'] and r['original']['bytes'] == p['bytes']
                    for r in closure_rows), 'Proposal/recipe not part of approved delta source closure')
    require(recipe['inputs']['coverage'] == source['decision_coverage'] and
            recipe['bins'] == dict(n=8, lower=[.5,.5,.2], upper=[2.,2.,.85], sparse_threshold=5,
                endpoint='half-open except final inclusive upper edge',
                weight='d=max(5-N_strict_core_all_q,0); p=d/sum(d)',
                selection='102 distinct cells sampled without replacement with these initial weights; uniform target inside each selected cell'),
            'Original acquisition decision rule differs')
    inputs.read(source['decision_coverage'])  # Original1804 decision table unchanged; never replace it.
    data_receipt = inputs.json(source['current_data_receipt'])
    require(data_receipt['status'] == 'PASS_PREPARED_BUNDLE_BB00' and
            data_receipt['selected_formal_geometries'] == 6329 and
            data_receipt['split_counts'] == dict(train=3801, validation=1269, test=1259), 'Current6329 freeze differs')
    manifest_pin = data_receipt['data_manifest']; manifest = inputs.json(manifest_pin)
    root = Path(manifest_pin['path']).parent
    contract = inputs.json(data_receipt['contract'])
    require(contract['field_names'] == list(FIELDS) and contract['units'] == 'um' and
            data_receipt['contract']['sha256'] == recipe['geometry']['contract_sha256'] and
            contract['grid_um'] == .005 and contract['grid_status'] == 'SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR',
            'Geometry contract drift')
    require(manifest['geometry_fields'] == list(FIELDS) and manifest['port_contract'] == contract['port_contract'], 'Field/port mismatch')
    split_pin = member_pin(manifest, root, 'splits.json'); splits = inputs.json(split_pin)
    require(splits['seed'] == 17 and splits['counts'] == data_receipt['split_counts'] and
            splits['requested_fractions'] == [.6,.2,.2], 'Current split drift')
    current_rows_pin = member_pin(manifest, root, 'SOURCE_ROWS.csv'); current_rows = inputs.csv(current_rows_pin)
    current = metadata_index(current_rows, split_map=splits['by_geometry_sha256'])
    require(len(current) == 6329 and set(current) == set(splits['by_geometry_sha256']), 'Incomplete whole current pool')
    before, train_labels = baseline_coverage(current_rows, splits['by_geometry_sha256'])
    require(len(train_labels) == 3801, 'Actual frozen train count mismatch')
    known_manifest = inputs.json(source['known_manifest'])
    known_artifacts = {Path(p['path']).name: p for p in known_manifest['artifacts']}
    known_receipt = inputs.json(known_artifacts['RECEIPT.json'])
    known_contract = inputs.json(known_artifacts['CONTRACT_PINS.json'])
    require(known_receipt['status'] == 'PASS_MEMBERSHIP_AND_LABEL_SOURCE_FREEZE' and
            known_receipt['core_members'] == 6700 and known_contract['geometry_order'] == list(FIELDS) and
            known_contract['scientific_fingerprint'] == manifest['contract_fingerprint_sha256'], 'Known pool contract drift')
    known = metadata_index(inputs.csv(known_artifacts['CORE_MEMBERS_6700.csv']))
    require(len(known) == 6700 and set(current) <= set(known), 'Known6700 does not contain full current6329')
    reserved, reserved_counts, unhashable, reserved_grid = reserved_index(inputs.json(source['reserved']), inputs)
    old_rows = inputs.json(source['old_prefix'])['rows'] + inputs.json(source['old_fixed'])['items']
    require(len(old_rows) == len({r['request_id'] for r in old_rows}) == 45, 'Previous45 identity denominator')
    require(not ({r['request_id'] for r in old_rows} & {r['request_id'] for r in rows}), 'Repeated old candidate ID')
    old_digest = hashlib.sha256(('\n'.join(sorted(r['request_id'] for r in old_rows))+'\n').encode()).hexdigest()
    require(old_digest == 'ee1c2a9423a28359ac2bd7c3cc2a56d3150a3d50c055b5ca72cd25bc59f26500', 'Previous45 frozen IDs drift')
    previous, previous_grid = defaultdict(list), defaultdict(list)
    for row in old_rows:
        p = proposals[row['candidate_id']]
        require(p['request_id'] == row['request_id'], 'Previous proposal identity drift')
        h = canonical(p['geometry'])
        require(h == p['canonical_geometry_sha256'], 'Previous canonical geometry drift')
        record = {k: p[k] for k in ('candidate_id', 'request_id', 'source', 'arm', 'global_order')}
        previous[h].append(record); previous_grid[nominal_grid_hash(p['geometry'])].append(dict(record, geometry_sha256=h))
    grid_indexes = dict(reserved=reserved_grid, previous45=dict(previous_grid))
    for name, pool in (('current6329', current), ('known6700', known)):
        group = defaultdict(list)
        for h, row in pool.items():
            group[row['nominal_grid_sha256']].append({k: row[k] for k in ('geometry_sha256','geometry_id','source_group','candidate_id')})
        grid_indexes[name] = dict(group)
    ledger = classify_rows(rows, proposals, current, known, reserved, dict(previous), grid_indexes)
    require(sum(r['potential_strict_core'] for r in ledger) == 31, 'Approved31 strict/core scope changed')
    require([r['global_order'] for r in ledger] == sorted(r['global_order'] for r in ledger), 'Delta order changed')
    members = []
    for record in ledger:
        if not record['admitted_research_increment']:
            continue
        row = record['original']
        members.append(dict(schema='eucap15_research_increment_member.v1', request_id=row['request_id'],
            candidate_id=row['candidate_id'], geometry_sha256=row['geometry_sha256'],
            nominal_grid_sha256=record['nominal_grid_sha256'], geometry_fields=list(FIELDS),
            geometry_units='um', geometry=record['proposal_geometry'], frequency_hz=FREQUENCY, actual=row['actual'],
            split=record['fixed_split'], strict_lumped_valid=True, source=row['source'], arm=row['arm'],
            q_proxy=row['q_proxy'], target=row['target'], proxy=row['frozen_proxy'], target_cell=record['target_cell'],
            predicted_cell=record['predicted_cell'], actual_cell=record['actual_cell'],
            original256_proposal=source['proposals'], original120_result=artifacts['REQUEST_RESULTS.json'],
            original120_independent_qa=source['delta_qa'], original_result=row['original_result'],
            full56_evidence=feature_refs(row, closure), contract=data_receipt['contract'],
            label_policy='REUSE_FROZEN_INDEPENDENTLY_APPROVED_LABELS_NO_REEXTRACTION',
            evidence_class='FRESH_REAL_EMX', membership='ADMITTED_SEPARATE_RESEARCH_INCREMENT',
            production_accepted=False, merged_into_current6329=False, training_performed=False,
            parent_family='UNKNOWN_NOT_CERTIFIED', source_snapshot_utc=delta_summary['snapshot_utc']))
    admitted_train = [dict(geometry_hash=r['geometry_sha256'], split='train', frequency_hz=FREQUENCY,
        strict_lumped_valid=True, evidence_class='FRESH_REAL_EMX', actual=r['actual']) for r in members if r['split'] == 'train']
    train_hashes = {r['geometry_hash'] for r in train_labels}
    gain = coverage.coverage_gain(before, admitted_train, baseline_hashes=train_hashes)
    require(gain['counts']['existing_geometry'] == gain['counts']['duplicate_rows'] == gain['counts']['new_out_of_domain'] == 0,
            'Admitted train geometry was not unique/core')
    contributions = {}
    for name in SOURCES:
        selected = [r for r in members if r['source'] == name]
        train = [dict(geometry_hash=r['geometry_sha256'], split='train', frequency_hz=FREQUENCY,
            strict_lumped_valid=True, evidence_class='FRESH_REAL_EMX', actual=r['actual']) for r in selected if r['split'] == 'train']
        sg = coverage.coverage_gain(before, train, baseline_hashes=train_hashes)
        contributions[name] = dict(original_delta_rows=sum(r['source'] == name for r in ledger),
            potential_strict_core=sum(r['source'] == name and r['potential_strict_core'] for r in ledger),
            admitted_by_split=dict(Counter(r['split'] for r in selected)), train_added=len(train),
            newly_occupied_cells=sg['newly_occupied_cells'], sparse_crossed_5=sg['sparse_crossed_5'],
            sparse_lt5_before=sum(r['N_strict_core_all_q'] < 5 for r in before),
            sparse_lt5_after=sum(r['N_strict_core_all_q'] < 5 for r in sg['after_coverage']),
            deficit_before=sum(max(5-r['N_strict_core_all_q'],0) for r in before),
            deficit_after=sum(max(5-r['N_strict_core_all_q'],0) for r in sg['after_coverage']),
            filled_deficit=sum(max(5-a['N_strict_core_all_q'],0)-max(5-b['N_strict_core_all_q'],0)
                               for a,b in zip(before,sg['after_coverage'])),
            per_cell=sg['per_cell'], overlap_note='Source new-bin counts need not add: sources may enter the same bin')
    after_labels = train_labels + admitted_train
    summary = dict(schema='eucap15_research_acquisition_admission_summary.v1',
        status='COMPLETE_RESEARCH_INCREMENT_ADMISSION_PENDING_INDEPENDENT_QA',
        original_delta=120, potential_strict_core=31, admitted_unique_geometries=len(members),
        original120_unique_canonical_geometries=len({r['geometry_sha256'] for r in ledger}),
        potential31_unique_canonical_geometries=len({r['geometry_sha256'] for r in ledger if r['potential_strict_core']}),
        admitted_by_split={name: sum(r['split'] == name for r in members) for name in SPLITS},
        excluded_potential=31-len(members), disposition_counts=dict(Counter(r['disposition'] for r in ledger)),
        conflict_reasons_nonexclusive=dict(Counter(c for r in ledger for c in r['conflicts'])),
        baseline_geometry_scope=dict(current6329=len(current), known6700=len(known), previous_observed45=len(old_rows), reserved_rows=reserved_counts,
            unhashable_reserved_rows=unhashable, dedup='CAMPAIGN_ORDERED_FIELDS_9DP_PARAMETER_IDENTITY',
            nominal_grid_diagnostic='Existing float64 np.rint(g/.005)*.005; grid collisions excluded as GRID_EQUIVALENCE_REVIEW, not actual GDS equality',
            all_history_exhaustive=False, family_independence='NOT_CERTIFIED'),
        frozen6329_unchanged=True, production_accepted_added=0, model_loads=0, native_calls=0, training_calls=0,
        current_baseline_train=3801, baseline_coverage_grid=[8,8,8], sparse_threshold=5,
        original1804_decision_coverage=source['decision_coverage'], original_recipe=source['recipe'],
        train_coverage=dict(before_occupied=gain['occupied_before'], after_occupied=gain['occupied_after'],
            newly_occupied=gain['newly_occupied_cells'], sparse_crossed_5=gain['sparse_crossed_5'],
            sparse_lt5_before=sum(r['N_strict_core_all_q'] < 5 for r in before),
            sparse_lt5_after=sum(r['N_strict_core_all_q'] < 5 for r in gain['after_coverage']),
            occupancy_fraction_gain=gain['occupancy_fraction_gain'], admitted_train_added=len(admitted_train),
            deficit_before=sum(max(5-r['N_strict_core_all_q'],0) for r in before),
            deficit_after=sum(max(5-r['N_strict_core_all_q'],0) for r in gain['after_coverage']),
            filled_deficit=sum(max(5-a['N_strict_core_all_q'],0)-max(5-b['N_strict_core_all_q'],0)
                               for a,b in zip(before,gain['after_coverage']))),
        train_distributions_before=distribution(train_labels, before, cfg['high_k_threshold']),
        train_distributions_after=distribution(after_labels, gain['after_coverage'], cfg['high_k_threshold']),
        source_contributions={k: {a:b for a,b in v.items() if a != 'per_cell'} for k,v in contributions.items()},
        observation_not_sampling_feedback='New validation/test actual values are stored separately, never used in coverage or next-target allocation',
        inference='No next targets, no model/recipe changes, no equal-budget or causality claim',
        scope='KNOWN_REFERENCE_POOLS_ONLY_NOT100K_HISTORY_NOT_FINAL')
    write_json(out/'ALL120_ADMISSION_LEDGER.json', ledger)
    for name in SPLITS:
        write_json(out/('MEMBERS_'+name.upper()+'.json'), [r for r in members if r['split'] == name])
    write_json(out/'MEMBERS_ALL.json', members)
    write_json(out/'COVERAGE_DELTA.json', gain)
    write_json(out/'SOURCE_CONTRIBUTIONS.json', contributions)
    write_csv(out/'COVERAGE_BEFORE.csv', before); write_csv(out/'COVERAGE_AFTER.csv', gain['after_coverage'])
    write_json(out/'SUMMARY.json', summary)
    inputs.stable()
    write_json(out/'SOURCE_MANIFEST.json', dict(schema='eucap15_admission_source_manifest.v1',
        inputs=list(inputs.sources.values()), configuration=config_pin, all_read_inputs_unchanged=True,
        full56_references='Bound to previously QA-approved SOURCE_CLOSURE; source bodies/S4P not reread'))
    artifacts = [pin(p) for p in sorted(out.iterdir()) if p.is_file()]
    write_json(out/'ADMISSION_RECEIPT.json', dict(schema='eucap15_research_increment_admission_receipt.v1',
        status=summary['status'], created_utc=datetime.now(timezone.utc).isoformat(), configuration=config_pin,
        summary=summary, artifacts=artifacts, command=[sys.executable, *sys.argv],
        baseline_data_root_readonly=str(root), admission_destination=str(out), source_inputs_unchanged=True))
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--config-sha', required=True)
    args = parser.parse_args(); p = pin(args.config)
    require(p['sha256'] == args.config_sha, 'Configuration SHA differs')
    run(p)


if __name__ == '__main__':
    main()
