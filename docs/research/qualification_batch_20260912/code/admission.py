"""Append frozen validation/test members without changing their split; no native API."""
import csv
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path

from atomic_primitives import atomic_json, lease
from geometry_helpers import canonical_geometry_sha256, _production_geometry_fingerprint

FP = 'f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e'
FIRST_SHA = 'ad2a96db8e9ac5e8e5ae7c8ea8aec11cd8eafedfbd8439ec755864f6db15e1bc'
STATUS = 'PASS_CURRENT_CONTRACT_QUALIFIED_INCREMENT_COMMITTED'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def raw(pin):
    path = Path(pin['path'])
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_ino, after.st_size, after.st_mtime_ns), 'source changed while reading: '+str(path))
    require(len(data) == pin.get('bytes', pin.get('size_bytes')) and
            hashlib.sha256(data).hexdigest() == pin['sha256'], 'source identity drift: '+str(path))
    return data


def validate_shared(inputs):
    require(inputs['contract']['scientific_fingerprint'] == FP, 'scientific contract drift')
    require(inputs['qa']['status'] == 'GO_SCOPED_SAVED_VALUES_AND_IDENTITIES', 'independent QA missing')
    require(inputs['admission_summary']['baseline_geometry_scope']['reserved_rows'] ==
            {'REFERENCE64_ALL11Q_RESERVED': 704, 'DEVELOPMENT6329_128_ALL11Q_RESERVED': 1408},
            'reserved evaluation coverage incomplete')
    entries = inputs['entries']
    require(len(entries) == 11 and len({e['member']['candidate_id'] for e in entries}) == 11,
            'wrong frozen holdout partition')
    require(Counter(e['member']['split'] for e in entries) == {'validation': 3, 'test': 8},
            'original holdout splits changed')
    require(len({e['member']['geometry_sha256'] for e in entries}) == 11, 'duplicate partition geometry')


def formal_index(inputs, reader=raw):
    """One finite metadata lookup, reusing the proven source sequence, not P215."""
    q = inputs['continuity']
    state = json.loads(reader(q['current_formal_state']['pin']))
    require(state == q['current_formal_state']['value'], 'locate new formal checkpoint before admission')
    keys = {}
    sequences = set()
    fields = inputs['contract']['geometry_order']
    for source in q['accepted_sources']:
        included = 0
        for row in csv.DictReader(io.StringIO(reader(source['source']).decode())):
            seq = int(row['accepted_sequence'])
            if seq <= source['low']:
                continue
            require(seq not in sequences and row['campaign_contract_fingerprint'] == FP,
                    'formal sequence or contract conflict')
            sequences.add(seq)
            included += 1
            key = _production_geometry_fingerprint([float(row['geom__'+f]) for f in fields])
            keys.setdefault(key, []).append(seq)
        require(included == source['included_rows'], 'source coverage drift')
    require(sorted(sequences) == list(range(1, state['current_accepted']+1)), 'non-contiguous formal ledger')
    require(state['feature_rows'] == len(sequences)*56, 'formal feature count conflict')
    return keys


