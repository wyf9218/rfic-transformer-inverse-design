# 15 GHz EuCAP：当前研究与代码入口（2026-09-09）

更新入口：[2026-09-11真实增量](20260911_heldout_and_last2.md)。下文保留9月9日历史快照；
其中队列运行中及test/基线fresh EMX NOT_RUN均不是当前状态。15个开发对已完成，
选定基线1259行开发holdout和原128物理终态现已接收；未重训或改FINAL合同。

当前主线是 **15 GHz 开发研究**，不是 FINAL 实验完成声明。先选择频率，再输入 `[Lp, Ls, min(Qp,Qs), |k|]`；频率用于选择模型，不作为单频 MLP 的输入。本页记录已冻结成果和入口，不维护会随进程变化的已完成 arm 数量。

## 已完成与正在执行

- 新开发视图包含 **6,329 个正式来源唯一几何**：3,801 train / 1,269 validation / 1,259 test。来源为 6,281 FORMAL_BASE + 48 STAGE238；348 historical-research 与 23 original64-validation 成员排除。旧 3,018 个几何的 split 逐条保留，所有 Q 保留。parent/prototype 独立性未认证，不能称为 family-independent FINAL 数据。
- 该视图只存真实 15 GHz 标签，支持 `BB00/FREQUENCY_TANDEM_ONLY`；未伪造其他 55 频点或 S 参数。normalizer 仅由 train 拟合。
- 新 `3x256_seed17` 基线已从头完成 Forward 16,300 次更新（best 14,300）、Inverse 13,300 次更新（best 11,300），合计 **29,600 次真实主训练更新**。两者按 validation 早停；不声称已证明充分收敛。
- 1,269 行 validation 已评价；前向比较保留 EM 标签，反向诊断为 `SELF_PROXY`，不能称为新几何真实 EMX 精度。test、基线 Q-scan、新基线 fresh EMX 均 `NOT_RUN`。
- 独立新进程加载 F/I 的 best/last、隔离输出的各一步 optimizer 续训验证均 PASS；原 checkpoint 字节未变。额外两步属于 `NOT_RANKING` 诊断，不计入 29,600。验证覆盖计数器、scheduler、sampler、Torch RNG 和有限输出，不宣称全训练轨迹逐位等价。
- 同一 6,329 视图上其余 14 个开发对已进入既有有限串行队列：5 种 hidden tuple × seeds 17/29/43，总计划 15 对。各 arm 独立训练 own Forward；Inverse 深宽对照使用同一个冻结基线 Forward 评分基准。运行状态请读取原队列终态，不重复提交。
- 原 3,018 视图的 15 对训练与证据全部保留，不重复。新旧视图的更新预算不同，因此不能仅凭结果变化归因为数据量提升。

## 原 64 个物理候选：独立历史证据

原始分母固定为 64：58 个完成 EMX 提取 = 23 strict-valid + 35 strict-invalid（SRF 规则）；另有 4 GDS fail、2 analytic fail。严格联合命中 **17/64**，失败与无效没有替换或从原分母中删除。

这属于此前冻结 REFERENCE 模型的物理试验，**不是新 6,329 开发基线的验证**。strict-invalid 不代表没有 EMX 提取结果，也不能当成 strict-valid 排名数据。命中依据冻结绝对容差，不能解释成统一“相对误差小于 5%”。

FINAL 数据/模型、最终 10,000 请求与独立 100×11Q audit 均 `NOT_RUN`；FINAL 物理读取/原生派发适配器 **`NOT_INSTALLED`**。纯函数和接口通过 synthetic 测试，不等于已经部署原生自动验证，也不等于物理精度已验收。

## 可复用代码入口

以下路径相对本代码库根目录；输入仍须是获授权、SHA 校验后的实际私有交接文件，不能假定 GitHub 克隆含数据或权重。

| 目的 | 入口与边界 |
| --- | --- |
| 正式来源 15 GHz 开发视图 | `research/broadband56_nn/eucap15_formal_development_view.py::prepare`：冻结交接、旧 split、合同 pins；全新 no-clobber 输出。 |
| 单频网络 | `research/broadband56_nn/bb00.py`、`frequency_tandem.py`：10D→hidden→4 与 4→hidden→10；hidden 为 2×256、3×128、3×256、3×512、5×256。 |
| 训练/显式恢复 | `research/broadband56_nn/frequency_study.py`：`python -m research.broadband56_nn.frequency_study run --request <request.json>`；恢复用 `resume`。这不是让读者启动或重启当前队列。 |
| 开发评价 | `research/broadband56_nn/eucap15_development_evaluation.py`：`--config <config.json> --out <new-directory>`；validation-only，不读取 test 排名。 |
| 闭合结果汇总 | `research/broadband56_nn/eucap15_development_tables.py`：`--input <input.json> --out <new-directory>`；仅读 JSON 元数据，不训练或推理；三 seed 未齐必须 PARTIAL。新旧数据收据分别按精确 schema 读取计数，不猜字段。 |
| 补样与同成本比较 | `eucap15_acquisition.py`、`eucap15_prepare_acquisition.py`、`eucap15_acquisition_budget.py::matched_prefixes`（均在 `research/broadband56_nn/`）：固定配方/seed；真实开始顺序与失败成本保留；缺完整账本不发布 equal-budget 结论。 |
| FINAL 接口代码 | `eucap15_final_frame.py`、`eucap15_final_routing.py`、`eucap15_final_audit.py`、`eucap15_final_statistics.py`（同目录）：原始分母、完整 11Q、自身 Q target、冻结 q_proxy、缺失/失败与 regret；未冻结 FINAL 前不得拿开发模型冒充 FINAL。 |

