"""Synthetic exact endpoint-set guards; no simulator or ledger writes."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
import consume_endpoint_v2 as c


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.items=[dict(new=dict(path='/synthetic/'+name,sha256=sha,bytes=1),parent=None)
                    for name,sha in c.ENDPOINT_V2_SOURCE_SHA.items()]
        self.upgrade=dict(changes={str(i):x for i,x in enumerate(self.items)})
        self.active=dict(endpoint_sources=deepcopy(self.items),successor_registration=dict(
            endpoint_upgrade=dict(path='/synthetic/upgrade',sha256=c.ENDPOINT_V2_UPGRADE_SHA,bytes=1)))
        self.reads=[]
        self.reader=SimpleNamespace(load=lambda _:self.upgrade,read=self.reads.append)

    def test_exact_five_each_actual_pin_read(self):
        c.validate_endpoint_sources(self.active,self.reader)
        self.assertEqual(self.reads,[x['new'] for x in self.items])

    def test_legacy_six_preserved_and_five_rejected(self):
        old=dict(endpoint_sources=self.items+[self.items[0]])
        c.validate_endpoint_sources(old,self.reader)
        with self.assertRaisesRegex(ValueError,'SOURCE6'):
            c.validate_endpoint_sources(dict(endpoint_sources=self.items),self.reader)

    def test_upgrade_identity_rejected(self):
        self.active['successor_registration']['endpoint_upgrade']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'UNTRUSTED_ENDPOINT'):
            c.validate_endpoint_sources(self.active,self.reader)

    def test_wrong_frozen_pin_rejected_even_when_manifest_agrees(self):
        self.active['endpoint_sources'][0]['new']['sha256']='0'*64
        self.upgrade['changes']['0']=self.active['endpoint_sources'][0]
        with self.assertRaisesRegex(ValueError,'SOURCE5'):
            c.validate_endpoint_sources(self.active,self.reader)

    def test_missing_extra_duplicate_or_relocated_pin_rejected(self):
        for items in (self.items[:-1],self.items+[self.items[0]],self.items[:4]+[self.items[0]],
                      [dict(self.items[0],new=dict(self.items[0]['new'],path='/foreign/evaluator.py')),*self.items[1:]]):
            with self.subTest(items=items):
                self.active['endpoint_sources']=items
                with self.assertRaisesRegex(ValueError,'SOURCE_BINDING_CHANGED'):
                    c.validate_endpoint_sources(self.active,self.reader)

    def test_actual_byte_check_failure_propagates(self):
        def fail(_):raise ValueError('actual source bytes changed')
        self.reader.read=fail
        with self.assertRaisesRegex(ValueError,'actual source bytes'):
            c.validate_endpoint_sources(self.active,self.reader)


if __name__=='__main__':unittest.main(verbosity=2)
