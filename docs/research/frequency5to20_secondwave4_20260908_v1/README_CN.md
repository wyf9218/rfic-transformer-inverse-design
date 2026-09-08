# 5–20 GHz：累计20请求与第二轮4请求补充

统计创建于2026-09-08T11:17:57.612361UTC（snapshot_0011），原生快照包创建于11:18:01.601051UTC；不是本补充报告的发布时间。
这是固定HELDOUT_TRIPLE_AUDIT请求，不是随机10000请求的真实EMX验证。

累计20个请求、220个原始Q槽：176次独立求解，167项严格有效、9项严格无效。
另11项解析失败、20项GDS失败、13项Calibre失败：220 = 167 + 9 + 11 + 20 + 13。
全320请求计划中尚未accounted的300个请求不算完成；全计划221项预先解析失败也不混成本批失败。

## 新完成的四个第二请求

全部保留原始Q=10..20的11候选，q_proxy是EMX前冻结的选择。四个请求均不具备原11全部严格有效的条件，因此q_emx仍为空。

| 路线 | 数据/模型范围 | 预选Q | 实际求解 | 严格有效 | 严格无效 | 求解前失败 | 预选结果 |
|---|---|---:|---:|---:|---:|---|---|
| 15 GHz | 开发5K | 11 | 8 | 5 | 3 | 3解析 | 严格联合命中 |
| 5 GHz | 正式10K | 10 | 10 | 10 | 0 | 1Calibre | 严格联合命中 |
| 10 GHz | 正式10K | 12 | 9 | 9 | 0 | 2GDS | 严格联合命中 |
| 20 GHz | 正式10K | 15 | 9 | 8 | 1 | 2GDS | 严格无效，不计命中 |

新增44原槽 = 36求解 + 3解析 + 4GDS + 1Calibre；36求解中32严格有效、4严格无效。
逐项状态见[NEW4_REQUEST_STATUS.csv](NEW4_REQUEST_STATUS.csv)。

## 预选几何的真实EMX目标误差

以下仅列三个严格有效预选个案的目标相对绝对百分比，不能作为总体精度估计。

| 路线/预选Q | Lp误差% | Ls误差% | Qmin误差% | 耦合系数误差% |
|---|---:|---:|---:|---:|
| 开发15 GHz / Q11 | 0.469001 | 1.052725 | 0.721536 | 1.496521 |
| 正式5 GHz / Q10 | 0.125247 | 0.014639 | 0.824154 | 0.111166 |
| 正式10 GHz / Q12 | 0.308154 | 0.125882 | 0.988725 | 1.274109 |

严格联合命中按固定绝对容差[Lp 0.125nH, Ls 0.125nH, Qmin 1, |k| 0.04]判定，不是各目标相对误差5%，也不是Qmin必须大于目标Q。
评分仍为sqrt(mean(((observed-target(Q))/[2.5,2.5,20,0.8])**2))；精确同分选较小Q。

20 GHz / Q15是必须保留的负结果：原descriptor有效，但below_half_srf=false、strict_lumped_valid=false。
原记录的SRF bracket为39–40GHz；没有修改提取门限。其|k|绝对残差0.064480278也超过0.04。
仅作有限等效描述诊断的四项相对误差为5.362958%、1.702194%、4.085037%、23.288445%；这些值不进入strict主精度，也没有改选其他Q。
全部16条特征记录及target/actual/frozen-proxy/两种残差/百分比/strict标签见[NEW4_SELECTED_PHYSICS.csv](NEW4_SELECTED_PHYSICS.csv)。

## 累计16条路线状态

正式源快照为10000唯一几何，56频点完整保留；不是每频10000个gradient-training样本。
固定geometry-hash split、train-only normalizer和256×3 Tandem结构不变。
16组均完成首预算、保存/重载/诊断续训/留出测试；每频10000×11Q代理压力测试已完成，共176万SELF_PROXY逻辑候选。
所有模型仍PROVISIONAL/PARTIAL，19/20GHz预算结束时反向ramp未完成，不宣称充分收敛。

