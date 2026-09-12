# New256 DOE / train-neighborhood proposal method

This is engineering sampling, not a controlled victory over another algorithm.
The original256 proposal batch is already frozen; do not regenerate it.
The public configurable preparer is a non-byte-identical refactor, **not** the
source executed for that batch and not a native execution controller.

## Frozen method and actual preparation result

- 192 ordinary10D geometry-LHS proposals (75%), seed2026091201.
- 64 train-only neighborhood proposals (25%), seed2026091202.
- Parents:64 unique, chosen without replacement from3801 frozen train geometries;
  original strict/core and analytical feasibility are required. No val/test
  numeric labels, model predictions or new64 physical outcomes guided sampling.
- New local kernel: uniform draw within parent±1% original coordinate span,
  intersected with unchanged geometry bounds before drawing, then original5nm quantization.
  One saved grid coordinate is0.000350590534508um outside its raw local box, still within global bounds; the local-box guarantee applies to raw draws, not every quantized coordinate.
  The old EXPLORATION function was global LHS, not this neighborhood method.
- Fixed order: three DOE then one neighbor, repeated64 times. New geometry split
  uses canonical geometry identity and originalseed17; train ancestry is retained
  and no train-child geometry is certified independent finaltest.
- Raw/grid geometry, parent ID/hash, jitter interval, RNG unit draws, seed and
  all failures/duplicates are retained; no failure replacement.
- All target, q_proxy, proxy, score and model_id fields are null. Geometry sampling
  does not establish physical uniformity or feasibility of every requested tuple.
- Actual frozen proposals:DOE174/192 analyticPASS,neighbor64/64 analyticPASS;
  18 original failures,0 known duplicates,238 local eligible,0 starts at preparation.
  None of these is a fresh-EMX success or a formally accepted geometry count.

## Identity, scope and callable public interface

Full qualification scope remains15GHz,Lp/Ls0.5–2.0nH,K_abs0.2–0.85,actual strict/SRF
evidence. Q=min(Qp,Qs); Q10–20 overlap is reported separately. No range is narrowed.
Reuse prior known-pool canonical/nominal-grid exclusion metadata and append prior31
research members and closed64 proposals. Canonical9-decimal,research rounded12,
and production1e-6 ROUND_HALF_UP hashes remain distinct. The production-hash known
subset is6329+31+64 raw/grid. FullP215 union is incomplete; owner live-ledger and
reservation checks remain required. Original20train additions are exclusions only.

The public path interface requires an explicit new v2 intent, not the old intent:

```sh
python -m research.broadband56_nn.eucap15_geometry_proposals \
  --intent /absolute/new_intent.json --intent-sha EXACT_SHA256 \
  --out /absolute/new_nonexistent_output
```

`inputs` must explicitly pin six files:contract,splits,current_source_rows,
exclusion_metadata,prior_members31,prior_proposals64. Each pin has absolute `path`
and lowercase `sha256`; `sources` additionally pins production_geometry_helpers
and code dependencies. Private data/helper access must actually exist—Git does
not carry them. The CLI retains the192/64 counts and two fixed seeds; it is a
reproducible proposal tool, not a budget reset, autonomous worker or resume tool.
The intent also declares study_id,counts,seeds,physical_qualification_scope and suggested_new_budget.
Use the original frozen manifest for current execution; new refactor source mapping
and the actual private-script SHA are in PRODUCTION256_SOURCE_MAP.json.

Native owner alone registers new budget and verifies resources; old64 budget and
negative outcomes stay unchanged. No native launch or new real result is claimed
by this code/document delivery. Four old kernel tests and256 draws were not rerun.
