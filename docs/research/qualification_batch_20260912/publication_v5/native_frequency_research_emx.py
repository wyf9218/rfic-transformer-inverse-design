"""Research-only exact existing-GDS EMX and original exact56 label extraction.

No production launcher, GDS generation, target objective, model inference or Q
winner selection is invoked here. The caller owns finite scheduling. A request
and every evidence/runtime input must be pinned before preflight. Completed
solver outputs can be reused; partial physics or partial extraction is never
silently repeated. Python optimization is forbidden because legacy cores and
the research adapters contain assertions.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

from .io import canonical_sha, read_json, save_json, utc_now

CONFIG_SHA256 = '431ad59c22df2471746ab5a2eb73a3a7a6484fa511b71c9d6e6c93fb4b3d04d7'
FREQUENCIES = [n * 10**9 for n in range(5, 61)]
PORTS = ['P001', 'P002', 'P003', 'P004']
GEOMETRY_CHECKS = ('geometry_range_pass', 'topology_pass', 'line_width_sync_pass',
    'angle_45_135_pass', 'ground_clearance_pass', 'foundry_layout_audit_pass',
    'manufacturing_grid_canonicalization_pass', 'foundry_slotted_ground_frame_pass',
    'foundry_power_line_contract_pass', 'foundry_via_stack_and_landing_pad_pass',
    'foundry_bridge_connection_pass')
FOUNDRY_DECK_SHA256 = '8252a77efecf92d3b187d83f7047df45433ce662c53683996c175b2aa80653ef'
REQUIRED_RUNTIME = (
    'rfic_transformer_inverse_design/api.py',
    'rfic_transformer_inverse_design/core/types.py',
    'rfic_transformer_inverse_design/core/defaults.py',
    'rfic_transformer_inverse_design/network_analysis.py',
    'rfic_transformer_inverse_design/paths.py',
    'rfic_transformer_inverse_design/execution/zeus_cadence.py',
    'rfic_transformer_inverse_design/sim/emx/simulation.py',
    'rfic_transformer_inverse_design/sim/emx/layout_export.py',
    'rfic_transformer_inverse_design/sim/touchstone.py',
    'rfic_transformer_inverse_design/sim/base.py',
    'rfic_transformer_inverse_design/analysis/extraction.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_gds_identity.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py',
)


class ResearchEmxError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise ResearchEmxError(message)


def guard():
    require(__debug__ and sys.flags.optimize == 0,
            'Python optimization is forbidden; set PYTHONOPTIMIZE=0 and do not use -O')


def pin(path):
    path = Path(os.path.abspath(path))
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink input forbidden')
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), 'Expected regular immutable input')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'Input changed during hashing')
    return {'path': str(path), 'sha256': digest, 'bytes': after.st_size}


def verify(record):
    require(isinstance(record, dict) and set(('path', 'sha256', 'bytes')) <= set(record), 'Exact file pin required')
    actual = pin(record['path'])
    require(all(actual[k] == record[k] for k in actual), 'Immutable input mismatch: ' + str(record.get('path')))
    return Path(actual['path'])


OPERATIONAL_BUDGET_ENV = 'RFIC_RESEARCH_OPERATIONAL_BUDGET'
OPERATIONAL_USE_ENV = 'RFIC_RESEARCH_OPERATIONAL_BUDGET_USE'


def operational_budget(path, *, base_pin=None, executing_source=None):
    """Validate a separate admission budget; never merge it into science inputs."""
    budget_pin = pin(path)
    value = read_json(path)
    require(set(value) == {'schema', 'base_config', 'original_dispatch_deadline_utc',
                          'new_dispatch_deadline_utc', 'release'}, 'Unexpected operational budget fields')
    require(value['schema'] == 'frequency_physical_operational_budget.v1', 'Wrong operational budget schema')
    require(set(value['base_config']) == {'path', 'sha256', 'bytes'}, 'Unexpected operational base pin fields')
    verify(value['base_config'])
    require(base_pin is None or value['base_config'] == base_pin, 'Operational base config differs')
    base = read_json(value['base_config']['path'])
    require(base['schema'] == 'frequency_physical_finite_dispatch.v1', 'Wrong operational base schema')
    require(value['original_dispatch_deadline_utc'] == base['dispatch_deadline_utc'], 'Original deadline differs')
    old = datetime.fromisoformat(value['original_dispatch_deadline_utc'].replace('Z', '+00:00'))
    new = datetime.fromisoformat(value['new_dispatch_deadline_utc'].replace('Z', '+00:00'))
    require(old.tzinfo is not None and new.tzinfo is not None and new > old, 'New deadline must be strictly later')
    release = value['release']
    require(set(release) == {'code_root', 'source_pins'}, 'Unexpected operational release fields')
    code = Path(release['code_root'])
    require(code.is_absolute() and code != Path(base['code_root']), 'A separate release path is required')
    expected = ['research/__init__.py', 'research/broadband56_nn/__init__.py',
                *['research/broadband56_nn/' + name for name in
                  ('io.py', 'frequency_physical_dispatch.py', 'frequency_research_emx.py',
                   'frequency_research_gds_audit.py', 'frequency_research_calibre.py')]]
    require(all(set(p) == {'path', 'sha256', 'bytes'} for p in release['source_pins']), 'Unexpected operational source pin fields')
    sources = {str(verify(p)): p for p in release['source_pins']}
    require(len(sources) == len(release['source_pins']) == len(expected) and
            set(sources) == {str(code / name) for name in expected}, 'Incomplete or extra operational release sources')
    previous = {p['path']: p for p in base['source_pins']}
    for name in expected:
        if Path(name).name in ('frequency_physical_dispatch.py', 'frequency_research_emx.py'):
            continue
        original = previous.get(str(Path(base['code_root']) / name))
        require(original is not None, 'Original dependency pin missing')
        verify(original)
        require(all(sources[str(code / name)][k] == original[k] for k in ('sha256', 'bytes')),
                'Operational release changed a non-budget dependency')
    if executing_source is not None:
        require(pin(executing_source) == sources.get(str(Path(executing_source).absolute())),
                'Executing operational source is not release-pinned')
    return dict(pin=budget_pin, value=value, base=base)


def operational_deadline(request, output):
    """Only an admitted parent use may extend a new candidate's admission time."""
    path = os.environ.get(OPERATIONAL_BUDGET_ENV)
    use_path = os.environ.get(OPERATIONAL_USE_ENV)
    require(bool(path) == bool(use_path), 'Operational budget/use must be supplied together')
    if not path:
        return request['dispatch_deadline_utc']
    budget = operational_budget(path, executing_source=__file__)
    base, value = budget['base'], budget['value']
    use_file = verify(json.loads(use_path))
    require(use_file.is_relative_to(Path(base['out']) / 'operational_budget_uses'), 'Foreign operational use receipt')
    use = read_json(use_file)
    require(use['status'] == 'ADMITTED_UNDER_ORIGINAL_QUEUE_AND_GLOBAL_LEASES' and
            use['budget'] == budget['pin'] and use['base_config'] == value['base_config'] and
            use['original_deadline_utc'] == value['original_dispatch_deadline_utc'] and
            use['new_deadline_utc'] == value['new_dispatch_deadline_utc'] and use['release'] == value['release'],
            'Operational parent use binding differs')
    # The wrapper already holds/inherits the global lease; require the original
    # queue lease to be held too, so a saved use cannot authorize a free-standing run.
    queue = os.open(Path(base['out']) / 'queue.lock', os.O_RDWR)
    try:
        try:
            fcntl.flock(queue, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(queue, fcntl.LOCK_UN)
            raise ResearchEmxError('Operational wrapper requires a held original queue lease')
    finally:
        os.close(queue)
    for key, base_key in [('dispatch_deadline_utc', 'dispatch_deadline_utc'),
                          ('global_lock_path', 'global_lock_path'), ('resource_budget', 'resource_budget'),
                          ('private_config', 'configuration'), ('runtime', 'emx_runtime')]:
        require(request[key] == base[base_key], 'Operational budget cannot change scientific/runtime fields')
    plan = read_json(verify(base['dispatch_manifest']))
    jobs = [j for j in plan['jobs'] if j['request_id'] == request['request_id']]
    require(len(plan['jobs']) == 320 and len(jobs) == 1, 'Candidate outside original320 request plan')
    job = jobs[0]
    require(all(request[k] == job[k] for k in ('frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy')) and
            all(request['records'][k] == job['candidate_records'][k] for k in ('sha256', 'bytes')),
            'Operational request identity differs')
    require(Path(output).absolute() == Path(base['out']) / 'requests' / request['request_id'] / f"emx_q{request['q_requested']}",
            'Operational output must remain in original request directory')
    return value['new_dispatch_deadline_utc']


def load_runtime(repo):
    """Load only the already deployed low-level cores; reject cached foreign code."""
    repo = Path(repo).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo))
    from rfic_transformer_inverse_design import api
    from rfic_transformer_inverse_design.core.types import TransformerLayoutExport
    from rfic_transformer_inverse_design.execution import zeus_cadence
    from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as geometry
    from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity as identity
    from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as features
    from rfic_transformer_inverse_design.analysis.extraction import _external_to_internal_port_order
    for obj in (api.load_run_config, TransformerLayoutExport, zeus_cadence._prepare_emx_simulation,
                geometry.canonical_geometry_sha256, identity.gds_timestamp_normalized_sha256,
                features.audit_exact56_s4p, _external_to_internal_port_order):
        require(Path(inspect.getsourcefile(obj)).resolve().is_relative_to(repo), 'Foreign cached runtime module')
    return SimpleNamespace(load_config=api.load_run_config, layout_type=TransformerLayoutExport,
        load_manifest=zeus_cadence.load_emx_layout_manifest, prepare=zeus_cadence._prepare_emx_simulation,
        geometry_fields=list(geometry.GEOMETRY_FIELDS), geometry_hash=geometry.canonical_geometry_sha256,
        normalized_gds_hash=identity.gds_timestamp_normalized_sha256,
        audit_s4p=features.audit_exact56_s4p, permutation=_external_to_internal_port_order)


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _context(request_path, output):
    guard()
    request_pin = pin(request_path)
    request = read_json(request_path)
    if request.get('schema') == 'eucap15_production_geometry_emx_request.v1':
        from .eucap15_controlled_context import emx_context
        return emx_context(request_path, output)
    if request.get('schema') == 'eucap15_acquisition_emx_request.v1':
        from .eucap15_acquisition_context import emx_context
        return emx_context(request_path, output)
    selected_only = request.get('schema') == 'eucap15_selected_emx_request.v1'
    require(selected_only or request.get('schema') == 'frequency_research_emx_request.v1',
            'Exact research request schema required')
    f, q = request['frequency_ghz'], request['q_requested']
    require(type(f) is int and 5 <= f <= 20 and type(q) is int and 10 <= q <= 20, 'Integer frequency/Q outside supported route')
    require(request['q_proxy'] is None or type(request['q_proxy']) is int and 10 <= request['q_proxy'] <= 20, 'Invalid frozen q_proxy')
    pins = [request_pin]
    for name in ('records', 'qscan_freeze', 'gds_audit', 'calibre_index', 'private_config'):
        verify(request[name]); pins.append(request[name])
    selected_context = None
    if selected_only:
        from .eucap15_selected_context import load_selected_context
        require(request.get('production_campaign_membership') is False,
                'Selected request must not enter production campaign membership')
        selected_context = load_selected_context(request['selected_manifest'], request['request_id'],
                                                 path_map=request.get('path_map'))
        require(all(request[name] == value for name, value in selected_context.context.items()),
                'Selected request context differs from frozen original')
        require(request.get('development_binding') == getattr(selected_context, 'development_binding', None),
                'Development EMX binding mismatch')
        require(request['records'] == selected_context.records_pin and
                request['qscan_freeze'] == selected_context.freeze_pin,
                'Selected request records/freeze differ from resolved original pins')
        chosen = selected_context.selected
        require(chosen is not None and chosen['analytic_grid'] is True and
                chosen['candidate_id'] == request['candidate_id'] and q == request['q_proxy'] == chosen['q_target'],
                'Only original analytic-pass q_proxy may run; no failure replacement')
        pins += [selected_context.manifest_pin, selected_context.reference_pin]
    require(request['private_config']['sha256'] == CONFIG_SHA256, 'Private config identity differs from proven contract')
    runtime = request['runtime']
    repo = Path(runtime['repo']).resolve()
    by_path = {str(verify(p)): p for p in runtime['source_pins']}
    require(len(by_path) == len(runtime['source_pins']), 'Duplicate runtime source pin')
    require(all(str(repo / name) in by_path for name in REQUIRED_RUNTIME), 'Incomplete common runtime source closure')
    pins += runtime['source_pins']
    for name in ('process_file', 'emx_wrapper'):
        verify(runtime[name]); pins.append(runtime[name])
    rt = load_runtime(repo)
    records = selected_context.records if selected_only else _jsonl(request['records']['path'])
    require(len(records) == 11 and sorted(r['q_target'] for r in records) == list(range(10, 21)), 'Exactly original eleven Q records required')
    require(len({r['candidate_id'] for r in records}) == 11, 'Duplicate original candidate identity')
    for row in records:
        for name in ('request_id', 'frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy'):
            require(row[name] == request[name], 'Original record mismatch: ' + name)
    selected = [r for r in records if r['candidate_id'] == request['candidate_id']]
    require(len(selected) == 1 and selected[0]['q_target'] == q, 'Original Q candidate not found')
    row = selected[0]
    require(row['analytic_grid'] is True, 'ANALYTIC_FAIL_NOT_DISPATCHED: no success replacement')
    require(row['geometry_fields'] == rt.geometry_fields, 'Geometry field order mismatch')
    require(len(row['grid_geometry']) == len(rt.geometry_fields), 'Geometry dimensionality mismatch')
    geometry_sha = rt.geometry_hash(dict(zip(rt.geometry_fields, row['grid_geometry'])))
    candidate_sha = hashlib.sha256(row['candidate_id'].encode()).hexdigest()
    freeze = selected_context.freeze if selected_only else read_json(request['qscan_freeze']['path'])
    if not selected_only:
        require(freeze['status'] == 'FROZEN_BEFORE_QSCAN_AND_NEW_EMX' and freeze['frequency_ghz'] == f and freeze['model_id'] == request['model_id'] and
                freeze['config']['dataset_scope'] == request['dataset_scope'], 'Frozen model/frequency/scope mismatch')
    protocol = (selected_context.comparison_protocol
                if selected_only and hasattr(selected_context, 'development_binding') else freeze['protocol'])
    require(protocol['q_values'] == list(range(10, 21)) and protocol['q_scalar'] == 'min(Qp,Qs)',
            'Original integer Q grid and Q scalar required')
    if not selected_only:
        require(protocol['physical_optimum_requires'] == '11/11 valid exact fresh EMX candidates',
                'Original comparison protocol required')
    require(len(protocol['score_scale']) == len(protocol['absolute_tolerances']) == 4 and
            all(math.isfinite(float(x)) and x > 0 for x in (*protocol['score_scale'], *protocol['absolute_tolerances'])), 'Invalid frozen comparison scale')
    require(len(row['target']) == len(row['grid_proxy']) == 4 and
            all(math.isfinite(float(x)) and x > 0 for x in row['target']) and row['target'][2] == q, 'Exact original target required')
    audit = read_json(request['gds_audit']['path'])
    require(audit['source_pins']['eleven_records'] == request['records'] and
            audit['source_pins']['qscan_freeze'] == request['qscan_freeze'] and
            audit['source_pins']['private_config'] == request['private_config'], 'Actual GDS audit used different original records/freeze/config')
    audited = audit['records']
    require(len(audited) == 11 and {r['candidate_id'] for r in audited} == {r['candidate_id'] for r in records}, 'Audit must retain all eleven originals')
    if selected_only:
        require(audit.get('schema') == 'eucap15_selected_request_gds_audit.v1' and
                audit.get('physical_selection') == 'Q_PROXY_ONLY' and
                audit.get('selected_candidate_id') == row['candidate_id'] and
                audit.get('N_audit_attempted') == 1 and audit.get('N_selected') == 1 and
                audit['source_pins'].get('selected_manifest') == selected_context.manifest_pin and
                audit['source_pins'].get('reference') == selected_context.reference_pin,
                'Selected-only GDS audit binding differs')
        require(all(r['status'] == 'NOT_REQUESTED_MAIN_PILOT' and r['audit_attempted'] is False and
                    r['cadence_routed'] is False and r['calibre_eligible'] is False
                    for r in audited if r['candidate_id'] != row['candidate_id']),
                'Unselected Q candidates must not have physical execution in main pilot')
        require(audit.get('development_binding') == getattr(selected_context, 'development_binding', None),
                'GDS/EMX development identity mismatch')
    return _physical_context(request, request_pin, pins, rt, row, audited, runtime, protocol, output,
        f=f, q=q, selected_only=selected_only, selected_context=selected_context)


