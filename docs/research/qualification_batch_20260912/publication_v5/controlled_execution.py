"""Budget/native-start binding installed in the existing single-owner solve path."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

import controlled_metadata as m
import native_birth
import native_resource_probe
import start_slots as slots


def pin(path):
    p = Path(path)
    raw = p.read_bytes()
    return dict(path=str(p), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def document(identity):
    return m.parse(m.read_pin(identity))


def now():
    return datetime.now(timezone.utc).isoformat()


def plan_release(release, config):
    """Validate one tested recovery while retaining the original budget origin."""
    if release.get('concurrency_amendment') is not None:
        from fixed48_amendment import validate
        a=release['concurrency_amendment']
        parent=document(a['parent_release']);old=document(parent['config'])
        origin=plan_release(parent,old) or a['parent_release']
        return validate(release,config,document,pin,origin)
    recovery = release.get('startup_recovery')
    if recovery is None:
        return None
    m.require(config.get('startup_recovery') == recovery, 'RECOVERY_CONFIG_BINDING')
    parent = document(recovery['parent_release'])
    old = document(parent['config'])
    stop = document(recovery['stop_receipt'])
    quiet = document(recovery['quiescent_receipt'])
    start = document(recovery['parent_start'])
    m.require(stop['status'] == 'DEFECTIVE_DISPATCHER_STOPPED_NO_CHILD_SIGNAL' and
        stop['child_signals'] == stop['healthy_solver_kills'] == 0 and
        stop['result_pins_unchanged'] is True and stop['plan_unchanged'] is True and
        stop['pid'] == start['pid'] and stop['start_ticks'] == int(start['start_ticks']) and
        start['release'] == recovery['parent_release'], 'RECOVERY_STOP_NOT_BOUND')
    m.require(quiet['processes'] == {'descendants': [], 'native': []} and
        quiet['plan'] == recovery['original_plan'], 'RECOVERY_QUIESCENCE_OR_PLAN_CHANGED')
    for p in quiet['results']:
        m.read_pin(p)
    original = document(recovery['original_plan'])
    m.require(original['release'] == recovery['parent_release'] and
        Path(recovery['original_plan']['path']) == Path(old['budget_root'])/'start_ledger/PLAN.json',
        'RECOVERY_MUST_RETAIN_ORIGINAL_PLAN')
    m.require(config['budget_root'] == old['budget_root'] and
        Path(config['out']).parent.is_relative_to(Path(old['budget_root'])/'recoveries'), 'RECOVERY_BUDGET_ROOT')
    # Replace only declared source identities and relocated executable paths.
    replacements = {p['new']['path']: p['parent'] for p in release['endpoint_sources'] if p['parent'] is not None}
    def normalize(v):
        if isinstance(v, dict):
            if v.get('path') in replacements and set(v) == {'path','sha256','bytes'}:
                m.require(v == pin(v['path']), 'REPLACEMENT_IDENTITY_DRIFT')
                return replacements[v['path']]
            return {k: normalize(x) for k, x in v.items()}
        if isinstance(v, list): return [normalize(x) for x in v]
        if isinstance(v, str):
            for current, previous in ((config['repo'], old['repo']), (config['code_root'], old['code_root']), (config['out'], old['out'])):
                if v == current or v.startswith(current+'/'): return previous+v[len(current):]
        return v
    excluded = {'source_pins', 'startup_recovery'}
    m.require({k:normalize(v) for k,v in config.items() if k not in excluded} ==
        {k:v for k,v in old.items() if k not in excluded}, 'RECOVERY_CHANGED_NON_PATH_CONTRACT')
    return recovery['parent_release']


def allocated_bytes(root):
    """Count allocated bytes once per inode, including logs and in-flight files."""
    root = slots.path_ok(root)
    seen = set()
    total = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        for p in [Path(parent), *[Path(parent)/n for n in files+directories]]:
            m.require(not p.is_symlink(), 'BUDGET_TREE_SYMLINK_FORBIDDEN')
            try:
                s = p.stat()
            except FileNotFoundError:
                continue
            key = (s.st_dev, s.st_ino)
            if key not in seen:
                seen.add(key)
                total += s.st_blocks * 512
    return total


def load_execution(binding, batch):
    release = document(binding['release'])
    m.require(release['schema'] == 'eucap15_production256_delegated_release.v1' and
        release['authority']['type'] == 'PROJECT_OWNER_STANDING_AUTHORIZATION' and
        release['authority']['scope'] == '15GHZ_EUCAP_NATIVE_ONLY_NO_NN', 'DELEGATED_SCOPE_REQUIRED')
    config = document(release['config'])
    m.require(config['original_manifest'] == batch.manifest_pin and
        config['max_native_concurrency'] == (48 if release.get('concurrency_amendment') else 1)
        and config['resource_budget']['cpu_per_solver'] == 2,
        'UNCHANGED_FROZEN_BATCH_AND_SERIAL_NATIVE_BUDGET_REQUIRED')
    source = pin(__file__)
    m.require(source in release['sources'], 'WRONG_EXECUTION_IMPLEMENTATION')
    for identity in release['sources']:
        m.read_pin(identity)
    budget_root = slots.path_ok(config['budget_root'])
    ledger_root = slots.path_ok(binding['ledger_root'])
    m.require(ledger_root == budget_root/'start_ledger' and
        Path(config['out']).is_relative_to(budget_root), 'WRONG_BUDGET_ROOT')
    plan = document(binding['plan'])
    origin = plan_release(release, config) or binding['release']
    expected = slots.plan(origin, plan['admitted_at_utc'], plan['deadline_utc'],
        batch.rows, evidence_class='DELEGATED_NATIVE_RELEASE')
    m.require(plan == expected and Path(binding['plan']['path']) == ledger_root/'PLAN.json',
        'PLAN_DIFFERS_FROM_RELEASE_AND_ORIGINAL256')
    m.read_pin(config['emx_runtime']['native_executable'])
    return release, config, plan, slots.SlotBook(ledger_root, plan)


def budget_state(config, plan, records, row, observed_utc=None):
    at = datetime.fromisoformat(observed_utc or now())
    used = allocated_bytes(config['budget_root'])
    # All remaining headroom is reserved to the single in-flight candidate;
    # this is a reservation, not a claim about historical peak disk demand.
    headroom = max(0, plan['incremental_storage_max_bytes'] - used)
    status = 'ELIGIBLE_WITHIN_BUDGET'
    if at >= datetime.fromisoformat(plan['deadline_utc']):
        status = 'NOT_DISPATCHED_DEADLINE'
    elif used >= plan['incremental_storage_max_bytes']:
        status = 'NOT_DISPATCHED_STORAGE_CAP'
    elif any(r['candidate_id'] == row['candidate_id'] for r in records):
        status = 'EXISTING_SLOT_NO_AUTOMATIC_REDISPATCH'
    elif len(records) >= plan['total_max'] or sum(r['arm'] == row['arm'] for r in records) >= plan['per_arm_max']:
        status = 'NOT_DISPATCHED_ARM_START_CAP'
    extra={}
    if config.get('concurrency_amendment'):
        pointer=Path(config['out'])/'fixed48/LATEST_STORAGE.json'
        if pointer.exists():
            identity=document(pin(pointer));state=document(identity)
            m.require(state['ceiling_bytes']==plan['incremental_storage_max_bytes'] and
                state['budget_root']==config['budget_root'],'FIXED48_STORAGE_BUDGET_CHANGED')
            claims=state['claims'];own=claims.get(row['request_id'],{}).get('remaining',0)
            other=sum(v['remaining'] for k,v in claims.items() if k!=row['request_id'])
            extra=dict(inflight_storage=identity,projected_own_remaining_bytes=own,
                concurrent_other_reservations_bytes=other,reservation_for_single_inflight_bytes=0,
                storage_projection_is_not_a_hard_peak_bound=True)
            if status=='ELIGIBLE_WITHIN_BUDGET' and used+own+other>plan['incremental_storage_max_bytes']:
                status='RESOURCE_WAIT_PROJECTED_STORAGE'
    value=dict(schema='eucap15_production256_budget_decision.v1', status=status, observed_utc=at.isoformat(),
        allocated_incremental_bytes=used, remaining_headroom_bytes=headroom,
        reservation_for_single_inflight_bytes=headroom, concurrent_other_reservations_bytes=0,
        includes_temporary_logs_metadata_and_inflight=True, empirical_peak_bound='UNKNOWN_NOT_ASSUMED',
        candidate_id=row['candidate_id'], plan_sha256=slots.digest(plan))
    value.update(extra)
    return value


def prepare_native_dispatch(request, proof, output, lease_fd):
    """Runs before the subprocess interception, avoiding license-query recursion."""
    from research.broadband56_nn.frequency_research_emx import global_lease, ResearchEmxError
    output = slots.path_ok(output)
    binding = request['controlled_execution']
    batch = m.load_batch(request['controlled_manifest'], request.get('path_map'))
    release, config, plan, book = load_execution(binding, batch)
    row = batch.candidate(request['request_id'])['original']
    m.require(row['request_id'] not in config['intake_duplicate_ids'], 'INTAKE_DUPLICATE_NO_NATIVE_DISPATCH')
    m.require(output == Path(config['out'])/row['request_id']/'emx_selected', 'WRONG_NATIVE_OUTPUT')
    m.require(proof['candidate_id'] == row['candidate_id'] and
        proof['geometry_sha256'] == row['canonical_geometry_sha256'], 'WRONG_NATIVE_CANDIDATE')
    with global_lease(config['global_lock_path'], lease_fd):
        if config.get('concurrency_amendment'):
            from fixed48_runtime import verify_emx_permit
            permit=verify_emx_permit(config,binding['release'],row)
            resources=document(permit['resource'])
        else:
            resources = native_resource_probe.probe(config)
    resource_path = output.parent/('emx_DISPATCH_RESOURCE_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    slots.write_once(resource_path, resources)
    if resources['status'] != 'PASS':
        raise ResearchEmxError('RESOURCE_WAIT_REQUIRED_NO_DISPATCH')
    with book.locked():
        decision = budget_state(config, plan, book.records(), row)
    decision_path = resource_path.with_name(resource_path.stem+'_BUDGET.json')
    slots.write_once(decision_path, decision)
    if decision['status']=='RESOURCE_WAIT_PROJECTED_STORAGE':
        raise ResearchEmxError('RESOURCE_WAIT_REQUIRED_NO_DISPATCH')
    m.require(decision['status'] == 'ELIGIBLE_WITHIN_BUDGET', decision['status'])
    preflight_pin = pin(output/'PREFLIGHT.json')
    called = False

    def invoke(command, kwargs):
        nonlocal called
        m.require(not called and command == proof['command'], 'ONE_EXACT_INVOCATION_ONLY')
        called = True
        m.read_pin(preflight_pin)
        m.read_pin(proof['gds'])
        m.read_pin(config['emx_runtime']['emx_wrapper'])
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(resources['utc'])).total_seconds()
        m.require(0 <= age <= 90, 'STALE_NATIVE_ADMISSION')
        reservation = book.reserve(row['candidate_id'], preflight_pin, proof['gds'], command,
            now(), allocated_bytes(config['budget_root']))
        m.require(reservation['created'] is True, reservation['status'])
        return native_birth.observe_invocation(command, kwargs,
            expected_executable=config['emx_runtime']['native_executable'], slot=reservation['slot'],
            output=output/'solve'/'native_observation', plan_sha256=slots.digest(plan))
    return invoke


def full_start_capture(binding, batch, out):
    """Fixed-cutoff census of declared slots and births, including unclosed jobs."""
    _, config, plan, book = load_execution(binding, batch)
    capture_started = now()
    rows = []
    with book.locked():
        records = book.records()
        for slot in records:
            rid=slot['candidate']['request_id']
            root = Path(config.get('candidate_roots',{}).get(rid,str(Path(config['out'])/rid)))/'emx_selected/solve/native_observation'
            births = []
            if root.is_dir():
                for path in sorted(root.glob('NATIVE_BIRTH_*.json')):
                    identity = pin(path)
                    birth = document(identity)
                    m.require(birth['plan_sha256'] == slots.digest(plan) and
                        birth['launch_binding'] == slot['launch_binding'] and
                        birth['candidate'] == slot['candidate'], 'FOREIGN_NATIVE_BIRTH')
                    m.require(birth['expected_executable'] == config['emx_runtime']['native_executable'],
                        'NATIVE_EXECUTABLE_CHANGED')
                    processes = {p['pid']: p for p in birth['ancestry']}
                    m.require(native_birth.matches_native(birth['process'], birth['ancestor'], processes,
                        birth['command'], birth['executable_sha256'],
                        config['emx_runtime']['native_executable']['sha256'], birth['ancestor']['uid']),
                        'UNPROVEN_NATIVE_BIRTH')
                    births.append(dict(pin=identity, birth=birth))
            rows.append(dict(slot=slot, births=births, actual_native_starts=len(births) if births else None,
                reservation_is_not_native_start=True, result_closed=(root.parent.parent.parent/'RESULT.json').is_file()))
    all_births = [b for r in rows for b in r['births']]
    ordered = sorted(all_births, key=lambda b:b['birth']['process']['start_ticks'])
    ticks = [b['birth']['process']['start_ticks'] for b in ordered]
    boots = {b['birth'].get('boot_id') for b in all_births}
    unambiguous = (len(set(ticks)) == len(ticks) and
        all(len(r['births']) == 1 for r in rows) and
        (not all_births or (len(boots) == 1 and None not in boots)))
    ledger = dict(schema='eucap15_production256_full_native_start_capture.v1',
        capture_started_utc=capture_started, cutoff_utc=now(), original_denominator=m.PROPOSAL_COUNT,
        plan=binding['plan'], release=binding['release'], rows=rows,
        all_reserved_candidates_included=True, unclosed_births_included=True,
        actual_native_starts=len(all_births) if unambiguous else None,
        observed_native_start_lower_bound=len(all_births),
        actual_start_order=[dict(order=i+1, **b) for i,b in enumerate(ordered)] if unambiguous else None,
        order_basis='LINUX_PROC_START_TICKS_NOT_FROZEN_PROPOSAL_ORDER',
        boot_ids=sorted(b for b in boots if b is not None),
        unresolved_reservations=[r['slot']['candidate_id'] for r in rows if len(r['births']) != 1])
    slots.write_once(out, ledger)
    return pin(out)
