# 冻结 3×256 架构的 nested real10K/20K 受控实验预注册 v1

状态：`PRE_RESULT_PROTOCOL_FROZEN_CODE_AND_DATA_BINDINGS_PENDING`  
冻结时间：`2026-08-24T07:35:00Z`  
结果盲：是

## 研究问题

在数据来源、split、共同 holdout、normalization、网络结构、decoder、loss、optimizer、optimizer-update 预算、paired seeds 和评价流程全部相同时，real-EMX source table 从 10,000 行增加到其精确 nested 20,000 行会带来什么变化？唯一预期自变量是 source/gradient-training row count。

旧 selected20k 不是历史 real10k 的超集，因此不进入因果比较。新 20K 只复用既有权威 real-EMX 100K 表，不生成新 EMX；保留历史 10K 的全部行，再从历史 train cells 按预冻结 stable-SHA quota 选 10,000 个无 geometry/Touchstone 重复的新行。

## 四类分母

| arm | source table | gradient train | validation | test |
|---|---:|---:|---:|---:|
| small | 10,000 | 7,871 | 1,227 | 902 |
| large | 20,000 | 17,871 | 1,227 | 902 |

validation/test 的逐行 identity 和完整 physical cells 必须相同，三 partition 的 cell overlap 必须为 0。报告不得用“10K/20K 模型”代替这四类分母。

## 冻结模型与训练合同

- forward：`10→256→256→256→4`，135,428 参数。
- inverse：`4→256→256→256→10`，135,434 参数。
- 总参数：270,862；GELU；independent-sigmoid decoder；无 local refinement。
- paired seeds：`20260711, 20260712, 20260713`，每个 seed 在两臂使用相同初始化随机流。
- batch 1,024；row-uniform continuous-permutation；forward/inverse 各精确 1,200 updates。
- validation 每 20 updates；Adam，LR `1e-3` constant，weight decay `1e-6`。
- declared-range MSE；response/geometry-anchor/topology 权重=`1/0.01/0`；Q 训练语义为 exact Qmin。
- 两臂共用逐字节相同的 declared midpoint/half-range normalization 和 decoder envelope；禁止按 arm 重算经验统计。
- 六臂训练全部 `validation_only`、test access=0；全部 weights 和收据通过后，才由另行冻结并独立 QA 的 evaluator 一次性解封共同 test。

## 评价与统计

共同 real-EMX test 902 行的主估计对象是 forward 对真实 geometry/response 行的误差。inverse 到数据集中单个 geometry label 的距离仅作次要诊断，因为反问题解不唯一。

冻结 physical target frame 仍为唯一现有 10,000 帧（SHA=`c9d7…6407`），不得重生成。主面板为 8,000 条 `|K|<=0.8`；2,000 条 high-K 只作超支持域压力测试；full10k 另列。该帧上的 inverse→own-forward 指标只能称 tandem proxy self-consistency，不能称 fresh-EMX 物理准确率。

每个 feature 必须报告 MAE、RMSE、P50/P90/P95/P99/Max absolute error；同时报告 joint NRMSE、fixed-span engineering joint error、Q target-met rate 和 Q shortfall。K 接近零时不以 target-relative APE 作主指标。

主统计单位是三个 paired training seeds。必须展示三组原值和 paired lines，再报告 `large-small` delta、delta 均值、样本标准差和 df=2 的双侧 t 95% 区间，并显式标注 n=3 很小。physical-cell bootstrap 只作固定有限帧的空间敏感性，不冒充部署总体置信区间。

## 执行与释放门

- serial single-child；每个 seed 内 small 后 large。
- 每臂启动前 load1 必须不高于 40；nice 19；BLAS 4 threads。
- 任一 unfinished attempt 为 `AMBIGUOUS`，禁止自动重启；任一已记录 FAIL 禁止自动重试。
- 训练必须有独立 exact-GO 收据，且逐字绑定代码、trainer、Python、数据物化闭包、合同、seed 和输出身份。
- 任何臂失败或任一控制变量不一致，都不得发布完整 paired 因果结论；失败分母和负结果必须保留。
- 本实验不运行新 EMX，因此不得发布“新预测几何的 fresh-EMX 精度”。

机器可读完整合同见 `CONTROLLED_EXPERIMENT_PREREGISTRATION_V1.json`；代码/data SHA 将由后续不可变 receipt 追加绑定，不回写本预注册。
