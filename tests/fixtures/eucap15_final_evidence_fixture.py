"""Handwritten FINAL-shaped evidence; every byte is SYNTHETIC, never EMX.

Reuses frozen-context and saved-label fixture logic, not their tests or any
model/native code. The tiny GDS stream tests timestamp hashing, not geometry.
"""
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import struct

from research.broadband56_nn import eucap15_final_context as context_reader
from research.broadband56_nn import eucap15_final_evidence as evidence
from research.broadband56_nn.eucap15_final_binding import candidate_binding
from research.broadband56_nn.eucap15_development128_results import Mirror
from research.broadband56_nn.frequency_research_emx import GEOMETRY_CHECKS
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import GEOMETRY_FIELDS
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import gds_timestamp_normalized_sha256
from tests import test_eucap15_final_context as context_fixture
from tests import test_eucap15_final_routing as routing_fixture
from tests.fixtures.eucap15_selected_evidence_fixture import SyntheticEvidence, pin, sha, cleaned


class FinalEvidence(SyntheticEvidence):
    def __init__(self, tmp_path, monkeypatch, *, order=0, q=14, actual=None,
                 below_half_srf=True, physics=True, analytic=True):
        fields = list(GEOMETRY_FIELDS)
        monkeypatch.setattr(context_fixture, 'FIELDS', fields)
        monkeypatch.setattr(routing_fixture, 'FIELDS', fields)
        frozen = context_fixture.Fixture(tmp_path, monkeypatch)
        if not analytic:
            frozen.records[order][q-10]['analytic_grid'] = False
            frozen.route(order); frozen.publish()
        self.ctx = context_reader.load_context(frozen.frame_path)
        self.frozen = frozen
        self.root = tmp_path
        self.paths, self.json_order = {}, []
        self.request_id = frozen.requests[order]['request_id']
        self.candidate_id = f'{self.request_id}-q{q:02d}'
        self.binding = candidate_binding(self.ctx, self.candidate_id)
        b = self.binding
        self.native = tmp_path/'original_native'/self.request_id/self.candidate_id
        self.solver_root = self.native/'emx_selected'
        self.wanted, self.proxy = b['target'], b['grid_proxy']
        self.scale, self.tau = b['score_scale'], b['absolute_tolerances']
        self.geometry_sha = b['candidate_geometry_identity_sha256']
        self.candidate_sha = sha(self.candidate_id.encode())
        self.put('binding', self.native/'FINAL_BINDING.json', b)
        self.blob('config', tmp_path/'SYNTHETIC_private_config.yaml', b'SYNTHETIC_CONFIG_NOT_A_PDK\n')
        self.expected_config = self.p('config')
        def rec(kind, dtype=0, payload=b''):
            return struct.pack('>HBB', len(payload)+4, kind, dtype)+payload
        gds = (rec(0, 2, b'\x02\x58')+rec(1, 2, b'\x00\x01'*12)
               +rec(5, 2, b'\x00\x02'*12)+rec(7)+rec(4))
        self.blob('gds', self.native/'streamout'/'synthetic.gds', gds)
        self.blob('ports', self.native/'layout'/'ports.json', b'{"synthetic":true}\n')
        normalized = gds_timestamp_normalized_sha256(self.paths['gds'])
        self.put('geometry', self.native/'gds_audit'/'GEOMETRY_AUDIT.json', dict(
            schema='independent_research_candidate_gds_geometry_audit.v1', candidate_id=self.candidate_id,
            overall_status='PASS', candidate_id_sha256=self.candidate_sha,
            candidate_geometry_identity_sha256=self.geometry_sha,
            gds_path=self.p('gds')['path'], gds_sha256=self.p('gds')['sha256'],
            gds_timestamp_normalized_sha256=normalized,
            process_token='/TSMC65_05_12_26/', original_artifacts_unchanged=True,
            production_campaign_membership=False, evidence=[self.p('ports')],
            original_artifacts=[self.p('gds'), self.p('ports')],
            checks={key: True for key in GEOMETRY_CHECKS}))
        self.blob('deck', tmp_path/'SYNTHETIC_foundry_deck', b'SYNTHETIC DECK NOT A FOUNDRY RULESET\n')
        # Sole fixture boundary: a fake deck cannot have the real foundry SHA.
        monkeypatch.setattr(evidence, 'FOUNDRY_DECK_SHA256', self.p('deck')['sha256'], raising=False)
        self.blob('report', self.native/'calibre'/'drc_report.txt', b'SYNTHETIC NOT A CALIBRE EXECUTION\n')
        self.put('drc', self.native/'calibre'/'drc_summary.json', dict(
            overall_status='PASS', blocking_drc_violation_count=0,
            drc_scope='foundry_macro_ip_back_end', candidate_id_sha256=self.candidate_sha,
            candidate_geometry_identity_sha256=self.geometry_sha,
            gds_path=self.p('gds')['path'], gds_sha256=self.p('gds')['sha256'],
            geometry_audit_sha256=self.p('geometry')['sha256'],
            gds_timestamp_normalized_sha256=normalized, process_token='/TSMC65_05_12_26/',
            gds_top_cell='TRANSFORMER', drc_report_path=self.p('report')['path'],
            drc_report_sha256=self.p('report')['sha256'],
            drc_source_rule_deck_path=self.p('deck')['path'],
            drc_source_rule_deck_sha256=self.p('deck')['sha256'],
            checks={key: True for key in (*GEOMETRY_CHECKS, 'foundry_drc_pass',
                'no_blocking_drc_violations', 'calibre_result_accounting_complete')}))
        self.paths['index'] = self.native/'calibre'/'drc_index.csv'
        self.index_rows = [dict(candidate_id_sha256=self.candidate_sha,
            drc_summary_path=self.p('drc')['path'], drc_summary_sha256=self.p('drc')['sha256'], status='PASS')]
        self.write_index()
        self.put('audit', self.native/'gds_audit'/'REQUEST_GDS_AUDIT.json', dict(
            schema='eucap15_final_candidate_gds_audit.v1', final_binding=self.p('binding'),
            physical_selection='FROZEN_MAIN_AUDIT_UNION', candidate_id=self.candidate_id,
            N_audit_attempted=1, N_audit_pass=1,
            source_pins={**b['source_pins'], 'final_binding':self.p('binding'), 'private_config':self.p('config')},
            records=[dict(status='PASS', candidate_id=self.candidate_id,
                candidate_id_sha256=self.candidate_sha, candidate_geometry_identity_sha256=self.geometry_sha,
                gds=self.p('gds'), port_manifest=self.p('ports'), geometry_audit=self.p('geometry'))]))
        common = dict(final_binding=self.p('binding'), candidate_id=self.candidate_id,
            model_id=b['model_id'], dataset_scope='FINAL_FROZEN_DATASET', frequency_ghz=15,
            q_requested=q, q_proxy=b['q_proxy'], q_emx=None,
            physical_selection='FROZEN_MAIN_AUDIT_UNION', memberships=b['memberships'],
            original_request_denominator=b['original_request_denominator'])
        self.put('request', self.native/'EMX_REQUEST.json', dict(
            **{k:v for k,v in common.items() if k!='q_emx'},
            schema='eucap15_final_emx_request.v1', request_id=self.request_id,
            target_source=b['original_record']['target_source'], production_campaign_membership=False,
            gds_audit=self.p('audit'), calibre_index=self.p('index'), private_config=self.p('config')))
        self.command = ['SYNTHETIC_EMX_NEVER_EXECUTED', '--gds', self.p('gds')['path']]
        self.blob('s4p', self.solver_root/'solve'/'synthetic.s4p', b'! SYNTHETIC ONLY, NOT SIMULATED\n')
        self.blob('log', self.solver_root/'solve'/'solver.log', b'NO NATIVE TOOL EXECUTED\n')
        self.put('command', self.solver_root/'solve'/'emx_command.json', self.command)
        self.put('proof', self.solver_root/'PREFLIGHT.json', dict(**common,
            schema='eucap15_final_emx_preflight.v1', status='PASS', request_id=self.request_id,
            candidate_id_sha256=self.candidate_sha, geometry_sha256=self.geometry_sha,
            original_record=b['original_record'], executed_hit_tolerances=self.tau,
            protocol=dict(q_values=list(range(10,21)), q_scalar='min(Qp,Qs)',
                          score_scale=self.scale, absolute_tolerances=self.tau),
            frequency_grid_hz=list(evidence.FREQUENCIES), port_order=['P001','P002','P003','P004'],
            port_permutation=[0,1,3,2], reference_ohm=50,
            full11_physical_optimum='NOT_EVALUATED_BY_NATIVE_OWNER', production_membership=False,
            output=str(self.solver_root), request=self.p('request'), gds=self.p('gds'),
            port_manifest=self.p('ports'), calibre=self.p('drc'), command=self.command,
            source_pins=[*b['source_pins'].values(), *[self.p(k) for k in
                ('binding','request','audit','geometry','index','config','gds','ports','drc','deck','report')]]))
        self.put('solver', self.solver_root/'SOLVER_RECEIPT.json', dict(
            schema='frequency_research_fresh_solver.v1', status='PASS', candidate_id=self.candidate_id,
            preflight=self.p('proof'), source_gds_before=self.p('gds'), source_gds_after=self.p('gds'),
            real_emx=True, production_modified=False, q_emx=None, touchstone=self.p('s4p'),
            artifacts=[self.p(k) for k in ('s4p','log','command')]))
        # Reuse the old fixture's original56 row/CSV logic, not its evidence reader.
        numerical = SyntheticEvidence(tmp_path/'SYNTHETIC_LABEL_FIXTURE',
            actual=self.wanted if actual is None else actual,
            below_half_srf=below_half_srf, physics=physics)
        self.rows = deepcopy(numerical.rows)
        self.paths['csv'] = self.solver_root/'features'/'features_56.csv'
        self.write_csv(self.rows)
        feature = numerical.read('feature')
        actual_raw = [self.rows[10][k] for k in ('lp_nh','ls_nh','qmin','k_abs')]
        errors = [a-t for a,t in zip(actual_raw,self.wanted)]
        finite = all(math.isfinite(a) for a in actual_raw)
        hits = [math.isfinite(e) and abs(e)<=t for e,t in zip(errors,self.tau)]
        valid = finite and below_half_srf and physics
        feature.update(**common, schema='eucap15_final_fresh_features.v1',
            target=self.wanted, proxy_self=self.proxy, score_scale=self.scale,
            absolute_hit_tolerances=self.tau, q_optimum_status='NOT_EVALUATED_BY_NATIVE_OWNER',
            preflight=self.p('proof'), solver_receipt=self.p('solver'),
            emx_minus_target=cleaned(errors), emx_minus_proxy=cleaned([a-p for a,p in zip(actual_raw,self.proxy)]),
            normalized_response_score=math.sqrt(sum((e/s)**2 for e,s in zip(errors,self.scale))/4) if finite else None,
            within_tolerance=hits, joint_response_hit=all(hits), strict_joint_hit=bool(all(hits) and valid),
            target_relative_signed_percent=cleaned([100*e/t for e,t in zip(errors,self.wanted)]),
            target_relative_absolute_percent=cleaned([100*abs(e)/t for e,t in zip(errors,self.wanted)]))
        self.put('feature', self.solver_root/'features'/'FEATURE_RECEIPT.json', feature)
        self.put('manifest', self.solver_root/'features'/'MANIFEST.json', dict(
            schema='eucap15_final_feature_manifest.v1', final_binding=self.p('binding'),
            artifacts=[self.p('feature'),self.p('csv')], inputs_unchanged=True))
        self.order = ['binding','config','gds','ports','geometry','deck','report','drc','index',
            'audit','request','s4p','log','command','proof','solver','csv','feature','manifest']

    def write_index(self):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=list(self.index_rows[0]), lineterminator='\n')
        writer.writeheader(); writer.writerows(self.index_rows)
        self.blob('index', self.paths['index'], stream.getvalue().encode())

    def repin_after(self, key):
        def replace(value):
            if isinstance(value, dict):
                if {'path','sha256','bytes'} <= value.keys(): return pin(value['path'])
                return {k:replace(v) for k,v in value.items()}
            if isinstance(value,list): return [replace(v) for v in value]
            return value
        for name in self.order[self.order.index(key)+1:]:
            if name == 'index':
                for row in self.index_rows:
                    row['drc_summary_sha256'] = pin(row['drc_summary_path'])['sha256']
                self.write_index()
            elif name in self.json_order:
                value = replace(self.read(name))
                if name == 'drc' and key in ('geometry','gds'):
                    value['geometry_audit_sha256'] = self.p('geometry')['sha256']
                self.put(name, self.paths[name], value)

    def mirror(self):
        entries = []
        sources = {p['path']:p for p in self.binding['source_pins'].values()}
        sources.update({str(p):pin(p) for p in self.paths.values()})
        for index, original in enumerate(sources.values()):
            dest = self.root/'MIRROR_ONLY'/f'{index:03d}'/Path(original['path']).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(Path(original['path']).read_bytes())
            entries.append(dict(original=original, resolved=pin(dest)))
        return Mirror(entries)

    def inspect(self, mirror=None):
        return evidence.inspect_features(self.ctx, self.candidate_id, self.p('manifest'),
            mirror or self.mirror(), expected_private_config=self.expected_config)
