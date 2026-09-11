"""Reuse the accepted126 rows and inspect only newly closed last2 terminals.

No dispatcher, model, network, old physical-chain replay or plot is invoked.
The original full consumer remains immutable. Its checks are reused with an
internally derived sparse view AFTER the full owner publication is validated.
Only this precisely pinned accepted baseline is authorized as prior evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import io
import json
from pathlib import Path

from . import eucap15_development128_results as existing
from .eucap15_selected_metrics import summarize
from .frequency_physical_statistics import csv_write, save, require
from .io import utc_now

initial = existing.initial
pin, fields = initial.pin, initial.fields
BASE_RECEIPT_SHA = 'bdc061331571924ad8ca49bec02eef4b2a8b4b81542e8f73cc9e400d120716bc'
BASE_QA_SHA = '14ec1067a5bb6aa807b89856f281364523e05282c6381ab757f012088fd1dd03'
BASE_COUNTS = dict(ANALYTIC_FAIL=35, GDS_FAIL=6, EMX_INVALID=37, STRICT_VALID=48, PENDING=2)
CSV_FIELDS = ['request_id', 'candidate_id', 'candidate_geometry_identity_sha256',
    'q_proxy', 'q_emx', 'target', 'grid_proxy', 'state', 'actual', 'strict_joint_hit',
    'touchstone_sha', 'status_detail', 'selected_support_status',
    'selected_outside_support_by_feature', 'original_source_record', 'full11_status',
    'native_result_pin']
JSON_FIELDS = ('target', 'grid_proxy', 'actual', 'selected_outside_support_by_feature',
               'original_source_record', 'native_result_pin')
IMMUTABLE_FIELDS = ('request_id', 'candidate_id', 'candidate_geometry_identity_sha256',
                    'q_proxy', 'q_emx', 'target', 'grid_proxy', 'original_source_record')


class DeltaMirror(existing.Mirror):
    """Reject old candidate-tree reads before opening any physical artifact."""
    def __init__(self, entries, previous, *, protected_roots=()):
        self.old_roots = set(map(Path, protected_roots)) | {
            Path(row['native_result_pin']['path']).parent for row in previous
            if row['native_result_pin'] is not None}
        super().__init__(entries)

    def blocked(self, value):
        return any(Path(value).is_relative_to(root) for root in self.old_roots)

    def check(self, item):
        require(not self.blocked(item['path']),
                'accepted candidate tree must not be re-read')
        require(item['path'] in self.entries, 'unpublished source')
        require(not self.blocked(self.entries[item['path']]['resolved']['path']),
                'resolved path aliases an accepted candidate tree')
        return super().check(item)

    @property
    def paths(self):
        # inspect_features snapshots this dict and requires an exact key before
        # reading. Filtering both endpoints blocks its otherwise direct reads.
        return {p: e['resolved']['path'] for p, e in self.entries.items()
                if not self.blocked(p) and not self.blocked(e['resolved']['path'])}


def read_pinned(item):
    item = initial.pin_shape(item)
    require(pin(item['path']) == item, 'pinned input changed')
    raw = Path(item['path']).read_bytes()
    require(len(raw) == item['bytes'] and existing.hashlib.sha256(raw).hexdigest() == item['sha256'],
            'input changed while reading')
    return raw


def parse_rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8'), newline=''))
    require(reader.fieldnames == CSV_FIELDS, 'accepted row schema differs')
    rows = []
    for source in reader:
        require(None not in source and all(v is not None for v in source.values()), 'malformed CSV row')
        row = dict(source)
        for name in JSON_FIELDS:
            row[name] = existing._strict_json(source[name]) if source[name] else None
        require(source['q_proxy'] in [str(q) for q in range(10, 21)], 'invalid original Q')
        row['q_proxy'] = int(source['q_proxy'])
        require(source['q_emx'] == '', 'no post-hoc EMX-selected Q permitted')
        row['q_emx'] = None
        require(source['strict_joint_hit'] in ('', 'True', 'False'), 'invalid saved boolean')
        row['strict_joint_hit'] = {'': None, 'True': True, 'False': False}[source['strict_joint_hit']]
        row['touchstone_sha'] = source['touchstone_sha'] or None
        rows.append(row)
    require(len(rows) == 128 and len({r['request_id'] for r in rows}) == 128, 'exact unique128 required')
    return rows


def load_baseline(receipt_path, qa_path):
    receipt_pin, qa_pin = pin(receipt_path), pin(qa_path)
    require(receipt_pin['sha256'] == BASE_RECEIPT_SHA and qa_pin['sha256'] == BASE_QA_SHA,
            'only the independently accepted126 baseline may be reused')
    receipt = existing._strict_json(read_pinned(receipt_pin))
    qa = existing._strict_json(read_pinned(qa_pin))
    fields(qa, dict(schema='independent_development128_statistics_qa.v1',
        status='GO_SCOPED_DESCRIPTIVE_DEVELOPMENT128', source_result_receipt=receipt_pin,
        FINAL=False, N_terminal=126, N_joint_hit=40), 'accepted physical QA')
    fields(receipt, dict(schema='eucap15_development128_result_receipt.v1',
        status='PASS_PUBLISHED_SNAPSHOT_ACCOUNTING_NOT_FINAL', N_original_requests=128,
        FINAL=False), 'accepted result receipt')
    artifacts = {Path(p['path']).name: p for p in receipt['artifacts']}
    require(len(artifacts) == len(receipt['artifacts']), 'duplicate baseline artifact name')
    row_pin, summary_pin = artifacts['REQUEST_RESULTS.csv'], artifacts['SUMMARY.json']
    rows = parse_rows(read_pinned(row_pin))
    summary = existing._strict_json(read_pinned(summary_pin))
    require(Counter(r['state'] for r in rows) == Counter(BASE_COUNTS), 'accepted state population differs')
    require(sum(r['native_result_pin'] is not None for r in rows) == 126, '126 accepted terminals required')
    fields(summary, dict(N_original_requests=128, N_owner_terminal_receipts_consumed=126,
        N_joint_hit=40, model_id=initial.MODEL_ID, FINAL=False), 'accepted summary')
    closure_pin = artifacts['SOURCE_CLOSURE.json']
    closure = existing._strict_json(read_pinned(closure_pin))
    # Use previously accepted resolutions, not a new snapshot's claimed aliases.
    bindings = {}
    for entry in closure['native_sources']:
        original = initial.pin_shape(entry['original'])
        resolved = initial.pin_shape(entry['resolved'])
        require(original['sha256'] == resolved['sha256'] and original['bytes'] == resolved['bytes'],
                'accepted mirror changed bytes')
        require(original['path'] not in bindings or bindings[original['path']] == entry,
                'conflicting accepted mirror mapping')
        bindings[original['path']] = entry
    protected = set()
    for row in rows:
        old = row['native_result_pin']
        if old is None:
            continue
        require(old['path'] in bindings and bindings[old['path']]['original'] == old,
                'accepted terminal lacks its prior resolution')
        protected.add(str(Path(old['path']).parent))
        protected.add(str(Path(bindings[old['path']]['resolved']['path']).parent))
    return rows, [receipt_pin, qa_pin, row_pin, summary_pin, closure_pin], sorted(protected)


def sparse_new_view(previous, snapshot):
    """Compare metadata only; a previous terminal can never vanish or change."""
    entries = snapshot['requests']
    require(len(entries) == len(previous) == 128, 'full original128 observation required')
    delta = deepcopy(snapshot)
    delta['batch'] = None  # individual proofs establish rows; do not reopen old batch results
    new_ids = []
    for old, entry, sparse in zip(previous, entries, delta['requests']):
        require(set(entry) == {'request_id', 'result'} and entry['request_id'] == old['request_id'],
                'original request order or identity changed')
        before, after = old['native_result_pin'], entry['result']
        if before is not None:
            require(after == before, 'accepted terminal withdrawn or replaced')
            sparse['result'] = None
        else:
            require(old['state'] == 'PENDING', 'unproved nonpending prior row')
            if after is not None:
                initial.pin_shape(after)
                new_ids.append(old['request_id'])
    require(0 < len(new_ids) <= 2, 'no new last2 terminal; do not rerun the accepted baseline')
    return delta, new_ids


def merge_rows(previous, inspected, new_ids):
    require(len(previous) == len(inspected) == 128 and len(set(new_ids)) == len(new_ids),
            'invalid merge shape')
    new = set(new_ids)
    require(new <= {r['request_id'] for r in previous}, 'unknown delta ID')
    merged = []
    for old, checked in zip(previous, inspected):
        fields(checked, {key: old[key] for key in IMMUTABLE_FIELDS}, 'unchanged selected identity')
        if old['request_id'] in new:
            require(old['state'] == 'PENDING' and old['native_result_pin'] is None,
                    'only a pending row can advance')
            require(checked['native_result_pin'] is not None and checked['state'] != 'PENDING',
                    'new result did not establish a terminal')
            merged.append(checked)
        else:
            merged.append(deepcopy(old))
    return merged


def run(receipt_path, qa_path, out, *, snapshot_path=None, snapshot_sha256=None):
    out = initial.path(out)
    require(out.parent.is_dir() and not out.is_relative_to(Path(__file__).resolve().parents[2]),
            'existing separate research output parent required')
    out.mkdir(exist_ok=False)  # keep outside catch: never append FAIL into an existing delivery
    try:
        previous, sources, protected_roots = load_baseline(receipt_path, qa_path)
        intent = dict(created_utc=utc_now(), baseline=sources, native_calls=0, model_loads=0,
                      old_native_result_or_feature_reads=0, old_chain_QA_reruns=0)
        save(out/'INTENT.json', intent)
        if snapshot_path is None:
            require(snapshot_sha256 is None, 'snapshot SHA without snapshot')
            report = dict(status='BASELINE_REUSE_PREFLIGHT_PASS_NOT_NEW_PHYSICAL_RESULTS',
                N_previous_terminal=126, N_pending=2,
                pending_ids=[r['request_id'] for r in previous if r['state'] == 'PENDING'],
                new_terminal_results_consumed=0, REAL_EMX_VALIDATION='NOT_RUN_IN_THIS_PREFLIGHT')
        else:
            snapshot_pin = pin(snapshot_path)
            require(snapshot_pin['sha256'] == snapshot_sha256, 'explicit snapshot SHA differs')
            snapshot = existing._strict_json(read_pinned(snapshot_pin))
            # Candidate-context pins come from the accepted original INTENT, itself receipt-bound.
            base_receipt = existing._strict_json(read_pinned(sources[0]))
            # Reuse the accepted chain implementation, not merely its module name.
            for implementation_pin in base_receipt['implementation']:
                read_pinned(implementation_pin)
            sources += base_receipt['implementation']
            original_intent_pin = next(p for p in base_receipt['artifacts'] if Path(p['path']).name == 'INTENT.json')
            original_intent = existing._strict_json(read_pinned(original_intent_pin))
            require(original_intent['manifest']['sha256'] == existing.MANIFEST_SHA and
                    original_intent['qa']['sha256'] == existing.QA_SHA, 'original pilot binding changed')
            ctx = initial.load_context(original_intent['manifest'], original_intent['qa'])
            mirror = DeltaMirror(snapshot['sources'], previous, protected_roots=protected_roots)
            existing.validate_export(ctx, snapshot, mirror)
            sparse, new_ids = sparse_new_view(previous, snapshot)
            old_terminal_paths = {r['native_result_pin']['path'] for r in previous if r['native_result_pin']}
            inspected, closures = existing.consume(ctx, sparse, mirror)
            require(not (old_terminal_paths & set(mirror.used)), 'old terminal was re-read')
            merged = merge_rows(previous, inspected, new_ids)
            result = summarize(merged, score_spans=ctx.freeze['score_scale'],
                               tolerances=ctx.freeze['absolute_tolerances'])
            report = result['summary']
            report.update(status='PASS_LAST2_INCREMENT_REUSING_ACCEPTED126_NOT_FINAL',
                N_previous_terminal=126, new_terminal_results_consumed=len(new_ids),
                N_owner_terminal_receipts_consumed=126 + len(new_ids),
                new_ids=new_ids, snapshot_utc=snapshot['snapshot_utc'],
                model_id=initial.MODEL_ID, dataset_scope=initial.SCOPE,
                REAL_EMX_VALIDATION='CLOSED_PUBLISHED_INCREMENT_WITH_ACCEPTED_PRIOR_RESULTS',
                pending_semantics='Frozen published observation, not live process status')
            csv_write(out/'REQUEST_RESULTS.csv', merged, fields=CSV_FIELDS)
            csv_write(out/'PHYSICAL_METRICS.csv', result['metric_rows'])
            csv_write(out/'ERROR_ECDF.csv', result['ecdf_rows'], fields=initial.ECDF_FIELDS)
            save(out/'SOURCE_CLOSURE.json', dict(baseline=sources, snapshot=snapshot_pin,
                delta_terminal_closures=closures, native_sources=list(mirror.used.values()),
                original_context=ctx.sources, old_native_result_or_feature_reads=0,
                batch_receipt_not_reopened=True))
            sources += [snapshot_pin, original_intent_pin] + ctx.sources
            mirror.recheck()
        report.update(FINAL=False, native_calls=0, model_loads=0, new_training_updates=0,
                      old_native_result_or_feature_reads=0, figures_created=0)
        save(out/'SUMMARY.json', report)
        for source in sources:
            require(pin(source['path']) == source, 'source changed before publication')
        artifacts = [pin(p) for p in sorted(out.iterdir()) if p.is_file()]
        save(out/'RECEIPT.json', dict(schema='eucap15_last2_increment_receipt.v1',
            status=report['status'], completed_utc=utc_now(), source_pins=sources,
            implementation=[pin(Path(__file__)), pin(Path(existing.__file__)),
                pin(Path(initial.__file__)), pin(Path(summarize.__code__.co_filename))],
            artifacts=artifacts, new_terminal_results_consumed=report['new_terminal_results_consumed'],
            native_calls=0, old_native_result_or_feature_reads=0, FINAL=False))
        with (out/'SHA256SUMS').open('x') as stream:
            for item in artifacts + [pin(out/'RECEIPT.json')]:
                stream.write(item['sha256'] + '  ' + Path(item['path']).name + '\n')
        return report
    except Exception as error:
        save(out/'FAILURE_RECEIPT.json', dict(status='NO_GO_PRESERVED', error=repr(error),
            created_utc=utc_now(), native_calls=0, new_training_updates=0))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-receipt', type=Path, required=True)
    parser.add_argument('--baseline-qa', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--snapshot-sha256')
    args = parser.parse_args(argv)
    report = run(args.baseline_receipt, args.baseline_qa, args.out,
        snapshot_path=args.snapshot, snapshot_sha256=args.snapshot_sha256)
    print(json.dumps(report, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
