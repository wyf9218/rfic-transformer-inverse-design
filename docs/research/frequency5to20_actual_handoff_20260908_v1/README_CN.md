# 原生物理和报告程序已实际自动续接

2026-09-08 18:02:05 UTC只读核验。原320请求继续，不是重新启动一套试验。

- 原MARS2146776于18:00:00按原预算自然PARTIAL退出，没有停止已有child。
- 已安装等待器2094607于18:00:02一次启动既有dispatcher，新PID998085/UID2259579/start_ticks369433155，核验时存活并执行7GHz第五请求EMX Q10。
- 原配置SHA35b380eb5d7d1309e87e1bc63e6e93144bd6515187fe8b13da43311d7603b09a不变；独立预算SHA581f7479088c171ae33d668dee2e494fdd6f46373b438926af8999c4039e0691已实际传入，后续准入截至09-10 18:00 UTC。
- 原本机统计50120及导出等待80544自然退出，旧导出5个任务exit0，仍需新数值/视觉验收。
- 原handoff86088在同进程于18:01:54进入报告续接，复用原320/3520、69个已结算请求及原report锁；baseline_rerender=false。没有另开一套报告worker。

## 运营状态与科学发布分开

69/320、251未结算来自旧consumer终态和报告seed，只表示运营交接。snapshot_0060和终态导出图没有因exit0获得独立数值/视觉GO。最新已发布科学结果仍为[63请求报告](../frequency5to20_new23_cumulative63_20260908_v1/README_CN.md)，最新PPT仍40请求。

16模型仍首预算PARTIAL/PROVISIONAL，176万SELF_PROXY不重跑，正式10K15 fresh EMX仍NOT_RUN。原科学合同、生产和GUI未改。本次观察没有启动、停止或发信号给任何进程。

保留实际native998085和reporter86088；只验收真正新增结果，不重复等待已退出的2146776/50120/80544、不重装等待器。开机/崩溃自动恢复未安装，整体Goal ACTIVE。

## 精确证据

[OBSERVED_HANDOFF.json](OBSERVED_HANDOFF.json)保存真实PID、出生标记、时间、原始与新预算身份、关闭依赖及相对私有证据路径/SHA。私有源未上传，相对路径不是公开可读承诺。

native CHILD_LAUNCH_RECEIPT的远端与已安装reporter本地镜像一致：f9228b51f7ea837af75c0e532720ce38d37b715c93efcb20c1e8479f50e648de。
