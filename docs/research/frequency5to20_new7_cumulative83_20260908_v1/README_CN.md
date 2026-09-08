# 5–20 GHz：累计83请求统计与新增7请求逐项图

## 冻结边界与结论

数值、状态、公开CSV notebook及本轮21张图的源QA已闭合。作者候选验收状态保留；最终公开批准以PACKAGE_QA.json为准。本文件不是实时运行状态。统计时间2026-09-08T20:58:59.162713+00:00，快照包时间2026-09-08T20:59:15.093990+00:00。
累计83/320请求已闭合：913槽、747求解=712 strict+35 invalid；59解析拒绝/75 GDS失败/32 DRC失败。新增7请求：77槽、62求解=60 strict+2 invalid；13解析拒绝/2 GDS失败/0 DRC失败。原计划3520槽中仍有2445 pending；221全计划预知解析拒绝含尚未闭合请求，不能与已闭合失败合计混算。
累计11请求完整11严格有效才允许q_emx。以下是固定请求帧的描述性结果，不是随机总体准确率，也不能据此宣称模型冠军。

## 训练、测试与物理scope分开

16路正式模型均沿用76包的既有训练、test、保存/重载、诊断续训和包身份；本轮未重新验权重。全部PARTIAL/PROVISIONAL。19/20GHz反向ramp未完成。源表每频10000唯一几何，不等于10000行全部参与梯度训练。15GHz物理仍为开发5K，正式10K15 fresh EMX NOT_RUN。

| GHz | train/val/test | F/I更新 | 训练/test | 物理scope | 已闭合/20 | 求解/strict/invalid | 完整11 | 新图 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 6015/2002/1983 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 6/20 | 51/51/0 | 0 | 见后置图发布收据 |
| 6 | 6015/2002/1983 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 45/45/0 | 1 | 见后置图发布收据 |
| 7 | 6015/2002/1983 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 46/46/0 | 1 | 见后置图发布收据 |
| 8 | 6014/2001/1982 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 40/40/0 | 0 | 见后置图发布收据 |
| 9 | 5997/1989/1974 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 49/49/0 | 2 | 见后置图发布收据 |
| 10 | 5933/1965/1953 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 6/20 | 58/58/0 | 2 | 见后置图发布收据 |
| 11 | 5793/1908/1905 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 50/48/2 | 1 | 见后置图发布收据 |
| 12 | 5486/1800/1806 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 47/47/0 | 0 | 见后置图发布收据 |
| 13 | 4937/1618/1624 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 44/44/0 | 0 | 见后置图发布收据 |
| 14 | 4107/1341/1376 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 52/52/0 | 2 | 见后置图发布收据 |
| 15 | 3175/1006/1046 | 12000/12000 | PARTIAL / 既有PASS | 开发5K，正式NOT_RUN | 6/20 | 58/51/7 | 1 | 见后置图发布收据 |
| 16 | 2225/711/722 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 40/36/4 | 0 | 见后置图发布收据 |
| 17 | 1420/468/455 | 8875/8875 | PARTIAL / 既有PASS | 正式10K | 5/20 | 37/34/3 | 0 | 见后置图发布收据 |
| 18 | 886/280/296 | 5538/5538 | PARTIAL / 既有PASS | 正式10K | 5/20 | 44/39/5 | 1 | 见后置图发布收据 |
| 19 | 537/154/179 | 3357/3357 | PARTIAL / 既有PASS | 正式10K | 5/20 | 38/33/5 | 0 | 见后置图发布收据 |
| 20 | 279/89/96 | 1744/1744 | PARTIAL / 既有PASS | 正式10K | 5/20 | 48/39/9 | 0 | 见后置图发布收据 |

## 新7请求预选候选

保持原顺序16/17/18/19GHz第五请求，再开发15/正式5/10GHz第六请求。百分比为保存的abs(EMX-target)/abs(target)×100，不是绝对联合命中容差；failed/pending为空，不填0。invalid有限descriptor只作诊断，不进入strict主误差。

