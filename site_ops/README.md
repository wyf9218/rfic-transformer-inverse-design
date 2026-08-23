# Site Operations Archive

This directory preserves sanitized operational wrappers developed during the
research campaign. They are classified by purpose:

- `checks/`: local and remote contract verification;
- `mars/`: MARS/EMX launch, monitor, recovery, and packaging wrappers;
- `hfss/`: HFSS execution and return-processing wrappers;
- `other/`: checkpoint activation and supporting orchestration.

These files are **not portable entry points**. User names, host names, and local
paths were replaced with examples, but each script still requires review and a
site-specific configuration before execution. Generated paste/bootstrap files
larger than 500 KB were excluded because they embed archives rather than source
logic.

The supported reusable implementation is under `scripts/` and
`rfic_transformer_inverse_design/`.
