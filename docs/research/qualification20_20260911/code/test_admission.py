"""Local software tests only; shared MARS sources are NOT validated by fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import admission as a
from atomic_primitives import lease, BusyStudy

D = Path(__file__).resolve().parent
INPUTS = json.loads((D/'INPUTS.json').read_bytes())


def local_reader(entry):
    pair = entry['member']['full56_evidence']['feature_receipt']
    remote = Path(pair['original']['path']).parents[3]
    local = Path(pair['resolved']['path']).parents[3]

    def read(pin):
        path = Path(pin['path'])
        if path.is_relative_to(remote):
            data = (local/path.relative_to(remote)).read_bytes()
            a.require(hashlib.sha256(data).hexdigest() == pin['sha256'], 'local fixture hash mismatch')
            return data
        return b'LOCAL_TEST_ONLY_SHARED_PIN_NOT_VERIFIED'
    return read


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()/'qualified15_single_member_v1'
        (self.root/'records').mkdir(parents=True)
        (self.root/'records/000001.json').write_bytes((D/'FIRST_COMMIT.json').read_bytes())
        self.entry = copy.deepcopy(INPUTS['entries'][0])

    def tearDown(self):
        self.temp.cleanup()

    def qualify(self, entry=None, formal=None, reader=None):
        e = entry if entry is not None else self.entry
        return a.qualify(e, INPUTS, formal or {}, reader or local_reader(e))

    def pub(self, e):
        return a.publish(self.root, e, 'SYNTHETIC_LOCAL_TEST', verify=lambda evidence: None)

    def test_all19_frozen_metadata(self):
        a.validate_shared(INPUTS)
        for entry in INPUTS['entries']:
            with self.subTest(candidate=entry['member']['request_id']):
                self.qualify(entry)

    def test_append_and_identical_replay_preserves_first(self):
        before = (self.root/'records/000001.json').read_bytes()
        evidence = self.qualify()
        one = self.pub(evidence)
        two = self.pub(evidence)
        self.assertEqual((one['added'], two['added']), (1, 0))
        self.assertEqual(one['sha256'], two['sha256'])
        self.assertEqual(before, (self.root/'records/000001.json').read_bytes())
        self.assertEqual(a.ledger(self.root)[-1]['value']['checkpoint']['increment_accepted'], 2)

    def test_multiple_records_chain_and_restart(self):
        for entry in INPUTS['entries'][:3]:
            self.pub(self.qualify(entry))
        self.assertEqual(len(a.ledger(self.root)), 4)
        self.assertEqual(self.pub(self.qualify(INPUTS['entries'][1]))['added'], 0)

    def test_split_and_geometry_drift_rejected(self):
        for field, value in [('split', 'test'), ('geometry_sha256', '0'*64), ('geometry_units', 'nm')]:
            e = copy.deepcopy(self.entry)
            e['member'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.qualify(e)

    def test_known_pool_and_formal_duplicates_rejected(self):
        e = copy.deepcopy(self.entry)
        e['classified']['known_pool_match'] = 'SYNTHETIC_DUPLICATE'
        with self.assertRaises(ValueError):
            self.qualify(e)
        key = a._production_geometry_fingerprint(self.entry['member']['geometry'])
        with self.assertRaises(ValueError):
            self.qualify(formal={key:[123]})

    def test_label_and_history_drift_rejected(self):
        e = copy.deepcopy(self.entry)
        e['member']['actual'][0] += .01
        with self.assertRaises(ValueError):
            self.qualify(e)
        e = copy.deepcopy(self.entry)
        e['historical_lookup']['historical_production_geometry_sha256'] = '0'*64
        with self.assertRaises(ValueError):
            self.qualify(e)

    def test_missing_member_does_not_promote_or_block_next(self):
        def missing(pin):
            raise FileNotFoundError('SYNTHETIC_MISSING_ARTIFACT')
        with self.assertRaises(FileNotFoundError):
            self.qualify(reader=missing)
        self.assertEqual(len(a.ledger(self.root)), 1)
        self.assertEqual(self.pub(self.qualify(INPUTS['entries'][1]))['added'], 1)

    def test_source_drift_prevents_commit(self):
        def fail(e):
            raise ValueError('SYNTHETIC_SOURCE_DRIFT')
        with self.assertRaises(ValueError):
            a.publish(self.root, self.qualify(), 'TEST', verify=fail)
        self.assertEqual(len(a.ledger(self.root)), 1)

    def test_lock_excludes_other_writer(self):
        with lease(self.root/'WRITE.lock'):
            with self.assertRaises(BusyStudy):
                self.pub(self.qualify())

    def test_first_tamper_rejected(self):
        path = self.root/'records/000001.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            self.pub(self.qualify())

    def test_broken_chain_and_record_evidence_rejected(self):
        result = self.pub(self.qualify())
        path = Path(result['path'])
        d = json.loads(path.read_bytes())
        d['record']['physical15']['lp_nh'] += 1
        path.write_text(json.dumps(d))
        with self.assertRaises(ValueError):
            a.ledger(self.root)

    def test_conflicting_replay_rejected(self):
        e = self.qualify()
        self.pub(e)
        e['physical15']['lp_nh'] += 1
        with self.assertRaises(ValueError):
            self.pub(e)

    def test_sparse_request_and_q_candidate_stay_distinct(self):
        e = copy.deepcopy(next(e for e in INPUTS['entries'] if e['member']['source'] == 'SPARSE_TARGETED'))
        q = self.qualify(e)
        self.assertNotEqual(q['member']['request_id'], q['member']['candidate_id'])
        e['member']['q_proxy'] += 1
        with self.assertRaises(ValueError):
            self.qualify(e)


if __name__ == '__main__':
    unittest.main()
