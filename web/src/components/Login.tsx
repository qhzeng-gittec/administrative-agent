"use client";

import { useState } from "react";
import { login, setToken, type User } from "@/lib/api";
import Icon from "./Icon";
import styles from "./Login.module.css";

const DEMOS = [
  { type: "团队成员", user: "employee", pass: "employee123", desc: "办理企业任务、查看进度与反馈", icon: "chat" as const },
  { type: "超级管理员", user: "admin", pass: "admin123", desc: "查看内部进化、能力版本与灰度", icon: "shield" as const },
];

export default function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(""); setBusy(true);
    try {
      const res = await login(username.trim(), password);
      setToken(res.token); onLogin(res.user);
    } catch (err) { setError(String(err instanceof Error ? err.message : err)); }
    finally { setBusy(false); }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.ambientOne} /><div className={styles.ambientTwo} />
      <section className={styles.story}>
        <div className={styles.wordmark}><span className={styles.seal}>桥</span><span>星桥 STARBRIDGE</span></div>
        <div className={styles.eyebrow}><span /> SELF-EVOLVING ENTERPRISE AGENT</div>
        <h1>让每次办理<br />成为下一次改进的证据。</h1>
        <p className={styles.lead}>协同 IT、人事行政和财务，从重复反馈中发现共同问题，用隔离评测决定是否采纳新策略。</p>
        <div className={styles.arch}>
          {[
            ["database", "任务证据", "政策、工单与执行记录"],
            ["brain", "处理经验", "按需加载可验证的策略"],
            ["loop", "对照实验", "验证、灰度与版本撤回"],
          ].map(([icon, title, text]) => <div key={title} className={styles.archItem}>
            <span className={styles.archIcon}><Icon name={icon as "database"} size={19} /></span>
            <div><b>{title}</b><small>{text}</small></div>
          </div>)}
        </div>
        <div className={styles.trust}><Icon name="shield" size={16} /> 企业任务模拟 · 反馈聚类 · 可追溯实验</div>
      </section>

      <section className={styles.loginSide}>
        <form className={styles.card} onSubmit={submit}>
          <div className={styles.mobileBrand}><span className={styles.seal}>桥</span> 星桥</div>
          <div className={styles.cardHead}>
            <span className={styles.kicker}>WELCOME BACK</span>
            <h2>进入企业 Agent 工作台</h2>
            <p>系统会依据账号权限进入对应工作台</p>
          </div>
          <label className={styles.field}><span>账号</span><div className={styles.inputWrap}><Icon name="agent" size={17}/><input value={username} onChange={e => setUsername(e.target.value)} placeholder="请输入账号" autoFocus /></div></label>
          <label className={styles.field}><span>密码</span><div className={styles.inputWrap}><Icon name="shield" size={17}/><input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="请输入密码" /></div></label>
          {error && <div className={styles.error}>{error}</div>}
          <button className={styles.submit} type="submit" disabled={busy}>{busy ? <><span className={styles.spinner}/>正在验证</> : <>安全登录 <Icon name="arrow" size={17}/></>}</button>
          <div className={styles.divider}><span>演示身份快速进入</span></div>
          <div className={styles.demoList}>{DEMOS.map(d => <button key={d.user} type="button" className={styles.demo} onClick={() => { setUsername(d.user); setPassword(d.pass); setError(""); }}>
            <span className={styles.demoIcon}><Icon name={d.icon} size={17}/></span><span><b>{d.type}</b><small>{d.desc}</small></span><code>{d.user}</code>
          </button>)}</div>
          <div className={styles.security}><Icon name="shield" size={14}/> 本地演示环境 · Token 带有效期 · 操作按角色隔离</div>
        </form>
      </section>
    </div>
  );
}
