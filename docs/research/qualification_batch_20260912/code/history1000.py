"""Bounded historical chain audit; no native calls and no accepted-ledger writes."""
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import math
from pathlib import Path

from geometry_helpers import canonical_geometry_sha256, _production_geometry_fingerprint

FP='f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e'
CLASSES=('qualified','duplicate','out_of_range','physical_invalid','missing_evidence','incompatible')

class Disposition(ValueError):
    def __init__(self, status, message):
        self.status=status
        super().__init__(message)

def need(ok, message, status='incompatible'):
    if not ok:raise Disposition(status,message)

def pin(path):
    p=Path(path);data=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))

def read(identity):
    p=Path(identity['path'])
    need(not any(q.is_symlink() for q in (p,*p.parents)), 'source symlink')
    before=p.stat();data=p.read_bytes();after=p.stat()
    need((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),
         'source changed during read')
    need(hashlib.sha256(data).hexdigest()==identity['sha256'] and
         len(data)==identity.get('bytes',identity.get('size_bytes')), 'source SHA/size drift: '+str(p))
    return data

def save(path, value):
    with Path(path).open('x') as f:
        json.dump(value,f,indent=2,allow_nan=False);f.write('\n')

def identities(geometry, fields):
    # The grid identity is a duplicate alarm, not a geometry rewrite.
    import numpy as np
    return dict(canonical_9dp=canonical_geometry_sha256(dict(zip(fields,geometry))),
                production_1e6=_production_geometry_fingerprint(geometry),
                nominal_grid_5nm=canonical_geometry_sha256(dict(zip(fields,np.rint(np.asarray(geometry)/.005)*.005))))

def label_state(row):
    values={k:float(row[k]) for k in ('lp_nh','ls_nh','qp','qs','qmin','signed_k','k_abs')}
    need(all(math.isfinite(x) for x in values.values()), 'nonfinite physical values','physical_invalid')
    need(values['qmin']==min(values['qp'],values['qs']) and values['k_abs']==abs(values['signed_k']),
         'Q/K definition differs')
    need(all(row[k].lower()=='true' for k in ('strict_lumped_valid','below_half_srf','broadband_descriptor_valid')),
         'saved strict15/SRF/descriptor predicate failed','physical_invalid')
    need(.5<=values['lp_nh']<=2 and .5<=values['ls_nh']<=2 and .2<=values['k_abs']<=.85,
         'outside current15 core range','out_of_range')
    return values

