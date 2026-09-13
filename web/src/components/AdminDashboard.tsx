"use client";

import { useCallback, useEffect, useState } from "react";
import { dashboard, type Dashboard, type User } from "@/lib/api";
import Icon, { type IconName } from "./Icon";
import styles from "./admin/admin.module.css";
import OverviewPanel from "./admin/OverviewPanel";
import DeptPanel from "./admin/DeptPanel";
import ReviewPanel from "./admin/ReviewPanel";
import LoopPanel from "./admin/LoopPanel";
import AgentPanel from "./admin/AgentPanel";
import InsightsPanel from "./admin/InsightsPanel";

type Tab = "overview" | "dept" | "review" | "loop" | "insights" | "agent";
const TABS: { key: Tab; label: string; sub: string; icon: IconName }[] = [
  { key: "overview", label: "态势总览", sub: "全局运行与审核阶段", icon: "grid" },
  { key: "dept", label: "知识资产", sub: "部门与文档事实治理", icon: "building" },
  { key: "review", label: "可信审核", sub: "人工校验与渐进退出", icon: "review" },
  { key: "loop", label: "进化 Loop", sub: "反馈、反思与策略部署", icon: "loop" },
  { key: "insights", label: "记忆与实验", sub: "五层记忆和灰度观测", icon: "brain" },
  { key: "agent", label: "Agent 网络", sub: "部门执行单元与弹性", icon: "agent" },
];

export default function AdminDashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sideOpen, setSideOpen] = useState(false);
  const isSuper = !user.dept_id;

  const refresh = useCallback(async () => {
    try { setData(await dashboard()); setError(""); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const current = TABS.find(t => t.key === tab)!;

  return <div className={styles.wrap}>
    <aside className={`${styles.sidebar} ${sideOpen ? styles.sidebarMobileOpen : ""}`}>
      <div className={styles.brand}><span className={styles.brandSeal}>文</span><span><b>文枢</b><small>WENSHU CONTROL</small></span></div>
      <div className={styles.scopeCard}><span className={styles.scopeIcon}><Icon name={isSuper ? "shield" : "building"} size={17}/></span><span><b>{isSuper ? "超级管理空间" : "部门治理空间"}</b><small>{isSuper ? "全局策略与事实控制平面" : `${user.dept_id} · 数据已强制隔离`}</small></span></div>
      <nav className={styles.nav}>{TABS.map(t => <button key={t.key} className={`${styles.navItem} ${tab === t.key ? styles.navActive : ""}`} onClick={() => {setTab(t.key);setSideOpen(false)}}>
        <span className={styles.navIcon}><Icon name={t.icon} size={18}/></span><span><b>{t.label}</b><small>{t.sub}</small></span>
        {t.key === "review" && (data?.pending_review_count || 0) > 0 && <em>{data!.pending_review_count}</em>}
      </button>)}</nav>
      <div className={styles.sidebarFoot}><div className={styles.live}><span/> CONTROL PLANE ONLINE</div><button onClick={onLogout}><Icon name="logout" size={16}/>退出系统</button></div>
    </aside>
    {sideOpen && <button className={styles.backdrop} onClick={() => setSideOpen(false)} aria-label="关闭菜单"/>}
    <section className={styles.workspace}>
      <header className={styles.header}>
        <button className={styles.mobileMenu} onClick={() => setSideOpen(true)}><Icon name="menu"/></button>
        <div><div className={styles.breadcrumb}>控制台 / {isSuper ? "全局" : user.dept_id}</div><h1>{current.label}</h1><p>{current.sub}</p></div>
        <div className={styles.headerRight}><button className={styles.refreshBtn} onClick={refresh}><Icon name="refresh" size={16}/>刷新数据</button><div className={styles.avatar}>{user.name.slice(0,1)}</div><div className={styles.userInfo}><b>{user.name}</b><small>{isSuper ? "超级管理员" : "部门管理员"}</small></div></div>
      </header>
      <main className={styles.body}>
        {error && <div className={styles.errorBanner}><Icon name="shield" size={17}/>{error}</div>}
        {loading || !data ? <div className={styles.loading}><span/><b>正在同步控制平面</b><small>读取事实、记忆与策略状态</small></div> : <>
          {tab === "overview" && <OverviewPanel data={data} refresh={refresh}/>}
          {tab === "dept" && <DeptPanel data={data} refresh={refresh} user={user}/>}
          {tab === "review" && <ReviewPanel data={data} refresh={refresh}/>}
          {tab === "loop" && <LoopPanel data={data} refresh={refresh} user={user}/>}
          {tab === "insights" && <InsightsPanel user={user}/>}
          {tab === "agent" && <AgentPanel data={data}/>}
        </>}
      </main>
    </section>
  </div>;
}
