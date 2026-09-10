# Tandem method attribution

The retained private five-page discussion draft now has a method citation and
third bibliography entry. Existing experimental statements, tables, styles and
sections are unchanged. This is a citation update, not a new model or result.

Verified reference: D. Liu, Y. Tan, E. Khoram, and Z. Yu, “Training Deep Neural
Networks for the Inverse Design of Nanophotonic Structures,” *ACS Photonics*,
5(4), 1365–1369, 2018. [Publisher](https://pubs.acs.org/doi/10.1021/acsphotonics.7b01377);
[author manuscript](https://arxiv.org/pdf/1710.04724v3), pp. 5–6 and Fig. 4.

This is a nanophotonics precedent for training an inverse through a pretrained,
fixed forward model using response error. Keeping gradients with respect to
geometry is an implementation consequence, not a quoted autograd API claim.
It does not establish this project's RF accuracy, Q-selection novelty, foundry
validity, SRF acceptance or convergence.

The existing local recipe's response weighting, geometry anchor and schedule
remain implementation-specific. Read `research/broadband56_nn/bb00.py` lines
397–408 and 498–502 for forward binding/freezing and inverse backpropagation;
the inference wrapper's `no_grad` is not a training defect.

## Delivered identities

- Private version: `paper_tandem_citation_20260910_v1`.
- DOCX: `d0072cf39927c9d5d3f5cb03bb93073751da57db360ab82f84ebe5f42dc2c954`.
- Delivery receipt: `cf21b20034fb735370e09192b24db7829c334d530f55554e51054b637eeaacb5`.
- Manifest: `30259604eb31a3df0e82c69fd7514de1bd899616c885c1b8ec1acf74f30220e1`.
- SHA256SUMS: `876435774026c3c91f0f196999af9f27f46de54ef8dbce0a8259cd5e702833df` (20 entries verified).
- Independent content/OOXML QA: `64324a1819bd2babe46b792e358db07f8d77f99cf58f037e119e9a544fbe45b8`.
- Trainer source: `aa2bd332a4a0e1f15015f9e361fec08222d22f8f0c26f8701ed584fb0895dd45`.
- Frequency wrapper: `0251dba10a831b2595c60fcb8c2274a1188faa346b4c80b65f9555e5550ef4b7`.

All five rendered pages reviewed; three references fit without layout changes.
Author identity, author-made figures and final submission compliance remain
outstanding. The prior private draft is preserved. Private manuscript contents,
weights, geometries, raw EM evidence and runtime configuration are not uploaded.

New training/inference/native dispatch/test-data access: zero. Native state was
not refreshed for this document increment; use the dated interface receipts,
not this file, for execution status. The EuCAP goal remains ACTIVE / NOT_FINAL.
