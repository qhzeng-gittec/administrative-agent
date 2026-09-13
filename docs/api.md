# REST API 说明

后端默认监听 `http://localhost:8000`，交互式文档见 `/docs`（Swagger UI）。

## 通用约定

- 前缀：`/api/v1`
- 返回结构：`{"code": 0, "message": "ok", "data": ...}`；错误 `code != 0`。
- 鉴权：登录后前端在请求头携带 `Authorization: Bearer <token>`；管理端接口要求管理员角色。

## 鉴权接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | 登录，返回 `{token, user}`；账号 `student/student123`、`admin/admin123` |
| GET | `/api/v1/auth/me` | 当前登录用户 |
| GET | `/api/v1/auth/users` | 用户列表（管理员） |

## 管理端（管理员）接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/dashboard` | Loop 全景仪表盘（部门阶段/审核统计/Skills/反馈/trace） |
| GET | `/api/v1/admin/agents` | 各部门子 Agent 可视化数据 |
| GET | `/api/v1/admin/review/orders` | 审核单列表（可过滤 dept_id/status） |
| GET | `/api/v1/admin/review/orders/{id}` | 审核单详情（含题目/系统答案/引用） |
| POST | `/api/v1/admin/review/orders/{id}/submit` | 提交审核结果（逐题判定，累计正确率，自动推进 Loop 阶段） |
| POST | `/api/v1/admin/documents/{doc_id}/review` | 为文档（重新）生成审核单 |
| GET | `/api/v1/admin/review/stats` | 各部门审核统计与渐进退出进度 |
| GET | `/api/v1/admin/feedback/pending` | 待处理反馈（Observe） |
| GET | `/api/v1/admin/traces` | 最近问答 trace（Execute） |
| POST | `/api/v1/admin/loop/phase` | 设置全局 Loop 阶段 |
| GET | `/api/v1/admin/system-insights` | 五个记忆平面、事实平面、反馈与策略实验聚合视图 |

> 人工审核 Loop：新文档入库自动出题 → 系统作答 → 发部门管理员审核单 → 逐题判定沉淀题库与反馈；
> 当某部门累计正确率 ≥ `REVIEW_ACCURACY_THRESHOLD` 且样本 ≥ `REVIEW_MIN_SAMPLES` 时，该部门自动进入
> `human_out_of_loop`，未抽中的文档自动通过，同时保留抽检和错误回退。

> 部门管理员数据隔离：`dept_id` 非空的管理员（如 `jwc_admin`）调用管理端接口时只能看到/操作本部门的
> 部门、文档、Skill、Hooks/Rules、审核单；跨部门访问返回 403。系统管理员（`admin`，`dept_id` 为空）看全部。

## 接口列表

### 健康检查

- `GET /healthz` —— 存活探针（K8s liveness）
- `GET /readyz` —— 就绪探针（检查 MongoDB/Redis 连接，K8s readiness）

### 对话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat` | 非流式问答，返回答案 + 引用 |
| POST | `/api/v1/chat/stream` | SSE 流式问答 |
| GET | `/api/v1/chat/{session_id}/history` | 会话历史 |
| DELETE | `/api/v1/chat/{session_id}` | 清空会话 |

请求体：

```json
{
  "query": "退课截止时间是第几周？",
  "session_id": "uuid",
  "dept_ids": null
}
```

`user_id` 只从 Bearer Token 获取；客户端提交的身份字段不会被接受。会话读取和删除同时校验所有权。

> 学生端不再手动选部门：`dept_ids` 传 `null`，后端由 **DeptRouter（自动部门路由 Agent）** 将问题匹配到最符合的部门。
> 响应新增 `route` 字段：`{dept_ids, dept_names, matched_by(keyword|llm|all), confidence, reasons}`，供前端展示「自动路由到 XX 部门」。

