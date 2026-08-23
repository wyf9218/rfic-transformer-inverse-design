# Contributing

Thanks for improving rfic-transformer-inverse-design.

## Development Setup

```bash
python -m pip install -e ".[gui,opt,test]"
python -m pytest -q
```

If Qt is installed on a headless machine, set:

```bash
export QT_QPA_PLATFORM=offscreen
```

## Guidelines

- Keep foundry process files, license servers, tapeout data, and private paths
  out of the repository.
- Use synthetic or publicly shareable examples in tests and documentation.
- Keep public APIs in `rfic_transformer_inverse_design.api` stable when practical.
- Add focused tests for behavior changes.

## Releasing

Before publishing a release:

```bash
python -m pytest -q
python -m build
python -m twine check dist/*
```
