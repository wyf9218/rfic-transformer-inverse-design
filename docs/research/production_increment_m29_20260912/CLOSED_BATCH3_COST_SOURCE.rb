#!/usr/bin/env ruby
# Read-only derivation from one already-received closed-batch observation and
# its previously published qualification count. No native or ledger operations.
require 'json'
require 'digest'
require 'time'
raise 'usage: ruby SOURCE.rb OBSERVATION.json M27_SNAPSHOT.json' unless ARGV.size == 2
def read_pinned(path, expected)
  bytes = File.binread(path)
  actual = Digest::SHA256.hexdigest(bytes)
  raise "INPUT_IDENTITY_MISMATCH: #{path}" unless actual == expected
  [JSON.parse(bytes), {path: path, sha256: actual, bytes: bytes.bytesize}]
end
o, op = read_pinned(ARGV[0], 'cc8b03cf51a7402218fcf1c56f7db80eff2e2e24d00fd20d5b7aaba21773ca3a')
prior, pp = read_pinned(ARGV[1], 'ac0cf36c63b2b344645320696e91002865a1b8507097c15ff4b6b0eef2dbe405')
s = o.fetch('old_scope')
plan = s.fetch('plan').fetch('value')
terminal = s.fetch('batch_terminal').fetch('value')
storage = s.fetch('storage').fetch('value')
counts = prior.fetch('failure_preserved').fetch('original_batch3')
release = s.fetch('release')
[plan.fetch('release'), terminal.fetch('release'), storage.fetch('binding').fetch('release')].each do |p|
  raise 'RELEASE_BINDING_MISMATCH' unless p == release
end
raise 'NOT_CLOSED' unless terminal.fetch('status') == 'ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE'
raise 'COUNT_RECONCILIATION' unless counts.fetch('terminal') == terminal.fetch('N_original_requests') &&
  counts.fetch('fresh') + counts.fetch('analytic_fail') == counts.fetch('terminal') &&
  terminal.fetch('results').size == counts.fetch('terminal')
formal = o.fetch('formal_increment').select do |r|
  r.fetch('request_id').start_with?('eucap15_continuous_doe_neighborhood_20260912_batch000003-')
end
last_formal = formal.max_by {|r| Time.iso8601(r.fetch('utc'))}
raise 'LAST_FORMAL_HEAD_NOT_BOUND' unless last_formal.fetch('pin') == o.fetch('formal_head')
raise 'STILL_STORAGE_RESERVED' unless storage.fetch('remaining_projected_bytes') == 0
start = Time.iso8601(plan.fetch('admitted_at_utc'))
end_physical = Time.iso8601(terminal.fetch('utc'))
end_formal = Time.iso8601(last_formal.fetch('utc'))
raise 'INVALID_TIME_ORDER' unless start < end_physical && end_physical <= end_formal
seconds = end_formal - start
result = {
  schema: 'eucap15_closed_registered_batch_cost.v1',
  status: 'DESCRIPTIVE_SINGLE_COMPLETED_BATCH_NOT_STEADY_STATE',
  batch: 'eucap15_continuous_doe_neighborhood_20260912_batch000003',
  sources: {received_observation: op, reused_published_counts: pp,
    release: release, plan: s.fetch('plan').fetch('pin'),
    native_batch_terminal: s.fetch('batch_terminal').fetch('pin'),
    final_formal_member: last_formal.fetch('pin'), storage: s.fetch('storage').fetch('pin')},
  scope: 'Budget admission through final physical terminal and final formal record; candidate-factory time before admission is excluded.',
  start_utc: plan.fetch('admitted_at_utc'), physical_terminal_utc: terminal.fetch('utc'),
  final_formal_utc: last_formal.fetch('utc'), counts: counts,
  physical_terminal_elapsed_seconds: end_physical - start,
  formal_closure_elapsed_seconds: seconds,
  last_terminal_to_last_formal_seconds: end_formal - end_physical,
  observed_batch_rates_not_stable_hourly: {
    fresh_per_hour_over_formal_window: counts.fetch('fresh') * 3600.0 / seconds,
    formally_qualified_unique_per_hour_over_formal_window: counts.fetch('formal') * 3600.0 / seconds
  },
  yields: {formal_per_original: counts.fetch('formal').fdiv(counts.fetch('terminal')),
    formal_per_fresh: counts.fetch('formal').fdiv(counts.fetch('fresh'))},
  storage: {
    observed_utc: storage.fetch('utc'), budget_root: storage.fetch('budget_root'),
    allocated_bytes: storage.fetch('total_allocated_bytes'),
    allocated_gib: storage.fetch('total_allocated_bytes').fdiv(1024**3),
    amortized_batch_allocated_bytes_per_formal_member: storage.fetch('total_allocated_bytes').fdiv(counts.fetch('formal')),
    scope: 'Entire batch tree at near-terminal snapshot, including failed-candidate cost. Not an incremental before/after measurement or exact per-member footprint.',
    excludes: ['shared source/environment/PDK', 'global formal ledger outside batch tree', 'historical pool', 'future archive copies'],
    peak_or_quota_claim: false
  },
  current_native_concurrency_claim: false,
  stable_hourly_throughput_claim: false,
  full_campaign_end_to_end_cost: nil,
  full_campaign_cost_missing: ['pre-admission candidate-factory duration/cost', 'shared and formal-ledger allocated storage', 'multi-batch sustainable resource availability'],
  projections_to_100k: 'NOT_PERFORMED',
  actual_physics_rerun: false, existing_qualification_repeated: false, new_tests: 0
}
puts JSON.pretty_generate(result)
