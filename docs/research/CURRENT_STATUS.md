# 当前研究和生产状态

## 2026-09-12 M31：46新增EMX，20条fresh正式入账，实际自动接续第5批

14:22:35 UTC相对14:09:26：53新终态=46fresh＋6解析失败＋1保留执行失败；36strict有效，25范围内strict候选。19个新结果已确认唯一正式（14train/4validation/1test），另M30 DOE156/test补登1，故本窗fresh正式＋20、history＋0、认证下界6904。6个新候选仍待正式合并，不计已认证唯一。

现场26个独立原生EMX×2CPU，申请/执行容量48。CPU扣预约后7.625核，只诊断允许额外3份共享双CPU工具；不是额外每类3，也不是48已准入。第4批已在14:14:20自然闭合，原continuation实际自动启动第5批（21/256终态），无需聊天推动。计时安装器仍等待受支持边界，未生效，不影响既有48容量或健康生产。见[真实现场快照](production_increment_m31_20260912/SNAPSHOT.json)及[原生所有者状态](production_increment_m31_20260912/NATIVE_STATUS.md)。

14新train仅资格接收，并集3998→4012，163/512覆盖格、欠填1907不变，高K>0.8新增0；不是新训练或覆盖优势。历史strict112首次实际索引按ID关联112/112，原行序只有1条相同；normalized索引匹配不等于实际GDS字节证明。首成员5文件精确读取已交唯一owner，尚在执行，当前新历史资格UNKNOWN、入账0；不阻塞生产、不再读取其他111工件。新证据见[SHA256SUMS](production_increment_m31_20260912/SHA256SUMS)。未重做旧物理、模型、QA或讨论稿；以下为较早截面。

## 2026-09-12 M30：79份新增EMX、39条fresh正式入账

14:09:26 UTC相对13:44:51：新79fresh+10解析，49strict、40范围strict候选；正式确认唯一39（23train/8val/8test），history0，下界6884。DOE156/test待known-pool/full-history合并及正式提交，不能计第40个认证新增。现场3独立EMX×2CPU，申请/执行容量48；扣预约CPU13.449核，额外6为三类工具共享诊断空间，不是48已准入。

普通生产/后继程序活，batch4为224/256终态。计时2模块已在MARS最终路径8专项PASS；普通安装器3297923等待全批终态和owner/metadata自然退出，尚未生效，无健康任务重启。见[本轮实际现场](production_increment_m30_20260912/SNAPSHOT.json)与[调度变更状态](production_increment_m30_20260912/NATIVE_STATUS.md)。只接以后新增，不由AI守候边界。

23新train资格接收并集3975→3998（不是新训练），163/512格及欠填1907不变，高K>0.8新增0。历史strict112四文件请求未发送，旧PASS112不算新认证；[五页增量讨论稿](production_increment_m30_20260912/EuCAP_15GHz_Development_Results_Cost_Update_v2.docx)仅补已闭合batch3成本和资格边界，旧负结果不变，不是FINAL，无AI论文图。新文件身份见[SHA256SUMS](production_increment_m30_20260912/SHA256SUMS)。以下都是较早截面。

## 2026-09-12 M29：61新增fresh EMX、25正式入账

13:52离线源码判断已完成：[前置供给判断](production_increment_m29_20260912/FRONTEND_JUDGMENT_CN.md)。同13:44资源仅余46.245CPU当量，可新增23个双CPU工具；无EMX就绪等待。Calibre8未饱和，Cadence8→12离线反事实不增加CPU，未部署配额改动/未重启。此判断不更新运行观察时间。

13:44:51 UTC相对13:32：新61fresh，25范围内strict唯一全部正式，history0/pending0，下界6845。实际nativeEMX0，容量48；7Cadence许可及上游等待仍在，无EMX等待。前置供给/配额正由唯一owner检查，未声称新部署或48实跑。
已接收train+18至3975，163/512格不变。新闭合第3批成本分析与1875历史同族判定见[production_increment_m29_20260912](production_increment_m29_20260912/README.md)；普通生产不等Git，以下皆历史截面。

## 2026-09-12 M28：53份新增EMX、23条fresh入账（含旧pending3）

