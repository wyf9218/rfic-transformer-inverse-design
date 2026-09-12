"""One new-case integration at the real export entry; stop before GDS/native.

This is not a replay of the six-case or negative-guard suites. It verifies
the newly wired CLI/evaluator/export policy, same-invocation lineage and
resolved metadata writeback. Original sources/results are not overwritten.
"""
import argparse
from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch


def pin(p):
    p = Path(p).absolute(); raw = p.read_bytes()
    return dict(path=str(p), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class WiringCaptured(Exception):
    pass


def main():
    p = argparse.ArgumentParser()
    for name in ('repo', 'replay', 'cases', 'evidence', 'aliases', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); a.out.mkdir(parents=True, exist_ok=False)
    subprocess.Popen = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('NATIVE_FORBIDDEN'))
    assert pin(a.replay)['sha256'] == '7088341440eb8afe73e84150448f3761aa710296917e8b7843b9d8b1a9ccf52b'
    assert pin(a.cases)['sha256'] == 'dcf9f8ff184a939d7b052d6f3f6f627f8ce74e22462c4b2ca0d2a0014781df60'
    assert pin(a.evidence)['sha256'] == '020db65bb9cb82dd6511a8d08fe6710c4c596782020f3cbc121e5cc8e6f14b1c'
    prior = json.loads(a.replay.read_bytes())
    old = prior['results'][0]; assert old['request_id'].endswith('GEOMETRY_DOE-027')
    assert pin(old['capture']['path']) == old['capture']
    saved = json.loads(Path(old['capture']['path']).read_bytes())
    case = next(r for r in json.loads(a.cases.read_bytes())['rows'] if r['request_id'] == old['request_id'])
    evidence = json.loads(a.evidence.read_bytes())
    record = next(r for r in evidence['results'] if r['request_id'] == old['request_id'])
    entry = next(p for p in record['directly_referenced_files'] if p['path'].endswith('/candidate_queue_dataset_summary.json'))
    local_entry = next(f for f in evidence['files'] if f['pin'] == entry)['local_path']
    assert pin(local_entry)['sha256'] == entry['sha256']
    args = json.loads(Path(local_entry).read_bytes())['arguments']
    aliases = json.loads(a.aliases.read_bytes())
    sys.path.insert(0, str(a.repo.absolute()))
    from rfic_transformer_inverse_design.api import load_run_config, TransformerOptimizationAdapter
    from rfic_transformer_inverse_design.execution.evaluator import TransformerEmxEvaluator
    from rfic_transformer_inverse_design.layout import export, port_endpoint_lineage as lineage
    from scripts import run_candidate_queue_dataset as single
    from scripts import run_candidate_queue_dataset_parallel as parallel
    assert lineage.POLICY == 'lineage_cross_binding_20260912_v2'
    cfg = load_run_config(Path(aliases[args['config']]))
    cfg = single._apply_overrides(cfg, SimpleNamespace(**{k: args[k] for k in (
        'force_wideband_5_60_1p0', 'force_wideband_5_60_0p5', 'force_wideband_5_50_0p1',
        'force_port_mode', 'force_cadence_pin_purpose')}))
    for previous in prior['physical_input_pins']:
        assert pin(previous['local']['path']) == previous['local']
    cfg = replace(cfg, emx=replace(cfg.emx, emx_process_file=Path(aliases[str(cfg.emx.emx_process_file)])))
    adapter = TransformerOptimizationAdapter(cfg.bounds)
    source_geometry = dict(zip(case['geometry_fields'], case['geometry']))
    vector = [source_geometry['line_width_um'] if k in ('primary_width_um', 'secondary_width_um')
              else source_geometry[k] for k in adapter.field_order()]
    geometry = adapter.from_vector(vector).with_shared_line_width(source_geometry['line_width_um'])
    assert [geometry.flat_dict()[k] for k in case['geometry_fields']] == case['geometry']
    cli = []
    for module in (single, parallel):
        argv = ['--candidate-csv', 'NOT_READ.csv', '--out-dir', str(a.out / 'NOT_RUN')]
        assert module._parse_args(argv).port_endpoint_policy == 'legacy'
        assert module._parse_args(argv + ['--port-endpoint-policy', lineage.POLICY_V2]).port_endpoint_policy == lineage.POLICY_V2
        cli.append(dict(source=pin(module.__file__), v2_accepted=True, default='legacy', main_executed=False))
    evaluator = TransformerEmxEvaluator(cfg, a.out / 'evaluator_constructor_only', port_endpoint_policy=lineage.POLICY_V2)
    assert evaluator.port_endpoint_policy == lineage.POLICY_V2
    seen = {}; original_canonicalizer = export._canonicalize_cell_to_foundry_grid
    original_lineage = lineage.construct_shared_port_edges_with_lineage

    def canonicalizer(*, cell, grid_um):
        seen['same_call_before'] = lineage.snapshot_polygons(cell)
        assert seen['same_call_before'] == saved['before']
        result = original_canonicalizer(cell=cell, grid_um=grid_um)
        seen['same_call_after'] = lineage.snapshot_polygons(cell)
        assert seen['same_call_after'] == saved['after']
        return result

    def constructed(**kwargs):
        assert kwargs['before'] == seen['same_call_before']
        assert lineage.snapshot_polygons(kwargs['cell']) == seen['same_call_after']
        labels = [(x.text, x.origin, x.layer, x.texttype) for x in kwargs['cell'].labels]
        result = original_lineage(**kwargs)
        assert lineage.snapshot_polygons(kwargs['cell']) == seen['same_call_after']
        assert labels == [(x.text, x.origin, x.layer, x.texttype) for x in kwargs['cell'].labels]
        assert result['lineage_binding']['policy'] == lineage.POLICY
        seen['construction'] = result
        return result

    def stop_before_downstream_audit(**kwargs):
        # This exact export call has completed its metadata writeback. Do not
        # rerun downstream bridge/GDS checks or let lib.write_gds execute.
        frame = inspect.currentframe().f_back.f_locals
        result = seen['construction']
        assert frame['ports'] == result['resolved_ports']
        evidence_ports = frame['port_ground_overlap_evidence_for_audit']['ports']
        changes = result['lineage_binding']['rebound_ports']
        assert len(changes) == 1 and changes[0]['port_id'] == 'P002'
        rebound = changes[0]
        item = evidence_ports['P002']
        assert item['terminal_y_um'] == rebound['canonical_face_center_um']
        assert item['measured_overlap_um'] == 10.0
        assert item['cross_center_evidence_class'] == 'PRE_CADENCE_SAME_EDGE_LINEAGE_NOT_ACTUAL_GDS_MEASUREMENT'
        assert item['overlap_evidence_class'] == 'PRE_CADENCE_CONSTRUCTION_DERIVED_NOT_ACTUAL_GDS_MEASUREMENT'
        seen['written_metadata'] = item
        seen['stopped_before_downstream_bridge_audit_and_gds'] = True
        raise WiringCaptured()

    with patch.object(export, '_canonicalize_cell_to_foundry_grid', canonicalizer), \
         patch.object(lineage, 'construct_shared_port_edges_with_lineage', constructed), \
         patch.object(export, '_foundry_bridge_connections_audit', stop_before_downstream_audit):
        try:
            export.export_transformer_layout(geometry, cfg, a.out / 'single_new_export',
                validate_geometry=False, port_endpoint_policy=evaluator.port_endpoint_policy)
        except WiringCaptured:
            pass
    assert seen.get('stopped_before_downstream_bridge_audit_and_gds')
    assert not list(a.out.rglob('*.gds'))
    sources = [Path(export.__file__), Path(lineage.__file__),
        a.repo / 'rfic_transformer_inverse_design/execution/evaluator.py',
        Path(single.__file__), Path(parallel.__file__)]
    for source in sources:
        compile(source.read_bytes(), str(source), 'exec')
    report = dict(schema='eucap15_terminal_face_v2_actual_export_wiring_check.v1',
        status='PASS', deployment='DEVELOPMENT_NOT_DEPLOYED', request_id=case['request_id'],
        source_pins=[pin(__file__)] + [pin(s) for s in sources], original_source_replay=pin(a.replay),
        same_call_before_sha256=digest(seen['same_call_before']),
        same_call_after_sha256=digest(seen['same_call_after']), same_call_lineage_verified=True,
        cli_acceptance=cli, evaluator_v2_constructor_accepted=True,
        rebound=seen['construction']['lineage_binding'], written_metadata=seen['written_metadata'],
        label_containment_checks=seen['construction']['label_containment_checks'],
        polygon_coordinates_changed=0, labels_changed=0, gds_written=0,
        cadence_runs=0, calibre_runs=0, emx_runs=0, physical_validation='NOT_RUN',
        stopped_before_downstream_bridge_audit_and_gds=True,
        old_failure_results_modified=False, old_suites_rerun=0,
        new_version_export_constructions=1, six_case_suite_rerun=False)
    with (a.out / 'RECEIPT.json').open('x') as f:
        json.dump(report, f, indent=2, allow_nan=False)
    print(json.dumps(dict(receipt=pin(a.out / 'RECEIPT.json'), status='PASS')))


if __name__ == '__main__':
    main()
