"""Finite continuation of the one boundary installer; current native batch drains."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(sys.argv[1]).resolve()
operation = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root/'runtime'))
import controlled_execution as e
import controlled_metadata as m
import continue_batches as c
import native_birth
import quiescent_handoff as q
import start_slots as s
import waiter_reservation as reservation


def doc(path):
    return e.document(e.pin(path))


def events(old):
    return [json.loads(x) for x in (old/'CONTINUATION_EVENTS.jsonl').read_bytes().splitlines()]


def view(old, identity, rp, owner_identity, metadata_identity):
    p = Path('/proc')/str(identity['pid'])
    actual = native_birth.proc_info(identity['pid'])
    all_processes = {}
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            v = native_birth.proc_info(int(entry.name))
            if v['uid'] == os.getuid() and v['state'] != 'Z':
                all_processes[v['pid']] = v
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    children = [v for v in all_processes.values() if v['ppid'] == identity['pid']]
    allowed = [owner_identity, metadata_identity]
    recognized = all(any(v['pid'] == k['pid'] and v['start_ticks'] == k['start_ticks'] and
                        v['uid'] == k['uid'] for k in allowed) for v in children)
    fds = {v.name:os.readlink(v) for v in (p/'fd').iterdir()}
    safe_paths = {'/dev/null', str(old/'CONTINUATION.log'), str(old/'CONTINUATION.lock')}
    ev = events(old)
    last = ev[-1]
    return dict(process=actual, token=e.pin(old/'CONTINUATION_EVENTS.jsonl'),
        waiting=last['status'] == 'WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT' and last['release'] == rp,
        owner_alive=c.reg.alive(owner_identity), wchan=(p/'wchan').read_text(),
        children_recognized=recognized, children=children, fds=fds,
        fds_safe=set(fds.values()) <= safe_paths and str(old/'CONTINUATION.lock') in fds.values())


def main():
    spec = doc(operation/'SPECIFICATION.json')
    for pin in spec['sources']:
        m.read_pin(pin)
    m.require(doc(operation/'INTEGRATION_TEST.json')['status'] == 'PASS', 'NEW_INTEGRATION_TEST_NOT_PASS')
    prior_terminal = doc(operation/'PREVIOUS_INSTALLER_TERMINAL.json')
    m.require(prior_terminal['status'] == 'WAITING_INSTALLER_CONTROLLED_CLOSE_NO_PRODUCTION_SIGNAL' and
              not c.reg.alive(prior_terminal['previous_identity']), 'PREVIOUS_INSTALLER_STILL_ALIVE')
    staged = doc(root/'STAGED.json')
    for pin in staged['runtime_sources']:
        m.read_pin(pin)
    previous = e.document(staged['previous_deployment'])
    old = Path(staged['previous_chain'])
    start_pin = e.pin(old/'CONTINUATION_START.json')
    identity = e.document(start_pin)['process']
    m.require(e.document(start_pin)['deployment'] == staged['previous_deployment'], 'STANDBY_CHANGED')
    c.verify_deployment(old)
    base_cfg = e.document(e.document(previous['initial_release'])['config'])
    repo = base_cfg['repo']
    if previous.get('endpoint_upgrade'):
        from endpoint_upgrade import load
        repo = load(previous['endpoint_upgrade'])['new_repo']
    sys.path.insert(0, repo)
    from research.broadband56_nn.frequency_research_emx import global_lease
    # Reuse the old installer's strict full-batch / no-descendant checks verbatim.
    module_spec = importlib.util.spec_from_file_location('prior_boundary_installer', root/'install_at_boundary.py')
    prior = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(prior)
    with global_lease(old/'PERMIT_RELEASE_UPGRADE.lock'):
        s.write_once(operation/'START.json', dict(utc=e.now(), process=native_birth.proc_info(os.getpid()),
            previous_installer_terminal=e.pin(operation/'PREVIOUS_INSTALLER_TERMINAL.json'),
            previous_start=start_pin, runtime=e.pin(root/'runtime/fixed48_runtime.py'),
            mode='RESERVE_WAITING_PARENT_DRAIN_EXISTING_NATIVE_BATCH', child_signals=0))
        ev = events(old)
        state = c.replay_waiting_state(previous, ev)
        rp = state['current_release']
        cfg = e.document(e.document(rp)['config'])
        out = Path(cfg['out'])
        owner_identity = doc(out/'START_RECEIPT.json')
        md = next(v for v in reversed(ev) if v['status'] == 'SUCCESSOR_METADATA_READER_STARTED_NOT_A_NATIVE_CONTROLLER' and v['release'] == rp)
        metadata_identity = e.document(md['start'])['process']
        snapshot = lambda:view(old, identity, rp, owner_identity, metadata_identity)
        fd = None
        terminated = False
        try:
            if c.reg.alive(owner_identity):
                fd, evidence = reservation.reserve(identity, snapshot)
                s.write_once(operation/'BOUNDARY_RESERVED.json', dict(utc=e.now(), release=rp,
                    status='ONLY_NEXT_BATCH_PARENT_STOPPED_CURRENT_OWNER_AND_METADATA_UNSIGNALLED',
                    evidence=evidence, owner=owner_identity, metadata=metadata_identity))
            else:
                fd = q.freeze(identity, lambda:prior.processes(identity, cfg))
                m.require(fd is not None, 'OWNER_CLOSED_BUT_NOT_QUIESCENT_NO_BLIND_HANDOFF')
            deadline = time.monotonic() + 3600
            while True:
                m.require(c.replay_waiting_state(previous, events(old)) == state, 'FROZEN_BOUNDARY_CHANGED')
                quiet = prior.processes(identity, cfg)
                if not c.reg.alive(owner_identity) and not c.reg.alive(metadata_identity) and quiet == dict(descendants=[], native=[]):
                    break
                m.require(time.monotonic() < deadline, 'DRAIN_TIMEOUT_RESUME_PREVIOUS_STANDBY')
                time.sleep(5)
            terminal_pin = e.pin(out/'BATCH_RECEIPT.json')
            terminal = e.document(terminal_pin)
            m.require(terminal['release'] == rp and terminal['N_original_requests'] == 256 and
                terminal['status'] == 'ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE', 'EXACT_BATCH_TERMINAL_REQUIRED')
            event_pin = e.pin(old/'CONTINUATION_EVENTS.jsonl')
            for pin in staged['runtime_sources']:
                m.read_pin(pin)
            registry = prior.sync_boundary_registry(old, root)
            registered = e.document(e.document(rp)['successor_registration']['inputs'])
            m.require(c.reg.registered_profile(cfg['original_manifest']) == registered['profile'],
                      'CURRENT_BOUNDARY_REGISTRY_NOT_RESOLVED')
            closure = c.reg.require_parent_terminal(rp)
            m.require(closure['terminal'] == terminal_pin, 'BOUNDARY_TERMINAL_CHANGED')
            s.write_once(root/'BOUNDARY_PREFLIGHT.json', dict(utc=e.now(), status='PASS', previous_start=start_pin,
                previous_deployment=staged['previous_deployment'], events=event_pin, state=state, terminal=terminal_pin,
                processes=quiet, source_tests=staged['tests'], registry=registry, closure=closure,
                reservation_operation=e.pin(operation/'START.json'), native_actions=0))
            q.terminate_frozen(identity, fd)
            fd = None
            terminated = True
            s.write_once(root/'HANDOFF.json', dict(utc=e.now(), status='TERMINAL_BOUNDARY_STANDBY_REPLACED_NO_CHILD_SIGNAL',
                previous_start=start_pin, all_threads_stopped=True, child_signals=0, descendants=[], native=[], current_owner_already_dead=True))
            checkpoint = dict(schema='eucap15_terminal_boundary_continuation_checkpoint.v1', utc=e.now(),
                previous_deployment=staged['previous_deployment'], previous_start=start_pin,
                handoff=e.pin(root/'HANDOFF.json'), events=event_pin, state=state, terminal=terminal_pin)
            s.write_once(root/'CONTINUATION_CHECKPOINT.json', checkpoint)
            deployment = deepcopy(previous)
            deployment.update(utc=e.now(), runtime_sources=staged['runtime_sources'], factory_sources=staged['factory_sources'],
                continuation_checkpoint=e.pin(root/'CONTINUATION_CHECKPOINT.json'))
            s.write_once(root/'DEPLOYMENT.json', deployment)
            m.require(c.continuation_state(c.verify_deployment(root)) ==
                (state['current_release'], state['next_index'], state['prior_batches']), 'READBACK_POSITION_MISMATCH')
            with (root/'CONTINUATION.log').open('xb') as log:
                child = subprocess.Popen([deployment['python'], '-B', str(root/'runtime/continue_batches.py'), str(root)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            s.write_once(root/'CONTINUATION_LAUNCH.json', dict(utc=e.now(), process=native_birth.proc_info(child.pid),
                deployment=e.pin(root/'DEPLOYMENT.json')))
            time.sleep(3)
            m.require(child.poll() is None, 'NEW_STANDBY_EXITED_SEE_LOG')
            s.write_once(root/'INSTALL_RECEIPT.json', dict(status='NEXT_BATCH_RUNTIME_INSTALLED_NOT_NATIVE_CONCURRENCY_PROOF',
                utc=e.now(), previous_start=start_pin, checkpoint=e.pin(root/'CONTINUATION_CHECKPOINT.json'),
                deployment=e.pin(root/'DEPLOYMENT.json'), standby=native_birth.proc_info(child.pid),
                runtime=e.pin(root/'runtime/fixed48_runtime.py'), publication=e.pin(root/'PUBLICATION.json'),
                healthy_native_signals=0, previous_batch_restarted=False, next_index=state['next_index'],
                installer_continuation=e.pin(operation/'START.json')))
            s.write_once(operation/'TERMINAL.json', dict(status='INSTALLED', utc=e.now(), receipt=e.pin(root/'INSTALL_RECEIPT.json')))
        finally:
            if fd is not None and not terminated:
                q.resume(fd)
                s.write_once(operation/'RESUMED_PREVIOUS_PARENT.json', dict(utc=e.now(), status='PRIOR_STANDBY_RESUMED', child_signals=0))


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise RuntimeError('INSTALLER_INTERRUPTED_' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        main()
    except BaseException as error:
        if not (operation/'TERMINAL.json').exists():
            s.write_once(operation/'TERMINAL.json', dict(utc=e.now(), status='CONTINUATION_NOT_COMPLETE',
                error=type(error).__name__ + ': ' + str(error), child_signals=0))
        raise
