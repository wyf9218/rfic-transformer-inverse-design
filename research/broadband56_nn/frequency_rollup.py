"""Read-only model-free rollup of frozen per-frequency research results.

Source results are never changed. An existing destination is rejected. This is
a descriptive finite-holdout report, not a causal model-size comparison.
"""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

FEATURES = ('Lp_nH', 'Ls_nH', 'Qmin', 'K_abs')
SPANS = np.array([2.5, 2.5, 20., .8])


def pin(path):
    path = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(path=str(path), bytes=path.stat().st_size, sha256=digest.hexdigest())


def read(path):
    return json.loads(Path(path).read_text())


def checked(value, base=None):
    path = Path(value['path'])
    if not path.is_absolute():
        if base is None:
            raise ValueError('relative artifact requires its owning package directory')
        path = Path(base)/path
        if not path.resolve().is_relative_to(Path(base).resolve()):
            raise ValueError('relative artifact escapes its owning package')
    actual = pin(path)
    if actual['sha256'] != value['sha256'] or ('bytes' in value and actual['bytes'] != value['bytes']):
        raise ValueError('source identity mismatch: ' + value['path'])
    return actual


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def write_csv(path, rows):
    with Path(path).open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def metrics(rows, frequency, panel, mode, source):
    result = []
    targets = 'truth__' if panel == 'A_HELDOUT_FORWARD' else 'target__'
    all_error = []
    for feature in FEATURES:
        errors = np.array([float(r.get('prediction__' + feature) or 'nan') -
                           float(r.get(targets + feature) or 'nan') for r in rows])
        saved = np.array([float(r.get('error__' + feature) or 'nan') for r in rows])
        if not np.allclose(errors, saved, rtol=1e-10, atol=1e-12, equal_nan=True):
            raise ValueError('saved residual differs from prediction minus target')
        all_error.append(errors)
        finite = errors[np.isfinite(errors)]
        absolute = np.abs(finite)
        row = dict(frequency_ghz=frequency, panel=panel, mode=mode, feature=feature,
                   evidence='HELDOUT_EM_LABELS' if panel == 'A_HELDOUT_FORWARD' else 'SELF_PROXY',
                   N_requested=len(rows), N_finite=len(finite), N_invalid=len(rows)-len(finite),
                   residual_scope='ALL_FINITE_PREDICTIONS_GEOMETRY_FAILURES_RETAINED',
                   source_sha256=source['sha256'], quantile='linear_empirical_absolute_error',
                   uncertainty='DESCRIPTIVE_FIXED_HOLDOUT_SINGLE_SEED_NO_POPULATION_CI')
        row.update(Bias=float(np.mean(finite)) if len(finite) else None,
                   MAE=float(np.mean(absolute)) if len(finite) else None,
                   RMSE=float(np.sqrt(np.mean(finite**2))) if len(finite) else None)
        row.update({f'P{q}': float(np.percentile(absolute, q)) if len(finite) else None for q in (50, 90, 95)})
        result.append(row)
    errors = np.asarray(all_error).T
    hits = np.isfinite(errors).all(axis=1) & (np.abs(errors) <= .05*SPANS).all(axis=1)
    if panel == 'B_FOUR_TARGET_INVERSE':
        hits &= np.array([r['analytical_pass'].lower() == 'true' and
                          all(r['valid__'+f].lower() == 'true' for f in FEATURES) for r in rows])
    if panel == 'B_FOUR_TARGET_INVERSE' and any((r['joint_hit'].lower() == 'true') != bool(h) for r, h in zip(rows, hits)):
        raise ValueError('saved joint-hit flag disagrees with fixed-span tolerance')
    return result, int(hits.sum())


