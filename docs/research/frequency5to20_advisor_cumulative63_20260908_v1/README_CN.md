# 5–20 GHz：累计63请求导师PPT与问答

[下载10页可编辑PPT](advisor_frequency5to20_cumulative63_v1.pptx) · [导师10问答](ADVISOR_QA_CN.md) · [14项需求覆盖](REQUIREMENT_COVERAGE.json) · [16频率状态](STATUS16.csv)

本版复用已验收snapshot_0054：2026-09-08 17:11:58 UTC，63/320请求结算，560实际求解，533strict有效。51张详细物理图与完整误差CSV继续使用[累计63科学报告](../frequency5to20_new23_cumulative63_20260908_v1/README_CN.md)。不混入更晚后台计数。

## 可以汇报的内容

10页依次覆盖进度、网络结构与调用、共同10K及逐频有效分母、留出电感误差、留出Q/耦合误差、随机Q扫描SELF_PROXY、物理流程计数、20GHz第四请求Q14的strict无效负例、学习曲线与预算、局限及模型调用。

全16对首预算训练/保存/加载/诊断续训/test与176万SELF_PROXY候选已完成，权重仍PARTIAL/PROVISIONAL。19/20GHz有效train仅537/279、反向反馈ramp未完成。正式10K15 fresh EMX仍NOT_RUN，15GHz物理是开发5K，分开报告。

63个已结算请求包含693原槽：533strict+27invalid+46解析拒绝+60GDS失败+27DRC失败。只有7请求原11项全strict可计算完整q_emx。失败保持空值，独立求解工件不当作IID样本。

20GHz第四请求预选Q14的Q百分比误差2.70%，但below_half_srf=false。页8有限描述值不纳入strict主误差。联合命中用绝对容差[0.125nH,0.125nH,1,0.04]，不等于目标相对5%。

## 每页预览

[1](slide_01.png) · [2](slide_02.png) · [3](slide_03.png) · [4](slide_04.png) · [5](slide_05.png) · [6](slide_06.png) · [7](slide_07.png) · [8](slide_08.png) · [9](slide_09.png) · [10](slide_10.png)

## 版本与证据

PPT有10原生图、6原生表和10嵌入literal工作簿。原8个未改图的数据与此前40版一致，仅物理图与文本按冻结63更新。最终科学、视觉与问答批准见[QA_SUMMARY](QA_SUMMARY.json)。原v1换行显示NO_GO保留，本发布为仅修换行后的candidate_v2精确字节。

覆盖表和问答在PPT独立审查前冻结，其“63版PPT尚需QA”表示当时状态，不是本交付终态。[RUN_STATE](RUN_STATE.md)和DELIVERY_RECEIPT记录后置精确验收。原科学/模型图的历史scoped QA不升级为全图逐一细看。

NOT_NATIVE_POWERPOINT_TESTED：已导出、重载及逐页检查预览，未声称在原生PowerPoint中打开。所有SHA可在SHA256SUMS核查。PPT SHA-256：`cd49400f0ff518143ec5365476819d9c39a6d2293910c9d2a94eb4a68b8c3af2`。

## 运行状态与科学冻结分开

2026-09-08 18:36:20 UTC的既有只读观察确认998085、2094607、86088均已退出。Native在18:33:18返回DISPATCH_FAILED_NO_RETRY，reporter终态为PARTIAL。原错误为“Native stage did not finish with evidence”；该次最小观察尚未确定底层失败阶段，不据最后一条日志推断根因。

Reporter记录的72/320结算和248待结算仅是运营元数据，没有新增科学或图表GO。本PPT仍使用已验收的63请求。此前“程序正在运行”只属于更早观察，不能当作当前状态。本包不自行恢复或重启程序。

观察来源：`reports/frequency5to20_20260908T063800Z/advisor_slides_cumulative63_20260908T182629Z/RUNTIME_FAILURE_OBSERVATION.json`，SHA-256：`97f01a77b99d7814d8f44be2f93498ece03b1d8b58b67c76ad8e4923f8e9f4fd`。此私有来源未上传，摘要与实际观察时间同时记录在RUN_STATE.json，不是实时计数。

本交付没有修改原生产、GUI或物理/报告程序，没有启动新训练/仿真或发送信号。完整320仍未完成。私有数据、权重、PDK和QA来源不随本包上传，研究根相对路径不表示GitHub可下载。
