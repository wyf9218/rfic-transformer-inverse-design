"""Bounded identity-only extension of the existing proposal exclusions.

No source qualification, labels, models, ledger writes, or native execution.
Namespaces remain separate; unknown full-history union stays unknown.
"""
import math
import re


def require(ok, message):
    if not ok:
        raise ValueError(message)


def extend_known_subsets(*, canonical_sets, grid_sets, production_known,
                         original_rows, historical_rows, fields, grid_um,
                         canonicalize, grid_geometry, fingerprint,
                         expected_original_count=256, expected_historical_count=5718):
    require(len(original_rows)==expected_original_count, 'wrong frozen previous proposal count')
    require(len(historical_rows)==expected_historical_count, 'wrong frozen historical identity count')
    require(len({r['request_id'] for r in original_rows})==len(original_rows), 'old request IDs duplicated')
    old_name='PREVIOUS256_ALL_RAW_AND_GRID'
    hist_name='HISTORICAL5718_AUDITED_IDENTITY_METADATA_ONLY'
    for name in (old_name, hist_name):
        require(name not in canonical_sets and name not in grid_sets, 'exclusion extension already installed')
        canonical_sets[name]=set();grid_sets[name]=set()
    for row in original_rows:
        raw, grid = row['raw_proposed_geometry'], row['geometry']
        require(row['geometry_fields']==fields and len(raw)==len(grid)==len(fields), 'old geometry schema differs')
        require(all(type(v) in (int,float) and math.isfinite(v) for v in raw+grid), 'nonfinite old geometry')
        snapped=grid_geometry(raw,grid_um)
        require(list(snapped)==list(grid), 'old raw-to-grid binding differs')
        ch=canonicalize(dict(zip(fields,grid)),fields=fields)
        rh=canonicalize(dict(zip(fields,raw)),fields=fields)
        require(ch==row['canonical_geometry_sha256'] and
                fingerprint(grid)==row['production_geometry_fingerprint'] and
                fingerprint(raw)==row['raw_production_geometry_fingerprint'], 'old saved identity mismatch')
        canonical_sets[old_name].update((ch,rh));grid_sets[old_name].add(ch)
        production_known.update((fingerprint(raw),fingerprint(grid)))
    source_sequences=set()
    for row in historical_rows:
        seq=row['source_old_accepted_sequence'];ids=row['identities']
        require(type(seq) is int and seq not in source_sequences, 'historical sequence duplicate/noninteger')
        source_sequences.add(seq)
        require(set(ids)=={'canonical_9dp','nominal_grid_5nm','production_1e6'} and
                all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in ids.values()), 'historical namespace schema differs')
        require(row['geometry_sha256']==ids['canonical_9dp'], 'historical canonical binding differs')
        canonical_sets[hist_name].add(ids['canonical_9dp'])
        grid_sets[hist_name].add(ids['nominal_grid_5nm'])
        production_known.add(ids['production_1e6'])
    return dict(previous_proposals=len(original_rows),historical_identity_rows=len(historical_rows),
        previous_raw_and_grid_identities_preserved=True,historical_numeric_labels_used=0,
        historical_geometry_payloads_read=0,historical_formal_status_inferred=False,
        historical_grid_production_hash_not_invented=True,
        full_history_union_complete=False,owner_current_mixed_ledger_check='REQUIRED',
        added_sets={name:dict(canonical=len(canonical_sets[name]),nominal_grid=len(grid_sets[name]))
                    for name in (old_name,hist_name)})


def known_duplicate_reasons(*, canonical, raw_canonical, production, raw_production,
                            canonical_sets, grid_sets, production_known):
    reasons=[]
    for name, hashes in canonical_sets.items():
        if canonical in hashes:reasons.append('EXISTING_CANONICAL_'+name)
        if raw_canonical in hashes:reasons.append('EXISTING_RAW_CANONICAL_'+name)
    for name, hashes in grid_sets.items():
        if canonical in hashes:reasons.append('NOMINAL_GRID_EQUIVALENT_'+name)
    if production in production_known:
        reasons.append('EXISTING_PRODUCTION_FINGERPRINT_KNOWN_SUBSET')
    if raw_production in production_known:
        reasons.append('EXISTING_RAW_PRODUCTION_FINGERPRINT_KNOWN_SUBSET')
    return reasons
