"""New regression: real result/roundtrip/consumer path, synthetic GDS, no tools.

Only the low-level geometry exporter and native command boundary are faked.
Both evaluator instances, cache identities, roundtrip summary generation,
result conversion, and the production queue's evidence consumer are real.
This proves software path binding, never Cadence/DRC/EMX correctness.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import gdstk
import pytest

from scripts import run_candidate_queue_dataset as queue
from rfic_transformer_inverse_design.api import default_run_config
from rfic_transformer_inverse_design.core.types import TransformerLayoutExport
from rfic_transformer_inverse_design.execution import evaluator as evaluation
from rfic_transformer_inverse_design.execution import zeus_cadence as cadence

POLICY = 'shared_port_edges_20260912_v1'


def fixture(tmp_path, monkeypatch):
    config = default_run_config('1t1t')
    geometry = config.bounds.midpoint()
    # A path-binding fixture is deliberately not a production geometry claim.
    monkeypatch.setattr(type(config.bounds), 'validate', lambda self, g: [])
    monkeypatch.setattr(type(geometry), 'validate', lambda self: [])
    monkeypatch.setattr(evaluation, 'run_transformer_gdstk_checks',
        lambda **kw: SimpleNamespace(errors=[], warnings=[], metrics={}))
    calls = []

    def synthetic_export(*, out_dir, port_endpoint_policy, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        gds = out_dir / 'transformer_layout.gds'
        library = gdstk.Library()
        cell = library.new_cell('TRANSFORMER')
        cell.add(gdstk.rectangle((0, 0), (10, 10)))
        cell.add(gdstk.Label('SYNTHETIC_POLICY_' + port_endpoint_policy, (1, 1)))
        ports = []
        for i in range(1, 5):
            signal, ground = f'P{i:03d}', f'P{i:03d}_G'
            cell.add(gdstk.Label(signal, (i, 2), layer=39, texttype=51))
            cell.add(gdstk.Label(ground, (i, 3), layer=39, texttype=51))
            ports.append(dict(name=signal, signal_labels=[signal], ground_labels=[ground],
                internal_size_um=[1.0, 1.0]))
        library.write_gds(gds)
        manifest = out_dir / 'transformer_layout.layout.json'
        manifest.write_text(json.dumps(dict(layout_path=str(gds), top_cell='TRANSFORMER',
            ports=ports, metal_layer=39, metal_datatype=60, ground_layer=35, ground_datatype=0,
            label_layer=39, label_datatype=51, cadence_pin_purpose=51)))
        calls.append(dict(policy=port_endpoint_policy, path=str(gds)))
        return TransformerLayoutExport(gds_path=gds, manifest_path=manifest,
            preview_path=out_dir / 'NOT_RENDERED.png', debug_preview_path=out_dir / 'NOT_RENDERED_DEBUG.png',
            top_cell='TRANSFORMER')

    def synthetic_cadence_command(*, cwd, stdout_path, stderr_path, failure_label, **kwargs):
        Path(stdout_path).write_text('SYNTHETIC_NO_NATIVE_EXECUTION\n')
        Path(stderr_path).write_text('')
        if failure_label == 'Cadence strmout export':
            root = Path(cwd).parent
            library = gdstk.read_gds(root / 'layout/transformer_layout.gds')
            library.write_gds(root / 'streamout/transformer_layout_cadpins.gds')

    monkeypatch.setattr(evaluation, 'export_transformer_layout', synthetic_export)
    monkeypatch.setattr(cadence, '_run_logged_command', synthetic_cadence_command)
    monkeypatch.setattr(cadence, 'render_emx_layout_preview', lambda *a, **kw: None)
    monkeypatch.setattr(cadence, 'render_emx_port_debug_panels', lambda *a, **kw: None)
    monkeypatch.setattr(cadence, '_run_emx', lambda **kw: pytest.fail('EMX must never run'))
    return config, geometry, calls


@pytest.mark.parametrize('policy', ['legacy', POLICY])
def test_policy_roundtrip_gds_and_real_queue_consumer_agree(tmp_path, monkeypatch, policy):
    config, geometry, calls = fixture(tmp_path, monkeypatch)
    evaluator = evaluation.TransformerEmxEvaluator(config, tmp_path / 'case', port_endpoint_policy=policy)
    result = evaluator.evaluate_geometry_batch([geometry], run_emx=False, cadence_streamout_only=True)[0]
    contract = queue._cadence_streamout_output_contract(results=[result], enabled=True, expected_ports=4)
    assert result.error is None
    assert all(c['pass'] for c in contract['checks']), contract
    assert len(calls) == 2 and all(c['policy'] == policy for c in calls)
    assert calls[0]['path'] == calls[1]['path']  # Inner export must not silently switch cache.
    assert result.work_dir == evaluator.eval_dir / evaluator.cache_key(geometry)
    assert result.layout.gds_path == result.work_dir / 'streamout/transformer_layout_cadpins.gds'
    assert (result.work_dir / 'summary_cadence_roundtrip.json').is_file()
    assert len(list(evaluator.eval_dir.iterdir())) == 1
    labels = {label.text for cell in gdstk.read_gds(result.layout.gds_path).cells for label in cell.labels}
    assert 'SYNTHETIC_POLICY_' + policy in labels
    assert result.touchstone_path is None and not list(tmp_path.rglob('*.s?p'))
    default = evaluation.TransformerEmxEvaluator(config, tmp_path / 'legacy_reference')
    assert (result.cache_key == default.cache_key(geometry)) == (policy == 'legacy')
    (tmp_path / 'SYNTHETIC_CONSUMER_RESULT.json').write_text(json.dumps(dict(
        policy=policy, result_cache_key=result.cache_key, result_work_dir=str(result.work_dir),
        actual_fixture_gds=str(result.layout.gds_path), export_calls=calls, consumer=contract,
        native_calls=0, scientific_validation='NOT_RUN'), indent=2))


def test_dropped_policy_reproduces_mismatch_and_consumer_still_rejects(tmp_path, monkeypatch):
    config, geometry, calls = fixture(tmp_path, monkeypatch)
    real_roundtrip = cadence.run_transformer_zeus_cadence_roundtrip

    def broken_legacy_handoff(**kwargs):
        kwargs.pop('port_endpoint_policy', None)
        return real_roundtrip(**kwargs)

    monkeypatch.setattr(evaluation, 'run_transformer_zeus_cadence_roundtrip', broken_legacy_handoff)
    evaluator = evaluation.TransformerEmxEvaluator(config, tmp_path / 'reproduction', port_endpoint_policy=POLICY)
    result = evaluator.evaluate_geometry(geometry, run_emx=False, cadence_streamout_only=True)
    contract = queue._cadence_streamout_output_contract(results=[result], enabled=True, expected_ports=4)
    # The original incident had successful roundtrip geometry but mismatched ownership.
    assert result.error is None
    assert [c['policy'] for c in calls] == [POLICY, 'legacy']
    assert calls[0]['path'] != calls[1]['path']
    assert not (result.work_dir / 'summary_cadence_roundtrip.json').exists()
    failed = {c['name'] for c in contract['checks'] if not c['pass']}
    assert 'cadence_streamout_roundtrip_summaries_pass' in failed
    assert 'cadence_streamout_gds_files_are_candidate_bound_and_nonzero' in failed
    (tmp_path / 'SYNTHETIC_EXPECTED_REJECTION.json').write_text(json.dumps(dict(
        status='EXPECTED_FAILURE_OF_INJECTED_OLD_HANDOFF', consumer=contract,
        export_calls=calls, native_calls=0, scientific_validation='NOT_RUN'), indent=2))
