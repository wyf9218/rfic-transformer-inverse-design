# M31 only: two current native scopes plus one explicitly pinned prior pending.
require 'json'
require 'digest'
obs_path, prior_path = ARGV
raise 'OBS SHA' unless Digest::SHA256.file(obs_path).hexdigest == 'adae9d180af1df250a5248ef86fc9bf4fa35f9fc823b4156d2caef7521ed9051'
raise 'prior SHA' unless Digest::SHA256.file(prior_path).hexdigest == 'a19b6db64ad1f2336f75b9bc106c0d9382441378d3e4413c9a10a060ff7cb92e'
d=JSON.parse(File.read(obs_path)); prior=JSON.parse(File.read(prior_path))
raise 'baseline' unless d.fetch('baseline_utc')==prior.fetch('observed_utc')
headers=d.fetch('formal_increment'); by_header=headers.group_by{|h|h.fetch('request_id')}
raise 'header uniqueness' unless by_header.values.all?{|a|a.length==1}
raise 'ledger increment' unless headers.length==20 && d.fetch('formal_added')==20 && d.fetch('formal_count')==6904
raise 'head' unless headers.last['pin']==d.fetch('formal_head')
raise 'sequence' unless headers.map{|h|File.basename(h['pin']['path'],'.json').to_i}==(6885..6904).to_a
prior_by_id=prior.fetch('rows').map{|r|[r.fetch('request_id'),r]}.to_h
expected={'old_scope'=>{'FRESH_EMX_EXTRACTED'=>31,'CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION'=>1},
'new_scope'=>{'FRESH_EMX_EXTRACTED'=>15,'ANALYTIC_FAIL_NOT_DISPATCHED'=>6}}
rows=%w[old_scope new_scope].flat_map do |scope|
  s=d.fetch(scope); entries=s.fetch('new_results')
  raise 'counts' unless s.fetch('new_counts')==expected.fetch(scope)
  raise 'actual counts' unless entries.group_by{|e|e['value']['status']}.transform_values(&:length)==expected.fetch(scope)
  entries.map do |entry|
    r=Marshal.load(Marshal.dump(entry.fetch('value'))); p=r.fetch('original_proposal')
    %w[request_id candidate_id source arm].each{|k|raise "identity #{k}" unless r.fetch(k)==p.fetch(k)}
    raise 'old terminal repeated' if prior_by_id.key?(r.fetch('request_id'))
    raise 'geometry identity' unless r.fetch('candidate_geometry_identity_sha256')==p.fetch('canonical_geometry_sha256')
    %w[release execution_release].each do |k|
      raise "release #{k}" if r.key?(k) && r[k]!=s.fetch('release')
      raise "fresh missing #{k}" if r['status']=='FRESH_EMX_EXTRACTED' && !r.key?(k)
    end
    split=p.fetch('assigned_development_split'); raise 'split' unless %w[train validation test].include?(split)
    f=by_header.fetch(r.fetch('request_id'),[])
    f.each do |h|
      raise 'header release' unless h.fetch('new_result_release')==r.fetch('release')
      raise 'header split' unless h.fetch('assigned_split_from_frozen_request')==split
      raise 'header eligibility' unless r.fetch('core15_eligible') && r.fetch('valid_for_strict_comparison')
    end
    r.merge!('geometry_sha256'=>p.fetch('canonical_geometry_sha256'),'strict'=>r['valid_for_strict_comparison'],
    'assigned_development_split'=>split,'result_pin'=>entry.fetch('pin'),'formal_records'=>f,
    'original_result_accepted_flag'=>r.fetch('production_accepted'))
    missing=%w[valid_for_strict_comparison core15_eligible].reject{|k|r.key?(k)}
    unless missing.empty?
      r['source_missing_flag_fields']=missing
      r['core15_eligible']=nil unless r.key?('core15_eligible')
    end
    %w[geometry geometry_fields geometry_units seed recipe_sha256 target_cell predicted_cell].each{|k|r[k]=p[k] if p.key?(k)}
    r
  end
end
raise 'unique current' unless rows.length==53 && rows.map{|r|r['request_id']}.uniq.length==53 && rows.map{|r|r['geometry_sha256']}.uniq.length==53
current_ids=rows.map{|r|r['request_id']}
old_headers=headers.reject{|h|current_ids.include?(h['request_id'])}
raise 'prior header' unless old_headers.length==1
h=old_headers[0]; request=h.fetch('request_id')
raise 'prior expected' unless request=='eucap15_continuous_doe_neighborhood_20260912_batch000004-GEOMETRY_DOE-156'
backfill=Marshal.load(Marshal.dump(prior_by_id.fetch(request)))
raise 'prior pending eligibility' unless backfill['formal_records']==[] && backfill['strict']==true && backfill['core15_eligible']==true
raise 'prior split' unless h.fetch('assigned_split_from_frozen_request')==backfill.fetch('assigned_development_split') && backfill['assigned_development_split']=='test'
raise 'unexpected prior release header' unless h.key?('new_result_release') && h['new_result_release'].nil?
backfill['original_formal_records']=backfill['formal_records']
backfill['formal_records']=[h]
backfill['backfill_binding_note']='Exact unique request and original test split; current header new_result_release is null and preserved. Prior RESULT release remains pinned; no missing native field fabricated.'
raise 'current core' unless rows.count{|r|r['core15_eligible']==true}==25
admitted=rows.select{|r|!r['formal_records'].empty?}
raise 'current formal' unless admitted.length==19 && admitted.group_by{|r|r['assigned_development_split']}.transform_values(&:length)=={'validation'=>4,'train'=>14,'test'=>1}
doc={'schema'=>'eucap15_received_successor_new_window_view.v1','window_id'=>'M31_new53','feature_order'=>%w[Lp_nH Ls_nH Qmin K_abs],
'observed_utc'=>d.fetch('utc'),'baseline_utc'=>d.fetch('baseline_utc'),
'source'=>{'path'=>obs_path,'sha256'=>Digest::SHA256.file(obs_path).hexdigest,'bytes'=>File.size(obs_path)},
'prior_received_pins'=>[{'path'=>prior_path,'sha256'=>Digest::SHA256.file(prior_path).hexdigest,'bytes'=>File.size(prior_path)}],
'source_reliance'=>'CALLER_VERIFIED_PHYSICAL_SOURCES_AND_FORMAL_JOINS_NOT_REVERIFIED_HERE',
'adaptation'=>'Only new_results in old_scope and new_scope; 19 new exact request/release/split joins plus original DOE156 test backfill. No missing source evidence synthesized.',
'new_terminal'=>53,'new_emx_completed'=>46,'new_strict_valid'=>rows.count{|r|r['strict']==true},'new_strict_range_unique'=>25,
'fresh_formal_this_increment'=>19,'new_core_without_formal'=>6,'prior_pending_formal_backfill'=>1,'ledger_window_fresh_formal'=>20,
'rows'=>rows,'backfilled_prior_rows'=>[backfill]}
text=JSON.pretty_generate(doc)+"\n"
print(ARGV[2] ? text[ARGV[2].to_i,ARGV[3].to_i].to_s : text)
