"use client";

import { useCallback, useEffect, useState } from "react";
import { getToken } from "@/lib/api";
import styles from "./SupportDesk.module.css";

type Input = { question: string; domain: string; environment: Record<string, unknown> };
type Task = { id: string; input: Input; conversation?: { role: string; content: string }[]; run?: { status: string; result: { status: string; explanation: string; missing_information: string[] } | null; requests: { id: string; kind: string; department: string; status: string }[]; tool_trace: unknown[]; tool_calls: number; loaded_skills: string[]; experiment_assignments?: { skill_id: string; cohort: string; simulation: boolean }[] } };
type Message = { id: string; text: string; status: string; error?: string; classification?: { signal: string; task_id: string | null; evidence: string } };
type Job = { id: string; type: string; status: string; error?: string };
type Cluster = { id: string; domain: string; tasks: number; signal_tasks: number; suspected_error_rate: number; reason: string; decision?: string };
type Batch = { id: string; status: string; group: Cluster; error?: string; skill_id?: string; reflection?: { decision: string; reason: string; findings: { task_id: string; evidence: Record<string, unknown> }[]; candidate?: { name: string; instructions: string } }; evaluation?: { baseline_correct: number; candidate_correct: number; samples: number; reasons: string[] } };
type Mining = { config: { window: number; min_cluster: number; min_signals: number; interval_seconds: number; feedback_model: string; review_model: string }; runs: { id: string; tasks: number; reflection_calls: number; clusters: Cluster[] }[]; batches: Batch[]; capability_candidates?: { id: string; status: string; decision: { decision: string; reason: string; tool: unknown; skill: unknown } }[]; jobs: Job[] };
type Experiment = { id: string; feedback_test: { cases: number; signal_correct: number; association_correct: number }; frozen_test: { cases: number; baseline_correct: number; candidate_correct: number }; batches: Batch[]; selected_skills: string[]; limitations: string[]; release_acceptance?: { passed: boolean; regressions: string[] } };
type CapabilityExperiment = { id: string; progress?: { stage: string; completed: number; total?: number }; learning_summary?: { cases: number; correct: number; suspected_signals: number }; report?: { accepted_simulation: boolean; frozen_test: { cases: number; baseline_correct: number; candidate_correct: number; regressions: string[]; tool_used_cases: number }; tool_shadow?: { cases: number; baseline_correct: number; candidate_correct: number; tool_used: number }; prompt_only_control?: { cases: number; correct: number }; proposals: { id: string; status: string; decision: { decision: string; reason: string; tool: unknown; skill: unknown } }[] } };
const names: Record<string, string> = { onboarding: "入职办理", access: "权限申请", expense: "报销检查", possible_error: "疑似执行错误", preference: "偏好不一致", none: "无相关反馈", queued: "排队中", running: "执行中", completed: "已完成", failed: "失败", waiting_approval: "等待审批", needs_information: "需要补充材料", blocked: "办理受阻", no_change: "无需修改", maintenance: "维护建议", validated: "验证通过", rejected: "候选被拒绝", skill: "生成候选", evaluating: "对照评测中", reflecting: "批量复盘中" };
Object.assign(names, { submitted: "已提交", mailbox: "邮箱申请", device: "设备申请", repository: "仓库权限申请", contractor_review: "外包人员审核", standard_access: "资源权限申请", production_review: "生产权限审核", expense_review: "报销审核" });

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/enterprise${path}`, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  return value.data as T;
}

export default function EnterpriseDesk({ miningMode, admin }: { miningMode: boolean; admin: boolean }) {
  const [examples, setExamples] = useState<{ id: string; input: Input }[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [mining, setMining] = useState<Mining | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [capabilityExperiments, setCapabilityExperiments] = useState<CapabilityExperiment[]>([]);
  const [selected, setSelected] = useState("");
  const [exampleId, setExampleId] = useState("");
  const [question, setQuestion] = useState("");
  const [environment, setEnvironment] = useState("");
  const [feedback, setFeedback] = useState("");
  const [outcomeEvidence, setOutcomeEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    if (miningMode) { const [m, e, c] = await Promise.all([api<Mining>("/mining"), api<Experiment[]>("/experiments"), api<CapabilityExperiment[]>("/capability-experiments")]); setMining(m); setExperiments(e); setCapabilityExperiments(c); }
    else {
      const [t, m, j] = await Promise.all([api<Task[]>("/tasks"), api<Message[]>("/messages"), api<Job[]>("/jobs")]);
      setTasks(t); setMessages(m); setJobs(j);
    }
  }, [miningMode]);
  useEffect(() => {
    let alive = true;
    api<{ id: string; input: Input }[]>("/examples").then(value => { if (alive) setExamples(value); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    refresh().catch(e => setError(e.message));
    const interval = setInterval(() => refresh().catch(e => setError(e.message)), 5000);
    return () => clearInterval(interval);
  }, [refresh]);
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); await refresh(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const task = tasks.find(t => t.id === selected) || tasks[0];
  const pending = jobs.some(j => j.type === "execute" && ["queued", "running"].includes(j.status));
  return <>
    {error && <div role="alert" className={styles.error}>{error}</div>}
    {miningMode ? <>
      <section className={styles.card}><h2>从重复问题中发现可改进的流程</h2><p>小模型只标记反馈。任务簇达到门槛后，后台才复盘共同原因；零星信号继续积累。</p>
        {mining && <><div className={styles.stats}><div><b>{mining.config.window}</b><small>历史任务窗口</small></div><div><b>{mining.config.min_cluster}</b><small>最小簇规模</small></div><div><b>{mining.config.min_signals}</b><small>最少疑似错误任务</small></div></div>
          <p className={styles.muted}>自动检查间隔 {mining.config.interval_seconds} 秒 · 反馈识别 {mining.config.feedback_model} · 批量复盘 {mining.config.review_model}</p></>}
        <button className={styles.primary} disabled={busy || mining?.jobs.some(j => ["queued", "running"].includes(j.status))} onClick={() => act(async () => { await api("/mining", {}); })}>检查历史任务并挖掘</button>
        <button disabled={busy} onClick={() => act(async () => { await api("/demo-history", {}); })}>加载 72 条合成历史并识别反馈</button>
        <p className={styles.muted}>演示历史中的错误是预设案例，分类与后续复盘调用真实模型。重复加载不会重复计数。</p>
        <p className={styles.muted}>疑似错误反馈率不等于真实错误率。无共同问题、知识缺失、工具故障会分别留下结论，不强行生成 Skill。</p>
      </section>
      {mining?.jobs.slice(0, 3).map(j => <section className={styles.card} key={j.id}><b>{names[j.status] || j.status}</b>{j.error && <><p>{j.error}</p><button disabled={busy} onClick={() => act(async () => { await api(`/jobs/${j.id}/retry`, {}); })}>重试失败作业</button></>}</section>)}
      {(mining?.runs[0]?.clusters || []).map(c => <section className={styles.card} key={c.id}><h3>{names[c.domain]} · {c.tasks} 个任务</h3><p>{c.signal_tasks} 个任务有疑似错误反馈 · {(c.suspected_error_rate * 100).toFixed(1)}%</p><p>{c.reason === "insufficient_tasks" ? "簇规模不足，继续积累" : c.reason === "insufficient_signals" ? "疑似错误信号不足，不复盘" : "达到复盘门槛"} · {names[c.decision || ""] || c.decision}</p></section>)}
      {!mining?.batches.length && !mining?.capability_candidates?.length && <section className={styles.card}><h3>暂未触发批量复盘</h3><p>任务积累和反馈识别会在后台进行，达到门槛后在这里展示复盘证据、候选及对照结果。</p></section>}
      {mining?.capability_candidates?.map(c => <section className={styles.card} key={c.id}><h3>能力复盘 · {c.decision.decision}</h3><p>{c.decision.reason}</p><p>状态：{c.status}。候选需要隔离评测，未自动启用。</p><details><summary>候选工具或策略</summary><pre>{JSON.stringify(c.decision.tool || c.decision.skill, null, 2)}</pre></details></section>)}
      {mining?.batches.map(b => <section className={styles.card} key={b.id}><span className={styles.pill}>{names[b.status] || b.status}</span><h2>{names[b.group.domain]} · {b.group.tasks} 个任务</h2><p>{b.reflection?.reason || b.error || "正在读取任务记录…"}</p>
        {b.reflection?.candidate && <details><summary>{b.reflection.candidate.name}</summary><pre>{b.reflection.candidate.instructions}</pre></details>}
        {b.evaluation && <p>独立验证：{b.evaluation.baseline_correct}/{b.evaluation.samples} → {b.evaluation.candidate_correct}/{b.evaluation.samples}。{b.evaluation.reasons.join(" · ")}</p>}
        <details><summary>复盘依据（模型判断，可追溯）</summary><pre>{JSON.stringify(b.reflection?.findings, null, 2)}</pre></details>
      </section>)}
      {experiments.slice(0, 1).map(e => <section className={styles.card} key={e.id}><h2>真实模型 · 隔离实验</h2><p>实验 {e.id}，候选保存在隔离实验库，不自动发布到当前工作台。</p><div className={styles.stats}><div><b>{e.feedback_test.signal_correct}/{e.feedback_test.cases}</b><small>反馈标签正确</small></div><div><b>{e.frozen_test.baseline_correct} → {e.frozen_test.candidate_correct}</b><small>冻结任务通过数 / {e.frozen_test.cases}</small></div><div><b>{e.selected_skills.length}</b><small>验收通过的策略</small></div></div>
        {e.release_acceptance && <p className={e.release_acceptance.passed ? styles.notice : styles.error}>{e.release_acceptance.passed ? "冻结验收通过，等待后续灰度。" : `候选组合未通过该次实验的验收门槛，继续使用基线；其中 ${e.release_acceptance.regressions.length} 条退步，上方分数是候选尝试结果。`}</p>}
        {e.batches.map(b => <details key={b.id}><summary>{names[b.group.domain]} · {names[b.status] || b.status}</summary><p>{b.reflection?.reason}</p>{b.evaluation && <p>{b.evaluation.baseline_correct}/{b.evaluation.samples} → {b.evaluation.candidate_correct}/{b.evaluation.samples} · {b.evaluation.reasons.join(" · ")}</p>}</details>)}
        <details><summary>评测范围与限制</summary>{e.limitations.map(t => <p key={t}>{t}</p>)}</details></section>)}
      {capabilityExperiments.slice(0, 1).map(e => <section className={styles.card} key={e.id}>
        <h2>缺陷注入 · 工具构建实验</h2><p>{e.id} · 环境与用户反馈为模拟，历史轨迹来自业务模型实际执行。</p>
        {e.learning_summary ? <p>历史采集 {e.learning_summary.cases} 条，通过 {e.learning_summary.correct} 条，疑似错误反馈 {e.learning_summary.suspected_signals} 条。</p> : <p>正在采集执行轨迹：{e.progress?.completed || 0}/72</p>}
        {e.report ? <><div className={styles.stats}><div><b>{e.report.frozen_test.baseline_correct} → {e.report.frozen_test.candidate_correct}</b><small>冻结通过数 / {e.report.frozen_test.cases}</small></div><div><b>{e.report.frozen_test.tool_used_cases}</b><small>发布验收中的工具调用任务</small></div></div>
          <p className={e.report.accepted_simulation ? styles.notice : styles.error}>{e.report.accepted_simulation ? "通过隔离实验验收，未发布到生产。" : `未通过该次实验的采纳门槛；冻结对照中有 ${e.report.frozen_test.regressions.length} 条退步，具体原因见候选评测记录。`}</p>
          {e.report.tool_shadow && <><h3>报销工具独立影子对照 · 未采纳</h3><p>基线 {e.report.tool_shadow.baseline_correct}/{e.report.tool_shadow.cases} → 提供工具 {e.report.tool_shadow.candidate_correct}/{e.report.tool_shadow.cases}；实际调用工具 {e.report.tool_shadow.tool_used} 条。{e.report.prompt_only_control && `仅优化提示词：${e.report.prompt_only_control.correct}/${e.report.prompt_only_control.cases}。`}</p><p>工具验证时出现退步，额外隔离对照不会恢复发布资格。</p></>}
          {e.report.proposals.map(p => <details key={p.id}><summary>{p.decision.decision} · {p.status}</summary><p>{p.decision.reason}</p><pre>{JSON.stringify(p.decision.tool || p.decision.skill, null, 2)}</pre></details>)}</> : <p>当前阶段：{e.progress?.stage || "等待实验开始"} · 已完成 {e.progress?.completed || 0}/{e.progress?.total || 72}。完成后展示候选和逐阶段验收结果。</p>}
      </section>)}
    </> : <div className={styles.columns}>
      <section className={styles.card}><h2>企业任务</h2><p className={styles.muted}>本地模拟工单环境。覆盖 IT、人事行政、财务协作；申请不会真正开通权限或付款。</p>
        <label>加载案例<select value={exampleId} onChange={e => { setExampleId(e.target.value); const x = examples.find(x => x.id === e.target.value); if (x) { setQuestion(x.input.question); setEnvironment(JSON.stringify(x.input.environment, null, 2)); } }}><option value="">选择一个开发集案例</option>{examples.map(e => <option value={e.id} key={e.id}>{names[e.input.domain]} · {e.input.question}</option>)}</select></label>
        <label>任务需求<textarea rows={3} value={question} onChange={e => setQuestion(e.target.value)} /></label>
        <details><summary>模拟档案、材料与政策</summary><textarea className={styles.code} rows={15} value={environment} onChange={e => setEnvironment(e.target.value)} aria-label="模拟任务环境" /></details>
        <button className={styles.primary} disabled={busy || !exampleId || !question.trim()} onClick={() => act(async () => {
          const value = await api<{ task: Task }>("/tasks", { domain: examples.find(x => x.id === exampleId)!.input.domain, question, environment: JSON.parse(environment) }); setSelected(value.task.id);
        })}>开始办理 →</button>
        <h3>自然语言反馈</h3><p className={styles.muted}>例如“仓库权限漏了”或“解释短一点”。识别与业务处理独立执行，不会立即复盘或创建 Skill。</p>
        <textarea rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} aria-label="后续消息" />
        <button disabled={busy || pending || !task || !feedback.trim()} onClick={() => act(async () => { await api("/messages", { text: feedback, task_id: task?.id }); setFeedback(""); })}>发送后续消息</button>
        <h3>最近的反馈识别</h3>{messages.slice(0, 6).map(m => <div className={styles.evidence} key={m.id}><b>{names[m.classification?.signal || m.status] || m.status}</b><p>{m.text}</p><small>{m.classification?.task_id ? `关联任务 ${m.classification.task_id.slice(0, 8)}` : "未归责历史任务"}</small>{m.error && <p>{m.error}</p>}</div>)}
      </section>
      <section className={styles.card}><h2>办理过程与结果</h2>
        <label>历史任务<select value={task?.id || ""} onChange={e => setSelected(e.target.value)}><option value="" disabled>尚无任务</option>{tasks.map(t => <option value={t.id} key={t.id}>{t.input.question}</option>)}</select></label>
        {pending && <p role="status">后台正在执行，结果会自动更新…</p>}
        {jobs.filter(j => j.status === "failed").slice(0, 2).map(j => <p className={styles.error} key={j.id}>{j.error}</p>)}
        {task?.conversation?.map((m, i) => <div key={i} className={styles.evidence}><b>{m.role === "user" ? "员工" : "企业 Agent"}</b><p className={styles.answer}>{m.content}</p></div>)}
        {task?.run && <><span className={styles.pill}>{names[task.run.result?.status || task.run.status] || task.run.status}</span><h3>跨部门申请</h3>
          {task.run.requests.length ? task.run.requests.map(r => <div className={styles.evidence} key={r.id}><b>{r.department} · {names[r.kind] || r.kind}</b><small>{names[r.status] || r.status}</small></div>) : <p>尚未创建申请。</p>}
          <p>{task.run.tool_calls} 次工具调用 · 加载 {task.run.loaded_skills.length} 条策略</p>
          {admin && task.run.experiment_assignments?.map(a => <div className={styles.evidence} key={a.skill_id}><b>模拟灰度 · {a.cohort === "treatment" ? "试验组" : "对照组"}</b><p>基于实际工单结果记录，每位参与者只计一次；自然语言不满意不直接充当失败结论。</p><textarea aria-label="模拟灰度结果依据" value={outcomeEvidence} onChange={e => setOutcomeEvidence(e.target.value)} rows={2} /><div className={styles.actions}>{[true, false].map(success => <button key={String(success)} disabled={busy || !outcomeEvidence.trim()} onClick={() => act(async () => { await api(`/tasks/${task.id}/outcome`, { skill_id: a.skill_id, success, evidence: outcomeEvidence }); })}>{success ? "核实成功" : "核实失败"}</button>)}</div></div>)}
          <details><summary>完整工具轨迹</summary><pre>{JSON.stringify(task.run.tool_trace, null, 2)}</pre></details></>}
      </section>
    </div>}
  </>;
}
