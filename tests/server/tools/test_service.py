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
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    with pytest.raises(ValueError):
        asyncio.run(service.validate_query("select 1 limit 1", key_name="acc", ctx=DummyContext()))


def test_validate_query_allows_read_only_query(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "true")

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
    monkeypatch.setenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "true")

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
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")

    def fake_post(url, json, headers, timeout):
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

    assert result["error_kind"] == "mcp_input_validation"
    assert result["error_code"] == "INPUT_VALIDATION_FAILED"
    assert result["_meta"]["validation"]["reason"] == "unsafe"


def test_get_suggested_table_names_returns_candidates_shape(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == "https://service.test/service/rules/prod/v1/rules/search"
        assert params == {"path__icontains": "path-query", "latest": "true"}
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {"attributes": {"path": "rules/catalog/product"}},
                    {"attributes": {"path": "rules/catalog/order"}},
                ]
            }
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("path-query")
    assert result["query"] == "path-query"
    assert [item["lookup_key"] for item in result["candidates"]] == ["order", "product"]
    assert "product_master" in result["candidates"][1]["aliases"]


def test_get_suggested_table_names_returns_envelope_on_non_json(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="<html>bad</html>",
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("path-query")
    assert result["error_kind"] == "sp_api_client"


def test_get_table_schema_strips_master_suffix(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == "https://service.test/service/rules/prod/v1/rules/search"
        assert params == {"path__icontains": "orders", "latest": "true"}
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {"attributes": {"path": "catalog/orders"}},
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("orders_master")

    assert result["lookup_key"] == "orders"
    assert result["schema"] == {"attributes": {"path": "catalog/orders"}}


def test_get_suggested_table_names_extracts_leaf_from_hierarchical_path(monkeypatch):
    """Real Rules API returns hierarchical paths; candidate lookup keys should use leaves."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_advertised_products"}},
                    {"attributes": {"path": "amazon-ads/amzn_ads_sb_campaigns"}},
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("amzn_ads")
    assert [item["lookup_key"] for item in result["candidates"]] == [
        "amzn_ads_sb_campaigns",
        "amzn_ads_sp_advertised_products",
    ]


def test_get_suggested_table_names_returns_envelope_on_non_200(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(status_code=500, text="server error")

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("anything")
    assert result["error_kind"] == "sp_api_http"


def test_get_suggested_table_names_returns_envelope_on_request_exception(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    import requests as _requests

    def fake_get(*args, **kwargs):
        raise _requests.RequestException("network down")

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("anything")
    assert result["error_kind"] == "sp_api_client"


def test_get_table_schema_uses_icontains_filter(monkeypatch):
    """Regression: the Rules API stores hierarchical paths; exact `path=`
    returned 0 rows in production. Filter must be path__icontains."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_advertised_products"}},
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("amzn_ads_sp_advertised_products")

    assert captured["url"] == "https://service.test/service/rules/prod/v1/rules/search"
    assert captured["params"] == {"path__icontains": "amzn_ads_sp_advertised_products", "latest": "true"}
    assert result["lookup_key"] == "amzn_ads_sp_advertised_products"
    assert result["schema"]["attributes"]["path"] == "amazon-ads/amzn_ads_sp_advertised_products"


def test_get_table_schema_picks_exact_suffix_when_multiple_match(monkeypatch):
    """Live-validated case: path__icontains=amzn_ads_sp_campaigns returns
    three rows (campaigns, campaigns_by_adgroup, campaigns_by_placement).
    The endswith tie-break must pick the row that ends exactly with the
    requested bare name."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_campaigns"}},
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_campaigns_by_adgroup"}},
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_campaigns_by_placement"}},
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("amzn_ads_sp_campaigns")

    assert result["lookup_key"] == "amzn_ads_sp_campaigns"
    assert result["schema"]["attributes"]["path"] == "amazon-ads/amzn_ads_sp_campaigns"


def test_get_table_schema_returns_envelope_when_no_matches(monkeypatch):
    """Live-validated case: sp_sales_and_traffic_sku has no rule published.
    Tool must return None cleanly (not raise, not return an unrelated row)."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text='{"data":[]}',
            json=lambda: {"data": []},
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("sp_sales_and_traffic_sku")
    assert result["error_kind"] == "mcp_input_validation"
    assert result["error_code"] == "TABLE_NOT_FOUND"


