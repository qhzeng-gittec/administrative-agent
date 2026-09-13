/**
 * 多智能体定义（基于 pi Agent + tool calling）。
 * 每个 Agent 是一个 pi Agent 实例：systemPrompt + tools + agent loop（模型自主决定调用工具）。
 */
import { Agent, type AgentTool } from "@earendil-works/pi-agent-core";
import type { Model } from "@earendil-works/pi-ai";
import { buildTools } from "./tools.js";
import type { Config } from "./config.js";

export interface AgentRuntime {
  model: Model<"openai-completions">;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  streamFn: any; // models.streamSimple.bind(models)，第三方类型边界
  tools: AgentTool[];
}

export type AgentType = "intent" | "rewrite" | "answer" | "verify" | "reflect";

export interface AgentExecutionResult {
  agentType: AgentType;
  output: string | unknown;
  outputMode: "text" | "json";
  latencyMs: number;
}

/** 运行一个 pi Agent，收集最终文本输出（累积流式 text_delta）。 */
export async function runAgent(
  runtime: AgentRuntime,
  systemPrompt: string,
  prompt: string,
  tools: AgentTool[] = [],
): Promise<string> {
  const agent = new Agent({
    initialState: {
      systemPrompt,
      model: runtime.model,
      tools,
    },
    streamFn: runtime.streamFn,
  });

  let text = "";
  const unsubscribe = agent.subscribe((event) => {
    if (
      event.type === "message_update" &&
      event.assistantMessageEvent.type === "text_delta"
    ) {
      text += event.assistantMessageEvent.delta;
    }
  });

  await agent.prompt(prompt);
  unsubscribe();
  const result = text.trim();
  if (!result && agent.state.errorMessage) {
    throw new Error(`agent 出错: ${agent.state.errorMessage}`);
  }
  return result;
}

/** 运行并解析 JSON 输出（容忍代码块围栏）。 */
export async function runAgentJson(
  runtime: AgentRuntime,
  systemPrompt: string,
  prompt: string,
  tools: AgentTool[] = [],
): Promise<unknown> {
  const raw = await runAgent(runtime, systemPrompt, prompt, tools);
  return extractJson(raw);
}

/**
 * 统一概率性 Agent 执行入口。Python 控制平面传入已经过权限、记忆与事实治理的
 * prompt 和 allowedTools；pi 只负责 Agent loop / tool calling / 模型执行。
 */
export async function executeAgent(
  runtime: AgentRuntime,
  agentType: AgentType,
  systemPrompt: string,
  prompt: string,
  outputMode: "text" | "json",
  allowedTools: string[] = [],
): Promise<AgentExecutionResult> {
  const started = Date.now();
  const tools = runtime.tools.filter((tool) => allowedTools.includes(tool.name));
  const output = outputMode === "json"
    ? await runAgentJson(runtime, systemPrompt, prompt, tools)
    : await runAgent(runtime, systemPrompt, prompt, tools);
  return { agentType, output, outputMode, latencyMs: Date.now() - started };
}

export function extractJson(text: string): unknown {
  let s = text.trim();
  if (s.startsWith("```")) {
    s = s.replace(/^```[a-zA-Z]*\s*/, "").replace(/```\s*$/, "");
  }
  const start = Math.min(
    ...[s.indexOf("{"), s.indexOf("[")].filter((i) => i >= 0),
  );
  const end = Math.max(s.lastIndexOf("}"), s.lastIndexOf("]"));
  if (start < 0 || end < 0 || end <= start) {
    throw new Error(`无法解析 JSON: ${s.slice(0, 200)}`);
  }
  return JSON.parse(s.slice(start, end + 1));
}

// ---------------------------------------------------------------------------
// 各 Agent 的 systemPrompt
// ---------------------------------------------------------------------------

export const INTENT_PROMPT = `你是"文枢"制度问答系统的意图识别智能体。
先调用 list_departments 工具获取有效部门 id，再判断用户问题的意图类型、涉及部门、用户身份、是否需要跨部门协同。
最终只输出 JSON（不要多余解释）：
{"type":"regulation_consult|process_guide|deadline_query|complaint|chitchat|other","depts":["dept_id"],"user_role":"student|teacher|admin","entities":{},"needs_cross_dept":false,"confidence":0.0}`;

export const REWRITER_PROMPT = `你是查询改写智能体。将用户问题改写为 1-3 个更适合检索的 query（补全省略、术语标准化）。
可调用 get_glossary 工具获取术语表。最终只输出 JSON：
{"queries":["query1","query2"]}`;

export const ANSWER_PROMPT = `你是学校制度咨询助手"文枢"。基于给定的制度条款回答用户问题。
【必须遵守的规则】
- 所有回答必须附带来源条款引用（以 [来源N] 形式标注）。
- 只依据条款回答，不得编造；条款中无明确答案时必须说"根据现有制度文件未找到明确规定"。
- 涉及截止时间/日期的问题，可调用 lookup_calendar 工具查询校历。
- 若给定条款不足以回答，可调用 retrieve_documents 工具补充检索。

【参考条款】
{chunks}

请用简洁准确的中文回答。`;

export const VERIFIER_PROMPT = `你是答案校验智能体。检查答案是否可靠，只输出 JSON：
{"passed":true/false,"score":0.0-1.0,"issues":["问题"]}
检查项：① 关键结论是否有条款支撑 ② 是否与原文矛盾 ③ 是否遗漏关键信息 ④ 引用格式是否正确。`;
