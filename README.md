# 文枢 · 跨部门文档处理与问答助手

面向学校多部门（教务处、学生处、财务处、人事处、后勤处、研究生院等）的官方制度文档智能处理与问答系统。

打通 **「文档入库 → 智能问答 → 自我进化」** 全链路：文档自动解析入库、多智能体协同精准问答（可溯源）、Loop Engineering 自我进化（自动沉淀 Skills/Hooks/Rules）、K8s 按部门弹性伸缩。

> 详细技术方案见 [`design_files/文枢-跨部门自进化文档处理与问答助手技术方案.md`](design_files/文枢-跨部门自进化文档处理与问答助手技术方案.md)

## 架构（前后端分离 + 模块分离）

```
┌────────────┐   REST    ┌──────────────────────────┐
│ Next.js     │ ───────► │ Python Orchestrator/API  │
└────────────┘           └────────────┬─────────────┘
                                      │ 并行部门路由
                         ┌────────────┼────────────┐
                         ▼            ▼            ▼
                    dept-agent   dept-agent   dept-agent
                         └────────────┬────────────┘
                                      ▼
                        MongoDB + Redis Stream + Worker
```

| 服务 | 目录 | 职责 |
|---|---|---|
| 前端 | `web/` | React + Next.js 聊天界面 |
| 后端 | `backend/` | FastAPI：文档解析/切片/向量化、BM25+向量混合检索、MongoDB/Redis 存储、对外 REST API |
| Agent 执行引擎 | `services/pi-agent/` | 统一执行 Intent/Rewrite/Answer/Verify/Reflect 的模型推理、Agent loop 和受控工具调用 |

> Python 是唯一控制平面：负责固定 DAG、鉴权、事实、记忆、部门隔离、动态策略、灰度和回滚。
> pi 是统一的概率性 Agent 执行引擎：负责 Agent loop、模型调用和受控 tool calling。pi 不直接决定数据权限或策略发布。

## 目录结构

```
program/
├── README.md                 # 本文件
├── docker-compose.yml        # 全栈编排（MongoDB/Redis/backend/worker/pi-agent/web）
├── .env.example              # 环境变量样例
├── Makefile
├── docs/                     # 架构 / API / 部署 / Loop 文档
├── docs/change-audit.md      # 代码修改与说明文档覆盖审计
├── backend/                  # Python 后端（见 backend/README.md）
├── services/pi-agent/        # pi 智能体服务（见 services/pi-agent/README.md）
├── web/                      # Next.js 前端（见 web/README.md）
├── deploy/                   # K8s / Helm 部署（见 deploy/README.md）
├── design_files/             # 设计输入
└── department_files/         # 示例部门文档
```

## 🚀 安装 Docker Desktop 后怎么跑（推荐）

> 前提：已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 并启动（Docker 图标变为 running）。

```bash
# 1. 进入项目目录
cd program

# 2. 复制环境变量并填写真实密钥（或直接用已提供的 .env）
cp .env.example .env

# 3. 一键构建并启动全栈（首次会下载镜像，较慢）
docker compose up --build -d

# 4. 查看各服务状态与日志
docker compose ps
docker compose logs -f
```

启动完成后：

| 服务 | 地址 |
|---|---|
| 前端聊天界面 | http://localhost:8080 |
| 后端 API / OpenAPI 文档 | http://localhost:8000/docs |
| pi 智能体服务 | http://localhost:8100/health |
| MongoDB | `localhost:27017`（账号密码见 `.env`） |

### 首次导入示例部门文档

```bash
# 种子数据（部门/术语/校历/默认规则）
docker compose exec backend python -m scripts.seed_data

# 导入 department_files 下的 PDF/Word（脚本会自动探测 /app/department_files，也可显式指定）
docker compose exec backend python -m scripts.ingest_department_files --base /app/department_files
```

`seed_data` 与后端启动过程会幂等初始化 3 个可执行基线 Skill（极端天气安全响应、校园事项步骤导航、学术节点与截止日期核验）。它们会真实参与查询匹配、检索扩展、回答模板和策略执行记录，不是只用于页面展示。

管理端“进化 Loop”采用异步作业跟踪：触发后页面自动轮询 `queued → running → completed`，展示 Observe / Reflect / Adapt / Deploy 阶段、反馈信号、根因、候选、发布结果和策略资产前后变化。

### 模型连通性自检（doctor）

```bash
# 验证 DeepSeek + 中转站（bge 重排/Embedding）能否调用
docker compose exec backend python -m scripts.doctor

# 验证 pi 框架 + DeepSeek 是否正常
# 注意：doctor 依赖 devDependencies（tsx），容器镜像内不可用，只能在本地运行：
cd services/pi-agent && npm install && npm run doctor
```

停止与清理：

```bash
docker compose down           # 停止
docker compose down -v        # 停止并清除数据卷
```

## 本地开发（非 Docker）

