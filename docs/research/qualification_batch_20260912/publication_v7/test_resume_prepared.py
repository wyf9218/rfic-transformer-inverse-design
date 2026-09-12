"""Two new retry/checkpoint fixtures; no production preparation or native calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock
import resume_prepared as r

ROOT=Path(__file__).parents[4]
ATOMIC_PATH=ROOT/'reports/eucap15_native_owner_20260909T062500Z/qualified15_holdout_partition_v3/atomic_primitives.py'
spec=importlib.util.spec_from_file_location('original_atomic_fixture_only',ATOMIC_PATH)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


def pin(path):
    path=Path(path).absolute();raw=path.read_bytes()
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def read_exact(p,mapping=None):
    path=Path((mapping or {}).get(p['path'],p['path']));raw=path.read_bytes()
    r.require(hashlib.sha256(raw).hexdigest()==p['sha256'] and len(raw)==p['bytes'],'fixture pin changed')
    return raw


def fixture(root):
    union=root/'qualified15_single_member_v1';(union/'records').mkdir(parents=True)
    digest=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
    evidence=dict(schema='SYNTHETIC_PREPARED_NOT_REAL',member={'request_id':r.REQUEST_ID,'split':'validation'})
    epath=root/'PREPARED_EVIDENCE.json';a.atomic_json(epath,evidence,immutable=True)
    binding=dict(schema='SYNTHETIC_BINDING',source=pin(Path(r.__file__)),evidence=pin(epath))
    c=NS(lease=a.lease,atomic_json=a.atomic_json,SCHEMA='SYNTHETIC_FORMAL_NOT_REAL',digest=digest,
         legacy=NS(STATUS='SYNTHETIC_QUALIFIED',FP='SYNTHETIC_FP'))
    def publish(*args,**kwargs):
        with a.lease(union/'WRITE.lock'):
            path=union/'records/000001.json'
            value=dict(schema=c.SCHEMA,status=c.legacy.STATUS,
                       record=dict(request_id=r.REQUEST_ID,split='validation',scientific_contract_fingerprint=c.legacy.FP),
                       evidence=evidence,evidence_digest=digest(evidence))
            sha=a.atomic_json(path,value,immutable=True)
            return dict(status=c.legacy.STATUS,path=str(path),sha256=sha,added=1)
    c.publish=Mock(side_effect=publish)
    return NS(b=NS(pin=pin,read_exact=read_exact,safe_path=lambda p:Path(p).absolute()),
        c=c,state=root/r.STATE_NAME,union=union,binding=binding,evidence=evidence,evidence_pin=pin(epath),
        failed_pin={'path':'/SYNTHETIC/FAILED.json','sha256':r.FAILED_SHA,'bytes':1},
        prepared_pin={'path':'/SYNTHETIC/PREPARED.json','sha256':'a'*64,'bytes':1},mapping={},busy=a.BusyStudy)


class PreparedRetryTests(unittest.TestCase):
    def test_busy75_then_commit0_then_proven_replay_without_reprepare_or_reappend(self):
        with tempfile.TemporaryDirectory(prefix='train002_resume_fixture_') as td:
            ctx=fixture(Path(td).resolve())
            with a.lease(ctx.union/'WRITE.lock'):
                _,first,code=r.attempt(ctx)
            self.assertEqual(code,75);self.assertEqual(first['outcome']['status'],'RETRYABLE_LEDGER_BUSY')
            self.assertFalse((ctx.state/'SUCCESS.json').exists())
            binding_bytes=(ctx.state/'BINDING.json').read_bytes()
            _,second,code=r.attempt(ctx);self.assertEqual(code,0);self.assertEqual(second['outcome']['added'],1)
            committed=(ctx.union/'records/000001.json').read_bytes()
            _,third,code=r.attempt(ctx);self.assertEqual(code,0)
            self.assertEqual(third['outcome']['status'],'ALREADY_PROVEN_COMMITTED_NO_COUNT_CHANGE')
            self.assertEqual(third['outcome']['added'],0);self.assertFalse(third['publication_attempted'])
            self.assertEqual(ctx.c.publish.call_count,2)
            self.assertEqual((ctx.union/'records/000001.json').read_bytes(),committed)
            self.assertEqual((ctx.state/'BINDING.json').read_bytes(),binding_bytes)
            self.assertEqual(len(list((ctx.state/'attempts').glob('*.json'))),3)
            self.assertFalse(any(x['preparation_reexecuted'] or x['receiver_reexecuted'] for x in (first,second,third)))

    def test_changed_binding_or_committed_bytes_cannot_be_relabelled_success(self):
        with tempfile.TemporaryDirectory(prefix='train002_resume_fixture_') as td:
            ctx=fixture(Path(td).resolve());r.attempt(ctx)
            binding_bytes=(ctx.state/'BINDING.json').read_bytes()
            original=ctx.binding;ctx.binding=dict(original,evidence={'sha256':'e'*64})
            _,value,code=r.attempt(ctx);self.assertEqual(code,2)
            self.assertIn('binding conflict',value['outcome']['error'])
            self.assertEqual((ctx.state/'BINDING.json').read_bytes(),binding_bytes)
            ctx.binding=original
            record=ctx.union/'records/000001.json'
            a.atomic_json(record,{'SYNTHETIC_TAMPER':True})
            _,value,code=r.attempt(ctx);self.assertEqual(code,2)
            self.assertIn('commit bytes changed',value['outcome']['error'])
            self.assertEqual(ctx.c.publish.call_count,1)


if __name__=='__main__':unittest.main()