def qualify(entry, inputs, formal, reader=raw):
    m, c, gap = entry['member'], entry['classified'], entry['gap']
    rid, cid, geometry = m['request_id'], m['candidate_id'], m['geometry_sha256']
    contract = inputs['contract']
    require(cid == c['candidate_id'] == gap['candidate_id'] and
            rid == c['request_id'] == gap['request_id'], 'member ID conflict')
    require(m['split'] == c['fixed_split'] == gap['split'] and m['split'] in ('validation', 'test'), 'split drift')
    require(m['geometry_fields'] == c['geometry_fields'] == contract['geometry_order'], 'field order drift')
    require(m['geometry_units'] == 'um' and m['frequency_hz'] == 15_000_000_000, 'unit/frequency drift')
    require(m['geometry'] == c['proposal_geometry'], 'frozen geometry changed')
    require(canonical_geometry_sha256(dict(zip(m['geometry_fields'], m['geometry']))) == geometry ==
            c['geometry_sha256'] == gap['geometry_sha256'], 'geometry digest mismatch')
    bounds = contract['geometry_bounds']
    require(len(m['geometry']) == 10 and all(math.isfinite(v) and lo <= v <= hi for v, lo, hi in
            zip(m['geometry'], bounds['lower'], bounds['upper'])), 'geometry outside frozen bounds')
    require(m['membership'] == 'ADMITTED_SEPARATE_RESEARCH_INCREMENT' and
            m['production_accepted'] is False and m['evidence_class'] == 'FRESH_REAL_EMX', 'membership drift')
    require(c['admitted_research_increment'] is True and c['potential_strict_core'] is True and
            c['conflicts'] == [] and c['current_pool_match'] is None and c['known_pool_match'] is None and
            c['previous45_matches'] == [] and c['reserved_matches'] == [] and
            c['nominal_grid_matches'] == {} and c['within_delta_nominal_grid_count'] == 1,
            'frozen known-pool or reserved-set conflict')
    key = _production_geometry_fingerprint(m['geometry'])
    historic = entry['historical_lookup']
    require(historic['request_id'] == rid and historic['historical_production_geometry_sha256'] == key
            and historic['matches'] == [], 'frozen historical lookup mismatch')
    require(not formal.get(key), 'already in formal broadband ledger')
    require(entry['controlled64_same_geometry'] == [], 'controlled64 geometry conflict')
    checked = {}

    def check(pin):
        data = reader(pin)
        checked[pin['path']] = pin
        return data

    feature = json.loads(check(m['full56_evidence']['feature_receipt']['original']))
    pre = json.loads(check(gap['preflight']))
    solver = json.loads(check(gap['solver_receipt']))
    calibre = json.loads(check(gap['calibre']))
    check(gap['gds'])
    check(gap['port_manifest'])
    check(m['original_result'])
    for item in m['full56_evidence'].values():
        check(item['original'])
    for pin in pre['source_pins']:
        check(pin)
    for pin in contract['pins']:
        shared = [p for p in pre['source_pins'] if p['path'] == pin['path']]
        if shared:
            require(shared[0]['sha256'] == pin['sha256'], 'physical runtime/config mismatch')
    p = feature['original_proposal']
    require(p['request_id'] == rid and p['candidate_id'] == cid and p['q_proxy'] == m['q_proxy'] and
            p['target'] == m['target'] and p['proxy'] == m['proxy'], 'original request/candidate/Q binding conflict')
    require(p['geometry'] == m['geometry'] and p['geometry_fields'] == m['geometry_fields'] and
            p['canonical_geometry_sha256'] == geometry == pre['geometry_sha256'], 'actual proposal mismatch')
    require(feature['candidate_id'] == pre['candidate_id'] == solver['candidate_id'] == cid,
            'actual physical member mismatch')
    require(feature['preflight'] == gap['preflight'] and feature['solver_receipt'] == gap['solver_receipt']
            and pre['gds'] == gap['gds'] and pre['calibre'] == gap['calibre'], 'physical dependency mismatch')
    require(pre['status'] == solver['status'] == 'PASS' and solver['real_emx'] is True, 'no real solver PASS')
    require(calibre['blocking_drc_violation_count'] == 0 and bool(calibre['checks']) and
            all(v is True for v in calibre['checks'].values()), 'Calibre not zero-blocking PASS')
    require(pre['frequency_grid_hz'] == contract['full_frequency_hz'] and
            pre['port_order'] == contract['port_contract']['port_order'] and
            pre['port_permutation'] == [0, 1, 3, 2] and pre['reference_ohm'] == 50, 'port/grid mismatch')
    grid = feature['original_56_summary']
    require(grid['port_count'] == 4 and grid['frequency_points'] == 56 and
            grid['frequency_start_hz'] == 5_000_000_000 and grid['frequency_stop_hz'] == 60_000_000_000 and
            grid['frequency_step_hz'] == 1_000_000_000 and bool(grid['checks']) and
            all(v is True for v in grid['checks'].values()), 'saved 56-point QA incomplete')
    require(grid['passivity_fail_frequency_count'] == grid['reciprocity_fail_frequency_count'] == 0,
            'saved physical QA failed')
    f = feature['original_frequency_row']
    physical = {k: f[k] for k in ('lp_nh', 'ls_nh', 'qp', 'qs', 'qmin', 'k_abs')}
    require(all(math.isfinite(v) for v in physical.values()) and f['qmin'] == min(f['qp'], f['qs']),
            'invalid extracted values')
    require(feature['strict_lumped_valid'] is True and feature['core15_eligible'] is True and
            feature['production_membership'] is False and .5 <= f['lp_nh'] <= 2 and
            .5 <= f['ls_nh'] <= 2 and .2 <= f['k_abs'] <= .85, 'not strict core15 eligible')
    require(all(c['original'][k] is True for k in
            ('strict_valid', 'below_half_srf', 'descriptor_valid', 'physics_qa_pass')), 'strict predicates missing')
    require([f[k] for k in ('lp_nh', 'ls_nh', 'qmin', 'k_abs')] == m['actual'] == c['original']['actual'],
            'frozen physical label mismatch')
    require(m['full56_evidence'] == gap['full56_evidence'], 'frozen artifact mapping conflict')
    return dict(member=m, physical15=physical, classified=c, historical_lookup=historic,
                known_pool_scope='FROZEN_CURRENT6329_CORE6700_RESERVED2112_PREVIOUS45_NEW120_NOMINAL_GRID',
                current_formal_geometry_matches=[], historical_production_geometry_sha256=key,
                source_pins=list(checked.values()), source_scope=inputs['source_pins'],
                scientific_contract_fingerprint=FP, physical_qa_repeated=False,
                qualification='PASS_RECORDED_CHAIN_AND_CURRENT_BYTE_IDENTITIES_NOT_NEW_PHYSICAL_QA')