def _physical_context(request, request_pin, pins, rt, row, audited, runtime, protocol, output,
                      *, f, q, selected_only, selected_context):
    geometry_sha = rt.geometry_hash(dict(zip(rt.geometry_fields, row['grid_geometry'])))
    candidate_sha = hashlib.sha256(row['candidate_id'].encode()).hexdigest()
    selected_audit = [r for r in audited if r['candidate_id'] == row['candidate_id']][0]
    require(selected_audit['status'] == 'PASS', 'Actual GDS audit candidate is not PASS')
    require(selected_audit['candidate_id_sha256'] == candidate_sha and
            selected_audit['candidate_geometry_identity_sha256'] == geometry_sha, 'Actual GDS candidate identity mismatch')
    for name in ('gds', 'port_manifest', 'geometry_audit'):
        verify(selected_audit[name]); pins.append(selected_audit[name])
    gds = Path(selected_audit['gds']['path'])
    manifest_path = Path(selected_audit['port_manifest']['path'])
    geom_audit = read_json(selected_audit['geometry_audit']['path'])
    require(geom_audit['overall_status'] == 'PASS' and all(geom_audit['checks'].get(k) is True for k in GEOMETRY_CHECKS)
            and all(v is True for v in geom_audit['checks'].values()), 'Actual geometry/foundry checks failed')
    require(geom_audit['candidate_id_sha256'] == candidate_sha and geom_audit['candidate_geometry_identity_sha256'] == geometry_sha,
            'Geometry audit is for another candidate')
    require(geom_audit['gds_sha256'] == selected_audit['gds']['sha256'] and Path(geom_audit['gds_path']) == gds, 'Geometry audit GDS mismatch')
    require(selected_audit['port_manifest'] in geom_audit['original_artifacts'], 'Port manifest is not actual audited artifact')
    require(rt.normalized_gds_hash(gds) == geom_audit['gds_timestamp_normalized_sha256'], 'Normalized GDS mismatch')
    for p in geom_audit['original_artifacts']:
        verify(p); pins.append(p)
    with Path(request['calibre_index']['path']).open(newline='') as stream:
        drc_rows = [r for r in csv.DictReader(stream) if r['candidate_id_sha256'] == candidate_sha]
    require(len(drc_rows) == 1, 'Exactly one candidate-bound Calibre row required')
    drc_row = drc_rows[0]
    drc_pin = pin(drc_row['drc_summary_path'])
    require(drc_pin['sha256'] == drc_row['drc_summary_sha256'], 'Calibre receipt SHA mismatch')
    drc = read_json(drc_pin['path']); pins.append(drc_pin)
    require(drc['overall_status'] == 'PASS' and drc['blocking_drc_violation_count'] == 0 and
            drc['drc_scope'] == 'foundry_macro_ip_back_end' and all(v is True for v in drc['checks'].values()) and
            all(drc['checks'].get(k) is True for k in (*GEOMETRY_CHECKS, 'foundry_drc_pass', 'no_blocking_drc_violations', 'calibre_result_accounting_complete')),
            'Exact zero-blocking Calibre required')
    require(drc['candidate_id_sha256'] == candidate_sha and drc['candidate_geometry_identity_sha256'] == geometry_sha and
            drc['gds_sha256'] == selected_audit['gds']['sha256'] and Path(drc['gds_path']) == gds and
            drc['geometry_audit_sha256'] == selected_audit['geometry_audit']['sha256'], 'Calibre/GDS/geometry evidence mismatch')
    require(drc['process_token'] == '/TSMC65_05_12_26/' and drc['gds_top_cell'] == 'TRANSFORMER' and
            drc['gds_timestamp_normalized_sha256'] == geom_audit['gds_timestamp_normalized_sha256'] and
            drc['drc_source_rule_deck_sha256'] == FOUNDRY_DECK_SHA256, 'Calibre process/deck/normalized-GDS mismatch')
    for path_key, sha_key in (('drc_report_path', 'drc_report_sha256'), ('drc_source_rule_deck_path', 'drc_source_rule_deck_sha256')):
        p = pin(drc[path_key]); require(p['sha256'] == drc[sha_key], 'Calibre source/report changed'); pins.append(p)
    cfg = rt.load_config(Path(request['private_config']['path']))
    require([int(x) for x in cfg.target.frequency_points_hz()] == FREQUENCIES, 'Expected original exact56 sweep')
    require(cfg.emx.execution_mode == 'local' and cfg.emx.port_mode == 'single_ended_shield_grounded' and
            cfg.emx.cadence_pin_purpose == 51 and cfg.emx.ground_unused_s8p_ports is False and
            cfg.emx.power_line_8port.touchstone_mode == 'signal_4_grounded_aux' and
            list(cfg.emx.power_line_8port.port_map) == PORTS and
            tuple(tuple(p) for p in cfg.emx.differential_port_pairs) == ((0, 1), (2, 3)), 'Original config port contract mismatch')
    require(tuple(rt.permutation()) == (0, 1, 3, 2), 'Original extraction port permutation mismatch')
    manifest = rt.load_manifest(manifest_path)
    require(manifest.top_cell == 'TRANSFORMER' and manifest.cadence_pin_purpose == 51 and
            [p.name for p in manifest.ports] == PORTS and [list(p.signal_labels) for p in manifest.ports] == [[p] for p in PORTS] and
            [list(p.ground_labels) for p in manifest.ports] == [[p + '_G'] for p in PORTS], 'Actual port manifest contract mismatch')
    layout = rt.layout_type(gds_path=gds, manifest_path=manifest_path, preview_path=gds.with_suffix('.png'),
                          debug_preview_path=gds.with_name('unused_preview.png'), top_cell=manifest.top_cell)
    sim, _ = rt.prepare(run_config=cfg, work_dir=Path(output) / 'solve', layout=layout, manifest=manifest)
    sim.connect()  # Existing path resolver only; never runs a child.
    command = sim._build_emx_command(gds)
    require(pin(command[0]) == runtime['emx_wrapper'] and pin(sim._resolve_process_path()) == runtime['process_file'], 'Resolved native wrapper/process mismatch')
    require([str(x) for x in command if str(x).lower().endswith('.gds')] == [str(gds)], 'Command must use exactly original audited GDS')
    require('--s-impedance=50' in command and '--cadence-pins=51' in command and '--parallel=2' in command and
            '--simultaneous-frequencies=0' in command and
            [x for x in command if x.startswith('--port=')] == [f'--port={p}={p}:{p}_G' for p in PORTS] and
            command[command.index('--sweep') + 1:command.index('--sweep') + 3] == ['5000000000', '60000000000'] and
            command[command.index('--sweep-stepsize') + 1] == '1000000000', 'Resolved command differs from exact56/4port/50ohm/thread2 contract')
    pins.append(pin(__file__))
    proof = dict(schema='frequency_research_emx_preflight.v1', status='PASS', request=request_pin,
        request_id=request['request_id'], candidate_id=row['candidate_id'], candidate_id_sha256=candidate_sha,
        geometry_sha256=geometry_sha, frequency_ghz=f, q_requested=q, q_proxy=request['q_proxy'], q_emx=None,
        model_id=request['model_id'], dataset_scope=request['dataset_scope'], original_record=row,
        original_candidate_statuses=[{'candidate_id': r['candidate_id'], 'status': r['status']} for r in audited],
        source_pins=list({p['path']: p for p in pins}.values()), gds=selected_audit['gds'], port_manifest=selected_audit['port_manifest'],
        calibre=drc_pin, command=command, frequency_grid_hz=FREQUENCIES, port_order=PORTS, port_permutation=[0, 1, 3, 2],
        config_differential_port_pairs=[[0, 1], [2, 3]], reference_ohm=50, protocol=protocol, output=str(Path(output).absolute()),
        production_membership=False, no_gds_generation=True, no_example_target_objective=True)
    if selected_only:
        proof.update(schema='eucap15_selected_emx_preflight.v1', physical_selection='Q_PROXY_ONLY',
            selected_manifest=selected_context.manifest_pin, reference=selected_context.reference_pin,
            executed_hit_tolerances=(selected_context.executed_hit_tolerances
                if hasattr(selected_context, 'development_binding') else selected_context.freeze['executed_legacy_tolerance_float64']),
            original_request_denominator=(selected_context.original_request_denominator
                if hasattr(selected_context, 'development_binding') else selected_context.manifest['N_requests']),
            unselected_physical_status='NOT_REQUESTED_MAIN_PILOT',
            full11_physical_optimum='NOT_EVALUATED_SINGLE_PRESELECTED_CANDIDATE')
        if hasattr(selected_context, 'development_binding'):
            proof['development_binding'] = selected_context.development_binding
    return request, proof, sim, rt


