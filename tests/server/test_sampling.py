import asyncio
from types import SimpleNamespace

import pytest

from src.server import sampling


def test_create_openai_client_returns_none_without_dependency(monkeypatch):
    monkeypatch.setattr(sampling, "OpenAI", None)

    assert sampling.create_openai_client() is None


def test_create_openai_client_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert sampling.create_openai_client() is None


def test_sample_text_calls_openai_responses_api(monkeypatch):
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "sample-key")
    monkeypatch.setenv("FASTMCP_SAMPLING_MODEL", "sample-model")
    monkeypatch.setenv("FASTMCP_SAMPLING_BASE_URL", "https://openai.test/v1")

    created_clients = []
    response_calls = []

    class FakeResponses:
        def create(self, **kwargs):
            response_calls.append(kwargs)
            return SimpleNamespace(output_text='  {"allow": true}  ')

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created_clients.append(kwargs)
            self.responses = FakeResponses()

    monkeypatch.setattr(sampling, "OpenAI", FakeOpenAI)

    result = asyncio.run(
        sampling.sample_text(
            system_prompt="system instructions",
            user_prompt="SELECT 1 LIMIT 1",
            temperature=0,
            max_tokens=400,
        )
    )

    assert result == '{"allow": true}'
    assert created_clients == [
        {"api_key": "sample-key", "base_url": "https://openai.test/v1"}
    ]
    assert response_calls == [
        {
            "model": "sample-model",
            "instructions": "system instructions",
            "input": "SELECT 1 LIMIT 1",
            "temperature": 0,
            "max_output_tokens": 400,
        }
    ]


def test_sample_text_requires_configured_client(monkeypatch):
    monkeypatch.setattr(sampling, "create_openai_client", lambda: None, raising=False)

    with pytest.raises(RuntimeError, match="OpenAI client is unavailable"):
        asyncio.run(
            sampling.sample_text(
                system_prompt="system",
                user_prompt="query",
            )
        )