| 次序 | GHz/scope | 第几请求 | q_proxy/q_emx | 预选阶段 | 联合判定 | Lp/Ls/Q/abs(k)目标误差% |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 16 正式10K | 5 | 12/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 1.164/0.398/2.399/1.666 |
| 2 | 17 正式10K | 5 | 14/NOT_AVAILABLE | GDS_FAIL | NOT_AVAILABLE | NOT_AVAILABLE |
| 3 | 18 正式10K | 5 | 14/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.967/1.464/0.778/3.077 |
| 4 | 19 正式10K | 5 | 15/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 2.378/0.148/0.701/11.735 |
| 5 | 15 开发5K | 6 | 11/12 | SOLVED_STRICT_VALID | HIT | 0.644/0.456/1.672/0.973 |
| 6 | 5 正式10K | 6 | 10/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.415/0.134/2.443/2.711 |
| 7 | 10 正式10K | 6 | 12/12 | SOLVED_STRICT_VALID | HIT | 0.458/0.042/0.409/0.517 |

完整7请求及28预选四特征行见NEW7_REQUEST_STATUS.csv / NEW7_SELECTED_PHYSICS.csv。原始validity/SRF与失败门仅引用独立QA的NEW7_FAILURE_ORIGINAL_FLAGS.json；不推断未证明的工艺根因，不用其它Q替换失败预选。

## 方法、限制与下一交付

Q_scalar=min(Qp,Qs)，每Q候选对自己的Q目标做四指标对称匹配。评分跨度[2.5 nH,2.5 nH,20,0.8]；绝对联合容差[0.125 nH,0.125 nH,1,0.04]。q_proxy在EMX前冻结，q_emx仅完整strict11集合事后选取。固定Q15/q_proxy/q_emx共同比较保持完整请求集。
EMX-target与EMX-frozen-grid-proxy、selected/all、strict/finite-diagnostic及开发/正式scope均分开。整请求bootstrap不是把同请求11候选视作IID，有限小样本描述区间不等于部署总体CI。Q20超strict train边际范围不证明物理不可达，可达性UNKNOWN。
开发5K15的条件strict-valid ECDF与原分母residual-attainment已获本轮精确视觉GO。前者仅描述strict幸存候选的误差分布，后者保留原候选分母；两者不能混称成功率或总体准确率，也不构成正式10K15物理验证。

本包21PNG=14张新请求图+7张累计汇总/开发15分布图，不是83请求逐项图全部重画。源QA已闭合，公开副本单独验收；不重跑科学checker。旧76图与63PPT保持原边界；历史40报告正确入口为frequency5to20_new6_cumulative40_20260908_v2/README_CN.md。

## 源证据

- `reports/frequency5to20_20260908T063800Z/milestone_new7_cumulative83_20260908T210500Z/SOURCE_SELECTION.json` SHA-256 `ab8cc4932c6bb61732d0454f138044bde844d1424916b21f8224e61d2f86c637`。
- `reports/frequency5to20_20260908T063800Z/milestone_new7_cumulative83_20260908T210500Z/numeric_qa_v1/attempt_v1/INDEPENDENT_NUMERICAL_QA_RECEIPT.json` SHA-256 `f94808f6bc3490c7baf1cf0ab329621e073130be799484d64dc5de8388108b50`。
- `reports/frequency5to20_20260908T063800Z/native_failure_diagnosis_20260908T184000Z/reporting_recovery_install_v1/run_v1/snapshots/snapshot_0011/statistics/FREQUENCY_STATUS.csv` SHA-256 `758d59da273c5037f280879de25bb4534867ba1d3adc60ed46ad3e0cfd391aaf`。
- `github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new13_cumulative76_20260908_v2/STATUS16.json` SHA-256 `550ebb052e59b0ccabe72fed80acd063d957d7f4645d585f5dffb41329a32677`。
- `github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new13_cumulative76_20260908_v2/PACKAGE_QA.json` SHA-256 `a4655f213e99e25ac51b55f2565b3b9890785f664fa93b906b4ad2f80d37b615`。

研究根相对路径仅作定位，私有数据/权重/底层工件未上传。

## 负结果与剩余工作

17GHz第五请求预选Q14为GDS_FAIL，四项EMX误差保留空值；不能换Q声称成功。新增两项strict-invalid是18GHz非预选Q11/Q12（不是预选Q14）。19GHz预选Q15的abs(k)相对误差约11.735%，但满足绝对联合容差；百分比诊断与命中判据必须分开。新完整11请求为开发15GHz第六与正式10GHz第六，q_proxy/q_emx分别11/12和12/12；这不构成特征逐项最优或模型冠军。

