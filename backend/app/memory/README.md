# 记忆架构：一个事实平面 + 五个记忆平面

官方制度文档不属于模型记忆，而是最高权威的独立事实平面。所有长期记忆必须有明确范围、来源、时效和删除策略。

| 平面 | 实现 | 存储 | 作用 |
|---|---|---|---|
| 事实平面 | `facts.py` | `documents/chunks/doc_relations/glossary` | 只返回 active 官方原文，回答引用的唯一事实依据 |
| 会话工作记忆 | `working.py` | Redis | 最近消息、摘要、实体、部门和 chunk ID，TTL 默认 30 分钟 |
| 情景记忆 | `episodic.py` | `conversation_events/conversation_summaries` | append-only 消息事件、会话恢复与滚动摘要 |
| 用户语义记忆 | `user_semantic.py` | `user_memory_items/memory_candidates` | 用户明确偏好和已验证资料；敏感项拒绝，推断项待审核 |
| 组织知识记忆 | `organization.py` | `org_memory_items/memory_topics` | FAQ、流程提示、校历和协调结果；FAQ 必须绑定官方来源 |
| 程序性/学习记忆 | `learning.py` + `app/loop/` | Skills/Hooks/Rules/实验集合 | 改变下一轮执行方式，支持灰度、回放和回滚 |

## 统一上下文

`context_builder.py` 是唯一读取入口。它按用户、部门、角色、时效和权威等级选择性召回，并执行字符预算。
代码入口类为 `FactPlane` 与 `MemoryContextBuilder`。
上下文进入 Intent、Query Rewriter 和 Answer；记忆只能帮助理解指代、用户偏好和检索方向，不能作为制度引用。
组织记忆命中后，系统重新读取其 `doc_id/chunk_id/document_version` 对应的 active 原文，再交给 Answer 与 Verifier。

## 权威顺序

```text
active 官方文档 > 管理员审核组织记忆 > 用户明确声明 > 会话摘要 > 系统推断
```

系统推断不会直接进入回答。`source_type=inferred` 且未获同意时只写入 `memory_candidates`。

## 生命周期与隐私

- 工作记忆：Redis TTL 30 分钟，只保存 chunk ID，不保存完整检索正文。
- 会话事件：默认 90 天；摘要和用户低敏记忆默认 180 天。
- `mongodb.py` 为上述集合创建 TTL 索引；`retention.py` 为内存模式及后台 Worker 提供显式清理。
- 禁止长期记忆身份证、密码、心理测评结果、健康、处分和财务明细。
- 用户可通过 `/api/v1/memory/me` 查看、明确写入和删除自己的记忆。
- 部门管理员只能管理本部门组织记忆；文档归档或 supersede 会把派生组织记忆标记为 stale。
- `memory_usage` 记录实际被注入回答的记忆与 trace；`memory_audit` 记录写入和删除。

## 旧数据迁移

```bash
python -m scripts.migrate_memory
```

迁移脚本会把旧用户偏好转为细粒度记忆，移除长期画像中的原始问题/反馈文本；无来源旧 FAQ 只进入待审核候选，不会直接生效。
