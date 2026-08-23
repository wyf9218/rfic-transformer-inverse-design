# Experiment Contract

Every publishable experiment should record:

- source table path, row count, and SHA-256;
- source record/manifest hash chain;
- exact input and geometry columns;
- physical ranges and cell-grid definition;
- train/validation/test identity hashes;
- implementation and model-contract fingerprints;
- model seed, split seed, architecture, loss, and optimizer budget;
- best epoch selected only from validation data;
- complete held-out predictions and per-cell metrics;
- model weights and artifact SHA-256 values;
- geometry/DRC and real-EM closure evidence when applicable.

Comparisons fail closed when any shared contract differs. Test data may not be
used for gradients, early stopping, hyperparameter selection, or threshold
tuning. Proxy predictions never count as real samples.
