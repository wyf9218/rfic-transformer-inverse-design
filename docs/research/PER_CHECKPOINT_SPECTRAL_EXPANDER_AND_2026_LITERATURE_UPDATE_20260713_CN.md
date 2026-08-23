# 每 10 万宽带信息充分性基线与 2024-2026 文献更新

更新时间：2026-07-13 CDT

## 1. 当前可证实状态

- 正式 accepted pool：`100,282` 条真实 EMX、物理范围内、10-D 几何唯一数据。
- 固定首批模型表：`100,000` 行；不得与暂停时 targeted raw `.s4p=91,980` 混用。
- 物理范围：`Lp/Ls=0.5-3.0 nH`、`Q=5-25`、`|K|=0-0.8`；100k 行均在显式范围内。
- strict 4-D uniformity：`FAIL`；occupied `119/256=0.464844`，normalized entropy `0.744805`，nonzero max/min `3755`。
- 主要数据缺口：Q 边缘分布、`Lp-Q` 和 `Ls-Q` 联合覆盖；balanced selector 不能创造真实 EMX 尚未覆盖的物理单元。
- Ridge random-holdout 仅为执行基线：mean normalized MAE `0.178794`，max dimension normalized MAE `0.241201`。random split 不是正式 physical-cell OOD。
- direct MLP 架构搜索最佳候选为 3x256 GELU；其较大误差不能单独归因于网络容量，因为任务同时存在 one-to-many、中心频率信息压缩和稀疏物理单元问题。
- 2026-07-12 17:32 CDT 起 MARS generation 按导师要求为 tapeout 暂停；本次更新不启动 EMX、worker、watcher、controller 或 supervisor。

## 2. 本次代码改进

原流程只在第 10 个检查点（1M）运行：

`[Lp, Ls, Q=min(Qp,Qs), |K|] @ 15 GHz -> 5-60 GHz full complex S4P`

这会把最重要的输入信息充分性问题推迟到百万数据全部生成之后。本次已将该 PCA/Ridge spectral-expander 基线改为每个 100k 检查点运行，并固定：

1. 最多 10,000 条真实 S4P，避免模型检查抢占 EMX 生成资源。
2. 4-D physical-cell grouped OOD，训练/验证/测试 cell overlap 必须为 0。
3. 4-port、5-60 GHz、0.5 GHz、111 点合同。
4. raw S4P 互易性误差上限 `0.02`、无源性超限上限 `0.05`。
5. 仅报告 `COMPLETE_REVIEW_REQUIRED`，未预注册模型误差阈值前不能宣称模型通过。
6. 该基线只回答中心频率四物理量对宽带谱的信息充分性；不能替代 DRC、predicted-geometry 新 EMX、HFSS 相关性或测量。

相关合同回归：`33 passed`；checkpoint shell syntax 与 Python compile 均通过。

## 3. 最新论文与本项目动作

### A. 多端口 RF/sub-THz 全频 forward emulator

Karahan et al., Nature Communications 2024 将任意平面多端口结构图映射到跨频率 scattering/radiation response，再在 forward emulator 上做 inverse synthesis。该工作直接支持本项目把 full complex S4P forward surrogate 作为可信闭环，而不是只用 15 GHz 的四个标量判断几何是否正确。

**当前动作**：每 100k 先运行低秩 spectral-expander 信息充分性基线；若四标量到宽带 S4P 的 OOD 误差明显偏大，后续 inverse 输入应加入压缩后的宽带谱描述符或目标频率序列，而不是继续扩大 direct MLP。

### B. 把频率当作序列，而不是扁平向量

Sahu et al., Scientific Reports 2025 用 BiLSTM/GRU 显式建模被动微波电路频率响应，并结合 global sensitivity analysis 限制模型域。论文的关键价值是频率相邻性建模，而不是说明某个具体网络可直接迁移到片上变压器。

**已落地动作**：300k checkpoint 已接入 pointwise frequency-conditioned MLP 与轻量 GRU 的同数据、同 physical-cell OOD、同 optimizer-update、近似同参数量对比；固定 10,000 条真实 S4P、111 点、3 seeds，并报告 full-band/逐频/15 GHz `Lp/Ls/Q/|K|`/resonance-neighborhood 指标。原始 S4P quality、cell overlap、参数预算、测试集隔离和图表产物均为硬门；结果只可提名 frozen-forward inverse 消融，不以平均误差单独选模型。PCA/Ridge 仍作为每 100k 的低成本信息充分性基线。

### C. sensitivity 用于模型与局部搜索，不用于缩窄真实数据范围

Koziel et al., Scientific Reports 2024/2025 表明 fast global sensitivity analysis 可找出对宽带响应影响最大的联合方向，并在受限设计域中降低 surrogate 样本需求。

**适用边界**：本项目目标包含整个已声明工艺/物理范围的均匀覆盖，因此不能用 sensitivity 直接删除低敏感度几何方向或缩窄生产采样域。可以用它来：

- 设计 shared encoder、interaction layer 和局部 refinement 的步长尺度；
- 识别几何变量联合方向并规划消融；
- 在 full-domain 随机探索臂保留的前提下，提高局部目标求解效率。

### D. one-to-many 用多起点 + 高保真 forward refinement

Grbčić et al., npj Computational Materials 2025 的 multi-fidelity ensemble 针对 one-to-many 逆问题，先由低保真 inverse 生成多个近似解，再由高保真 forward model分别细化和排序。

**当前动作**：现有 tandem 已采用 frozen forward consistency、4-start 投影局部细化和几何边界投影。下一步必须对 top-k 进行 SHA 去重、DRC、真实 EMX closure，并按 paired target 比较；代理值只能排序，不能成为标签。

