"""Exact five-source endpoint upgrade for future independent batches only."""
from copy import deepcopy
from pathlib import Path

import controlled_execution as e
import controlled_metadata as m

POLICY = 'shared_port_edges_lineage_20260912_v2'
PARENT_POLICY = 'shared_port_edges_20260912_v1'
SOURCES = {
    'rfic_transformer_inverse_design/layout/port_endpoint_lineage.py': '7d0e0a67aed3364cc4cbcdf2ea2bc6121863eb4184d48cc50f0ec8abd9536061',
    'rfic_transformer_inverse_design/layout/export.py': '7030d03fe08806ff1a8b5e3a34dddc8471c135659fe7d9c783613e1058f0e8e7',
    'rfic_transformer_inverse_design/execution/evaluator.py': '3bf7f1d11f74b85e007f5bf964bec8f19379d708ab5bc850b90c122b08ac30a9',
    'scripts/run_candidate_queue_dataset.py': '3c908101249cbdb7e7644352e696ab102f75c5269fce5fc8121c7dbb6e8b884c',
    'scripts/run_candidate_queue_dataset_parallel.py': '0b0341ecec11d6049e9a3de95899ed3cb576e30b4783b9a467de66bf837f3020',
}


def load(identity):
    value = e.document(identity)
    m.require(value['schema'] == 'eucap15_successor_endpoint_upgrade.v1' and
              value['policy'] == POLICY and value['parent_policy'] == PARENT_POLICY,
              'EXACT_SUCCESSOR_ENDPOINT_SCOPE_REQUIRED')
    m.require(set(value['changes']) == set(SOURCES), 'ENDPOINT_FIVE_SOURCE_SET_CHANGED')
    for name, expected in SOURCES.items():
        item = value['changes'][name]
        m.require(item['new']['sha256'] == expected and
                  item['new']['path'] == str(Path(value['new_repo']) / name),
                  'ENDPOINT_SOURCE_IDENTITY_CHANGED')
        m.read_pin(item['new'])
    for proof in value['evidence']:
        m.read_pin(proof)
    return value


def repo_pins(base):
    found = {}
    def visit(v):
        if isinstance(v, dict):
            if set(v) == {'path', 'sha256', 'bytes'} and v['path'].startswith(base['repo'] + '/'):
                m.require(v['path'] not in found or found[v['path']] == v, 'CONFLICTING_REPO_PIN')
                found[v['path']] = v
            for item in v.values(): visit(item)
        elif isinstance(v, list):
            for item in v: visit(item)
    visit(base)
    return found


def _inventory(value, base):
    old = repo_pins(base)
    declared = {item['parent']['path']: item['parent'] for item in value['files'] if item['parent'] is not None}
    m.require(base['repo'] == value['old_repo'] and old == declared,
              'ENDPOINT_PARENT_SOURCE_INVENTORY_CHANGED')
    names = set()
    for item in value['files']:
        name = str(Path(item['new']['path']).relative_to(value['new_repo']))
        m.require(name not in names, 'DUPLICATE_ENDPOINT_SOURCE')
        names.add(name)
        if name in SOURCES:
            m.require(item == value['changes'][name], 'ENDPOINT_CHANGED_SOURCE_NOT_BOUND')
        else:
            m.require(item['parent'] is not None and
                      item['parent']['sha256'] == item['new']['sha256'] and
                      item['parent']['bytes'] == item['new']['bytes'],
                      'UNDECLARED_PHYSICAL_SOURCE_CHANGE')
        if item['parent']:
            m.require(item['parent']['path'] == str(Path(value['old_repo']) / name),
                      'ENDPOINT_SOURCE_RELOCATION_CHANGED')
    m.require(set(SOURCES) <= names, 'ENDPOINT_SOURCE_MISSING')


def _translate(config, value, reverse=False):
    source, target = ('new', 'parent') if reverse else ('parent', 'new')
    replacements = {i[source]['path']: i[target] for i in value['files'] if i[source] and i[target]}
    oldroot, newroot = (value['new_repo'], value['old_repo']) if reverse else (value['old_repo'], value['new_repo'])
    def visit(v):
        if isinstance(v, dict):
            if set(v) == {'path', 'sha256', 'bytes'} and v['path'] in replacements:
                bound = next(i[source] for i in value['files'] if i[source] and i[source]['path'] == v['path'])
                m.require(v == bound, 'ENDPOINT_CONFIG_PIN_CONFLICT')
                return deepcopy(replacements[v['path']])
            return {k: visit(x) for k, x in v.items()}
        if isinstance(v, list):
            return [visit(x) for x in v]
        if isinstance(v, str) and (v == oldroot or v.startswith(oldroot + '/')):
            return newroot + v[len(oldroot):]
        return v
    return visit(config)


def apply(config, identity, base):
    value = load(identity); _inventory(value, base)
    m.require(config['repo'] in (value['old_repo'], value['new_repo']), 'UNEXPECTED_PHYSICAL_REPO')
    m.require(config['endpoint_policy'] in (PARENT_POLICY, POLICY), 'UNEXPECTED_ENDPOINT_POLICY')
    if config['endpoint_policy'] == POLICY:
        m.require(config.get('endpoint_upgrade') == identity and config['repo'] == value['new_repo'],
                  'ENDPOINT_UPGRADE_REBIND_CONFLICT')
    updated = _translate(config, value) if config['repo'] == value['old_repo'] else deepcopy(config)
    updated['endpoint_policy'] = POLICY
    updated['endpoint_upgrade'] = identity
    for item in value['files']:
        if item['new'] not in updated['source_pins']:
            updated['source_pins'].append(item['new'])
    return updated


def normalize_for_comparison(config, identity, base):
    value = load(identity); _inventory(value, base)
    m.require(config['endpoint_policy'] == POLICY and config['repo'] == value['new_repo'] and
              config.get('endpoint_upgrade') == identity, 'UNBOUND_SUCCESSOR_ENDPOINT_POLICY')
    actual_repo_pins = {p['path']: p for p in config['source_pins'] if p['path'].startswith(value['new_repo'] + '/')}
    expected_repo_pins = {i['new']['path']: i['new'] for i in value['files']}
    m.require(actual_repo_pins == expected_repo_pins, 'SUCCESSOR_PHYSICAL_SOURCE_INVENTORY_CHANGED')
    restored = _translate(config, value, reverse=True)
    restored.pop('endpoint_upgrade')
    restored['endpoint_policy'] = PARENT_POLICY
    return restored
