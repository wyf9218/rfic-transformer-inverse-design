# 5–20 GHz：第二轮新增 8 个固定请求的真实 EMX 增量

本包冻结于 **S19**：统计时间 **2026-09-08 12:38:23.642122 UTC**，快照封装时间 **12:38:27.721717 UTC**。这不是实时服务器状态。

本次新增 6、7、8、9、11、12、13、14 GHz 的第二个预选请求：**88 个原始候选，76 次独立 EMX 求解，75 个 strict-valid、1 个 strict-invalid，9 个 GDS 失败、3 个 DRC 失败**。负结果与未闭合请求均保留。本包不新增训练、推理、求解或统计计算，只公开已冻结结果及精确批准的图。

## 总分母与模型状态

| 集合 | 请求 | 原始候选 | 独立求解 | strict-valid | strict-invalid | analytic 失败 | GDS 失败 | DRC 失败 | pending |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 本轮新增、已闭合 | 8 | 88 | 76 | 75 | 1 | 0 | 9 | 3 | 0 |
| S19 累计已闭合 | 28 | 308 | 252 | 242 | 10 | 11 | 29 | 16 | 0 |
| 全部固定计划框架 | 320 | 3520 | 252 | 242 | 10 | 221 | 29 | 16 | 3002 |

全框架还有 **292 个请求未闭合**。221 是全部计划中已知的 analytic 失败，其中仅 11 个属于已闭合的 28 请求；**3002 pending 不属于这 28 个已闭合请求内部**。候选联合命中计数：本轮 42、累计 132；命中率必须与对应的已闭合原分母或 strict 分母同时报告，不把全框架覆盖率当最终准确率。

[16 频率状态 JSON](STATUS16.json) 与 [CSV](STATUS16.csv) 将训练、测试、物理验证和出图分列。16 对模型均完成已有预算下的真实权重更新、保存、重载/诊断续训与测试；训练仍标 **PROVISIONAL_PARTIAL**，不是“充分收敛”。5–16 GHz 正反向各 12000 updates；17/18/19/20 GHz 分别为 8875/5538/3357/1744，19/20 GHz 的 warmup/ramp 与训练预算尤其有限。共同正式快照是 accepted_sequence=1..10000，实际 train/val/test 有效标签数量按频率不同，详见状态表，不能把 10000 全部称为梯度训练样本。

**15 GHz 必须分开：**正式 10K 模型 `f15-strict_lumped-f5f2ced054ef` 的新几何 fresh EMX 为 **NOT_RUN**；本计划中的 15 GHz 物理结果属于开发 5K 模型 `f15-5a9547401a37`。正式 10K 汇总误差图的 15 GHz 列为 NOT_SCOPED，禁止用开发结果补入。

## 新增请求：预选 Q 与失败层级

全部请求来自既有冻结 `HELDOUT_TRIPLE_AUDIT` 的第二行，request_id 尾缀为 `000001`；不是按成功结果重新挑选。所有原始请求均有 Q=10..20 共 11 个槽。

| GHz | q_proxy | q_emx | 求解 | strict | invalid | GDS fail | DRC fail | 联合命中候选 | 预选候选结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 6 | 10 | — | 10 | 10 | 0 | 1 | 0 | 4 | strict，HIT |
| 7 | 11 | — | 10 | 10 | 0 | 0 | 1 | 5 | strict，HIT |
| 8 | 12 | — | 8 | 8 | 0 | 1 | 2 | 7 | GDS_FAIL，无 EMX 值 |
| 9 | 11 | 11 | 11 | 11 | 0 | 0 | 0 | 7 | strict，HIT |
| 11 | 11 | — | 9 | 8 | 1 | 2 | 0 | 3 | strict，但 NOT_HIT |
| 12 | 13 | — | 10 | 10 | 0 | 1 | 0 | 6 | strict，HIT |
| 13 | 13 | — | 7 | 7 | 0 | 4 | 0 | 4 | GDS_FAIL，无 EMX 值 |
| 14 | 16 | 14 | 11 | 11 | 0 | 0 | 0 | 6 | strict，HIT |

