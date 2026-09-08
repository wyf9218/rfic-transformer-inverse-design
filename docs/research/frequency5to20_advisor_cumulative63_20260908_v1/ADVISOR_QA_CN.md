# 导师问答：已验收63请求的事实与边界

本稿只引用2026-09-08 17:11:58 UTC冻结的S54科学结果及此前已验收训练/代理工件。63版PPT尚需其自身QA；不混入更晚未验收快照。

## 1. 模型究竟做什么，为什么不用一个网络同时满足56点？

先选整数频率，再给[Lp,Ls,Q_scalar=min(Qp,Qs),|k|]，只加载该频率的一对256×3 MLP，输出10维几何并用同频forward诊断。频率不是网络输入，不要求同一几何同时满足56点。训练复用BB00实现，但AdamW/响应配方不能称历史优化器的逐字节复现。

证据：[S10_MODEL_SPEC]、[S17_NATIVE_FIGURE_INDEX]、[S27_LIBRARY_USAGE]（精确路径与SHA见下）。

## 2. “10K模型”是不是用了10000个梯度训练样本？

不是。10000是共同唯一几何源表，含560000条频率记录；全局hash划分为6015/2002/1983。每频再取strict有效子集：15GHz是3175训练/1006验证/1046测试；19GHz是537/154/179，20GHz279/89/96。归一化只拟合对应有效train，不能把源表或多频行当独立梯度样本。

证据：[S11_PROFILE]、[S13_TRAIN_STATUS]、[S32_DATA_MANIFEST]（精确路径与SHA见下）。

## 3. 16个模型是否都训练完、是否收敛？

16对都有真实权重、加载与单步诊断续训及test结果，但状态全部PARTIAL/PROVISIONAL。5–16GHz各F/I12000更新，17–20分别8875/5538/3357/1744；19/20反向反馈ramp未完成。学习曲线存在不等于充分收敛，单步续训文件也不是替换best的排名模型。

证据：[S13_TRAIN_STATUS]、[S16_NATIVE_FIGURE_QA]、[S09_STATUS]（精确路径与SHA见下）。

## 4. 模型准确率究竟多少？能否只给一个百分数？

不能混成一个准确率。A留出forward比较原几何EM标签；B/Q扫描是新几何SELF_PROXY；C新几何fresh EMX才是物理证据。例如正式15GHz1046个test的四项MAE依次为0.007439891 nH、0.009066370 nH、0.162300497、0.007293296。对应P95为0.018998739 nH、0.022081485 nH、0.475067051、0.020628847。这些不是新15GHz设计的物理精度。

证据：[S14_HELDOUT_METRICS]、[S15_ROLLUP_QA]、[S23_PHYSICAL_METRICS]（精确路径与SHA见下）。

## 5. 15GHz已有物理结果，为什么正式10K15仍写NOT_RUN？

物理队列在EMX前已冻结开发5K15模型f15-5a9547401a37，S54为4请求/36求解/32strict。正式10K15是另一模型f15-strict_lumped-f5f2ced054ef，本次物理NOT_RUN。不能中途换权重或把前者贴成后者，更不能由这两批未受控结果声称5K→10K因果改进。

证据：[S04_PUBLIC_REPORT]、[S09_STATUS]、[S08_STATUS_QA]（精确路径与SHA见下）。

## 6. 为什么相对误差超过5%仍HIT？q_proxy是不是最大Q？

评分E=sqrt(mean(((response−target)/[2.5,2.5,20,0.8])²))，低分优先、同分选低Q；不是最高Q或Q下限。命中要求四项绝对误差分别≤[0.125nH,0.125nH,1,0.04]，不是逐目标相对5%。百分比仅诊断，|k|靠近零尤其不稳定；评分跨度、normalizer和支持域也不是同一概念。

证据：[S01_REQUIREMENT]、[S04_PUBLIC_REPORT]、[S07_NUMERIC_QA]（精确路径与SHA见下）。

## 7. 63请求、560求解、533有效和7完整11分别是什么意思？

63闭合请求保留693原槽：560次独立求解中533strict、27invalid；另46解析、60GDS、27DRC失败。原320/3520仍257请求未闭合、2652pending；全计划221解析中仅46属于闭合组。只有7请求全部11候选strict有效，才能比较固定Q15、预选q_proxy和事后q_emx。失败或缺失不补零、不换成更好Q。

证据：[S04_PUBLIC_REPORT]、[S07_NUMERIC_QA]、[S24_SELECTION]（精确路径与SHA见下）。

## 8. 高频误差不大但strict-invalid是什么原因？能放宽SRF吗？

