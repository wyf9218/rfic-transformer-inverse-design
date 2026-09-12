"""Process-local verified prefix for the unchanged historical publish function.

Only newly appended record bodies are read/hashed. Cached records still receive
complete metadata checks on every call: ordinary files are not immutable merely
because their writer uses exclusive publication. A fresh process verifies the
prefix once again; no unauthenticated on-disk cache or database is introduced.
"""
import hashlib
import json
from pathlib import Path
import stat
import threading
from types import FunctionType

import history_publication as h
import atomic_primitives
import geometry_helpers

HISTORY_SOURCE_SHA='885f794ada737faad7deed9c4cef3bc56ac92d7e7a631fd98c5381eb3b5a2811'


def signature(path):
    s=Path(path).lstat()
    h.require(stat.S_ISREG(s.st_mode),'PREFIX_NOT_REGULAR_FILE: '+str(path))
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink)


def directory_identity(path):
    s=Path(path).lstat()
    h.require(stat.S_ISDIR(s.st_mode),'PREFIX_NOT_DIRECTORY: '+str(path))
    return (s.st_dev,s.st_ino,s.st_mode)


class IncrementalPublisher:
    def __init__(self,root,*,first_sha=h.legacy.FIRST_SHA):
        self.root=Path(root);self.first_sha=first_sha;self.lock=threading.RLock()
        self.entries=[];self.signatures=[];self.requests=set()
        self.identities={k:set() for k in ('canonical_9dp','production_1e6','nominal_grid_5nm')}
        self.source_signatures={};self.source_pins=[]
        for module in (h,h.legacy,atomic_primitives,geometry_helpers):
            path=Path(module.__file__).resolve();before=signature(path)
            body=path.read_bytes();sha=hashlib.sha256(body).hexdigest()
            h.require(signature(path)==before,'SOURCE_CHANGED_DURING_PREFIX_BINDING')
            if module is h:h.require(sha==HISTORY_SOURCE_SHA,'UNSUPPORTED_HISTORY_PUBLISH_SOURCE')
            self.source_signatures[path]=before
            self.source_pins.append(dict(path=str(path),sha256=sha,bytes=len(body)))
        self.functions={name:getattr(h,name) for name in ('publish','ledger','make_record','build_evidence','identities','raw','digest','lease','atomic_json')}
        self.contract=(h.SCHEMA,h.EVIDENCE_SCHEMA,h.FP,h.STATUS)
        self.directory_signatures={path:directory_identity(path) for path in (self.root,self.root/'records')}
        self.lock_identity=None;self.body_reads=0
        # Bind only this function's ledger dependency. The executed publish code
        # object, source reconstruction, lease, atomic append, and readback are
        # unchanged; no shared module/global monkeypatch or extra writer exists.
        scope=dict(h.publish.__globals__);scope['ledger']=self._ledger
        self._original_publish=FunctionType(h.publish.__code__,scope,h.publish.__name__,
            h.publish.__defaults__,h.publish.__closure__)
        self._original_publish.__kwdefaults__=dict(h.publish.__kwdefaults__ or {})

    def _check_context(self):
        for path,expected in self.source_signatures.items():
            h.require(signature(path)==expected,'PREFIX_VALIDATOR_SOURCE_CHANGED: '+str(path))
        h.require(all(getattr(h,name) is fn for name,fn in self.functions.items()) and
            self.contract==(h.SCHEMA,h.EVIDENCE_SCHEMA,h.FP,h.STATUS),'PREFIX_VALIDATOR_CONTEXT_CHANGED')
        for path,expected in self.directory_signatures.items():
            h.require(directory_identity(path)==expected,'PREFIX_DIRECTORY_REPLACED: '+str(path))
        lock=signature(self.root/'WRITE.lock')
        identity=(lock[0],lock[1],lock[5])
        if self.lock_identity is None:self.lock_identity=identity
        h.require(identity==self.lock_identity,'PREFIX_WRITE_LOCK_REPLACED')

    def _append_verified(self,path,sequence):
        before=signature(path);data=path.read_bytes();self.body_reads+=1
        sha=hashlib.sha256(data).hexdigest();value=json.loads(data)
        h.require(signature(path)==before,'LEDGER_CHANGED_DURING_READ')
        record=value['record'];checkpoint=value['checkpoint']
        h.require(value['status']==h.STATUS and record['eucap15_qualified_accepted'] is True and
            record['scientific_contract_fingerprint']==h.FP,'unqualified or wrong-contract ledger')
        h.require(record['increment_sequence']==checkpoint['last_increment_sequence']==checkpoint['increment_accepted']==
            checkpoint['increment_15ghz_rows']==sequence and checkpoint['referenced_frequency_rows']==sequence*56,
            'ledger checkpoint sequence mismatch')
        h.require(value['evidence_digest']==h.digest(value['evidence']),'stored evidence corrupt')
        if sequence==1:
            h.require(sha==self.first_sha and checkpoint['prior_commit'] is None,'first record changed')
        else:
            h.require(checkpoint['prior_commit']==self.entries[-1]['sha256'],'broken prior commit chain')
        if value.get('schema')==h.SCHEMA:
            h.require(record==h.make_record(value['evidence'],sequence),'historical record/evidence mismatch')
            h.require(value['counting']['old_broadband_added']==0 and value['counting']['this_certified_increment']==1,
                'historical broadband recount forbidden')
        else:
            h.require(record['production_accepted_sequence'] is None and record['old_broadband_production_accepted'] is False,
                'unknown mixed-ledger historical schema')
            if sequence>1:h.require(record==h.legacy.make_record(value['evidence'],sequence),'legacy record/evidence mismatch')
        h.require(record['request_id'] not in self.requests,'duplicate ledger request')
        keys=h.identities(record['geometry'],record['geometry_fields'])
        h.require(keys['canonical_9dp']==record['geometry_sha256'],'ledger geometry mismatch')
        for kind,key in keys.items():h.require(key not in self.identities[kind],'duplicate ledger '+kind)
        # Retain just the fields read by the unchanged publish loop, not every
        # large historical evidence body in RAM. No caller receives this cache.
        entry=dict(path=str(path),sha256=sha,identities=keys,
            value=dict(record=dict(request_id=record['request_id']),evidence_digest=value['evidence_digest']))
        self.entries.append(entry);self.signatures.append(before);self.requests.add(record['request_id'])
        for kind,key in keys.items():self.identities[kind].add(key)

    def _ledger(self,root,first_sha):
        # Called only by the original publish function while its WRITE.lock is held.
        h.require(Path(root)==self.root and first_sha==self.first_sha,'PREFIX_ROOT_OR_FIRST_IDENTITY_CHANGED')
        self._check_context()
        paths=sorted((self.root/'records').glob('*.json'))
        h.require(paths and len(paths)>=len(self.entries),'PREFIX_TRUNCATED_OR_MISSING')
        for sequence,path in enumerate(paths,1):
            h.require(path.name==f'{sequence:06d}.json','ledger sequence/path conflict')
            if sequence<=len(self.entries):
                h.require(str(path)==self.entries[sequence-1]['path'] and signature(path)==self.signatures[sequence-1],
                    'CACHED_PREFIX_CHANGED: '+str(path))
            else:self._append_verified(path,sequence)
        return self.entries

    def publish(self,evidence,utc,*,reader=h.raw):
        with self.lock:
            return self._original_publish(self.root,evidence,utc,reader=reader,first_sha=self.first_sha)

    def summary(self):
        with self.lock:
            return dict(schema='eucap15_process_local_verified_publication_prefix.v1',
                root=str(self.root),verified_prefix_count=len(self.entries),
                head=None if not self.entries else {k:self.entries[-1][k] for k in ('path','sha256')},
                record_body_reads=self.body_reads,source_pins=self.source_pins,
                cache_durability='PROCESS_LOCAL_REVALIDATE_ON_RESTART',
                prefix_metadata_check='ALL_RECORDS_EACH_USE_DEV_INODE_SIZE_MTIME_CTIME_MODE_NLINK',
                limitations=['O(N) prefix metadata remains; no trusted filesystem change journal is assumed.',
                    'Not a physical qualification or full-history certification receipt.',
                    'Verified prefix may trail the record just atomically appended until the next call.'])
