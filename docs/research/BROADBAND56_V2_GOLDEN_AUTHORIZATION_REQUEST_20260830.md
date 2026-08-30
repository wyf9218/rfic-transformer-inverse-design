# Broadband56 V2 one-golden authorization request

## Current proven state

Preparation is complete and no physical execution has started.

- Campaign: `broadband56_real_emx_balanced200k_tsmc65_v2`
- Contract fingerprint: `f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e`
- Corrected R2 baseline SHA-256: `037889d41f41bbedbcd38188c44a5c8c1ccffa62943c334a72f0980f38bf864f`
- Private preparation receipt SHA-256: `1cbc646d0223a4f5655552eeb0339becf3d0cc5ac7a86a2bc72b19b0ea7b8ca6`
- Private 56-point configuration SHA-256: `d83114199dfe82e1f1d1fc90db8e021bad9b2d98118d1bba0011b5a9f8e24327`
- Preparation checks: `40 PASS / 0 FAIL`
- Current accepted geometries / S4P / feature rows: `0 / 0 / 0`
- Cadence / Calibre / EMX action taken under the preparation approval: `no / no / no`

The preparation receipt says `PREPARED_FOR_GOLDEN_GATE`; that is a readiness
state, not authorization to run a golden sample.

## Exact authorization candidate

Path:

`docs/research/BROADBAND56_V2_GOLDEN_AUTHORIZATION_CANDIDATE_20260830.json`

Exact SHA-256:

`655a490c027a5aa96412ac982891123e17250412d90819f52d5cb8e17a082965`

The candidate is request-only and has no execution effect. It authorizes
nothing until the project owner explicitly approves this exact SHA-256.

## Requested scope

`APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY`

After exact-SHA approval, the permitted sequence would be:

1. Reverify the approved candidate, contract fingerprint, preparation receipt,
   private configuration, private runtime identities, process isolation, and
   output-path noncollision.
2. Run fresh load, resource, and license gates. Stop without simulator action
   unless every gate is PASS.
3. Create one no-clobber golden root and freeze one deterministic canonical
   geometry from the exact ten-dimensional bounds.
4. Run that one geometry only through analytical/topology gates, Cadence GDS,
   Calibre DRC, fresh EMX, exact 56-point four-port S4P parsing, feature
   extraction, and golden-mode audit.
5. Stop after the one golden receipt, whether it passes or fails.

This scope does **not** authorize a 32-geometry pilot, 1,000-geometry pilot,
queue, supervisor, Phase A/B/C, or the 200,000-geometry campaign.

## Exact approval wording

An unambiguous approval response is:

> I, Yufeng Wang, project owner and project leader, explicitly approve
> `docs/research/BROADBAND56_V2_GOLDEN_AUTHORIZATION_CANDIDATE_20260830.json`
> with exact SHA-256
> `655a490c027a5aa96412ac982891123e17250412d90819f52d5cb8e17a082965`
> for `APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY`. I authorize fresh
> load/resource/license gates and, only if all gates PASS, exactly one
> no-clobber golden geometry through the frozen analytical, topology, Cadence,
> Calibre, EMX, S4P, feature, provenance, and golden-audit chain bound to
> campaign fingerprint
> `f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e`.
> Stop after the golden receipt regardless of PASS or FAIL. This approval does
> not authorize 32/1,000 pilots, a queue, supervisor, Phase A/B/C, or the 200K
> campaign.

Until that exact approval exists, the next execution state remains
`PREPARATION_PASS_GOLDEN_NOT_AUTHORIZED`.

After, and only after, that explicit approval is received, record it with the
no-clobber, record-only command:

```bash
python scripts/record_broadband56_v2_golden_authorization.py \
  --candidate docs/research/BROADBAND56_V2_GOLDEN_AUTHORIZATION_CANDIDATE_20260830.json \
  --candidate-sha256 655a490c027a5aa96412ac982891123e17250412d90819f52d5cb8e17a082965 \
  --approved-by '<explicit approver identity>' \
  --approved-utc '<timezone-aware approval timestamp>' \
  --approval-reference '<reference to the explicit approval instruction>' \
  --out-dir '<new no-clobber golden-authorization receipt directory>'
```

The recorder only writes an approval receipt and SHA index. It cannot query a
license or resource, create a geometry or GDS, or invoke a simulator.