不能用descriptor凑strict。20GHz第四请求预选Q14的原descriptor_valid/physics_qa_pass为true，但strict_lumped_valid/below_half_srf为false，主副SRF原括区均39–40GHz；有限小误差仍排除strict主统计。另一负例5GHz第四Q11在ground clearance/power-line/via-stack相关GDS门失败，未有EMX，不推更细根因。保留生产标签规则，不因结果好而放宽。

证据：[S04_PUBLIC_REPORT]、[S31_PHYSICAL_SELECTED]、[S07_NUMERIC_QA]（精确路径与SHA见下）。

## 9. 10000组随机目标、Q20域外和可达性如何解释？

每频10000组三指标×11Q仅代理压力测试，总176万随机逻辑候选，不是176万物理样本。全部16路Q20超观察strict-train边际范围；54/176支持格OUT，但OUT不等于物理不可达。Lp/Ls/|k|联合可达性UNKNOWN，三指标来自真实几何也不保证11个Q皆可实现。当前验收物理请求是预选H来源，不代表整个随机C框架。

证据：[S18_QSCAN_QA]、[S20_QSUPPORT]、[S21_QSUPPORT_NUMERIC_QA]、[S04_PUBLIC_REPORT]（精确路径与SHA见下）。

## 10. 为什么还没有物理CDF/密度或“最佳模型”结论？区间可信吗？

S54每频仅3–4个请求；现有显示政策CDF需≥5请求、密度需≥20请求及目标变化，因此本版物理两图明确不可用。代理CDF可展示但另标SELF_PROXY。已有95%区间为整请求2000次bootstrap的有限小样本描述，绝非11Q各自IID；共同完整组更少。频率有效群体、预算和训练暴露不同，没有多seed受控实验，不能宣称总体准确率或因果冠军。

证据：[S25_PHYSICAL_DISTRIBUTION_AVAILABILITY]、[S18_QSCAN_QA]、[S07_NUMERIC_QA]、[S13_TRAIN_STATUS]（精确路径与SHA见下）。

## 复用与缺项

可复用：56点标签/范围，16路曲线和holdout诊断，64张正式SELF_PROXY图，Q支持图，以及S54精确51图。旧模型/Qscan图的验收包含contact总览与单图抽检，不升级为全部逐图细看；正式15模型图沿用其原逐图QA，本次未重复视觉检查。未补齐：S54物理CDF/密度、正式10K15物理、完整随机目标物理、多seed受控对比及充分收敛证据；不能为赶汇报伪补。旧40PPT可作版式参考，不能改标题便称63已验收。

## 精确证据索引

除明确标记的外部用户附件外，下列路径均相对研究根目录，不是网页可下载链接。私有原始数据、权重、用户附件及私有QA来源未随本稿上传；路径引用不表示GitHub含有该文件。完整本机绝对路径仅在不公开的SOURCE_MANIFEST.json保留。

- S10_MODEL_SPEC：github_worktrees/frequency-indexed-mlp-20260908/docs/FREQUENCY_INDEXED_TANDEM.md
  SHA-256：`ca80a0761422a2cb823b1e84ddc33e4be7d70f0ff72800605fd008a3541fb4b2`（5855 B）

- S17_NATIVE_FIGURE_INDEX：reports/frequency5to20_20260908T063800Z/native_model_figures_qa_v2/FINAL_MANIFEST.json
  SHA-256：`7b3c1612c550451e19938dac8c734a4918274c932811fcc8a5916c5d3dfd5085`（134624 B）

- S27_LIBRARY_USAGE：github_worktrees/frequency-indexed-mlp-20260908/docs/research/FREQUENCY_LIBRARY_USAGE_CN.md
  SHA-256：`54a54f7f04101eb030fa0dec036f34f6c26c45ba52bc67b5fd14b988c2aab514`（2609 B）

- S11_PROFILE：reports/frequency5to20_20260908T063800Z/training/f05/posttrain/profile/frequency_data_profile.json
  SHA-256：`6277b34c471bd101fa87ada0668324f515dc975539dc3a93dca9713db8798de6`（1418506 B）

- S13_TRAIN_STATUS：reports/frequency5to20_20260908T063800Z/rollup_v3/FREQUENCY_STATUS.csv
  SHA-256：`40079440bb24c8171009263aaf18e9bf24992e5769e82dcf62a42a0f6edd3bf6`（3785 B）

- S32_DATA_MANIFEST：reports/frequency_indexed_mlp_20260908T041800Z/formal10k_15ghz/data/data_manifest.json
  SHA-256：`390ce2169cc7bf66b9a2c83529fa2c7f217ce1fadea90cd001141ac4ec50d015`（203809 B）

