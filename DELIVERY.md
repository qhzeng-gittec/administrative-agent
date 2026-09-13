# 文枢源码交付包说明

本交付包包含完整源码、设计资料、样例部门文件、测试、评测集以及 Docker/Kubernetes/Helm 部署文件。

## 已包含

- `backend/`：FastAPI 控制平面、事实/记忆、RAG、Loop、测试和脚本
- `services/pi-agent/`：pi Agent Runtime 源码及 npm 锁文件
- `web/`：Next.js 前端源码及 npm 锁文件
- `deploy/`：Docker、Kubernetes、Helm、HPA 和监控配置
- `department_files/`：项目附带的样例部门 PDF/DOCX
- `design_files/`：技术方案、代码解读文档、参考 PDF 和架构图片
- `docs/`、`loadtest/`：说明文档和 k6 压测脚本
- `.env.example`、`services/pi-agent/.env.example`：配置模板，不含真实密钥
- `design_files/文枢-前端页面使用指南.md`：学生、部门管理员和超级管理员逐步操作说明

## 未包含（均可重新生成）

- Python 虚拟环境：`backend/.venv/`、`venv/`
- Node 依赖：`node_modules/`
- Next.js/TypeScript 构建结果：`.next/`、`dist/`、`build/`、`out/`、`*.tsbuildinfo`
- 测试和语言缓存：`__pycache__/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`
- 本机运行数据和缓存：`data/`、`volumes/`、`chroma_data/`、`.cache/`、日志
- 本机真实配置：`.env`、`.env.local`、私钥和证书
- macOS/IDE 文件：`.DS_Store`、`.idea/`、`.vscode/`
- 本项目以前生成的交付 ZIP 与校验文件：`wenshu-project-source-*.zip*`

## 接收方快速启动

### Docker

```bash
cp .env.example .env
# 编辑 .env，填写模型 API Key、数据库口令和内部服务令牌
docker compose up --build -d
docker compose exec backend python -m scripts.seed_data
docker compose exec backend python -m scripts.ingest_department_files --base /app/department_files
```

### 本地安装依赖

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../web
npm ci

cd ../services/pi-agent
npm ci
```

更多说明见根目录 `README.md`、`docs/` 及各模块 README。
