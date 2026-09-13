"use client";

import { useState } from "react";
import type { User } from "@/lib/api";
import EnterpriseTasks from "./EnterpriseTasks";
import EvolutionPanel from "./EvolutionPanel";
import styles from "./SupportDesk.module.css";

export default function Workbench({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [tab, setTab] = useState("tasks");
  const admin = user.role === "admin" && !user.dept_id;
  return <div className={styles.shell}>
    <aside className={styles.sidebar}>
      <div className={styles.brand}><span>桥</span><div>星桥<small>ENTERPRISE AGENT</small></div></div>
      <button className={tab === "tasks" ? styles.selected : ""} onClick={() => setTab("tasks")}>企业任务</button>
      {admin && <button className={tab === "evolution" ? styles.selected : ""} onClick={() => setTab("evolution")}>内部进化</button>}
      <div className={styles.sidebarNote}>让执行留下证据<br />让改进经过验证<small>当前连接本地模拟申请环境</small></div>
      <div className={styles.account}>{user.name}<button onClick={onLogout}>退出登录</button></div>
    </aside>
    <main className={styles.main}><header><div><div className={styles.eyebrow}>STARBRIDGE / {tab === "tasks" ? "WORKSPACE" : "EVOLUTION"}</div>
      <h1>{tab === "tasks" ? "把事项交给助手" : "从实际执行中改进"}</h1>
      <p>{tab === "tasks" ? "提交需求，查看办理结果，再反馈需要改进的地方。" : "系统准备评测、提出变更、执行对照，并从后续任务中决定启用或回滚。"}</p></div>
      <span className={styles.pill}>模拟业务环境</span></header>
      {tab === "tasks" ? <EnterpriseTasks /> : <EvolutionPanel />}
    </main>
  </div>;
}
