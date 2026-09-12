# M24：真实自动续批、增量入账与安全交接修复

最终统计窗口：2026-09-12 11:59:12.528673 → 12:29:42.606254 UTC。四份互补现场捕获构成三段首次结果接收；观察截面不证明此后持续存活。

- 新150终态：135 fresh EMX、15解析失败；84 strict，其中63范围内strict唯一几何，63全部正式入账。
- fresh正式增量65＝本窗口63＋窗口前2条pending补登；35train/17validation/13test。历史增量0，期末待入账0。窗口中另2条pending随后闭合，已包含在63中，不能再加。
- 正式并集可靠下界6736，不是100K完成；头记录、来源与SHA见最终快照。
- batch000002自然结束：230真实EMX＋26解析失败，正式111；原程序于12:01:02自动启动000003，未重置旧预算。
- 12:29:42实际12个独立EMX×2CPU／14 permits／执行器容量48。当前批132终态（117 EMX＋15解析失败），124未终态；未终态不等于正在运行。
- 资源检查PASS；策略回放的额外25槽不是实际许可或已实现并发。磁盘可用461631918080 B；配额UNKNOWN；未清理。当前批实际1090715648 B＋预计保留3225346048 B，预算5368709120 B。
- 23eddf性能修复尚未安装。当前owner2057484、metadata2057487继续运行；有限交接installer3265510已实际运行，只暂挂续批父进程3841226的下一批派发，不向健康owner/metadata/模拟器子进程发信号。

[最终快照](FINAL_SNAPSHOT.json) 是当前统计入口。[中间快照](SNAPSHOT.json) 保留12:19证据，不覆盖成最新状态。最终快照scope中的THREE指三段结果窗口；source_observations实际列出四份互补捕获，不能据scope字符串推定捕获数。

[59条首次接收](LANDING_NEW59_RECEIPT.json)、[43条追加](LANDING_ADD43_RECEIPT.json)、[48条追加](LANDING_ADD48_RECEIPT.json) 各对应一次首次投影，没有重算旧窗口。
三段[train源1](TRAIN_NEW59_SOURCE_ROWS.json)、[train源2](TRAIN_ADD43_SOURCE_ROWS.json)、[train源3](TRAIN_ADD48_SOURCE_ROWS.json) 保留几何、字段、strict、结果和提交绑定。new59 schema是格式版本，实际分母由window_id及行数决定；含非train backfill的集合必须先按split过滤。DOE/邻域来源没有原目标格或预测格，不补造目标误差。

## 安全边界已实际保留，安装尚未完成

原等待installer516965由精确PID身份受控关闭。12:28:45.937129的[BOUNDARY_RESERVED](handoff/BOUNDARY_RESERVED.json) 证明唯一替代installer已经暂挂原续批父进程的64线程，等待当前批与metadata自然收尾；并非“新运行包已部署”。
[SPECIFICATION](handoff/SPECIFICATION.json)、[原installer终态](handoff/PREVIOUS_INSTALLER_TERMINAL.json)、[START](handoff/START.json)、[LAUNCH](handoff/LAUNCH.json) 和一次[真实Linux合成父子集成检查](handoff/INTEGRATION_TEST.json)保留。集成检查不是EMX试跑。
已归档此次最小代码：handoff/code/。这些是已执行操作的证据，不是允许重复运行、另起installer的快捷入口；运行依赖指定私有路径与身份。

恢复边界：不可逆终止旧续批父进程前，仍持有pidfd时，finally可恢复父进程；终止完成后，部署/读回/启动出错不能恢复原PID，必须按实际TERMINAL、检查点、部署及启动证据和进程身份使用既有恢复入口。INSTALL缺失不等于可以重启。SIGKILL/主机故障不受Python finally保障。
最终截面INSTALL/TERMINAL均尚无，未观察到此类失败。当前合法下一入口是同一installer的终态/安装/续批证据，不新建控制器。

12:08曾出现隔离检查失败并重置稳定样本计数，12:13后恢复；两项争用记录精确绑定到当前批DOE002，另一strmout归属仍UNKNOWN。没有放宽隔离检查，也不能把低并发全部归因于磁盘或某一个已证原因。

## 已接收train来源并集（非全部生产覆盖）

已有程序增加对完整绑定的内嵌几何的支持，旧外部geometry入口与重复来源拒绝保持。四次实际续接：
3865→3871（+6，前窗口M23）→3886（+15）→3895（+9）→3906（+11）。
合计41新唯一＝前窗口6＋本窗口35正式train；覆盖163/512不变，欠填缺额1911→1909，一格达到5。这不是全生产覆盖，也不是算法优势证明。没有修改模型或采样方法。

原native header的UNKNOWN不改写，通过精确request/pin/release与显式split_resolution校验train；不消费validation/test物理标签。
[最终续接收据及命令](TRAIN_COVERAGE_FINAL_RECEIPT.json) 对应3906；[中间收据](TRAIN_COVERAGE_RECEIPT.json) 对应3895，二者不混同。
[针对性检查](TRAIN_EMBEDDED_TARGETED_CHECKS.json)覆盖前三步及新增3项合成测试，其中受影响1项修复后单独复核；第四步有独立真实运行收据，不改写此前检查记录。[首次失败](TRAIN_EMBEDDED_FIRST_FAILURE.json)保留，已成功六条未重算。

历史下一100条只恢复offset65076入口；同源公共工艺/端口/版本缺证未闭合，未处理资格UNKNOWN，不写0或100合格；本窗口历史新读、新认证与正式提交均0。未要求原生重扫或重新求解。

没有重训15组、重跑旧128/64、全套旧QA或生成AI论文图。100K及FINAL冻结后的独立10K仍未完成；生产不等待此发布。
