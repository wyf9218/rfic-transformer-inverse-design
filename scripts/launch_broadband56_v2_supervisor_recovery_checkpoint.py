#!/usr/bin/env python3
"""Resume the same approved supervisor at a committed checkpoint, without Golden.

The recovery prefix preserves the existing isolation auditor's owner marker;
the authorization and handoff scope are normal checkpoint continuation.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def pin(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('startup input must be absolute without symlinks')
    data = path.read_bytes()
    return dict(path=str(path), size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def bound(record):
    if pin(record['path']) != record:
        raise ValueError('startup bound file drift: '+str(record.get('path')))
    return Path(record['path'])


def load(record, name):
    path = bound(record)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    bound(record)
    return module


def bootstrap(candidate_record):
    """Import only the exact bundle and the existing bound historical FULL validator."""
    candidate = json.loads(bound(candidate_record).read_bytes())
    runtime, files = candidate['runtime_files'], candidate['bound_files']
    if pin(__file__) != runtime['checkpoint_launcher']:
        raise ValueError('checkpoint launcher does not match approved bytes')
    repo = Path(__file__).resolve().parents[1]
    for key, relative in (
        ('checkpoint_startup_module', 'rfic_transformer_inverse_design/campaigns/broadband56_checkpoint_startup.py'),
        ('checkpoint_handoff_module', 'rfic_transformer_inverse_design/campaigns/broadband56_checkpoint_handoff.py'),
        ('queue_controller', 'scripts/run_broadband56_v2_swap_override_queue_controller.py'),
    ):
        if bound(runtime[key]) != repo/relative:
            raise ValueError('normal startup runtime escaped the candidate-bound repository')
    manifest = json.loads(bound(files['new_runtime_manifest']).read_bytes())
    after_records = [v['after'] for v in manifest['source_file_pairs']]
    after_records += [v['after'] for v in manifest.get('added_source_files', [])]
    after_records += [v['after'] for v in manifest.get('private_source_files', [])]
    for record in after_records:
        bound(record)
    for key in ('checkpoint_launcher', 'checkpoint_startup_module', 'checkpoint_handoff_module', 'queue_controller'):
        if runtime[key] not in after_records:
            raise ValueError('normal startup module is absent from immutable runtime manifest')
    for name in tuple(sys.modules):
        if name == 'rfic_transformer_inverse_design' or name.startswith('rfic_transformer_inverse_design.'):
            del sys.modules[name]
    sys.path.insert(0, str(repo))
    executor = load(runtime['legacy_executor'], 'b56_checkpoint_bound_legacy_executor')
    canonical = 'rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization'
    historical = load(runtime['historical_full_candidate_validator'], canonical)
    errors = historical.validate_full_campaign_candidate(json.loads(bound(files['full_campaign_candidate']).read_bytes()))
    if errors:
        raise ValueError('existing FULL candidate failed its original bound validator: '+repr(errors[:12]))
    from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_startup as startup
    if pin(startup.__file__) != runtime['checkpoint_startup_module']:
        raise ValueError('loaded normal startup module differs')
    startup.validate_candidate(executor, candidate_record)
    isolation = load(runtime['isolation_identity_module'], 'b56_checkpoint_bound_isolation')
    me = isolation.read_process_identity(os.getpid())
    prior = json.loads(bound(files['prior_supervisor_lease']).read_bytes())
    if me is None or any(me.get(k) != prior['physical_process'].get(k) for k in (
            'uid', 'executable_path', 'executable_sha256')):
        raise ValueError('startup does not use the approved private Python identity')
    import numpy
    if numpy.__version__ != '2.5.0':
        raise ValueError('approved private numpy identity differs')
    wrapper = load(runtime['queue_controller'], 'b56_checkpoint_bound_wrapper')
    controller = load(files['delegate_controller'], 'b56_checkpoint_bound_delegate')
    hook = load(runtime['capacity_hook'], 'b56_checkpoint_bound_capacity_hook')
    binding = json.loads(bound(files['capacity_binding']).read_bytes())
    hook.validate(binding)
    return candidate, executor, startup, isolation, wrapper, controller, hook, binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preflight', 'launch'), required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--approval')
    parser.add_argument('--approval-sha256')
    parser.add_argument('--checkpoint-boundary')
    parser.add_argument('--checkpoint-boundary-sha256')
    parser.add_argument('--operation-root')
    parser.add_argument('--successor-root')
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    fd = None
    try:
        candidate_record = pin(args.candidate)
        if candidate_record['sha256'] != args.candidate_sha256:
            raise ValueError('candidate SHA-256 mismatch')
        candidate, executor, startup, isolation, wrapper, controller, hook, binding = bootstrap(candidate_record)
        if args.mode == 'preflight':
            print(json.dumps(dict(overall_status='PASS_IMPORTS_ONLY_NOT_LAUNCH_AUTHORITY',
                candidate=candidate_record, launcher=pin(__file__),
                actual_checkpoint_required_before_launch=True, controller_main_called=False,
                lease_created=False, simulator_action_taken=False)))
            return 0
        required = (args.approval, args.approval_sha256, args.checkpoint_boundary,
                    args.checkpoint_boundary_sha256, args.operation_root, args.successor_root)
        if not all(required):
            raise ValueError('launch requires exact approval, committed checkpoint and new output paths')
        approval = pin(args.approval)
        boundary = pin(args.checkpoint_boundary)
        if approval['sha256'] != args.approval_sha256 or boundary['sha256'] != args.checkpoint_boundary_sha256:
            raise ValueError('approval or committed-checkpoint SHA mismatch')
        startup.validate_authority(executor, candidate_record, approval)
        executor._wait_for_detached_parent()
        # Never create, truncate or replace the original authoritative lock.
        fd = os.open(candidate['global_lock_path'], os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepared = startup.prepare_controls(executor, candidate_record=candidate_record,
            approval_record=approval, boundary_record=boundary, operation_root=args.operation_root,
            successor_root=args.successor_root, isolation=isolation, lock_fd=fd)
        return startup.run_controller(executor, prepared, wrapper=wrapper, controller=controller,
            capacity_hook=hook, capacity_binding=binding, lock_fd=fd)
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps(dict(overall_status='BLOCKED', error=str(error))), file=sys.stderr)
        return 2
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
