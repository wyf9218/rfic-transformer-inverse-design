# Research handover entry

Before continuing, read `docs/handover/HANDOVER_EN.md`, `QUICKSTART.md`, `ASSET_MAP.md` and `VERIFICATION.md` in the same directory. The pre-handover README and dated production reports are historical, not live execution authority.

- Inspect current Git status; preserve unrelated/uncommitted work.
- Public code and private data/model repositories are separate. Never publish private data, weights, credentials, process decks or site paths in the public repository.
- Check existing manifests and completed outputs before starting any expensive work. Use new no-clobber output directories.
- Keep source rows, gradient-training rows, validation rows and test rows separate.
- Original100K minimum-Q, real10k symmetric matching and reconstructed symmetric100K are distinct protocols. Do not silently substitute contracts, weights, splits, normalizers or decoders.
- Keep proxy, held-out EM-labelled data and candidate-bound fresh EMX evidence separate. Preserve failed and missing requests in their original denominators.
- Native simulation needs explicit task scope and an authorized site environment. A local GUI/package smoke is not native acceptance; never fabricate missing QA/DRC evidence.
- Run only checks needed for changed paths. The archived whole-project runner has documented collection failures; do not claim it passes or repair unrelated historical production code as part of a handover edit.
- No research controller, training, simulation or production task should start merely because an old report describes one.