def verify_member(a, index, label, contract, checked, shared):
    gid=a['geometry_sha256'];fields=contract['geometry_order']
    def check(p):
        data=read(p);checked[p['path']]=p;return data
    def doc(p):return json.loads(check(p))
    need(a['campaign_contract_fingerprint']==index['campaign_contract_fingerprint']==FP,'member contract')
    need(gid==a['geometry_id']==index['geometry_id']==index['geometry_sha256']==label['geometry_sha256'], 'member identity')
    need(a['accepted_sequence']==label['accepted_sequence'], 'source accepted sequence')
    geometry=[float(a['geom__'+f]) for f in fields]
    need(geometry==[float(label['geom__'+f]) for f in fields], 'geometry/label mismatch')
    ids=identities(geometry,fields)
    need(ids['canonical_9dp']==gid, 'canonical source identity')
    need(all(lo<=v<=hi for v,lo,hi in zip(geometry,contract['geometry_bounds']['lower'],contract['geometry_bounds']['upper'])), 'geometry bounds')
    need(all(a[k]=='PASS' for k in ('duplicate_status','geometry_bounds_status','analytical_status','topology_status',
         'cadence_gds_status','calibre_status','emx_status','s4p_status','s_to_z_status','feature_extraction_status')) and
         a['calibre_blocking_violations']=='0', 'source acceptance predicates')
    s4p=dict(path=index['s4p_path'],sha256=index['s4p_sha256'],bytes=int(index['s4p_size_bytes']))
    need(s4p['path']==label['s4p_path'] and s4p['sha256']==label['s4p_sha256'],'S4P label binding')
    check(s4p)
    path=Path(s4p['path']).parents[1]/'EXACT_AUDITED_GDS_FRESH_EMX_RECEIPT.json'
    receipt_pin=pin(path);r=doc(receipt_pin)
    need(r['overall_status']=='PASS' and r['fresh_real_emx_executed'] is True and
         r['proxy_or_historical_label_used'] is False and r['source_pins_unchanged_after_emx'] is True,
         'fresh source receipt failed','physical_invalid')
    need(r['contract_fingerprint_sha256']==FP and r['geometry_identity_sha256']==r['candidate_id_sha256']==gid,
         'receipt geometry/contract')
    expected_config=next(p for p in contract['pins'] if p['path']==r['private_configuration']['path'])
    need(r['private_configuration']['sha256']==expected_config['sha256'],'config mismatch')
    shared(r['private_configuration'])
    out=r['emx_output']
    need(r['frequency_contract']['exact_hz']==contract['full_frequency_hz'] and
         out['num_ports']==4 and out['num_frequency_points']==56 and all(v is True for v in out['checks'].values()),
         'original full frequency contract')
    need((out['touchstone_path'],out['touchstone_sha256'],out['touchstone_size_bytes'])==
         (s4p['path'],s4p['sha256'],s4p['bytes']),'receipt S4P mismatch')
    need(r['manifest_contract']['port_order']==['P001','P002','P003','P004'] and
         r['manifest_contract']['cadence_pin_purpose']==51 and r['top_cell']=='TRANSFORMER', 'ports or top cell')
    gds=r['source_exact_gds'];check(gds);check(r['source_layout_manifest']);check(r['source_calibre_report'])
    drc=doc(r['source_calibre_zero_blocking_receipt'])
    need(drc['overall_status']=='PASS' and drc['calibre_executed'] is True and
         drc['calibre_blocking_violations']==0 and drc['source_files_unchanged'] is True,
         'Calibre not zero blocking','physical_invalid')
    need(drc['geometry_identity_sha256']==drc['candidate_id_sha256']==gid and
         drc['gds_path']==gds['path'] and drc['gds_sha256']==gds['sha256'] and
         drc['contract_fingerprint_sha256']==FP, 'DRC geometry/GDS binding')
    check(drc['source_calibre_summary']);audit=doc(drc['source_geometry_audit'])
    need(audit['overall_status']=='PASS' and audit['candidate_geometry_identity_sha256']==gid and
         audit['gds_sha256']==gds['sha256'] and audit['gds_path']==gds['path'] and
         all(audit['checks'][k] is True for k in audit['effective_required_geometry_checks']),
         'actual layout audit failed','physical_invalid')
    for p in audit['source_evidence'].values():
        if p['path'].endswith('.proc'):shared(p)
        else:check(p)
    command_pin=dict(path=out['emx_command_path'],sha256=out['emx_command_sha256'],bytes=out['emx_command_size_bytes'])
    check(command_pin)
    values=label_state(label)
    return dict(geometry=geometry,geometry_fields=fields,identities=ids,physical15=values,
                q10_to20_supported=10<=values['qmin']<=20,s4p=s4p,gds=gds,
                source_receipt=receipt_pin,source_calibre=r['source_calibre_zero_blocking_receipt'])

