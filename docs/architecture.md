# 架构与模块划分

## 1. 服务拓扑（前后端分离 + 模块分离）

```
┌────────────┐   REST /api/v1   ┌──────────────────────────┐
│ Next.js     │ ───────────────► │ Python Orchestrator/API  │
└────────────┘                  └───────────┬──────────────┘
                              控制平面      │        Agent 执行
                                           ├──────────────► pi Runtime
                                           │ 并行 HTTP
                         ┌─────────────────┼─────────────────┐
                         ▼                 ▼                 ▼
                  dept-agent-jwc    dept-agent-cwc    dept-agent-*
                         └─────────────────┬─────────────────┘
                                           ▼
                               MongoDB + Redis Stream
```

## 2. 五层架构 → 代码映射

| 层 | 设计组件 | 代码位置 |
|---|---|---|
| L1 接入层 | API Gateway / REST / Web | `backend/app/api/`、`web/`（Next.js） |
| L2 Harness 协同层 | Orchestrator / Intent / Rewriter / Retrieval / Answer / Verifier / Feedback | `backend/app/harness/` |
| L3 Loop 进化层 | Loop Engine / Skill Miner / Hook / Rule / 策略实验 | `backend/app/loop/` |
| L4 数据与检索层 | MongoDB / Vector / Embedding / BM25 / Reranker | `backend/app/pipeline/`、`backend/app/retrieval/`、`backend/app/storage/` |
| L5 基础设施层 | Docker / K8s / Redis / Monitoring | `deploy/`、`docker-compose.yml` |

## 3. 一次问答请求的数据流

1. `web` 调 `POST /api/v1/chat`（Next.js rewrites 代理到 backend）。
2. `backend/app/harness/orchestrator.py` 构建动态策略与记忆上下文，掌握唯一控制权。
3. Python 固定 DAG 调用 pi Runtime 执行 Intent、Rewrite、Answer、Verify；pi 不可用时回退 Python 本地实现。
4. 部门 Agent 通过 `DEPT_ID` 强制隔离，只访问自身文档；跨部门请求允许部分成功并返回失败部门。
   全局 Orchestrator 会把经过治理和预算裁剪的记忆上下文透传给部门 Agent，保持多轮实体一致。
5. MongoDB 保存共享向量、策略版本、实验分桶和执行结果；Redis Stream 承载入库、反馈与 Loop 作业。
6. 手动 Loop 通过持久作业记录阶段进度和结构化结果；Web 自动轮询，而不是把入队 `job_id` 当成执行报告。

## 4. 事实平面与五个记忆平面

```text
active documents/chunks（事实）
          │
          ▼
MemoryContextBuilder
  ├─ Redis 会话工作记忆
  ├─ conversation_events/summaries 情景记忆
  ├─ user_memory_items 用户语义记忆
  ├─ org_memory_items 组织知识记忆
  └─ Skills/Hooks/Rules/Experiments 程序性学习记忆
```

官方事实权威最高。组织记忆只能帮助召回，系统必须重新验证其 `doc/chunk/version` 并把原始 chunk
加入证据集；用户偏好和会话摘要不能成为制度引用。上下文使用情况写入 `memory_usage` 并关联 trace。

## 5. 控制平面与执行平面

- Python 是唯一控制平面，保证权限、事实、记忆和策略只有一个事实来源。
- `services/pi-agent` 是统一 Agent 执行平面，执行 Intent/Rewrite/Answer/Verify/Reflect。
- Python 传入已经治理的 prompt、官方 chunks、动态 Rules、allowed tools 和阶段超时。
- pi 不读 MongoDB、不决定 `dept_id`、不发布策略；调用失败时 Python 自动降级。
- `loop/default_skills.py` 提供三条可执行基线 Skill；自动挖掘 Skill 与其共用版本、灰度和 outcome 治理。

## 6. 职责边界

- **Python 后端**：确定性逻辑 —— 文档解析/切片/向量化、混合检索、存储、对外 API、冲突检测、鉴权、人工审核 Loop。
- **pi-agent**：概率性推理执行 —— Agent loop、模型调用、结构化输出和白名单 tool calling。
- **web**：展示与交互（登录 / 学生问答 / 管理后台），仅通过 REST 与后端通信。

## 7. 鉴权与人工审核

- `backend/app/auth.py`：账号 + HMAC Token，角色 `student`/`admin`（可绑定 `dept_id` 做部门管理员）。
- `backend/app/review/review_engine.py`：新文档自动出题 → 系统作答 → 审核单 → 累计正确率 → 部门渐进退出（人在环中→环外）。
- `backend/app/api/deps.py`：`require_user` / `require_admin` / `scope_dept` 鉴权依赖；管理端接口均要求管理员角色，
  部门管理员（`dept_id` 非空）数据隔离，系统管理员（`dept_id` 空）看全部。

## 8. 自动部门路由（DeptRouter）

- `backend/app/harness/agents/dept_router.py`：学生问题 → 最匹配部门。
  策略：关键词精确匹配（`DEPT_KEYWORDS`，可解释）→ LLM 语义路由 → 全部部门兜底。
  返回 `{dept_ids, dept_names, matched_by, confidence, reasons}`。
- `Orchestrator.answer()` 在用户未指定 `dept_ids` 时先调用 `DeptRouter.route()`，将路由结果透传给 Python DAG，
  并在响应中返回 `route` 字段，前端据此展示「自动路由到 XX 部门」。
