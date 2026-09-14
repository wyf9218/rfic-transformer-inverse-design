# 15 GHz operating-point policy

`operating_point_15ghz_v1` changes the research eligibility domain, not the EM
solver or extraction formula. SRF and the original strict mask remain diagnostic
data; half-SRF is not a new eligibility, training or evaluation gate.

The shared implementation is `research/broadband56_nn/operating_point15.py`.
It consumes saved audited descriptors (finite S/Z/features and positive R/X on
both windings), exact 15 GHz, existing numerical QA, and Kabs 0.2–0.85.
There is no fixed inductance window or training-pool Q10–20 filter. Numerical
eligibility alone does not certify foundry/ports/original GDS/DRC/EMX provenance.

The dataset builder defaults to this policy:

```sh
python -m research.broadband56_nn.eucap15_split811 \
  --snapshot QUALIFIED_SNAPSHOT.json --out NEW_DATA_DIRECTORY \
  --contract GEOMETRY_CONTRACT.json --exposure-cutoff CUTOFF \
  --first-doe-batch FIRST_BATCH --previous-mapping ORIGINAL_MAPPING.json \
  --label-policy operating_point_15ghz_v1
```

Use actual private inputs and a new output directory. Each selected member must
carry complete operating-point and compatibility evidence. The separate 80/10/10
mapping retains family/exposure restrictions; unmet independent holdout quotas
remain unmet. A new snapshot does not mean a model has been trained on it.

New 15 GHz F/I configs use `label_mode: OPERATING_POINT_15GHZ` and
`label_policy: operating_point_15ghz_v1`. Both entry points check the dataset policy
and split before training. The new mask, not old strict, selects labels; each
frequency normalizer is fitted only to its valid train subset and bound to the
checkpoint. Historical explicit modes and exact resume remain unchanged.

For an explicitly versioned fixed-Q physical analysis:

```sh
python -m research.broadband56_nn.frequency_physical_statistics \
  --operating-point-input FIXED_REQUESTS_WITH_FEATURE_PINS.json --out NEW_STATS_DIRECTORY
```

The JSON declares `analysis_kind` (`PREDECLARED_OPERATING_POINT` or
`POST_HOC_ORIGINAL_PROTOCOL_PRESERVED`) and unique `requests`. Each retains
request_id, candidate_id, q_proxy, original status, and feature_pin or null.
The original preselected Q must match the saved feature. Pending/failed requests
remain in the denominator. MAE/P95/CDF are explicitly conditional on operating-
point validity. This is no new EMX, no accuracy improvement claim, and no rewrite
of the historical 128/64 protocols. Only numeric source tables are produced.

Legacy range-policy objects are retained byte-for-byte as historical evidence;
their old strict/SRF metadata is superseded by the explicit new label policy.
The positive-L/K range function itself has no half-SRF condition.

Paper wording: “15 GHz operating-point impedance-derived descriptor modeling;
half the self-resonance frequency is not used as a sample-exclusion criterion.”
Q is inductive Q; K is an impedance-derived descriptor at the operating point,
not a claim of pure magnetic coupling or complete broadband characterization.
