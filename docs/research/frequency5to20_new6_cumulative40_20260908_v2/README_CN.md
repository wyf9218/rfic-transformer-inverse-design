# 5–20 GHz：新增6请求与累计40请求的真实EMX结果

S31 固定统计时间 2026-09-08T14:10:53.000718+00:00，包创建 2026-09-08T14:10:57.229418+00:00。这不是实时服务器状态；仅新增正式10K的10/20/6/7/8/9 GHz第三请求，旧34请求不重新评价。

## 完成状态与分母

| 范围 | 请求 | 原槽 | 独立求解 | strict有效 | strict无效 | analytic失败 | GDS失败 | DRC失败 | pending |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 本次新增闭合 | 6 | 66 | 53 | 51 | 2 | 0 | 8 | 5 | 0 |
| S31累计闭合 | 40 | 440 | 353 | 339 | 14 | 26 | 40 | 21 | 0 |
| 固定全计划 | 320 | 3520 | 353 | 339 | 14 | 221 | 40 | 21 | 2885 |

**280 请求未闭合**。221是全计划已知解析失败，其中26属于已闭合请求；2885 pending 不属于已闭合的440槽。候选联合命中为新增27、累计185，不把覆盖率或strict有效率称作总体accuracy。失败保留原分母，不填零误差。

[STATUS16.json](STATUS16.json)和[STATUS16.csv](STATUS16.csv)保留16路训练、数据、模型、包SHA及加载/续训/test证据。全部仍为PROVISIONAL_PARTIAL：5–16 GHz正反向各12000 updates；17/18/19/20各8875/5538/3357/1744。19/20 GHz 反向 ramp 未完成；保存、加载、诊断续训和测试PASS不等于充分收敛。176万SELF_PROXY候选复用，不重跑。

正式accepted_sequence=1..10000保留全部56点，共同geometry-hash划分、有效train-only normalizer；各频率有效train/val/test数不同，不能说10000全部梯度训练。

**15 GHz身份分开：**正式10K模型f15-strict_lumped-f5f2ced054ef freshEMX为NOT_RUN；物理15GHz仅来自开发5K模型f15-5a9547401a37，累计25求解/22strict，不填入正式15GHz列（NOT_SCOPED）。随机10000物理请求为另一未运行框架。

状态保留 new_s31_physical_figures=PENDING_EXACT_QA 及旧S19/S25图时间线；之后逐图精确 GO 见[PUBLIC_RECEIPT.json](PUBLIC_RECEIPT.json)，不静默改已封存字段。

## 新增六组：固定第三请求

每请求原始Q=10..20全11槽；尾缀000002，顺序为既有HELDOUT_TRIPLE_AUDIT，不按结果改抽样。

| GHz | q_proxy | q_emx | 求解 | strict | invalid | GDS失败 | DRC失败 | 联合命中候选 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 11 | — | 9 | 9 | 0 | 2 | 0 | 5 |
| 20 | 13 | — | 9 | 7 | 2 | 2 | 0 | 0 |
| 6 | 10 | — | 6 | 6 | 0 | 2 | 3 | 3 |
| 7 | 10 | — | 9 | 9 | 0 | 1 | 1 | 4 |
| 8 | 14 | — | 9 | 9 | 0 | 1 | 1 | 6 |
| 9 | 13 | 12 | 11 | 11 | 0 | 0 | 0 | 9 |

逐条见[NEW6_REQUEST_STATUS.csv](NEW6_REQUEST_STATUS.csv)。预选候选相对目标的绝对百分比误差如下，实际目标/EMX/网格代理/带符号残差见[NEW6_SELECTED_PHYSICS.csv](NEW6_SELECTED_PHYSICS.csv)。

| GHz | Lp (%) | Ls (%) | Q_scalar (%) | abs(k) (%) | 绝对联合容差 |
|---|---:|---:|---:|---:|---|
| 10 | 0.109 | 0.592 | 1.196 | 0.701 | HIT |
| 20 | 1.567 | 1.448 | 3.573 | 31.277 | NOT_HIT |
| 6 | 0.261 | 0.719 | 0.344 | 4.051 | HIT |
| 7 | 0.264 | 0.229 | 0.675 | 0.533 | HIT |
| 8 | 0.226 | 0.352 | 2.617 | 2.058 | HIT |
| 9 | 0.322 | 1.220 | 0.572 | 2.200 | HIT |

**20 GHz预选Q13：strict有效但NOT_HIT。** abs(k)带符号残差+0.041373774013270415超过绝对容差0.04，目标相对误差31.27710869141991%。同请求原Q10/Q12为strict-invalid、below_half_srf=false，原secondary SRF brackets分别36–37/38–39GHz；它们不是Q13，不能把其标签无效解释套到Q13。有限descriptor不进入strict主误差。

新增8项GDS失败和5项Calibre DRC失败均保留原记录，见公开收据的failure_flags源路径/SHA；不推断更细版图根因，不替换候选，也不把GDS PASS当成DRC原因。

## 共同评价与区间边界

四目标为 [Lp, Ls, Q_scalar, abs(k)]，Q_scalar=min(Qp,Qs)，对称匹配。频率只选择单频 256×3 Tandem MLP，不是第五个模型输入。新几何的实际 EMX 与 SELF_PROXY 不能混称；本包物理证据不会自动验证另一次模型调用产生的几何。

