# 2026-09-11：开发 holdout、128请求终态与新64读取接口

本页仅汇总已保存、已核验的新增证据；文档整理没有额外重跑训练、推理、测试或原生结果接收。所有结果均为 **DEVELOPMENT / NOT_FINAL**，三类证据不能混作同一精度结论。

## 1. 冻结模型的一次开发 holdout

此前依据 validation 确定并冻结的 **3×256、seed 17** F→I模型对，完成了原 `test` split **1259行**的一次开发 holdout 评估，没有依据这次结果重新选择模型。它与 validation 选模分开，但尚未认证 parent-family 或全部历史训练暴露层面的独立性，不能称为最终无偏部署评估。

F与已有留出EM标签比较，全部1259行有预测；下表为已保存汇总的舍入显示，不是新鲜EMX验证。

| 指标 | F MAE | F RMSE |
| --- | ---: | ---: |
| Lp（nH） | 0.007493 | 0.009656 |
| Ls（nH） | 0.007142 | 0.009311 |
| Qmin（无量纲） | 0.148353 | 0.213032 |
| \|k\|（无量纲） | 0.006409 | 0.008757 |

I的连续和网格几何均为 **SELF_PROXY 1253/1259（99.52%）**；解析通过1254/1259。全部1259仍是联合命中分母，不能剔除5项解析不通过后报告命中率。由于完整群体包含不可评价项，I的完整群体MAE/RMSE保留 `null`，不以条件子集误差替代。

四项命中条件均为对称目标误差不超过固定跨度 `[2.5, 2.5, 20, 0.8]` 的5%，不是目标相对5%，Q也不是下限或最大化目标。本次没有11Q扫描、fresh EMX或FINAL10000评估；**1253/1259不能与下节128请求的物理命中率直接比较**。

独立已保存行复核结论为 `PASS_SCOPED_NEW_HOLDOUT_SAVED_ROWS`。首次 `run_v1` 的 `UnicodeDecodeError` 失败证据保留；它发生在模型加载和test标签访问之前。`run_v2` 先修正进程观察的文本解码，再进行唯一一次评估；冻结输入、评价器及指标定义未改。本页不公开原始进程文本或失败trace。

## 2. 原128请求：最后两项接收后完整结算

此次只接收末两项闭合结果，复用此前已接收126项；接收程序未重新运行原生求解，也未重读旧126项原生结果/FEATURE。

| 原始128请求的终态 | 数量 |
| --- | ---: |
| ANALYTIC_FAIL | 35 |
| GDS_FAIL | 7 |
| EMX_INVALID | 38 |
| STRICT_VALID | 48 |
| PENDING | 0 |

联合命中 **40/128＝31.25%**；仅在48个strict有效结果内的条件命中为 **40/48＝83.33%**，不能替代原128分母。其余8个strict结果未联合命中。失败和invalid未被删去或替换，误差统计不将无有效物理值的行补零。

新请求125为 `EMX_INVALID`：原FEATURE的 `descriptor_valid=true`、`physics_qa_pass=true`，但 `strict_lumped_valid=false`、`below_half_srf=false`。原扫频记录的初、次级SRF均落在25–26 GHz区间，因此15 GHz不满足半SRF条件；**不是EMX求解失败**，区间内插值也不应冒称实际仿真频点。新请求126为 `GDS_FAIL`。这里不将其余37项历史invalid的原因推断为同一种失败。

这是当前6329开发模型的每请求一个事前预选候选的物理结果，与旧参考模型的原64请求结果保持分开。`q_emx=null`、`complete11_status=NOT_EVALUATED`、置信区间 `NOT_ESTIMATED`；代理11候选不等于11次物理试验。求解尝试数、缓存命中率不能从这些结果行反推，仍为未知。

保存的目录存储观察按完整request_id关联128项：全部请求目录allocated合计 **555,569,152 B**；48个strict目录合计 **306,262,016 B**，平均约6,380,459 B/strict目录。若把全部128项（含失败）分摊到48个strict结果，则约11,574,357 B/strict结果，二者不是同一成本口径。这不是峰值存储，不包含未列共享工件或整个项目；strict有效也不等于production accepted。研究盘可用557,225,426,944 B仅为 **2026-09-11 07:39:23.301573 UTC** 的保存观察，不是quota或FINAL100K准入证明；本轮清理及释放均为0。

