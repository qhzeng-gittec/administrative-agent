# 文枢后端（Python + FastAPI）

实现技术方案中的 L1 接入层（API）、L2 Harness 协同层、L3 Loop 进化层、L4 数据与检索层。

## 目录结构

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口 + lifespan + /metrics + 种子账号/字段回填
│   ├── config.py            # 环境配置（DeepSeek / 中转站 / 存储 / Loop / 鉴权 / 审核）
│   ├── deps.py              # 依赖装配（构建全局单例容器，含 auth / review_engine）
│   ├── auth.py              # 鉴权：用户账号 + HMAC Token + 角色（student/admin）
│   ├── api/                 # L1 接入层
│   │   ├── router.py        # 路由汇总
│   │   ├── schemas.py       # 请求/响应模型
│   │   ├── deps.py          # 鉴权依赖（get_optional_user / require_user / require_admin）
│   │   └── routes/          # auth / chat / documents / departments / feedback / admin / internal / health
│   ├── harness/             # L2 多智能体协同
│   │   ├── orchestrator.py  # 总调度（DAG 编排）
│   │   ├── base.py          # Intent / Answer / Citation / Verification 类型
│   │   └── agents/          # Intent / DeptRouter / Rewriter / Retrieval / Answer / Verifier / Feedback
│   ├── loop/                # L3 Loop 进化层
│   │   ├── loop_engine.py   # Execute→Observe→Reflect→Adapt→Deploy
│   │   ├── skill_miner.py   # DBSCAN 聚类 + Skill 草稿 + 沙箱回测
│   │   ├── default_skills.py # 3 条幂等可执行基线 Skill
│   │   ├── skill_executor.py # workflow、灰度分桶和 outcome
│   │   ├── hook_engine.py   # 事件响应钩子
│   │   ├── rule_engine.py   # 硬约束规则
│   │   └── feedback_collector.py
│   ├── review/              # 人工审核 Loop（人在环中/环上/环外）
│   │   └── review_engine.py # 自动出题 → 系统作答 → 审核单 → 累计正确率 → 渐进退出
│   ├── memory/              # 事实平面 + 会话/情景/用户/组织/学习五个记忆平面
│   ├── retrieval/           # L4 BM25 + 向量 + 混合检索 + 重排
│   ├── pipeline/            # 文档解析 / 清洗 / 切片 / 元数据 / 索引 / 冲突检测
│   ├── llm/                 # DeepSeek + 中转站客户端 + Embedding
│   ├── storage/             # MongoDB / Redis / 统一存储(含内存回退)
│   └── utils/               # 日志 / Prometheus 指标
├── scripts/                 # 种子数据 / 演示数据 / 导入部门文档 / 依赖等待
├── tests/                   # 单元 + 端到端冒烟测试
├── requirements.txt
├── pyproject.toml
└── Dockerfile
```

## 环境要求

- Python 3.9+（推荐 3.11）
- MongoDB 7.0（可选，`STORAGE_MODE=memory` 时无需）
- Redis 7（可选，memory 模式回退内存）

## 安装

### 方式一：Docker（推荐）

```bash
# 在项目根目录 program/ 下
docker compose up --build
```

### 方式二：本地

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 配置密钥
cp ../.env.example ../.env   # 编辑填入 DEEPSEEK_API_KEY / RELAY_API_KEY

# 内存模式启动（无需 MongoDB/Redis，快速体验）
export STORAGE_MODE=memory
export EMBEDDING_PROVIDER=hash   # 无网络时用确定性向量
uvicorn app.main:app --reload --port 8000
```

### 可选依赖

- 向量检索：`pip install chromadb`（或设置 `VECTOR_BACKEND=chroma`）
- 本地向量：`pip install sentence-transformers`（`EMBEDDING_PROVIDER=local`）
- OCR 扫描件：`pip install paddleocr paddlepaddle`（体积大，按需）

## 环境变量（关键）

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 主力对话模型（默认 `deepseek-v4-flash`） |
| `RELAY_API_KEY` / `RELAY_BASE_URL` | 中转站（OpenAI 兼容），用于 bge 等非 DeepSeek 模型 |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | `relay`(`text-embedding-3-large`) / `local` / `hash` |
| `STORAGE_MODE` | `mongo` / `memory` |
| `LOOP_PHASE` | `human_in_loop` / `human_on_loop` / `human_out_of_loop` |
| `AUTH_SECRET` | Token 签名密钥（生产务必修改） |
| `REVIEW_QUESTION_COUNT` / `REVIEW_ACCURACY_THRESHOLD` / `REVIEW_MIN_SAMPLES` | 人工审核 Loop：出题数 / 退出阈值 / 最小样本 |

## 运行与验证

```bash
# 种子数据（部门/术语/校历/默认规则/3 条基线 Skill）
python -m scripts.seed_data

# 演示数据（合并部门 + 每部门模拟文档/待审核单/badcase/初始 Skill）
python -m scripts.seed_demo_data

# 导入 department_files 示例文档
python -m scripts.ingest_department_files --base ../department_files

# 测试
pytest
```

## 关键设计说明

1. **Harness 轻量编排**：Agent 走固定 DAG（Intent→Rewrite→Retrieve→Answer→Verify），
   不依赖 LangChain/AutoGen，输入输出结构化、可调试。
2. **混合检索**：BM25（jieba）+ 向量（`text-embedding-3-large`/local/hash）RRF 融合 + 重排。
3. **Loop 自进化**：反馈队列 → Reflect 归因 → Skill/Hook/Rule 更新 → 灰度部署，
   按 `LOOP_PHASE` 控制人在环中/环上/环外。
4. **统一存储抽象**：`DataStore` 接口 + Mongo/Memory 双实现，离线可跑、可测。
5. **全链路回退**：LLM 未配置或失败时各 Agent 自动降级（关键词意图 / 术语扩展 / 原文拼接 / 启发式校验），系统不崩。
6. **鉴权与角色**：登录签发带 `iat/exp` 的 HMAC Token；身份只从 Token 获取，资源操作校验用户/部门所有权。
7. **人工审核 Loop**：新文档入库自动出题 → 系统作答 → 发部门管理员审核单 → 累计题库/反馈，
   部门正确率超阈值且样本达标后进入 `human_out_of_loop`，并保留稳定抽检和错误回退。
8. **可信记忆**：官方文档保持独立事实平面；统一 `MemoryContextBuilder` 选择性注入五类记忆，
   组织 FAQ 必须回查 active 原始 chunk，用户可查看和删除自己的低敏长期记忆。
9. **pi Agent Runtime**：Python 控制平面通过统一 `/v1/agent/run` 协议执行 Intent、Rewrite、Answer、
   Verify、Reflect；pi 故障时自动回退原有 Python Agent，不影响事实与权限治理。
10. **可观测 Loop 作业**：手动 Loop 经 Redis Stream 异步执行，作业持续记录阶段进度并返回结构化报告；
    前端自动轮询、展示反馈信号、根因、候选、发布结果和策略资产前后差异。

## API 文档

启动后访问 http://localhost:8000/docs 。接口清单见 [../docs/api.md](../docs/api.md)。
