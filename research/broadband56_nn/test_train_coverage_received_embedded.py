"""Three synthetic checks of the embedded compact path only; no old QA replay."""
import copy
import unittest

from .eucap15_train_coverage_received import FIELDS, campaign, compact_groups, embedded_metadata, geometry_identity


class EmbeddedCompactTests(unittest.TestCase):
    def setUp(self):
        values = [100.125 + i for i in range(10)]
        self.row = dict(request_id="synthetic-train", source="GEOMETRY_DOE", geometry=values,
                        geometry_fields=FIELDS, geometry_units="um", assigned_development_split="train",
                        geometry_sha256=campaign.canonical_geometry_sha256(dict(zip(FIELDS, values))),
                        strict=True, formally_admitted_in_received_source=True,
                        valid_for_strict_comparison=True, core15_eligible=True,
                        result_pin=dict(path="/synthetic/RESULT.json", sha256="a" * 64, bytes=1),
                        formal_records=[dict(request_id="synthetic-train", assigned_split_from_frozen_request="train",
                                             pin=dict(path="/synthetic/formal.json", sha256="b" * 64, bytes=1))])

    def test_embedded_success_and_non_train_backfill_never_consumed(self):
        class HeldOut(dict):
            def __getitem__(self, key):
                if key not in ("request_id", "assigned_development_split"):
                    raise AssertionError("Held-out content accessed")
                return super().__getitem__(key)

        other = HeldOut(request_id="synthetic-test", assigned_development_split="test")
        doc = dict(schema="eucap15_new59_compact_train_and_backfill_sources.v1",
                   current_formal_train_rows=[], prior_formal_backfill_rows=[self.row, other])
        groups, excluded = compact_groups(doc)
        self.assertEqual(groups[1][1], [self.row])
        self.assertEqual(excluded, [dict(request_id="synthetic-test", split="test",
                                       reason="NON_TRAIN_BACKFILL_NOT_CONSUMED")])
        self.assertIs(embedded_metadata(groups[1][1][0]), self.row)
        self.assertEqual(geometry_identity(self.row), (self.row["geometry_sha256"], None))
        old = dict(schema="eucap15_new27_compact_train_and_pending_sources.v1",
                   current_formal_train_rows=[self.row], prior_formal_backfill_train_rows=[],
                   pending_formal_rows=[other])
        self.assertEqual(compact_groups(old)[0][0][1], [self.row])

    def test_missing_or_conflicting_embedded_fields_fail_closed(self):
        for field in ("geometry", "geometry_fields", "geometry_units", "source", "strict", "result_pin"):
            row = copy.deepcopy(self.row)
            del row[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "EMBEDDED_METADATA_MISSING"):
                embedded_metadata(row)
        for change, message in ((dict(strict=False), "QUALIFICATION_NOT_TRUE"),
                                (dict(source=""), "SOURCE_MISSING"),
                                (dict(result_pin=dict(path="/x", sha256="wrong", bytes=1)), "PIN_INVALID")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                embedded_metadata(dict(self.row, **change))
        wrong_hash = dict(self.row, geometry_sha256="c" * 64)
        self.assertEqual(geometry_identity(embedded_metadata(wrong_hash))[1], "CANONICAL9_HASH_MISMATCH")
        wrong_split = copy.deepcopy(self.row)
        wrong_split["formal_records"][0]["assigned_split_from_frozen_request"] = "test"
        with self.assertRaisesRegex(ValueError, "Formal header join differs"):
            embedded_metadata(wrong_split)
        resolved = copy.deepcopy(self.row)
        native = resolved["formal_records"][0]
        native["assigned_split_from_frozen_request"] = "UNKNOWN"
        resolution = dict(header_pin=native["pin"], request_id=native["request_id"],
                          assigned_split_from_frozen_request="train",
                          new_result_release=dict(path="/synthetic/owner/RELEASE.json", sha256="d" * 64, bytes=1))
        resolved["formal_records"] = [dict(request_id=native["request_id"], pin=native["pin"],
            assigned_development_split="train", native_header=native, split_resolution=resolution,
            split_resolution_source=dict(path="/synthetic/OBSERVATION.json", sha256="e" * 64))]
        resolved["result_pin"]["path"] = "/synthetic/owner/synthetic-train/RESULT.json"
        self.assertIs(embedded_metadata(resolved), resolved)
        for field, value in (("header_pin", dict(native["pin"], sha256="f" * 64)),
                             ("request_id", "different-request"),
                             ("assigned_split_from_frozen_request", "test")):
            wrong = copy.deepcopy(resolved)
            wrong["formal_records"][0]["split_resolution"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "RESOLUTION_JOIN_DIFFERS"):
                embedded_metadata(wrong)

    def test_schema_and_backfill_key_conflicts_rejected(self):
        doc = dict(schema="eucap15_new59_compact_train_and_backfill_sources.v1",
                   current_formal_train_rows=[], prior_formal_backfill_rows=[])
        with self.assertRaisesRegex(ValueError, "CONFLICTING_BACKFILL_KEYS"):
            compact_groups(dict(doc, prior_formal_backfill_train_rows=[]))
        with self.assertRaisesRegex(ValueError, "Wrong received source schema"):
            compact_groups(dict(doc, schema="unrecognized.v1"))
        with self.assertRaisesRegex(ValueError, "MISSING_DECLARED_BACKFILL_KEY"):
            compact_groups(dict(schema=doc["schema"], current_formal_train_rows=[],
                                prior_formal_backfill_train_rows=[]))


if __name__ == "__main__":
    unittest.main()
