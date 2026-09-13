.PHONY: help up down logs build doctor ingest seed backend-run backend-test pi-run pi-doctor web-dev

help:
	@echo "文枢 · 常用命令"
	@echo "  make up             docker compose 启动全栈"
	@echo "  make down           docker compose 停止"
	@echo "  make logs           跟踪日志"
	@echo "  make doctor         模型连通性自检（Python）"
	@echo "  make ingest         导入 department_files 示例文档"
	@echo "  make seed           种子数据（部门/术语/校历/规则）"
	@echo "  make backend-run    本地运行后端"
	@echo "  make backend-test   运行后端单元测试"
	@echo "  make pi-run         本地运行 pi 智能体服务"
	@echo "  make pi-doctor      pi 框架自检"
	@echo "  make web-dev        本地运行前端 dev server"

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

doctor:
	docker compose exec backend python -m scripts.doctor

ingest:
	docker compose exec backend python -m scripts.ingest_department_files --base /app/department_files

seed:
	docker compose exec backend python -m scripts.seed_data

backend-install:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

backend-run:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

backend-test:
	cd backend && . .venv/bin/activate && pytest

pi-install:
	cd services/pi-agent && npm install

pi-run:
	cd services/pi-agent && npm run dev

# pi doctor 依赖 devDependencies，容器内不可用，仅在本地 npm install 后运行
pi-doctor:
	cd services/pi-agent && npm run doctor

web-install:
	cd web && npm install

web-dev:
	cd web && BACKEND_URL=http://localhost:8000 npm run dev
