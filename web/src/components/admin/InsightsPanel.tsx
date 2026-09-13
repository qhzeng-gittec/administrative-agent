"use client";
import { useCallback, useEffect, useState } from "react";
import { systemInsights, type SystemInsights, type User } from "@/lib/api";
import Icon, { type IconName } from "../Icon";
import styles from "./admin.module.css";

const PLANE_ICON: Record<string, IconName> = { working: "activity", episodic: "clock", user: "agent", organization: "building", learning: "loop" };
const SIGNAL_LABEL: Record<string,string> = { up:"采纳",down:"点踩",correction:"纠错",copy:"复制",follow_up:"追问",abandon:"中断",verifier_pass:"校验通过",verifier_fail:"校验失败" };
export default function InsightsPanel({ user }: { user: User }) {
  const [data,setData]=useState<SystemInsights|null>(null); const [error,setError]=useState("");
  const load=useCallback(()=>systemInsights().then(setData).catch(e=>setError(String(e))),[]);
  useEffect(()=>{load()},[load]);
  if(error) return <div className={styles.errorBanner}>{error}</div>;
  if(!data) return <div className={styles.loading}><span/><b>正在构建记忆拓扑</b></div>;
  const f=data.fact_plane,e=data.evolution;
  return <div className={styles.panelStack}>
    <section className={styles.heroPanel}><div><span className={styles.eyebrow}>MEMORY GOVERNANCE</span><h2>五个记忆平面，一个独立事实平面</h2><p>记忆负责理解上下文与持续学习；制度事实始终回到 active 文档与完整 chunk，以来源和版本守住可信边界。</p></div><div className={styles.heroBadge}><Icon name="brain" size={28}/><span><b>{data.memory_planes.reduce((n,p)=>n+p.count,0)}</b><small>可治理记忆单元</small></span></div></section>
    <div className={styles.memoryMap}>
      <div className={styles.planeRail}>{data.memory_planes.map((p,i)=><div className={styles.memoryPlane} key={p.key}><span className={styles.planeIndex}>0{i+1}</span><span className={styles.planeIcon}><Icon name={PLANE_ICON[p.key]} size={19}/></span><div><b>{p.name}</b><small>{p.detail}</small><code>{p.store}</code></div><strong>{p.count}</strong></div>)}</div>
      <div className={styles.factCore}><div className={styles.orbit}/><span className={styles.factIcon}><Icon name="database" size={28}/></span><small>INDEPENDENT SOURCE OF TRUTH</small><h3>制度事实平面</h3><p>MongoDB 完整文档与 Chunk</p><div className={styles.factStats}><span><b>{f.active_documents}</b>有效文档</span><span><b>{f.chunks}</b>事实切片</span><span><b>{f.relations}</b>知识关系</span><span><b>{f.conflicts}</b>冲突关系</span></div></div>
    </div>
    <div className={styles.grid3}>
      <div className={styles.card}><div className={styles.cardTitle}><span><Icon name="shield" size={17}/>记忆治理</span><em>GUARDRAIL</em></div><div className={styles.statRows}><span>引用使用记录<b>{data.governance.usage_records}</b></span><span>失效组织记忆<b>{data.governance.stale_org_memory}</b></span><span>待确认候选<b>{data.governance.pending_candidates}</b></span><span>隐私视角<b>{user.dept_id ? "部门隔离" : "全局审计"}</b></span></div></div>
      <div className={styles.card}><div className={styles.cardTitle}><span><Icon name="layers" size={17}/>策略演进</span><em>VERSIONED</em></div><div className={styles.statRows}><span>策略版本快照<b>{e.strategy_versions}</b></span><span>策略执行记录<b>{e.executions}</b></span><span>运行中实验<b>{e.experiments.filter(x=>x.status==="running").length}</b></span><span>生命周期建议<b>{e.proposals.length}</b></span></div></div>
      <div className={styles.card}><div className={styles.cardTitle}><span><Icon name="activity" size={17}/>灰度流量</span><em>A/B BUCKET</em></div><div className={styles.bucket}><div style={{flex:e.treatment||1}}>Treatment <b>{e.treatment}</b></div><div style={{flex:e.control||1}}>Control <b>{e.control}</b></div></div><p className={styles.cardHint}>稳定哈希分桶 · 保留策略版本与命中记录 · 支持自动回滚</p></div>
    </div>
    <div className={styles.grid2}>
      <div className={styles.card}><div className={styles.cardTitle}><span><Icon name="activity" size={17}/>反馈信号雷达</span><em>OBSERVE</em></div><div className={styles.signalGrid}>{Object.entries(data.signals).map(([k,v])=><div key={k}><span>{SIGNAL_LABEL[k]||k}</span><b>{v}</b><i style={{width:`${Math.min(100,v*8)}%`}}/></div>)}</div></div>
      <div className={styles.card}><div className={styles.cardTitle}><span><Icon name="clock" size={17}/>最近执行 Trace</span><em>TRACEABLE</em></div><div className={styles.traceList}>{data.recent_traces.length===0?<div className={styles.empty}>暂无执行记录</div>:data.recent_traces.slice(0,7).map(t=><div key={t.id}><span className={t.success?styles.traceOk:styles.traceFail}/><span><b>{t.query||"系统审核任务"}</b><small>{t.intent||"review"} · {t.latency_ms}ms</small></span><time>{t.created_at.slice(5,16).replace("T"," ")}</time></div>)}</div></div>
    </div>
  </div>;
}
