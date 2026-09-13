"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listReviewOrders,
  submitReview,
  type Dashboard,
  type ReviewOrder,
} from "@/lib/api";
import styles from "./admin.module.css";
import { phaseBadgeClass, phaseLabel } from "./labels";

type VerdictMap = Record<number, { correct: boolean; correction: string }>;

export default function ReviewPanel({ data, refresh }: { data: Dashboard; refresh: () => void }) {
  const [deptId, setDeptId] = useState("");
  const [status, setStatus] = useState("");
  const [orders, setOrders] = useState<ReviewOrder[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [verdicts, setVerdicts] = useState<Record<string, VerdictMap>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      setOrders(await listReviewOrders(deptId || undefined, status || undefined));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  }, [deptId, status]);

  useEffect(() => {
    load();
  }, [load]);

  const depts = data.departments || [];

  function setVerdict(orderId: string, index: number, correct: boolean) {
    setVerdicts((prev) => {
      const cur = prev[orderId] || {};
      return { ...prev, [orderId]: { ...cur, [index]: { correct, correction: cur[index]?.correction || "" } } };
    });
  }
  function setCorrection(orderId: string, index: number, correction: string) {
    setVerdicts((prev) => {
      const cur = prev[orderId] || {};
      return { ...prev, [orderId]: { ...cur, [index]: { correct: cur[index]?.correct ?? false, correction } } };
    });
  }

  async function onSubmit(order: ReviewOrder) {
    setBusy(true);
    setMsg("");
    setErr("");
    try {
      const vmap = verdicts[order._id] || {};
      if (Object.keys(vmap).length !== order.qa_pairs.length) {
        setErr(`请逐题选择“正确”或“错误”（已审核 ${Object.keys(vmap).length}/${order.qa_pairs.length}）`);
        return;
      }
      const list = order.qa_pairs.map((_, i) => ({
        index: i,
        correct: vmap[i].correct,
        correction: vmap[i]?.correction || "",
      }));
      const res = await submitReview(order._id, list);
      setMsg(
        `审核完成：${res.correct}/${res.total} 正确（准确率 ${Math.round((res.accuracy ?? 0) * 100)}%）。部门 Loop 阶段已按正确率自动推进。`
      );
      setExpanded(null);
      await load();
      refresh();
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  const pendingCount = orders.filter((o) => o.status === "pending").length;

  return (
    <div>
      <h2 className={styles.title}>
        审核中心 <span className={styles.muted}>（新文档自动出题 → 系统作答 → 人工审核 → 积累反馈）</span>
      </h2>

      <div className={styles.row} style={{ marginBottom: 16 }}>
        <select className={styles.select} value={deptId} onChange={(e) => setDeptId(e.target.value)}>
          <option value="">全部部门</option>
          {depts.map((d) => (
            <option key={d._id} value={d._id}>
              {d.name}
            </option>
          ))}
        </select>
        <select className={styles.select} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="pending">待审核</option>
          <option value="reviewed">已审核</option>
          <option value="auto_approved">自动通过（环外）</option>
        </select>
        <span className={styles.muted}>待审核 {pendingCount} 单</span>
      </div>

      {msg && <div style={{ marginBottom: 12, color: "#15803d", fontSize: 13 }}>{msg}</div>}
      {err && <div className={styles.error} style={{ marginBottom: 12 }}>{err}</div>}

      {orders.length === 0 ? (
        <div className={styles.empty}>暂无审核单。上传新文档后系统会自动生成审核单。</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {orders.map((order) => {
            const open = expanded === order._id;
            return (
              <div key={order._id} className={styles.card}>
                <div className={styles.rowBetween}>
                  <div>
                    <h3 style={{ marginBottom: 4 }}>{order.doc_title}</h3>
                    <div className={styles.muted}>
                      {order._id} · {order.dept_id} · {order.created_at?.slice(0, 19).replace("T", " ")}
                    </div>
                  </div>
                  <div className={styles.row}>
                    {order.accuracy != null && (
                      <span className={styles.badge + " " + styles.badgeBlue}>
                        准确率 {Math.round(order.accuracy * 100)}%
                      </span>
                    )}
                    <span
                      className={
                        styles.badge +
                        " " +
                        (order.status === "pending"
                          ? styles.badgeAmber
                          : order.status === "auto_approved"
                          ? styles.badgeGreen
                          : styles.badgeBlue)
                      }
                    >
                      {order.status === "pending"
                        ? "待审核"
                        : order.status === "reviewed"
                        ? "已审核"
                        : "自动通过"}
                    </span>
                    <button className={styles.btnGhost + " " + styles.btnSm} onClick={() => setExpanded(open ? null : order._id)}>
                      {open ? "收起" : "查看详情"}
                    </button>
                  </div>
                </div>

                {open && (
                  <div style={{ marginTop: 12 }}>
                    {order.qa_pairs.map((qa, i) => {
                      const v = (verdicts[order._id] || {})[i];
                      const correct = v?.correct;
                      const isReviewed = order.status !== "pending";
                      return (
                        <div key={i} className={styles.qablock}>
                          <div className={styles.qaQuestion}>
                            {i + 1}. {qa.question}
                          </div>
                          {qa.expected && (
                            <div className={styles.muted} style={{ marginBottom: 6 }}>
                              参考答案要点：{qa.expected}
                            </div>
                          )}
                          <div className={styles.qaAnswer}>系统回答：{qa.answer}</div>
                          {qa.citations?.length > 0 && (
                            <div className={styles.qaMeta}>
                              <b>引用：</b>
                              {qa.citations.map((c, j) => (
                                <span key={j} className={styles.citation}>
                                  [{c.chunk_index != null ? c.chunk_index + 1 : "?"}] {c.doc_title || c.doc_id}
                                  {c.section_path?.length ? " · " + c.section_path.join(" > ") : ""}
                                </span>
                              ))}
                            </div>
                          )}

                          {!isReviewed ? (
                            <>
                              <div className={styles.verdictRow}>
                                <button
                                  className={`${styles.verdictBtn} ${correct === true ? styles.correctSel : ""}`}
                                  onClick={() => setVerdict(order._id, i, true)}
                                >
                                  ✓ 正确
                                </button>
                                <button
                                  className={`${styles.verdictBtn} ${correct === false ? styles.incorrectSel : ""}`}
                                  onClick={() => setVerdict(order._id, i, false)}
                                >
                                  ✗ 错误
                                </button>
                                {correct === false && (
                                  <input
                                    className={styles.input}
                                    style={{ flex: 1 }}
                                    placeholder="填写纠正意见（将作为反馈沉淀）"
                                    value={v?.correction || ""}
                                    onChange={(e) => setCorrection(order._id, i, e.target.value)}
                                  />
                                )}
                              </div>
                            </>
                          ) : (
                            <div className={styles.qaMeta}>
                              审核结果：{qa.verdict === "rejected" ? "✗ 错误" : "✓ 正确"}
                              {qa.correction ? ` · 纠正：${qa.correction}` : ""}
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {order.status === "pending" && (
                      <div className={styles.row}>
                        <button className={styles.btn} disabled={busy} onClick={() => onSubmit(order)}>
                          {busy ? "提交中…" : "提交审核结果"}
                        </button>
                        <span className={styles.muted}>提交后写入题库并累计正确率，超过阈值将取消该部门人工审核。</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <h2 className={styles.title} style={{ marginTop: 24 }}>
        各部门审核统计与阶段
      </h2>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>部门</th>
            <th>阶段</th>
            <th>已审核样本</th>
            <th>正确</th>
            <th>正确率</th>
            <th>进度</th>
          </tr>
        </thead>
        <tbody>
          {depts.map((d) => {
            const s = d.review_stats || { total: 0, correct: 0, accuracy: 0 };
            const pct = Math.round((s.accuracy ?? 0) * 100);
            return (
              <tr key={d._id}>
                <td>{d.name}</td>
                <td>
                  <span className={styles.badge + " " + phaseBadgeClass(d.loop_phase)}>{phaseLabel(d.loop_phase)}</span>
                </td>
                <td>{s.total}</td>
                <td>{s.correct}</td>
                <td>{pct}%</td>
                <td>
                  <div className={styles.row}>
                    <div className={styles.progress}>
                      <div
                        className={`${styles.progressFill} ${pct >= 80 ? styles.progressFillGreen : styles.progressFillAmber}`}
                        style={{ width: Math.min(100, pct) + "%" }}
                      />
                    </div>
                    <span className={styles.muted}>80%</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
