# 5–20 GHz：累计76请求统计与新增13请求逐项图

数值、16频率状态及本轮31张图的精确源QA已闭合，作者候选验收状态保留；最终公开批准以PACKAGE_QA.json为准。STATUS16保留当时PENDING字段，后置批准见FIGURE_RELEASE_STATUS.json。本文件不是实时服务器状态。
冻结统计时间：2026-09-08T20:07:23.526702+00:00；快照包时间：2026-09-08T20:07:35.703247+00:00。不读取本边界之后的结果。

## 已完成与未完成

16个整数频率均有真实正向/反向权重及既有保存、重载、诊断续训和test证据。训练全部仍为 PARTIAL/PROVISIONAL，不声称充分收敛；19/20 GHz反向ramp未完成。每频率源表10000个唯一几何，实际梯度train/validation/test行数见下表，不能写成10000行均参与梯度训练。
本轮没有重新训练、推理、评价或EMX。训练及SELF_PROXY图复用已批准证据。15 GHz正式10K模型fresh EMX仍 NOT_RUN；物理15 GHz仅开发5K模型，不混入正式10K性能。

## 物理分母

已闭合76/320请求、836原始Q槽：685独立求解 = 652 strict-valid + 33 strict-invalid；另46解析拒绝、73 GDS失败、32 DRC失败。闭合槽无pending。新增13请求143槽：125求解 = 119 strict + 6 invalid，0解析拒绝、13 GDS失败、5 DRC失败。
全部计划3520槽保留 2509 pending、221预知解析拒绝；221含尚未闭合请求，不可与闭合76的73 GDS和32 DRC直接相加。244请求尚未闭合。累计9请求完整11个严格有效，才允许q_emx。

| GHz | train/val/test | F/I更新 | 训练/test | 物理scope | 已闭合/20 | 求解/strict/invalid | 完整11 | 新图 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 6015/2002/1983 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 41/41/0 | 0 | 见后置图发布收据 |
| 6 | 6015/2002/1983 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 45/45/0 | 1 | 见后置图发布收据 |
| 7 | 6015/2002/1983 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 46/46/0 | 1 | 见后置图发布收据 |
| 8 | 6014/2001/1982 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 40/40/0 | 0 | 见后置图发布收据 |
| 9 | 5997/1989/1974 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 49/49/0 | 2 | 见后置图发布收据 |
| 10 | 5933/1965/1953 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 47/47/0 | 1 | 见后置图发布收据 |
| 11 | 5793/1908/1905 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 50/48/2 | 1 | 见后置图发布收据 |
| 12 | 5486/1800/1806 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 47/47/0 | 0 | 见后置图发布收据 |
| 13 | 4937/1618/1624 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 44/44/0 | 0 | 见后置图发布收据 |
| 14 | 4107/1341/1376 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 5/20 | 52/52/0 | 2 | 见后置图发布收据 |
| 15 | 3175/1006/1046 | 12000/12000 | PARTIAL / 既有PASS | 开发5K；正式10K NOT_RUN | 5/20 | 47/40/7 | 0 | 见后置图发布收据 |
| 16 | 2225/711/722 | 12000/12000 | PARTIAL / 既有PASS | 正式10K | 4/20 | 33/29/4 | 0 | 见后置图发布收据 |
| 17 | 1420/468/455 | 8875/8875 | PARTIAL / 既有PASS | 正式10K | 4/20 | 30/27/3 | 0 | 见后置图发布收据 |
| 18 | 886/280/296 | 5538/5538 | PARTIAL / 既有PASS | 正式10K | 4/20 | 36/33/3 | 1 | 见后置图发布收据 |
| 19 | 537/154/179 | 3357/3357 | PARTIAL / 既有PASS | 正式10K | 4/20 | 30/25/5 | 0 | 见后置图发布收据 |
| 20 | 279/89/96 | 1744/1744 | PARTIAL / 既有PASS | 正式10K | 5/20 | 48/39/9 | 0 | 见后置图发布收据 |

## 新13请求：保持原顺序与预选Q

百分比是已保存的 EMX 对目标绝对相对误差，顺序Lp/Ls/Q/|k|，不是命中容差。GDS失败保留空值；strict-invalid的可计算描述误差不进入STRICT_LUMPED主误差。

