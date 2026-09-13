# 部署与高并发方案

## 1. 本地开发（Docker，推荐）

```bash
cd program
cp .env.example .env     # 填写密钥
docker compose up --build
```

## 2. 生产部署（K8s）

`deploy/` 目录提供：

- `deploy/k8s/` —— 原生 YAML 清单（namespace / mongodb / redis / orchestrator / loop-engine / dept-agent / gateway / monitoring）
- `deploy/helm/wenshu/` —— Helm Chart，模板化部署新部门 Agent

```bash
helm install wenshu deploy/helm/wenshu -n wenshu --create-namespace \
  --set secrets.deepseekApiKey=... --set secrets.relayApiKey=...
```

### 部门 Agent 弹性伸缩

每个部门 Agent 是独立 Deployment + HPA，按 `wenshu_dept_agent_inflight` 自定义 Pods 指标伸缩：

- 冷门部门（国际交流处）：`minReplicas=1`
- 热门部门（教务处/学生处）：`minReplicas=2`、`maxReplicas=20`

```yaml
# 新增一个部门 Agent（模板化）
helm upgrade --install dept-agent-x deploy/helm/wenshu -n wenshu \
  --set department.id=dept_x --set department.name="XX处"
```

## 3. 高并发关键设计

| 设计点 | 实现 |
|---|---|
| 部门级隔离 | 独立 Deployment + HPA，Pod 反亲和 |
| 共享检索 | Mongo 共享向量 + 基于 active chunks 的无状态 BM25，避免 Pod 索引漂移 |
| 异步处理 | 入库/反馈唤醒/Loop 走 Redis Stream；上传文件由 backend/worker 共享 RWX PVC |
| 会话记忆 | Redis TTL 状态只保存摘要、实体和 chunk ID；长期事件进入 Mongo TTL 集合 |
| 限流降级 | Ingress 限流；部门服务失败时使用共享检索降级并标记 degraded departments |
| 连接池 | motor 异步 MongoDB + redis.asyncio 连接池 |
| 预热 | 高峰前扩容 + 预加载热点 FAQ |

## 4. 可观测性

当前清单包含 Prometheus、Grafana 和 Prometheus Adapter；Loki/OpenTelemetry 尚未在仓库清单中落地。

- 指标端点：`/metrics`（prometheus_client）
- 部门 HPA 指标：`wenshu_dept_agent_inflight`
- 问答指标：`wenshu_query_latency_seconds`、`wenshu_answer_adoption_total`、`wenshu_skill_trigger_total`
- pi 执行指标：`wenshu_pi_agent_execution_total{agent,status}`，可区分 success/fallback/error/disabled
