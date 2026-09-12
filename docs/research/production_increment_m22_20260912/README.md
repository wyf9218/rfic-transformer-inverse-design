# M22：真实生产增量与 train 身份绑定

现场窗口：2026-09-12 11:34:31.323430 → 11:49:33.122961 UTC。

- 新79终态：70 fresh EMX、9解析失败；46 strict，36范围内strict唯一全部正式入账。
- fresh正式+36（train25/validation3/test8），历史正式+0，backfill0，pending0；认证并集可靠下界6659。
- 原批累计211终态=188fresh+23analytic；45无终态，不等于45正在运行。
- 申请48、执行器48、实际1个独立EMX×2CPU；资源策略重放可再准入36，不是36实跑。
- EMX等待3、Calibre等待1、Cadence等待5；各工具permit1；33候选还无tool intent。
- CPU资源不再是M21的2槽限制。当前供给/派发没有填满槽位；不能将全部差额定量归因于单个代码点。
- 已有唯一有限安装器516965/start401625264仍等待BATCH_TERMINAL_AND_OWNER_EXIT；23eddf修复未部署，不声称提速。
- 现行自动提取、筛选、去重、正式提交、跨批接续继续；本窗口尚无新batch启动。旧预算不重置。

## 新结果一次落点接收

[LANDING_RECEIPT.json](LANDING_RECEIPT.json) 保存实际命令、源pin和25train/3validation/8test分母；[TRAIN_SOURCE_ROWS.json](TRAIN_SOURCE_ROWS.json) 保留25条完整几何、单位及正式记录绑定。
25个train落21格，对冻结3801基线空格命中0/欠填命中1（DOE107，[0,0,0]计数3）。高K>.8为0；Q10..20为25。
79个原提案均没有目标格/预测格；不补造目标误差或格命中率。

## 单独的已接收 train 来源并集

[TRAIN_COVERAGE_RECEIPT.json](TRAIN_COVERAGE_RECEIPT.json) 与 [TRAIN_RECEIVED_UNION_COVERAGE.csv](TRAIN_RECEIVED_UNION_COVERAGE.csv) 仅累计原3821起点和M21的19个正式train，不含本次M22的25train或未接收中间窗口。
9DP规范几何重建19/19匹配、重复0，得到3840身份；覆盖163→163/512，新增格0，欠填总量1913→1912。
状态是RECEIVED_SOURCE_UNION_NOT_FULL_PRODUCTION；数据质量检查通过补取本地19个train几何闭合身份，没有读取val/test响应标签或修改模型/采样器。

## 历史小分区

[SCOUT14_TWO_MEMBER_HANDOFF.json](SCOUT14_TWO_MEMBER_HANDOFF.json) 绑定原14条中2条当前范围成员的实际GDS/命令/S4P，原111点保持。现行兼容、SRF、split隔离及跨源去重尚未闭合；formal+0，未知资格不写0。

未重训15组、未重跑旧128/64/旧132、未执行新全套QA、未生成论文图片。100K与最终独立10K未完成。生产不以本次出版为门禁。
