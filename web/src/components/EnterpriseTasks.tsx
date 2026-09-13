"use client";

import { useEffect, useState } from "react";
import { enterpriseApi as api, labels } from "@/lib/enterprise";
import styles from "./SupportDesk.module.css";

type Input = { domain: string; question: string; environment: Record<string, unknown> };
type Task = { id: string; input: Input; conversation?: { role: string; content: string }[]; run?: {
  status: string; result: { status: string; explanation: string; missing_information: string[] } | null;
  requests: { kind: string; department: string; status: string }[]; tool_trace: unknown[]; loaded_skills: string[];
  evolution_assignment?: { stage: string; cohort: string; cycle_id: string } | null;
} };
type Message = { id: string; text: string; classification?: { signal: string; task_id: string | null }; status: string };

export default function EnterpriseTasks() {
  const [examples, setExamples] = useState<{ id: string; input: Input }[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [domain, setDomain] = useState("onboarding");
  const [question, setQuestion] = useState("");
  const [environment, setEnvironment] = useState("");
  const [selected, setSelected] = useState("");
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    async function refresh() {
      try {
        const [t, m] = await Promise.all([api<Task[]>("/tasks"), api<Message[]>("/messages")]);
        if (alive) { setTasks(t); setMessages(m); }
      } catch (e) { if (alive) setError(String(e)); }
    }
    api<{ id: string; input: Input }[]>("/examples").then(rows => {
      if (alive) { setExamples(rows); setQuestion(rows[0].input.question); setEnvironment(JSON.stringify(rows[0].input.environment, null, 2)); }
    }).catch(e => { if (alive) setError(String(e)); });
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); setTasks(await api<Task[]>("/tasks")); setMessages(await api<Message[]>("/messages")); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const task = tasks.find(t => t.id === selected) || tasks[0];
  return <>{error && <div role="alert" className={styles.error}>{error}</div>}<div className={styles.columns}>
    <section className={styles.card}><h2>新建任务</h2><p>申请在本地保存，不实际开通权限或付款。示例仅提供可编辑业务资料，不会导入历史成绩。</p>
      <label>办理事项<select value={domain} onChange={e => { const row = examples.find(r => r.id === e.target.value); if (row) { setDomain(row.id); setQuestion(row.input.question); setEnvironment(JSON.stringify(row.input.environment, null, 2)); } }}>
        {examples.map(e => <option key={e.id} value={e.id}>{labels[e.id]}</option>)}</select></label>
      <label>你的需求<textarea value={question} onChange={e => setQuestion(e.target.value)} rows={3} /></label>
      <details><summary>编辑模拟员工、政策与材料</summary><textarea aria-label="模拟业务资料" value={environment} onChange={e => setEnvironment(e.target.value)} rows={16} /></details>
      <button className={styles.primary} disabled={busy || !question.trim()} onClick={() => act(async () => {
        const result = await api<{ task: Task }>("/tasks", { question, domain, environment: JSON.parse(environment) }); setSelected(result.task.id);
      })}>提交办理</button>
      <h3>任务记录</h3>{tasks.map(t => <p key={t.id}><button onClick={() => setSelected(t.id)}>{labels[t.input.domain]} · {t.input.question.slice(0, 28)}</button></p>)}
    </section>
    <section className={styles.card}><h2>办理进度与反馈</h2>{!task ? <p>提交一个任务后，这里会显示申请和处理记录。</p> : <>
      <p>{task.input.question}</p><span className={styles.pill}>{labels[task.run?.result?.status || task.run?.status || "queued"] || task.run?.status}</span>
      <p className={styles.answer}>{task.run?.result?.explanation || (task.run ? "本次未正常交付结果，请查看执行记录。" : "正在处理…")}</p>
      {task.run?.requests.map((r, i) => <p key={i}>{r.department} · {r.kind} · {labels[r.status] || r.status}</p>)}
      {task.run?.evolution_assignment && <p className={styles.muted}>{labels[task.run.evolution_assignment.stage]} · {task.run.evolution_assignment.cohort === "treatment" ? "候选组" : "原版本组"}。后台使用隔离副本进行对照，不重复修改此任务。</p>}
      <details><summary>执行依据与工具记录</summary><pre>{JSON.stringify(task.run?.tool_trace, null, 2)}</pre></details>
      <details><summary>任务对话</summary>{task.conversation?.map((m, i) => <p key={i}><b>{m.role === "user" ? "你" : "助手"}：</b>{m.content}</p>)}</details>
      <label>反馈或补充需求<textarea value={feedback} onChange={e => setFeedback(e.target.value)} rows={3} /></label>
      <div className={styles.actions}><button disabled={busy || !feedback.trim()} onClick={() => act(async () => { await api("/messages", { text: feedback }); setFeedback(""); })}>仅反馈</button>
        <button disabled={busy || !feedback.trim()} onClick={() => act(async () => { await api("/messages", { text: feedback, task_id: task.id }); setFeedback(""); })}>继续办理</button></div>
      {messages.filter(m => m.classification?.task_id === task.id).slice(0, 5).map(m => <p className={styles.muted} key={m.id}>{m.text} · {labels[m.classification!.signal]}</p>)}
    </>}</section>
  </div></>;
}
