# 模拟变压器 AI 反向建模：科研与工程交接（2026-08-23）

本文件是后续研究者或 GPT 的首要中文入口。它总结本轮工作已经形成的可复核证据、尚未完成的目标、禁止越界的结论，以及本仓库内可直接复用的代码。仓库是脱敏代码与科研状态快照；真实 EMX 数据、模型权重、GDS、PDK、许可证、服务器身份和私有运行路径不在仓库中。

## 一句话结论

周一汇报所需的底层证据已经完成大半：历史 100k/200k 模型结构已核实，固定 10,000 物理目标上的代理模型统计已完成，10,000 目标经过物理实现漏斗后已有 7,298 个 fresh EMX 成功结果；但严格 nested 100k/200k 控制变量训练尚在运行，fresh-EMX 最终误差图表因发布接口独立审计未通过而不能作为正式结论，最终 PPT/HTML 也尚未封版。

按八个里程碑计，当前是 **4 项完成、1 项运行中、3 项待完成或被门禁阻断**。这比给出一个模糊百分比更能反映实际进度。

## 当前研究问题

1. 历史 200k 模型结构是什么，与 deployed 100k 模型相比表现如何？
2. 在同一冻结的 10,000 目标上，两者的代理闭环误差是多少？
3. 若严格控制架构、decoder、split、训练预算、seed 和推理流程，仅改变数据规模，nested 100k 与 200k 是否有可归因的提升？
4. 模型输出的几何经过解析、Cadence、Calibre 和 fresh EMX 后，真实电磁误差是多少？
5. 当前合同下把 `|K|` 扩展到小于 1，并把累计数据扩展到 200k/300k/400k/500k 时，精度和训练成本如何变化？

## 已核实的模型结构

“100k/200k”在这里首先表示 source-table rows，不表示所有行都参与梯度训练。

| 项目 | deployed 100k | historical 200k |
|---|---:|---:|
| source-table rows | 100,000 | 200,000 |
| gradient-training rows | 78,891 | 161,446 |
| validation rows | 9,740 | 19,135 |
| test rows | 11,369 | 19,419 |
| inverse network | 4→512→512→256→10 | 4→128→128→10 |
| forward network | 10→256→256→128→4 | 10→128→128→4 |
| inverse parameters | 399,114 | 18,442 |
| forward parameters | 102,020 | 18,436 |
| total parameters | 501,134 | 36,878 |
| decoder | hard-feasible + Q-minimum guardband | independent sigmoid |

这两个历史模型的架构、decoder、数据来源和训练合同不同，因此只能称为**非受控现状描述**。不能把二者差异归因于“数据由 100k 增至 200k”。真正回答数据规模因果问题的是仍在运行的 nested paired experiment。

## 冻结 10,000 物理目标

- 10,000 个目标是 deterministic centered Latin-hypercube finite coverage frame，seed=`20260810`。
- 目标完全唯一；其中 legacy panel 8,000 个，范围 `|K|≤0.8`；extension panel 2,000 个，范围 `|K|>0.8`。
- 它不是 iid 随机样本，也不是 10,000 条 EMX 标签。正式表述应为“冻结的有限覆盖目标框架”。
- 冻结目标文件 SHA-256：`c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407`。

### 固定目标上的历史代理闭环统计

| panel | deployed 100k joint proxy RMSE | historical 200k joint proxy RMSE | 200k 相对降低 |
|---|---:|---:|---:|
| all 10,000 | 0.245843 | 0.202643 | 17.57% |
| legacy 8,000 | 0.221335 | 0.184191 | 16.78% |
| extension 2,000 | 0.325940 | 0.263850 | 19.05% |

all-10k 各物理量 MAE：

