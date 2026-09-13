# 代码与说明文档一致性审计

本文件记录 2026-08-17 对前三阶段改造的文档覆盖检查。状态以当前代码为准。

| 优化点 | 主要代码 | 原文档状态 | 本轮处理 |
|---|---|---|---|
| Token 带 `iat/exp`、身份只取 Token | `backend/app/auth.py`、`api/routes/chat.py` | API 示例仍含 `user_id`，Token 过期未说明 | 更新根/后端/API 文档 |
| 会话、反馈、部门与冲突权限 | `api/routes/*.py` | 仅笼统描述管理端鉴权 | 补充所有权与部门范围说明 |
| active 文档过滤、chunk 回填、冲突检测修复 | `retrieval_agent.py`、`conflict_detector.py` | 检索文档未覆盖 | 更新检索/架构说明 |
| 重复哈希、版本链、归档失效 | `pipeline/indexer.py` | 未说明 | 更新 Pipeline/后端说明 |
| 真实评测集与四项指标 | `evaluation/`、`scripts/evaluate_rag.py` | 根 README 有入口，测试文档不完整 | 补充评测命令与指标口径 |
| Python Harness 为唯一控制平面 | `harness/orchestrator.py`、配置 | 早期根架构图曾写 pi 全量委托 | Python 保留治理，pi 统一执行五类概率性 Agent |
| Skill 执行、灰度、回放、回滚 | `loop/skill_executor.py`、`strategy_evaluator.py` | Loop 文档仅描述旧灰度概念 | 更新 Loop 模块与集合说明 |
| Redis Stream 异步入库/反馈/Loop | `storage/job_queue.py`、`scripts/async_worker.py` | API 仍写同步上传，Loop README 仍写定时 Worker | 更新 API、Loop、部署说明 |
| 独立部门 Agent、`DEPT_ID` 隔离、部分成功 | `dept_agent_client.py`、`internal.py` | 部署文档覆盖不完整 | 更新架构/API/部署说明 |
| Mongo 共享向量与无状态 BM25 | `vector_store.py`、`bm25.py` | 后端 README 仍以 Chroma/bge-m3 为主 | 更新检索与部署说明 |
| Prometheus Adapter + 自定义 HPA | `deploy/k8s/prometheus-adapter.yaml` | `docs/deployment.md` 仍写 QPS/CPU | 更新为 inflight Pods 指标 |
| 事实平面 + 五个记忆平面 | `app/memory/facts.py`、`context_builder.py` 等 | 已完成 | 根/后端/架构/API/存储/记忆/Loop/部署说明均已更新 |
| pi Agent Runtime | `integrations/pi_runtime.py`、`services/pi-agent/src/server.ts` | 已完成 | 统一 `/v1/agent/run`、内部鉴权、白名单工具和 Python 降级 |
| Loop 异步作业可视化 | `backend/app/api/routes/admin.py`、`backend/app/storage/job_queue.py`、`web/src/components/admin/LoopPanel.tsx` | 旧前端只显示入队 `job_id` | 增加阶段进度、历史作业、结构化结果和策略前后差异 |
| 可执行基线 Skill | `loop/default_skills.py`、`loop/skill_executor.py` | 初始 Skill 列表为空，无法演示 | 幂等种子化三条真实 workflow，并保存版本、分桶和指标 |
| 前端三角色与记忆/实验视图 | `web/src/components/` | 旧页面功能分散、核心平面不可见 | 新增角色化控制台、`InsightsPanel`、个人记忆和权限一致展示 |

审计规则：新模块必须在模块 README、`docs/architecture.md`、必要的 API/部署文档中至少各有一个可追溯入口；接口请求模型以 FastAPI schema 为准。
