# 三输入 MLP Q 扫描应用

该应用是 2026-08-05 MARS 表单式反向综合界面的三输入版本。用户只输入
`Lp`、`Ls` 和 `|K|`，程序固定扫描整数 `Q=10,11,...,20`，并用同一冻结
MLP 生成 11 组 10 维几何候选。页面直接显示选中 Q、结构预览、逐候选
物理特征和误差；在 `physical` 模式下还提供最终 GDS/S4P 下载。

## 两种证据模式

- `proxy`：使用冻结正向代理快速排序，输出结构预览和代理误差。结果仅是
  候选，不是物理验证。
- `physical`：私有 MARS 后端必须为全部 11 个候选生成 GDS、运行 fresh
  EMX、导出 S4P，并在 15 GHz 提取 `Lp/Ls/Qp/Qs/|K|`。程序以
  `Q_scalar=min(Qp,Qs)` 重新计算统一误差，随后输出真实 EMX 误差最低的
  GDS。缺少任意候选、GDS、S4P 或证据字段时，任务失败关闭。

误差排序固定使用四个声明范围 `[2.5 nH, 2.5 nH, 20, 0.8]` 的归一化
RMSE。三个输入保持不变，Q 是唯一扫描变量；并列时选择较小 Q。

## MARS 启动

```bash
python3 -m pip install -e .
source apps/mlp_q_sweep_gui/private_backend.env
bash apps/mlp_q_sweep_gui/START_ON_MARS.sh
```

默认地址是 `http://127.0.0.1:8765/`。冻结权重、真实 GDS/S4P、PDK、许可证
和站点路径均保留在 MARS 私有目录，不进入公开 GitHub。

三个输入之外，`design_id` 只用于文件名和证据绑定，不参与模型推理。

## 后端合同

公开 GUI 会调用：

```text
$RFIC_Q_SWEEP_PHYSICAL_BACKEND \
  --request-json <run>/physical_backend_request.json \
  --out-dir <run>/physical_backend
```

后端必须写出 `<out-dir>/physical_results.json`：

```json
{
  "schema": "rfic_q_sweep_physical_results.v1",
  "label_source": "FRESH_REAL_EMX",
  "results": [
    {
      "candidate_id": "candidate_001_q10",
      "q_target": 10,
      "geometry_sha256": "<request 中的同一 SHA-256>",
      "features_15ghz": {
        "Lp_nH": 1.15,
        "Ls_nH": 1.40,
        "Qp": 12.1,
        "Qs": 11.8,
        "K_abs": 0.76
      },
      "artifacts": {
        "gds": "candidate_001_q10/layout.gds",
        "s4p": "candidate_001_q10/emx.s4p",
        "preview": "candidate_001_q10/structure.png"
      }
    }
  ]
}
```

`results` 必须精确覆盖 Q=10 至 20 的 11 个候选。所有文件必须位于后端
输出目录内，避免路径逃逸和候选错绑。
