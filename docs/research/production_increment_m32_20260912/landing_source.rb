#!/usr/bin/env ruby
# M32-only caller-verified native projection; reuses prior increment contract.
require 'json'
require 'digest'
obs_path,prior_path=ARGV
raise 'OBS SHA' unless Digest::SHA256.file(obs_path).hexdigest=='285342a83037ab7f188e3d6947f4d381d458e609b46b3a12306e76e5f7627060'
raise 'PRIOR SHA' unless Digest::SHA256.file(prior_path).hexdigest=='ce4dc44f409902b117a9b6fe68e1ac7a6617dffa7d4f61f30fa8c5d432b1ff04'
d=JSON.parse(File.read(obs_path));prior=JSON.parse(File.read(prior_path))
raise 'TIME' unless d['baseline_utc']==prior['observed_utc']
raise 'SCOPE' unless d['new_scope'].nil?
s=d.fetch('old_scope'); headers=d.fetch('formal_increment')
byh=headers.group_by{|h|h.fetch('request_id')};raise 'HEADER DUPLICATE' unless byh.values.all?{|a|a.size==1}
raise 'FORMAL WINDOW' unless headers.size==78 && d['formal_added']==78 && d['formal_count']==6982
raise 'HEAD' unless headers.last['pin']==d['formal_head']
raise 'HEADER SEQUENCE' unless headers.map{|h|File.basename(h['pin']['path'],'.json').to_i}==(6905..6982).to_a
priorby=prior.fetch('rows').map{|r|[r['request_id'],r]}.to_h
entries=s.fetch('new_results')
expected={'FRESH_EMX_EXTRACTED'=>151,'ANALYTIC_FAIL_NOT_DISPATCHED'=>17}
raise 'NEW COUNTS' unless s['new_counts']==expected && entries.group_by{|e|e['value']['status']}.transform_values(&:size)==expected
rows=entries.map do |e|
 r=Marshal.load(Marshal.dump(e.fetch('value')));p=r.fetch('original_proposal')
 %w[request_id candidate_id source arm].each{|k|raise "IDENTITY #{k}" unless r[k]==p[k]}
 raise 'PRIOR REPEATED' if priorby.key?(r['request_id'])
 raise 'GEOMETRY' unless r['candidate_geometry_identity_sha256']==p['canonical_geometry_sha256']
 %w[release execution_release].each do |k|
  raise "RELEASE #{k}" if r.key?(k) && r[k]!=s['release']
  raise "FRESH RELEASE MISSING #{k}" if r['status']=='FRESH_EMX_EXTRACTED' && !r.key?(k)
 end
 split=p.fetch('assigned_development_split');raise 'SPLIT' unless %w[train validation test].include?(split)
 f=byh.fetch(r.fetch('request_id'),[])
 f.each do |h|
  raise 'FORMAL RELEASE' unless h.fetch('new_result_release')==r.fetch('release')
  raise 'FORMAL SPLIT' unless h.fetch('assigned_split_from_frozen_request')==split
  raise 'FORMAL ELIGIBILITY' unless r.fetch('core15_eligible') && r.fetch('valid_for_strict_comparison')
 end
 r.merge!('geometry_sha256'=>p.fetch('canonical_geometry_sha256'),'strict'=>r['valid_for_strict_comparison'],
 'assigned_development_split'=>split,'result_pin'=>e.fetch('pin'),'formal_records'=>f,
 'original_result_accepted_flag'=>r.fetch('production_accepted'))
 missing=%w[valid_for_strict_comparison core15_eligible].reject{|k|r.key?(k)}
 unless missing.empty?
  r['source_missing_flag_fields']=missing
  r['core15_eligible']=nil unless r.key?('core15_eligible')
 end
 %w[geometry geometry_fields geometry_units seed recipe_sha256 target_cell predicted_cell].each{|k|r[k]=p[k] if p.key?(k)}
 r
end
raise 'UNIQUE CURRENT' unless rows.size==168 && rows.map{|r|r['request_id']}.uniq.size==168 && rows.map{|r|r['geometry_sha256']}.uniq.size==168
ids=rows.map{|r|r['request_id']}
oldh=headers.reject{|h|ids.include?(h['request_id'])}
priorpending=prior.fetch('rows').select{|r|r['formal_records']==[] && r['strict']==true && r['core15_eligible']==true}
raise 'EXACT PRIOR PENDING SET' unless oldh.size==6 && oldh.map{|h|h['request_id']}.sort==priorpending.map{|r|r['request_id']}.sort
backfill=oldh.map do |h|
 r=Marshal.load(Marshal.dump(priorby.fetch(h.fetch('request_id'))))
 raise 'PRIOR PENDING' unless r['formal_records']==[] && r['strict']==true && r['core15_eligible']==true
 raise 'PRIOR SPLIT' unless h.fetch('assigned_split_from_frozen_request')==r.fetch('assigned_development_split')
 raise 'PRIOR RELEASE CONFLICT' if h['new_result_release'] && h['new_result_release']!=r['release']
 r['original_formal_records']=r['formal_records'];r['formal_records']=[h]
 r['backfill_binding_note']='Exact six pinned M31 pending requests and original splits; null current-header release preserved, prior RESULT release retained, no missing evidence synthesized.'
 r
end
raise 'BACKFILL SPLITS' unless backfill.group_by{|r|r['assigned_development_split']}.transform_values(&:size)=={'train'=>4,'test'=>2}
core=rows.select{|r|r['core15_eligible']==true}
raise 'CORE' unless core.size==72 && core.all?{|r|r['formal_records'].size==1}
raise 'CURRENT SPLITS' unless core.group_by{|r|r['assigned_development_split']}.transform_values(&:size)=={'validation'=>20,'train'=>39,'test'=>13}
doc={'schema'=>'eucap15_received_successor_new_window_view.v1','window_id'=>'M32_new168','feature_order'=>%w[Lp_nH Ls_nH Qmin K_abs],
'observed_utc'=>d['utc'],'baseline_utc'=>d['baseline_utc'],
'source'=>{'path'=>obs_path,'sha256'=>Digest::SHA256.file(obs_path).hexdigest,'bytes'=>File.size(obs_path)},
'prior_received_pins'=>[{'path'=>prior_path,'sha256'=>Digest::SHA256.file(prior_path).hexdigest,'bytes'=>File.size(prior_path)}],
'source_reliance'=>'CALLER_VERIFIED_PHYSICAL_SOURCES_AND_FORMAL_JOINS_NOT_REVERIFIED_HERE',
'adaptation'=>'Only168 new native RESULTs;72 exact new formal request/release/split joins and six exact prior pending formal joins. Original evidence and failure fields preserved.',
'new_terminal'=>168,'new_emx_completed'=>151,'new_strict_valid'=>rows.count{|r|r['strict']==true},'new_strict_range_unique'=>72,
'fresh_formal_this_increment'=>72,'new_core_without_formal'=>0,'prior_pending_formal_backfill'=>6,'ledger_window_fresh_formal'=>78,
'rows'=>rows,'backfilled_prior_rows'=>backfill}
text=JSON.pretty_generate(doc)+"\n";print(ARGV[2] ? text[ARGV[2].to_i,ARGV[3].to_i].to_s : text)
