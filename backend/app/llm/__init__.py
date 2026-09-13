"""LLM 客户端层。

- DeepSeekClient：主力对话模型（https://api.deepseek.com）
- RelayClient：中转站（OpenAI 兼容，用于 bge 等非 DeepSeek 模型）
- EmbeddingClient：向量模型（默认经中转站调用 text-embedding-3-large）
"""
from app.llm.client import LLMClient, LLMError
from app.llm.deepseek import DeepSeekClient
from app.llm.relay import RelayClient
from app.llm.embeddings import EmbeddingClient

__all__ = [
    "LLMClient",
    "LLMError",
    "DeepSeekClient",
    "RelayClient",
    "EmbeddingClient",
]
