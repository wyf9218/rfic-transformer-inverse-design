"""Publish exactly one qualified 15 GHz data increment; no solver/control API."""
import hashlib
import json
from pathlib import Path

MEMBER_ID = 'eucap15_dev_acquisition_pair_20260909_v1-GEOMETRY_DOE-006'
GEOMETRY = '804eddfad6a985c668daae57cba007891999da88db9d09d20df210ea8bd48205'
FEATURE_SHA = 'ef546e7b9a7484220f607c7ade9a6fcec3046f014dd17068a78629d898a30a3a'
FP = 'f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def validate(evidence):
    member = evidence['member']
    qualification = evidence['qualification']
    classified = evidence['classified']
    core = evidence['core_check']
    require(member['request_id'] == member['candidate_id'] == MEMBER_ID, 'exact one member only')
    require(member['geometry_sha256'] == GEOMETRY and member['split'] == 'train', 'identity/split drift')
    require(member['full56_evidence']['feature_receipt']['original']['sha256'] == FEATURE_SHA, 'feature drift')
    require(member['production_accepted'] is False and member['membership'] ==
            'ADMITTED_SEPARATE_RESEARCH_INCREMENT', 'original research membership changed')
    require(classified['candidate_id'] == MEMBER_ID and classified['geometry_sha256'] == GEOMETRY,
            'classifier member mismatch')
    require(classified['admitted_research_increment'] is True and classified['potential_strict_core'] is True
            and classified['conflicts'] == [] and classified['fixed_split'] == 'train', 'classification not eligible')
    require(classified['nominal_grid_matches'] == {} and classified['within_delta_nominal_grid_count'] == 1,
            'grid equivalence conflict')
    require(classified['proposal_geometry'] == member['geometry'] and
            classified['geometry_fields'] == member['geometry_fields'], 'classifier geometry mismatch')
    require(qualification['request_id'] == MEMBER_ID and qualification['shared_current_contract'] == FP,
            'current contract mismatch')
    require(qualification['existing_physical_evidence'] ==
            'PASS_RECORDED_CHAIN_AND_CURRENT_BYTE_IDENTITIES_NOT_NEW_PHYSICAL_QA', 'physical evidence incomplete')
    require(qualification['core15_eligible'] is True and qualification['current_formal_geometry_matches'] == [],
            'current pool conflict')
    require(qualification['historical_index_lookup_reused']['matches'] == [], 'historical index conflict')
    require(core['status'] == 'NO_MATCH_IN_FROZEN_CORE6700_ONLY' and core['matches'] == [], 'core pool conflict')
    require(evidence['controlled64_same_geometry'] == [], 'new controlled batch geometry conflict')
    require(evidence['physical120_independent_qa']['status'] == 'GO_SCOPED_SAVED_VALUES_AND_IDENTITIES',
            'independent physical QA missing')
    observed = [qualification['physical15'][k] for k in ('lp_nh', 'ls_nh', 'qmin', 'k_abs')]
    require(member['actual'] == classified['original']['actual'] == observed, 'physical label mismatch')
    require(classified['original']['strict_valid'] is True and
            classified['original']['below_half_srf'] is True and
            classified['original']['descriptor_valid'] is True and
            classified['original']['physics_qa_pass'] is True, 'strict predicates missing')
    # The existing classifier excludes all 1,408 frozen development128 Q candidates.
    require(evidence['admission_summary']['baseline_geometry_scope']['reserved_rows'] ==
            {'REFERENCE64_ALL11Q_RESERVED': 704, 'DEVELOPMENT6329_128_ALL11Q_RESERVED': 1408},
            'reserved evaluation coverage incomplete')
    return member


def publish(root, evidence, *, atomic_json, lease, verify_sources, utc):
    member = validate(evidence)
    root = Path(root)
    require(root.name == 'qualified15_single_member_v1', 'wrong dataset namespace')
    require(not any(p.is_symlink() for p in (root, *root.parents)), 'symlink destination')
    identity = digest(evidence)
    with lease(root/'WRITE.lock'):
        records = sorted((root/'records').glob('*.json'))
        if records:
            require(len(records) == 1 and records[0].name == '000001.json', 'unexpected ledger extent')
            existing = json.loads(records[0].read_text())
            require(existing['evidence_digest'] == identity and existing['record']['request_id'] == MEMBER_ID,
                    'conflicting prior commit')
            require(existing['checkpoint']['increment_accepted'] == 1, 'corrupt committed count')
            return dict(status='ALREADY_COMMITTED_NO_COUNT_CHANGE', path=str(records[0]),
                        sha256=hashlib.sha256(records[0].read_bytes()).hexdigest(), added=0)
        verify_sources()
        record = dict(request_id=MEMBER_ID, candidate_id=member['candidate_id'],
            candidate_id_sha256=hashlib.sha256(member['candidate_id'].encode()).hexdigest(),
            geometry_sha256=GEOMETRY, geometry=member['geometry'], geometry_fields=member['geometry_fields'],
            geometry_units='um', split='train', source=member['source'],
            frequency_hz=15_000_000_000, physical15=evidence['qualification']['physical15'],
            original56=member['full56_evidence'], increment_sequence=1,
            production_accepted_sequence=None, old_broadband_production_accepted=False,
            eucap15_qualified_accepted=True, original_research_member_unchanged=True,
            scientific_contract_fingerprint=FP)
        commit = dict(schema='eucap15_single_member_qualified_increment.v1',
            status='PASS_CURRENT_CONTRACT_QUALIFIED_INCREMENT_COMMITTED', utc=utc,
            evidence_digest=identity, evidence=evidence, record=record,
            checkpoint=dict(increment_accepted=1, increment_15ghz_rows=1,
                referenced_frequency_rows=56, prior_commit=None, last_increment_sequence=1),
            counting=dict(this_certified_increment=1, old_broadband_added=0,
                prior_research_member_count_changed=False, complete_100k_total=None,
                full_history_certified=False, qualified_union_with6700_not_committed=True),
            limitations=['Uniqueness is certified against the enumerated known pools and this increment.',
                'Historical sources not yet admitted must be deduplicated against this immutable member before any future union.',
                'No exhaustive historical qualification, new gradient row, whole-band strict validity, or independent test claim.',
                'This is not old campaign accepted_sequence 21136 and does not resume that campaign.'])
        path = root/'records/000001.json'
        sha = atomic_json(path, commit, immutable=True)
        restored = json.loads(path.read_text())
        require(restored == commit and hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'readback mismatch')
        return dict(status=commit['status'], path=str(path), sha256=sha, added=1)