def test_get_table_schema_returns_envelope_on_non_200(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(status_code=403, text="forbidden")

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("any_table")
    assert result["error_kind"] == "sp_api_http"


def test_get_table_schema_returns_envelope_on_non_json(monkeypatch):
    """A 200 with a non-JSON body (e.g. a proxy error HTML page) must not crash."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="<html>gateway error</html>",
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("any_table")
    assert result["error_kind"] == "sp_api_client"


def test_get_table_schema_strips_master_suffix_in_query(monkeypatch):
    """Callers may pass '<table>_master' (from get_suggested_table_names);
    the schema lookup must strip the suffix before searching."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {"data": [{"attributes": {"path": "amazon-ads/amzn_ads_sp_campaigns"}}]},
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    service.get_table_schema("amzn_ads_sp_campaigns_master")

    # Suffix must be removed before it hits the wire
    assert captured["url"] == "https://service.test/service/rules/prod/v1/rules/search"
    assert captured["params"] == {"path__icontains": "amzn_ads_sp_campaigns", "latest": "true"}


def test_execute_query_denies_on_validation_error(monkeypatch):
    """When validate_query raises ValueError, execute_query should deny (fail-closed)."""
    async def raise_validation_error(*args, **kwargs):
        raise ValueError("Sampling API key required")

    monkeypatch.setattr(service, "validate_query", raise_validation_error)

    def fail_get_auth_headers(*args, **kwargs):
        pytest.fail("get_auth_headers should not be called when validation raises")

    monkeypatch.setattr(service, "get_auth_headers", fail_get_auth_headers)

    def fail_post(*args, **kwargs):
        pytest.fail("execute_query should not perform HTTP request on validation error")

    monkeypatch.setattr(service.requests, "post", fail_post)

    result = asyncio.run(service.execute_query("select 1", "acc", ctx=object()))

    assert result["error_kind"] == "internal_error"
    assert "Query validation unavailable" in result["summary"]
    assert result["_meta"]["validation"] == "unavailable"


def test_get_amazon_api_access_token_handles_non_json_error_response(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=502,
            text="bad gateway",
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_api_access_token(123)

    assert result["error_kind"] == "sp_api_http"
    assert result["summary"] == "Failed to retrieve Amazon API access token"


# ---------------------------------------------------------------------------
# Phase 2a — service.py error path coverage
#
# Contracts under test (docs/tool-contracts.md):
# - execute_query: non-200 / RequestException return structured error dict.
# - get_table_schema: RequestException returns None; ambiguous multi-match
#   (no endswith hit) returns None (refuse to guess).
# - get_amazon_api_access_token: missing/null access_token is an error
#   shape, not a success-like None-token response.
# - get_amazon_advertising_profiles: every upstream failure returns [].
# ---------------------------------------------------------------------------


import requests as _requests_module  # noqa: E402  (keep top-of-file tidy in existing file)


# --- execute_query ---------------------------------------------------------


def test_execute_query_non_200_returns_structured_error(monkeypatch):
    """A non-200 from the Query API must surface as a structured error dict,
    not a raised exception or a raw response object."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    async def passing_validation(*args, **kwargs):
        return {"decision": {"allowed": True}}

    monkeypatch.setattr(service, "validate_query", passing_validation)

    def fake_post(url, json, headers, timeout):
        return SimpleNamespace(status_code=502, text="bad gateway")

    monkeypatch.setattr(service.requests, "post", fake_post)

    result = asyncio.run(service.execute_query("SELECT 1 LIMIT 1", "acc", ctx=object()))

    assert result["error_kind"] == "sp_api_http"
    assert result["summary"] == "Failed to execute query"
    assert result["_meta"]["status"] == 502


def test_execute_query_network_failure_returns_structured_error(monkeypatch):
    """REGRESSION: RequestException in the query POST path must not propagate
    — it must be caught and reported via the structured error shape."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    async def passing_validation(*args, **kwargs):
        return {"decision": {"allowed": True}}

    monkeypatch.setattr(service, "validate_query", passing_validation)

    def raising_post(*args, **kwargs):
        raise _requests_module.Timeout("query backend unreachable")

    monkeypatch.setattr(service.requests, "post", raising_post)

    result = asyncio.run(service.execute_query("SELECT 1 LIMIT 1", "acc", ctx=object()))

    assert result["error_kind"] == "sp_api_client"
    assert result["summary"] == "Query execution failed"
    assert result["_meta"]["status"] is None


def test_execute_query_non_json_200_returns_structured_error(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    async def passing_validation(*args, **kwargs):
        return {"decision": {"allowed": True}}

    monkeypatch.setattr(service, "validate_query", passing_validation)

    def fake_post(url, json, headers, timeout):
        return SimpleNamespace(
            status_code=200,
            text="<html>gateway error</html>",
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(service.requests, "post", fake_post)

    result = asyncio.run(service.execute_query("SELECT 1 LIMIT 1", "acc", ctx=object()))

    assert result["error_kind"] == "sp_api_client"
    assert result["summary"] == "Failed to parse query response"
    assert result["_meta"]["status"] == 200


# --- get_table_schema ------------------------------------------------------


def test_get_table_schema_network_failure_returns_none(monkeypatch):
    """REGRESSION: RequestException must not propagate from the Rules API call."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def raising_get(*args, **kwargs):
        raise _requests_module.ConnectionError("rules API down")

    monkeypatch.setattr(service.requests, "get", raising_get)

    result = service.get_table_schema("amzn_ads_sp_campaigns")
    assert result["error_kind"] == "sp_api_client"


def test_get_table_schema_returns_envelope_on_ambiguous_multi_match(monkeypatch):
    """Contract: when alias resolution has no deterministic match, return envelope.

    This protects callers from silently receiving the wrong rule when their
    search term matches several unrelated paths."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    # None of these ends with exactly 'campaigns':
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_campaigns_by_adgroup"}},
                    {"attributes": {"path": "amazon-ads/amzn_ads_sp_campaigns_by_placement"}},
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("campaigns")
    assert result["error_kind"] == "mcp_input_validation"


# --- get_amazon_api_access_token ------------------------------------------


def test_get_amazon_api_access_token_happy_path(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/service/amzadv/token/7")
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {"data": {"access_token": "amzn-token", "client_id": "amzn-client"}},
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_api_access_token(7)

    assert result == {"access_token": "amzn-token", "client_id": "amzn-client"}


@pytest.mark.parametrize(
    "body,scenario",
    [
        ({"data": {}}, "missing access_token"),
        ({"data": {"access_token": None, "client_id": "c"}}, "null access_token"),
        ({"data": {"access_token": "", "client_id": "c"}}, "empty access_token"),
        ({"data": None}, "data is None"),
        ({"data": "not-a-dict"}, "data wrong type"),
    ],
    ids=lambda v: v if isinstance(v, str) else "body",
)
def test_get_amazon_api_access_token_treats_missing_or_falsy_as_error(
    monkeypatch, body, scenario
):
    """REGRESSION: earlier behavior returned {'access_token': None, ...} on
    these payloads, and downstream callers (get_amazon_advertising_profiles)
    treated the key's presence as 'token available' and silently built
    broken Authorization headers. Contract: return an error shape."""
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(status_code=200, text="{}", json=lambda: body)

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_api_access_token(7)

    assert result["error_kind"] == "auth_error", f"scenario: {scenario} — result: {result}"


def test_get_amazon_api_access_token_network_failure_returns_error_shape(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def raising_get(*args, **kwargs):
        raise _requests_module.Timeout("amzadv service unreachable")

    monkeypatch.setattr(service.requests, "get", raising_get)

    result = service.get_amazon_api_access_token(7)

    assert result["error_kind"] == "sp_api_client"
    assert result["_meta"]["status"] is None


def test_get_amazon_api_access_token_non_200_returns_error_shape(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=404,
            text='{"errors":[{"detail":"not found"}]}',
            json=lambda: {"errors": [{"detail": "not found"}]},
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_api_access_token(7)

    assert result["error_kind"] == "sp_api_http"


def test_get_amazon_api_access_token_sanitizes_traceback_details(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=500,
            text='Traceback (most recent call last): File "/var/task/service/views/base.py", line 1',
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_api_access_token(7)
    assert result["error_kind"] == "internal_error"
    assert result["_meta"]["sanitized"] is True


def test_get_amazon_api_access_token_missing_ritam_id_returns_auth_error(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {"ritam_data": {"name": "missing-id"}},
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_api_access_token(7)
    assert result["error_kind"] == "auth_error"


# --- get_amazon_advertising_profiles --------------------------------------


def test_get_amazon_advertising_profiles_returns_empty_when_identity_error(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"_envelope_version": 1, "error_kind": "mcp_input_validation"},
    )

    def fail_token(*args, **kwargs):
        pytest.fail("token lookup must not happen when identity lookup failed")

    monkeypatch.setattr(service, "get_amazon_api_access_token", fail_token)

    result = service.get_amazon_advertising_profiles(9)
    assert result["error_kind"] == "mcp_input_validation"


def test_get_amazon_advertising_profiles_returns_empty_when_token_has_error(monkeypatch):
    """REGRESSION: token helper's error shape (post-alignment) must short-circuit
    the profiles call, not produce a broken Authorization header."""
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"id": "9", "region": "na"},
    )
    monkeypatch.setattr(
        service,
        "get_amazon_api_access_token",
        lambda rid, ctx=None: {
            "_envelope_version": 1,
            "error_kind": "auth_error",
        },
    )

    def fail_profiles(*args, **kwargs):
        pytest.fail("profiles API must not be called when token has error")

    monkeypatch.setattr(service.requests, "get", fail_profiles)

    result = service.get_amazon_advertising_profiles(9)
    assert result["error_kind"] == "auth_error"


def test_get_amazon_advertising_profiles_unknown_region_returns_empty(monkeypatch):
    """Unknown region → soft failure (no KeyError)."""
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"id": "9", "region": "mars"},
    )
    monkeypatch.setattr(
        service,
        "get_amazon_api_access_token",
        lambda rid, ctx=None: {"access_token": "t", "client_id": "c"},
    )

    def fail_profiles(*args, **kwargs):
        pytest.fail("profiles API must not be called for unknown region")

    monkeypatch.setattr(service.requests, "get", fail_profiles)

    result = service.get_amazon_advertising_profiles(9)
    assert result["error_kind"] == "mcp_input_validation"


def test_get_amazon_advertising_profiles_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"id": "9", "region": "na"},
    )
    monkeypatch.setattr(
        service,
        "get_amazon_api_access_token",
        lambda rid, ctx=None: {"access_token": "t", "client_id": "c"},
    )

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=401, text="unauthorized")

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_advertising_profiles(9)
    assert result["error_kind"] == "sp_api_http"


def test_get_amazon_advertising_profiles_network_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"id": "9", "region": "na"},
    )
    monkeypatch.setattr(
        service,
        "get_amazon_api_access_token",
        lambda rid, ctx=None: {"access_token": "t", "client_id": "c"},
    )

    def raising_get(*args, **kwargs):
        raise _requests_module.ConnectionError("amazon api unreachable")

    monkeypatch.setattr(service.requests, "get", raising_get)

    result = service.get_amazon_advertising_profiles(9)
    assert result["error_kind"] == "sp_api_client"


