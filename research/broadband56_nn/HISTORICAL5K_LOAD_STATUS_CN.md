# 历史5K私有包：真实模型加载验收

2026-09-08 UTC。本条补充已发布的打包及报告状态，不改变模型实现或新10K合同。

## 已完成

- 新独立CPU进程实际实例化并严格加载四forward和六inverse的best/last：20个角色，13个不同checkpoint文件。
- 配置与真实网络架构一致，权重tensor digest、数据/normalizer/合同及共享F2/FREF身份核对通过。
- 原包235文件前后身份相同；导入的研究代码来自包内software，而非历史worktree。
- 本次不是仅反序列化字典：另执行了实际网络的严格`load_state_dict`，没有运行`forward()`。
- CPU2线程，总耗时3.85秒，子进程峰值RSS约422MiB；推理、优化器构造/更新和反传计数均为0。

父验收收据SHA256：`f6f7fd01d02d4143a6fb1b5f06a5029c365417dfa0222d87c7479d66253faf41`。
子进程收据SHA256：`82406651afa366a0b7c52bf245a6bd5c50f8affd0f5839c49e0b5b7fc8df1fa5`。
原包前/后文件索引SHA256均为：`3c3de4da84c7e6bf23eb24fe811f541b556e09c3676e1bf41d801a4342a9af7a`。
独立静态验收92项检查通过；收据SHA256：`1b69611b735e62f9af2434b95adef35fec21c116562262c68b585b717cf05315`，仅放行已声明的加载证据，不是整项目GO。

## 必须保留的限制

- `LOAD_ONLY_PASS`不等于迁移后的推理或续训验收；后二者仍`NOT_RUN`。
- 使用Python层调用/文件审计，不宣称原生操作系统沙箱验收。
- 原报告使用现有Chrome补做浏览器验收时，官方工具在静态图表阶段超时；视觉验收未通过。未安装浏览器、未改数值，原`structural_only`报告与全部失败证据保留。
- 浏览器失败收据SHA256：`c8ed0e8ae31732748d92fd39f24f169d1bf0c51cb9172cc0a80d3f0f444c924c`。
- 六组旧模型仍`PRETRAINED_PARTIAL`；新10K七组正式训练`NOT_STARTED`，自动触发`NOT_INSTALLED`，`REAL_EMX_VALIDATION=NOT_RUN`。

本次没有新训练、数值评价、MARS或生产/GUI操作，不延长旧训练预算。
公开提交只含这份脱敏状态说明；私有权重、数据、日志和内部路径未上传。
