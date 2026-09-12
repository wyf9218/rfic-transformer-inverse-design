# M27 — 第4批恢复、15份新增真实EMX与首历史成员实际工件检查

最新实际执行截面：**2026-09-12 13:23:11 UTC，28个独立EMX×2CPU运行**，资源健康7/5，owner/metadata/continuation精确活。13:16→13:23新增9fresh、1EMX阶段失败、1解析失败；4core中1正式、3pending。
该新增正式train已首次续算：[后继接收](POST_RECOVERY_LANDING_RECEIPT.json)/[覆盖续算](POST_RECOVERY_TRAIN_RECEIPT.json)，3943→3944、占格163/512不变、欠填1908→1907；3pending不参与覆盖与认证计数。
本轮12:58→13:23合计24fresh、11范围内strict候选；8个已认证唯一fresh正式入账、3待提交，history+0，认证下界6797。见[最终新增截面](FINAL_SNAPSHOT.json)。CPU扣系统及在途预约后剩5.8262核，共享诊断额外2工具，不夸称48实跑；普通程序继续，当前批11/256终态。以下13:16为恢复初期的保留截面。

- 产出窗口：2026-09-12 12:58:26→13:12:04 UTC，15份新fresh EMX；7个范围内strict唯一全部正式（6train、1test），history+0，pending0，认证并集下界6796。
- 13:16:53 UTC恢复后截面：第4批owner1052218、metadata1052221、continuation1041957真实存活；申请48/执行容量48、实际native0，资源稳定检查1/5。此时无新EMX；不能宣称48并行已达成。
- 原第3批256终态（234fresh、22解析失败）与116正式全部保留。原installer因缺失第3批registry注册记录而失败，已在新目录补齐原9060字节SHA绑定，按原checkpoint恢复第4批；无旧批重跑/旧预算重置/科学改动。
- 自动资源检查、部分准入、候选回调和后继接续由普通程序负责；恢复后开始阶段尚未观察到第4→5批转换。资源投影不是实际并发；每solver2CPU，工具容量Cadence8/Calibre8/EMX48。
- 新6train仅对已接收源并集续算：3937→3943，占格163/512、欠填1908均不变；不是全生产池覆盖，不宣称策略优势，不训练模型。
- 首历史成员d87ff4d971f5f26f保留111频点，精确15GHz现行映射下strict+范围通过；原GDS的8标签离网及条件端口P005/P007偏移失败，原Calibre未执行/proc执行SHA仍缺。原工件不可直接入账，formal+0；不外推其他63成员，不冒充fresh。

[完整截面](SNAPSHOT.json)、[恢复原收据](RECOVERY_LAUNCHED.json)、[必需启动核验](RECOVERY_PREFLIGHT.json)、[15条实际落点接收](LANDING_RECEIPT.json)、[6train增量](TRAIN_COVERAGE_RECEIPT.json)、[历史成员最终处置](HISTORY_DISPOSITION.json)。
原始路径和SHA在收据中；仅新工件校验，无旧QA/15组训练/旧128与64重跑，无AI论文图。100K/FINAL/独立10000请求仍未完成。
