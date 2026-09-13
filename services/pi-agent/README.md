# 文枢 pi Agent Runtime

基于 [pi](https://github.com/earendil-works/pi)（`@earendil-works/pi-agent-core` + `@earendil-works/pi-ai`）实现的统一概率性 Agent 执行引擎。

> Python 仍是唯一控制平面；pi Runtime 只执行经过 Python 权限、事实、记忆和策略治理后的 Agent 请求。

## 架构职责

| 服务 | 职责 |
|---|---|
| **pi Runtime（本服务）** | Intent / Rewrite / Answer / Verify / Reflect 的 Agent loop、模型调用、结构化输出和受控工具调用 |
| **Python 控制平面** | 鉴权、固定 DAG、事实检索、记忆治理、部门隔离、动态策略、灰度、发布和回滚 |

pi 服务通过 `fetch` 调用 Python 后端的**内部接口**（`/api/v1/internal/*`）获取数据、执行检索、写入反馈与 Loop 产物。

## 目录结构

```
services/pi-agent/
├── src/
│   ├── config.ts        # 环境配置
│   ├── providers.ts     # pi-ai 提供方（DeepSeek / 中转站，OpenAI 兼容）
│   ├── runtime.ts       # 运行时装配（model + streamFn + tools）
│   ├── tools.ts         # AgentTool 工具集（检索/校历/部门/术语/反馈/沉淀产物）
│   ├── agents.ts        # Agent 定义 + runAgent 运行器 + prompt
│   ├── orchestrator.ts  # Intent→Rewrite→Retrieve→Answer→Verify DAG
│   ├── loop.ts          # Loop 引擎（Observe→Reflect→Adapt）
│   ├── server.ts        # Fastify HTTP 服务
│   ├── doctor.ts        # pi 侧自检
│   └── index.ts         # 入口
├── Dockerfile
└── package.json
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 探活 |
| POST | `/v1/agent/run` | 统一 Agent 执行协议，要求 `X-Internal-Token` |
| POST | `/answer` | 问答（pi 编排完整 DAG） |
| POST | `/loop/run` | 兼容型 pi 内部 Loop（非生产 Web 主路径） |

`/answer` 和 `/loop/run` 为兼容接口，同样要求内部 Token；生产主链使用 `/v1/agent/run`。

## 本地运行

```bash
cd services/pi-agent
npm install
npm run doctor       # 验证 pi + DeepSeek 是否可用
npm run dev          # tsx watch 开发模式（:8100）
npm run build && npm start   # 生产模式
```

## 环境变量

见 [`.env.example`](./.env.example)。关键：`DEEPSEEK_API_KEY`、`BACKEND_URL`（Python 后端地址）。

## 关键设计

1. **pi Agent loop + tool calling**：Intent/Answer 等 Agent 是 pi `Agent` 实例，模型自主决定调用工具
   （`list_departments` / `get_glossary` / `retrieve_documents` / `lookup_calendar` 等）。
2. **Loop 主路径边界**：生产 Loop 由 Python `LoopEngine` + Redis Stream Worker 控制，pi 通过统一
   `/v1/agent/run` 执行 Reflect；`loop.ts` 与 `/loop/run` 仅保留为兼容/独立实验路径，不负责生产策略发布。
3. **职责收敛**：pi 不读取业务数据库，也不决定部门权限、记忆写入或策略发布。
4. **故障降级**：Runtime 不可用或输出不满足 schema 时，Python Agent 使用原有本地 LLM/规则路径。
