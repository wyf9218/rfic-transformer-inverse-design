# Delivered asset map

All paths in this table are relative to the **public** repository [rfic-transformer-inverse-design-data](https://github.com/wyf9218/rfic-transformer-inverse-design-data); no invitation is needed to read or clone it. Public code remains in the separate code repository. Original files are copied unchanged unless explicitly labelled a relocation contract or compressed representation. `ASSET_MANIFEST.json` records source-to-destination mappings and SHA-256 values. The initial private-transfer status in dated records is historical.

| Asset | Delivered path | What it enables / limit |
|---|---|---|
| Original 100K table | `dataset/original100k.csv.gz` | Exact 100,000-row CSV after decompression; original source identity retained |
| Original split + nested D10k/D50k indices | `research/dataset_size_811_20260921_v1/data/` | D100k 78,891/9,740/11,369; D10k 8,000/1,000/1,000; D50k 40,000/5,000/5,000 |
| Original seed20260713 model | `models/original100k/` | F/I NPZ, embedded normalizers, summary and frozen NumPy trainer |
| Separate real10k reference | `models/real10k/` | Frozen reference weights, histories and validation predictions; empty stored test file is not a completed test |
| Symmetric-recipe 100K | `models/symmetric100k/` | Matching weights, summary, history; new relocation-only `model_contract.json` |
| First-seed size models | `research/dataset_size_811_20260921_v1/trained_models/` | D10k and D50k; D100k reuses original seed20260713, not a missing duplicate |
| First-seed width models | `research/architecture_100k_20260921_v1/trained_models/` | Small/Medium/Large, not the asymmetric original100K architecture |
| Seed17/29 models and evaluations | `research/repeats_20260921_v1/received/` | Twelve additional systems, saved predictions and terminal records |
| Frozen training implementation | `research/{datasize_20260921_v1,dataset_size_811_20260921_v1,architecture_100k_20260921_v1,repeats_20260921_v1,legacy10k_recipe_on_original100k_20260921_v1}/` | Archived scripts/configs; historical absolute paths are evidence, not portable commands |
| Split helper | `runtime/model_splitting.py` | Exact helper expected by frozen training adapter |
| Current100K forward plots/CSV | `results/current100k_forward_evidence_20260921_v1/` | Symmetric reconstruction, 11,369 held-out EM-labelled rows; not fresh inverse EM |
| Frozen 10,000 target frame | `results/figure_data/random10000_targets.csv` | Existing target identity; do not regenerate to improve results |
| Q-scan results | `results/qscan100k_20260921_v1/`, `results/legacy8k_logic_alignment_20260921_v1/` | Proxy-only, with original methods and denominators retained |
| Paired64 result evidence | `results/paired64_final/`, `results/paper_paired64_v1/` | Final response/receipt tables and figures; raw GDS/S4P not included in this compact transfer |
| Distribution and methods | `results/paper_distribution_v1/`, `results/paper_methods_v1/` | Existing paper sources and captions, not new experiments |
| CSV-only replot | `replot/` | Frozen replot and aggregator; exact commands in QUICKSTART |
| Prior detailed notes | `historical_notes/` | Chinese archival algorithm/structure notes and historical site-path references; English handover is the current entry |

The three-seed aggregate directories are:

- `research/repeats_20260921_v1/received/aggregate_v1/dataset/d14358c5cefdd731/`
- `research/repeats_20260921_v1/received/aggregate_v1/architecture/b0e1ba535efa6985/`

## Not bundled, and why

The complete raw GDS/S-parameter corpus, foundry PDK/process decks, native licenses, credentials and SSH keys are **not uploaded**. CSVs and receipts retain original site-local raw-artifact references. An institutional account and access to the original storage are required to inspect those raw artifacts or run a new physical experiment. Public availability of the manifest does not make those remote paths accessible or grant licensed-tool/process rights to a new user.

The exact original real10k trainer bytes have not been recovered. Its weights can be loaded; the September symmetric recipe is a reconstruction, not a bitwise replay. No handover command silently substitutes another model or normalizer.

## Model checkpoint semantics

The delivered final NPZ stores selected F and I plus their normalizers. Saved stage checkpoints and histories are retained where present. A validation-selected best update can precede the final optimizer update. Do not rename a selected checkpoint “last” or claim arbitrary-update optimizer/RNG recovery: the published continuation wrapper supports the archived completed-stage semantics only.
