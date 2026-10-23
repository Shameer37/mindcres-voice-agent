
import json

import httpx
import pytest

from app.services.llm_service import LLMExtractionService


@pytest.mark.asyncio
async def test_extracts_rating_and_nps(monkeypatch):
    service = LLMExtractionService()

    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps({
                        "identity_confirmed": None,
                        "issue_resolved": None,
                        "still_facing": None,
                        "technician_on_time": None,
                        "technician_rating": 5,
                        "nps": 9,
                        "other_feedback": None,
                    })
                }
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await service.extract(
        "I'd give the technician five stars and recommend "
        "SkyFiber nine out of ten."
    )

    assert result is not None
    assert result["technician_rating"] == 5
    assert result["nps"] == 9


@pytest.mark.asyncio
async def test_returns_none_when_ollama_is_unavailable(monkeypatch):
    service = LLMExtractionService()

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError(
            "Ollama is unavailable",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await service.extract("I rate the technician five.")

    assert result is None
