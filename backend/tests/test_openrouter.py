import httpx
import pytest

from app.config import Settings
from app.llm.client import LLMError
from app.llm.openrouter import OpenRouterClient


@pytest.mark.asyncio
async def test_transient_connection_failure_retries_and_records_it(monkeypatch, tmp_path):
    attempts = []
    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    audit = tmp_path / "calls.jsonl"
    model = OpenRouterClient(Settings(openrouter_api_key="fake"), "test", audit)
    assert await model.complete_json([]) == {}
    assert len(attempts) == 2 and '"retry": 1' in audit.read_text()
    assert "fake" not in audit.read_text()


@pytest.mark.asyncio
async def test_access_rejection_is_not_retried(monkeypatch):
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(403, json={"error": "denied"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        await OpenRouterClient(Settings(openrouter_api_key="fake"), "test").complete_json([])
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_truncated_json_is_not_a_completed_response(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(LLMError, match="未完成"):
        await OpenRouterClient(Settings(openrouter_api_key="fake"), "test").complete_json([])
