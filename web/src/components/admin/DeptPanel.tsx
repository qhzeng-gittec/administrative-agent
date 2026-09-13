"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createDepartment,
  getUploadJob,
  listDocuments,
  updateDocumentStatus,
  uploadDocument,
  type Dashboard,
  type Document,
  type User,
} from "@/lib/api";
import styles from "./admin.module.css";

const PIPELINE_ORDER = ["upload", "parse", "clean", "chunk", "metadata", "vectorize", "index", "relations"];

export default function DeptPanel({ data, refresh, user }: { data: Dashboard; refresh: () => void; user: User }) {
  const [deptId, setDeptId] = useState("");
  const [docs, setDocs] = useState<Document[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  // 新建部门表单
  const [nid, setNid] = useState("");
  const [nname, setNname] = useState("");
  const [ncat, setNcat] = useState("general");

  const loadDocs = useCallback(async () => {
    try {
      setDocs(await listDocuments(deptId || undefined));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  }, [deptId]);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !deptId) {
      setErr("请先选择部门并选择文件");
      return;
    }
    setBusy(true);
    setMsg("");
    setErr("");
    try {
      const res = await uploadDocument(file, deptId);
      setMsg(`《${res.file_name}》已进入异步入库队列，作业号 ${res.job_id}。`);
      for (let i = 0; i < 60; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const job = await getUploadJob(res.job_id);
        if (job.status === "completed") {
          setMsg(`文档入库完成：检测到 ${job.result?.relations ?? 0} 条跨部门关联，审核单已生成。`);
          break;
        }
        if (job.status === "failed") {
          throw new Error(job.result?.error || "异步入库失败");
        }
      }
      e.target.value = "";
      await loadDocs();
      refresh();
    } catch (ex) {
      setErr(String(ex instanceof Error ? ex.message : ex));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateDept() {
    if (!nid || !nname) return;
    setErr("");
    try {
      await createDepartment({ id: nid, name: nname, category: ncat });
      setNid("");
      setNname("");
      await refresh();
    } catch (ex) {
      setErr(String(ex instanceof Error ? ex.message : ex));
    }
  }

  async function toggleStatus(doc: Document, status: string) {
    try {
      await updateDocumentStatus(doc._id, status);
      await loadDocs();
      refresh();
    } catch (ex) {
      setErr(String(ex instanceof Error ? ex.message : ex));
    }
  }

  const depts = data.departments || [];
  const isSuper = !user.dept_id;

  return (
    <div>
      <div className={styles.sectionHead}><div><span className={styles.eyebrow}>KNOWLEDGE ASSETS</span><h2>部门与制度事实治理</h2><p>{isSuper ? "创建部门、导入制度并观察从解析到关系发现的完整链路。" : "当前空间只显示和操作本部门资产，跨部门数据由后端强制隔离。"}</p></div></div>
      <div className={styles.grid}>
        {isSuper && <div className={styles.card}>
          <h3>新建部门</h3>
          <div className={styles.row} style={{ marginBottom: 8 }}>
            <input className={styles.input} placeholder="ID（如 dept_xxx）" value={nid} onChange={(e) => setNid(e.target.value)} />
            <input className={styles.input} placeholder="名称（如 国际交流处）" value={nname} onChange={(e) => setNname(e.target.value)} />
          </div>
          <div className={styles.row}>
            <select className={styles.select} value={ncat} onChange={(e) => setNcat(e.target.value)}>
              <option value="general">通用</option>
              <option value="academic">教务</option>
              <option value="student">学生</option>
              <option value="finance">财务</option>
              <option value="admin">行政</option>
              <option value="logistics">后勤</option>
            </select>
            <button className={styles.btn} onClick={onCreateDept} disabled={!nid || !nname}>
              创建
            </button>
          </div>
        </div>}

        <div className={styles.card}>
          <h3>上传新文档（自动解析入库）</h3>
          <div className={styles.row} style={{ marginBottom: 8 }}>
            <select className={styles.select} value={deptId} onChange={(e) => setDeptId(e.target.value)}>
              <option value="">选择部门</option>
              {depts.map((d) => (
                <option key={d._id} value={d._id}>
                  {d.name}（{d._id}）
                </option>
              ))}
            </select>
          </div>
          <label className={styles.filePicker}><input type="file" accept=".pdf,.docx,.md,.txt,.html" onChange={onUpload} disabled={busy || !deptId} /><span>选择制度文件</span></label>
          <div className={styles.muted} style={{ marginTop: 8 }}>
            支持 PDF / Word / Markdown / TXT。上传后自动走 3.2 数据处理 Pipeline，并自动出题发起审核单。
          </div>
          {busy && <div className={styles.muted} style={{ marginTop: 8 }}>解析入库中…</div>}
        </div>
      </div>

      {msg && <div style={{ marginTop: 12, color: "#15803d", fontSize: 13 }}>{msg}</div>}
      {err && <div className={styles.error} style={{ marginTop: 12 }}>{err}</div>}

      <h2 className={styles.title} style={{ marginTop: 24 }}>
        文档列表（{deptId ? depts.find((d) => d._id === deptId)?.name || deptId : "全部"}）
      </h2>
      {docs.length === 0 ? (
        <div className={styles.empty}>暂无文档，请选择部门并上传</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {docs.map((doc) => (
            <div key={doc._id} className={styles.card}>
              <div className={styles.rowBetween}>
                <h3>{doc.title}</h3>
                <div className={styles.row}>
                  <span className={styles.badge + " " + (doc.status === "active" ? styles.badgeGreen : styles.badgeGray)}>
                    {doc.status}
                  </span>
                  <span className={styles.badge + " " + styles.badgeBlue}>{doc.doc_type || "other"}</span>
                </div>
              </div>
              <div className={styles.muted} style={{ marginBottom: 10 }}>
                {doc.source?.file_name || doc._id} · 版本 {doc.version || "1.0"} · {doc.chunk_count ?? 0} 切片 · 向量
                {doc.vector_status === "ready" ? "已就绪" : doc.vector_status}
              </div>

              <PipelineStages doc={doc} />

              <div className={styles.row} style={{ marginTop: 10 }}>
                {doc.status !== "archived" && (
                  <button className={styles.btnGhost + " " + styles.btnSm} onClick={() => toggleStatus(doc, "archived")}>
                    归档
                  </button>
                )}
                {doc.status !== "active" && (
                  <button className={styles.btnGhost + " " + styles.btnSm} onClick={() => toggleStatus(doc, "active")}>
                    恢复为 active
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PipelineStages({ doc }: { doc: Document }) {
  const stages = doc.pipeline_stages || [];
  // Production documents persist the final ingestion facts rather than every
  // transient stage. A ready vector plus stored chunks means the synchronous
  // pipeline and relation scan completed successfully.
  const completedFromFacts = doc.vector_status === "ready" && (doc.chunk_count ?? 0) > 0;
  const ordered = PIPELINE_ORDER.map((key) => {
    const s = stages.find((x) => x.key === key);
    return s || { key, name: key, done: completedFromFacts };
  });
  return (
    <div className={styles.row} style={{ gap: 6 }}>
      {ordered.map((s, i) => (
        <span key={s.key} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className={styles.badge + " " + (s.done ? styles.badgeGreen : styles.badgeGray)}>
            {s.name}
            {s.detail ? `（${s.detail}）` : ""}
          </span>
          {i < ordered.length - 1 && <span className={styles.muted}>→</span>}
        </span>
      ))}
    </div>
  );
}
