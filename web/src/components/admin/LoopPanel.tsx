"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveSkill, getLoopJob, listLoopJobs, listTraces, pendingFeedback, runLoop, setLoopPhase,
  type Dashboard, type Hook, type LoopCycleReport, type LoopJob, type Rule, type Skill, type User,
} from "@/lib/api";
import Icon from "../Icon";
import styles from "./admin.module.css";
import { LOOP_STAGES, PHASE_DESC, PHASE_LABEL, phaseBadgeClass, phaseLabel } from "./labels";

const STAGE_INDEX: Record<string, number> = { queued: -1, observe: 1, reflect: 2, adapt: 3, deploy: 4, complete: 5 };
const ARTIFACT_LABEL: Record<string, string> = { skills: "Skill 总数", active_skills: "已生效 Skill", pending_skills: "待审核 Skill", hooks: "Hooks", rules: "Rules", experiments: "实验", strategy_versions: "策略版本" };
const ROOT_CAUSE_LABEL: Record<string, string> = { retrieval: "检索召回", intent: "意图路由", generation: "答案生成", knowledge_gap: "知识缺口" };

export default function LoopPanel({ data, refresh, user }: { data: Dashboard; refresh: () => void; user: User }) {
  const [currentJob, setCurrentJob] = useState<LoopJob | null>(null);
  const [history, setHistory] = useState<LoopJob[]>([]);
  const [feedback, setFeedback] = useState<Record<string, unknown>[] | null>(null);
  const [traces, setTraces] = useState<Record<string, unknown>[] | null>(null);
  const [error, setError] = useState("");
  const isSuper = !user.dept_id;

  const loadHistory = useCallback(async () => {
    if (!isSuper) return;
    try {
      const jobs = await listLoopJobs(8); setHistory(jobs);
      if (!currentJob && jobs[0]) setCurrentJob(jobs[0]);
    } catch { /* history does not block the panel */ }
  }, [isSuper, currentJob]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  useEffect(() => {
    if (!currentJob || !["queued", "running"].includes(currentJob.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getLoopJob(currentJob._id); setCurrentJob(next);
        if (["completed", "failed"].includes(next.status)) {
          window.clearInterval(timer); await refresh(); await loadHistory();
        }
      } catch (e) { setError(e instanceof Error ? e.message : String(e)); window.clearInterval(timer); }
    }, 900);
    return () => window.clearInterval(timer);
  }, [currentJob?._id, currentJob?.status, refresh, loadHistory]);

  async function onRunLoop() {
    setError("");
    try {
      const queued = await runLoop();
      setCurrentJob({
        _id: queued.job_id, type: "run_loop", status: "queued",
        progress: { stage: "queued", detail: queued.message }, result: undefined,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      });
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  async function onLoadFeedback() { setFeedback(await pendingFeedback()); }
  async function onLoadTraces() { setTraces(await listTraces(20)); }
  async function onSetPhase(phase: string) { await setLoopPhase(phase); refresh(); }
  async function onApproveSkill(id: string) { await approveSkill(id); refresh(); }

  const report = currentJob?.status === "completed" ? currentJob.result as LoopCycleReport : null;
  const currentStage = currentJob?.status === "completed" ? "complete" : currentJob?.progress?.stage || currentJob?.status || "idle";
  const currentIndex = STAGE_INDEX[currentStage] ?? -1;
  const skills = data.skills || [], hooks = data.hooks || [], rules = data.rules || [];

  return <div className={styles.panelStack}>
    <div className={styles.sectionHead}><div><span className={styles.eyebrow}>EVOLUTION LOOP</span><h2>反馈如何改变下一轮行为</h2><p>提交后持续跟踪 Worker 的真实执行阶段；完成时展示信号、根因、策略变化和部署结果。</p></div></div>

    <section className={styles.loopConsole}>
      <div className={styles.loopConsoleHead}>
        <div><span className={styles.liveDot}/><b>{currentJob ? `LOOP JOB · ${currentJob._id.slice(-10)}` : "LOOP CONTROL CENTER"}</b><small>{currentJob ? jobStatusText(currentJob) : "等待管理员触发一次受治理的进化循环"}</small></div>
        <div className={styles.row}>
          <span className={styles.badge + " " + styles.badgeBlue}>全局阶段：{phaseLabel(data.loop_phase_global)}</span>
          {isSuper && <button className={styles.btn} onClick={onRunLoop} disabled={!!currentJob && ["queued", "running"].includes(currentJob.status)}>{currentJob && ["queued", "running"].includes(currentJob.status) ? "执行中…" : "▶ 触发新一轮 Loop"}</button>}
        </div>
      </div>
      <div className={styles.loopTimeline}>{LOOP_STAGES.map((stage, index) => {
        const state = currentJob ? index < currentIndex ? "done" : index === currentIndex ? "running" : "waiting" : "waiting";
        return <div key={stage.key} className={`${styles.timelineStep} ${styles[`timeline_${state}`]}`}>
          <div className={styles.timelineIndex}>{state === "done" ? <Icon name="check" size={14}/> : index + 1}</div>
          <div><b>{stage.name}</b><small>{stage.desc}</small>{state === "running" && <em>{currentJob?.progress?.detail || "处理中"}</em>}</div>
          {index < LOOP_STAGES.length - 1 && <span className={styles.timelineLine}/>}
        </div>;
      })}</div>
      {currentJob?.status === "failed" && <div className={styles.loopError}>执行失败：{String((currentJob.result as {error?:string})?.error || "未知错误")}</div>}
      {!currentJob && <div className={styles.loopHint}><Icon name="activity" size={17}/><span><b>触发后页面不会停留在 job_id</b><small>系统将自动轮询作业，依次点亮 Observe、Reflect、Adapt 和 Deploy，并展示最终变化。</small></span></div>}
    </section>

    {error && <div className={styles.errorBanner}>{error}</div>}
    {report && <LoopReport report={report}/>}

    {isSuper && history.length > 0 && <section className={styles.card}>
      <div className={styles.cardTitle}><span><Icon name="clock" size={17}/>最近 Loop 运行记录</span><em>ASYNC JOB HISTORY</em></div>
      <div className={styles.loopHistory}>{history.map(job => <button key={job._id} className={currentJob?._id === job._id ? styles.historyActive : ""} onClick={() => setCurrentJob(job)}><span className={statusClass(job.status)}/><span><b>{job._id.slice(-12)}</b><small>{new Date(job.created_at).toLocaleString("zh-CN")} · {job.progress?.stage || job.status}</small></span><em>{job.status}</em></button>)}</div>
    </section>}

    <div className={styles.phaseRow}>{(["human_in_loop", "human_on_loop", "human_out_of_loop"] as const).map((phase, i) => <div key={phase} className={`${styles.phaseCard} ${data.loop_phase_global === phase ? styles.active : ""}`} onClick={() => isSuper && onSetPhase(phase)} style={{cursor:isSuper?"pointer":"default"}}><div className={styles.phaseTitle}>Phase {i+1} · {PHASE_LABEL[phase]} {data.loop_phase_global===phase&&"✓"}</div><div className={styles.phaseDesc}>{PHASE_DESC[phase]}</div></div>)}</div>

    <div className={styles.row}><button className={styles.btnGhost} onClick={onLoadFeedback}>加载待处理反馈（Observe）</button><button className={styles.btnGhost} onClick={onLoadTraces}>加载最近 Trace（Execute）</button></div>
    {feedback && <FeedbackTable rows={feedback}/>}
    {traces && <TraceTable rows={traces}/>}

    <div className={styles.sectionHead} style={{marginTop:10}}><div><span className={styles.eyebrow}>EXECUTABLE POLICY MEMORY</span><h2>Skill 自进化（Skill Miner）</h2><p>基线 Skill 提供可演示的真实工作流；自动 Skill 仍需由高频问题聚类、Trace 回放和灰度实验产生。</p></div><div className={styles.skillSummary}><span><b>{skills.length}</b>总数</span><span><b>{skills.filter(s=>s.status==="active").length}</b>已生效</span><span><b>{skills.reduce((n,s)=>n+(s.metrics?.trigger_count||0),0)}</b>命中</span></div></div>
    <div className={styles.skillGrid}>{skills.map(skill => <SkillCard key={skill._id} skill={skill} canApprove={!user.dept_id || skill.dept_id === user.dept_id} onApprove={onApproveSkill}/>)}</div>

    <div className={styles.sectionHead} style={{marginTop:10}}><div><span className={styles.eyebrow}>POLICY GUARDRAILS</span><h2>Hooks 与 Rules</h2></div></div>
    <div className={styles.grid2}><ArtifactList title={`Hooks · ${hooks.length}`} rows={hooks}/><ArtifactList title={`Rules · ${rules.length}`} rows={rules}/></div>
  </div>;
}

function LoopReport({report}:{report:LoopCycleReport}) {
  const causes = report.reflect?.root_causes || {};
  const changes = Object.entries(report.changes || {}).filter(([,value])=>value!==0);
  return <section className={styles.reportPanel}>
    <div className={styles.reportHero}><span className={styles.reportCheck}><Icon name="check" size={22}/></span><div><span>LOOP CYCLE COMPLETE</span><h3>{report.summary}</h3><p>{report.next_action}</p></div><div className={styles.reportDuration}><b>{(report.duration_ms/1000).toFixed(1)}s</b><small>执行耗时</small></div></div>
    <div className={styles.reportMetrics}><div><span>Observe</span><b>{report.observed}</b><small>待处理反馈</small></div><div><span>Bad Cases</span><b>{report.bad_cases}</b><small>进入根因分析</small></div><div><span>Adapt</span><b>{report.adaptations?.length||0}</b><small>策略候选</small></div><div><span>Deploy</span><b>{(report.deployed?.skills||0)+(report.deployed?.hooks||0)+(report.deployed?.rules||0)}</b><small>本轮发布</small></div></div>
    <div className={styles.reportGrid}>
      <div className={styles.reportCard}><h4>观察到的反馈信号</h4>{Object.keys(report.signals||{}).length===0?<p>本轮没有新信号</p>:<div className={styles.signalPills}>{Object.entries(report.signals).map(([k,v])=><span key={k}>{signalLabel(k)} <b>{v}</b></span>)}</div>}</div>
      <div className={styles.reportCard}><h4>Reflect 根因分布</h4>{Object.keys(causes).length===0?<p>没有 bad case，因此无需归因</p>:<div className={styles.causeBars}>{Object.entries(causes).map(([k,v])=><div key={k}><span>{ROOT_CAUSE_LABEL[k]||k}</span><i><em style={{width:`${Math.min(100,Number(v)*20)}%`}}/></i><b>{v}</b></div>)}</div>}</div>
      <div className={styles.reportCard}><h4>策略资产变化</h4>{changes.length===0?<p>本轮只完成健康检查，没有新增或发布策略。</p>:<div className={styles.changeList}>{changes.map(([k,v])=><span key={k}>{ARTIFACT_LABEL[k]||k}<b className={Number(v)>0?styles.deltaUp:styles.deltaDown}>{Number(v)>0?"+":""}{v}</b></span>)}</div>}</div>
    </div>
    {report.adaptations?.length>0&&<div className={styles.adaptationList}><h4>本轮生成的候选</h4>{report.adaptations.map((a,i)=><span key={a.id||i}><em>{a.type}</em><b>{a.name||a.id}</b><small>{a.auto_activated?"已自动生效":"等待审核/灰度"}</small></span>)}</div>}
  </section>;
}

function SkillCard({skill,canApprove,onApprove}:{skill:Skill;canApprove:boolean;onApprove:(id:string)=>void}) {
  const metrics=skill.metrics||{}, steps=(skill.action?.steps as Array<Record<string,unknown>>|undefined)||[];
  return <article className={styles.skillCard}><header><span className={styles.skillIcon}><Icon name="spark" size={18}/></span><div><span>{skill.origin==="builtin_baseline"?"BUILT-IN BASELINE":skill.auto_generated?"LOOP MINED":"MANAGED SKILL"}</span><h3>{skill.name}</h3></div><em className={skill.status==="active"?styles.skillActive:styles.skillPending}>{skill.status==="active"?"已生效":"待审核"}</em></header><p>{skill.description||"从重复问题模式中沉淀的可执行策略。"}</p><div className={styles.skillMeta}><span>v{skill.version||1}</span><span>{skill.scope==="global"?"全局":skill.dept_id}</span><span>灰度 {Math.round((skill.gray_percent??1)*100)}%</span></div><div className={styles.skillTriggers}>{(skill.trigger?.intent_patterns||[]).map(p=><span key={p}>{p}</span>)}</div><div className={styles.workflow}><b>执行工作流</b>{steps.map((step,i)=><div key={i}><span>{i+1}</span><strong>{actionLabel(String(step.action||""))}</strong><small>{stepDetail(step)}</small>{i<steps.length-1&&<i/>}</div>)}</div><div className={styles.skillMetrics}><span><b>{metrics.trigger_count||0}</b>触发</span><span><b>{Math.round((metrics.success_rate||0)*100)}%</b>成功率</span><span><b>{skill.replay?.delta!=null?`${Math.round(skill.replay.delta*100)}%`:"—"}</b>回放提升</span></div>{skill.rubric_rules?.length?<details className={styles.skillRules}><summary>查看反思与约束规则</summary><ul>{[...(skill.unique_rules||[]),...(skill.rubric_rules||[])].map((r,i)=><li key={i}>{r}</li>)}</ul></details>:null}{skill.status==="pending"&&canApprove&&<button className={styles.btn} onClick={()=>onApprove(skill._id)}>审核通过并激活</button>}</article>;
}

function FeedbackTable({rows}:{rows:Record<string,unknown>[]}) { return <section className={styles.card}><div className={styles.cardTitle}><span>待处理反馈 · {rows.length}</span><em>OBSERVE QUEUE</em></div>{rows.length===0?<div className={styles.empty}>无待处理反馈</div>:<table className={styles.table}><thead><tr><th>信号</th><th>问题</th><th>回答摘要</th></tr></thead><tbody>{rows.slice(0,10).map((f,i)=><tr key={i}><td><span className={styles.badge+" "+(f.signal==="down"||f.signal==="correction"?styles.badgeRed:styles.badgeGray)}>{String(f.signal)}</span></td><td>{String(f.query||"")}</td><td>{String(f.answer||"").slice(0,120)}</td></tr>)}</tbody></table>}</section> }
function TraceTable({rows}:{rows:Record<string,unknown>[]}) { return <section className={styles.card}><div className={styles.cardTitle}><span>最近 Trace · {rows.length}</span><em>EXECUTE HISTORY</em></div><table className={styles.table}><thead><tr><th>时间</th><th>问题</th><th>意图</th><th>延迟</th><th>校验</th></tr></thead><tbody>{rows.map((t,i)=><tr key={i}><td className={styles.mono}>{String(t.created_at||"").slice(0,19).replace("T"," ")}</td><td>{String(t.query||"")}</td><td>{String((t.intent as Record<string,unknown>)?.type||"")}</td><td>{String(t.latency_ms||0)}ms</td><td><span className={styles.badge+" "+(t.success?styles.badgeGreen:styles.badgeRed)}>{t.success?"通过":"未通过"}</span></td></tr>)}</tbody></table></section> }
function ArtifactList({title,rows}:{title:string;rows:Array<Hook|Rule>}) { return <section className={styles.card}><div className={styles.cardTitle}><span>{title}</span><em>ACTIVE POLICY</em></div>{rows.map((row,i)=>{const detail="content" in row?row.content:JSON.stringify((row as Hook).trigger||{});return <div className={styles.artifactRow} key={row._id||i}><div><b>{row.name||row._id}</b><small>{detail}</small></div><span className={styles.badge+" "+(row.status==="active"?styles.badgeGreen:styles.badgeAmber)}>{row.status}</span></div>})}</section> }
function jobStatusText(job:LoopJob){if(job.status==="completed")return "执行完成，可查看本轮影响";if(job.status==="failed")return "执行失败，请检查错误信息";return job.progress?.detail||"等待 Worker 领取任务"}
function statusClass(status:string){return status==="completed"?styles.statusDone:status==="failed"?styles.statusFailed:styles.statusRunning}
function signalLabel(key:string){return ({up:"采纳",down:"点踩",correction:"纠错",copy:"复制",follow_up:"追问",abandon:"中断",verifier_pass:"校验通过",verifier_fail:"校验失败"} as Record<string,string>)[key]||key}
function actionLabel(action:string){return ({extract_entity:"提取关键实体",retrieve:"扩展事实召回",generate:"结构化生成",call_tool:"调用治理工具"} as Record<string,string>)[action]||action}
function stepDetail(step:Record<string,unknown>){const p=(step.params||{}) as Record<string,unknown>;return String(p.query||p.template||p.tool||p.entity||"执行策略步骤")}