[逐请求原始计数 CSV](NEW8_REQUEST_STATUS.csv)；[预选候选四物理量、冻结代理及完整精度残差 CSV](NEW8_SELECTED_PHYSICS.csv)。空字段保留 null/缺失语义，不填 0、不替换成另一个成功 Q。

下表为 **q_proxy 预选候选的 EMX 相对目标绝对百分比误差**，只作诊断；实际值和目标值见完整 CSV。

| GHz | Lp (%) | Ls (%) | Q_scalar (%) | abs(k) (%) |
|---|---:|---:|---:|---:|
| 6 | 0.282 | 0.866 | 0.828 | 0.816 |
| 7 | 0.543 | 0.363 | 1.146 | 0.825 |
| 8 | — | — | — | — |
| 9 | 0.144 | 0.631 | 0.337 | 1.059 |
| 11 | 0.757 | 2.014 | 1.112 | 11.705 |
| 12 | 0.566 | 0.487 | 1.068 | 1.143 |
| 13 | — | — | — | — |
| 14 | 0.545 | 0.212 | 1.135 | 1.619 |

11 GHz 的预选 **Q11 本身 strict-valid**，但 abs(k) 残差 **+0.046436273081205515 > 0.04**，所以不是联合命中。同一请求另一个 **Q14** 是 strict-invalid，原标签 `below_half_srf=false`；其有限 descriptor 值只进非 strict 诊断，不进入 strict 主误差。不能把 Q14 的失败原因套到 Q11。

## 评价口径、共同集与不确定性

每个频率使用一对 256×3 Tandem MLP，频率只用于模型选择，输入四目标为 [Lp, Ls, Q_scalar, abs(k)]，其中 Q_scalar=min(Qp,Qs)。本次物理结果是新生成网格几何的实际 EMX，不等于代理自身误差，也不自动验证其他新调用的几何。

[METRICS.csv](METRICS.csv) 保留原表全部维度：frequency/model/scope、`all_candidates` 与 `selected_q_proxy`、`strict_valid` 与有限值诊断，以及 `emx_minus_target` / `emx_minus_frozen_proxy`。主误差只读 strict-valid 行。冻结代理必须来自同一已导出网格几何，不以连续几何代理替代。MAE、RMSE、Bias、绝对误差 P50/P90/P95 的单位分别为 nH、nH、无量纲、无量纲；Bias 的符号是 EMX 减参考。失败不进入连续误差均值，但保留在原始分母、状态及联合命中统计中。

冻结归一化跨度为 [2.5 nH, 2.5 nH, 20, 0.8]，绝对联合容差为 [0.125 nH, 0.125 nH, 1, 0.04]。百分比为 `100*abs(EMX-target)/abs(target)`；接近零的分母需保留源表缺失/诊断语义。百分比不等于上述容差，也不能用 5% 相对误差统一代替联合命中门槛。

`q_proxy` 在 EMX 前由冻结网格代理预选。**仅原始 11 个候选全部 strict-valid，才定义 q_emx**；其余请求保持 null，不从幸存者中选“冠军”。[SELECTION_COMPARISON.csv](SELECTION_COMPARISON.csv) 只在共同完整 11 候选请求上比较固定 Q15、q_proxy、q_emx：S19 共 **3 请求**（9 GHz 第二请求，14 GHz 第一及第二请求），仍按频率/模型/scope 分组，不混池。新增 9 GHz q_proxy/q_emx=11/11；14 GHz=16/14，冻结 `selection_loss=0.003159679249955162`，这是选择评分差，不是训练 loss 或百分比误差。

区间是 **按完整 request_id 重采样**的描述性 percentile bootstrap，95%、2000 次、固定 seed=2026090801；不把同一请求的 11 候选当 IID。每路 R1 时 NOT_ESTIMABLE；R2 时仅 PROVISIONAL_SMALL_REQUEST_N。预选有限请求不是已证明的部署总体随机样本，不作总体 accuracy、跨频率因果归因或充分收敛/物理精度冠军宣称。

