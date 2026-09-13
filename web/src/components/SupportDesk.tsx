"use client";

import { useCallback, useEffect, useState } from "react";
import { getToken, type User } from "@/lib/api";
import styles from "./SupportDesk.module.css";
import EnterpriseDesk from "./EnterpriseDesk";

type Incident = { question: string; domain: "docker" | "ci"; observations: Record<string, Record<string, unknown>> };
type Example = { id: string; title: string; incident: Incident };
type Trace = { id: string; tool: string; arguments: string; result: unknown };
type Run = { id: string; status: string; cause_label: string; diagnosis: null | { cause: string; action: string; target: string; evidence: string[]; explanation: string }; tool_trace: Trace[]; tool_calls: number; latency_ms: number; loaded_skills: string[] };
type Skill = { id: string; name: string; domain: string; status: string; version: number; description: string; instructions: string; evaluation?: { reasons: string[]; samples: number; baseline_correct: number; candidate_correct: number }; samples?: Record<string, number> };
type Metric = { cases: number; correct: number; accuracy: number; tool_calls: number };
type Report = { id: string; model: string; completed_at: string; baseline_test: Metric; final_test: Metric; iterations: { iteration: number; domain: string; decision: string; reason?: string; evaluation?: { reasons: string[] } }[]; limitations: string[]; splits: Record<string, number> };
type Experiments = { reports: Report[]; jobs: { _id: string; status: string; result?: { error?: string } }[] };
type Doc = { id: string; title: string; content: string; source: string; version: number };
type Review = { _id: string; incident: Incident; run: Run; feedback: { resolved: boolean; correction: string }; reviewed_resolution?: { diagnosis: NonNullable<Run["diagnosis"]> } };

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/support${path}`, { method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  return value.data as T;
}

const statuses: Record<string, string> = { candidate: "候选", validated: "验证通过", rejected: "已拒绝", canary: "灰度中", active: "正式生效", rolled_back: "已撤回", inconclusive: "证据不足", retired: "已替换", simulation_completed: "模拟完成", no_candidate: "无需新增策略" };
const reasons: Record<string, string> = { no_measurable_benefit: "没有可测量收益", regression: "出现回归", skill_never_executed: "未实际加载技能", insufficient_samples: "样本不足" };

export default function SupportDesk({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [tab, setTab] = useState("企业任务");
  const [examples, setExamples] = useState<Example[]>([]);
  const [question, setQuestion] = useState("");
  const [domain, setDomain] = useState<"docker" | "ci">("docker");
  const [snapshot, setSnapshot] = useState('{"config":{},"runtime":{},"logs":{}}');
  const [run, setRun] = useState<Run | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [experiments, setExperiments] = useState<Experiments>({ reports: [], jobs: [] });
  const [docs, setDocs] = useState<Doc[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [correction, setCorrection] = useState("");
  const admin = user.role === "admin" && !user.dept_id;
  const refresh = useCallback(async () => {
    if (admin) {
      const [newSkills, newExperiments, newReviews] = await Promise.all([api<Skill[]>("/skills"), api<Experiments>("/experiments"), api<Review[]>("/reviews")]);
      setSkills(newSkills); setExperiments(newExperiments); setReviews(newReviews);
    }
  }, [admin]);
  useEffect(() => {
    let alive = true;
    Promise.all([api<Example[]>("/examples"), api<Doc[]>("/documents")]).then(([e, d]) => {
      if (alive) { setExamples(e); setDocs(d); }
    }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { refresh().catch(e => setError(e.message)); }, [refresh]);
  const running = experiments.jobs.some(j => ["queued", "running"].includes(j.status));
  useEffect(() => {
    if (!running) return;
    const interval = setInterval(() => refresh().catch(e => setError(e.message)), 5000);
    return () => clearInterval(interval);
  }, [running, refresh]);

  async function act(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  function choose(example: Example) {
    setDomain(example.incident.domain); setQuestion(example.incident.question);
    setSnapshot(JSON.stringify(example.incident.observations, null, 2)); setRun(null); setNotice("");
  }
  const report = experiments.reports[0];
  return <div className={styles.shell}>
    <aside className={styles.sidebar}>
      <div className={styles.brand}><span>桥</span><div>星桥<small>ENTERPRISE AGENT</small></div></div>
      <div className={styles.navLabel}>企业服务</div>
      {["企业任务", "诊断工作台", "操作手册", ...(admin ? ["反馈挖掘", "策略库", "迭代实验"] : [])].map((item, i) =>
        <button key={item} className={tab === item ? styles.selected : ""} onClick={() => setTab(item)}><span>0{i + 1}</span>{item}</button>)}
      <div className={styles.sidebarNote}>积累反馈 · 批量复盘<br />改进必须通过实验。<small>企业任务与研发支持 · 演示环境</small></div>
      <div className={styles.account}><b>{user.name}</b><small>{admin ? "策略管理员" : "团队成员"}</small><button onClick={onLogout}>退出登录</button></div>
    </aside>
    <main className={styles.main}>
      <header><div><div className={styles.eyebrow}>STARBRIDGE / {tab}</div><h1>{tab}</h1><p>{tab === "诊断工作台" ? "从配置与日志出发，定位问题并给出下一步。" : "保留每一次改进的证据，也保留没有收益的结论。"}</p></div><span className={styles.pill}>IT · 人事行政 · 财务</span></header>
      {["企业任务", "反馈挖掘"].includes(tab) && <EnterpriseDesk key={tab} miningMode={tab === "反馈挖掘"} admin={admin} />}
      {error && <div role="alert" className={styles.error}>{error}</div>}
      {notice && <div role="status" className={styles.notice}>{notice}</div>}
      {tab === "诊断工作台" && <div className={styles.columns}>
        <section className={styles.card}><h2>描述一个问题</h2><p className={styles.muted}>可加载合成案例体验，或粘贴自己的脱敏快照。工具只读取这些材料。</p>
          <label>加载示例<select disabled={busy} defaultValue="" onChange={e => { const example = examples.find(x => x.id === e.target.value); if (example) choose(example); }}><option value="" disabled>选择一个开发集案例</option>{examples.map(e => <option value={e.id} key={e.id}>{e.title}</option>)}</select></label>
          <label>问题域<select disabled={busy} value={domain} onChange={e => setDomain(e.target.value as "docker" | "ci")}><option value="docker">Docker 开发环境</option><option value="ci">CI 构建与测试</option></select></label>
          <label>遇到了什么问题？<textarea disabled={busy} rows={3} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：后端在容器里连接不上数据库。" /></label>
          <label>配置、状态与日志<textarea className={styles.code} disabled={busy} rows={15} value={snapshot} onChange={e => setSnapshot(e.target.value)} spellCheck={false} /></label>
          <button className={styles.primary} disabled={busy || !question.trim()} onClick={() => act(async () => { setRun(null); setRun(await api<Run>("/diagnose", { question, domain, observations: JSON.parse(snapshot) })); })}>{busy ? "正在读取证据与诊断…" : "开始诊断 →"}</button>
        </section>
        <section className={styles.card}>{run ? <>
          <div className={styles.eyebrow}>诊断结果 / {run.status}</div><h2>{run.cause_label}</h2>
          <p className={styles.answer}>{run.diagnosis?.explanation || "本次未能完成诊断，请查看执行记录。"}</p>
          {run.diagnosis && <div className={styles.evidence}><b>建议处理对象</b><code>{run.diagnosis.target || "需要补充证据或无需变更"}</code><small>依据：{run.diagnosis.evidence.join(" · ")}</small></div>}
          <div className={styles.stats}><div><b>{run.tool_calls}</b><small>工具调用</small></div><div><b>{(run.latency_ms / 1000).toFixed(1)}s</b><small>诊断耗时</small></div><div><b>{run.loaded_skills.length}</b><small>加载策略</small></div></div>
          <h3>执行记录</h3>{run.tool_trace.map((t, i) => <details key={t.id}><summary>{i + 1}. {t.tool}</summary><pre>{JSON.stringify(t.result, null, 2)}</pre></details>)}
          <label>处理结果与纠正<textarea rows={2} value={correction} onChange={e => setCorrection(e.target.value)} placeholder="实际原因是什么？最终如何解决？" /></label>
          <div className={styles.actions}>{[true, false].map(resolved => <button disabled={busy} key={String(resolved)} onClick={() => act(async () => { await api(`/runs/${run.id}/feedback`, { resolved, correction }); await refresh(); setNotice("反馈已保存，等待人工核实处理结果；不会直接修改生效策略。"); })}>{resolved ? "已解决" : "仍需处理"}</button>)}</div>
        </> : <div className={styles.empty}><span>01 → 02 → 03</span><h2>诊断过程会显示在这里</h2><p>读取证据 → 核对手册 → 提交诊断</p><p>有明确收益的策略才进入试用。没有适用 Skill 时，使用基础诊断流程。</p></div>}</section>
      </div>}
      {tab === "操作手册" && <div className={styles.docGrid}>{docs.map(d => <section key={d.id} className={styles.card}><span className={styles.pill}>有效版本 {d.version}</span><h2>{d.title}</h2><p>{d.content}</p>{d.source.startsWith("https:") && <a href={d.source} target="_blank" rel="noreferrer">技术依据 ↗</a>}</section>)}</div>}
      {tab === "处理反馈" && <>
        <section className={styles.card}><h2>先核实结果，再提出改进</h2><p>审核实际原因与处理对象，形成可回放的参考结论。原始点踩不会直接生成生效规则。</p><div className={styles.actions}>{(["docker", "ci"] as const).map(d => <button key={d} disabled={busy || running} onClick={() => act(async () => { await api("/improve", { domain: d }); await refresh(); setNotice("改进任务已排队：从已审核记录生成候选，再运行隔离验证。可在迭代实验中查看任务状态。"); })}>生成并验证 {d.toUpperCase()} 策略</button>)}</div></section>
        {!reviews.length && <section className={styles.card}><p>还没有处理反馈。在诊断完成后记录实际处理结果，它会出现在这里。</p></section>}
        {reviews.map(r => <section key={r._id} className={styles.card}><span className={styles.pill}>{r.reviewed_resolution ? "已审核" : "待审核"}</span><h2>{r.incident.question}</h2><p>员工反馈：{r.feedback.resolved ? "已解决" : "仍需处理"} · {r.feedback.correction || "没有补充说明"}</p><details><summary>查看原始诊断与环境证据</summary><pre>{JSON.stringify({ incident: r.incident, diagnosis: r.run.diagnosis }, null, 2)}</pre></details>
          <label>审核参考结论（修改为已核实的原因、动作和对象）<textarea className={styles.code} rows={10} value={reviewDrafts[r._id] ?? JSON.stringify(r.reviewed_resolution?.diagnosis ?? r.run.diagnosis ?? { cause: "insufficient_evidence", action: "request_evidence", target: "", evidence: ["config", "runtime", "logs"], explanation: "请补充核实结果" }, null, 2)} onChange={e => setReviewDrafts({ ...reviewDrafts, [r._id]: e.target.value })} /></label>
          <button className={styles.primary} disabled={busy} onClick={() => act(async () => { const body = reviewDrafts[r._id] ? JSON.parse(reviewDrafts[r._id]) : r.reviewed_resolution?.diagnosis ?? r.run.diagnosis; if (!body) throw new Error("请填写核实后的参考结论"); await api(`/runs/${r._id}/review`, body); await refresh(); setNotice("参考结论已保存。仅明确错误或高调用成本的记录参与候选生成。"); })}>保存核实结果</button>
        </section>)}
      </>}
      {tab === "策略库" && <><div className={styles.flow}>实际错误 / 多余调用 → 候选 → 隔离验证 → 10% 灰度 → 正式生效或撤回</div>
        {!skills.length && <section className={styles.card}><h2>还没有候选策略</h2><p>在迭代实验中运行基线，只有出现错误或高调用成本才提出候选。</p></section>}
        <div className={styles.docGrid}>{skills.map(s => <section key={s.id} className={styles.card}><span className={styles.pill}>{statuses[s.status] || s.status}</span><h2>{s.name} <small>v{s.version}</small></h2><p>{s.description}</p><details><summary>查看诊断指导</summary><p className={styles.answer}>{s.instructions}</p></details>
          {s.evaluation && <p>验证：{s.evaluation.baseline_correct} → {s.evaluation.candidate_correct} / {s.evaluation.samples} 题<br />{s.evaluation.reasons.map(r => reasons[r] || r).join("；") || "验证门槛通过"}</p>}
          {s.samples && <p>独立用户：实验组 {s.samples.treatment}，对照组 {s.samples.control}</p>}
          <div className={styles.actions}>{s.status === "validated" && <button disabled={busy} onClick={() => act(async () => { await api(`/skills/${s.id}/canary`, {}); await refresh(); })}>{["onboarding", "access", "expense"].includes(s.domain) ? "开始模拟灰度" : "开始真实灰度"}</button>}{["active", "canary"].includes(s.status) && <button disabled={busy} onClick={() => act(async () => { await api(`/skills/${s.id}/rollback`, {}); await refresh(); })}>撤回此版本</button>}</div>
        </section>)}</div><p className={styles.muted}>灰度以独立用户计数，最多 1,000 条或 14 天；终点评估两组各至少 30 条，并检验改善证据。模拟数据无法触发正式上线。</p></>}
      {tab === "迭代实验" && <>
        <section className={styles.card}><div className={styles.reportHead}><div><h2>一次可追溯的策略迭代</h2><p>252 个合成案例，覆盖 14 类故障与控制场景。开发、验证、冻结验收各 84 题。</p></div><button className={styles.primary} disabled={busy || running} onClick={() => act(async () => { await api("/experiments", {}); await refresh(); })}>{running ? "实验执行中…" : "运行真实模型实验"}</button></div><p className={styles.muted}>会产生模型 API 调用。评分核对诊断、处理对象和证据访问；结果代表快照诊断能力，不代表已修复真实服务。</p>
        {experiments.jobs.slice(-3).map(j => <p key={j._id}><code>{j._id.slice(-8)}</code> · {j.status}{j.result?.error && `：${j.result.error}`}</p>)}</section>
        {report && <><div className={styles.stats}><div><b>{report.baseline_test.correct}/{report.baseline_test.cases}</b><small>基线 · 冻结验收</small></div><div><b>{report.final_test.correct}/{report.final_test.cases}</b><small>最终策略 · 冻结验收</small></div><div><b>{report.iterations.length}</b><small>候选决策记录</small></div></div>
        <section className={styles.card}><h2>保留成功与失败的实验</h2><p>{report.model} · {new Date(report.completed_at).toLocaleString()}</p>{report.iterations.map((it, i) => <div key={i} className={styles.iteration}><b>第 {it.iteration} 轮 · {it.domain.toUpperCase()}</b><span>{statuses[it.decision] || it.decision}</span><p>{it.evaluation?.reasons.map(r => reasons[r] || r).join("；") || it.reason || "已完成验证"}</p></div>)}<details><summary>测评范围与限制</summary>{report.limitations.map(l => <p key={l}>{l}</p>)}</details></section></>}
      </>}
    </main>
  </div>;
}
