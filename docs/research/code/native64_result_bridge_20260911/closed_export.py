"""Bounded closed-RESULT metadata capture; no scan, transport, solver or new QA."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from controlled_metadata import parse, read_pin, require
import controlled_result as producer
from start_slots import path_ok, write_once


def utc():return datetime.now(timezone.utc).isoformat()


def validate_closed(batch,request_id,source):
    value=parse(read_pin(source));status=value['status']
    if status=='ANALYTIC_FAIL_NOT_DISPATCHED':
        expected=producer.original_hold(batch,request_id)
    elif status=='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION':
        expected=producer.failed_candidate(batch,request_id,error=value['error'],stage_evidence=value['stage_evidence'])
    elif status=='FRESH_EMX_EXTRACTED':
        expected=producer.fresh_candidate(batch,request_id,feature_pin=value['feature'],
            observation_pin=value['native_observation'],plan_pin=value['plan'])
    else:
        raise ValueError('UNIMPLEMENTED_CLOSED_RESULT_STATUS: '+status)
    require(value==expected,'CLOSED_RESULT_DOES_NOT_MATCH_FROZEN_PRODUCER')
    read_pin(source)
    return value


def capture(batch,declared_results,owner_root,output):
    """Read only supplied immutable result pins, in original proposal order.

    The capture timestamps describe reading, never a native start. A partial
    capture cannot establish the total number or order of actual native starts.
    """
    owner=path_ok(owner_root);target=path_ok(output)
    require(owner.is_dir(),'EXISTING_OWNER_ROOT_REQUIRED')
    require(not target.exists(),'NO_CLOBBER_EXPORT_REQUIRED')
    require(not target.is_relative_to(owner) and not owner.is_relative_to(target),
        'CAPTURE_OUTPUT_MUST_NOT_OVERLAP_OWNER')
    known={r['request_id'] for r in batch.rows}
    require(set(declared_results)<=known,'FOREIGN_RESULT_ID')
    started=utc();records=[];snapshots=[]
    for proposal in batch.rows:
        rid=proposal['request_id'];source=declared_results.get(rid)
        if source is None:
            records.append(dict(request_id=rid,candidate_id=proposal['candidate_id'],
                arm=proposal['arm'],original_global_order=proposal['global_order'],
                capture_state='NO_CLOSED_RESULT_IN_THIS_CAPTURE',source_result=None,
                actual_native_starts=None,result=None))
            continue
        require(source['path']==str(owner/rid/'RESULT.json'),'FOREIGN_OWNER_RESULT_PATH')
        value=validate_closed(batch,rid,source)
        records.append(dict(request_id=rid,candidate_id=proposal['candidate_id'],arm=proposal['arm'],
            original_global_order=proposal['global_order'],capture_state='PINNED_CLOSED_RESULT',
            source_result=source,actual_native_starts=value['actual_native_starts'],result=value))
        snapshots.append(source)
    counts=Counter(r['result']['status'] for r in records if r['result'] is not None)
    closed=[r for r in records if r['result'] is not None]
    unknown=[r['request_id'] for r in records if r['actual_native_starts'] is None]
    # Recheck all closed input bytes before the first output write.
    for pin in snapshots:read_pin(pin)
    value=dict(schema='eucap15_controlled64_closed_metadata_capture.v1',
        status='COMPLETE_CLOSED_METADATA_CAPTURE' if len(closed)==64 else 'PARTIAL_CLOSED_METADATA_CAPTURE',
        capture_started_utc=started,capture_completed_utc=utc(),
        controlled_manifest=batch.manifest_pin,controlled_intent=batch.intent_pin,
        owner_root=str(owner),original_proposal_denominator=64,N_closed=len(closed),
        N_not_observed_closed=64-len(closed),closed_status_counts=dict(counts),records=records,
        observed_native_births_in_closed_results=sum(r['actual_native_starts'] or 0 for r in closed),
        native_total_known=not unknown,actual_native_starts=None if unknown else sum(r['actual_native_starts'] for r in records),
        requests_with_unknown_native_count=unknown,actual_native_start_order='NOT_RECONSTRUCTED',
        native_started_utc=None,clock_note='Capture timestamps are not solver start times',
        source_result_pins=snapshots,raw_artifacts_copied=False,
        artifact_transport='NOT_IMPLEMENTED_USE_ORIGINAL_PRIVATE_PINS_AND_VERIFIED_PATH_MAP',
        physical_qa_rerun=False,production_accepted_added=0,
        inputs_checked_by='Existing result producer evidence binding, not fresh physical validation')
    target.mkdir(parents=True,exist_ok=False)
    write_once(target/'CLOSED_METADATA_CAPTURE.json',value)
    require(parse((target/'CLOSED_METADATA_CAPTURE.json').read_bytes())==value,'CAPTURE_READBACK_MISMATCH')
    return value
