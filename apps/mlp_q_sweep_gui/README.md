# Browser GUI packaging

Use the current English [handover quickstart](../../docs/handover/QUICKSTART.md#5-open-the-packaged-browser-gui) and [software inventory](../../docs/handover/HANDOVER_EN.md#6-software-packaging-and-entry-point-inventory).

The Python package includes the self-contained browser HTML, version-bound model loader, CLI and local launcher. It does not include model weights or proprietary process data. `START_LOCAL.sh` binds to loopback and starts **proxy mode**. The interface currently retains Chinese labels.

Default profile `deployed100k` selects the original seed20260713 model, not real10k. A replacement must include its exact contract, summary, weights, normalizer and decoder. `custom` selects a complete replacement bundle's `model_contract.json`.

Layout and physical adapters are code interfaces, not a native installation. The archived physical producer is **NOT_NATIVE_ACCEPTED** for the current QA/DRC evidence contract; do not treat proxy success or a synthetic software test as GDS/EMX validation. Historical `README_CN.md` and MARS launch instructions are retained for provenance and are not an instruction to restart any server service.
