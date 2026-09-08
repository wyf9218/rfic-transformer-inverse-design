# 5–20 GHz 单频 Tandem 研究交接

最新机器状态见 `FREQUENCY5TO20_STATUS_20260908.json`。此前15 GHz开发5K结果和旧七组结果保留，不混入正式10K主表。

## 已实际完成

16个整数频率均使用原256×3正向/反向结构：10维几何→四指标、四指标→10维几何。频率只用于模型路由，不加入网络输入。每组先从零训练正向，用validation选best，再冻结正向训练反向；诊断续训不进入排名。

正式快照为accepted_sequence=1..10000，保留完整56点及有效性标记。共同几何hash划分6015/2002/1983；各频率严格有效子集不同，20 GHz实际只有279/89/96，不能称10000行都训练过。

两worker普通有限队列已正常结束，16对F/I完成预算、保存、独立进程加载/诊断续训、包内权重重载、原始test评价。全部仍PROVISIONAL/PARTIAL，不宣称充分收敛。

16组分别完成10000随机三目标的Q10..20扫描，共1760000逻辑候选，另有固定留出三目标审计。代理候选数不是独立EMX求解数。旧四目标一次推理压力试验是独立补充，不冒充Q扫描。

首组15 GHz使用已存在开发5K模型立即启动独立MARS Cadence-only链：11逻辑候选中9解析通过派发，2失败保留。该观测时Calibre/fresh EMX尚未完成，q_emx不可填造；生产控制器、数据、环境与配置未改。

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

图表和私有模型/日志的精确路径与SHA在私有交付目录，公开仅工程代码、测试和脱敏状态。剩余最高优先级为完成真实物理链，再按冻结请求顺序跨频率轮转；不是追加复杂网络实验。
