"""One-shot, no-clobber development acquisition metadata, never a native launcher.

Uses closed train-only coverage and an existing development pair. The original
64 requests and every checkpoint are read-only. No test labels are loaded.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

import numpy as np
import torch

from .io import canonical_sha, read_json, save_json, sha256, utc_now
from .frequency_evaluation import pin
from .frequency_qscan import _batch
from .frequency_tandem import load_frequency_pair
from .frequency_large_eval import _geometry_hash, _geometry_status
from .evaluation import _grid_from_contract, _grid_geometry
from .eucap15_acquisition import validate_coverage, sparse_targets, geometry_lhs
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import canonical_geometry_sha256


def inputs(root):
    e = root / 'reports/eucap15ghz_20260908T220300Z'
    d = e / 'development_current_snapshot_20260909T062152Z'
    native = root / 'reports/eucap15_native_owner_20260909T062500Z/mars_receipts_v1'
    pair = d / 'trials/3x256_seed17'
    specs = {
        'coverage': (e / 'reference_domain_audit_v1/run_v1/coverage_before.csv', 'de8b8a499bc9355b695f231e49f745007acfc1640b09b264bdf9ba1f74c672d6'),
        'contract': (d / 'data_v1/contract.json', '09aea00c966b9e6d41d423b60c82e45ef6604533493e95afce071e02ba250447'),
        'pair_receipt': (pair / 'PAIR_RECEIPT.json', 'e7dc111875086f488f7243f5b9093a6e70cbeadd8e755a2bf95f499a4a760976'),
        'forward': (pair / 'forward/attempt_0001/checkpoint_step_008500.pt', '1a6bb162d8465fa1727720c4d0615848c2c07a9d60cc8db1bfd018d60b3c59d9'),
        'inverse': (pair / 'inverse/attempt_0001/checkpoint_step_009700.pt', '8fd238246e14e783d5d21dca771c98a2ea17bda19fdcd6ea5edadb004db603c1'),
        'normalizer': (pair / 'forward/attempt_0001/normalizer.json', '8a02b75afb762c30f882997334bedfb84d0c49dfd0d77d0ac45fe843fff01826'),
        'production_geometry_ledger': (native / 'history_current_campaign_v2/current_campaign_exact15.csv', 'ca474f01a799824ccd6de7b43d8de578c8f7f3b5bb26048d4bd52e561d3aa813'),
        'research_geometry_ledger': (native / 'historical_research15_v1/research_exact15.csv', '8cb5ed059596f80a75ba12912283cef5d2b55eed7ac8a52d9cd83a7724bd50af'),
        'original64': (e / 'development_pilot64_v1/run_v1/handoff/SUBMISSION_MANIFEST.json', '901da0e80d50a090d0bb4eea9e31324bb6770a9deb564bba4366b0a19666b9b4'),
    }
    # Fail before creating an output or drawing any new candidates.
    found = {}
    for name, (path, expected) in specs.items():
        value = pin(path)
        if value['sha256'] != expected:
            raise ValueError(f'closed input SHA mismatch: {name}')
        found[name] = value
    return found


def csv_rows(path):
    with open(path, newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def jsonl(path, rows):
    with open(path, 'x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n')


def write_csv(path, rows):
    if not rows:
        raise ValueError('cannot silently emit an empty candidate table')
    with open(path, 'x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def memory_headroom():
    """Read OS evidence; Darwin free+inactive+speculative is a headroom estimate,
    NOT Linux MemAvailable and NOT a promise all inactive pages are reclaimable.
    This avoids installing anything in the frozen research environment.
    """
    if platform.system() == 'Darwin':
        raw = subprocess.check_output(['vm_stat'], text=True)
        page_size = int(re.search(r'page size of (\d+) bytes', raw).group(1))
        pages = {key:int(re.search(r'^'+re.escape(key)+r':\s*(\d+)',raw,re.M).group(1))
                 for key in ('Pages free','Pages inactive','Pages speculative')}
        pressure = subprocess.check_output(['memory_pressure','-Q'],text=True)
        pct = int(re.search(r'memory free percentage:\s*(\d+)%',pressure).group(1))
        return dict(headroom_estimate_bytes=page_size*sum(pages.values()),
            estimate='vm_stat free+inactive+speculative; not guaranteed available RAM',
            memory_pressure_free_percent=pct, vm_stat=raw, memory_pressure=pressure,
            passed=page_size*sum(pages.values())>=2*1024**3 and pct>=20)
    if platform.system() == 'Linux':
        raw=Path('/proc/meminfo').read_text()
        available=int(re.search(r'^MemAvailable:\s*(\d+) kB',raw,re.M).group(1))*1024
        return dict(headroom_estimate_bytes=available,estimate='Linux MemAvailable',passed=available>=2*1024**3)
    raise RuntimeError('unsupported read-only research RAM probe')


def duplicate_reasons(h9, known, original64, multiplicity):
    """All three sets use the same canonical 9-decimal parameter identity."""
    reasons=[]
    if h9 in known: reasons.append('KNOWN_CLOSED_GEOMETRY')
    if h9 in original64: reasons.append('ORIGINAL64_SELECTED_GEOMETRY')
    if h9 is not None and multiplicity[h9]>1: reasons.append('DUPLICATE_PAIR_PARAMETER_GROUP_SYMMETRIC_HOLD')
    return reasons


def prepare(root, out, lock_path):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise FileExistsError(f'no repeat inference / no overwrite: {out}')
    pins = inputs(root)
    contract = read_json(pins['contract']['path'])
    fields = contract['field_names']
    if len(fields) != 10:
        raise ValueError('audited ten-field contract required')
    coverage = validate_coverage([r for r in csv_rows(pins['coverage']['path']) if int(r['grid_n']) == 8])
    if sum(int(r['N_strict_core_all_q']) for r in coverage) != 1804:
        raise ValueError('train coverage identity/count mismatch')
    pilot = read_json(pins['original64']['path'])
    pilot_hashes = {r['candidate_geometry_identity_sha256'] for r in pilot['requests']}
    if len(pilot['requests']) != 64:
        raise ValueError('original 64 identity mismatch')
    known = set()
    ledger_counts = {}
    # Geometry columns only: responses, core flags and test labels are not read
    # into the acquisition formula or used for filtering by performance.
    for name in ('production_geometry_ledger', 'research_geometry_ledger'):
        count = 0
        with open(pins[name]['path'], newline='', encoding='utf-8') as stream:
            for row in csv.DictReader(stream):
                values = {field: float(row['geom__' + field]) for field in fields}
                known.add(canonical_geometry_sha256(values, fields=fields))
                count += 1
        ledger_counts[name] = count
    if ledger_counts != {'production_geometry_ledger': 20973, 'research_geometry_ledger': 1110}:
        raise ValueError('geometry-only exclusion ledger row count mismatch')
    disk = shutil.disk_usage(out.parent if out.parent.exists() else root)
    memory = memory_headroom()
    if disk.free < 2 * 1024**3 or not memory['passed']:
        raise RuntimeError('research preparation resource gate: need 2 GiB available RAM and disk')
    with open(lock_path, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        torch.set_num_threads(2)
        out.mkdir(parents=True, exist_ok=False)
        start = time.monotonic()
        recipe = dict(
            schema='eucap15_acquisition_pair_recipe.v1', created_utc=utc_now(),
            study_id='eucap15_dev_acquisition_pair_20260909_v1',
            scope='DEVELOPMENT_CURRENT_SNAPSHOT_NOT_FINAL', status='FROZEN_BEFORE_NEW_INFERENCE_AND_EMX',
            source_dataset_sha256='22b80842b2a19d35bf22abc2dadbc66ffab3d0ab691062e53dcc2117c57877bf',
            frequency_hz=15000000000, label_mode='STRICT_LUMPED', inputs=pins,
            model_selection='first completed 3x256 seed17 baseline, not validation winner or FINAL',
            coverage_source='actual strict train-only EM labels; 1804 geometries; no validation/test counts',
            bins=dict(n=8, lower=[.5,.5,.2], upper=[2.,2.,.85], sparse_threshold=5,
                endpoint='half-open except final inclusive upper edge',
                weight='d=max(5-N_strict_core_all_q,0); p=d/sum(d)',
                selection='102 distinct cells sampled without replacement with these initial weights; uniform target inside each selected cell'),
            sampling_probability_note='d/sum(d) is the initial draw weight, not the marginal inclusion probability after 102 without-replacement draws',
            counts=dict(directed_arm=128, sparse_targeted=102, geometry_exploration=26, geometry_doe_control=128),
            exploration=dict(requested_approx_fraction=.20, actual_fraction=26/128,
                rounding='128 minus floor(0.8*128)=26; exploration before native gates'),
            seeds=dict(sparse=2026090901, exploration=2026090902, doe=2026090903, order=2026090904),
            rng='numpy PCG64; LHS separately permuted strata in each of ten geometry dimensions',
            q_scan=dict(values=list(range(10,21)), scalar='min(Qp,Qs)',
                score='sqrt(mean(((grid_forward-target)/[2.5,2.5,20,0.8])**2))',
                tie_break='smaller integer Q', selection='exactly one pre-EM candidate; all 11 predictions must be finite',
                no_q_fallback=True, exploration_and_doe_q_target=None),
            geometry=dict(contract_sha256=pins['contract']['sha256'], grid_um=_grid_from_contract(contract),
                lhs_bounds='exact frozen ten-field contract; no surrogate filtering, repair or resampling',
                identity='parameter vector, not proof of same actual GDS'),
            exclusions=dict(known_geometry_unique=len(known), ledger_rows=ledger_counts,
                original64_parameter_hashes=len(pilot_hashes), scope='listed closed ledgers plus original64 and within-pair proposals only',
                within_pair='all members of a duplicate parameter group are held symmetrically, independent of arm or dispatch order; no replacement or double-counted solve',
                all_history='UNKNOWN; owner must check latest ledger and in-flight identities before dispatch'),
            budget=dict(original_proposals_per_arm=128, maximum_native_attempts_per_arm=128,
                comparison='descriptive first-pilot coverage at matched chronological started-native-attempt prefix m; require both prefixes closed, keep native failures',
                order='use frozen per-arm proposal order, never sort by completion time or success; m counts actual solver starts including failures and strict-invalid',
                estimand='conditional-on-EMX-admission equal-started-attempt cost comparison; not end-to-end proposal efficiency',
                checkpoints=[16,32,64,128], unequal_actual_attempts='no equal-budget claim until common closed prefix exists',
                pre_native_failures='report against all 128 proposals separately, no replacement',
                costs='record native attempts, solver seconds, pre-native failures and end-to-end time separately',
                downstream_equal_N_training_comparison='NOT_RUN', causal_or_general_superiority='NOT_ESTABLISHED_SINGLE_PAIR'),
            actual_gain=dict(status='NOT_RUN', value=None,
                primary='8-cubed strict core all-Q coverage; Q10..20 intersection is separate prespecified secondary, not a replacement endpoint',
                rule='strict exact15 actual EM landing only; unique previously unseen geometry; same GDS/DRC/full-sweep receipt closure required',
                split='retain common hash split; report acquisition landing all splits separately from train-only gain',
                no_train_test_feedback=True),
            native_execution=dict(owner_thread='019eb52f-9739-73b2-8483-af70553603a8',
                status='NOT_SUBMITTED', automatic_dispatch='NOT_INSTALLED_BY_THIS_MODULE',
                candidate_schema='eucap15_acquisition_candidate.v1',
                original64_priority='preserve existing queue and identities; do not modify its executor',
                sweep='reuse original 5..60 GHz full56 sweep and SRF/2 evidence; no 15GHz-only shortcut'),
            code={p.name:pin(p) for p in [Path(__file__), Path(__file__).with_name('eucap15_acquisition.py'),
                Path(__file__).with_name('frequency_qscan.py'), Path(__file__).with_name('frequency_tandem.py'),
                Path(__file__).with_name('bb00.py'), Path(__file__).with_name('evaluation.py'),
                Path(__file__).with_name('frequency_large_eval.py'), Path(__file__).with_name('frequency_evaluation.py'),
                Path(__file__).with_name('io.py'),
                Path(__file__).resolve().parents[2]/'rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py',
                Path(__file__).resolve().parents[2]/'rfic_transformer_inverse_design/synthesis/q_sweep.py']},
            runtime=dict(python=sys.version, executable=sys.executable, platform=platform.platform(),
                numpy=np.__version__, torch=torch.__version__, cpu_threads=2, gpu_used=False,
                ram_probe=memory, available_disk_bytes=disk.free,
                lock_path=str(Path(lock_path).resolve()), lock_held=True),
        )
        recipe_sha = save_json(out/'RECIPE_FREEZE.json', recipe)
        try:
            forward, inverse, fs, ins = load_frequency_pair(pins['forward']['path'], pins['inverse']['path'],
                frequency_ghz=15, label_mode='STRICT_LUMPED', device='cpu')
            if fs['data_sha'] != recipe['source_dataset_sha256'] or canonical_sha(fs['contract']) != canonical_sha(contract):
                raise ValueError('loaded pair data/contract differs from acquisition freeze')
            if canonical_sha(fs['normalizer']) != canonical_sha(read_json(pins['normalizer']['path'])):
                raise ValueError('loaded normalizer identity mismatch')
            save_json(out/'MODEL_LOAD_RECEIPT.json', dict(status='PASS', forward=pins['forward'], inverse=pins['inverse'],
                normalizer=pins['normalizer'], role='DEVELOPMENT_ACQUISITION_ONLY_NOT_ORIGINAL64',
                pair_identity_checked=True, no_training=True, source_data_sha256=fs['data_sha'], created_utc=utc_now()))
            targets = sparse_targets(coverage,102,recipe['seeds']['sparse'])
            save_json(out/'SPARSE_TARGETS.json', targets)
            requests = [dict(request_id=f"{recipe['study_id']}-SPARSE-{i:03d}",
                target_source='TRAIN_COVERAGE_SPARSE_CELL', lp_nh=t['target'][0],ls_nh=t['target'][1],k_abs=t['target'][2],
                preselected_emx=True, sparse_cell=t['cell'], sparse_N=t['N'], sparse_deficit=t['deficit']) for i,t in enumerate(targets)]
            freeze = dict(artifacts={'normalizer.json':pins['normalizer'], 'geometry_contract.json':pins['contract']},
                frequency_ghz=15, config={'allow_extrapolation':True,'dataset_scope':'DEVELOPMENT_CURRENT_SNAPSHOT'},
                model_id='dev15-3x256-seed17-'+pins['inverse']['sha256'][:12])
            q_records, summaries, failures = _batch(requests,freeze,forward,inverse)
            jsonl(out/'QSCAN_LOGICAL_CANDIDATES.jsonl',q_records)
            save_json(out/'QSCAN_SUMMARIES.json',summaries)
            save_json(out/'INFERENCE_FAILURES.json',failures)
            by_request = {r['request_id']:r for r in q_records if r['proxy_preselected']}
            directed = []
            for req in requests:
                selected = by_request.get(req['request_id'])
                directed.append(dict(request_id=req['request_id'], candidate_id=selected['candidate_id'] if selected else req['request_id']+'-NO_SELECTION',
                    source='SPARSE_TARGETED', target=selected['target'] if selected else None,
                    requested_triple=[req['lp_nh'],req['ls_nh'],req['k_abs']], sparse_cell=req['sparse_cell'],
                    q_proxy=selected['q_proxy'] if selected else None, proxy=selected['grid_proxy'] if selected else None,
                    score=selected['grid_proxy_score'] if selected else None,
                    support=selected['support_status'] if selected else 'NO_SELECTION',
                    geometry=selected['grid_geometry'] if selected else None,
                    analytic_pass=selected['analytic_grid'] if selected else False))
            doe = []
            for name,count,seed,output in [('EXPLORATION',26,recipe['seeds']['exploration'],directed),
                                          ('GEOMETRY_DOE',128,recipe['seeds']['doe'],doe)]:
                geometry = _grid_geometry(geometry_lhs(contract['lower'],contract['upper'],count,seed),_grid_from_contract(contract))
                _, analytic = _geometry_status(geometry,contract)
                for i,(g,passed) in enumerate(zip(geometry,analytic)):
                    identity=f"{recipe['study_id']}-{name}-{i:03d}"
                    output.append(dict(request_id=identity,candidate_id=identity,source=name,target=None,requested_triple=None,
                        sparse_cell=None,q_proxy=None,proxy=None,score=None,support='GEOMETRY_DOE_NO_PROXY_SELECTION',
                        geometry=g.tolist(),analytic_pass=bool(passed)))
            if len(q_records)!=1122 or len(directed)!=128 or len(doe)!=128:
                raise ValueError('frozen proposal denominators differ')
            rng=np.random.default_rng(recipe['seeds']['order'])
            directed=[directed[i] for i in rng.permutation(128)]
            doe=[doe[i] for i in rng.permutation(128)]
            order=[]
            for i in range(128):
                arms=[('COVERAGE_DIRECTED',directed[i]),('GEOMETRY_DOE_CONTROL',doe[i])]
                if rng.integers(2): arms.reverse()
                for arm,row in arms:
                    order.append(dict(row,arm=arm,arm_order=i+1,global_order=len(order)+1))
            group_hashes=[]
            for row in order:
                g=row['geometry']
                finite=g is not None and np.isfinite(np.asarray(g,float)).all()
                group_hashes.append(canonical_geometry_sha256(dict(zip(fields,g)),fields=fields) if finite else None)
            multiplicity=Counter(h for h in group_hashes if h is not None)
            for row in order:
                g=row['geometry']
                finite=g is not None and np.isfinite(np.asarray(g,float)).all()
                h9=canonical_geometry_sha256(dict(zip(fields,g)),fields=fields) if finite else None
                h12=_geometry_hash(g,fields) if finite else None
                duplicate=duplicate_reasons(h9,known,pilot_hashes,multiplicity)
                row.update(schema='eucap15_acquisition_candidate.v1',recipe_sha256=recipe_sha,
                    frequency_hz=15000000000, geometry_fields=fields,geometry_units='um',
                    canonical_geometry_sha256=h9, parameter_geometry_hash=h12,identity_not_actual_gds=True,
                    duplicate_reasons=duplicate,local_dispatch_eligible=finite and row['analytic_pass'] and not duplicate,
                    native_status='NOT_RUN',gds_status='NOT_RUN',drc_status='NOT_RUN',emx_status='NOT_RUN',
                    actual_response=None,actual_landing=None,coverage_gain=None,
                    no_replacement=True,no_q_fallback=True,owner_current_ledger_check='REQUIRED')
            jsonl(out/'SELECTED_CANDIDATES.jsonl',order)
            write_csv(out/'CANDIDATE_INDEX.csv',[dict(arm=r['arm'],arm_order=r['arm_order'],global_order=r['global_order'],
                candidate_id=r['candidate_id'],source=r['source'],q_proxy=r['q_proxy'],analytic_pass=r['analytic_pass'],
                duplicate_reasons='|'.join(r['duplicate_reasons']),local_dispatch_eligible=r['local_dispatch_eligible'],
                canonical_geometry_sha256=r['canonical_geometry_sha256'],parameter_geometry_hash=r['parameter_geometry_hash'],
                native_status='NOT_RUN') for r in order])
            counts={arm:dict(original_denominator=128,N_analytic_pass=sum(r['analytic_pass'] for r in order if r['arm']==arm),
                N_local_dispatch_eligible=sum(r['local_dispatch_eligible'] for r in order if r['arm']==arm),
                N_duplicate_proposals=sum(bool(r['duplicate_reasons']) for r in order if r['arm']==arm),
                N_native_attempts=0,N_closed_native_results=0,actual_gain=None) for arm in ('COVERAGE_DIRECTED','GEOMETRY_DOE_CONTROL')}
            receipt=dict(schema='eucap15_acquisition_preparation_receipt.v1',status='PREPARED_NOT_NATIVE_RELEASE',
                created_utc=utc_now(),recipe_sha256=recipe_sha,counts=counts,N_sparse_targets=102,
                N_logical_q_inferences=1122,N_selected_proposals=256,N_new_training_runs=0,N_new_training_updates=0,
                q_selected=dict(sorted(Counter(str(r['q_proxy']) for r in directed if r['source']=='SPARSE_TARGETED').items())),
                inference_failure_counts={k:len(v) for k,v in failures.items()},
                model_load='PASS',REAL_EMX_VALIDATION='NOT_RUN',elapsed_seconds=time.monotonic()-start,
                storage_deleted_bytes=0,owner_acceptance='NOT_YET_RECEIVED',automatic_native_trigger='NOT_INSTALLED',
                no_original64_changes=True,no_test_label_access=True,actual_physical_error=None,
                next_safe_action='sole owner validates candidate schema, latest dedup/native bindings and existing resource gates; no second controller')
            save_json(out/'PREPARATION_RECEIPT.json',receipt)
            files=[p for p in sorted(out.iterdir()) if p.is_file()]
            manifest={p.name:pin(p) for p in files}
            save_json(out/'MANIFEST.json',dict(schema='eucap15_acquisition_manifest.v1',files=manifest,source_pins=pins))
            with (out/'SHA256SUMS').open('x',encoding='utf-8') as stream:
                for p in files+[out/'MANIFEST.json']:
                    stream.write(f'{sha256(p)}  {p.name}\n')
            print(json.dumps(receipt,ensure_ascii=False,allow_nan=False))
            return receipt
        except Exception as exc:
            save_json(out/'FAILURE_RECEIPT.json',dict(status='FAIL_PRESERVE_NO_SILENT_RETRY',created_utc=utc_now(),
                error=repr(exc),recipe_sha256=recipe_sha,native_dispatched=False))
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--lock',required=True)
    args=parser.parse_args()
    prepare(args.root,args.out,args.lock)


if __name__=='__main__':
    main()
