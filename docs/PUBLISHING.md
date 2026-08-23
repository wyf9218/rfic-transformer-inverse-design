# Publishing To GitHub

## Recommended Visibility

Create the repository as **private** until the research supervisor approves a
public release. The source is sanitized, but research timing and unpublished
methods may still be sensitive.

## GitHub CLI

After installing and authenticating `gh`:

```bash
gh auth login
gh repo create wyf9218/rfic-transformer-inverse-design \
  --private --source=. --remote=origin --push
```

## Existing Empty Repository

If an empty private repository is created in the GitHub web interface:

```bash
git remote add origin git@github.com:wyf9218/rfic-transformer-inverse-design.git
git push -u origin main
```

Before every push:

```bash
python tools/build_script_catalog.py
python tools/run_public_tests.py
python tools/build_repository_manifest.py
git status --short
```

Do not add real solver outputs or bypass `.gitignore` with `git add -f`.