| 次序 | GHz/scope | 原request后缀 | q_proxy/q_emx | 预选候选终态 | 联合命中 | 四项目标误差% |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 19 正式10K | 000003 | 14/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.910/1.979/0.816/3.094 |
| 2 | 15 开发5K | 000004 | 13/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 1.081/0.218/1.300/3.813 |
| 3 | 5 正式10K | 000004 | 10/NOT_AVAILABLE | GDS_FAIL | NOT_AVAILABLE | NOT_AVAILABLE |
| 4 | 10 正式10K | 000004 | 11/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.775/0.717/0.332/2.009 |
| 5 | 20 正式10K | 000004 | 16/NOT_AVAILABLE | SOLVED_INVALID | NOT_HIT | 4.468/1.187/3.934/26.968 |
| 6 | 6 正式10K | 000004 | 10/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.118/0.686/0.045/1.019 |
| 7 | 7 正式10K | 000004 | 10/10 | SOLVED_STRICT_VALID | HIT | 1.019/0.570/2.185/6.571 |
| 8 | 8 正式10K | 000004 | 11/NOT_AVAILABLE | GDS_FAIL | NOT_AVAILABLE | NOT_AVAILABLE |
| 9 | 9 正式10K | 000004 | 14/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.303/0.198/0.354/1.414 |
| 10 | 11 正式10K | 000004 | 11/13 | SOLVED_STRICT_VALID | HIT | 0.591/0.938/1.235/3.318 |
| 11 | 12 正式10K | 000004 | 13/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.331/0.895/1.927/0.860 |
| 12 | 13 正式10K | 000004 | 13/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.158/0.148/0.139/2.871 |
| 13 | 14 正式10K | 000004 | 13/NOT_AVAILABLE | SOLVED_STRICT_VALID | HIT | 0.142/0.651/0.002/2.986 |

负结果必须保留：5 GHz第五请求预选Q10、8 GHz第五请求预选Q11为GDS_FAIL，无EMX真值，不用其它Q替换成功。20 GHz第五请求预选Q16是SOLVED_INVALID，四项描述误差仍保存但不计入strict主误差；其|k|目标残差0.057087852674063605超过绝对容差0.04。6个invalid的原SRF/validity旗标和13个GDS/5个DRC原失败证据见独立QA的NEW13_FAILURE_ORIGINAL_FLAGS.json，不据此猜测未证明根因。
新增完整11请求仅7 GHz第五（q_proxy=10、q_emx=10）和11 GHz第五（11、13）。q_emx是本请求事后EMX评分最小的Q，不是新训练模型，不选幸存者补足11，也不证明物理性能冠军。

## 评价口径与边界

Q_scalar=min(Qp,Qs)，四指标对称匹配各候选自己的Q目标。评分使用冻结跨度[2.5 nH,2.5 nH,20,0.8]；联合命中使用绝对容差[0.125 nH,0.125 nH,1,0.04]。低评分不是自动命中；近零目标百分比不作为主指标。EMX-target与EMX-frozen-grid-proxy分开；selected q_proxy与all candidates两个estimand分开；固定Q15/q_proxy/q_emx只用完整严格11的共同请求集。
整请求bootstrap是冻结有限请求的描述性区间，不把11候选当IID，不代表部署总体置信区间。各路仅4或5个请求，训练seed、数据支持和预算并非控制变量跨频实验，不能作跨频因果排名。Q20超出各频strict train边际范围不等于物理不可达；可达性仍UNKNOWN。
主交付沿用GitHub科学CSV/已批准图及可复现notebook，不新建app或PPT。旧63图使用既有批准映射，不从新快照回退到曾NO-GO的原图。本包只包含新增13请求的26张逐Q图和5张累计汇总图，不表示31张图覆盖全部76请求的逐项图。

## 精确证据