def ledger(root, first_sha=FIRST_SHA):
    paths = sorted((root/'records').glob('*.json'))
    require(bool(paths), 'existing sequence1 required')
    result = []
    seen_ids, seen_geometries = set(), set()
    for seq, path in enumerate(paths, 1):
        require(not path.is_symlink() and path.name == f'{seq:06d}.json', 'ledger path/sequence conflict')
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        value = json.loads(data)
        require(value['status'] == STATUS, 'uncommitted record')
        r, cp = value['record'], value['checkpoint']
        require(r['increment_sequence'] == cp['last_increment_sequence'] == cp['increment_accepted'] ==
                cp['increment_15ghz_rows'] == seq and cp['referenced_frequency_rows'] == seq*56,
                'ledger checkpoint conflict')
        require(r['production_accepted_sequence'] is None and r['old_broadband_production_accepted'] is False
                and r['eucap15_qualified_accepted'] is True and r['scientific_contract_fingerprint'] == FP,
                'ledger scope conflict')
        require(r['request_id'] not in seen_ids and r['geometry_sha256'] not in seen_geometries,
                'duplicate committed member')
        require(value['evidence_digest'] == digest(value['evidence']), 'stored evidence corrupt')
        if seq == 1:
            require(sha == first_sha and cp['prior_commit'] is None, 'first record changed')
        else:
            require(cp['prior_commit'] == result[-1]['sha256'], 'broken commit chain')
            require(value['record'] == make_record(value['evidence'], seq), 'stored record/evidence conflict')
        seen_ids.add(r['request_id'])
        seen_geometries.add(r['geometry_sha256'])
        result.append(dict(path=str(path), sha256=sha, value=value))
    return result


def make_record(evidence, sequence):
    m = evidence['member']
    return dict(request_id=m['request_id'], candidate_id=m['candidate_id'],
        candidate_id_sha256=hashlib.sha256(m['candidate_id'].encode()).hexdigest(),
        geometry_sha256=m['geometry_sha256'], geometry=m['geometry'], geometry_fields=m['geometry_fields'],
        geometry_units='um', split=m['split'], source=m['source'], frequency_hz=15_000_000_000,
        physical15=evidence['physical15'], original56=m['full56_evidence'], increment_sequence=sequence,
        production_accepted_sequence=None, old_broadband_production_accepted=False,
        eucap15_qualified_accepted=True, original_research_member_unchanged=True,
        scientific_contract_fingerprint=FP)


def publish(root, evidence, utc, *, first_sha=FIRST_SHA, verify):
    root = Path(root)
    require(root.name == 'qualified15_single_member_v1', 'wrong existing namespace')
    require(not any(p.is_symlink() for p in (root, *root.parents, root/'records', root/'WRITE.lock')),
            'symlink destination')
    require(evidence['member']['split'] in ('validation', 'test') and evidence['scientific_contract_fingerprint'] == FP and
            evidence['qualification'] == 'PASS_RECORDED_CHAIN_AND_CURRENT_BYTE_IDENTITIES_NOT_NEW_PHYSICAL_QA',
            'not an eligible original holdout qualification')
    with lease(root/'WRITE.lock'):
        committed = ledger(root, first_sha)
        m = evidence['member']
        for item in committed:
            r = item['value']['record']
            if r['request_id'] == m['request_id']:
                require(item['value']['evidence_digest'] == digest(evidence), 'conflicting replay')
                return dict(status='ALREADY_COMMITTED_NO_COUNT_CHANGE', added=0,
                            path=item['path'], sha256=item['sha256'])
            require(r['geometry_sha256'] != m['geometry_sha256'], 'duplicate committed geometry')
        verify(evidence)
        seq = len(committed)+1
        value = dict(schema='eucap15_frozen_holdout_qualified_increment.v3', status=STATUS, utc=utc,
            record=make_record(evidence, seq), evidence=evidence, evidence_digest=digest(evidence),
            checkpoint=dict(increment_accepted=seq, increment_15ghz_rows=seq, referenced_frequency_rows=seq*56,
                            last_increment_sequence=seq, prior_commit=committed[-1]['sha256']),
            counting=dict(this_certified_increment=1, old_broadband_added=0, complete_100k_total=None,
                          full_history_certified=False, prior_research_member_count_changed=False),
            limitations=['Known-pool qualification only; unqualified historical sources must deduplicate at future admission.',
                         'No new EMX, no gradient training, no full-band strict-validity claim.',
                         'Not an append to the old broadband accepted sequence.'])
        path = root/'records'/f'{seq:06d}.json'
        sha = atomic_json(path, value, immutable=True)
        require(json.loads(path.read_bytes()) == value and hashlib.sha256(path.read_bytes()).hexdigest() == sha,
                'publication readback failed')
        return dict(status=STATUS, added=1, path=str(path), sha256=sha)
