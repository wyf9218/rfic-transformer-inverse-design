"""Normal startup producer fixtures; no real lease, approval or simulator action."""
import copy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_handoff as cp
from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_startup as startup
from tests.test_broadband56_checkpoint_handoff import checkpoint_fixture, process_fixture
from tests.test_broadband56_scheduling import write


def load_script(path):
    spec = importlib.util.spec_from_file_location('checkpoint_fixture_'+Path(path).stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixtureIsolation:
    LEASE_SCHEMA = 'rfic_transformer.broadband56_v2_supervisor_lease.v1'
    LEASE_VALIDITY_MODEL = 'PID_UID_START_TICKS_BOOT_ID_CMDLINE_EXECUTABLE'
    SUPERVISOR_MARKERS = ('launch_broadband56_v2_supervisor',)
    RUNNER_MARKERS = ('fixture_runner',)
    CADENCE_MARKERS = ('fixture_cadence',)
    CALIBRE_MARKERS = ('fixture_calibre',)
    EMX_MARKERS = ('fixture_emx',)

    def __init__(self, current, prior):
        self.current, self.prior, self.old_alive, self.child = current, prior, False, False

    def read_process_identity(self, pid):
        if pid == self.current['pid']:
            return copy.deepcopy(self.current)
        if pid == self.prior['pid'] and self.old_alive:
            return copy.deepcopy(self.prior)

    def enumerate_owner_processes(self, uid, probe_pid):
        result = [dict(pid=self.current['pid'], command_text='launch_broadband56_v2_supervisor_fixture.py')]
        if self.child:
            result.append(dict(pid=333, command_text='fixture_emx'))
        return result

    _campaign_process = staticmethod(lambda text: True)
    _contains_marker = staticmethod(lambda text, markers: any(m in text for m in markers))
    _public_process_record = staticmethod(lambda value: dict(value))


class FixtureExecutor:
    IMMUTABLE_ARTIFACTS = cp.CONTROL_ROOT_FILES[:10]
    HANDOFF_SCHEMA = 'rfic_transformer.broadband56_v2_swap_policy_supervisor_handoff.v1'
    HANDOFF_DECISION = 'HANDOFF_SAME_LOGICAL_SUPERVISOR_FOR_SWAP_POLICY_OVERLAY'
    utc_now = staticmethod(lambda: '2026-09-06T00:00:00+00:00')
    parse_utc = staticmethod(lambda value: datetime.fromisoformat(value))

    @staticmethod
    def validate_static_candidate(candidate):
        for group in ('bound_files', 'runtime_files'):
            for record in candidate[group].values():
                cp.bound(record)

    @staticmethod
    def project_roles(module, include_self):
        return dict(supervisors=[os.getpid()], runners=[], cadence=[], calibre=[], emx=[333] if module.child else [])

    @staticmethod
    def create_rebind_controls(**kw):
        c, op, root = kw['candidate'], kw['operation_root'], kw['successor_root']
        rebind = write(op/'QUEUE_BACKEND_REBIND_RECEIPT.json', dict(overall_status='PASS',
            new_backend_manifest=cp.pin(kw['backend_path'])))
        auth = cp.read(cp.bound(c['bound_files']['existing_full_campaign_receipt']))
        auth['backend_identity_manifest'] = cp.pin(kw['backend_path'])
        composite = write(op/'COMPOSITE_FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json', auth)
        write(root/'FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json', auth)
        return rebind, composite

    @staticmethod
    def controller_argv(**kw):
        return ['--campaign-root', str(kw['successor_root']), '--post-rebind-execution-gate', str(kw['post_gate']), '--resume']

    @staticmethod
    def write_json_atomic(path, value):
        temporary = path.with_suffix('.fixture-tmp')
        write(temporary, value)
        os.replace(temporary, path)


@pytest.fixture
def fixture_executor():
    # On MARS use the pinned deployed outer executor, not a rewritten substitute.
    path = os.environ.get('B56_TEST_LEGACY_EXECUTOR')
    if path:
        record = cp.pin(path)
        assert record['sha256'] == os.environ['B56_TEST_LEGACY_EXECUTOR_SHA256']
        return load_script(path)
    return FixtureExecutor()


@pytest.fixture
def context(tmp_path, fixture_executor):
    root, attempt, backend, auth = checkpoint_fixture(tmp_path, cp, 861)
    for name in cp.CONTROL_ROOT_FILES[:6]:
        write(root/name, dict(fixture_only=True, contract_name=name))
    dummy = cp.pin(write(tmp_path/'bound-fixture.json', dict(fixture_only=True, old_process_pid=17)))
    old_queue = cp.pin(write(root/'MARS_QUEUE_ENTRY.json', dict(campaign_id=cp.CAMPAIGN_ID,
        queue_id=cp.QUEUE_ID, contract_fingerprint_sha256=cp.SCIENTIFIC_CONTRACT_FINGERPRINT,
        candidate=dummy, backend_identity_manifest=backend)))
    for name in ('MARS_QUEUE_RECEIPT.json', 'SUPERVISOR_IDENTITY.json', 'CAMPAIGN_LOCK.json'):
        write(root/name, dict(fixture_only=True, control_name=name))
    (root/'SHA256SUMS.txt').write_text(''.join(cp.pin(root/name)['sha256']+'  '+name+'\n'
        for name in fixture_executor.IMMUTABLE_ARTIFACTS))
    lock = tmp_path/'global.lock'
    lock.write_text(cp.SUPERVISOR_ID+'\n')
    fd = os.open(lock, os.O_RDWR)
    old_process, current = process_fixture(103), process_fixture(os.getpid())
    isolation = FixtureIsolation(current, old_process)
    prior_lease = cp.pin(write(tmp_path/'prior_lease.json', dict(
        schema=isolation.LEASE_SCHEMA, validity_model=isolation.LEASE_VALIDITY_MODEL,
        campaign_id=cp.CAMPAIGN_ID, queue_id=cp.QUEUE_ID, logical_supervisor_id=cp.SUPERVISOR_ID,
        physical_process=old_process, lease_generation=28, backend_identity_manifest=backend,
        campaign_lock=dict(path=str(lock), expected_contents=cp.SUPERVISOR_ID, exclusive_flock_required=True))))
    proof = cp.committed_boundary(root, attempt, backend=backend, authorization=auth)
    boundary = cp.pin(write(tmp_path/'boundary.json', proof))
    target_backend = cp.pin(write(tmp_path/'new_backend.json', cp.read(cp.bound(backend))))
    template = cp.read(cp.bound(proof['source_stages'][0]))
    template['operational_progress_rebind'] = dict(previous_rebound_receipt=proof['source_stages'][0])
    golden = cp.pin(write(tmp_path/'golden_template.json', template))
    overlay = cp.pin(write(tmp_path/'old_overlay.json', dict(script_identities={},
        supervisor_recovery_handoffs=[dummy], failure_receipt=dummy, fixture_only=True)))
    bound_names = ('original_full_campaign_backend', 'corrected_layout_approval',
        'corrected_private_configuration', 'capacity_compat_rebind_plan', 'new_backend_verification',
        'rebound_helper', 'base_rebound_controller', 'base_resource_gate_auditor', 'swap_override_receipt',
        'previous_operational_handoff', 'isolation_hotfix_handoff', 'inner_supervisor_handoff',
        'delegate_controller', 'frozen_contract', 'preparation_receipt', 'policy_approval_receipt',
        'full_campaign_candidate', 'resource_probe', 'private_python')
    files = dict.fromkeys(bound_names, dummy)
    files.update(source_backend=backend, source_authorization=auth, current_queue_entry=old_queue,
        new_backend_manifest=target_backend, existing_full_campaign_receipt=auth,
        prior_supervisor_lease=prior_lease, golden_reuse_template=golden, current_operational_overlay=overlay)
    runtime = dict.fromkeys(('stage_launcher', 'queue_controller', 'swap_policy_module',
        'swap_resource_gate_auditor', 'isolation_identity_auditor', 'isolation_identity_module',
        'capacity_policy_module', 'capacity_schema_adapter'), dummy)
    candidate = dict(authorization_scope=startup.SCOPE, campaign_id=cp.CAMPAIGN_ID, queue_id=cp.QUEUE_ID,
        logical_supervisor_id=cp.SUPERVISOR_ID, contract_fingerprint_sha256=cp.SCIENTIFIC_CONTRACT_FINGERPRINT,
        scientific_contract_changed=False, nn_training_authorized=False, execution_authorized=False,
        generated_utc='2026-09-05T00:00:00+00:00', fixed_generation_policy=startup.FIXED48_GENERATION_POLICY,
        current_campaign_root=str(root), authorized_successor_root_prefix=str(tmp_path/'successor_'),
        global_lock_path=str(lock), predecessor_physical_pid=103, predecessor_lease_generation=28,
        next_lease_generation=29, bound_files=files, runtime_files=runtime, prior_recovery_handoffs=[dummy],
        source_schema='rfic_transformer.broadband56_v2_swap_operational_override_snapshot.v1',
        target_schema='rfic_transformer.broadband56_v2_capacity_resource_snapshot.v1',
        adapter_profile='SWAP_OVERRIDE_V1_TO_CAPACITY_RESOURCE_V1_STRICT_ADAPTER', maximum_snapshot_age_seconds=300)
    cpin = cp.pin(write(tmp_path/'candidate.json', candidate))
    approval = dict(overall_status='PASS', decision='APPROVE_'+startup.SCOPE,
        authorization_scope=startup.SCOPE, approved_candidate=cpin,
        approved_by='Yufeng Wang, project owner and project leader',
        approved_utc='2026-09-05T00:01:00+00:00', approval_reference='fixture only '+cpin['sha256'],
        nn_training_authorized=False, new_campaign_queue_or_logical_supervisor_authorized=False)
    apin = cp.pin(write(tmp_path/'approval.json', approval))
    value = dict(executor=fixture_executor, source=root, candidate=candidate,
        kwargs=dict(candidate_record=cpin, approval_record=apin, boundary_record=boundary,
            operation_root=tmp_path/'operation', successor_root=tmp_path/'successor_fixture',
            isolation=isolation, lock_fd=fd))
    try:
        yield value
    finally:
        os.close(fd)


def prepare(context):
    return startup.prepare_controls(context['executor'], **context['kwargs'])


def test_real_producer_order_migrates_into_new_control_envelope(context):
    before = {str(p): p.read_bytes() for p in context['source'].rglob('*') if p.is_file()}
    result = prepare(context)
    assert result['state']['current_accepted'] == 861
    assert result['state']['current_stage'] == 'PILOT_1000'
    assert cp.read(cp.bound(result['migration']))['accepted_preserved'] == 861
    lease, handoff = cp.read(cp.bound(result['lease'])), cp.read(cp.bound(result['handoff']))
    assert 'restart_failure_receipt' not in lease and 'restart_failure_receipt' not in handoff
    assert lease['lease_generation'] == 29
    assert handoff['resume_stage'] == 'PILOT_1000'
    assert cp.validate_checkpoint_handoff(handoff) == result['state']
    wrapper = load_script(Path(__file__).parents[1]/'scripts/run_broadband56_v2_swap_override_queue_controller.py')
    snapshot = write(Path(result['operation_root'])/'snapshot.json', dict(fixture_only=True))
    gate = write(Path(result['operation_root'])/'gate.json', dict(overall_status='PASS',
        current_stage='PILOT_1000', current_accepted=861, active_simulator_jobs=0))
    post, argv = startup.finalize_controls(context['executor'], result, snapshot=snapshot, gate=gate)
    wrapper._validate_checkpoint_resume_binding(handoff, handoff_record=result['handoff'],
        post_gate=cp.read(cp.bound(post)), lease=lease)
    assert '--resume' in argv and str(cp.bound(post)) in argv
    assert before == {str(p): p.read_bytes() for p in context['source'].rglob('*') if p.is_file()}
    controller = load_script(Path(__file__).parents[1]/'scripts/run_broadband56_v2_authorized_queue_controller.py')
    assert controller._validate_resume_root(Path(result['root']), evidence=dict(
        candidate=context['candidate']['bound_files']['full_campaign_candidate'],
        backend_identity_manifest=result['backend'],
        backend_identity_verification_receipt=context['candidate']['bound_files']['new_backend_verification'])) == (cp.QUEUE_ID, cp.SUPERVISOR_ID)
    with pytest.raises(FileExistsError):
        prepare(context)


@pytest.mark.parametrize('bad', ['old_alive', 'child', 'wrong_python', 'wrong_lock', 'old_count', 'uncommitted', 'approval'])
def test_invalid_startup_is_rejected_before_any_control_write(context, bad):
    kw = context['kwargs']
    if bad == 'old_alive': kw['isolation'].old_alive = True
    elif bad == 'child': kw['isolation'].child = True
    elif bad == 'wrong_python': kw['isolation'].current['executable_sha256'] = 'c'*64
    elif bad == 'wrong_lock': Path(context['candidate']['global_lock_path']).write_text('different owner')
    elif bad in ('old_count', 'uncommitted'):
        path = context['source']/'CAMPAIGN_STATUS.json'
        value = cp.read(path)
        value['current_accepted' if bad == 'old_count' else 'overall_status'] = 100 if bad == 'old_count' else 'PILOT_1000_RUNNING'
        write(path, value)
    else:
        path = cp.bound(kw['approval_record']); value = cp.read(path)
        value['approved_candidate']['sha256'] = 'e'*64
        write(path, value); kw['approval_record'] = cp.pin(path)
    with pytest.raises((ValueError, OSError)):
        prepare(context)
    assert not kw['operation_root'].exists()
    assert not kw['successor_root'].exists()


@pytest.mark.parametrize('bad', ['extra_file', 'existing_stage', 'control_drift', 'wrong_backend'])
def test_control_envelope_rejects_existing_or_unbound_output(context, monkeypatch, bad):
    original = startup.checkpoint.migrate_boundary
    def tampered(proof, **kw):
        root = Path(kw['target_root'])
        if bad == 'extra_file': (root/'unbound.json').write_text('{}')
        elif bad == 'existing_stage': (root/'stages/already-running').mkdir()
        elif bad == 'control_drift': (root/'FREQUENCY_CONTRACT.json').write_text('{}')
        else: kw['target_backend'] = context['candidate']['bound_files']['source_backend']
        return original(proof, **kw)
    monkeypatch.setattr(startup.checkpoint, 'migrate_boundary', tampered)
    with pytest.raises(ValueError):
        prepare(context)
    assert (context['kwargs']['operation_root']/'NORMAL_CHECKPOINT_STARTUP_FAILURE.json').is_file()
    assert not (context['kwargs']['operation_root']/'supervisor_leases').exists()


def test_source_control_checksum_drift_cannot_be_reindexed_as_valid(context):
    (context['source']/'FREQUENCY_CONTRACT.json').write_text('{"changed":true}')
    with pytest.raises(ValueError, match='source immutable control artifact changed'):
        prepare(context)
    assert not context['kwargs']['successor_root'].exists()


@pytest.mark.parametrize('real_policy', [False, True])
def test_prelaunch_five_probes_keep_indexes_and_same_live_hook(context, monkeypatch, real_policy):
    from rfic_transformer_inverse_design.campaigns import broadband56_capacity_policy as base_policy
    from rfic_transformer_inverse_design.campaigns import broadband56_swap_override_policy as swap_policy
    from tests.test_broadband56_swap_override_policy import _snapshot
    prepared = prepare(context)
    calls, decisions = [], []
    root = Path(prepared['root'])
    def factory(*args, **kwargs):
        assert wrapper._measured_capacity_hook_installed
        assert kwargs['isolation_lease_generation'] == 29
        def probe(script, directory, index):
            calls.append(index)
            return write(directory/f'fixture_{index}.json', _snapshot() if real_policy else dict(fixture_only=True))
        return probe
    def resource_gate(**kw):
        assert kw['current_accepted'] == 861 and kw['stage'] == 'PILOT_1000'
        return write(root/'resource_gates'/f"fixture_{kw['check_index']}.json", dict(
            overall_status='PASS', current_stage='PILOT_1000', current_accepted=861, active_simulator_jobs=0))
    def decision(**kw):
        import inspect
        inspect.signature(original_decision).bind(**kw)
        decisions.append(kw['snapshot_path'])
        assert kw['current_accepted'] == 861 and kw['stage'] == 'PILOT_1000'
        if real_policy:
            assert kw['policy']['pass'] is True
            assert kw['legacy_policy'] is swap_policy.adaptive_concurrency
        return dict(concurrency=12 if len(decisions) == 5 else 0, requested_concurrency=48)
    def main(argv):
        assert wrapper._measured_capacity_hook_installed
        assert cp.read(root/'CAMPAIGN_STATUS.json')['check_index'] == 45
        assert len(decisions) == 5
        assert '--resume' in argv
        if real_policy:
            assert controller.evaluate_capacity_snapshot is swap_policy.evaluate_capacity_snapshot
        return 0
    wrapper = SimpleNamespace(_swap_override_probe_factory=factory, main=main,
        OVERRIDE_PATH_ENV='B56_TEST_OVERRIDE', OVERRIDE_SHA_ENV='B56_TEST_OVERRIDE_SHA',
        OVERLAY_PATH_ENV='B56_TEST_OVERLAY', OVERLAY_SHA_ENV='B56_TEST_OVERLAY_SHA',
        swap_policy=swap_policy if real_policy else SimpleNamespace(
            evaluate_capacity_snapshot=lambda *a, **k: {}, adaptive_concurrency=None))
    controller = SimpleNamespace(_run_probe=None, _write_resource_gate=resource_gate,
        _pilot_bytes_per_geometry=lambda root: None, _pilot_safe_concurrency=lambda root: None,
        evaluate_capacity_snapshot=base_policy.evaluate_capacity_snapshot if real_policy else lambda *a, **k: {},
        adaptive_concurrency=base_policy.adaptive_concurrency if real_policy else None)
    hook = SimpleNamespace(install=lambda wrapper, binding: setattr(wrapper, '_measured_capacity_hook_installed', True))
    original_decision = startup.concurrency_for_snapshot
    monkeypatch.setattr(startup, 'concurrency_for_snapshot', decision)
    monkeypatch.setattr(startup.time, 'sleep', lambda seconds: None)
    assert startup.run_controller(context['executor'], prepared, wrapper=wrapper, controller=controller,
        capacity_hook=hook, capacity_binding={}, lock_fd=context['kwargs']['lock_fd']) == 0
    assert calls == [41, 42, 43, 44, 45]
    assert cp.read(root/'CAMPAIGN_STATUS.json')['current_accepted'] == 861


def launcher():
    return load_script(Path(__file__).parents[1]/'scripts/launch_broadband56_v2_supervisor_recovery_checkpoint.py')


def test_launcher_uses_existing_owner_marker_without_isolation_change():
    from rfic_transformer_inverse_design.campaigns import broadband56_isolation_identity as iso
    entry = launcher()
    assert iso._contains_marker(entry.__file__, iso.SUPERVISOR_MARKERS)


def test_launcher_rejects_wrong_candidate_sha_before_bootstrap(context, monkeypatch, capsys):
    entry = launcher()
    def forbidden(record):
        raise AssertionError('wrong candidate must not reach bootstrap')
    monkeypatch.setattr(entry, 'bootstrap', forbidden)
    assert entry.main(['--mode','launch','--candidate',context['kwargs']['candidate_record']['path'],
        '--candidate-sha256','0'*64]) == 2
    assert 'candidate SHA-256 mismatch' in capsys.readouterr().err
    assert not context['kwargs']['operation_root'].exists()


@pytest.mark.parametrize('mode', ['preflight', 'launch'])
def test_launcher_preflight_is_not_authority_and_launch_needs_approval(context, monkeypatch, capsys, mode):
    entry = launcher()
    monkeypatch.setattr(entry, 'bootstrap', lambda record:
        (context['candidate'],context['executor'],startup,context['kwargs']['isolation'],None,None,None,None))
    args = ['--mode',mode,'--candidate',context['kwargs']['candidate_record']['path'],
        '--candidate-sha256',context['kwargs']['candidate_record']['sha256']]
    assert entry.main(args) == (0 if mode == 'preflight' else 2)
    captured = capsys.readouterr()
    if mode == 'preflight':
        value = json.loads(captured.out)
        assert value['overall_status'] == 'PASS_IMPORTS_ONLY_NOT_LAUNCH_AUTHORITY'
        assert not value['controller_main_called'] and not value['lease_created']
    else:
        assert 'launch requires exact approval' in captured.err
    assert not context['kwargs']['operation_root'].exists()
