# Literature-Driven Model Roadmap

## Near-Term Baselines

1. **Tandem networks:** preserve the frozen-forward response-consistency
   baseline before adding more expensive generative models.
2. **Multi-headed tandem:** test multiple deterministic candidates under the
   same forward proxy and physical-cell split.
3. **Balanced regression:** compare ordinary MSE and balanced losses on sparse
   physical cells without changing real-label counts.
4. **Frequency-sequence models:** compare pointwise MLP and lightweight GRU on
   the complete complex S-parameter sweep.

## Conditional Generative Models

MDN, conditional VAE, normalizing flow, and diffusion are considered only after
real data demonstrates meaningful one-to-many geometry modes. They must use the
same real-EM budget and report DRC/EM closure, not diversity alone.

## Active Sampling

Uncertainty sampling is compared against random, space-filling, physical-cell
deficit, and geometry-diversity policies under equal solver budgets. Proxy
uncertainty ranks candidates but does not become a label.

## Physics And Reliability

- structured reciprocal complex-S outputs;
- passivity diagnostics and reported projection corrections;
- boundary-OOD and sparse-cell stress tests;
- conformal calibration only under stated exchangeability assumptions;
- cross-solver residual analysis after port/stackup equivalence is proven.

## Representative Sources

- Multi-headed Tandem Neural Network, Optics & Laser Technology 176 (2024),
  <https://doi.org/10.1016/j.optlastec.2024.110997>
- TNN/MDN/CGAN inverse-design comparison, Optics Communications 554 (2024),
  <https://doi.org/10.1016/j.optcom.2023.130122>
- Modified CVAE microwave inverse design, IEEE T-MTT 73(11) (2025),
  <https://doi.org/10.1109/TMTT.2025.3583316>
- Conditional diffusion for microwave filters, Electronics 15(3) (2026),
  <https://doi.org/10.3390/electronics15030527>
- Balanced MSE for imbalanced regression, CVPR 2022,
  <https://openaccess.thecvf.com/content/CVPR2022/html/Ren_Balanced_MSE_for_Imbalanced_Visual_Regression_CVPR_2022_paper.html>

Results from other devices are methodological references, not transferable
sample-size or accuracy guarantees for this RFIC transformer problem.
