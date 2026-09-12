# Metadata triage only. Current qualification and formal publication are separate.
# jq --slurpfile identity JOINED_ROWS.json -f FIRST100_TRIAGE.jq ARTIFACT_EVIDENCE.json
. as $a
| ($identity[0].rows | map({key:(.source_row_index|tostring), value:.}) | from_entries) as $i
| if (.rows|length)!=100 or ($i|length)!=100 then error("unexpected partition size") else . end
| [.rows[] | . as $r | $i[(.source_row_index|tostring)] as $j
   | if $j==null or $j.evaluation!=$r.evaluation or $j.join_status!="PASS" then error("row identity mismatch") else . end
   | {
       source_row_index, evaluation, merge_source,
       raw_geometry_identity_sha256:$j.historical_audit_row.raw_geometry_identity_sha256,
       production_geometry_fingerprint_sha256:$j.historical_audit_row.production_geometry_fingerprint_sha256,
       category:(if .s4p_header.status=="READ_STABLE" then "B_RAW_RESPONSE_OBSERVED_EVIDENCE_INCOMPLETE"
                 elif .gds.status=="READ_STABLE" then "C_GEOMETRY_FOR_REVALIDATION"
                 else "D_UNDETERMINED" end),
       raw_response_readability_scope:.s4p_header.pin_kind,
       historical_full_s4p_sha256:.s4p_header.historical_full_s4p_sha256,
       current_full_s4p_sha256:.s4p_header.current_full_s4p_sha256,
       current_gds:.gds.pin,
       top_cell:.layout_manifest.value.top_cell,
       summary_geometry_check_skipped:.summary.value.geometry_check.metrics.skipped,
       recorded_command_matches_summary:(.recorded_emx_command==.summary.value.command),
       recorded_command_matches_receipt:(.recorded_emx_command==.emx_command_receipt.value),
       missing_evidence:(.missing_current_qualification + ["S4P current full-body identity/read/validation not performed; only first 8192 bytes observed"]),
       current_formal_eligible:null, formal_added:0
     }
  ] as $rows
| {
    schema:"p215_first100_metadata_triage.v1",
    observed_utc:$a.utc,
    source_export:$a.source_export,
    artifact_evidence_sha256:"f7158ec18d3e5c1f263450db24b0b794b454f9730bd27530ee2197193fbdb0dd",
    scope:"P215 original source rows 0..99, not a representative certification of all historical rows",
    counts:{
      processed_rows:($rows|length),
      A_SUFFICIENT_FOR_CURRENT_REEXTRACTION:0,
      B_RAW_RESPONSE_OBSERVED_EVIDENCE_INCOMPLETE:([$rows[]|select(.category=="B_RAW_RESPONSE_OBSERVED_EVIDENCE_INCOMPLETE")]|length),
      C_GEOMETRY_FOR_REVALIDATION:([$rows[]|select(.category=="C_GEOMETRY_FOR_REVALIDATION")]|length),
      D_UNDETERMINED:([$rows[]|select(.category=="D_UNDETERMINED")]|length),
      p215_source_rows_outside_this_partition:215685,
      current_qualified:null, formally_added:0
    },
    current_gds_unique_bytes:($rows|map(.current_gds.sha256)|unique|length),
    caveats:[
      "Category B proves a readable response header, not a validated full Touchstone body or current scientific eligibility.",
      "Shared multi-topcell GDS files do not imply duplicate geometries; each row retains topcell plus geometry identity.",
      "All first100 historical geometry_check.metrics.skipped=true; an old ok=true is not a current Calibre zero-blocking receipt.",
      "111 vs 56 frequency points alone does not imply incompatibility. Re-extraction must use actual frequencies without interpolation and retain actual SRF evidence.",
      "Existing source-level version evidence may be reused; do not require manual per-SHA approval or rebuild the full P215 index.",
      "No production owner, model, targets, split, S4P, native task or formal record is changed by this offline triage."
    ],
    rows:$rows
  }
