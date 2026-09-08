"""Synthetic presentation-only regression tests; no physical or model calls."""
import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from research.broadband56_nn import frequency_physical_request_score_v2 as fix

v1 = fix.v1


def source(f=16, zero=True):
    rid = f"f{f}_qscan-HELDOUT_TRIPLE_AUDIT-000000"
    qp = 16 if f == 16 else 14
    fail = {16, 19, 20} if f == 16 else {12, 17, 18, 19, 20}
    rows = []
    for q in range(10, 21):
        good = q not in fail
        rows.append(dict(request_id=rid, candidate_id=f"{rid}-q{q}", frequency_ghz=f,
            model_id=f"synthetic-{f}", dataset_scope=v1.FORMAL, target_source="HELDOUT_TRIPLE_AUDIT",
            label_mode="STRICT_LUMPED", q_target=q, q_proxy=qp, selected_before_emx=q == qp,
            stage="SOLVED_STRICT_VALID" if good else "GDS_FAIL", solved=good, strict_valid=good,
            joint_hit=good, target=[1, 1, q, .4], proxy=[1, 1, q, .4],
            actual=[1, 1, q, .4] if good else None,
            emx_minus_target=[0]*4 if good else None, emx_minus_frozen_proxy=[0]*4 if good else None,
            target_relative_absolute_percent=[0]*4 if good else None,
            proxy_score=(0 if zero else 1e-8) if q == qp else abs(q-qp)*.01,
            emx_score=.01 if good else None, score_scale=list(v1.SCALE), absolute_tolerances=list(v1.TAU),
            source_pins={}))
    req = dict(request_id=rid, frequency_ghz=str(f), model_id=f"synthetic-{f}", dataset_scope=v1.FORMAL,
               q_proxy=str(qp), q_emx="", status="ACCOUNTED", full11_gate="NOT_AVAILABLE_INCOMPLETE_OR_INVALID")
    return dict(rows=rows, request=req)


def entry(s):
    return dict(request_id=s["request"]["request_id"], signature=v1.request_signature(s["rows"]))


class RequestScoreV2(unittest.TestCase):
    def tearDown(self):
        v1.plot_style().close("all")

    def test_v1_globals_and_source_unmodified(self):
        data = source(); original = copy.deepcopy(data); old_export = v1.export
        fig = fix.construct_score(data, {"sha256": "a"*64})
        self.assertEqual(len(fig.axes), 1)
        self.assertIs(v1.export, old_export)
        self.assertEqual(data, original)

    def test_exact_plot_data_and_selection_line_unchanged(self):
        fig = fix.construct_score(source(), {"sha256": "a"*64})
        lines = [(line, line.get_xydata().copy()) for line in fig.axes[0].lines]
        result = fix.repair_artists(fig)
        for line, expected in lines:
            np.testing.assert_array_equal(line.get_xydata(), expected)
        self.assertEqual(result["failure_q_slots"], [16, 19, 20])
        self.assertFalse(result["line_data_changed"])
        self.assertEqual(fig.axes[0].lines[2].get_label(), "preselected q_proxy=16")

    def test_failure_labels_are_in_disjoint_axis_not_masked_by_box(self):
        fig = fix.construct_score(source(17), {"sha256": "a"*64})
        fix.repair_artists(fig)
        ax, strip = fig.axes
        self.assertEqual(len(ax.texts), 0)
        self.assertEqual(len(strip.texts), 5)
        self.assertLess(strip.bbox.y1, ax.bbox.y0)
        self.assertEqual(len(strip.lines), 0)
        self.assertTrue(all(text.get_bbox_patch() is None for text in strip.texts))

    def test_zero_marker_clearance_and_nonnegative_ticks(self):
        fig = fix.construct_score(source(zero=True), {"sha256": "a"*64})
        result = fix.repair_artists(fig)
        self.assertEqual(result["status"], "ARTIST_DATA_AND_BOUNDS_PASS")
        self.assertLess(fig.axes[0].get_ylim()[0], 0)
        self.assertTrue(all(t >= 0 for t in fig.axes[0].get_yticks()))
        self.assertIn(0, fig.axes[0].get_yticks())

    def test_tiny_positive_marker_clearance(self):
        fig = fix.construct_score(source(17, zero=False), {"sha256": "a"*64})
        self.assertEqual(fix.repair_artists(fig)["N_score_marks"], 17)

    def test_ci_score_semantics_visible(self):
        fig = fix.construct_score(source(), {"sha256": "a"*64})
        fix.repair_artists(fig)
        text = "\n".join(t.get_text() for t in fig.texts)
        for phrase in ("R=1", "CI NOT_ESTIMABLE", "not percentages", "failure slots are not replaced", "full11 gate unmet"):
            self.assertIn(phrase, text)

    def test_original11_missing_rejected(self):
        s = source(); s["rows"].pop()
        with self.assertRaisesRegex(ValueError, "original11"):
            fix.validate_source(s, entry(s))

    def test_signature_mutation_rejected(self):
        s = source(); e = entry(s); s["rows"][0]["proxy_score"] = 999
        with self.assertRaisesRegex(ValueError, "signature"):
            fix.validate_source(s, e)

    def test_context_change_rejected(self):
        s = source(); s["rows"][0]["model_id"] = "other"
        with self.assertRaisesRegex(ValueError, "context"):
            fix.validate_source(s, entry(s))

    def test_selected_failure_is_not_replaced(self):
        s = source(); fix.validate_source(s, entry(s))
        selected = next(row for row in s["rows"] if row["selected_before_emx"])
        self.assertEqual(selected["q_target"], 16)
        self.assertEqual(selected["stage"], "GDS_FAIL")
        self.assertIsNone(selected["emx_score"])

    def test_full11_gate_cannot_be_bypassed(self):
        s = source(); s["request"]["q_emx"] = "14"
        with self.assertRaisesRegex(ValueError, "full11"):
            fix.validate_source(s, entry(s))

    def test_output_no_clobber_before_source_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "output exists"):
                fix.build(Path(tmp)/"missing-contract.json", Path(tmp))

    def test_source_pin_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"source"; p.write_text("synthetic")
            bad = dict(v1.pin(p), sha256="0"*64)
            with self.assertRaisesRegex(ValueError, "pin mismatch"):
                v1.verify(bad)

    def test_export_only_score_trio(self):
        with tempfile.TemporaryDirectory() as tmp:
            fig = fix.construct_score(source(), {"sha256": "a"*64}); fix.repair_artists(fig)
            exports = v1.export(fig, Path(tmp), "proxy_emx_score_by_q_v2")
            self.assertEqual(sorted(Path(p["path"]).suffix for p in exports), [".pdf", ".png", ".svg"])
            self.assertTrue(all(v1.verify(p).is_file() for p in exports))


if __name__ == "__main__":
    unittest.main()
