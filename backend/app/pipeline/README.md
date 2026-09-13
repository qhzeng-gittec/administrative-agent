# 文档处理 Pipeline

上传 → 解析 → 清洗 → 语义切片 → 元数据提取 → 向量化 → 索引构建 → 跨部门关联挖掘。

## 文件

- `parser.py` —— PDF/Word/Markdown/HTML/TXT 统一解析为结构化块（保留标题层级/表格/列表）
- `cleaner.py` —— 页眉页脚水印去除、全角半角统一、繁简转换
- `chunker.py` —— 按条款语义边界切片（章/节/条/款），目标 300-600 字
- `metadata_extractor.py` —— LLM 抽取生效日期/类型/关键词/适用范围/引用
- `indexer.py` —— 入库编排（解析→切片→向量→BM25→MongoDB）
- `conflict_detector.py` —— 跨部门冲突检测（规则层引用 + 语义层相似 + LLM 判定）

## 语义切片策略

- 不按固定 token 硬切，按标题层级（章/节/条/款）划分 section
- 条款边界（第X条/编号）作为最小单元
- 短 chunk 合并、超长 chunk 硬切，控制在目标区间

## 版本与派生记忆

- 同部门相同文件哈希拒绝重复入库。
- 同标题新文件形成 `supersedes` 版本链；新版本 ready 后旧版本才归档。
- 文档归档、删除或被新版本替代时，绑定该版本的 `org_memory_items` 自动标记为 stale。
