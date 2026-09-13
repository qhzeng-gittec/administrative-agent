# Harness 协同层（L2）

多智能体固定 DAG 编排（不依赖 LangChain/AutoGen）。

## 协作流程

```
Python Orchestrator → MemoryContext → pi Intent → pi Rewrite → Python Retrieval/Fact Plane → pi Answer → pi Verifier → Feedback
```

## 文件

- `orchestrator.py` —— 总调度：构建记忆上下文、加载程序性记忆、检索官方事实、生成、校验和 trace 落库
- `base.py` —— `Intent` / `Answer` / `Citation` / `VerificationResult` 类型
- `agents/intent_agent.py` —— 意图/部门/身份/跨部门判断（LLM + 关键词回退）
- `agents/query_rewriter.py` —— 补全/术语标准化/多 query（LLM + glossary 回退）
- `agents/retrieval_agent.py` —— 混合检索执行（多 query × 多部门）
- `agents/answer_agent.py` —— 带引用答案生成（LLM + 原文拼接回退）
- `agents/verifier_agent.py` —— 校验（LLM + 启发式回退）
- `agents/feedback_agent.py` —— 反馈收集写入队列

## 设计要点

- 每个 Agent 输入输出结构化，LLM 失败自动降级，系统不因模型不可用而崩溃。
- 跨部门协同由 Intent 判断 + Hook 扩展部门范围，Retrieval 并行检索多部门。
- 用户/会话/组织记忆经过 `MemoryContextBuilder` 选择性注入；只有事实平面 chunk 可以作为引用。
- 无来源 FAQ 不允许直答；组织记忆命中后必须回查 active 文档版本并经过 Verifier。
- Intent/Rewrite/Answer/Verifier 统一交给 pi Runtime 执行；Python 原实现作为故障降级。
- Python 显式传入白名单 `allowed_tools`，pi 不能扩大工具权限或绕过事实检索。
- `SkillExecutor` 在 Retrieval 前执行命中 workflow，可扩展 query/top-k、加入输出模板或校历约束；
  `default_skills.py` 的基线 Skill 与 Loop 自动挖掘 Skill 走同一条执行路径。