13:32:23 UTC相对13:23：新53fresh、20范围内strict唯一全部正式；另3旧pending补登，history0，正式并集下界6820。现场实际4独立EMX，申请/容量48；17等待任务随后由程序全部放行，不把permit计为同时原生并发。
资源有余量，准入窗口约3..128秒；共享串行链/I/O占比未知，无永久阻塞证据。原owner/后继程序健康继续，无重启。接收train+13至3957，163/512占格不变；历史63参数首次检查PASS但正式0。
新增结果和边界见[production_increment_m28_20260912](production_increment_m28_20260912/README.md)。以下M27及更早为历史截面。

## 2026-09-12 M27：24份新增EMX、8条正式入账，真实28路EMX运行

12:58→13:23累计24fresh、11范围内strict候选，其中8正式/3待提交，history+0，下界6797。13:23:11实际28独立EMX×2CPU、资源健康7/5，申请/容量48；剩余CPU准入仅5.8262核，不宣称48实跑。
第4批11/256终态，普通程序继续派发与接续；原第3批及缺registry故障证据保留，无旧预算重置。已接收train先+6再+1至3944、占格163/512不变、欠填1908→1907；3pending排除。首历史成员actualGDS失败，formal0。
证据与限制见[production_increment_m27_20260912](production_increment_m27_20260912/README.md)。以下M26及更早运行数字均是历史截面，不作为当前存活证明。

## 2026-09-12 M26：42份新增真实EMX、23条正式入账

观察12:58:26 UTC（基线12:44:41）：实际4独立EMX×2CPU，申请/容量48；新44终态=42fresh+2解析失败，23范围内strict唯一全部正式，fresh+23/history+0，总下界6789。
当前批241/256终态，剩15；唯一installer等待健康当前批及metadata自然收尾。INSTALL/第4批尚无，资源诊断额外15不当实际并发。没有重启健康任务。
13新train已首次续算：已接收并集3937，占格163/512不变、欠填1909→1908，高K>.8增量0。首历史shard00064行已逐项关联、等宽无损映射成立；63旧标签在范围但缺物理绑定，新增历史认证0。
新证据见[production_increment_m26_20260912](production_increment_m26_20260912/README.md)。Goal ACTIVE；生产不等待此发布。

最新完整统计现场：2026-09-12 12:44:41.975935 UTC（不是之后持续存活保证）。

- 12:29:42→12:44:41：60新fresh EMX，30新范围内strict唯一全部正式；18train/6validation/6test。
- fresh正式+30，history+0，pending0；认证并集可靠下界6766。
- 申请/容量48，实际7EMX×2CPU；资源扣除系统和在途许可后仅余1.4293 CPU，不能再准入双CPU任务。
- 当前健康batch3继续，installer3265510精确活；23ed未安装、第4批未启动，不重复派发或强杀。
- [最新事实与证据](production_increment_m25_20260912/README.md)；[最终增量截面](production_increment_m25_20260912/FINAL_SNAPSHOT.json)。
- 已接收train并集3906→3924，覆盖163/512未增加；不是全生产池，不宣称采样优势。
- 已首次取得1875个历史分片执行索引；身份/物理资格缺口仍在，正式新增0；首分片已接回，下一5个明确工件引用接收中。
- 100K、FINAL模型、独立10000请求未完成。以下历史段落不是当前调度指令或模型可用性证明。

## Latest approved scientific release: 83 / 320

[New7 / cumulative83 results and 16-frequency table](frequency5to20_new7_cumulative83_20260908_v1/README_CN.md) and [exact release receipt](frequency5to20_new7_cumulative83_20260908_v1/PACKAGE_QA.json).
Statistics are frozen at 2026-09-08 20:58:59 UTC: 913 closed slots, 747 solves = 712 strict-valid + 35 invalid; 59 analytical + 75 GDS + 32 DRC failures.
The original 3520-slot plan retains 2445 pending slots. Eleven complete strict11 requests permit full-set q_emx. This is not 83 all-success requests.
New7 adds 77 slots, 62 solves (60 strict + 2 invalid), 13 analytical and 2 GDS failures; original order and failure evidence are retained.
21 PNGs = 14 new-request plots + 7 aggregates/distributions; 7 CSVs and a 5-cell saved executed notebook. Native Jupyter NOT_RUN. No repeat of old76 figures.
All16 first-budget models remain PARTIAL/PROVISIONAL; physical15 is development5K and formal10K15 fresh EMX NOT_RUN. The 63-request PPT remains unchanged.
The full320 goal remains active; original finite programs continue independently. Later operational observations do not grant numerical or visual GO.
Single read-only runtime observation at21:25:28 UTC: original native1050236/reporter1978 identities alive; one7GHz request6, Q12–15, four EMX solvers×2threads. Closed metadata21:24:57 counted85/320,235pending. This is not approval of85 scientific results or a guarantee of later liveness.

