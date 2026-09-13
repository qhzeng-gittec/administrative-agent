/**
 * Fastify HTTP 服务：对外暴露 /answer 与 /loop/run，供 Python 后端（或前端）调用。
 */
import Fastify, { type FastifyInstance } from "fastify";
import type { Config } from "./config.js";
import type { Runtime } from "./runtime.js";
import { answer } from "./orchestrator.js";
import { runLoop } from "./loop.js";
import { executeAgent, type AgentType } from "./agents.js";
import { timingSafeEqual } from "node:crypto";

const AGENT_TYPES = new Set<AgentType>(["intent", "rewrite", "answer", "verify", "reflect"]);
const TOOL_NAMES = new Set([
  "retrieve_documents", "lookup_calendar", "list_departments", "get_glossary",
  "submit_feedback", "list_pending_feedback", "save_skill", "save_hook", "save_rule",
]);

function internalAuthorized(config: Config, header: string | string[] | undefined): boolean {
  if (!config.internalApiToken || typeof header !== "string") return false;
  // 长度不同直接失败；长度相同再用 timingSafeEqual，避免共享令牌时序泄漏。
  const expected = Buffer.from(config.internalApiToken);
  const supplied = Buffer.from(header);
  return expected.length === supplied.length && timingSafeEqual(expected, supplied);
}

export function buildApp(config: Config, runtime: Runtime): FastifyInstance {
  const app = Fastify({ logger: false });

  app.get("/health", async () => ({ status: "ok", service: "wenshu-pi-agent" }));

  app.post("/v1/agent/run", async (req, reply) => {
    if (!internalAuthorized(config, req.headers["x-internal-token"])) {
      return reply.code(config.internalApiToken ? 401 : 503).send({
        code: 1, message: config.internalApiToken ? "内部服务令牌无效" : "内部服务令牌未配置",
      });
    }
    const body = (req.body ?? {}) as {
      agentType?: AgentType;
      systemPrompt?: string;
      prompt?: string;
      outputMode?: "text" | "json";
      allowedTools?: string[];
      timeoutMs?: number;
      traceId?: string;
    };
    if (!body.agentType || !AGENT_TYPES.has(body.agentType)) {
      return reply.code(400).send({ code: 1, message: "agentType 非法" });
    }
    if (!body.systemPrompt || !body.prompt) {
      return reply.code(400).send({ code: 1, message: "systemPrompt 和 prompt 必填" });
    }
    if (body.systemPrompt.length > 32_000 || body.prompt.length > 128_000) {
      return reply.code(413).send({ code: 1, message: "Agent prompt 超出大小限制" });
    }
    const outputMode = body.outputMode === "text" ? "text" : "json";
    const allowedTools = (body.allowedTools ?? []).filter((name) => TOOL_NAMES.has(name));
    const timeoutMs = Math.max(250, Math.min(Number(body.timeoutMs ?? 30_000), 120_000));
    try {
      const execution = executeAgent(
        runtime.runtime, body.agentType, body.systemPrompt, body.prompt, outputMode, allowedTools,
      );
      let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
      const timeout = new Promise<never>((_, reject) => {
        timeoutHandle = setTimeout(() => reject(new Error(`pi agent timeout after ${timeoutMs}ms`)), timeoutMs);
      });
      const result = await Promise.race([execution, timeout]).finally(() => {
        if (timeoutHandle) clearTimeout(timeoutHandle);
      });
      return {
        code: 0, message: "ok",
        data: { ...result, traceId: body.traceId ?? "", allowedTools },
      };
    } catch (err) {
      req.log.error(err);
      return reply.code(502).send({ code: 1, message: String(err) });
    }
  });

  app.post("/answer", async (req, reply) => {
    if (!internalAuthorized(config, req.headers["x-internal-token"])) {
      return reply.code(config.internalApiToken ? 401 : 503).send({ code: 1, message: "内部服务未授权" });
    }
    const body = (req.body ?? {}) as {
      query?: string;
      sessionId?: string;
      userId?: string;
      deptIds?: string[] | null;
    };
    if (!body.query) {
      return reply.code(400).send({ code: 1, message: "query 必填" });
    }
    try {
      const result = await answer(runtime.runtime, config, {
        query: body.query,
        sessionId: body.sessionId,
        userId: body.userId ?? "anonymous",
        deptIds: body.deptIds ?? null,
      });
      console.log(`[pi-agent] /answer handled: "${body.query.slice(0, 40)}" -> ${result.answer.length} chars, intent=${result.intentType}`);
      return { code: 0, message: "ok", data: result };
    } catch (err) {
      req.log.error(err);
      return reply.code(500).send({ code: 1, message: String(err) });
    }
  });

  app.post("/loop/run", async (req, reply) => {
    if (!internalAuthorized(config, req.headers["x-internal-token"])) {
      return reply.code(config.internalApiToken ? 401 : 503).send({ code: 1, message: "内部服务未授权" });
    }
    const data = await runLoop(runtime.runtime, config);
    return { code: 0, message: "ok", data };
  });

  return app;
}
