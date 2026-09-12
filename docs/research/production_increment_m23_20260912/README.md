# M23：批次尾部、断点覆盖与两条历史SRF实证

完整现场窗口：2026-09-12 11:49:33.122961 → 11:59:12.528673 UTC。

- 新27终态：24 fresh EMX、3解析失败；19strict、14范围内strict。
- fresh正式+12（6train/1validation/5test），历史+0。DOE176(train)、TRAIN058(test)仍pending，不计已提交训练支持。
- 正式合格并集下界6671。当前批238终态=212fresh+26analytic、core104/formal102。
- 实际18个独立EMX×2CPU，正好是当前批次剩余18项；不是资源上限18或48实跑。
- 当前资源策略重放可再准入24（非真实permit），没有其它阶段等派发。
- 唯一已有installer仍等待原批自然终态/owner退出；23eddf修复未部署，现行自动提交和跨批程序保持。

## 新27的一次落点接收

[LANDING_RECEIPT.json](LANDING_RECEIPT.json) 保存精确命令与输入身份。
正式6train各落不同已充分支持格：冻结3801基线空0/欠填0，高K>.8为0。DOE为19结果/16EMX/6core/5formal；邻域为8/8/8/7。
[TRAIN_SOURCE_ROWS.json](TRAIN_SOURCE_ROWS.json) 分开6正式train和2pending，保留几何、单位、有效性和源绑定；不补造原提案没有的目标/预测格。

## 已接收来源并集断点续接（不是全生产覆盖）

[TRAIN_COVERAGE_RECEIPT.json](TRAIN_COVERAGE_RECEIPT.json) 与 [TRAIN_RECEIVED_UNION_COVERAGE.csv](TRAIN_RECEIVED_UNION_COVERAGE.csv) 仅在M22保存的3840身份基础上接续其25新train，不含本次M23的6train或未接收中间窗口。
一次实际运行：3840→3865，新增25/重复0/挂起0；163→163/512，新格0；欠填1912→1911（[0,0,0]由3→4）。
既有程序新增checkpoint参数及已消费源SHA幂等拒绝。[RESUME_TARGETED_TESTS.json](RESUME_TARGETED_TESTS.json) 为4个新resume-only合成测试，不是旧物理或全套QA重跑。没有重算3821/旧120/M21。

## 两条历史原111点响应

[SCOUT14_TWO_DIAGNOSTICS.json](SCOUT14_TWO_DIAGNOSTICS.json) 保存原S4P、命令、提取器和完整111点CSV引用；两次现成提取器实际调用，未重新EMX。
5b93827f...原Lp/Ls/K在范围内，但当前映射的primary/secondary SRF约19.7666/19.7567GHz；bca15651...约19.9898/19.9777GHz。两条15GHz descriptor有效，但均不满足15GHz低于半SRF。
与历史四指标数值一致不证明工艺/实际GDS/端口兼容。所取layout metadata没有完整几何向量；split/跨源去重仍未闭合。保留原111点、不伪写56点、不因Q<10硬拒绝全池；这2条不提交正式合格，formal+0。

生产没有等待这些离线任务；未重训15组/重跑旧128或64/生成论文图片。100K与FINAL后的独立10K尚未完成。
