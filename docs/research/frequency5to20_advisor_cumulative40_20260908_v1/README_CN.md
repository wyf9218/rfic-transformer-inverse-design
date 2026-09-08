# 5–20 GHz 单频Tandem汇报：累计40组冻结物理请求

新版10页[可编辑PPT](advisor_frequency5to20_cumulative40_v3.pptx)复用已验证模型、训练曲线和留出测试数据，只更新累计物理验证与汇报问答。旧PPT及全部失败稿保留。

## 已完成

- 16个整数频率模型对：Forward 10→256→256→256→4，Inverse 4→256→256→256→10。频率只选择模型。
- 共同accepted_sequence=1..10000几何快照、全56点标签、跨频率同一geometry-hash split；normalizer只拟合各频有效train。
- 16对首预算训练、保存、加载、单更新诊断续训及test均有既有证据。全部仍标PARTIAL/PROVISIONAL。
- 每频10000随机请求×11个Q，16频合计1760000个SELF_PROXY逻辑候选；本次未重算。
- 物理报告采用独立QA通过的snapshot_0031：40请求/440原槽，353独立求解，339严格有效、14严格无效，另26解析拒绝、40 GDS失败、21 DRC失败。
- 只有5请求满足原11项全部strict有效，才能发布q_emx。其他请求不以成功子集冒充完整最优。

## 关键局限与负结果

- 各频仅2–3个物理请求。整请求bootstrap区间标PROVISIONAL_SMALL_REQUEST_N，不能宣称总体精度冠军或因果提升。
- 15 GHz物理结果使用开发5K模型，3请求/25求解/22strict有效。正式10K的15 GHz新几何fresh EMX仍为NOT_RUN。
- 正式20 GHz第三请求预选Q13：Qmin目标相对误差3.57%，但|k|绝对误差0.041374超过冻结0.040容差，联合NOT_HIT。PPT同时保留四指标的真实目标、EMX值和百分比。
- 命中判定使用绝对容差[0.125 nH,0.125 nH,1,0.04]，不是目标相对5%。评分使用固定跨度[2.5,2.5,20,0.8]，不是最大Q或Q≥下限。
- 每角色更新上限min(12000,ceil(200×有效train/32))。19/20 GHz分别3357/1744步，原200-epoch上限早于inverse的600+3000步ramp终点；不是时限失败。诊断续训不等于正式预算延长。
- 原320请求目标尚未完成。所有图、表只代表标明的冻结快照；更晚运行状态不混入本PPT。

## 证据与使用

- [16频率训练/测试/EMX/出图状态](../frequency5to20_new6_cumulative40_20260908_v2/STATUS16.json)
- [源统计、逐请求图和SHA](../frequency5to20_new6_cumulative40_20260908_v2/README_CN.md)
- [20 GHz等新增6请求预选实测CSV](../frequency5to20_new6_cumulative40_20260908_v2/NEW6_SELECTED_PHYSICS.csv)
- 本目录DELIVERY_RECEIPT.json与SHA256SUMS绑定PPT和状态文件；图表为10个原生图，另有6个原生表与10个嵌入工作簿。
- 10页均渲染审查。未实际在原生PowerPoint中打开，状态NOT_NATIVE_POWERPOINT_TESTED。备注含数据源及SHA；私有权重、数据、PDK不随公开稿上传。

15:01:21 UTC的单次只读现场确认原MARS进程2146776在18 GHz第三请求的Calibre阶段继续推进。现有普通程序负责增量统计，不部署AI周期轮询。本轮未启动训练/推理/EMX，未改变生产、GUI或资源与截止预算。

