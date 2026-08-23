# 当前问题与 GPT 交接摘要

更新时间：`2026-08-23T05:06:04Z`

## 1. 用户真正要完成的目标

在周一向导师汇报以下四件事，并确保每个结论都能追溯到代码、输入、样本分母、误差定义、图表源数据、收据与 SHA-256：

1. 历史 200k 模型的结构、真实训练分母与参数量；
2. 相比 100k 模型的当前表现差异，以及哪些差异不能解释为“数据量带来的提升”；
3. 在冻结 fixed10k 物理目标上的大规模统计；
4. 模型几何经过真实 EMX 后的误差、失效漏斗与适用边界。

## 2. 当前已经可以成立的事实

- fixed10k 是确定性的 centered-LHS 有限测试框架，不是独立同分布随机抽样；SHA-256 为 `c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407`。
- historical-200k 的 source/train/validation/test 行数为 `200000 / 161446 / 19135 / 19419`；“200k”不能被表述为 20 万行全部参与梯度训练。
- historical-200k 的 inverse 为 `4→128→128→10`、`18,442` 参数；forward 为 `10→128→128→4`、`18,436` 参数；合计 `36,878` 参数。
- deployed-100k 的 inverse 为 `4→512→512→256→10`、`399,114` 参数；forward 为 `10→256→256→128→4`、`102,020` 参数；合计 `501,134` 参数，并带 hard feasible-Q guardband。
- fixed10k 代理评估的 joint normalized RMSE：100k=`0.245843`，historical-200k=`0.202643`；legacy 子集为 `0.221335 / 0.184191`，extension 子集为 `0.325940 / 0.263850`。
- historical-200k 在 fixed10k 上的四项代理 MAE：Lp=`0.24884 nH`、Ls=`0.30137 nH`、Q=`3.92985`、K=`0.14731`。
- 物理实现漏斗是 `10000→7926→7373→7298→7298`；对应失败数 `2074 / 553 / 75`，最终 survivor 为 `7298`，其中 legacy=`5992`、extension=`1306`。

## 3. 当前最重要的五个问题

### P1 — 100k 与 historical-200k 不是严格控制变量实验

两者不仅数据量不同，网络结构、参数量、decoder/可行性约束和推理流程也不同。因此现有 fixed10k 数字只能写成“描述性工程对照”，不能声称误差下降由训练数据从 100k 增至 200k 导致。真正的因果结论必须等待同架构、同 split、同预算、同 seed 规则的 nested 100k/200k 配对实验完成。

### P2 — fresh EMX 数值尚未被正式释放到报告链

7298 个真实 EMX survivor 已形成冻结人口，但 Stage07/Stage08 正式结果接口目录仍未生成。现阶段只允许使用漏斗和分母，不能填写 target→EMX 或 proxy→EMX 的数值。必须先冻结 result-free 接口与 MARS 原生预检候选，再由独立审计给出 GO，之后只能恢复既有 stopped watcher，禁止新开或重跑 EMX。

### P3 — survivor 统计存在选择偏差，不能代表原始 10,000

只有 7298/10000 进入最终 fresh EMX 可评估集合，且失效不是随机缺失（MNAR）。所以 fresh EMX 精度的合法分母是 7298 survivors；不得把 survivor-only 统计外推成“全部一万个目标”的 EMX 精度。报告必须同时展示完整漏斗和分层结果。

### P4 — controlled nested 100k/200k 尚未达到终态

最近一次只读 MARS 观察为 terminal arms=`7/10`、complete pairs=`3/5`；监督进程存活但没有可安全手工接管的子任务，load1=`60.67`，高于冻结启动阈值 `<40`。禁止手工补跑，以免破坏预注册训练预算和配对关系。只有达到 `10/10 arms + 5/5 pairs` 才允许正式配对统计。

### P5 — 当前瓶颈是证据发布门禁，而不是画图能力

代理统计、模型结构、样本分母和物理漏斗已经足够搭建结果盲态报告；真正未完成的是：候选包不可变冻结、独立 QA、精确哈希复核、只恢复既有 watcher、Stage07/08 正式接口生成、随后三条误差链的最终统计与报告渲染。

## 4. 三条误差链必须分开

1. `target → proxy`：模型代理层面是否达到目标；fixed10k 已有结果。
2. `target → fresh EMX`：真实物理实现是否达到原目标；尚未正式释放数值。
3. `proxy → fresh EMX`：代理与电磁真值之间的 reality gap；尚未正式释放数值。

主要汇报指标应包含 joint normalized RMSE，并分别报告 Lp、Ls、Q、K；Q 还要优先报告 one-sided shortfall。四个归一化跨度固定为 `2.5 / 2.5 / 20 / 1`。

## 5. 已采取的安全推进方式

- 已创建 no-clobber 目标目录与 `RUN_STATE.md`；没有覆盖历史结果。
- 正在并行冻结 report-interface-v8、result-free MARS preflight/transport 候选和结果盲态报告壳层。
- 未访问 fresh EMX 正式数值、未发送 watcher 恢复信号、未重跑 EMX、未重新生成 fixed10k、未重新训练 historical 模型、未手工启动 controlled arms。
- 所有 FAIL/NO-GO 继续保留，不会改名成成功或静默覆盖。

## 6. 后续 GPT 的唯一合法执行顺序

1. 验证两个候选包的 manifest、receipt、SHA256SUMS、文件权限、无符号链接、无未登记文件；
2. 写出 `INDEPENDENT_QA_REQUIRED.json` 并保持 fresh-result-blind；
3. 由独立审计者对精确候选哈希给出 GO/NO-GO；
4. 只有 GO 后才重新只读检查 MARS watcher PID、状态、子进程、输出目录和重复运行风险；
5. 所有门禁通过后，仅恢复 PID `2901805` 对应的既有 stopped watcher；不得启动新 watcher 或重算 EMX；
6. Stage07/08 生成后分别统计三条误差链，并按 7298、5992、1306 的明确分母报告；
7. 渲染并视觉复核图表、PPTX、HTML、讲稿、Q&A、CSV、manifest、receipt 与 SHA-256 索引；
8. controlled experiment 只做状态跟踪；不到 `10/10 + 5/5` 不做因果归因。

## 7. 导师可能追问时的最短回答

- “为什么不能说 200k 一定更好？”——因为当前 100k/200k 同时改变了结构、参数量和约束，只能描述差异；严格数据量归因等待 nested paired experiment。
- “为什么 EMX 不是 10,000 个？”——10,000 是目标框架，经过物理可实现性与流程门禁后剩 7,298 个可评估 survivor；必须报告漏斗，不能隐藏缺失。
- “0.202643 是真实 EMX 误差吗？”——不是，它是 target→proxy 的 joint normalized RMSE；真实 EMX 对应的另外两条链尚未通过正式发布门禁。
- “200k 是不是用满 20 万训练？”——不是，源表 200,000 行，实际 gradient-training 161,446 行，另有 validation 19,135 和 test 19,419。

## 8. 机器可读伴随文件

同目录的 `CURRENT_PROBLEMS_GPT_HANDOFF.json` 是供后续 GPT/脚本读取的结构化状态。实时状态仍以最新 receipt、进程只读观察和 `RUN_STATE.md` 为准。
