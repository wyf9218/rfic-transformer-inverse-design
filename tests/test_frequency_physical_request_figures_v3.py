"""Synthetic source-bound layout tests, without native/model/statistics calls."""
import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from research.broadband56_nn import frequency_physical_request_figures_v3 as fix

v1 = fix.v1


def source():
    rid = "synthetic-f10-second"
    rows = []
    for q in range(10, 21):
        valid = q not in (18, 19)
        rows.append(dict(request_id=rid, frequency_ghz=10, q_target=q, q_proxy=12,
            dataset_scope=v1.FORMAL, model_id="synthetic-independent-model", strict_valid=valid,
            solved=valid, stage="SOLVED_STRICT_VALID" if valid else "GDS_FAIL",
            proxy_score=.000001 if q == 12 else abs(q-12)*.01, emx_score=.02 if valid else None,
            target_relative_absolute_percent=[q/10, q/20, q/30, q/40] if valid else None))
    return dict(rows=rows, request=dict(request_id=rid, q_emx="", q_proxy="12"))


class RequestFiguresV3(unittest.TestCase):
    def tearDown(self):
        v1.plot_style().close("all")

    def test_contract_not_limited_to_prior_two_requests(self):
        contract=dict(schema="frequency_physical_request_figure_contract.v3", no_clobber=True,
                      jobs=[dict(request_id="a-future-published-request", kind="percent")])
        fix.authorized(contract, "a-future-published-request", "percent")

    def test_unlisted_job_rejected(self):
        c=dict(schema="frequency_physical_request_figure_contract.v3", no_clobber=True, jobs=[])
        with self.assertRaisesRegex(ValueError, "authorized"):
            fix.authorized(c, "unlisted", "score")

    def test_duplicate_job_rejected(self):
        j=dict(request_id="x", kind="score")
        c=dict(schema="frequency_physical_request_figure_contract.v3", no_clobber=True, jobs=[j,j])
        with self.assertRaisesRegex(ValueError, "uniquely"):
            fix.authorized(c, "x", "score")

    def test_invalid_kind_rejected(self):
        c=dict(schema="frequency_physical_request_figure_contract.v3", no_clobber=True, jobs=[])
        with self.assertRaisesRegex(ValueError, "kind"):
            fix.authorized(c, "x", "all")

    def test_percent_constructor_no_global_export_mutation_or_extra_figure(self):
        s=source(); old=copy.deepcopy(s); exporter=v1.export
        fig=fix.construct_percent(s, {"sha256":"a"*64})
        self.assertEqual(len(fig.axes), 4)
        self.assertIs(v1.export, exporter)
        self.assertEqual(s, old)
        self.assertEqual(len(v1.plot_style().get_fignums()), 1)

    def test_adjacent_failure_labels_wrap_only(self):
        fig=fix.construct_percent(source(), {"sha256":"a"*64})
        old=[(t,t.get_text(),t.get_position()) for ax in fig.axes for t in ax.texts]
        result=fix.repair_percent(fig)
        self.assertEqual(result['N_wrapped_failure_labels'], 8)
        for text,value,pos in old:
            expected="GDS/\nCadence\nfail" if value=="GDS/Cadence\nfail" else value
            self.assertEqual(text.get_text(), expected)
            self.assertEqual(text.get_position(), pos)

    def test_percentage_bar_values_preserved(self):
        fig=fix.construct_percent(source(), {"sha256":"a"*64})
        rectangles=[(p,p.get_path().vertices.copy(),p.get_transform().get_matrix().copy()) for ax in fig.axes for p in ax.patches]
        fix.repair_percent(fig)
        for p,vertices,matrix in rectangles:
            np.testing.assert_array_equal(p.get_path().vertices, vertices)
            np.testing.assert_array_equal(p.get_transform().get_matrix(), matrix)

    def test_no_matching_percent_label_fails_without_mutating_values(self):
        s=source()
        for r in s['rows']:
            if not r['strict_valid']:r['stage']='DRC_FAIL'
        fig=fix.construct_percent(s, {'sha256':'a'*64})
        with self.assertRaisesRegex(ValueError, 'no matching'):
            fix.repair_percent(fig)

    def test_score_reuses_v2_and_preserves_line_data(self):
        fig=fix.v2.construct_score(source(), {'sha256':'a'*64})
        before=[(line,line.get_xydata().copy()) for line in fig.axes[0].lines]
        result=fix.v2.repair_artists(fig)
        self.assertEqual(result['status'],'ARTIST_DATA_AND_BOUNDS_PASS')
        for line,values in before:np.testing.assert_array_equal(values,line.get_xydata())

    def test_percent_export_only_one_trio(self):
        with tempfile.TemporaryDirectory() as tmp:
            fig=fix.construct_percent(source(), {'sha256':'a'*64});fix.repair_percent(fig)
            exports=v1.export(fig,Path(tmp),'target_percent_by_q_v3')
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ['target_percent_by_q_v3.pdf','target_percent_by_q_v3.png','target_percent_by_q_v3.svg'])
            for p in exports:v1.verify(p)

    def test_no_clobber_before_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'output exists'):
                fix.build('missing', 'unlisted', 'score', tmp)


if __name__=='__main__':unittest.main()