def main():
    d=Path(__file__).resolve().parent;inputs=json.loads((d/'INPUTS.json').read_bytes());out=d/'audit'
    out.mkdir(exist_ok=False)
    shared_pins={}
    def shared(p):
        prior=shared_pins.get(p['path'])
        need(prior is None or prior['sha256']==p['sha256'], 'shared source identity conflict')
        if prior is None:read(p);shared_pins[p['path']]=p
    contract=inputs['contract']
    for p in contract['pins']:shared(p)
    source=inputs['source'];tables={}
    for name in ('accepted','index'):
        raw=read(source[name]);tables[name]={r['geometry_sha256']:r for r in csv.DictReader(io.StringIO(raw.decode()))}
    label_raw=read(inputs['labels']);labels={r['geometry_sha256']:r for r in csv.DictReader(io.StringIO(label_raw.decode()))}
    known={k:{} for k in ('canonical_9dp','production_1e6','nominal_grid_5nm')}
    for p in inputs['baseline_records']:
        value=json.loads(read(p));r=value['record']
        for key,identity in identities(r['geometry'],r['geometry_fields']).items():known[key][identity]=dict(namespace='current_qualified',sequence=r['increment_sequence'])
    rows=sorted(tables['accepted'].values(),key=lambda r:int(r['accepted_sequence']))[:1000]
    need([int(r['accepted_sequence']) for r in rows]==list(range(1,1001)),'bounded source sequence')
    counts=Counter({k:0 for k in CLASSES});total=0;blocks=[]
    for start in range(0,1000,100):
        results=[]
        for a in rows[start:start+100]:
            checked={};gid=a['geometry_sha256']
            result=dict(source_old_accepted_sequence=int(a['accepted_sequence']),geometry_sha256=gid,
                        qualification_sequence=None,qualified_ledger_committed=False,old_broadband_recounted=False)
            try:
                details=verify_member(a,tables['index'][gid],labels[gid],contract,checked,shared)
                matches={k:known[k][identity] for k,identity in details['identities'].items() if identity in known[k]}
                result.update(details)
                if matches:result.update(status='duplicate',reason='already in certified union or earlier qualified row',matches=matches)
                else:
                    result.update(status='qualified',reason='current bytes and source-chain qualification; publication pending')
                    for k,identity in details['identities'].items():known[k][identity]=dict(namespace='this_historical_audit',source_old_accepted_sequence=int(a['accepted_sequence']))
            except (FileNotFoundError,PermissionError) as e:result.update(status='missing_evidence',reason=str(e))
            except Disposition as e:result.update(status=e.status,reason=str(e))
            except KeyError as e:result.update(status='missing_evidence',reason='required source field missing: '+str(e))
            result['checked_source_pins']=list(checked.values());results.append(result);counts[result['status']]+=1;total+=1
        path=out/f'block_{start+1:06d}_{start+100:06d}.json'
        save(path,dict(utc=datetime.now(timezone.utc).isoformat(),rows=results,cumulative_processed=total,
                      cumulative_classifications=dict(counts),baseline_qualified_ledger_count=len(inputs['baseline_records']),
                      metadata_source_pins={k:source[k] for k in ('accepted','index')},label_source=inputs['labels'],
                      current_qualified_ledger_added=0,simulator_actions=0,labels_recomputed=False))
        blocks.append(pin(path));print(json.dumps(dict(processed=total,counts=dict(counts))),flush=True)
    for p in shared_pins.values():read(p)
    for p in inputs['baseline_records']:read(p)
    save(out/'RECEIPT.json',dict(status='1000_SOURCE_CHAIN_AUDIT_COMPLETE_SHARED_PUBLICATION_PENDING',
        utc=datetime.now(timezone.utc).isoformat(),processed=total,classifications=dict(counts),blocks=blocks,
        shared_source_pins=list(shared_pins.values()),input_pin=pin(d/'INPUTS.json'),source_pin=pin(__file__),
        unchanged_baseline_qualified=31,current_qualified_ledger_added=0,
        remaining_old21135_records_not_processed_this_package=20135,
        unqualified_other_history_pending=True,full100k_qualified_total=None,
        sampling_changed=False,labels_recomputed=False,simulator_actions=0,training_actions=0))
    print(json.dumps(dict(receipt=pin(out/'RECEIPT.json'))))

if __name__=='__main__':main()