当前队列的工作区相对入口为 `reports/eucap15ghz_20260908T220300Z/formal6329_capacity_20260909_v1/continue_frozen_development.py`。它复用原训练、租约、停止及评价循环；当前源码/协议已冻结，不应为整理代码库修改活动运行。汇总模块不在其 12 项训练源码 pins 中，本次兼容修改未触碰这些 pins。

## 测试与可复现边界

- 本次表收据兼容性：仅新增 21 个 metadata synthetic 用例，一次执行全部 PASS；覆盖原/新 schema、缺失/未知/双字段歧义、整数与分母拒绝。没有生成真实汇总表，也未重跑历史模型测试。
- 6,329 数据视图：原版本 23 个 synthetic PASS；真实首次冻结暴露 owner join 物理行序假设，失败保留。窄修复改为验证完整唯一 subset-index 排列，另 2 个新用例 PASS；不能把旧 23 当成修复后重新运行的全套。
- FINAL audit + statistics：47 + 23 = 70 个不同 synthetic 用例 PASS，仅为代码/元数据语义验证，不是 FINAL 原生或数值验收。
- mirror-reader 的 14 个 synthetic PASS 依赖兄弟 worktree 中两个精确冻结 fixture/source 文件。`tests/test_eucap15_mirror_reader.py` 要求旧 fixture SHA `9d884386c1dd42edf091e7e1642c61f34d9e02de4e61620520f5565e3a8bad5b` 和旧 validator SHA `80e9fd7578fe141615d99e6fc33e3a38f6e8bf801add5f5eef6d14121189e8dc`。因此**不承诺全新单一克隆可以直接运行整个 pytest 套件**；缺这些依赖不应通过跳过校验冒称全通过。
- 本说明不新增测试、图表、训练或原生运行；已有代码验收不能替代冻结输入与真实完成证据。

该 mirror-reader 双快照比较已明确列入 `tests/site_integration_tests.txt`，沿用仓库现有的私有环境测试分类；公开 CI 不运行这14项，研究工作区的原通过证据不变。这不是整个 pytest 全通过的声明。本次没有重复运行全库回归；新21项兼容测试的精确执行收据见下。

## 冻结证据索引

以下均为**研究工作区相对路径**与完整 SHA-256；仅公开路径/摘要/hash，不随代码上传私有收据正文、日志、训练数据、权重、PDK/GDS/S4P。它们不是可在 GitHub 下载的公共数据链接。收据是各自时点的证据，历史的 next-action 字段不是当前运行指令。

| 证据 | 工作区相对路径 | SHA-256 |
| --- | --- | --- |
| 新基线完整交付 | `reports/eucap15ghz_20260908T220300Z/formal6329_development_20260909_v1/delivery_v1/DELIVERY_RECEIPT.json` | `cb8123321a3b026fddef1b187f7ea890a2b9de4eedbdf565a8cb7f4446ba063e` |
| 独立加载/续训 | `reports/eucap15ghz_20260908T220300Z/formal6329_development_20260909_v1/resume_acceptance_v1/BB00_LOAD_RESUME_RECEIPT.json` | `3852b1706bf1bfa3bd145cb2cb8f47ac32de54d2e47eba10d7cb79866fa455d1` |
| 原 64 物理闭合 | `reports/eucap15ghz_20260908T220300Z/original64_terminal_statistics_20260909_v1/DELIVERY_RECEIPT.json` | `763789ad59dd7b8828df21cf19194c3390b2c16c1b466e9be6c6993b8c94ad8e` |
| FINAL 纯代码验收 | `reports/eucap15ghz_20260908T220300Z/final_physical_statistics_20260909_v1/DELIVERY_RECEIPT.json` | `fc42826dc7788820e5736009ec4270fdbbac11362cb905d2405331e5f4a0b908` |
| mirror 依赖范围 | `reports/eucap15ghz_20260908T220300Z/original64_terminal_statistics_20260909_v1/mirror_reader_tests_v2/AUTHOR_RECEIPT.json` | `f467b454238be6afa492d1d258a681811a68761aec698f1932cfe1ead49c42ce` |
| 原视图实现 23 测试 | `reports/eucap15ghz_20260908T220300Z/formal6329_development_20260909_v1/synthetic_tests_v1/TEST_RECEIPT.json` | `c41c092757fe2f69b842cabeda93bb11671bb6ed3bab7807d1941cd1386fe862` |
| 行序窄修复 2 测试 | `reports/eucap15ghz_20260908T220300Z/formal6329_development_20260909_v1/synthetic_order_fix_v1/TEST_RECEIPT.json` | `693e65294a434260274a6b3703b7d5b8a566f73b4fa81a11cc5b573f67925f12` |
| 表收据兼容 21 测试 | `reports/eucap15ghz_20260908T220300Z/formal6329_capacity_20260909_v1/table_compatibility_v1/TEST_RECEIPT.json` | `2c86a8676ec691c2be2ab023b145c404bfa6a35b21d693d3a65b01518c25a914` |

下一合法动作是复用正在运行的 6,329 开发队列和已封存评价，终态后汇总完整 shape/seed 比较；物理补样由唯一 native owner 交付闭合证据。不能把等待中的物理结果、历史 REFERENCE 或 SELF_PROXY 结论替换成新模型 FINAL 精度。
