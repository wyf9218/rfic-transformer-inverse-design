# 受控 nested real10K/20K 预注册补充 v1.1

冻结时间：`2026-08-24T08:00:00Z`  
状态：`PRE_RESULT_ADDITIVE_CLARIFICATION_FROZEN`

静态 trainer 源码审计发现，base v1 已冻结 schedule 名称、optimizer-update 域和 `60/300` update，但没有逐项写出五个仍会影响训练的默认常量。此补充在数据物化、训练、评价或任何结果访问前追加冻结：

- warmup fraction：`0.05`
- ramp fraction：`0.25`
- adaptive EMA decay：`0.95`
- adaptive multiplier min/max：`0.25/4.0`

base v1 文件保持原字节和 SHA=`19aca777…16417`；本补充不更改研究问题、唯一自变量、数据分母、模型结构、seed、指标或统计方法，只消除隐式默认漂移。
