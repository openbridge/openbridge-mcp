import asyncio
from types import SimpleNamespace

import pytest

from src.server.tools import service


def test_validate_query_requires_context():
    with pytest.raises(ValueError):
        asyncio.run(service.validate_query("select 1", key_name="acc"))


def test_validate_query_requires_openai_key(monkeypatch):
    class DummyContext:
        async def sample(self, **kwargs):
            return SimpleNamespace(text="{}")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError):
        asyncio.run(service.validate_query("select 1 limit 1", key_name="acc", ctx=DummyContext()))


def test_validate_query_allows_read_only_query(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class DummyContext:
        def __init__(self):
            self.calls = []

        async def sample(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(text='{"allow": true, "read_only": true}')

    ctx = DummyContext()
    result = asyncio.run(
        service.validate_query(
            "SELECT * FROM example LIMIT 5",
            key_name="acc",
            ctx=ctx,
        )
    )

    assert result["decision"]["allowed"] is True
    assert result["heuristics"]["has_limit"] is True
    assert result["sampling"]["details"]["allow"] is True
    assert ctx.calls, "expected sampling to be invoked"


def test_validate_query_denies_query_without_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class DummyContext:
        async def sample(self, **kwargs):
            return SimpleNamespace(text='{"allow": true, "read_only": true}')

    result = asyncio.run(
        service.validate_query(
            "SELECT id FROM dataset",
            key_name="acc",
            ctx=DummyContext(),
        )
    )

    assert result["decision"]["allowed"] is False
    assert "Query lacks a LIMIT clause" in result["heuristics"]["warnings"][0]


def test_execute_query_returns_data_on_success(monkeypatch):
    async def fake_validate_query(*args, **kwargs):
        return {"decision": {"allowed": True}}

    monkeypatch.setattr(service, "validate_query", fake_validate_query)
    monkeypatch.setattr(service, "get_auth_headers", lambda: {"Authorization": "token"})
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")

    def fake_post(url, json, headers):
        assert url == "https://service.test/service/query/production/query"
        assert json["data"]["attributes"]["query"] == "select 1"
        return SimpleNamespace(status_code=200, json=lambda: {"data": [{"row": 1}]})

    monkeypatch.setattr(service.requests, "post", fake_post)

    rows = asyncio.run(service.execute_query("select 1", "acc", ctx=object()))

    assert rows == [{"row": 1}]


def test_execute_query_short_circuits_on_failed_validation(monkeypatch):
    async def fake_validate_query(*args, **kwargs):
        return {
            "decision": {"allowed": False},
            "reason": "unsafe",
        }

    monkeypatch.setattr(service, "validate_query", fake_validate_query)

    def fail_get_auth_headers():
        pytest.fail("get_auth_headers should not be called when validation fails")

    monkeypatch.setattr(service, "get_auth_headers", fail_get_auth_headers)

    def fail_post(*args, **kwargs):
        pytest.fail("execute_query should not perform HTTP request on validation failure")

    monkeypatch.setattr(service.requests, "post", fail_post)

    result = asyncio.run(service.execute_query("select 1", "acc", ctx=object()))

    assert result == [{"error": "Query validation failed", "validation": {"decision": {"allowed": False}, "reason": "unsafe"}}]


def test_get_suggested_table_names_returns_master_suffix(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None):
        assert url == "https://service.test/service/rules/prod/v1/rules/search"
        assert params == {"path": "path-query", "latest": "true"}
        return SimpleNamespace(
            json=lambda: {
                "data": [
                    {"attributes": {"path": "rules/catalog/product"}},
                    {"attributes": {"path": "rules/catalog/order"}},
                ]
            }
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    names = service.get_suggested_table_names("path-query")

    assert names == ["product_master", "order_master"]


def test_get_table_rules_strips_master_suffix(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda: {"Authorization": "token"})

    def fake_get(url, headers=None):
        assert url == "https://service.test/service/rules/prod/v1/rules/search?path=orders&latest=true"
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [
                    {"attributes": {"path": "catalog/orders"}},
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    rules = service.get_table_rules("orders_master")

    assert rules == {"attributes": {"path": "catalog/orders"}}
