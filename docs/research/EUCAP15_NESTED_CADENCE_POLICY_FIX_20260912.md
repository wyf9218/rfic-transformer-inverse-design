# 15 GHz：Cadence 内层端点策略遗漏的修复记录

以下“未收到”状态保留部署前记录；后续已执行的恢复及两条真实出生见
[04:02 UTC实际增量](qualification_batch_20260912/INCREMENT_0402.md)。执行链完成不等于strict合格。

状态：`LOCAL_SYNTHETIC_REGRESSION_PASS; REVISED_NATIVE_RECEIPT_NOT_RECEIVED`。
本说明只覆盖确定性的缓存与构造策略传递问题，不证明物理精度或制造通过。

## 现场证据与根因

2026-09-12 03:38:29 UTC 的已保存现场观察中，逐例核验了以下两条：

| 请求 | 外层预期缓存身份 | 实际内层缓存身份 | 原消费端结果 |
| --- | --- | --- | --- |
| DOE-000 | `f819b832862ab743` | `0f3750a6df1d955b` | 拒绝 |
| DOE-002 | `14f583ae1c6117c7` | `d49456679cfda345` | 拒绝 |

两例的 roundtrip 自报成功，但其 GDS 与 roundtrip 收据位于另一缓存身份下。
外层 evaluator 已使用新端点策略；调用 Cadence roundtrip 时却未继续传递策略。
roundtrip 新建的第二个 evaluator 因而使用默认 `legacy`，重新导出并送入真实 Cadence。
因此不仅是查找目录错误：实际 Cadence 输入使用了旧构造策略。
原消费端要求 GDS、roundtrip、结果工作目录和候选身份一致，正确拒绝了结果。
不能跟随任意 layout 路径、复制旧收据或放宽身份检查来把这两例变成成功。

## 最小修复与测试覆盖缺口

基于提交 `bb615e19d913003c318775504986a20fdbfa736c`，仅修改两个运行源码：

- `execution/evaluator.py`：向本地 roundtrip 显式传递非 legacy 策略。
- `execution/zeus_cadence.py`：接收策略，并传给其内部 evaluator；默认仍为 legacy。

完整包前缀均为 `rfic_transformer_inverse_design/`。
原 `scripts/run_candidate_queue_dataset.py` 消费端字节未改，所有原身份与物理门禁保留。
此前 outer-wiring 测试只覆盖到首次 export 参数，遗漏了内部二次导出和结果消费链。
这是已暴露的测试覆盖缺口；此前本地 PASS 不能解释为完整原生链已经通过。

新增三个 synthetic 回归，不调用 Cadence、Calibre 或 EMX：

1. legacy 策略：真实双层 evaluator、roundtrip 收据与原消费端一致，通过。
2. 新策略：相同真实软件链消费有效的 synthetic GDS 文件，通过；两次导出身份一致。
3. 故意丢弃内层策略：重现两个缓存目录，原消费端仍拒绝，符合预期。

只模拟底层导出和原生命令边界；这不是仅捕获 export 参数，也不是物理验证。
首轮三个测试因本机中文绝对路径触发原 ASCII SKILL 写入限制而失败，证据保留于 `run_v1`。
第二轮只改用独立 ASCII 测试工作目录，生产补丁不变，三个测试全部通过；未重跑旧套件。

## 新发布边界

端点发布 closure 必须包含六个源文件：原五源中的 evaluator 更新，加上 zeus_cadence。
必须更新精确 SHA 绑定，不能继续沿用旧 zeus_cadence 的 pin。
原生所有者在新的 attempt 目录进行恢复；原候选、顺序、累计预算和既有失败不改写。
不重置预算、不替换候选、不把旧 legacy 结果改名充当新策略结果。
目前尚未收到修复版本的原生部署/验证收据，不能报告其 Cadence、DRC 或 EMX 已 PASS。

## 可核验身份（不包含私有几何、PDK 或现场绝对路径）

- 新 evaluator SHA-256：`840058395057a5efc9ef6927d3104a4a37ba6978cbd9131cc23ac1607f8f5654`
- 新 zeus_cadence SHA-256：`3879d9cbeeb3e46844a3363f94e6e74ecfb2bb6d868fb3df6794064a6cee510f`
- 未改消费端 SHA-256：`3b554546626daed08150f91db5d95983490a5e38ef39605083b2838e2899d7eb`
- 新测试 SHA-256：`6c30ae309a492e27a876e19cd81881bb132faf93dadb5cb82155182394b6073c`
- 私有工作区相对收据：`reports/eucap15ghz_20260908T220300Z/endpoint_cache_consumer_fix_20260912_v1/RECEIPT.json`
  SHA-256：`8257fa6ff9135c2ff3cddd9f4e6b506ee2c49a94fd1ff39ad4e516f356e24343`
- 同目录 `run_v2/TEST_RECEIPT.json` SHA-256：`8ebf98b5f3fe60682b37e6c40df52f715539d712a186084d5c64291277fb37e3`
- 同目录 `run_v1/TEST_RECEIPT.json` SHA-256：`22ca221a9f30861dba52dbf4a691f4dd976d58df74f964af490dfeb4e95528c8`
- 私有观察相对路径：`reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/production256_observation_20260912T033829292707Z/OBSERVATION.json`
  SHA-256：`ac9cdd93cccd8792fb52d2b846a08df260795263fd144bc005f7cc771a1332ba`

上述私有收据仅列身份供有权限者复核，不作为公开仓库中的完整原生证据文件发布。
