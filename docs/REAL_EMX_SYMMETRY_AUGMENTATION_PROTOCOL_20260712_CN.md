# 真实 EMX 对称增强验证协议

日期：2026-07-12

## 当前状态

- 本协议只预注册未来的模型级 augmentation 消融。
- 当前真实 transformed pair 数为 `0`，没有任何 symmetry augmentation 结论。
- 工具未部署到正在运行的首批 100k MARS runtime，不影响端口、工艺、频率、队列、worker 或真实样本计数。

## 目的

只有当一个版图变换连同明确的端口 permutation 在当前 TSMC65/EMX 问题中被真实仿真证明为数值等价时，才允许把它作为训练期增强候选。图像看起来对称、解析直觉或代理预测均不构成证明。

## 禁止的默认假设

- M9 与 M10 互换默认不等价，因为层高、损耗和对 ground/shield 的耦合不同。
- 未记录端口 permutation 的镜像默认不等价。
- 同一个 `.s4p` 复制到两个路径、自配对、相同 geometry ID 或非 EMX 文件不能计为验证 pair。
- augmentation 行不计入 100 万真实 EMX、10-D geometry-unique、accepted checkpoint 或任何 1D/2D/4D 均匀性分母。

## 真实 pair 采样

1. 从 accepted real-EMX pool 中按 4-D Lp/Ls/Q/|K| physical cell 分层选择至少 `128` 个 reference geometry。
2. 至少覆盖 `8` 个不同 physical cells；报告每个 cell 的 pair 数，不用总数量掩盖集中采样。
3. 对每个 reference 生成一个独立 transformed GDS，保留不同的 geometry/evaluation ID。
4. transformed GDS 使用与 reference 完全一致的 EMX 合同独立求解并输出新的 `.s4p`。
5. 在 pair manifest 中记录把 transformed ports 重排回 reference port order 的一基 permutation。

## Pair manifest

CSV 必需列：

```text
pair_id
reference_touchstone
transformed_touchstone
reference_geometry_id
transformed_geometry_id
physical_cell_id
reference_source_kind
transformed_source_kind
transformed_ports_for_reference
```

`transformed_ports_for_reference=2,1,3,4` 的含义是：reference P1-P4 分别对应 transformed P2、P1、P3、P4。该向量必须是 `1..4` 的双射。

## EMX 文件内合同

每份 `.s4p` 的注释必须同时证明：

- EMX version banner 与实际 EMX command；
- TSMC65 process token；
- `--include-command-line`；
- `--cadence-pins=51`；
- `--s-impedance=50`；
- `--accuracy=standard`；
- `--parallel=2`；
- P001-P004 均有对应 `_G` reference；
- `5-60 GHz`、`0.5 GHz` sweep。

仅在 CSV 中写 `source_kind=EMX` 不通过来源门。

## 执行命令

```bash
python scripts/audit_real_emx_port_permutation_symmetry.py \
  --pairs-csv /path/to/real_emx_symmetry_pairs.csv \
  --out-dir /path/to/real_emx_symmetry_audit \
  --min-pairs 128 \
  --min-physical-cells 8 \
  --bootstrap-repetitions 1000 \
  --bootstrap-seed 20260712
```

## 数值硬门

- 两份网络必须都是 4-port、111 点、完全相同的频率网格和 reference impedance。
- permutation 对齐后 full complex-S RMSE 不超过 `1e-3`。
- permutation 对齐后 complex-S 最大绝对误差不超过 `1e-2`。
- 15 GHz Lp/Ls/Q/|K| 的最大声明范围归一化误差不超过 `1e-2`。
- 两份 raw `.s4p` 的 reciprocity error 均不超过 `2e-2`。
- 两份 raw `.s4p` 的 passivity excess 均不超过 `5e-2`。
- 所有 pair 必须逐条通过；不允许只报告平均值。

工具同时按 `physical_cell_id` 做固定种子 cluster bootstrap，报告 complex-S 与物理特征误差 p95 的 95% bootstrap 区间。它不是部署目标的 IID 置信区间，也不能替代逐条硬门。

## 后续模型消融

只有本协议 PASS 后，才可在相同真实训练行、相同 backbone、optimizer updates、seeds 和 physical-cell OOD split 下比较：

1. no augmentation；
2. verified symmetry augmentation。

validation/test 必须保持未增强的真实 EMX 行。采用要求至少包括 equal-cell OOD 与 p95 tail 改善、无逐特征显著回归、DRC/拓扑通过以及独立真实 EMX closure。增强数据量、训练时间和推理时间必须单独报告。

## 允许的结论

协议 PASS 只允许写：

> 该精确定义的 layout transform 与 port permutation 可进入受控模型 augmentation 消融。

协议 PASS 不允许写：

- 新增了 128 条或更多真实训练样本；
- 数据均匀性得到改善；
- 其他镜像、旋转或 M9/M10 互换也等价；
- augmentation 已改善模型；
- 可以减少既定的真实 EMX checkpoint 或最终 100 万证据。

## 实现与测试

- 工具：`scripts/audit_real_emx_port_permutation_symmetry.py`
- 测试：`tests/test_audit_real_emx_port_permutation_symmetry_script.py`
- 相关 Touchstone/extraction 联合回归：`32 passed`
- raw-85k stable index 真实 EMX 文件头 smoke：10 项全部通过；本地/远端 Touchstone SHA-256 同为 `cfc472ce4899591b39e33ec9a63d2e0d17b086b48df79aabb7f8abc74ce29b52`。
