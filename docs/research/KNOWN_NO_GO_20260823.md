# Known NO-GO Evidence (2026-08-23)

This file preserves negative results that constrain what may be reported. A failed gate is evidence; it must not be deleted, renamed as success, or bypassed by silently rerunning into the same output directory.

## RQ-I fixed10k release v7

- Final decision: **NO-GO**
- Severity counts P0/P1/P2/P3: `0/2/0/0`
- Checks: 69 passed of 71
- P1-1: a post-PASS root replacement could leave an apparently PASS-only release.
- P1-2: a file introduced before manifest construction could be automatically enrolled instead of being rejected as unregistered.

Offline frozen evidence SHA-256 values:

| artifact | SHA-256 |
|---|---|
| receipt | `daef95d8c4a8426f67e32f996b1bea96a3d2a9fa2454205dab88cfb1fb300ca7` |
| index | `593d455f197a064d6789a85dae2366cc8e5bb8f679dc2b757eeb081928991fc3` |
| report | `d817bd887150a4c4b1bb4b7af93b558b1b916147ccfe0230d7a20974c3ce7545` |
| findings | `6f0f5f93db980fce51633eb353f2c66ed468bd5633d3b9e43f965ac89fa384ac` |
| output-interface evidence | `283bdeba3b493104a2c24e1ae5f4f3ba42da29c1f48ff9a61cde83b216013362` |
| audit harness | `748fe7d62a34d67fd5af335ae14c36b712dfcb14c37d3cc06abf2e261395ff5e` |
| manifest | `23463b7d6133f7240c6b3a5dadb712895479cb6043985a33c7507a6578c253f2` |

Required repair: create a new no-clobber successor; bind the complete artifact set before execution; make post-PASS identity immutable; independently test both adversarial cases.

## Report-interface compatibility v7

- Final decision: **NO-GO**
- Severity counts P0/P1/P2/P3: `0/3/0/0`
- P1-1: the output-interface file could change after writing while receipt/index still published PASS using its old SHA.
- P1-2: 41 nested roles could alias to only 40 unique artifact identities and still pass.
- P1-3: an explicit narrative extrapolating survivor performance to the original 10,000 targets passed the validator.

Offline frozen evidence SHA-256 values:

| artifact | SHA-256 |
|---|---|
| index | `659436c55f53aad9b5dc753af65ee68551b04c7508099b800ccf104e865df2f1` |
| manifest | `9f2de383152016ebd523417bae73d82e68777cab7aaaab28b570e56cf1bd14cc` |
| receipt | `3df9bc6f6b5bea90b75e977541646339d9074cb552a5d9ec6782b8e9480074fd` |
| report | `ba66b5f97981d7425db158608c04a1fd40b895db0bda47baa0071570fa40d505` |
| findings | `44ebff3b245868cfaffd2ad3a93e2f0031ef9dfed20ff1159ab80ef7312fd935` |
| output-interface evidence | `f5a27ea8d6feecfec7ff2739f1876728863d080bd60612f3d74a7505b118e83e` |
| audit harness | `8c503a6eb324fc9cf6de76b25fbca41615fec1c3e4a22d771558f3a089e50d58` |

Required repair: atomic version/SHA binding, strict role-to-identity uniqueness, and fail-closed language rules that prohibit all-original-10k accuracy claims from survivor-only evidence.

## Monday report partial v3

- Final decision: **VISUAL NO-GO**
- Findings: duplicate content trees, visible red blocker states, and horizontal overflow.
- It is a diagnostic partial artifact, not a deliverable for the supervisor.

Required repair: rebuild from the accepted evidence graph after the statistical interfaces pass, then render every page/slide and perform independent visual QA.

## Unvalidated successor work

The following successor concepts existed as partial work but were interrupted before validation:

- RQ-I release v8 WIP;
- Monday report v4 result-blind scaffold WIP.

They must be treated as `UNVALIDATED_WIP`, not as repairs and not as evidence of progress beyond their saved files.

## Reporting prohibitions until repair

- Do not publish fresh-EMX bar charts or a final accuracy table from a release whose interface is NO-GO.
- Do not call the fixed10k frame an iid random sample.
- Do not call 7,298 EMX survivors “10,000 EMX tests.”
- Do not extrapolate survivor-conditioned performance to the 2,702 rejected/failed targets.
- Do not attribute historical 100k/200k differences to dataset size.
- Do not call source-table rows gradient-training rows.
- Do not hide failed gates, incomplete seed arms, or Q-target regression.

The authoritative NO-GO artifacts remain in the private offline evidence store and are intentionally excluded from this sanitized code repository. Their hashes above allow identity checks when access is granted.
