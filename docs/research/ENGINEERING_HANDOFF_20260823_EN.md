# RFIC Transformer AI Inverse Modeling: Research Handoff (2026-08-23)

This is the primary English handoff for a future researcher or GPT. The repository is a sanitized code and evidence-state snapshot. It excludes real EMX datasets, model weights, GDS, PDK material, licenses, credentials, server identities, and private run paths.

## Executive status

Four of eight milestones are complete, one is actively running, and three are pending or blocked by formal gates. The historical 100k/200k model structures are verified; proxy evaluation on a frozen 10,000-target frame is complete; and the physical flow produced 7,298 successful fresh-EMX survivors. The controlled nested 100k/200k experiment is not yet complete, and the final EMX statistics/charts cannot be released until the reporting interface passes independent QA.

## Model identities

The labels 100k and 200k refer to source-table rows, not to rows used for gradient updates.

| Field | deployed 100k | historical 200k |
|---|---:|---:|
| source rows | 100,000 | 200,000 |
| gradient-training rows | 78,891 | 161,446 |
| validation rows | 9,740 | 19,135 |
| test rows | 11,369 | 19,419 |
| inverse network | 4→512→512→256→10 | 4→128→128→10 |
| forward network | 10→256→256→128→4 | 10→128→128→4 |
| total parameters | 501,134 | 36,878 |
| decoder | hard-feasible with Q guardband | independent sigmoid |

This is an uncontrolled descriptive comparison because the architectures, decoders, data sources, and training contracts differ. It cannot establish that a larger dataset caused an improvement.

## Frozen 10,000-target frame

The frame is a deterministic centered Latin-hypercube finite-coverage design with seed `20260810`: 8,000 legacy targets at `|K|≤0.8` and 2,000 extension targets at `|K|>0.8`. It is neither an iid random sample nor 10,000 EMX labels. Target SHA-256: `c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407`.

| panel | deployed 100k joint proxy RMSE | historical 200k joint proxy RMSE | relative reduction |
|---|---:|---:|---:|
| all 10,000 | 0.245843 | 0.202643 | 17.57% |
| legacy 8,000 | 0.221335 | 0.184191 | 16.78% |
| extension 2,000 | 0.325940 | 0.263850 | 19.05% |

All-10k physical MAEs for 100k versus 200k were: `Lp` 0.44947/0.24884 nH, `Ls` 0.39612/0.30137 nH, `Q` 4.43261/3.92985, and `|K|` 0.17840/0.14731. However, Q-target attainment was 37.83% versus 29.84%, so the 200k model was not uniformly better.

The historical 0.925% test result belongs to a different held-out-manifold, 40×4-refinement, `|K|≤0.8` task. It must not be presented as the same accuracy measure as the one-shot fixed10k evaluation.

## Fresh-EMX physical funnel

The fixed targets passed through the following non-random selection funnel:

`10,000 targets → 7,926 analytically feasible → 7,373 Cadence → 7,298 Calibre → 7,298 fresh EMX`.

The final survivor set contains 5,992 legacy and 1,306 extension cases. Stage06 is terminal PASS with 7,298/7,298 S4P files and no failed shards. Because the 2,702 losses are missing-not-at-random, survivor accuracy cannot be extrapolated to all 10,000 targets. Formal reporting must separate target→EMX, target→proxy, proxy→EMX, and stage-failure rates, with explicit denominators.

## Controlled data-scale experiment

The nested paired 100k/200k experiment freezes architecture, decoder, source, split, training budget, seeds, and inference. The last read-only state had 7/10 terminal arms and 3/5 complete seed pairs. This is the only experiment authorized to support a causal statement about data-scale improvement.

## Formal NO-GO evidence

- RQ-I fixed10k release v7: NO-GO, severity counts P0/P1/P2/P3=`0/2/0/0`.
- Report-interface compatibility v7: NO-GO, `0/3/0/0`; it allowed stale hashes, role aliasing, and an invalid all-10k extrapolation narrative.
- Monday report partial v3: visual NO-GO.
- Successor v8/v4 directories are unvalidated work in progress, not accepted fixes.

See [KNOWN_NO_GO_20260823.md](KNOWN_NO_GO_20260823.md) for the exact failure contracts.

## Milestones

| # | milestone | state |
|---:|---|---|
| 1 | historical model architecture evidence | COMPLETE |
| 2 | frozen 10,000-target generation | COMPLETE |
| 3 | historical proxy comparison | COMPLETE |
| 4 | physical funnel and 7,298 fresh-EMX runs | COMPLETE |
| 5 | controlled nested 100k/200k training | RUNNING (7/10 arms, 3/5 pairs) |
| 6 | formal fresh-EMX statistics and charts | BLOCKED BY INTERFACE NO-GO |
| 7 | final Monday HTML/PPTX | NOT FINALIZED |
| 8 | current-contract 200k/300k/400k/500k, `|K|<1` learning curve | NOT COMPLETE |

## Next valid actions

1. Let the existing supervisor finish the remaining paired arms without duplicate manual launches.
2. Fix the release/report-interface P1 findings in new no-clobber successor directories and repeat independent QA.
3. After a GO, publish survivor-conditioned EMX tables, intervals, and bar charts together with the full 10,000-target funnel.
4. Publish paired statistics after all five controlled seed pairs complete.
5. Then run the current-contract 200k/300k/400k/500k learning curve at `|K|<1`, holding the test frame and training contract fixed and recording compute time.
6. Finalize the Monday deck only from accepted evidence.

Read [HANDOFF_STATE_20260823.json](HANDOFF_STATE_20260823.json), [CODE_MAP_20260823.md](CODE_MAP_20260823.md), the reproducibility contract, and the repository manifest before making new claims.
