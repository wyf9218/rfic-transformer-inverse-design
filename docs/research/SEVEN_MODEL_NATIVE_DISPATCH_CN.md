# 七组10K研究：普通调度适配器

这是既有七组统一入口外的一层调度保护，不是新训练器，不改变研究
身份、模型、数据选择、划分、评价或训练预算。正式源码目录的26个
Python模块保持原来的冻结身份。

本项目私有研究Mac已取得精确安装GO，完成一次真实注册与原生触发，
状态为`INSTALLED_NATIVE_FIRST_TICK_VERIFIED`；首次因7.97GiB可用内存
低于原8GiB门槛而等待，没有进入MARS或训练。详见
[脱敏运行证据](../../research/broadband56_nn/NATIVE_DISPATCH_STATUS_20260908.json)。
这不代表任意GitHub克隆已部署；其他环境没有安装收据时仍为
`NOT_INSTALLED`。配置文件、测试和代码GO本身不证明部署。

## 为什么不能直接定时执行原训练命令

原统一入口支持从部分checkpoint恢复；这不等于每次程序退出都可以
自动重新提交。适配器只允许明确未开训的资源/数据等待再次进入入口。
部分训练、失败、未知退出以及父进程中断后缺失终态都要求先核对证据。

| 观察结果 | 后续普通定时检查 |
|---|---|
| 明确WAITING_RESOURCE或WAITING_FOR_10K，且无训练开始证据 | 允许检查同一研究 |
| 当前适配器锁仍被持有 | 不提交第二份 |
| 已有派发意图但无终态收据 | NEEDS_RECONCILIATION，不按PID消失重提 |
| PARTIAL、FAILED或无法解释的返回 | 保留证据并停止自动重提 |
| 已创建训练预算或阶段目录后的资源等待 | 停在恢复核验门，不重启预算 |
| 已完成 | 保留结果，不重新训练 |

每次执行前验证固定配置SHA、独立GO的SHA及其配置/代码绑定，随后
验证同一研究的请求、激活标记、只读访问配置和前台wrapper的SHA。
派发记录保存在独立研究调度目录中，不写生产目录，也不改原研究计划。

适配器在持有永久锁时先写不可覆盖的INTENT，再调用固定的
`/bin/bash <已冻结wrapper>`；日志和COMPLETION单独保存。子进程继承锁，
不使用PID超时、强杀、删除锁或自动延长训练期限。普通状态检查
`status`和`check-config`不启动wrapper。

## 已实现CLI的调用形式

下列变量必须由私有部署收据给出，不是从GitHub自动获得的私有数据。
`BB_DISPATCH_SCRIPT`指本仓库的`tools/seven_model_native_dispatch.py`。

```sh
"$BB_RESEARCH_PYTHON" -B "$BB_DISPATCH_SCRIPT" check-config \
  --config "$BB_DISPATCH_CONFIG" --config-sha256 "$BB_DISPATCH_CONFIG_SHA" \
  --qa-receipt "$BB_DISPATCH_QA" --qa-sha256 "$BB_DISPATCH_QA_SHA"

"$BB_RESEARCH_PYTHON" -B "$BB_DISPATCH_SCRIPT" status \
  --config "$BB_DISPATCH_CONFIG" --config-sha256 "$BB_DISPATCH_CONFIG_SHA" \
  --qa-receipt "$BB_DISPATCH_QA" --qa-sha256 "$BB_DISPATCH_QA_SHA"

"$BB_RESEARCH_PYTHON" -B "$BB_DISPATCH_SCRIPT" tick \
  --config "$BB_DISPATCH_CONFIG" --config-sha256 "$BB_DISPATCH_CONFIG_SHA" \
  --qa-receipt "$BB_DISPATCH_QA" --qa-sha256 "$BB_DISPATCH_QA_SHA"
```

`tick`可能进入真实训练，不是只读诊断命令；没有精确GO不得执行。
原私有foreground命令已经验证到资源等待，不能据此宣称数据传输、
正式七组训练或终态打包已经通过实际验收。

## 普通macOS调度边界

最小候选使用独立用户LaunchAgent、绝对参数及低频StartInterval，
`RunAtLoad=false`、`KeepAlive=false`、`AbandonProcessGroup=true`。
不修改旧的MARS watcher、不使用`kickstart -k`、不创建AI定时唤醒。

本机launchd手册说明：机器睡眠或上一次调用尚未退出时，StartInterval
可以被丢弃，不保证补跑，也不承诺数据达到10K后立即启动。
AbandonProcessGroup用于避免适配器父进程结束后launchd清理其进程组；
它不保证训练能够跨系统重启或退出登录继续运行。

安装前必须独立审核精确候选、配置和plist，并有新的安装/回读收据。
测试只使用临时合成元数据、mock或明确隔离的普通Python子进程；
这不是MARS验收，也不是模型训练验证。

## 人工恢复

不删除调度记录、不创建第二个study、不将PARTIAL改名成功。先核对
原进程、阶段收据、checkpoint和原绝对截止时间；只有恢复门允许时，
使用既有`seven_suite resume-seven`继续同一研究中未完成的部分。
已完成且身份匹配的阶段复用；过期预算不能通过重新调用获得新期限。
适配器不自行批准恢复，不提供清除失败记录后自动重试的命令。

本层不评价数值，不访问未授权fresh-EMX统计，训练与生产继续隔离。
