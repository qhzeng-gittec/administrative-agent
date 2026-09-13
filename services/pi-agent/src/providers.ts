/**
 * 模型提供方装配：基于 @earendil-works/pi-ai 的统一 LLM API。
 *
 * - deepseek：主力对话模型（OpenAI 兼容，https://api.deepseek.com）
 * - relay   ：中转站（OpenAI 兼容，https://yunwu.ai/v1），用于非 DeepSeek 模型
 */
import {
  createModels,
  createProvider,
  envApiKeyAuth,
  type Model,
  type Models,
} from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import type { Config } from "./config.js";

/** 构造一个 OpenAI-completions 兼容模型描述。 */
function openAIModel(
  provider: string,
  id: string,
  name: string,
  baseUrl: string,
  extraCompat: Record<string, unknown> = {},
): Model<"openai-completions"> {
  return {
    id,
    name,
    api: "openai-completions",
    provider,
    baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 32000,
    // 非标准 OpenAI 服务：使用 system 角色、不传 reasoning_effort/store
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      supportsStore: false,
      ...extraCompat,
    },
  };
}

export function buildModels(config: Config): Models {
  const models = createModels();

  models.setProvider(
    createProvider({
      id: "deepseek",
      name: "DeepSeek",
      baseUrl: config.deepseekBaseUrl,
      auth: { apiKey: envApiKeyAuth("DeepSeek API key", ["DEEPSEEK_API_KEY"]) },
      models: [
        openAIModel("deepseek", config.deepseekModel, "DeepSeek 对话", config.deepseekBaseUrl),
      ],
      api: openAICompletionsApi(),
    }),
  );

  models.setProvider(
    createProvider({
      id: "relay",
      name: "中转站",
      baseUrl: config.relayBaseUrl,
      auth: { apiKey: envApiKeyAuth("中转站 API key", ["RELAY_API_KEY"]) },
      models: [
        openAIModel("relay", config.relayModel, "中转站对话", config.relayBaseUrl, {
          supportsUsageInStreaming: false,
        }),
      ],
      api: openAICompletionsApi(),
    }),
  );

  return models;
}

/** 获取默认对话模型（DeepSeek）。 */
export function getMainModel(models: Models, config: Config): Model<"openai-completions"> {
  const m = models.getModel("deepseek", config.deepseekModel);
  if (!m) throw new Error(`未找到 DeepSeek 模型: ${config.deepseekModel}`);
  return m as Model<"openai-completions">;
}
