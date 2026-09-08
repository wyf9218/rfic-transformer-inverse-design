# 5–20 GHz：新增 6 请求与累计 34 请求的真实 EMX 结果

固定快照 **S25** 的统计时间为 **2026-09-08 13:30:30.769638 UTC**，封装时间为 **13:30:35.054959 UTC**，不是实时服务器状态。本包只整理相对 S19 新闭合的 6 请求：16/17/18/19 GHz 第二请求、开发 5K 的 15 GHz 第三请求、正式 10K 的 5 GHz 第三请求。

**新增 66 原始候选、48 独立求解、46 strict-valid、2 strict-invalid；另有 15 analytic 失败、3 GDS 失败。** 这 6 请求全部闭合，不隐藏失败、不换预选 Q。训练、数据、模型、预算和物理管线均未改变；本次打包不新增推理、求解或统计计算。

## 分母与完成状态

| 集合 | 请求 | 原始候选 | 独立求解 | strict-valid | strict-invalid | analytic fail | GDS fail | DRC fail | pending |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 本次新增、已闭合 | 6 | 66 | 48 | 46 | 2 | 15 | 3 | 0 | 0 |
| S25 累计、已闭合 | 34 | 374 | 300 | 288 | 12 | 26 | 32 | 16 | 0 |
| 全部固定计划框架 | 320 | 3520 | 300 | 288 | 12 | 221 | 32 | 16 | 2951 |

全框架尚有 **286 请求未闭合**。221 是全部计划已知的 analytic 失败，其中 26 属于已闭合的 34 请求；2951 pending 不属于已闭合的 374 槽。候选联合命中计数为新增 **26**、累计 **158**。报告命中率必须说明对应的是原始已闭合分母还是 strict 分母，不把全计划覆盖率当最终准确率。

[STATUS16.json](STATUS16.json) 与 [STATUS16.csv](STATUS16.csv) 保留 16 路既有训练、加载/续训、测试、模型身份与包 SHA，仅从 S25 映射物理计数。全部模型仍 **PROVISIONAL_PARTIAL**：5–16 GHz 正反向各 12000 updates，17/18/19/20 GHz 分别为 8875/5538/3357/1744，19/20 GHz 反向 ramp 未完成。保存、重载/诊断续训与测试 PASS 不等于充分收敛。

状态文件保留封存时的 `new_s25_physical_figures=PENDING_EXACT_QA`，不静默改已批准字段。之后逐图精确 GO 单独列于 [PUBLIC_RECEIPT.json](PUBLIC_RECEIPT.json)；这是证据时间顺序，不表示本包图未经过验收。

**15 GHz 身份严格分开：**正式 10K 模型 `f15-strict_lumped-f5f2ced054ef` 的新几何 fresh EMX 为 **NOT_RUN**；本包 15 GHz 物理结果来自开发 5K 模型 `f15-5a9547401a37`。开发物理不补入正式 10K 图的 15 GHz 列，该列为 NOT_SCOPED。正式共同数据快照为 accepted_sequence=1..10000，但各频率有效 train/val/test 数量不同，不能称 10000 全部参加梯度训练。

## 新增请求与预选候选

所有请求来自既有冻结 `HELDOUT_TRIPLE_AUDIT` 原始顺序，每请求保留 Q=10..20 的 11 槽。下表“第二/第三”分别对应 request_id 尾缀 `000001/000002`，并非按结果重新抽样。

| GHz / 模型scope | 请求序号 | q_proxy | q_emx | 求解 | strict | invalid | analytic fail | GDS fail | 联合命中候选 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 / 正式10K | 第二 | 12 | — | 7 | 7 | 0 | 3 | 1 | 4 |
| 17 / 正式10K | 第二 | 12 | — | 5 | 3 | 2 | 6 | 0 | 2 |
| 18 / 正式10K | 第二 | 17 | 17 | 11 | 11 | 0 | 0 | 0 | 6 |
| 19 / 正式10K | 第二 | 14 | — | 7 | 7 | 0 | 3 | 1 | 6 |
| 15 / 开发5K | 第三 | 12 | — | 8 | 8 | 0 | 3 | 0 | 5 |
| 5 / 正式10K | 第三 | 11 | — | 10 | 10 | 0 | 0 | 1 | 3 |

