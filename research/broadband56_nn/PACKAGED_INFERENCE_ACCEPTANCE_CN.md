# 私有包小批量推理验收

状态：`PASS_SMALL_VALIDATION_INFERENCE`。2026-09-08已在macOS、Python3.12、Torch2.8.0、CPU2线程上实际执行。
这不是新10K训练、完整留出评价、精度排名、物理验收或迁移续训通过；Linux/MARS执行尚未验证。

新增独立入口：`tools/verify_broadband56_package_inference.py`。
它不修改冻结训练源码，不访问MARS，不执行optimizer/backward或模拟器。

## 已验证范围

- 已有历史5K私有包中的F1/F2/F3/FREF四个正向及BB01–BB06六个反向，使用各自best checkpoint。
- 在固定原数据顺序中取首两个15GHz四项标签均有效的validation几何；全部组件/任务共用这两个ID，不按输出换样本。
- PHYSICAL任务仅提供15GHz的Lp/Ls/Qmin/|K|四值；完整S参数任务另行提供56×32通道，不混用输入条件。
- 真实正向、反向、几何解码、own-forward/common-forward及物理提取路径运行；保留无效物理标记，不计算误差排名。
- 在此极小样本中，24个反向候选的解析几何检查均通过。这不代表总体通过率、网格化后制造性或EMX准确率。
- 235个包文件执行前后逐项SHA相同；研究模块全部从包内software加载，没有历史训练路径兜底。
- Bundle会解码整个NPZ（包括test存储）；只将所选validation行送入模型，没有新test推理或指标。

首次验收保留FAIL：Torch在macOS导入时可选读取`/proc/self/maps`，被审计拒绝，但Torch捕获异常后继续。
修订版没有授予该访问权限，只识别至多一次来自`torch._load_global_deps`的这项已阻止系统探测。
其他访问拒绝、optimizer或反向传播调用仍判失败；记录实际调用位置。两个版本的输出NPZ逐字节相同，原FAIL不改写。
这些Python诊断不是原生系统调用沙箱。

## 命令

在已安装本项目研究依赖的独立环境中，从代码库根目录运行：

```bash
"$BB_RESEARCH_PYTHON" -B tools/verify_broadband56_package_inference.py \
  --package "$BB_PRIVATE_PACKAGE" \
  --expected-package-receipt-sha256 "$BB_PACKAGE_RECEIPT_SHA256" \
  --out "$BB_NEW_PRIVATE_QA_DIRECTORY" --batch 2
```

四个变量由使用者填写；私有数据/权重不在GitHub中。SHA必须来自已信任的PACKAGE_RECEIPT.json。
输出目录必须全新，且不能与包目录互相嵌套；已有失败/成功目录都会拒绝复用。
命令是手动验收入口，不是后台训练调度。父进程等待不设自动重试，观察超时不等于子进程终止。

## 证据与边界

- 入口源码SHA：`91504e28eae7eca2aa5bb631e6d9f735b904f411d0459c1204cf131dcbee717c`。
- 实际终态收据SHA：`4aa45c4c0ca6461d72b32e03bcbb86e03545d284bfa11f8be604482dbed219ba`。
- 子进程收据SHA：`5fecc34afd5a61339f89330a8e2175c99218fae95acd9bc472ecf85d81921deb`。
- 新增CLI边界测试8项通过；与真实推理验收分别记录，不混称测试覆盖。
- 旧六组科研状态仍PRETRAINED_PARTIAL；BB00不在这次六组包推理验收内。
- 迁移后的optimizer续训、完整迁移数值一致性、Linux/MARS运行仍NOT_RUN；REAL_EMX_VALIDATION=NOT_RUN。
- 正式新10K七组结果不由本检查产生；既有数据/资源/去重门和AUTOMATIC_TRIGGER=NOT_INSTALLED不变。
