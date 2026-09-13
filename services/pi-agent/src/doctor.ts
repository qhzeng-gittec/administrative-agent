/**
 * pi 侧自检：验证 pi 框架 + DeepSeek 提供方能否通过 pi Agent loop 正常对话。
 *
 * 说明：
 * - DeepSeek 是主力对话模型（关键路径，决定退出码）。
 * - 中转站（yunwu）在系统中用于 Embedding / bge 重排，走 Python 后端直连 HTTP
 *   （见 backend/scripts/doctor.py，已单独验证）。此处仅作信息性探测。
 *
 * 用法：npm run doctor
 */
import { loadConfig } from "./config.js";
import { buildModels, getMainModel } from "./providers.js";
import { buildTools } from "./tools.js";
import { runAgent, type AgentRuntime } from "./agents.js";
import type { Model } from "@earendil-works/pi-ai";

interface DoctorResult {
  name: string;
  ok: boolean;
  critical: boolean;
  sample?: string;
  error?: string;
}

async function checkModel(
  name: string,
  model: Model<"openai-completions">,
  runtime: AgentRuntime,
  critical: boolean,
): Promise<DoctorResult> {
  try {
    const out = await runAgent({ ...runtime, model }, "你是助手，请只回复两个字：OK", "你好");
    return { name, ok: out.length > 0, critical, sample: out.slice(0, 80) };
  } catch (err) {
    return { name, ok: false, critical, error: String(err) };
  }
}

async function main(): Promise<void> {
  const config = loadConfig();
  const models = buildModels(config);
  const tools = buildTools(config);
  const runtime: AgentRuntime = {
    model: getMainModel(models, config),
    streamFn: models.streamSimple.bind(models),
    tools,
  };

  const results: DoctorResult[] = [];
  results.push(await checkModel("pi + DeepSeek Agent", getMainModel(models, config), runtime, true));

  const relay = models.getModel("relay", config.relayModel) as Model<"openai-completions"> | undefined;
  if (relay) {
    results.push(await checkModel("pi + 中转站 Agent(信息性)", relay, runtime, false));
  }

  console.log("=".repeat(60));
  console.log("pi-agent 自检");
  console.log("=".repeat(60));
  for (const r of results) {
    const mark = r.ok ? "PASS" : r.critical ? "FAIL" : "WARN";
    console.log(`[${mark}] ${r.name}`);
    if (r.ok) console.log(`       sample: ${r.sample}`);
    else console.log(`       ${r.error ? "error : " + r.error : "未获得输出"}`);
  }
  if (!relay) {
    console.log("[INFO] 中转站在本系统中用于 Embedding/bge 重排，经 Python 直连验证（backend/scripts/doctor.py）");
  }
  console.log("=".repeat(60));

  const criticalOk = results.filter((r) => r.critical).every((r) => r.ok);
  process.exit(criticalOk ? 0 : 1);
}

main();