- S16_NATIVE_FIGURE_QA：reports/frequency5to20_20260908T063800Z/native_model_figures_qa_v2/FINAL_QA_RECEIPT.json
  SHA-256：`335ff21e01adbde77c9b66cddcb041189cac563fadda379d9940115817e4b983`（15119 B）

- S09_STATUS：github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/STATUS16.json
  SHA-256：`c305155148efa00787fa0d27a337664a529e477e3770b146e372dce276ba3acb`（48975 B）

- S14_HELDOUT_METRICS：reports/frequency5to20_20260908T063800Z/rollup_v3/HELDOUT_METRICS.csv
  SHA-256：`eb0395c7638714d4aeb60e2a48a9834fcbe9ee194b456a778d99614a3552149e`（75161 B）

- S15_ROLLUP_QA：reports/frequency5to20_20260908T063800Z/native_rollup_independent_qa_v1/AUDIT_RECEIPT.json
  SHA-256：`fb15080fff66c9723de9fdb317419ee40cfadcde7e012029e190c5e4e5833586`（9800 B）

- S23_PHYSICAL_METRICS：github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/METRICS.csv
  SHA-256：`d8968b5ced3b5ccc5219aa7d54643eb916a361d94a000d72e3296fcf56a4d63a`（426369 B）

- S04_PUBLIC_REPORT：github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/README_CN.md
  SHA-256：`67903de2c9d9c79c5acad45a3e9551ad22ada9a245914bda7a32d768c6bb4979`（14387 B）

- S08_STATUS_QA：reports/frequency5to20_20260908T063800Z/milestone_new23_cumulative63_v1/status_qa_v1/INDEPENDENT_STATUS_QA_RECEIPT.json
  SHA-256：`d45e99cbfbb7ff5005506174086f8df3d55a92e80e9ad601393e87214627263a`（8157 B）

- S01_REQUIREMENT：Codex_Priority_5_20GHz_Train_Evaluate_Plot_v3.md（外部用户附件，仅列原文件名）
  SHA-256：`e9e1ad7003425b611ff19b51270f885bddac8f60fc521caf9e331ab177bbdd63`（11467 B）

- S07_NUMERIC_QA：reports/frequency5to20_20260908T063800Z/milestone_new23_cumulative63_v1/numeric_qa_v1/attempt_v2/INDEPENDENT_NUMERICAL_QA_RECEIPT.json
  SHA-256：`644730ae7c7dfd5a149971044facfde766773edba01723355674a414a8ae7a91`（99361 B）

- S24_SELECTION：github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/SELECTION_COMPARISON.csv
  SHA-256：`da30034d17bf0f1f603b55475f137df01bd1d954df60d6580c3d6a32657048e9`（56668 B）

- S31_PHYSICAL_SELECTED：github_worktrees/frequency-indexed-mlp-20260908/docs/research/frequency5to20_new23_cumulative63_20260908_v1/NEW23_SELECTED_PHYSICS.csv
  SHA-256：`e4a28a270c40023ae1df0c34515e377a3861a75504ab2f9310fab37de7a4a09a`（30762 B）

- S18_QSCAN_QA：reports/frequency5to20_20260908T063800Z/qscan_formal10k_figures/FINAL_SCOPED_VISUAL_QA.json
  SHA-256：`0214bd8b615e856e46779eae74de8ccb143e722f73112c5eaf069126dee81145`（4742 B）

- S20_QSUPPORT：reports/frequency5to20_20260908T063800Z/model_library_qsupport_v1/q_support_v1/Q_TRAIN_SUPPORT.csv
  SHA-256：`adb04d3f380d1ed0b68725f39159afd56661a593af9545fb2119f48d59f7d4d6`（17151 B）

- S21_QSUPPORT_NUMERIC_QA：reports/frequency5to20_20260908T063800Z/model_library_qsupport_v1/q_support_numerical_qa_v1/NUMERICAL_QA.json
  SHA-256：`586e4c8c7bd1a306ddc98c1c8a96cbd55b0b6e09e8dd52e844d85dbcf5ae8daf`（8594 B）

- S25_PHYSICAL_DISTRIBUTION_AVAILABILITY：reports/frequency5to20_20260908T063800Z/physical_incremental_statistics_v1/run_v1/snapshots/snapshot_0054/figures/DISTRIBUTION_AVAILABILITY.json
  SHA-256：`c2cf3868899e42040191a4f718501b07fdc6536f6f46934956826d291a41d120`（6097 B）

