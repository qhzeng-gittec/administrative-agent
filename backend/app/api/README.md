# 接入层（L1）

REST API 与健康探针，统一前缀 `/api/v1`，返回结构 `{code, message, data}`。

## 文件

- `router.py` —— 路由汇总（`/api/v1` 前缀）
- `schemas.py` —— 请求/响应 Pydantic 模型
- `deps.py` —— 鉴权依赖（`get_optional_user` / `require_user` / `require_admin`）
- `routes/auth.py` —— 登录 / 当前用户 / 用户列表
- `routes/chat.py` —— 对话（非流式 + SSE 流式 + 会话历史）
- `routes/documents.py` —— 文档上传/作业状态/列表/详情/状态（Redis Stream 异步入库）
- `routes/departments.py` —— 部门管理与冲突查询
- `routes/feedback.py` —— 显式反馈收集
- `routes/memory.py` —— 用户自有记忆查看/写入/删除，组织记忆发布/撤销
- `routes/admin.py` —— 仪表盘 / 系统洞察 / 部门子 Agent / 审核中心 / Loop 作业跟踪 / Skills / Glossary
- `routes/internal.py` —— 供部门 Agent 和 pi Runtime 白名单工具调用的共享令牌内部接口
- `routes/health.py` —— `/healthz`（存活）与 `/readyz`（就绪）

## 鉴权

登录后前端在请求头携带 `Authorization: Bearer <token>`；Token 包含 `iat/exp`。用户身份只从 Token
获取；会话、反馈和记忆接口校验所有权，组织记忆接口校验部门管理员范围。
全局 Loop 触发、作业历史与阶段切换仅系统管理员可用；部门管理员请求返回 403。

## 说明

路由通过 `request.app.state.container` 获取全局单例容器，无全局可变状态，便于测试替换。
