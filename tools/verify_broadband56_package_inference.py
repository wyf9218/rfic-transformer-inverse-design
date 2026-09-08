#!/usr/bin/env python3
"""Small validation-only inference acceptance for an existing six-model package.

No training, simulator, full evaluation or accuracy ranking. Imports the copied
package's own software in a new CPU process, never the checkout's research code.
Python audit/profile guards are diagnostics, not a native security sandbox.
"""
import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import time
import traceback


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def pin(path):
    return {'path': str(path), 'sha256': sha(path), 'size_bytes': Path(path).stat().st_size}


def expected_blocked_platform_probe(path, writing, platform, caller_module, caller_function):
    """Recognize a still-DENIED optional Torch Linux probe on macOS only.

    Torch 2.8 _load_global_deps catches this unavailable /proc read. This does
    not grant read permission, permit a source fallback or ignore other denials.
    """
    return (platform == 'darwin' and not writing and str(path) == '/proc/self/maps'
            and caller_module == 'torch' and caller_function == '_load_global_deps')


def inside(root, base, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('Package operational references must be relative')
    result = (base / relative).resolve(strict=True)
    if not result.is_relative_to(root):
        raise ValueError('Package reference escapes root')
    return result


def package_tree(root, expected_receipt):
    if sha(root / 'PACKAGE_RECEIPT.json') != expected_receipt:
        raise ValueError('Wrong package receipt SHA')
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in package')
        if path.is_file():
            rows.append({'path': str(path.relative_to(root)), 'sha256': sha(path)})
    index = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        if name in index:
            raise ValueError('Duplicate SHA index entry')
        index[name] = digest
    if index != {r['path']: r['sha256'] for r in rows if r['path'] != 'SHA256SUMS'}:
        raise ValueError('Package file set/SHA closure mismatch')
    # Anchor executable bytes through the externally pinned root receipt, not
    # merely through a self-consistent, potentially replaced SHA index.
    for item in read(root / 'PACKAGE_RECEIPT.json')['results'].values():
        folder = inside(root, root, item['package_path'])
        if sha(folder / 'PACKAGE.json') != item['package_sha256']:
            raise ValueError('Component identity differs from root receipt')
        artifact = read(folder / 'PACKAGE.json')
        software = inside(root, folder, artifact['software_root'])
        identity = software / 'SOFTWARE_IDENTITY.json'
        if sha(identity) != artifact['software_identity_sha256']:
            raise ValueError('Executable source identity differs from component')
        source_records = read(identity)['files']
        for source in source_records:
            if sha(inside(root, software, source['path'])) != source['sha256']:
                raise ValueError('Packaged executable source SHA differs')
        actual_python = {str(p.relative_to(software)) for p in software.rglob('*.py')}
        if actual_python != {p['path'] for p in source_records if p['path'].endswith('.py')}:
            raise ValueError('Unexpected or missing package Python source')
    return rows


def resource_check(out):
    if sys.platform == 'darwin':
        raw = subprocess.check_output(['vm_stat'], text=True)
        page = int(re.search(r'page size of (\d+) bytes', raw).group(1))
        available = page * sum(int(re.search(r'Pages ' + name + r':\s+(\d+)', raw).group(1))
                               for name in ('free', 'inactive', 'speculative'))
        definition = 'macOS (free+inactive+speculative) pages; availability estimate, not reserved memory'
    elif sys.platform.startswith('linux'):
        raw = Path('/proc/meminfo').read_text()
        available = int(re.search(r'MemAvailable:\s+(\d+)', raw).group(1)) * 1024
        definition = 'Linux MemAvailable'
    else:
        raise RuntimeError('Unsupported resource observation platform')
    disk = shutil.disk_usage(out).free
    result = {'observed_utc': utc(), 'device': 'cpu', 'threads': 2,
              'available_estimate_bytes': available, 'available_definition': definition,
              'disk_free_bytes': disk, 'required_available_bytes': 2 * 1024**3,
              'required_disk_bytes': 1024**3, 'training_resource_gate': 'NOT_APPLICABLE_NO_TRAINING'}
    result['status'] = 'PASS' if available >= 2 * 1024**3 and disk >= 1024**3 else 'WAITING_RESOURCE'
    write(out / 'RESOURCE_OBSERVATION.json', result)
    if result['status'] != 'PASS':
        raise RuntimeError('Small CPU QA resource reserve unavailable')


def child(args):
    root, out = args.package, args.out
    software = root / 'software'
    result = {'schema': 'bb_packaged_small_inference_child.v1', 'started_utc': utc(),
              'pid': os.getpid(), 'parent_pid': os.getppid(), 'device': 'cpu',
              'scope': 'SMALL_VALIDATION_INTERFACE_INFERENCE', 'resume': 'NOT_RUN',
              'full_evaluation': 'NOT_RUN', 'real_emx_validation': 'NOT_RUN'}
    counts = {'optimizer_constructions': 0, 'optimizer_steps': 0, 'autograd_calls': 0,
              'denied_io_or_process_calls': 0, 'expected_blocked_platform_probes': 0}
    denied_events = []
    started = time.monotonic()
    try:
        if os.getppid() != args.child_parent:
            raise ValueError('Parent process identity mismatch')
        sys.path.insert(0, str(software))
        read_roots = [root, out, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                      Path('/System/Library'), Path('/usr/lib'), Path('/usr/share')]
        exact_reads = {Path(__file__).resolve(), Path('/dev/null'), Path('/etc/localtime').resolve()}

        def audit(event, values):
            if event in {'subprocess.Popen', 'os.system', 'os.exec', 'socket.connect', 'socket.bind'}:
                counts['denied_io_or_process_calls'] += 1
                raise RuntimeError('Inference child forbids subprocesses and network')
            if event != 'open' or not values or not isinstance(values[0], (str, bytes, os.PathLike)):
                return
            path = Path(os.fsdecode(values[0])).resolve()
            flags = values[2] if len(values) > 2 and isinstance(values[2], int) else 0
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if (writing and not path.is_relative_to(out)) or (not writing and path not in exact_reads
                    and not any(path.is_relative_to(folder) for folder in read_roots)):
                counts['denied_io_or_process_calls'] += 1
                caller = sys._getframe(1)
                expected = expected_blocked_platform_probe(path, writing, sys.platform,
                    caller.f_globals.get('__name__', ''), caller.f_code.co_name)
                counts['expected_blocked_platform_probes'] += int(expected)
                denied_events.append({'event': event, 'path': str(path), 'writing': writing,
                    'expected_blocked_platform_probe': expected,
                    'caller_file': caller.f_code.co_filename, 'caller_function': caller.f_code.co_name,
                    'caller_line': caller.f_lineno, 'access_was_granted': False})
                raise PermissionError('Inference-only Python file access denied: ' + str(path))

        sys.addaudithook(audit)
        import numpy as np
        import torch
        from research.broadband56_nn import evaluation, specs, seven_evaluation, training, physics
        from research.broadband56_nn.models import PACKAGE_MAPPING
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)

        def profile(frame, event, argument):
            if event != 'call':
                return
            module, name = frame.f_globals.get('__name__', ''), frame.f_code.co_name
            key = None
            if module.startswith('torch.optim.') and name in ('step', '__init__'):
                key = 'optimizer_steps' if name == 'step' else 'optimizer_constructions'
            elif module.startswith('torch.autograd') and name in ('grad', 'backward'):
                key = 'autograd_calls'
            if key:
                counts[key] += 1
                raise RuntimeError('Forbidden inference acceptance operation: ' + key)

        sys.setprofile(profile)
        receipt = read(root / 'PACKAGE_RECEIPT.json')
        labels = ('F1', 'F2', 'F3', 'FREF', *PACKAGE_MAPPING)
        if receipt.get('schema') != 'bb_delivery_package.v1' or set(receipt['results']) != set(labels):
            raise ValueError('Expected exactly four forward and six inverse package components')
        artifacts, records, contracts, folders = {}, {}, {}, {}
        data_root = None
        for label in labels:
            result_row = receipt['results'][label]
            folder = inside(root, root, result_row['package_path'])
            folders[label] = folder
            manifest = folder / 'PACKAGE.json'
            if sha(manifest) != result_row['package_sha256']:
                raise ValueError('Component manifest SHA mismatch')
            artifact = artifacts[label] = read(manifest)
            source = inside(root, folder, artifact['data_root'])
            if data_root is not None and source != data_root:
                raise ValueError('Components do not use one shared snapshot')
            data_root = source
            checkpoint = inside(root, folder, artifact['best_checkpoint'])
            records[label] = {'checkpoint': {'path': str(checkpoint), 'sha256': artifact['best_sha256']}}
            contracts[label] = inside(root, folder, artifact['contract_path'])
            if sha(contracts[label]) != artifact['contract_sha256']:
                raise ValueError('Runtime contract SHA mismatch')
        bundle = training.Bundle(data_root)
        if bundle.manifest['normalizer_fit_split'] != 'train' or bundle.data_sha != receipt['data_sha']:
            raise ValueError('Snapshot or train-only normalizer evidence differs')
        if any(artifact['data_manifest_sha'] != bundle.manifest_sha or artifact['data_sha'] != bundle.data_sha
               or artifact['normalizer_sha'] != bundle.norm_sha for artifact in artifacts.values()):
            raise ValueError('Component data/normalizer identity differs from copied data')
        array = bundle.arrays
        if not np.array_equal(array['frequency_hz'], np.arange(5, 61) * 1_000_000_000):
            raise ValueError('Frequency axis is not exact production56')
        eligible = ((array['split'] == 1) & array['y_valid'][:, 10].all(-1)
                    & np.isfinite(array['y'][:, 10]).all(-1))
        if 'strict_lumped_valid' in array:
            eligible &= array['strict_lumped_valid'][:, 10]
        indices = np.flatnonzero(eligible)[:args.batch]
        if len(indices) != args.batch:
            raise ValueError('Insufficient valid15GHz validation rows; no substitution')
        batch = bundle.batch(indices, 'cpu')
        if not bool(torch.isfinite(batch['geometry']).all() and torch.isfinite(batch['s']).all()):
            raise ValueError('Selected input geometry/S is nonfinite; no replacement')
        selection = {'rule': 'first eligible rows in original packaged dataset order, before model loading',
                     'eligibility': 'validation AND all4 finite strict-valid15GHz targets',
                     'indices': indices.tolist(), 'ids': array['geometry_ids'][indices].tolist(),
                     'batch': args.batch, 'data_sha': bundle.data_sha, 'normalizer_sha': bundle.norm_sha,
                     'snapshot_split_counts': bundle.manifest['split_counts'],
                     'npz_access': 'Bundle decodes all snapshot arrays including test storage; only selected validation rows enter models',
                     'test_inference': 'NOT_RUN', 'test_metrics': 'NOT_RUN', 'accuracy_metrics': 'NOT_RUN'}
        write(out / 'FROZEN_VALIDATION_INPUTS.json', selection)
        panel15 = seven_evaluation.physical15_spec(batch['y'][:, 10], bundle.frequency)
        panel_s = specs.make_spec(batch['s'], torch.zeros_like(batch['y']),
                                 torch.zeros_like(batch['y_valid']), bundle.frequency,
                                 np.random.default_rng(17), task='SPECTRUM', mode='full')
        panels = {'PHYSICAL_15GHZ_FOUR_TARGETS': panel15, 'SPECTRUM_FULL': panel_s}
        encoded, panel_evidence, saved = {}, {}, {}
        for name, spec in panels.items():
            tokens, condition = specs.tokenize(spec, bundle.norm)
            poisoned = {key: value.clone() if isinstance(value, torch.Tensor) else value for key, value in spec.items()}
            for prefix in ('s', 'y'):
                poisoned[prefix + '_target'][~spec[prefix + '_mask']] = float('nan')
                poisoned[prefix + '_tolerance'][~spec[prefix + '_mask']] = float('nan')
            poisoned['y_relation'][~spec['y_mask']] = 999
            hidden_tokens, hidden_condition = specs.tokenize(poisoned, bundle.norm)
            if not torch.equal(tokens, hidden_tokens) or not torch.equal(condition, hidden_condition):
                raise ValueError('Hidden values altered tokens')
            expected_conditions = 1 if name.startswith('PHYSICAL') else 56
            if tokens.shape != (args.batch, 56, 137) or not bool((condition.sum(-1) == expected_conditions).all()):
                raise ValueError('Input shape or requested-frequency count mismatch')
            if name.startswith('PHYSICAL') and (bool(spec['s_mask'].any()) or int(spec['y_mask'].sum()) != 4 * args.batch):
                raise ValueError('Physical interface includes extra/missing conditions')
            encoded[name] = tokens, condition
            panel_evidence[name] = {'token_shape': list(tokens.shape), 'condition_count_each': expected_conditions,
                                   'hidden_value_invariance': True}
            saved[name + '__tokens'] = tokens.numpy()
            saved[name + '__condition'] = condition.numpy()
        forwards, metadata, checked = {}, {}, []
        with torch.inference_mode():
            for label in labels:
                role = 'forward' if label.startswith('F') else 'inverse'
                kind = ('F1' if label == 'FREF' else label) if role == 'forward' else PACKAGE_MAPPING[label][1]
                model, meta = evaluation._load_model(records[label], bundle, 'cpu', role, kind)
                if training.canonical_sha(meta['contract']) != training.canonical_sha(read(contracts[label])):
                    raise ValueError('Checkpoint and packaged runtime contract differ')
                metadata[label] = meta
                if role == 'forward':
                    forwards[label] = model
                    response = evaluation._raw_forward(model, batch['geometry'], bundle)
                    if response.shape != (args.batch, 56, 32) or not bool(torch.isfinite(response).all()):
                        raise ValueError('Forward output shape or finiteness failure')
                    saved[label + '__source_geometry_s'] = response.numpy()
                    checked.append({'label': label, 'kind': kind, 'step': meta['step'],
                                    'checkpoint': records[label]['checkpoint'], 'inference': 'PASS'})
                    continue
                own_label = PACKAGE_MAPPING[label][0]
                reference = artifacts[label]['forward_reference']
                reference_path = inside(root, folders[label], reference['checkpoint_path'])
                own_meta = metadata[own_label]
                if (str(reference_path) != records[own_label]['checkpoint']['path']
                        or reference['sha256'] != records[own_label]['checkpoint']['sha256']
                        or reference['model_sha'] != own_meta['model_sha']
                        or meta['forward_model_sha'] != own_meta['model_sha']
                        or own_meta['model_sha'] == metadata['FREF']['model_sha']):
                    raise ValueError('Copied own-forward/FREF binding mismatch')
                decoder = training.decoder_from_contract(meta['contract'], 'cpu')
                task_rows = {}
                for name, (tokens, condition) in encoded.items():
                    logits = model(tokens, condition)
                    geometry = decoder(logits)
                    if logits.shape != (args.batch, 10) or geometry.shape != (args.batch, 10) or not bool(torch.isfinite(geometry).all()):
                        raise ValueError('Inverse shape or finiteness failure')
                    flags = evaluation._feasibility_flags(geometry.numpy(), decoder)
                    pass_flags = flags['analytical_pass'].numpy()
                    own_s = evaluation._raw_forward(forwards[own_label], geometry, bundle)
                    common_s = evaluation._raw_forward(forwards['FREF'], geometry, bundle)
                    if not bool(torch.isfinite(own_s).all() and torch.isfinite(common_s).all()):
                        raise ValueError('Tandem inference produced nonfinite S')
                    physical = physics.extract_physical(own_s, bundle.frequency, meta['contract']['port_contract'])
                    if physical['y'].shape != (args.batch, 56, 4):
                        raise ValueError('Physical extractor shape mismatch')
                    key = label + '__' + name
                    saved.update({key + '__geometry_um': geometry.numpy(), key + '__analytical_pass': pass_flags,
                                  key + '__own_s': own_s.numpy(), key + '__common_s': common_s.numpy(),
                                  key + '__own_y': physical['y'].numpy(), key + '__own_y_valid': physical['valid_strict'].numpy()})
                    task_rows[name] = {'interface': 'PASS', 'analytical_pass_each': pass_flags.tolist(),
                                       'own_forward': own_label, 'physical_validity_retained': True,
                                       'manufacturability': 'NOT_PROVEN', 'accuracy': 'NOT_EVALUATED'}
                if training.model_digest(model) != meta['model_sha']:
                    raise ValueError('Inverse model changed during inference')
                checked.append({'label': label, 'kind': kind, 'step': meta['step'],
                                'checkpoint': records[label]['checkpoint'], 'tasks': task_rows})
                del model, decoder
                gc.collect()
            for label, model in forwards.items():
                if training.model_digest(model) != metadata[label]['model_sha']:
                    raise ValueError('Forward changed during inference')
        with (out / 'INFERENCE_ARRAYS.npz').open('xb') as stream:
            np.savez_compressed(stream, **saved)
        imported = []
        for name, module in sorted(sys.modules.items()):
            if name.startswith('research.') and getattr(module, '__file__', None):
                path = Path(module.__file__).resolve()
                if not path.is_relative_to(software):
                    raise ValueError('Research source imported outside copied package')
                imported.append({'module': name, **pin(path)})
        result.update(components=checked, panels=panel_evidence,
                      selection=pin(out / 'FROZEN_VALIDATION_INPUTS.json'), outputs=pin(out / 'INFERENCE_ARRAYS.npz'),
                      imported_modules=imported, new_optimizer_updates=0,
                      python_guard='PYTHON_AUDIT_AND_PROFILE_ONLY_NOT_NATIVE_SANDBOX',
                      torch_source=pin(Path(torch.__file__).resolve()), torch_version=torch.__version__,
                      weights_unchanged_in_memory=True, scientific_maturity='NOT_ASSESSED_INTERFACE_CHECK_ONLY')
        if (len(checked) != 10 or any(counts[k] for k in ('optimizer_constructions', 'optimizer_steps', 'autograd_calls'))
                or counts['expected_blocked_platform_probes'] > 1
                or counts['denied_io_or_process_calls'] != counts['expected_blocked_platform_probes']):
            raise ValueError('Component count or unexpected operation guard mismatch')
        result['status'] = 'PASS_SMALL_VALIDATION_INFERENCE'
    except Exception as error:
        result.update(status='FAIL', error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    finally:
        sys.setprofile(None)
        result.update(completed_utc=utc(), elapsed_seconds=time.monotonic() - started, operation_counters=counts,
                      denied_events=denied_events, access_policy_relaxed=False,
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024))
        write(out / 'CHILD_RECEIPT.json', result)
    return 0 if result['status'] == 'PASS_SMALL_VALIDATION_INFERENCE' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--expected-package-receipt-sha256', required=True)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--batch', type=int, choices=range(1, 9), default=2)
    parser.add_argument('--child-parent', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.package = args.package.resolve(strict=True)
    args.out = args.out.resolve()
    if args.out == args.package or args.out.is_relative_to(args.package) or args.package.is_relative_to(args.out):
        raise ValueError('Output and package must be separate, non-nested directories')
    if args.child_parent is not None:
        return child(args)
    args.out.mkdir(parents=True, exist_ok=False)
    result = {'schema': 'bb_package_inference_acceptance.v1', 'started_utc': utc(), 'parent_pid': os.getpid(),
              'script': pin(Path(__file__).resolve()), 'package_root': str(args.package),
              'package_receipt_sha256': args.expected_package_receipt_sha256, 'batch': args.batch,
              'new_training_performed': False, 'resume': 'NOT_RUN', 'real_emx_validation': 'NOT_RUN'}
    before = None
    try:
        resource_check(args.out)
        before = package_tree(args.package, args.expected_package_receipt_sha256)
        write(args.out / 'PACKAGE_BEFORE.json', before)
        temp = args.out / 'tmp'
        temp.mkdir()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                   MKL_NUM_THREADS='2', TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp))
        env.pop('PYTHONPATH', None)
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--package', str(args.package),
                   '--expected-package-receipt-sha256', args.expected_package_receipt_sha256,
                   '--out', str(args.out), '--batch', str(args.batch), '--child-parent', str(os.getpid())]
        result['command'] = command
        with (args.out / 'CHILD.log').open('x') as log:
            process = subprocess.Popen(command, cwd=args.out, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            write(args.out / 'PROCESS.json', {'parent_pid': os.getpid(), 'child_pid': process.pid,
                                            'started_utc': utc(), 'training': False})
            print(json.dumps({'child_pid': process.pid, 'status': 'INFERENCE_ACCEPTANCE_RUNNING'}), flush=True)
            code = process.wait()
        after = package_tree(args.package, args.expected_package_receipt_sha256)
        write(args.out / 'PACKAGE_AFTER.json', after)
        proof = read(args.out / 'CHILD_RECEIPT.json')
        if code or proof['status'] != 'PASS_SMALL_VALIDATION_INFERENCE' or before != after:
            raise ValueError('Child acceptance failed or package changed')
        if proof['pid'] != process.pid or proof['parent_pid'] != os.getpid():
            raise ValueError('Child receipt process identity mismatch')
        result.update(status='PASS_SMALL_VALIDATION_INFERENCE', child_pid=process.pid, returncode=code,
                      package_unchanged=True, package_files=len(after), child_receipt=pin(args.out / 'CHILD_RECEIPT.json'))
    except Exception as error:
        result.update(status='FAIL', error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc(),
                      partial_outputs_preserved=True)
    result['completed_utc'] = utc()
    write(args.out / 'INFERENCE_ACCEPTANCE_RECEIPT.json', result)
    print(json.dumps({'status': result['status'], 'receipt': str(args.out / 'INFERENCE_ACCEPTANCE_RECEIPT.json')}, ensure_ascii=False))
    return 0 if result['status'] == 'PASS_SMALL_VALIDATION_INFERENCE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
