"""Synthetic metadata-anchor guards, not physical qualification evidence."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
import family_entry as f


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self.source=dict(path='/synthetic/runtime/batch_registration.py',sha256=f.ENDPOINT_V2_REGISTRATION_SHA,bytes=1)
        self.upgrade=dict(path='/synthetic/UPGRADE.json',sha256=f.ENDPOINT_V2_UPGRADE_SHA,bytes=1)
        self.release=dict(config={},sources=[self.source],successor_registration=dict(endpoint_upgrade=self.upgrade))
        self.reads=[]
        self.fixed=SimpleNamespace(read=lambda _:dict(code_root='/synthetic/runtime'),bytes_for=self.reads.append)
        self.successor=SimpleNamespace(TRUSTED_REGISTRATION_SHA='legacy-registry-sha')

    def test_exact_anchor_and_bytes_checked(self):
        self.assertEqual(f.successor_registration_anchor(self.fixed,self.successor,self.release),f.ENDPOINT_V2_REGISTRATION_SHA)
        self.assertEqual(self.reads,[self.upgrade,self.source])

    def test_old_registry_without_upgrade_unchanged(self):
        self.release['successor_registration']={}
        self.assertEqual(f.successor_registration_anchor(self.fixed,self.successor,self.release),'legacy-registry-sha')
        self.assertEqual(self.reads,[])

    def test_wrong_upgrade_rejected(self):
        self.upgrade['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'upgrade identity'):
            f.successor_registration_anchor(self.fixed,self.successor,self.release)

    def test_missing_or_wrong_registry_rejected(self):
        for sources in ([],[dict(self.source,sha256='0'*64)],[dict(self.source,path='/foreign/batch_registration.py')]):
            with self.subTest(sources=sources):
                self.release['sources']=sources
                with self.assertRaisesRegex(ValueError,'registration source'):
                    f.successor_registration_anchor(self.fixed,self.successor,self.release)

    def test_changed_bytes_propagate(self):
        def changed(_):raise ValueError('actual bytes mismatch')
        self.fixed.bytes_for=changed
        with self.assertRaisesRegex(ValueError,'actual bytes mismatch'):
            f.successor_registration_anchor(self.fixed,self.successor,self.release)


if __name__=='__main__':unittest.main(verbosity=2)
