# Public Release Policy

## Included

- reusable source code and tests;
- synthetic process examples;
- portable configuration templates;
- workflow and research documentation;
- CI configuration.

## Excluded

- commercial PDK and foundry-rule documents;
- Cadence/EMX/HFSS binaries and license configuration;
- credentials, tokens, user names, internal hosts, and workstation paths;
- real `.s4p`, `.s8p`, GDS, AEDT, STEP, and tapeout data;
- generated model weights, datasets, logs, and temporary bundles;
- unpublished paper PDFs or third-party copyrighted material.

## Pre-Push Checks

1. run the credential/path scanner;
2. inspect `git status` and staged file sizes;
3. reject files larger than the configured source limit;
4. run focused and full tests;
5. verify that templates contain placeholders only;
6. create a private repository until the supervisor approves publication.

Scientific summaries may state verified aggregate facts but must not expose raw
proprietary data or imply that pending gates have passed.