唯一剩余完成条件：原320请求中的237请求在本冻结边界尚未闭合，且后续产物仍需完成相应QA。2445 pending属于原3520计划槽。本包不读取实时进程，不把新结果未闭合写成模型训练未开始，也不宣称当前服务器一定仍运行。

## 可检查表格与Notebook

[16频率状态](STATUS16.csv)、[新增7请求](NEW7_REQUEST_STATUS.csv)、[预选候选四项数值与目标/代理百分比](NEW7_SELECTED_PHYSICS.csv)、[各频率分母](FREQUENCY_STATUS.csv)、[320请求状态](REQUEST_STATUS.csv)、[六误差统计](METRICS.csv)、[完整11共同选择比较](SELECTION_COMPARISON.csv)。

[公开CSV复核notebook](PUBLIC_CSV_CHECKS.ipynb)的5单元已按标准库顺序执行、原生Jupyter kernel未运行。只复核7CSV分母/字段与保存的预选百分比；不能从仅公开的预选行重建全部候选MAE/P95或bootstrap。完整私有源链审核以精确数值QA收据为准。

### 16 GHz，第5请求（正式10K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![score](f16_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

![percent](f16_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

### 17 GHz，第5请求（正式10K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![percent](f17_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f17_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 18 GHz，第5请求（正式10K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![score](f18_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

![percent](f18_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

### 19 GHz，第5请求（正式10K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![score](f19_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

![percent](f19_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

### 15 GHz，第6请求（开发5K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![percent](qscan15_development5k_20260908_v1-HELDOUT_TRIPLE_AUDIT-000005_percent.png)

![score](qscan15_development5k_20260908_v1-HELDOUT_TRIPLE_AUDIT-000005_score.png)

### 5 GHz，第6请求（正式10K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![percent](f05_qscan-HELDOUT_TRIPLE_AUDIT-000005_percent.png)

![score](f05_qscan-HELDOUT_TRIPLE_AUDIT-000005_score.png)

### 10 GHz，第6请求（正式10K）

每个Q均对自己的四指标目标计误差；缺失/invalid明确保留，百分比不是绝对联合命中阈值。

![percent](f10_qscan-HELDOUT_TRIPLE_AUDIT-000005_percent.png)

![score](f10_qscan-HELDOUT_TRIPLE_AUDIT-000005_score.png)

### route_status

原320/3520分母与各频率阶段计数，保留失败和pending；进度不等于准确率。

![route_status](aggregate_route_status.png)

### all_candidates

严格有效幸存候选的物理单位MAE/P95，样本与请求数明确；不是忽略失败后的总体性能。

![all_candidates](aggregate_all_candidates.png)

### heatmap

同一冻结跨度和色标，正式15列不借用开发5K；跨频数据支持不同，不作因果排名。

![heatmap](aggregate_heatmap.png)

### selected

仅评价EMX前冻结的q_proxy；预选失败保留在原分母，不按EMX结果重新挑成功。

![selected](aggregate_selected.png)

### selection

累计11个完整strict11请求的共同集合，比较fixedQ15/q_proxy/q_emx；整请求小样本区间不是总体CI，q_emx是回顾性联合评分选择。

![selection](aggregate_selection.png)

### f15_ecdf

仅开发5K15。strict-valid条件ECDF使用51候选/6请求，selected为6；只描述有效子集误差，未伪造CI或平滑。原220槽另含20失败/无效及149pending，不能称全220达到同一条件。

![f15_ecdf](aggregate_f15_ecdf.png)

### f15_attainment

仅开发5K15。原分母为220候选与20预选请求，曲线终点分别51/220、6/20；失败/pending保留。它不等于前图的条件ECDF，也不是部署总体准确率。

![f15_attainment](aggregate_f15_attainment.png)

## 历史版本与调用

[已批准76包](../frequency5to20_new13_cumulative76_20260908_v2/README_CN.md)、[已批准63包](../frequency5to20_new23_cumulative63_20260908_v1/README_CN.md)、[已批准40包](../frequency5to20_new6_cumulative40_20260908_v2/README_CN.md)、[63请求导师PPT](../frequency5to20_advisor_cumulative63_20260908_v1/README_CN.md)保留原件。PPT没有冒改成83边界。模型调用仍见[统一调用入口](../FREQUENCY_LIBRARY_USAGE_CN.md)。本包未重新训练、推理、求解或修改生产/GUI；整体目标未完成。
