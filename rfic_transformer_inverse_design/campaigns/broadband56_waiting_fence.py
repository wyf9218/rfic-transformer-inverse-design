"""PIDFD-fenced interruption of the verified zero-dispatch project control tree.

No supervisor or solver is launched here. Only the four proven Python control
processes may receive signals; native solvers and unrelated processes never do.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import time

from . import broadband56_checkpoint_handoff as cp
from . import broadband56_isolation_identity as iso
from .broadband56_running_isolation import running_stage_children
from .broadband56_waiting_recovery import validate_prefix, write, INTERRUPTION_SCHEMA


def descendants(owner_pid, proc_root=Path('/proc')):
    parents, states = {}, {}
    for path in proc_root.iterdir():
        if path.name.isdigit():
            try:
                text = (path/'stat').read_text()
                rest = text[text.rfind(')')+2:].split()
                parents[int(path.name)] = int(rest[1])
                states[int(path.name)] = rest[0]
            except (OSError, ValueError, IndexError):
                pass
    found = {owner_pid}
    while True:
        grown = found | {pid for pid, parent in parents.items() if parent in found}
        if grown == found:
            # A stopped parent cannot reap its completed sampler until resumed.
            return sorted(pid for pid in found if states.get(pid) not in ('Z', 'X'))
        found = grown


def verify_control_tree(*, prefix, lease, history):
    processes = iso.enumerate_owner_processes(lease['physical_process']['uid'], probe_pid=os.getpid(), include_arguments=True)
    owner = iso.read_process_identity(lease['physical_process']['pid'])
    if owner is None or not iso.process_identity_matches(owner, lease['physical_process']):
        raise ValueError('interruption owner process identity differs')
    binding = running_stage_children(history_path=history, lease=lease, processes=processes)
    controls = [p for p in processes if p['pid'] in binding['owned_pids']]
    if (len(controls) != 3 or any(binding['native_counts'].values())
            or any(p['executable_sha256'] != owner['executable_sha256'] for p in controls)):
        raise ValueError('interruption requires exactly three Python controls and no native solver')
    by_parent = {p['parent_pid']:p for p in controls}
    ordered = [owner]
    for _ in range(3):
        if ordered[-1]['pid'] not in by_parent:
            raise ValueError('interruption control ancestry is not a single chain')
        ordered.append(by_parent[ordered[-1]['pid']])
    if 'run_broadband56_v2_exact_gds_emx_batch.py' not in ordered[-1].get('command_text', ''):
        raise ValueError('interruption leaf is not the zero-dispatch EMX batch')
    if str(Path(prefix['source_stage_dir'])/'backend/roles/08_exact_audited_gds_emx_runner') not in ordered[-1].get('command_text', ''):
        raise ValueError('interruption leaf belongs to a different stage')
    return ordered


def interrupt(*, prefix_record, out_dir):
    """Fence dispatch, prove quiescence, then terminate only pinned control PIDs."""
    prefix = validate_prefix(prefix_record)
    lease = cp.read(cp.bound(prefix['prior_supervisor_lease']))
    context = cp.read(cp.bound(prefix['source_context']))
    ordered = verify_control_tree(prefix=prefix, lease=lease, history=context['stage_resource_history'])
    out = Path(out_dir)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    fds, stopped = {}, []
    committed_to_stop = False
    try:
        for process in ordered:
            pid = process['pid']
            fd = os.pidfd_open(pid)
            current = iso.read_process_identity(pid)
            if current is None or not iso.process_identity_matches(current, process):
                os.close(fd)
                raise ValueError('PID changed while acquiring interruption handles')
            fds[pid] = fd
        # Fence the sole dispatcher first; stop the ancestors so none may advance.
        for process in reversed(ordered):
            signal.pidfd_send_signal(fds[process['pid']], signal.SIGSTOP)
            stopped.append(process)
        limit = time.monotonic()+90
        control_ids = {p['pid'] for p in ordered}
        while True:
            alive = [iso.read_process_identity(p['pid']) for p in ordered]
            if any(p is None for p in alive):
                raise ValueError('control exited before the frozen evidence boundary')
            extra = set(descendants(ordered[0]['pid']))-control_ids
            # The pre-existing read-only capacity sampler may finish naturally.
            if not extra and all(p['state'] in ('T', 't') for p in alive):
                break
            if time.monotonic() >= limit:
                raise ValueError('control tree did not reach a solver-free frozen boundary')
            time.sleep(0.2)
        validate_prefix(prefix_record)
        frozen = write(out/'FROZEN_CONTROL_TREE.json', dict(
            owner=lease['physical_process'], frozen_control_processes=[iso._public_process_record(p) for p in alive],
            native_solver_pids=[], dispatch=prefix['dispatch'], physical_prefix=prefix_record,
            dispatch_fence='PIDFD_SIGSTOP_LEAF_THEN_ANCESTORS',
            standing_owner_authorization=prefix['standing_owner_authorization']))
        # Recheck after the durable freeze proof; no stopped process can dispatch.
        if set(descendants(ordered[0]['pid'])) != control_ids:
            raise ValueError('new descendant appeared after the frozen boundary')
        committed_to_stop = True
        for process in reversed(ordered):
            signal.pidfd_send_signal(fds[process['pid']], signal.SIGKILL)
        limit = time.monotonic()+30
        while any(iso.read_process_identity(p['pid']) is not None for p in ordered):
            if time.monotonic() >= limit:
                raise ValueError('fenced control termination not yet proven')
            time.sleep(0.2)
        validate_prefix(prefix_record)
        remaining = iso.enumerate_owner_processes(lease['physical_process']['uid'], probe_pid=os.getpid(), include_arguments=True)
        foreign = [p for p in remaining if iso._project_execution_process(p.get('command_text', ''))]
        if foreign:
            raise ValueError('project execution survived controlled interruption')
        lock = Path(lease['campaign_lock']['path'])
        with lock.open('r+') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if handle.read().strip() != cp.SUPERVISOR_ID:
                raise ValueError('original campaign lock contents differ')
            death = write(out/'CONTROL_PROCESS_DEATH.json', dict(owner=lease['physical_process'],
                terminated_control_pids=[p['pid'] for p in ordered], survivors=[], dispatch=prefix['dispatch'],
                lock_path=str(lock), exclusive_lock_observed=True, solver_processes_signalled=[]))
            receipt = write(out/'CONTROLLED_WAITING_INTERRUPTION_RECEIPT.json', dict(
                schema=INTERRUPTION_SCHEMA, overall_status='PASS_CONTROLLED_INTERRUPTION_NOT_STAGE_COMPLETION',
                standing_owner_authorization=prefix['standing_owner_authorization'],
                prior_supervisor_lease=prefix['prior_supervisor_lease'], checkpoint_boundary=prefix['checkpoint_boundary'],
                physical_prefix=prefix_record, frozen_tree_evidence=frozen, process_death_evidence=death,
                dispatch_fenced_before_termination=True, healthy_native_solvers_terminated=0,
                surviving_project_processes=[], old_process_confirmed_dead=True,
                exclusive_lock_reacquired=True, current_accepted=prefix['current_accepted'],
                feature_rows=prefix['feature_rows'], source_artifacts_modified=False,
                stage_completed=False, simulator_action_taken=False))
        return receipt
    except BaseException as error:
        write(out/'INTERRUPTION_FAILURE.json', dict(overall_status='FAIL', error=repr(error),
            control_termination_started=committed_to_stop, physical_prefix=prefix_record))
        if not committed_to_stop:
            for process in reversed(stopped):
                try:
                    signal.pidfd_send_signal(fds[process['pid']], signal.SIGCONT)
                except ProcessLookupError:
                    pass
        raise
    finally:
        for fd in fds.values():
            os.close(fd)
