# 5–20 GHz 研究状态

本里程碑：终态报告等待器已安装；MARS 后续预算为源码候选。整体 Goal ACTIVE。

## 已确认动作

- 15:56:39 UTC 本机 PID80544 存活，绑定原 consumer PID50120。
- 原 consumer 自然退出后才读取真实 FINAL_RECEIPT，最多派发一次有限导出。
- 已显式选择正式/开发各两类指标图及 selection，共五类；不会盲目重绘全部请求。
- 实际终态导出 NOT_STARTED；新图视觉验收 NOT_GRANTED。
- 等待器开机/崩溃自动重启 NOT_INSTALLED，同路径重复安装会拒绝。
- 11+22+31=64 项聚焦合成测试主体通过；不是物理或数值验收。
- 预算测试 v1/v2 各31 setup errors、0主体执行，已保留原失败证据。
- 两部分均获独立源码审查 GO；consumer 六个受保护 SHA 全部未变。

## 未部署的原队列后续预算

- 可选 operational-budget 只改变单独记录的准入截止，不改原配置或科学身份。
- 原配置/请求/输出/已完成收据保持；既有求解的提取恢复仍匹配其原 wrapper。
- 要求合格自然 PARTIAL、无 FAILURE、原 queue/global 租约，不放宽失败重试。
- 尚未选择实际新预算、传输 release 或部署，native Linux 验证 NOT_RUN。
- 原18:00物理/18:15统计预算不变；新增本机报告等待准入截止19:00。
- 本轮新训练、推理、GDS、Calibre、EMX启动数均为0；生产/GUI未改。

## 科研成果边界（复用，不重复）

- 16频率首预算训练/保存/加载/诊断续训/test/包完成，但均 PARTIAL/PROVISIONAL。
- 1760000 SELF_PROXY逻辑候选已完成，不重跑。
- 最新独立验收仍为 snapshot_0031：40/320请求，353求解，339 strict有效。
- 最新10页PPT仍是 advisor_slides_cumulative40_20260908T150100Z/candidate_v3/output。
- 正式10K15 fresh EMX=NOT_RUN；开发5K15不能混入正式结果。
- 单次MARS现场15:37:11 UTC：原PID2146776 alive，20GHz第四请求Cadence。
- 这不是实时计数，也未把后续日志中的数值直接更新到已验收图表。

## 证据与下一入口

- 本地目录 terminal_report_export_20260908T154000Z。
- 安装证据 INSTALLATION_RECEIPT.json；源码QA两份独立收据。
- 精确源码、配置、JUnit与失败证据 SHA 在 RUN_STATE.json 与清单。
- 公开入口 docs/research/frequency5to20_terminal_export_20260908_v1。
- 研究分支 codex/frequency-indexed-tandem-20260908，不写main。
- 下一步：复用唯一原生队列及已安装等待器；仅处理真正终态。
- 当前剩余：原320请求尚未全部关闭；预算续接未部署，不假称自动接续完成。