### 文档入库

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/documents/upload` | 上传文档（multipart），返回 Redis Stream 异步作业 id |
| GET | `/api/v1/documents/jobs/{job_id}` | 查询本人发起的入库作业状态 |
| GET | `/api/v1/documents` | 文档列表（按部门过滤） |
| GET | `/api/v1/documents/{doc_id}` | 文档详情 |
| POST | `/api/v1/documents/{doc_id}/status` | 更新状态（审核/归档） |

### 部门

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/departments` | 部门列表 |
| POST | `/api/v1/departments` | 创建部门 |
| GET | `/api/v1/departments/{dept_id}/conflicts` | 该部门冲突检测结果 |

### 反馈

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/feedback` | 提交显式反馈（点赞/点踩/纠错） |
| POST | `/api/v1/feedback/implicit` | 提交隐式反馈（copy/follow_up/abandon） |

### 记忆治理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/memory/me` | 查看当前用户的 active 低敏长期记忆 |
| POST | `/api/v1/memory/me` | 用户明确同意后写入偏好/资料；敏感字段拒绝 |
| DELETE | `/api/v1/memory/me/{memory_id}` | 删除当前用户自己的记忆 |
| GET | `/api/v1/memory/sessions/{session_id}/summary` | 查看本人会话摘要 |
| GET | `/api/v1/memory/organization` | 管理员查看权限范围内组织记忆 |
| POST | `/api/v1/memory/organization` | 发布带官方 source refs 的组织记忆 |
| DELETE | `/api/v1/memory/organization/{memory_id}` | 撤销权限范围内组织记忆 |

组织 `faq/procedure_tip/conflict_resolution` 没有 active `doc_id/chunk_id/document_version` 来源时拒绝发布。

### Loop / 管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/skills` | Skills 列表 |
| POST | `/api/v1/admin/skills/{id}/approve` | 审核通过 Skill |
| POST | `/api/v1/admin/loop/run` | 手动触发一次 Loop 循环 |
| GET | `/api/v1/admin/loop/jobs/{job_id}` | 查询 Loop 作业实时状态、阶段进度和结构化结果（系统管理员） |
| GET | `/api/v1/admin/loop/jobs?limit=10` | 最近 Loop 作业历史（系统管理员） |
| GET | `/api/v1/admin/loop/stats` | Loop 运行统计 |
| GET | `/api/v1/admin/glossary` | 术语表 |
| POST | `/api/v1/admin/glossary` | 新增术语映射 |

## 内部接口（供部门 Agent / pi Runtime 调用）

前缀 `/api/v1/internal/*`，供部门 Agent 和 pi Runtime 的白名单工具调用，不面向前端。
**全部内部接口要求请求头 `X-Internal-Token: <INTERNAL_API_TOKEN>`**（与 pi-agent 环境变量保持一致）；
未配置 `INTERNAL_API_TOKEN` 时内部接口直接返回 503（fail-closed）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/internal/retrieve` | 混合检索，返回 chunk（含 doc_title） |
| POST | `/api/v1/internal/dept/answer` | 部门 Agent 专用问答；强制请求 dept_id 与实例 `DEPT_ID` 一致 |
| GET | `/api/v1/internal/departments` | 部门列表 |
| GET | `/api/v1/internal/calendar` | 校历（全局记忆） |
| GET | `/api/v1/internal/glossary` | 术语表 |
| POST | `/api/v1/internal/feedback` | 提交反馈 |
| GET | `/api/v1/internal/feedback/pending` | 待处理反馈 |
| POST | `/api/v1/internal/artifacts` | 保存 Skill/Hook/Rule（Loop 产物） |

## pi 智能体服务接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 探活 |
| POST | `/answer` | 问答（pi 编排 DAG） |
| POST | `/loop/run` | 触发 Loop 循环 |

`/answer` 与 `/loop/run` 是 pi Runtime 的兼容接口，不是生产 Web 主链。生产问答由 Python 固定 DAG 调用
`/v1/agent/run`；生产 Loop 由 Python `LoopEngine` + Redis Stream Worker 治理，pi 只执行 Reflect 等概率性 Agent。
