"""Synthetic controlled64 completion chain, never a native execution.

The caller-context boundary is explicit: 64 handwritten, frozen proposals are
supplied as if already checked by load_context. Only opaque config/deck SHA
constants are replaced with synthetic-byte hashes. No chain/label check is
mocked. The GDS-shaped timestamp stream is not a manufacturable layout, and
the S4P bytes are only a hash-bearing placeholder, not physical measurements.
"""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import struct

from research.broadband56_nn import eucap15_controlled_evidence as evidence
from research.broadband56_nn.eucap15_acquisition_evidence import MirrorReader, GEOMETRY_CHECKS
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import gds_timestamp_normalized_sha256
from tests.fixtures.eucap15_selected_evidence_fixture import SyntheticEvidence, pin, sha
from tests.test_eucap15_acquisition_evidence import labels_fixture, csv_bytes
from tests.test_eucap15_controlled_results import frame, intent


RUNTIME_RELATIVES = (
    'rfic_transformer_inverse_design/api.py',
    'rfic_transformer_inverse_design/core/types.py',
    'rfic_transformer_inverse_design/core/defaults.py',
    'rfic_transformer_inverse_design/network_analysis.py',
    'rfic_transformer_inverse_design/paths.py',
    'rfic_transformer_inverse_design/execution/zeus_cadence.py',
    'rfic_transformer_inverse_design/sim/emx/simulation.py',
    'rfic_transformer_inverse_design/sim/emx/layout_export.py',
    'rfic_transformer_inverse_design/sim/touchstone.py',
    'rfic_transformer_inverse_design/sim/base.py',
    'rfic_transformer_inverse_design/analysis/extraction.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_gds_identity.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py',
)