完整身份和失败分母见 [NEW6_REQUEST_STATUS.csv](NEW6_REQUEST_STATUS.csv)。本次六个 q_proxy 预选候选的已保存 strict/联合命中标记均为 True，但不能据此宣称模型或全部候选“100%准确”。

预选候选相对目标的绝对百分比误差如下；完整目标、EMX 值、冻结网格代理、带符号残差和有效性标记见 [NEW6_SELECTED_PHYSICS.csv](NEW6_SELECTED_PHYSICS.csv)。

| GHz / scope | Lp (%) | Ls (%) | Q_scalar (%) | abs(k) (%) |
|---|---:|---:|---:|---:|
| 16 / 正式10K | 0.764 | 0.180 | 0.671 | 4.160 |
| 17 / 正式10K | 0.391 | 0.421 | 1.144 | 3.101 |
| 18 / 正式10K | 2.954 | 0.376 | 0.909 | 3.764 |
| 19 / 正式10K | 1.308 | 0.899 | 1.420 | 5.052 |
| 15 / 开发5K | 0.207 | 0.510 | 0.453 | 1.765 |
| 5 / 正式10K | 0.419 | 0.183 | 0.314 | 0.935 |

### 失败与无效原标签

17 GHz 第二请求 **Q10 与 Q13** 原记录均为 descriptor_valid=true、physics_qa_pass=true、strict_lumped_valid=false、below_half_srf=false；primary SRF 均为 33–34 GHz bracket。有限 descriptor 值仅作非 strict 诊断，不进入 strict 主误差。这是原提取判定，不是本轮重提取或新增因果诊断；二者不是该请求预选 Q12。

三项 GDS 失败是 **16 GHz 第二请求 Q15、19 GHz 第二请求 Q16、5 GHz 第三请求 Q10**。原记录均 cadence_routed=true、calibre_eligible=false，failed_checks 为 `ground_clearance_pass`、`foundry_power_line_contract_pass`、`foundry_via_stack_and_landing_pad_pass`。仅保存原门禁字段，不据此猜测更细的版图根因；这些候选没有 EMX 真值。原失败字段文件及 SHA 在公开收据中定位，私有原件不包含在本包。

## 共同评价与区间边界

四目标为 [Lp, Ls, Q_scalar, abs(k)]，Q_scalar=min(Qp,Qs)，对称匹配。频率只选择单频 256×3 Tandem MLP，不是第五个模型输入。新几何的实际 EMX 与 SELF_PROXY 不能混称；本包物理证据不会自动验证另一次模型调用产生的几何。

[METRICS.csv](METRICS.csv) 原样保留 frequency/model/scope、`all_candidates` 与 `selected_q_proxy`、strict 主误差与有限值诊断、`emx_minus_target` 与 `emx_minus_frozen_proxy` 的独立行。冻结代理必须对应同一已导出网格几何，不能换成连续几何代理。主指标为物理单位 MAE/RMSE/Bias/绝对误差 P50/P90/P95；Bias 符号是 EMX 减参考。Lp/Ls 单位 nH，Q 与 abs(k) 无量纲。失败保留在原分母和联合命中计数，不填零误差。

冻结归一化跨度为 [2.5 nH, 2.5 nH, 20, 0.8]，绝对联合容差为 [0.125 nH, 0.125 nH, 1, 0.04]。百分比为 `100*abs(EMX-target)/abs(target)`，仅诊断，近零分母沿用原缺失/不稳定语义。不能把统一 5% 相对误差当联合容差；例如本次 19 GHz 的 abs(k) 百分比为 5.052%，并不否定其绝对容差命中。

q_proxy 在 EMX 前冻结。**原 11 候选全部 strict-valid 才允许 q_emx**，不在幸存者中选冠军。新增 18 GHz 第二请求满足此门，q_proxy=q_emx=17，selection_loss=0。S25 累计完整 11 请求共 **4**：9 GHz 第二请求、14 GHz 第一与第二请求、18 GHz 第二请求。

