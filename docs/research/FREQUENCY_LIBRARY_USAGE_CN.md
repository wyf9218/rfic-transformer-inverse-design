# 按整数频率调用正式模型库

当前正式模型库包含5–20GHz共16对独立256×3 Tandem MLP。频率只选择模型，不作为MLP输入；目标顺序为 `[Lp_nH,Ls_nH,Q_scalar,K_abs]`，其中 `Q_scalar=min(Qp,Qs)`。

从本代码库根目录、已有独立研究Python环境运行：

```sh
python -m research.broadband56_nn.frequency_library resolve --registry /PRIVATE/rollup_v3/MODEL_REGISTRY.json --frequency-ghz 15 --label-mode STRICT_LUMPED
python -m research.broadband56_nn.frequency_library infer --registry /PRIVATE/rollup_v3/MODEL_REGISTRY.json --frequency-ghz 15 --label-mode STRICT_LUMPED --targets '[1.2,1.2,14,0.3]'
```

第一条只解析已保存的模型身份和包位置，不加载权重或训练。第二条复用既有包加载器，核验并调用所选频率的best反向及同频best正向，输出几何与SELF_PROXY诊断。不是Q扫描，不自动挑选其他Q。

## 调用边界

- 未训练、缺失、重复、非整数或不匹配的频率/标签路由明确拒绝；不取最近频率、不复制邻频权重。
- 默认拒绝超出所选模型训练边际范围的目标，不裁剪原目标。仅研究调用可显式添加 `--allow-extrapolation`；外推标记保留。
- 处于四维边际范围内，不代表四指标组合可达或几何可制造。输出是连续几何；本入口不生成GDS、不进行网格导出、Cadence、Calibre或EMX。
- 每次新调用保持 `SELF_PROXY` 和 `REAL_EMX_VALIDATION=NOT_RUN`。不能借其他几何已有S4P证明这次输出正确。
- 模型仍为首预算 `PROVISIONAL_PARTIAL`，不是已充分收敛。该入口不训练、不续训、不改变best/last或物理队列。
- 此正式registry的15GHz指向正式10K包，不是现有物理验证队列使用的开发5K15模型；二者不得混用。

## 身份与迁移

`MODEL_REGISTRY.json`由已有rollup产生，入口核验同目录`ROLLUP_RECEIPT.json`中的registry身份，再核验所选包收据、MODEL_INDEX及精确路由。推理继续由原`frequency_package.infer_index`完整核验包中工件。

registry是现有机器的定位清单，记录的私有路径不在GitHub。单个模型包采用原有可迁移格式，但复制registry文件本身不会复制模型。迁移到MARS时必须传输并核验实际包、生成新的位置清单和收据；本补充不宣称整库已经部署到MARS。

原有单包调用、打包和受预算约束的续训入口继续保留，见`frequency_package --help`及[FREQUENCY_INDEXED_TANDEM](../FREQUENCY_INDEXED_TANDEM.md)。原训练/测试成绩与这次入口验收分开保存。