| 指标 | deployed 100k | historical 200k |
|---|---:|---:|
| `Lp` MAE (nH) | 0.44947 | 0.24884 |
| `Ls` MAE (nH) | 0.39612 | 0.30137 |
| `Q` MAE | 4.43261 | 3.92985 |
| `|K|` MAE | 0.17840 | 0.14731 |
| Q target met | 37.83% | 29.84% |
| Q shortfall | 3.6103 | 3.3478 |

这些结果说明 historical 200k 在 joint proxy RMSE 和多数连续误差上更好，但 Q 达标率反而低 7.99 个百分点。因此不能只汇报一个总 RMSE，更不能宣称全面优化。

历史 200k 报告中的 0.925% 属于 held-out manifold、40×4 refinement 且 `|K|≤0.8` 的另一任务；固定 10,000 是 one-shot、范围更宽的目标覆盖。两者分母和推理流程不同，不能作为同一“准确率”直接比较。

## 真实物理链与 fresh EMX

固定 10,000 目标的实现漏斗如下：

| 阶段 | 数量 | 相对上一阶段损失 |
|---|---:|---:|
| 原始冻结目标 | 10,000 | — |
| 解析可实现 | 7,926 | 2,074 |
| Cadence 成功 | 7,373 | 553 |
| Calibre 通过 | 7,298 | 75 |
| fresh EMX 成功 | 7,298 | 0 |

fresh EMX 幸存集合中 legacy=5,992、extension=1,306。Stage06 已终态 PASS，7,298/7,298 个 S4P 完成，没有失败 shard。

但这个集合是经过实现与版图门禁筛选后的 survivor set，缺失不是随机的（MNAR）。因此正式误差必须同时分开报告：

1. target → EMX；
2. target → proxy；
3. proxy → EMX；
4. 原始 10,000 的各阶段失败率；
5. all-survivors、legacy-survivors、extension-survivors 的各自分母与区间。

在报告接口独立审计通过前，不得把 7,298 个幸存样本的准确率外推成原始 10,000 的准确率。

## 严格控制变量实验

正在运行的是 nested 100k/200k paired experiment。唯一目标是隔离“实际 gradient-training rows 的数据规模”影响；架构、decoder、数据源、split、训练预算、seed 和推理流程必须冻结。

最近一次只读状态：10 个训练臂中 7 个终态，5 个 seed-pair 中 3 对完整；rep4-small 已完成，rep4-large 待资源负载满足冻结阈值后启动，rep5-small/large 尚未启动。监督进程仍存活并按 no-clobber 合同等待，禁止手动绕过或重复启动。

完成后必须使用 paired replicate statistics，至少报告每个 seed 的差值、均值/中位数、bootstrap 或配对区间、效应方向一致性，以及失败臂。只有这个实验可以支持“由于数据规模增加而提升”的因果表述。

## 已确认的 NO-GO

失败是科研证据，不能删除或改名为成功。

- RQ-I fixed10k release v7：正式 NO-GO，P0/P1/P2/P3=`0/2/0/0`。问题包括 post-PASS root replacement 仍可能保留 PASS-only，以及 manifest 之前出现的未注册文件可被自动纳入。
- report interface compatibility v7：正式 NO-GO，P0/P1/P2/P3=`0/3/0/0`。问题包括输出接口被替换后 receipt/index 仍引用旧 SHA、41 个角色可别名成 40 个唯一身份、以及把 survivor 准确率外推到原始 10,000 的文案仍可通过验证。
- Monday report partial v3：视觉 NO-GO，存在重复树、红色阻断项和横向溢出。
- v8/v4 两个后继工作目录只是未验证 WIP，不能视为正式修复。

详细问题见 [KNOWN_NO_GO_20260823.md](KNOWN_NO_GO_20260823.md)。

## 八个里程碑状态

