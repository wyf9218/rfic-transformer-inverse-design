# M25：60次新增真实EMX与30条正式入账

现场窗口2026-09-12 12:29:42.606254→12:44:41.975935 UTC。该截面不是之后持续存活的保证。

- 首次接收65新终态：60 fresh EMX、5解析失败；40 strict，其中30范围内strict唯一几何。
- 30全部正式入账：18train/6validation/6test，pending和backfill均0；历史正式增量0。正式头006766，可靠并集下界6766。
- batch3累计197终态=177fresh+20解析失败，86正式合格，59未终态；未终态不是59个实跑。
- 申请/执行器48；实跑7独立EMX×2CPU，许可7 EMX＋1 Cadence。
- 12:43:58硬门PASS并不代表可再派发。实测idle CPU21.4293，扣系统4及未反映于样本起点的8个permit×2CPU后仅1.4293，不足新增一个双CPU求解器，策略回放三工具additional均0。不能由粗粒度normalized负载PASS强塞48路。
- installer3265510/start402005416与现有owner/metadata精确活；standby3841226仍为下一批边界暂停。无INSTALL/TERMINAL/RESUMED，23ed未部署，第4批未启动；不重复启动或杀健康求解器。
- 本窗口唯一原生所有者只做一次增量现场读取，原生动作/信号/新资源probe/旧132结果正文重读均0。普通资源等待、结果提取和入账程序继续。

[FINAL_SNAPSHOT.json](FINAL_SNAPSHOT.json)保存进程、资源与来源身份。[首次接收收据](LANDING_RECEIPT.json)附准确命令与逐项绑定；新core与正式记录request集合精确匹配，不仅是数量相等。[train源](TRAIN_SOURCE_ROWS.json)保留完整10D几何/单位/strict/结果和正式身份。
new59 compact是格式版本，不是本窗口分母；本窗口是65终态、18正式train。DOE/邻域没有原目标或预测格，不编造目标误差。

## 研究覆盖增量

同一既有CLI一次续接18条：3906→3924，重复/已有/域外/HOLD均0；占格163/512、欠填缺额1909均不变。范围是RECEIVED_SOURCE_UNION_NOT_FULL_PRODUCTION，不是全生产池，也没有重新训练模型。
新18条落15格，相对冻结3801基线空格/欠填命中均0，Q10..20为18，高K>0.8为0，最大K0.40321099352096434。不把新合格数量写成覆盖改善或采样优势。
[覆盖续接收据](TRAIN_COVERAGE_RECEIPT.json)保留准确命令、断点及CSV来源；不消费validation/test物理标签，未重跑旧来源或测试。

磁盘可用463459753984 B，配额UNKNOWN；当前批实际1663262720 B＋预计预留2616893440 B低于5GiB。没有清理，也不把挂载点空间变化归为本任务释放量。短窗计数不外推稳定每小时吞吐。

原生程序资源等待期间，已首次取得7.24MB历史summary原字节，SHA与历史声明精确一致；[读取收据](HISTORICAL_PARENT_READ_RECEIPT.json)记录1875个分片的来源入口。它没有历史执行绑定proc/config字节身份、逐几何GDS pin、Calibre/deck零阻断收据与原split，因此历史认证和入账仍为0。现仅请求首shard_000 summary一次，不重读100行标签或旧93缺失路径。该后续读取未闭合时不推断通过；历史工作不是生产前置。初版[SNAPSHOT.json](SNAPSHOT.json)保留读取尚待返回的截面，最终统计入口为FINAL_SNAPSHOT。
100K及FINAL冻结后的独立10000验证仍未完成。旧15组训练、旧128/64、旧QA与论文图均未重做。

历史补证后续见[HISTORY_SUPPLEMENT](HISTORY_SUPPLEMENT.json)：首shard已读取，新增历史config/候选CSV/结果CSV三项SHA绑定；11D包含两侧独立线宽，不能直接当作现有共享线宽10D。当前资格仍缺工艺、逐项GDS/DRC及划分证据，认证和入账仍0；仅5个新明确引用文件的有界接收已提交原owner，不追全库。
