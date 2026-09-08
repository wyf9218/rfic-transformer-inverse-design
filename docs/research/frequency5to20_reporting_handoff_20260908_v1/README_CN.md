# 一次性报告交接已安装

2026-09-08T17:01:03.539Z；整体320请求目标仍ACTIVE。

## 实际动作

- 16:58:51 UTC启动一次；16:59:09核验PID86088/UID501及精确启动身份存活，WAIT_LAUNCH收据存在、stderr为空。
- 当前WAITING_READ_ONLY_DEPENDENCIES，没有调用新reporter、生成统计或图，不是全流程成功。
- 本机原consumer50120及exportonce80544精确身份退出后，才读取它们的真正终态。
- 随后只读取已安装MARS等待器自己的新child/FAILURE/TERMINAL，不修改或派发科研进程。
- 原样镜像真正child收据，再冻结resolved request、预检并最多调用一次既有reporting_resume。
- 原captures/figures作为seed保留，同一reporting lock；没有新请求时不重画baseline。
- 23项synthetic测试PASS，独立源码GO；初版22PASS和源码保留，发布换行边界发现及修复留痕。
- 实际本机--check PASS：两个旧进程均alive；实际只读SSH检查返回PENDING/CHILD_NOT_CLOSED，符合原生等待阶段。
- 本机24GiB、内存free74%、盘余498567444KiB；一个串行BLAS1报告worker，只在旧两进程退出后工作。

## 原生续接与预算

[此前安装的MARS at job1](../frequency5to20_native_resume_20260908_v1/README_CN.md)不重复提交。
它等待原native PID自然退出，再最多调用一次原dispatcher，原PARTIAL/noFAILURE/两租约与资源门不变。
新物理准入截止单独记录为2026-09-10 18:00 UTC；本报告等待截止19:00、报告截止20:00。
截至各自安装核验：native child尚未启动，新reporter也尚未调用，不能写成已完成自动续接或320结果。

## 能力与限制

- 状态依赖由普通程序有界检查，不需要AI周期性SSH轮询。
- 自动提交已安装；开机或崩溃自动重启NOT_INSTALLED，同输出目录不得再次启动。
- PID86090的有界idle-sleep assertion跟随实际PID86088；不保证合盖、重启或SSH凭据持续可用。
- 网络暂不可读不是进程终态；仅在报告等待预算内只读再试，不绕过认证或启动重复任务。
- 真正新图仍需其相应数值/视觉验收；最新独立验收PPT仍为[累计40请求版本](../frequency5to20_advisor_cumulative40_20260908_v1/README_CN.md)。
- 16模型首预算仍PARTIAL/PROVISIONAL，176万SELF_PROXY复用；正式10K15物理NOT_RUN、开发5K15分开。

## 入口及证据

本机唯一已运行命令：
`python -B -m research.broadband56_nn.frequency_physical_reporting_handoff_once --request <精确HANDOFF_CONFIG_V1.json>`

禁止重新执行该命令或安装器。
[机器状态/SHA](EXECUTION_STATUS.json)与[校验清单](SHA256SUMS)标明证据；配置及私有源保留在工程目录。
后续只验收真正新终态；不重训、重生成、重求解、不重做40请求PPT。