需 Python 3.9+（推荐 3.11）、Node.js ≥ 22.19。

```bash
# 1) 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export STORAGE_MODE=memory   # 无 MongoDB/Redis 时用内存模式
uvicorn app.main:app --reload --port 8000

# 2) pi 智能体服务（另开终端）
cd services/pi-agent
npm install
npm run dev                  # :8100

# 3) 前端（另开终端）
cd web
npm install
BACKEND_URL=http://localhost:8000 npm run dev   # :3000
```

## 环境变量（关键项）

| 变量 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | 主力对话模型 | `deepseek-v4-flash` |
| `RELAY_API_KEY` / `RELAY_BASE_URL` | 中转站（非 DeepSeek 模型） | `https://yunwu.ai/v1` |
| `EMBEDDING_MODEL` | 向量模型（经中转站） | `text-embedding-3-large` |
| `RERANKER_MODEL` | bge 重排模型（经中转站） | `BAAI/bge-reranker-v2-m3` |
| `PI_AGENT_ENABLED` | 是否使用 pi 统一执行概率性 Agent；失败时自动回退 Python 本地实现 | `true` |
| `PI_RUNTIME_TIMEOUT_*` | pi Intent/Rewrite/Answer/Verify/Reflect 分阶段超时 | `8/10/45/20/45s` |
| `DEPT_AGENTS_ENABLED` / `DEPT_ID` | 全局部门路由开关 / 部门 Pod 强制范围 | `false` / 空 |
| `VECTOR_BACKEND` | 向量存储；K8s 使用共享 `mongo` | `memory` |
| `STORAGE_MODE` | `mongo` / `memory` | `mongo` |
| `AUTH_SECRET` | Token 签名密钥（**生产必须改为强随机值**） | dev 占位值 |
| `INTERNAL_API_TOKEN` | 内部接口 `/internal/*` 共享令牌（backend 与 pi-agent 一致） | 空（未配置则内部接口不可用） |
| `SEED_DEMO_USERS` | 是否创建演示账号（生产设 `false`） | `true` |
| `MAX_UPLOAD_MB` | 文档上传大小上限 | `20` |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_SECONDS` | 登录失败限流 | `5` / `300` |
| `MEMORY_SESSION_TTL_SECONDS` | Redis 工作记忆 TTL | `1800` |
| `MEMORY_EVENT_RETENTION_DAYS` / `MEMORY_SUMMARY_RETENTION_DAYS` | 情景事件/摘要保留期 | `90` / `180` |
| `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` | MongoDB 根账号（compose 初始化） | `wenshu_admin` / 强随机 |
| `REDIS_PASSWORD` | Redis 口令（compose requirepass） | 强随机 |

> 已通过 `scripts/doctor.py` 实测：DeepSeek `deepseek-v4-flash` ✅、中转站 `gpt-5.5` ✅、
> `text-embedding-3-large` ✅、bge 重排 `BAAI/bge-reranker-v2-m3` ✅。
> 注意：该中转站**不提供** `bge-m3` embedding 与 `gpt-5.5-pro`（这两个名字无效）。

## 各模块 README

- [`backend/README.md`](backend/README.md)
- [`services/pi-agent/README.md`](services/pi-agent/README.md)
- [`web/README.md`](web/README.md)
- [`deploy/README.md`](deploy/README.md)
- [`docs/architecture.md`](docs/architecture.md) · [`docs/api.md`](docs/api.md) · [`docs/deployment.md`](docs/deployment.md) · [`docs/loop-engineering.md`](docs/loop-engineering.md)

## 技术栈

Python 3.11 · FastAPI · MongoDB(motor) · Redis · Next.js 15 · React 19 · TypeScript ·
[pi](https://github.com/earendil-works/pi)（pi-agent-core + pi-ai）· DeepSeek（对话）·
text-embedding-3-large / bge-reranker-v2-m3（经中转站）· Docker · Kubernetes · Helm

## 验证

```bash
cd backend && .venv/bin/pytest -q
cd web && npm run build
cd services/pi-agent && npm run build
```

真实文档评测集位于 `backend/evaluation/real_document_qa.json`，运行
`python -m scripts.evaluate_rag` 可得到 Recall@5、MRR、引用正确率和答案一致性。
部门 Agent 的 1→20 副本负载测试见 `loadtest/README.md`。

## 记忆与事实边界

系统采用“一个独立事实平面 + 五个记忆平面”：

- `documents/chunks` 是最高权威事实源，不属于模型记忆；
- Redis 会话工作记忆；
- MongoDB 情景事件与摘要；
- 可解释、可删除的用户语义记忆；
- 带官方来源和部门权限的组织知识记忆；
- Skills/Hooks/Rules/实验组成的程序性与学习记忆。

所有组织 FAQ 必须绑定 active 文档 chunk，归档或版本替换后自动失效。详见
[`backend/app/memory/README.md`](backend/app/memory/README.md)。
