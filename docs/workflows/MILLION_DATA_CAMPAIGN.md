# Cumulative Real-EM Campaign Protocol

This document records the long-horizon cumulative campaign contract represented by the repository. It is a protocol, not evidence that one million accepted simulations have completed.

## Current priority versus long-horizon target

The current supervisor-facing priority is a controlled 200k/300k/400k/500k learning curve with the physical target domain extended to `|K|<1`. The repository also retains code for an older long-horizon plan up to one million accepted real-EM rows. These goals must not be conflated:

- 200k/300k/400k/500k is the current requested learning-curve scope;
- 1M is a retained campaign capability and future target;
- neither row count may be called complete without a terminal accepted-pool receipt.

## Cumulative checkpoint contract

1. Labels must come from parseable real EM Touchstone outputs.
2. Every accepted row must bind geometry identity, simulator configuration, source artifact hash, physical features, and acceptance result.
3. Checkpoints are cumulative prefixes, not independently resampled tables.
4. The fixed evaluation target/test frame must not be used for gradient updates or checkpoint selection.
5. Architecture, decoder, split, optimizer budget, seeds, and inference must be frozen when attributing a change to data size.
6. Every checkpoint records source-table, gradient-training, validation, and test row counts separately.
7. Wall time, optimizer updates, real-row draws, and hardware/runtime identity must be recorded.
8. Failed shards and rejected candidates remain in the campaign denominator audit.

## Coverage contract

Uniform marginal histograms are insufficient. Each cumulative checkpoint should audit:

- one-dimensional marginals for `Lp`, `Ls`, `Q`, and `|K|`;
- pairwise support;
- occupied four-dimensional cells and entropy;
- boundary and high-`|K|` coverage;
- geometry diversity and duplicate identities;
- reachability versus the frozen target frame.

Acquisition may improve coverage, but surrogate predictions may rank candidates only; they cannot become real-EM labels.

## Model reporting at each checkpoint

Report the same metrics and denominators at every checkpoint:

- joint declared-range-normalized RMSE;
- per-feature MAE/RMSE in physical units;
- Q minimum-attainment rate and shortfall;
- manufacturability/analytical/Cadence/Calibre/fresh-EMX funnel rates;
- paired seed effects and uncertainty intervals;
- training time and optimizer budget;
- failure cases and out-of-support panels.

Do not declare a data-scale improvement from checkpoint means alone if any control changed.

## Completion gate

A cumulative checkpoint is complete only when its no-clobber artifact root has:

- a terminal execution receipt;
- a complete accepted/rejected identity index;
- source and output SHA-256 manifests;
- data-quality and split-leakage audits;
- model/evaluation summaries with explicit denominators;
- all required independent release gates at GO.

The 2026-08-23 handoff does not claim completion of the current 500k curve or the retained 1M campaign.
