"""环境自检脚本（doctor）：验证 DeepSeek 与中转站（bge 等非 DeepSeek 模型）能否成功调用。

用法：
    cd backend
    python -m scripts.doctor          # 使用 .env / 环境变量中的密钥
    python -m scripts.doctor --json   # 输出 JSON 结果（供 CI / 前端展示）

检查项：
    1. DeepSeek 对话模型（默认 deepseek-v4-flash）
    2. 中转站对话模型（默认 gpt-5.5-pro，OpenAI 兼容）
    3. 中转站 Embedding 模型（默认 bge-m3，/embeddings）

退出码：全部通过返回 0，任一失败返回 1。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# 默认配置（与 .env 一致；经 doctor 实测校验过的可用模型名）
DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_RELAY_BASE = "https://yunwu.ai/v1"
DEFAULT_RELAY_MODEL = "gpt-5.5"                     # 中转站对话模型（gpt-5.5-pro 无效）
DEFAULT_EMBED_MODEL = "text-embedding-3-large"       # 中转站 Embedding（bge-m3 在该站不可用）
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"     # 中转站 bge 重排模型（bge 系列）


def _load_env() -> None:
    """加载 program/.env 或 backend/.env（不覆盖已存在的环境变量）。"""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",          # 从 backend/ 运行时读 program/.env
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        return


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def check_chat(base_url: str, api_key: str, model: str, name: str, timeout: float = 30.0) -> dict[str, Any]:
    """检查对话模型 /chat/completions。"""
    started = time.perf_counter()
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "请用一句话介绍你自己。"}],
        "temperature": 0.1,
        "max_tokens": 64,
        "stream": False,
    }
    result = {"name": name, "kind": "chat", "model": model, "url": url, "ok": False}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=_headers(api_key), json=payload)
        latency = round((time.perf_counter() - started) * 1000, 1)
        result["latency_ms"] = latency
        result["status"] = resp.status_code
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            result["ok"] = True
            result["sample"] = content[:120]
        else:
            result["error"] = resp.text[:500]
    except httpx.HTTPError as exc:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def check_embedding(base_url: str, api_key: str, model: str, name: str, timeout: float = 30.0) -> dict[str, Any]:
    """检查 Embedding 模型 /embeddings（bge-m3 等）。"""
    started = time.perf_counter()
    url = base_url.rstrip("/") + "/embeddings"
    payload = {"model": model, "input": ["跨部门文档问答助手"]}
    result = {"name": name, "kind": "embedding", "model": model, "url": url, "ok": False}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=_headers(api_key), json=payload)
        latency = round((time.perf_counter() - started) * 1000, 1)
        result["latency_ms"] = latency
        result["status"] = resp.status_code
        if resp.status_code == 200:
            data = resp.json()
            vec = data["data"][0]["embedding"]
            result["ok"] = True
            result["dim"] = len(vec)
            result["sample"] = f"dim={len(vec)}, head={vec[:3]}"
        else:
            result["error"] = resp.text[:500]
    except httpx.HTTPError as exc:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def check_rerank(base_url: str, api_key: str, model: str, name: str, timeout: float = 30.0) -> dict[str, Any]:
    """检查 bge 重排模型 /rerank。"""
    started = time.perf_counter()
    url = base_url.rstrip("/") + "/rerank"
    payload = {
        "model": model,
        "query": "选课时间",
        "documents": ["选课在第16至18周进行", "学费在开学前缴纳"],
    }
    result = {"name": name, "kind": "rerank", "model": model, "url": url, "ok": False}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=_headers(api_key), json=payload)
        latency = round((time.perf_counter() - started) * 1000, 1)
        result["latency_ms"] = latency
        result["status"] = resp.status_code
        if resp.status_code == 200:
            data = resp.json()
            scores = [r["relevance_score"] for r in data.get("results", [])]
            result["ok"] = True
            result["sample"] = f"top scores={[round(s, 4) for s in scores[:3]]}"
        else:
            result["error"] = resp.text[:500]
    except httpx.HTTPError as exc:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def run_all() -> list[dict[str, Any]]:
    _load_env()
    deepseek_base = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE)
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
    deepseek_model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    relay_base = os.environ.get("RELAY_BASE_URL", DEFAULT_RELAY_BASE)
    relay_key = os.environ.get("RELAY_API_KEY", "")
    relay_model = os.environ.get("RELAY_MODEL", DEFAULT_RELAY_MODEL)
    embed_model = os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBED_MODEL)
    rerank_model = os.environ.get("RERANKER_MODEL", DEFAULT_RERANK_MODEL)

    results = []
    results.append(await check_chat(deepseek_base, deepseek_key, deepseek_model, "DeepSeek 对话"))
    results.append(await check_chat(relay_base, relay_key, relay_model, "中转站 对话"))
    results.append(await check_embedding(relay_base, relay_key, embed_model, "中转站 Embedding"))
    results.append(await check_rerank(relay_base, relay_key, rerank_model, "中转站 bge 重排"))
    return results


def _print_report(results: list[dict[str, Any]]) -> None:
    print("=" * 66)
    print("文枢 · 模型连通性自检（doctor）")
    print("=" * 66)
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['name']:24s} model={r['model']:20s} "
              f"status={r.get('status', '-')} latency={r.get('latency_ms', '-')}ms")
        if r["ok"]:
            print(f"       sample: {r.get('sample', '')}")
        else:
            print(f"       error : {r.get('error', '')[:200]}")
    all_ok = all(r["ok"] for r in results)
    print("-" * 66)
    print("结论：", "全部通过 ✅" if all_ok else "存在失败项 ❌")
    print("=" * 66)


async def main() -> int:
    parser = argparse.ArgumentParser(description="文枢模型连通性自检")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = parser.parse_args()

    results = await run_all()
    if args.json:
        print(json.dumps({"results": results, "all_ok": all(r["ok"] for r in results)}, ensure_ascii=False, indent=2))
    else:
        _print_report(results)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
