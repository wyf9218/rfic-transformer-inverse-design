# 逐频率真实物理统计与增量图表

两个程序只读取已发布、SHA绑定的本地研究证据，不调用模型、不连接服务器、不生成几何或仿真。输入的CAPTURE必须由既有只读收集入口在请求收据完整关闭后发布；不能拿正在写入的临时文件或裸路径凑输入。

```sh
python -B -m research.broadband56_nn.frequency_physical_statistics --manifest /PRIVATE/DISPATCH_MANIFEST.json --captures /PRIVATE/PUBLISHED_CAPTURES.json --out /PRIVATE/NEW_STATISTICS
python -B -m research.broadband56_nn.frequency_physical_statistics_figures --stats /PRIVATE/NEW_STATISTICS --out /PRIVATE/NEW_FIGURES --previous /PRIVATE/PREVIOUS_FIGURES/FIGURES_RECEIPT.json --reuse-request-figures /PRIVATE/FIRST15/DELIVERY_RECEIPT.json
```

这些是参数化接口，不是包含私有输入的可直接运行公开配置。第一批没有previous时省略该参数；已存在首15图的本次试验必须给出其真实交付收据。不得重复调用同一已完成统计快照的绘图或覆盖输出目录。

## 证据和分母

- 冻结物理框架为320请求、每请求11个原Q候选，共3520槽。单频20请求、220槽；候选不是独立使用请求。
- 模型ID、频率、数据范围、标签模式、目标来源及协议SHA共同分组。开发5K15GHz不混入正式10K15GHz物理表现。
- 同时输出EMX减目标、EMX减求解前冻结的网格几何代理预测；全部候选与预选q_proxy分表，严格有效主分析与有限数值诊断分开。
- 四指标MAE、RMSE、Bias均为物理单位；P50/P90/P95是绝对误差分位数。相对百分比仅辅助，近零参考值不编造百分比。
- 评分跨度为[2.5 nH,2.5 nH,20,0.8]，成功绝对容差[0.125 nH,0.125 nH,1,0.04]；不是目标相对5%。
- 原始槽、已完成请求槽、严格有效槽三种分母分别保存。尚未完成的槽不填0误差；完整规划框架的解析失败可在求解前已知，不能误写为本批完成请求的失败数。
- 独立求解数由原求解收据路径/SHA/字节身份去重；相同S4P字节不自动认定为相同求解。另列唯一S4P SHA数。
- q=15、q_proxy、q_emx只在同一完整11项严格有效请求集比较。该集合为空则NOT_AVAILABLE，不排名幸存者。
- 95%区间采用固定seed、2000次整请求cluster bootstrap。少于2请求不可估计；2–9请求标PROVISIONAL。它不是总体准确率、候选IID区间或因果提升证据。

## 输出与增量运行

统计保存逐候选JSON/CSV、METRICS、REQUEST_STATUS、FREQUENCY_STATUS、SELECTION_COMPARISON、SUMMARY、MANIFEST及SHA256SUMS。最后发布STATS_RECEIPT.json，未见PUBLISHED收据不得作为完成结果引用。

绘图保留5–20 GHz全部频率槽，缺测留空；开发15另表。包含正式模型MAE/P95、固定尺度热图、各原始Q的评分和显眼百分比、满足样本条件时的条件ECDF/原框架达成曲线及密度图。每图的源表、精确统计收据SHA和适用分母可追溯；人工视觉QA是独立收据，不因脚本正常退出而自动通过。

新快照只画新请求图；旧请求需内容签名和工件SHA一致才复用，聚合图按新增请求快照更新。本次私人部署使用一个普通有限Python消费者接续既有首轮收集器：首16期间只读已发布本地收据，干净终态、原PID退出及本地lease检查通过后才接续其余304个请求的只读收集。它不是新的训练/物理调度器，没有信号、重启或生产修改权限逻辑；实际安装状态以私有LAUNCH_RECEIPT及进程检查为准，开机自动恢复未安装。

已有正式16频率训练、随机Q扫描、模型权重及物理原始结果保持原样。本接口补齐结果汇总，不产生新的训练证据或物理真值。