All sections below are timestamped history; their latest/current wording does not override the 83-request scientific freeze.


## Current scientific release:76; separately observed runtime:80

[New13 / cumulative76 statistics and figures](frequency5to20_new13_cumulative76_20260908_v2/README_CN.md)
and [exact post-candidate publication approval](frequency5to20_new13_cumulative76_20260908_v2/PACKAGE_QA.json)
freeze76/320 requests,836 closed slots,685 solves=652 strict-valid+33 invalid.
Closed failures remain46 analytical+73 GDS+32 DRC; the original3520 plan
retains2509 pending slots. Nine complete-strict11 requests permit q_emx.
The31 PNGs are only13 new requests×2 plus5 cumulative aggregates; historical
graphs and the cumulative63 PPT are preserved. Seven CSVs and a5-cell
actually executed public CSV notebook are included; native Jupyter NOT_RUN.

At2026-09-08 20:34:17 UTC,one read-only observation verified original native
1050236 and local reporter1978 alive. Development5K15GHz request6,Q10
was solving. Latest closed report metadata at20:31:52 contained80 requests
and240 pending; later numbers and figures have not received scientific GO.
The frozen76 source and real-time operational state must not be pooled.

All16 first-budget models remain PARTIAL/PROVISIONAL with prior held-out
and1,760,000 SELF_PROXY evidence. Physical15GHz is development5K;
formal10K15 fresh EMX remains NOT_RUN. No training,proxy or existingEMX
was repeated. Original4×CPU2 resource cap,dual leases and deadlines remain.

The exact scoped package fixes a historical40-report link in a newv2.
Its complete failedv1 package and NO_GO evidence are preserved privately;
42 files are byte-identical. No scientific values or images were changed.

All sections below are timestamped historical checkpoints; their then-current
pending/running/latest wording does not override this76-science/80-runtime split.


## Actual resource recovery and reporting successor, 2026-09-08 19:44 UTC

[Actual installation evidence](frequency5to20_resource_recovery_actual_20260908_v1/README_CN.md)
supersedes the older candidate-only and stopped-process observations below.
One at job2 exec launched native PID1050236 at19:38:28; independent /proc
inspection verified its exact10-argument command and immutable inputs.
At19:43:41 it was running12GHz's fifth request with four actual EMX solvers.
The original11GHz Q17 failure files remain intact; a distinct recovery success
record was produced. Completed Q10–Q16 artifacts were not regenerated.

Local reporter PID1978 was observed at19:44:24 with the actual new native
identity,72 reused captures,unchanged previous_figures and baseline_rerender=false.
Its first new snapshot brought the operational count to73/320,247 pending.
This is not a new numerical/visual approval: the released science and PPT
remain cumulative63,with51 approved physical figures.
The original4×CPU2 cap,dual leases,320/3520 frame and deadlines remain unchanged.
Production and GUI were not modified. Ordinary finite programs perform waits;
boot/crash restart is NOT_INSTALLED. These are timestamped observations,
not a guarantee that the processes remain alive indefinitely.

## Recovery code checkpoint, 2026-09-08 19:14 UTC

[Exact code identities and test evidence](FREQUENCY_RESOURCE_RECOVERY_STATUS_20260908.json):
resource-only dispatcher recovery passed106 author synthetic tests and50
independent cases (40 overlap plus10 additional). Independent code review
found no material blocker in that scope. The thin reporting successor passed70
new/existing regressions and read-only validation of the actual72-request
predecessor lineage. No baseline was recaptured, recalculated or rerendered.
The code and tests are committed separately from the advisor deck.

At this historical checkpoint deployment was not complete; native Linux tests,
actual input preflight and real launch evidence were still pending.
They have now been completed as recorded in the19:44 installation above;
new scientific results are still not approved. The exact original budget,4×2CPU research
cap, two leases, old failure evidence and all completed EMX artifacts remain
the required continuation contract.

## Advisor63 publication and diagnosed native exit, 2026-09-08 18:52 UTC