class ControlledEvidence(SyntheticEvidence):
    """Reuse only old synthetic file helpers, not the old64/256 context."""

    def __init__(self, root, monkeypatch, *, source='SPARSE_TARGETED', actual=None,
                 below_half_srf=True, physics=True, mapped_sources=True):
        self.root = root.resolve()
        self.paths, self.json_order = {}, []
        self.native = self.root/'SYNTHETIC_NATIVE_NO_EXECUTION'
        self.model_id = 'SYNTHETIC_MODEL_NOT_FINAL'
        proposals = frame()
        for row in proposals:
            row.update(schema='eucap15_acquisition_candidate.v1',
                protocol_schema='eucap15_controlled_acquisition_intent.v1',
                geometry_units='um', no_replacement=True, no_q_fallback=True,
                reservation_status='NOT_RESERVED', native_status='NOT_SUBMITTED',
                actual_response=None, solver_start_order=None,
                parameter_geometry_hash=sha(('parameter:'+row['candidate_id']).encode()))
            row['qscan_source_candidate'] = row['candidate_id'] if row['source']=='SPARSE_TARGETED' else None
        self.original = deepcopy(next(r for r in proposals if r['source']==source))
        self.request_id = self.original['request_id']
        self.candidate_id = self.original['candidate_id']
        self.geometry_sha = self.original['canonical_geometry_sha256']
        self.candidate_sha = sha(self.candidate_id.encode())
        self.solver_root = self.native/self.request_id/'emx_selected'
        # The synthetic label helper's target exactly matches this chosen row.
        targeted = source=='SPARSE_TARGETED'
        label_proposal, feature, self.rows = labels_fixture(source=source,
            actual=actual, target=self.original['target'], half_srf=below_half_srf, physics=physics)
        if targeted:
            self.original['proxy'] = deepcopy(label_proposal['proxy'])
            from research.broadband56_nn.eucap15_acquisition import actual_landing
            self.original['predicted_cell'] = list(actual_landing([label_proposal['proxy'][i] for i in (0,1,3)]))
            proposals = [deepcopy(self.original) if r['candidate_id']==self.candidate_id else r for r in proposals]
        frozen = self.root/'FROZEN_RESEARCH_INPUTS'
        self.put('intent', frozen/'INTENT.json', intent())
        self.blob('proposals', frozen/'SELECTED_CANDIDATES.jsonl', b''.join(
            (json.dumps(r,sort_keys=True,allow_nan=False)+'\n').encode() for r in proposals))
        self.put('preparation', frozen/'PREPARATION_RECEIPT.json', dict(
            status='PREPARED_NOT_NATIVE_RELEASE', intent=self.p('intent'), N_total_proposals=64,
            N_logical_proxy_candidates=275, N_selected_q=25, source_bytes_unchanged=True))
        self.put('controlled_manifest', frozen/'MANIFEST.json', dict(
            schema='eucap15_controlled_acquisition_manifest.v1', status='PREPARATION_ONLY_NOT_NATIVE_RELEASE',
            intent=self.p('intent'), files={self.paths[k].name:self.p(k) for k in ('proposals','preparation')}))
        self.batch = dict(manifest=self.p('controlled_manifest'), intent=self.p('intent'),
            proposals=self.p('proposals'), preparation=self.p('preparation'), intent_value=self.read('intent'),
            rows={r['candidate_id']:r for r in proposals}, model_id=self.model_id)
        self.sources, self.path_map = {}, {}
        for name,key in [('manifest','controlled_manifest'),('intent','intent'),
                         ('proposals','proposals'),('preparation','preparation')]:
            original = self.p(key)
            if mapped_sources:
                self.blob('transport_'+name, self.native/'transport'/self.paths[key].name,
                    self.paths[key].read_bytes())
                resolved = self.p('transport_'+name)
                self.path_map[original['path']] = resolved['path']
            else:
                resolved = original
            self.sources[name] = resolved
        self.blob('config', self.native/'private'/'SYNTHETIC_CONFIG.yaml', b'SYNTHETIC CONFIG NOT A PDK\n')
        self.blob('deck', self.native/'private'/'SYNTHETIC_DECK.drc', b'SYNTHETIC NOT A FOUNDRY RULESET\n')
        runtime_repo=self.native/'SYNTHETIC_RUNTIME_NOT_IMPORTED'
        for i,relative in enumerate(RUNTIME_RELATIVES):
            self.blob(f'runtime_{i}',runtime_repo/relative,('# SYNTHETIC NEVER IMPORTED '+relative+'\n').encode())
        self.blob('emx_wrapper',self.native/'SYNTHETIC_WRAPPER_NEVER_EXECUTED',b'# SYNTHETIC NEVER EXECUTED\n')
        self.blob('process',self.native/'private'/'SYNTHETIC_PROCESS.proc',b'SYNTHETIC PROCESS NOT A PDK\n')
        self.runtime=dict(repo=str(runtime_repo),source_pins=[self.p(f'runtime_{i}') for i in range(len(RUNTIME_RELATIVES))],
            emx_wrapper=self.p('emx_wrapper'),process_file=self.p('process'))
        monkeypatch.setattr(evidence,'CONFIG_SHA',self.p('config')['sha256'])
        monkeypatch.setattr(evidence,'DECK_SHA',self.p('deck')['sha256'])
        self.source_context = dict(request_id=self.request_id, frequency_ghz=15,
            q_proxy=self.original['q_proxy'], model_id=self.model_id,
            model_used_for_proposal=targeted, candidate_model_id=self.original['model_id'],
            dataset_scope='DEVELOPMENT_CONTROLLED_ACQUISITION64', target_source=source,
            arm=self.original['arm'], arm_order=self.original['arm_order'],
            global_order=self.original['global_order'])
        common = dict(candidate_id=self.candidate_id, frequency_ghz=15,
            q_requested=self.original['q_proxy'], q_proxy=self.original['q_proxy'], q_emx=None,
            model_id=self.model_id, dataset_scope=self.source_context['dataset_scope'],
            model_used_for_proposal=targeted, candidate_model_id=self.original['model_id'],
            physical_selection='FROZEN_CONTROLLED_ACQUISITION_SINGLE', production_membership=False,
            controlled_manifest=self.sources['manifest'], controlled_intent=self.sources['intent'],
            original_controlled_manifest=self.batch['manifest'], original_controlled_intent=self.batch['intent'])
        def rec(kind, dtype=0, payload=b''):
            return struct.pack('>HBB',len(payload)+4,kind,dtype)+payload
        # Same minimal timestamp-normalizable GDS fixture construction as the
        # previous selected/FINAL tests; no real layout or native library call.
        gds = rec(0,2,b'\x02\x58')+rec(1,2,b'\x00\x01'*12)+rec(5,2,b'\x00\x02'*12)+rec(7)+rec(4)
        self.blob('gds', self.native/self.request_id/'cadence'/'synthetic.gds', gds)
        self.blob('ports', self.native/self.request_id/'cadence'/'ports.json', b'{"synthetic":true}\n')
        self.normalized = gds_timestamp_normalized_sha256(self.paths['gds'])
        self.put('geometry', self.native/self.request_id/'audit'/'GEOMETRY_AUDIT.json', dict(
            schema='independent_research_candidate_gds_geometry_audit.v1', overall_status='PASS',
            candidate_id=self.candidate_id, candidate_id_sha256=self.candidate_sha,
            candidate_geometry_identity_sha256=self.geometry_sha, gds_path=self.p('gds')['path'],
            gds_sha256=self.p('gds')['sha256'], gds_timestamp_normalized_sha256=self.normalized,
            checks={k:True for k in GEOMETRY_CHECKS}, original_artifacts=[self.p('gds'),self.p('ports')]))
        self.blob('report', self.native/self.request_id/'calibre'/'report.txt', b'SYNTHETIC NO CALIBRE EXECUTION\n')
        self.put('drc', self.native/self.request_id/'calibre'/'summary.json', dict(
            overall_status='PASS', blocking_drc_violation_count=0, drc_scope='foundry_macro_ip_back_end',
            candidate_id_sha256=self.candidate_sha, candidate_geometry_identity_sha256=self.geometry_sha,
            gds_path=self.p('gds')['path'], gds_sha256=self.p('gds')['sha256'],
            geometry_audit_sha256=self.p('geometry')['sha256'], gds_timestamp_normalized_sha256=self.normalized,
            process_token='/TSMC65_05_12_26/', gds_top_cell='TRANSFORMER',
            drc_report_path=self.p('report')['path'], drc_report_sha256=self.p('report')['sha256'],
            drc_source_rule_deck_path=self.p('deck')['path'], drc_source_rule_deck_sha256=self.p('deck')['sha256'],
            checks={k:True for k in (*GEOMETRY_CHECKS,'foundry_drc_pass',
                'no_blocking_drc_violations','calibre_result_accounting_complete')}))
        self.paths['index'] = self.native/self.request_id/'calibre'/'index.csv'
        self.index_rows = [dict(candidate_id_sha256=self.candidate_sha,
            drc_summary_path=self.p('drc')['path'], drc_summary_sha256=self.p('drc')['sha256'], status='PASS')]
        self.write_index()
        self.put('audit', self.native/self.request_id/'audit'/'REQUEST_GDS_AUDIT.json', dict(
            schema='eucap15_controlled_acquisition_gds_audit.v1', N_logical=1, N_audit_attempted=1,
            request=self.source_context, source_pins=dict(self.sources,private_config=self.p('config')),
            records=[dict(candidate_id=self.candidate_id,status='PASS',candidate_id_sha256=self.candidate_sha,
                candidate_geometry_identity_sha256=self.geometry_sha,gds=self.p('gds'),
                port_manifest=self.p('ports'),geometry_audit=self.p('geometry'))]))
        self.put('request', self.native/self.request_id/'EMX_REQUEST.json', dict(
            **self.source_context, schema='eucap15_controlled_acquisition_emx_request.v1',
            candidate_id=self.candidate_id, q_requested=self.original['q_proxy'],
            controlled_manifest=self.batch['manifest'],path_map=self.path_map,production_campaign_membership=False,
            gds_audit=self.p('audit'),calibre_index=self.p('index'),private_config=self.p('config'),runtime=self.runtime))
        self.command = [self.p('emx_wrapper')['path'], self.p('gds')['path'],'TRANSFORMER',self.p('process')['path'],
            '--touchstone','-s',str(self.solver_root/'solve'/'synthetic.s4p'),'--include-command-line',
            '--sweep','5000000000','60000000000','--sweep-stepsize','1000000000',
            '--s-impedance=50','--cadence-pins=51','--parallel=2','--simultaneous-frequencies=0',
            *[f'--port={p}={p}:{p}_G' for p in ('P001','P002','P003','P004')]]
        self.blob('s4p', self.solver_root/'solve'/'synthetic.s4p', b'! SYNTHETIC HASH PLACEHOLDER NOT EMX\n')
        self.blob('log', self.solver_root/'solve'/'emx.stderr.log', b'SYNTHETIC NO TOOL EXECUTED\n')
        self.put('command', self.solver_root/'solve'/'emx_command.json', self.command)
        self.record = dict(self.original,grid_geometry=self.original['geometry'],grid_proxy=self.original['proxy'],
            analytic_grid=self.original['analytic_pass'],candidate_id_sha256=self.candidate_sha,
            candidate_geometry_identity_sha256=self.geometry_sha)
        statuses = [dict(candidate_id=self.candidate_id,status='PASS')]
        self.put('proof', self.solver_root/'PREFLIGHT.json', dict(**common,
            schema='eucap15_controlled_acquisition_emx_preflight.v1',status='PASS',request_id=self.request_id,
            candidate_id_sha256=self.candidate_sha,geometry_sha256=self.geometry_sha,
            original_record=self.record,original_proposal=self.original,original_request_denominator=64,
            protocol=dict(score_scale=[2.5,2.5,20.,.8],absolute_tolerances=[.125,.125,1.,.04]),
            frequency_grid_hz=[f*10**9 for f in range(5,61)],port_order=['P001','P002','P003','P004'],
            port_permutation=[0,1,3,2],config_differential_port_pairs=[[0,1],[2,3]],reference_ohm=50,
            output=str(self.solver_root),full11_physical_optimum='NOT_EVALUATED_NO_Q_REPLACEMENT',
            request=self.p('request'),gds=self.p('gds'),port_manifest=self.p('ports'),calibre=self.p('drc'),
            original_candidate_statuses=statuses,command=self.command,
            source_pins=[*self.sources.values(),*self.runtime['source_pins'],self.p('emx_wrapper'),self.p('process'),*[self.p(k) for k in
                ('request','audit','index','config','geometry','gds','ports','drc','deck','report')]]))
        self.put('solver', self.solver_root/'SOLVER_RECEIPT.json', dict(
            schema='frequency_research_fresh_solver.v1',status='PASS',candidate_id=self.candidate_id,
            preflight=self.p('proof'),source_gds_before=self.p('gds'),source_gds_after=self.p('gds'),
            real_emx=True,production_modified=False,q_emx=None,touchstone=self.p('s4p'),
            artifacts=[self.p(k) for k in ('s4p','log','command')]))
        self.paths['csv'] = self.solver_root/'features'/'features_56.csv'
        self.blob('csv',self.paths['csv'],csv_bytes(self.rows))
        feature.update(**common,schema='eucap15_controlled_acquisition_fresh_features.v1',
            status='PASS_EXTRACTION',original_proposal=self.original,original_proposal_denominator=64,
            preflight=self.p('proof'),solver_receipt=self.p('solver'),score_scale=[2.5,2.5,20.,.8],
            absolute_hit_tolerances=[.125,.125,1.,.04],original_candidate_statuses=statuses,
            q_optimum_status='NOT_EVALUATED_NO_Q_REPLACEMENT')
        self.put('feature',self.solver_root/'features'/'FEATURE_RECEIPT.json',feature)
        self.put('manifest',self.solver_root/'features'/'MANIFEST.json',
            dict(inputs_unchanged=True,artifacts=[self.p('feature'),self.p('csv')]))
        self.order = ['geometry','drc','index','audit','request','command','proof','solver','csv','feature','manifest']

    def write_index(self):
        self.blob('index',self.paths['index'],csv_bytes(self.index_rows))

    def repin_after(self,key):
        def replace(value):
            if isinstance(value,dict):
                if {'path','sha256','bytes'} <= value.keys():
                    return dict(value,**pin(value['path']))
                return {k:replace(v) for k,v in value.items()}
            if isinstance(value,list): return [replace(v) for v in value]
            return value
        start=self.order.index(key)+1 if key in self.order else 0
        for name in self.order[start:]:
            if name=='index':
                for row in self.index_rows:
                    row['drc_summary_sha256']=pin(row['drc_summary_path'])['sha256']
                self.write_index()
            elif name in self.json_order:
                value=replace(self.read(name))
                if name=='drc' and key=='geometry':value['geometry_audit_sha256']=self.p('geometry')['sha256']
                self.put(name,self.paths[name],value)

    def mirror(self):
        mapping={}
        for i,p in enumerate(self.paths.values()):
            dst=self.root/'MIRROR_ONLY'/f'{i:03d}'/p.name
            dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(p.read_bytes())
            mapping[str(p)]=str(dst)
        return MirrorReader(mapping)

    def entry(self):
        return dict(candidate_id=self.candidate_id,geometry_sha256=self.geometry_sha,
            arm=self.original['arm'],feature=self.p('feature'),solver=self.p('solver'),s4p=self.p('s4p'))

    def inspect(self,reader=None,entry=None,batch=None):
        return evidence.inspect_feature_chain(reader or self.mirror(),entry or self.entry(),batch or self.batch)