def preflight(request_path, output):
    output = Path(output).absolute()
    require(not any(p.is_symlink() for p in (output, *output.parents)), 'Symlink output forbidden')
    _, proof, sim, _ = _context(request_path, output)
    sim.disconnect()
    path = output / 'PREFLIGHT.json'
    if output.exists():
        require(path.is_file() and read_json(path) == proof, 'Existing output is not the same frozen request')
    else:
        output.mkdir(parents=True, exist_ok=False); save_json(path, proof)
    return pin(path)


@contextmanager
def global_lease(path, inherited_fd=None):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink lock forbidden')
    own = inherited_fd is None
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600) if own else inherited_fd
    try:
        s, expected = os.fstat(fd), path.stat()
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and (s.st_dev, s.st_ino) == (expected.st_dev, expected.st_ino), 'Lease FD/owner/inode mismatch')
        require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR, 'Lease FD must be read/write')
        if not own:
            probe = os.open(path, os.O_RDWR)
            try:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    fcntl.flock(probe, fcntl.LOCK_UN)
                    raise ResearchEmxError('Inherited FD does not represent a held lease')
            finally:
                os.close(probe)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(fd, True)
        yield fd
    finally:
        if own:
            os.close(fd)


def resources(request, output):
    budget = request['resource_budget']
    ceiling=4
    if 'controlled_execution' in request:
        import controlled_execution as execution
        active=execution.document(request['controlled_execution']['release'])
        if active.get('concurrency_amendment'):
            config=execution.document(active['config'])
            execution.plan_release(active,config)
            require(config['resource_budget']==budget,'Exact fixed48 resource binding required')
            ceiling=48
    require(budget['cpu_per_solver'] == 2 and 1 <= budget['max_global_solvers'] <= ceiling, 'Resource contract must bound native2 and authorized global capacity')
    deadline = datetime.fromisoformat(operational_deadline(request, output).replace('Z', '+00:00'))
    require(deadline.tzinfo is not None and datetime.now(timezone.utc) < deadline, 'Dispatch budget expired; do not interrupt any existing solver')
    values = {s.split(':')[0]: int(s.split(':')[1].split()[0]) * 1024 for s in Path('/proc/meminfo').read_text().splitlines() if ':' in s}
    r = dict(cpu_logical=os.cpu_count(), loadavg=list(os.getloadavg()), available_memory_bytes=values['MemAvailable'], free_disk_bytes=shutil.disk_usage(output).free, observed_utc=utc_now())
    cpu_ok=(r['loadavg'][0]/r['cpu_logical']<=1.10 and r['loadavg'][1]/r['cpu_logical']<=1.10) if ceiling==48 else r['loadavg'][0]<r['cpu_logical']-2
    require(r['cpu_logical'] >= 4 and cpu_ok and
            r['available_memory_bytes'] >= max(8 * 1024**3, budget['min_memory_available_bytes']) and
            r['free_disk_bytes'] >= max(20 * 1024**3, budget['min_disk_free_bytes']), 'RESOURCE_WAIT_REQUIRED_NO_DISPATCH')
    return r


