# Three-input MLP Q-sweep application

## Frozen contract

The application uses the hash-bound historical real-10k MLP checkpoint
`real10k_center15ghz_seed20260711`:

- inverse MLP: `4 -> 256 -> 256 -> 256 -> 10`;
- frozen forward surrogate: `10 -> 256 -> 256 -> 256 -> 4`;
- input order: `Lp, Ls, Q_scalar, |K|` at 15 GHz;
- training definition: `Q_scalar = min(Qp, Qs)`;
- geometry output: ten bounded continuous variables;
- model support: `Lp/Ls=0.5..3.0 nH`, `Q=5..25`, `|K|=0..0.8`.

The public repository contains only the model contract and expected SHA-256
digests. The private NPZ checkpoint is loaded at runtime and rejected when its
hash, architecture, columns, or support do not match.

## Selection algorithm

`Lp`, `Ls`, and `|K|` are held fixed. Q is the sole sweep variable and is always
the exact integer grid `10..20`. The same frozen MLP produces one geometry per
Q. Candidate score is

```text
sqrt(mean(((observed - target) / [2.5, 2.5, 20, 0.8])**2))
```

The lower score wins; an exact tie selects the lower Q. This is symmetric exact
target matching. It is not the one-sided `Q >= Qmin` objective.

## Evidence boundary

The frozen forward surrogate is useful for rapid candidate ranking, but its
reconstruction is not a fresh physical label. A result may be called the
physical-error minimum only when a private backend evaluates all eleven exact
GDS layouts with fresh real EMX, derives `Q_scalar=min(Qp,Qs)`, and returns the
bound GDS/S4P artifacts. Missing candidates or mismatched geometry hashes cause
a fail-closed result.

Foundry DRC and independent HFSS correlation remain separate downstream gates.

The machine-readable implementation and verification receipt is
[MLP_Q_SWEEP_GUI_RELEASE_20260824.json](../research/MLP_Q_SWEEP_GUI_RELEASE_20260824.json).

## Commands

Proxy diagnostic:

```bash
rfic-transformer-q-sweep \
  --model-dir /private/model \
  --out-dir /new/run \
  --design-id demo \
  --lp-nh 1.15 --ls-nh 1.40 --k-abs 0.76
```

Fresh-EMX physical selection:

```bash
rfic-transformer-q-sweep \
  --model-dir /private/model \
  --out-dir /new/run \
  --design-id demo \
  --lp-nh 1.15 --ls-nh 1.40 --k-abs 0.76 \
  --mode physical \
  --physical-backend-command "bash /private/run_q_sweep_gds_emx_backend.sh"
```

Web application:

```bash
rfic-transformer-q-sweep-gui \
  --model-dir /private/model \
  --output-root /private/runs \
  --mode physical \
  --physical-backend-command "bash /private/run_q_sweep_gds_emx_backend.sh" \
  --open-browser
```
