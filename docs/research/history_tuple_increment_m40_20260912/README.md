# M40：两条历史候选原始几何身份闭合

2026-09-12 16:28:09 UTC；只做未完成的身份绑定，不重新运行物理检查或模型。

- 复用本地完整原CSV：112行、192列、329959字节；SHA与原远端来源1bb5ce9f…精确一致。原生所有者确认无需远端访问或传输。
- authority ordinals 1、2各按完整S4P路径匹配唯一源行；原10字段tuple与已绑定GDS审计完全相等。
- 使用现存历史规划器实际_vector_digest函数：NumPy float64、round12、原字节SHA256，2/2精确等于原candidate_geometry_identity。保留canonical9/raw17/production6独立别名；它们不同并非不一致，不用一种哈希覆盖另一种。
- 这证明具体tuple和原哈希的对应关系，不证明当前本地规划器字节就是历史运行字节，也不证明工艺和EM端口配置已完全兼容。
- UNKNOWN split和benchmark arm/pair预约保留；历史正式入账0，新EMX0，新增训练0。尚需当前兼容EM执行证据、历史111正式入口及跨来源唯一合并。
- 复用M35/M36实际物理分量收据；未重做9项部署测试、EMX、GDS审计或旧数值提取。生产及安全边界安装不依赖本工作。

RESULT.json保存逐项检查、实际源路径、source ordinal、全部10字段及独立哈希。脚本只写新的no-clobber研究结果；不写正式账本。