The [10-slide cumulative63 advisor deck and10 Q&A](frequency5to20_advisor_cumulative63_20260908_v1/README_CN.md)
now have exact numerical, visual and independent public-copy review. The22-file
release preserves the20-file author candidate and adds PACKAGE_QA.json plus
RELEASE_SHA256SUMS. PPT SHA-256:
`cd49400f0ff518143ec5365476819d9c39a6d2293910c9d2a94eb4a68b8c3af2`.
No native PowerPoint test is claimed. The physical snapshot is still63 requests,
560 solves and533 strict-valid labels, with all failures and5K/10K boundaries retained.

MARS was reachable at18:52:26 UTC;998085/2094607 and local86088 were absent.
The native dispatcher ended18:33:18 with DISPATCH_FAILED_NO_RETRY. The exact
cause was a pre-solver resource-admission rejection at11GHz's fifth request,Q17.
Only PREFLIGHT existed forQ17; no solve directory or native EMX launch existed.
The failed compound guard did not persist which CPU/load,memory or disk
subcondition triggered. No resource-specific cause is inferred. Q10–Q16
completion receipts match and must be reused; Q18–Q20 were not dispatched.
The read-only diagnosis receipt SHA-256 is
`b7fb800b99adc0440baa2399fd37e1b1eaf74bbe3bca8ca2eb1e85c809fd7cb7`
(private source: reports/frequency5to20_20260908T063800Z/native_failure_diagnosis_20260908T184000Z/remote_readonly_v1/DIAGNOSIS_RECEIPT.json).

Reporting ended PARTIAL with72 accounted/248 unsettled requests. These counts
are operational metadata, not new scientific approval. Recovery code and
reporter compatibility are being prepared in the independent research worktree;
recovery is not deployed and no new native launch is claimed. Production and GUI
are unchanged. The full320-request objective remains active. Earlier running
observations below are timestamped history, not present process status.

## Actual automatic native and reporting handoff, 18:02 UTC

[The actual handoff observation](frequency5to20_actual_handoff_20260908_v1/README_CN.md)
confirms original2146776 ended naturally at its18:00 cutoff, then the installed
waiter started native998085 once with the separate operational budget.
Read-only18:02:05 inspection found it alive at7GHz's fifth request,EMX Q10.
Original local50120/80544 also exited naturally; the existing86088 process
entered the reporting successor at18:01:54,seeded69 requests and did not rerender
the baseline. No process was manually launched,restarted or signalled.
69/320 is the operational handoff count, not a new scientific release.
The latest independently approved numbers/figures remain cumulative63 below;
snapshot0060 and terminal exports still need their own scoped review.

## Frozen cumulative63 report, 2026-09-08 17:11:58 UTC

The [new23 / cumulative63 physical report](frequency5to20_new23_cumulative63_20260908_v1/README_CN.md)
contains the latest independently checked numerical snapshot:63/320 settled
requests,693 original slots,560 actual solves=533 strict-valid+27 invalid.
The other133 closed slots retain46 analytical,60 GDS and27 DRC failures.
Only7 complete-strict11 requests permit full-set q_emx. The released figure
index contains46 request figures and5 aggregates; exact publication acceptance
is recorded separately in PACKAGE_QA.json, not inferred from renderer success.
STATUS16 preserves its earlier candidate timestamp; FIGURE_RELEASE_STATUS
records the later exact figure approval without rewriting historical pending fields.

All16 frequency pairs remain first-budget PARTIAL/PROVISIONAL with saved
load/diagnostic-resume, held-out and1,760,000 total SELF_PROXY candidate evidence.
Physical15GHz is development5K; formal10K15 fresh EMX remains NOT_RUN.
The latest advisor PPT remains cumulative40. This new static report does not
change that deck or claim the original320-request goal is complete.

Direct read-only runtime observation at17:43:13 UTC found MARS PID2146776
alive and advancing20GHz's fifth request atEMX Q15. PID2094607 remained
waiting for its natural exit; the successor had not started. Local50120,
80544 and86088 were alive at17:42. These later runtime observations are
not included in the frozen63 numerical counts. No process was signalled.

## Historical operational installation notes, 16:59 UTC and earlier

Latest [reporting handoff installation](frequency5to20_reporting_handoff_20260908_v1/README_CN.md):
local PID86088 was verified alive at16:59:09 UTC, waiting for exact old producer
exits and the real native-child receipt.23 final synthetic tests/source review
and actual readonly local/SSH checks passed. This is INSTALLED_WAITING, not a
new report or physics completion. Native at job1 is already installed; do not
resubmit either entry. Reboot/crash restart is NOT_INSTALLED.

