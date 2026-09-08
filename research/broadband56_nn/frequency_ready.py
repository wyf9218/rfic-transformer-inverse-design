"""Finite model-ready dependency: reuse completed A/B; run independent Q scan."""
import argparse
import json
from pathlib import Path

from .io import read_json, save_json, utc_now
from .frequency_evaluation import pin, verify_pin
from . import frequency_qscan


def finish(study,out,seed):
    study,out=Path(study),Path(out)
    pair=read_json(study/'PAIR_RECEIPT.json')
    post=read_json(study/'posttrain/POSTTRAIN_RECEIPT.json')
    if post['status']!='ARTIFACTS_READY_VISUAL_QA_PENDING':raise ValueError('completed native posttrain required')
    for role in pair['roles'].values():
        for key in ('receipt','best','last'):verify_pin(role[key])
    config=dict(schema='frequency_qscan_request.v1',study_id=out.name,dataset_scope='FORMAL_10K',
        data_root=pair['data_root'],forward_checkpoint=pair['roles']['forward']['best']['path'],
        inverse_checkpoint=pair['roles']['inverse']['best']['path'],frequency_ghz=pair['frequency_ghz'],
        label_mode=pair['label_mode'],random_count=10000,holdout_count=100,batch_requests=32,
        allow_extrapolation=True,seed=int(seed),device='cpu')
    if not (out/'QSCAN_FREEZE.json').exists():frequency_qscan.prepare(config,out)
    elif read_json(out/'QSCAN_FREEZE.json')['config']!=config:raise ValueError('model-ready Qscan identity differs')
    result=frequency_qscan.run(out)
    receipt=out/'MODEL_READY_DEPENDENCY_RECEIPT.json'
    if not receipt.exists():
        save_json(receipt,dict(schema='frequency_ready_dependency.v1',created_utc=utc_now(),
            status='QSCAN_PROXY_READY_PHYSICAL_PENDING',pair=pin(study/'PAIR_RECEIPT.json'),
            original_AB_reused_without_recalculation=pin(study/'posttrain/evaluation/test/EVALUATION_SUMMARY.json'),
            qscan=pin(out/'QSCAN_SUMMARY.json'),model_package=pin(study/'posttrain/package/PACKAGE_RECEIPT.json'),
            REAL_EMX_VALIDATION='NOT_RUN',physical_dispatch='NOT_INSTALLED_PENDING_RESEARCH_ISOLATION_HANDOFF'))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',required=True)
    p.add_argument('--out',required=True);p.add_argument('--seed',required=True,type=int)
    a=p.parse_args();result=finish(a.study,a.out,a.seed)
    print(json.dumps(dict(status=result['status'],frequency_ghz=result['frequency_ghz'])))


if __name__=='__main__':main()
