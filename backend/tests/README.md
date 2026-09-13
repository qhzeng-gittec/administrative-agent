# 测试

离线可跑（`conftest.py` 强制 `STORAGE_MODE=memory` + `EMBEDDING_PROVIDER=hash`），
无需 MongoDB/Redis/网络/真实 LLM。

```bash
cd backend
pytest            # 运行全部
pytest tests/test_chunker.py   # 单文件
```

当前基线：**59 passed**（离线配置）。

| 文件 | 覆盖 |
|---|---|
| `test_chunker.py` | 语义切片（标题层级 / 大小边界 / 空文档） |
| `test_bm25.py` | BM25 检索 + 部门过滤 |
| `test_retrieval.py` | 混合检索 + 向量 cosine |
| `test_agents.py` | 意图/改写/校验的无 LLM 回退 |
| `test_pipeline.py` | 解析/清洗 |
| `test_loop.py` | 规则/钩子引擎 + Skill Miner 聚类 |
| `test_e2e.py` | 入库→问答、Loop 循环冒烟 |
| `test_loop_runtime.py` | 基线 Skill 幂等种子、真实 workflow、灰度、动态 Rule、Trace 排序和回滚 |
| `test_department_agents.py` | 部门隔离/并行部分成功/共享向量 |
| `test_job_queue.py` | Redis Stream 队列持久状态、running 阶段和进度回写 |
| `test_memory_architecture.py` | 事实权威、五平面、来源/隔离/敏感性/TTL/删除/原子计数 |
| `test_memory_migration.py` | 旧四层记忆迁移幂等性与无来源 FAQ 隔离 |
| `test_memory_api.py` | 用户记忆 API、敏感字段拒绝和组织记忆管理权限 |
| `test_pi_runtime.py` | pi Runtime 优先执行、本地降级、协议和启用条件 |
| `test_document_versions.py` | 重复文件、版本链和旧版本归档 |
| `test_review.py` | 审核单逐题判定、未审禁止默认通过和阶段推进 |
| `test_store.py` / `test_redis_store.py` | Mongo 原子更新与 Redis 凭据日志脱敏 |