The following native milestone is historical as of16:44; its then-pending
reporting automation is now installed as described above.

Latest [native-resume installation](frequency5to20_native_resume_20260908_v1/README_CN.md):
MARS at job1 / PID2094607 was alive at16:44:29 UTC, waiting for the original
PID2146776 identity to exit. No successor physical child was started then.
Transport,31 native-Linux synthetic tests and actual input preflight passed.
The separately recorded budget is not yet active. Reporting-successor code
passed23 synthetic tests and source review; its automatic handoff is NOT_INSTALLED.
The [16-frequency GPT handoff](GPT_HANDOFF_20260908T1610_CN.md) remains a historical
16:10 snapshot; a later direct16:34 check counted58/320 closed requests, not58
complete-strict11 sweeps. Latest independently approved numerical report is cumulative40.

## 2026-09-08: One Local Terminal-Export Waiter Installed

The [finite export delivery](frequency5to20_terminal_export_20260908_v1/README_CN.md)
has 33 focused synthetic tests passing and a bounded independent code-review GO.
One local waiter was observed alive at 15:56:39 UTC. It waits for the original
consumer's natural exit and exact terminal receipt, then runs five explicit
aggregate export jobs once. Real terminal export is NOT_STARTED; new figures
still require visual acceptance. Reboot/crash auto-restart is NOT_INSTALLED.
This does not modify or signal MARS, production, GUI or the existing consumer.
The cumulative40 deck below remains the latest independently approved report.

## 2026-09-08: Updated Advisor Deck, Frozen Cumulative40

The [updated10-slide native-editable advisor deck](frequency5to20_advisor_cumulative40_20260908_v1/README_CN.md)
combines existing16-pair training/held-out/SELF_PROXY evidence with the
independently approved snapshot_0031:40 closed requests,440 original slots,
353 independent solves=339 strict-valid+14 invalid. It is not a live counter.
The other87 closed slots retain26 analytical,40 GDS and21 DRC failures.
Only5 complete-strict11 requests permit q_emx. Physical15GHz is development5K;
formal10K15 fresh EMX remains NOT_RUN. The original320-request goal is active.

19/20GHz stopped at their original200-epoch update caps (3357/1744 per role),
before the inverse600+3000-update response ramp completed. All16 model pairs
remain PARTIAL. Loading and one-update diagnostic resume PASS do not establish
convergence or authorize treating diagnostic weights as ranked models.
This delivery adds no training, inference or simulation and does not modify
production/GUI or the running native reporting program. The deck preserves
the original training/data/held-out plots and adds a real20GHz NOT_HIT example.


## Historical Production Observation, 2026-09-05

Latest published observation: `2026-09-05T00:38:32Z`, not a live dashboard.
The active objective is exactly **200,000 unique accepted fresh real-EMX
geometries**, each with a four-port S4P at 5 through 60 GHz inclusive, 1 GHz
spacing and 56 frequency points. The full target is 11,200,000
geometry-frequency rows. **NN training is not authorized for this campaign.**

- Independently verified production progress: **31 accepted geometries,
  31 S4P files and 1,736 feature rows**. Golden validation samples are excluded.
- Failure accounting: 40 terminal candidates = 31 accepted + 9 Cadence-stage
  rejections. Rejected candidates were not relabeled or hidden.
- An independent read-only audit verified 255 artifact identities, the four
  migrated progress receipts, all 31 exact-frequency S4P files and 161,448
  stored feature-field comparisons. It did not run a simulator or replace data.
- One generation-20 supervisor was verified alive. It was waiting for the
  approved hard resource gates before a new Rescue Golden; available memory
  was 31.7%, below the unchanged 40% threshold. Zero simulators were active.
- Pilot 32 remains incomplete. The existing authorized chain continues after
  a genuine Golden PASS; neither a new campaign nor NN training is required.

The [public progress snapshot](BROADBAND56_PUBLIC_PROGRESS_20260905T003832Z.json)
contains exact aggregate counts, evidence SHA-256 identities and limitations.
The private runtime remains at source commit
`74bda20fb82c05fd63b4a4a625f1822a7b702423`; publishing this status does not modify it.
No raw geometry, GDS, S4P, private paths, PDK or credentials are published.