## 已验收图：16 请求图与 5 汇总图

图内为原始冻结数据，不重算。本轮保留 8 张原 PASS 图；另 8 张仅修布局并经独立 PNG+完整 PDF 页验收后使用 v3。原 NO-GO 与失败证据未删除，正在运行的旧消费者/renderer 未因此升级。

| GHz | Q 扫描评分图 | 各Q四项百分比图（标示预选Q） |
|---|---|---|
| 6 | [评分](f06_second_score.png) | [百分比](f06_second_percent.png) |
| 7 | [评分](f07_second_score.png) | [百分比](f07_second_percent.png) |
| 8 | [评分](f08_second_score.png) | [缺失/失败状态](f08_second_percent.png) |
| 9 | [评分](f09_second_score.png) | [百分比](f09_second_percent.png) |
| 11 | [评分](f11_second_score.png) | [百分比](f11_second_percent.png) |
| 12 | [评分](f12_second_score.png) | [百分比](f12_second_percent.png) |
| 13 | [评分](f13_second_score.png) | [缺失/失败状态](f13_second_percent.png) |
| 14 | [评分](f14_second_score.png) | [百分比](f14_second_percent.png) |

- [16 路分层状态与原分母](route_status.png)
- [正式 10K：全部 strict-valid 候选物理单位 MAE/P95](formal_all_candidates_mae_p95.png)
- [正式 10K：预选 q_proxy 物理单位 MAE/P95](formal_selected_q_proxy_mae_p95.png)
- [正式 10K：冻结跨度归一化 MAE 热图](formal_normalized_mae_heatmap.png)
- [共同完整 11 请求的固定 Q15 / q_proxy / q_emx 对照](formal_complete11_selection_v2.png)

## 调用入口与尚未完成项

整数频率模型路由、默认不外推/不截断/不取最近频率的接口见[统一模型调用说明](../FREQUENCY_LIBRARY_USAGE_CN.md)。全部 16 路 Q20 高于已观测 strict train 的 Q 边缘范围，证据见[模型库与 Q 支持域](../frequency_library_qsupport_20260908_v1/README_CN.md)；这是训练支持范围不足，不能据此宣称物理不可达。

尚未闭合的 292 个固定请求不是已完成；本包不更改调度预算或派发任务。正式 15 GHz fresh EMX、更多独立请求及更充分的高频训练仍有缺口。本包不声称已由这些有限结果选出最优模型。

## 证据、可复查范围与文件字节

[PUBLIC_RECEIPT.json](PUBLIC_RECEIPT.json) 给出全部公开文件 pins、原始来源与 QA 映射；[SHA256SUMS](SHA256SUMS) 覆盖其余 29 文件。独立数值 QA 仅批准对应数字口径，视觉 GO 仅批准精确图像字节；包还需独立静态发布检查。

S19 数值收据 SHA `7a7e51f384303c918c1a3111476ec965bf6d4198c40f5dbf14e7e762ddf4d433`；本轮数值 QA SHA `75439bf9fb85923953d3a42dbfa2534d4eff3f993cf6d6313f42a73139121e8d`；16 行状态 QA SHA `ef3433cf845425dbc9d651df90127d81d7fd795e5691786a667ea712a968ff39`。8 张 v3 独立 GO SHA `1a204bb1745df88b226165e6e0152674aa9052b24c7ee18c154ae089ac5475bf`；5 汇总图 GO SHA `7e0f2da651d79116ab6b54135efc8b024b60758486aaf7fced7aee34defa03d8`。

两份 NEW8 CSV 与原 LF 文件逐字节相同。METRICS/SELECTION_COMPARISON 仅 CRLF→LF，原始 SHA 与公开 SHA 均记录；数值、字段、顺序和缺失标记不变。公开包只含汇总/选定候选结果、状态和 PNG；私有完整原始数据、权重、PDK、认证信息及本机/服务器绝对路径均不包含。receipt 中以相对研究路径表示的私有源明确标 PRIVATE_NOT_INCLUDED。