def test_get_amazon_advertising_profiles_non_json_returns_empty(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"id": "9", "region": "na"},
    )
    monkeypatch.setattr(
        service,
        "get_amazon_api_access_token",
        lambda rid, ctx=None: {"access_token": "t", "client_id": "c"},
    )

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="<html>oops</html>",
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_advertising_profiles(9)
    assert result["error_kind"] == "sp_api_client"


def test_get_amazon_advertising_profiles_happy_path(monkeypatch):
    """Happy-path smoke to confirm the chain wires up correctly after all
    the defensive refactors."""
    monkeypatch.setattr(
        service,
        "get_remote_identity_by_id",
        lambda rid, ctx=None: {"id": "9", "region": "na"},
    )
    monkeypatch.setattr(
        service,
        "get_amazon_api_access_token",
        lambda rid, ctx=None: {"access_token": "t", "client_id": "c"},
    )

    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return SimpleNamespace(
            status_code=200,
            text="[]",
            json=lambda: [{"profileId": 123}],
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_amazon_advertising_profiles(9)

    assert result == [{"profileId": 123}]
    assert captured["url"] == "https://advertising-api.amazon.com/v2/profiles"
    assert captured["headers"]["Authorization"] == "Bearer t"
    assert captured["headers"]["Amazon-Advertising-API-ClientId"] == "c"


def test_get_suggested_table_names_returns_structured_candidates(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params == {"path__icontains": "sp_orders", "latest": "true"}
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {
                        "attributes": {
                            "path": "selling-partner/reports-sales/sp_orders_report",
                            "destination": {"tablename": "sp_orders_report_v14"},
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("sp_orders")

    assert result["query"] == "sp_orders"
    assert result["candidates"][0]["lookup_key"] == "sp_orders_report"
    assert "sp_orders_report_master" in result["candidates"][0]["aliases"]


def test_get_suggested_table_names_no_match_returns_envelope(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(status_code=200, text='{"data": []}', json=lambda: {"data": []})

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_suggested_table_names("xqzpdq")

    assert result["error_kind"] == "mcp_input_validation"
    assert result["error_code"] == "TABLE_NOT_FOUND"
    assert result["hints"]
    assert result["examples"]


def test_get_table_schema_alias_variants_resolve_same_canonical(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {
                        "attributes": {
                            "path": "selling-partner/reports-sales/sp_orders_report",
                            "destination": {"tablename": "sp_orders_report_v14"},
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    bare = service.get_table_schema("sp_orders_report")
    master = service.get_table_schema("sp_orders_report_master")
    versioned = service.get_table_schema("sp_orders_report_v14")

    assert bare["lookup_key"] == "sp_orders_report"
    assert master["lookup_key"] == "sp_orders_report"
    assert versioned["lookup_key"] == "sp_orders_report"


def test_get_table_schema_not_found_includes_suggestions(monkeypatch):
    monkeypatch.setattr(service, "SERVICE_API_BASE_URL", "https://service.test")
    monkeypatch.setattr(service, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    calls = {"count": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return SimpleNamespace(status_code=200, text='{"data": []}', json=lambda: {"data": []})
        return SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "data": [
                    {
                        "attributes": {
                            "path": "selling-partner/reports-sales/sp_orders_report",
                            "destination": {"tablename": "sp_orders_report_v14"},
                        }
                    },
                    {
                        "attributes": {
                            "path": "selling-partner/orders/sp_orders_pii_master",
                            "destination": {"tablename": "sp_orders_pii_master"},
                        }
                    },
                ]
            },
        )

    monkeypatch.setattr(service.requests, "get", fake_get)

    result = service.get_table_schema("sp_orders2")

    assert result["error_kind"] == "mcp_input_validation"
    assert result["error_code"] == "TABLE_NOT_FOUND"
    assert result["hints"]
    assert result["examples"]