def _validate_solver_receipt(output, proof):
    path = Path(output) / 'SOLVER_RECEIPT.json'
    receipt = read_json(path)
    require(read_json(Path(output) / 'PREFLIGHT.json') == proof, 'Current request differs from completed preflight')
    require(receipt['status'] == 'PASS' and receipt['preflight'] == pin(Path(output) / 'PREFLIGHT.json') and
            receipt['candidate_id'] == proof['candidate_id'] and receipt['source_gds_before'] == receipt['source_gds_after'] == proof['gds'], 'Completed solver identity mismatch')
    for p in proof['source_pins'] + receipt['artifacts']:
        verify(p)
    require(verify(receipt['touchstone']).is_relative_to(Path(output) / 'solve'), 'Touchstone escaped solver output')
    return receipt


def solve(request_path, output, *, inherited_global_lease_fd=None):
    preflight(request_path, output)
    output = Path(output).absolute()
    request, proof, sim, _ = _context(request_path, output)
    controlled = proof.get('physical_selection') == 'FROZEN_PRODUCTION_GEOMETRY_SINGLE'
    if (output / 'SOLVER_RECEIPT.json').exists():
        sim.disconnect(); return _validate_solver_receipt(output, proof)
    require(not (output / 'solve').exists() and not (output / 'SOLVER_FAILURE.json').exists(), 'PARTIAL_OR_FAILED_PHYSICS: do not repeat solver')
    with global_lease(request['global_lock_path'], inherited_global_lease_fd) as lease_fd:
        for p in proof['source_pins']:
            verify(p)
        observed = resources(request, output)
        native_hook = None
        if controlled:
            from controlled_execution import prepare_native_dispatch
            try:
                native_hook = prepare_native_dispatch(request, proof, output, lease_fd)
            except Exception:
                sim.disconnect()
                raise
        run = output / 'solve'; run.mkdir(exist_ok=False)
        save_json(run / 'RUNNING.json', dict(pid=os.getpid(), started_utc=utc_now(), resources=observed, lease_inherited=inherited_global_lease_fd is not None))
        before = pin(proof['gds']['path'])
        original_run = subprocess.run
        def inherited_run(*args, **kwargs):
            kwargs['pass_fds'] = tuple(set(kwargs.get('pass_fds', ())) | {lease_fd})
            if native_hook is not None:
                require(len(args) == 1, 'Single exact solver argv required')
                return native_hook(args[0], kwargs)
            return original_run(*args, **kwargs)
        try:
            sim.create_project(run / 'emx'); save_json(run / 'emx/emx_command.json', proof['command'])
            require(sim._build_emx_command(Path(proof['gds']['path'])) == proof['command'], 'Solver command changed after preflight')
            subprocess.run = inherited_run
            try:
                sim.run_solver()
            finally:
                subprocess.run = original_run; sim.disconnect()
            require(pin(before['path']) == before, 'Original GDS changed during solver')
            for p in proof['source_pins']:
                verify(p)
            import numpy as np
            result = sim.get_s_parameters(); touchstone = Path(sim._last_touchstone_path)
            require(touchstone.is_relative_to(run) and touchstone.suffix == '.s4p', 'Expected own raw .s4p')
            require(result.num_ports == 4 and result.num_freqs == 56 and result.s_matrix.shape == (56, 4, 4) and
                    np.array_equal(result.freqs_hz, np.asarray(FREQUENCIES)) and np.isfinite(result.s_matrix).all() and
                    np.all(np.asarray(result.reference_impedance_ohm) == 50.0), 'Fresh S4P exact56/4port/50ohm/finite contract failed')
            require(not list(run.rglob('*.gds')) and not any(p.is_symlink() for p in run.rglob('*')), 'Unexpected generated GDS or symlink')
            receipt = dict(schema='frequency_research_fresh_solver.v1', status='PASS', candidate_id=proof['candidate_id'],
                preflight=pin(output / 'PREFLIGHT.json'), source_gds_before=before, source_gds_after=pin(before['path']),
                touchstone=pin(touchstone), artifacts=[pin(p) for p in sorted(run.rglob('*')) if p.is_file()],
                ended_utc=utc_now(), real_emx=True, production_modified=False, q_emx=None)
            save_json(output / 'SOLVER_RECEIPT.json', receipt)
            return receipt
        except BaseException as exc:
            save_json(output / 'SOLVER_FAILURE.json', dict(status='FAIL_NO_AUTOMATIC_RETRY', error=f'{type(exc).__name__}: {exc}', ended_utc=utc_now()))
            raise


