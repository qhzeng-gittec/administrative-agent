# 存储层

MongoDB（motor 异步）+ Redis（redis.asyncio）+ 内存回退。

## 文件

- `models.py` —— 事实、策略、会话事件、用户语义与组织记忆的 Pydantic 模型
- `mongodb.py` —— MongoDB 客户端 + 索引创建
- `store.py` —— `DataStore` 统一接口 + `MongoStore` / `MemoryStore`
- `redis_store.py` —— 会话（工作记忆）+ 缓存，`RedisSessionStore` / `MemorySessionStore`

## 集合（对应技术方案 3.1 + 鉴权/审核扩展）

`departments` / `documents` / `chunks` / `doc_relations` / `skills` / `hooks` / `rules` /
`feedback` / `traces` / `glossary` / `user_profiles` / `dept_memory` / `global_memory` /
`faq_cache` / `users` / `review_orders` / `test_questions` /
`strategy_versions` / `strategy_executions` / `strategy_proposals` / `experiments` /
`vector_embeddings` / `async_jobs` / `conversation_events` / `conversation_summaries` /
`user_memory_items` / `org_memory_items` / `memory_candidates` / `memory_usage` /
`memory_audit` / `memory_topics` / `memory_sequences`

其中：

- `users` —— 登录账号（角色 `student`/`admin`，可绑定 `dept_id` 做部门管理员）
- `review_orders` —— 人工审核单（新文档自动出题 → 系统作答 → 逐题判定）
- `test_questions` —— 测试题库（审核反馈积累，驱动部门渐进退出）
- `conversation_events/summaries` —— 有 TTL 的情景记忆，不再用 Trace 兼任会话历史
- `user_memory_items` —— 细粒度、版本化、可删除的用户低敏记忆
- `org_memory_items` —— 带官方来源、部门范围、审核状态和时效的组织知识
- `memory_usage/audit` —— 记忆使用与变更审计
- `async_jobs` —— 入库与 Loop 作业的 `queued/running/completed/failed`、阶段进度和结构化结果

## 说明

- `STORAGE_MODE=memory` 时全内存实现，离线开发/测试无需 MongoDB/Redis。
- TTL 索引自动清理会话事件、摘要、用户记忆、组织记忆和热点明细。
- Mongo `increment()` 使用 `$inc`，用于多 Pod 下的事件序号和热点计数，避免整文档覆盖丢更新。
