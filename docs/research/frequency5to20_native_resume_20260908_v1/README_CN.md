# 原320请求：MARS一次性续接已安装

记录时间：2026-09-08T16:47:39.997Z。整体目标仍 ACTIVE，不是320请求全部完成。

## 实际完成

- 16:44:02 UTC只提交了一次 `at job 1`；16:44:29确认等待器PID2094607存活，UID2259579/start_ticks368977125。
- 等待原PID2146776/UID2259579/start_ticks366027333自然退出；核验时child尚未启动，无失败或终态。
- 原配置不改；新release传输与哈希通过，31项Linux合成测试通过；实际320输入预检PASS，未启动仿真。
- 独立预算记录后续准入截止2026-09-10 18:00 UTC，原18:00截止保持。新预算尚未被实际续接使用，不是截止前全部完成承诺。
- 全频共用原资源与两把原锁；只复用既有dispatcher和原320/3520框架，已完成请求不重跑。
- 原生等待器最终23项合成测试及独立源码审查GO。发现的写收据失败异常已修复：保留失败，并无超时等待已启动child。
- 报告续接源码23项合成测试及独立源码审查GO，保留原consumer、只读reader、统计和绘图源码六个固定SHA。

## 尚未完成，不能合并成PASS

- 原生续接：INSTALLED_WAITING，不是CONTINUATION_STARTED。
- 报告续接：CODE_READY；自动触发NOT_INSTALLED，真实preflight/run尚未执行。
- 旧本机consumer50120与报告waiter80544于本轮只读核验存活，不重复启动。
- 新报告入口只在旧真实FINAL和旧导出终态后接手，原样继承capture/figure证据，只为新结算请求更新，不重画相同baseline。
- 当前新增源码/软件测试不等于新增物理精度证据。已验收PPT仍是累计40请求版本。

## 不重复的科研成果

[16频率及科研边界的交接表](../GPT_HANDOFF_20260908T1610_CN.md)保留16:10历史现场；随后16:34直接检查为58/320收据。
58包括失败候选，不是58组完整strict11。数值/图的验收仍使用snapshot_0031的40请求，不能替换分母。
16对首预算模型仍PARTIAL/PROVISIONAL；176万SELF_PROXY复用，正式10K15的fresh EMX仍NOT_RUN，开发5K15分开。
[导师PPT及QA](../frequency5to20_advisor_cumulative40_20260908_v1/README_CN.md)无需重做。

## 复现与后续入口

- `frequency_physical_resume_once.py --config <精确WAIT_CONFIG> --check`：只读；实际已安装，禁止再次提交。
- 真正续接由已运行等待器自动最多调用一次现有dispatcher；仍要求原自然PARTIAL、无FAILURE及原queue/global leases。
- `python -B -m research.broadband56_nn.frequency_physical_reporting_resume --request <真实依赖冻结后的request> --preflight`：仅在依赖已终态后可用。
- 报告入口没有伪造的占位依赖或预先存在的“成功”命令；薄自动交接仍在实现。
- 源码位于本研究分支，不修改main、生产supervisor、数据、GUI、旧模型或已有EMX工件。
- [机器可读状态及SHA](EXECUTION_STATUS.json)；[本公开交付校验](SHA256SUMS)。私有配置、原始数据、权重与PDK不随GitHub发布。

本里程碑下一合法动作是完成本地一次性报告依赖交接，而不是再安装native等待器、重训或重算已有结果。
