"""集中配置：从环境变量 / .env 读取。

DeepSeek 为主力对话模型；中转站（OpenAI 兼容）用于 bge 等非 DeepSeek 模型。
"""
from __future__ import annotations

from functools import lru_cache
from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 服务 ----
    app_name: str = "星桥 · 企业 Agent 与策略进化"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    feedback_model: str = "qwen/qwen3-8b"
    enterprise_model: str = "deepseek/deepseek-v3.2"
    mining_embedding_model: str = "qwen/qwen3-embedding-8b"
    mining_interval_seconds: int = 300
    mining_min_signals: int = 3
    mining_cluster_eps: float = 0.20
    evolution_judge_model: str = "deepseek/deepseek-v3.2"
    evolution_generated_cases: int = Field(default=4, ge=1, le=8)
    evolution_max_candidates: int = Field(default=3, ge=1, le=8)
    evolution_author_rounds: int = Field(default=16, ge=1, le=64)
    evolution_min_pairs: int = Field(default=4, ge=1)
    evolution_trial_samples: int = Field(default=20, ge=2)
    evolution_trial_days: int = Field(default=14, ge=1)
    evolution_gray_percent: float = Field(default=0.1, gt=0, le=1)
    support_model: str = "deepseek-flash"
    support_data_dir: str = "data/support"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    # 显式列出允许的前端来源（生产替换为实际域名）；带凭据请求不允许通配符
    cors_origins: str = "http://localhost:3000,http://localhost:8080"
    api_prefix: str = "/api/v1"

    # ---- DeepSeek（主力对话模型） ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_temperature: float = 0.1
    deepseek_max_tokens: int = 2048
    deepseek_timeout: float = 60.0

    # ---- 中转站（非 DeepSeek 模型，OpenAI 兼容） ----
    relay_api_key: str = ""
    relay_base_url: str = "https://yunwu.ai/v1"
    relay_model: str = "gpt-5.5"

    # ---- Embedding ----
    # provider: relay | local | hash
    # 注意：该中转站不提供 bge-m3 embedding，实测可用 text-embedding-3-large(3072)
    embedding_provider: str = "relay"
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 3072

    # ---- 存储 ----
    storage_mode: str = "mongo"  # mongo | memory
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "wenshu"
    redis_addr: str = "redis://localhost:6379"
    redis_db: int = 0
    async_stream_name: str = "wenshu:jobs"
    upload_storage_dir: str = "/tmp/wenshu-uploads"

    # ---- 检索 ----
    vector_backend: str = "memory"  # memory | mongo | chroma | milvus
    hybrid_topk: int = 5
    bm25_top: int = 20
    vector_top: int = 20
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # ---- pi 智能体服务（Harness + Loop 深度整合） ----
    # Python Harness 是唯一生产问答运行时；pi-agent 保留为实验服务，默认不接主链。
    pi_agent_enabled: bool = True
    pi_agent_url: str = "http://localhost:8100"
    pi_agent_timeout: float = 120.0
    pi_runtime_timeout_intent: float = 8.0
    pi_runtime_timeout_rewrite: float = 10.0
    pi_runtime_timeout_answer: float = 45.0
    pi_runtime_timeout_verify: float = 20.0
    pi_runtime_timeout_reflect: float = 45.0

    # ---- 部门 Agent 服务发现 ----
    dept_id: str = ""
    dept_agents_enabled: bool = False
    dept_agent_url_template: str = "http://dept-agent-{slug}:8000"
    dept_agent_timeout: float = 6.0
    dept_agent_partial_timeout: float = 2.0

    # ---- Loop 进化 ----
    loop_enabled: bool = True
    skill_min_cluster: int = 20      # 7 天窗口内同模式问题最小次数
    skill_sandbox_min_success: float = 0.85
    hook_high_confidence: float = 0.9  # 高置信度自动生效阈值
    loop_phase: str = "human_in_loop"  # human_in_loop | human_on_loop | human_out_of_loop（默认从 Phase 1 起步）
    loop_gray_percent: float = 0.1
    loop_rollback_min_samples: int = 10
    loop_rollback_margin: float = 0.1
    review_sample_rate: float = 0.1

    # ---- 记忆治理（事实平面 + 五个记忆平面）----
    memory_session_ttl_seconds: int = 1800
    memory_event_retention_days: int = 90
    memory_summary_retention_days: int = 180
    memory_user_retention_days: int = 180
    memory_topic_retention_days: int = 90
    memory_max_recent_messages: int = 10
    memory_context_max_chars: int = 6000
    memory_user_limit: int = 8
    memory_org_limit: int = 8

    # ---- 鉴权 ----
    auth_secret: str = "wenshu-dev-secret-change-me"
    auth_token_ttl_hours: int = 24
    # 内部接口（/internal/*）共享 Token：为空则内部接口不可用（fail-closed）
    internal_api_token: str = ""
    # 演示种子账号开关：生产务必 SEED_DEMO_USERS=false（并删除已创建的演示账号）
    seed_demo_users: bool = True
    # 登录限流（内存版，单进程有效）
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    # ---- 上传限制 ----
    max_upload_mb: int = 20

    # ---- 人工审核 Loop（部门渐进退出）----
    review_question_count: int = 3       # 新文档入库后自动生成的测试题数量
    review_accuracy_threshold: float = 0.8   # 正确率超过该阈值后取消人工审核
    review_min_samples: int = 5          # 达到阈值所需的最小审核样本数

    # ---- 超时（全链路） ----
    timeout_intent: float = 1.0
    timeout_retrieval: float = 2.0
    timeout_answer: float = 5.0
    timeout_verify: float = 3.0

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