### E. 条件扩散作为后续多解模型

Li et al. 2025 的 spectrum-to-structure conditional diffusion 面向 one-to-many，并把制造信息纳入候选生成。该方向适合本项目后续输出多个不同几何，而不是回归一个被模态平均的几何。

**采用条件**：只有 tandem physical-cell OOD、DRC 和真实 EMX closure 基线稳定后，才启动 conditional diffusion/CVAE top-k arm。评价必须包含响应误差、DRC-valid fraction、候选多样性和真实 EMX success rate。

### F. 2026 PNGF 是潜在求解器研究方向，不是当前 EMX 的替代品

Sun et al., Nature Communications 2026 通过 precomputed numerical Green function 与 low-rank updates，将固定环境内的候选评估降到毫秒级，并报告最高 16,000x 加速。其核心假设是静态环境和可局部修改的离散优化区。

**本项目判断**：这一方法值得作为独立求解器加速研究，但不能未经验证替代 Cadence EMX/TSMC65 工艺栈。当前百万标签仍必须来自真实 EMX；若后续试验 PNGF，只能作为低保真候选排序，并需与 EMX 做同端口、同工艺、同频率的配对校准。

### G. active learning 必须同时保留边界、极值和随机探索

Diaw et al., Nature Machine Intelligence 2024 强调局部极值和端点采样，并在 surrogate 低于有效性阈值时持续更新。Park et al., Scientific Reports 2025 的 active-learning inverse optimization 也保留随机样本以平衡 exploitation/exploration。

**当前动作**：恢复后继续使用五分支 120k 原始队列：`coarse_4d=35704`、`rare_marginal=35712`、`pairwise_gap=24584`、`random_exploration=12000`、`geometry_diversity=12000`。代理只决定候选优先级；所有 accepted 标签必须来自真实 EMX。

## 4. 固定执行优先级

### P0：首批 100k

1. 保存所有 random-split 结果为 exploratory，不作为正式结论。
2. 完成 explicit physical-cell OOD tandem、tail audit 和 predicted-geometry feasibility。
3. 在资源允许时运行新增的 100k physical-spec spectral-expander，先回答中心频率四特征的信息充分性。
4. 固定 common test panel、split seed、模型预算和 tail 指标，后续检查点不得事后换门槛。

### P1：200k-300k

1. 200k 比较 `Q=min(Qp,Qs)` 与独立 `Qp/Qs` 输入。
2. 运行已接入的 PCA/Ridge、frequency-conditioned MLP、GRU 等数据/等 OOD 宽带 forward 对比；GRU 只有同时改善宽带、15 GHz 四物理量、谐振邻域和无源性时才进入后续消融。
3. 300k 运行 frequency-resolution 和 sensitivity/effective-dimension 消融。
4. 对 `2400/3216/4000/8000/16000/32000/64000/100000` 做 nested learning curve，回答“多少数据够用”，而不是依据他人论文直接决定样本数。

### P2：真实闭环

1. 每个目标输出 geometry-unique top-k。
2. DRC 与端口/ground 合同先行。
3. predicted geometry 重新跑真实 EMX，不能重放训练 S4P。
4. 代表性样本在 HFSS 采用同工艺层、同端口定义、同频率网格交叉验证。
5. 最终同时报告 mean、equal-cell、p95、worst-cell 和失败样本，不只展示“漂亮曲线”。

## 5. 参考文献

1. Karahan et al., “Deep-learning enabled generalized inverse design of multi-port radio-frequency and sub-terahertz passives and integrated circuits,” Nature Communications 15, 10734 (2024). https://www.nature.com/articles/s41467-024-54178-1
2. Sahu et al., “Surrogate modeling of passive microwave circuits using recurrent neural networks and domain confinement,” Scientific Reports 15, 13322 (2025). https://www.nature.com/articles/s41598-025-91643-3
3. Koziel and Pietrenko-Dabrowska, “Improved efficacy behavioral modeling of microwave circuits through dimensionality reduction and fast global sensitivity analysis,” Scientific Reports 14 (2024). https://www.nature.com/articles/s41598-024-70246-4
4. Grbčić et al., “Inverse design of photonic surfaces via multi fidelity ensemble framework and femtosecond laser processing,” npj Computational Materials 11 (2025). https://www.nature.com/articles/s41524-025-01518-4
5. Li et al., “Inverse Design of Metamaterials with Manufacturing-Guiding Spectrum-to-Structure Conditional Diffusion Model” (2025). https://arxiv.org/abs/2506.07083
6. Sun et al., “Near real-time full-wave inverse design of electromagnetic devices,” Nature Communications 17, 2372 (2026). https://www.nature.com/articles/s41467-026-69477-y
7. Diaw et al., “Efficient learning of accurate surrogates for simulations of complex systems,” Nature Machine Intelligence 6, 568-577 (2024). https://www.nature.com/articles/s42256-024-00839-1
8. Park et al., “Inverse binary optimization of convolutional neural network in active learning efficiently designs nanophotonic structures,” Scientific Reports 15, 15187 (2025). https://www.nature.com/articles/s41598-025-99570-z

## 6. 当前结论

首批 100k 已经足以证明“只增加样本数或加深 direct MLP”不是可靠策略。当前最优路线是：先用每 100k 的宽带信息充分性基线判断输入是否缺失，再用 frequency-aware forward model、tandem/multi-start refinement、五分支真实 EMX 采样和 EMX-HFSS 闭环逐级提高可信度。百万数据仍是累计目标，但是否继续到每个里程碑应由固定 learning curve、strict 4-D uniformity 和真实闭环结果共同决定。
