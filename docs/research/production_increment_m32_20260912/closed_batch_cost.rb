#!/usr/bin/env ruby
# Minimal batch4 adaptation of closed_batch3_cost_m29_v1/SOURCE.rb.
# Uses received pins, lightweight cumulative counts, and current formal headers.
# No individual physical RESULT re-read, remote access, admission, or old QA.
require 'json'
require 'digest'
require 'time'
raise 'usage: ruby SOURCE.rb M31_OBSERVATION.json M29_SNAPSHOT.json M30_SNAPSHOT.json' unless ARGV.size==3
def read_pinned(path, expected)
  raw=File.binread(path); sha=Digest::SHA256.hexdigest(raw)
  raise "INPUT_IDENTITY_MISMATCH: #{path}" unless sha==expected
  [JSON.parse(raw),{path:path,sha256:sha,bytes:raw.bytesize}]
end
o,op=read_pinned(ARGV[0],'adae9d180af1df250a5248ef86fc9bf4fa35f9fc823b4156d2caef7521ed9051')
m29,m29p=read_pinned(ARGV[1],'a1370c0ccd81491ea6598ae2ef72d855fa70ce9fd64e86bc73c176a4248dd16f')
m30,m30p=read_pinned(ARGV[2],'375999c3166f6ea38f90f11cae0bf089312322d4f6db3ff6263f9bcda10b79fa')
batch='eucap15_continuous_doe_neighborhood_20260912_batch000004'
s=o.fetch('old_scope'); release=s.fetch('release')
plan=s.fetch('plan').fetch('value')
terminal=s.fetch('batch_terminal').fetch('value')
storage=s.fetch('storage').fetch('value')
[plan.fetch('release'),terminal.fetch('release'),storage.fetch('binding').fetch('release')].each{|p|raise 'RELEASE_BINDING' unless p==release}
raise 'BATCH_NOT_CLOSED' unless terminal.fetch('status')=='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE'
raise 'WRONG_PRIOR_BATCH' unless m29.fetch('batch').fetch('index')==4 && m30.fetch('batch').fetch('id')==batch
raise 'NONCONTIGUOUS_WINDOW' unless m30.fetch('observed_utc')==o.fetch('baseline_utc')
b30=m30.fetch('batch'); delta=s.fetch('new_counts')
raise 'UNEXPECTED_DELTA' unless delta=={'FRESH_EMX_EXTRACTED'=>31,'CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION'=>1}
counts={
 terminal:b30.fetch('terminal')+delta.values.sum,
 fresh:b30.fetch('cumulative_fresh')+delta.fetch('FRESH_EMX_EXTRACTED'),
 analytic_fail_not_dispatched:b30.fetch('cumulative_analytic_not_dispatched'),
 retained_pre_native_rejection:b30.fetch('cumulative_retained_pre_native_rejection'),
 candidate_execution_failure:delta.fetch('CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION')
}
raise 'COUNT_RECONCILIATION' unless counts[:terminal]==terminal.fetch('N_original_requests') &&
 terminal.fetch('results').size==counts[:terminal] &&
 counts.values_at(:fresh,:analytic_fail_not_dispatched,:retained_pre_native_rejection,:candidate_execution_failure).sum==counts[:terminal]
formal=o.fetch('formal_increment').select{|r|r.fetch('request_id').start_with?(batch+'-')}
raise 'DUPLICATE_FORMAL_REQUEST' unless formal.map{|r|r['request_id']}.uniq.size==formal.size
result_requests=terminal.fetch('results').map{|r|File.basename(File.dirname(r.fetch('path')))}
formal.each do |h|
 raise 'HEADER_REQUEST_NOT_BATCH' unless result_requests.include?(h.fetch('request_id'))
 raise 'HEADER_RELEASE' if h['new_result_release'] && h['new_result_release']!=release
