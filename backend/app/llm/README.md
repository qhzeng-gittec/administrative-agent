# LLM 客户端层

统一 OpenAI 兼容接口（httpx 异步），区分 DeepSeek 与中转站。

## 文件

- `client.py` —— `LLMClient` 基类：`complete` / `complete_json` / `stream`（SSE）
- `deepseek.py` —— DeepSeek 主力对话模型（`deepseek-v4-flash`）
- `relay.py` —— 中转站客户端（OpenAI 兼容，含 `/embeddings`）
- `embeddings.py` —— 向量模型（relay `text-embedding-3-large` / 本地 / 确定性 hash 回退）

## 约定

- 对话模型：DeepSeek（`DEEPSEEK_*` 环境变量）
- Embedding 与 bge reranker：中转站（`RELAY_*`，OpenAI 兼容）；该中转站不支持 bge-m3 embedding。
- 未配置 Key 时快速失败（抛 `LLMError`），由上层 Agent 降级，不发起网络请求