[SELECTION_COMPARISON.csv](SELECTION_COMPARISON.csv) 只在相同完整请求集内比较固定 Q15 / q_proxy / q_emx，按频率/模型/scope 分组；其余组为 NOT_AVAILABLE。selection_loss 是冻结评分差，不是百分比误差或训练 loss。

95% 区间采用整 request_id 的描述性 percentile bootstrap，2000 次、seed=2026090801，绝不把 11 个 Q 候选当 IID。R1 为 NOT_ESTIMABLE；R2/R3 为 PROVISIONAL_SMALL_REQUEST_N。固定预选请求未证明代表部署总体，不作总体 accuracy、跨频率因果排名或“物理精度冠军”宣称。

## 精确批准的图

本包 12 张请求 PNG = 6 原 PASS + 6 独立批准修复；5 张累计汇总 PNG 另有精确视觉收据。修复只改显示，原 NO-GO 与原数值保留；没有升级正在运行的旧 renderer/consumer。

| GHz / 请求 | Q 扫描评分图 | 各 Q 四项百分比图（标示预选 Q） |
|---|---|---|
| 16 / 第二 | [评分](f16_second_score.png) | [百分比](f16_second_percent.png) |
| 17 / 第二 | [评分](f17_second_score.png) | [百分比](f17_second_percent.png) |
| 18 / 第二 | [评分](f18_second_score.png) | [百分比](f18_second_percent.png) |
| 19 / 第二 | [评分](f19_second_score.png) | [百分比](f19_second_percent.png) |
| 15 / 第三，开发5K | [评分](f15_third_score.png) | [百分比](f15_third_percent.png) |
| 5 / 第三，正式10K | [评分](f05_third_score.png) | [百分比](f05_third_percent.png) |

- [16 路分层状态与原分母](route_status.png)
- [正式 10K：全部 strict 候选物理单位 MAE/P95](formal_all_candidates_mae_p95.png)
- [正式 10K：预选 q_proxy 物理单位 MAE/P95](formal_selected_q_proxy_mae_p95.png)
- [正式 10K：冻结跨度归一化 MAE 热图](formal_normalized_mae_heatmap.png)
- [共同完整 11 请求：固定 Q15 / q_proxy / q_emx](formal_complete11_selection_v3.png)

## 调用、缺口与证据

整数频率模型调用见[统一模型库说明](../FREQUENCY_LIBRARY_USAGE_CN.md)。[此前 Q 训练支持域审计](../frequency_library_qsupport_20260908_v1/README_CN.md)已显示全部 16 路 Q20 超过观察到的 strict train 边缘范围；这是训练支持域描述，不是物理不可达结论。本包不重复该审计。

仍有 286 固定请求未闭合，正式 15 GHz fresh EMX 未运行，高频训练仍为预算部分完成。默认不外推/不截断/不取最近频率的合同与既有运行预算保持不变，本包不是 320 请求任务完成声明。

[PUBLIC_RECEIPT.json](PUBLIC_RECEIPT.json) 记录 24 项产物 pins、全部源/图 GO 映射及变换；[SHA256SUMS](SHA256SUMS) 覆盖其余 25 文件。NEW6 两 CSV 与 STATUS16.csv 逐字节复制；METRICS/SELECTION 仅 CRLF→LF，源/公开双 SHA 保存。STATUS16.json 只把私有绝对路径机械相对化并标 PRIVATE_NOT_INCLUDED，模型、训练、数值和状态字段未改。

S25 数值收据 SHA `9dcc8b81d2c52f3ecfef100a716d92e1f681773cb69b52e5dd5b50af72f0c11b`；独立数字 GO SHA `5b2c9cb0344f015d2784bb69ec1b6c146e61e4916affa2b5d9996d7eeb53671b`；16 路状态 GO SHA `f807c4a90fc04e6a97adeefdaebc70b59edb49a1055f92f71141ca315d637a74`。图 QA 只批准精确图像字节，数字 QA 与公开静态检查分别留痕。

公开仅结果、状态和 PNG，不含完整私有数据、权重、PDK、认证信息或本机/服务器绝对路径。相对研究路径 + SHA 用于定位私有证据，不表示其文件已经上传。
