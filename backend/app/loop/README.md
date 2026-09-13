# Loop 进化层（L3）

反馈驱动的自优化循环：Execute → Observe → Reflect → Adapt → Deploy。

## 文件

- `loop_engine.py` —— 五阶段循环编排、bad case 归因（Reflect）、灰度部署（Deploy）
- `default_skills.py` —— 三条幂等、可真实执行的基线 Skill 与 v1 策略快照
- `skill_miner.py` —— trace embedding 聚类（DBSCAN）→ LLM 生成 Skill 草稿 → 沙箱回测
- `hook_engine.py` —— 事件响应钩子（触发评估 + 动作应用）
- `rule_engine.py` —— 硬约束规则（运行时注入 + 进化更新）
- `feedback_collector.py` —— 显式/隐式/自动反馈汇总
- `skill_executor.py` —— Skill workflow 执行、稳定 treatment/control 分桶、结果记录
- `strategy_evaluator.py` —— 基线/候选同题回放与策略质量比较

## 人在环中/环上/环外

由 `LOOP_PHASE` 控制：

- `human_in_loop` —— 自动产物全部人工审核
- `human_on_loop` —— 置信度 ≥ `HOOK_HIGH_CONFIDENCE` 自动生效
- `human_out_of_loop` —— 圈定范围全自动

## 人工审核 Loop（人在环中/环上/环外）

除 Skills/Hooks/Rules 的自动进化外，`../review/review_engine.py` 实现了以"人逐步退出"为核心的审核闭环：

1. 新文档入库后自动出题（LLM 依据文档条款生成测试题）。
2. 系统用自身检索 + 生成链路作答（自测）。
3. 生成"审核单"发给部门管理员逐题判定正确/错误（反馈与题库积累）。
4. 累计正确率超过 `REVIEW_ACCURACY_THRESHOLD` 且样本 ≥ `REVIEW_MIN_SAMPLES` 时，
   该部门进入 `human_out_of_loop`；未抽中的文档自动通过，抽检错误会退回 `human_on_loop`。

## 后台 Worker

`python -m scripts.async_worker` 通过 Redis Stream 消费异步入库、反馈唤醒和 Loop 作业。
手动 Loop 作业持久化 `queued/running/completed/failed`，运行中记录当前阶段，完成后返回反馈、根因、候选、
发布结果、策略资产前后变化和下一步建议；管理端通过 `/api/v1/admin/loop/jobs/{job_id}` 自动跟踪。
策略版本、实验与执行结果分别存入 `strategy_versions`、`experiments`、`strategy_executions`；
treatment 劣于 control 时自动回滚。

## 基线 Skill 与自动挖掘 Skill

后端启动和 `scripts.seed_data` 会幂等创建“极端天气安全响应”“校园事项步骤导航”“学术节点与截止日期核验”。
三者直接进入 `SkillExecutor`，会扩展 query、提升 top-k、注入输出模板或校历约束，并记录 treatment/control、
命中次数和成功率。Trace 达到聚类阈值后，Skill Miner 仍会生成新的候选 Skill，两类 Skill 共用同一治理链。

## 演示数据

`python -m scripts.seed_demo_data` 为各部门生成模拟文档、待审核单与 badcase，并把
badcase 反思出的 rubric 规则写入各部门初始 Skill（见 `docs/loop-engineering.md`）。
