"""Explicit read-only MARS discovery/transport for the private 10K study.

No daemon is installed. A probe reads completed JSON receipts, not active CSVs.
Large immutable data files are copied only after the formal threshold is met.
The producer root and old raw receipts remain byte-identical and unmodified.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from .io import read_json, sha256, utc_now
from .study_once import atomic_json, pin, verify_pin


REMOTE_DISCOVERY = r'''
import base64,hashlib,json,pathlib,sys
request=json.load(sys.stdin)
h=request['handoff']
root=pathlib.Path(h['production_root'])
def read_pin(p):
 p=pathlib.Path(p)
 if p.is_symlink() or not p.is_file(): raise ValueError('not a regular committed receipt')
 a=p.stat(); raw=p.read_bytes(); b=p.stat()
 if (a.st_ino,a.st_size,a.st_mtime_ns)!=(b.st_ino,b.st_size,b.st_mtime_ns): raise ValueError('receipt changed while reading')
 return {'path':str(p),'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),'raw_base64':base64.b64encode(raw).decode()}
base=h['checkpoint']['boundary_progress']
base_pin=base.get('pin',base)
boundary=read_pin(base_pin['path'])
if boundary['sha256']!=base_pin['sha256']: raise ValueError('base boundary changed')
prior=boundary['sha256']; previous=json.loads(base64.b64decode(boundary['raw_base64']))
count=h['checkpoint']['count']; chain=[]
if previous['accepted_after']!=count: raise ValueError('base count differs')
for k in ('campaign_id','contract_fingerprint_sha256'):
 if previous.get(k)!=h.get(k) or not h.get(k): raise ValueError('base producer identity differs')
for k in ('backend_id','backend_identity_manifest_sha256','full_campaign_authorization_receipt_sha256'):
 if not previous.get(k): raise ValueError('base lacks required producer identity')
pending=[]
for p in sorted((root/'stages').glob('*/STAGE_PROGRESS_RECEIPT.json')):
 if p==pathlib.Path(base_pin['path']): continue
 try: item=read_pin(p); value=json.loads(base64.b64decode(item['raw_base64']))
 except (OSError,ValueError): continue
 if value.get('accepted_after',-1)>count: pending.append((item,value))
while True:
 successors=[x for x in pending if x[1].get('prior_progress_receipt_sha256')==prior]
 if not successors: break
 if len(successors)!=1: raise ValueError('ambiguous producer commit successor')
 item,value=successors[0]
 for k in ('campaign_id','contract_fingerprint_sha256','backend_id','backend_identity_manifest_sha256','full_campaign_authorization_receipt_sha256'):
  if value.get(k)!=previous.get(k): raise ValueError('producer identity changed; new handoff required: '+k)
 if value.get('stage')!='PHASE_A': raise ValueError('stage transition requires a new handoff')
 if value['accepted_before']!=count or value['accepted_after']!=count+value['accepted_this_attempt']: raise ValueError('broken accepted count chain')
 if value['attempt_index']!=previous['attempt_index']+1: raise ValueError('broken attempt chain')
 if value.get('overall_status')!='INCOMPLETE' or value.get('decision')!='CONTINUE_SAMPLING': raise ValueError('unsupported commit terminal; needs reviewed adapter')
 for k in ('accepted_blocking_calibre_count','accepted_duplicate_geometry_count','historical_label_count','interpolated_frequency_record_count','manual_gds_modification_count','mixed_contract_fingerprint_count','proxy_label_count'):
  if value.get('safeguards',{}).get(k)!=0: raise ValueError('producer safeguard failed: '+k)
 if value.get('failure_accounting',{}).get('accepted_geometries')!=value['accepted_this_attempt']: raise ValueError('accepted accounting differs')
 required={'accepted_geometry_increment','long_features','s4p_artifact_index','exact_gds_emx_receipt_index'}
 if not required.issubset(value.get('artifacts',{})): raise ValueError('incomplete committed artifact closure')
 for artifact in value['artifacts'].values():
  p=pathlib.Path(artifact['path'])
  if not p.is_file() or p.is_symlink() or p.stat().st_size!=artifact['size_bytes']: raise ValueError('committed artifact missing/size differs')
 chain.append(item);count=value['accepted_after'];prior=item['sha256'];previous=value
 pending.remove(successors[0])
 if count>=10000: break
print(json.dumps({'committed_accepted':count,'boundary':boundary,'increments':chain,'end_receipt_sha256':prior,'large_artifacts_hashed':False}))
'''


def _ssh_prefix(host, control_path):
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError("invalid SSH host")
    return ["ssh", "-S", str(control_path), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", host]


def _cache_raw(value, cache):
    raw = base64.b64decode(value["raw_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != value["sha256"] or len(raw) != value["size_bytes"]:
        raise ValueError("remote raw receipt transport identity mismatch")
    target = Path(cache) / (value["sha256"] + ".json")
    if not target.exists():
        # Preserve the exact raw bytes, not a reserialized JSON approximation.
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    if sha256(target) != value["sha256"]:
        raise ValueError("local content-addressed receipt changed")
    return {"path": str(target.resolve()), "sha256": value["sha256"],
            "size_bytes": len(raw), "source_path": value["path"]}


def fetch_exact(value, cache, host, control_path):
    """No-clobber streaming transport; a failed partial file remains evidence."""
    cache = Path(cache)
    target = cache / value["sha256"]
    if target.exists():
        verify_pin({"path": str(target), "sha256": value["sha256"], "bytes": value["size_bytes"]})
    else:
        fd, partial = tempfile.mkstemp(prefix="transfer_attempt_", dir=cache)
        with os.fdopen(fd, "wb") as outgoing:
            command = _ssh_prefix(host, control_path) + ["cat -- " + shlex.quote(value["path"])]
            result = subprocess.run(command, stdout=outgoing, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if result.returncode != 0 or sha256(partial) != value["sha256"] or Path(partial).stat().st_size != value["size_bytes"]:
            atomic_json(str(partial) + ".FAILED.json", {"status": "FAIL", "source": value,
                "returncode": result.returncode, "partial_path": partial,
                "error": result.stderr.decode(errors="replace")[-2000:]}, immutable=True)
            raise ValueError("remote transfer failed identity validation; partial evidence retained")
        os.link(partial, target)
        os.unlink(partial)
        target.chmod(0o444)
    return {"path": str(target.resolve()), "sha256": value["sha256"],
            "size_bytes": value["size_bytes"], "source_path": value["path"]}


def probe_and_localize(handoff_path, expected_handoff_sha256, base_source_manifest,
                       out, *, host, control_path):
    handoff_path, out = Path(handoff_path).resolve(), Path(out).resolve()
    if sha256(handoff_path) != expected_handoff_sha256:
        raise ValueError("producer handoff identity differs")
    handoff = read_json(handoff_path)
    if handoff.get("schema") != "b56_private_read_only_data_handoff.v1":
        raise ValueError("unsupported producer handoff")
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "content_sha256"
    cache.mkdir(exist_ok=True)
    run = subprocess.run(_ssh_prefix(host, control_path) + ["python3 -c " + shlex.quote(REMOTE_DISCOVERY)],
                         input=json.dumps({"handoff": handoff}), capture_output=True, text=True)
    if run.returncode != 0:
        raise RuntimeError("read-only MARS discovery failed: " + run.stderr[-2000:])
    discovered = json.loads(run.stdout)
    boundary = _cache_raw(discovered["boundary"], cache)
    increments = [{"receipt": _cache_raw(item, cache)} for item in discovered["increments"]]
    result = {"schema": "bb_read_only_research_probe.v1", "status": "WAITING_FOR_10K",
              "committed_accepted": discovered["committed_accepted"], "observed_utc": utc_now(),
              "handoff": pin(handoff_path), "boundary": boundary, "increments": increments,
              "end_receipt_sha256": discovered["end_receipt_sha256"],
              "large_artifact_hash_closure": "NOT_RUN_BELOW_THRESHOLD", "automatic_trigger": "NOT_INSTALLED",
              "production_modified": False}
    if discovered["committed_accepted"] >= 10000:
        # Transfer is research-local; retain a ten-GiB reserve before admitting
        # all uncached immutable artifacts. No producer storage policy changes.
        pending = {handoff["checkpoint"]["status"]["sha256"]: handoff["checkpoint"]["status"]}
        for record in increments:
            raw = read_json(record["receipt"]["path"])
            for value in list(raw["artifacts"].values()) + list((raw.get("round_cumulative_inputs") or {}).values()):
                pending[value["sha256"]] = value
        transfer_bytes = sum(value["size_bytes"] for digest, value in pending.items() if not (cache / digest).exists())
        free = shutil.disk_usage(out).free
        if free < 10 * 1024**3 + transfer_bytes:
            result.update(status="WAITING_RESOURCE", reason="Research-local transport disk reserve unavailable",
                disk_free_bytes=free, uncached_transfer_bytes=transfer_bytes, reserve_bytes=10 * 1024**3,
                large_artifact_hash_closure="NOT_RUN_RESOURCE_GATE", training_started=False)
            observation = out / ("probe_" + result["observed_utc"].replace(":", "").replace("+", "_") + ".json")
            atomic_json(observation, result, immutable=True)
            return {**result, "receipt": pin(observation)}
        base = read_json(base_source_manifest)
        if base.get("schema") != "bb_source_manifest.v1":
            raise ValueError("existing verified local base manifest required")
        expected = handoff["checkpoint"]["inputs"]
        for local_role, remote_role in (("accepted_geometries", "accepted_geometries"),
                                       ("long_features", "long_features"),
                                       ("sparameter_index", "artifact_index")):
            local_pin = base["files"][local_role]
            if local_pin["sha256"] != expected[remote_role]["sha256"]:
                raise ValueError("local base is not the handoff cumulative checkpoint")
            local = (Path(base_source_manifest).resolve().parent / local_pin["path"]).resolve()
            verify_pin({"path": str(local), "sha256": local_pin["sha256"]})
        for record in increments:
            raw = read_json(record["receipt"]["path"])
            record["files"] = {name: fetch_exact(value, cache, host, control_path)
                               for name, value in raw["artifacts"].items()}
            if raw.get("round_cumulative_inputs"):
                record["round_cumulative_inputs"] = {
                    name: fetch_exact(value, cache, host, control_path)
                    for name, value in raw["round_cumulative_inputs"].items()}
        status_pin = fetch_exact(handoff["checkpoint"]["status"], cache, host, control_path)
        # Add the newly verified status reference in a derived research manifest;
        # never edit the historical 5000 manifest or any producer receipt bytes.
        localized_base = json.loads(json.dumps(base))
        for item in localized_base["files"].values():
            item["path"] = str((Path(base_source_manifest).resolve().parent / item["path"]).resolve())
        localized_base["files"]["checkpoint_status"] = status_pin
        base_path = out / ("base_" + sha256(base_source_manifest) + "_with_status.json")
        if base_path.exists():
            if read_json(base_path) != localized_base:
                raise ValueError("localized base descriptor changed")
        else:
            atomic_json(base_path, localized_base, immutable=True)
        source = {"schema": "bb_committed_increment_source.v1", "campaign_id": handoff["campaign_id"],
                  "contract_fingerprint_sha256": handoff["contract_fingerprint_sha256"],
                  "port_contract": base["port_contract"], "base_source_manifest": pin(base_path),
                  "checkpoint_status": status_pin,
                  "boundary_progress_receipt": boundary, "increments": increments,
                  "committed_accepted": discovered["committed_accepted"], "source_handoff": pin(handoff_path)}
        source_path = out / ("source_" + discovered["end_receipt_sha256"] + ".json")
        if source_path.exists():
            if read_json(source_path) != source:
                raise ValueError("same commit closure produced a different source identity")
        else:
            atomic_json(source_path, source, immutable=True)
        result.update(status="SOURCE_TRANSPORT_VERIFIED_PENDING_SELECTION", source_manifest=pin(source_path),
                      large_artifact_hash_closure="PASS")
    observation = out / ("probe_" + result["observed_utc"].replace(":", "").replace("+", "_") + ".json")
    atomic_json(observation, result, immutable=True)
    return {**result, "receipt": pin(observation)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("handoff", "expected-handoff-sha256", "base-source-manifest", "out", "host", "control-path"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    result = probe_and_localize(args.handoff, args.expected_handoff_sha256,
                               args.base_source_manifest, args.out, host=args.host, control_path=args.control_path)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