[METRICS.csv](METRICS.csv) 原样保留 frequency/model/scope、`all_candidates` 与 `selected_q_proxy`、strict 主误差与有限值诊断、`emx_minus_target` 与 `emx_minus_frozen_proxy` 的独立行。冻结代理必须对应同一已导出网格几何，不能换成连续几何代理。主指标为物理单位 MAE/RMSE/Bias/绝对误差 P50/P90/P95；Bias 符号是 EMX 减参考。Lp/Ls 单位 nH，Q 与 abs(k) 无量纲。失败保留在原分母和联合命中计数，不填零误差。

冻结归一化跨度为 [2.5 nH, 2.5 nH, 20, 0.8]，绝对联合容差为 [0.125 nH, 0.125 nH, 1, 0.04]。百分比为 `100*abs(EMX-target)/abs(target)`，仅诊断，近零分母沿用原缺失/不稳定语义。不能把统一 5% 相对误差当联合容差。

q_proxy 在 EMX 前冻结。**原 11 候选全部 strict-valid 才允许 q_emx**，不在幸存者中选冠军。新增 9 GHz 第三请求满足此门，q_proxy=13、q_emx=12、selection_loss=0.0009579096860278555。S31 累计完整11请求共 **5**：9 GHz 第二与第三请求、14 GHz 第一与第二请求、18 GHz 第二请求。

[SELECTION_COMPARISON.csv](SELECTION_COMPARISON.csv) 只在相同完整请求集内比较固定 Q15 / q_proxy / q_emx，按频率/模型/scope 分组；其余组为 NOT_AVAILABLE。selection_loss 是冻结评分差，不是百分比误差或训练 loss。

95% 区间采用整 request_id 的描述性 percentile bootstrap，2000 次、seed=2026090801，绝不把 11 个 Q 候选当 IID。R1 为 NOT_ESTIMABLE；R2/R3 为 PROVISIONAL_SMALL_REQUEST_N。固定预选请求未证明代表部署总体，不作总体 accuracy、跨频率因果排名或“物理精度冠军”宣称。

## 精确批准的图

12张请求图=4原PASS+8修复GO；5张累计汇总图全部GO。修正只改显示，原10张显示NO-GO证据保留；运行renderer/consumer、网络与物理合同均未改。PNG及对应完整PDF均经实际视觉验收，PDF/SVG私有完整路径及SHA可从精确收据追溯。

| GHz / 第三请求 | 评分图 | 各 Q 四项百分比图（标示预选 Q） |
|---|---|---|
| 10 | [评分](f10_third_score.png) | [百分比](f10_third_percent.png) |
| 20 | [评分](f20_third_score.png) | [百分比](f20_third_percent.png) |
| 6 | [评分](f06_third_score.png) | [百分比](f06_third_percent.png) |
| 7 | [评分](f07_third_score.png) | [百分比](f07_third_percent.png) |
| 8 | [评分](f08_third_score.png) | [百分比](f08_third_percent.png) |
| 9 | [评分](f09_third_score.png) | [百分比](f09_third_percent.png) |

- [16路物理分母与状态](route_status.png)
- [全部strict候选 MAE/P95](formal_all_candidates_mae_p95.png)
- [预选q_proxy MAE/P95](formal_selected_q_proxy_mae_p95.png)
- [归一化MAE热图](formal_normalized_mae_heatmap.png)
- [完整11请求共同集选择比较](formal_complete11_selection_v3.png)

## 调用、预算与证据

[统一模型库调用说明](../FREQUENCY_LIBRARY_USAGE_CN.md)；[既有Q支持域审计](../frequency_library_qsupport_20260908_v1/README_CN.md)：全部16路Q20超出观察到的strict train边缘范围，联合可达性UNKNOWN，不等于物理不可达。不截断目标，不取最近频率，不换冻结权重。

18:00 UTC派发、18:15 UTC报告是现有工程配置时限；用户亲自指定精确时刻的来源未证明，meeting_time=UNCONFIRMED。此次仅审计来源与逐stage截止/原生PARTIAL或FAIL可能性，没有修改任何运行deadline，未宣称自动延期或会议时间已确认。

[PUBLIC_RECEIPT.json](PUBLIC_RECEIPT.json)保存24项产物pins、17图精确GO映射、源/公开双SHA；[SHA256SUMS](SHA256SUMS)覆盖其余25文件。NEW6两CSV与STATUS CSV逐字节复制；METRICS/SELECTION仅CRLF→LF，STATUS JSON仅私有路径相对化并标PRIVATE_NOT_INCLUDED。模型、训练、数值及状态原值不变。

S31统计SHA 30893a768643367b53ae9e2e804dead319bea5a01f56af7427de5172fe5c6c00；数值GO SHA b4213f4c9e2f693ea1dd8830c576a0bec12fec6c116fbeddf5502aae6c894d58；状态GO SHA 713736ee4da14ba968003aa8ccf7aaf1b2a23996b8a787cdcf573308af535ca0。公开包不含私有权重、完整数据、PDK、凭据或私有绝对路径；相对路径与SHA不表示私有源已上传。

原320请求目标尚未完成；本包是可汇报的冻结增量，不是最终模型精度或充分收敛声明。
