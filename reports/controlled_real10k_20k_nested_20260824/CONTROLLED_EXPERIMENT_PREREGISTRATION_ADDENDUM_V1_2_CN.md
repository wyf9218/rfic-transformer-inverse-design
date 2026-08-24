# 受控 real-EMX 10K/20K 预注册补充 v1.2

冻结时间：`2026-08-24T08:10:33Z`

状态：`PRE_RESULT_ADDITIVE_SPATIAL_SENSITIVITY_CONTRACT_FROZEN`；尚未物化数据、训练、评估、读取结果或运行 fresh EMX。

本补充不修改 v1 或 v1.1，只把 v1 已要求的 physical-cell cluster bootstrap 操作化：

- 重采样次数固定为 `2000`，主 seed 固定为 `2026082402`，使用 NumPy `Generator(PCG64)`。
- 分别对共同 real-EMX holdout 902、fixed10k 全集、legacy 8000 和 high-K 2000 四个冻结帧/面板进行重采样。
- cluster 单位为共同合同的 4D、每维 4-bin physical cell。
- common/legacy 使用原始 `K_abs`；high-K/full10k 仅在分 cell 时使用 `min(K_abs,0.8)`，误差计算始终使用未裁剪原值。
- 每次从面板内的 C 个已观察 cell 中有放回抽 C 个 cell；被抽中的 cell 保留全部成员行及抽样重数。
- 六个模型共享同一次抽到的行多重集，先计算每个 paired seed 的 `large-small`，再对三个 seed 取均值。
- 对每个预注册标量指标报告未重采样点估计以及 2000 次结果的 2.5%/97.5% 百分位区间；不计算 bootstrap p-value。
- 该区间只表示冻结有限帧的空间组成敏感性，不表示训练 seed 不确定性、部署总体不确定性或 fresh-EMX 真值，也不替代 df=2 的 paired-seed t 区间。

精确机器可读合同见同目录 `CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_2.json`。