- source_selection：`reports/frequency5to20_20260908T063800Z/milestone_new13_cumulative76_20260908T201100Z/SOURCE_SELECTION.json` SHA-256 `43ff3f712716efa9882ebd6da8ad1351e2e6cb2f44a6cb6704541b16124ddff7`。
- numeric_qa：`reports/frequency5to20_20260908T063800Z/milestone_new13_cumulative76_20260908T201100Z/numeric_qa_v1/attempt_v1/INDEPENDENT_NUMERICAL_QA_RECEIPT.json` SHA-256 `f37b9184c587ef5defeabfdd462d1298c651c3a1c862b66f2e354576c270e334`。
- frequency_status：`reports/frequency5to20_20260908T063800Z/native_failure_diagnosis_20260908T184000Z/reporting_recovery_install_v1/run_v1/snapshots/snapshot_0004/statistics/FREQUENCY_STATUS.csv` SHA-256 `d2d74d3814384ad65fc20b87b1e285d3fc4d2fd69583ab5b469ff595940b2a14`。
- statistics_receipt：`reports/frequency5to20_20260908T063800Z/native_failure_diagnosis_20260908T184000Z/reporting_recovery_install_v1/run_v1/snapshots/snapshot_0004/statistics/STATS_RECEIPT.json` SHA-256 `040a259f3d82d5f92cfaaa90edda304e94480de905788635f8e45d7607a436eb`。
- previous_published_status：`github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/STATUS16.json` SHA-256 `c305155148efa00787fa0d27a337664a529e477e3770b146e372dce276ba3acb`。
- previous_63_figure_release：`github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/FIGURE_RELEASE_STATUS.json` SHA-256 `d758a9612d4571d0849076e8ec0a47f3be382001d302b8790d7a0f83987b28cf`。

以上研究根相对路径只用于定位；私有数据、权重和底层工件未因本候选而上传。

## 图与复核入口

[逐Q数值](NEW13_SELECTED_PHYSICS.csv)、[新13请求](NEW13_REQUEST_STATUS.csv)、[16频率状态](STATUS16.csv)、[全部请求状态](REQUEST_STATUS.csv)、[四指标六误差统计](METRICS.csv)、[完整11共同选择比较](SELECTION_COMPARISON.csv)。

[公开CSV复核notebook](PUBLIC_CSV_CHECKS.ipynb)只检查公开表及其分母；不能重验未上传的私有源链、原始S4P或完整bootstrap。原独立科学审核与执行状态仍由QA_SUMMARY引用。

### 19 GHz，第4请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f19_qscan-HELDOUT_TRIPLE_AUDIT-000003_percent.png)

![score](f19_qscan-HELDOUT_TRIPLE_AUDIT-000003_score.png)

### 15 GHz，第5请求（开发5K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](qscan15_development5k_20260908_v1-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](qscan15_development5k_20260908_v1-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 5 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f05_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f05_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 10 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f10_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f10_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 20 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f20_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f20_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 6 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f06_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f06_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 7 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f07_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f07_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 8 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f08_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f08_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 9 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f09_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f09_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 11 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f11_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f11_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 12 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f12_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f12_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 13 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f13_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f13_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### 14 GHz，第5请求（正式10K）

逐Q四項目标百分比显式保留失败/null；评分曲线与绝对联合命中定义分开。

![percent](f14_qscan-HELDOUT_TRIPLE_AUDIT-000004_percent.png)

![score](f14_qscan-HELDOUT_TRIPLE_AUDIT-000004_score.png)

### route_status

全计划320/3520分母，失败与pending保留；不是模型准确率。

![route_status](aggregate_route_status.png)

### all_candidates

仅strict有效候选的物理单位条件误差。不能隐去失败或作跨频冠军排名。

![all_candidates](aggregate_all_candidates.png)

### heatmap

固定跨度误差；正式15GHz缺列保持NOT_SCOPED，不借开发5K。

![heatmap](aggregate_heatmap.png)

### selected

EMX前冻结q_proxy的条件误差；预选失败/invalid仍见逐请求原始分母。

![selected](aggregate_selected.png)

### selection

仅9个完整strict11请求的共同集。整请求小样本区间是描述性，不是部署总体CI。

![selection](aggregate_selection.png)

## 历史复用与范围

[已批准累计63统计与新增23图](../frequency5to20_new23_cumulative63_20260908_v1/README_CN.md)、[已批准40请求包](../frequency5to20_new6_cumulative40_20260908_v2/README_CN.md)、[已批准63请求PPT](../frequency5to20_advisor_cumulative63_20260908_v1/README_CN.md)原件保持。PPT仍是63边界，不冒称更新到76。

[模型调用](../FREQUENCY_LIBRARY_USAGE_CN.md)。本包没有重新训练/推理/仿真，也没有检查当前进程。更晚运营进度不混入76科学边界；整体目标未完成。
