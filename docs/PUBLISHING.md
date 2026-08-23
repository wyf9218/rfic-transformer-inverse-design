# Publishing To GitHub

## Current Visibility

This sanitized handoff is intentionally **public**. The repository owner
authorized public visibility on 2026-08-23 so external GPT/research tools can
read the code, contracts, status, and evidence summaries without GitHub account
access.

For a new fork or a snapshot with additional artifacts, start private until its
own publication scope has been reviewed. Do not infer that this approval covers
real solver data, weights, GDS, Touchstone files, PDK files, licenses,
credentials, or site-specific paths; those remain outside this repository.

## GitHub CLI

After installing and authenticating `gh`:

```bash
gh auth login
gh repo create wyf9218/rfic-transformer-inverse-design \
  --public --source=. --remote=origin --push
```

## Existing Empty Repository

If an empty repository is created in the GitHub web interface:

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
