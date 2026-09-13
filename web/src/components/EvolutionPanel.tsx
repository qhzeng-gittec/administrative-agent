"use client";

import { useEffect, useState } from "react";
import { enterpriseApi as api, labels } from "@/lib/enterprise";
import styles from "./SupportDesk.module.css";

type Gate = { samples: number; judged: number; unknown: number; fixes: number; regressions: number; passed: boolean; reasons: string[] };
type Capability = { id: string; kind: string; status: string; domain: string; version: number; selection: string; body: { name: string } };
type Cycle = { id: string; domain: string; status: string; error?: string; source: string; created_at: string;
  change?: { action: string; reason: string; target_id: string | null }; partition_counts: Record<string, number>;
  plan?: { criteria: string[]; cases: unknown[] }; validation?: Gate; holdout?: Gate; canary_summary?: Gate; monitoring_summary?: Gate;
  history: { status: string; at: string }[]; progress?: { stage: string; completed: number; total: number } };
type State = { cycles: Cycle[]; capabilities: Capability[]; jobs: { id: string; type: string; status: string; error?: string }[];
  last_run: { status: string; tasks?: number }[]; config: { window: number; min_cluster: number; min_signals: number; interval_seconds: number; judge_model: string } };

export default function EvolutionPanel() {
  const [data, setData] = useState<State | null>(null);
  const [detail, setDetail] = useState<{ id: string; value: unknown } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    async function refresh() { try { const value = await api<State>("/evolution"); if (alive) setData(value); } catch (e) { if (alive) setError(String(e)); } }
    void refresh(); const timer = setInterval(refresh, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  async function act(fn: () => Promise<void>) { setBusy(true); setError(""); try { await fn(); setData(await api<State>("/evolution")); } catch (e) { setError(String(e)); } finally { setBusy(false); } }
  return <>{error && <div role="alert" className={styles.error}>{error}</div>}
    <section className={styles.card}><h2>系统内部闭环</h2><p>工作台执行与反馈 → 相似任务聚类 → 冻结历史分区及自生成案例 → 提出能力变更 → Judge对照 → 模拟灰度 → 启用后观察或回滚。</p>
      <p className={styles.muted}>这里只展示系统自己的周期。开发者外部测评、预设错误历史和旧实验成绩不参与准入。Judge判断保留证据和不确定性，不能视为人工标准答案。</p>
      {data && <><div className={styles.stats}><div><b>{data.config.window}</b><small>历史窗口</small></div><div><b>{data.config.min_cluster}</b><small>最小任务簇</small></div><div><b>{data.config.min_signals}</b><small>发现分区最低疑似错误任务</small></div></div>
        <p className={styles.muted}>每{data.config.interval_seconds}秒检查 · Judge：{data.config.judge_model}</p></>}
      <button className={styles.primary} disabled={busy || data?.jobs.some(j => j.type === "mine" && ["queued", "running"].includes(j.status))} onClick={() => act(async () => { await api("/evolution", {}); })}>立即检查并推进闭环</button>
    </section>
    {!data?.cycles.length && <section className={styles.card}><h2>等待实际任务积累</h2><p>已有工作台历史达到规模且分区样本充分后，系统才准备内部评测。不会用外部测试数据填满这个页面。</p>
      {data?.last_run[0] && <p>{labels[data.last_run[0].status] || data.last_run[0].status} · 已观察 {data.last_run[0].tasks || 0} 个任务</p>}</section>}
    {data?.jobs.filter(j => j.status === "failed").slice(0, 3).map(j => <div className={styles.error} key={j.id}>后台{j.type === "observe" ? "观察" : "进化"}作业失败：{j.error}
      {j.type === "observe" && <button disabled={busy} onClick={() => act(async () => { await api(`/jobs/${j.id}/retry`, {}); })}>重试观察</button>}</div>)}
    {data?.cycles.map(c => <section className={styles.card} key={c.id}><span className={styles.pill}>{labels[c.status] || c.status}</span><h2>{labels[c.domain]} · {c.change ? labels[c.change.action] : "准备变更"}</h2>
      <p>{c.change?.reason || "先冻结评价材料，随后由复盘模型提出变更。"}</p>
      <p className={styles.muted}>复盘历史 {c.partition_counts.discovery} · 验证历史 {c.partition_counts.validation} · 保留历史 {c.partition_counts.holdout} · 模型生成补充案例 {c.plan?.cases.length || 0}</p>
      {c.progress && <p>{labels[c.progress.stage]}：{c.progress.completed}/{c.progress.total}</p>}
      {(["validation", "holdout", "canary_summary", "monitoring_summary"] as const).map(stage => { const g = c[stage]; return g && <p key={stage}><b>{labels[stage] || (stage === "canary_summary" ? "后续任务灰度" : "启用后观察")}：</b>可判定 {g.judged}/{g.samples}，修复 {g.fixes}，退步 {g.regressions}，无法判断 {g.unknown}</p>; })}
      {c.error && <p className={styles.error}>{c.error}</p>}
      <details><summary>周期流转与评价标准</summary><p>{c.history.map(h => labels[h.status] || h.status).join(" → ")}</p>{c.plan?.criteria.map(t => <p key={t}>{t}</p>)}</details>
      <div className={styles.actions}><button disabled={busy} onClick={() => act(async () => { setDetail({ id: c.id, value: await api(`/evolution/${c.id}`) }); })}>查看冻结材料、候选和逐条证据</button>
        {c.status === "failed" && <button disabled={busy} onClick={() => act(async () => { await api(`/evolution/${c.id}/retry`, {}); })}>从已有记录重试</button>}
        {["canary", "active"].includes(c.status) && <button disabled={busy} onClick={() => act(async () => { await api(`/evolution/${c.id}/rollback`, {}); })}>撤回这次变更</button>}</div>
      {detail?.id === c.id && <pre>{JSON.stringify(detail.value, null, 2)}</pre>}
    </section>)}
    <section className={styles.card}><h2>Skill与工具版本</h2>{!data?.capabilities.length && <p>尚无内部闭环启用的能力。新增、修订和淘汰通过评测与灰度后会在这里留下记录。</p>}
      {data?.capabilities.map(c => <details key={c.id}><summary>{c.body.name} · {c.kind === "skill" ? "Skill" : "组合工具"} v{c.version} · {labels[c.status] || c.status}</summary><p>{c.selection}</p><pre>{JSON.stringify(c.body, null, 2)}</pre></details>)}</section>
  </>;
}
