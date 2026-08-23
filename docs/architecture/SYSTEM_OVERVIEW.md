# System Overview

## Scope

The project automates a closed design loop for parameterized integrated
transformers. Geometry is generated under a fixed process and port contract,
evaluated by electromagnetic simulation, converted into physical features, and
used to train forward and inverse models.

## Components

| Component | Responsibility |
|---|---|
| `layout` | Octagonal coils, feeds, shields, ports, and geometry validation |
| `process` | Synthetic/public stackup abstractions and process interfaces |
| `sim` | EMX command construction and Touchstone handling |
| `analysis` | S-parameter, impedance, inductance, Q, K, and passivity metrics |
| `dataset` | Candidate records, manifests, accepted pools, and deduplication |
| `optimize` | Candidate search and target-driven geometry optimization |
| `scripts` | Reproducible research workflows and fail-closed audits |

## Ten-Dimensional Geometry Contract

The production learning table uses ten independent geometry columns:

1. primary outer width
2. primary outer height
3. secondary outer width
4. secondary outer height
5. shared conductor width
6. primary terminal span
7. secondary terminal span
8. horizontal offset
9. primary feed extension
10. secondary feed extension

Derived construction values are not counted as additional independent labels.
All model outputs still require full topology and DRC validation.

## Evidence Layers

1. **Execution evidence:** commands completed and artifacts are parseable.
2. **Data evidence:** source hashes, geometry identities, frequency/port shape,
   and physical ranges are valid.
3. **Model evidence:** held-out physical-cell results and tail metrics are
   finite and isolated from training.
4. **Physics evidence:** predicted candidates pass geometry/DRC and fresh EMX.
5. **Cross-solver evidence:** selected structures correlate with HFSS under an
   explicitly matched stackup and port reference.

No lower layer substitutes for a higher one.
