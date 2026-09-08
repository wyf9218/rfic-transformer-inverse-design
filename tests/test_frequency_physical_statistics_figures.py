"""Synthetic static-renderer tests; no model or native physical calls."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.broadband56_nn import frequency_physical_statistics_figures as fig


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False))


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def candidate(f, request_index, q, *, solved=False):
    rid=f'synthetic-f{f}-{request_index:02d}'
    return dict(request_id=rid,candidate_id=rid+f'-q{q}',frequency_ghz=f,model_id=f'synthetic-model-{f}',
        dataset_scope=fig.DEVELOPMENT if f==15 else fig.FORMAL,target_source='HELDOUT_TRIPLE_AUDIT',label_mode='STRICT_LUMPED',
        q_target=q,q_proxy=14,selected_before_emx=q==14,stage='SOLVED_STRICT_VALID' if solved else 'PENDING',
        solved=solved,strict_valid=True if solved else None,joint_hit=True if solved else None,
        target=[1.,1.,float(q),.4],proxy=[1.,1.,float(q),.4],actual=[1.01,1.01,float(q)+.1,.401] if solved else None,
        emx_minus_target=[.01,.01,.1,.001] if solved else None,emx_minus_frozen_proxy=[.01,.01,.1,.001] if solved else None,
        target_relative_absolute_percent=[1.,1.,10/q,.25] if solved else None,
        proxy_score=0.,emx_score=.003 if solved else None,score_scale=list(fig.SCALE),absolute_tolerances=list(fig.TAU),
        source_pins={'fixture':{'sha256':'a'*64,'path':'/synthetic/source','bytes':1}})


def snapshot(root, *, modify=None):
    root.mkdir()
    rows=[candidate(f,r,q) for f in fig.FREQUENCIES for r in range(20) for q in range(10,21)]
    if modify:modify(rows)
    requests=[dict(request_id=group['request_id'],frequency_ghz=group['frequency_ghz'],model_id=group['model_id'],
        dataset_scope=group['dataset_scope'],q_proxy=14,q_emx=None,status='PENDING',full11_gate='NOT_AVAILABLE_INCOMPLETE_OR_INVALID') for group in rows[::11]]
    write_json(root/'CANDIDATE_ROWS.json',dict(schema='frequency_physical_candidate_rows.v1',feature_order=list(fig.FEATURES),rows=rows))
    write_json(root/'SUMMARY.json',dict(schema='synthetic',N_accounted_requests=0))
    write_csv(root/'REQUEST_STATUS.csv',requests)
    write_csv(root/'FREQUENCY_STATUS.csv',[dict(frequency_ghz=f,N_planned_requests=20) for f in fig.FREQUENCIES])
    write_csv(root/'METRICS.csv',[dict(frequency_ghz=5,model_id='synthetic',dataset_scope=fig.FORMAL,estimand='all_candidates',
        comparison='emx_minus_target',validity='strict_valid',feature='Lp_nH',n=0,mae=None,abs_error_p95=None)])
    write_csv(root/'SELECTION_COMPARISON.csv',[dict(frequency_ghz=5,dataset_scope=fig.FORMAL,status='NOT_AVAILABLE',N_common_complete_requests=0)])
    files=[fig.pin(p) for p in root.iterdir()]
    write_json(root/'MANIFEST.json',dict(artifacts=files))
    (root/'SHA256SUMS').write_text('synthetic-only manifest witness')
    write_json(root/'STATS_RECEIPT.json',dict(schema='frequency_physical_statistics_receipt.v1',status='PUBLISHED',
        artifacts=files,manifest=fig.pin(root/'MANIFEST.json'),summary=fig.pin(root/'SUMMARY.json'),sha256sums=fig.pin(root/'SHA256SUMS')))
    return root


class PhysicalFigures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_complete_original_frame_loaded(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'))
        self.assertEqual((len(data['requests']),len(data['candidates'])),(320,3520))
        self.assertEqual(len(data['grouped']),320)

    def test_no_missing_zero_coercion(self):
        self.assertIsNone(fig.number(''));self.assertIsNone(fig.number(None));self.assertEqual(fig.number(0),0)
        self.assertIsNone(fig.number('nan'))

    def test_source_sha_change_rejected(self):
        root=snapshot(self.root/'stats');(root/'CANDIDATE_ROWS.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'pin mismatch'):fig.load_snapshot(root)

    def test_partial_receipt_rejected(self):
        root=snapshot(self.root/'stats');receipt=json.loads((root/'STATS_RECEIPT.json').read_text());receipt['status']='PARTIAL'
        write_json(root/'STATS_RECEIPT.json',receipt)
        with self.assertRaisesRegex(ValueError,'complete statistics'):fig.load_snapshot(root)

    def test_duplicate_q_slot_rejected(self):
        root=snapshot(self.root/'stats',modify=lambda rows:rows[0].update(q_target=11))
        with self.assertRaisesRegex(ValueError,'original Q slot'):fig.load_snapshot(root)

    def test_changed_model_rejected(self):
        root=snapshot(self.root/'stats',modify=lambda rows:rows[1].update(model_id='other'))
        with self.assertRaisesRegex(ValueError,'model context'):fig.load_snapshot(root)

    def test_unsolved_numeric_error_rejected(self):
        root=snapshot(self.root/'stats',modify=lambda rows:rows[0].update(actual=[0,0,0,0]))
        with self.assertRaisesRegex(ValueError,'zero-filled'):fig.load_snapshot(root)

    def test_tolerance_change_rejected(self):
        root=snapshot(self.root/'stats',modify=lambda rows:rows[0].update(absolute_tolerances=[.05]*4))
        with self.assertRaisesRegex(ValueError,'tolerance contract'):fig.load_snapshot(root)

    def test_full11_gate_required(self):
        root=snapshot(self.root/'stats');rows=fig.csv_rows(root/'REQUEST_STATUS.csv');rows[0]['q_emx']='14';write_csv(root/'REQUEST_STATUS.csv',rows)
        manifest=json.loads((root/'MANIFEST.json').read_text());manifest['artifacts']=[fig.pin(p['path']) for p in manifest['artifacts']];write_json(root/'MANIFEST.json',manifest)
        receipt=json.loads((root/'STATS_RECEIPT.json').read_text());receipt['manifest']=fig.pin(root/'MANIFEST.json');write_json(root/'STATS_RECEIPT.json',receipt)
        with self.assertRaisesRegex(ValueError,'all11'):fig.load_snapshot(root)

    def test_stage_failures_distinct(self):
        row=candidate(5,0,10)
        for stage,expected in [('ANALYTIC_FAIL','Analytic fail'),('GDS_FAIL','GDS/Cadence fail'),('DRC_FAIL','Calibre fail'),('PENDING','Pending'),('EMX_NOT_SOLVED','Other not solved')]:
            self.assertEqual(fig.stage_bucket(dict(row,stage=stage)),expected)
        self.assertEqual(fig.stage_bucket(dict(row,solved=True,strict_valid=False)),'EMX invalid')

    def test_signature_covers_candidate_identity_and_physical_source(self):
        rows=[candidate(5,0,q,solved=True) for q in range(10,21)]
        first=fig.request_signature(rows)
        changed=copy.deepcopy(rows);changed[0]['candidate_id']='different'
        self.assertNotEqual(first,fig.request_signature(changed))
        changed=copy.deepcopy(rows);changed[0]['source_pins']['fixture']['sha256']='b'*64
        self.assertNotEqual(first,fig.request_signature(changed))
        changed=copy.deepcopy(rows);changed[0]['source_pins']['fixture']['path']='/relocated/samebytes'
        self.assertEqual(first,fig.request_signature(changed))

    def test_previous_changed_request_rejected(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'));rid=next(iter(data['grouped']))
        receipt=dict(schema='frequency_physical_statistics_figures_receipt.v1',status='COMPLETE',statistics={'sha256':'previous'},
            request_figures=[dict(request_id=rid,signature='wrong',exports=[])])
        p=self.root/'previous.json';write_json(p,receipt)
        with self.assertRaisesRegex(ValueError,'content changed'):fig.previous_reuse(p,data)

    def test_no_repeat_same_snapshot(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'));p=self.root/'previous.json'
        write_json(p,dict(schema='frequency_physical_statistics_figures_receipt.v1',status='COMPLETE',statistics=data['source_pin'],request_figures=[]))
        with self.assertRaisesRegex(ValueError,'already has figures'):fig.previous_reuse(p,data)

    def test_no_clobber_before_render(self):
        root=snapshot(self.root/'stats');out=self.root/'figs';out.mkdir()
        with patch.object(fig,'route_status',side_effect=AssertionError('must not render')):
            with self.assertRaisesRegex(ValueError,'no-clobber'):fig.build(root,out)

    def test_single_request_cannot_trigger_density_or_cdf(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'))
        data['candidates']=[candidate(f,0,q,solved=True) for f in fig.FREQUENCIES for q in range(10,21)]
        with patch.object(fig,'export',side_effect=AssertionError('sparse data must not draw')):
            exports,availability=fig.distribution_plots(data,self.root)
        self.assertEqual(exports,[])
        self.assertTrue(all(r['N_strict_valid_requests']==1 and r['CDF'].startswith('NOT_AVAILABLE') for r in availability))

    def test_empty_common11_is_not_plotted(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'))
        with patch.object(fig,'export',side_effect=AssertionError('no pseudo comparison')):
            self.assertEqual(fig.selection_plots(data,self.root),[])

    def test_formal_lookup_does_not_borrow_development(self):
        data=dict(metrics=[dict(frequency_ghz=15,dataset_scope=fig.DEVELOPMENT,estimand='all_candidates',comparison='emx_minus_target',validity='strict_valid',feature='Lp_nH',mae=123)])
        self.assertEqual(fig.metric_group(data,fig.FORMAL,'all_candidates','emx_minus_target','Lp_nH'),{})

    def test_real_static_export_three_formats(self):
        plt=fig.plot_style();page,ax=plt.subplots(figsize=(6,4));ax.plot([1,2],[1,2]);page.text(.1,.9,'SYNTHETIC QA ONLY')
        pins=fig.export(page,self.root,'synthetic')
        self.assertEqual({Path(p['path']).suffix for p in pins},{'.png','.svg','.pdf'})
        self.assertTrue(all(p['bytes']>100 for p in pins))

    def test_complete_marker_is_last_and_partial_marker_absent(self):
        root=snapshot(self.root/'stats');out=self.root/'figs'
        with patch.object(fig,'route_status',return_value=[]),patch.object(fig,'mae_p95',return_value=[]),\
             patch.object(fig,'heatmap',return_value=[]),patch.object(fig,'selection_plots',return_value=[]),\
             patch.object(fig,'distribution_plots',return_value=([],[])):
            result=fig.build(root,out)
        self.assertEqual(Path(result['path']).name,'FIGURES_RECEIPT.json')
        self.assertFalse((out/'.FIGURES_RECEIPT.pending.json').exists())
        self.assertEqual(json.loads((out/'FIGURES_RECEIPT.json').read_text())['N_accounted_requests'],0)

    def test_distribution_uses_request_threshold_and_keeps_fixed_denominator(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'))
        data['candidates']=[candidate(f,r,q,solved=(f==15)) for f in fig.FREQUENCIES for r in range(20) for q in range(10,21)]
        rendered=[]
        def capture(page,out,stem):
            rendered.append(stem)
            for ax in page.axes:
                for line in ax.lines:self.assertLessEqual(max(line.get_ydata(),default=0),1)
            fig.plot_style().close(page)
            return []
        with patch.object(fig,'export',side_effect=capture):
            _,availability=fig.distribution_plots(data,self.root)
        self.assertEqual(rendered,['f15_conditional_strict_valid_ecdf','f15_residual_attainment'])
        self.assertEqual(availability[10]['N_strict_valid_requests'],20)
        self.assertTrue(availability[10]['density'].startswith('NOT_AVAILABLE'))

    def test_ecdf_uses_valid_denominator_attainment_original_and_empty_selected(self):
        data=fig.load_snapshot(snapshot(self.root/'stats'))
        data['candidates']=[candidate(f,r,q,solved=(f==5 and r<5 and q!=14)) for f in fig.FREQUENCIES for r in range(20) for q in range(10,21)]
        captured={}
        def capture(page,out,stem):
            captured[stem]=[list(line.get_ydata()) for line in page.axes[0].lines]
            if stem.endswith('ecdf'):
                self.assertTrue(any('n=0; no curve' in t.get_text() for t in page.axes[0].texts))
                self.assertEqual(len(page.axes[0].lines),1)
            fig.plot_style().close(page);return []
        with patch.object(fig,'export',side_effect=capture):
            _,availability=fig.distribution_plots(data,self.root)
        self.assertEqual(captured['f05_conditional_strict_valid_ecdf'][0][-1],1)
        self.assertAlmostEqual(captured['f05_residual_attainment'][0][-1],50/220)
        self.assertEqual(availability[0]['CDF'],'RENDERED_CONDITIONAL_STRICT_VALID_ECDF')
        self.assertEqual(availability[0]['attainment'],'RENDERED_DESCRIPTIVE_ATTAINMENT')

    def test_score_only_repair_does_not_redraw_percent_graph(self):
        rows=[candidate(5,0,q,solved=True) for q in range(10,21)]
        captured=[]
        def capture(page,out,stem):
            captured.append(stem);fig.plot_style().close(page);return []
        with patch.object(fig,'export',side_effect=capture):
            fig.request_q_plots({'source_pin':{'sha256':'a'*64}},rows,{'q_emx':14},self.root,score_only=True)
        self.assertEqual(captured,['proxy_emx_score_by_q'])


if __name__=='__main__':unittest.main()
