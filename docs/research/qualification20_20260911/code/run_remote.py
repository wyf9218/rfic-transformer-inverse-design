"""One bounded metadata-admission execution on MARS, with immutable readback."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import admission as a
from atomic_primitives import atomic_json

D = Path(__file__).resolve().parent
RN = Path('/volumes/research-localdata/ywang3652/eucap15_native_owner_20260909T062500Z')


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    release = json.loads((D/'RELEASE.json').read_bytes())
    for name, pin in release['files'].items():
        data = (D/name).read_bytes()
        a.require(hashlib.sha256(data).hexdigest() == pin['sha256'] and len(data) == pin['bytes'],
                  'released file changed: '+name)
    inputs = json.loads((D/'INPUTS.json').read_bytes())
    test = json.loads((D/'TEST_RECEIPT.json').read_bytes())
    a.require(test['returncode'] == 0, 'software tests failed')
    for pin in test['source_pins']:
        a.require(hashlib.sha256((D/Path(pin['path']).name).read_bytes()).hexdigest() == pin['sha256'],
                  'tested runtime/input mismatch')
    a.validate_shared(inputs)
    for i, pin in enumerate(inputs['source_pins']):
        data = (D/'proofs'/f'{i:02d}.json').read_bytes()
        a.require(hashlib.sha256(data).hexdigest() == pin['sha256'] and len(data) == pin['bytes'],
                  'frozen research proof mismatch')
    target = RN/'qualified15_single_member_v1'
    before = a.ledger(target)
    shared = inputs['contract']['pins']+[inputs['continuity']['checkpoint'], inputs['continuity']['progress']]
    for pin in shared:
        a.raw(pin)
    formal = a.formal_index(inputs)
    qualified, outcomes = [], []
    for entry in inputs['entries']:
        rid = entry['member']['request_id']
        try:
            evidence = a.qualify(entry, inputs, formal)
            atomic_json(D/'qualification'/f'{rid}.json', evidence, immutable=True)
            qualified.append(evidence)
        except (ValueError, KeyError, OSError) as error:
            row = dict(request_id=rid, status='HOLD_NO_COMMIT', utc=now(), error_type=type(error).__name__,
                       error=str(error), native_actions=0)
            atomic_json(D/'qualification'/f'{rid}_HOLD.json', row, immutable=True)
            outcomes.append(row)
    # Source pins are rechecked, but no historical SQL or physical extraction is rerun.
    for pin in shared+[inputs['continuity']['current_formal_state']['pin']]+[
            source['source'] for source in inputs['continuity']['accepted_sources']]:
        a.raw(pin)

    def verify(evidence):
        for pin in evidence['source_pins']:
            a.raw(pin)
        a.raw(inputs['continuity']['current_formal_state']['pin'])

    for evidence in qualified:
        rid = evidence['member']['request_id']
        try:
            result = a.publish(target, evidence, now(), verify=verify)
            replay = a.publish(target, evidence, now(), verify=verify)
            a.require(replay['added'] == 0 and replay['sha256'] == result['sha256'], 'non-idempotent replay')
            outcomes.append(dict(request_id=rid, utc=now(), status='COMMITTED_READBACK',
                                 result=result, replay=replay))
        except (ValueError, KeyError, OSError) as error:
            outcomes.append(dict(request_id=rid, status='HOLD_COMMIT_REQUIRES_RECONCILIATION',
                                 error_type=type(error).__name__, error=str(error), utc=now()))
    after = a.ledger(target)
    a.require(after[0]['sha256'] == before[0]['sha256'] == a.FIRST_SHA, 'first commit modified')
    a.raw(inputs['continuity']['current_formal_state']['pin'])
    v = os.statvfs(target)
    q = subprocess.run(['quota', '-s'], capture_output=True, text=True, timeout=30)
    commits = [x for x in outcomes if x['status'] == 'COMMITTED_READBACK']
    receipt = dict(schema='eucap15_existing19_train_admission_readback.v2', utc=now(),
        status='BOUNDED_PARTITION_PROCESSED_NOT_FULL_HISTORY_COMPLETION',
        release=dict(path=str(D/'RELEASE.json'), sha256=hashlib.sha256((D/'RELEASE.json').read_bytes()).hexdigest()),
        processed=len(outcomes), qualified=len(qualified), committed_new=sum(x['result']['added'] for x in commits),
        preexisting_replays=sum(x['result']['added'] == 0 for x in commits),
        verification_replays=len(commits), replay_added=sum(x['replay']['added'] for x in commits),
        held=sum(x['status'].startswith('HOLD') for x in outcomes), unprocessed=19-len(outcomes),
        outcomes=outcomes, before_checkpoint=before[-1]['value']['checkpoint'],
        final_checkpoint=after[-1]['value']['checkpoint'],
        record_pins=[{k:x[k] for k in ('path','sha256')} for x in after],
        unchanged_remaining_splits=inputs['unchanged_remaining_splits'],
        old_broadband_accepted=inputs['continuity']['current_formal_state']['value']['current_accepted'],
        old_broadband_feature_rows=inputs['continuity']['current_formal_state']['value']['feature_rows'],
        old_broadband_added=0, research6329_modified=False, full_history_qualified=False,
        full_100k_qualified_total=None, native_actions=0, training_actions=0, new64_admissions=0,
        historical_index_queries=0, physical_qa_reruns=0, available_disk_bytes=v.f_bavail*v.f_frsize,
        quota=dict(returncode=q.returncode, stdout=q.stdout, stderr=q.stderr,
                   research_mount_quota='UNKNOWN_UNLESS_LISTED_FOR_THIS_MOUNT'))
    sha = atomic_json(D/'READBACK_RECEIPT.json', receipt, immutable=True)
    print(json.dumps(dict(receipt_path=str(D/'READBACK_RECEIPT.json'), receipt_sha256=sha,
        **{k:receipt[k] for k in ('utc','processed','qualified','committed_new','preexisting_replays',
              'verification_replays','replay_added','held','unprocessed','final_checkpoint','available_disk_bytes','quota')})))


if __name__ == '__main__':
    main()
