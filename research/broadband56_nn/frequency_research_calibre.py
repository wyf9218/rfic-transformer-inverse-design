"""Lease-inheriting wrapper of the unchanged, hash-pinned standalone DRC CLI."""
import argparse
import csv
import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path

from .frequency_research_emx import global_lease, guard, pin, require, verify
from .io import read_json, save_json, utc_now


def run(request_path, inherited_fd):
    guard()
    request_pin = pin(request_path)
    request = read_json(request_path)
    require(request['schema'] == 'frequency_research_calibre_request.v1', 'Wrong DRC request')
    for key in ('input_index', 'script', 'gds_hash_source'):
        verify(request[key])
    for value in request['runtime_sources']:
        verify(value)
    out = Path(request['out'])
    require(not out.exists(), 'Partial or complete DRC output exists: never repeat it')
    rows = list(csv.DictReader(Path(request['input_index']['path']).open(newline='')))
    require(0 < len(rows) <= 11, 'DRC accepts only this request eligible candidates')
    sys.path.insert(0, request['repo'])
    import rfic_transformer_inverse_design.layout
    name = 'rfic_transformer_inverse_design.layout.gds_hash'
    spec = importlib.util.spec_from_file_location(name, request['gds_hash_source']['path'])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import gds_timestamp_normalized_sha256
    for row in rows:
        require(pin(row['gds_path'])['sha256'] == row['gds_sha256'], 'DRC input GDS changed')
        require(module.gds_timestamp_normalized_sha256(row['gds_path']) ==
                row['gds_timestamp_normalized_sha256'] == gds_timestamp_normalized_sha256(Path(row['gds_path'])),
                'DRC normalized GDS identity changed')
    with global_lease(request['global_lock_path'], inherited_fd) as fd:
        original_run = subprocess.run
        def inherited_run(*args, **kwargs):
            kwargs['pass_fds'] = tuple(set(kwargs.get('pass_fds', ())) | {fd})
            return original_run(*args, **kwargs)
        subprocess.run = inherited_run
        original_argv = sys.argv
        sys.argv = [request['script']['path'], '--input-index-csv', request['input_index']['path'],
                    '--out-dir', str(out), '--maximum-candidates', str(len(rows)),
                    '--calibre-module', 'mentor/old/2025', '--nice-level', '19']
        code = 0
        try:
            runpy.run_path(sys.argv[0], run_name='__main__')
        except SystemExit as error:
            code = int(error.code or 0)
        finally:
            subprocess.run = original_run
            sys.argv = original_argv
        require(code in (0, 1), 'Unexpected standalone Calibre exit')
        index = out / 'drc_index.csv'
        evidence = list(csv.DictReader(index.open(newline='')))
        require(len(evidence) == len(rows) and {x['candidate_id_sha256'] for x in evidence} ==
                {x['candidate_id_sha256'] for x in rows}, 'Incomplete DRC candidate evidence')
        require(pin(request_path) == request_pin, 'DRC request changed during execution')
        for key in ('input_index', 'script', 'gds_hash_source'):
            verify(request[key])
        for value in request['runtime_sources']:
            verify(value)
        for row in rows:
            require(pin(row['gds_path'])['sha256'] == row['gds_sha256'] and
                    module.gds_timestamp_normalized_sha256(row['gds_path']) ==
                    row['gds_timestamp_normalized_sha256'] == gds_timestamp_normalized_sha256(Path(row['gds_path'])),
                    'DRC source GDS identity changed during native execution')
        save_json(out / 'RESEARCH_WRAPPER_RECEIPT.json', dict(status='PROCESS_COMPLETE',
            native_returncode=code, input_request=request_pin, completed_utc=utc_now(),
            index=pin(index), summary=pin(out / 'tsmc65_calibre_macro_drc_batch_summary.json'),
            N_candidates=len(rows), N_pass=sum(x['overall_status'] == 'PASS' for x in evidence),
            production_modified=False, solver_started=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--inherited-global-lease-fd', type=int, required=True)
    args = parser.parse_args()
    run(args.request, args.inherited_global_lease_fd)
