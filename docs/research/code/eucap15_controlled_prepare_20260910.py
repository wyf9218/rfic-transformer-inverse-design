"""One frozen 3801-train coverage-directed/DOE pair; no native dispatch.

Reuse existing sampling, Q-scan, grid and analytic functions. All original
proposals survive the ledger; failures/duplicates are held, never resampled.
This private thin adapter does not modify the historical 256-proposal CLI.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import traceback


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(path):
    p = Path(path)
    require(p.is_absolute() and not p.is_symlink() and p.is_file(), 'Exact regular absolute file required: ' + str(p))
    h = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return dict(path=str(p), sha256=h.hexdigest(), bytes=p.stat().st_size)


def checked(value):
    require(pin(value['path']) == value, 'Input SHA/size differs: ' + value['path'])
    return Path(value['path'])


def read(value):
    return json.loads(checked(value).read_text())


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def csv_rows(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def jsonl(path, rows):
    with Path(path).open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n')


def csv_write(path, rows):
    with Path(path).open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, separators=(',', ':'), allow_nan=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def finite_geometry(g):
    return isinstance(g, (list, tuple)) and len(g) == 10 and all(type(v) in (int, float) and math.isfinite(v) for v in g)


def run(intent_path, intent_sha, out):
    intent_pin = pin(intent_path)
    require(intent_pin['sha256'] == intent_sha, 'Frozen intent SHA differs')
    intent = read(intent_pin)
    require(intent['schema'] == 'eucap15_controlled_acquisition_intent.v1' and
            intent['status'] == 'FROZEN_BEFORE_TARGETS_AND_INFERENCE', 'Wrong intent')
    require(intent['counts'] == dict(proposals_per_arm=32, sparse_targets=25, exploration=7,
            doe=32, logical_q_candidates=275), 'Frozen count contract differs')
    require(intent['budget']['solver_starts_per_arm_max'] == 16 and
            intent['budget']['native_wall_seconds_max'] == 21600 and
            intent['budget']['incremental_storage_bytes_max'] == 2147483648, 'Frozen budget differs')
    out = Path(out)
    require(str(out) == intent['output'] and out.is_absolute() and not out.exists() and not out.is_symlink(), 'No-clobber output mismatch')
    for item in [*intent['inputs'].values(), *intent['implementation'].values()]:
        checked(item)
    require(intent['implementation']['prepare_controlled']['path'] == str(Path(__file__).resolve()), 'Different thin adapter path')
    out.mkdir(parents=False, exist_ok=False)
    started = time.monotonic()
    inputs = intent['inputs']
    write(out / 'PREPARATION_INTENT.json', dict(intent=intent_pin, status='SINGLE_ATTEMPT_RESERVED',
        created_utc=datetime.now(timezone.utc).isoformat(), argv=sys.argv))
    try:
        import numpy as np
        import torch
        from research.broadband56_nn.eucap15_acquisition import validate_coverage, sparse_targets, geometry_lhs, actual_landing
        from research.broadband56_nn.eucap15_prepare_acquisition import memory_headroom
        from research.broadband56_nn.frequency_qscan import _batch, score, select_q
        from research.broadband56_nn.frequency_tandem import load_frequency_pair
        from research.broadband56_nn.frequency_large_eval import _geometry_hash, _geometry_status
        from research.broadband56_nn.evaluation import _grid_geometry, _grid_from_contract
        from research.broadband56_nn.io import canonical_sha
        from research.broadband56_nn.data import _split_for_hash
        from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import canonical_geometry_sha256

        memory = memory_headroom()
        disk = shutil.disk_usage(out)
        write(out/'RESOURCE_CHECK.json', dict(memory=memory, available_disk_bytes=disk.free,
            CPU_threads=2, native_resource_admission='NOT_OBSERVED_NOT_GRANTED', model_calls_yet=0))
        require(memory['passed'] and disk.free >= 2*1024**3, 'Local preparation resource gate not passed')
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        coverage = validate_coverage(csv_rows(checked(inputs['decision_coverage'])))
        require(sum(r['N_strict_core_all_q'] for r in coverage) == 3801 and
                sum(r['N_strict_core_all_q'] > 0 for r in coverage) == 159, 'Exact3801/159 coverage differs')
        summary = read(inputs['historical_admission_summary'])
        require(summary['train_coverage']['admitted_train_added'] == 20 and
                summary['train_coverage']['after_occupied'] == 163, 'Historical20 reference differs')
        data_receipt = read(inputs['data_receipt'])
        require(data_receipt['split_counts'] == dict(train=3801, validation=1269, test=1259), '6329 split differs')
        pair = read(inputs['pair'])
        require(pair['frequency_ghz'] == 15 and pair['roles']['forward']['best'] == inputs['forward'] and
                pair['roles']['inverse']['best'] == inputs['inverse'], 'Frozen current6329 baseline pair differs')
        contract, norm = read(inputs['contract']), read(inputs['normalizer'])
        fields = contract['field_names']
        require(len(fields) == 10 and norm['field_names'] == fields and norm['response_spans'] == [2.5,2.5,20,.8], 'Geometry/score contract differs')
        splits = read(inputs['splits'])
        require(splits['seed'] == 17 and splits['counts'] == data_receipt['split_counts'], 'Fixed development split differs')

        # Metadata-only exclusions. Never use label strings or new128 outcomes
        # in target allocation or ranking. Keep native latest-ledger recheck.
        canonical_sets, grid_sets = {}, {}
        def add(name, rows):
            hs, gs = set(), set()
            for h, g in rows:
                require(finite_geometry(g), 'Invalid known geometry metadata: ' + name)
                require(canonical_geometry_sha256(dict(zip(fields,g)),fields=fields) == h, 'Known canonical geometry differs: ' + name)
                hs.add(h)
                gg = _grid_geometry(np.asarray([g]), _grid_from_contract(contract))[0].tolist()
                gs.add(canonical_geometry_sha256(dict(zip(fields,gg)),fields=fields))
            canonical_sets[name], grid_sets[name] = hs, gs
        current = csv_rows(checked(inputs['current_source_rows']))
        require(len(current) == 6329, 'Current source metadata count differs')
        add('CURRENT6329', [(r['geometry_sha256'], [float(r['geom__'+f]) for f in fields]) for r in current])
        known = csv_rows(checked(inputs['known6700']))
        require(len(known) == 6700, 'Known core metadata count differs')
        add('KNOWN6700', [(r['geometry_sha256'], [float(r['geom__'+f]) for f in fields]) for r in known])
        added = read(inputs['historical20_train'])
        require(len(added) == 20 and all(r['split'] == 'train' for r in added), 'Prior20 train metadata differs')
        add('PREVIOUS20_TRAIN_EXCLUSION_ONLY', [(r['geometry_sha256'],r['geometry']) for r in added])
        reserved = read(inputs['reserved64_128'])
        require(len(reserved['rows']) == 2112 and reserved['geometry_fields'] == fields, '64/128 allQ reservation differs')
        add('RESERVED64_AND128_ALL_Q', [(r['geometry_sha256'],r['geometry']) for r in reserved['rows'] if r['geometry_sha256'] is not None])
        with checked(inputs['old256_proposals']).open() as stream:
            old = [json.loads(line) for line in stream]
        require(len(old) == 256, 'Old256 proposal metadata differs')
        add('OLD256_PROPOSALS', [(r['canonical_geometry_sha256'],r['geometry']) for r in old if r['canonical_geometry_sha256'] is not None])
        write(out/'EXCLUSION_METADATA.json', dict(
            canonical_hashes={k:sorted(v) for k,v in canonical_sets.items()},
            nominal_grid_hashes={k:sorted(v) for k,v in grid_sets.items()},
            numeric_response_values_used=0, CSV_label_fields_mechanically_parsed=True,
            original64_or128_predictions_or_results_used_for_selection=False,
            all_history_complete=False, owner_latest_ledger_and_reservation_recheck='REQUIRED'))
        # Exact copy of the already validated3801 aggregate, not recomputation
        # from validation/test or relabeling the old1804 decision grid.
        with (out/'DECISION_COVERAGE.csv').open('xb') as stream:
            stream.write(checked(inputs['decision_coverage']).read_bytes())
        targets = sparse_targets(coverage,25,intent['seeds']['sparse'])
        write(out/'SPARSE_TARGETS.json', targets)
        requests = [dict(request_id=f"{intent['study_id']}-SPARSE-{i:03d}",
            target_source='FROZEN3801_TRAIN_COVERAGE_SPARSE_CELL', lp_nh=t['target'][0],
            ls_nh=t['target'][1],k_abs=t['target'][2],preselected_emx=True,
            target_cell=t['cell'],sparse_N=t['N'],sparse_deficit=t['deficit'],
            initial_selection_weight=t['selection_weight']) for i,t in enumerate(targets)]
        write(out/'REQUESTS.json',requests)
        f,i,fs,ins=load_frequency_pair(inputs['forward']['path'],inputs['inverse']['path'],
            frequency_ghz=15,label_mode='STRICT_LUMPED',device='cpu')
        require(fs['data_sha'] == ins['data_sha'] == data_receipt['dataset']['sha256'] and
                fs['normalizer'] == ins['normalizer'] == norm and fs['contract_sha'] == canonical_sha(contract), 'Loaded baseline identity differs')
        require(fs['step']==14300 and ins['step']==11300, 'Wrong baseline best steps')
        write(out/'MODEL_LOAD_RECEIPT.json',dict(status='PASS',forward=inputs['forward'],inverse=inputs['inverse'],
            pair=inputs['pair'],model_id=intent['model_id'],data_sha256=fs['data_sha'],
            model_choice='PREEXISTING_BASELINE_NOT_SELECTED_USING_NEW128',training_updates=0))
        freeze=dict(artifacts={'normalizer.json':inputs['normalizer'],'geometry_contract.json':inputs['contract']},
            frequency_ghz=15,config={'allow_extrapolation':True,'dataset_scope':'DEVELOPMENT_CURRENT_SNAPSHOT'},model_id=intent['model_id'])
        q_records,summaries,failures=_batch(requests,freeze,f,i)
        require(len(q_records)==275 and len(summaries)==25, 'Q-scan original count differs')
        jsonl(out/'QSCAN_LOGICAL_CANDIDATES.jsonl',q_records)
        write(out/'QSCAN_SUMMARIES.json',summaries)
        write(out/'INFERENCE_FAILURES.json',failures)
        directed=[]
        for req in requests:
            rows=[r for r in q_records if r['request_id']==req['request_id']]
            require([r['q_target'] for r in rows]==list(range(10,21)), 'Q slots changed')
            expected=select_q(score(np.asarray([r['grid_proxy'] for r in rows],float),np.asarray([r['target'] for r in rows],float)))['q_proxy']
            selected=[r for r in rows if r['proxy_preselected']]
            require(len(selected)==(0 if expected is None else 1) and all(r['q_proxy']==expected for r in rows), 'Q preselection disagrees')
            s=selected[0] if selected else None
            predicted=actual_landing([s['grid_proxy'][j] for j in (0,1,3)]) if s else None
            directed.append(dict(request_id=req['request_id'],candidate_id=s['candidate_id'] if s else req['request_id']+'-NO_SELECTION',
                source='SPARSE_TARGETED',target=s['target'] if s else None,
                requested_triple=[req['lp_nh'],req['ls_nh'],req['k_abs']],target_cell=req['target_cell'],sparse_cell=req['target_cell'],
                q_proxy=expected,q_emx=None,proxy=s['grid_proxy'] if s else None,score=s['grid_proxy_score'] if s else None,
                support=s['support_status'] if s else 'NO_SELECTION',
                decoded_continuous_geometry=s['continuous_geometry'] if s else None,raw_lhs_geometry=None,
                geometry=s['grid_geometry'] if s else None,analytic_pass=s['analytic_grid'] if s else False,
                predicted_cell=list(predicted) if predicted is not None else None,
                model_id=intent['model_id'],qscan_source_candidate=s['candidate_id'] if s else None))
        doe=[]
        for name,n,seed,output in [('EXPLORATION',7,intent['seeds']['exploration'],directed),('GEOMETRY_DOE',32,intent['seeds']['doe'],doe)]:
            raw=geometry_lhs(contract['lower'],contract['upper'],n,seed)
            grid=_grid_geometry(raw,_grid_from_contract(contract))
            _,analytic=_geometry_status(grid,contract)
            for pos,(g,passed) in enumerate(zip(grid,analytic)):
                cid=f"{intent['study_id']}-{name}-{pos:03d}"
                output.append(dict(request_id=cid,candidate_id=cid,source=name,target=None,requested_triple=None,
                    target_cell=None,sparse_cell=None,q_proxy=None,q_emx=None,proxy=None,score=None,
                    support='GEOMETRY_DOE_NO_PROXY_SELECTION',decoded_continuous_geometry=None,raw_lhs_geometry=raw[pos].tolist(),
                    geometry=g.tolist(),analytic_pass=bool(passed),predicted_cell=None,
                    model_id=None,qscan_source_candidate=None))
        require(len(directed)==len(doe)==32, 'Arm proposal cap differs')
        rng=np.random.Generator(np.random.PCG64(intent['seeds']['order']))
        directed=[directed[j] for j in rng.permutation(32)];doe=[doe[j] for j in rng.permutation(32)]
        order=[]
        for pos in range(32):
            arms=[('COVERAGE_DIRECTED',directed[pos]),('GEOMETRY_DOE_CONTROL',doe[pos])]
            if rng.integers(2):arms.reverse()
            for arm,row in arms:order.append(dict(row,arm=arm,arm_order=pos+1,global_order=len(order)+1))
        hashes=[canonical_geometry_sha256(dict(zip(fields,r['geometry'])),fields=fields) if finite_geometry(r['geometry']) else None for r in order]
        multiplicity=Counter(h for h in hashes if h is not None)
        for row,h in zip(order,hashes):
            reasons=[]
            if h is not None:
                for name,known_hashes in canonical_sets.items():
                    if h in known_hashes:reasons.append('EXISTING_CANONICAL_'+name)
                for name,known_grid in grid_sets.items():
                    if h in known_grid:reasons.append('NOMINAL_GRID_EQUIVALENT_'+name)
                if multiplicity[h]>1:reasons.append('DUPLICATE_PARAMETER_GROUP_ALL_MEMBERS_SYMMETRIC_HOLD')
            row.update(schema='eucap15_acquisition_candidate.v1',protocol_schema=intent['schema'],
                recipe_sha256=intent_pin['sha256'],frequency_hz=15000000000,geometry_fields=fields,geometry_units='um',
                canonical_geometry_sha256=h,parameter_geometry_hash=_geometry_hash(row['geometry'],fields) if h else None,
                identity_not_actual_gds=True,duplicate_reasons=reasons,
                local_dispatch_eligible=h is not None and row['analytic_pass'] and not reasons,
                assigned_development_split=('train','validation','test')[_split_for_hash(h,17)] if h else None,
                split_used_for_proposal_selection=False,no_replacement=True,no_q_fallback=True,
                native_status='NOT_SUBMITTED',reservation_status='NOT_RESERVED',
                gds_status='NOT_RUN',drc_status='NOT_RUN',emx_status='NOT_RUN',actual_response=None,
                actual_landing=None,coverage_gain=None,solver_start_order=None,solver_seconds=None,
                total_stage_cost_seconds=None,storage_bytes=None,owner_current_ledger_check='REQUIRED')
        jsonl(out/'SELECTED_CANDIDATES.jsonl',order)
        csv_write(out/'CANDIDATE_INDEX.csv',[{k:r[k] for k in ('arm','arm_order','global_order','request_id','candidate_id','source','q_proxy',
            'analytic_pass','duplicate_reasons','local_dispatch_eligible','canonical_geometry_sha256','assigned_development_split',
            'target_cell','predicted_cell','actual_landing','native_status','reservation_status','solver_seconds','storage_bytes')} for r in order])
        csv_write(out/'ROUND_TRACE.csv',[dict(round=1,arm=arm,intent_sha256=intent_pin['sha256'],
            baseline_train=3801,baseline_occupied=159,original_proposal_cap=32,sparse_targets=25 if arm=='COVERAGE_DIRECTED' else 0,
            exploration_proposals=7 if arm=='COVERAGE_DIRECTED' else 0,maximum_solver_starts=16,
            predeclared_equal_train_K=4,actual_solver_starts=None,actual_unique_strict_core_train=None,
            equal_m_status='NOT_RUN',equal_K_status='NOT_RUN',coverage_gain=None,
            stage_cost_seconds=None,incremental_storage_bytes=None,native_status='NOT_SUBMITTED') for arm in ('COVERAGE_DIRECTED','GEOMETRY_DOE_CONTROL')])
        counts={arm:dict(original_denominator=32,analytic_pass=sum(r['analytic_pass'] for r in order if r['arm']==arm),
            analytic_fail=sum(not r['analytic_pass'] for r in order if r['arm']==arm),
            duplicate_held=sum(bool(r['duplicate_reasons']) for r in order if r['arm']==arm),
            local_dispatch_eligible=sum(r['local_dispatch_eligible'] for r in order if r['arm']==arm)) for arm in ('COVERAGE_DIRECTED','GEOMETRY_DOE_CONTROL')}
        # Recheck all frozen source bytes including model inputs after the sole
        # inference. No output is a native release or evidence of solver starts.
        for item in [*inputs.values(),*intent['implementation'].values()]:checked(item)
        receipt=dict(schema='eucap15_controlled_acquisition_preparation.v1',status='PREPARED_NOT_NATIVE_RELEASE',
            created_utc=datetime.now(timezone.utc).isoformat(),intent=intent_pin,counts=counts,N_requests_sparse=25,
            N_logical_proxy_candidates=275,N_total_proposals=64,N_selected_q=sum(r['q_proxy'] is not None for r in directed if r['source']=='SPARSE_TARGETED'),
            source_bytes_unchanged=True,model_loads=2,training_updates=0,inference_batches=1,
            sampling_feedback='FIXED_ORIGINAL3801_BASELINE_ONE_ROUND_NO_WITHIN_ROUND_ADAPTATION',
            prior20_train_used_only_for_exclusion=True,previous128_diagnostic_used=False,
            numeric_validation_or_test_labels_used=0,metadata_label_fields_mechanically_parsed=True,
            qscan_evidence='SELF_PROXY',raw_decoder_logits='NOT_EXPOSED_NOT_CLAIMED',
            original64_and_old256_and128_regeneration=False,
            native_status='NOT_SUBMITTED',native_release_granted=False,owner_adapter='NOT_INSTALLED_FOR_NEW_FRAME',
            actual_emx_starts=None,actual_physical_gain=None,elapsed_seconds=time.monotonic()-started,
            preparation_output_bytes=sum(p.stat().st_size for p in out.iterdir() if p.is_file()),
            failures={k:len(v) for k,v in failures.items()},
            limitations=intent['limitations'])
        write(out/'PREPARATION_RECEIPT.json',receipt)
        artifacts={p.name:pin(p) for p in sorted(out.iterdir()) if p.is_file()}
        write(out/'MANIFEST.json',dict(schema='eucap15_controlled_acquisition_manifest.v1',
            status='PREPARATION_ONLY_NOT_NATIVE_RELEASE',intent=intent_pin,files=artifacts))
        with (out/'SHA256SUMS').open('x') as stream:
            for p in sorted(out.iterdir()):
                if p.is_file() and p.name!='SHA256SUMS':stream.write(pin(p)['sha256']+'  '+p.name+'\n')
        print(json.dumps(dict(status=receipt['status'],receipt=pin(out/'PREPARATION_RECEIPT.json'),manifest=pin(out/'MANIFEST.json'),counts=counts),ensure_ascii=False))
    except Exception as error:
        write(out/'FAILURE_RECEIPT.json',dict(status='FAIL_PRESERVED_NO_AUTOMATIC_RETRY',intent=intent_pin,
            error=repr(error),traceback=traceback.format_exc(),native_dispatched=False))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent',required=True);parser.add_argument('--intent-sha',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    run(args.intent,args.intent_sha,args.out)