## 3. 新64：本地读取接线通过，物理对照尚未执行

新增 **16项合成组件集成测试全部PASS**（无失败、无跳过），实际串联既有RESULT发布、closed export、物理证据读取及比较入口；不是16次EMX。测试保留完整64分母和14个原解析失败，并检查缺失发布、未分类失败、来源变更与出生观察等边界。

本次证据范围内，新64原生实验 **NOT_RUN**，未接收真实新64物理结果。局部闭合记录、wrapper PID或预约slot均不能代替全部实际EMX出生及起始顺序账本；缺少完整账本、预算起点和cutoff时，同实际启动数m及预声明合格train K=4的比较保持 `null`。DOE/探索不伪造设计target或目标误差，也不以缺失RESULT推断肯定未派发。

可审查的仓库相对实现入口：

- `research/broadband56_nn/frequency_evaluation.py`
- `research/broadband56_nn/eucap15_development128_last2.py`
- `research/broadband56_nn/eucap15_controlled_results.py`
- `research/broadband56_nn/eucap15_controlled_evidence.py`
- `tests/test_eucap15_controlled_closed_integration.py`

旧15组训练和既有QA未因本页整理而重复执行；本页不包含权重、目标、逐项几何、原始truth、PDK内容或私人绝对路径。

## 可核源 SHA-256

以下是内部证据的逻辑名称与内容指纹，不是公开数据下载链接。汇总与收据在本次整理中直接读取并核hash；125 FEATURE原因复用负责方对原闭合证据的已核字段与pin，未再次读取物理链。

| 证据逻辑名称 | SHA-256 |
| --- | --- |
| holdout run_v2 RECEIPT.json | `aa12f42ed9ed21100703d4ea5893ea3014dc02b9219e119e156e820b4ef6cbe9` |
| holdout EVALUATION_SUMMARY.json | `62be2c463bb7e786a20825220b7335f0e7348fe3804d717fc21bc7524afbe6b2` |
| holdout review_v1 REVIEW.json | `dfceaf5a0bf10f2be56647017b2b06d3487d10203a45902ef9b8369f133f9ebe` |
| holdout ATTEMPT_01_FAILURE_NOTE.json（仅失败事实摘要） | `deaa54c711fd4f1ed49542490fb1f8f7db828a2ef096a41fcf74894426a3c2fa` |
| last2 RECEIPT.json | `a0d205d84a6a2f62ea7fb43b4577d1233e24e340d4056d4512f7ab5e1d0ef248` |
| last2 SUMMARY.json | `4ce2e2c1220d36432d2d4ba6f2ae04836052ab4b7bacf98419ffe040dfb3febc` |
| 新64 closed integration AUTHOR_RECEIPT.json | `74320551b97c26d2e963fb67e449f1b6d7afd2de1a999db152a6a2f96c92977a` |
| 128保存存储 SUMMARY.json | `7b77bdfb6092769af58f06fb117a8b720a8149204da1f444c69b684317f0a3bd` |
| 128保存存储 RECEIPT.json | `7caa23d83fe957bf8e083a6cdeb539802433f99865ff230b4117bc47359e64f9` |
| 新125 FEATURE（原因字段证据） | `175e23a90a0c2643c3584d3ae68b48dbdc253f8cc50095df73acbcc0aa57816c` |

| 实现逻辑名称（对应上列仓库入口） | SHA-256 |
| --- | --- |
| frequency_evaluation.py | `2ff21d623f938dc312b7f0bca2aa5aa7ceb89af9803226ee9ab5f68ee1f692ab` |
| eucap15_development128_last2.py | `0e8d27c9c3e669b9ab13ab3be98e243cc309a9ebc88d076bee136316200a4c61` |
| eucap15_controlled_results.py | `bc8f2ef6b91eeec228c2e3722a6ae722817dc235e933e67fcd38e76538c7685c` |
| eucap15_controlled_evidence.py | `0fb1d6ca50e8455f3cc87fb46007e889f856a3c5821c9d00e4c25334932c27a7` |
| test_eucap15_controlled_closed_integration.py | `225d5aa71a36e7f335b1713d4f3847d7bf53560fc8e290e86fc04fc174a60cf7` |