def collect(training_root, formal15, qscan_root, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    states, measurements, sources, models = [], [], [], []
    data_sha = None
    for frequency in range(5, 21):
        study = Path(formal15) if frequency == 15 else Path(training_root)/f'f{frequency:02d}'
        pair_path = study/'PAIR_RECEIPT.json'
        pair = read(pair_path)
        assert pair['frequency_ghz'] == frequency and pair['label_mode'] == 'STRICT_LUMPED'
        sources.append(pin(pair_path))
        training = {}
        for role, values in pair['roles'].items():
            sources.extend(checked(values[key]) for key in ('receipt', 'best', 'last'))
            training[role] = read(values['receipt']['path'])
            t = training[role]
            assert t['trainable_weights_changed'] and not t['historical_weights_loaded']
            assert t['frequency_ghz'] == frequency and t['started_step'] == 0
            data_sha = data_sha or t['data_sha']
            assert t['data_sha'] == data_sha
        paths = dict(acceptance=study/'posttrain/acceptance/BB00_LOAD_RESUME_RECEIPT.json',
                     package=study/'posttrain/package/PACKAGE_RECEIPT.json',
                     evaluation=study/'posttrain/evaluation/test/EVALUATION_SUMMARY.json',
                     posttrain=study/'posttrain/POSTTRAIN_RECEIPT.json')
        sources.extend(pin(p) for p in paths.values())
        acceptance, package, evaluation = (read(paths[k]) for k in ('acceptance', 'package', 'evaluation'))
        assert acceptance['status'] == 'PASS' and acceptance['original_checkpoint_bytes_unchanged']
        assert package['status'] == 'PORTABLE_INFERENCE_PACKAGE_READY'
        n = evaluation['target_count']
        assert evaluation['frequency_ghz'] == frequency and evaluation['split'] == 'test'
        computed_joint = {}
        for name, panel in (('forward_predictions.csv', 'A_HELDOUT_FORWARD'), ('inverse_predictions.csv', 'B_FOUR_TARGET_INVERSE')):
            source = checked(evaluation['artifacts'][name]); sources.append(source)
            with Path(source['path']).open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            for mode in ('forward',) if name.startswith('forward') else ('continuous', 'grid'):
                subset = rows if mode == 'forward' else [r for r in rows if r['mode'] == mode]
                assert len(subset) == n and len({r['target_id'] for r in subset}) == n
                assert all(int(r['frequency_ghz']) == frequency and r['split'] == 'test' for r in subset)
                measured, hits = metrics(subset, frequency, panel, mode, source)
                measurements.extend(measured); computed_joint[mode] = hits
                native = evaluation['forward'] if mode == 'forward' else evaluation['inverse'][mode]
                for m in measured:
                    feature = m['feature']
                    source_metric = native['features'][feature]
                    eligible = subset if mode == 'forward' else [r for r in subset
                        if r['analytical_pass'].lower() == 'true' and r['valid__'+feature].lower() == 'true']
                    values = np.array([float(r['error__'+feature]) for r in eligible])
                    values = values[np.isfinite(values)]
                    assert len(values) == source_metric['evaluable_denominator']
                    for key in ('MAE', 'RMSE'):
                        derived = (np.mean(np.abs(values)) if key == 'MAE' else np.sqrt(np.mean(values**2))) if len(values) else None
                        expected = source_metric['conditional_evaluable'][key.lower()]
                        assert derived is None and expected is None or np.isclose(derived, expected, rtol=1e-10, atol=1e-12)
                if mode != 'forward':
                    assert native['joint_hit_count'] == hits and native['joint_hit_denominator'] == n
        qs_path = Path(qscan_root)/f'f{frequency:02d}_qscan/QSCAN_SUMMARY.json'
        qs = read(qs_path); sources.append(pin(qs_path)); sources.append(checked(qs['freeze']))
        assert qs['frequency_ghz'] == frequency and qs['dataset_scope'] == 'FORMAL_10K'
        qg = next(g for g in qs['groups'] if g['target_source'] == 'RANDOM_LHS_TRIPLE' and g['selected_support'] == 'ALL_REQUESTS')
        assert qg['N_requested'] == 10000
        exposure = evaluation['exposure']
        state = dict(frequency_ghz=frequency, snapshot_geometries=10000,
            strict_total=sum(exposure[k]['eligible_geometries'] for k in ('train','validation','test')),
            gradient_train=exposure['train']['eligible_geometries'], validation=exposure['validation']['eligible_geometries'], test=n,
            F_updates=training['forward']['completed_step'], I_updates=training['inverse']['completed_step'],
            F_status=training['forward']['status'], I_status=training['inverse']['status'],
            convergence='NOT_ESTABLISHED', load_resume='PASS', package='READY_PROVISIONAL',
            heldout_test='PASS_SAVED_CSV_RECONCILIATION', B_grid_joint=computed_joint['grid'],
            B_grid_analytic_pass=evaluation['inverse']['grid']['analytical_pass_count'],
            Q_random_requests=qg['N_requested'], Q_logical_candidates=qg['N_requested']*11,
            Q_proxy_complete=qg['N_complete_proxy'], Q_response_hit=qg['joint_response_hit'],
            Q_analytic_pass=qg['analytic_pass'], Q_joint_and_analytic=qg['end_to_end_proxy_hit'],
            Q_failed=qg['N_failed_proxy'], fresh_EMX='NOT_RUN', fresh_EMX_complete_requests=0,
            fresh_EMX_independent_solves=0, native_figures='GENERATED_VISUAL_QA_SEPARATE')
        states.append(state)
        models.append(dict(frequency_ghz=frequency, status='PROVISIONAL_PARTIAL',
                           pair=pin(pair_path), package_receipt=pin(paths['package']),
                           model_index=checked(package['model_index'], paths['package'].parent), nearest_frequency_fallback=False))
    write_csv(out/'FREQUENCY_STATUS.csv', states)
    write_csv(out/'HELDOUT_METRICS.csv', measurements)
    created = datetime.now(timezone.utc).isoformat()
    save(out/'MODEL_REGISTRY.json', dict(created_utc=created, models=models, frequency_is_input=False))
    unique_sources = {p['path']:p for p in sources}
    save(out/'SOURCE_MANIFEST.json', dict(created_utc=created, data_sha256=data_sha, sources=list(unique_sources.values())))
    save(out/'RUN_STATE.json', dict(schema='frequency_rollup.v1', created_utc=created,
        status='TRAINING_BUDGET_AND_PROXY_COMPLETE_PHYSICAL_PENDING', frequencies=states,
        training_model_pairs=16, randomized_requests=160000, randomized_logical_candidates=1760000,
        real_emx_validation='NOT_RUN', physical_state_scope='QSCAN_SOURCE_TIME_NOT_LIVE_PHYSICAL_EXECUTOR',
        test_access='saved predictions only; no model loading or tuning',
        causal_claim=False, uncertainty='finite heldout descriptive, single training seed',
        next_safe_action='Complete isolated GDS/Calibre/fresh-EMX chain; preserve all 11 Q candidates and failures.'))
    lines = ['# 5–20 GHz 正式10K研究状态', '', f'观测 UTC：{created}',
        '16组真实F/I已完成本轮预算；PARTIAL不等于充分收敛。',
        '每组已保存/重载/诊断续训/测试；16×10000随机三目标×11Q代理扫描完成。',
        '真实EMX状态见独立物理执行收据；本表源Q结果仍NOT_RUN，不把代理称为物理精度。',
        '列：GHz / strict / train / val / test / F更新 / I更新 / Q联合且解析命中（分母10000）', '']
    lines.extend(f"- {s['frequency_ghz']} / {s['strict_total']} / {s['gradient_train']} / {s['validation']} / {s['test']} / {s['F_updates']} / {s['I_updates']} / {s['Q_joint_and_analytic']}" for s in states)
    lines.extend(['', '全频同一10K几何hash划分；各频率有效子集不同，不能把跨频率差异归因于模型优劣。',
                  '所有模型PROVISIONAL；16–20 GHz尤其小样本。Q10–20域外候选与解析失败保留。',
                  '误差阈值为绝对[0.125nH,0.125nH,1,0.04]，不是目标相对5%。',
                  'SOURCE_MANIFEST.json含精确输入SHA；HELDOUT_METRICS.csv从原预测独立重聚合。'])
    (out/'RUN_STATE.md').write_text('\n'.join(lines)+'\n')
    artifacts = [pin(p) for p in sorted(out.iterdir()) if p.is_file()]
    save(out/'ROLLUP_RECEIPT.json', dict(status='PASS_SAVED_ARTIFACT_RECONCILIATION', created_utc=created,
        source=pin(__file__), artifacts=artifacts, models_loaded=0, optimizer_updates=0, simulations_launched=0))
    indexed = sorted(p for p in out.iterdir() if p.is_file())
    (out/'SHA256SUMS').write_text(''.join(f"{pin(p)['sha256']}  {p.name}\n" for p in indexed))
    return states


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('training-root','formal15','qscan-root','out'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args()
    print(json.dumps(collect(a.training_root, a.formal15, a.qscan_root, a.out)))


if __name__ == '__main__':
    main()
