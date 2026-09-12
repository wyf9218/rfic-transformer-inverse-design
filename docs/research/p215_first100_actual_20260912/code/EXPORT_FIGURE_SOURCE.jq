# True per-row diagnostic table; not fresh EMX or currently qualified samples.
. as $d
| ($targets[0]|map({key:(.source_row_index|tostring),value:.})|from_entries) as $t
| (["source_row_index","evaluation","raw_geometry_identity_sha256","production_geometry_fingerprint_sha256","gds_sha256","s4p_sha256","Lp_nH","Ls_nH","Qp","Qs","Qmin","K_abs","K_signed","conditional_strict","core_range","half_srf_pass","off_grid_vertices","noncanonical_edges","current_qualified","evidence_class"]|@csv),
  (.rows[] | . as $r | $t[(.source_row_index|tostring)] as $p
   | if .evaluation != $p.evaluation or .s4p_sha256 != $p.source.sha256 then error("row binding mismatch") else
       [ .source_row_index,.evaluation,.raw_geometry_identity_sha256,.production_geometry_fingerprint_sha256,
         .gds_sha256,.s4p_sha256,$p.row.lp_nh,$p.row.ls_nh,$p.row.qp,$p.row.qs,$p.row.qmin,$p.row.k_abs,$p.row.signed_k,
         .conditional_strict,.conditional_core_range,($p.row.below_half_srf=="true"),.off_grid_vertices,.noncanonical_edges,
         .current_qualified,"HISTORICAL_EM_RESPONSE_CURRENT_MAPPING_DIAGNOSTIC_NOT_FRESH"]|@csv
     end)
