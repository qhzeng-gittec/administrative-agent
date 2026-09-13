/**
 * Agent 工具集：pi Agent 通过工具调用（function calling）访问 Python 后端的数据与检索能力。
 */
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";
import type { Config } from "./config.js";
import {
  getCalendar,
  getGlossary,
  listDepartments,
  listPendingFeedback,
  retrieveChunks,
  saveArtifact,
  submitFeedback,
} from "./backend.js";

function textResult(text: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text }], details };
}

interface RetrieveParams {
  query: string;
  dept_ids?: string[];
  top_k?: number;
}
interface FeedbackParams {
  query: string;
  answer: string;
  signal: string;
}

export function buildTools(config: Config): AgentTool[] {
  return [
    {
      name: "retrieve_documents",
      label: "检索制度文档",
      description: "混合检索（BM25+向量+重排）制度条款 chunk，返回与问题相关的原文片段。",
      parameters: Type.Object({
        query: Type.String({ description: "检索查询" }),
        dept_ids: Type.Optional(Type.Array(Type.String())),
        top_k: Type.Optional(Type.Number({ default: 5 })),
      }),
      execute: async (_id, rawParams) => {
        const params = rawParams as RetrieveParams;
        const chunks = await retrieveChunks(config, params.query, params.dept_ids ?? null, params.top_k ?? 5);
        const text = chunks.map((c, i) => `[来源${i + 1}] ${c.content ?? ""}`).join("\n\n");
        return textResult(text || "未检索到相关条款", { count: chunks.length });
      },
    },
    {
      name: "lookup_calendar",
      label: "查询校历",
      description: "查询当前学期校历（开学/放假/选课周等时间节点）。",
      parameters: Type.Object({}),
      execute: async () => {
        const data = await getCalendar(config);
        return textResult(JSON.stringify(data, null, 2), {});
      },
    },
    {
      name: "list_departments",
      label: "列出部门",
      description: "列出所有部门及其 id（用于判断问题涉及的部门）。",
      parameters: Type.Object({}),
      execute: async () => {
        const depts = await listDepartments(config);
        return textResult(depts.map((d) => `${d._id}(${d.name ?? ""})`).join(", "), { count: depts.length });
      },
    },
    {
      name: "get_glossary",
      label: "查询术语表",
      description: "查询同义词/术语映射表，用于查询改写与标准化。",
      parameters: Type.Object({}),
      execute: async () => {
        const data = await getGlossary(config);
        return textResult(JSON.stringify(data, null, 2), {});
      },
    },
    {
      name: "submit_feedback",
      label: "提交反馈",
      description: "将用户反馈（点赞/点踩/纠错）写入反馈队列供 Loop 消费。",
      parameters: Type.Object({
        query: Type.String(),
        answer: Type.String(),
        signal: Type.String({ description: "up | down | correction" }),
      }),
      execute: async (_id, rawParams) => {
        await submitFeedback(config, rawParams as FeedbackParams);
        return textResult("反馈已提交", {});
      },
    },
    {
      name: "list_pending_feedback",
      label: "读取待处理反馈",
      description: "读取尚未被 Loop 消费的反馈信号。",
      parameters: Type.Object({}),
      execute: async () => {
        const data = await listPendingFeedback(config);
        return textResult(JSON.stringify(data, null, 2), {});
      },
    },
    {
      name: "save_skill",
      label: "保存 Skill",
      description: "将自动挖掘的 Skill 草稿写入存储（待审核或自动生效）。",
      parameters: Type.Object({
        name: Type.String(),
        trigger: Type.String({ description: "触发条件描述" }),
        steps: Type.String({ description: "执行步骤描述" }),
        confidence: Type.Optional(Type.Number()),
      }),
      execute: async (_id, rawParams) => {
        const data = await saveArtifact(config, { type: "skill", ...(rawParams as Record<string, unknown>) });
        return textResult(JSON.stringify(data, null, 2), {});
      },
    },
    {
      name: "save_hook",
      label: "保存 Hook",
      description: "将自动挖掘的 Hook 草稿写入存储。",
      parameters: Type.Object({
        name: Type.String(),
        trigger: Type.String({ description: "触发条件描述" }),
        action: Type.String({ description: "触发动作描述" }),
        confidence: Type.Optional(Type.Number()),
      }),
      execute: async (_id, rawParams) => {
        const data = await saveArtifact(config, { type: "hook", ...(rawParams as Record<string, unknown>) });
        return textResult(JSON.stringify(data, null, 2), {});
      },
    },
    {
      name: "save_rule",
      label: "保存 Rule",
      description: "将自动归纳的 Rule 写入存储。",
      parameters: Type.Object({
        name: Type.String(),
        content: Type.String({ description: "规则内容" }),
        confidence: Type.Optional(Type.Number()),
      }),
      execute: async (_id, rawParams) => {
        const data = await saveArtifact(config, { type: "rule", ...(rawParams as Record<string, unknown>) });
        return textResult(JSON.stringify(data, null, 2), {});
      },
    },
  ];
}
