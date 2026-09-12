"""New resume-only checks; synthetic identities, no old physical replay."""
import unittest

from .eucap15_train_coverage_received import SCOPE, resume_state


class ResumeCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.csv = dict(path="/synthetic/coverage.csv", sha256="a" * 64, bytes=1)
        self.old = "b" * 64
        self.new = dict(sha256="c" * 64)
        self.cp = dict(scope=SCOPE, full_production_cursor=None, original_split_unchanged=True,
                       coverage=self.csv, geometry_hashes=["d" * 64], unique_train=1,
                       received_source=dict(sha256=self.old))

    def test_original_checkpoint_migrates_consumed_identity_without_replay(self):
        identities, consumed = resume_state(self.cp, self.csv, self.new)
        self.assertEqual(identities, {"d" * 64})
        self.assertEqual(consumed, {self.old})

    def test_repeated_source_rejected_from_old_or_new_checkpoint(self):
        for old in (self.old, "e" * 64):
            self.cp["consumed_source_sha256s"] = ["e" * 64]
            with self.assertRaisesRegex(ValueError, "SOURCE_ALREADY_CONSUMED"):
                resume_state(self.cp, self.csv, dict(sha256=old))

    def test_coverage_binding_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "coverage pin mismatch"):
            resume_state(self.cp, dict(self.csv, sha256="f" * 64), self.new)

    def test_duplicate_checkpoint_identity_rejected(self):
        self.cp["geometry_hashes"] *= 2
        self.cp["unique_train"] = 2
        with self.assertRaisesRegex(ValueError, "identity count differs"):
            resume_state(self.cp, self.csv, self.new)


if __name__ == "__main__":
    unittest.main()
