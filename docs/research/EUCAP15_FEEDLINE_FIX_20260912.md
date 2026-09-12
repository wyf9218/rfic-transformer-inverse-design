# Feedline-only development intervention

Version: `eucap15_feedline_projection_development_v1`. This is a new explicit
opt-in candidate-construction method, not a replacement for frozen BB00,
15 GHz checkpoints, the original development128/controlled64, or FINAL inference.
No native queue, model loader, default decoder, GUI or production path was changed.

The 35 saved analytical failures were replayed once from their actual decoded
vectors using the original source-bound geometry contract. Original predecoder
logits were not saved and were not reconstructed. Original targets and frozen Q
choices are retained only as historical metadata, not as validated selections
for the new geometries.

## Construction

The implementation reuses `physics._feed_requirements`, `geometry_feasibility`,
and the source-verified export grid. It takes the maximum of each old feed and
its required minimum, holding every other continuous coordinate fixed. The
continuous Torch path retains the gradient through the geometrical requirement.
After ordinary nearest-grid export, requirements are recalculated on the grid
vector; only feed coordinates can move upward to a legal lattice tick. The old
analytical checker is unchanged. A bound violation is not clipped or authorized;
the replay records a HOLD and continues. Non-feed failures remain failures.

Use only by explicit selection in a **new** development path:

```python
from research.broadband56_nn.eucap15_feedline_projection import FeedlineProjection, VERSION

projection = FeedlineProjection(source_bound_geometry_contract, version=VERSION)
new_record = projection.construct(saved_decoded_geometry)
# For differentiable future method work, use projection.continuous(tensor).
# Neither call performs neural inference or authorizes native execution.
```

## Actual new results and limitations

- 35 original analytic failures replayed; 35 new geometries pass the unchanged
  **analytical** gate, with 21 primary-feed and 14 secondary-feed changes.
- Maximum feed increase is 150.165 um. Such a large intervention may change RF
  behavior significantly; no assertion of preserved response or improved
  physical hit rate is supported.
- 13 distinct new synthetic regressions passed across two test invocations:
  12 initial passes and one corrected-fixture pass. The initial failing fixture
  was itself analytically feasible; it was corrected without changing code.
  The failed attempt and source are retained. Old QA was not rerun.
- 0 neural inference calls, optimizer updates, GDS, DRC or EMX runs. New proxy,
  Q preselection and physical values are null / NOT_RUN; old results do not transfer.
- Outputs retain source decoded / old grid / new continuous / pre-ceiling grid /
  new grid / deltas / source pins. Raw original logits and actual GDS are null.
- `new_parameter_geometry_sha256` is the research rounded12 parameter-vector
  schema, **not** the production 1e-6 ROUND_HALF_UP fingerprint or a cross-source
  qualification-union uniqueness claim. A newly dispatched candidate still needs
  the existing production geometry identity and known-pool dedup procedure.
- For future Q-scan inference, apply a declared new method before scoring all
  eleven Q candidates and select Q using the new predictions. Do not repair the
  original selected candidate and retain its old proxy/Q as though unchanged.

Private evidence directory (not shipped with public private geometry):
`reports/eucap15ghz_20260908T220300Z/feedline_fix_20260912_v1/`.
The actual no-clobber replay command is in `REPLAY_EXECUTION.json`; the existing
`run_v1` intentionally rejects repetition. No old result directory was overwritten.
