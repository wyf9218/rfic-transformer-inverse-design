# 5–20 GHz 单频 Tandem 研究交接

最新机器状态见 `FREQUENCY5TO20_STATUS_20260908.json`。此前15 GHz开发5K结果和旧七组结果保留，不混入正式10K主表。

## 已实际完成

16个整数频率均使用原256×3正向/反向结构：10维几何→四指标、四指标→10维几何。频率只用于模型路由，不加入网络输入。每组先从零训练正向，用validation选best，再冻结正向训练反向；诊断续训不进入排名。

正式快照为accepted_sequence=1..10000，保留完整56点及有效性标记。共同几何hash划分6015/2002/1983；各频率严格有效子集不同，20 GHz实际只有279/89/96，不能称10000行都训练过。

两worker普通有限队列已正常结束，16对F/I完成预算、保存、独立进程加载/诊断续训、包内权重重载、原始test评价。全部仍PROVISIONAL/PARTIAL，不宣称充分收敛。

16组分别完成10000随机三目标的Q10..20扫描，共1760000逻辑候选，另有固定留出三目标审计。代理候选数不是独立EMX求解数。旧四目标一次推理压力试验是独立补充，不冒充Q扫描。

首组15 GHz沿用已存在开发5K模型：11原始候选中9项完成实际GDS、Calibre零阻断、同一GDS的fresh EMX与严格标签，2解析失败保留。预先选定Q14的Lp/Ls/Qmin/|k|相对误差分别约0.079%/0.341%/0.636%/0.294%，严格联合命中。该结论只限一个预选目标；q_emx仍为空，不能从9成功子集推断完整11项最优，也不能用于新正式10K15模型的物理准确率。

独立有限物理程序已实际部署并启动。每请求先完成一个原生求解，再进入全局最多4槽；Cadence和Calibre按请求单通道，原候选固定顺序跨频率轮转，资源不足等待。09:07:13UTC只读收集已核验首4请求、36个求解：开发15为9、正式5/10/20分别为8/9/10。正式5的Q15/17/18在Calibre阻断而未求解；四个请求均未达到11/11严格有效，所以q_emx均为空，不代表总体物理精度。MARS重启后自动启动未安装；同一config只在确认无活进程/子进程及占锁后诊断恢复，不重复提交。

本机只读首轮汇总程序也已实际启动，120秒普通Python检查，固定前16请求/176原候选逐个收集一次，完成或截止后自动输出CSV、JSON、百分比误差图和SHA闭包；不提交物理、不发信号、不改远端，不用AI持续轮询。未来真实图人工视觉QA仍NOT_RUN，首轮最终产物在当前快照尚未生成。本机睡眠/SSH故障可影响收集，不能承诺开机自动恢复。

[离线导师报告 v2](frequency5to20_report_20260908_v2/report.html)及[可供GPT审查的结构化内容](frequency5to20_report_20260908_v2/artifact.json)已公开导出。报告仅含较早两个物理个案/17求解，快照与当前36求解分开；结构和实际源数据通过校验，但浏览器布局验证尚未完成。源码/数据/模型记录以对应SHA和私有入口为准，不把公开仓库当成含私有权重。

## 统一入口

所有命令均从本代码库根运行，私有路径由操作者提供；不要把公开仓库当成含有私有数据或权重。

```sh
python -m research.broadband56_nn.frequency_queue run --config /PRIVATE/QUEUE_CONFIG.json
python -m research.broadband56_nn.frequency_queue run --config /PRIVATE/QUEUE_CONFIG.json --resume
python -m research.broadband56_nn.frequency_ready --study /PRIVATE/STUDY --out /PRIVATE/QSCAN --seed 2026100815
python -m research.broadband56_nn.frequency_package infer --index /PRIVATE/PACKAGE/MODEL_INDEX.json --frequency-ghz 15 --label-mode STRICT_LUMPED --targets '[1.2,1.2,14,0.3]'
python -m research.broadband56_nn.frequency_rollup --training-root /PRIVATE/TRAINING --formal15 /PRIVATE/FORMAL15 --qscan-root /PRIVATE/QSCANS --out /PRIVATE/NEW_ROLLUP
```

队列第一条已真实运行并结束，不再提交；第二条仅用于实际中断且无活进程时的显式恢复，不能为追求更大预算重跑。历史15 GHz完成包只读复用，不重新调用新版posttrain覆盖旧source identity。模型恢复命令及device锁/预算参数见 `frequency_package resume --help`；诊断续训与正式续训必须区分。

`frequency_ready`是有限模型完成依赖，不是AI轮询。只对未完成的同身份Qscan执行；已有结果不再推理。`frequency_rollup`只读取保存的预测，不加载模型，实际逐行重算、校验原指标并输出SHA闭包，拒绝覆盖已有目录。相对MODEL_INDEX身份和解析可行性联合命中回归均有测试。

## 评价边界

- A：正向对原测试几何真实EM标签的误差。
- B：原四目标反向几何的SELF_PROXY误差，联合命中包含原解析/响应有效性门。
- Q扫描：固定Lp/Ls/|k|，每个q与自己的四目标评分；分开记录纯响应命中和响应且解析可行。
- Fresh EMX：必须保持候选→实际GDS→Calibre→同一GDS求解→56点提取身份链；尚未完成不填数。

评分跨度[2.5nH,2.5nH,20,0.8]；成功绝对容差[0.125nH,0.125nH,1,0.04]，不是目标相对5%。随机三目标的真实可达性默认UNKNOWN。域外、解析失败、DRC失败、缺失及pending保留原分母；不以成功子集冒称11项完整最优。

聚合CSV的误差统计保留全部有限代理预测（包括解析失败），另核验原评价的可评价条件分母，两个口径明确区分。单seed、固定留出描述性统计，不提供未经设计的总体置信区间或因果排名。

训练预算同时受12000步和200 epoch上限约束；高频有效训练数较少，17/18/19/20 GHz实际更新8875/5538/3357/1744。反向响应项有600步warmup和3000步ramp，19/20 GHz在渐增阶段结束。这是本轮PARTIAL限制，不能把跨频率差异解释成已收敛性能优劣；未擅自改变冻结实验或覆盖模型。

## 唯一物理队列与恢复入口

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 python -B -m research.broadband56_nn.frequency_physical_dispatch --config /PRIVATE/RUNTIME_CONFIG.json --preflight
PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 python -B -m research.broadband56_nn.frequency_physical_dispatch --config /PRIVATE/RUNTIME_CONFIG.json
```

该接口已用实际Linux16组元数据、8次继承锁检查及原有同GDS命令preflight验证；实际Cadence、GDS检查、Calibre和新5GHz求解均已走通。精确机器命令/config/SHA/PID在私有handoff；公开占位符不是含数据的可直接运行配置。第二条同时是检查后恢复入口，无独立重复提交命令。已有完整阶段按精确intent/terminal复用；仅有solver PASS而缺提取时仅调用extract，失败/部分原生输出停门保留。不能手工删除锁、改失败记录或换新输出路径重复仿真。

源码、原数据、私有配置、PDK和生产运行均不改动。当前281合成测试通过，测试数不等于真实训练或仿真数。

图表和私有模型/日志的精确路径与SHA在私有交付目录，公开仅工程代码、测试和脱敏状态。剩余最高优先级为完成真实物理链，再按冻结请求顺序跨频率轮转；不是追加复杂网络实验。
