"""Member-only qualification delta; no main, scheduling, native or ledger writes.

Derived from history1000.py SHA256
c2fa7380183dae8d58a95b6244134248619c1b24aeec821e511a6b17b0c43ea8.
The existing owner feeds its explicit next block; this module cannot replay 1..1000.
Required gate keys are supplied in the owner's pinned contract, never inferred
from a candidate's own possibly empty check dictionary.
"""
import hashlib
import json
import math
from pathlib import Path

from geometry_helpers import canonical_geometry_sha256, _production_geometry_fingerprint

FP = 'f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e'
CLASSES = ('qualified', 'duplicate', 'out_of_range', 'physical_invalid', 'missing_evidence', 'incompatible')


class Disposition(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class SourceIdentityError(RuntimeError):
    """Fatal shared-source NO-GO; intentionally outside per-member handlers."""


def need(ok, message, status='incompatible'):
    if not ok:
        raise Disposition(status, message)


def pin(path):
    p = Path(path)
    data = p.read_bytes()
    return dict(path=str(p), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def read(identity):
    p = Path(identity['path'])
    need(not any(q.is_symlink() for q in (p, *p.parents)), 'source symlink')
    before = p.stat()
    data = p.read_bytes()
    after = p.stat()
    need((before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns), 'source changed during read')
    need(hashlib.sha256(data).hexdigest() == identity['sha256'] and len(data) == identity.get('bytes', identity.get('size_bytes')), 'source SHA/size drift: ' + str(p))
    return data


def make_shared_verifier():
    """Owner retains cache for the block/source and rechecks pins at closure."""
    shared_pins = {}

    def shared(p):
        try:
            prior = shared_pins.get(p['path'])
            if prior is not None and prior['sha256'] != p['sha256']:
                raise SourceIdentityError('shared source identity conflict: ' + p['path'])
            if prior is None:
                read(p)
                shared_pins[p['path']] = p
        except (Disposition, OSError, KeyError, TypeError, ValueError) as exc:
            raise SourceIdentityError('shared source NO-GO: ' + str(exc)) from exc

    return shared, shared_pins


def identities(geometry, fields):
    import numpy as np
    return dict(canonical_9dp=canonical_geometry_sha256(dict(zip(fields, geometry))),
                production_1e6=_production_geometry_fingerprint(geometry),
                nominal_grid_5nm=canonical_geometry_sha256(dict(zip(fields, np.rint(np.asarray(geometry) / .005) * .005))))


def label_state(row):
    values = {k: float(row[k]) for k in ('lp_nh', 'ls_nh', 'qp', 'qs', 'qmin', 'signed_k', 'k_abs')}
    need(all(math.isfinite(x) for x in values.values()), 'nonfinite physical values', 'physical_invalid')
    need(values['qmin'] == min(values['qp'], values['qs']) and values['k_abs'] == abs(values['signed_k']), 'Q/K definition differs')
    need(all(row[k].lower() == 'true' for k in ('strict_lumped_valid', 'below_half_srf', 'broadband_descriptor_valid')), 'saved strict15/SRF/descriptor predicate failed', 'physical_invalid')
    need(.5 <= values['lp_nh'] <= 2 and .5 <= values['ls_nh'] <= 2 and .2 <= values['k_abs'] <= .85, 'outside current15 core range', 'out_of_range')
    return values


def validate_gate_contract(contract):
    """Run once before the owner enters its existing member loop."""
    try:
        gates = contract['qualification_required_checks_v2']
        for name in ('emx_output', 'actual_gds'):
            keys = gates[name]
            if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not k for k in keys) or len(keys) != len(set(keys)):
                raise SourceIdentityError('empty/invalid pinned required gate keys: ' + name)
    except (KeyError, TypeError) as exc:
        raise SourceIdentityError('missing pinned required gate contract: ' + str(exc)) from exc
    return gates


def require_checks(checks, required, label, false_status='incompatible', all_values=False):
    if not isinstance(required, list) or not required or any(not isinstance(k, str) or not k for k in required):
        raise SourceIdentityError(label + ': missing nonempty pinned required-key set')
    need(isinstance(checks, dict), label + ': checks must be a dictionary')
    missing = sorted(set(required) - set(checks))
    need(not missing, label + ': missing required checks: ' + ','.join(missing), 'missing_evidence')
    need(all(checks[k] is True for k in required), label + ': required predicate failed', false_status)
    if all_values:
        need(all(v is True for v in checks.values()), label + ': extra predicate failed', false_status)


def verify_member(a, index, label, contract, checked, shared, stage=None):
    stage = {} if stage is None else stage

    def at(name):
        stage['name'] = name

    def check(p):
        data = read(p)
        checked[p['path']] = p
        return data

    def doc(p):
        stage['artifact_path'] = p['path']
        value = json.loads(check(p))
        need(isinstance(value, dict), 'expected JSON object: ' + p['path'])
        return value

    gates = validate_gate_contract(contract)
    at('member_geometry_identity')
    gid = a['geometry_sha256']
    fields = contract['geometry_order']
    need(a['campaign_contract_fingerprint'] == index['campaign_contract_fingerprint'] == FP, 'member contract')
    need(gid == a['geometry_id'] == index['geometry_id'] == index['geometry_sha256'] == label['geometry_sha256'], 'member identity')
    need(a['accepted_sequence'] == label['accepted_sequence'], 'source accepted sequence')
    geometry = [float(a['geom__' + f]) for f in fields]
    need(geometry == [float(label['geom__' + f]) for f in fields], 'geometry/label mismatch')
    ids = identities(geometry, fields)
    need(ids['canonical_9dp'] == gid, 'canonical source identity')
    need(all(lo <= v <= hi for v, lo, hi in zip(geometry, contract['geometry_bounds']['lower'], contract['geometry_bounds']['upper'])), 'geometry bounds')
    need(all(a[k] == 'PASS' for k in ('duplicate_status', 'geometry_bounds_status', 'analytical_status', 'topology_status', 'cadence_gds_status', 'calibre_status', 'emx_status', 's4p_status', 's_to_z_status', 'feature_extraction_status')) and a['calibre_blocking_violations'] == '0', 'source acceptance predicates')
    at('s4p_identity')
    s4p = dict(path=index['s4p_path'], sha256=index['s4p_sha256'], bytes=int(index['s4p_size_bytes']))
    need(s4p['path'] == label['s4p_path'] and s4p['sha256'] == label['s4p_sha256'], 'S4P label binding')
    check(s4p)
    at('emx_receipt_json')
    receipt_pin = pin(Path(s4p['path']).parents[1] / 'EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json')
    r = doc(receipt_pin)
    need(r['overall_status'] == 'PASS' and r['fresh_real_emx_executed'] is True and r['proxy_or_historical_label_used'] is False and r['source_pins_unchanged_after_emx'] is True, 'fresh source receipt failed', 'physical_invalid')
    need(r['contract_fingerprint_sha256'] == FP and r['geometry_identity_sha256'] == r['candidate_id_sha256'] == gid, 'receipt geometry/contract')
    at('configuration_binding')
    expected_config = next((p for p in contract['pins'] if p['path'] == r['private_configuration']['path']), None)
    need(expected_config is not None, 'configuration path absent from pinned source contract')
    if r['private_configuration']['sha256'] != expected_config['sha256']:
        raise SourceIdentityError('configuration shared identity mismatch: ' + expected_config['path'])
    shared(r['private_configuration'])
    at('emx_output_required_checks')
    out = r['emx_output']
    require_checks(out['checks'], gates['emx_output'], 'EMX output', all_values=True)
    need(r['frequency_contract']['exact_hz'] == contract['full_frequency_hz'] and out['num_ports'] == 4 and out['num_frequency_points'] == 56, 'original full frequency contract')
    need((out['touchstone_path'], out['touchstone_sha256'], out['touchstone_size_bytes']) == (s4p['path'], s4p['sha256'], s4p['bytes']), 'receipt S4P mismatch')
    need(r['manifest_contract']['port_order'] == ['P001', 'P002', 'P003', 'P004'] and r['manifest_contract']['cadence_pin_purpose'] == 51 and r['top_cell'] == 'TRANSFORMER', 'ports or top cell')
    at('actual_gds_files')
    gds = r['source_exact_gds']
    check(gds)
    check(r['source_layout_manifest'])
    check(r['source_calibre_report'])
    at('calibre_receipt_json')
    drc = doc(r['source_calibre_zero_blocking_receipt'])
    need(drc['overall_status'] == 'PASS' and drc['calibre_executed'] is True and drc['calibre_blocking_violations'] == 0 and drc['source_files_unchanged'] is True, 'Calibre not zero blocking', 'physical_invalid')
    need(drc['geometry_identity_sha256'] == drc['candidate_id_sha256'] == gid and drc['gds_path'] == gds['path'] and drc['gds_sha256'] == gds['sha256'] and drc['contract_fingerprint_sha256'] == FP, 'DRC geometry/GDS binding')
    check(drc['source_calibre_summary'])
    at('actual_gds_audit_json')
    audit = doc(drc['source_geometry_audit'])
    at('actual_gds_required_checks')
    declared = audit['effective_required_geometry_checks']
    need(isinstance(declared, list) and declared and len(declared) == len(set(declared)), 'actual GDS required-key declaration empty or malformed', 'missing_evidence')
    need(set(declared) == set(gates['actual_gds']), 'actual GDS required-key declaration differs from pinned source contract')
    require_checks(audit['checks'], gates['actual_gds'], 'actual GDS audit', false_status='physical_invalid')
    need(audit['overall_status'] == 'PASS' and audit['candidate_geometry_identity_sha256'] == gid and audit['gds_sha256'] == gds['sha256'] and audit['gds_path'] == gds['path'], 'actual layout audit failed', 'physical_invalid')
    at('actual_gds_source_evidence')
    for p in audit['source_evidence'].values():
        if p['path'].endswith('.proc'):
            shared(p)
        else:
            check(p)
    at('emx_command_identity')
    check(dict(path=out['emx_command_path'], sha256=out['emx_command_sha256'], bytes=out['emx_command_size_bytes']))
    at('physical15_label_values')
    values = label_state(label)
    return dict(geometry=geometry, geometry_fields=fields, identities=ids, physical15=values, q10_to20_supported=10 <= values['qmin'] <= 20, s4p=s4p, gds=gds, source_receipt=receipt_pin, source_calibre=r['source_calibre_zero_blocking_receipt'])


def classify_member(a, index, label, contract, known, shared):
    """Return one terminal row; shared-source NO-GO propagates to the owner."""
    checked, stage = {}, {'name': 'source_row_identity'}
    result = dict(source_old_accepted_sequence=a.get('accepted_sequence'), geometry_sha256=a.get('geometry_sha256'), qualification_sequence=None, qualified_ledger_committed=False, old_broadband_recounted=False)
    try:
        result['source_old_accepted_sequence'] = int(a['accepted_sequence'])
        details = verify_member(a, index, label, contract, checked, shared, stage)
        matches = {k: known[k][identity] for k, identity in details['identities'].items() if identity in known[k]}
        result.update(details)
        if matches:
            result.update(status='duplicate', reason='already in certified union or earlier qualified row', matches=matches)
        else:
            result.update(status='qualified', reason='current bytes and source-chain qualification; publication pending')
            for k, identity in details['identities'].items():
                known[k][identity] = dict(namespace='this_historical_audit', source_old_accepted_sequence=result['source_old_accepted_sequence'])
    except Disposition as exc:
        result.update(status=exc.status, reason=str(exc))
    except (FileNotFoundError, PermissionError) as exc:
        result.update(status='missing_evidence', reason=f'{type(exc).__name__}: {exc}')
    except KeyError as exc:
        result.update(status='missing_evidence', reason='required source field missing: ' + str(exc))
    except json.JSONDecodeError as exc:
        result.update(status='incompatible', reason=f'malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}')
    except StopIteration:
        result.update(status='missing_evidence', reason='required source lookup produced no matching item')
    except (ValueError, TypeError) as exc:
        result.update(status='incompatible', reason=f'malformed value/shape: {type(exc).__name__}: {exc}')
    result['evidence_stage'] = stage['name']
    if 'artifact_path' in stage:
        result['last_parsed_artifact_path'] = stage['artifact_path']
    result['checked_source_pins'] = list(checked.values())
    return result


if __name__ == '__main__':
    raise SystemExit('LIBRARY_ONLY: use the existing owner next-block loop; no dispatch or replay is installed by this candidate.')
