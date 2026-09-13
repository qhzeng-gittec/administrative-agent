"""pytest 根配置：强制离线（memory 存储 + hash 向量），保证测试无需外部服务。"""
from __future__ import annotations

import os
import sys

# 保证 backend/ 可导入 app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 必须在导入 app 之前设置
os.environ.setdefault("STORAGE_MODE", "memory")
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("EMBEDDING_DIM", "128")
os.environ.setdefault("LOOP_ENABLED", "true")
os.environ.setdefault("LOOP_PHASE", "human_on_loop")
os.environ.setdefault("PI_AGENT_ENABLED", "false")
os.environ.setdefault("RERANKER_ENABLED", "false")
os.environ.setdefault("DEEPSEEK_API_KEY", "")
os.environ.setdefault("RELAY_API_KEY", "")
