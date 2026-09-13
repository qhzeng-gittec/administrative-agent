/**
 * Loop Engine（基于 pi Agent loop）：Observe → Reflect → Adapt。
 * Loop Agent 读取待处理反馈，归因并调用工具沉淀 Skill / Hook / Rule。
 */
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Config } from "./config.js";
import { runAgentJson, type AgentRuntime } from "./agents.js";

const LOOP_PROMPT = `你是"文枢"系统的 Loop 进化智能体（反馈驱动的自优化循环）。

按以下步骤执行：
1. 调用 list_pending_feedback 工具读取待处理反馈。
2. 分析 bad case（signal 为 down/correction/verifier_fail 的反馈），归因到：retrieval（检索）、intent（意图）、generation（生成）、knowledge_gap（知识缺口）。
3. 对高频可复用模式，通过工具沉淀：
   - save_skill：某类问题反复出现且有稳定解法 → 沉淀为 Skill
   - save_hook：特定条件下触发额外动作 → 沉淀为 Hook
   - save_rule：必须遵守的硬约束 → 沉淀为 Rule
4. 最终输出 JSON 摘要：{"observed":0,"bad_cases":0,"root_causes":{},"adaptations":[{"type":"skill|hook|rule","name":"..."}]}`;

function pickTools(tools: AgentTool[], names: string[]): AgentTool[] {
  return tools.filter((t) => names.includes(t.name));
}

export async function runLoop(
  runtime: AgentRuntime,
  config: Config,
): Promise<unknown> {
  if (!config.loopEnabled) {
    return { skipped: true, reason: "loop disabled" };
  }
  const tools = pickTools(runtime.tools, [
    "list_pending_feedback",
    "save_skill",
    "save_hook",
    "save_rule",
  ]);
  try {
    return await runAgentJson(runtime, LOOP_PROMPT, "请执行一次 Loop 进化循环。", tools);
  } catch (err) {
    console.warn("Loop 执行失败:", err);
    return { observed: 0, bad_cases: 0, root_causes: {}, adaptations: [], error: String(err) };
  }
}