| # | 里程碑 | 状态 |
|---:|---|---|
| 1 | 历史 100k/200k 模型结构与证据 | COMPLETE |
| 2 | 冻结 10,000 目标生成与唯一性 | COMPLETE |
| 3 | 历史模型 fixed10k proxy 对比 | COMPLETE |
| 4 | 10,000→7,298 物理漏斗与 fresh EMX 生成 | COMPLETE |
| 5 | nested 100k/200k 严格控制变量训练 | RUNNING（7/10 arms，3/5 pairs） |
| 6 | fresh EMX 正式误差统计与柱状图 | BLOCKED BY INTERFACE NO-GO |
| 7 | 周一最终 HTML/PPTX | NOT FINALIZED |
| 8 | 当前合同 200k/300k/400k/500k、`|K|<1` 学习曲线 | NOT STARTED/NOT COMPLETE |

## 本仓库中可复用的代码

- `rfic_transformer_inverse_design/`：可复用的版图、仿真、数据质量、特征抽取、建模和拆分模块。
- `scripts/train_physical_feature_tandem_inverse.py`：冻结 forward surrogate 的 tandem inverse 训练主程序。
- `scripts/build_controlled_data_scaling_split.py`：构造 nested data-scaling split。
- `scripts/audit_controlled_subset_overlap.py`：审计控制组与扩大组的嵌套关系和泄漏风险。
- `scripts/bind_controlled_fref.py` 与 `scripts/bind_controlled_fref_forward_stage.py`：冻结并绑定受控实验的 forward reference。
- `scripts/run_controlled_paired_training.py`：成对、按 seed 运行受控训练；公开版已去除具体主机和解释器路径。
- `scripts/evaluate_controlled_tandem_shared_fref_fixed_targets.py`：在同一 forward reference 和 fixed targets 上评估。
- `scripts/analyze_controlled_paired_replicates.py`：成对重复统计。
- `scripts/evaluate_historical_tandem_fixed_targets.py`：历史模型固定目标评估。
- `research_snapshot/20260823/physical_chain/`：本轮真实物理链的脱敏分析/溯源快照；它不是独立可执行生产环境。

完整入口见 [CODE_MAP_20260823.md](CODE_MAP_20260823.md)。

## 后续 GPT 的建议阅读顺序

1. 根目录 `README.md`；
2. 本文件；
3. [HANDOFF_STATE_20260823.json](HANDOFF_STATE_20260823.json)；
4. [KNOWN_NO_GO_20260823.md](KNOWN_NO_GO_20260823.md)；
5. [CODE_MAP_20260823.md](CODE_MAP_20260823.md)；
6. `docs/reproducibility/EXPERIMENT_CONTRACT.md`；
7. 相关脚本、测试与仓库 SHA-256 manifest。

后续 GPT 在得出新结论前，应先核对 source-table rows、gradient-training rows、validation rows、test rows、分母、目标 panel、误差定义、是否 fresh EMX，以及证据等级。

## 下一合法入口

1. 继续让现有监督进程完成剩余 3 个训练臂，不手动并发重跑。
2. 新建 no-clobber successor，修复两个正式发布接口的 P1 问题并重新独立 QA。
3. 只有接口 GO 后，发布 7,298 survivor-conditioned fresh-EMX 表格、置信区间和柱状图，并把 2,702 个失败明确列入分母审计。
4. 使用完成的五对 seed 结果发布 controlled nested 100k/200k 因果对比。
5. 再启动当前合同 `|K|<1` 的 200k/300k/400k/500k 数据与训练学习曲线，每个 checkpoint 冻结相同测试集、模型合同和训练预算，并记录 wall time/GPU time。
6. 最后封版周一 HTML/PPTX；当前 partial 不可直接交付。

## 证据边界

仓库中的数字来自已冻结的离线 receipts、summaries、manifests 和 SHA-256 索引。由于原始私有 artifacts 未随仓库发布，外部审阅者可以复核代码、研究合同和内部一致性，但不能只凭这个仓库重新计算全部真实 EMX 数值。需要完全复算时，必须由数据所有者在受控环境中提供对应 artifacts，并重新核验哈希。
