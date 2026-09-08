"""Synthetic contracts only: no real model, Cadence, Calibre, EMX, or SSH."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from research.broadband56_nn import frequency_research_emx as m


@pytest.fixture
def example(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    def raw(name, value):
        path = base / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
        return m.pin(path)
    config = raw('private_config.yaml', 'synthetic test config')
    deck = raw('foundry_deck', 'synthetic test deck')
    monkeypatch.setattr(m, 'CONFIG_SHA256', config['sha256'])
    monkeypatch.setattr(m, 'FOUNDRY_DECK_SHA256', deck['sha256'])
    core = [raw('repo/' + name, 'synthetic source ' + name) for name in m.REQUIRED_RUNTIME]
    wrapper = raw('wrapper', 'synthetic wrapper, never executed')
    proc = raw('process', 'synthetic process')
    geometry_fields = ['width', 'height']
    geometry_hash = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    records = [dict(candidate_id=f'original-request-q{q}', request_id='original-request', q_target=q,
                    frequency_ghz=15, model_id='f15-synthetic', dataset_scope='DEVELOPMENT_5K_NOT_FORMAL_10K', q_proxy=14,
                    analytic_grid=q < 19, geometry_fields=geometry_fields, grid_geometry=[float(q), 3.0],
                    target=[1.0, 1.1, float(q), .3], grid_proxy=[1.02, 1.05, float(q), .31]) for q in range(10, 21)]
    original = raw('eleven.jsonl', '\n'.join(json.dumps(r) for r in records) + '\n')
    protocol = dict(q_values=list(range(10, 21)), q_scalar='min(Qp,Qs)',
                    physical_optimum_requires='11/11 valid exact fresh EMX candidates',
                    score_scale=[2.5, 2.5, 20., .8], absolute_tolerances=[.125, .125, 1., .04])
    freeze_value = dict(status='FROZEN_BEFORE_QSCAN_AND_NEW_EMX', frequency_ghz=15, model_id='f15-synthetic',
                        config=dict(dataset_scope=records[0]['dataset_scope']), protocol=protocol)
    freeze = raw('QSCAN_FREEZE.json', freeze_value)
    candidate_sha = hashlib.sha256(records[0]['candidate_id'].encode()).hexdigest()
    geom_sha = geometry_hash(dict(zip(geometry_fields, records[0]['grid_geometry'])))
    gds, manifest = raw('cadence/exact.gds', 'synthetic GDS bytes'), raw('cadence/layout.json', 'synthetic manifest')
    checks = {k: True for k in m.GEOMETRY_CHECKS}
    geom = raw('geometry_audit.json', dict(overall_status='PASS', checks=checks, candidate_id_sha256=candidate_sha,
                candidate_geometry_identity_sha256=geom_sha, gds_sha256=gds['sha256'], gds_path=gds['path'],
                gds_timestamp_normalized_sha256='a' * 64, original_artifacts=[gds, manifest]))
    audit_records = [dict(candidate_id=r['candidate_id'], status='PASS' if r['analytic_grid'] else 'ANALYTIC_FAIL') for r in records]
    audit_records[0].update(candidate_id_sha256=candidate_sha, candidate_geometry_identity_sha256=geom_sha,
                            gds=gds, port_manifest=manifest, geometry_audit=geom)
    audit = raw('REQUEST_GDS_AUDIT.json', dict(records=audit_records,
                source_pins=dict(eleven_records=original, qscan_freeze=freeze, private_config=config)))
    report = raw('DRC.rep', 'synthetic zero-blocking DRC')
    drc = raw('drc_summary.json', dict(overall_status='PASS', blocking_drc_violation_count=0,
              drc_scope='foundry_macro_ip_back_end', checks={**checks, 'foundry_drc_pass': True,
                'no_blocking_drc_violations': True, 'calibre_result_accounting_complete': True},
              candidate_id_sha256=candidate_sha, candidate_geometry_identity_sha256=geom_sha, gds_sha256=gds['sha256'],
              gds_path=gds['path'], geometry_audit_sha256=geom['sha256'], process_token='/TSMC65_05_12_26/',
              gds_top_cell='TRANSFORMER', gds_timestamp_normalized_sha256='a' * 64,
              drc_report_path=report['path'], drc_report_sha256=report['sha256'],
              drc_source_rule_deck_path=deck['path'], drc_source_rule_deck_sha256=deck['sha256']))
    index = raw('drc_index.csv', 'candidate_id_sha256,drc_summary_path,drc_summary_sha256\n' +
                f"{candidate_sha},{drc['path']},{drc['sha256']}\n")
    req = dict(schema='frequency_research_emx_request.v1', request_id='original-request', candidate_id=records[0]['candidate_id'],
               frequency_ghz=15, q_requested=10, q_proxy=14, model_id='f15-synthetic', dataset_scope=records[0]['dataset_scope'],
               records=original, qscan_freeze=freeze, gds_audit=audit, calibre_index=index, private_config=config,
               runtime=dict(repo=str(base / 'repo'), source_pins=core, process_file=proc, emx_wrapper=wrapper),
               global_lock_path=str(base / 'physical.lock'), dispatch_deadline_utc='2099-01-01T00:00:00Z',
               resource_budget=dict(cpu_per_solver=2, max_global_solvers=1, min_memory_available_bytes=8 * 1024**3, min_disk_free_bytes=20 * 1024**3))
    request = raw('request.json', req)
    cfg = NS(target=NS(frequency_points_hz=lambda: m.FREQUENCIES), emx=NS(execution_mode='local',
          port_mode='single_ended_shield_grounded', cadence_pin_purpose=51, ground_unused_s8p_ports=False,
          differential_port_pairs=((0, 1), (2, 3)), power_line_8port=NS(touchstone_mode='signal_4_grounded_aux', port_map=m.PORTS)))
    calls = NS(solve=0, extraction=0, strict=True, solver_failure=False, wrong_frequency=False)
    class Sim:
        def __init__(self, work): self.work = Path(work); self._last_touchstone_path = None
        def connect(self): pass
        def disconnect(self): pass
        def _resolve_process_path(self): return Path(proc['path'])
        def _build_emx_command(self, gds_path):
            return [wrapper['path'], str(gds_path), 'TRANSFORMER', proc['path'], '--s-impedance=50', '--cadence-pins=51',
                    '--parallel=2', '--simultaneous-frequencies=0', *[f'--port={p}={p}:{p}_G' for p in m.PORTS],
                    '--sweep', '5000000000', '60000000000', '--sweep-stepsize', '1000000000']
        def create_project(self, path): Path(path).mkdir(parents=True, exist_ok=True)
        def run_solver(self):
            calls.solve += 1
            if calls.solver_failure: raise RuntimeError('synthetic solver failure')
            self._last_touchstone_path = self.work / 'emx/emx.s4p'
            self._last_touchstone_path.write_text('synthetic four-port exact56 fixture')
        def get_s_parameters(self):
            f = np.asarray(m.FREQUENCIES).copy()
            if calls.wrong_frequency: f[0] += 1
            return NS(num_ports=4, num_freqs=56, s_matrix=np.zeros((56, 4, 4), complex), freqs_hz=f, reference_impedance_ohm=50.)
    def audit_s4p(path):
        calls.extraction += 1
        rows = [dict(frequency_hz=f, lp_nh=1., ls_nh=1.1, qmin=12., k_abs=.3, qp=13., qs=12.,
                     broadband_descriptor_valid='true', strict_lumped_valid='true' if calls.strict else 'false',
                     passivity_status='PASS', reciprocity_status='PASS') for f in m.FREQUENCIES]
        return NS(rows=rows, summary=dict(primary_srf={'status': 'SYNTHETIC'}, secondary_srf={'status': 'SYNTHETIC'}))
    runtime = NS(load_config=lambda _: cfg, layout_type=lambda **k: NS(**k),
                 load_manifest=lambda _: NS(top_cell='TRANSFORMER', cadence_pin_purpose=51,
                  ports=[NS(name=p, signal_labels=[p], ground_labels=[p + '_G']) for p in m.PORTS]),
                 prepare=lambda **kw: (Sim(kw['work_dir']), []), geometry_fields=geometry_fields,
                 geometry_hash=geometry_hash, normalized_gds_hash=lambda _: 'a' * 64, audit_s4p=audit_s4p,
                 permutation=lambda: (0, 1, 3, 2))
    monkeypatch.setattr(m, 'load_runtime', lambda _: runtime)
    monkeypatch.setattr(m, 'resources', lambda *a: {'synthetic_resources': True})
    def change(**values):
        req.update(values); raw('request.json', req)
    return NS(base=base, request=Path(request['path']), output=base / 'candidate_out', request_value=req,
              change=change, raw=raw, calls=calls, runtime=runtime, cfg=cfg, records=records, freeze=freeze_value,
              audit_records=audit_records, gds=gds, manifest=manifest, geom=geom, drc=drc)


def test_preflight_no_solver_and_idempotent(example):
    one = m.preflight(example.request, example.output)
    assert one == m.preflight(example.request, example.output)
    assert example.calls.solve == example.calls.extraction == 0
    assert not (example.output / 'solve').exists()


def test_complete_reuse_and_missing_extraction_recovery(example):
    m.solve(example.request, example.output)
    assert example.calls.solve == 1 and example.calls.extraction == 0
    value = m.run_candidate(example.request, example.output)
    assert value['actual_fresh_emx'][2] == 12 and value['target_relative_signed_percent'][2] == 20
    assert value['q_proxy'] == 14 and value['q_emx'] is None and not value['strict_joint_hit']
    assert m.run_candidate(example.request, example.output) == value
    assert example.calls.solve == example.calls.extraction == 1


def test_partial_solver_is_never_repeated(example):
    m.preflight(example.request, example.output); (example.output / 'solve').mkdir()
    with pytest.raises(m.ResearchEmxError, match='PARTIAL_OR_FAILED_PHYSICS'): m.solve(example.request, example.output)
    assert example.calls.solve == 0


def test_partial_feature_directory_is_sticky_failure(example):
    m.solve(example.request, example.output); (example.output / 'features').mkdir()
    with pytest.raises(m.ResearchEmxError, match='PARTIAL_FEATURES'): m.extract(example.request, example.output)
    assert (example.output / 'FEATURE_FAILURE.json').exists()
    with pytest.raises(m.ResearchEmxError, match='FAILED_FEATURES'): m.extract(example.request, example.output)
    assert example.calls.solve == 1 and example.calls.extraction == 0


def test_solver_failure_preserved_and_not_retried(example):
    example.calls.solver_failure = True
    with pytest.raises(RuntimeError, match='synthetic solver failure'): m.solve(example.request, example.output)
    assert (example.output / 'SOLVER_FAILURE.json').exists()
    with pytest.raises(m.ResearchEmxError, match='PARTIAL_OR_FAILED_PHYSICS'): m.solve(example.request, example.output)
    assert example.calls.solve == 1


def test_exact_frequency_output_rejected(example):
    example.calls.wrong_frequency = True
    with pytest.raises(m.ResearchEmxError, match='exact56'): m.solve(example.request, example.output)
    assert not (example.output / 'SOLVER_RECEIPT.json').exists()


@pytest.mark.parametrize('value', [4, 21, 15.5, True])
def test_invalid_frequency(example, value):
    example.change(frequency_ghz=value)
    with pytest.raises(m.ResearchEmxError, match='Integer frequency'): m.preflight(example.request, example.output)
    assert example.calls.solve == 0


def test_analytic_failure_not_dispatched(example):
    example.change(candidate_id='original-request-q19', q_requested=19)
    with pytest.raises(m.ResearchEmxError, match='ANALYTIC_FAIL'): m.preflight(example.request, example.output)
    assert not example.output.exists()


def test_changed_source_rejected_before_solve(example):
    m.preflight(example.request, example.output)
    Path(example.request_value['runtime']['source_pins'][0]['path']).write_text('changed')
    with pytest.raises(m.ResearchEmxError, match='Immutable input mismatch'): m.solve(example.request, example.output)
    assert example.calls.solve == 0


def test_changed_gds_rejected_before_solve(example):
    Path(example.gds['path']).write_text('different GDS')
    with pytest.raises(m.ResearchEmxError, match='Immutable input mismatch'): m.preflight(example.request, example.output)
    assert example.calls.solve == 0


def test_invalid_strict_label_retained_not_promoted(example):
    example.calls.strict = False
    value = m.run_candidate(example.request, example.output)
    assert value['descriptor_valid'] is True and value['strict_lumped_valid'] is False
    assert value['valid_for_strict_comparison'] is False and value['actual_fresh_emx'][2] == 12
    assert value['q_emx'] is None


@pytest.mark.parametrize('frequency', [5, 20])
def test_non15_endpoints_share_sweep_but_extract_exact_frequency(example, frequency):
    model = f'f{frequency}-synthetic'
    for row in example.records:
        row.update(frequency_ghz=frequency, model_id=model, dataset_scope='FORMAL_10K')
    original = example.raw('eleven.jsonl', '\n'.join(json.dumps(r) for r in example.records) + '\n')
    example.freeze.update(frequency_ghz=frequency, model_id=model)
    example.freeze['config']['dataset_scope'] = 'FORMAL_10K'
    freeze = example.raw('QSCAN_FREEZE.json', example.freeze)
    audit = json.loads(Path(example.request_value['gds_audit']['path']).read_text())
    audit['source_pins'].update(eleven_records=original, qscan_freeze=freeze)
    audit_pin = example.raw('REQUEST_GDS_AUDIT.json', audit)
    example.change(frequency_ghz=frequency, model_id=model, dataset_scope='FORMAL_10K',
                   records=original, qscan_freeze=freeze, gds_audit=audit_pin)
    value = m.run_candidate(example.request, example.output)
    assert value['frequency_ghz'] == frequency
    assert value['original_frequency_row']['frequency_hz'] == frequency * 10**9
    assert json.loads((example.output / 'PREFLIGHT.json').read_text())['frequency_grid_hz'] == m.FREQUENCIES


def test_audit_source_config_crossbinding_rejected(example):
    value = json.loads(Path(example.request_value['gds_audit']['path']).read_text())
    value['source_pins']['private_config'] = dict(example.request_value['private_config'], sha256='0' * 64)
    example.change(gds_audit=example.raw('REQUEST_GDS_AUDIT.json', value))
    with pytest.raises(m.ResearchEmxError, match='audit used different'): m.preflight(example.request, example.output)
    assert example.calls.solve == 0


def test_empty_actual_checks_do_not_pass(example):
    value = json.loads(Path(example.geom['path']).read_text()); value['checks'] = {}
    geom = example.raw('geometry_audit.json', value)
    audit = json.loads(Path(example.request_value['gds_audit']['path']).read_text())
    audit['records'][0]['geometry_audit'] = geom
    example.change(gds_audit=example.raw('REQUEST_GDS_AUDIT.json', audit))
    with pytest.raises(m.ResearchEmxError, match='Actual geometry/foundry checks failed'): m.preflight(example.request, example.output)
    assert example.calls.solve == 0


def test_extract_different_request_rejected(example):
    m.solve(example.request, example.output)
    # Additional request metadata changes the immutable request identity.
    example.change(additional_note='changed after solve')
    with pytest.raises(m.ResearchEmxError, match='differs from completed preflight'): m.extract(example.request, example.output)
    assert example.calls.extraction == 0


def test_inherited_held_fd_and_false_fd(example):
    lock = example.base / 'fd.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(m.ResearchEmxError, match='does not represent a held lease'):
            with m.global_lease(lock, fd): pass
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with m.global_lease(lock, fd) as borrowed: assert borrowed == fd
        assert os.fstat(fd)  # Borrowed descriptor is not closed/unlocked.
        other = os.open(example.base / 'other.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with pytest.raises(m.ResearchEmxError, match='inode mismatch'):
                with m.global_lease(lock, other): pass
        finally: os.close(other)
    finally: os.close(fd)


def test_own_lease_excludes_separate_fd(example):
    lock = example.base / 'own.lock'
    with m.global_lease(lock):
        second = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError): fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally: os.close(second)


def test_optimized_python_rejected_without_runtime():
    script = 'from research.broadband56_nn.frequency_research_emx import guard; guard()'
    result = subprocess.run([sys.executable, '-O', '-B', '-c', script], capture_output=True, text=True)
    assert result.returncode != 0 and 'Python optimization is forbidden' in result.stderr
