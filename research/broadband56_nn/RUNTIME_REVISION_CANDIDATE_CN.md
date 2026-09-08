# 七组10K运行身份迁移候选

本增补只修复原冻结请求与修正后运行入口的衔接，不改变模型、数据选择、划分、评价、初始化、seed、更新步数或1800秒预算。

## 边界

- `AUTOMATIC_TRIGGER=NOT_INSTALLED`。代码实现、候选代码QA、精确提案激活QA、正式训练是不同门禁。
- 原25模块保持原文件及SHA；仅允许delivery.py、bb00_delivery.py、seven_suite.py运行控制差异，新增runtime_revision.py。其余22模块必须字节一致。
- 原study key/control root、experiment_plan.json、初始event/status、永久锁及SHA索引保留。准备阶段只允许尚有原4文件且唯一事件为WAITING_FOR_10K的研究目录。
- 候选提案及原件副本写在研究目录外的新no-clobber目录。没有模型加载、优化、数据读取或生产访问。
- 旧入口不会自动升级，也未被密码学禁用。迁移后必须显式使用候选请求路径；不要调用仍绑定历史代码的旧脚本。

## 独立审核后可用的命令接口

以下是接口模板，不是已部署定时器，也不是正式10K已启动的声明。路径和SHA必须来自实际冻结证据；不可填近似值。先在固定候选代码根目录使用原研究解释器。

```sh
"$RESEARCH_PYTHON" -m research.broadband56_nn.seven_suite prepare-runtime-revision \
  --base-request "$BASE_REQUEST" --base-sha256 "$BASE_SHA256" \
  --predecessor-index "$ORIGINAL_SHA256SUMS" --index-sha256 "$INDEX_SHA256" \
  --out "$NEW_EXTERNAL_PROPOSAL_DIR"
```

准备生成PROPOSAL.json及CANDIDATE_REQUEST.json，不激活。独立QA必须返回`GO_FOR_PRETRAINING_RUNTIME_REVISION`，绑定proposal/base/candidate/source集合/runtime_revision源SHA；启动人另提供QA收据自身SHA。普通CODE_ONLY_GO不满足激活门。

```sh
"$RESEARCH_PYTHON" -m research.broadband56_nn.seven_suite activate-runtime-revision \
  --proposal "$PROPOSAL" --proposal-sha256 "$PROPOSAL_SHA256" \
  --qa-receipt "$EXACT_INDEPENDENT_GO" --qa-sha256 "$GO_SHA256" \
  --out "$NEW_EXTERNAL_ACTIVATION_ATTEMPT"
```

激活使用原study/device双锁，锁内重验SHA并独占发布ACTIVE_RUNTIME_REVISION.json；不创建训练预算、不提交训练。不明身份在输出路径获准前失败时不写任何文件；获准后的失败保留ACTIVATION_FAILED.json。失败目录不得复用。已成功激活后可在新外部attempt读取同一marker返回ALREADY_ACTIVE，即使合法journal已前进也不重复激活。

只有实际激活、原数据/资源/去重门均满足后，才使用原统一入口及精确活动请求。check-and-run-once、resume-seven、evaluate-seven、package-seven均解析同一原study根目录，不新建身份绕过锁。parent argv、worker收据、partial attempt请求、stage终态、registry和package都绑定活动请求及原plan。异源或无身份的partial checkpoint必须人工核对证据，不能自动吸收续训。

## 验证口径与限制

本候选测试只验证合成元数据、真实临时文件/锁竞争及mock父子控制流。没有真实模型训练、真实数据冻结或物理精度结果。BB00历史回放与正式新10K训练仍分开；BB00宽带NOT_SUPPORTED，REAL_EMX_VALIDATION=NOT_RUN。

文件读取将哈希与JSON解析绑定同一描述符，拒绝读取期间替换；激活前再次核验提案与GO。锁是协作式排他，不是针对恶意同用户并发改写所有文件的安全边界。打包截止时间仍为准入/update-start门，不强杀在途更新，也不续期。

失败初审及测试记录保留。代码通过不能替代精确真实提案QA，未安装的自动触发不得写成自动训练能力。
