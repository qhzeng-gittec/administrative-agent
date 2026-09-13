"""Durable asynchronous message observation and threshold-triggered mining."""
from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from pathlib import Path

from app.enterprise.feedback import classify, task_context
from app.enterprise.assessment import execute
from app.enterprise.evolution import expire_trials, observe_episode, run_cycle, select_catalog
from app.enterprise.store import EnterpriseStore
from app.llm.openrouter import OpenRouterClient
from app.support.evolution import now

logger = logging.getLogger(__name__)


class EnterpriseService:
    def __init__(self, settings):
        self.settings = settings
        self.store = EnterpriseStore(Path(settings.support_data_dir) / "skills.sqlite")

    def add_message(self, user_id: str, text: str, task_id: str | None = None) -> dict:
        # Context is captured before this message's business execution, so later
        # results cannot be incorrectly used to interpret an earlier complaint.
        history = [t for t in self.store.records("task") if t["user_id"] == user_id and t.get("origin") == "workbench_task"]
        message = {"id": uuid.uuid4().hex, "user_id": user_id, "text": text, "created_at": now(),
                   "origin": "workbench_message",
                   "context": task_context(history), "snapshots": {t["id"]: t for t in history[:8]},
                   "status": "queued", "business_task_id": task_id}
        self.store.put("message", message)
        self.store.enqueue("classify", {"message_id": message["id"]}, job_id=f"classify-{message['id']}")
        return message

    async def process(self, job: dict) -> dict:
        payload = job["payload"]
        if job["type"] == "classify":
            row = self.store.get("message", payload["message_id"])
            if "classification" in row and "reported_attempt" in row:
                return row["classification"]
            model = OpenRouterClient(self.settings, self.settings.feedback_model)
            row["classification"] = await classify(model, row["text"], row["context"])
            row["reported_attempt"] = row.pop("snapshots").get(row["classification"]["task_id"])
            row.update(status="completed", model=model.model)
            self.store.put("message", row)
            return row["classification"]
        if job["type"] == "execute":
            task = self.store.get("task", payload["task_id"])
            llm = OpenRouterClient(self.settings, self.settings.enterprise_model)
            expire_trials(self.store)
            capabilities, assignment = select_catalog(self.store, task["input"]["domain"], task["user_id"])
            conversation = copy.deepcopy(task.get("conversation", [])) + [{"role": "user", "content": payload["text"]}]
            snapshot = {"id": task["id"], "input": copy.deepcopy(task["input"]), "conversation": conversation}
            # A continuation starts with the actual existing applications, not the original empty state.
            if task.get("run"):
                snapshot["input"]["environment"]["requests"] = copy.deepcopy(task["run"]["requests"])
            episode_id = payload.get("execution_id", job["id"])
            saved = self.store.get("episode", episode_id)
            if saved:
                current = task.get("run") or {}
                if current.get("executed_at", "") >= saved["run"]["executed_at"]:
                    if saved["assignment"]:
                        self.store.enqueue("observe", {"episode_id": episode_id}, job_id=f"observe-{episode_id}")
                    return {"task_id": task["id"], "status": current["status"], "recovered": True}
                task["run"] = saved["run"]
                conversation = copy.deepcopy(saved["snapshot"]["conversation"])
            else:
                task["run"] = await execute(llm, snapshot, capabilities)
                task["run"].update(executed_at=now(), episode_id=episode_id, evolution_assignment=assignment)
                self.store.put("episode", {"id": episode_id, "task_id": task["id"], "user_id": task["user_id"],
                    "origin": "workbench_execution", "snapshot": snapshot, "run": task["run"], "assignment": assignment,
                    "capability_ids": [c["id"] for c in capabilities], "created_at": now()})
            explanation = (task["run"].get("result") or {}).get("explanation", "本次未完成，请查看执行记录。")
            task["conversation"] = conversation + [{"role": "assistant", "content": explanation}]
            task["updated_at"] = now()
            self.store.put("task", task)
            if task["run"].get("evolution_assignment"):
                self.store.enqueue("observe", {"episode_id": episode_id}, job_id=f"observe-{episode_id}")
            return {"task_id": task["id"], "status": task["run"]["status"]}
        if job["type"] == "mine":
            llm = OpenRouterClient(self.settings, self.settings.enterprise_model)
            judge = OpenRouterClient(self.settings, self.settings.evolution_judge_model)
            expire_trials(self.store)
            result = await run_cycle(self.store, llm, judge, self.settings, retry_id=payload.get("cycle_id"))
            self.store.put("mining_run", {"id": job["id"], "created_at": now(), **result})
            return result
        if job["type"] == "observe":
            return await observe_episode(self.store, OpenRouterClient(self.settings, self.settings.enterprise_model),
                OpenRouterClient(self.settings, self.settings.evolution_judge_model), self.settings,
                self.store.get("episode", payload["episode_id"]))
        raise ValueError(f"未知企业作业类型：{job['type']}")

    async def worker(self, job_type: str):
        while True:
            job = self.store.claim(job_type)
            if job is None:
                await asyncio.sleep(0.5)
                continue
            async def heartbeat():
                while True:
                    await asyncio.sleep(30)
                    self.store.renew(job)
            pulse = asyncio.create_task(heartbeat())
            try:
                result = await self.process(job)
            except asyncio.CancelledError:
                job.update(status="failed", error="服务关闭中断作业，可明确重试", finished_at=now())
                self.store.put("job", job)
                raise
            except Exception as exc:
                logger.exception("企业后台作业失败 %s", job["id"])
                job.update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=now())
                self.store.put("job", job)
                if job["type"] == "classify":
                    message = self.store.get("message", job["payload"]["message_id"])
                    message.update(status="failed", error=job["error"])
                    self.store.put("message", message)
            else:
                job.update(status="completed", result=result, finished_at=now())
                self.store.put("job", job)
            finally:
                pulse.cancel()
                try:
                    await pulse
                except asyncio.CancelledError:
                    pass

    async def scheduler(self):
        while True:
            await asyncio.sleep(self.settings.mining_interval_seconds)
            expire_trials(self.store)
            if not self.settings.openrouter_api_key:
                continue
            if not any(j["type"] == "mine" and j["status"] in {"queued", "running"} for j in self.store.records("job")):
                # Stable time bucket prevents duplicate scheduler insertion.
                import time
                bucket = int(time.time()) // self.settings.mining_interval_seconds
                self.store.enqueue("mine", {"source": "scheduled"}, job_id=f"scheduled-mining-{bucket}")