| GHz | 训练 | 测试 | 已关闭请求 | EMX求解 | 严格有效 | 严格无效 |
|---|---|---|---:|---:|---:|---:|
| 5 | 首预算/PARTIAL | 已完成 | 2/20 | 18 | 18 | 0 |
| 6 | 首预算/PARTIAL | 已完成 | 1/20 | 8 | 8 | 0 |
| 7 | 首预算/PARTIAL | 已完成 | 1/20 | 7 | 7 | 0 |
| 8 | 首预算/PARTIAL | 已完成 | 1/20 | 9 | 9 | 0 |
| 9 | 首预算/PARTIAL | 已完成 | 1/20 | 8 | 8 | 0 |
| 10 | 首预算/PARTIAL | 已完成 | 2/20 | 18 | 18 | 0 |
| 11 | 首预算/PARTIAL | 已完成 | 1/20 | 9 | 9 | 0 |
| 12 | 首预算/PARTIAL | 已完成 | 1/20 | 10 | 10 | 0 |
| 13 | 首预算/PARTIAL | 已完成 | 1/20 | 9 | 9 | 0 |
| 14 | 首预算/PARTIAL | 已完成 | 1/20 | 11 | 11 | 0 |
| 15* | 首预算/PARTIAL | 已完成 | 2/20 | 17 | 14 | 3 |
| 16 | 首预算/PARTIAL | 已完成 | 1/20 | 8 | 8 | 0 |
| 17 | 首预算/PARTIAL | 已完成 | 1/20 | 6 | 6 | 0 |
| 18 | 首预算/PARTIAL | 已完成 | 1/20 | 10 | 8 | 2 |
| 19 | 首预算/PARTIAL | 已完成 | 1/20 | 9 | 6 | 3 |
| 20 | 首预算/PARTIAL | 已完成 | 2/20 | 19 | 18 | 1 |

*15 GHz物理使用冻结开发5K模型，正式10K15的freshEMX仍NOT_RUN。精确模型ID、包SHA及各频train/val/test见[STATUS16.json](STATUS16.json)。
累计仅14GHz首请求具备11/11严格有效，旧精确共同集选择对比不变，不从其他请求幸存子集选q_emx。

## 不确定性和分母

5/10/15/20GHz各有2个已关闭请求，其余各1个。R表示不同请求组的数量，不暗示这些固定预选请求已证明统计独立；不能把一个请求的11个Q当11个独立目标。
R=2的95%整请求bootstrap区间只作有限样本描述，标PROVISIONAL；相同结果造成退化区间不表示高确定性。
20GHz新的预选Q无效，因此其selected_q_proxy strict统计仍只有R=1，CI不可估计；all-candidate或有限描述诊断的R=2另列。
MAE/RMSE/Bias/P50/P90/P95用物理单位；Bias带符号，其余分位数为绝对残差。EMX-target与EMX-frozen-proxy分开。
不作总体准确率、物理不可达、跨频率因果优劣或模型冠军结论。

## 已核验图表

图均来自精确保存CSV/JSON。显示修复仅调整近零标记可见性与失败文字，不改变数值、预选Q或失败判定。

- [累计请求/候选状态](route_status.png)
- [严格有效预选Q的MAE/P95](formal_selected_q_proxy_mae_p95.png)
- [全部严格有效候选的MAE/P95](formal_all_candidates_mae_p95.png)
- [固定跨度归一化MAE热图](formal_normalized_mae_heatmap.png)
- 开发15GHz：[评分](f15_second_score.png) / [逐Q百分比](f15_second_percent.png)
- 正式5GHz：[评分](f05_second_score.png) / [逐Q百分比](f05_second_percent.png)
- 正式10GHz：[评分](f10_second_score.png) / [逐Q百分比](f10_second_percent.png)
- 正式20GHz：[评分](f20_second_score.png) / [逐Q百分比](f20_second_percent.png)

原失败图和既有PPT/首轮报告均保留；本补充不覆盖旧快照。各图的精确批准范围见PUBLIC_RECEIPT.json及SHA256SUMS。
数值独立核对包括3520原身份、3476未变行、128受影响metric记录/768误差数值和整请求CI；未导入或调用统计producer重算流程。

## 运行边界

11:23:36UTC只读现场核验：原MARS队列2146776存活，6GHz第二请求Q10完成，随后进入RESOURCE_WAIT。
1分钟load192.521、192逻辑CPU；这是负载指标，不是CPU使用率百分比。既有资源门自动等待，不扩容抢占生产。
原只读消费者50120继续汇总，首16collector已自然结束，不重启、不重收首16。
全局研究最多4个EMX×2CPU、Cadence1、Calibre1；18:00UTC停止新增派发、18:15UTC报告截止，不kill健康求解器。
新绘图入口为离线standalone，未热升级运行中的v1 renderer/consumer；不重训、不重推理、不重生成。
