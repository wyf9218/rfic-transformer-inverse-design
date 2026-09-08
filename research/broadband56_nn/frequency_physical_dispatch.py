"""Finite, single-owner round-robin research physical queue (never production).

Resumption reuses exact terminal stage receipts; partial native outputs stop the
queue for diagnosis instead of rerunning a simulator. A permanent inherited
global lease survives parent loss while a native child is alive. Resource waits
are ordinary bounded program waits, not AI polling or scheduler resubmission.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .io import read_json, save_json, utc_now
from .frequency_research_emx import (guard, pin, require, verify, global_lease,
    operational_budget, OPERATIONAL_BUDGET_ENV, OPERATIONAL_USE_ENV)

ORDER = [15, 5, 10, 20, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19]


class BudgetEnded(RuntimeError):
    pass


def bounded_map(function, items, workers):
    """At most pool-size submitted; failure stops dispatch, not running children."""
    require(type(workers) is int and 1 <= workers <= 4, 'Invalid global EMX pool size')
    remaining = iter(items)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        active = set()
        for _ in range(workers):
            item = next(remaining, None)
            if item is not None:
                active.add(pool.submit(function, item))
        while active:
            done, active = wait(active, return_when=FIRST_COMPLETED)
            # Check every completed future before dispatching replacements.
            try:
                results.extend(future.result() for future in done)
            except BaseException:
                for future in active:
                    future.cancel()
                raise
            for _ in done:
                item = next(remaining, None)
                if item is not None:
                    active.add(pool.submit(function, item))
    return results


def frozen_json(path, value):
    """Reuse exact generated metadata, never change a request on resume."""
    path = Path(path)
    if path.exists():
        require(read_json(path) == value, 'Frozen runtime binding differs: ' + str(path))
    else:
        save_json(path, value)
    return pin(path)


def relocate(original, manifest, payload):
    matches = [x for x in manifest['relocations'] if x['original'] == original]
    require(len(matches) == 1, 'Original pin missing/duplicated in verified transport')
    item = matches[0]
    relative = Path(item['relative_path'])
    require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe input relocation')
    result = pin(Path(payload) / relative)
    require(result['sha256'] == original['sha256'] == item['sha256'] and
            result['bytes'] == original['bytes'] == item['bytes'], 'Relocated byte identity differs')
    return result


def validate(config):
    guard()
    require(config['schema'] == 'frequency_physical_finite_dispatch.v1', 'Wrong dispatch schema')
    require(type(config['max_global_solvers']) is int and 1 <= config['max_global_solvers'] <= 4 and
            config['cadence_jobs'] == 1, 'At most four global EMX solvers; one Cadence worker')
    require(config['resource_budget']['max_global_solvers'] == config['max_global_solvers'], 'Pool budgets differ')
    require(config['production_modified'] is False, 'Production mutation forbidden')
    require(config['resource_budget']['cpu_per_solver'] == 2, 'Unexpected research CPU budget')
    for value in config['source_pins']:
        verify(value)
    for key in ('relocation_manifest', 'dispatch_manifest'):
        verify(config[key])
    relocation = read_json(config['relocation_manifest']['path'])
    plan = read_json(config['dispatch_manifest']['path'])
    require(plan['frequency_order'] == ORDER and len(plan['jobs']) == 320,
            'Exact original 16-frequency x 20-request order required')
    require(plan['max_requests_per_frequency'] == 20 and plan['max_global_emx_concurrency'] == 4,
            'Frozen maximums changed')
    require(len({x['request_id'] for x in plan['jobs']}) == 320, 'Duplicate request identity')
    require(plan['jobs'][0]['route'] == 'REUSE_EXISTING_FIRST15_GDS_CALIBRE', 'First15 reuse lost')
    for number, job in enumerate(plan['jobs']):
        require(job['dispatch_order'] == number and job['round_index'] == number // 16 and
                job['frequency_ghz'] == ORDER[number % 16] and job['original_request_order'] == number // 16,
                'Dispatch order differs from frozen request order')
        require(job['N_logical_candidates'] == 11 and job['N_analytic_pass'] + job['N_analytic_failed'] == 11,
                'Request denominator changed')
        for name in ('pending_receipt', 'candidate_records', 'candidate_csv'):
            relocate(job[name], relocation, config['payload_root'])
    return plan, relocation


def resources(config):
    memory = {s.split(':')[0]: int(s.split(':')[1].split()[0]) * 1024
              for s in Path('/proc/meminfo').read_text().splitlines() if ':' in s}
    budget = config['resource_budget']
    observed = dict(cpu_logical=os.cpu_count(), load_one_minute=os.getloadavg()[0],
                    memory_available_bytes=memory['MemAvailable'],
                    disk_free_bytes=shutil.disk_usage(config['out']).free)
    workers = config['max_global_solvers']
    admitted = (observed['cpu_logical'] >= max(4, 2*workers) and
                observed['load_one_minute'] < observed['cpu_logical'] - 2*workers and
                observed['memory_available_bytes'] >= max(8*1024**3, budget['min_memory_available_bytes'])*workers and
                observed['disk_free_bytes'] >= max(20*1024**3, budget['min_disk_free_bytes'])*workers)
    return admitted, observed


def summarize_request(job, records, features):
    """Strict optimum only for 11/11 original valid EMX, never best-survivor."""
    require(len(records) == 11 and [r['q_target'] for r in records] == list(range(10, 21)) and
            len({r['candidate_id'] for r in records}) == 11,
            'Exactly original ordered eleven Q10..20 records required')
    require(all(type(r['analytic_grid']) is bool and r['request_id'] == job['request_id'] for r in records),
            'Original request identity or analytic flag differs')
    by_q = {int(x['q_requested']): x for x in features}
    require(len(by_q) == len(features), 'Duplicate physical feature candidate')
    for q, feature in by_q.items():
        row = next(r for r in records if r['q_target'] == q)
        require(feature.get('request_id', job['request_id']) == job['request_id'] and
                feature['candidate_id'] == row['candidate_id'] and row['analytic_grid'] is True and
                all(feature[k] == job[k] for k in ('frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy')),
                'Physical feature model/request identity differs')
    valid = [x for x in by_q.values() if x['valid_for_strict_comparison']]
    winner = min(valid, key=lambda x: (x['normalized_response_score'], x['q_requested'])) if len(valid) == 11 else None
    selected = by_q.get(job['q_proxy'])
    return dict(status='REQUEST_ACCOUNTED', request_id=job['request_id'], frequency_ghz=job['frequency_ghz'],
        model_id=job['model_id'], dataset_scope=job['dataset_scope'], target_source=job['target_source'],
        q_proxy=job['q_proxy'], q_emx=None if winner is None else winner['q_requested'],
        N_logical=11, N_analytic_fail=sum(not r['analytic_grid'] for r in records),
        N_solved=len(by_q), N_strict_valid=len(valid), N_not_solved=11-len(by_q),
        q_proxy_real_validation='NOT_RUN' if selected is None else 'RUN',
        q_proxy_strict_joint_hit=None if selected is None else selected['strict_joint_hit'],
        q_proxy_target_relative_absolute_percent=None if selected is None else selected['target_relative_absolute_percent'],
        q_proxy_score=None if selected is None else selected['normalized_response_score'],
        selected_q_agrees_with_emx=None if winner is None else job['q_proxy'] == winner['q_requested'],
        q_emx_gate='PASS_ORIGINAL_ELEVEN_VALID' if winner else 'NOT_ELEVEN_VALID_NO_OPTIMUM_CLAIM',
        failure_denominator_retained=True, success_replacement=False, production_modified=False)


class Dispatcher:
    def __init__(self, path, operational_budget_path=None, resource_wait_recovery_path=None):
        self.config_pin = pin(path)
        self.config = read_json(path)
        self.plan, self.relocation = validate(self.config)
        self.out = Path(self.config['out'])
        self.fd = None
        self.queue_fd = None
        self.resource_recovery = None
        self.operational = (operational_budget(operational_budget_path, base_pin=self.config_pin,
                            executing_source=None if resource_wait_recovery_path else __file__)
                            if operational_budget_path else None)
        if resource_wait_recovery_path:
            self.resource_recovery = dict(pin=pin(resource_wait_recovery_path),
                                          value=read_json(resource_wait_recovery_path))
            self.verify_resource_recovery()
        self.operational_use = None
        self.environment = dict(os.environ, PYTHONOPTIMIZE='0', PYTHONDONTWRITEBYTECODE='1',
            PYTHONPATH=self.config['code_root'] + os.pathsep + self.config['repo'],
            OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
        # No ambient environment may silently extend an ordinary invocation.
        self.environment.pop(OPERATIONAL_BUDGET_ENV, None)
        self.environment.pop(OPERATIONAL_USE_ENV, None)

    def verify_resource_recovery(self, *, failures=False):
        """An explicit exception binds old evidence, not a rewritten base budget."""
        manifest = self.resource_recovery
        verify(manifest['pin'])
        value = manifest['value']
        require(set(value) == {'schema', 'base_config', 'previous_operational_budget',
                              'release', 'acknowledged_failures', 'stages'} and
                value['schema'] == 'frequency_physical_resource_wait_recovery.v1',
                'Unexpected resource recovery fields')
        require(self.operational is not None and value['base_config'] == self.config_pin and
                value['previous_operational_budget'] == self.operational['pin'],
                'Recovery must retain exact base and previous operational budget')
        operational_budget(self.operational['pin']['path'], base_pin=self.config_pin)
        old = self.operational['value']['release']
        release = value['release']
        require(set(release) == {'code_root', 'source_pins'}, 'Unexpected recovery release fields')
        code = Path(release['code_root'])
        require(code.is_absolute() and str(code) not in (old['code_root'], self.config['code_root']),
                'Recovery requires a new release path')
        old_sources = {str(Path(p['path']).relative_to(old['code_root'])): p for p in old['source_pins']}
        sources = {str(verify(p).relative_to(code)): p for p in release['source_pins']}
        require(len(release['source_pins']) == len(sources) == 7 and set(sources) == set(old_sources),
                'Recovery must preserve the seven-source closure')
        for name, previous in old_sources.items():
            if name != 'research/broadband56_nn/frequency_physical_dispatch.py':
                require(all(sources[name][k] == previous[k] for k in ('sha256', 'bytes')),
                        'Only the dispatcher may change in a resource recovery release')
        require(pin(__file__) == sources['research/broadband56_nn/frequency_physical_dispatch.py'],
                'Executing recovery dispatcher is not release-pinned')
        require(value['stages'] and len({p['process']['path'] for p in value['stages']}) == len(value['stages']),
                'Recovery stage set is empty or duplicated')
        for entry in value['stages']:
            self.resource_evidence(entry, unused=False)
            # Runtime peer admissions may overlap an already admitted Q17 child.
            # Only initial admission (and that candidate's own retry boundary)
            # requires a preflight-only directory, never unrelated peer checks.
            if failures != 'pins_only' and self.completed_resource_attempt(entry) is None:
                self.resource_evidence(entry)
        if failures:
            actual = [pin(p) for p in sorted(self.out.glob('FAILURE_*.json'))]
            expected = value['acknowledged_failures']
            require(expected and sorted(expected, key=lambda p: p['path']) == actual,
                    'Unacknowledged or changed queue failure; no recovery')
            for p in expected:
                failure = read_json(verify(p))
                require(failure['status'] == 'FAIL_NO_RETRY' and failure['config'] == self.config_pin and
                        failure['error'] == 'ResearchEmxError: Native stage did not finish with evidence',
                        'A different queue failure cannot be acknowledged as resource wait')
            allowed = {p['process']['path'] for p in value['stages']}
            if failures is True:
                for p in self.out.glob('requests/*/*_PROCESS.json'):
                    process = read_json(p)
                    if process['returncode'] != 0 and str(p) not in allowed:
                        basis = p.with_name(p.name.replace('_PROCESS.json', '_RESOURCE_WAIT_RECOVERY.json'))
                        require(basis.is_file(), 'Foreign failed stage is not resource recovery evidence')
                        prior = read_json(basis)
                        require(prior['config'] == self.config_pin and prior['entry']['process'] == pin(p),
                                'Foreign failed stage recovery basis differs')
                        self.resource_evidence(prior['entry'], unused=False)

    def resource_evidence(self, entry, *, unused=True):
        """Recognize only the exact wrapper rejection before any solver artifact."""
        require(set(entry) == {'request', 'intent', 'process', 'log', 'preflight', 'old_wrapper'},
                'Unexpected resource stage evidence fields')
        for p in entry.values():
            verify(p)
        request = read_json(entry['request']['path'])
        q = request['q_requested']
        require(type(q) is int and 10 <= q <= 20, 'Foreign resource stage Q')
        root = self.out / 'requests' / request['request_id']
        name = f'emx_q{q}'
        output = root / name
        expected_paths = dict(request=root / f'EMX_Q{q}_REQUEST.json', intent=root / (name+'_INTENT.json'),
            process=root / (name+'_PROCESS.json'), log=root / (name+'.log'), preflight=output / 'PREFLIGHT.json')
        require(all(entry[k]['path'] == str(p) for k, p in expected_paths.items()), 'Foreign resource stage path')
        jobs = [j for j in self.plan['jobs'] if j['request_id'] == request['request_id']]
        require(len(jobs) == 1 and all(request[k] == jobs[0][k] for k in
                ('frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy')), 'Foreign resource request identity')
        for key, base_key in [('dispatch_deadline_utc', 'dispatch_deadline_utc'),
                ('global_lock_path', 'global_lock_path'), ('resource_budget', 'resource_budget'),
                ('private_config', 'configuration'), ('runtime', 'emx_runtime')]:
            require(request[key] == self.config[base_key], 'Resource recovery scientific fields changed')
        require(all(request['records'][k] == jobs[0]['candidate_records'][k] for k in ('sha256', 'bytes')),
                'Resource recovery records differ')
        intent = read_json(entry['intent']['path'])
        command = self.module_command('frequency_research_emx', 'run', '--request', entry['request']['path'],
            '--output', output, '--inherited-global-lease-fd', '<inherited-global-lease-fd>')
        require(intent == dict(config=self.config_pin, command=command, output=str(output),
            completion=str(output / 'features/FEATURE_RECEIPT.json')), 'Resource recovery intent differs')
        process = read_json(entry['process']['path'])
        require(process['intent'] == intent and process['returncode'] == 1 and process['completion'] is None and
                process['log'] == entry['log'], 'Not an exact preflight resource rejection process')
        self.resource_rejection(process, output, unused=False)
        proof = read_json(entry['preflight']['path'])
        require(proof['schema'] == 'frequency_research_emx_preflight.v1' and proof['status'] == 'PASS' and
                proof['request'] == entry['request'] and proof['output'] == str(output) and
                all(proof[k] == request[k] for k in ('request_id', 'candidate_id', 'frequency_ghz',
                    'q_requested', 'q_proxy', 'model_id', 'dataset_scope')), 'Preflight request identity differs')
        wrappers = [p for p in proof['source_pins'] if Path(p['path']).name == 'frequency_research_emx.py']
        release = self.operational['value']['release'] if getattr(self, 'operational', None) else dict(
            code_root=self.config['code_root'], source_pins=self.config['source_pins'])
        expected_wrapper = next(p for p in release['source_pins'] if
            p['path'] == str(Path(release['code_root']) / 'research/broadband56_nn/frequency_research_emx.py'))
        require(wrappers == [entry['old_wrapper']] and entry['old_wrapper'] == expected_wrapper,
                'Original preflight wrapper must be the exact previous release')
        if getattr(self, 'operational', None):
            use = read_json(verify(process['operational_budget_use']))
            require(use['budget'] == self.operational['pin'] and use['base_config'] == self.config_pin and
                    use['release'] == release and use['status'] == 'ADMITTED_UNDER_ORIGINAL_QUEUE_AND_GLOBAL_LEASES' and
                    use['original_deadline_utc'] == self.config['dispatch_deadline_utc'] and
                    use['new_deadline_utc'] == self.operational['value']['new_dispatch_deadline_utc'],
                    'Previous resource process budget/use differs')
        if unused:
            self.resource_rejection(process, output)
        return intent

    @staticmethod
    def resource_rejection(process, output, *, unused=True):
        verify(process['log'])
        lines = Path(process['log']['path']).read_text().rstrip().splitlines()
        require(process['returncode'] == 1 and process['completion'] is None and lines and
                lines[-1] == 'ResearchEmxError: RESOURCE_WAIT_REQUIRED_NO_DISPATCH',
                'Not an exact RESOURCE_WAIT_REQUIRED_NO_DISPATCH rejection')
        if unused:
            require(output.is_dir() and {p.name for p in output.iterdir()} == {'PREFLIGHT.json'},
                    'Resource recovery requires only PREFLIGHT; solver/feature evidence forbids retry')

    def completed_resource_attempt(self, entry):
        """Recover only a closed successful attempt, including a missing sidecar."""
        original = Path(entry['process']['path'])
        basis_path = original.with_name(original.name.replace('_PROCESS.json', '_RESOURCE_WAIT_RECOVERY.json'))
        if not basis_path.is_file():
            return None
        basis_pin = pin(basis_path)
        basis = read_json(basis_path)
        require(basis['schema'] == 'frequency_resource_wait_basis.v1' and basis['config'] == self.config_pin and
                basis['entry'] == entry, 'Recovery replay basis differs')
        if basis['authorization'] is not None:
            require(getattr(self, 'resource_recovery', None) is not None and
                    basis['authorization'] == self.resource_recovery['pin'], 'Explicit resource manifest required')
        intent = read_json(entry['intent']['path'])
        attempts = original.with_name(original.name.replace('_PROCESS.json', '_RESOURCE_WAIT_ATTEMPTS'))
        if not attempts.exists():
            return None
        directories = sorted(attempts.iterdir())
        for number, directory in enumerate(directories, 1):
            require(directory.is_dir() and directory.name == f'{number:06d}', 'Unexpected resource attempt path')
            started = read_json(directory / 'INTENT.json')
            require(started['basis'] == basis_pin and started['intent'] == intent and
                    started['old_wrapper'] == entry['old_wrapper'], 'Resource attempt binding differs')
            terminal = directory / 'PROCESS.json'
            require(terminal.is_file(), 'Unreceipted resource attempt retained; never launch twice')
            attempt = read_json(terminal)
            require(attempt['intent'] == intent and attempt['basis'] == basis_pin and
                    attempt['log']['path'] == str(directory / 'wrapper.log') and
                    attempt.get('operational_budget_use') == started.get('operational_budget_use'),
                    'Resource attempt process differs')
            verify(attempt['log'])
            if attempt.get('operational_budget_use') is not None:
                verify(attempt['operational_budget_use'])
            if attempt['returncode'] == 0:
                require(directory == directories[-1] and attempt['completion'] is not None and
                        attempt['completion']['path'] == intent['completion'], 'Attempt after success or completion differs')
                verify(attempt['completion'])
                return terminal
            # Earlier closed resource denials remain valid history even after
            # the final admitted attempt has produced physics/features.
            self.resource_rejection(attempt, Path(intent['output']), unused=False)
        return None

    def recover_resource_wait(self, root, name, command, output, completion, entry=None):
        """Wait within the same budget; only never-started physics may be admitted."""
        basis_path = root / (name+'_RESOURCE_WAIT_RECOVERY.json')
        if entry is not None:
            self.resource_evidence(entry, unused=not basis_path.exists())
            frozen_json(basis_path, dict(schema='frequency_resource_wait_basis.v1', config=self.config_pin,
                entry=entry, authorization=self.resource_recovery['pin'] if getattr(self, 'resource_recovery', None) else None))
        basis_pin = pin(basis_path)
        basis = read_json(basis_path)
        require(basis['schema'] == 'frequency_resource_wait_basis.v1' and basis['config'] == self.config_pin,
                'Resource recovery basis changed')
        if basis['authorization'] is not None:
            require(getattr(self, 'resource_recovery', None) is not None and
                    basis['authorization'] == self.resource_recovery['pin'], 'Explicit resource manifest required')
        entry = basis['entry']
        intent = self.resource_evidence(entry, unused=False)
        require(entry['process']['path'] == str(root / (name+'_PROCESS.json')) and
                intent['output'] == str(output) and intent['completion'] == str(completion), 'Recovery stage differs')
        state = root / (name+'_RESOURCE_WAIT_RECOVERY_PROCESS.json')
        def finish(attempt_path):
            attempt = read_json(attempt_path)
            require(attempt['intent'] == intent and attempt['basis'] == basis_pin and
                    attempt['returncode'] == 0 and attempt['completion'] is not None and
                    attempt['completion']['path'] == str(completion) and
                    attempt['log']['path'] == str(attempt_path.parent / 'wrapper.log'),
                    'Resource recovery child failed; no retry')
            verify(attempt['completion']); verify(attempt['log'])
            receipt = dict(intent=intent, returncode=0, completion=attempt['completion'], log=attempt['log'],
                status='RESOURCE_WAIT_RECOVERY_PROCESS_COMPLETE', original_process=entry['process'],
                basis=basis_pin, attempt=pin(attempt_path), operational_budget_use=attempt.get('operational_budget_use'))
            frozen_json(state, receipt)
            return receipt
        completed_attempt = self.completed_resource_attempt(entry)
        if state.exists():
            saved = read_json(state)
            require(saved['basis'] == basis_pin and saved['original_process'] == entry['process'], 'Recovery receipt differs')
            verify(saved['attempt'])
            require(completed_attempt == Path(saved['attempt']['path']), 'Completed recovery attempt differs')
            return finish(Path(saved['attempt']['path']))
        if completed_attempt is not None:
            return finish(completed_attempt)
        attempts = root / (name+'_RESOURCE_WAIT_ATTEMPTS')
        attempts.mkdir(exist_ok=True)
        existing = sorted(attempts.iterdir())
        number = len(existing)
        while True:
            self.admit()
            self.resource_evidence(entry)
            if getattr(self, 'resource_recovery', None):
                self.verify_resource_recovery(failures='pins_only')
            overlay = getattr(self, 'operational', None)
            environment = dict(self.environment)
            if overlay:
                require(self.operational_use is not None, 'Original leases must be admitted before recovery')
                environment.update(PYTHONPATH=overlay['value']['release']['code_root'] + os.pathsep + self.config['repo'])
                environment[OPERATIONAL_BUDGET_ENV] = overlay['pin']['path']
                environment[OPERATIONAL_USE_ENV] = json.dumps(self.operational_use, sort_keys=True)
            number += 1
            directory = attempts / f'{number:06d}'
            directory.mkdir(exist_ok=False)
            frozen_json(directory / 'INTENT.json', dict(basis=basis_pin, intent=intent,
                old_wrapper=entry['old_wrapper'], operational_budget_use=getattr(self, 'operational_use', None)))
            log = directory / 'wrapper.log'
            with log.open('xb') as stream:
                child = subprocess.Popen(['nice', '-n', '19', *command], cwd=self.config['repo'], env=environment,
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, pass_fds=(self.fd, self.queue_fd))
                self.event('RESOURCE_WAIT_CHILD', request=root.name, stage_name=name, child_pid=child.pid, attempt=number)
                code = child.wait()
            attempt_path = directory / 'PROCESS.json'
            attempt = dict(intent=intent, basis=basis_pin, returncode=code, completed_utc=utc_now(), log=pin(log),
                completion=pin(completion) if completion.is_file() else None,
                operational_budget_use=getattr(self, 'operational_use', None))
            frozen_json(attempt_path, attempt)
            if code == 0:
                return finish(attempt_path)
            self.resource_rejection(attempt, output)
            self.event('RESOURCE_RACE_WAIT_NO_SOLVER_STARTED', request=root.name, stage_name=name, attempt=number)
            self.budget()
            time.sleep(30)

    def event(self, stage, **fields):
        self.out.mkdir(parents=True, exist_ok=True)
        value = dict(utc=utc_now(), stage=stage, pid=os.getpid(), **fields)
        with (self.out / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())

    def budget(self):
        overlay = getattr(self, 'operational', None)
        if overlay:
            verify(overlay['pin'])
        deadline = overlay['value']['new_dispatch_deadline_utc'] if overlay else self.config['dispatch_deadline_utc']
        end = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) >= end:
            raise BudgetEnded('Dispatch cutoff reached; no native child stopped')

    def admit(self):
        waiting = False
        while True:
            self.budget()
            passed, observed = resources(self.config)
            if passed:
                if waiting:
                    self.event('RESOURCE_READMITTED', observed=observed)
                return observed
            if not waiting:
                self.event('RESOURCE_WAIT', observed=observed)
            waiting = True
            time.sleep(30)

    def module_command(self, name, *arguments):
        return [self.config['python'], '-B', '-m', 'research.broadband56_nn.' + name, *map(str, arguments)]

    def extraction_environment(self, output):
        """Keep the exact wrapper __file__ identity of an already solved candidate."""
        environment = dict(self.environment)
        environment.pop(OPERATIONAL_BUDGET_ENV, None)
        environment.pop(OPERATIONAL_USE_ENV, None)
        overlay = getattr(self, 'operational', None)
        if not overlay:
            return environment
        proof = read_json(Path(output) / 'PREFLIGHT.json')
        wrappers = [p for p in proof['source_pins'] if Path(p['path']).name == 'frequency_research_emx.py']
        require(len(wrappers) == 1, 'Existing solver wrapper identity is ambiguous')
        candidates = [(self.config['code_root'], self.config['source_pins']),
                      (overlay['value']['release']['code_root'], overlay['value']['release']['source_pins'])]
        matches = [(root, p) for root, sources in candidates for p in sources if p == wrappers[0]]
        require(len(matches) == 1, 'Existing solver wrapper is not original or current pinned release')
        code_root, wrapper = matches[0]
        verify(wrapper)
        environment['PYTHONPATH'] = code_root + os.pathsep + self.config['repo']
        return environment

    def process(self, root, name, command, output, completion, allow_codes=(0,)):
        """Never infer success from process absence, or repeat partial output."""
        state = root / (name + '_PROCESS.json')
        semantic_command = list(command)
        if '--inherited-global-lease-fd' in semantic_command:
            semantic_command[semantic_command.index('--inherited-global-lease-fd') + 1] = '<inherited-global-lease-fd>'
        intent = dict(config=self.config_pin, command=semantic_command, output=str(output), completion=str(completion))
        if state.exists():
            receipt = read_json(state)
            if receipt['returncode'] not in allow_codes:
                basis = root / (name+'_RESOURCE_WAIT_RECOVERY.json')
                recovery = getattr(self, 'resource_recovery', None)
                entries = [e for e in recovery['value']['stages'] if e['process']['path'] == str(state)] if recovery else []
                if basis.is_file() or entries:
                    require(receipt['intent'] == intent, 'Previous process intent changed')
                    return self.recover_resource_wait(root, name, command, output, completion,
                        entry=entries[0] if entries else None)
            require(receipt['intent'] == intent and receipt['returncode'] in allow_codes, 'Previous process failed or changed')
            verify(receipt['completion'])
            return receipt
        if output.exists():
            require((root / (name + '_INTENT.json')).is_file() and
                    read_json(root / (name + '_INTENT.json')) == intent,
                    'Unowned or changed native output; duplicate dispatch refused')
            if name.startswith('emx_q') and (output / 'SOLVER_RECEIPT.json').is_file():
                # The exact existing solver is checked by extract(). It never
                # launches EMX, and refuses failed/partial feature evidence.
                extract_command = list(command)
                extract_command[extract_command.index('run')] = 'extract'
                extract_command = extract_command[:extract_command.index('--inherited-global-lease-fd')]
                recovery_log = root / (name + '_EXTRACT_RECOVERY.log')
                require(not recovery_log.exists(), 'An earlier recovery attempt exists; inspect, never repeat')
                with recovery_log.open('xb') as stream:
                    recovered = subprocess.run(extract_command, cwd=self.config['repo'], env=self.extraction_environment(output),
                        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                        pass_fds=(self.fd, self.queue_fd))
                require(recovered.returncode == 0 and completion.is_file(), 'Existing solver extraction did not validate')
                recovery = dict(status='EXISTING_SOLVER_REUSED_EXTRACTION_ONLY', log=pin(recovery_log))
            else:
                require(completion.is_file(), 'Partial native output retained; duplicate dispatch refused: ' + str(output))
                terminal = read_json(completion)
                if name == 'cadence':
                    require(terminal['overall_status'] == 'PASS', 'Unreceipted Cadence did not pass')
                elif name == 'gds_audit':
                    require(terminal['input_request'] == pin(root / 'GDS_REQUEST.json'), 'GDS terminal binding differs')
                elif name == 'calibre':
                    require(terminal['input_request'] == pin(root / 'CALIBRE_REQUEST.json') and
                            terminal['status'] == 'PROCESS_COMPLETE', 'DRC terminal binding differs')
                else:
                    raise RuntimeError('Unknown recovery stage; no simulator may be repeated')
                recovery = dict(status='COMPLETE_CHILD_TERMINAL_ADOPTED_NO_NATIVE_RERUN')
            receipt = dict(intent=intent, returncode=0, completed_utc=utc_now(),
                log=pin(root / (name + '.log')), completion=pin(completion), recovery=recovery)
            save_json(state, receipt)
            return receipt
        self.admit()
        for value in self.config['source_pins']:
            verify(value)
        environment = self.environment
        overlay = getattr(self, 'operational', None)
        if overlay:
            require(getattr(self, 'operational_use', None) is not None, 'Operational budget has not acquired original leases')
            if getattr(self, 'resource_recovery', None):
                self.verify_resource_recovery(failures='pins_only')
            else:
                operational_budget(overlay['pin']['path'], base_pin=self.config_pin, executing_source=__file__)
            if name.startswith('emx_q'):
                environment = dict(self.environment,
                    PYTHONPATH=overlay['value']['release']['code_root'] + os.pathsep + self.config['repo'])
                environment[OPERATIONAL_BUDGET_ENV] = overlay['pin']['path']
                environment[OPERATIONAL_USE_ENV] = json.dumps(self.operational_use, sort_keys=True)
        frozen_json(root / (name + '_INTENT.json'), intent)
        log = root / (name + '.log')
        require(not log.exists(), 'Previous unreceipted process attempt retained; no retry')
        self.event('NATIVE_STAGE_STARTED', request=root.name, stage_name=name)
        with log.open('xb') as stream:
            child = subprocess.Popen(['nice', '-n', '19', *command], cwd=self.config['repo'],
                env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                pass_fds=(self.fd, self.queue_fd))
            self.event('NATIVE_CHILD', request=root.name, stage_name=name, child_pid=child.pid)
            code = child.wait()
        receipt = dict(intent=intent, returncode=code, completed_utc=utc_now(), log=pin(log),
            completion=pin(completion) if completion.is_file() else None)
        if overlay:
            receipt['operational_budget_use'] = self.operational_use
        save_json(state, receipt)
        if name.startswith('emx_q') and code == 1 and receipt['completion'] is None and \
                log.read_text().rstrip().endswith('\nResearchEmxError: RESOURCE_WAIT_REQUIRED_NO_DISPATCH'):
            proof = read_json(output / 'PREFLIGHT.json')
            wrappers = [p for p in proof['source_pins'] if Path(p['path']).name == 'frequency_research_emx.py']
            require(len(wrappers) == 1, 'Resource-rejected wrapper identity is ambiguous')
            entry = dict(request=proof['request'], intent=pin(root / (name+'_INTENT.json')),
                process=pin(state), log=pin(log), preflight=pin(output / 'PREFLIGHT.json'), old_wrapper=wrappers[0])
            self.event('RESOURCE_RACE_WAIT_NO_SOLVER_STARTED', request=root.name, stage_name=name, attempt=0)
            return self.recover_resource_wait(root, name, command, output, completion, entry)
        require(code in allow_codes and receipt['completion'] is not None, 'Native stage did not finish with evidence')
        self.event('NATIVE_STAGE_TERMINAL', request=root.name, stage_name=name, returncode=code)
        return receipt

    def first15_reuse(self, job):
        terminal = Path(job['existing_root']) / 'remaining_q11to18_queue_v1/TERMINAL.json'
        require(terminal.is_file(), 'Existing first15 finite queue has not completed; never recreate it')
        value = read_json(terminal)
        require(value['status'] == 'COMPLETE' and value['N_logical'] == 11 and value['N_real_emx_solved'] == 9 and
                value['q_proxy'] == job['q_proxy'] and value['q_emx'] is None, 'Existing first15 terminal mismatch')
        for record in value['completed'] + value['features']:
            require(pin(record['path'])['sha256'] == record['sha256'], 'Existing first15 receipt changed')
        source = relocate(job['candidate_records'], self.relocation, self.config['payload_root'])
        records = [json.loads(s) for s in Path(source['path']).read_text().splitlines()]
        features = []
        for item in value['features']:
            feature = read_json(item['path'])
            require(feature['overall_status'] == 'PASS_EXTRACTION' and
                    feature['qscan_freeze']['sha256'] == self.plan['source_freezes'][0]['sha256'], 'Existing first15 feature freeze differs')
            # Explicit schema translation, not another extraction or inference.
            feature['dataset_scope'] = feature['research_tier']
            feature['valid_for_strict_comparison'] = feature['valid_for_frozen_strict_comparison']
            features.append(feature)
        result = summarize_request(job, records, features)
        result.update(status='REUSED_EXISTING_FIRST15_NO_SIMULATOR', source_terminal=pin(terminal),
                      legacy_feature_receipts=[pin(x['path']) for x in value['features']])
        return result

    def resource_artifacts(self, root):
        """Expose recovery metadata pins to the existing shallow receipt reader."""
        result = {}
        def add(value):
            verify(value)
            result[value['path']] = value
        def add_use(value):
            if value is None:
                return
            add(value)
            use = read_json(value['path'])
            add(use['budget']); add(use['base_config'])
            for source in use['release']['source_pins']:
                add(source)
        for path in sorted(root.glob('emx_q*_RESOURCE_WAIT_RECOVERY.json')):
            add(pin(path))
            basis = read_json(path)
            for value in basis['entry'].values():
                add(value)
            add_use(read_json(basis['entry']['process']['path']).get('operational_budget_use'))
            if basis['authorization'] is not None:
                add(basis['authorization'])
                manifest = read_json(basis['authorization']['path'])
                add(manifest['base_config']); add(manifest['previous_operational_budget'])
                for value in [*manifest['acknowledged_failures'], *manifest['release']['source_pins']]:
                    add(value)
            attempts = root / path.name.replace('_RESOURCE_WAIT_RECOVERY.json', '_RESOURCE_WAIT_ATTEMPTS')
            for directory in sorted(attempts.iterdir()):
                for name in ('INTENT.json', 'PROCESS.json', 'wrapper.log'):
                    add(pin(directory / name))
                add_use(read_json(directory / 'PROCESS.json').get('operational_budget_use'))
        return list(result.values())

    def execute_request(self, job):
        root = self.out / 'requests' / job['request_id']
        root.mkdir(parents=True, exist_ok=True)
        completed = root / 'REQUEST_RECEIPT.json'
        if completed.exists():
            value = read_json(completed)
            require(value['frozen_job'] == job and value['config'] == self.config_pin, 'Completed request identity changed')
            for evidence in value['artifacts']:
                verify(evidence)
            self.event('REUSE_COMPLETED_REQUEST', request=job['request_id'])
            return value
        if job['dispatch_order'] == 0:
            value = self.first15_reuse(job)
            value.update(frozen_job=job, config=self.config_pin, artifacts=[value['source_terminal'], *value['legacy_feature_receipts']])
            save_json(completed, value)
            return value
        context = {k: job[k] for k in ('request_id', 'frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy', 'target_source')}
        record_pin = relocate(job['candidate_records'], self.relocation, self.config['payload_root'])
        freeze_original = self.plan['source_freezes'][ORDER.index(job['frequency_ghz'])]
        freeze_pin = relocate(freeze_original, self.relocation, self.config['payload_root'])
        records = [json.loads(s) for s in Path(record_pin['path']).read_text().splitlines()]
        from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import GEOMETRY_FIELDS, canonical_geometry_sha256
        eligible = [r for r in records if r['analytic_grid']]
        rows = []
        for record in eligible:
            require(all(record[k] == context[k] for k in context), 'Candidate context changed')
            geometry = dict(zip(record['geometry_fields'], record['grid_geometry']))
            identity = canonical_geometry_sha256(geometry)
            rows.append(dict(candidate_id=record['candidate_id'],
                candidate_id_sha256=hashlib.sha256(record['candidate_id'].encode()).hexdigest(),
                geometry_id='research-'+identity, geometry_sha256=identity,
                candidate_geometry_identity_sha256=identity, acquisition_source='INDEPENDENT_RESEARCH_FIXED_Q_SCAN',
                **{'geom__'+k: geometry[k] for k in GEOMETRY_FIELDS}))
        candidate_csv = root / 'cadence_candidates.csv'
        if rows and not candidate_csv.exists():
            with candidate_csv.open('x', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        cadence = root / 'cadence_only'
        audit_request = root / 'GDS_REQUEST.json'
        frozen_json(audit_request, dict(schema='frequency_research_gds_audit_request.v1', production_campaign_membership=False,
            request=context, source_pins=dict(eleven_records=record_pin, qscan_freeze=freeze_pin),
            cadence=dict(root=str(cadence), routes={r['candidate_id']: f'parallel_shards/shard_{i:03d}' for i, r in enumerate(eligible)}),
            runtime=self.config['gds_runtime']))
        # Full original-record/frozen-model/physical-contract metadata gate is
        # before any GDS generation, and does not load a model or touch GDS.
        from .frequency_research_gds_audit import load_request
        load_request(audit_request)
        if rows:
            with candidate_csv.open(newline='') as stream:
                actual = list(csv.DictReader(stream))
            require(actual == [{k: str(v) for k, v in row.items()} for row in rows], 'Candidate CSV changed')
            command = [self.config['python'], '-B', self.config['cadence_script']['path'], '--candidate-csv', str(candidate_csv),
                '--out-dir', str(cadence), '--config', self.config['configuration']['path'],
                '--jobs', '1', '--chunk-size', '1', '--batch-size', '1', '--expected-count', str(len(rows)), '--expected-jobs', '1',
                '--cadence-streamout-only', '--fail-on-error', '--force-port-mode', 'single_ended_shield_grounded',
                '--force-cadence-pin-purpose', '51', '--force-wideband-5-60-1p0', '--expected-port-mode', 'single_ended_shield_grounded',
                '--expected-pin-purpose', '51', '--expected-frequency-start-ghz', '5', '--expected-frequency-stop-ghz', '60',
                '--expected-frequency-step-ghz', '1', '--expected-frequency-points', '56', '--expected-touchstone-extension', '.s4p', '--expected-ports', '4']
            self.process(root, 'cadence', command, cadence, cadence / 'parallel_candidate_queue_dataset_summary.json')
        audit_out = root / 'gds_audit'
        self.process(root, 'gds_audit', self.module_command('frequency_research_gds_audit', '--request', audit_request, '--out', audit_out),
                     audit_out, audit_out / 'REQUEST_GDS_AUDIT.json')
        audit = read_json(audit_out / 'REQUEST_GDS_AUDIT.json')
        features = []
        if audit['N_audit_pass']:
            drc_out = root / 'calibre'
            drc_request = root / 'CALIBRE_REQUEST.json'
            frozen_json(drc_request, dict(schema='frequency_research_calibre_request.v1', input_index=audit['calibre_input'],
                repo=self.config['repo'], out=str(drc_out), global_lock_path=self.config['global_lock_path'],
                script=self.config['calibre_script'], gds_hash_source=self.config['calibre_gds_hash'], runtime_sources=self.config['source_pins']))
            self.process(root, 'calibre', self.module_command('frequency_research_calibre', '--request', drc_request,
                '--inherited-global-lease-fd', self.fd), drc_out, drc_out / 'RESEARCH_WRAPPER_RECEIPT.json')
            drc_index = pin(drc_out / 'drc_index.csv')
            with Path(drc_index['path']).open(newline='') as stream:
                drc_rows = {r['candidate_id_sha256']: r for r in csv.DictReader(stream)}
            solver_records = [r for r in audit['records'] if r['status'] == 'PASS' and
                              drc_rows[r['candidate_id_sha256']]['overall_status'] == 'PASS']
            def solve_one(record):
                q = record['q_target']
                emx_request = root / f'EMX_Q{q}_REQUEST.json'
                frozen_json(emx_request, dict(schema='frequency_research_emx_request.v1', **{k: v for k, v in context.items() if k != 'target_source'},
                    candidate_id=record['candidate_id'], q_requested=q, records=record_pin, qscan_freeze=freeze_pin,
                    gds_audit=pin(audit_out / 'REQUEST_GDS_AUDIT.json'), calibre_index=drc_index, private_config=self.config['configuration'],
                    runtime=self.config['emx_runtime'], global_lock_path=self.config['global_lock_path'],
                    dispatch_deadline_utc=self.config['dispatch_deadline_utc'], resource_budget=self.config['resource_budget']))
                emx_out = root / f'emx_q{q}'
                # This outer receipt is conservative: an unreceipted previous
                # attempt is inspected, never relaunched under another path.
                feature_path = emx_out / 'features/FEATURE_RECEIPT.json'
                self.process(root, f'emx_q{q}', self.module_command('frequency_research_emx', 'run', '--request', emx_request,
                    '--output', emx_out, '--inherited-global-lease-fd', self.fd), emx_out, feature_path)
                return read_json(feature_path)
            if solver_records:
                # One actual native candidate per request is completed before
                # admitting the remaining originals to the global <=4 pool.
                features.append(solve_one(solver_records[0]))
                features.extend(bounded_map(solve_one, solver_records[1:], self.config['max_global_solvers']))
        value = summarize_request(job, records, features)
        artifacts = [pin(p) for p in sorted(root.rglob('*')) if p.is_file() and
                     (p.name.endswith('RECEIPT.json') or p.name.endswith('_PROCESS.json') or p.name == 'REQUEST_GDS_AUDIT.json')]
        artifacts = list({p['path']: p for p in [*artifacts, *self.resource_artifacts(root)]}.values())
        value.update(config=self.config_pin, frozen_job=job, artifacts=artifacts, completed_utc=utc_now())
        save_json(completed, value)
        self.event('REQUEST_ACCOUNTED', request=job['request_id'], N_solved=value['N_solved'], q_emx=value['q_emx'])
        return value

    def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.queue_fd = os.open(self.out / 'queue.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.queue_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.set_inheritable(self.queue_fd, True)
            if (self.out / 'TERMINAL.json').exists():
                require(read_json(self.out / 'TERMINAL.json')['config'] == self.config_pin, 'Terminal configuration changed')
                return read_json(self.out / 'TERMINAL.json')
            if getattr(self, 'resource_recovery', None):
                self.verify_resource_recovery(failures=True)
            else:
                require(not list(self.out.glob('FAILURE_*.json')), 'Previous failure preserved; not silently retried')
            if getattr(self, 'operational', None):
                require(any(read_json(p).get('status') == 'PARTIAL_BUDGET_ENDED_NO_CHILD_STOPPED' and
                            read_json(p).get('config') == self.config_pin for p in self.out.glob('PARTIAL_*.json')),
                        'Operational continuation requires original natural budget-exit receipt')
            self.event('WAIT_EXISTING_FIRST15')
            first = self.plan['jobs'][0]
            terminal = Path(first['existing_root']) / 'remaining_q11to18_queue_v1/TERMINAL.json'
            while not terminal.is_file():
                self.budget()
                require(not list(terminal.parent.glob('FAILURE_*.json')), 'Existing first15 failed; no duplicate dispatch')
                time.sleep(30)
            self.first15_reuse(first)
            self.admit()
            with global_lease(self.config['global_lock_path']) as fd:
                self.fd = fd
                if getattr(self, 'operational', None):
                    use_root = self.out / 'operational_budget_uses'
                    use_root.mkdir(exist_ok=True)
                    use = dict(
                        status='ADMITTED_UNDER_ORIGINAL_QUEUE_AND_GLOBAL_LEASES', budget=self.operational['pin'],
                        base_config=self.config_pin, original_deadline_utc=self.config['dispatch_deadline_utc'],
                        new_deadline_utc=self.operational['value']['new_dispatch_deadline_utc'],
                        release=self.operational['value']['release'], pid=os.getpid(), created_utc=utc_now())
                    if getattr(self, 'resource_recovery', None):
                        use['resource_wait_recovery'] = self.resource_recovery['pin']
                    self.operational_use = frozen_json(use_root / f'USE_{time.time_ns()}.json', use)
                self.event('GLOBAL_RESEARCH_LEASE_ACQUIRED', max_emx_concurrency=self.config['max_global_solvers'],
                           cadence_concurrency=1, per_request_initial_emx_concurrency=1)
                results = []
                for job in self.plan['jobs']:
                    self.budget()
                    results.append(self.execute_request(job))
                result = dict(status='FINITE_ROUNDROBIN_COMPLETE', config=self.config_pin, N_requests=len(results),
                    N_logical=len(results)*11, N_solved=sum(x['N_solved'] for x in results),
                    completed_utc=utc_now(), source_receipts=[pin(self.out / 'requests' / j['request_id'] / 'REQUEST_RECEIPT.json') for j in self.plan['jobs']],
                    production_modified=False)
                if getattr(self, 'operational', None):
                    result['operational_budget_use'] = self.operational_use
                save_json(self.out / 'TERMINAL.json', result)
                return result
        except BudgetEnded as error:
            value = dict(status='PARTIAL_BUDGET_ENDED_NO_CHILD_STOPPED', config=self.config_pin, utc=utc_now(), error=str(error))
            if getattr(self, 'operational', None):
                value.update(operational_budget=self.operational['pin'], operational_budget_use=self.operational_use)
            save_json(self.out / f'PARTIAL_{time.time_ns()}.json', value)
            return value
        except BlockingIOError:
            return dict(status='ALREADY_OWNED_NO_DUPLICATE', config=self.config_pin)
        except Exception as error:
            save_json(self.out / f'FAILURE_{time.time_ns()}.json', dict(status='FAIL_NO_RETRY', config=self.config_pin,
                utc=utc_now(), error=type(error).__name__+': '+str(error)))
            raise
        finally:
            os.close(self.queue_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--operational-budget', help='Separate immutable admission budget; never edits base config')
    parser.add_argument('--resource-wait-recovery', help='Exact preserved preflight-only failure manifest; never retries physics')
    args = parser.parse_args()
    runner = Dispatcher(args.config, args.operational_budget, args.resource_wait_recovery)
    if args.preflight and runner.resource_recovery:
        runner.verify_resource_recovery(failures=True)
    result = dict(status='INPUTS_VALIDATED_NO_SIMULATOR_LAUNCHED', N_requests=320) if args.preflight else runner.run()
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
