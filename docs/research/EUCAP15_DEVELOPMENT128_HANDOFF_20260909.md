# EuCAP15 development128 handoff

As of 2026-09-09T22:23Z. This is a development evidence update, not a FINAL-model or physical-accuracy release.

## Frozen model and requests

The pilot uses the predeclared 3x256 seed17 baseline from the frozen formal-only 6329-geometry development view, not an ablation winner. Model ID: `f15-development6329-3x256-seed17-9d69d7ebfc66`. The split is 3801 train / 1269 validation / 1259 unevaluated test. The earlier historical-reference 64 is a separate experiment and remains unchanged.

The actual new inference run froze 128 request triples and 1408 integer-Q candidates (Q10 through Q20; seed 202609091501). It selected exactly one q_proxy per request using the unchanged symmetric fixed spans [2.5,2.5,20,0.8], with smaller Q on exact ties. There were no exact minimum ties in this frame.

The original denominator remains 128: 93 preselected candidates pass the analytical gate and 35 fail. In 28 of those 35 failures another Q candidate passes analytically, but the frozen q_proxy is deliberately not replaced. Analytical eligibility is neither DRC passage nor EMX success.

The observed training marginal maxima are Qmin 18.70441442177916 and |k| 0.5806891255192034. Of 1408 candidates, 706 target vectors exceed at least one training marginal bound; 702 are marginally within bounds but joint feasibility is unknown. The target domain is preserved, not clipped.

## Independent candidate review and native intake

Independent saved-candidate QA returned GO for list identity and arithmetic only. It checked 128/1408 accounting, selected identity, all original Q values and support flags. The largest independently recomputed score difference was 5.55e-17. It did not reload weights or rerun inference, training or simulation.

The sole native owner received nine frozen metadata/QA files on MARS and verified their byte sizes and hashes. Its actual receipt says RECEIVED_NOT_DISPATCHABLE: the development-scope adapter, complete source closure, new128 dedup/admission and post256 queue entry are not installed/completed. New128 native launches=0. Existing healthy acquisition work is not stopped or modified.

Native dispatch remains owned by the data-generation task. No second controller or AI periodic polling was installed. The research-side initial-ledger reader is implemented and was executed exactly once against the actual frozen pilot after nine dedicated synthetic tests and independent static review. It produced 128 request rows: 35 analytical failures and 93 pending without consumed physical evidence. All physical errors and the completed success fraction remain null. Six output SHA entries passed. This is explicitly FROZEN_INITIAL_LEDGER_NOT_CURRENT_LIVE_STATUS; it is not a live monitor or a native-result importer. Unknown native-publication input is rejected.

## Paper and retained training evidence

All 15 development pairs have saved-weight load/resume evidence. The 14 additional pairs passed 28 isolated one-step diagnostics; the original baseline proof was reused. These diagnostics do not add to the 538099 primary optimizer updates or establish convergence. See [the earlier results](EUCAP15_MILESTONE_RESULTS_20260909.md) for the validated five-shape table and separate historical64/matched16 results.

The five-page discussion draft now includes the real 6329 validation table and matched16 acquisition observations. Independent review checked all five existing rendered pages and source mappings. Author details and submission-font compliance remain pending. No paper figures were generated. This is not the final paper or independent final-test evidence.

## Research entry

Module: `research.broadband56_nn.eucap15_development128_reader`; tests: `tests/test_eucap15_development128_reader.py`.

The command takes `--manifest`, `--manifest-sha256`, `--qa-receipt`, `--qa-sha256` and a fresh absolute `--out` outside the source worktree. It requires the private hash-bound JSON/JSONL sources; a GitHub checkout does not contain those inputs. Do not supply `--publication-index`: native-result consumption remains NOT_INSTALLED_AWAIT_OWNER_SCHEMA.

The exact successfully executed local command is retained in the private `development128_reader_20260909_v1/EXECUTION_RECEIPT.json`. Its `run_v1` output is complete and must not be rerun or overwritten. No models, test arrays, new targets or solvers were used by this entry.

## Immutable evidence identities

Private artifacts remain under the local reports/eucap15ghz_20260908T220300Z tree unless noted. Their hashes identify evidence; their raw contents and model weights are not published here.

| Evidence | SHA256 |
| --- | --- |
| Frozen6329 dataset | 4dc6e91a644db2aca0a580958ac8b31e1e26ed73497b935145202b07227ef580 |
| Pilot128 inference receipt | 00df4a0b533b51cd5ee42327ad7620e21b6612f899473930e9360552c9d58d8e |
| Pilot128 selected submission manifest | b3d388571dbbd6f94d1d273d11910cb9518038ac790233ad2bd483bb5f337ff8 |
| Independent128 candidate QA | ede9ed845a8c804de16450f5636cab0b9244b66ae03796de3755c12c5b1b4acb |
| Sole-owner metadata intake receipt | 0a7cc5cc47683256556a2e26de65e94bdb6953b75c3643366e035b292a69b372 |
| Additional14 resume completion | 27928028b2dbadef931fd349c793cfbbc2a72b5322408be9647a2142976ea1d6 |
| Five-page discussion DOCX | a4588cf527087054bc6ccd9d23319c56a3319dde10e0b7d6eda157cd8c5721a5 |
| Independent paper review | 7665503c0f2d42387dfc68aa725b04039de5e02c354775d81f55892b036eae8d |
| Initial-reader independent static review | 4eed47909fb45b1dc1800524226032e67944250764391bad9330087efdc9dbe2 |
| Actual128 initial-ledger receipt | 9b8856a7f733b5712fdbee7559d5360a1ebe119bd757624b2a808e3dd8582711 |

FINAL data/model selection and the 10000 main physical requests with prechosen 100 full-Q audits remain unexecuted. No test feedback is admitted into the same final model.
