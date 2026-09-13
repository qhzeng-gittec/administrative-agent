# 部署（Docker / Kubernetes / Helm）

## 目录结构

```
deploy/
├── README.md
├── k8s/                     # 原生 K8s 清单
│   ├── namespace.yaml
│   ├── secrets.yaml.example
│   ├── mongodb.yaml
│   ├── redis.yaml
│   ├── backend.yaml         # Orchestrator + API（全局服务，HPA）
│   ├── loop-engine.yaml     # Loop Engine（独立后台 Worker）
│   ├── dept-agent.yaml      # 部门 Agent（模板化示例）
│   ├── ingress.yaml
│   ├── monitoring.yaml      # Prometheus / Grafana
│   ├── prometheus-adapter.yaml # 部门 Agent 自定义 HPA 指标
│   └── uploads-pvc.yaml     # backend/worker 共享上传文件
└── helm/
    └── wenshu/              # Helm Chart（模板化部署新部门 Agent）
        ├── Chart.yaml
        ├── values.yaml
        └── templates/
```

## 1. Docker（本地，推荐）

见项目根目录 `docker-compose.yml` 与根 `README.md`。

## 2. Kubernetes 原生部署

```bash
kubectl apply -f deploy/k8s/namespace.yaml
# 先创建 secret（复制样例并填真实密钥）
cp deploy/k8s/secrets.yaml.example deploy/k8s/secrets.yaml
kubectl apply -f deploy/k8s/secrets.yaml
kubectl apply -f deploy/k8s/mongodb.yaml
kubectl apply -f deploy/k8s/redis.yaml
kubectl apply -f deploy/k8s/uploads-pvc.yaml
kubectl apply -f deploy/k8s/backend.yaml
kubectl apply -f deploy/k8s/loop-engine.yaml
kubectl apply -f deploy/k8s/dept-agent.yaml
kubectl apply -f deploy/k8s/monitoring.yaml
kubectl apply -f deploy/k8s/prometheus-adapter.yaml
kubectl apply -f deploy/k8s/ingress.yaml
```

## 3. Helm 部署（推荐，部门 Agent 模板化）

```bash
helm install wenshu deploy/helm/wenshu -n wenshu --create-namespace \
  --set secrets.deepseekApiKey=sk-... \
  --set secrets.relayApiKey=sk-... \
  --set secrets.authSecret=<强随机值> \
  --set secrets.internalApiToken=<强随机值> \
  --set secrets.mongodbUri="mongodb://user:pass@mongodb:27017/wenshu?authSource=admin" \
  --set secrets.redisAddr="redis://:pass@redis:6379"
```

> 注意：`--set` 的键名必须与 `values.yaml` 的 `secrets.*` 结构一致（如 `secrets.mongodbUri`、`secrets.redisAddr`）；
> 键名写错会被 Helm 静默忽略，仍使用默认占位值。

### 新增部门 Agent

```bash
# 用同一 Chart 模板化部署新部门（每个部门独立 Deployment + HPA）
helm upgrade --install dept-agent-x deploy/helm/wenshu -n wenshu \
  --set department.id=dept_x --set department.name="XX处" \
  --set department.minReplicas=1 --set department.maxReplicas=10
```

## 4. 高并发设计（对应技术方案 7.3）

| 设计点 | K8s 实现 |
|---|---|
| 部门级隔离 | 每部门独立 Deployment + HPA，Pod 反亲和 |
| 弹性伸缩 | HPA 经 Prometheus Adapter 按部门 Agent 在途请求数扩缩，热门部门 maxReplicas=20 |
| 限流降级 | Ingress/Gateway 层限流；LLM 失败降级 FAQ/原文检索 |
| 可观测性 | Prometheus（`/metrics`）+ Grafana |

`uploads-pvc.yaml` 需要集群支持 `ReadWriteMany`。部署后确认自定义指标与 HPA：

```bash
kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1 | grep wenshu_dept_agent_inflight
kubectl get hpa -n wenshu
```

1→20 Pod 的负载验证命令和通过阈值见 `../loadtest/README.md`。

## 5. 镜像构建

```bash
# 后端
docker build -t school-doc-agent:v1.0 -f ../backend/Dockerfile ../backend
# 前端
docker build -t school-doc-web:v1.0 ../web
```

## 6. pi Agent Runtime + Next.js 前端

Python 负责控制平面，pi 负责统一概率性 Agent 执行。服务拓扑为：

| 服务 | 清单/构建 |
|---|---|
| Python 后端 | `backend.yaml` + `../backend/Dockerfile` |
| pi Agent Runtime（Node/TS） | `pi-agent.yaml` + `../services/pi-agent/Dockerfile` |
| Next.js 前端 | `../web/Dockerfile` |

本地全栈一键启动见根 `docker-compose.yml`（已包含 mongodb/redis/backend/pi-agent/web）。
生产默认部署 pi Runtime；服务不可用时 Python 自动降级到本地 Agent。所有执行接口要求内部 Token。
