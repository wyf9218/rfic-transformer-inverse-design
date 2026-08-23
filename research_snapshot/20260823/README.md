# 2026-08-23 Physical-Chain Provenance Snapshot

This directory contains selected, sanitized production-source snapshots that document how the historical-200k fixed10k physical chain was audited, gated, and prepared for fresh EMX statistics.

It is **not a standalone production environment**. Some scripts import modules, site tools, simulator binaries, data manifests, or sibling programs that are intentionally absent from this public-safe repository. Use it for review, provenance, and porting only. Do not run it against a foundry environment without rebuilding an explicit site contract.

## Files and original production SHA-256

| snapshot file | original SHA-256 | purpose |
|---|---|---|
| `physical_chain/audit_historical_200k_fixed10k_emx_readiness.py` | `a977569f603571f65869a17623b41d87737c37d4b71fc232d749eee2105fad84` | readiness gate before physical execution |
| `physical_chain/audit_historical_200k_cadence_streamout_results.py` | `24d29b6e2e562076e7a86c7420a8d9d3582060cd82dc256e148d17835ca68632` | Cadence/stream-out result audit |
| `physical_chain/freeze_historical_200k_calibre_pass_queue_zero_safe.py` | `676571897e1725a4491a1284f4ef3228aa5552e7415e00e56d3898c1191e4c80` | freezes Calibre-pass queue with zero-safe behavior |
| `physical_chain/run_candidate_bound_existing_gds_fresh_emx.py` | `85ce393fec9b3b9300376672ed3cfd18013e143ff44499b0585a8395501e1c6e` | candidate-bound fresh-EMX runner |
| `physical_chain/build_historical_200k_fixed10k_fresh_emx_statistics_v2_zero_safe.py` | `46dd3dc9230c5b1d334eee49ea7c33597f87a3661d279743d4bb85c71604793e` | survivor-aware EMX statistics builder |
| `physical_chain/test_historical_200k_statistics_v2_zero_safe_smoke.py` | `4a080ecde2008691e9301879ea85688c5b798287bf06afe0dfe7cf134e15e648` | smoke test for zero-safe statistics behavior |
| `physical_chain/prepare_current_foundry_domain_pilot.py` | `b8b8d00dc2fb53f1eac7306f90847785f822c1ffff0c12ae638bf3a15bc22a9f` | production dependency snapshot |
| `physical_chain/current_foundry_one_shot_config_identity.py` | `337bccd3e99388bbc3bae9f7d265592b7f0050d500f9ff734dab173563534685` | configuration-identity helper |
| `physical_chain/run_high_k_q_overlap_stage2_supervisor.py` | `9f6c6e4f475984096598973614883040912f8d5525676ea7a0f1bbee89abf590` | staged supervisor logic |
| `physical_chain/feasibility.py` | `f0745b44edf75b0a0b81fb3aeff8aaa86e7e7ca964a13e0375bd13e4dbb94813` | analytical feasibility helper |
| `physical_chain/gds_hash.py` | `1a4bd4f410691421891d223b4f981aaef3d74cb543058c640ce44c10024a9f84` | GDS identity helper |

## Sanitization changes

- Removed the production hostname and foundry-process-token defaults from the Calibre queue freezer; both are now explicit required inputs.
- No usernames, server paths, license endpoints, PDK paths, data roots, credentials, or real artifacts are included.
- The controlled paired-training wrapper outside this directory similarly uses `sys.executable` and an optional explicit host contract instead of production-specific defaults.
- Algorithmic logic was otherwise retained; original hashes above identify the private source from which each copy was derived.

## Evidence boundary

The snapshot documents code identity but does not itself prove the reported scientific numbers. Those numbers remain bound to offline receipts, manifests, summaries, and artifact hashes listed in `docs/research/EVIDENCE_SHA256_20260823.txt`. A future rerun must rebuild all input identities and cannot treat this directory as a validated simulator installation.
