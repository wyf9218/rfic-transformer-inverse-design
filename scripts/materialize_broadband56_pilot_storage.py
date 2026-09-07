#!/usr/bin/env python3
"""Produce the existing pilot storage sidecar from committed physical artifacts."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    CAMPAIGN_ID, SCIENTIFIC_CONTRACT_FINGERPRINT, required_storage_bytes,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import validate_stage_receipt


def checked_path(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('absolute non-symlink evidence path required')
    return path


def pin(path):
    path = checked_path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(path=str(path), sha256=digest.hexdigest(), size_bytes=path.stat().st_size)


def bound(record):
    if pin(record['path']) != record:
        raise ValueError('source identity mismatch: '+record['path'])
    return Path(record['path'])


def read(path):
    return json.loads(Path(path).read_bytes())


def stage_root(path):
    path = checked_path(path)
    indexes = [i for i, part in enumerate(path.parts) if part == 'stages']
    if len(indexes) != 1:
        raise ValueError('pilot source must belong to one campaign stage')
    i = indexes[0]
    if not path.parts[i-1].startswith(CAMPAIGN_ID+'_') or len(path.parts) <= i+2:
        raise ValueError('pilot source is outside this campaign')
    return Path(*path.parts[:i+2])


def inventory(roots):
    """Count retained storage, including failures, without following links."""
    seen = set()
    digest = hashlib.sha256()
    totals = []
    for root in sorted(roots):
        root = checked_path(root)
        if not root.is_dir():
            raise ValueError('missing pilot source directory')
        count = links = logical = allocated = charged = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            directory_links = [Path(current)/name for name in dirs if (Path(current)/name).is_symlink()]
            for path in [Path(current), *(Path(current)/name for name in sorted(files)), *directory_links]:
                info = path.lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                    raise ValueError('unsupported pilot artifact type: '+str(path))
                identity = (info.st_dev, info.st_ino)
                record = [str(path), *identity, info.st_mode, info.st_size,
                          info.st_blocks, info.st_mtime_ns, info.st_ctime_ns]
                digest.update(json.dumps(record, separators=(',', ':')).encode()+b'\n')
                if identity in seen:
                    continue
                seen.add(identity)
                count += int(stat.S_ISREG(info.st_mode))
                links += int(stat.S_ISLNK(info.st_mode))
                logical += info.st_size
                allocated += info.st_blocks * 512
                charged += max(info.st_size, info.st_blocks * 512)
            # Cadence library links consume their own inode, not the external PDK.
            dirs[:] = [name for name in dirs if not (Path(current)/name).is_symlink()]
        totals.append(dict(path=str(root), regular_files=count, symlinks_not_followed=links, logical_bytes=logical,
                           allocated_bytes=allocated, charged_bytes=charged))
    return dict(roots=totals, inventory_sha256=digest.hexdigest(),
                total_charged_bytes=sum(row['charged_bytes'] for row in totals))


def prepare(stage_path, expected_sha):
    source = pin(stage_path)
    if source['sha256'] != expected_sha:
        raise ValueError('pilot stage SHA differs')
    payload = read(bound(source))
    root = checked_path(stage_path).parent.parent.parent
    queue = read(root/'MARS_QUEUE_ENTRY.json')
    backend = queue['backend_identity_manifest']
    bound(backend)
    authorization = pin(root/'FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json')
    errors = validate_stage_receipt(payload, stage='PILOT_1000', cumulative_target=1000,
        backend_manifest_sha256=backend['sha256'], authorization_receipt_sha256=authorization['sha256'],
        prior_stage_receipt_sha256=payload.get('prior_stage_receipt_sha256'), verify_artifacts=True)
    if errors:
        raise ValueError('pilot terminal validation failed: '+repr(errors))
    checkpoint = read(bound(payload['artifacts']['checkpoint_status']))
    if (checkpoint.get('checkpoint_status') != 'PILOT_1000_COMPLETE'
            or checkpoint.get('accepted_geometries') != 1000
            or checkpoint.get('geometry_frequency_rows') != 56000):
        raise ValueError('pilot checkpoint not complete')
    ledger = payload['artifacts']['attempt_ledger']
    with bound(ledger).open() as stream:
        rows = list(csv.DictReader(stream))
    accepted = [row['geometry_id'] for row in rows if row['terminal_stage'] == 'ACCEPTED']
    if len(accepted) != 1000 or len(set(accepted)) != 1000:
        raise ValueError('pilot ledger accepted denominator or uniqueness differs')
    roots = {Path(stage_path).parent}
    physical = {}
    for row in rows:
        if row.get('campaign_contract_fingerprint') != SCIENTIFIC_CONTRACT_FINGERPRINT:
            raise ValueError('pilot ledger contract differs')
        for key in ('candidate_source', 'gds', 'calibre_report', 'emx_log', 's4p'):
            path, sha = row.get(key+'_path'), row.get(key+'_sha256')
            if not path:
                if sha:
                    raise ValueError('hash without pilot artifact path')
                continue
            if path not in physical:
                physical[path] = pin(path)
            if physical[path]['sha256'] != sha:
                raise ValueError('pilot ledger physical identity differs: '+path)
            roots.add(stage_root(path))
    measured = inventory(roots)
    if measured != inventory(roots):
        raise ValueError('pilot inventory changed during measurement')
    bound(source)
    bound(ledger)
    if measured['total_charged_bytes'] <= 0:
        raise ValueError('pilot measured storage must be positive')
    per_geometry = measured['total_charged_bytes']/1000
    required = required_storage_bytes(stage='PHASE_A', current_accepted=1000,
                                      measured_pilot_bytes_per_geometry=per_geometry)
    fs = os.statvfs(root)
    free = fs.f_bavail * fs.f_frsize
    return dict(schema='rfic_transformer.broadband56_measured_pilot_storage.v1',
        overall_status='PASS_MEASUREMENT_NOT_RESOURCE_ADMISSION',
        generated_utc=datetime.now(timezone.utc).isoformat(), campaign_id=CAMPAIGN_ID,
        contract_fingerprint_sha256=SCIENTIFIC_CONTRACT_FINGERPRINT,
        campaign_root=str(root), source_stage_receipt=source, attempt_ledger=ledger,
        backend_identity_manifest=backend, full_campaign_authorization_receipt=authorization,
        measurement_method='SUM_MAX_LOGICAL_ALLOCATED_BYTES_PER_UNIQUE_INODE_IN_LEDGER_BOUND_STAGE_DIRS',
        includes_failed_attempts_and_retained_intermediates=True,
        accepted_unique_geometries=1000, geometry_frequency_rows=56000,
        bytes_per_geometry=per_geometry, measurement=measured,
        unchanged_storage_safety_factor=1.25, remaining_geometries=199000,
        required_storage_bytes=required, filesystem_available_bytes=free,
        storage_gate='PASS' if free >= required else 'FAIL',
        production_resource_admission='NOT_RUN', simulator_action_taken=False,
        source_evidence_modified=False, producer=pin(__file__))


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-receipt', required=True)
    parser.add_argument('--stage-sha256', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args(argv)
    out = checked_path(args.out_dir)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        result = prepare(args.stage_receipt, args.stage_sha256)
        write(out/'PILOT_1000_RESOURCE_SUMMARY.json', result)
        print(json.dumps(pin(out/'PILOT_1000_RESOURCE_SUMMARY.json')))
    except Exception as error:
        write(out/'STORAGE_MEASUREMENT_FAILURE.json', dict(overall_status='FAIL', error=repr(error)))
        raise


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
