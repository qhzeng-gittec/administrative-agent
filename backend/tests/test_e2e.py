"""端到端冒烟测试：入库 → 检索 → 问答（全程离线，无真实 LLM / 外部服务）。"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ingest_and_answer(fresh_container, tmp_path):
    c = fresh_container
    await c.store.upsert_department(
        {
            "_id": "dept_jwc",
            "name": "教务处",
            "admin_users": [],
            "agent_config": {"model": "deepseek-v4-flash", "temperature": 0.1},
        }
    )

    # 写入并入库一篇文档
    doc_file = tmp_path / "选课办法.txt"
    doc_file.write_text("第一章 选课\n第一条 学生应在每学期第16至18周完成下学期选课。", encoding="utf-8")
    doc = await c.indexer.ingest(doc_file, dept_id="dept_jwc", uploaded_by="test")
    assert doc["chunk_count"] >= 1
    assert doc["vector_status"] == "ready"

    # 问答（LLM 未配置 → 各 Agent 自动回退，仍能产出带引用的答案）
    result = await c.orchestrator.answer("选课时间是什么时候？", user_id="u1", dept_ids=["dept_jwc"])
    assert result["answer"]
    assert result["session_id"]
    events = await c.episodic_memory.session_events(result["session_id"], "u1")
    assert [event["type"] for event in events] == ["user_message", "assistant_message"]
    summary = await c.episodic_memory.get_summary(result["session_id"], "u1")
    assert summary and summary["summary"]


@pytest.mark.asyncio
async def test_loop_cycle_offline(fresh_container):
    c = fresh_container
    await c.rule_engine.seed_defaults()
    await c.hook_engine.seed_defaults()
    # 提交一条点踩反馈，触发一次循环（离线可跑）
    await c.feedback_collector.collect_explicit("s1", "u1", "退课怎么办", "答", "down")
    report = await c.loop_engine.run_cycle()
    assert "observed" in report
