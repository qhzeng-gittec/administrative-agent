"use client";

import type { Dashboard } from "@/lib/api";
import styles from "./admin.module.css";
import { PHASE_DESC, PHASE_LABEL, phaseBadgeClass, phaseLabel } from "./labels";

export default function OverviewPanel({
  data,
  refresh,
}: {
  data: Dashboard;
  refresh: () => void;
}) {
  const depts = data.departments || [];
  const threshold = data.thresholds?.accuracy ?? 0.8;
  const minSamples = data.thresholds?.min_samples ?? 5;

  return (
    <div>
      <div className={styles.grid} style={{ marginBottom: 16 }}>
        <div className={styles.card}>
          <div className={styles.metric}>
            <span className={styles.metricValue}>{depts.length}</span>
            <span className={styles.metricLabel}>部门数</span>
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.metric}>
            <span className={styles.metricValue}>{data.trace_count}</span>
            <span className={styles.metricLabel}>问答 Trace 数（Execute）</span>
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.metric}>
            <span className={styles.metricValue}>{data.pending_review_count}</span>
            <span className={styles.metricLabel}>待人工审核单</span>
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.metric}>
            <span className={styles.metricValue}>{data.test_question_count}</span>
            <span className={styles.metricLabel}>累计测试题（题库）</span>
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.metric}>
            <span className={styles.metricValue}>{data.skills?.length ?? 0}</span>
            <span className={styles.metricLabel}>Skills（已沉淀）</span>
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.metric}>
            <span className={styles.metricValue}>
              {data.feedback ? Math.round((data.feedback.adoption_rate ?? 0) * 100) + "%" : "—"}
            </span>
            <span className={styles.metricLabel}>回答采纳率（👍）</span>
          </div>
        </div>
      </div>

      <h2 className={styles.title}>Loop 渐进退出（Human Fade-out）</h2>
      <div className={styles.row} style={{ marginBottom: 12 }}>
        <span className={styles.badge + " " + styles.badgeBlue}>
          全局阶段：{phaseLabel(data.loop_phase_global)}
        </span>
        <span className={styles.muted}>
          退出阈值：正确率 ≥ {Math.round(threshold * 100)}% 且样本 ≥ {minSamples}
        </span>
        <button className={styles.btnGhost + " " + styles.btnSm} onClick={refresh}>
          刷新
        </button>
      </div>

      <div className={styles.grid2}>
        {depts.map((d) => {
          const stats = d.review_stats || { total: 0, correct: 0, accuracy: 0 };
          const acc = stats.accuracy ?? 0;
          const pct = Math.min(100, Math.round(acc * 100));
          const phase = d.loop_phase || "human_in_loop";
          return (
            <div key={d._id} className={styles.card}>
              <div className={styles.rowBetween}>
                <h3>{d.name}</h3>
                <span className={styles.badge + " " + phaseBadgeClass(phase)}>{phaseLabel(phase)}</span>
              </div>
              <div className={styles.muted} style={{ marginBottom: 8 }}>
                正确率 {Math.round(acc * 100)}% · 样本 {stats.total} · 文档 {d.doc_count} · 冲突 {d.conflict_count}
              </div>
              <div className={styles.row}>
                <div className={styles.progress}>
                  <div
                    className={`${styles.progressFill} ${pct >= Math.round(threshold * 100) ? styles.progressFillGreen : styles.progressFillAmber}`}
                    style={{ width: pct + "%" }}
                  />
                </div>
                <span className={styles.muted}>{pct}%</span>
              </div>
              <div className={styles.muted} style={{ marginTop: 6 }}>
                目标 {Math.round(threshold * 100)}%
                {stats.total < minSamples ? ` · 还差 ${minSamples - stats.total} 个样本进入环外` : ""}
              </div>
              {d.fade_out && (
                <div className={styles.muted} style={{ marginTop: 6, color: "#15803d" }}>
                  ✅ 已于 {d.fade_out.achieved_at?.slice(0, 10)} 退出人工审核
                </div>
              )}
            </div>
          );
        })}
      </div>

      <h2 className={styles.title} style={{ marginTop: 24 }}>
        Loop 阶段说明
      </h2>
      <div className={styles.phaseRow}>
        {(["human_in_loop", "human_on_loop", "human_out_of_loop"] as const).map((p) => (
          <div key={p} className={`${styles.phaseCard} ${data.loop_phase_global === p ? styles.active : ""}`}>
            <div className={styles.phaseTitle}>
              {["Phase 1", "Phase 2", "Phase 3"][["human_in_loop", "human_on_loop", "human_out_of_loop"].indexOf(p)]}{" "}
              {PHASE_LABEL[p]}
            </div>
            <div className={styles.phaseDesc}>{PHASE_DESC[p]}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
