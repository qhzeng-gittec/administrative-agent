# Loop Engineering 自进化机制

## 1. Loop vs Automation

- **Automation**：定时执行固定流程，输入/逻辑/输出确定（如"每天凌晨重新索引新文档"）。
- **Loop**：每轮结果作为反馈输入，**改变下一轮行为**（如"点踩 → 分析归因 → 调整检索策略 → 下次变好"）。

## 2. 五阶段循环

```
Execute ──► Observe ──► Reflect ──► Adapt ──► Deploy ──┐
   ▲                                                   │
   └───────────────────────────────────────────────────┘
```

| 阶段 | 实现位置 | 职责 |
|---|---|---|
| Execute | `loop_engine.py` | 按当前 Skills/Hooks/Rules 回答，记录完整 trace |
| Observe | `feedback_collector.py` | 收集显式（点赞/点踩/纠错）、隐式（追问/复制）、自动（Verifier 自检）反馈 |
| Reflect | `loop_engine.py` + pi Runtime | pi 生成根因与候选建议，Python 校验、回放和发布 |
| Adapt | `skill_miner.py` / `hook_engine.py` / `rule_engine.py` | 生成 Skill/Hook/Rule 更新，进入审核或自动生效 |
| Deploy | `loop_engine.py` | 灰度发布，回测验证后全量，回到 Execute |

`skill_executor.py` 稳定分配 treatment/control，`strategy_evaluator.py` 对相同历史问题执行基线与候选回放；
执行、版本和实验写入 `strategy_executions`、`strategy_versions`、`experiments`，劣化策略自动回滚。

## 3. 可修改范围（Mutable Scope）

| 对象 | 可自动修改 | 需人工审核 | 不可自动修改 |
|---|---|---|---|
| Skills | 触发条件、参数模板 | Skill 本体（Prompt 逻辑） | — |
| Hooks | 触发阈值、路由规则 | Hook 动作定义 | — |
| Rules | 权重、优先级、top-k | 规则内容 | — |

## 4. 成功指标（Success Metric）

- 回答质量：采纳率（👍/👎）、答案引用条款数（≥1）、Verifier 评分（0-1）
- 检索质量：Hit Rate@5、MRR
- 效率目标：首 token < 2s、P99 < 8s、单轮成本 < ¥0.02；当前 SSE 仍为完整回答后的分片输出，首 token 目标尚未达成
- 覆盖：可回答率 ≥ 90%

## 5. 人逐步退出循环

- **Phase 1 人在环中**：所有自动产物必须人工审核后生效。
- **Phase 2 人在环上**：置信度 > `HOOK_HIGH_CONFIDENCE` 自动生效，否则推审核队列。
- **Phase 3 人在环外**：圈定范围内全自动运行。

通过环境变量 `LOOP_PHASE` 控制：`human_in_loop | human_on_loop | human_out_of_loop`。

## 6. Skills / Hooks / Rules

- **Skills**：程序化知识"怎么做"。如 `deadline_query`（截止时间查询 + 校历工具 + 倒计时模板）。
- **Hooks**：事件响应"何时触发什么"。如 `cross_dept_hook`（选课+缴费 → 同时检索教务处+财务处）。
- **Rules**：硬约束"必须遵守"。如 `cite_source_rule`（必须带引用）、`no_guess_rule`（无依据则明说）。

### 可执行基线 Skill

`backend/app/loop/default_skills.py` 在启动时幂等提供三条真实 workflow：极端天气安全响应、校园事项步骤导航、
学术节点与截止日期核验。它们用于在高频 Trace 尚未达到自动挖掘阈值前展示完整执行链，且会真实改变 query、top-k、
输出模板或校历约束，记录版本、分桶、命中和成功率。自动挖掘 Skill 与它们使用同一个 `SkillExecutor`。

## 7. 人工审核 Loop（部门渐进退出）

`backend/app/review/review_engine.py` 把"人逐步退出"落地为可观测流程：

1. **自动出题**：新文档入库后，LLM 依据文档条款生成测试题（题库 `test_questions` 积累）。
2. **系统作答**：用自身检索 + 生成链路作答（带引用）。
3. **审核单**：题目 + 答案发给部门管理员（`review_orders`），逐题判定正确/错误。
4. **渐进退出**：累计正确率 ≥ `REVIEW_ACCURACY_THRESHOLD`（默认 0.8）且样本 ≥ `REVIEW_MIN_SAMPLES`（默认 5）时，
   该部门推进到 `human_out_of_loop`；后续仍按 `REVIEW_SAMPLE_RATE` 抽检，错误时自动退回 `human_on_loop`。

部门 `loop_phase` 三态：`human_in_loop`（100% 人工）→ `human_on_loop`（正确率达标、样本积累中）→ `human_out_of_loop`（自动）。

> 提示：`POST /admin/loop/run`（前端「手动触发一次 Loop」）的 Observe 阶段只读「未消费反馈」
> （`feedback` 集合中 `consumed=False` 的记录），循环结束会把它们全部置为已消费。
> 因此第二次点击会返回 `observed: 0`（反馈已处理完，属正常行为）；重复演示可重跑
> `python -m scripts.seed_demo_data` 恢复 badcase。
>
> 当前管理端会自动轮询 `/api/v1/admin/loop/jobs/{job_id}`，显示 `queued/running/completed/failed`、阶段进度、
> 信号、根因、候选、发布结果和前后差异；入队返回的 `job_id` 不再被当作最终报告。

## 8. 演示扩展 Skill 与 rubric 规则

运行可选的 `seed_demo_data` 后，每个演示部门还会增加一个 Skill（`skill_<dept>_seed`），包含：

- `unique_rules`：该部门独特规则（如教务处"退课截止第8周、退费按剩余周比例"），种子静态写入。
- `rubric_rules`：由 badcase 反思总结出的评分卡规则，**初始为空，运行 Loop 后由 Reflect→Adapt 沉淀**。
  执行「手动触发一次 Loop」时，`_deterministic_suggestions` 会从 badcase 的 `detail.rule` 抽取规则建议，
  并把每条规则追加到对应部门 Skill 的 `rubric_rules`（同时生成待审核的全局/部门 Rule）。

Skill 指标：`trigger_count` / `success_count` / `success_rate` / `last_triggered` 在每次命中该 Skill
的问答后实时更新（`orchestrator._record_skill_usage`）。

演示数据生成：`python -m scripts.seed_demo_data`（合并部门 + 模拟文档 + 待审核单 + badcase + 初始 Skill）。

Trace 属于观察数据；只有经过审核或实验验证的 Skills/Hooks/Rules 才进入程序性记忆。Loop 不得把
敏感用户文本直接提升为长期用户记忆。

pi 只参与 Reflect 的概率性分析，不直接激活策略；Mutable Scope、实验、审核和回滚仍由 Python 控制。
