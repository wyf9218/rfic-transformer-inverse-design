# 终态有限导出：代码完成，一次性等待器已安装

2026-09-08 15:56:39 UTC 现场，不是实时仪表盘。

- 本机等待器 PID 80544 已确认存活，绑定原 consumer PID 50120 的 UID、启动时间、命令摘要与配置 SHA。
- 原 consumer 自然退出且发布对应 FINAL_RECEIPT 后，只调用一次有限导出；当前真实终态导出 NOT_STARTED。
- 11 项等待器测试及 22 项批处理测试通过，均为合成验证，不是物理/数值/视觉验收。
- 独立代码审查 GO；未来新图仍为待独立视觉验收，不能用退出码零代替 GO。
- 原 MARS 队列、生产、GUI、统计程序及冻结图形模块未被改动或发信号。
- 开机/崩溃自动重启 NOT_INSTALLED；安装不等于可重复提交。

## 程序接口

从仓库根目录、已有受控 Python 环境执行。路径参数必须是实际冻结文件，不在公开仓库假定存在私有数据。

```sh
python -B -m research.broadband56_nn.frequency_physical_export_once --request /absolute/path/ONCE_CONFIG_V1.json --check
python -B -m research.broadband56_nn.frequency_physical_export_once --request /absolute/path/ONCE_CONFIG_V1.json
```

以上实际私有配置的只读 preflight 已通过，且第二条已执行一次。已安装后不要再次运行；no-clobber 会拒绝同一输出。配置固定源码 pins、原 producer 身份与配置、原终态路径、两个独立同级输出目录和报告专用等待截止时间。

有限批处理默认只读 plan，输入必须为原统计程序真实终态，不可伪造中间终态：

```sh
python -B -m research.broadband56_nn.frequency_physical_export_batch --final /absolute/path/FINAL_RECEIPT.json --out /absolute/path/new_export --mode plan --aggregate formal-selected --aggregate formal-all --aggregate development-selected --aggregate development-all --aggregate selection
```

审核同一 plan 后使用 `--mode run`。已安装等待器已显式选定这五类汇总；不会把全部逐请求图盲目重新渲染。逐请求修复另有显式 `--request-contract`；复用确切已验收修复图可传 `--approved-export-index`，两者不会由等待器自动推断或新增。

## 续接与边界

批处理在同一精确 plan 下只复用完整作者收据，缺失、改变、失败或无收据半成品均保留且拒绝重复导出。逐图处置分别为 REUSED_APPROVED、EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE、ORIGINAL_UNREVIEWED。批次 COMPLETE 仅表示有限导出完成，绝不把物理 PARTIAL 改为全部 320 完成。

原物理预算 18:00 UTC、统计预算 18:15 UTC 不变；新增本机等待准入截止 19:00 UTC 只属于报告整理。已准入的有限导出自然结束，不发送超时终止信号。只有原 consumer 退出后才启用一个串行本地绘图 worker，BLAS=1，无模型/GPU/仿真调用。

最新已验收汇报仍是[累计 40 请求的 10 页演示稿](../frequency5to20_advisor_cumulative40_20260908_v1/README_CN.md)。16 频率模型均是首预算 PARTIAL/PROVISIONAL；全 320 请求研究目标继续。当前 MARS 现场只确认原队列在推进，不把新日志中的值直接并入已验收结果。

本次代码和证据身份见 RUN_STATE.json 与 SHA256SUMS。公开交付不包含私有配置、路径、权重、数据、PDK 或许可证。

## 原队列后续预算：仅源码候选，未部署

另保留可选 operational-budget 实现；它不修改原配置、旧截止时间、请求、模型、几何、评分、并发或输出身份。新预算须独立成文并使用独立源码 release；已解候选的提取恢复仍使用原 wrapper 身份。原队列须自然产生合格 PARTIAL，且原 queue/global 租约均可取得，才允许后续接入；失败证据仍拒绝自动重试。

新增 31 项合成测试主体通过；此前两个测试运行在 fixture setup 即失败、各 0 项主体执行，原 JUnit/失败收据均保留。此处没有改 MARS 截止时间、选择新预算、传输新 release 或启动续接。Native Linux 验证 NOT_RUN，部署 NOT_DEPLOYED。源码候选不能写成“已经自动延长物理队列”。
