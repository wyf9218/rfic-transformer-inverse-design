#!/usr/bin/env python3
"""Verify private handover assets or extract the exact original CSV. No experiments."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil

DATASET_SHA = 'fe94d15b0d083330fe7a64d3e059d083a5aa32d73394c85fc4b881e9904e68a2'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def member(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Invalid manifest path')
    p = root / relative
    if p.is_symlink() or not p.resolve().is_relative_to(root):
        raise ValueError('Manifest member escapes asset root')
    return p


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('verify', 'extract'))
    p.add_argument('--assets', type=Path, required=True)
    p.add_argument('--out', type=Path)
    p.add_argument('--all', action='store_true', help='Verify all archived files, not only core assets')
    a = p.parse_args()
    root = a.assets.resolve(strict=True)
    manifest = json.loads((root / 'ASSET_MANIFEST.json').read_text())
    if manifest.get('schema') != 'liu_private_research_assets.v1':
        raise ValueError('Unexpected manifest; do not substitute another dataset')
    files = manifest['files']
    chosen = files if a.all else [x for x in files if x['path'].startswith(
        ('dataset/', 'models/original100k/', 'models/real10k/', 'models/symmetric100k/',
         'contracts/', 'runtime/', 'research/dataset_size_811_20260921_v1/data/'))]
    required = {'dataset/original100k.csv.gz', 'models/original100k/physical_feature_tandem_inverse_weights.npz',
                'models/symmetric100k/physical_feature_tandem_inverse_weights.npz'}
    if not required.issubset({x['path'] for x in chosen}):
        raise ValueError('Incomplete core asset manifest')
    for item in chosen:
        f = member(root, item['path'])
        if not f.is_file() or f.stat().st_size != item['bytes'] or digest(f) != item['sha256']:
            raise ValueError('Missing or changed asset: ' + item['path'])
    result = {'status': 'PASS', 'verified_files': len(chosen), 'scope': 'all' if a.all else 'core',
              'training_started': False, 'simulation_started': False}
    if a.mode == 'extract':
        if a.out is None: p.error('extract requires --out NEW.csv')
        # Exclusive create; a failed/partial extraction is retained, never silently overwritten.
        with gzip.open(root / 'dataset/original100k.csv.gz', 'rb') as src, a.out.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        if digest(a.out) != DATASET_SHA: raise ValueError('Extracted CSV identity mismatch')
        result.update(dataset=str(a.out.resolve()), dataset_sha256=DATASET_SHA)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