### Public Repository Test Limitation

The public test runner currently reports **1,983 passed, 1 failed, 54 skipped,
1 deselected, and 4 passed subtests**. The failure is
`tests/test_port_ground_metrics.py::test_metric_contract_binds_the_actual_sources`:
the public geometry-metric metadata retains an old SHA for the Cadence batch
script. Both the metadata and the script are byte-identical to source HEAD
`74bda20fb82c05fd63b4a4a625f1822a7b702423`, so this mismatch predates this
documentation-only publication. It remains unresolved and is not reported as a
green full-suite result. The snapshot records the exact expected/actual hashes
and private JUnit hash. The running private package has not been modified.

## Historical Snapshot: 2026-08-23

The following sections are preserved historical evidence, **not current
process status or authorization to train**. Their last live observation was
`2026-08-23T04:10:07Z`; the old raw-load threshold and model-training priorities
do not control the currently authorized Broadband56 campaign.

### Historical Primary Goals

- Monday report: explain the historical-200k architecture and exact row denominators; compare it with deployed-100k without causal overclaiming; complete fixed10k statistics/charts; release the five-pair controlled 100k/200k effect; release survivor-conditioned fresh-EMX three-chain errors; and finalize an advisor-ready HTML/PPTX plus question-and-answer material.
- Post-Monday mainline: build a current-contract, strict-`|K|<1`, controlled 200k/300k/400k/500k learning curve.
- Long term: map `[Lp,Ls,Qmin,|K|]` to manufacturable 10-D geometry and close proxy, layout, EMX, and sampled HFSS/measurement evidence.

### Historical Complete Evidence Blocks

- Historical deployed-100k and historical-200k model structures, row counts, parameter counts, and decoder differences were verified.
- A deterministic, unique 10,000-target finite-coverage frame was frozen: 8,000 legacy targets at `|K|≤0.8` and 2,000 extension targets above 0.8.
- Both historical models were evaluated on that identical frame. Historical 200k had 17.57% lower all-target joint proxy RMSE, but Q-target attainment was 7.99 percentage points lower.
- The physical funnel is closed through fresh EMX: `10,000 → 7,926 → 7,373 → 7,298 → 7,298`.
- Stage06 produced 7,298/7,298 fresh S4P survivor artifacts with no failed shards.

### Historical Running Snapshot

- The strict nested 100k/200k paired experiment has 7/10 terminal arms and 3/5 complete seed pairs at `2026-08-23T04:10:07Z`.
- The existing supervisor was alive with zero active children. Observed load1 was `231.03`, above the frozen prelaunch threshold `40`; rep4-large remained staged with no launch or terminal receipt. Do not launch it manually.
- It holds architecture, decoder, source, split, budget, seed contract, forward reference, and fixed-target inference constant.
- No historical comparison may substitute for this experiment's eventual causal data-scale result.

### Historical Blocked Or Incomplete Claims

- The formal fresh-EMX statistics and figures are blocked by a report-interface NO-GO (`P0/P1/P2/P3=0/3/0/0`).
- The RQ-I fixed10k release is NO-GO (`0/2/0/0`).
- The Monday HTML/PPTX is not finalized; an earlier partial is visual NO-GO.
- The current-contract `|K|<1` 200k/300k/400k/500k learning curve is not complete.
- Full-domain uniformity, final inverse-design closure across the original 10,000 targets, and EMX/HFSS agreement remain unproven.

### Denominator And Interpretation Rules

- “100k/200k” must be expanded into source-table, gradient-training, validation, and test rows.
- The fixed10k frame is deterministic coverage, not iid random sampling and not 10,000 EMX labels.
- The 7,298 EMX results are survivor-conditioned. The 2,702 analytical/Cadence/Calibre losses are MNAR and must remain in funnel reporting.
- Historical 100k/200k is an uncontrolled descriptive comparison; only the paired nested experiment can support a data-scale causal claim.
- Historical held-out-manifold accuracy and fixed10k one-shot error are different tasks and must not share one accuracy label.

Read [ENGINEERING_HANDOFF_20260823_CN.md](ENGINEERING_HANDOFF_20260823_CN.md) for the historical state and [KNOWN_NO_GO_20260823.md](KNOWN_NO_GO_20260823.md) before using any historical figure or claim.