def _clean(value):
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, dict): return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [_clean(v) for v in value]
    return value


def _extract(request_path, output):
    output = Path(output).absolute()
    _, proof, sim, rt = _context(request_path, output); sim.disconnect()
    receipt = _validate_solver_receipt(output, proof)
    target = output / 'features'
    if target.exists():
        require((target / 'FEATURE_RECEIPT.json').exists() and (target / 'MANIFEST.json').exists(), 'PARTIAL_FEATURES: preserve and do not overwrite')
        value = read_json(target / 'FEATURE_RECEIPT.json')
        require(value['status'] == 'PASS_EXTRACTION' and value['preflight'] == pin(output / 'PREFLIGHT.json') and
                value['solver_receipt'] == pin(output / 'SOLVER_RECEIPT.json') and value['candidate_id'] == proof['candidate_id'], 'Existing feature receipt mismatch')
        manifest = read_json(target / 'MANIFEST.json')
        require(manifest['inputs_unchanged'] is True and pin(target / 'FEATURE_RECEIPT.json') in manifest['artifacts'], 'Incomplete feature manifest')
        for p in manifest['artifacts']: verify(p)
        return value
    result = rt.audit_s4p(Path(receipt['touchstone']['path']))
    rows = list(result.rows)
    require(len(rows) == 56 and [int(r['frequency_hz']) for r in rows] == FREQUENCIES, 'Original feature extractor did not retain exact56')
    row = rows[proof['frequency_ghz'] - 5]
    actual = [row[k] for k in ('lp_nh', 'ls_nh', 'qmin', 'k_abs')]
    original = proof['original_record']; wanted, proxy = original['target'], original['grid_proxy']
    errors = [a - t for a, t in zip(actual, wanted)] if wanted is not None else None
    scale = proof['protocol']['score_scale']
    tau = proof.get('executed_hit_tolerances', proof['protocol']['absolute_tolerances'])
    finite = all(math.isfinite(v) for v in actual)
    descriptor = str(row['broadband_descriptor_valid']).lower() == 'true'
    strict = str(row['strict_lumped_valid']).lower() == 'true'
    physics = row['passivity_status'] == row['reciprocity_status'] == 'PASS'
    hits = [bool(math.isfinite(e) and abs(e) <= t) for e, t in zip(errors, tau)] if errors is not None else None
    value = _clean(dict(schema='frequency_research_fresh_features.v1', status='PASS_EXTRACTION',
        candidate_id=proof['candidate_id'], frequency_ghz=proof['frequency_ghz'], q_requested=proof['q_requested'], q_proxy=proof['q_proxy'], q_emx=None,
        model_id=proof['model_id'], dataset_scope=proof['dataset_scope'], solver_receipt=pin(output / 'SOLVER_RECEIPT.json'),
        preflight=pin(output / 'PREFLIGHT.json'), actual_fresh_emx=actual, target=wanted, proxy_self=proxy,
        emx_minus_target=errors, emx_minus_proxy=[a - p for a, p in zip(actual, proxy)] if proxy is not None else None,
        target_relative_signed_percent=[100 * e / t for e, t in zip(errors, wanted)] if wanted is not None else None,
        target_relative_absolute_percent=[100 * abs(e) / t for e, t in zip(errors, wanted)] if wanted is not None else None,
        percent_definition='100*(fresh_EMX-original_target)/original_target; not declared hit tolerance',
        score_scale=scale, absolute_hit_tolerances=tau, within_tolerance=hits, joint_response_hit=all(hits) if hits is not None else None,
        normalized_response_score=math.sqrt(sum((e / s)**2 for e, s in zip(errors, scale)) / 4) if finite and errors is not None else None,
        descriptor_valid=descriptor, strict_lumped_valid=strict, physics_qa_pass=physics,
        valid_for_strict_comparison=finite and descriptor and strict and physics,
        strict_joint_hit=all(hits) and finite and descriptor and strict and physics if hits is not None else None,
        original_56_summary=result.summary, original_frequency_row=row, original_candidate_statuses=proof['original_candidate_statuses'],
        q_optimum_status='NOT_COMPUTED_BY_SINGLE_CANDIDATE_ADAPTER_REQUIRE_ORIGINAL_11_VALID', generated_utc=utc_now(),
        nonfinite_values_preserved_in_csv=True, json_nonfinite_representation='null', production_membership=False))
    if proof.get('physical_selection') == 'Q_PROXY_ONLY':
        value.update(schema='eucap15_selected_fresh_features.v1', physical_selection='Q_PROXY_ONLY',
            selected_manifest=proof['selected_manifest'], reference=proof['reference'],
            original_request_denominator=proof['original_request_denominator'],
            unselected_physical_status='NOT_REQUESTED_MAIN_PILOT',
            q_optimum_status='NOT_EVALUATED_MAIN_PRESELECTED_CANDIDATE')
    if proof.get('physical_selection') == 'FROZEN_ACQUISITION_SINGLE':
        original = proof['original_proposal']
        value.update(schema='eucap15_acquisition_fresh_features.v1', physical_selection='FROZEN_ACQUISITION_SINGLE',
            acquisition_manifest=proof['acquisition_manifest'], acquisition_recipe=proof['acquisition_recipe'],
            original_proposal=original, original_proposal_denominator=256,
            q_optimum_status='NOT_EVALUATED_NO_Q_REPLACEMENT', target_errors_defined=wanted is not None,
            core15_eligible=finite and descriptor and strict and physics and .5<=actual[0]<=2 and .5<=actual[1]<=2 and .2<=actual[3]<=.85,
            q10_to20_supported=finite and 10<=actual[2]<=20)
    if proof.get('physical_selection') == 'FROZEN_PRODUCTION_GEOMETRY_SINGLE':
        from .eucap15_controlled_context import add_feature_binding
        add_feature_binding(value, proof)
    if 'development_binding' in proof:
        value['development_binding'] = proof['development_binding']
    target.mkdir(exist_ok=False)
    with (target / 'features_56.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    save_json(target / 'FEATURE_RECEIPT.json', value)
    _validate_solver_receipt(output, proof)
    save_json(target / 'MANIFEST.json', dict(artifacts=[pin(p) for p in sorted(target.iterdir()) if p.is_file()], inputs_unchanged=True))
    return value


def extract(request_path, output):
    failure = Path(output) / 'FEATURE_FAILURE.json'
    require(not failure.exists(), 'FAILED_FEATURES: preserve failure; no automatic repeat')
    try:
        return _extract(request_path, output)
    except BaseException as exc:
        if Path(output).is_dir() and (Path(output) / 'PREFLIGHT.json').is_file() and not failure.exists():
            save_json(failure, dict(status='FAIL_NO_AUTOMATIC_RETRY', error=f'{type(exc).__name__}: {exc}', ended_utc=utc_now()))
        raise


def run_candidate(request_path, output, *, inherited_global_lease_fd=None):
    solve(request_path, output, inherited_global_lease_fd=inherited_global_lease_fd)
    return extract(request_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('preflight', 'solve', 'extract', 'run'))
    parser.add_argument('--request', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--inherited-global-lease-fd', type=int)
    args = parser.parse_args()
    if args.operation == 'preflight': result = preflight(args.request, args.output)
    elif args.operation == 'extract': result = extract(args.request, args.output)
    else: result = (solve if args.operation == 'solve' else run_candidate)(args.request, args.output, inherited_global_lease_fd=args.inherited_global_lease_fd)
    print(json.dumps(result, allow_nan=False, sort_keys=True))


if __name__ == '__main__':
    main()