end
counts[:formal]=m29.fetch('batch').fetch('formal')+m30.fetch('window').fetch('new_fresh_formal_unique')+formal.size
last_formal=formal.max_by{|r|Time.iso8601(r.fetch('utc'))}
raise 'LAST_FORMAL_RELEASE_MISSING' unless last_formal.fetch('new_result_release')==release
raise 'STILL_STORAGE_RESERVED' unless storage.fetch('remaining_projected_bytes')==0
start=Time.iso8601(plan.fetch('admitted_at_utc'))
physical_end=Time.iso8601(terminal.fetch('utc'))
formal_end=Time.iso8601(last_formal.fetch('utc'))
storage_time=Time.iso8601(storage.fetch('utc'))
raise 'INVALID_TIME_ORDER' unless start<physical_end && physical_end<=formal_end && formal_end<=Time.iso8601(o.fetch('utc'))
result={
 schema:'eucap15_closed_registered_batch_cost.v2',
 status:'DESCRIPTIVE_SINGLE_CLOSED_BATCH_WITH_PRETERMINAL_STORAGE_SAMPLE',
 batch:batch,
 sources:{received_observation:op,reused_m29_counts:m29p,reused_m30_counts:m30p,release:release,
 plan:s.fetch('plan').fetch('pin'),native_batch_terminal:s.fetch('batch_terminal').fetch('pin'),
 last_observed_batch_formal_member:last_formal.fetch('pin'),storage:s.fetch('storage').fetch('pin')},
 scope:'Recorded budget/resource admission to batch terminal and last observed batch formal submission. Pre-admission candidate factory and shared services excluded.',
 admission_utc:plan.fetch('admitted_at_utc'),physical_terminal_utc:terminal.fetch('utc'),
 last_observed_batch_formal_utc:last_formal.fetch('utc'),observation_utc:o.fetch('utc'),
 counts:counts,
 count_derivation:{fresh:'M30 cumulative202 + current batch4 delta31 =233',
 analytical:'M30 cumulative21; no new batch4 analytical failure',
 pre_native:'M30 retained pre-native1 preserved',
 execution_failure:'M31 current batch4 retained candidate failure1; actual_native_starts null, not inferred',
 formal:'M29 batch4 cumulative49 + M30 new39 + M31 batch4 headers18 =106',
 formal_headers_include_prior_test_backfill:1,new_current_batch4_formal_headers:17,
 formal_record_sequence_end:File.basename(last_formal.fetch('pin').fetch('path'),'.json')},
 physical_terminal_elapsed_seconds:physical_end-start,
 formal_submission_elapsed_seconds:formal_end-start,
 last_terminal_to_last_formal_seconds:formal_end-physical_end,
 yields:{formal_per_original:counts[:formal].fdiv(counts[:terminal]),formal_per_fresh:counts[:formal].fdiv(counts[:fresh])},
 terminal_production_accepted_added_field:{source_value:terminal.fetch('production_accepted_added'),
 interpretation:'This wrapper field is not the separate formal ledger count; formal106 uses actual owner header readbacks and previously closed counts.'},
 storage:{
 observed_utc:storage.fetch('utc'),budget_root:storage.fetch('budget_root'),
 allocated_bytes:storage.fetch('total_allocated_bytes'),allocated_gib:storage.fetch('total_allocated_bytes').fdiv(1024**3),
 remaining_projected_bytes:storage.fetch('remaining_projected_bytes'),
 seconds_before_physical_terminal:physical_end-storage_time,
 seconds_before_last_formal:formal_end-storage_time,
 relation_to_terminal:storage_time<physical_end ? 'PRETERMINAL_SAMPLE_NOT_FINAL' : 'AT_OR_AFTER_TERMINAL_SAMPLE',
 sample_allocation_divided_by_formal_count_bytes:storage.fetch('total_allocated_bytes').fdiv(counts[:formal]),
 scope:'Existing whole batch-tree allocated-byte sample includes failures but predates terminal and last formal record. Ratio is sample allocation divided by confirmed formal count, not final per-sample cost.',
 final_batch_tree_allocated_bytes:nil,final_cost_status:'UNKNOWN_NO_POST_CLOSURE_ALLOCATION_SAMPLE',
 incremental_before_after_bytes:nil,peak_bytes:nil,quota:nil,
 excludes:['pre-admission candidate factory','shared source/environment/PDK','formal ledger outside batch tree','historical pool','later outputs/archive copies'],
 cleanup_or_new_du:false
 },
 cost_budget: {elapsed_hours_max:12,storage_ceiling_bytes:storage.fetch('ceiling_bytes'),old_budget_reset:false},
 current_native_concurrency_claim:false,stable_hourly_throughput_claim:false,throughput_per_hour_computed:false,
 controlled_comparison_to_old64:false,projections_to_100k:'NOT_PERFORMED',
 full_campaign_end_to_end_cost:nil,
 full_campaign_cost_missing:['pre-admission candidate-factory duration/cost','post-closure batch allocation sample','shared and formal-ledger storage/CPU cost'],
 actual_physics_rerun:false,existing_qualification_repeated:false,individual_result_bodies_reprocessed:0,new_tests:0,remote_calls:0
}
puts JSON.pretty_generate(result)
